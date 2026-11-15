# -*- coding: utf-8 -*-
"""
量一下「字号下限 80%」在真实图纸上的代价：多少条会因此写不下、退回原文。

翻译用假译器：把中文换成等宽度的英文单词串，不关心意思，只关心长度 ——
排版压力完全由长度决定，长度对了，结论就成立。
"""
import re
import sys
import pymupdf
from core import pdf_engine as pe
from core import image_text as it
from core import typography as ty

WORDS = ("hot dip galvanized square tube yellow part frame panel internal outer "
         "thick corner guard blue area weld side by side").split()


def fake(text, lang="zh"):
    """中文 → 等宽英文。1 个汉字约等于 2 个英文字符宽，取 2.4 偏保守。"""
    zh = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    if not zh:
        return text
    want = int(zh * 2.4)
    out, n = [], 0
    while n < want:
        w = WORDS[len(out) % len(WORDS)]
        out.append(w)
        n += len(w) + 1
    return " ".join(out)


def run(floor, hard, minread, tag):
    ty.SHRINK_FLOOR = floor           # _plan_one 运行时读这几个常量
    ty.SHRINK_HARD = hard
    ty.MIN_READABLE = minread
    it.MIN_WRITE_SIZE = minread
    src = r"C:/Users/stanl/Desktop/新储物柜示意图2025-12-19/新储物柜示意图2025-12-19.pdf"
    dst = "_floor_%s.pdf" % tag
    stats = {}
    pe.process_pdf(src, dst, fake, render_preview=False, scan_images=True,
                   ocr_full_pages=True, stats_out=stats, verify=False,
                   detect_outlined=False)
    miss = stats.get("untranslated") or []
    print("%s  未译 %d 条" % (tag, len(miss)))
    for m in miss[:12]:
        print("     ", m)
    return len(miss)


if __name__ == "__main__":
    a = run(0.25, 0.25, 3.5, "old")
    b = run(0.80, 0.80, 5.5, "hard80")
    c = run(0.80, 0.40, 5.5, "new")
    print("\n改前 %d 条 / 硬卡八成 %d 条 / 新规则(可读 5.5pt 兜底) %d 条" % (a, b, c))
