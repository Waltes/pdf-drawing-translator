# -*- coding: utf-8 -*-
"""
合成样本测试：不需要客户的原始文件，把「客户截图里那几种坏法」造出来跑一遍。

客户报的现象（第一版截图）：QTY750 panels 粘连、SURFACE TREATMENT 字挤在一起、
个别字变形。这些症状有三种可能的成因，逐一造样复现：

  A. 一句话被 CAD 切成多个 span、中间只有坐标空隙没有空格字符 → 拼成 QTY750panels
  B. 上游已经是粘连串（OCR 或旧版缓存）→ 术语表查不到，原样返回英文
  C. 文字被表格线/下划线穿过 → 旧逻辑判「压线」放弃，英文留在图上

样本一次性造好，跑真实的 process_pdf 主流程（翻译函数用术语表代替模型），
然后按像素和文字层双重验收。
"""
import os
import sys

import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import dict_engine as de   # noqa: E402  术语表，替代模型翻译
from core import pdf_engine          # noqa: E402  被测主流程
from core import typography as ty    # noqa: E402

OUT = "_synth"

_GL = de.Glossary()


def lookup(text, lang="zh"):
    """用术语表当翻译器：走它真实的 n-gram 最长匹配，未命中的词原样保留。"""
    t = (text or "").strip()
    if not t:
        return t
    out, _miss = de.en_to_zh(t, _GL)
    return out or t


def span_block(page, spans, base_pt, size=9):
    """
    按 span 列表往页面上写字。
    spans = [(文本, 与上一段的额外横向间隙)]，间隙只体现在坐标上，不写空格字符 —— 
    这正是 CAD 导出的真实形态。
    """
    x = base_pt[0]
    for text, gap in spans:
        page.insert_text((x, base_pt[1]), text, fontsize=size, fontname="helv")
        x += pymupdf.get_text_length(text, fontname="helv", fontsize=size) + gap


def build():
    os.makedirs(OUT, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=420)

    # 标题
    page.insert_text((40, 40), "SYNTHETIC TITLE BLOCK SAMPLES", fontsize=13, fontname="helv")

    # A：三个 span，中间只有坐标空隙（2.2pt ≈ 0.24em）
    page.insert_text((40, 90), "A  span-split", fontsize=7, fontname="helv", color=(0.5, 0.5, 0.5))
    span_block(page, [("QTY", 0), ("750", 2.2), ("panels", 2.2)], (150, 90))

    # B：单个 span 里就是粘连串
    page.insert_text((40, 125), "B  glued-string", fontsize=7, fontname="helv", color=(0.5, 0.5, 0.5))
    page.insert_text((150, 125), "QTY750panels", fontsize=9, fontname="helv")

    # C：带下划线的标题（表格线紧贴文字底部）
    page.insert_text((40, 160), "C  underlined", fontsize=7, fontname="helv", color=(0.5, 0.5, 0.5))
    page.insert_text((150, 160), "SURFACE TREATMENT", fontsize=9, fontname="helv")
    page.draw_line((150, 162.5), (268, 162.5), color=(0, 0, 0), width=0.5)

    # D：窄框里塞长句（标题栏常见格宽不够）
    page.insert_text((40, 200), "D  narrow-cell", fontsize=7, fontname="helv", color=(0.5, 0.5, 0.5))
    page.insert_text((150, 200), "DRAWING NO.:SB-2024-001-REV", fontsize=9, fontname="helv")
    page.draw_rect(pymupdf.Rect(145, 188, 250, 206), color=(0, 0, 0), width=0.5)

    # E：混排 + 下伸部（g/y/p），验证同字号下字高一致。两行间隔 30pt，互不侵入
    page.insert_text((40, 250), "E  caps vs desc", fontsize=7, fontname="helv", color=(0.5, 0.5, 0.5))
    page.insert_text((150, 250), "SURFACE TREATMENT", fontsize=9, fontname="helv")
    page.insert_text((150, 280), "Hot-dip galvanized", fontsize=9, fontname="helv")

    src = os.path.join(OUT, "synth_src.pdf")
    doc.save(src)
    doc.close()
    return src


def ink_heights(path, rects, zoom=8):
    """量一组矩形内的墨迹高度（pt）。用来判断「一行字有没有一大一小」。"""
    import numpy as np
    d = pymupdf.open(path)
    pg = d[0]
    out = []
    for r in rects:
        clip = pymupdf.Rect(r)
        clip = pymupdf.Rect(clip.x0 - 3, clip.y0 - 3, clip.x1 + 3, clip.y1 + 3) & pg.rect
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, colorspace="gray")
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        rows = np.where((a < 180).any(axis=1))[0]
        out.append((rows[-1] - rows[0] + 1) / zoom if len(rows) else 0.0)
    d.close()
    return out


def main():
    src = build()
    dst = os.path.join(OUT, "synth_dst.pdf")
    stats = {}
    pdf_engine.process_pdf(src, dst, lookup, render_preview=False,
                           scan_images=False, ocr_full_pages=False,
                           stats_out=stats, verify=False, detect_outlined=False)
    d = pymupdf.open(dst)
    text = d[0].get_text()
    print("=== 译文页文字层 ===")
    for line in [x for x in text.splitlines() if x.strip()]:
        print("   ", line)
    print("=== 未译清单 ===")
    for item in stats.get("untranslated", []):
        print("   ", item)
    print("=== 残留英文检查 ===")
    bad = []
    for needle in ("QTY750", "QTY 750 PANELS", "SURFACE TREATMENT", "Hot-dip"):
        if needle.lower() in text.lower():
            bad.append(needle)
            print("   [残留]", needle)
    if not bad:
        print("    无残留")
    d.close()

    # 字高一致性：E 组两行（全大写 vs 带下伸部）
    h = ink_heights(dst, [(150, 200, 275, 216), (150, 214, 275, 230)])
    print("=== E 组字高 ===")
    print("   全大写行 =%.2fpt   下伸部行 =%.2fpt   比值=%.2f" % (h[0], h[1], h[1] / max(h[0], 0.01)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
