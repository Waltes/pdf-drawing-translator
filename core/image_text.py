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

import math
import os
import re

import pymupdf

from .paths import DATA_DIR
# 折行规则与文字层共用一套（core/textfit.py）。两边各写一份的话，
# 同一张图上一样宽的框会出现「一处折行、一处留白」的不一致。
from . import layout
from . import textfit
# 字号反算与标点规范也共用一套（core/typography.py）：
# 文字层和位图层写字的方式不同，但「写出来该多大、该有哪些空格」是同一件事。
from . import typography
# 发行版开关：定制版要「严格原位」排版（字号位置都锁死），见 edition.strict_layout
from . import edition
# 工程符号的图形检测（core/glyphs.py）。OCR 读不出来的 ø，
# 只能靠字形结构从图像里找回来，这条链独立成模块便于单独测。
from . import glyphs

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
# 3.5 这个值本身太低 —— 实测有标注被写到 3.53pt，A3 上不到 1.3mm，
# 是一道灰线。真正判定「小到不能要」的界线放在 typography.MIN_READABLE，
# 这里跟着它走，文字层与位图层用同一个数，不各写一份。
MIN_WRITE_SIZE = typography.MIN_READABLE

# 文字朝向的三档分界（度）。取值全部来自 7 份客户图纸的实测分布：
#   横排抖动 —— 实测 0~1.5°，偶尔 2°，留到 12° 绝不会误判；
#   竖排误框 —— 竖排标注被 OCR 框成略斜的四边形，实测 5.9°、6.8°。
#              这一档必须判成竖排（走 _plan_vertical），不能当斜排 ——
#              斜排路径是「沿任意角铺开」，用在竖排上会把字写歪几度，
#              跟旁边的尺寸线不再平行，比原来的问题更显眼；
#   真斜排   —— 实测 21.2°、22.2°（沿斜管/引线的半径标注）。
SLANT_MIN = 12.0      # 起算斜排的角度
SLANT_MAX = 78.0      # 超过这个角度算竖排（走竖排那条路）
# 竖排的兜底判据：OCR 有时把竖排框成轴对齐矩形，角度是 0，只剩长宽比可用。
# 判据从「宽 < 高/1.5」放宽到「宽 < 高×0.9」：实测竖排 70mm 的框是
# 15.0×21.7（1.45），按老阈值判成横排，于是这条竖排标注被水平重写，
# 一条 70mm 横躺在尺寸线旁边 —— 客户报的「竖的字变横的」就是这个。
VERT_ASPECT = 0.9

# 斜排的二次复核（见 _ink_axis）。OCR 四边形只在「一条字斜着写」时可信；
# 它把两行水平字框成一条时给的角度纯属噪声（实测 -28.5°，写出来直接冲出页面）。
# ratio 要够细长才算一条线，角度要和墨迹主轴对得上才算同一件事。
#
# 阈值 3.0 是两个实测值之间取的：真斜排 5.23（合成图 24°，一条 8 字中文），
# 两行水平字被框成一条时 2.18（护栏图纸那条 032*2mm圆管）。取 2.0 会把后者
# 放进来 —— 那条按 28.5° 写回去，69pt 长的标注立刻斜着冲出页顶。
SLANT_RATIO = 3.0
SLANT_TOL = 22.0

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

    「有拉丁字母」这一条要过 typography.needs_translation：
    ø12mm、25mm、0.75kg 里的 mm/kg 是单位后缀，不是词。只看字母的话，
    纯尺寸会被判成英文送进模型 —— 方向上是英译中时正好走这条路，
    模型把 ø 写成 @、把 12 写成 12.00，客户在图上看到的就是 @12.00mm。
    """
    text = text or ""
    if _CJK_RE.search(text) or _KANA_RE.search(text):
        return "zh"
    if _LATIN_RE.search(text) and typography.needs_translation(text):
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

    v1.1.3 起把「字母+数字」的混合串也纳入（QTY750panels、M8x40BOLT）：
    原先只认纯字母，客户图上的数量行就是这么粘着写出去的。
    切分规则统一放在 core/typography.split_glued，切不全就不动。
    """
    t = (text or "").strip()
    if " " in t or len(t) < 5:
        return text
    vocab = _get_vocab()
    if not vocab or t.upper() in vocab:
        return text
    fixed = typography.split_glued(t, vocab)
    return fixed or text


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

    # 整串都对不上时，退一步按词拆开译。
    #
    # 为什么需要这层：客户图上的 SURFACE TREATMENT:Hot-dip galvanized 是一条
    # 完整的标注，整串在术语表里当然没有这一条，于是一直保留英文。
    # 按词拆开之后，「表面处理」「热浸锌」都在表里，整条就能译出来。
    # 代价是词序可能不地道 —— 但图纸标注本来就是短语堆叠，比留着英文强。
    return _translate_by_parts(text)


# 分段兜底的最低命中率。低于这个比例说明 OCR 认错了字或者表里确实没有，
# 硬拼出来的中英混杂比原文更难读，不如保持英文并在清单里报出来。
_PART_HIT_RATIO = 0.6

_GLOSSARY_CACHE = None


def _get_glossary():
    """术语表只加载一次（669 条 CSV，每个词都重读会把整页 OCR 拖慢几倍）"""
    global _GLOSSARY_CACHE
    if _GLOSSARY_CACHE is None:
        try:
            from . import dict_engine
            _GLOSSARY_CACHE = dict_engine.Glossary()
        except Exception:
            _GLOSSARY_CACHE = False
    return _GLOSSARY_CACHE or None


def _get_word_vocab():
    """
    术语库里出现过的**单个英文词**（大写）。

    和 _get_vocab 的区别：那个是整条词条（"DRAWING NO."，带点带空格），
    只能判断「这一整串认不认识」；拆分粘连串需要的是单词表，
    否则 QTY750panels 永远拆不动。
    """
    global _WORD_VOCAB
    if _WORD_VOCAB is None:
        words = set()
        try:
            from .dict_engine import Glossary
            for en in Glossary().en2zh:
                for w in en.split():
                    w = w.strip(".,;:()[]{}/\\-\"'")
                    if len(w) >= 2 and w.isalpha():
                        words.add(w.upper())
        except Exception:
            pass
        _WORD_VOCAB = words
    return _WORD_VOCAB


_WORD_VOCAB = None


def _translate_by_parts(text):
    """
    把一整条拆成词逐段译，命中率够高才采用。

    返回 None 表示「译不动」—— 调用方会保持原文并计入未译清单。
    """
    gl = _get_glossary()
    if gl is None:
        return None
    t = (text or "").strip()
    if not t:
        return None
    # 先把粘在一起的词拆开再逐段译：QTY750panels 这种串不拆开，
    # 每个词都查不到，整条只能原样保留。
    t = typography.split_glued_text(t, _get_word_vocab())
    words = re.findall(r"[A-Za-z][A-Za-z']*", t)
    if not words:
        return None
    try:
        from . import dict_engine
        out, miss = dict_engine.en_to_zh(t, gl)
    except Exception:
        return None
    if not out or out == t:
        return None
    miss_words = [m for m in miss if re.search(r"[A-Za-z]", m)]
    ratio = 1.0 - len(miss_words) / float(len(words))
    if ratio < _PART_HIT_RATIO:
        return None
    return out


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
        ink_box = None                 # 实测墨迹范围，用来把填充框收到字边上
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
                cols = np.where(dark.any(axis=0))[0]
                if len(rows) >= 2 and len(cols) >= 2:
                    ink_h = (rows[-1] - rows[0] + 1) / z
                    # 换回页面坐标。填充框按这个范围算，旁边紧贴的剖面线、
                    # 点划线才不会被一起涂掉
                    ink_box = pymupdf.Rect(
                        clip.x0 + (bx0 + cols[0]) / z,
                        clip.y0 + (by0 + rows[0]) / z,
                        clip.x0 + (bx0 + cols[-1] + 1) / z,
                        clip.y0 + (by0 + rows[-1] + 1) / z)
        except Exception:
            ink_box = None
        # hint 的语义是「原件这一行的墨迹高度」，不是字号。
        # 字号由排版模块按目标文字自己的字面比例反算（typography.size_for_ink）：
        # 同样一个字号，全大写的行（SURFACE TREATMENT）和带下伸部的行
        # （Hot-dip galvanized）写出来差三成高。原来统一除以 0.86 再乘一个
        # 固定系数，结果带 g/y/p 的句子被写成比原件高一到两成 ——
        # 客户看到的就是「字变形了、跟旁边不一样大」。
        hint = ink_h

        cand = {"text": text, "rect": box, "bg": bg, "fg": fg,
                "hint": "en", "vertical": False, "ink_box": ink_box}
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
    for c, out, _fill, _size, _font, _lines, *_rest in plans:
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


def _merge_paragraphs(cands, x_em=0.5, right_em=0.8, gap_em=(-0.4, 2.0)):
    """
    把同一段里被 OCR 按行切开的几行合并成一条（段落级）。只在严格模式用。

    为什么必须做：
        客户图纸右侧那段说明是「10毫米的孔穿透护栏 / 左右两边管各有一个 /
        用于护栏与护栏连接」三行，每行只有 90pt 宽。逐行翻译时每行都要
        独立塞进 90pt，英文比中文长两倍半，只能把字号压到 5~6pt ——
        渲染出来是一片看不清的灰字，客户原话是「这块怎么这么小」。
        三行合成一段后，可用高度是三行的总高，字号能回到 9~10pt。

    判据三条一起用，少一条都会误合并：
      · 左端对齐 —— 折行的段落左端是一致的；而「护栏侧面位置」和它下面
        那段差 21pt，正是靠这条分开的。
      · 下一行不含字段冒号 —— 「表面处理：」这种是独立的字段标签，
        不是上一句的续行。只看左端会把「材料：…」和「表面处理：…」
        并成一句（两者左端都在 127.3）。
      · 下一行不比上一行宽 —— 被自动折行的段落，除最后一行外都是排满的，
        最后一行只会更短。「材料：…」193pt 到「表面处理：…」223pt 是变长，
        说明换了一条信息，不是同一段的收尾。
    """
    if len(cands) < 2:
        return cands
    # 竖排/斜排不参与段落合并：这条规则的每一条判据（左端对齐、行宽收窄）
    # 都是按水平折行推出来的，竖排文字「左端对齐」在竖直方向上是另一回事，
    # 合起来会把尺寸线上互不相干的几条标注串成一句。
    order = sorted(cands, key=lambda c: (c["rect"].y0 + c["rect"].y1) / 2)
    out, cur = [], []
    for c in order:
        if c.get("vertical") or c.get("angle"):
            if cur:
                out.extend(_fold_paragraph(cur))
                cur = []
            out.append(c)
            continue
        if not cur:
            cur = [c]
            continue
        prev = cur[-1]
        h = max(prev["rect"].height, c["rect"].height, 1.0)
        dx0 = abs(c["rect"].x0 - prev["rect"].x0)
        gap = c["rect"].y0 - prev["rect"].y1
        h_ratio = abs((c.get("ink_h") or 0) - (prev.get("ink_h") or 0)) / max(
            prev.get("ink_h") or 1.0, 1.0)
        has_colon = ("：" in c["text"]) or bool(
            re.search(r"(?<![0-9]):(?![0-9])", c["text"]))
        wider = c["rect"].width > prev["rect"].width + h * 0.5
        # 居中缩进也算续行：图纸上「方块支撑80*80*3mm」下一行的「红色部分」
        # 是居中排的，左端差 30pt 但横向中心落在上一行范围内、且更短。
        cx = (c["rect"].x0 + c["rect"].x1) / 2
        centered = (prev["rect"].x0 <= cx <= prev["rect"].x1) and not wider
        left_aligned = dx0 <= h * x_em
        gap_ok = gap_em[0] * h <= gap <= gap_em[1] * h
        # 行距一致性：同一段里被折开的几行，行距是均匀的。
        # 只卡「间距 ≤ 2 倍字高」拦不住两组不同的标注 —— 第二组往往居中
        # 缩进排，会命中上面的 centered 判据。实测托盘图纸右侧：
        # 组内行距 1.0 / -0.2 / 0.4pt（框紧贴或交叠），两组之间 27.0pt
        # （1.78 倍字高），没超过 2.0 上限，于是被当成续行并成一句，
        # 图上读起来成了「红色部分包含边框和内撑」。
        # 再加一道：本行间距要和段内已有的行距对得上，差超过 0.6 字高
        # 就是换了一条信息。用相对差而不是绝对阈值，段内行距本身偏大的
        # 段落不受影响，只有间距突然变大才断。
        if gap_ok and len(cur) >= 2:
            base_gap = cur[-1]["rect"].y0 - cur[-2]["rect"].y1
            gap_ok = abs(gap - base_gap) <= 0.6 * h
        same = ((left_aligned or centered) and not has_colon and not wider
                and gap_ok
                and h_ratio <= 0.20)
        if same:
            cur.append(c)
        else:
            out.extend(_fold_paragraph(cur))
            cur = [c]
    if cur:
        out.extend(_fold_paragraph(cur))
    return out


