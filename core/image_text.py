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
# 折行规则与文字层共用一套（core/textfit.py）。两边各写一份的话，
# 同一张图上一样宽的框会出现「一处折行、一处留白」的不一致。
from . import textfit

# OCR 组件与 numpy 都改成惰性加载（首次真正要用图像模式时才导入）。
#
# 为什么不放在模块顶层：安装包已经把 rapidocr / onnxruntime / cv2 / numpy
# 一并打了进去，这四样加起来两百多兆，导入要花两三秒、常驻内存上百兆。
# 而绝大多数图纸是有文字层的，走常规流程就够了，一辈子用不到图像模式。
# 顶层导入等于让所有人每次启动都为一个可能不用的功能付代价。
_OCR_CLASS = None     # 缓存 RapidOCR 类；None 表示还没探测过
_OCR_PROBED = False   # 区分「没探测过」和「探测过但没有」
_NP = None            # 缓存 numpy 模块


def ocr_available():
    """
    OCR 组件是否可用。第一次调用才真正导入，结果会缓存。

    打包版里组件随安装包一起来，正常不会失败；源码运行时没装就会返回 False，
    界面据此如实告知「图像模式用不了」，而不是让用户点完等半天没反应。
    """
    global _OCR_CLASS, _OCR_PROBED
    if not _OCR_PROBED:
        _OCR_PROBED = True
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR_CLASS = RapidOCR
        except Exception:
            _OCR_CLASS = None
    return _OCR_CLASS is not None


def _numpy():
    """取 numpy 模块，首次调用时导入并缓存。"""
    global _NP
    if _NP is None:
        import numpy as _mod
        _NP = _mod
    return _NP


ZOOM = 4              # 位图渲染倍率，4 倍能让 6pt 的小字被稳定识别
ZOOM_LARGE = 3        # 整页级别的大图用 3 倍，兼顾识别率与速度
LARGE_AREA = 400000   # 超过这个面积的区域视为「大图」
MIN_CONF = 0.50       # OCR 置信度下限，太低的多半是图形噪点
MAX_BG_STD = 46       # 背景色标准差上限：背景太花就不动，免得盖出色块
MIN_CONTRAST = 34     # 文字与背景的最小亮度差，太接近说明识别到的是图形噪点

# 写回字号的下限。低于这个值的字印在图纸上等于没写：
# 原文被背景色盖掉，新文字小到看不清，用户看到的就是一块空白。
# 宁可这一条不写（保留原文、记进未译清单），也不能交出一块白斑。
MIN_WRITE_SIZE = 3.5

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
    """取 OCR 引擎单例。引擎初始化要加载三个 onnx 模型，只在首次调用时做一次。"""
    global _ocr_engine
    if _ocr_engine is None:
        if not ocr_available():
            raise RuntimeError("OCR 组件不可用")
        _ocr_engine = _OCR_CLASS()
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
    np = _numpy()   # 惰性取的，见模块顶部说明
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


# ---------------------------------------------------------------------------
# 转曲文字：图上看得见，文字层里却没有
# ---------------------------------------------------------------------------
# 起因（客户实际踩到）：SledBox 图纸的视图标题在导出时被转成了曲线，
# 放大看是空心描边 —— 只有绿色笔画、没有填充。文字层里一个字都没有，
# 常规流程完全读不到，于是程序照常交出一份「翻译版」，
# 未译清单还写着「全部译出」，而图上明明还有 6 处英文。
#
# 为什么不能靠矢量形状猜：
# 试过。图纸上大量小折线本身就是「只描边、不填充」的小路径，
# 纯几何判断在一页上给出 26 处候选，其中只有 6 处真是字。
# 所以改用 OCR 当基准：OCR 读得出来、文字层却没有的，才认定是转曲文字。
#
# 填不填实：实测两种都试。
#   - 原样 OCR（空心不填）：6 处全检出，读出的字更准（Top view、Front view 都对）
#   - 闭运算填实后 OCR：同样 6 处，但把字母 i 读成 l（Top vlew、Sldo vow）
# 所以默认走原样，只有原样一处都没读出来时才退回填实那一路 ——
# 那种情况说明这个文件的轮廓太细，需要加粗才认得出。
OUTLINE_ZOOM = 3.0            # 整页渲染倍数。实测 3.0 与 4.0 检出数一样，3.0 快一倍
OUTLINE_MIN_LETTERS = 3       # 至少含几个字母才算「一句话」，纯数字不报
OUTLINE_MIN_LETTER_RATIO = 0.4  # 字母占非空白字符的比例下限，挡掉线条被读成的乱码
OUTLINE_KNOWN_OVERLAP = 0.35  # 与文字层词框重叠超过这个比例，就是同一处重复识别


