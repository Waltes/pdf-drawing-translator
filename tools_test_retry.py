# -*- coding: utf-8 -*-
"""v1.0.8 定向重译的端到端验证：用假翻译器跑真实 process_pdf。

不接真模型，避免烧额度；要验的是「检测→翻译→复核→打回重译→复审→出图」
这条链路本身，三个场景各跑一遍：

  A 一次就过     —— 不该触发重译，也不该多花轮次
  B 第一次没译出 —— 复核打回，只重翻那几条，第二轮通过，文件照出
  C 三轮都改不对 —— 到轮次上限必须停手，不出文件（不能无限烧额度）

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


def run_case(name, lines, translator, expect_blocked, expect_retry, tmp):
    src = os.path.join(tmp, name + "_in.pdf")
    out = os.path.join(tmp, name + "_out.pdf")
    make_pdf(src, lines)
    stats = {}
    pdf_engine.process_pdf(src, out, translator, render_preview=False,
                           translate_batch=translator.translate_many,
                           scan_images=False, stats_out=stats,
                           dst_lang="en", reviewer=translator, verify=True)
    rep = stats.get("review")
    blocked = bool(stats.get("blocked"))
    made = os.path.exists(out)
    text = ""
    if made:
        d = pymupdf.open(out)
        text = d[0].get_text()
        d.close()

    ok = (blocked == expect_blocked
          and made != expect_blocked
          and (translator.retry_calls > 0) == expect_retry
          and (expect_blocked or not any(c in text for c in "米公斤")))
    print(f"[{'通过' if ok else '失败'}] {name}")
    print(f"    阻断={blocked} 出文件={made} 轮次={rep.round if rep else '-'} "
          f"重译调用={translator.retry_calls}")
    print(f"    过程={rep.history if rep else '-'}")
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
    ]
    print("\n结论:", "全部通过" if all(results) else "有失败项")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
