# -*- coding: utf-8 -*-
"""
文字排版规则：字号怎么定、词距怎么留、标点怎么写

为什么单独成一个模块：
    这是「怎么把字写得像原件」的规则集合，文字层（pdf_engine）和位图层
    （image_text）都要用，两边又不许互相 import（会成环）。
    和 core/textfit.py 的分工：textfit 管「放不放得下」，本模块管
    「写得对不对」—— 字号取多少、字与字之间该不该有空格、标点用全角还是半角。

三条规则都是从实际返工里长出来的：

1. 字号要按「字面高度」反算，不能把字面高度当字号。
   图纸看着协不协调，对齐的是笔画占的高度，不是字号那个数字。
   同一条标注，全大写的（SURFACE TREATMENT）和带下伸部的
   （Hot-dip galvanized）字面高度差三成；用一个固定系数去乘，
   一处写成偏小、一处写成偏大，客户一眼就看出是后贴上去的。

2. 词距不能丢。CAD 导出常把一个句子切成好几个 span，中间没有空格字符，
   只有横坐标上的一段空隙；OCR 也常把窄空格吃掉。不按坐标把空格补回来，
   QTY 750 panels 就会写成 QTY750panels。

3. 中文用全角标点，西文标点后面带一个空格。半角逗号句号混在中文里，
   是「机翻味」最明显的地方；而 "TREATMENT:Hot-dip" 这种冒号后没空格，
   是英文排版里最扎眼的一处。
"""

import re

# 汉字、假名、谚文：这些文字之间不能加空格（「门板」不是「门 板」）
_WIDE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

# 汉字字面高度与字号的比。
# 雅黑实测 0.965（含一圈抗锯齿边缘），扣掉边缘后的几何值约 0.94；
# 实际写回后按 0.96 标定最贴原图（量出来的新旧墨迹高度比 1.00）。
# 不同中文字体差不到 3%，统一按 0.96 算，误差不到半个像素。
CJK_INK_RATIO = 0.96

# 抗锯齿会让实测墨迹比几何轮廓高一点点，补偿掉，免得字号偏小
AA_MARGIN = 0.02

# 西文墨迹比例的合理区间。低于 0.35 说明这串只有一个逗号之类的符号，
# 高于 0.95 说明取到了坏数据 —— 两种都退回一个中性的默认值
_LATIN_RATIO_MIN = 0.35
_LATIN_RATIO_MAX = 0.95

# 相邻两个 span 的横向空隙超过 0.16 个字宽，就当成原本有一个空格。
# 0.16 的来源：中文空格 0.5 字宽、西文空格 0.28 字宽，
# 而 CAD 里词与词之间常留 0.2~0.3；取 0.16 既能抓住词距，
# 又不会把「1020」被切成「10」+「20」这种字内的缝当成空格。
GAP_EM = 0.16

# 写回去的最小字号。低于它能看清但没法读，宁可这处保留原文
MIN_SIZE = 3.5


def is_wide(ch):
    """这个字符是不是方块字（汉字/假名/谚文）"""
    return bool(_WIDE_RE.match(ch or ""))


def ink_ratio(font, text):
    """
    这串字用这个字体写出来，墨迹高度是字号的几成。

    量法：逐个字符取字形轮廓的上下边界，取并集。比「按字号乘一个固定系数」
    准得多 —— SURFACE TREATMENT（0.76）和 Hot-dip galvanized（0.95）
    差了三成，一条标注一个系数才能跟原件对齐。
    """
    s = (text or "").strip()
    if not s:
        return 0.72
    if _WIDE_RE.search(s):
        return CJK_INK_RATIO
    lo = hi = None
    for ch in set(s):
        if ch.isspace():
            continue
        try:
            r = font.glyph_bbox(ord(ch))
        except Exception:
            continue
        if r is None or r.is_empty:
            continue
        lo = r.y0 if lo is None else min(lo, r.y0)
        hi = r.y1 if hi is None else max(hi, r.y1)
    if lo is None or hi is None:
        return 0.72
    return max(_LATIN_RATIO_MIN, min(_LATIN_RATIO_MAX, (hi - lo) + AA_MARGIN))


def size_for_ink(font, text, want_ink, floor=MIN_SIZE):
    """
    求「写出来墨迹高度正好等于 want_ink」所需的字号。

    want_ink 是原件那一行字的实际墨迹高度（由渲染后数暗像素行得到），
    不是 OCR 框高 —— 框自带留白，拿框高当字高会写出偏大一圈的字。
    """
    if want_ink is None or want_ink <= 0:
        return floor
    return max(floor, want_ink / ink_ratio(font, text))