def _outlined_from_image(rgb, zoom, known):
    """把一张整页位图交给 OCR，挑出「文字层里没有」的那些。"""
    tmp = os.path.join(DATA_DIR, "_ocr_outline_tmp.png")
    try:
        from PIL import Image
        Image.fromarray(rgb.astype("uint8")).save(tmp)
        result, _ = _get_ocr()(tmp)
    except Exception:
        return []
    finally:
        # 临时图用完就删。留在这儿既不占地方也不体面，
        # 而且会被当成新文件出现在数据目录里。
        try:
            os.remove(tmp)
        except Exception:
            pass

    hits = []
    for item in (result or []):
        try:
            pts, raw_text, score = item[0], item[1], float(item[2])
        except Exception:
            continue
        if score < MIN_CONF:
            continue
        text = fix_ocr_spacing(clean_ocr_text(raw_text))
        # 没有字母的（尺寸数字、编号、纯符号）不用翻译，报出来只会添乱。
        # 光数字母个数不够：图纸上的细线会被读成 "001 'os 06" 这种，
        # 掺着两三个字母，所以还要看字母占实际字符的比例。
        letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", text))
        compact = len(re.sub(r"\s", "", text)) or 1
        if (letters < OUTLINE_MIN_LETTERS
                or letters < OUTLINE_MIN_LETTER_RATIO * compact):
            continue

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        box = pymupdf.Rect(min(xs) / zoom, min(ys) / zoom,
                           max(xs) / zoom, max(ys) / zoom)
        if box.is_empty:
            continue
        # 和文字层已有的词重叠 = 同一处文字被重复识别，不是转曲字
        if any((w & box).get_area() > OUTLINE_KNOWN_OVERLAP * box.get_area()
               for w in known):
            continue
        # 同一处被切成两半时留更长的那个
        dup = False
        for i, (h_box, h_text) in enumerate(hits):
            if (h_box & box).get_area() > 0.5 * min(h_box.get_area(), box.get_area()):
                if len(text) > len(h_text):
                    hits[i] = (box, text)
                dup = True
                break
        if not dup:
            hits.append((box, text))
    return hits


def scan_outlined_text(page):
    """
    找出这一页上「看得见、文字层里却没有」的文字（转曲文字）。

    只读，不改文件。结果交给调用方去如实汇报 ——
    「还有哪几处是原文」，比「全部译出」这句空话有用得多。

    返回 [(rect, text)]，rect 是 PDF 坐标下的字块位置。
    识别不出来或组件不可用时返回空列表：宁可少报，不能瞎报。
    """
    if not ocr_available():
        return []
    np = _numpy()
    try:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(OUTLINE_ZOOM, OUTLINE_ZOOM))
    except Exception:
        return []
    if pix.width < 80 or pix.height < 80:
        return []

    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    rgb = arr[:, :, :3] if pix.n >= 3 else np.repeat(arr[:, :, :1], 3, axis=2)

    known = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]
    hits = _outlined_from_image(rgb, OUTLINE_ZOOM, known)
    if hits:
        return hits

    # 原样一处都没读出来：多半是轮廓太细，填实再来一次
    try:
        from PIL import Image, ImageFilter
        gray = np.asarray(Image.fromarray(rgb.astype("uint8")).convert("L"))
        dark = (gray < 200).astype(np.uint8)
        flat = Image.fromarray(((1 - dark) * 255).astype(np.uint8))
        # 灰度图里胀大黑字用 MinFilter，收缩回原粗细用 MaxFilter（写反会得到全白图）
        filled = flat.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
        return _outlined_from_image(np.array(filled.convert("RGB")), OUTLINE_ZOOM, known)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 转曲文字：认得出就就地改成中文
# ---------------------------------------------------------------------------
# 为什么改写要先过术语表，而不是把 OCR 结果直接丢给模型：
# OCR 读空心轮廓字不稳 —— Top view 读成 Top vlew、Side view 读成 Sldo vow
# （同一份文件里都复现过）。模型会把错字当成正经词照译，于是图上被写进
# 一个别的词；这种错误比留着英文更难发现，客户要拿图纸去下料。
# 先对术语表、容忍一两个字母的偏差，既能纠正误读，又不会凭空造词。
OUTLINE_TERM_RATIO = 0.34     # 模糊匹配的编辑距离上限（按词长比例算，见 _term_close）


