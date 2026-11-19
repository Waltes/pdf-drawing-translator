# -*- coding: utf-8 -*-
"""合成一份「斜排 + 竖排 + 横排」对照图，验证三种朝向是否各按各的方向写回。

为什么必须合成：7 份客户图纸里斜排只有 3 条（R35mm、Ø12mm、96），而且都是
纯尺寸标注、根本不用翻译 —— 真实图纸测不到斜排路径。合成一条带中文的斜排
标注，才能把「擦除 → 翻译 → 按角度写回」这条链真正跑通。

图里放三条：
  A 斜排  沿 25° 引线写「半径35mm圆管」   —— 验证角度保持、字号装进两头界限
  B 竖排  「上下调节100mm」                —— 对照组，验证竖排没被新判据带偏
  C 横排  「材料：外方管20×20×1.5mm」      —— 对照组，验证普通横排不受影响

用法：
    py -3 _synth_slant.py            # 造图 + 跑 + 导出对照图
    py -3 _synth_slant.py make       # 只造图
"""
import json
import math
import os
import shutil
import sys

sys.path.insert(0, ".")

from PIL import Image, ImageDraw, ImageFont

import pymupdf

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "_synth")
FONT_CN = r"C:\Windows\Fonts\msyh.ttc"
CFG = r"C:\Users\stanl\AppData\Roaming\PDFDrawingTranslator\data\config.json"

# 位图按 4 倍渲染（与 image_text.ZOOM 一致），这样位图里 24px 的字
# 落到 PDF 上是 6pt，正好是图纸小标注的量级
ZOOM = 4


