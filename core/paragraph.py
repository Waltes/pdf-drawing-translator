"""
段落重组与回填

为什么需要这个模块（这是整份图纸最难读的地方）：
  图纸上的一整段说明，在 PDF 里通常是「一行一个文字对象」。比如：

      A 9 ft square tube for 8 ft fence. It slips over a fence foot, pivots with lock pins at both
      ends, and ends in a locking collar that closes around both extender posts above the 6 ft
      panel.

  如果逐行翻译，得到的是三句互不相干的残片，中文读起来是「一根9英尺方管…两端」然后
  「末端，终止于一个锁紧环…6英尺」然后「面板。」——完全读不懂，这正是机翻腔的来源。

  正确做法：
    1. 先把同一段落的几行合并成一个完整句子，整段送翻译 → 译文通顺
    2. 再按原来每一行的宽度，把中文重新断成同样行数 → 排版位置一点不变

  这条链路是「翻译后让人看得懂」和「位置不变」能同时成立的前提。
"""

import re

import pymupdf

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 断行时优先在这些标点后断开，读起来才不别扭
BREAK_AFTER = "，。；：、！？,;:"
BREAK_BEFORE = "）】》"

# 列表项标记：「1.」「(2)」「•」「-」「①」等。
# 行首带这些标记说明它是新的一条，绝不能并进上一段——
# 否则「1. …步骤」会把「2. …步骤」吞进来，翻译出来就是两件事混成一句。
ITEM_RE = re.compile(r"^\s*(?:\d+[.)、]\s|[•·▪\-–—]\s|[\u2460-\u24ff\u2776-\u2793]|\(\d+\))")


def _starts_item(text):
    return bool(ITEM_RE.match(text or ""))


def _right_edge(g):
    return g.rect.x1


def _can_join(prev, nxt, block_right):
    """
    同一文字块内，nxt 是不是 prev 的折行续接。
    判定：字号一致、左端对齐、行距紧凑、prev 排到接近整块右边界（说明是被自动折行），
    且 nxt 不是新的一条（不带「1.」「•」这类记号）。
    """
    if abs(nxt.size - prev.size) > 0.7:
        return False
    # 左端对齐的容差。列表项的续行会缩进到标记之后（「1. 」占的位置），
    # 所以当上一行是列表项时，要给更大的缩进余量，否则步骤永远接不上。
    left_tol = max(2.0, prev.size * 2.8) if _starts_item(prev.text) else max(2.0, prev.size * 0.9)
    if abs(nxt.rect.x0 - prev.rect.x0) > left_tol:
        return False
    gap = nxt.rect.y0 - prev.rect.y1
    if not (-0.2 * prev.size <= gap <= 1.9 * prev.size):
        return False
    if (block_right - prev.rect.x1) > max(4.0, prev.size * 2.2):
        return False                      # prev 没排满，是独立短行
    if prev.text.strip().endswith((".", "。", ":", "：")) and not _is_wrapped(prev, block_right):
        return False
    if _starts_item(nxt.text):
        return False
    return True


def _is_wrapped(line, block_right):
    """这一行是否是排满后被折行的（而不是人为断句）"""
    return (block_right - line.rect.x1) <= max(4.0, line.size * 2.2)


def _is_continuation(prev, nxt, items_all):
    """
    跨文字块的续接判定。

    为什么必须有这一遍：很多图纸把「1. 步骤第一行」和「第二行」放进两个不同的
    PDF 文字块，只按块内判断永远接不上，翻译出来就是
    「1. 把套管套在底座立杆上，把底座放在接缝后方的桌面」+「距离。」这种断头句。

    最可靠的信号：英文句子续行的开头一定是小写字母（distance. / on the bottom rail.），
    而图纸上的独立标注几乎都是大写开头的名词短语。
    再加上「上一行没写完」「上一行在本栏里排满了」「左端对齐」三重校验，误判概率很低。
    """
    if abs(nxt.size - prev.size) > 0.8:
        return False
    gap = nxt.rect.y0 - prev.rect.y1
    if not (-0.3 * prev.size <= gap <= 2.0 * prev.size):
        return False
    if abs(nxt.rect.x0 - prev.rect.x0) > max(3.0, prev.size * 3.0):
        return False
    if _starts_item(nxt.text):
        return False
    ptext = prev.text.strip()
    if not ptext or ptext.endswith((".", "。", ":", "：", "!", "?", "！", "？")):
        return False
    if not ptext[-1].isalnum() and ptext[-1] not in ",，-":
        return False

    # 「排满了」要按本栏的右边界判断，不能用整页的——
    # 图纸上说明文字常放在右侧窄栏里，用整页宽度衡量会永远判成"没排满"。
    col_right = max((x.rect.x1 for x in items_all
                     if abs(x.rect.x0 - prev.rect.x0) < max(40.0, prev.size * 5)), default=0)
    if col_right > 0 and (col_right - prev.rect.x1) > max(12.0, prev.size * 6):
        return False

    first = nxt.text.strip()[:1]
    if not first:
        return False
    # 小写字母开头 = 句子中间；中文开头也允许接续（中文换行不带空格）
    return first.islower() or bool(CJK_RE.match(first))