def _norm_term(text):
    """归一成比对用的形式：大写、去标点、压空格"""
    t = (text or "").upper().strip()
    t = t.replace("—", "-").replace("–", "-").replace("~", "-")
    t = re.sub(r"[^A-Z0-9 \-/]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _edit_distance(a, b, cap=4):
    """编辑距离，超过 cap 就提前返回，省时间（术语表几百条，逐条算得很快）"""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _term_close(a, b):
    """
    两个术语算不算同一个词（容忍 OCR 的字母误读）。

    距离上限按词长走：Top view（8 字符）容 2 个字符差，
    能接住 vlew/view、Sldo/Side；SCALE（5 字符）只容 1 个，
    免得把外形相近的短词认成同一个。
    """
    if not a or not b:
        return False
    if a == b:
        return True
    longest = max(len(a), len(b))
    if longest < 4:
        return False
    cap = max(1, int(round(longest * OUTLINE_TERM_RATIO)))
    return _edit_distance(a, b, cap) <= cap


# 剖切线标题：A-A、B-B 这类。中文制图习惯写成「A-A 剖视」，
# 术语表里没有整条，所以单独按格式认。
_SECTION_RE = re.compile(r"^(?:CUT\s*|SECTION\s*)?([A-Z])\s*-\s*([A-Z])$")
_SECTION_WORD_RE = re.compile(r"^(CUT|SECTION)\s+([A-Z])\s*-\s*([A-Z])$")


def lookup_outline_term(text):
    """
    把 OCR 读出来的转曲文字对到一条中文术语上。对不上返回 None。

    对不上就返回 None，由调用方如实报「这一处仍是原文」——
    宁可留英文，也不往图纸上写没有把握的词。
    """
    norm = _norm_term(text)
    if not norm:
        return None

    m = _SECTION_WORD_RE.match(norm)
    if m:
        return f"{m.group(2)}-{m.group(3)} 剖视"
    m = _SECTION_RE.match(norm)
    if m:
        return f"{m.group(1)}-{m.group(2)} 剖视"

    try:
        from . import dict_engine
        en2zh = dict_engine.Glossary().en2zh
    except Exception:
        return None

    # 先找完全一致的，再退到模糊匹配。
    # 顺序不能反：模糊匹配会先撞上「外形相近但意思不同」的词条
    # （VIEW 撞上 VALVE 之类），完全一致的那条反而轮不到。
    hit = en2zh.get(norm)
    if hit:
        return hit
    for en, zh in en2zh.items():
        if _term_close(norm, en):
            return zh
    return None


def _seg_hits_rect(p1, p2, r):
    """
    线段有没有穿过矩形内部（Liang-Barsky 裁剪）。

    为什么不直接用外接矩形判相交：图纸边框是一根覆盖整页的大矩形，
    它的外接框和每个标题框都相交，按外接框判会把全部标题误判成「压线」。
    只有按真实线段算，边框那四条边才会正确地判为不穿过标题。
    """
    x0, y0 = float(p1[0]), float(p1[1])
    x1, y1 = float(p2[0]), float(p2[1])
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - r.x0), (dx, r.x1 - x0),
                 (-dy, y0 - r.y0), (dy, r.y1 - y0)):
        if abs(p) < 1e-9:
            if q < 0:
                return False        # 平行且在框外
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return False
            t0 = max(t0, t)
        else:
            if t < t0:
                return False
            t1 = min(t1, t)
    return t1 - t0 > 1e-6


def _item_points(item):
    """把一条绘图指令里的控制点都取出来（不同 op 的点位不同）"""
    op = item[0]
    if op in ("l", "re", "qu"):
        return []
    pts = []
    for v in item[1:]:
        if isinstance(v, pymupdf.Point):
            pts.append((v.x, v.y))
        elif isinstance(v, (tuple, list)) and len(v) == 2:
            pts.append((float(v[0]), float(v[1])))
    return pts


