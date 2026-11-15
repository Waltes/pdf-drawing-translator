# -*- coding: utf-8 -*-
"""
从图像里找回 OCR 吞掉的工程符号。

为什么需要这一层
----------------
客户报：源图「流水孔 ø12mm」译成「Weep hole 12 mm」，直径符号整个没了。

逐段实测下来，责任不在翻译模型 —— 把 ø 喂进去它会原样保留：

    流水孔ø12mm   -> Weep hole ø12mm
    流水孔12mm    -> Weep hole 12mm      ← 原文里根本没有，模型也不会凭空补
    流水孔@12mm   -> Weep hole @12mm     ← 读错成 @ 它也照抄，不猜

问题出在读字这一步。同一个符号换着法子试：

    整页 300dpi        -> 读到「流水孔12mm」，符号被丢弃，连痕迹都没有
    整页 800dpi        -> 读到「流水孔012mm」，认成了数字 0
    切出来单字送识别    -> 空结果，连着邻字一起才读到「01」

这个符号在识别模型的字典里基本不存在，调倍率、调预处理都救不回来。
只能绕开识别，直接看**形状**。

怎么认
------
直径符号的形状在字形里很独特：一个闭合圆环，被一条斜线分成两半，所以

    · 内部有 2 个洞（数字 0、字母 o 只有 1 个）
    · 两个洞的质心连线是**斜**的（θ/8 是上下 90°，φ/% 是左右 0°）
    · 整体高度只有数字的六到八成（它是小写字母，只占 x-height）

实测这份图纸：目标簇洞数 2、两洞连线 116~122°、高度是数字的 0.7；
而「1」「2」洞数 0、高度 1.0，中文字洞数 0~4、高度 1.3。
几档之间拉得很开，不是勉强分开。

为什么要两段跑
--------------
整页渲染用的是 3~4 倍，那个倍率下笔画只有两三个像素宽，描边的字一闭运算
就糊成实心，洞全没了 —— 实测 4 倍直接判成「洞数 0」。

好在放大就稳定：同一个符号在 6、8、10、12 倍下洞数一律是 2、角度 116~122°，
根本不用挑倍率。所以先用低倍快速找出「尺寸和比例像个符号」的候选，
只对候选按 8 倍重新渲染一次做精判。绝大多数标注连候选都没有，
一次多余渲染都不会触发。

补在哪
------
符号簇有自己的横坐标，把整行文本按字符宽度铺开，就能算出每个字符的大致中心，
离符号簇最近的那个字符就是它该待的地方。只在这个字符是数字或字母时才插 ——
落在汉字上说明对齐估偏了，这时候宁可不补。

保守起见只认这一种符号。°±×□ 的形态特征弱得多，硬认会把数字读坏 ——
认错一个符号等于把客户的孔加工错，宁可不留。

形状分不开的怎么办
------------------
有一类靠形状永远分不开：工程字体的「带斜线的零」。它就是个全高椭圆加一条
左下到右上的斜线，跟大写 Ø 同形同高，洞数、分隔角、洞面积占比全落在同一区间。
调阈值没有用 —— 信息量不够，不是参数没调好。

形状之外还有一条线索：符号该待的位置上已经站着什么。直径符号永远写在数字
前面（ø12、Ø50），那个位置上要是已经有 0，它就是数字，不是符号。
所以补之前先看位置，位置被 0/O/o/θ 占着就放弃。漏一个符号人一眼能看出来，
补错一个符号要按错的孔径加工 —— 两边代价差得远，一律往漏的方向偏。
"""

# 只有真要用的人才 import cv2：没装 OCR 组件时整条图像模式本来就不走，
# 不能让一个可选依赖把模块导入搞崩。
_CV = None
_NP = None


def _cv2():
    global _CV
    if _CV is None:
        try:
            import cv2
            _CV = cv2
        except Exception:
            _CV = False
    return _CV or None


def _np():
    global _NP
    if _NP is None:
        try:
            import numpy
            _NP = numpy
        except Exception:
            _NP = False
    return _NP or None


