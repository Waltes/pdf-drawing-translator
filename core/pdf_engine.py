"""
PDF 图纸翻译核心引擎

职责：
  1. 提取图纸上的文字，连同位置、字号、颜色、书写方向一起拿到
  2. 定向擦除文字（关键：只删文字，图形线稿一根不动）
  3. 在原始位置写回译文，字号自适应、旋转还原、颜色沿用

为什么不栅格化：一旦把页面转成图片再贴回去，线条会变糊、缩放会失真。
本引擎全程在 PDF 矢量层操作，输出文件除文字外与原件完全一致。
"""

import math
import os
import re

import pymupdf

from . import image_text
from . import i18n
from . import paragraph as paragraph_mod
from . import review as review_mod
# 单位换算的判定要和翻译层用同一套规则，不能各写一份：
# 「这条原文里有没有会被换算的量」直接问翻译层要答案，
# 否则这边改了那边没改，会出现「单位声明改了、数值却没换算」的错位。
from .translate import (UNIT_IMPERIAL, UNIT_KEEP, UNIT_METRIC,
                        convert_to_metric, convert_units, has_imperial,
                        has_metric)


def needs_convert(text, mode=UNIT_METRIC):
    """
    按输出单位判断「这条文字里有没有要换算的量」。

    公制模式看英制（英寸→毫米），英制模式看公制（毫米→英寸），
    保持原样模式一律 False —— 这时候连标注版都不该生成，
    否则会出现一份「标了一堆黄块、数值一个没变」的图，比不标更让人困惑。
    """
    if mode == UNIT_IMPERIAL:
        return has_metric(text)
    if mode == UNIT_KEEP:
        return False
    return has_imperial(text)

# ---------------------------------------------------------------------------
# 字体池
# 中文和西文必须分开：中文用系统中文字体，西文用 PDF 内置的西文字体。
# 这里刻意**不**再挂 Arial：PyMuPDF 写系统 TTF 时，ToUnicode 表是按字形反查
# 码位生成的，Arial 把连字符/软连字符、空格/不换行空格、·/U+A78F 画成同一个
# 字形，反查会挑错 —— 肉眼一模一样，复制出来却是断的。具体见 font_for。
# ---------------------------------------------------------------------------
FONT_FILES = {
    "cjk": [
        ("C:/Windows/Fonts/msyh.ttc", "微软雅黑"),
        ("C:/Windows/Fonts/simhei.ttf", "黑体"),
        ("C:/Windows/Fonts/simsun.ttc", "宋体"),
    ],
}

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

# 假名（日文）与谚文（韩文）。它们不在 CJK_RE 的汉字区里，但同样不能用西文字体：
# 内置西文字体没有假名字形，写进去就是一片空白。
# 这两个语种是国际版的目标语之一，不单独认出来，译文直接变白纸。
KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")

# 纯 ASCII：能直接被 PDF 的 WinAnsi 编码装下，用内置西文字体最稳
ASCII_ONLY_RE = re.compile(r"[\x00-\x7f]*\Z")


# 图纸上有一类文字根本不该翻译：图号（BR-01）、修订号（E）、日期、纯型号。
# 它们不是"没翻"，而是"不需要翻"，混进未译清单会让覆盖率看起来很差，也会误导用户。
ID_ONLY_RE = re.compile(r"^(?:[A-Z]{1,4}[-\s]?\d+[A-Za-z0-9\-\/\.]*|[A-Z]|[A-Z](?:-[A-Z])+|\d+[A-Za-z]{0,2})$")


def is_identifier(text: str) -> bool:
    """判断这串文字是不是图号/修订号/型号这类标识符（不需要翻译）"""
    t = (text or "").strip()
    if not t or len(t) > 16:
        return False
    if has_chinese(t):
        return False
    return bool(ID_ONLY_RE.match(t))


class FontPool:
    """字体加载与缓存。字体文件只读一次，后续复用"""

    # 不读磁盘、直接用 PyMuPDF 内置的两个字体：
    #   helv —— 内置 Helvetica，纯 ASCII 用它（字符映射干净，见 font_for）
    #   cjk  —— 内置 CJK 字体，覆盖最广，韩文只认它
    BUILTIN = {"latin_ascii": "helv", "builtin_cjk": "cjk"}

    def __init__(self):
        self._cache = {}

    def get(self, kind):
        """kind: 'cjk' / 'latin' / 'latin_ascii' / 'builtin_cjk'"""
        if kind in self._cache:
            return self._cache[kind]
        if kind in self.BUILTIN:
            font = pymupdf.Font(self.BUILTIN[kind])
            self._cache[kind] = font
            return font
        font = None
        for path, _name in FONT_FILES.get(kind, []):
            if os.path.exists(path):
                try:
                    font = pymupdf.Font(fontfile=path)
                    break
                except Exception:
                    continue
        if font is None:
            # 兜底：PyMuPDF 内置 CJK 字体（宋体风格），保证不崩
            font = pymupdf.Font("cjk") if kind == "cjk" else pymupdf.Font("helv")
        self._cache[kind] = font
        return font


FONTS = FontPool()