def _covered_line_art(drawings, fill):
    """
    填充框里除了这行字自己的笔画，还压着什么别的东西没有。

    为什么要这道关：改写是拿背景色把整块盖掉再写中文。
    框里若有尺寸线、剖切线穿过，盖完就断了 —— 图上看着像凭空少了根线。
    所以先问一遍：有没有哪一笔真的穿过了这个框（不是外接框相交，是线段穿过）。
    有就放弃这一处，留着英文如实上报，交给用户自己决定要不要手改。
    """
    tol = fill.height * 0.30      # 整笔都在这个范围内，视作字形的一笔
    out = pymupdf.Rect(fill.x0 - tol, fill.y0 - tol,
                       fill.x1 + tol, fill.y1 + tol)
    for g in drawings:
        for item in g.get("items", ()):
            op = item[0]
            if op == "re":
                r = pymupdf.Rect(item[1])
                if r.intersects(fill) and not (r.contains(fill)
                                               or fill.contains(r)):
                    return True
                continue
            pts = _item_points(item)
            if len(pts) < 2:
                continue
            if all(out.contains(pymupdf.Point(p)) for p in pts):
                continue          # 这一笔整条都在框内，是字自己的笔画
            for a, b in zip(pts, pts[1:]):
                if _seg_hits_rect(a, b, fill):
                    return True
    return False


def rewrite_outlined(page, untranslated=None, write_marks=None, report=None):
    """
    把这一页的转曲文字就地改成中文。返回 (写成条数, 剩下的原文列表)。

    只处理能对到术语表的那几处；对不上的照旧留着，并计入未译清单。
    这不是「智能翻译」，是有边界的改写 —— 边界写在 lookup_outline_term 上。
    """
    if not ocr_available():
        return 0, []
    hits = scan_outlined_text(page)
    if not hits:
        return 0, []

    np = _numpy()   # 惰性取的，见模块顶部说明
    drawings = page.get_drawings()
    font_cjk = _get_cjk_font()
    font_latin = pymupdf.Font("helv")

    plans, left = [], []
    for box, text in hits:
        term = lookup_outline_term(text)
        if not term:
            left.append((box, text))
            continue

        # 采样底色和字色：底色多半就是纸面的白，字色沿用原来的绿色，
        # 写成中文还是同一个颜色，不突兀
        h = max(box.height, 1.0)
        pad = max(h * 1.2, 3.0)
        clip = pymupdf.Rect(box.x0 - pad, box.y0 - pad,
                            box.x1 + pad, box.y1 + pad) & page.rect
        try:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(4, 4), clip=clip)
        except Exception:
            left.append((box, text))
            continue
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        rgb = (arr[:, :, :3] if pix.n >= 3
               else np.repeat(arr[:, :, :1], 3, axis=2)).astype(int)
        sampled = sample_colors(rgb, box, 4, clip)
        if not sampled:
            left.append((box, text))
            continue
        bg, fg, _std = sampled

        # 量原字实际的墨迹高度（OCR 框带留白，按框高定字号会大到四成）。
        # 只在框内找深色像素，框外的线条不参与。
        ink_h = box.height * 0.72      # 量不出来时的退路：按经验比例估
        try:
            z = 4.0
            bx0 = max(int((box.x0 - clip.x0) * z), 0)
            by0 = max(int((box.y0 - clip.y0) * z), 0)
            bx1 = min(int((box.x1 - clip.x0) * z), rgb.shape[1])
            by1 = min(int((box.y1 - clip.y0) * z), rgb.shape[0])
            sub = rgb[by0:by1, bx0:bx1]
            if sub.size:
                dark = sub.min(axis=2) < 180
                rows = np.where(dark.any(axis=1))[0]
                if len(rows) >= 2:
                    ink_h = (rows[-1] - rows[0] + 1) / z
        except Exception:
            pass
        # 汉字实际字面约占字号 0.86，反推回去才对得上原来的大小
        hint = min(max(ink_h / 0.86, 3.5), box.height * 1.2)

        cand = {"text": text, "rect": box, "bg": bg, "fg": fg,
                "hint": "en", "vertical": False}
        plan = _plan_one(cand, term, None, font_cjk, font_latin,
                         glyph_h_hint=hint)
        if plan is None:
            # 框太窄，中文塞不进去（缩到看不清就等于留下一个白块）
            left.append((box, text))
            continue
        if _covered_line_art(drawings, plan[2]):
            left.append((box, text))
            continue
        plans.append(plan)

    if not plans:
        for _box, text in left:
            if untranslated is not None:
                untranslated.append(("曲线文字", text))
        return 0, left

    n = paint_plans(page, plans, report)

    # 写完立刻回读：盖掉原文却没写上去，是最难被发现的坏结果
    # （图上留一块白斑，谁也不会去查）。回读不到就当没写成，退回未译。
    failed = []
    for c, out, _fill, _size, _font, _lines in plans:
        got = page.get_text("text", clip=c["rect"]).replace(" ", "")
        if got and out.replace(" ", "")[:1] in got:
            if write_marks is not None:
                write_marks.append({"page": None, "rect": c["rect"],
                                    "ink": [c["rect"]], "out": out,
                                    "src": c["text"]})
        else:
            failed.append((c["rect"], c["text"]))

    for box, text in left + failed:
        if untranslated is not None:
            untranslated.append(("曲线文字", text))
    return max(n - len(failed), 0), left + failed


