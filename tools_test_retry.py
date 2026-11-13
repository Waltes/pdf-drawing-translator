# -*- coding: utf-8 -*-
"""定向重译 + 折行 + 必出图的端到端验证：用假翻译器跑真实 process_pdf。

不接真模型，避免烧额度；要验的是「检测→翻译→复核→打回重译→复审→出图」
这条链路本身。五个场景各跑一遍：

  A 一次就过     —— 不该触发重译，也不该多花轮次
  B 第一次没译出 —— 复核打回，只重翻那几条，第二轮通过
  C 三轮都改不对 —— 到轮次上限必须停手（不能无限烧额度），
                    **但文件照出**，未译清单位置要写清楚（v1.0.9 的规矩）
  D 框太窄       —— 文字层折两行，不溢出、不留空白
  E 窄到折也放不下 —— 保留原文，不出白块，文件照出
  F 折行后又重写 —— 上一版溢出框外的那截必须擦掉，不能留半截旧词（v1.1.0）
  G 同页同样两处 —— 一处写成一处置空时，未译清单不能被整条划掉（v1.1.0）
  H 重译修好了   —— 账本里的旧账要清，否则报告永远挂着同一条（v1.1.0）

中文必须嵌真字体才回读得出来：insert_text 默认用的内置拉丁字体没有中文的
ToUnicode 映射，抽回来是一串「····」，那样测出来的「通过」是假的。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymupdf
from core import pdf_engine
from core.translate import MetricReport, verify_metric_output

CJK = r"C:\Windows\Fonts\msyh.ttc"
GOOD = "2 pcs 5 mm galvanized plate, 2790 mm long"
BAD = "仍是中文，没有翻出来"


class FakeTranslator:
    """
    可控的假翻译器。

    first_pass   第一次批量翻译返回什么（None 表示原样返回，模拟「没译出」）
    retry_pass   复核打回后返回什么
    """

    def __init__(self, first_pass=None, retry_pass=GOOD):
        self.dst_lang = "en"
        self.first_pass = first_pass
        self.retry_pass = retry_pass
        self.batch_calls = 0
        self.retry_calls = 0
        self.metric_report = MetricReport()

    def translate_many(self, items, max_workers=12, progress=None):
        self.batch_calls += 1
        if self.first_pass is None:
            # 原样返回 = 触发「这一条没有译出」，正是 v1.0.6 那两处中文的成因
            return [t for t, _l in items]
        return [self.first_pass for _ in items]

    def review_candidates(self, limit=30):
        return []

    def retranslate(self, items):
        self.retry_calls += 1
        return [self.retry_pass for _ in items]


def make_pdf(path, lines):
    d = pymupdf.open()
    p = d.new_page(width=420, height=300)
    p.insert_font(fontname="cjk", fontfile=CJK)
    y = 60
    for t in lines:
        p.insert_text((40, y), t, fontname="cjk", fontsize=12)
        # 行距必须拉开到 4 倍字高以上，否则会被段落重组合成一句，
        # 测出来的就不是「两条各自重译」而是「一段重译」了
        y += 80
    d.save(path)
    d.close()


def run_case(name, lines, translator, expect_unresolved, expect_retry, tmp):
    src = os.path.join(tmp, name + "_in.pdf")
    out = os.path.join(tmp, name + "_out.pdf")
    make_pdf(src, lines)
    stats = {}
    pdf_engine.process_pdf(src, out, translator, render_preview=False,
                           translate_batch=translator.translate_many,
                           scan_images=False, stats_out=stats,
                           dst_lang="en", reviewer=translator, verify=True)
    rep = stats.get("review")
    unresolved = bool(stats.get("review_unresolved"))
    made = os.path.exists(out)
    text = ""
    if made:
        d = pymupdf.open(out)
        text = d[0].get_text()
        d.close()

    # 关键变化：made 一律为真。v1.0.9 起复核不再拦文件，
    # 「不出文件」这条路已经拆了，这里必须守住。
    clean = not any(c in text for c in "米公斤")
    ok = (made
          and unresolved == expect_unresolved
          and (translator.retry_calls > 0) == expect_retry
          and (clean or expect_unresolved))
    print(f"[{'通过' if ok else '失败'}] {name}")
    print(f"    出文件={made} 遗留未解决={unresolved} 轮次={rep.round if rep else '-'} "
          f"重译调用={translator.retry_calls}")
    print(f"    过程={rep.history if rep else '-'}")
    print(f"    未译={stats.get('untranslated')}")
    print(f"    页面={text.splitlines() if text else '（未生成）'}")
    return ok


def run_wrap_case(tmp, narrow, expect_lines):
    """
    折行验证：中文两字框里塞英文长词。

    narrow=True 时把框做到放不下一行、但有高度折两行；
    narrow=False 时框小到折行也救不了，验证「保留原文而不是留白块」。
    """
    name = "D折行" if narrow else "E放不下"
    src = os.path.join(tmp, name + "_in.pdf")
    out = os.path.join(tmp, name + "_out.pdf")
    d = pymupdf.open()
    p = d.new_page(width=420, height=300)
    p.insert_font(fontname="cjk", fontfile=CJK)
    # 「门板」在 12pt 中文下大约 24pt 宽，英文 DOOR PANEL 要 50pt 以上 ——
    # 正是真实图纸里那种「中文两个字 = 英文九字符」的窄框
    p.insert_text((40, 60), "门板", fontname="cjk", fontsize=12)
    if not narrow:
        # 极窄：连一个单词都放不下（把文字缩到 4pt，框只有 8pt 宽）
        p.insert_text((200, 200), "合页", fontname="cjk", fontsize=4)
    d.save(src)
    d.close()

    class T(FakeTranslator):
        def translate_many(self, items, max_workers=12, progress=None):
            self.batch_calls += 1
            # 「合页」故意给一个连单行都摆不下的长译法：
            # 这是「折行也救不了」那一类的代表，验证的是「保留原文」而非「留白块」
            out = []
            for t, _l in items:
                if "门板" in t:
                    out.append("DOOR PANEL")
                elif "合页" in t:
                    out.append("HINGE WITH PIN AND SPRING")
                else:
                    out.append(t)
            return out

    tr = T()
    stats = {}
    pdf_engine.process_pdf(src, out, tr, render_preview=False,
                           translate_batch=tr.translate_many, scan_images=False,
                           stats_out=stats, dst_lang="en", reviewer=tr,
                           verify=True)
    made = os.path.exists(out)
    text, lines = "", []
    if made:
        d = pymupdf.open(out)
        page = d[0]
        text = page.get_text()
        # 按行取回，确认「DOOR」和「PANEL」被拆成两行写在不同 baseline 上
        ys = sorted({round(s["origin"][1], 1)
                     for b in page.get_text("dict")["blocks"] if b["type"] == 0
                     for ln in b["lines"] for s in ln["spans"]})
        d.close()

    if narrow:
        ok = (made and "DOOR" in text and "PANEL" in text and len(ys) >= 2
              and not any(c in text for c in "门板"))
    else:
        # 放不下：原文必须还在（不能留白块），且要进未译清单
        ok = made and "合页" in text and len(stats.get("untranslated") or []) >= 1

    print(f"[{'通过' if ok else '失败'}] {name}")
    print(f"    出文件={made} 行数={len(ys)} 未译={stats.get('untranslated')}")
    print(f"    页面={text.splitlines() if text else '（未生成）'}")
    return ok


def run_ghost_case(tmp):
    """
    F 折行写完再打回重写：上一版写到框外的那一截必须一起擦掉。

    v1.0.9 只按原框擦，折出来的第二行顶着原框上沿、单行还可能溢出到 1.4 倍
    框宽 —— 溢出部分擦不掉，重写后页面上就留着上一版的半截旧词
    （实测残留过一个 H）。这种字没人会当成「没翻」去查，只会觉得图上有个怪符号。
    """
    src = os.path.join(tmp, "F_in.pdf")
    d = pymupdf.open()
    p = d.new_page(width=420, height=300)
    p.insert_font(fontname="cjk", fontfile=CJK)
    p.insert_text((40, 60), "门板门板门板门板", fontname="cjk", fontsize=12)
    d.save(src)
    d.close()

    d = pymupdf.open(src)
    p = d[0]
    g = pdf_engine.extract_groups(p)[0]
    first = "DOOR PANEL WITH HINGE AND LOCK"
    plan = pdf_engine.plan_group(g, first, pdf_engine.text_obstacles(p, [g.rect]))
    pdf_engine.erase_text(p, [g])
    pdf_engine.write_groups(p, [(g, first)], plans=[plan])
    ink = [r for r in (g.rect, plan.get("ink")) if r is not None]

    rec = {"page": 1, "kind": "single", "obj": g, "src": "门板门板门板门板",
           "rect": g.rect, "ink": ink, "out": first, "lang": "zh"}
    ok_write = pdf_engine._rewrite_record(d, rec, "门板")
    text = p.get_text().strip()
    d.close()

    # 旧译文的任何一个词都不该留在这页上
    ghost = [w for w in ("DOOR", "PANEL", "HINGE", "LOCK") if w in text]
    ok = ok_write and not ghost and "门板" in text
    print(f"[{'通过' if ok else '失败'}] F 折行重写不留残影")
    print(f"    重写={ok_write} 页面={text!r} 残留={ghost or '无'}")
    return ok


def run_prune_case():
    """
    G 同一页两处一模一样的原文，一处写成了、一处框太窄没写成。

    未译清单里只有没写成的那一条，划账时必须看「这一处的记录是不是都写成了」，
    不能按「页码 + 原文」一刀切 —— 切掉之后清单说全部译出，图上还留着原文，
    而目标中文时复核不做文字层残留扫描，这一处就彻底没人发现了。
    """
    half = {"untranslated": [(1, "DOOR PANEL")]}
    pdf_engine._prune_untranslated(
        half, [{"page": 1, "src": "DOOR PANEL", "out": "门板"},
               {"page": 1, "src": "DOOR PANEL", "out": None}])
    whole = {"untranslated": [(1, "DOOR PANEL")]}
    pdf_engine._prune_untranslated(
        whole, [{"page": 1, "src": "DOOR PANEL", "out": "门板"}])
    ok = bool(half["untranslated"]) and not whole["untranslated"]
    print(f"[{'通过' if ok else '失败'}] G 未译清单不误划")
    print(f"    只写成一处的清单={half['untranslated']} · "
          f"两处都写成的清单={whole['untranslated']}")
    return ok


def run_ledger_case(tmp):
    """
    H 一条译文第一次算错、重译后算对，账本里的旧账要跟着清。

    不清的话第二轮复核照旧按第一次的结果报同一处，报告里挂着一条早就修好的
    「数值可疑」—— 用户拿着清单去核对，怎么核都对，最后只能怀疑工具坏了。
    """
    class T(FakeTranslator):
        """出口复现真实链路的复算记账"""

        def translate_many(self, items, max_workers=12, progress=None):
            self.batch_calls += 1
            out = []
            for t, _l in items:
                val, _i = verify_metric_output(
                    t, "508 mm x 1000 mm x 25 mm", dst="en", page=1,
                    report=self.metric_report, mode="metric")
                out.append(val)
            return out

        def retranslate(self, items):
            self.retry_calls += 1
            out = []
            for src, _h, _w in items:
                val, _i = verify_metric_output(
                    src, "1270 mm x 508 mm", dst="en", page=1,
                    report=self.metric_report, mode="metric")
                out.append(val)
            return out

    src = os.path.join(tmp, "H_in.pdf")
    out = os.path.join(tmp, "H_out.pdf")
    make_pdf(src, ["50 in. wide x 20 in. deep"])
    tr = T()
    # 首轮就让它带着一条 warn 进场，模拟「第一次复算没过」
    tr.metric_report.add("warn", 1, "50 in. wide x 20 in. deep",
                         "508 mm x 1000 mm x 25 mm", "译文数值与复算不符")
    stats = {}
    pdf_engine.process_pdf(src, out, tr, render_preview=False,
                           translate_batch=tr.translate_many, scan_images=False,
                           stats_out=stats, dst_lang="en", reviewer=tr, verify=True)
    rep = stats.get("review")
    numbers = len([it for it in rep.issues if it.kind == "number"]) if rep else -1
    text = ""
    if os.path.exists(out):
        dd = pymupdf.open(out)
        text = dd[0].get_text().strip()
        dd.close()
    ok = (os.path.exists(out) and not stats.get("review_unresolved")
          and numbers == 0 and "1270.00" in text)
    print(f"[{'通过' if ok else '失败'}] H 重译修好后不再挂旧账")
    print(f"    遗留={stats.get('review_unresolved')} 数值问题={numbers} "
          f"页面={text!r}")
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="retry_")
    lines = ["托盘承重 500 公斤", "护栏总长 3.05 米"]
    results = [
        run_case("A一次就过", lines, FakeTranslator(first_pass=GOOD), False, False, tmp),
        run_case("B打回重译", lines, FakeTranslator(first_pass=None), False, True, tmp),
        run_case("C三轮不改", lines, FakeTranslator(first_pass=None, retry_pass=BAD),
                 True, True, tmp),
        run_wrap_case(tmp, narrow=True, expect_lines=2),
        run_wrap_case(tmp, narrow=False, expect_lines=1),
        run_ghost_case(tmp),
        run_prune_case(),
        run_ledger_case(tmp),
    ]
    print("\n结论:", "全部通过" if all(results) else "有失败项")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