def _fold_paragraph(lines):
    """把一组行折成一条候选。单行原样返回"""
    if len(lines) < 2:
        return lines
    lines.sort(key=lambda c: c["rect"].y0)
    text = ""
    for c in lines:
        seg = c["text"].strip()
        if not seg:
            continue
        # 行与行之间一律加空格：原文这里是折行，不是词内分隔。
        # 不加的话「穿透护栏」+「左右两边管」会拼成「穿透护栏左右两边管」，
        # 模型读成一个长名词，译出来就是一串没有断句的词。
        if text:
            text += " "
        text += seg
    first = lines[0]
    rect = pymupdf.Rect(min(c["rect"].x0 for c in lines),
                        min(c["rect"].y0 for c in lines),
                        max(c["rect"].x1 for c in lines),
                        max(c["rect"].y1 for c in lines))
    inks = [c.get("ink_box") for c in lines if c.get("ink_box")]
    ink = None
    if inks:
        ink = pymupdf.Rect(min(i.x0 for i in inks), min(i.y0 for i in inks),
                           max(i.x1 for i in inks), max(i.y1 for i in inks))
    sizes = [c.get("ink_h") or 0 for c in lines]
    return [{"text": text, "rect": rect, "bg": first.get("bg"),
             "fg": first.get("fg"), "hint": ocr_lang(text),
             "vertical": first.get("vertical", False),
             "angle": first.get("angle", 0.0),
             "ink_h": max(sizes) if sizes else 0.0, "ink_box": ink}]


def _join_split(a, b):
    """
    把同一行上相邻的两块 OCR 结果拼成一条。

    三件事要一起做，漏一件都会留下难看的痕迹：
      1. 拼文本。两侧都是汉字就不加空格（「材料：」+「外方管」），
         有一侧是西文才加，否则会写出「MATERIAL: OUTERSQUARE」。
      2. 去重复标点。OCR 常把冒号同时留在前一块的尾和后一块的头 ——
         实测「数量：」+「：750片」，直接拼就是两个冒号，
         这正是客户截图里 QTY:: 的来源（旧版把它当翻译的错，其实在更上游）。
      3. 框取并集。合并后的框才是这一行原文的完整范围，
         字号按它算才不会偏小、排版才有地方折行。
    """
    ta, tb = a["text"].rstrip(), b["text"].lstrip()
    punct = "：:，,；;、。."
    if ta and tb and ta[-1] in punct and tb[0] in punct:
        tb = tb[1:].lstrip()
    if ta and tb and not (textfit.is_wide(ta[-1]) or textfit.is_wide(tb[0])
                          or ta[-1] in punct):
        text = ta + " " + tb
    else:
        text = ta + tb
    ra, rb = a["rect"], b["rect"]
    rect = pymupdf.Rect(min(ra.x0, rb.x0), min(ra.y0, rb.y0),
                        max(ra.x1, rb.x1), max(ra.y1, rb.y1))
    ia, ib = a.get("ink_box"), b.get("ink_box")
    ink = None
    if ia and ib:
        ink = pymupdf.Rect(min(ia.x0, ib.x0), min(ia.y0, ib.y0),
                           max(ia.x1, ib.x1), max(ia.y1, ib.y1))
    elif ia or ib:
        ink = pymupdf.Rect(ia or ib)
    return {"text": text, "rect": rect, "bg": a.get("bg"), "fg": a.get("fg"),
            "hint": ocr_lang(text), "vertical": a.get("vertical", False),
            "angle": a.get("angle", 0.0),
            "ink_h": max(a.get("ink_h") or 0.0, b.get("ink_h") or 0.0),
            "ink_box": ink}


def _merge_split_labels(cands, gap_em=0.6, row_em=0.35):
    """
    把同一行上被 OCR 切碎的标注合并回一条。只在严格模式用。

    为什么非合并不可（2026-11-18 实测这份图纸）：
        OCR 把「材料：外方管20*20*1.5mm，」切成 127~169 和 171~319 两块，
        第一块只剩 42pt 宽。英文 MATERIAL: 五个字要 48pt，放不下就一路缩，
        最后写成 9.13pt —— 而原文墨迹高度是 21。客户看到的「字突然变小」
        根本不是排版算法缩的，是上游把一条句子切碎了。
        同理「表面处理：」+「热镀锌材料喷塑黑色」被切成两块，各自排，
        一块缩到 11pt、另一块也缩，整行看着就是拼贴。

    两道阈值：
      row_em  同一行 —— 纵向中心差不超过 0.35 个字高。图纸上同一行的标注
              基线是一致的，而上下相邻两行之间至少差一个行距（实测 1.7 字高），
              0.35 落在这两者中间，分得开。
      gap_em  行距 —— 允许 -0.4 到 2.0 个字高。下限给负值是必须的：
              OCR 框是贴着笔画给的，两行字挨得紧时后一行的框顶会压进前一行
              的框底（实测「方块支撑80*80*3mm」底 267.3、「红色部分」顶 267.0，
              差 -0.3pt）。按正值卡，这对就永远合并不了，客户看到的就是
              「RED PART 比上面那行小一半」。
      另外两条是「像不像同一段」的正反判据，见下面的 has_colon / wider。
      gap_em  同一句 —— 横向间距不超过 0.6 个字高。实测同一条内部的字间距是
              0.1~0.25 字高，相邻两列之间是 1.7 字高以上，0.6 卡在中间。
              不按钮字宽判断，因为「材料：」和「外方管」之间只隔了 2pt，
              而「内方管」和「尺寸：」隔了 8pt，两处都是同一条。
    """
    if len(cands) < 2:
        return cands
    rows, cur = [], []
    for c in sorted(cands, key=lambda x: (x["rect"].y0 + x["rect"].y1) / 2):
        cy = (c["rect"].y0 + c["rect"].y1) / 2
        if cur:
            prev = cur[-1]
            py = (prev["rect"].y0 + prev["rect"].y1) / 2
            h = max(prev["rect"].height, 1.0)
            if abs(cy - py) > h * row_em:
                rows.append(cur)
                cur = []
        cur.append(c)
    if cur:
        rows.append(cur)

    out = []
    for row in rows:
        row.sort(key=lambda c: c["rect"].x0)
        merged = [row[0]]
        for c in row[1:]:
            prev = merged[-1]
            h = max(prev["rect"].height, c["rect"].height, 1.0)
            gap = c["rect"].x0 - prev["rect"].x1
            ok = gap <= h * gap_em
            # 上一块以逗号/顿号/冒号收尾 —— 这句话没说完，下一块是它的后半截。
            # 这类拼接允许更宽的间距（1.2 字高）：客户图纸底部
            # 「材料：外方管…，内方管…，尺寸：…，」三块之间隔了 8~17pt，
            # 按 0.6 字高合不起来，各自成条后被右边界卡死、只能一路缩字号，
            # 实测「材料」那一条缩到 8.6pt，而同一基线上邻块是 14.6pt。
            if not ok and prev["text"].rstrip().endswith(("，", "、", "：", "；", ",")):
                ok = gap <= h * 1.2
            if ok:
                merged[-1] = _join_split(prev, c)
            else:
                merged.append(c)
        out.extend(merged)
    return out


def _quad_angle(box):
    """
    OCR 检测框给的方向角（度），-90~90。

    为什么能拿到角度：RapidOCR 的检测框本来就是一个四边形，斜着写的标注
    框也是斜的 —— 旧代码只取 min/max 拼成轴对齐矩形，顺手把这个信息丢了，
    于是斜排和横排在下游看起来一模一样，永远按水平重写。

    取 p0→p1 这条边：点序是「左上、右上、右下、左下」，这条边与文字基线
    平行，就是文字走向。屏幕坐标 y 向下，所以「从左下往右上写」是负角。

    噪声：竖排标注会被检测成略斜的框（实测 5.9°、6.8°），横排也有 1~2° 抖动。
    所以调用方要按阈值分档，不能见到非零角度就当斜排。
    """
    try:
        (x0, y0), (x1, y1) = box[0], box[1]
        a = math.degrees(math.atan2(y1 - y0, x1 - x0))
    except Exception:
        return 0.0
    # 归到 -90~90：atan2 给的是 -180~180，且点序反了会得到 180° 附近的值
    while a > 90:
        a -= 180
    while a <= -90:
        a += 180
    return a


def _fix_ink_for_slanted(text, ink_h, rect):
    """
    斜排文字的墨迹高修正：按框宽反推真实字高。

    OCR 给的框是轴对齐包围盒。斜着写的标注，纵向尺寸是对角线撑出来的，
    比真实字高大得多 —— 实测 `ø32*2mm圆管` 沿斜管写，墨迹量出 47.5，
    同页真标题才 36.8。拿它算字号会写出 38pt 的大字，一条标注折成三行
    堆在图形上方，比不翻还难看。

    判据两条一起用，缺一条都会误伤：
      · 框的宽高比在 1.0~3.0。斜排的字横向还是铺开的（实测 1.47、1.49），
        竖排（`125mm`、`1103mm` 那种）宽高比不到 0.5，绝不能进这里 ——
        它们的墨迹高是真的，按宽度反推会把尺寸字砍掉一半。
      · 按墨迹高算，这串字水平排开该有的宽度，框宽连六成都不到。
        正常横排文字这条不会命中（实测 3.7~9.1 倍的宽高比，远高于阈值）。

    命中就用「框宽 ÷ 每单位墨迹高的横向宽度」反推墨迹高。
    """
    s = text or ""
    if not s or ink_h <= 0 or rect.height <= 0 or rect.width <= 0:
        return ink_h
    ratio = rect.width / rect.height
    if not (1.0 < ratio < 3.0):
        return ink_h
    wide = sum(1 for ch in s if typography.is_wide(ch))
    per_ink = wide * 0.95 + (len(s) - wide) * 0.55
    if per_ink <= 0 or rect.width >= ink_h * per_ink * 0.6:
        return ink_h
    return max(typography.MIN_SIZE, rect.width / per_ink)


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

            # 文字朝向：横排 / 竖排 / 斜排三档。
            #
            # 判据分两层，缺一层都会出错：
            #   第一层看 OCR 四边形本身的角度（_quad_angle）。这是唯一可靠的
            #   斜排证据 —— 框是斜的，说明这一串字是斜着写的。
            #   第二层看长宽比，只用来兜竖排：OCR 对竖排常常给的是轴对齐矩形
            #   （角度 0），这时没有角度可用，只能看「是不是竖着长」。
            #
            # 为什么必须分清楚：竖排框只有十几点宽，当成横排排版，几百点长的
            # 译文会把字号压到 1 点，图上是一块白斑（客户报的就是这个）；
            # 斜排若当成横排，字会被水平写回去，与旁边斜着的引线、管子脱开，
            # 一眼就能看出是后贴上去的。
            bw = max(xs) - min(xs)
            bh = max(ys) - min(ys)
            _ang = _quad_angle(box)
            if abs(_ang) >= SLANT_MAX:
                vertical, slant = True, 0.0
            elif abs(_ang) >= SLANT_MIN:
                # OCR 说这条是斜的，先问一句墨迹自己：真是一条细长的斜字吗。
                # 不做这一步，OCR 把两行水平字框成一条时那个角度会被当真
                # （实测 -28.5°），一条 69pt 的标注斜着冲出页面顶部。
                real, ratio = _ink_axis(rgb, clip, txt_rect, zoom)
                if ratio >= SLANT_RATIO and abs(real - _ang) <= SLANT_TOL:
                    vertical, slant = False, _ang
                else:
                    vertical, slant = False, 0.0
            elif bw < bh * VERT_ASPECT:
                vertical, slant = True, 0.0
            else:
                vertical, slant = False, 0.0

            bg, fg, _std = sampled
            # 实测墨迹高度与水平范围：字号要跟原字一样高就得知道原字多高，
            # 填充框要只盖字本身就得知道笔画到哪儿为止
            _ink = _measure_ink_h(rgb, clip, txt_rect, zoom)
            # OCR 会把直径符号整个吞掉：整页 300dpi 读成「流水孔12mm」，
            # 800dpi 读成「流水孔012mm」，切出单字送识别直接给空。
            # 翻译模型是可靠的（给它 ø 就原样保留，不给也不凭空补），
            # 所以缺口只能在读字这一端补 —— 从字形结构上把符号找回来。
            if not vertical:
                fixed, added = glyphs.complete_symbols(
                    page, clip, txt_rect, zoom, text, rgb=rgb)
                if added and fixed != text:
                    text = fixed
                    hint = ocr_lang(text)
            candidates.append({"text": text, "rect": txt_rect, "bg": bg,
                               "fg": fg, "hint": hint, "vertical": vertical,
                               "angle": slant,
                               # 四边形原样留着：斜排的字身方向、轴向长度、
                               # 擦除范围都得靠它算 —— 轴对齐框的投影里混着
                               # 两个分量，斜着量一定偏大（实测引线被框进来，
                               # 擦除时啃掉一截）。
                               "quad": [pymupdf.Point(clip.x0 + p[0] / zoom,
                                                      clip.y0 + p[1] / zoom)
                                        for p in box],
                               "ink_h": _fix_ink_for_slanted(text, _ink[0],
                                                             txt_rect),
                               "ink_box": _ink[1]})

    if not candidates:
        return 0

    # 严格模式：先把被 OCR 切碎的同一条标注拼回去，再翻译、再排版。
    # 必须放在翻译之前 —— 切碎翻译出来的英文是按半句翻的，前半句
    # MATERIAL: 后面没有宾语，翻出来也不通。
    if edition.strict_layout():
        candidates = _merge_split_labels(candidates)
        # 再按段落合并：同一条说明被 OCR 按行切开时，逐行排会把字号压到看不清
        candidates = _merge_paragraphs(candidates)

    if os.environ.get("PT_DEBUG"):
        import sys as _sys
        for _c in sorted(candidates, key=lambda x: (x["rect"].y0, x["rect"].x0)):
            _r = _c["rect"]
            print(f"[cand] ({_r.x0:6.1f},{_r.y0:6.1f})-({_r.x1:6.1f},{_r.y1:6.1f})"
                  f" ink={_c.get('ink_h', 0):5.1f}"
                  f" ang={_c.get('angle', 0):5.1f}"
                  f" v={int(bool(_c.get('vertical')))}"
                  f" {_c['text']!r}", file=_sys.stderr)

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

    # 页面占用图：必须在**任何写入之前**采一次，否则我们刚涂上去的背景色
    # 和刚写上去的英文会被当成「原有内容」，自己把自己挡住。
    #
    # 不做「一次性释放所有标注框」：那样等于宣布所有标注的地盘都是无主的，
    # 后一条会搬进前一条的位置，两条标注互换位置。改成每条只忽略自己那块
    # （见 _plan_one 传下去的 ignore）。
    #
    # 构造失败不能影响出图：退回旧算法（各写各的，不做空间检查），
    # 只是重叠问题回来而已，不该让整页翻译失败。
    space = None
    try:
        space = layout.PageSpace(page)
    except Exception:
        space = None

    # v1.0.8：把「每条怎么排」和「怎么落到页面上」拆成两个模块函数。
    # 目的是让复核打回的那几条能在原位重写一遍 —— 重写要走的正是同一套
    # 排版与盖字逻辑，复制一份出来迟早会两边不一致。
    # v1.1.6：先按原件字高归类，同类译文才可能写出同一个字号。
    # 必须在排版之前做完 —— 字号是排版的输入，不是它的产物；
    # 等每条算完再回头对齐，等于让后算的去迁就先算的。
    # 严格模式（定制版）跳过归类：字号按每条自己的原字高直接算，
    # 不追求「同类同字号」—— 追求同类同字号就必须允许那条长的缩一点，
    # 而客户要的是每条都跟自己的原文一样大。
    if not edition.strict_layout():
        _assign_size_classes(candidates, outs)

    # v1.1.7：标出哪些是「对着图形居中」的标题。也要在排版之前做完 ——
    # 它决定译文往哪边长（绕重心对称长，还是一头固定往另一头伸）。
    # 放在归类和占用图之后：判据要用到实测字高和页面占用情况。
    #
    # 严格模式也要跑（2026-11-19 客户反馈「标题没有居中」）：
    #   全部左对齐的结果是标题向右长，重心被推走。但这条判据自带两道关
    #   （字号须是本页最大档的 85% 以上 + 原框中心与图形中心差半字高内），
    #   标注根本进不来，只有视图标题会被标 center。
    #   标注仍然左对齐 —— 客户 11-18 要的「左对齐」说的是标注，不是标题。
    try:
        _assign_align(page, candidates, space,
                      by_size_only=edition.strict_layout())
    except Exception:
        pass

    # 同一行并排的字段：标出每条向右最多能伸到哪。
    # 必须在排版之前算完，理由同上面两步 —— 位置是排版的输入。
    try:
        _mark_right_stop(candidates)
    except Exception:
        pass

    plans = []
    for c, out in zip(candidates, outs):
        # 纯标点（原文只有一个「。」「，」）：没有可译内容，也不该留在英文图上。
        # OCR 会把句末那个全角句号单独切出来，中译英后它就那么在图上待着，
        # 第 2 页底部「POWDER COATING」下面那个孤零零的「。」就是它。
        # 这里只擦不写 —— 写一遍还是同一个符号。
        if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", c["text"]):
            try:
                page.draw_rect(c["rect"], color=None,
                               fill=tuple(float(v) / 255 for v in c["bg"]),
                               width=0)
            except Exception:
                pass
            continue
        plan = _plan_one(c, out, untranslated, font_cjk, font_latin,
                         glyph_h_hint=c.get("ink_h"), space=space)
        if plan is not None:
            plans.append(plan)
    # 收口：同类里落单的那几条拉回到同一个字号。必须在登记记录之前做 ——
    # 记录里的回读框要跟着最终写框走，拉齐改了写框，记录也得是新的。
    # 严格模式跳过：这一步会改已定稿的字号，与「字号锁死」直接冲突。
    if not edition.strict_layout():
        _align_class_sizes(plans)
    else:
        # 严格模式只对「居中的标题」收口，其余一律不碰。
        # 理由：标题是同一页里成套出现的（底座平面图 / 底座侧面图），
        # 客户看到的是「两个标题是不是一样大」。字号由各自源字高反算，
        # 同高不同字组合反算出来会差几个点（实测 28.67 / 30.40，5.7%），
        # 并排放着能看出来。标注不做这一步 —— 每条都跟自己的原文对齐。
        _align_center_sizes(plans)

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
            # ink 里三个框都要：原 OCR 框、实际盖字用的外扩框、真正落笔的写框。
            # 折行时第一行会贴到框顶之外，只按原框回读会读不到、
            # 被判成空白块（复核打回重译也修不好，纯空转）；
            # 有占用图之后译文可能被加宽或挪了一行，写框也要一起回读。
            mark = {"page": None, "rect": c["rect"],
                    "ink": [c["rect"], p[2], p[7] if len(p) > 7 else c["rect"]],
                    "out": p[1], "src": c["text"]}
            if write_marks is not None:
                write_marks.append(mark)
        if fixes is not None:
            fixes.append({"page": None, "kind": "image", "obj": c,
                          "src": c["text"], "rect": c["rect"],
                          "ink": ([c["rect"], p[2], p[7]] if len(p) > 7 else
                                  [c["rect"], p[2]]) if p is not None else [c["rect"]],
                          "out": p[1] if p else None, "mark": mark})

    return paint_plans(page, plans, report)