def process_page_images(page, translate_fn, used_rects, report,
                        translate_batch=None, untranslated=None,
                        force_full=False, write_marks=None, fixes=None):
    """
    处理一页里位图上的文字。

    used_rects:      文字层已经占用的区域，OCR 落在这里说明是重复识别，跳过
    report:          收集改写记录，格式与文字层一致
    translate_batch: 批量翻译函数，传了就把本页所有图片文字打包一次翻完
    untranslated:    收集翻不出来的条目，用于最终如实汇报覆盖率
    force_full:      整页当作一个位图区域处理（文字已转曲的 PDF 走这条路）
    write_marks:     写入记录。页码这里没有，留 None，由 pdf_engine 补 ——
                     和 report 的处理方式一致。
                     这份记录是输出前复核唯一的「擦掉的地方有没有写回字」凭据：
                     背景色盖上去就撤不回来了，只能事后回读。
    fixes:           复核打回后能重写这一条的登记记录（v1.0.8）。
                     每个候选都记，含框、背景色、字色、原文；
                     没写成的也记，重译拿到更短的译文后可以再写一次。

    返回改写条数
    """
    if not ocr_available():
        if untranslated is not None:
            untranslated.append(("图片内文字", "OCR 组件不可用，位图里的文字未处理"))
        return 0

    np = _numpy()   # 惰性取的，见模块顶部说明
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

            # 竖排还是横排：OCR 返回的框是轴对齐矩形，给不出文字朝向，
            # 只能靠长宽比认。竖排的是尺寸线旁边的旋转标注（制图标准里
            # 逆时针 90°，从下往上读），横排的是普通说明。
            # 这一步必须区分：竖排框宽度只有十几点，若当水平文字排版，
            # 可排版长度就等于框宽，几百点长的英文译文会把字号压到 1 点，
            # 图上看着就是一片空白 —— 客户报的就是这个。
            bw = max(xs) - min(xs)
            bh = max(ys) - min(ys)
            vertical = bh > bw * 1.5

            bg, fg, _std = sampled
            candidates.append({"text": text, "rect": txt_rect, "bg": bg,
                               "fg": fg, "hint": hint, "vertical": vertical})

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

    # v1.0.8：把「每条怎么排」和「怎么落到页面上」拆成两个模块函数。
    # 目的是让复核打回的那几条能在原位重写一遍 —— 重写要走的正是同一套
    # 排版与盖字逻辑，复制一份出来迟早会两边不一致。
    plans = []
    for c, out in zip(candidates, outs):
        plan = _plan_one(c, out, untranslated, font_cjk, font_latin)
        if plan is not None:
            plans.append(plan)

    # 复核重译要用的登记记录：每个候选都记一条，写成的带上译文，
    # 没写成的记 None —— 后者正是「空白块 / 未译」的打回对象，
    # 拿到更短的译文后还能再写一次。
    # 写入记录也在这里生成，而不是在 paint_plans 里：记录要能跟登记记录
    # 对上（rec["mark"]），重写成功后才好把回读校验的期望文本一起换掉，
    # 否则第二轮复核会拿旧译文去回读，永远判「空白」。
    by_id = {id(p[0]): p for p in plans}
    for c in candidates:
        p = by_id.get(id(c))
        mark = None
        if p is not None:
            # ink 里两个框都要：原 OCR 框，和实际盖字用的外扩框。
            # 折行时第一行会贴到框顶之外，只按原框回读会读不到、
            # 被判成空白块（复核打回重译也修不好，纯空转）
            mark = {"page": None, "rect": c["rect"], "ink": [c["rect"], p[2]],
                    "out": p[1], "src": c["text"]}
            if write_marks is not None:
                write_marks.append(mark)
        if fixes is not None:
            fixes.append({"page": None, "kind": "image", "obj": c,
                          "src": c["text"], "rect": c["rect"],
                          "ink": [c["rect"], p[2]] if p is not None else [c["rect"]],
                          "out": p[1] if p else None, "mark": mark})

    return paint_plans(page, plans, report)