# 阈值集中放这儿，自检和实测调参只改这一处
# 符号高 / 行内字身高。小写 ø 只占 x-height（0.5~0.75），大写的 Ø 顶到
# 数字高度（1.00）—— 两种写法图纸上一样常见，上限必须放到 1 以上，
# 否则「Ø50」这种大写写法一条都认不出来。
HEIGHT_RATIO = (0.40, 1.08)
ASPECT_RANGE = (0.45, 1.45)   # 簇宽 / 簇高
HOLE_COUNT = 2                # 斜线把圆环分成两个洞
HOLE_GAP_MIN = 0.15           # 两洞质心间距 / 簇宽，低于这个算同心洞（如「回」）
SEP_ANGLE_MIN = 12.0          # 分隔方向偏离水平/垂直至少这么多度，才可能是斜线
HOLE_AREA_RANGE = (0.10, 0.62)  # 两洞面积和 / 簇面积，挡掉 % 那种小洞
MIN_BOX_INK = 0.004           # 簇面积 / 字高²，低于这个算噪点
ZOOM_FINE = 8                 # 精判用的渲染倍率，实测 6~12 倍结果一致

# 文本里哪些字符算是「已经被读出来的直径写法」。
# 认得出来的话就地换掉，别再插一个重复的。
# 拆成两组，因为处理方式不同：
#   真符号 —— OCR 读对了，原样尊重，一个字节都不改（尤其不许把小写改大写）；
#   误读   —— OCR 把它读成了别的字符，形状判出来就要换掉。
TRUE_SYMBOLS = "øØ⌀φΦ∅"
# `·` 也算误读：PDF 字体缺 ToUnicode 表时，字符会被抽成中点，这是实测遇到过的。
# 敢把它列进来，是因为只在形状判出直径符号时才替换 —— 那时右边已经是数字，
# 语境就是直径标注，不会是普通的分隔点。
MISREAD_SYMBOLS = "@·"
SYMBOL_VARIANTS = TRUE_SYMBOLS + MISREAD_SYMBOLS

# 这些字符占着符号该在的位置时，一律不补。
#
# 为什么必须有这一道
# ------------------
# 「带斜线的零」是工程字体专门用来区分数字 0 和字母 O 的写法：一个全高椭圆
# 加一条左下到右上的斜线。它的形状跟大写 Ø 一模一样 —— 洞数 2、分隔角约 60°、
# 洞面积占比也在这个区间内，靠形状根本分不开，调阈值也分不开。
#
# 更要紧的是 h_ref 取的是行内最高的字形，这一行里有斜线零的时候，
# 高度比恒等于 1.0，那道闸门等于没有。所以这不是「精度不够」，是判据缺一环。
#
# 补不补，只能看上下文：符号该在的位置上要是已经站着一个 0，那就不是符号，
# 是数字。宁可不补 —— 漏一个符号人扫一眼就看得出来，补错一个符号
# 客户会按错的孔径加工。
DIGIT_LOOKALIKE = "0OoθΘ"


def _char_em(ch):
    """一个字符大致占几 em。只用来估算字符中心，不求精确。"""
    if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff":
        return 1.0
    if ch in "il.,:;'!|`":
        return 0.28
    if ch in "mwMW":
        return 0.85
    if ch.isdigit() or ("A" <= ch <= "Z"):
        return 0.58
    if "a" <= ch <= "z":
        return 0.52
    return 0.5


def _gray(patch, np):
    if patch.ndim == 3:
        g = (patch[:, :, 0] * 0.299 + patch[:, :, 1] * 0.587 +
             patch[:, :, 2] * 0.114)
    else:
        g = patch.astype(float)
    return np.clip(g, 0, 255).astype("uint8")


def _holes(cv, np, mask):
    """一个字形簇内部的洞：质心与面积。外轮廓父号为 -1，洞不是。"""
    cnts, hier = cv.findContours(mask, cv.RETR_CCOMP, cv.CHAIN_APPROX_SIMPLE)
    if hier is None or not len(cnts):
        return []
    hier = hier[0]
    out = []
    for i, c in enumerate(cnts):
        if hier[i][3] == -1:
            continue
        a = float(cv.contourArea(c))
        if a < 10:
            continue
        m = cv.moments(c)
        if m["m00"] < 1:
            continue
        out.append((m["m10"] / m["m00"], m["m01"] / m["m00"], a))
    return out