def _measure_ink_h(rgb, clip, box, zoom):
    """
    量一个 OCR 框里实际笔迹的高度（点数）。

    OCR 框是贴着字给的，但带留白：实测 9pt 的框里字只有 5pt 高。按框高定字号
    会写成比原文大四成的一行字，客户看到的就是「字变形了、跟旁边不一样大」。

    只在框内找深色像素，框外的线条不参与。量不出来时按框高七成估 ——
    这个退路比不估好：估错了只是字号偏大偏小，不估就是框高，偏大更多。
    """
    np = _numpy()
    ink_h = box.height * 0.72
    ink_box = None
    try:
        x0 = max(int((box.x0 - clip.x0) * zoom), 0)
        y0 = max(int((box.y0 - clip.y0) * zoom), 0)
        x1 = min(int((box.x1 - clip.x0) * zoom), rgb.shape[1])
        y1 = min(int((box.y1 - clip.y0) * zoom), rgb.shape[0])
        sub = rgb[y0:y1, x0:x1]
        if sub.size:
            dark = sub.min(axis=2) < 180
            rows = np.where(dark.any(axis=1))[0]
            cols = np.where(dark.any(axis=0))[0]
            if len(rows) >= 2 and len(cols) >= 2:
                ink_h = (rows[-1] - rows[0] + 1) / zoom
                # 顺带把笔迹的水平范围也量出来，回到页面坐标。
                # 填充框按这个范围收，旁边紧贴的剖面线、点划线才不会被涂掉。
                ink_box = pymupdf.Rect(
                    clip.x0 + (x0 + cols[0]) / zoom,
                    clip.y0 + (y0 + rows[0]) / zoom,
                    clip.x0 + (x0 + cols[-1] + 1) / zoom,
                    clip.y0 + (y0 + rows[-1] + 1) / zoom)
    except Exception:
        ink_box = None
    return ink_h, ink_box


def _ink_axis(rgb, clip, box, zoom):
    """
    框内墨迹的主轴方向和「细长程度」。用来复核 OCR 给的斜排角度。

    为什么需要复核（2026-11-19 护栏图纸实测）：
        `032*2mm圆管 / ROUND TUBE` 是两行水平字，OCR 却给了一个 -28.5° 的
        四边形。按角度判成斜排、旋转 28° 写回去，一条 69pt 长的标注立刻
        斜着冲出页面顶部（y=-2.3），复核抓出「越界 1 处」。
        OCR 的四边形只在「一条文字斜着写」时可信；它把两行字框成一条时，
        四条边给出的角度没有意义。

    判据两条，都要满足才算真斜排：
      · 主轴必须细长 —— 单行文字的墨迹沿走向铺开，长度远大于字高；
        两行水平字堆在一起像个方块，长短轴差不多（本函数返回的 ratio 小）。
      · 主轴角度要和 OCR 给的角度基本一致 —— 差得远说明四边形不可信。

    返回 (角度, ratio)。角度按主轴向量算，归到 -90~90；ratio 是
    长短轴标准差之比，1.0 表示是个圆（谈不上方向）。
    """
    np = _numpy()
    try:
        x0 = max(int((box.x0 - clip.x0) * zoom), 0)
        y0 = max(int((box.y0 - clip.y0) * zoom), 0)
        x1 = min(int((box.x1 - clip.x0) * zoom), rgb.shape[1])
        y1 = min(int((box.y1 - clip.y0) * zoom), rgb.shape[0])
        sub = rgb[y0:y1, x0:x1]
        if sub.size == 0:
            return 0.0, 1.0
        dark = sub.min(axis=2) < 180
        ys, xs = np.nonzero(dark)
        if len(xs) < 24:
            return 0.0, 1.0
        xs = xs.astype(float) - xs.mean()
        ys = ys.astype(float) - ys.mean()
        cov = np.cov(np.vstack([xs, ys]))
        w, v = np.linalg.eigh(cov)      # 升序：w[1] 是主轴方差
        major, minor = float(w[1]), float(w[0])
        if major <= 0:
            return 0.0, 1.0
        ratio = math.sqrt(major / max(minor, 1e-6))
        ang = math.degrees(math.atan2(float(v[1, 1]), float(v[0, 1])))
        while ang > 90:
            ang -= 180
        while ang <= -90:
            ang += 180
        return ang, ratio
    except Exception:
        return 0.0, 1.0


def _assign_size_classes(candidates, outs):
    """
    把原件里「一样大」的文字归成一类，同类译文用一个字号。

    用原件实测的墨迹高度归堆，不看框高（框带着留白，同高的字能差两成）。
    每类取成员高度的中位数当这一类的目标高度，之后排版时每一条都按
    「目标高度等于类高度」反算字号 —— 于是四个视图标题写出来一样高。

    注意这里统一的是**字面高度**，不是字号数字：全大写的 FRAME TOP VIEW
    和带下伸部的 Exterior front view，要写成同样高，字号数字本来就不该相同。
    客户看的是「一样大」，不是「同一个字号数字」。

    长句在窄框里可能确实放不到类高度，那一类会走「特例」通道（单独缩小并
    在报告里点名），而不是把整类的字号都拖下来陪它 —— 一条长句拖小一页标题，
    正是这一版要修的问题。
    """
    items = []
    for c, out in zip(candidates, outs):
        if not out or not out.strip():
            continue
        # 斜排不参与同类归堆：它的墨迹高是「框宽 ÷ 每字宽」反推出来的估计值，
        # 跟横排直接量到的墨迹高不是一个量，塞进同一堆会把整类的目标高度拽偏，
        # 结果一页横排标题跟着斜着那条标注一起改字号。
        if c.get("angle"):
            c["cls_ink"] = None
            continue
        vertical = c.get("vertical", False)
        h = c.get("ink_h")
        if not h or h <= 0:
            # 没量到墨迹高度时按经验估：横排框高含两成留白，竖排取框宽
            h = c["rect"].width if vertical else c["rect"].height * 0.72
        items.append((float(h), c))
    if not items:
        return
    items.sort(key=lambda t: t[0])
    classes = []
    for h, c in items:
        # 与当前类的基准高度比：相差在容差内就算同一类
        if classes and h <= classes[-1][0] * (1 + typography.CLASS_TOL):
            classes[-1][1].append((h, c))
        else:
            classes.append([h, [(h, c)]])
    for _base, members in classes:
        hs = sorted(h for h, _ in members)
        median = hs[len(hs) // 2]
        for _h, c in members:
            c["cls_ink"] = median


def _fill_rect(r, ink=None):
    """
    盖掉原文用的填充矩形。

    优先按**实测墨迹范围**收框（ink，原件里那一行字真正的像素边界），只留
    0.5pt 抗锯齿余量。原因：原来的做法是「OCR 框再向外扩最多 2pt」，而 OCR
    框本身已经比字大一圈，两下一叠就探出去三四个点 —— 图纸上标注紧贴剖面线、
    点划线，这几毫米足够把线切出一道齐口，客户截图里 Inner frame 上方那条
    剖线就是这么断的。收到底字的边界上，就不碰旁边的线了。

    ink 不可信（没量到、只量到一小块、超出 OCR 框太多）时退回原来的外扩：
    宁可多盖一点，也不能留半截原字变成叠影。
    """
    if ink is not None:
        try:
            ok = (ink.width >= r.width * 0.5 and ink.height >= r.height * 0.35
                  and r.contains(ink))
            if ok:
                pad = 0.5
                return pymupdf.Rect(ink.x0 - pad, ink.y0 - pad,
                                    ink.x1 + pad, ink.y1 + pad)
        except Exception:
            pass
    pad = max(min(r.height * 0.32, 2.0), 0.6)
    return pymupdf.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad)


def _box_from(x0, base, size, width, n_lines):
    """
    一行（或多行）译文实际占的矩形。

    基线以上取 INK_TOP 倍字高（cap height + ascender 余量），以下取
    DESCENDER 倍字高留给 y/g/p 的下伸部。多行时再往上叠 (n-1) 个行距。

    这个矩形是空间检查的依据 —— 检查的必须和真正画出来的范围一致，
    否则「检查通过」就成了空话：按框高查、按字高画，下伸部照样掉到外面。
    """
    lead = textfit.WRAP_LINE_LEAD
    top = base - size * typography.INK_TOP - (max(1, n_lines) - 1) * size * lead
    return pymupdf.Rect(x0, top, x0 + width, base + size * typography.DESCENDER)


def _balanced_two(font, text, size):
    """把一句话拆成大体等宽的两行。拆不开（只有一个词）返回 None"""
    words = text.split()
    if len(words) < 2:
        return None
    best = None
    for i in range(1, len(words)):
        a = " ".join(words[:i])
        b = " ".join(words[i:])
        widest = max(font.text_length(a, fontsize=size),
                     font.text_length(b, fontsize=size))
        if best is None or widest < best[0]:
            best = (widest, [a, b])
    return best[1] if best else None