def _plan_one(c, out, untranslated=None, font_cjk=None, font_latin=None,
              glyph_h_hint=None):
    """
    算出一条图内文字该不该写、写在哪个框里、用多大字号。

    out 为空或和原文一样就不写（返回 None，同时记进未译清单），
    其余情况返回 (候选, 译文, 填充框, 字号, 字体)。

    这段原先内联在 process_page_images 里，v1.0.8 抽出来是为了让复核打回后
    的重写走完全相同的排版规则 —— 两条路径各写一份，迟早会走偏。
    """
    if not out or not out.strip() or out.strip().upper() == c["text"].upper():
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None
    out = out.strip()
    if font_cjk is None:
        font_cjk = _get_cjk_font()
    if font_latin is None:
        font_latin = pymupdf.Font("helv")
    font = font_cjk if _CJK_RE.search(out) else font_latin

    r = c["rect"]
    vertical = c.get("vertical", False)
    # 两个字高方向的定义要分开算，这是竖排能不能正常写出来的关键：
    #   横排 —— 字高是框高，排版长度沿框宽往右
    #   竖排 —— 字高是框宽，排版长度沿框高往上
    # 混用的话，竖排框宽只有十几点，英文译文几百点长，
    # 字号会被压到 1 点出头，等于把一个白块留在图上。
    #
    # glyph_h_hint：转曲文字改写专用。OCR 框是贴着笔画外接框给的，
    # 自带不少留白（实测 9pt 的框里字只有 5pt 高），按框高定字号会写成
    # 比原文大四成的一行字。调用方量出实际墨迹高度传进来，字号才准。
    # 只影响字号，填充框仍按原框算 —— 盖不干净原文会留叠影。
    box_h = r.height
    glyph_h = r.width if vertical else (glyph_h_hint or r.height)
    if vertical:
        maxw = r.height
    else:
        # 横向余量压到 0.2 字高。原先是 0.6，英文长句伸出原框，
        # 伸出去那段下面还压着原文没被盖住，就成了叠影。
        # 但也不能把填充框跟着译文拉宽 —— 试过，个别 OCR 框一宽，
        # 整块图纸被涂白（左侧立柱和尺寸线被盖掉），比叠影更糟。
        # 折中：框不动，让译文缩字号写进框里。
        maxw = r.width + r.height * 0.2

    # 外扩 0.32 字高：OCR 框贴着字形，笔画超出的部分全靠这里盖干净。
    # 分两轮写之后，外扩大一点也不会吃掉相邻行的译文了。
    # 这里按原框高度算（不是按 hint 量出来的字高）：填充框的任务是盖住原文，
    # 拿缩小的字高去算，外扩跟着变小，原来的笔画就会露一圈。
    pad = max(min(box_h * 0.32, 2.0), 0.6)
    fill = pymupdf.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad)
    # 异常保护：填充框最多是原框的 3.5 倍面积，再大说明这条 OCR 框本身不可信，
    # 宁可留下原文记进未译清单，也不能把图形涂掉
    if fill.width * fill.height > r.width * r.height * 3.5 + 40:
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None

    # 字号对齐原文字高，放不下再缩；颜色沿用原文字色。
    # 英文标注普遍比中文窄也比中文矮，用同一个字号会显得偏小，
    # 乘 1.08 补一点观感；上限仍受排版长度约束
    size = glyph_h * (1.0 if _CJK_RE.search(out) else 1.08)
    width = font.text_length(out, fontsize=size)
    if width > maxw > 0:
        size = size * maxw / width
    # 缩到这个程度就不要再写了：原文已经盖掉、字又小到看不清，
    # 等于在图上留一块白斑。保留原文并如实报进未译清单，
    # 至少用户还知道这条没翻。
    #
    # 两个下限要合起来看：
    #   绝对下限 3.5pt —— 再小就是屏幕上一根细线
    #   相对下限 25%  —— 大标题缩到原字高的四分之一以下，
    #                    和旁边的标注明显不成比例，一眼看出是后贴的
    # 注意不能卡太死：标题字 28pt、译文变长后缩到 11pt 是正常排版，
    # 按 45% 卡会把这类正常情况全部拒掉（试过，整页中文一条都写不出来）。
    #
    # v1.0.9：判「太小」之前先试折两行。中文「门板」两个字变成
    # DOOR PANEL 九个字符，14pt 宽的框里单行无论怎么缩都是 2.5pt —— 等于没写；
    # 折成两行能把字号抬到 5pt，读得出，还在原来那块被盖住的区域里。
    lines = None
    thresh = max(MIN_WRITE_SIZE, glyph_h * 0.25)
    if size < thresh and not vertical:
        # 第二行只能往上借空间，借多少有硬上限：可用高度就是原框高度
        # （多给 15% 余量）。OCR 框本来就贴着字，往上多写一行就会
        # 越过被盖住的区域，压到上面那行原文上 —— 那比不译更难看。
        # 上限给 glyph_h（原文字高）而不是当前这个已经缩小的 size：
        # 折行的意义就是把字号抬回来，拿缩小的值当上限等于白折
        plan = textfit.wrap_fit(font, out, glyph_h, r.width,
                                glyph_h * 1.15)
        if plan:
            size, lines = plan
    if size < thresh:
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None
    return (c, out, fill, size, font, lines)


