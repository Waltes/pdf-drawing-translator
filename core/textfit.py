# -*- coding: utf-8 -*-
"""
文字排版：折行与宽度试算

单独拆成一个模块，是因为两个写入引擎都要用它，而它们互相不能 import：

    pdf_engine  处理文字层（矢量文字，原位替换）
    image_text  处理位图层（OCR 出来的字，盖掉再写）

image_text 被 pdf_engine 引用，反过来 import 就成环。排版规则又是同一套 ——
文字层和位图层对「什么算放得下」必须给同一个答案，否则同一份图纸里
两处一样宽的框，一处折行一处留白，看着就不可信。
所以规则放这里，两边都引用，不做两份。
"""

# 最多折几行。两行是有原因的：图纸上的标注框高度通常就是一个字高多一点，
# 三行必然压到下面那行字或尺寸线上。宁可这处保留原文，也不要把版面搞乱。
WRAP_MAX_LINES = 2

# 行距 = 字号 × 该系数。1.15 接近图纸原有行距，折出来的两行不会粘在一起，
# 也不会因为太松而超出原来占的高度
WRAP_LINE_LEAD = 1.15

# 折行后仍低于这个字号就不折：写了也看不清，不如保留原文并如实汇报
WRAP_MIN_SIZE = 3.5

# 中日韩文字逐字可断的行内区间（汉字、假名、谚文）
_CJK_RANGES = (
    (0x3400, 0x4dbf), (0x4e00, 0x9fff),      # 汉字（含扩展 A）
    (0x3040, 0x30ff), (0x31f0, 0x31ff),      # 日文假名
    (0x1100, 0x11ff), (0x3130, 0x318f), (0xac00, 0xd7af),   # 韩文
)


def is_wide(ch):
    """
    这个字符是不是「可以单独站在一行末尾」的宽字符。

    判这一条是为了知道两处该不该加空格：
    汉字之间不能加（「门板」不是「门 板」），西文词之间必须加。
    """
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _CJK_RANGES)


def tokens(text):
    """
    拆成断行单位：西文一个词算一个单位（词内不拆），
    汉字/假名/谚文一个字算一个单位（这些文字本来就可以在任意字之间换行）。
    """
    out, buf = [], ""
    for ch in text:
        if is_wide(ch):
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        elif ch == " ":
            if buf:
                out.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def wrap_text(font, text, maxw, size):
    """
    按 maxw 折行，返回行列表；折不出来返回 None。

    返回 None 的两种情形：单个词本身就比整行宽（折了照样溢出），
    或者文字是空的。这两种都说明「折行救不了这条」——
    调用方应当改为缩字号或保留原文，而不是硬写。

    绝不劈开英文单词是硬要求。试过一个更聪明的按字符断行版本，
    结果 DOOR PANEL 被切成 DOO / R / PA / NEL，比不翻译更难看。
    """
    toks = tokens(text)
    if not toks:
        return None
    lines, cur = [], ""
    for tok in toks:
        joiner = "" if (not cur or is_wide(cur[-1]) or is_wide(tok[0])) else " "
        cand = (cur + joiner + tok) if cur else tok
        if font.text_length(cand, fontsize=size) <= maxw:
            cur = cand
            continue
        if not cur:
            return None                 # 头一个字/词就放不下
        lines.append(cur)
        if font.text_length(tok, fontsize=size) > maxw:
            return None                 # 换到新行还是放不下
        cur = tok
    if cur:
        lines.append(cur)
    return lines or None


def wrap_fit(font, text, base_size, maxw, room_h, max_lines=WRAP_MAX_LINES):
    """
    试算「折行最多能写到多大字号」，返回 (字号, 行列表) 或 None。

    用二分而不是逐档试：字号是连续的，逐档试要么漏掉最优值，
    要么循环次数失控。字号越大行数越多、占高越大，条件对字号单调，二分成立。
    """
    if maxw <= 0 or room_h <= 0:
        return None
    lo, hi = WRAP_MIN_SIZE, max(base_size, WRAP_MIN_SIZE)
    best = None
    for _ in range(20):
        mid = (lo + hi) / 2
        lines = wrap_text(font, text, maxw, mid)
        ok = (lines and len(lines) <= max_lines
              and len(lines) * mid * WRAP_LINE_LEAD <= room_h)
        if ok:
            best = (mid, lines)
            lo = mid
        else:
            hi = mid
    return best