def _drift_steps(drift):
    """
    重心允许让位时，从小到大试的几个位置：先原地，再半个字高，最后一个字高。

    顺序就是优先级 —— _place_for_size 取第一个放得下的，所以先试偏移小的，
    能少挪就少挪。左右都给，是因为挡住它的可能是左边也可能是右边。
    """
    if drift <= 0:
        return [0.0]
    return [0.0, -drift * 0.5, drift * 0.5, -drift, drift]


def _candidates(r, size, width, n_lines, gap, shift_x, shift_y,
                align="left", drift=0.0, anchor_fallback=True):
    """
    在预算之内，这条译文能落在哪几个位置。

      · 居中（align="center"）：绕原框重心对称加宽，重心原地不动
      · 靠边（align="left"）：沿原基线加宽，左端不动或右端不动，各一个候选
      · 上下：整条上移一行、整条下移一行

    为什么必须分这两种：视图标题在原件里是「对着图形居中」的，不是齐左。
    只有左右两个候选时，译文比原文长一倍，重心必然被推走一半长度 ——
    实测第 2 页四个标题右移 40~45pt（视图中线在 247，标题跑到 292），
    第 1 页那个还顶到了图框线上。居中文字就该两头一起长。

    横竖分开给预算：左右延伸还在同一条基线上，读起来还是同一个信息点；
    上下挪动会把它从原来那一行挪走，客户一眼看出「这两条不挨着了」。
    """
    desc = size * typography.DESCENDER
    base0 = r.y1 - desc
    out = []
    if align == "center":
        # 对称加宽：左右各长一半。预算按两侧一起算，所以是 2 倍 shift_x
        if width <= r.width + 2 * shift_x:
            cx = (r.x0 + r.x1) / 2.0
            for off in _drift_steps(drift):
                out.append(_box_from(cx + off - width / 2.0, base0,
                                     size, width, n_lines))
    if width <= r.width + shift_x and (align != "center" or anchor_fallback):
        # 右端生长（左对齐保持）与左端生长（右对齐保持）。
        # 居中的那几条只在最后兜底时才允许退回这两种长法 ——
        # 它们是「重心跑掉」的根源，但总比留着一串英文强。
        out.append(_box_from(r.x0, base0, size, width, n_lines))
        out.append(_box_from(r.x1 - width, base0, size, width, n_lines))
    # 上移：基线抬到原框上沿再加 gap
    if r.height + gap <= shift_y:
        out.append(_box_from(r.x0, r.y0 - gap, size, width, n_lines))
    # 下移：整个字身上沿落到原框下沿以下
    if gap + size * typography.INK_TOP + desc <= shift_y:
        out.append(_box_from(r.x0, r.y1 + gap + size * typography.INK_TOP,
                             size, width, n_lines))
    return out


def _place_for_size(space, r, font, out, size, shift_x, shift_y, ignore=None,
                    align="left", drift=0.0, safe=None, anchor_fallback=True,
                    allow_wrap=True, pad=None):
    """
    在给定字号上找一个能写下的位置，返回 (写框, 行列表) 或 None。

    一行两行都试：一行最省地方，两行能换来更大的字号。同一字号下先试一行 ——
    能在原框里一行写完，就不折成两行，看着更像原图。

    ignore 只传本条自己的填充框：那块马上要被涂成背景，不算障碍。
    align/drift：见 _candidates（居中文字绕重心长）。
    safe：离图框的安全区，见 layout.PageSpace.is_clear。
    allow_wrap：标题通常不许折行 —— 同一页几个标题，一个折成两行、
    其余一行，比整体缩一成字更扎眼。折行的开关在 _search 第二档才打开。
    pad：与旁边内容要留的净空，None 用 layout.EXTRA_CLEAR（0.3pt）。
    """
    gap = max(1.2, size * 0.15)
    variants = [(1, [out])]
    two = _balanced_two(font, out, size) if allow_wrap else None
    if two:
        variants.append((2, two))
    for n, lines in variants:
        width = max(font.text_length(seg, fontsize=size) for seg in lines)
        for box in _candidates(r, size, width, n, gap, shift_x, shift_y,
                               align=align, drift=drift,
                               anchor_fallback=anchor_fallback):
            opts = {} if pad is None else {"pad": pad}
            if space.is_clear(box, ignore=ignore, safe=safe, **opts):
                return box, lines
    return None


def _best_size(space, r, font, out, lo, hi, shift_x, shift_y, ignore=None,
               align="left", drift=0.0, safe=None, anchor_fallback=True,
               allow_wrap=True, pad=None):
    """
    在 [lo, hi] 里二分找「放得下且有地方放」的最大字号。

    字号越大越难放下（越宽越高），所以二分成立。返回 (字号, 写框, 行列表)，
    一个都放不下时返回 None。shift_x / shift_y 是这一轮允许挪多远，
    都传 0 表示只准站在原地。
    """
    if hi <= 0:
        return None
    lo = min(lo, hi)
    best = None
    for _ in range(9):
        mid = (lo + hi) / 2
        got = _place_for_size(space, r, font, out, mid, shift_x, shift_y,
                              ignore=ignore, align=align, drift=drift,
                              safe=safe, anchor_fallback=anchor_fallback,
                              allow_wrap=allow_wrap, pad=pad)
        if got is not None:
            best = (mid, got[0], got[1])
            lo = mid
        else:
            hi = mid
    return best


def _pick(cur, cand, r, ink, align):
    """
    两个候选里挑一个：字号大的优先，但「重心挪了多少」要折价。

    折价换算见 typography.CENTER_DRIFT_COST：重心每偏一个字高，折一成字号。
    所以偏半个字高只换来一成以内的字号，就守着重心不要动 ——
    标题缩一成的字读者看不出，从视图中线偏出去一眼就看得出。

    非居中的文字没有重心可言（靠左就是靠左），只比字号。
    """
    if cand is None:
        return cur
    if cur is None:
        return cand

    def worth(p):
        size, box = p[0], p[1]
        # 折行要打折：单行只让一成字号就该守住单行，理由见 typography.WRAP_COST
        if p[2] is not None and len(p[2]) > 1:
            size *= typography.WRAP_COST
        if align != "center" or ink <= 0:
            return size
        off = abs((box.x0 + box.x1) / 2.0 - (r.x0 + r.x1) / 2.0) / ink
        return size * (1.0 - typography.CENTER_DRIFT_COST * min(off, 1.0))

    return cand if worth(cand) > worth(cur) + 1e-6 else cur


def _search(space, r, font, out, lo, hi, glyph_h, align, safe,
            widen, budget, ignore=None, pad=None):
    """
    四轮试探，一轮比一轮让步，返回「最值得要」的那个落点（字号, 框, 行）。

      第一轮 原地不动，重心也不动
      第二轮 沿基线加宽（居中的对称加宽，靠边的按原对齐方向长）
      第三轮 居中的才走：允许重心让半个字高，换更大的字号
      第四轮 上下挪一行（最后手段：一挪就不再和原来那条标注挨着）

    每一轮的结果都交给 _pick 折价比较，不是简单的「能放下就用」。
    原因是位置和字号是两件互相能换的东西：标题缩一成的字，读者看不出；
    从视图中线偏出半个字高，一眼就看得出。

    标题额外守一条：**先保一行**。折成两行的标题，字号能大一点，
    但同一页里三个标题两个一行、一个两行，比整体缩一成字更扎眼
    （实测第 2 页「Exterior top view」就是这么被折成两行的）。
    所以标题先按「不许折行」找一遍，能到类字号的九成就用；
    到了九成才说明这一行真的排不下，才允许折。
    """
    def rounds(allow_wrap):
        best = _best_size(space, r, font, out, lo, hi, 0.0, 0.0,
                          align=align, safe=safe, anchor_fallback=False,
                          allow_wrap=allow_wrap, pad=pad, ignore=ignore)
        # 居中的标题：加宽这一轮**必须**走，不能因为「原地已经够九成」就收手。
        # 实测第 2 页那个俯视图标题，原地正好能放到 17.42（够九成，够停），
        # 于是加宽那一轮没跑，整页四个标题跟着掉到 17.42 —— 而对称加宽明明
        # 能放到 19.09。标题的长度本来就该随图形铺开，这一轮对它没有代价。
        #
        # 第一轮交了折行结果的也要走：原地能塞下两行，不代表一行不行。
        # 实测「流水孔 ø12mm」原地折成两行能放到 12.51（够九成，于是收手），
        # 而沿基线加宽其实一行就能放到 12.19 —— 差 2.5% 的字号，
        # 却把原本一行的标注拆成两行，还压到下面那条 Red part 上。
        if (align == "center" or best is None
                or best[0] < hi * typography.CLASS_FLOOR
                or (best[2] is not None and len(best[2]) > 1)):
            best = _pick(best, _best_size(space, r, font, out, lo, hi,
                                          widen, 0.0, align=align,
                                          safe=safe, anchor_fallback=False,
                                          allow_wrap=allow_wrap, pad=pad,
                                          ignore=ignore),
                         r, glyph_h, align)
        if align == "center" and (best is None
                                  or best[0] < hi * typography.CLASS_FLOOR):
            best = _pick(best, _best_size(space, r, font, out, lo, hi,
                                          widen, 0.0, align=align,
                                          drift=glyph_h * typography.CENTER_DRIFT_EM,
                                          safe=safe, anchor_fallback=False,
                                          allow_wrap=allow_wrap, pad=pad,
                                          ignore=ignore),
                         r, glyph_h, align)
        if best is None or best[0] < hi * typography.CLASS_FLOOR:
            best = _pick(best, _best_size(space, r, font, out, lo, hi,
                                          widen, budget, align=align,
                                          drift=glyph_h * typography.CENTER_DRIFT_EM,
                                          safe=safe, anchor_fallback=False,
                                          allow_wrap=allow_wrap, pad=pad,
                                          ignore=ignore),
                         r, glyph_h, align)
        return best

    if align == "center":
        one = rounds(allow_wrap=False)
        if one is not None and one[0] >= hi * typography.CLASS_FLOOR:
            return one
        return _pick(one, rounds(allow_wrap=True), r, glyph_h, align)
    return rounds(allow_wrap=True)


def _box_down(x0, base, size, width, n_lines):
    """
    n 行译文实际占的矩形：第一行落在原基线上，后面的行往下排。

    和 _box_from 的区别：那个是往上叠（把原框当最后一行），这个是往下排。
    严格模式用往下排 —— 图纸上的标注是从上往下读的，第一行必须还在原位，
    读者才会把它和原来那条对上；往上叠等于整条往上面跑了。
    """
    lead = textfit.WRAP_LINE_LEAD
    top = base - size * typography.INK_TOP
    bottom = base + size * typography.DESCENDER + (max(1, n_lines) - 1) * size * lead
    return pymupdf.Rect(x0, top, x0 + width, bottom)