def paint_plans(page, plans, report=None):
    """
    把排版计划落到页面上，返回写入条数。

    分两轮写：先把所有原文统一盖掉，再统一写译文。
    一轮一条地处理会互相打架 —— 相邻两行只隔一个多字高，后一条的填充
    必然压到前一条刚写好的英文上，实测底部那几行长句就是这么整行糊掉的。
    """
    if not plans:
        return 0

    # 第一轮：用采样出来的背景色盖掉原文（不能用白色，会留下刺眼方块）
    for c, _out, fill, _size, _font, _lines in plans:
        page.draw_rect(fill, color=None,
                       fill=tuple(float(v) / 255 for v in c["bg"]), width=0)

    # 第二轮：写译文
    changed = 0
    for c, out, _fill, size, font, lines in plans:
        tw = pymupdf.TextWriter(page.rect)
        color = tuple(float(v) / 255 for v in c["fg"])
        if c.get("vertical"):
            # 竖排标注逆时针 90° 写回：以框右下角为 baseline 起点，
            # 文字向上生长，字身落在基线左侧，正好覆盖原框。
            # Matrix(90) 在 y 轴向下的坐标系里是逆时针 90°，
            # 和制图标准里垂直尺寸数字的朝向一致（从下往上读）。
            # 若按水平写法铺在十几点宽的窄框里，字会被压成一条缝。
            pivot = pymupdf.Point(c["rect"].x1, c["rect"].y1)
            tw.append(pivot, out, font=font, fontsize=size)
            tw.write_text(page, color=color, morph=(pivot, pymupdf.Matrix(90)))
        elif lines and len(lines) > 1:
            # 折行：末行贴原框底，其余往上叠。与文字层用同一套落点规则，
            # 两处对不上的话，同一张图上折出来的行距会一紧一松。
            gap = size * textfit.WRAP_LINE_LEAD
            for k, seg in enumerate(lines):
                base = pymupdf.Point(
                    c["rect"].x0, c["rect"].y1 - (len(lines) - 1 - k) * gap)
                tw2 = pymupdf.TextWriter(page.rect)
                tw2.append(base, seg, font=font, fontsize=size)
                tw2.write_text(page, color=color)
        else:
            tw.append(pymupdf.Point(c["rect"].x0, c["rect"].y1), out,
                      font=font, fontsize=size)
            tw.write_text(page, color=color)
        if report is not None:
            report.append((None, c["text"], out, False))
        changed += 1

    return changed


def rewrite_one(page, cand, out):
    """
    复核打回后在原位重写一条图内文字。

    能重写的前提是这条当初被识别出来过（cand 还留着框、背景色、字色），
    所以不需要重新 OCR；排版规则与首次写入完全一致。

    返回是否写成。写不成说明译文还是太长、字号会缩到不可读，
    调用方据此如实报「仍未解决」，不能当作已经修好。
    """
    plan = _plan_one(cand, out, None)
    if plan is None:
        return False
    return paint_plans(page, [plan]) > 0