def _judge(cv, np, page, rect, h_ref_pt):
    """
    对一块疑似符号的区域按 8 倍重新渲染，判断它是不是直径符号。

    rect 是墨迹的紧贴外接框，比例按它算；实际渲染时往外放一成，
    否则裁到边会把圆环一侧切掉，洞就不闭合了。

    返回 (是否, 说明文字)。说明给自检和排查用，不影响判定。
    """
    import math, pymupdf
    pad_x, pad_y = rect.width * 0.10, rect.height * 0.10
    clip = pymupdf.Rect(rect.x0 - pad_x, rect.y0 - pad_y,
                        rect.x1 + pad_x, rect.y1 + pad_y)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(ZOOM_FINE, ZOOM_FINE), clip=clip)
    if pix.width < 8 or pix.height < 8:
        return False, "裁剪太小", None
    if pix.n >= 3:
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)[:, :, :3].astype(int)
    else:
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n).astype(int)
    gray = _gray(a, np)
    _thr, ink = cv.threshold(gray, 0, 255,
                             cv.THRESH_BINARY_INV + cv.THRESH_OTSU)
    if int((ink > 0).sum()) == 0:
        return False, "没有墨迹", None

    h_pt = rect.height
    ratio = h_pt / float(h_ref_pt or 1)
    if not (HEIGHT_RATIO[0] <= ratio <= HEIGHT_RATIO[1]):
        return False, f"高度比 {ratio:.2f}", None
    aspect = rect.width / float(max(0.01, rect.height))
    if not (ASPECT_RANGE[0] <= aspect <= ASPECT_RANGE[1]):
        return False, f"宽高比 {aspect:.2f}", None

    k = max(3, int(min(ink.shape) * 0.10))
    mask = cv.morphologyEx(ink, cv.MORPH_CLOSE,
                           cv.getStructuringElement(cv.MORPH_ELLIPSE, (k, k)))
    holes = _holes(cv, np, mask)
    if len(holes) != HOLE_COUNT:
        return False, f"洞数 {len(holes)}", None

    (x1, y1, a1), (x2, y2, a2) = holes[0], holes[1]
    dx, dy = x2 - x1, y2 - y1
    dist = (dx * dx + dy * dy) ** 0.5
    if dist < HOLE_GAP_MIN * pix.width:
        return False, f"两洞太近 {dist:.1f}", None

    # 分隔方向：θ/8 上下（90°），φ/% 左右（0°），ø 斜着
    ang = abs(math.degrees(math.atan2(dy, dx)))
    if ang > 90:
        ang = 180 - ang
    if ang < SEP_ANGLE_MIN or abs(ang - 90) < SEP_ANGLE_MIN:
        return False, f"分隔方向 {ang:.0f}° 太正", None

    share = (a1 + a2) / float(max(1.0, pix.width * pix.height))
    if not (HOLE_AREA_RANGE[0] <= share <= HOLE_AREA_RANGE[1]):
        return False, f"洞占比 {share:.2f}", None
    # 大小写不猜。
    #
    # 原先按高度比 0.88 划线：小写 ø 只占 x-height，大写 Ø 顶满数字高度。
    # 实测不成立 —— 斜线要伸出圆环两端，墨迹框跟着变高，一个 x-height 的
    # 小写 ø 量出来是数字高度的 0.93，直接越线判成大写。这不是调阈值能修的：
    # 大写和小写的高度区间本来就重叠，斜线的外伸量又随字体变。
    #
    # 猜错的代价是「原图小写，输出变大写」，等于擅自改了原图。所以不猜：
    # 一律给小写 ø。图纸上小写是主流写法，而且原文里本来就是真符号的时候
    # 走的是「保留原文」分支，压根不会走到这儿。
    char = "ø"
    return True, f"洞数2 方向{ang:.0f}° 高度比{ratio:.2f}", char


def pymupdf_matrix_8():
    """惰性取 pymupdf 的缩放矩阵，省得模块顶层就依赖它。"""
    import pymupdf
    return pymupdf.Matrix(ZOOM_FINE, ZOOM_FINE)


