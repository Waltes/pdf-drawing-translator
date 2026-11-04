"""
图片内文字翻译

解决什么问题：
  有些图纸（尤其是带 3D 渲染图的）会把零件标注直接烤进位图里，
  文字层根本不存在，常规的「提取文字 → 擦除 → 重写」完全够不着。
  这份 BR-01 图纸第 1、2 页就是典型：Collar、Brace tube、Lock pin 全在位图内。

怎么解决：
  在位图里 OCR 找到文字 → 从文字四周的真实背景采样颜色 →
  用背景色盖掉原文字 → 用原文字的颜色写中文。

为什么不用白色块盖：
  踩过的坑。这些标注不是「白底标签」，而是深色文字直接压在浅灰围栏上，
  盖白块会留下刺眼的方块，一眼就能看出被改过。必须用周围真实颜色填充。

安全边界：
  背景太花（有图案、强纹理）的地方不动，宁可漏翻也不破坏图形；
  翻不出来的会记进未译清单，如实报给用户，不假装成功。
"""

import os
import re

import pymupdf

from .paths import DATA_DIR

try:
    from rapidocr_onnxruntime import RapidOCR
    HAS_OCR = True
except Exception:
    HAS_OCR = False

try:
    import numpy as np
    HAS_NUMPY = True
except Exception:
    HAS_NUMPY = False

ZOOM = 4              # 位图渲染倍率，4 倍能让 6pt 的小字被稳定识别
ZOOM_LARGE = 3        # 整页级别的大图用 3 倍，兼顾识别率与速度
LARGE_AREA = 400000   # 超过这个面积的区域视为「大图」
MIN_CONF = 0.50       # OCR 置信度下限，太低的多半是图形噪点
MAX_BG_STD = 46       # 背景色标准差上限：背景太花就不动，免得盖出色块
MIN_CONTRAST = 34     # 文字与背景的最小亮度差，太接近说明识别到的是图形噪点

_ocr_engine = None
_font_cjk = None


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    return _ocr_engine


def _get_cjk_font():
    global _font_cjk
    if _font_cjk is None:
        for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
                     "C:/Windows/Fonts/simsun.ttc"):
            if os.path.exists(path):
                _font_cjk = pymupdf.Font(fontfile=path)
                break
        else:
            _font_cjk = pymupdf.Font("cjk")
    return _font_cjk


def find_image_regions(page, min_area=8000):
    """
    找出页面上的位图区域。
    面积太小的（图标、logo）跳过；其余全部处理，
    因为很多图纸会把「技术要求」整块贴成图片。
    """
    regions = []
    try:
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"])
            # 和页面求交，防止图片超出页面边界
            r &= page.rect
            if r.is_empty:
                continue
            if r.width * r.height >= min_area:
                regions.append(r)
    except Exception:
        pass
    # 大图优先，避免小图先盖住大图里的内容
    regions.sort(key=lambda r: -(r.width * r.height))
    return regions