def _plan_strict(c, out, font, want_ink, fill, space, untranslated):
    """
    严格原位排版：字号按原文墨迹高度锁死，位置锁原文基线，只许向右长。

    这是定制版的默认行为，开关见 edition.strict_layout。客户 2026-11-18 的原话：

        「原文的位置，就是译文的位置，字号与原文保持一致。如果格式宽度不够，
          允许加长，但不能超出图纸的格子。遇到超长会超出格子的，才允许换行
          显示。但要遵从翻译目标语言的换行习惯，比如不能单词拆开。」
        「折行，必须翻译，不能保留原文。」

    为什么要把原来那套整个绕开（同类同字号 / 重心对齐 / 四轮落点搜索）：
        那套逻辑的优化目标是「让这一页排得好看」，代价是允许缩字号、挪位置。
        实测客户这份图纸的后果是：一条标注左移 25mm、同一行四条文字用了
        四种字号（19.38 / 15.67 / 13.62 / 13.82）、一行被拆成两行还整体上移 15pt。
        翻译工具没有权力替客户重排版面 —— 图纸上每个位置都是客户量过的。

    取舍顺序（前四步都不动字号，只有最后一步才让）：
        1. 原框宽度内单行写完 —— 最好，位置和字号都没动
        2. 向右延伸到最近障碍（竖线、相邻文字、图框）为止，单行写完
        3. 到上限仍放不下 —— 折行，第一行留在原基线，后面的行往下排
        4. 往下压到东西 —— 折行往上叠（原基线当最后一行）
        5. 还不行 —— 缩字号重来。绝不保留原文：客户要的是译文
    """
    r = c["rect"]
    centered = (c.get("align") == "center")
    base_size = typography.size_for_ink(font, out, want_ink)

    # 居中标题：先按宽度上限压字号，再按源框中心对齐。
    #
    # 为什么标题要单独处理（客户 2026-11-19 的原话「标题字体偏大，也没有居中」）：
    #   左端锚死在源框 x0 上，英文比中文长两三倍，多出来的部分全部往右长，
    #   重心被推走一半增量 —— 实测两个视图标题的中心一个 493、一个 472，
    #   同一页的两个标题都不在同一条竖线上，更不在图形中心。
    #   字号上，折减后 pt 数字是对的（38.28→30.68），但宽度是源的 2~2.5 倍，
    #   占了半个图幅，客户看到的就是「标题太大了」。
    # 所以标题走另一套：宽度不超过源宽 TITLE_W_MAX 倍（超了缩字号），
    # 位置按源框中心对称展开，左右各长一半。
    if centered:
        cap_w = r.width * typography.TITLE_W_MAX
        w0 = font.text_length(out, fontsize=base_size)
        if cap_w > 0 and w0 > cap_w:
            base_size = max(typography.MIN_SIZE, base_size * cap_w / w0)
    base = r.y1 - base_size * typography.DESCENDER      # 原基线
    cx = (r.x0 + r.x1) / 2.0                            # 源框中心

    if space is None:
        width = font.text_length(out, fontsize=base_size)
        x0 = cx - width / 2.0 if centered else r.x0
        return (c, out, fill, base_size, font, None, False,
                _box_from(x0, base, base_size, width, 1))

    if centered:
        # 居中的标题不折行、不右延：缩字号保证单行写完，两侧对称。
        # 折行的标题会变成上下两截，居中与否都失去意义。
        size = base_size
        for _ in range(10):
            width = font.text_length(out, fontsize=size)
            b = r.y1 - size * typography.DESCENDER
            box = _box_from(cx - width / 2.0, b, size, width, 1)
            if space.is_clear(box, ignore=fill):
                space.reserve(box)
                return (c, out, fill, size, font, None, False, box)
            if size * 0.9 < typography.MIN_SIZE:
                break
            size *= 0.9
        # 压到最小也躲不开障碍：仍然按中心写，如实记一笔偏小
        width = font.text_length(out, fontsize=typography.MIN_SIZE)
        b = r.y1 - typography.MIN_SIZE * typography.DESCENDER
        box = _box_from(cx - width / 2.0, b, typography.MIN_SIZE, width, 1)
        space.reserve(box)
        return (c, out, fill, typography.MIN_SIZE, font, None, True, box)

    # 这一条往右还有多少空地：以译文实际占的高度带为准，不是以原框为准 ——
    # 英文的字面高度和中文不一样，用原框高度去探，探出来的边界会偏。
    band = pymupdf.Rect(r.x0, base - base_size * typography.INK_TOP,
                        r.x1, base + base_size * typography.DESCENDER)
    limit_full = max(r.width, space.frame.x1 - r.x0)
    room_full = max(space.clear_width(band, limit_full, ignore=fill), r.width)
    # 同一行右边还有别人的字段时，留出断句的空再算一次可用宽度。
    # 先按「窄」的那个试，能保住字号就用它；保不住字号就退回宽的 ——
    # 取舍顺序是「字号一致 > 不贴邻」：同一基线上一条 9pt、一条 14pt，
    # 比两段字靠得近更刺眼（2026-11-19 第 1 页底部实测）。
    rs = c.get("right_stop")
    room_stop = room_full
    if rs:
        room_stop = max(space.clear_width(
            band, min(limit_full, max(r.width, rs - r.x0)), ignore=fill),
            r.width)

    def _attempt(room_w):
        """在给定可用宽度下，找出能放下的最大字号与写框（不占位）"""
        size = base_size
        for _ in range(8):
            width = font.text_length(out, fontsize=size)
            if os.environ.get("PT_DEBUG"):
                import sys as _sys
                print(f"[strict] sz={size:5.2f} w={width:6.1f} "
                      f"room={room_w:6.1f} x0={r.x0:6.1f} base={base:6.1f} "
                      f"ctr={centered} {out[:34]!r}", file=_sys.stderr)
            # 1) 单行，左端锁死，原框内就放得下
            box = _box_from(r.x0, base, size, width, 1)
            if width <= room_w and space.is_clear(box, ignore=fill):
                return size, None, box
            # 2)(3)(4) 折行。wrap_text 不拆单词，词内不软断 —— 这是硬要求
            lines = textfit.wrap_text(font, out, room_w, size)
            if lines:
                widest = max(font.text_length(seg, fontsize=size)
                             for seg in lines)
                for cand in (_box_down(r.x0, base, size, widest, len(lines)),
                             _box_from(r.x0, base, size, widest, len(lines))):
                    if space.is_clear(cand, ignore=fill):
                        return size, lines, cand
            # 5) 缩一档重来。0.85 是有意的：一档缩太多，同一页里几条需要缩的
            #    会长得明显不一样；缩到 3.5pt 以下就停，再小印不出来
            if size * 0.85 < typography.MIN_SIZE:
                break
            size *= 0.85
        return None

    best = _attempt(room_stop)
    if rs and (best is None or best[0] < base_size * 0.92):
        alt = _attempt(room_full)
        if alt is not None and (best is None or alt[0] > best[0] + 0.01):
            best = alt
    if best is not None:
        size, lines, box = best
        space.reserve(box)
        return (c, out, fill, size, font, lines,
                bool(lines) and len(lines) > 1, box)
    # 缩到最小还放不下：仍然写，占住原框那一条。
    # 不保留原文是客户明确要求的（「折行，必须翻译，不能保留原文」），
    # 代价是这一处会偏小或伸出去一点，如实记进偏小清单。
    width = font.text_length(out, fontsize=typography.MIN_SIZE)
    box = _box_from(r.x0, base, typography.MIN_SIZE, width, 1)
    space.reserve(box)
    return (c, out, fill, typography.MIN_SIZE, font, None, True, box)


def _align_center_sizes(plans):
    """
    把同一页的居中标题拉到同一个字号（严格模式专用）。

    只动 `align == "center"` 且单行写完的那几条。字号取本页居中标题里
    **最小的那个**，只往相近的（±25% 以内）拉 —— 差得远的说明级别不同
    （主标题 / 副标题），不是一个套系，不该拉平。

    为什么取最小而不是中位数：每条在 _plan_strict 里都已经过了
    TITLE_W_MAX 的宽度上限。拉齐时如果把小的往上推，推上去的那条宽度
    就重新超限了（实测 28.67→30.40 后倍数从 1.85 变 1.97）。取最小
    等于只用已经验证过装得下的那个值，往上推的风险直接没有。

    为什么只拉标题：标注每条都要跟自己的原文一样大，拉平就违背了
    「字号锁死」。标题不一样 —— 客户看的是「这两个标题是不是一样大」，
    实测两个同源字高的标题反算出来差 5.7%，并排放在一页上看得出来。
    """
    grp = [p for p in plans if p[0].get("align") == "center" and p[5] is None]
    if len(grp) < 2:
        return
    sizes = sorted(p[3] for p in grp)
    mid = sizes[0]
    if mid <= 0:
        return
    keep = {id(p[0]) for p in grp
            if abs(p[3] - mid) / mid <= 0.25 and abs(p[3] - mid) > 0.05}
    if not keep:
        return
    for i, p in enumerate(plans):
        if id(p[0]) not in keep:
            continue
        c, out, fill, size, font, lines, small, box = p
        w = font.text_length(out, fontsize=mid)
        cx = (c["rect"].x0 + c["rect"].x1) / 2.0
        base = c["rect"].y1 - mid * typography.DESCENDER
        plans[i] = (c, out, fill, mid, font, lines, small,
                    _box_from(cx - w / 2.0, base, mid, w, 1))


def _mark_right_stop(cands, gap_em=1.2):
    """
    标出每条文字向右最多能伸到哪 —— 同一行右侧还有别的字段时，留出字高的空。

    为什么需要（2026-11-19 客户图纸第 3 页实测）：
        底部「折叠框架上贴橡胶垫…」「2根5毫米厚铁板…」「U型槽立柱折叠框架」
        是三个并排的字段。英文比中文长，按「向右延伸到最近障碍」的规则
        一直长到邻居框的左边沿，只差 4pt —— 两段字挤在同一基线上，
        读起来是「…ATTACHING RUBBER GREY PART, WIDTH 80mm PAD AND…」，
        一句话里串了两个字段的意思。字没有压在一起，但读者会读错。
        占用图只能判「有没有压到墨迹」，判不出「读起来是不是连成一句」。

    判据：纵向中心相差半个字高以内算同一行；只认右边的邻居；
    停到邻居左边沿再退一个多字高，退出来的空白才够断句。
    """
    for c in cands:
        r = c["rect"]
        cy = (r.y0 + r.y1) / 2.0
        stop = None
        for o in cands:
            if o is c:
                continue
            ro = o["rect"]
            h = max(r.height, ro.height, 1.0)
            if abs((ro.y0 + ro.y1) / 2.0 - cy) > h * 0.6:
                continue                      # 不在同一行
            if ro.x0 < r.x1 - 1.0:
                continue                      # 左边的、或与本条重叠的不算
            if stop is None or ro.x0 < stop:
                stop = ro.x0
        if stop is None:
            continue
        # 留白按本条自己的墨迹高度算：字大字距也要大，否则小字上留的缝
        # 挪到大字旁边又变成连着写
        ink = c.get("ink_h") or r.height
        c["right_stop"] = max(r.x1, stop - max(ink * gap_em, 4.0))