def _insert_pos(text, frac):
    """
    按横向相对位置 frac（0~1）算出该插在第几个字符之前。

    只认数字、字母和误读字符：落在汉字上说明对齐估偏了，返回 None 让调用方放弃。
    """
    ems = [_char_em(c) for c in text]
    total = sum(ems) or 1.0
    acc = 0.0
    for i, em in enumerate(ems):
        center = (acc + em / 2.0) / total
        acc += em
        if abs(center - frac) <= 0.5 * em / total + 0.06:
            ch = text[i]
            # 数字和字母是正常落点。误读字符（@）也必须算落点 ——
            # 它本身就是来被替换掉的那个字符，把它拒了等于「判出了符号
            # 却补不进去」，@12mm 会原样留着。汉字不认。
            if ch.isdigit() or ch.isalpha() or ch in MISREAD_SYMBOLS:
                return i
    return None


def _apply(out, pos, ch):
    """
    把符号落实到文本里。返回 (新文本, 是否算补上一个)。

    顺序是「先认已有的 → 再挡形近字 → 最后才插」，不能反。
    反了就会出现两种脏结果：把原本好好的 ø 再插一个成「øø12」，
    或者把数字 0 插成「ø012」。
    """
    # ① 左边已经是真符号：OCR 读对了，原样留着。
    #    不用形状猜的结果覆盖它 —— OCR 认出来的写法比形状估计可信，
    #    而且原图小写就不许输出成大写。
    if pos > 0 and out[pos - 1] in TRUE_SYMBOLS:
        return out, False
    # ② 左边是误读字符（@）：换掉。
    if pos > 0 and out[pos - 1] in MISREAD_SYMBOLS:
        return out[:pos - 1] + ch + out[pos:], True
    # ③ 右边同理：真符号留着，误读的换掉。
    if pos < len(out) and out[pos] in TRUE_SYMBOLS:
        return out, False
    if pos < len(out) and out[pos] in MISREAD_SYMBOLS:
        return out[:pos] + ch + out[pos + 1:], True
    # ④ 该位置上站着的是 0/O/o/θ —— 跟直径符号同形，形状判不开，
    #    按数字处理，不补。这一道挡的就是「带斜线的零」。
    if pos < len(out) and out[pos] in DIGIT_LOOKALIKE:
        return out, False
    # ⑤ 位置是干净的，插进去。
    return out[:pos] + ch + out[pos:], True


