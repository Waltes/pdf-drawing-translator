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

# OCR 结果的语言判定用。中文图纸的识别结果里一个拉丁字母都没有，
# 原来那行「必须含 [A-Za-z]」的过滤会把它们全部当噪声扔掉 —— 中文转英文时
# OCR 通道等于整个失效。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def ocr_lang(text):
    """
    判断一条 OCR 文字该按哪种源语言送进翻译器。只返回 zh / en / none。

    判成 none 的（纯数字、纯单位、纯符号）不翻译：尺寸数字和零件序号
    原样保留才对，翻成 "three hundred" 是灾难。

    假名也并按 zh 处理：翻译器只认中英两种提示，判成 ja 会被当成
    「认不出的语种」，结果是不翻，比按中文处理更糟。
    """
    text = text or ""
    if _CJK_RE.search(text) or _KANA_RE.search(text):
        return "zh"
    if _LATIN_RE.search(text):
        return "en"
    return "none"


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


def find_image_regions(page, min_area=8000, force_full=False):
    """
    找出页面上的位图区域。
    面积太小的（图标、logo）跳过；其余全部处理，
    因为很多图纸会把「技术要求」整块贴成图片。

    force_full: 把整页当成一个区域处理。给「文字已转成曲线」的 PDF 用 ——
    这类文件（CAD 导出时勾了文字转曲）的文字全是矢量路径，
    既没有文字层、也没有位图对象，get_image_info() 返回空。
    常规流程一个区域都找不到，整页 OCR 是唯一还能碰到文字的路径。
    """
    if force_full:
        return [page.rect]

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

    # 离群像素占比：环带里有多少像素跟底色差得很远。
    # 为什么需要这个：图纸上大量标注是「红色部分」「灰色部分」这种，
    # 文字旁边紧挨着一块对应颜色的色块。环带必然采到那块颜色，
    # 标准差直接爆表，原来只看 std 就把它们全判成「背景太花」放弃了 ——
    # 实测一页 25 条标注因此有 16 条没翻，剩下的全是白底上的标题。
    # 底色本身仍是干净的（中位数落在白），只要离群像素是少数就不该放弃。
    dev = np.abs(ring.astype(int) - bg.astype(int)).sum(axis=1)
    far_ratio = float((dev > 90).mean())

    # 文字：文字框内部，取离背景最远的那部分
    inner = arr[ty0:ty1, tx0:tx1].reshape(-1, 3) if (tx1 > tx0 and ty1 > ty0) else ring
    if len(inner) == 0:
        return None
    dist = np.abs(inner.astype(int) - bg.astype(int)).sum(axis=1)
    thr = np.percentile(dist, 92)
    far = inner[dist >= max(thr, 30)]
    fg = np.median(far if len(far) else inner, axis=0)

    contrast = float(abs(float(bg.mean()) - float(fg.mean())))
    # 标准差超标「且」离群像素占多数，才说明真的压在花纹上；
    # 只是边上蹭到色块的放行，否则图上一半标注永远翻不了。
    if (bg_std > MAX_BG_STD and far_ratio > 0.45) or contrast < MIN_CONTRAST:
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
                        translate_batch=None, untranslated=None,
                        force_full=False):
    """
    处理一页里位图上的文字。

    used_rects:      文字层已经占用的区域，OCR 落在这里说明是重复识别，跳过
    report:          收集改写记录，格式与文字层一致
    translate_batch: 批量翻译函数，传了就把本页所有图片文字打包一次翻完
    untranslated:    收集翻不出来的条目，用于最终如实汇报覆盖率
    force_full:      整页当作一个位图区域处理（文字已转曲的 PDF 走这条路）

    返回改写条数
    """
    if not (HAS_OCR and HAS_NUMPY):
        if untranslated is not None:
            untranslated.append(("图片内文字", "未安装 OCR 组件，位图里的文字未处理"))
        return 0

    regions = find_image_regions(page, force_full=force_full)
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
            # 纯数字/编号（零件序号、尺寸数字）不翻译，它们只是指代。
            # 这里按 ocr_lang 判，不再要求「必须含拉丁字母」：中文图纸的识别结果
            # 一个字母都没有，按字母判会把整页中文全当噪声扔掉。
            hint = ocr_lang(text)
            if not text or hint == "none" or len(text) < 2:
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
            candidates.append({"text": text, "rect": txt_rect, "bg": bg,
                               "fg": fg, "hint": hint})

    if not candidates:
        return 0

    # 批量翻译（图片文字少但页多，逐条串行会把总时间拖长一倍）
    # 源的语种用每条自己的 hint，不再硬编码 "en"：中文图纸的标注传 en 进去，
    # 翻译器会判成「原文已是英文」直接原样返回，等于白跑一趟 OCR。
    if translate_batch:
        try:
            outs = translate_batch([(c["text"], c["hint"]) for c in candidates])
        except Exception:
            outs = [translate_fn(c["text"], c["hint"]) for c in candidates]
    else:
        outs = []
        for c in candidates:
            try:
                outs.append(translate_fn(c["text"], c["hint"]))
            except Exception:
                outs.append("")

    # 字体按译文语种选：中文用系统中文字体，英文用内置拉丁字体
    # （拉丁字形更接近图纸原有标注，嵌入体积也小）
    font_cjk = _get_cjk_font()
    font_latin = pymupdf.Font("helv")

    # 分两轮写：先把所有原文统一盖掉，再统一写译文。
    # 一轮一条地处理会互相打架 —— 相邻两行只隔一个多字高，后一条的填充
    # 必然压到前一条刚写好的英文上，实测底部那几行长句就是这么整行糊掉的。
    plans = []
    for c, out in zip(candidates, outs):
        if not out or not out.strip() or out.strip().upper() == c["text"].upper():
            if untranslated is not None:
                untranslated.append(("图片内文字", c["text"]))
            continue
        out = out.strip()
        font = font_cjk if _CJK_RE.search(out) else font_latin

        r = c["rect"]
        h = r.height
        # 横向余量压到 0.2 字高。原先是 0.6，英文长句伸出原框，
        # 伸出去那段下面还压着原文没被盖住，就成了叠影。
        # 但也不能把填充框跟着译文拉宽 —— 试过，个别 OCR 框一宽，
        # 整块图纸被涂白（左侧立柱和尺寸线被盖掉），比叠影更糟。
        # 折中：框不动，让译文缩字号写进框里。
        maxw = r.width + h * 0.2
        # 纵向外扩 0.32 字高：OCR 框贴着字形，笔画超出的部分全靠这里盖干净。
        # 分两轮写之后，外扩大一点也不会吃掉相邻行的译文了。
        pad = max(min(h * 0.32, 2.0), 0.6)
        fill = pymupdf.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad)
        # 异常保护：填充框最多是原框的 3.5 倍面积，再大说明这条 OCR 框本身不可信，
        # 宁可留下原文记进未译清单，也不能把图形涂掉
        if fill.width * fill.height > r.width * r.height * 3.5 + 40:
            if untranslated is not None:
                untranslated.append(("图片内文字", c["text"]))
            continue

        # 字号对齐原文字高度，放不下再缩；颜色沿用原文字色。
        # 英文标注普遍比中文窄也比中文矮，用同一个字号会显得偏小，
        # 乘 1.08 补一点观感；上限仍受文字框宽度约束
        size = h * (1.0 if _CJK_RE.search(out) else 1.08)
        width = font.text_length(out, fontsize=size)
        if width > maxw > 0:
            size = size * maxw / width
        plans.append((c, out, fill, size, font))

    # 第一轮：用采样出来的背景色盖掉原文（不能用白色，会留下刺眼方块）
    for c, _out, fill, _size, _font in plans:
        page.draw_rect(fill, color=None,
                       fill=tuple(float(v) / 255 for v in c["bg"]), width=0)

    # 第二轮：写译文
    changed = 0
    for c, out, _fill, size, font in plans:
        tw = pymupdf.TextWriter(page.rect)
        tw.append(pymupdf.Point(c["rect"].x0, c["rect"].y1), out,
                  font=font, fontsize=size)
        tw.write_text(page, color=tuple(float(v) / 255 for v in c["fg"]))
        report.append((None, c["text"], out, False))
        changed += 1

    return changed