def _plan_one(c, out, untranslated=None, font_cjk=None, font_latin=None,
              glyph_h_hint=None, space=None, max_shift=None):
    """
    算出一条图内文字该不该写、写在哪个框里、用多大字号。

    out 为空或和原文一样就不写（返回 None，同时记进未译清单），
    其余情况返回 (候选, 译文, 填充框, 字号, 字体)。

    这段原先内联在 process_page_images 里，v1.0.8 抽出来是为了让复核打回后
    的重写走完全相同的排版规则 —— 两条路径各写一份，迟早会走偏。
    """
    if not out or not out.strip():
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None
    # 规范化标点与空格：中文全角、英文冒号后带空格、多余空格压掉。
    # 位图层和文字层走同一套规则，一张图上不会出现两种逗号。
    out = typography.normalize(out.strip())
    # 收紧数字写法：模型爱给数值补尾零（3mm→3.00mm、25*25*1.5→25*25*1.50），
    # 一处白长两三个字符，长句就被挤得缩字号。这里先收回原件的长度，
    # 后面算字号拿到的才是真实宽度。
    out = typography.tighten_numbers(out)
    # 内容与原文一样 —— 只在写法上绕了一圈。这里必须停手。
    #
    # 为什么（客户报的 ø12mm → @12.00mm 就是漏了这一道）：
    # 一条纯尺寸标注（ø12mm、25mm）不需要翻译，翻译通道原样返回，
    # 下游只看到「文本变了」就去擦掉原字重写。可上游并不可靠 ——
    # OCR 会把 ø 认成 @ / 0 / ·，PDF 字体缺 ToUnicode 会把 ⌀ 抽成 ·。
    # 原图上的字是对的，我们一重写就把错字印了上去。
    # same_content 只抹平数字尾零和空白，符号差一个都算不同
    # （公斤≠kg、@≠ø），真该翻译的条目一条都不会被拦下。
    if (typography.same_content(out, c["text"])
            or out.strip().upper() == c["text"].strip().upper()):
        # 原文里没有可译内容（纯数字、纯单位、纯符号）：
        # 它本来就不需要处理，别记成漏译 —— 那种清单上出现 25mm、70mm
        # 只会让客户以为工具没干活。
        if not typography.needs_translation(c["text"]):
            return None
        # 有可译内容却没变，这是真的没翻出来，如实记一笔
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None
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
    fill = _fill_rect(r, c.get("ink_box"))
    # 异常保护：填充框最多是原框的 3.5 倍面积，再大说明这条 OCR 框本身不可信，
    # 宁可留下原文记进未译清单，也不能把图形涂掉
    if fill.width * fill.height > r.width * r.height * 3.5 + 40:
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None

    # 字号对齐原文字的**墨迹高度**，不是对齐「字号数字」，也不是对齐框高。
    # 三种来源要先统一成同一种东西：
    #   glyph_h_hint —— 调用方渲染后数暗像素量出来的真实墨迹高度，直接可用；
    #   OCR 框高/框宽 —— 框比笔画本身高约两成（含留白），按 0.85 折回墨迹高度；
    #   英文标注普遍比中文窄也比中文矮，不再乘 1.08 补观感 —— 那一条是
    #   在没有墨迹高度时的权宜之计，现在有了真实高度，补就成了放大。
    if vertical:
        # 竖排走单独一条路（2026-11-19 客户定）：竖排的两头是被夹住的 ——
        # 上面是尺寸线端点、图框线或上一条标注，下面是同样一帮东西，
        # 所以它没有「往右长」这个选项，只有一件事要做：量出这条竖带
        # 上下还能占多长，把字号缩到刚好装下。横排那套「原位+向右延伸+
        # 折行」在十几点宽的竖带里全是废动作。
        #
        # 注意不能把 glyph_h_hint 传进去：竖排的 ink_h 量的是沿轴向的长度
        # （这份图纸 84pt），不是字高。字高由 _plan_vertical 自己按列宽算。
        return _plan_vertical(c, out, font, fill, space, untranslated)
    if c.get("angle"):
        # 斜排走同一条思路（2026-11-19 客户定）：轴向是斜的，两头同样被夹住，
        # 同样只有缩字号一个动作。区别只在写入时要带旋转 —— 见 _plan_slant。
        return _plan_slant(c, out, font, fill, space, untranslated)
    want_ink = glyph_h_hint or (r.height * 0.85)

    # 定制版走严格原位（字号锁死、位置锁死、只许向右长、放不下才折行）。
    # 通用版保持下面那套「同类同字号 + 重心对齐 + 四轮落点搜索」不动。
    if edition.strict_layout():
        return _plan_strict(c, out, font, want_ink, fill, space, untranslated)

    # ---- 有占用图时：先看哪儿能放，再定字号 ----
    #
    # 顺序反过来是有原因的。旧算法是「框有多宽就压到多小」，字号是结果，
    # 不是选择：框窄一点、英文长一点，字就细到看不见，或者干脆写不进去。
    # 现在改成在「可读下限 ~ 原文大小」之间二分，找出能放下且有地方放的
    # 最大字号 —— 位置和字号一起求解，不再被一个框的宽度锁死。
    if space is not None and not vertical:
        floor_pt = typography.readable_floor(glyph_h)
        soft_pt = max(floor_pt, glyph_h * typography.SHRINK_FLOOR)
        want_size = typography.size_for_ink(font, out, want_ink)
        cls_ink = c.get("cls_ink")

        # 字号上限的来历（两个分支都在讲同一件事：不许凭空变大）：
        #
        # 无同类时取「旧算法字号」—— 旧算法是「压到原框宽度里」，这个值本身
        # 就在原件的量级上。拿「原字大小」当上限，等于允许译文比原件还大：
        # 实测同一页里两个同样大小的标题变成 19pt 和 13pt。
        #
        # 有同类时直接取「这一类的高度」反算，且只准缩一成（CLASS_FLOOR）。
        # 这一档是 v1.1.6 加的：旧做法每条各算各的框宽，框窄的那条字号掉下来，
        # 同一页四个视图标题写出四种大小 —— 客户的原话是「同类标题不一样大」。
        # 放不下就折行、挪位；再放不下才降级为特例单独缩小，并在报告里点名，
        # 不让一条长句把整页标题拖小。
        if cls_ink:
            # 同一个类，同一个字号 —— 直接由这类原字的墨迹高度换算成点数，
            # 不再拿每条的译文去「按字面高反算」。
            #
            # 差别在哪：按字面高反算，全大写的 FRAME TOP VIEW 和带小写的
            # Frame front view 会算出二十几点和十几点两个不同的字号（因为
            # 两者的墨迹占比不同），写在图上就是一大一小 —— 客户的原话是
            # 「同类标题不一样大」。换算成点数，一类就只有一个数。
            hi = cls_ink / typography.CJK_INK_RATIO
            lo = max(floor_pt, hi * typography.CLASS_FLOOR)
        else:
            maxw = r.width + r.height * 0.2
            legacy_size = want_size
            lw = font.text_length(out, fontsize=legacy_size)
            if lw > maxw > 0:
                legacy_size = legacy_size * maxw / lw
            hi = max(floor_pt, min(want_size, legacy_size))
            lo = floor_pt

        # 离图框留一个安全边距。贴着框线写，虽然没越界，客户看到的是
        # 「这块快要出格子了」—— 第 1 页右侧那个视图标题正是这么撞上去的。
        #
        # 只收左右，不动上下：这份图纸的视图标题本来就贴着图框上沿排，
        # 上下也按一个字高收进去，等于把三条标题全判成越界，
        # 一路退到可读下限（实测字号从 19.1 掉到 17.2，正好一成）。
        safe = None
        try:
            safe = space.inset(glyph_h * typography.FRAME_PAD_EM, pad_y=0.0)
        except Exception:
            safe = None
        # 原件里对着图形居中的标题（视图标题、图名）：长法要绕重心对称长，
        # 不能像标注那样一头固定往另一头伸。判据见 _assign_align。
        align = c.get("align") or "left"
        widen = glyph_h * typography.WIDEN_BUDGET
        budget = (glyph_h * typography.MOVE_BUDGET
                  if max_shift is None else max_shift)
        # 标题旁边要留净空（0.35 字高），标注不用：
        # 净空是拿「探针外扩」实现的，而探针还得先把自己的笔画让开 ——
        # 所以忽略区要按 fill 再放宽一点点，否则标题自己的抗锯齿边缘
        # 会被算成障碍，连原位都判「放不下」。
        if align == "center":
            pad = glyph_h * typography.TITLE_CLEAR_EM
            own = pymupdf.Rect(fill.x0 - 1.2, fill.y0 - 1.2,
                               fill.x1 + 1.2, fill.y1 + 1.2)
        else:
            pad, own = None, fill

        best = _search(space, r, font, out, lo, hi, glyph_h, align,
                       safe, widen, budget, ignore=own, pad=pad)
        # 安全边距是「好看」的要求，字号是「看得清」的要求，两件事撞车时
        # 先让好看那一步 —— 让步的代价只可能是「贴着框线写」，绝不能是
        # 「这一条干脆缩小一号」。只有真的没到类字号才去试。
        if best is None or best[0] < hi * typography.CLASS_FLOOR:
            best = _pick(best, _search(space, r, font, out, lo, hi, glyph_h,
                                       align, None, widen, budget,
                                       ignore=own, pad=pad),
                         r, glyph_h, align)
        # 还是放不下：放开类字号约束，一路缩到可读下限，能写就写。
        # 留中文比偏小更难解释，但偏小要被记下来（第七项 small）。
        #
        # 注意要一直探到 floor_pt，不能只降个八成就算了 —— 类字号 10pt 的
        # 标注按 0.8 停在 7.2pt，而它其实 5.6pt 就写得下，等于白白留中文。
        #
        # 这一轮把两重让步都打开：安全边距放开（贴边也比留英文强）、
        # 居中退回一头固定（重心偏了也比留英文强）。
        if best is None:
            drift = glyph_h * typography.CENTER_DRIFT_EM
            for k in range(7):
                probe = lo + (floor_pt - lo) * k / 6.0
                got = _place_for_size(space, r, font, out, probe, widen, budget,
                                      ignore=fill, align=align, drift=drift,
                                      safe=None, anchor_fallback=True)
                if got is not None:
                    best = (probe, got[0], got[1])
                    break
        if best is not None:
            size, box, lines = best
            # 定稿就占住，后面的条目不许压上来
            space.reserve(box)
            return (c, out, fill, size, font, lines, size < soft_pt, box)
        # 连可读下限都找不到地方：与其叠在别人身上，不如留原文并如实汇报
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None

    size = typography.size_for_ink(font, out, want_ink)
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
    # 下限分两级，顺序不能反：
    #   优先线 八成 —— 到这里就先折行把字号抬回来（折行不丢内容）；
    #   保底线 六成 —— 折行也救不回来才允许缩到这里，再低就不写。
    # 为什么不做成一刀切八成：拿这份中文图纸实测过，硬卡 0.8 会让
    # 13 条标注里 12 条写不进去、全部退回中文 —— 那比字小更难接受。
    # 也不能像早先那样直接卡到 25%：原字高 7pt 的标注被压到 3.53pt，
    # 打印出来是一道灰线，客户一眼就看出来「这块塞得特别小」。
    # 半成偏小（八成到六成之间）照写但记下来，报告里能查到是哪几处。
    hard = typography.readable_floor(glyph_h)
    soft = max(hard, glyph_h * typography.SHRINK_FLOOR)
    if size < soft and not vertical:
        # 第二行只能往上借空间，借多少有硬上限：可用高度就是原框高度
        # （多给 15% 余量）。OCR 框本来就贴着字，往上多写一行就会
        # 越过被盖住的区域，压到上面那行原文上 —— 那比不译更难看。
        # 上限给 glyph_h（原文字高）而不是当前这个已经缩小的 size：
        # 折行的意义就是把字号抬回来，拿缩小的值当上限等于白折
        plan = textfit.wrap_fit(font, out, glyph_h, r.width,
                                glyph_h * 1.15)
        if plan and len(plan[1]) >= 2 and plan[0] > size:
            size, lines = plan
    if size < hard:
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None
    # 第七项 small：字号落在优先线与保底线之间，写是写了但偏小。
    # 上层据此在报告里点出位置 —— 用户看图纸觉得「这块有点小」时，
    # 能查到是哪些地方、为什么小（原框太窄，英文又长）。
    # 第八项 place：真正落笔的矩形。没有占用图时就是原框；
    # 有占用图时可能被加宽或上下挪了一行，落笔必须按它来，不能还按原框。
    return (c, out, fill, size, font, lines, size < soft, r)


def _span_from(space, x0, x1, edge, direction, limit, ignore):
    """
    从 edge 这条边出发、朝 direction 方向能连续空出多少点。

    direction = -1 向上，+1 向下。探针是「从 edge 一路铺过来的整块矩形」，
    不是只探边上的薄薄一条 —— 竖排译文要占的是整列高度，中间哪一段被
    尺寸线端点、剖面线挡住，那一段就用不了，必须整块一起判。

    space 为 None（没有占用图）时返回 0：宁可按原框缩字号，也不要凭空调
    出一段其实压着别人的区域。这条退路是有意的。
    """
    if space is None or limit <= 0:
        return 0.0

    def ok(t):
        y0 = edge - t if direction < 0 else edge
        y1 = edge if direction < 0 else edge + t
        return space.is_clear(pymupdf.Rect(x0, y0, x1, y1),
                              ignore=ignore, pad=0.0)

    # 先按 2pt 一档往外推，找到第一档不通的位置
    best, t = 0.0, 2.0
    while t <= limit:
        if not ok(t):
            break
        best, t = t, t + 2.0
    # 再二分到 0.25pt，把「差一点」的那几档也吃掉
    lo, hi = best, min(best + 2.0, limit)
    while hi - lo > 0.25:
        mid = (lo + hi) / 2.0
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def _glyph_column(space, ink, ignore=None):
    """
    在 ink 的横向范围里剔掉贴着字的那条竖线，返回真正装字的那一条列。

    为什么必须剔：竖排标注右侧往往紧贴着尺寸线或图框线，那条线会落进
    OCR 框和墨迹框里。拿带线的框去探上下空间，第一档就被自己的线挡住
    （实测 up=0.5 / down=0.5，等于一个字都长不出去）；拿它当字高，
    会把 11pt 的字算成 13.3pt。

    判据不按「列里有多少墨」算 —— 试过，不成立：1pt 宽的尺寸线落在
    0.6pt 宽的探条里，占用率只有 0.5，跟汉字的竖笔画分不开。
    改成更直接的判据：**这一列正上方（或正下方）出去 3pt 是不是空白**。
    字的列上方是空的，压着线的那一列上方立刻就是线。

    收不动（上下都挨着别的标注，两条都不是线）时按已收的结果返回，
    不硬收到没有宽度 —— 收窄一列只会让字小一点，收没了就写不进去。
    """
    if space is None:
        return ink.x0, ink.x1
    lo, hi = ink.x0, ink.x1

    def clear_above_below(x0, x1):
        for y in (ink.y0 - 4.0, ink.y1 + 1.0):
            try:
                if space.is_clear(pymupdf.Rect(x0, y, x1, y + 3.0),
                                  ignore=ignore, pad=0.0):
                    return True
            except Exception:
                return True
        return False

    n = 0
    while hi - lo > 3.0 and n < 16 and not clear_above_below(lo, hi):
        hi -= 0.5
        n += 1
    n = 0
    while hi - lo > 3.0 and n < 16 and not clear_above_below(lo, hi):
        lo += 0.5
        n += 1
    return lo, hi


def _plan_vertical(c, out, font, fill, space, untranslated):
    """
    竖排标注的排版：沿轴向缩字号，装进上下界限为止。

    为什么单独一条路（2026-11-19 客户定，托盘图纸实测）：
      横排的方案是「原位 + 向右延伸 + 放不下才折行」。竖排没有横向空间 ——
      字身宽度就是一个字高（实测 11pt 左右），右边紧贴着尺寸线；它唯一的
      自由度在长度方向，而长度方向两头都被夹死：上到尺寸线端点、下到下一条
      标注。所以竖排只有一个动作：把字号缩到「译文长度 = 上下可用长度」。

    改之前的行为（1.1.5 及以前）：竖排落到通用分支，可排长度直接取 OCR 框
    高度，译文长两倍就判「缩到可读下限以下」并留原文。托盘图纸那两条
    「贴上橡胶垫后983mm」就是这么整块留在图上的 —— 原文 83pt，译文按原字高
    要 176pt，硬塞进 83pt 要 4.5pt，低于 5.5pt 的地板，于是放弃。

    现在先量真实界限：原字上下各有 55pt 空白（到尺寸线端点），一共 193pt，
    176pt 装得下，字号一点不用缩。

    只在缩到硬下限（3.5pt）还装不下时，才退回「写不出、报未译」。

    另有两个坑，都是这份图纸上踩出来的：
      · 竖排的 ink_h 量的是「沿轴向的长度」（84pt），不是字高 —— 字高是
        横跨方向的列宽。拿它当字高会算出 69pt 的字（实测）。
      · OCR 框右侧压着尺寸线，那条线会污染墨迹框：既挡住上下探测，
        又把字高算大。见 _glyph_column。
    """
    r = c["rect"]
    ink = c.get("ink_box")
    if ink is None or ink.is_empty or ink.height < r.height * 0.35:
        ink = r

    col_x0, col_x1 = _glyph_column(space, ink, ignore=fill)
    # 字高 = 跨列宽度（不是沿轴长度），这才是「原字多高」在竖排里的对应物
    glyph_h = max(col_x1 - col_x0, 1.0)
    # 盖字框必须重算：上游给的 fill 是照墨迹框外扩的，而墨迹框把右侧那条
    # 尺寸线一起框进来了（实测 ink.x1=74.3，线在 73.7~74.1）。直接用它盖，
    # 会在尺寸线上啃出一个和字一样长的缺口 —— 图上看到的就是「线断了一截」。
    # 这里按剔掉线之后的那条列重新给框，右侧只留 0.2pt 余量。
    vfill = pymupdf.Rect(col_x0 - 0.6, ink.y0 - 0.6, col_x1 + 0.2, ink.y1 + 0.6)

    # 上限跟着原字长走，不设固定 400。纯空白处（孤立标注、合成图）两侧都探到
    # 上限，居中后整条标注被挪到几百点之外 —— 实测合成图上「上下调节100mm」
    # 从 y≈160 挪到了 y≈430，跟旁边的竖线彻底脱开。
    # 真实图纸上下都有尺寸线挡着，量多少是多少；没有障碍时按原字长封顶。
    LIMIT = max(ink.height * 1.2, 60.0)
    up = _span_from(space, col_x0, col_x1, ink.y0, -1, LIMIT, vfill)
    down = _span_from(space, col_x0, col_x1, ink.y1, +1, LIMIT, vfill)
    avail = ink.height + up + down

    size = typography.size_for_ink(font, out, glyph_h)
    need = font.text_length(out, fontsize=size)
    if os.environ.get("PT_DEBUG_V"):
        import sys as _s
        print("[vert] col=%.1f~%.1f 字高%.1f avail=%.1f(上%.1f/下%.1f) need=%.1f size=%.2f"
              % (col_x0, col_x1, glyph_h, avail, up, down, need, size), file=_s.stderr)
    if avail <= 0 or need <= 0:
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None

    if need > avail:
        # 按比例缩：长度与字号成正比，一次除法就够，不用二分。
        # 乘 0.998 是留浮点余量 —— 算出来正好等于 avail 时，text_length
        # 再算一遍常常多出 1e-13，随即被判「还差一点」而整条放弃。
        size = max(size * avail / need * 0.998, typography.MIN_SIZE)
        need = font.text_length(out, fontsize=size)
    if need > avail + 0.05:
        # 缩到硬下限还装不下：与其把字压成一道灰线，不如留原文并如实报出
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None

    # 落点：在「原字 + 上下借到的空白」这一整段里居中，视觉重心与原件一致。
    # 只往上或只往下长的写法试过，会一头顶到尺寸线箭头上。
    mid = (ink.y0 - up + ink.y1 + down) / 2.0

    # 横向：英文的字身比汉字窄，别让它贴着右边的尺寸线写。
    # 把字身摆进原来那条列里居中 —— 基线在右、字身往左长，下伸部伸出基线
    # 0.22 字高，所以基线还要再往里让一点，否则 g、p 会压到线上。
    body = size * typography.ink_ratio(font, out)
    base_x = col_x1 - max(0.0, (glyph_h - body)) / 2.0 - size * typography.DESCENDER
    base_x = max(base_x, col_x0 + size * 0.2)
    place = pymupdf.Rect(col_x0, mid - need / 2.0, base_x, mid + need / 2.0)
    if space is not None:
        space.reserve(place)

    soft_pt = max(typography.readable_floor(glyph_h),
                  glyph_h * typography.SHRINK_FLOOR)
    return (c, out, vfill, size, font, None, size < soft_pt, place)


