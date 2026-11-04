# -*- coding: utf-8 -*-
"""
生成 README 用的效果对比图（docs/demo.png）。

为什么要单独造一张图纸，而不用现成的 sample：
sample/ 里那份是真实项目图纸，带客户公司名。放开源仓库里不合适。
这里虚构一个「铝制支架」，内容覆盖各种英制写法（英寸符号、混合分数、
两个单引号、磅、螺纹代号），一眼能看出换算和标黄的效果。

用法（需要装 Pillow 和 PyMuPDF）：
    python packaging/make_demo.py
"""

import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

import pymupdf
from PIL import Image, ImageDraw, ImageFont

W, H = 420, 468

# 英文原文照着真实图纸的写法造：混合分数、两个单引号、磅、螺纹代号全上，
# 这样图里能同时看到「该换算的换算了」和「不该动的没动」
LINES = [
    "01  Assembled bracket",
    'Tube OD 1-1/2", wall 1/8", overall 9 ft long.',
    'Base plate 1/4" thick, 4" x 6", weight 25 lb.',
    'Mount with 1/4"-20 bolts through 2 x 11/32" holes on R3-1/2".',
    "Weld all round, grind flush, then powder coat.",
]

# 精确对应的中文，直接给定而不是让机器翻 —— 演示图要干净，
# 不能让读者看到机翻的怪句子而误以为是工具水平。
ZH = {
    "01  Assembled bracket": "01  装配完成图",
    'Tube OD 1-1/2", wall 1/8", overall 9 ft long.':
        '管外径 1-1/2"，壁厚 1/8"，总长 9 ft。',
    'Base plate 1/4" thick, 4" x 6", weight 25 lb.':
        '底板厚 1/4"，尺寸 4" × 6"，重 25 lb。',
    'Mount with 1/4"-20 bolts through 2 x 11/32" holes on R3-1/2".':
        '用 1/4"-20 螺栓安装，穿过 2 个 11/32" 孔，位于 R3-1/2" 圆周上。',
    "Weld all round, grind flush, then powder coat.":
        "全周焊接，打磨平整后喷粉。",
    "ALUMINIUM BRACKET": "铝制支架",
    "UNITS": "单位",
    "inch": "英寸",
    "PART": "零件",
    "DWG NO.": "图号",
    "REV": "修订",
    "DATE": "日期",
    "SCALE 1:4": "比例 1:4",
}


def build_source(path):
    """造一张虚构的演示图纸"""
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)

    # ---- 标题栏 ----
    page.draw_rect(pymupdf.Rect(20, 20, 400, 92), color=(0.2, 0.2, 0.2), width=1)
    page.draw_line(pymupdf.Point(20, 56), pymupdf.Point(400, 56),
                   color=(0.45, 0.45, 0.45), width=0.6)
    for x in (120, 210, 270, 330):
        page.draw_line(pymupdf.Point(x, 56), pymupdf.Point(x, 92),
                       color=(0.45, 0.45, 0.45), width=0.6)

    page.insert_text((26, 42), "PART", fontsize=6, color=(0.35, 0.35, 0.35))
    page.insert_text((26, 52), "ALUMINIUM BRACKET", fontsize=9)
    page.insert_text((26, 70), "DWG NO.", fontsize=6, color=(0.35, 0.35, 0.35))
    page.insert_text((26, 86), "DEMO-001", fontsize=8)
    for x, label, value in ((126, "DATE", "2026-11-04"),
                            (216, "UNITS", "inch"),
                            (276, "REV", "A"),
                            (336, "SCALE 1:4", "1:4")):
        page.insert_text((x + 6, 70), label, fontsize=6, color=(0.35, 0.35, 0.35))
        page.insert_text((x + 6, 86), value, fontsize=8)

    # ---- 说明文字 ----
    y = 120
    for i, s in enumerate(LINES):
        fs = 9 if i == 0 else 8
        page.insert_text((24, y), s, fontsize=fs)
        y += 26

    # ---- 简易零件图 ----
    page.draw_rect(pymupdf.Rect(60, 292, 360, 430),
                   color=(0.55, 0.55, 0.55), width=0.8)
    page.draw_rect(pymupdf.Rect(120, 322, 300, 402),
                   color=(0.25, 0.25, 0.25), width=1.2)
    for cx in (152, 268):
        page.draw_circle(pymupdf.Point(cx, 362), 12,
                         color=(0.25, 0.25, 0.25), width=1.2)
    # 尺寸线：都横着排。旋转 90° 的中文/数字在 PDF 里会变成竖堆的碎字，
    # 演示图上看着像乱码，不如不放
    page.draw_line(pymupdf.Point(120, 414), pymupdf.Point(300, 414),
                   color=(0.4, 0.4, 0.4), width=0.5)
    page.insert_text((192, 426), '4"', fontsize=8)
    page.insert_text((126, 312), '1-1/2" OD', fontsize=7, color=(0.3, 0.3, 0.3))

    doc.save(path)
    doc.close()