def complete_symbols(page, clip, rect, zoom, text, rgb=None):
    """
    把 OCR 漏掉的直径符号补回文本。返回 (新文本, 补上的符号列表)。

    rgb 是这条文字所在区域已渲染好的位图（低倍），拿它做粗筛；
    精判再按 8 倍单独渲染候选。任何一步出问题都原样返回 ——
    这一层是锦上添花，补错符号比不补糟得多。
    """
    cv, np = _cv2(), _np()
    if cv is None or np is None or not text or rgb is None:
        return text, []
    # 直径符号永远贴着数字写（ø12、Ø50），整条里一个数字都没有的，
    # 不可能有它。先挡掉这一类，省下一次连通域分析 ——
    # 一页上「Grey part」「Red part」这种纯文字标注占了小一半。
    if not any(ch.isdigit() for ch in text):
        return text, []
    try:
        x0 = int((rect.x0 - clip.x0) * zoom)
        y0 = int((rect.y0 - clip.y0) * zoom)
        x1 = int((rect.x1 - clip.x0) * zoom)
        y1 = int((rect.y1 - clip.y0) * zoom)
        h_px = max(6, y1 - y0)
        pad = max(2, int(h_px * 0.10))
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1 = min(rgb.shape[1], x1 + pad)
        y1 = min(rgb.shape[0], y1 + pad)
        if x1 - x0 < 10 or y1 - y0 < 10:
            return text, []

        gray = _gray(rgb[y0:y1, x0:x1], np)
        # Otsu 比固定阈值稳：扫描件偏黄、位图偏灰都能自适应
        _thr, ink = cv.threshold(gray, 0, 255,
                                 cv.THRESH_BINARY_INV + cv.THRESH_OTSU)
        if int((ink > 0).sum()) == 0:
            return text, []
        # 闭运算核按「字」的尺度算，不能按 rect 算。
        #
        # rect 是 OCR 给的文字框，常带上下行距余量 —— 实测 12.5pt 的字装在
        # 28pt 高的框里，按框高算出来的核有 6 像素，比字之间的空隙还宽，
        # 「Ø50」三个字符被糊成一整块（宽高比 2.32，判废，符号从此补不回来）。
        # 按墨迹行高算就对：同一个 16pt 的 Ø，核 4 时三个字符还是分开的，
        # 核到 5 才开始粘。
        _ink_rows = np.nonzero((ink > 0).sum(axis=1))[0]
        glyph_px = (int(_ink_rows.max() - _ink_rows.min()) + 1
                    if len(_ink_rows) else h_px)
        k = max(3, int(glyph_px * 0.10))
        ink = cv.morphologyEx(ink, cv.MORPH_CLOSE,
                              cv.getStructuringElement(cv.MORPH_ELLIPSE, (k, k)))

        # 长横线会把所有列连成一片，连通域也跟着糊掉
        w_img = ink.shape[1]
        for y in range(ink.shape[0]):
            if int((ink[y] > 0).sum()) > w_img * 0.6:
                ink[y, :] = 0

        n, _labels, stats, _ = cv.connectedComponentsWithStats(
            (ink > 0).astype("uint8"), 8)
        blobs = []
        for i in range(1, n):
            bx, by, bw, bh, area = [int(v) for v in stats[i]]
            if area < h_px * h_px * MIN_BOX_INK or bh < h_px * 0.25:
                continue
            blobs.append((bx, by, bw, bh, area))
        if len(blobs) < 2:
            return text, []

        blobs.sort(key=lambda b: b[0])
        # 字高基准取行内最高的字形，不取中位数。
        #
        # 中位数听着更稳，实测更糟：一行「ø12mm」里 m 只有数字六成高，
        # 中位数会落到数字和 m 之间，符号自己的高度比反而被顶到 1.08 上限外，
        # 真符号当场漏认。取最高值时结果只会偏保守（比例偏小），
        # 不会把真符号筛掉 —— 而这一层的取舍就是宁可漏。
        #
        # 代价要说清楚：一行里有斜线零时，它自己就是最高的那个，
        # 高度比恒等于 1.0，这道闸门对它无效。斜线零靠 DIGIT_LOOKALIKE 挡，
        # 不靠这里。
        h_ref_px = max(b[3] for b in blobs)
        h_ref_pt = h_ref_px / float(zoom)
        left = min(b[0] for b in blobs)
        right = max(b[0] + b[2] for b in blobs)
        span = max(1, right - left)

        hits = []
        for bx, by, bw, bh, _area in blobs:
            # 粗筛只挡两头：比字身还高的是汉字，太矮的是标点碎屑。
            # 中间这一档（数字、大小写字母、符号）全交给精判，
            # 宁可多渲染几次，也不能在粗筛就把符号筛掉 —— 低倍下的
            # 比例本来就不准，实测同一个 ø 在 4 倍是 0.51、8 倍是 0.70。
            r = bh / float(h_ref_px)
            if not (0.32 <= r <= 1.10):
                continue
            box = pymupdf_rect(clip, zoom, x0 + bx, y0 + by, bw, bh)
            ok, _why, ch = _judge(cv, np, page, box, h_ref_pt)
            if ok:
                hits.append((((bx + bw / 2.0) - left) / span, bx, ch))
        if not hits:
            return text, []

        out = text
        added = []
        used = set()
        # 从右往左插，前面插过的字符不会影响后面算出来的下标
        for frac, _bx, ch in sorted(hits, key=lambda h: -h[0]):
            pos = _insert_pos(out, frac)
            if pos is None or pos in used:
                # 两个候选落到同一个字符上（斜线被当成独立笔画时会出现），
                # 只认第一个，否则会插成「øø12」。
                continue
            used.add(pos)
            out, ok = _apply(out, pos, ch)
            if ok:
                added.append(ch)
        if not added:
            return text, []
        return out, added
    except Exception:
        return text, []


def pymupdf_rect(clip, zoom, bx, by, bw, bh):
    """
    低倍位图上的像素框，换回 PDF 坐标。

    传进来的必须是整张位图的坐标（含窗口原点偏移），不是局部切片坐标 ——
    早先漏加原点，精判跑到页角去渲染，一律「没有墨迹」。
    """
    import pymupdf
    return pymupdf.Rect(clip.x0 + bx / float(zoom),
                        clip.y0 + by / float(zoom),
                        clip.x0 + (bx + bw) / float(zoom),
                        clip.y0 + (by + bh) / float(zoom))