def font_for(text):
    """
    按文字内容挑字体。四处写字的地方都走这里，别各挑各的。

    四档，按「这个字体到底有没有这个字、写进去还是不是原来那个码位」定：

    1. 韩文 → 内置 CJK 字体。实测 Windows 的雅黑、黑体、宋体、Arial
       **没有一个**带韩文字形（has_glyph 全 False），用系统字体就是满屏空白。
    2. 汉字 / 日文假名 → 雅黑（沿用原来的观感）。假名必须一起判：
       内置西文字体没有假名字形，日文译文会写成白纸。
    3. 内置西文字体能覆盖的（ASCII、西欧重音字母、西里尔字母、
       · ° × — “ ” • 这些符号）→ 内置 Helvetica。
       这一档存在的唯一理由是**字符映射准确**：PyMuPDF 写系统 TTF 时，
       ToUnicode 表按字形反查码位生成，而 Arial、雅黑都把若干字符画成
       同一个字形，反查会挑中错的那个 —— 实测「·」写成 U+A78F、
       「•」写成 U+00A0、「-」写成 U+00AD。肉眼一模一样，但复制出来是断的、
       搜索也搜不到；图纸上的数值是要被复制去下料的。内置字体的映射是干净的，
       宽度上 Helvetica 与 Arial 度量兼容，混排在同一页看不出差别。
    4. 内置西文字体装不下的（⑤⑭ 这类图纸编号、⌀ 等工程符号）
       → 内置 CJK 字体。它覆盖最广，映射同样干净。
       **不回退系统 Arial**：一行里混进一个⑤，整行的连字符和空格都会被
       写成软连字符和不换行空格（实测踩到过，就是这条注释的由来）。
    """
    s = text or ""
    if HANGUL_RE.search(s):
        return FONTS.get("builtin_cjk")
    if CJK_RE.search(s) or KANA_RE.search(s):
        return FONTS.get("cjk")
    helv = FONTS.get("latin_ascii")
    if ASCII_ONLY_RE.match(s) or all(helv.has_glyph(ord(c)) for c in set(s)):
        return helv
    return FONTS.get("builtin_cjk")


def has_chinese(text: str) -> bool:
    """判断字符串里是否含汉字"""
    return bool(CJK_RE.search(text or ""))


def detect_lang(text: str) -> str:
    """
    判断这段文字该往哪个方向翻：
      含汉字        -> 'zh'   需要转英文
      有字母        -> 'en'   需要转中文
      纯符号数字    -> 'none' 不需要翻译
    """
    if has_chinese(text):
        return "zh"
    if re.search(r"[A-Za-z]", text or ""):
        return "en"
    return "none"


# ---------------------------------------------------------------------------
# 文字块提取
# ---------------------------------------------------------------------------
class TextGroup:
    """
    一个「语义文字块」。
    图纸里一行标注常被 PDF 切成多个 span（每个字一个 span 很常见），
    必须按间距合并，否则翻译会逐字进行，语义全断。
    """

    __slots__ = ("text", "rect", "origin", "size", "color", "angle",
                 "dir", "spans", "lang", "index", "block")

    def __init__(self):
        self.text = ""
        self.rect = None
        self.origin = None
        self.size = 0.0
        self.color = 0
        self.angle = 0.0
        self.dir = (1.0, 0.0)
        self.spans = []
        self.lang = "none"
        self.index = -1
        self.block = -1        # 所属 PDF 文字块编号，用于段落重组

    def __repr__(self):
        return f"<Group {self.text!r} size={self.size:.1f} angle={self.angle:.0f}>"


def _span_projection(span, line_dir):
    """
    计算 span 沿书写方向的起止投影位置，用于判断相邻 span 是否连在一起。
    水平文字 d=(1,0) 投影就是 x；竖排文字 d=(0,-1) 投影是 -y。
    注意：dir 是 line 级属性，span 里没有这个键。
    """
    dx, dy = line_dir
    ox, oy = span["origin"]
    start = ox * dx + oy * dy
    # 用 bbox 在书写方向上的实际跨度，比字体测量更准
    b = pymupdf.Rect(span["bbox"])
    extent = abs(b.width * dx) + abs(b.height * dy)
    return start, extent


def extract_groups(page, page_no: int = 0):
    """
    从页面提取文字块列表。
    合并规则：同一行内、同字号、间距小于 0.6 个字高的 span 视为同一段文字。
    """
    groups = []
    raw = page.get_text("dict")

    for block in raw.get("blocks", []):
        if block.get("type") != 0:      # 0 = 文字块，其他是图片
            continue
        block_no = block.get("number", -1)

        for line in block.get("lines", []):
            line_dir = tuple(line.get("dir", (1.0, 0.0)))
            pending = []                # 当前正在累积的 span 集合
            last_end = None
            last_size = None

            for span in line.get("spans", []):
                text = span.get("text", "")
                # 纯空白 span 直接丢弃，避免占据位置
                if not text.strip():
                    continue

                start, extent = _span_projection(span, line_dir)
                size = span.get("size", 10)

                # 判断是否需要断开成新块：
                #   字号变了 或 与上一个 span 间距过大（超过 0.6 字高）
                need_split = False
                if pending:
                    if last_size and abs(size - last_size) > 0.5:
                        need_split = True
                    elif last_end is not None and (start - last_end) > 0.6 * size:
                        need_split = True
                    elif (start - last_end) < -0.5 * size:
                        # 位置回退说明是另一排（换行或不同基线）
                        need_split = True

                if need_split and pending:
                    groups.append(_make_group(pending, line_dir, page_no, block_no))
                    pending = []

                pending.append(span)
                last_end = start + extent
                last_size = size

            if pending:
                groups.append(_make_group(pending, line_dir, page_no, block_no))

    for i, g in enumerate(groups):
        g.index = i
        g.lang = detect_lang(g.text)
    return groups


def _make_group(spans, line_dir, page_no, block_no=-1):
    """把若干个相邻 span 合成一个文字块"""
    g = TextGroup()
    g.spans = list(spans)
    g.block = block_no
    g.text = "".join(s.get("text", "") for s in spans)
    g.dir = line_dir

    # 位置取并集，保证擦除时覆盖所有字符
    rect = pymupdf.Rect(spans[0]["bbox"])
    for s in spans[1:]:
        rect |= pymupdf.Rect(s["bbox"])
    g.rect = rect

    # 字号取最大（混合字号时按最大的排版，视觉上更接近原图）
    g.size = max(s.get("size", 10) for s in spans)
    # 颜色沿用第一个 span（同一段文字颜色通常一致）
    g.color = spans[0].get("color", 0)
    # 起点用第一个 span 的 baseline 起点
    g.origin = pymupdf.Point(spans[0]["origin"])

    # 旋转角：由书写方向反算（PDF 的 y 轴向下，所以取负号）
    dx, dy = line_dir
    g.angle = -math.degrees(math.atan2(dy, dx))
    return g


