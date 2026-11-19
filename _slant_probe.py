# -*- coding: utf-8 -*-
"""探测：图纸里到底有多少条是「斜着写」的，角度多少。

为什么要先量：斜排跟竖排不是一个动作。
    竖排 —— 轴向固定（竖直），只缩字号就能装进上下界限；
    斜排 —— 轴向本身是斜的，得先拿到角度，才能「沿斜轴量界限 + 沿斜轴排版」。
而角度信息其实一直在：RapidOCR 的检测框是四边形，斜排文字的框就是斜的。
现有代码只取 min/max 拼成轴对齐矩形，顺手把角度丢了 —— 所以要量一下，
这个四边形到底靠不靠得住（噪声大的话角度就没法用）。

用法：
    python _slant_probe.py            # 全部图纸
    python _slant_probe.py hulan      # 只跑 key 里含 hulan 的
"""
import glob
import json
import math
import os
import sys

sys.path.insert(0, ".")

import pymupdf

from core import image_text as IT

D = r"C:\Users\stanl\Desktop" + "\\"
OUT = "_batch"


def quad_angle(box):
    """四边形上边（p0→p1）的方向角，返回 -90~90。

    OCR 给的点序是「左上、右上、右下、左下」顺时针。取 p0→p1 这一条边，
    它跟文字基线平行，就是文字走向。角度按屏幕坐标算（y 向下），
    所以「从下往上斜写」是负角。
    """
    (x0, y0), (x1, y1) = box[0], box[1]
    a = math.degrees(math.atan2(y1 - y0, x1 - x0))
    if a > 90:
        a -= 180
    elif a <= -90:
        a += 180
    return a


def probe(path):
    doc = pymupdf.open(path)
    rows = []
    for pno in range(doc.page_count):
        page = doc[pno]
        regions = IT.find_image_regions(page, force_full=True)
        for clip in regions:
            zoom = IT.ZOOM_LARGE if clip.width * clip.height > IT.LARGE_AREA else IT.ZOOM
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
            if pix.width < 40 or pix.height < 40:
                continue
            tmp = os.path.join(IT.DATA_DIR, "_slant_tmp.png")
            pix.save(tmp)
            try:
                result, _ = IT._get_ocr()(tmp)
            except Exception:
                continue
            for item in (result or []):
                box, raw, score = item[0], item[1], item[2]
                try:
                    score = float(score)
                except Exception:
                    continue
                if score < IT.MIN_CONF:
                    continue
                text = IT.fix_ocr_spacing(IT.clean_ocr_text(raw))
                if not text or len(text) < 2:
                    continue
                a = quad_angle(box)
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                bw = (max(xs) - min(xs)) / zoom
                bh = (max(ys) - min(ys)) / zoom
                rows.append(dict(page=pno + 1, text=text, angle=round(a, 1),
                                 bw=round(bw, 1), bh=round(bh, 1)))
    doc.close()
    return rows


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sorted(glob.glob(os.path.join(OUT, "*.pdf")))
    for f in files:
        key = os.path.basename(f)
        if want and want.lower() not in key.lower():
            continue
        rows = probe(f)
        # 只报「明显斜」的：|角度| 超过 5 度且不到 85 度（竖排另有路径）
        slant = [r for r in rows if 5 < abs(r["angle"]) < 85]
        vert = [r for r in rows if abs(r["angle"]) >= 85]
        print("=" * 70)
        print("%s  共 %d 条  斜排 %d 条  竖排 %d 条"
              % (key, len(rows), len(slant), len(vert)))
        for r in slant:
            print("   p%-2d %6.1f° %5.1f×%-5.1f %s"
                  % (r["page"], r["angle"], r["bw"], r["bh"], r["text"][:40]))
    # 明细落盘，后续比对用
    allrows = {}
    for f in files:
        allrows[os.path.basename(f)] = probe(f)
    with open("_slant_probe.json", "w", encoding="utf-8") as fp:
        json.dump(allrows, fp, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