def translate_stub(items, max_workers=12, progress=None):
    """把英文换成预先写好的中文，英制写法原样留着交给换算出口处理"""
    out = []
    for text, _lang in items:
        t = text.strip()
        if t in ZH:
            out.append(ZH[t])
            continue
        r = text
        for k in sorted(ZH, key=len, reverse=True):
            if k in r:
                r = r.replace(k, ZH[k])
        out.append(r)
    return out


def render(pdf_path, png_path, dpi=100):
    doc = pymupdf.open(pdf_path)
    pix = doc[0].get_pixmap(dpi=dpi)
    pix.save(png_path)
    doc.close()


def label_bar(width, text, sub=""):
    """给每张图加一条标题，读图的人才知道哪边是哪边"""
    bar = Image.new("RGB", (width, 52), (246, 247, 249))
    d = ImageDraw.Draw(bar)
    try:
        f1 = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 22)
        f2 = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
    except Exception:
        f1 = f2 = ImageFont.load_default()
    d.text((22, 10), text, font=f1, fill=(30, 34, 40))
    if sub:
        w = d.textlength(text, font=f1)
        d.text((22 + w + 14, 15), sub, font=f2, fill=(110, 118, 128))
    return bar


def main():
    tmp = os.path.join(APP_ROOT, "_demo_tmp")
    os.makedirs(tmp, exist_ok=True)
    out_dir = os.path.join(APP_ROOT, "docs")
    os.makedirs(out_dir, exist_ok=True)

    src = os.path.join(tmp, "demo_en.pdf")
    build_source(src)

    from core import pdf_engine
    from core.translate import Translator

    tr = Translator()
    tr.reset_metric_report()
    tr._translate_inner = translate_stub

    zh = os.path.join(tmp, "demo_zh.pdf")
    marked = os.path.join(tmp, "demo_marked.pdf")
    pdf_engine.process_pdf(src, zh, None, render_preview=False,
                           translate_batch=tr.translate_many,
                           scan_images=False,
                           annotate_path=marked)

    p_en = os.path.join(tmp, "en.png")
    p_zh = os.path.join(tmp, "zh.png")
    render(src, p_en)
    # 有标注版就用标注版，标黄才是这张图要展示的重点
    render(marked if os.path.exists(marked) else zh, p_zh)

    # 顺带留两份示例图纸：装完不知道拿什么试的人，可以直接拖这个进去。
    # 内容是自己造的虚构零件，不涉及任何客户资料。
    smp = os.path.join(APP_ROOT, "samples")
    os.makedirs(smp, exist_ok=True)
    import shutil as _sh
    _sh.copy2(src, os.path.join(smp, "demo-bracket-en.pdf"))
    _sh.copy2(zh, os.path.join(smp, "demo-bracket-zh.pdf"))
    print("示例图纸已更新到", smp)

    left, right = Image.open(p_en).convert("RGB"), Image.open(p_zh).convert("RGB")
    gap, pad = 28, 28
    w = left.width + right.width + gap + pad * 2
    h = max(left.height, right.height) + 52 + pad * 2

    canvas = Image.new("RGB", (w, h), (232, 234, 238))
    x1 = pad
    canvas.paste(label_bar(left.width, "原文 · 英文", "英制尺寸"), (x1, pad))
    canvas.paste(left, (x1, pad + 52))
    x2 = pad + left.width + gap
    canvas.paste(label_bar(right.width, "译文 · 中文", "标黄＝由英制换算而来"), (x2, pad))
    canvas.paste(right, (x2, pad + 52))

    # 细边框，白底才不糊在一起
    d = ImageDraw.Draw(canvas)
    for x in (x1, x2):
        d.rectangle([x - 1, pad + 51, x + left.width, pad + 52 + left.height],
                    outline=(205, 208, 214))

    out = os.path.join(out_dir, "demo.png")
    canvas.save(out, optimize=True)
    print("已生成", out, canvas.size, f"{os.path.getsize(out) / 1024:.0f} KB")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