# ---------------------------------------------------------------------------
# 擦除与写回
# ---------------------------------------------------------------------------
def erase_text(page, groups):
    """
    定向擦除文字。
    fill=None      -> 不加白色遮盖块，擦完露出下面的原始图形
    graphics=NONE  -> 不移除线稿，这是图形不失真的关键
    images=NONE    -> 不移除图片，扫描件背景得以保留
    """
    for g in groups:
        # 略微外扩，避免字符边缘残留（0.3pt 足够，不会误伤邻近线条）
        r = pymupdf.Rect(g.rect)
        r.x0 -= 0.3
        r.y0 -= 0.3
        r.x1 += 0.3
        r.y1 += 0.3
        page.add_redact_annot(r, fill=None)

    page.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_NONE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        text=pymupdf.PDF_REDACT_TEXT_REMOVE,
    )


def _int_to_rgb(c):
    """PDF 里颜色是整数（0xRRGGBB），转成 0-1 浮点三元组"""
    return ((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255


def fit_font_size(font, text, base_size, max_len, min_scale=0.62, overflow=1.25):
    """
    计算写回字号。
    思路：译文比原文长是常态，硬缩字号会让图纸看起来不协调。
    所以优先允许适度溢出（标注周围通常有空白），只有溢出太夸张才缩字号。
    缩字号也设下限，避免小到看不清。
    """
    if max_len <= 0:
        return base_size
    natural = font.text_length(text, fontsize=base_size)
    if natural <= 0:
        return base_size
    if natural <= max_len * overflow:
        return base_size                     # 小幅溢出，保持字号不变
    scaled = base_size * max_len / natural
    return max(scaled, base_size * min_scale)


def write_paragraph(page, lines, new_text):
    """
    整段译文写回：按原来的行数、每行宽度重新断行，再逐行放回原来的 baseline。

    为什么不整段写成一行：图纸上的段落在版面上占固定高度，
    写成一行会压到下面的图形或表格线上；
    按原行数回填，版面高度和行距完全不变，只有字变了。

    例外：两三行的「短标签」（例如 ⑥ Lock pin / spring loaded, pull ring），
    中文往往一个短句就说完，硬按原行数拆会把词语劈开（「弹簧加」/「载，带拉环」）。
    这种一律合成一行，字号按最宽那行的空间自适应。
    """
    font = font_for(new_text)

    if len(new_text) <= 26 and len(lines) <= 2:
        box = max(g.rect.width for g in lines)
        size = fit_font_size(font, new_text, lines[0].size, box)
        # 只有「合成一行还看得清」时才合成；缩得太厉害就退回按行回填，
        # 宁可两行，也不要把字缩到看不清
        if size >= lines[0].size * 0.8:
            g = lines[0]
            tw = pymupdf.TextWriter(page.rect)
            tw.append(g.origin, new_text, font=font, fontsize=size)
            color = _int_to_rgb(g.color)
            if abs(g.angle) < 0.5:
                tw.write_text(page, color=color)
            else:
                tw.write_text(page, color=color, morph=(g.origin, pymupdf.Matrix(g.angle)))
            return (paragraph_mod.join_text(lines), new_text,
                    round(size, 1) != round(g.size, 1))

    parts, size = paragraph_mod.fit_paragraph(new_text, lines, font, lines[0].size)

    for g, seg in zip(lines, parts):
        if not seg:
            continue
        tw = pymupdf.TextWriter(page.rect)
        tw.append(g.origin, seg, font=font, fontsize=size)
        color = _int_to_rgb(g.color)
        if abs(g.angle) < 0.5:
            tw.write_text(page, color=color)
        else:
            tw.write_text(page, color=color, morph=(g.origin, pymupdf.Matrix(g.angle)))

    return (paragraph_mod.join_text(lines), new_text,
            round(size, 1) != round(lines[0].size, 1))


def write_groups(page, entries):
    """
    把译文写回原位。
    entries: [(TextGroup, 新文字), ...]
    返回 [(原文, 译文, 是否缩字号)，...] 供界面展示

    字号一致性处理：图纸对字号统一很讲究，同一段落里三行字如果大小不一，
    一眼就能看出是后贴的。所以先算出每条需要多大字号，再把「原始字号相同」
    的标注统一成一个字号，避免译文长短不同导致字号参差。
    """
    # ---- 第一轮：计算每条在保持可读的前提下需要多大字号 ----
    plan = []
    for g, new_text in entries:
        if not new_text:
            continue
        # 旋转文字的可排版长度是纵向跨度，水平文字是横向宽度
        box_len = g.rect.width if abs(g.angle % 180) < 1 else g.rect.height
        font = font_for(new_text)
        size = fit_font_size(font, new_text, g.size, box_len)
        plan.append({"g": g, "text": new_text, "font": font, "size": size})

    # ---- 第二轮：同一段落内的标注统一字号 ----
    # 只按「原始字号」整页统一是错的：标题栏的小字会被技术要求的长行连累一起缩小。
    # 所以再按纵向距离做一次聚类，只在真正相邻（同一段落）的标注之间统一。
    by_size = {}
    for item in plan:
        key = round(item["g"].size * 2) / 2      # 按 0.5pt 取整分组
        by_size.setdefault(key, []).append(item)

    clusters = []
    for size_key, items in by_size.items():
        # 旋转文字的方向不同，不参与纵向聚类，各自独立
        for it in (i for i in items if abs(i["g"].angle) >= 0.5):
            clusters.append([it])

        flat = [i for i in items if abs(i["g"].angle) < 0.5]
        # 按 baseline 纵向位置排序，间距超过 4 倍字高即视为另一段
        flat.sort(key=lambda i: (round(i["g"].origin.y, 1), i["g"].origin.x))
        cur, last_y = [], None
        for it in flat:
            y = it["g"].origin.y
            if last_y is not None and (y - last_y) > 4 * size_key:
                clusters.append(cur)
                cur = []
            cur.append(it)
            last_y = y
        if cur:
            clusters.append(cur)

    for items in clusters:
        if len(items) < 2:
            continue
        # 组内最费空间的那条决定统一字号；同时设一个下限，
        # 防止一条超长文字把同类标注整体拖到看不清
        smallest = min(it["size"] for it in items)
        base = max(it["g"].size for it in items)
        unified = max(smallest, base * 0.75)
        for it in items:
            # 不能超过自己实际需要的字号，否则会溢出
            it["size"] = min(it["size"], unified)

    # ---- 第三轮：写回 ----
    report = []
    for it in plan:
        g, new_text, font, size = it["g"], it["text"], it["font"], it["size"]
        tw = pymupdf.TextWriter(page.rect)
        tw.append(g.origin, new_text, font=font, fontsize=size)

        color = _int_to_rgb(g.color)
        if abs(g.angle) < 0.5:
            tw.write_text(page, color=color)
        else:
            # 任意角度：绕原 baseline 起点旋转，保证与原标注同角度
            tw.write_text(page, color=color, morph=(g.origin, pymupdf.Matrix(g.angle)))

        report.append((g.text, new_text, round(size, 1) != round(g.size, 1)))
    return report


# ---------------------------------------------------------------------------
# 单位声明栏改写
# ---------------------------------------------------------------------------
# 图纸标题栏里有一格写着「单位 / 英寸」（英文图是 UNITS / inch）。
# 图面尺寸全部换算成毫米之后，这一格如果继续写英寸，会出大事故：
# 车间看到单位是英寸、数值是 38.10，就会按 38.10 英寸下料，
# 实际要的是 38.10 毫米 —— 差 25.4 倍，比不换算更危险。
# 不换算至少原文还写着英寸符号，看得出是英制；换算了却不改单位，
# 等于给了一个确定的错误读数。
#
# 但只改成「毫米」也不够：看图纸的人核对原件时会对不上数，
# 所以还要注明「原图是英寸、数值已经换算过」。
_UNIT_WORD_RE = {
    # 公制输出时，图上的「单位：英寸」要认出来并改写
    UNIT_METRIC: re.compile(r"(?<![A-Za-z])(inch(?:es)?|英寸|英制|imperial)(?![A-Za-z])",
                            re.I),
    # 英制输出时反过来：认 mm / 毫米 / 公制
    UNIT_IMPERIAL: re.compile(r"(?<![A-Za-z])(mm|毫米|公制|metric|millimet(?:er|re)s?)"
                              r"(?![A-Za-z])", re.I),
}

# 判定「这是单位声明块」的长度上限。
# 声明块就是「英寸」「inch」这种一个词的短格子；
# 正文里的「3 英寸间隙」长度更长且带数字，走正常翻译流程。
UNIT_DECL_MAXLEN = 24


def is_unit_declaration(text, mode=UNIT_METRIC):
    """
    这一块文字是不是单位的声明（而不是某个具体尺寸）。

    三条同时成立才算：长度短、含单位词、不含数字。
    不含数字这条是关键 —— 「9 英尺」是尺寸，「英寸」才是声明。

    单位词按输出模式选：公制模式认 inch/英寸，英制模式认 mm/毫米。
    只认一边的话，反方向输出时这一格根本不会被改写，
    图纸上就会出现「数值是 78.74"、单位栏还写着毫米」的矛盾。
    """
    s = (text or "").strip()
    if not s or len(s) > UNIT_DECL_MAXLEN:
        return False
    if any(ch.isdigit() for ch in s):
        return False
    pat = _UNIT_WORD_RE.get(mode, _UNIT_WORD_RE[UNIT_METRIC])
    return bool(pat.search(s))


# 我们自己写下的说明行（见 write_unit_decl）。
# 认它的唯一用处是「二次处理时整块擦掉」：用户拿我们出过的图纸再跑一遍
# （客户退回、换个语种重出、接着上一版继续改）是很常见的用法，
# 那一行只有 3pt 高，被当成普通文字送去翻译再写回去会塞不下、
# 在图上留下半截字，和新写的说明行叠成两套。
_DECL_NOTE_RE = re.compile(r"(?:已换算|原图(?:英寸|毫米)|converted\s+from)", re.I)


def is_decl_note(text):
    """这一小块是不是我们上一版留下的单位说明行（而不是图纸原本的内容）"""
    s = (text or "").strip()
    if not s or len(s) > UNIT_DECL_MAXLEN:
        return False
    return bool(_DECL_NOTE_RE.search(s))


def write_unit_decl(page, g, src_text, mode=UNIT_METRIC, doc_lang="zh"):
    """
    改写单位声明格：第一行写新单位，第二行小字注明原单位和已换算。

    为什么分两行：这一格宽度通常只有 20~90pt（BR-01 是 90pt），
    写成一行「毫米（原图英寸，已换算）」会被自动缩到 4pt 以下，打印出来是糊的。
    分两行后主行能保住原字号大小，说明文字用 0.55 倍字号，看得清也不占地方。

    mode 决定写哪一边：公制模式「毫米 / 原图英寸」，英制模式「英寸 / 原图毫米」。
    写反了比不改更糟 —— 车间会照着错的单位下料。

    doc_lang 决定写哪种文字，指**这份产出文件**的语言，不是界面语言。
    中文界面的中国工程师翻一份英文图给国外客户，声明格里写「毫米」，
    收图的人一个字都看不懂；反过来英文界面的用户出中文图也一样。
    非中文的目标语（日/德/法…）一律用英文 —— 这些语言没有词表，
    写中文更看不懂，英文至少是图纸的通用语。
    """
    if (doc_lang or "zh") == "zh":
        main, note = ("英寸", "原图毫米，已换算") if mode == UNIT_IMPERIAL \
            else ("毫米", "原图英寸，已换算")
    else:
        main, note = ("INCHES", "converted from mm") if mode == UNIT_IMPERIAL \
            else ("mm", "converted from inch")

    font = font_for(main)
    color = _int_to_rgb(g.color)

    # 主行：宽度按格子自适应，但不放大超过原字号
    size = min(g.size, fit_font_size(font, main, g.size, g.rect.width))
    tw = pymupdf.TextWriter(page.rect)
    tw.append(g.origin, main, font=font, fontsize=size)
    tw.write_text(page, color=color)

    # 说明行：紧跟主行下方，字号 0.55 倍（下限 4.5pt，再小就印不出来）
    nfont = font_for(note)
    nsize = max(4.5, size * 0.55)
    # 放不下就把说明行缩到格子宽度以内
    nsize = min(nsize, fit_font_size(nfont, note, nsize, g.rect.width))
    norg = pymupdf.Point(g.origin.x, g.rect.y1 + nsize * 0.85)
    tw2 = pymupdf.TextWriter(page.rect)
    tw2.append(norg, note, font=nfont, fontsize=nsize)
    tw2.write_text(page, color=color)

    # 清单回执里的括号跟界面语言走：父级登记的是给人看的报表行，
    # 一份英文清单里夹一对全角括号（），排版上很扎眼
    pair = (f"{main} ({note})" if i18n.is_en() else f"{main}（{note}）")
    return (i18n.T("单位声明"), src_text, pair)


# ---------------------------------------------------------------------------
# 换算数据标黄：【标注】版
# ---------------------------------------------------------------------------
# 输出的第二版要在所有被换算过的数值上画荧光笔底。
# 做法上有个坑：draw_rect(overlay=False) 看似「画在文字下面」最理想，
# 实测这份图纸整页铺了一层白色底图，画在最底层的黄块被白底完全盖住，
# 一个字都看不见。所以改成 overlay=True + 半透明填充 ——
# 效果和荧光笔一样：黄底压在字上，字照样黑得清楚，而且文字仍是矢量、
# 可以选中和搜索，不是把页面转成图片。
MARK_FILL = (1.0, 0.92, 0.45)     # 淡黄，(255, 235, 115)
MARK_OPACITY = 0.45

# 数值 与 「数值(–数值) 单位」两种形态，标注和抽取都用这两个
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 标注版要认的单位，跟着输出单位走：
# 公制模式标 104.78mm / 11.34kg，英制模式标 4-1/8" / 11.34 lb。
# 认错了就会出现「黄块画在数字上、单位却是另一种」的怪图。
_MARK_UNIT_RE = {
    UNIT_METRIC: re.compile(r"(?:mm|kg)", re.I),
    UNIT_IMPERIAL: re.compile(r'(?:"|″|”|lb)', re.I),
}


def _num_unit_re(mode):
    """「数值 + 目标单位」的匹配式。范围写法的前一个数也要跟着抓"""
    unit = r'(?:[\"″”]|lb|lbs)' if mode == UNIT_IMPERIAL else r"(?:mm|kg)"
    return re.compile(
        r"\d[\d,]*(?:\.\d+)?(?:\s*[–—~～-]\s*\d[\d,]*(?:\.\d+)?)?\s*" + unit, re.I)


def paint_metric_marks(doc, converted_texts, mode=UNIT_METRIC):
    """
    给所有换算出来的数值画荧光笔底。

    converted_texts: {页码(0基): [该页换算后的译文, ...]}
    返回实际标注的数值个数。

    只标「这一条确实被换算过」的数值：
    原文本来就写目标单位的（公制模式下的 2000mm）不在这个集合里，不会被误标。
    """
    painted = 0
    num_unit = _num_unit_re(mode)
    unit_re = _MARK_UNIT_RE.get(mode, _MARK_UNIT_RE[UNIT_METRIC])
    for page_no, texts in converted_texts.items():
        if page_no >= len(doc):
            continue
        page = doc[page_no]

        # 该页换算产生的所有目标单位数值。
        # 存「纯数字」而不是「数字+单位」：范围写法 0.363–0.454kg 里的
        # 前一个值后面跟的是破折号不是 kg，按整串比对会把它漏掉。
        want = set()
        for t in texts:
            for m in num_unit.finditer(t or ""):
                for n in _NUM_RE.findall(m.group(0)):
                    want.add(n)
        if not want:
            continue

        for w in page.get_text("words"):
            token = w[4].strip().rstrip(",.;:，。；：")
            # 只有含目标单位的词才可能是换算结果，
            # 这一条同时挡住了「数量 2」「角度 38」这类纯数字被误标
            if not unit_re.search(token):
                continue
            # R88.90mm / Ø12.70mm / 0.363–0.454kg 这类，只要词里有任意一个
            # 换算出来的数值就整词标黄（和人工用荧光笔划一下的习惯一致）
            if not any(n in want for n in _NUM_RE.findall(token)):
                continue
            r = pymupdf.Rect(w[:4])
            r.x0 -= 1.0
            r.y0 -= 1.2
            r.x1 += 1.0
            r.y1 += 1.2
            page.draw_rect(r, color=None, fill=MARK_FILL,
                           fill_opacity=MARK_OPACITY, overlay=True)
            painted += 1
    return painted


def doc_has_metric(doc, mode=UNIT_METRIC):
    """
    这份文档里到底有没有会在本次设置下被换算的数值。

    为什么要预扫：单位声明栏在不同图纸的页面上都有（多页图纸每页都印标题栏），
    而第 1 页可能一个英制尺寸都没有。逐页决定「这一页要不要改单位声明」
    会因为「翻译到这一页时还不知道后面有没有换算」而出错，
    所以先整篇扫一遍，再统一决定。

    用 convert_units 判断而不是 has_imperial：
    后者把 NPT 1/2" 这类螺纹代号也算成「有英制」，而代号是不换算的，
    那样会出现「单位改成毫米、数值却一个没变」的矛盾文件。
    英制模式同理：判断的是「毫米→英寸会不会真的变」。
    """
    if mode == UNIT_KEEP:
        return False
    for page in doc:
        t = page.get_text() or ""
        if t and convert_units(t, mode) != t:
            return True
    return False


# ---------------------------------------------------------------------------
# 整份文档处理
# ---------------------------------------------------------------------------
def analyze_pdf(path):
    """
    体检：判断这份 PDF 能不能处理。
    矢量图纸能提取到大量文字；扫描件/图片版几乎提取不到，
    提前告诉用户，省得他等半天拿到一份没变化的文件。

    另外单独认一种情况：文字已转成曲线（traced）。
    CAD 导出 PDF 时勾了「文字转曲」，中文被画成矢量路径，
    结果既没有文字层、也没有位图对象，get_image_info() 是空的。
    这类文件在原来那套判据里跟「正常但字很少的图纸」长得一样，
    跑完一条都翻不出来、输出跟原文几乎一致，用户只会认为程序坏了。
    """
    doc = pymupdf.open(path)
    pages = len(doc)
    chars = 0
    images = 0
    paths = 0            # 矢量路径总数：用来区分「转曲的文字」和「真的空白页」
    for page in doc:
        chars += len(re.sub(r"\s", "", page.get_text() or ""))
        try:
            images += len(page.get_images())
        except Exception:
            pass
        try:
            paths += len(page.get_drawings())
        except Exception:
            pass
    doc.close()

    per_page = chars / pages if pages else 0
    # 每页不到 20 个字符、又存在图片，基本可以断定是扫描件
    scan_like = pages > 0 and per_page < 20 and images > 0
    # 转曲：几乎没有可提取的文字、一张位图都没有，但页面上有大量矢量路径。
    # 平均每页 20 条以上才判，纯边框的空页或封面不会被误判。
    traced = (pages > 0 and per_page < 20 and images == 0
              and paths / pages >= 20)
    return {"pages": pages, "chars": chars, "images": images,
            "paths": paths, "scan_like": scan_like, "traced": traced}


def process_pdf(src_path, dst_path, translate_fn, progress=None,
                render_preview=True, translate_batch=None, scan_images=True,
                stats_out=None, annotate_path=None, unit_mode=UNIT_METRIC,
                ocr_full_pages=False, verify=True, dst_lang=None,
                reviewer=None):
    """
    处理整份 PDF。
    translate_fn(text, lang) -> str      逐条翻译（兼容旧用法）
    translate_batch(items) -> list       批量翻译，items=[(text,lang)]。
                                         传了它就走批量并发，速度快十倍以上。
    scan_images                          是否用 OCR 处理位图里的文字
                                         （3D 渲染图上的零件标注等，需要装 OCR 库）
    progress(done, total, msg)           进度回调
    stats_out                            传入一个字典，回填覆盖率统计：
                                         targets / translated / untranslated(清单)
                                         有了它才能对用户如实交代「有没有全部译完」
    annotate_path                        传了就在出完译文版之后，再出一份带荧光笔标注的版本，
                                         把所有换算过的数值标黄。
                                         两版内容完全一样，区别只在标注。
    unit_mode                            输出单位：metric / imperial / keep。
                                         它决定了「什么算需要换算」「单位声明改成哪边」
                                         「标注版标哪些数」三件事，必须一路传到底，
                                         任何一处按公制写死都会造出自相矛盾的文件。
                                         单位声明格用哪种文字不在这里传 ——
                                         按 translate_fn 的目标语言定，见 write_unit_decl。
    ocr_full_pages                       把每页整页当位图做 OCR。
                                         给「文字已转曲」的 PDF 用：这类文件
                                         既没有文字层也没有位图对象，常规 OCR
                                         一个区域都找不到，只能整页渲染后识别。
                                         代价是慢（每页数秒）且覆盖原字时可能
                                         压到穿过的线框，属于有损方案。
    verify                               落盘前整份复核（core/review.py）。
                                         不过关就不生成文件，问题清单回填到
                                         stats_out["review"]，stats_out["blocked"]
                                         置真。默认开 —— 生成一份带中文残留或
                                         空白块的文件，比不出文件更糟。
    dst_lang                             产出的目标语言，复核判「残留」要用。
                                         不传就从 reviewer / 批量方法的绑定实例上猜，
                                         猜不到会退回界面语言 —— 那会让残留检查
                                         整个失效（界面是中文、产出是英文时，
                                         页面上所有汉字都不会被判成残留）。
                                         所以调用方**应当显式传**。
    reviewer                             翻译器实例，语义抽查要用。同 dst_lang，
                                         显式传最稳，别指望从 translate_batch 上反推。
    """
    doc = pymupdf.open(src_path)
    total = len(doc)
    all_report = []
    stat = {"targets": 0, "translated": 0, "untranslated": [], "image_texts": 0,
            "paragraphs": 0, "unit_decl_fixed": 0, "metric_marks": 0}
    # 写入记录 [(页码, Rect, 期望文本)]，供输出前的复核回读校验。
    # 擦除是不可逆的，只有写完再读回来才能证明「这里没有被擦成空白」。
    marks = []

    # 整篇先扫一遍有没有会换算的量。
    # 有才改单位声明栏 —— 没有换算却把「英寸」改成「毫米」，
    # 等于把一份英制图纸谎称成公制，比不改更糟。
    need_metric = doc_has_metric(doc, unit_mode)

    # 单位声明格写哪种文字（见 write_unit_decl）：跟**产出文件**的语种走，
    # 不是跟界面语言走。语种只有翻译器知道（目标语言下拉），从它身上读。
    # 两个入口都要看：调用方可能只传 translate_batch（走批量并发时是常态），
    # 那时 translate_fn 是 None，语种得从批量接口的绑定对象上取
    # （translate_many 是 Translator 的绑定方法，__self__ 就是那个实例）。
    # 都取不到就退回界面语言 —— 自检里就是这种情况，和改造前的行为一致。
    hint_obj = reviewer if reviewer is not None else (
        translate_fn if translate_fn is not None else
        getattr(translate_batch, "__self__", None))
    dst_hint = dst_lang or getattr(hint_obj, "dst_lang", "") or ""
    if dst_hint == "auto":
        # 目标选「自动」＝翻成系统语言，跟 Translator._resolve 用同一口径，
        # 否则会出现「文字翻了、单位声明格还写着另一种语言」的错位
        dst_hint = i18n.auto_target()
    # 非中文产出（含日/德/法这些没有词表的目标语）一律写英文声明：
    # 收图的人看不懂中文，英文至少是图纸通用语
    doc_lang = "zh" if dst_hint == "zh" else ("en" if dst_hint else i18n.get_lang())

    # 换算产生的译文按页留档，最后用来画荧光笔标注
    converted_texts = {}

    for page_no in range(total):
        page = doc[page_no]
        # 告诉翻译器当前在第几页。翻译器只在出口校验计量数据时用它记页码，
        # 好让「数据校验清单」能指到具体页，用户改图时不用满文件搜。
        # 用 try 包住：translate_fn 也可能是个普通函数（老用法），没有这个属性。
        try:
            translate_fn.page_no = page_no + 1
        except Exception:
            pass
        groups = extract_groups(page, page_no)

        # ---- 段落重组 ----
        # 把被 PDF 折成多行的同一段说明合成一句再翻译，
        # 否则中文会断在句子中间（「…6英尺」/「面板。」），这是机翻腔的最大来源。
        # 旋转文字（竖排标注）不参与段落合并，避免按宽度断行时算错方向。
        horizontal = [g for g in groups if abs(g.angle) < 0.5]
        rotated = [g for g in groups if abs(g.angle) >= 0.5]
        paras, singles = paragraph_mod.build_paragraphs(horizontal)
        singles.extend(rotated)

        units = []      # (kind, payload, 原文, lang)
        for p in paras:
            txt = paragraph_mod.join_text(p)
            lg = detect_lang(txt)
            if is_identifier(txt):
                continue
            if lg in ("zh", "en") or needs_convert(txt, unit_mode):
                units.append(("para", p, txt, lg if lg != "none" else "en"))
        for g in singles:
            t = g.text.strip()
            if not t or is_identifier(t):
                continue
            # 纯尺寸标注（76-1/2"、108"、1-1/2"）里没有一个字母，
            # detect_lang 判成 none，原来会直接跳过 ——
            # 而这是图纸上最常见的一种标注：尺寸线上只有数字和单位符号。
            # 结果就是正文里的单位都换算了，偏偏尺寸线上一个都没动。
            # 标成 size 单独走：这类没有文字可译，只有数值要算。
            if g.lang == "none" and needs_convert(t, unit_mode):
                units.append(("size", g, t, "en"))
            elif g.lang in ("zh", "en"):
                units.append(("single", g, t, g.lang))

        stat["targets"] += len(units)
        stat["paragraphs"] += len(paras)

        if progress:
            extra = f"（含 {len(paras)} 段整段说明）" if paras else ""
            progress(page_no, total,
                     f"第 {page_no + 1}/{total} 页：发现 {len(units)} 处标注{extra}")

        # ---------- 文字层：提取出来的矢量文字 ----------
        if units:
            # 纯尺寸标注（76-1/2"、4-1/8"）不送翻译通道。
            # 这类文本里没有一个可译的词，送去只会白花 token：
            # 模型多半原样返回 76-1/2" 或改写成 76.5"，两种结果都会被
            # 自己的质检判成不合格（「与原文相同」或「中英混杂」），
            # 一整批因此全军覆没，界面还报「AI 接口调用失败」——
            # 实测就是这么误报的。数值一律本地算，不询价。
            todo = [(u[2], u[3]) for u in units if u[0] != "size"]
            got = []
            if todo:
                if translate_batch:
                    if progress:
                        progress(page_no, total,
                                 f"第 {page_no + 1}/{total} 页：翻译 {len(todo)} 条…")
                    got = translate_batch(todo)
                else:
                    for text, lang in todo:
                        try:
                            got.append(translate_fn(text, lang))
                        except Exception:
                            got.append(text)
            # 把本地算好的尺寸结果按原顺序插回去，保证和 units 一一对应
            new_texts = []
            it = iter(got)
            for u in units:
                new_texts.append(next(it) if u[0] != "size" else u[2])

            para_entries, single_entries, decl_entries, stale_entries = [], [], [], []
            for (kind, payload, text, _lang), new_text in zip(units, new_texts):
                if not text:
                    continue

                # 纯尺寸标注不采信翻译通道的结果。
                # 这类文本里没有一个可译的词，模型要么原样返回、要么把
                # 76-1/2" 改写成 76.5" 或 6 英尺 4 英寸 —— 一旦改写，
                # 后面基于符号的正则换算就认不出来了。数值一律用自己算的。
                if kind == "size":
                    new_text = convert_units(text, unit_mode)

                # 上一版自己写下的说明行（「原图英寸，已换算」/
                # 「converted from inch」）：整块擦掉，不重写。
                # 用户拿我们出过的图再处理一次（客户退回、改语种重出）很常见，
                # 那时说明行会被当成普通文字送去翻译 —— 它只有 3pt 高，
                # 写回去塞不下几个字，图上就留下半截「换算」，
                # 和新写的说明行叠在一起，等于两套说明互相打架。
                # 新说明由 write_unit_decl 紧跟着主行重新写，不会漏。
                if need_metric and kind in ("single", "size") and \
                        is_decl_note(text):
                    stale_entries.append(payload)
                    continue

                # 单位声明格先拦下来：它不是尺寸，不能参与换算，
                # 也不能当普通文字翻（翻成「英寸」就等于没处理）。
                if need_metric and kind in ("single", "size") and \
                        is_unit_declaration(text, unit_mode):
                    decl_entries.append((payload, text))
                    continue

                # 记下「这一条确实换算过」的最终译文，供【标注】版标黄。
                # 三个条件都要满足：原文含可换算的量、有译文、译文里真的有目标单位。
                # 螺纹代号（NPT 1/2"）原文含英寸但不换算，不会被记进来。
                if (new_text and convert_units(text, unit_mode) != text
                        and _num_unit_re(unit_mode).search(new_text)):
                    converted_texts.setdefault(page_no, []).append(new_text)

                # 译文必须非空且确实变了，否则不处理，防止把原文搞丢
                if new_text and new_text.strip() and new_text.strip() != text:
                    if kind == "para":
                        para_entries.append((payload, new_text.strip()))
                    else:
                        single_entries.append((payload, new_text.strip()))
                else:
                    # 没翻出来的记进清单，最后如实告诉用户漏了哪些
                    stat["untranslated"].append((page_no + 1, text))

            if para_entries or single_entries or decl_entries or stale_entries:
                # 先擦后写，顺序不能反
                to_erase = []
                for p, _ in para_entries:
                    to_erase.extend(p)
                for g, _ in single_entries:
                    to_erase.append(g)
                for g, _ in decl_entries:
                    to_erase.append(g)
                to_erase.extend(stale_entries)
                erase_text(page, to_erase)

                for p, txt in para_entries:
                    all_report.append((page_no + 1,) + write_paragraph(page, p, txt))
                    stat["translated"] += 1
                    # 整段占的是一组行，取并集当作回读区域
                    pr = pymupdf.Rect(p[0].rect)
                    for ln in p[1:]:
                        pr |= ln.rect
                    marks.append((page_no + 1, pr, txt))

                if single_entries:
                    page_report = write_groups(page, single_entries)
                    all_report.extend((page_no + 1,) + r for r in page_report)
                    stat["translated"] += len(page_report)
                    marks.extend((page_no + 1, g.rect, txt)
                                 for g, txt in single_entries)

                for g, src in decl_entries:
                    all_report.append((page_no + 1,) +
                                      write_unit_decl(page, g, src, unit_mode, doc_lang))
                    stat["unit_decl_fixed"] += 1

                if progress:
                    total_w = len(para_entries) + len(single_entries) + len(decl_entries)
                    progress(page_no + 1, total,
                             f"第 {page_no + 1}/{total} 页：写入 {total_w} 处译文")

        # ---------- 位图层：烤进图片里的文字，只能靠 OCR ----------
        if scan_images:
            used = [g.rect for g in groups]
            img_misses = []
            n_img = image_text.process_page_images(
                page, translate_fn, used, all_report, translate_batch,
                untranslated=img_misses, force_full=ocr_full_pages,
                write_marks=marks)
            if n_img:
                # 图片层的记录拿不到页码，这里补上
                for i in range(len(all_report) - n_img, len(all_report)):
                    if all_report[i][0] is None:
                        all_report[i] = (page_no + 1,) + all_report[i][1:]
                # 写入记录同样是本页新加的那几条，页码一并补上
                for i in range(len(marks) - n_img, len(marks)):
                    if marks[i][0] is None:
                        marks[i] = (page_no + 1,) + marks[i][1:]
                stat["image_texts"] += n_img
                if progress:
                    progress(page_no + 1, total,
                             f"第 {page_no + 1}/{total} 页：替换图内标注 {n_img} 处")
            for item in img_misses:
                stat["untranslated"].append((page_no + 1, item[1]))

    # ---------- 输出前复核 ----------
    # 位置很关键：必须在落盘之前。
    # 一份带着中文残留或空白块的图纸交到客户手里，比这次没出文件糟得多 ——
    # 后者当场就能看见，前者要等客户发现。
    if verify:
        warn_issues, notice_issues = [], []
        ledger = getattr(hint_obj, "metric_report", None)
        for item in (getattr(ledger, "issues", None) or []):
            try:
                level, pno, s_txt, o_txt, detail = item
            except (TypeError, ValueError):
                continue
            text = f"第 {pno or '?'} 页 {s_txt} → {o_txt}：{detail}"
            # 只有确凿的疑点才拦：warn 是「译文数值与复算值不符」
            # 「原文数值在译文里找不到」这类。notice 是按单位个数数出来的，
            # 正确译文也常被判不等（见 MetricReport 的说明），只当提示。
            if level == "warn":
                warn_issues.append(text)
            elif level == "notice":
                notice_issues.append(text)
        review = review_mod.review_document(
            doc, marks, dst_hint or i18n.get_lang(),
            untranslated=stat["untranslated"],
            metric_issues=warn_issues,
            metric_notes=notice_issues,
            translator=hint_obj if hint_obj is not None else None)
        stat["review"] = review
        if review.blocking:
            stat["blocked"] = True
            doc.close()     # 内存里已经改好的内容直接丢弃，不落盘
            if stats_out is not None:
                stats_out.update(stat)
            return all_report

    # 子集化字体，只保留实际用到的字形，避免文件体积暴涨
    try:
        doc.subset_fonts()
    except Exception:
        pass

    doc.save(dst_path, garbage=3, deflate=True)
    doc.close()

    # ---- 第二版：把所有换算出来的数值标黄 ----
    # 在刚存好的译文版上重新打开再画，而不是直接改内存里那份 doc ——
    # 那样标注会跟着一起存进第一版，两版就分不开了。
    if annotate_path:
        try:
            d2 = pymupdf.open(dst_path)
            n = paint_metric_marks(d2, converted_texts, unit_mode)
            if n:
                d2.save(annotate_path, garbage=3, deflate=True)
                stat["metric_marks"] = n
            d2.close()
        except Exception as exc:
            # 不能静默吞掉：标注版生成失败时用户会以为文件没出，
            # 而译文版其实是好的 —— 必须让界面能说清是哪一步出的问题。
            stat["annotate_error"] = f"{type(exc).__name__}: {exc}"

    if render_preview:
        try:
            render_comparison(dst_path, dst_path.replace(".pdf", "_预览.png"))
        except Exception:
            pass

    if stats_out is not None:
        stats_out.update(stat)
    return all_report


def render_comparison(pdf_path, png_path, page_no=0, dpi=110):
    """把输出 PDF 首页渲染成图片，方便用户快速肉眼核对"""
    doc = pymupdf.open(pdf_path)
    if page_no >= len(doc):
        page_no = 0
    pix = doc[page_no].get_pixmap(dpi=dpi)
    pix.save(png_path)
    doc.close()
    return png_path
