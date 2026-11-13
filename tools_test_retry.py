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

中文必须嵌真字体才回读得出来：insert_text 默认用的内置拉丁字体没有中文的
ToUnicode 映射，抽回来是一串「····」，那样测出来的「通过」是假的。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymupdf
from core import pdf_engine
from core.translate import MetricReport

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
    ]
    print("\n结论:", "全部通过" if all(results) else "有失败项")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
