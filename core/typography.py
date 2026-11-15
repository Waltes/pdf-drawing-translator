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

# 可读下限：最终写进图纸的字，不小于这个值。
# 5.5pt 在 A3 上约 1.9mm 高，是车间照着图纸能认出的最低限度。
#
# 为什么需要一个「绝对值」而不是只看缩放比例：
# 实测这份中文图纸，标题是 16pt 的大字（框架正面图），译成英文后
# 在 70pt 宽的框里只能放到 8.8pt —— 按比例算是 55%，看着像「缩了一半」，
# 但 8.8pt 比正文标注还大，完全读得清。若一律按「不得低于原字的八成」卡，
# 这类大标题会被全部误杀、退回中文（实测 13 条里 12 条）。
# 反过来，7pt 的标注缩到 3.53pt 是 50%，比例看着和上面差不多，
# 但 3.53pt 打印出来就是一道灰线 —— 客户抱怨的正是这一种。
# 两者比例相近、可读性天差地别，所以判据必须落到绝对字号上。
MIN_READABLE = 5.5

# 字号缩放的**优先线**：缩到这个程度就该想办法救（折行、收紧数字），
# 而不是继续往下压。
SHRINK_FLOOR = 0.80

# 字号缩放比例的**保底线**。只对大字号起作用 —— 小字有 MIN_READABLE 兜着。
# 0.40 的意思：16pt 的标题最多缩到 6.4pt，30pt 的大标题最多到 12pt，
# 都还在可读范围内。再往下就不写，保留原文并如实汇报。
SHRINK_HARD = 0.40


def readable_floor(base_size):
    """
    这条标注允许缩到的最小字号。

    取「原文大小」与「可读下限」中较小的那个，两个方向都要顾：
      · 原文本就是 4pt 的小注 —— 译文保持原大小就行，不该因为不到 5.5 被拒；
      · 原文是 16pt 的标题 —— 也不许缩到 5.5 以下，低于这个就印不出来。
    所以门槛 = min(原字号, max(可读下限, 原字号 × 保底比例))。
    """
    if not base_size or base_size <= 0:
        return MIN_READABLE
    return min(base_size, max(MIN_READABLE, base_size * SHRINK_HARD))


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


# ---------------------------------------------------------------------------
# 数字写法收紧：把模型凭空写长的尺寸改回原件那么短
# ---------------------------------------------------------------------------
# 实测：模型译中文图纸时会给数值补尾零，连整数都不放过 ——
# 原文「3mm厚护角」译成 3.00mm，原文「1000mm长横杆」译成 1000.00mm，
# 原文「25*25*1.5mm」译成 25*25*1.50mm。每处白长 2~3 个字符。
#
# 这不只是排版问题：50.00 在工艺上表示公差等级 ±0.01，原文写的是 50，
# 补上尾零等于凭空给图纸加了精度要求，车间照这个加工是要多花钱的。
#
# 所以这里两头都做：提示词要求模型不补（治本），出口再收一道（兜底）。
# 兜底只敢动两类地方 —— 带单位的数值、× 连起来的尺寸链。
# 公差（±0.50）、半径（R0.50）、螺纹（M8×1.25）、千分位（1,000）都不碰：
# 那些地方的位数有含义，改了就是改要求。

# 尺寸链里的一个因子：数字（可带小数）+ 可选的毫米/厘米/米/英寸单位
_DIM_FACTOR = r"\d+(?:\.\d+)?\s*(?:mm|cm|in)?"
# 至少两个因子用 × / x / * 连起来，前后不能紧贴字母数字（M8×30 里的 8×30 不算）
_DIM_CHAIN = re.compile(
    r"(?<![\w.])(" + _DIM_FACTOR + r"(?:\s*[×xX*]\s*" + _DIM_FACTOR + r")+)(?![\w.])",
    re.IGNORECASE)
# 单个带单位的数值。前置排除 ± 和字母，公差与型号进不来。
# 只认「小数部分全是零」的那种（3.00mm、1000.00mm）——
# 这一类必定是模型格式化出来的：原文写 3，它写成 3.00。
# 像 101.60mm 这种是程序按英寸精确换算出来的（4" → 101.60mm），
# 那个 0 表示精度到 0.01，砍掉就变成 101.6，等于把精度说小了一档，
# 不能动。同理 1.20mm 也放过 —— 少省一个字符，换换算结果不被改，划算。
_DIM_UNIT = re.compile(r"(?<![\w.±])"
                       r"(\d+\.0+)(\s*)(mm|cm|in)(?![\w])", re.IGNORECASE)
# 拆因子用：数字 + 可选单位
_FACTOR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|cm|in)?", re.IGNORECASE)
# 乘号有多少种写法都归成一个，CAD 与模型混用时图上才整齐
_TIMES_RE = re.compile(r"\s*[×xX*]\s*")


def _trim_zeros(num):
    """
    2.50 → 2.5，3.00 → 3，1000.00 → 1000。

    只砍小数末尾的零。整数部分一个字符都不动 —— 那边的零是位数，
    1000 砍成 1 是把尺寸改错了一千倍。
    """
    if "." not in num:
        return num
    head, _, tail = num.partition(".")
    tail = tail.rstrip("0")
    return head if not tail else head + "." + tail


def _tighten_chain(m):
    """
    一条尺寸链：25*25*1.50mm → 25×25×1.5mm。

    中间那些重复单位（25mm×25mm×1.5mm）也在这里去掉，只留最后一个 ——
    这是工程制图的通行写法，同一行的尺寸共用一个单位。
    """
    factors = _FACTOR_RE.findall(m.group(1))
    if not factors:
        return m.group(0)
    unit = ""
    for _num, u in factors:
        if u:
            unit = u.lower()
    body = "×".join(_trim_zeros(n) for n, _u in factors)
    return body + unit


def tighten_numbers(text):
    """
    把模型写长的尺寸收回原件的长度。只改写法，不改任何数值大小。

    调用点在排版之前，所以改过之后「按框宽算字号」拿到的是真实的长度，
    不会因为一串 3.00 而被多压掉两成字号。
    """
    s = text or ""
    if not s or not any(ch.isdigit() for ch in s):
        return s
    s = _DIM_CHAIN.sub(_tighten_chain, s)
    # 单个带单位的数：3.00mm → 3mm。单位前的空格保持原样，
    # 这一层不管空格（那是 normalize 的活），只砍尾零
    s = _DIM_UNIT.sub(lambda m: _trim_zeros(m.group(1)) + m.group(2) + m.group(3), s)
    return s
