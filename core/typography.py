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

# 英文下伸部（y g p q j）占字号的比例。Helvetica 实测 0.212，取 0.22。
#
# 基线不能直接落在框底：落了，下伸部就掉到框外，压在框下面的图线或剖面线上。
# 客户截图里「Grey parts」那一条就是这么来的 —— 中文「灰色部分」没有下伸部，
# 英文有，同一个框底立刻现形。竖排文字用不到这个值。
DESCENDER = 0.22

# 字身相对基线的高度（cap height + 少量 ascender 余量），用于算写字矩形的高度。
# Helvetica 的 cap height 是 0.717，小写 ascender 到 0.75，这里给 0.80 留余量。
INK_TOP = 0.80

# ---- 同类文字同字号 ----
#
# 原件里同样高的文字（比如四个视图标题），译完必须还是同样高。旧逻辑是
# 每条各自「压到自己的框宽里」，框窄一条、译文长一点，字号就掉下来 ——
# 同一页里两个一样的标题变成 8.8pt 和 13pt，比整体偏小更扎眼：
# 客户不会去量字号，但一眼就看出「这两个不一样大」。
#
# 归类的容差：原件墨迹高度相差 12% 以内算一类。制图里同一级文字的高度
# 是一致的，差 12% 以上基本是真的两级（标题 vs 标注）。
CLASS_TOL = 0.12

# 同一类里允许的缩放余地。1.0 表示完全不许缩 —— 太硬，一个长句就写不进去；
# 0.90 表示最多缩一成（9pt 变 8.1pt），肉眼看不出，但能救回大部分长句。
# 超过这一成才去折行、挪位；再不行才降级为「特例」，单独缩小并在报告里点名。
CLASS_FLOOR = 0.90

# 挪位预算，按字高的倍数算。原来是 1.5 —— 实测标注行距只有 8pt 左右，
# 挪 13pt 出去，两条本来挨着的标注就隔开了一整行，看着不再像同一个信息点。
# 0.8 约等于 7pt，够躲开一条相邻标注，又不至于把标注甩出原来的位置。
#
# 注意这里只管**上下**。左右加宽是另一回事：沿同一条基线左右延伸，读起来
# 还是同一个信息点，客户不会觉得「跑走了」。两个预算合成一个，就是 v1.1.5
# 的问题 —— 为了不挪远把预算砍到 0.8 字高，结果连加宽都做不了，英文又比
# 中文长，一页标题只好各自缩字号，反而回到「同类标题不一样大」。
MOVE_BUDGET = 0.80

# 左右加宽的预算，同样是字高的倍数。加宽本身有占用图兜底（碰到东西就不许），
# 这里的数只是防跑飞。取 5.0：18pt 的标题给到 90pt，够把「外表正面图」五个字
# 写成长一倍的「Exterior Front View」而不缩字号 —— 实测 3.0（54pt）还差一点，
# 四个视图标题里上面两个被迫缩到 13.9pt，下面两个留在 17.2pt。
WIDEN_BUDGET = 5.0

# ---- 居中文字的重心 ----
#
# 视图标题、图名这类文字在原件里是**对着图形居中**的，不是齐左的。
# 译文比原文长时（外表正面图 → Exterior Front View，长度翻一倍），
# 老做法只有「左端不动往右长」和「右端不动往左长」两种，两头都不是原意：
# 实测第 2 页四个标题的重心全部右移 40~45pt，客户一眼看出「标题偏到一边了」；
# 第 1 页那个更糟 —— 右边就是图框，长出去直接顶到框线上，像要出格。
#
# 正确的长法是绕重心对称长：左右各长一半，重心原地不动。
# 所以先判「这条原本是不是居中的」，判据见 CENTER_TOL_EM。
#
# 判断「居中」的容差，按字高的倍数：原框中心与所标注图形中心相差半个字高
# 以内就算居中。实测这份图纸五个视图标题的偏差都在 0.6pt 以内，
# 而旁边那些标注（Blue parts、Grey parts）偏差在 40pt 以上 —— 中间空得很，
# 取半个字高（约 9pt）既接得住印刷级的偏位，也不会把标注误判成居中。
CENTER_TOL_EM = 0.5

# 判「居中」的字号门槛：这一条的字面高度不低于本页最大字高的一定比例。
# 居中是标题的排版属性，标注不是 —— 图纸标注靠引线定位，习惯齐左排成一列。
# 实测第 1 页左边那一列标注（外框50*50*2.5mm、蓝色部分、灰色部分…）正好
# 排在视图的中线上，只看几何会把它们全判成居中，译文两头一起长，
# 整列的左边缘就散了。0.85 的依据：标题 18~19pt、标注 9.7~11pt，
# 两类之间空得很宽，取 0.85 既能接住「同一级标题里略小一点的那个」，
# 又绝不会把标注拉进来。
CENTER_MIN_REL = 0.85