def _text_layer(size, text, angle, color=(0, 0, 0)):
    """在一个透明画布上水平写一行字，再整体转 angle 度（PIL 逆时针为正）。"""
    font = ImageFont.truetype(FONT_CN, size)
    tmp = Image.new("RGBA", (size * (len(text) + 2), size * 3), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.text((size // 2, size // 2), text, font=font, fill=color + (255,))
    bb = tmp.getbbox()
    tmp = tmp.crop(bb)
    # PIL 的 rotate 逆时针为正；屏幕上「从左下往右上写」在 PIL 里是 +angle
    return tmp.rotate(angle, expand=True, resample=Image.BICUBIC)


def make():
    """造一张 A4 横版图纸，图里压一张位图，位图上有三种朝向的中文标注。"""
    os.makedirs(OUT, exist_ok=True)
    W, H = 1200, 700           # 位图像素（对应 300×175pt）
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    px = 24                    # 位图字号 → PDF 上 6pt

    # A 斜排：先画一条 25° 的引线，再沿它写标注（与真实图纸的半径标注同构）
    ax, ay = 120, 560
    bx, by = ax + 360 * math.cos(math.radians(-25)), ay + 360 * math.sin(math.radians(-25))
    d.line([(ax, ay), (bx, by)], fill=(0, 0, 0), width=2)
    d.ellipse([ax - 8, ay - 8, ax + 8, ay + 8], outline=(0, 0, 0), width=2)  # 圆心
    tA = _text_layer(px, "半径35mm圆管", 25)
    img.paste(tA, (int(ax + 90), int(ay - 150)), tA)

    # B 竖排：竖直往上一列（图纸里尺寸线旁的写法）
    tB = _text_layer(px, "上下调节100mm", 90)
    img.paste(tB, (760, 300), tB)
    d.line([(742, 340), (742, 520)], fill=(0, 0, 0), width=2)   # 竖排旁边一条线

    # C 横排：普通说明
    font = ImageFont.truetype(FONT_CN, px)
    d.text((80, 60), "材料：外方管20×20×1.5mm", font=font, fill=(0, 0, 0))

    png = os.path.join(OUT, "slant_src.png")
    img.save(png)

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)      # A4 竖版，位图占上半页
    page.insert_image(pymupdf.Rect(30, 40, 565, 352), filename=png)
    src = os.path.join(OUT, "slant_src.pdf")
    doc.save(src)
    doc.close()
    print("造图完成：", src)
    return src


def make_accept():
    """
    造一份「改动点验收件」：四条中文，每条对应 1.1.7 的一个改动。

    客户图纸里查不到能验这些的素材 —— 日期在 7 份图纸里一条都没有，
    斜排只有 br01 一条 38° 的纯尺寸，所以手工试的时候拿真实图纸
    根本看不到改动有没有生效。这张图用来一次看全：

      ① 横排 日期：2026年11月16日   → 验月份校验（原来被数字规则拦下留中文）
      ② 横排 总长2.79米            → 验长度统一 mm（原来留「2.79米」或半换算）
      ③ 竖排 上下调节100mm         → 验竖排不被判成横排、竖直写回
      ④ 斜排 半径35mm圆管（沿25°）→ 验斜排沿原角度写回、不压引线

    只造图不跑翻译 —— 用法是用户自己在界面上拖这份进去点「开始」。
    """
    os.makedirs(OUT, exist_ok=True)
    W, H = 1200, 700
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    px = 24                                  # 位图 24px → PDF 上 6pt，图纸小标注量级
    font = ImageFont.truetype(FONT_CN, px)

    # ① 日期（横排，左上）
    d.text((60, 40), "日期：2026年11月16日", font=font, fill=(0, 0, 0))
    # ② 长度（横排，日期下方）—— 原文用米，看译完是不是 2790mm
    d.text((60, 110), "总长2.79米", font=font, fill=(0, 0, 0))

    # ③ 竖排（右侧，紧贴一条竖线，模拟尺寸线旁标注）
    tC = _text_layer(px, "上下调节100mm", 90)
    img.paste(tC, (930, 300), tC)
    d.line([(912, 360), (912, 540)], fill=(0, 0, 0), width=2)

    # ④ 斜排（左下，沿 25° 引线）
    ax, ay = 150, 600
    bx = ax + 340 * math.cos(math.radians(-25))
    by = ay + 340 * math.sin(math.radians(-25))
    d.line([(ax, ay), (bx, by)], fill=(0, 0, 0), width=2)
    d.ellipse([ax - 8, ay - 8, ax + 8, ay + 8], outline=(0, 0, 0), width=2)
    tD = _text_layer(px, "半径35mm圆管", 25)
    img.paste(tD, (int(ax + 80), int(ay - 160)), tD)

    png = os.path.join(OUT, "accept_src.png")
    img.save(png)

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)      # A4 竖版
    page.insert_image(pymupdf.Rect(30, 40, 565, 352), filename=png)
    src = os.path.join(OUT, "验收件_源.pdf")
    doc.save(src)
    doc.close()
    print("验收件：", src)
    return src


def run(src):
    """跑一遍中→英，导出输出 PDF、对照 PNG 和每条的位置角度。"""
    import core.edition as EDITION
    from core import pdf_engine
    from core.translate import Translator, load_config

    with open(CFG, encoding="utf-8") as f:
        cfg = load_config()
        cfg.setdefault("llm", {})["api_key"] = json.load(f)["llm"]["api_key"]
    cfg.setdefault("translate", {})["src"] = "zh"
    cfg["translate"]["dst"] = "en"
    tr = Translator(cfg)

    pairs = []

    def one(text, lang):
        out = tr.translate_many([(text, lang)])[0]
        pairs.append((text, out))
        return out

    def batch(items):
        res = tr.translate_many(items)
        pairs.extend([(t, o) for (t, _l), o in zip(items, res)])
        return res

    dst = os.path.join(OUT, "slant_dst.pdf")
    stats = {}
    pdf_engine.process_pdf(src, dst, one, translate_batch=batch, stats_out=stats,
                           unit_mode="metric", render_preview=False,
                           ocr_full_pages=True, dst_lang=tr.dst_lang, reviewer=tr)
    print("\n翻译：")
    for a, b in pairs:
        print("   %-28r -> %r" % (a, b))
    print("\n未译：", stats.get("untranslated"))
    rep = stats.get("review")
    if rep is not None:
        print("复核：", rep.count_by_kind())
        for ln in rep.lines():
            print("  ", ln)

    # 对照图：把输出页渲染出来，看字是不是还沿着原来那个角度
    d = pymupdf.open(dst)
    pix = d[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=pymupdf.Rect(20, 30, 580, 370))
    pix.save(os.path.join(OUT, "slant_dst.png"))
    print("对照图：", os.path.join(OUT, "slant_dst.png"))
    return dst


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "make":
        make()
    elif len(sys.argv) > 1 and sys.argv[1] == "accept":
        make_accept()
    else:
        run(make())
