# -*- coding: utf-8 -*-
"""
符号补全的回归样张。

为什么单独一个脚本
------------------
直径符号是「看起来像就算」的判据，最容易在改别的功能时被顺手弄坏，
而且坏法很隐蔽：不是报错，是悄悄多补一个符号、或者少补一个。
一页图纸上少一个 ø，客户看到的就是一个没有直径的孔。

所以把边界情况固化成样张，改 glyphs.py 的任何阈值之后先跑这个。

    python tools_test_glyphs.py

三组用例：
    正例    该补的必须补上（OCR 丢了符号）
    不动    形状像但不是符号（斜线零、数字、百分号、汉字），一个字都不许动
    原样    原文已经是真符号，不许改写（尤其不许小写改成大写）

退出码 0 表示全过，1 表示有失败。打印里会写清每个用例卡在哪。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2            # noqa: E402
import numpy as np    # noqa: E402
import pymupdf        # noqa: E402

from core import glyphs as gl   # noqa: E402


def slashed_zero(page, cx, cy, h, w=None, over=1.0, lw=1.15):
    """画一个带斜线的零：全高椭圆 + 左下→右上的斜线，跟大写 Ø 同形。"""
    w = w or h * 0.58
    page.draw_oval(pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
                   color=(0.35, 0.35, 0.35), width=lw)
    page.draw_line(pymupdf.Point(cx - w / 2 * over, cy + h / 2 * over),
                   pymupdf.Point(cx + w / 2 * over, cy - h / 2 * over),
                   color=(0.35, 0.35, 0.35), width=lw)


# (名字, 页面上真画什么, OCR 声称读到什么, 期望输出)
CASES = [
    ("OCR 丢了小写 ø",        "text:ø12mm",    "12mm",   "ø12mm"),
    ("OCR 丢了大写 Ø",        "text:Ø50",      "50",     "ø50"),
    ("OCR 读成 @",            "text:ø12mm",    "@12mm",  "ø12mm"),
    ("原文已有 ø，不许改写",   "text:ø12mm",    "ø12mm",  "ø12mm"),
    ("斜线零，位置已被 0 占住", "draw:slashed", "012mm",  "012mm"),
    ("无符号 25mm",           "text:25mm",     "25mm",   "25mm"),
    ("无符号 8mm",            "text:8mm",      "8mm",    "8mm"),
    ("百分号 100%",           "text:100%",     "100%",   "100%"),
    ("汉字 日",               "text:日",        "日",      "日"),
]


def build():
    """按 CASES 造一页样张，返回 (doc, page, 待测框列表, 低倍位图)。"""
    doc = pymupdf.open()
    page = doc.new_page(width=360, height=60 * len(CASES) + 40)
    boxes, yy = [], 40
    for name, spec, ocr, want in CASES:
        kind, _, txt = spec.partition(":")
        if kind == "text":
            fn = "china-s" if txt == "日" else "helv"
            page.insert_text((24, yy), txt, fontsize=16,
                             color=(0.5, 0.5, 0.5), fontname=fn)
        else:
            # 画「Ø」同形：圆环 + 斜线，再排 "12mm"
            slashed_zero(page, 32.0, yy - 6.0, 12.5)
            page.insert_text((42, yy), "12mm", fontsize=16,
                             color=(0.5, 0.5, 0.5), fontname="helv")
        boxes.append((pymupdf.Rect(18, yy - 20, 130, yy + 8), ocr, want, name))
        yy += 60

    # 低倍位图：这是流程里实际喂给 complete_symbols 的那张图，3 倍
    pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3))
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n)[:, :, :3].astype(int)
    return doc, page, boxes, arr


def main():
    doc, page, boxes, rgb = build()
    print("%-4s %-24s %-12s %-12s %s" % ("", "用例", "OCR 输入", "实际输出", "期望"))
    bad = []
    for rect, ocr, want, name in boxes:
        new, _added = gl.complete_symbols(
            page, page.rect, rect, 3, ocr, rgb=rgb)
        ok = (new == want)
        if not ok:
            bad.append("%s：%s→%s（期望 %s）" % (name, ocr, new, want))
        print("%-4s %-24s %-12s %-12s %s" % (
            "OK" if ok else "!!", name, ocr, new, want))
    doc.close()

    print()
    if bad:
        print("失败 %d 项：" % len(bad))
        for b in bad:
            print("  -", b)
        return 1
    print("全部通过（%d 项）" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