def _span_box(sp):
    """取 span 的矩形，取不到返回 None"""
    b = sp.get("bbox")
    if not b:
        return None
    try:
        return (b[0], b[1], b[2], b[3])
    except (TypeError, IndexError):
        return None


def join_spans(spans):
    """
    把一行里的若干 span 拼回一整串字，丢掉的字距按坐标补回来。

    为什么不能用 "".join：CAD 导出的 PDF 里，一句 QTY 750 panels
    经常是三个 span，彼此之间**没有空格字符**，空格只体现为横坐标的空隙。
    直接拼接就成了 QTY750panels —— 这条串送到翻译器里，模型会把它
    当成一个型号，原样返回，于是图上留下一串粘在一起的英文。
    """
    out = ""
    prev_sp, prev_text = None, ""
    for sp in spans:
        t = sp.get("text") or ""
        if not t:
            continue
        if prev_sp is not None and t:
            last, first = prev_text[-1:], t[:1]
            glued = (last and first and not last.isspace()
                     and not first.isspace() and not is_wide(last)
                     and not is_wide(first))
            if glued:
                a, b = _span_box(prev_sp), _span_box(sp)
                if a and b:
                    size = float(sp.get("size") or 0) or max(b[3] - b[1], 1.0)
                    if b[0] - a[2] > GAP_EM * size:
                        out += " "
        out += t
        prev_sp, prev_text = sp, t
    return out


# 连续空白（含不换行空格、零宽空格）压成一个半角空格
_WS_RE = re.compile(r"[ \t\u00a0\u2000-\u200b\u3000]+")
# 右括号、右引号、逗号句号前的空格是错的
_SPACE_BEFORE = re.compile(r"\s+([,.;:!?%)\]])")
# 左括号、左引号后面的空格是错的
_SPACE_AFTER_OPEN = re.compile(r"([(\[])\s+")
# 逗号、分号、冒号后面紧跟字母或汉字 —— 缺一个空格。
# 刻意不管句点和冒号在数字里的情况（5.00mm、1:2），只认这两处标点。
_MISSING_SPACE = re.compile(r"([,;:])(?=[A-Za-z\u3400-\u9fff])")

# 中文语境下的标点替换：只在中文字符旁边替换，
# 数字里的逗号（1,000）、型号里的点（5.00）一律不动
_CN_COMMA = re.compile(r"(?<=[\u4e00-\u9fff])\s*,\s*")
_CN_SEMI = re.compile(r"(?<=[\u4e00-\u9fff])\s*;\s*")
_CN_COLON = re.compile(r"(?<=[\u4e00-\u9fff])\s*:\s*")
_CN_PERIOD = re.compile(r"(?<=[\u4e00-\u9fff])\.(?=\s|$|[\u4e00-\u9fff])")
_CN_PAREN = re.compile(r"\(([^()]{1,40})\)")
_CN_HAS_WIDE = re.compile(r"[\u4e00-\u9fff]")

# 中英之间该留的空隙。两侧都不是标点才加，免得出现「，  5mm」这种写法
_CJK_LATIN = re.compile(
    r"(?<=[\u4e00-\u9fff])(?=[A-Za-z0-9])"
    r"|(?<=[A-Za-z0-9%])(?=[\u4e00-\u9fff])")


def normalize(text):
    """
    写入前的规范化：空格、标点。

    只做「不改字义、只改写法」的调整：压空格、补空格、中文换全角标点。
    不动大小写、不动缩写、不动数字格式 —— 那些属于翻译的职责，
    排版这一层动它们就是越权，容易把型号改坏。
    """
    s = _WS_RE.sub(" ", (text or "")).strip()
    if not s:
        return s
    s = _SPACE_AFTER_OPEN.sub(r"\1", s)
    s = _SPACE_BEFORE.sub(r"\1", s)
    s = _MISSING_SPACE.sub(r"\1 ", s)
    if _CN_HAS_WIDE.search(s):
        s = _CN_COMMA.sub("，", s)
        s = _CN_SEMI.sub("；", s)
        s = _CN_COLON.sub("：", s)
        s = _CN_PERIOD.sub("。", s)
        # 括号里是中文内容才换全角；括号里是英文缩写（如 (mm)）保持半角
        s = _CN_PAREN.sub(
            lambda m: ("（" + m.group(1) + "）")
            if _CN_HAS_WIDE.search(m.group(1)) else m.group(0), s)
    return s