# 判「居中」时往下找图形的纵向范围（字高的倍数）。标题通常在图形正上方，
# 中间隔着一排尺寸线，实测间距 6~10 个字高，取 14 留余量。
CENTER_REF_BAND_EM = 14.0

# 居中文字允许的重心偏移上限（字高倍数）。
# 不是 0，是因为「守重心」和「守字号」有时会打架：左边空、右边被图框挡住时，
# 硬要对称就得缩字号。允许挪半个字高，让排版有一条中间路线可走。
# 但偏移要付代价 —— 见 CENTER_DRIFT_COST。
CENTER_DRIFT_EM = 0.5

# 重心每偏移一个字高，折算成多少比例的字号损失。
# 0.2 的意思是：偏半个字高 ≈ 字号小一成。这个换算来自排版的常识取舍 ——
# 标题缩一成的字，读者看不出来；标题从视图中线偏出半个字高，一眼就看出来。
# 所以宁可缩字，也不愿偏；反过来，若偏移能换来超过一成的字号，才值得偏。
CENTER_DRIFT_COST = 0.2

# 折成两行的代价，折算成字号折扣。
# 原图是一条单行标注时，折行会多占一行纵向空间，把紧挨着的下一条标注挤开
# （实测「流水孔 ø12mm」折行后，ø12mm 那一行直接贴到下面的 Red part 上）；
# 而且同一页里有的标注一行、有的两行，看着比整体小一号更乱。
# 0.90 的意思是：单行只让一成以内的字号，就守住单行；折行能换来超过一成的
# 字号（长句常见）才折。
WRAP_COST = 0.90

# 文字与图框之间要留的安全边距（字高倍数）。
# 客户的原话是「几乎要出格子了」：译文长出去撞到图框线，虽然没有真的越界，
# 但距框线只剩十几点、还不到一个字高，看着就是快要掉出去。
# 留一个字高的净空，视觉上才「在框里」。真放不下时（最后一轮）会放弃这条约束，
# 不能因为怕贴边就把整条留成原文。
FRAME_PAD_EM = 1.0

# 标题与旁边别的文字之间要留的净空（字高倍数）。
# 原来只靠占用图的 0.3pt 垫片，等于「不压上就算过」：译文比原文长，
# 对称长出去之后左端离旁边的尺寸数字只剩 0.9pt —— 放大看是贴着写的，
# 客户会说「挤在一起了」。0.35 字高在 19pt 标题上约 6.6pt，是排版上
# 两段不相干文字的常用最小间距（约 1/3 字高）。
#
# 它是**偏好**不是硬约束：探针判占用是按面积占比算的（0.4% 以内算干净），
# 邻居只是蹭到角上时仍会放行 —— 这份图纸的「外表侧面图」就是这种情况，
# 左边那颗「66」尺寸字只探进来 3pt 就被放过了。真按字面卡 6.6pt，
# 这一条要缩到 13pt 才排得下，整页四个标题跟着掉两成 —— 那比贴着写更难接受。
# 所以：能留就留，留不出来先缩字号（不超过一成），再不行就接受贴边。
# 只对标题用：标注本来就密，一刀切要求净空会把整页标注挤到更小的字号。
TITLE_CLEAR_EM = 0.35


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
#
# 前置断言只挡 ASCII 字母数字，不用 \w：\w 在 Python 3 里是 Unicode 语义，
# ø Ø ⌀ ⌀ ± 这些符号全都算 \w，于是「ø12.00mm」被当成「紧贴字母」挡在门外，
# 尾零收不回来。客户报的 ø12mm→@12.00mm 就卡在这里 —— 符号一坏，
# 这条尺寸就永远带着 .00 回不去，进而被当成「有译文」重画一遍。
_DIM_CHAIN = re.compile(
    r"(?<![A-Za-z0-9_.])(" + _DIM_FACTOR + r"(?:\s*[×xX*]\s*" + _DIM_FACTOR + r")+)"
    r"(?![A-Za-z0-9_.])",
    re.IGNORECASE)