def sample_colors(arr, rect, zoom, clip):
    """
    从文字四周的环带里采样背景色和文字色。

    做法：
      背景 = 文字框外一圈（不含文字本身）的中位数颜色，这样才是真正的底色；
      文字 = 文字框内偏离背景最远的那部分像素的中位数，保证沿用原来的字色。
    深浅背景都支持：底色亮就取最暗的当字色，底色暗就取最亮的。

    返回 (背景RGB, 文字RGB, 背景标准差)；背景太花或对比度不足返回 None。
    """
    h = max(rect.height, 1.0)
    pad = pymupdf.Rect(rect.x0 - h * 0.45, rect.y0 - h * 0.42,
                       rect.x1 + h * 0.45, rect.y1 + h * 0.42)

    def to_px(r):
        return (max(int((r.x0 - clip.x0) * zoom), 0),
                max(int((r.y0 - clip.y0) * zoom), 0),
                min(int((r.x1 - clip.x0) * zoom), arr.shape[1]),
                min(int((r.y1 - clip.y0) * zoom), arr.shape[0]))

    px0, py0, px1, py1 = to_px(pad)
    tx0, ty0, tx1, ty1 = to_px(rect)
    if px1 - px0 < 6 or py1 - py0 < 6:
        return None

    outer = arr[py0:py1, px0:px1]
    if outer.size == 0:
        return None

    # 背景：外框里挖掉文字框，剩下的就是纯背景
    mask = np.ones(outer.shape[:2], dtype=bool)
    ix0, iy0 = max(tx0 - px0, 0), max(ty0 - py0, 0)
    ix1, iy1 = min(tx1 - px0, outer.shape[1]), min(ty1 - py0, outer.shape[0])
    if ix1 > ix0 and iy1 > iy0:
        mask[iy0:iy1, ix0:ix1] = False
    ring = outer[mask].reshape(-1, 3)
    if len(ring) < 12:
        ring = outer.reshape(-1, 3)

    bg = np.median(ring, axis=0)
    bg_std = float(np.std(ring, axis=0).mean())

    # 文字：文字框内部，取离背景最远的那部分
    inner = arr[ty0:ty1, tx0:tx1].reshape(-1, 3) if (tx1 > tx0 and ty1 > ty0) else ring
    if len(inner) == 0:
        return None
    dist = np.abs(inner.astype(int) - bg.astype(int)).sum(axis=1)
    thr = np.percentile(dist, 92)
    far = inner[dist >= max(thr, 30)]
    fg = np.median(far if len(far) else inner, axis=0)

    contrast = float(abs(float(bg.mean()) - float(fg.mean())))
    if bg_std > MAX_BG_STD or contrast < MIN_CONTRAST:
        return None
    return bg, fg, bg_std


def clean_ocr_text(text):
    """收拾 OCR 的脏活：各种破折号会被识别成奇怪的字符"""
    text = (text or "").strip()
    text = text.replace("—", "-").replace("–", "-").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s{2,}", " ", text)
    return text


_VOCAB = None


def _get_vocab():
    """拿术语库里的英文词表，用来还原 OCR 丢掉的空格"""
    global _VOCAB
    if _VOCAB is None:
        try:
            from .dict_engine import Glossary
            _VOCAB = set(Glossary().en2zh.keys())
        except Exception:
            _VOCAB = set()
    return _VOCAB


def fix_ocr_spacing(text):
    """
    OCR 经常把英文单词之间的空格吃掉（Brace tube → Bracetube、Fence foot → Fencefoot），
    导致词典查不到、整条被当成"没变化"跳过。
    这里用术语库词表做最长匹配，把连写串切回正常的词。切不出来就原样返回，不乱动。
    """
    t = (text or "").strip()
    if " " in t or len(t) < 5 or not re.fullmatch(r"[A-Za-z]+", t):
        return text
    vocab = _get_vocab()
    if not vocab or t.upper() in vocab:
        return text

    pieces, i = [], 0
    while i < len(t):
        for size in range(min(24, len(t) - i), 2, -1):
            if t[i:i + size].upper() in vocab:
                pieces.append(t[i:i + size])
                i += size
                break
        else:
            pieces.append(t[i])
            i += 1

    joined = " ".join(pieces)
    if " " not in joined or any(len(p) == 1 for p in pieces):
        return text
    return joined