def build_paragraphs(groups, min_lines=2):
    """
    把「一行一个对象」的文字块按段落聚合。

    两遍：
      第一遍 块内合并 —— 同一 PDF 文字块里被自动折行的连续行
      第二遍 跨块接续 —— 段落被拆到不同文字块的情况（表格、多栏排版常见）

    返回 (paragraphs, singles)
      paragraphs: [[TextGroup, TextGroup, ...], ...]
      singles   : [TextGroup, ...]  独立标注，单独翻译
    """
    items_all = [g for g in groups if g.text.strip()]
    if not items_all:
        return [], []
    content_right = max(g.rect.x1 for g in items_all)

    by_block = {}
    for g in items_all:
        by_block.setdefault(getattr(g, "block", -1), []).append(g)

    # ---- 第一遍：块内合并 ----
    groups_list = []
    for block_no, items in by_block.items():
        # 按纵向再横向排序，保证行序稳定
        items.sort(key=lambda g: (round(g.rect.y0, 1), g.rect.x0))
        # 整段的右边界：用整个文字块最靠右的那条来定，而不是当前累积的几行。
        # 否则「只有一行的短标签」永远会被判成"排满了"，两个不相干的标签就会被并成一句。
        block_right = max(x.rect.x1 for x in items)
        cur = [items[0]]
        for g in items[1:]:
            if _can_join(cur[-1], g, block_right):
                cur.append(g)
            else:
                groups_list.append(cur)
                cur = [g]
        groups_list.append(cur)

    # ---- 第二遍：跨块接续 ----
    groups_list.sort(key=lambda grp: (round(grp[0].rect.y0, 1), grp[0].rect.x0))
    merged = []
    for grp in groups_list:
        if merged and _is_continuation(merged[-1][-1], grp[0], items_all):
            merged[-1].extend(grp)
        else:
            merged.append(list(grp))

    paragraphs = [g for g in merged if len(g) >= min_lines]
    singles = [x for g in merged if len(g) < min_lines for x in g]
    return paragraphs, singles


def join_text(lines):
    """
    把多行拼成一句。英文折行处如果带连字符要拼回去（inter-
    national → international），中文直接相连。
    """
    out = ""
    for i, g in enumerate(lines):
        t = g.text.strip()
        if not t:
            continue
        if not out:
            out = t
            continue
        if out.endswith("-") and t[:1].islower():
            out = out[:-1] + t           # 拆词连字符
        elif CJK_RE.search(out[-1]) and CJK_RE.search(t[0]):
            out += t                     # 中文之间不加空格
        else:
            out += " " + t
    return re.sub(r"\s+", " ", out).strip()


def _cut_line(text, start, width, font, size):
    """
    从 text[start:] 取一段，宽度不超过 width。
    先按宽度贪心取满，再尽量在标点后退一格，避免把词句切在中间。
    """
    n = len(text)
    take = 0
    while start + take < n:
        if font.text_length(text[start:start + take + 1], fontsize=size) > width:
            break
        take += 1
    if take == 0:
        take = 1       # 至少吐一个字，否则会死循环
    seg = text[start:start + take]
    if start + take < n:                     # 不是最后一段才需要考虑断点
        best = -1
        for i, ch in enumerate(seg):
            if ch in BREAK_AFTER and i >= max(1, take // 2):
                best = i
        if best >= 0:
            take = best + 1
    return text[start:start + take], start + take


def split_to_lines(text, widths, font, size):
    """
    把整段译文按每行的可用宽度重新断行。
    widths: 每行可用的横向宽度（等于原来那一行英文实际占的宽度）
    返回行列表，长度与 widths 相同。
    """
    n = len(widths)
    if n == 1:
        return [text]

    out, pos = [], 0
    for i in range(n):
        if pos >= len(text):
            out.append("")
            continue
        # 最后一行装剩下的全部
        w = widths[i] if i < n - 1 else max(widths[i], 1e6)
        seg, pos = _cut_line(text, pos, w, font, size)
        out.append(seg.strip())
    return out


def fit_paragraph(text, lines, font, base_size, min_scale=0.78):
    """
    为整段安排字号与断行。

    原则：字号优先保持原样（同一段落里字号必须一致，忽大忽小一眼就能看出被改过）。
    只有实在装不下时才整体等比缩小，且不低于 min_scale，避免缩到看不清。

    返回 (行文本列表, 实际字号)
    """
    widths = [max(g.rect.width, 1.0) for g in lines]
    # 给最后一行留出整段宽度，中文通常比英文短，几乎都能装下
    widths[-1] = max(widths)
    # 平衡各行可用宽度：某一行特别窄时（比如 "⑥ Lock pin" 这种短标签），
    # 按原宽度硬断会把词劈开（「弹簧加」/「载，带拉环」）。
    # 允许窄行向右侧借用空间——相邻行本来就是同一段文字，借过来不会盖到别的东西。
    max_w = max(widths)
    widths = [max(w, 0.72 * max_w) for w in widths]

    target = re.sub(r"\s", "", text)

    size = base_size
    for _ in range(6):
        parts = split_to_lines(text, widths, font, size)
        if re.sub(r"\s", "", "".join(parts)) == target:
            return parts, size
        if size <= base_size * min_scale:
            break
        size *= 0.94
    return split_to_lines(text, widths, font, size), size