def _plan_slant(c, out, font, fill, space, untranslated):
    """
    斜排标注的排版：跟竖排同一条思路，轴向从竖直换成「原文字走向」。

    竖排的规则是（2026-11-19 客户定）：两头被夹死，只有缩字号这一个动作，
    量出这条带子还能占多长，把字号缩到刚好装下。斜排完全一样 ——
    R35mm、Ø12mm 这种沿引线斜写的半径标注，两头同样是引线端点和相邻图元，
    既不能横着长出去，也不能改方向，能动的只有字号。

    为什么不能沿用横排那套：横排是「原位 + 向右延伸 + 放不下就折行」。
    斜排用它会出两个错，都是这一版要消掉的：
      · 字写成水平的，方向没了 —— 原文沿 22° 引线写着，译文横躺在引线上，
        跟引线成一个夹角，一眼就看出是后贴的；
      · 折行更离谱 —— 两行水平字叠在一个斜着的狭长区域里，第二行必然压到
        引线或者旁边的尺寸线上。

    三个量都要在「斜坐标系」里算，用轴对齐矩形量会全错：
      · 轴向长度 L 与字高 h —— 直接取 OCR 四边形投影到 u/v 的跨度。
        四边形是贴着字给的，投影出来就是这条标注的真实长宽；
      · 两端界限 —— 沿 u 逐点探（PageSpace.clear_along），遇到线条就停。

    2026-11-19 合成图实测修三处（真实图纸里斜排都太短，暴露不出来）：
      · 借空白要「按需借、两边对称」。原来两侧各探 400pt，左边撞上引线
        立刻停、右边全是空白，于是中心一路右移 200pt，文字飞到引线端点
        以外，跟原标注脱开。现在先看译文按原字号需不需要变长，需要多少
        就在两侧对称借多少，一侧不够由另一侧补。
      · 从中心探到障碍的距离里含着半个字长（自己那一半），要扣掉。
        不扣的话「两端可用长度」凭空多出一个字长，字号算大。
      · 擦除区按 OCR 四边形给，不再用墨迹框投影。墨迹框是轴对齐的，
        引线从框里穿过去也算成墨迹，擦的时候会把引线啃掉一截（实测）。

    装不下就缩，缩到硬下限还装不下才留原文并如实报未译 —— 与竖排一致。
    """
    ang = float(c.get("angle") or 0.0)
    th = math.radians(ang)
    ux, uy = math.cos(th), math.sin(th)     # 文字走向（屏幕坐标，y 向下）
    vx, vy = -uy, ux                        # 字身方向

    ink = c.get("ink_box") or c["rect"]
    if ink is None or ink.is_empty:
        ink = c["rect"]

    quad = c.get("quad")
    if quad and len(quad) == 4:
        # 四边形贴合文字本身：u 方向跨度 = 轴向长度，v 方向跨度 = 字身高度
        cs = [(p.x, p.y) for p in quad]
        tight = True
    else:
        # 没有四边形（复核重写、合并后的候选）：退回轴对齐墨迹框，
        # 投影值一定偏大，字高再用 ink_h 压回来
        cs = [(ink.x0, ink.y0), (ink.x1, ink.y0),
              (ink.x1, ink.y1), (ink.x0, ink.y1)]
        tight = False
    su = [x * ux + y * uy for x, y in cs]
    sv = [x * vx + y * vy for x, y in cs]
    u0, u1 = min(su), max(su)
    v0, v1 = min(sv), max(sv)
    L = max(u1 - u0, 1.0)
    h_proj = max(v1 - v0, 1.0)

    if tight:
        # 四边形带一圈留白，按 0.9 折回字身高度（与横排「框高×0.85」同量级）
        h = h_proj * 0.9
    else:
        h = c.get("ink_h") or 0.0
        if h <= 0 or h > h_proj:
            h = h_proj
    h = max(min(h, L), 1.0)

    cxm = (ink.x0 + ink.x1) / 2.0
    cym = (ink.y0 + ink.y1) / 2.0

    def _own(x, y):
        """这个点是不是本条自己的原墨迹（量界限时要当空）。"""
        lu = x * ux + y * uy
        lv = x * vx + y * vy
        return (u0 - 0.5) <= lu <= (u1 + 0.5) and (v0 - 0.5) <= lv <= (v1 + 0.5)

    # 字高的一半是「从中心走到自己这一端」的距离，探出来的距离要扣掉它
    half_L = L / 2.0
    LIMIT = 400.0
    half_h = max(h * 0.42, 0.6)
    if space is not None:
        up_raw = max(0.0, space.clear_along(
            (cxm, cym), (-ux, -uy), half_h, LIMIT, keep=_own) - half_L)
        dn_raw = max(0.0, space.clear_along(
            (cxm, cym), (ux, uy), half_h, LIMIT, keep=_own) - half_L)
    else:
        # 没有占用图（复核重写、离线调用）时按「原字两端各借一个字高」估。
        # 不估的话这里只能硬塞进 L，一条 6 个字符的标注立刻被判装不下。
        up_raw = dn_raw = h

    size = typography.size_for_ink(font, out, h)
    need0 = font.text_length(out, fontsize=size)
    if need0 <= 0:
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None

    # 借空白按需、对称：译文按原字号比原位长多少，就在两侧各借一半。
    # 原位装得下就一点不借 —— 这样短标注（R35mm、Ø12mm 这种）绝不会挪位。
    if need0 <= L:
        up = dn = 0.0
    else:
        want = need0 - L
        up = min(up_raw, want / 2.0)
        dn = min(dn_raw, want / 2.0)
        rest = want - up - dn
        if rest > 0:
            # 一侧还有富余就补上，两边都没有就认了（下面会缩字号）
            spare_up, spare_dn = up_raw - up, dn_raw - dn
            add = min(rest, max(spare_up, spare_dn))
            if spare_up >= spare_dn:
                up += add
            else:
                dn += add
    avail = L + up + dn
    need = need0

    if os.environ.get("PT_DEBUG_V"):
        import sys as _s
        print("[slant] %.1f° L=%.1f h=%.1f avail=%.1f(上%.1f/下%.1f) "
              "need=%.1f size=%.2f" % (ang, L, h, avail, up, dn, need, size),
              file=_s.stderr)

    if need > avail:
        # 长度与字号成正比，一次除法即可（同竖排）
        size = max(size * avail / need * 0.998, typography.MIN_SIZE)
        need = font.text_length(out, fontsize=size)
    if need > avail + 0.05:
        if untranslated is not None:
            untranslated.append(("图片内文字", c["text"]))
        return None

    # 落点：在「原字 + 两端借到的空白」这一段里居中，方向仍是原走向。
    # start = 这一段的中点沿 -u 退回半个文字长度。
    mid_d = (dn - up) / 2.0
    sx = cxm + ux * mid_d - ux * need / 2.0
    sy = cym + uy * mid_d - uy * need / 2.0

    # 擦除区域用旋转四边形：轴对齐外接框会把斜框外的图形一起涂掉。
    # 外扩 0.6 是为了盖住笔画的抗锯齿边，竖排那边也是这个量级。
    pads = 0.6
    q = []
    for lu, lv in ((u0 - pads, v0 - pads), (u1 + pads, v0 - pads),
                   (u1 + pads, v1 + pads), (u0 - pads, v1 + pads)):
        q.append(pymupdf.Point(lu * ux + lv * vx, lu * uy + lv * vy))
    bbox = pymupdf.Rect(min(p.x for p in q), min(p.y for p in q),
                        max(p.x for p in q), max(p.y for p in q))
    if space is not None:
        space.reserve(bbox)

    # 落笔信息不能塞进返回元组的 Rect —— 起点和角度都在它之外。
    # 挂在候选上（同一个 dict 会一路传到 paint_plans），paint 里有就用。
    c["slant_paint"] = {"x": sx, "y": sy, "size": size, "angle": ang}
    c["slant_fill"] = q

    # 第八项（place）要给**实际写出去的范围**，不是原框。
    # 复核是照着它回读文字的：译文常比原文长（中文 7 字 → 英文 21 字符），
    # 拿原文那个框去读，只读得到落在框内的头几个字，于是「明明写上了」
    # 被判成空白块 → 打回重译 → 再写一遍，图上就成了两条平行的斜字
    # （2026-11-19 合成图实测：want='RADIUS35' got='A\nA\n'）。
    wt = h        # 字身高度，基线往 -v 方向长
    ecs = [(sx, sy),
           (sx + ux * need, sy + uy * need),
           (sx + ux * need - vx * wt, sy + uy * need - vy * wt),
           (sx - vx * wt, sy - vy * wt)]
    place = pymupdf.Rect(
        min(p[0] for p in ecs) - 1.0, min(p[1] for p in ecs) - 1.0,
        max(p[0] for p in ecs) + 1.0, max(p[1] for p in ecs) + 1.0)

    soft_pt = max(typography.readable_floor(h), h * typography.SHRINK_FLOOR)
    # 第三个返回值（fill）给的是外包框，只当「这条占哪儿」用；
    # 真正擦除时 paint_plans 会优先用 slant_fill 那个四边形 —— 轴对齐矩形
    # 会把斜框外的引线、尺寸线一起涂掉，那正是这一版要避免的。
    return (c, out, bbox, size, font, None, size < soft_pt, place)


def _content_center(rects, r, ink, space=None):
    """
    这条文字所标注的那块图形，横向中心在哪。找不到返回 None。

    做法：在文字**正下方**一带里，把横向落在搜索窗口内的图元求并集，
    取并集的中心。为什么是正下方 —— 视图标题、图名都是放在图形上方的，
    它对着的那块图形就是自己的注释对象；图形中心 = 标题该对齐的中线。

    三道过滤缺一不可：
      · 纵向从「紧贴下方」开始，找不到再一层层放宽（3 → 6 → 10 → 14 个字高）。
        为什么不能一上来就用大范围：第 2 页右上那个标题下面先经过视图，
        再往下 250pt 就是标题栏，一次性圈 14 个字高会把标题栏并进来，
        中心被拽到 630（真值是 589），于是把本来居中的标题判成「不居中」。
        而一上来就只看 3 个字高又可能什么都圈不到（视图离标题很远时），
        所以逐层放宽，第一个像样的就收手 —— 离得最近的那块才是它标的。
      · 横向只收与「标题框左右各扩一个字宽」相交的图元。图纸上两个视图
        并列（实测相距 200pt），不过滤就会把隔壁那个也算进来。
      · 并集明显比标题还窄（不到 1.3 倍）就算不上「标题标注的图形」，
        多半是下面还有一行字，这种继续放宽再看。

    图形是位图（转曲、贴图）时矢量图元一条都拿不到，退回问占用图
    「这一带哪儿有东西」—— 粗一档，但中心够用。
    """
    for band_em in (3.0, 6.0, 10.0, typography.CENTER_REF_BAND_EM):
        band_top = r.y1 + ink * 0.2
        band_bot = r.y1 + ink * band_em
        win = pymupdf.Rect(r.x0 - r.width, band_top, r.x1 + r.width, band_bot)

        u = None
        for x in rects:
            if x.y0 < band_top or x.y0 > band_bot:
                continue
            if x.x1 <= win.x0 or x.x0 >= win.x1:
                continue
            u = x if u is None else (u | x)
        if u is not None and u.width >= r.width * 1.3:
            return (u.x0 + u.x1) / 2.0

        if space is not None and band_em == typography.CENTER_REF_BAND_EM:
            # 最后一层还没有矢量图元：可能是位图图形，问占用图
            try:
                span = space.occupied_span(win)
            except Exception:
                span = None
            if span is not None and span.width >= r.width * 1.3:
                return (span.x0 + span.x1) / 2.0
    return None