def process_page_images(page, translate_fn, used_rects, report,
                        translate_batch=None, untranslated=None):
    """
    处理一页里位图上的文字。

    used_rects:      文字层已经占用的区域，OCR 落在这里说明是重复识别，跳过
    report:          收集改写记录，格式与文字层一致
    translate_batch: 批量翻译函数，传了就把本页所有图片文字打包一次翻完
    untranslated:    收集翻不出来的条目，用于最终如实汇报覆盖率

    返回改写条数
    """
    if not (HAS_OCR and HAS_NUMPY):
        if untranslated is not None:
            untranslated.append(("图片内文字", "未安装 OCR 组件，位图里的文字未处理"))
        return 0

    regions = find_image_regions(page)
    if not regions:
        return 0

    candidates = []
    # 临时图必须落在可写目录。打包安装后程序目录是只读的（Program Files），
    # 写在那儿会直接抛 PermissionError，把整页 OCR 拖垮。
    tmp = os.path.join(DATA_DIR, "_ocr_tmp.png")

    for clip in regions:
        zoom = ZOOM_LARGE if clip.width * clip.height > LARGE_AREA else ZOOM
        try:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
        except Exception:
            continue
        if pix.width < 40 or pix.height < 40:
            continue

        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        rgb = arr[:, :, :3] if pix.n >= 3 else np.repeat(arr[:, :, :1], 3, axis=2)
        rgb = rgb.astype(int)

        pix.save(tmp)
        try:
            result, _ = _get_ocr()(tmp)
        except Exception:
            continue

        for item in (result or []):
            box, raw_text, score = item[0], item[1], item[2]
            try:
                score = float(score)
            except Exception:
                score = 0.0
            if score < MIN_CONF:
                continue

            text = fix_ocr_spacing(clean_ocr_text(raw_text))
            # 纯数字/编号（零件序号）不翻译，它们只是指代
            if not text or not re.search(r"[A-Za-z]", text) or len(text) < 2:
                continue

            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            txt_rect = pymupdf.Rect(
                clip.x0 + min(xs) / zoom, clip.y0 + min(ys) / zoom,
                clip.x0 + max(xs) / zoom, clip.y0 + max(ys) / zoom)

            # 文字层已经处理过的区域不要重复动
            center = pymupdf.Point((txt_rect.x0 + txt_rect.x1) / 2,
                                   (txt_rect.y0 + txt_rect.y1) / 2)
            if any(r.contains(center) for r in used_rects):
                continue
            # 同一页重复识别出来的相同位置，去重
            if any(abs(c["rect"].y0 - txt_rect.y0) < 1.0 and
                   abs(c["rect"].x0 - txt_rect.x0) < 1.0 for c in candidates):
                continue

            sampled = sample_colors(rgb, txt_rect, zoom, clip)
            if not sampled:
                # 背景太花或对比度不足，不硬来；记下来如实告知
                if untranslated is not None:
                    untranslated.append(("图片内文字", text))
                continue

            bg, fg, _std = sampled
            candidates.append({"text": text, "rect": txt_rect, "bg": bg, "fg": fg})

    if not candidates:
        return 0

    # 批量翻译（图片文字少但页多，逐条串行会把总时间拖长一倍）
    if translate_batch:
        try:
            outs = translate_batch([(c["text"], "en") for c in candidates])
        except Exception:
            outs = [translate_fn(c["text"], "en") for c in candidates]
    else:
        outs = []
        for c in candidates:
            try:
                outs.append(translate_fn(c["text"], "en"))
            except Exception:
                outs.append("")

    font = _get_cjk_font()
    changed = 0
    for c, zh in zip(candidates, outs):
        if not zh or not zh.strip() or zh.strip().upper() == c["text"].upper():
            if untranslated is not None:
                untranslated.append(("图片内文字", c["text"]))
            continue
        zh = zh.strip()

        r = c["rect"]
        bg, fg = c["bg"], c["fg"]
        # 填充范围比文字框略外扩，确保原文字被盖干净（外扩量按字高比例，小字不会被吃掉笔画）
        pad = max(min(r.height * 0.10, 0.8), 0.35)
        fill = pymupdf.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad)
        # 用采样出来的背景色填充，而不是盖白色——才不会留下刺眼的方块
        page.draw_rect(fill, color=None, fill=tuple(float(v) / 255 for v in bg), width=0)

        # 写中文：字号对齐原文字高度，放不下再缩；颜色沿用原文字色
        h = r.height
        size = h
        width = font.text_length(zh, fontsize=size)
        maxw = (r.width + h * 0.6) - 1
        if width > maxw > 0:
            size = size * maxw / width
        tw = pymupdf.TextWriter(page.rect)
        tw.append(pymupdf.Point(r.x0, r.y1), zh, font=font, fontsize=size)
        tw.write_text(page, color=tuple(float(v) / 255 for v in fg))

        report.append((None, c["text"], zh, False))
        changed += 1

    return changed