# 单个带单位的数值。前置排除 ± 和 ASCII 字母数字，公差与型号进不来。
# 只认「小数部分全是零」的那种（3.00mm、1000.00mm）——
# 这一类必定是模型格式化出来的：原文写 3，它写成 3.00。
# 像 101.60mm 这种是程序按英寸精确换算出来的（4" → 101.60mm），
# 那个 0 表示精度到 0.01，砍掉就变成 101.6，等于把精度说小了一档，
# 不能动。同理 1.20mm 也放过 —— 少省一个字符，换换算结果不被改，划算。
#
# 前置断言同 _DIM_CHAIN，只挡 ASCII 字母数字：用 \w 的话「ø12.00mm」
# 「Ø12.00mm」都收不回来（ø、Ø 在 Unicode 里是 \w），实测只有这两种
# 写法会带着 .00 活到图上。± 仍然显式排除 —— 公差位数有含义。
_DIM_UNIT = re.compile(r"(?<![A-Za-z0-9_.±])"
                       r"(\d+\.0+)(\s*)(mm|cm|in)(?![A-Za-z0-9_])", re.IGNORECASE)
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


# 比「两串文字内容是否相同」时，把数字的写法差异抹平
_CONTENT_NUM = re.compile(r"\d+(?:\.\d+)?")


def same_content(a, b):
    """
    两串文字是不是**同一个内容，只是写法不同**。用于决定「要不要擦掉重写」。

    为什么需要这道判断：一条本来就该原样保留的文字（纯尺寸、纯代号），
    只要被后处理动过哪怕一个字符，下游就会认为「这是一条译文」，
    于是把原来的字擦掉、用它自己的字体重写一遍。上游任何一处不可靠
    —— OCR 把 ø 认成 @、PDF 字体没有 ToUnicode 把 ⌀ 抽成 · ——
    都会趁机被印到图上。客户报的 ø12mm 变 @12.00mm 正是这条路：
    这条本来不必翻译，是后处理加的 .00 让它「看起来变了」。

    抹平的是三件事：
      · 数字的尾零与位数    12.00 == 12 == 12.0
      · 所有空白            25 mm == 25mm
      · 首尾空白
    其余字符一律对照 —— 公斤 ≠ kg、@ ≠ ø，改了就要重写。

    注意不能拿它当「译文是否合格」用：它只管写法，不管对错。
    """
    def canon(s):
        s = re.sub(r"\s+", "", s or "")
        return _CONTENT_NUM.sub(lambda m: _trim_zeros(m.group(0)), s)

    return canon(a) == canon(b)


# 数值连同后缀单位，作为一个整体剥掉（12mm、0.5kg）。单位不是「可译的词」，
# 必须成对剥才干净 —— 单剥 mm 的话，ø12mm 里的 mm 会残留成「一个词」。
_NUM_UNIT_TOK = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:mm|cm|km|m|kg|mg|g|ml|l|in|inch|inches|ft|foot|feet"
    r"|lbs|lb|oz|pcs|pc)", re.IGNORECASE)
_PLAIN_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_NON_WORD = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff]+")
# 汉字/假名：一个字就算内容（「门」也要译）
_HAS_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")
# 拉丁字母：要求连续两个以上。单个字母在图纸上必然是代号或序号
# （R5 半径、M8 螺纹、H7 公差、A 剖切位置），没有一个是「词」，
# 送去翻译只会被改写。两个以上才算真有词（DN、THK、QTY、Panel）。
_HAS_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")


def needs_translation(text):
    """
    这条文字里到底有没有「需要翻译的内容」。

    判据：把数字连同单位、以及所有标点符号剥掉之后，还剩不剩汉字或拉丁字母词。

      25mm / ø12mm / ±0.5mm / 90° / 0.75mm   → 没有（纯尺寸，不该动）
      R5 / M8×30 / Ø50 H7 / 100l / 1/4"-20    → 没有（单字母代号，不该动）
      DN50 / SCH40 / 3公斤 / Panel / 外框正视图 → 有（真要处理）

    必须连单位一起剥：`ø12mm` 里的 mm 满足「有没有拉丁字母」这个粗暴判据，
    于是纯尺寸标注被当成英文标注，既被后处理改写、又被记进漏译清单。
    客户看到清单上写着「25mm 未译」只会以为工具没干活。

    单字母不算词：拿 R5 去翻译，模型会把它当缩写展开或改大小写，
    而图上 R5 就是半径 5，一个字符都不能动。
    """
    s = text or ""
    s = _NUM_UNIT_TOK.sub(" ", s)
    s = _PLAIN_NUM.sub(" ", s)
    s = _NON_WORD.sub(" ", s)
    return bool(_HAS_CJK.search(s) or _HAS_LATIN_WORD.search(s))