def _assign_align(page, candidates, space=None, by_size_only=False):
    """
    标出哪些文字是「对着图形居中」的，供 _candidates 决定往哪边长。

    为什么非要先判这一步：译文比原文长是常态（外表正面图五个字 →
    Exterior Front View 十八个字符，长度翻一倍）。只有「左端固定」和
    「右端固定」两种长法时，重心必然被推走一半增量 —— 实测第 2 页四个
    视图标题的重心全部右移 40~45pt，客户看到的就是「标题偏到一边」；
    第 1 页右边那个更糟，一路顶到图框线上。

    两道关，都要过：

    1. 字号得是这一页最大的那一档（不低于最大字高的 85%）。
       居中是**标题的排版属性**，标注不是 —— 图纸上的标注靠引线定位，
       习惯齐左排成一列。这一关是实测撞出来的：第 1 页左边那一列标注
       （外框50*50*2.5mm、蓝色部分、灰色部分…）正好排在视图中线上，
       几何上「看着居中」全被判成居中，于是译文两头一起长，
       整列标注的左边缘散掉了。加上字号这一关就干净了 ——
       标题 18~19pt、标注 9.7~11pt，中间空得很宽。

    2. 原框中心与所标注图形的中心相差半个字高以内。这份图纸五个视图
       标题的偏差都在 0.6pt 以内，很硬。

    认不出图形（下面没有图元、也没有位图内容）就不设 align，
    沿用原来的左对齐处理：宁可不动，也不要猜错方向。
    """
    ink_of = {}
    for c in candidates:
        if c.get("vertical"):
            continue
        ink = c.get("ink_h") or c["rect"].height * 0.72
        if ink <= 0:
            continue
        # 斜排文字要排除：OCR 框是轴对齐包围盒，斜着写的字墨迹高度被
        # 对角尺寸撑大。实测 `ø32*2mm圆管` 沿斜管写，ink 量出 47.5，
        # 比同页真标题的 36.8 还大 —— 它当了「本页最大字号档」，
        # 真标题就被 0.85 那一关挡在外面，两个视图标题一条都标不上居中。
        # 判法：按墨迹高算这串字水平排开该有多宽，框宽连一半都不到，
        # 那只能是被旋转过（或竖排漏标了 vertical）。
        wide = sum(1 for ch in c["text"] if typography.is_wide(ch))
        other = len(c["text"]) - wide
        need = wide * ink * 0.95 + other * ink * 0.55
        if need > 0 and c["rect"].width < need * 0.55:
            continue
        ink_of[id(c)] = ink
    if not ink_of:
        return
    top_ink = max(ink_of.values())
    try:
        drawings = page.get_drawings()
    except Exception:
        return
    rects = [dr["rect"] for dr in drawings
             if dr["rect"].width > 1 and dr["rect"].height > 1]
    for c in candidates:
        ink = ink_of.get(id(c))
        if not ink or ink < top_ink * typography.CENTER_MIN_REL:
            continue
        if by_size_only:
            # 严格模式（定制版）：本页最大字号档就是标题，直接标居中。
            #
            # 为什么不用下面那套几何判据（客户 2026-11-19 报「标题没有居中」时实测）：
            #   _content_center 要在标题下方一带把矢量图元求并集，取并集中心当
            #   「图形中心」。这份图纸一页有近三千个图元、横向窗口只覆盖标题宽
            #   左右各一格，左侧图元被窗口切掉，并集中心偏了 85pt（真值 326.7，
            #   它给 241.6），标题被判成「不居中」。
            #   而「标题居中」根本不需要知道图形中心在哪 —— 源框中心就是它该有
            #   的中心，译文保持这个中心、向两侧对称生长即可。少猜一步，少错一处。
            #
            # 但「最大字号档」不等于「标题」，还要排掉长得像句子的：
            #   第 1 页根本没有视图标题，全页最大的字是底部那条
            #   「材料：外方管…，内方管…，尺寸：…，」。按字号判它成了标题，
            #   于是被按原框中心居中排 —— 实测左端从 127.3 跑到 72.8，
            #   整整左移 54pt，而客户要的是「原文的位置就是译文的位置」。
            # 三条硬筛：带冒号的是字段、带数字的是规格、超过 14 个字的是句子。
            src = c["text"]
            if ("：" in src or ":" in src) or re.search(r"\d", src) \
                    or len(src) > 14:
                continue
            c["align"] = "center"
            continue
        r = c["rect"]
        center = _content_center(rects, r, ink, space)
        if center is None:
            continue
        tol = max(ink * typography.CENTER_TOL_EM, 3.0)
        if abs((r.x0 + r.x1) / 2.0 - center) <= tol:
            c["align"] = "center"


def _align_class_sizes(plans):
    """
    把同一类里已经定稿的字号拉齐到同一个值。

    排完一轮后，同类里总有一两条因为周围太挤、落到比类字号更小的档上。
    这时剩下的成员如果还各写各的，图上就又是「同类不一样大」—— 只是差距
    从两档变成了一档。所以最后做一次收口：同一类取其中最小的那个字号，
    其余成员跟着缩到它。

    为什么只往小拉、不往大推：拉小一定放得下（字变小、占位跟着变小，
    原来那块空着的地方仍然空着），往上推就得重新找位置，可能放不下。
    代价是——只要有一条挤不下，整类都会跟着小一点。这个取舍是客户挑的：
    同类字号不一致比整体小一号更扎眼。

    例外：类字号被拖到九成以下时不再往下拉，那说明那条是真的没地方，
    让它当特例（报告里会点名），不能为了它把整页标题缩小两成。
    """
    groups = {}
    for p in plans:
        c = p[0]
        if not c.get("cls_ink"):
            continue
        groups.setdefault(round(float(c["cls_ink"]), 2), []).append(p)
    for _key, members in groups.items():
        if len(members) < 2:
            continue
        sizes = [m[3] for m in members]
        hi = members[0][0]["cls_ink"] / typography.CJK_INK_RATIO
        floor = hi * typography.CLASS_FLOOR
        target = max(min(sizes), floor)
        for idx, p in enumerate(members):
            if p[3] <= target + 1e-6:
                continue
            c, out, fill, _size, font, lines, small, place = p
            width = max(font.text_length(seg, fontsize=target)
                        for seg in (lines or [out]))
            # 缩字号时往哪边收：居中的绕原重心收（两头各收一点），
            # 靠左的保持左端不动。这条不能省 —— 一缩字号就往右收，
            # 本来居中的标题又被推出去了几毫米，等于白做一次居中。
            if c.get("align") == "center":
                x0 = (place.x0 + place.x1) / 2.0 - width / 2.0
            else:
                x0 = place.x0
            box = _box_from(x0,
                            place.y1 - target * typography.DESCENDER,
                            target, width, len(lines) if lines else 1)
            members[idx] = (c, out, fill, target, font, lines, small, box)
        # 原位替换回 plans（保持顺序）
        for i, p in enumerate(plans):
            for m in members:
                if m[0] is p[0]:
                    plans[i] = m
                    break


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
    #
    # v1.1.6 试过「只擦字的像素、让底下的线透出来」，用的是带 alpha 的小图贴回，
    # 实测失败：PyMuPDF 合成 4 通道 pixmap 时把透明部分算成暗色，原字留下一圈
    # 灰色轮廓，比白块更难看（隔离测试可复现）。所以这里回到矩形填充，
    # 改成把矩形收紧到原字的实际墨迹范围（见 _fill_rect），不再连坐周边图线。
    for c, _out, fill, *_rest in plans:
        # 斜排的盖字区是旋转四边形，不能用矩形：轴对齐外包会把斜框外的
        # 引线、尺寸线一起涂掉（实测那条 22° 半径引线会被啃掉一截）。
        q = c.get("slant_fill")
        if q:
            page.draw_polyline(q, color=None, closePath=True,
                               fill=tuple(float(v) / 255 for v in c["bg"]),
                               width=0)
            continue
        page.draw_rect(fill, color=None,
                       fill=tuple(float(v) / 255 for v in c["bg"]), width=0)

    # 第二轮：写译文
    changed = 0
    for c, out, _fill, size, font, lines, *_rest in plans:
        if os.environ.get("PT_DEBUG_PAINT"):
            import sys as _sys
            _pl = _rest[1] if len(_rest) > 1 and _rest[1] is not None else c["rect"]
            import traceback as _tb
            _caller = [f.name for f in _tb.extract_stack()[:-1] if 'image_text' in f.filename][-3:]
            print(f"[paint] sz={size:5.2f} nlines={len(lines or [])} "
                  f"box=({_pl.x0:6.1f},{_pl.y0:6.1f},{_pl.x1:6.1f},{_pl.y1:6.1f}) "
                  f"caller={_caller} {out[:40]!r}", file=_sys.stderr)
        tw = pymupdf.TextWriter(page.rect)
        color = tuple(float(v) / 255 for v in c["fg"])
        # 落笔矩形：第八项给了就用它，没给（旧的调用方式）退回原框
        place = _rest[1] if len(_rest) > 1 and _rest[1] is not None else c["rect"]
        # 基线不能贴框底，要往下留出 y/g/p 的下伸部 —— 落了就压到框下的图线上
        base_y = place.y1 - size * typography.DESCENDER
        sp = c.get("slant_paint")
        if sp:
            # 斜排：起点和角度都是 _plan_slant 在斜坐标系里算好的（place 那个
            # 轴对齐框给不出起点，也没法表达角度）。morph 用 pivot + 旋转矩阵：
            # 矩阵把水平写法整体转过去，pivot 就是文字起点，转完仍然从那儿起。
            #
            # 符号必须按 PDF 文本空间的 y 方向算，不能按屏幕坐标直接套：
            # 文本空间 y 向上、页面坐标 y 向下，所以文本 (1,0) 投到页面上是
            # (a, -b) 而不是 (a, b)。写成 Matrix(cos, sin, -sin, cos) 会让
            # −24° 的标注朝右下写（实测 bbox 落在 pivot 下方 50pt），跟引线夹角
            # 变成两倍，位置也全错 —— 正确的 23 度标注要先加负号再进矩阵。
            # 竖排用的 Matrix(90) 同理：(a,-b)=(0,-1) 向上，与「文字向上生长」一致。
            th = math.radians(sp["angle"])
            pivot = pymupdf.Point(sp["x"], sp["y"])
            mat = pymupdf.Matrix(math.cos(th), -math.sin(th),
                                 math.sin(th), math.cos(th), 0, 0)
            tw.append(pivot, out, font=font, fontsize=size)
            tw.write_text(page, color=color, morph=(pivot, mat))
        elif c.get("vertical"):
            # 竖排标注逆时针 90° 写回：以落点框右下角为 baseline 起点，
            # 文字向上生长，字身落在基线左侧，正好覆盖原框。
            # Matrix(90) 在 y 轴向下的坐标系里是逆时针 90°，
            # 和制图标准里垂直尺寸数字的朝向一致（从下往上读）。
            # 若按水平写法铺在十几点宽的窄框里，字会被压成一条缝。
            #
            # 起点用 place 而不是原框：竖排缩字号后要在「原件 + 上下借到的
            # 空白」里居中（见 _plan_vertical），拿原框当起点只会上下一头沉。
            pivot = pymupdf.Point(place.x1, place.y1)
            tw.append(pivot, out, font=font, fontsize=size)
            tw.write_text(page, color=color, morph=(pivot, pymupdf.Matrix(90)))
        elif lines and len(lines) > 1:
            # 折行：末行贴基线，其余往上叠。与文字层用同一套落点规则，
            # 两处对不上的话，同一张图上折出来的行距会一紧一松。
            gap = size * textfit.WRAP_LINE_LEAD
            for k, seg in enumerate(lines):
                base = pymupdf.Point(
                    place.x0, base_y - (len(lines) - 1 - k) * gap)
                tw2 = pymupdf.TextWriter(page.rect)
                tw2.append(base, seg, font=font, fontsize=size)
                tw2.write_text(page, color=color)
        else:
            tw.append(pymupdf.Point(place.x0, base_y), out,
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

    这里必须带占用图（space）重排，不能等闲视之 —— 2026-11-19 实测：
    不带占用图时 _plan_strict 走的是「space is None」那条分支，直接按
    单行原字号算出写框，宽度不受任何限制。第 3 页右侧那条
    「5 pcs 40×80×3mm sq. tube …」重写后单行宽 494pt，从 x=683 一路写到
    x=1178，整条冲出页面（页宽 841.92），图上看起来就是文字越框被切掉。
    带占用图之后才走「探到障碍就折行」的正路。
    """
    space = None
    try:
        space = layout.PageSpace(page)
        # 原框马上要被涂掉重写，先从占用图里释放，否则探到的第一个障碍
        # 就是这条字自己，可用宽度恒为 0，只能一路缩字号
        space.release_many([cand["rect"]])
    except Exception:
        space = None
    if cand.get("angle"):
        # 斜排重写不带占用图。它不是「在框里缩」，而是「沿斜轴借空白」——
        # 而此刻页面上还压着上一版写的那行字，探测第一步就撞上它，
        # 借不到空白、落点还被推偏，于是两版错开叠着（实测合成图上是两条
        # 平行的 "RADIUS 35mm ROUND TUBE"）。上一版由调用方先擦掉
        # （见 pdf_engine._rewrite_record 的 stale），这里按原位重排即可。
        space = None
    # 字号口径要与首次写入一致：首次用的是渲染后实测的墨迹高度（ink_h），
    # 不是 OCR 框高。少了这一项，同一条字重写后字号会和上一版不一样。
    plan = _plan_one(cand, out, None, glyph_h_hint=cand.get("ink_h"),
                     space=space)
    if plan is None:
        return None
    if not paint_plans(page, [plan]):
        return None
    # 返回这一版实际落笔的框：打回记录要拿它更新 ink，
    # 否则下一轮再重写时，擦除只盖住第一版的位置，新写的那版会留下残字
    # （2026-11-19 实测第 3 页标题被连重写两次，图上留下
    #   `TEMPORA [TEMP. BARRICADE RACK TOP VIEW] OP VIEW` 三段叠字）。
    return plan[7] if len(plan) > 7 else cand["rect"]