def space_cjk_latin(text):
    """
    中文与西文/数字之间留一个半角空隙。

    这是中文排版的通行做法（「厚5mm塑料板」读起来挤，「厚 5mm 塑料板」顺）。
    调用方要按框宽决定用不用：窄框里先把字塞进去，空隙是锦上添花。
    """
    return _CJK_LATIN.sub(" ", text or "")


_GLUED_RE = re.compile(r"[A-Za-z0-9]+")


def _in_vocab(piece, vocab):
    """
    这个词算不算「认得的词」，带复数还原。

    术语表收单数（PANEL），图纸上写复数（QTY750panels），
    不做还原就永远拆不动，客户图上那串英文也就一直留着。
    只用于判断，不改写 —— 原文是复数，拆出来仍然是复数。
    """
    u = piece.upper()
    if u in vocab:
        return True
    for cand in ((u[:-3] + "Y") if u.endswith("IES") and len(u) > 4 else None,
                 u[:-2] if u.endswith("ES") and len(u) > 3 else None,
                 u[:-1] if u.endswith("S") and len(u) > 2 else None):
        if cand and cand in vocab:
            return True
    return False


def split_glued(text, vocab, max_word=24):
    """
    把 OCR 粘成一串的文本拆回词：QTY750PANELS → QTY 750 PANELS。

    只在「拆出来的每一段都是词表里的词，或者是一段数字」时才动手，
    拆不全就返回 None，交回原样 —— 宁可让翻译器看一串粘连的词，
    也不能把它切错（把 FENCE 切成 FE + NCE 这种，翻出来是另一个意思）。

    数字整段当一块，不再细分：QTY750 拆成 QTY + 750 对，
    拆成 QT + Y + 7 + 50 就荒谬了。
    """
    t = (text or "").strip()
    if not t or " " in t or len(t) < 5:
        return None
    if not _GLUED_RE.fullmatch(t):
        return None
    n = len(t)
    # best[i] = 前 i 个字符的最优拆法（块数最少）
    best = [None] * (n + 1)
    best[0] = []
    for i in range(n):
        if best[i] is None:
            continue
        # 数字串整块走
        j = i
        while j < n and t[j].isdigit():
            j += 1
        if j > i and (best[j] is None or len(best[j]) > len(best[i]) + 1):
            best[j] = best[i] + [t[i:j]]
        # 字母词按词表最长匹配
        for k in range(min(max_word, n - i), 1, -1):
            piece = t[i:i + k]
            if piece.isdigit() or not _in_vocab(piece, vocab):
                continue
            if best[i + k] is None or len(best[i + k]) > len(best[i]) + 1:
                best[i + k] = best[i] + [piece]
    parts = best[n]
    if not parts or len(parts) < 2:
        return None
    return " ".join(parts)


def fix_word_gaps(text):
    """
    只补「词与词之间丢掉的那个空格」，不碰别的。

    和 normalize 的分工：normalize 会把中文标点换成全角、动括号，
    那是给**译文**用的。原文只该做这一件事 —— 补空格。
    原文被改了标点，翻译缓存 key 就变了，之前译过的条目要重新花钱。

    实测来源：客户图上 SURFACE TREATMENT:Hot-dip galvanized 的冒号
    后面直接贴着字母，两个短语粘成一条，模型读成一段规格说明原样返回。
    """
    s = _WS_RE.sub(" ", (text or "")).strip()
    if not s:
        return s
    return _MISSING_SPACE.sub(r"\1 ", s)


def split_glued_text(text, vocab):
    """
    把整条文字里粘在一起的词拆开，其余部分原样保留。

    为什么需要这一层：split_glued 只认「整条都是字母数字」的串，
    而图纸上一条标注常是 QTY750 panels 这种「一截粘连 + 一个空格 + 一个词」。
    只对整条判，这种混合形态永远判不出来，客户图上那串英文就一直留着。

    按空白切成词，逐个词尝试，任何一个词被切开就采用新串。
    """
    t = text or ""
    if not t or not vocab:
        return t
    out, changed = [], False
    for word in t.split(" "):
        cut = split_glued(word, vocab) if word else None
        if cut:
            out.append(cut)
            changed = True
        else:
            out.append(word)
    return " ".join(out) if changed else t
