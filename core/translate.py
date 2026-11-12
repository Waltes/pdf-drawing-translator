"""
翻译调度层：四层通道，质量优先、逐级降级

  第一层  缓存          —— 同一句不重复花钱
  第二层  本地术语词典   —— 短标注、术语，离线、无限量、译法锁定
  第三层  AI 接口         —— 长句、整段说明，质量最高（需要用户配 key）
  第四层  免费在线接口   —— 没配 key 时的兜底，质量一般且有限流

设计原则（这几条是踩坑换来的）：
  1. 词典只碰短标注。长句交给词典会被逐词拆成「match the围栏or」，比不翻更糟。
  2. 译文必须干净。半中半英（Why this锁紧环、Padlock孔）一律判不合格。
  3. 翻不干净就保留原文。宁可整句还是英文，也不能交付读不懂的混合体。
  4. 数字必须保真。尺寸、型号、标准号丢了就是质量事故，校验不过就重试一次。
"""

import csv
import hashlib
import json
import math
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_HALF_UP

from . import dict_engine
from . import i18n
from . import paths
from .llm_client import (LLMClient, PRESETS, preset_for, price_of)
from .paths import DATA_DIR

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
CACHE_PATH = os.path.join(DATA_DIR, "cache.json")
FIXES_PATH = os.path.join(DATA_DIR, "fixes.csv")

# 在线接口偶尔会返回各种脏东西，必须拦在门外，不能写进图纸：
#   [ying4 ling3]   拼音注音（MyMemory 对无译文的词会这么返回）
#   %s %d           格式化占位残留
#   {0} {{name}}    模板占位残留
GARBAGE_RE = re.compile(r"\[[a-z]{2,}\d|%[sd]\b|\{\{?\d*[a-z_]*\}?\}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")
NUM_RE = re.compile(r"\d+(?:[.\-/]\d+)*")

# 公制长度单位 → 毫米的换算表，只为「数字保真」校验服务，不参与任何实际改写。
# 为什么需要它：中文图纸写「2.79米长」，模型按图纸习惯改写成「2790.00 mm」，
# 这是完全正确的换写法。但校验拿原文的 2.79 去译文里逐字找，一定找不到，
# 于是判「数字对不上」、把整条退回中文原文 —— 客户看到的就是一句没翻的中文。
# 有了这张表，同制内的换写法能过，跨制算错（4-1/8" 写成 100mm）照样被拦。
_METRIC_TO_MM = {
    "千米": 1_000_000.0, "公里": 1_000_000.0, "km": 1_000_000.0,
    "分米": 100.0, "dm": 100.0,
    "厘米": 10.0, "cm": 10.0,
    "毫米": 1.0, "mm": 1.0,
    "米": 1000.0, "m": 1000.0,
}
# 中文单位统一折算成英文代号，好跟译文里的单位对上号
_UNIT_CANON = {
    "千米": "km", "公里": "km", "分米": "dm", "厘米": "cm", "毫米": "mm", "米": "m",
}
# 长的单位名必须排在前面，否则「毫米」会被「米」先吃掉半截。
# 结尾的 (?![a-z]) 是防「5min」里的 m 被当成米。
_NUM_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(千米|公里|分米|厘米|毫米|米|km|dm|cm|mm|m)(?![a-z])",
    re.IGNORECASE)
# 译文侧只认英文单位代号
_NUM_UNIT_OUT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(km|dm|cm|mm|m)(?![a-z])", re.IGNORECASE)

# 中文译文里允许保留的计量单位（图纸单位随原图，不翻译）。
# 只在这些词紧跟数字时才放行，避免把句子里的普通英文单词（如 in）当成单位漏过。
UNIT_WORDS = {
    "mm", "cm", "m", "km", "um", "μm", "nm", "kg", "g", "mg", "t", "lb", "lbs",
    "oz", "ft", "in", "yd", "sq", "cu", "ga", "ml", "l", "pcs", "pce", "set",
    "sets", "hr", "min", "sec", "mpa", "kpa", "bar", "psi", "kn", "rpm", "hz",
    "kw", "w", "v", "a", "mah", "mil", "pt", "deg", "ph",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


# ===========================================================================
# 英制 → 公制换算（兜底）
# ===========================================================================
# 提示词能管住 AI 通道，管不住词典通道和免费在线通道 —— 那两条是逐词替换，
# 根本不认识 4-1/8" 是什么，只会原样留下。图纸上标着英寸、车间照着下料，
# 差一毫米就是错件，所以这里再补一道正则换算。
#
# 只认「写明了英制单位」的写法（英寸符号、inch、ft、lb、oz），绝不猜裸数字：
# 把光秃秃的 2 当成 2 英寸换成 50.8mm，会把「2 号件」「2 处」全毁掉。
# 也不认单独的 in —— 英文句子 "1 in the box" 里的 in 会被误当成英寸。
_M_IN = 25.4
_M_FT = 304.8
_M_LB = 0.45359237          # 磅 → 千克
_M_OZ = 0.028349523125      # 盎司 → 千克（质量一律用千克，不再单独用克）

# 英寸符号在真实图纸里有七八种写法，PDF 抽文字时还会被「排版化」：
#   "      直双引号，CAD 原生输出
#   ″      U+2033 双撇号（标准的英寸符号）
#   ” “    U+201D / U+201C 全角引号 —— Word、InDesign 排版过的 PDF 抽出来常是这两个，
#          人眼看着就是 4"，机器只认 ASCII 直引号就会整条漏掉
#   ''    两个直单引号，ASCII 图纸（尤其是从老 DXF 导出的）最常见
#   ’’    两个全角单引号，同上但被排版过
#   ′′    两个 U+2032
#   `     反引号，个别 CAD 字体映射错位时会出
# 注意这里绝不把单个 ' 当英寸：单个 ' 是英尺（4' = 1219.20mm，差 12 倍）。
_IN_MARK = r"(?:[\"″”“]|''|’’|′′|[`])"

# 英尺符号：' 直单引号、’ 全角右单引号、′ U+2032 分符号
_FT_MARK = r"(?:['’′])"

# 英寸/英尺前面允许出现的数字写法：
#   4          整数
#   1-1/2      带整数的混合分数（最常见的螺纹/管径写法）
#   1 - 1/2    同上，连字符两侧有空格（排版拉伸导致）
#   1 1/2      同上，用空格代替连字符（英美标准写法，图纸里极多）
#   3/4        纯分数
#   1.5        小数
# 分支顺序不能乱：混合分数的几种写法必须排在纯分数前面，
# 否则 1-1/2" 会先被 \d+/\d+ 抓成 1/2"，算出 12.70mm 而不是 38.10mm。
_IMP_NUM = r"(?:\d+\s*-\s*\d+/\d+|\d+\s+\d+/\d+|\d+/\d+|\d+\.\d+|\d+)"

# 数字左边允许紧贴什么 —— 这条断言决定了「漏算」还是「误算」
#
# 只挡小写字母和数字（单词粘连，如 part4"、type4"），放行大写字母。
# 为什么必须放行大写字母：机械图纸的尺寸标注大量带工程前缀，且前缀和数字之间
# 没有空格，紧贴着写：
#   R3-1/2"   半径 3.5 英寸
#   SR1-1/2"  球面半径
#   OD4"      外径
#   Ø4" φ4"   直径（符号本来就不在字母表里，一直能过）
#   L4" T4" W4" H4"   长/厚/宽/高
# 原来断言写的是 (?<![A-Za-z0-9/._-])，把 R3-1/2" 这类整条挡在门外 ——
# 实测用户那份护栏图纸第 6 页就印着两个 R3-1/2"，一条都没被换算。
#
# 放行大写不会误伤螺纹代号：1/4"-20、NPT 1/2" 走的是后面 _is_thread_code
# 的尾部/前缀检查，不依赖这条断言。
_LEAD = r"(?<![a-z0-9/._-])"

# 注意：这条正则不能加 re.I。
# _LEAD 靠 [a-z] 挡住小写单词粘连（part4"），一旦开 re.I，[a-z] 连大写一起挡，
# R3-1/2" 又会漏掉。所以单位词全部写成显式的大小写变体，不用忽略大小写开关。
# 单位词：显式列出大小写变体，不靠 re.I（原因见下面 IMPERIAL_RE 的注释）
_UNIT_WORDS = (r"[Ii][Nn][Cc][Hh][Ee][Ss]|[Ii][Nn][Cc][Hh]"
               r"|[Ff][Ee][Ee][Tt]|[Ff][Oo][Oo][Tt]"
               r"|[Ff][Tt](?![A-Za-z])|[Ll][Bb][Ss](?![A-Za-z])|[Ll][Bb](?![A-Za-z])"
               r"|[Oo][Zz](?![A-Za-z])")

IMPERIAL_RE = re.compile(
    _LEAD +
    r"(?P<num>" + _IMP_NUM + r")\s*"
    r"(?P<unit>" + _IN_MARK + r"|" + _UNIT_WORDS + r")")

# 数值范围：0.8–1.0 lb、2–3 in、10~12 oz
#
# 为什么单独一条：范围写法里的两个值各是一个量，必须都换算。
# 不处理的话正则会从中间切开，只吃掉后一个 —— 实测图纸第 7 页的
# 「about 0.8-1.0 lb」被换算成「0.8–0.454kg」：
# 后半个换了、前半个还是磅的数值，读起来像 0.8 到 0.454 千克，完全错。
#
# 分隔符只认破折号类（– — ~ ～），不认半角 "-"：
# "-" 在图纸里还兼职「带整数混合分数」的连接（1-1/2"），
# 认了会把 1-1/2" 当成「1 到 1/2 英寸」。
_RANGE_DASH = r"[–—~～]"

RANGE_RE = re.compile(
    _LEAD + r"(?P<a>" + _IMP_NUM + r")\s*(?P<dash>" + _RANGE_DASH + r")\s*"
    r"(?P<b>" + _IMP_NUM + r")\s*(?P<unit>" + _IN_MARK + r"|" + _UNIT_WORDS + r")")

# 英尺 + 英寸合写：2'-0"、12' 3-1/2"、8'-6"、2'-0''。建筑和护栏图纸极常见，
# 必须先整体吃掉，否则会先把 0" 换成 0mm，留下「2'-0mm」这种不伦不类的东西。
FT_IN_RE = re.compile(
    _LEAD + r"(?P<ft>\d+)\s*" + _FT_MARK + r"\s*(?:-\s*)?"
    r"(?P<inch>" + _IMP_NUM + r")\s*" + _IN_MARK)

# 单独英尺：5'、12.5'、8’。后面不许紧跟字母数字，否则 1990's 会被当成 1990 英尺
FT_RE = re.compile(_LEAD + r"(?P<num>\d+(?:\.\d+)?)\s*"
                   + _FT_MARK + r"(?![A-Za-z0-9])")

# 英寸的另一种缩写：4in、4 in.、4 IN。
# 为什么不直接认 in：英文句子 "1 in the box"、"2 in total" 里的 in 是介词，
# 一旦忽略大小写去匹配，就会把 1 换成 25.40mm，把整句改烂。
# 所以只认三种低风险写法：紧贴数字的 in、带句点的 in.、全大写的 IN。
IN_ABBR_RE = re.compile(
    _LEAD + r"(?P<num>" + _IMP_NUM + r")"
    r"(?P<unit>in(?![A-Za-z])|\s*in\.|\s*IN\b\.?)")

# 中文写法的英制单位：9英尺、12英寸、25磅、8盎司。
# 为什么必须认：英文图纸里的 9 ft 交给 AI 翻译时，模型有时会「贴心地」
# 译成「9英尺」而不是换算 —— 结果单位从英文换成中文，数值一个没动，
# 前面所有基于英制符号的换算全部绕过去了。实测在样例图纸上就出现过。
# 只认完整词（英尺/英寸），不认单字「寸」「尺」——那在管材里是市制俗称，
# 2寸管对应的是 DN50，按 2×25.4 算会得到一根不存在的管子。
CN_IMP_RE = re.compile(
    r"(?<![\d.])(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>英尺|英寸|磅|盎司)")


def _imp_value(num_text):
    """
    把 4-1/8 / 1 1/2 / 1 - 1/2 / 3/4 / 1.5 / 2 这几种写法都算成小数

    混合分数的三种分隔法先统一成 "整-分"，再走后面的分支：
      "1 1/2"   → "1-1/2"     空格分隔（英美标准写法）
      "1 - 1/2" → "1-1/2"     连字符两侧带空格（排版拉伸）
    不能直接删空格：删完 "1 1/2" 会变 "11/2"，算成 5.5 英寸，差 4 倍。
    """
    s = re.sub(r"\s+", " ", str(num_text).strip())
    s = re.sub(r"^(\d+) (\d+)/(\d+)$", r"\1-\2/\3", s)      # 1 1/2 → 1-1/2
    s = re.sub(r"^(\d+)\s*-\s*(\d+)/(\d+)$", r"\1-\2/\3", s)  # 1 - 1/2 → 1-1/2
    if "-" in s:
        whole, frac = s.split("-", 1)
        return float(whole) + _imp_frac(frac)
    if "/" in s:
        return _imp_frac(s)
    return float(s)


def _imp_frac(s):
    a, b = s.split("/", 1)
    b = float(b)
    return float(a) / b if b else 0.0


def _fmt_metric(v):
    """
    尺寸类数值（毫米）统一保留两位小数，末尾的零不省：19.05 / 104.78 / 609.60

    为什么不再省掉 ".00"：图纸上的数值要和公差、检验标准对齐，
    25.4 和 25.40 在不同人眼里精度含义不同，统一两位最省事，
    也免得 AI 通道（它按提示词输出两位）和正则通道格式不一致，
    同一个文件里出现 104.8 和 104.78 两种写法。

    不能直接用 f"{v:.2f}"：25.4*0.75 在浮点里是 19.049999999999997，
    格式化出来变 19.05 看似没事，但 0.375 这类值会掉到 .04；
    所以先把浮点毛刺按 9 位小数抹平，再用 Decimal 做标准四舍五入。
    """
    d = Decimal(repr(round(v, 9))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d:.2f}"


def _fmt_kg(v):
    """
    质量类数值（千克）按量级分档，其余同 _fmt_metric：

      ≥ 1kg  → 两位小数（0.01kg = 10g 精度，普通件够用）：11.34kg / 68.04kg
      < 1kg  → 三位小数（0.001kg = 1g 精度）：0.227kg / 0.028kg
      < 1g   → 四位小数，防止 0.4g 被写成 0.000kg 当成没有重量

    为什么小值要单独补一位：统一两位的话 1 盎司会写成 0.03kg，
    实际是 28.35g，被表达成 30g 量级 —— 五金袋、垫片这类轻小件过秤时
    这个误差是实打实的。三位小数只影响不到 1kg 的数，
    不会破坏「质量用 kg、大值两位小数」这条主规则。

    边界处理：0.9996kg 若按三位小数直接进位会得到 1.000kg，看着别扭，
    所以三位结果一旦 ≥1 就退回两位写法（1.00kg）。
    """
    d = Decimal(repr(round(v, 9)))
    if abs(d) < 1:
        places = Decimal("0.001") if abs(d) >= Decimal("0.001") else Decimal("0.0001")
        d3 = d.quantize(places, rounding=ROUND_HALF_UP)
        if abs(d3) < 1:
            return f"{d3:f}"
        # 进位后跨到 1 了，改用两位
        return f"{d3.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"
    return f"{d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def has_imperial(text):
    """这句里有没有需要换算的英制量。用来决定要不要处理，避免误伤无关文本"""
    if not text:
        return False
    return bool(IMPERIAL_RE.search(text) or FT_IN_RE.search(text)
                or FT_RE.search(text) or IN_ABBR_RE.search(text)
                or RANGE_RE.search(text) or CN_IMP_RE.search(text))


def _unit_plan(unit):
    """
    英制单位词 → (换算率, 格式化函数, 目标单位)。不认识返回 None。

    抽出来给三处共用：单值换算、范围换算、英尺英寸合写判定。
    原来这段分支在 repl 里写了一遍，加范围写法时又要抄一遍 ——
    抄出来的副本迟早和原件对不上，所以合成一个函数。
    """
    u = unit.lower()
    if u in ('"', "″", "”", "“", "`", "''", "’’", "′′", "inch", "inches"):
        return _M_IN, _fmt_metric, "mm"
    if u in ("ft", "foot", "feet", "'", "’", "′"):
        return _M_FT, _fmt_metric, "mm"
    if u in ("lb", "lbs"):
        return _M_LB, _fmt_kg, "kg"
    if u == "oz":
        return _M_OZ, _fmt_kg, "kg"
    return None


# 已经带 mm / kg 后缀的数值，用来统一小数位
#
# 后置断言为什么只挡小写、而且必须去掉 re.I：
#   中文译文里常写成「12.70mmU形夹销」「3.18mmC形卡簧」——毫米后面直接跟大写字母，
#   没有空格。原来的 (?![A-Za-z]) 会把这类值整个漏掉，校验层就认为
#   「译文里没有 mm」，误报成漏换算。去掉 re.I 之后 (?![a-z]) 才真的只挡小写，
#   单位的大小写靠字符类 [mM][mM] 显式覆盖，不依赖忽略大小写。
_METRIC_FMT_RE = re.compile(
    r"(?<![a-z0-9.])(\d+(?:\.\d+)?)(\s*)([mM][mM]|[kK][gG])(?![a-z])")


def normalize_metric_format(text):
    """
    把译文里所有 mm / kg 数值统一到规定的小数位。

    为什么正则通道算准了还要再做一遍：AI 通道是模型自己写的数，
    提示词有要求，实测绝大多数遵守，但不能保证 100%。同一个文件里
    冒出 104.8mm 和 104.78mm 两种写法，检验时会当成两个精度等级。
    出口统一抹一次，成本几乎为零。

    位数的规则和换算通道保持一致，不能各写各的：
      mm → 固定两位
      kg → ≥1 两位，<1 三位（轻小件补一位，避免 28g 被写成 0.03kg）

    只认紧跟 mm / kg 的数字，DN50、M8×30、Rev E 这些没后缀的代号一律不碰。
    """
    if not text or ("mm" not in text.lower() and "kg" not in text.lower()):
        return text

    def repl(m):
        unit = m.group(3).lower()
        try:
            v = Decimal(m.group(1))
        except Exception:
            return m.group(0)
        if unit == "kg":
            return _fmt_kg(float(v)) + m.group(2) + m.group(3)
        return f"{v:.2f}{m.group(2)}{m.group(3)}"

    return _METRIC_FMT_RE.sub(repl, text)


def convert_to_metric(text):
    """
    英制换算成公制。不认识的、像螺纹代号的，一律原样不动。

    螺纹规格 1/4"-20、NPT 1/2" 是标准件代号，换算过去国内买不到对应件，
    所以看到英寸符号后面紧跟 "-数字" 就直接放行。

    顺序有讲究，五步都不能换位置：
      0. 中文单位词（9英尺）最先，这类写法不进符号正则。
      1. 数值范围（0.8–1.0 lb）整体处理，否则只有后一个值会被换算。
      2. 英尺英寸合写其次，否则 2'-0" 里的 0" 会先被换掉，留下 2'-0mm。
      3. 英寸先于英尺，否则 4'' 会被当成 4 英尺（差 12 倍）。
      4. 英寸缩写（4in / 4 in. / 4 IN）单独一条，不能用忽略大小写的 in。
      5. 单独英尺最后，此时带英寸符号的已经被吃干净了。
    """
    if not text:
        return text

    # 0) 中文写法的英制单位（9英尺 / 25磅）。放在最前面：
    #    这类写法不进 IMPERIAL_RE，不先处理就一路过关，最后图纸上印着「9英尺」。
    def repl_cn(m):
        try:
            val = float(m.group("num"))
        except Exception:
            return m.group(0)
        unit = m.group("unit")
        if unit == "英尺":
            return _fmt_metric(val * _M_FT) + "mm"
        if unit == "英寸":
            return _fmt_metric(val * _M_IN) + "mm"
        if unit == "磅":
            return _fmt_kg(val * _M_LB) + "kg"
        if unit == "盎司":
            return _fmt_kg(val * _M_OZ) + "kg"
        return m.group(0)

    text = CN_IMP_RE.sub(repl_cn, text)

    # 1) 数值范围：0.8–1.0 lb。必须整体处理，否则只有后一个值会被换算
    def repl_range(m):
        plan = _unit_plan(m.group("unit"))
        if not plan:
            return m.group(0)
        factor, fmt, suffix = plan
        try:
            a = _imp_value(m.group("a"))
            b = _imp_value(m.group("b"))
        except Exception:
            return m.group(0)
        dash = m.group("dash")
        return f"{fmt(a * factor)}{dash}{fmt(b * factor)}{suffix}"

    text = RANGE_RE.sub(repl_range, text)

    # 2) 英尺 + 英寸合写：整体算成毫米
    def repl_ft_in(m):
        try:
            total = float(m.group("ft")) * 12 + _imp_value(m.group("inch"))
        except Exception:
            return m.group(0)
        return _fmt_metric(total * _M_IN) + "mm"

    text = FT_IN_RE.sub(repl_ft_in, text)

    # 2) 英寸 / 磅 / 盎司 / ft 单词写法
    #
    # 为什么英寸要排在单独英尺前面：
    #   4'' 这种写法（两个单引号表示英寸，ASCII 图纸最常见）如果先过 FT_RE，
    #   会被当成 4 英尺 + 一个多余的 ' ，算出 1219.20mm —— 差 12 倍，
    #   是能把整批料做废的错误。所以必须先让英寸规则把 '' 整体吃掉。
    def _is_thread_code(m):
        """判断这个量是不是螺纹/管件代号，代号一律不换算"""
        tail = text[m.end():]
        if re.match(r"-\s*\d", tail):
            return True                     # 1/4"-20、1/4"-20UNC
        head = text[max(0, m.start() - 6):m.start()].upper()
        return bool(re.search(r"(?:NPT|BSP|BSPT|NPS|UNC|UNF|UNEF)\s*$", head))

    def repl(m):
        if _is_thread_code(m):
            return m.group(0)

        plan = _unit_plan(m.group("unit"))
        if not plan:
            return m.group(0)
        factor, fmt, suffix = plan
        try:
            val = _imp_value(m.group("num"))
        except Exception:
            return m.group(0)
        return fmt(val * factor) + suffix

    text = IMPERIAL_RE.sub(repl, text)

    # 3) 英寸缩写：4in、4 in.、4 IN
    def repl_abbr(m):
        if _is_thread_code(m):
            return m.group(0)
        try:
            val = _imp_value(m.group("num"))
        except Exception:
            return m.group(0)
        return _fmt_metric(val * _M_IN) + "mm"

    text = IN_ABBR_RE.sub(repl_abbr, text)

    # 4) 单独英尺：5'、12.5'、8’
    def repl_ft(m):
        try:
            val = float(m.group("num"))
        except Exception:
            return m.group(0)
        return _fmt_metric(val * _M_FT) + "mm"

    return FT_RE.sub(repl_ft, text)


# ===========================================================================
# 公制 → 英制（输出单位选「英制」时才走这条路）
# ===========================================================================
# 为什么需要反方向：中文/公制图纸译给欧美客户看时，把 2000mm 原样留着，
# 对方车间同样没法下料。换算方向由用户在下拉里定，两条路必须对称 ——
# 对称的意思是：同样的容差、同样的校验、同样的标注版，不能一边认真一边糊弄。
#
# 只认写明了 mm / kg 的数值，裸数字一律不碰（和英制转公制同一条纪律）：
# 把光秃秃的 50 当成 50mm 换成 2"，会把「50 号件」「50 件」全毁掉。
_M_INV_IN = 1 / _M_IN
_M_INV_LB = 1 / _M_LB

_METRIC_NUM = r"\d[\d,]*(?:\.\d+)?"

# 数值左边界在这边要宽松一点：中文译文里常写「Ø50mm」「R12.7mm」，
# 也常写「尺寸50mm」—— 汉字紧贴数字是正常的，不能像英文那样挡。
# 但必须挡住「M8」「DN50」这类代号：它们后面跟的不是 mm，其实天然不会匹配。
_METRIC_RE = re.compile(
    r"(?<![a-z0-9.,])"
    r"(?P<num>" + _METRIC_NUM + r")\s*"
    r"(?P<unit>[mM][mM]|毫米|[kK][gG]|千克|公斤)")

_METRIC_RANGE_RE = re.compile(
    r"(?<![a-z0-9.,])"
    r"(?P<a>" + _METRIC_NUM + r")\s*(?P<dash>[–—~～])\s*"
    r"(?P<b>" + _METRIC_NUM + r")\s*"
    r"(?P<unit>[mM][mM]|毫米|[kK][gG]|千克|公斤)")

# 中文写法的公制单位：25毫米 / 3千克 / 1.5 公斤
CN_MET_RE = re.compile(
    r"(?<![\d.])(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>毫米|千克|公斤)")


def _fmt_inch(v):
    """
    毫米换算出来的英寸值 → 图纸上的写法。

    优先给分数：欧美图纸的车间习惯是读 4-1/8" 而不是 4.13"，
    给个小数对方还得自己换算成卷尺上的刻度。做法是在常见分母里找
    一个误差够小的（0.002" ≈ 0.05mm，正好是我们对毫米的容差量级），
    找到就用「整数-分子/分母」，找不到才退回两位小数 ——
    大尺寸像 2000mm → 78.74"，分母 64 都对不齐，硬凑成 78-47/64"
    反而没人看得懂。

    小数值必须防「舍成 0」：公差 0.05mm 约等于 1/500 英寸，
    round(0.002×64)=0，老代码会认下 0/64 并写出 0" ——
    等于把公差抹成零公差，比不换算危险得多。
    所以 0 这个解一律不收，宁可退到小数写法。
    """
    if v < 0:
        return "-" + _fmt_inch(-v)
    if v == 0:
        return "0"
    for d in (64, 32, 16, 8, 4, 2):
        n = round(v * d)
        if n == 0:
            # 这个分母表达不了这么小的值，换个更粗的分母也是 0，直接退小数
            continue
        if abs(n / d - v) <= 0.002:
            whole, rem = divmod(int(n), d)
            if rem == 0:
                return f"{whole}"
            g = math.gcd(rem, d)
            rem //= g
            d //= g
            return f"{whole}-{rem}/{d}" if whole else f"{rem}/{d}"
    # 小于 1/128 英寸（约 0.2mm）的细公差，分数写不出来，用小数并保住有效位：
    # 固定两位会把 0.002" 抹成 0.00"，等于没换算。位数加到结果不为 0 为止。
    for dec in (3, 4, 5):
        s = f"{v:.{dec}f}"
        if float(s) > 0:
            break
    # 三位小数末尾那个 0 是凑位数用的（78.740 / 0.020），抹掉更好读；
    # 只抹第三位之后的，两位小数保留 —— 小数点后一位的 0.50" 要留着
    if len(s.split(".")[-1]) > 2 and s.endswith("0"):
        s = s[:-1]
    return s


def _fmt_inch_text(v):
    """带英寸符号。符号用 ASCII 直引号：CAD 和老打印机对它兼容最好"""
    return _fmt_inch(v) + '"'


def _fmt_lb(v):
    """磅统一两位小数。末尾零保留，理由同毫米（精度含义要写明白）"""
    d = Decimal(repr(round(v, 9))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d:.2f}"


def has_metric(text):
    """这句里有没有要换算的公制量。用来决定要不要处理，避免误伤无关文本"""
    if not text:
        return False
    return bool(_METRIC_RE.search(text) or CN_MET_RE.search(text))


def convert_to_imperial(text):
    """
    公制换算成英制。顺序同 convert_to_metric：先中文单位词、再范围、再单值。

    范围要整体处理：写「10–12 mm」时如果逐值替换，会得到
    「0.39"–12"」这种事 —— 前一个换了、后一个还是毫米的数值顶着英寸符号。
    """
    if not text:
        return text

    def _plan(unit):
        u = unit.lower()
        if u in ("mm", "毫米"):
            return _M_INV_IN, "in"
        return _M_INV_LB, "lb"

    def _out(val, kind):
        return _fmt_inch_text(val) if kind == "in" else _fmt_lb(val) + " lb"

    # 0) 中文单位词
    def repl_cn(m):
        try:
            val = float(m.group("num"))
        except Exception:
            return m.group(0)
        factor, kind = _plan(m.group("unit"))
        return _out(val * factor, kind)

    text = CN_MET_RE.sub(repl_cn, text)

    # 1) 范围
    def repl_range(m):
        factor, kind = _plan(m.group("unit"))
        try:
            a = float(m.group("a").replace(",", ""))
            b = float(m.group("b").replace(",", ""))
        except Exception:
            return m.group(0)
        dash = m.group("dash")
        if kind == "in":
            return f"{_fmt_inch_text(a * factor)}{dash}{_fmt_inch_text(b * factor)}"
        return f"{_fmt_lb(a * factor)}{dash}{_fmt_lb(b * factor)} lb"

    text = _METRIC_RANGE_RE.sub(repl_range, text)

    # 2) 单值
    def repl(m):
        factor, kind = _plan(m.group("unit"))
        try:
            val = float(m.group("num").replace(",", ""))
        except Exception:
            return m.group(0)
        return _out(val * factor, kind)

    return _METRIC_RE.sub(repl, text)


# 输出单位模式。三个值贯穿「换算 → 校验 → 单位声明 → 标注版」整条链，
# 任何一环按默认公制硬编码，都会造出自相矛盾的文件。
UNIT_METRIC = "metric"
UNIT_IMPERIAL = "imperial"
UNIT_KEEP = "keep"
UNIT_MODES = (UNIT_METRIC, UNIT_IMPERIAL, UNIT_KEEP)


def normalize_unit_mode(mode):
    """配置里存的值可能是手改过的怪东西，一律折回合法值"""
    return mode if mode in UNIT_MODES else UNIT_METRIC


def convert_units(text, mode=UNIT_METRIC):
    """按选定单位换算一条文本。三个模式的唯一入口"""
    if not text:
        return text
    if mode == UNIT_IMPERIAL:
        return convert_to_imperial(text)
    if mode == UNIT_KEEP:
        return text
    return convert_to_metric(text)


def units_changed(src, out, mode=UNIT_METRIC):
    """这条译文相对原文有没有真的发生单位变化。标注版和单位声明都靠它判断"""
    if not src or not out:
        return False
    try:
        return convert_units(src, mode) != src
    except Exception:
        return False


# ===========================================================================
# 换算结果的校验：算完之后不能再信自己，必须用另一条路复算一遍
# ===========================================================================
# 为什么换算了还要验：
#   1) AI 通道的数值是模型自己写的。提示词要求两位小数、要求换算率，
#      实测大多数遵守，但模型偶尔会算错 —— 4-1/8" 写成 100mm 这种错，
#      图纸上就是错件，靠肉眼看几百条标注根本发现不了。
#   2) 正则通道也可能漏：写成「12 in」的（in 不敢当单位）、
#      藏在长句里的、被括号拆开的，第一遍换算未必覆盖全。
#   3) 两条通道产出的位数、单位类型可能不一致，同一张图上出现两种写法。
#
# 做法：拿原文用同一套规则**独立算一遍**得到期望值，再和译文里的公制值配对比较。
# 对不上且数量能一一对应，就直接改成复算值（这是我们算的确定值，可信）；
# 数量都对不上（说明模型漏算或改写了句子结构），只报警不动手 ——
# 那时候改哪个数都是猜，改错比不改更糟。

def _kind_cn(kind):
    """mm / kg 说成人话，写进校验清单时要给用户看。

    kind 在内部只表示「这是长度还是质量」，不表示单位本身 ——
    英制模式下同一个长度量写出来是 4-1/8" 而不是 104.78mm，
    但校验、配对、容差的逻辑完全一致，所以类别标签沿用一个。
    """
    return i18n.T("质量") if kind == "kg" else i18n.T("长度")


def _fmt_value(kind, v, mode=UNIT_METRIC):
    """按模式选格式化函数，保证校验改出来的值和换算通道的位数一致"""
    if mode == UNIT_IMPERIAL:
        return _fmt_lb(float(v)) if kind == "kg" else _fmt_inch(float(v))
    return _fmt_kg(float(v)) if kind == "kg" else _fmt_metric(float(v))


# 译文里的英制数值。分数写法（4-1/8"）必须整体吃进来，
# 否则只抓到 1/8 会把两个值都判成错。
_IMPERIAL_FMT_RE = re.compile(
    r"(?<![a-z0-9./\-])"
    r"(?P<num>\d+(?:\.\d+)?(?:\s*-\s*\d+/\d+)?)(?P<sp>\s*)"
    # 裸 in 必须排除「4 in the box」这种介词用法：后面跟空格 + 小写单词的一律不认。
    # 这和输入侧 IN_ABBR_RE 只认 in. / 全大写 IN 是同一条纪律的两面。
    r"(?P<unit>\"|″|”|''|[iI][nN](?![A-Za-z])(?!\s+[a-z])"
    r"|[iI][nN]\.|[iI][nN][cC][hH][eE][sS]?)")


def _extract_metric_values(text, mode=UNIT_METRIC):
    """
    抽出文本里所有换算后的数值。

    metric   → 认 mm / kg
    imperial → 认 4-1/8"、78.74"、11.34 lb

    返回 [(kind, Decimal 值, 起点, 终点, 原始写法)]。
    带坐标是为了改的时候能精确替换数字本身，不动后面的空格和单位。
    只认带单位的：DN50、M8×30、Ø50、Rev E 这些代号碰不得。
    """
    out = []
    if not text:
        return out

    if mode == UNIT_IMPERIAL:
        for m in _IMPERIAL_FMT_RE.finditer(text):
            try:
                v = Decimal(repr(_imp_value(m.group("num"))))
            except Exception:
                continue
            kind = "kg" if m.group("unit").lower().startswith("lb") else "mm"
            out.append((kind, v, m.start("num"), m.end("num"), m.group(0)))
        # 磅单独再扫一遍：上面那条正则的 unit 分支只为英寸而写
        for m in _IMPERIAL_LB_RE.finditer(text):
            try:
                v = Decimal(m.group(1))
            except Exception:
                continue
            out.append(("kg", v, m.start(1), m.end(1), m.group(0)))
        out.sort(key=lambda x: x[2])
        return out

    for m in _METRIC_FMT_RE.finditer(text):
        try:
            v = Decimal(m.group(1))
        except Exception:
            continue
        out.append((m.group(3).lower(), v, m.start(1), m.end(1), m.group(0)))
    return out


_IMPERIAL_LB_RE = re.compile(
    r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*(?:[lL][bB][sS]?|[pP][oO][uU][nN][dD][sS]?)(?![A-Za-z])")


def _metric_tol(kind, expect, mode=UNIT_METRIC):
    """
    允许多大误差算「对得上」。

    mm：0.05mm 或 0.5% 取大者。1/16" 换算成 1.59mm，模型写 1.6 也该放过。
    kg：0.002kg（2 克）或 0.5% 取大者 —— 小于 1kg 的值走三位小数，
        1 克的粒度不能被判成错。
    imperial：英寸 0.002"（≈0.05mm），磅 0.005 —— 和公制侧同一物理精度，
        不能因为换了单位就放松，否则 4-1/8" 写成 4" 会被当成误差内放过。
    容差不能放宽到「差不多就行」：图纸上 104.78 和 105 差 0.22mm，
    看着无所谓，实际是两条不同的料。
    """
    if mode == UNIT_IMPERIAL:
        if kind == "kg":
            return max(Decimal("0.005"), abs(expect) * Decimal("0.005"))
        return max(Decimal("0.002"), abs(expect) * Decimal("0.005"))
    if kind == "kg":
        return max(Decimal("0.002"), abs(expect) * Decimal("0.005"))
    return max(Decimal("0.05"), abs(expect) * Decimal("0.005"))


def _pair_by_value(expected, actual):
    """
    期望值和实际值配对：按数值最接近的原则贪心匹配。

    为什么不按出现顺序配：模型重写语序时会把数值挪位置，
    「4-1/8" 高 × 3 宽」译成「宽 3 × 高 4-1/8"」很常见，顺序配会全错。
    按值配的代价是：两个数量级相近的数值可能互换，所以后面还要求
    「同句里同单位的个数必须一致」才允许自动改数。
    """
    pairs = []
    used = set()
    for e in expected:
        best_j, best_d = None, None
        for j, a in enumerate(actual):
            if j in used:
                continue
            d = abs(a[1] - e[1])
            if best_d is None or d < best_d:
                best_j, best_d = j, d
        if best_j is None:
            pairs.append((e, None, None))
        else:
            used.add(best_j)
            pairs.append((e, actual[best_j], best_d))
    return pairs


class MetricReport:
    """
    一份 PDF 的计量数据校验账本。

    分三类记，因为用户要做的事不一样：
      checked —— 一共验了多少条（说明这套校验跑过了）
      fixed   —— 程序自己改正的（算错数、漏换算、小数位不对），用户不用管
      warned  —— 程序不敢动、要人看一眼的（数量对不上、单位对不上）
    """

    LIMIT = 400         # 明细上限，防止畸形 PDF 把内存吃满

    def __init__(self):
        self.reset()

    def reset(self):
        self.checked = 0
        self.fixed = 0
        self.warned = 0
        self.issues = []        # [(level, page, src, out, detail)]

    def add(self, level, page, src, out, detail):
        if level == "fix":
            self.fixed += 1
        else:
            self.warned += 1
        if len(self.issues) < self.LIMIT:
            self.issues.append((level, page, src, out, detail))

    def summary(self):
        """给状态条用的一行字"""
        if not self.checked and not self.issues:
            return i18n.T("计量数据校验：本份文件没有可换算的数值")
        s = i18n.fmt("计量数据校验：复算 {n} 条", n=self.checked)
        if self.fixed:
            s += i18n.fmt(" · 自动纠正 {n} 处", n=self.fixed)
        if self.warned:
            s += i18n.fmt(" · 需人工确认 {n} 处", n=self.warned)
        if not self.fixed and not self.warned:
            s += i18n.T(" · 全部一致")
        return s

    def has_warning(self):
        return any(i[0] != "fix" for i in self.issues)


def verify_metric_output(src, out, dst="zh", page=None, report=None,
                         mode=UNIT_METRIC):
    """
    校验一条译文的计量数据，能改的直接改，改不了的记下来。

    参数
      src     原文
      out     已换算、已规范化的译文
      dst     目标语言，只用于记录，不改变校验规则（两个方向的期望值算法一致：
              先把原文换成目标单位再抽值，原文本来就是目标单位的自然原样保留）
      page    页码，写清单用
      report  MetricReport，累积统计
      mode    输出单位。keep 模式直接跳过 —— 用户选的「不换算」，
              这里再去补算就是把他的选择推翻了

    返回 (修正后的译文, [(level, page, src, out, detail), ...])
    """
    issues = []

    def note(level, detail):
        issues.append((level, page, src or "", out, detail))
        if report is not None:
            report.add(level, page, src or "", out, detail)

    if not out or not out.strip() or mode == UNIT_KEEP:
        return out, issues

    fixed = out

    # ---- 1) 小数位复核 ----
    # 出口已经抹过一遍，这里是第二道。留着它的理由：万一以后有人改了出口顺序，
    # 或者 AI 通道的文本在上一步被别的东西重写过，这里还能兜住。
    # 英制侧没有这道：英寸是「整数-分数」，没有统一小数位可抹。
    if mode == UNIT_METRIC:
        nf = normalize_metric_format(fixed)
        if nf != fixed:
            note("fix", i18n.T("小数位不符合规定位数，已统一"))
            fixed = nf

    # ---- 2) 漏换算复核 ----
    # convert_units 对白名单（NPT 1/2"、1/4"-20）不动，所以「再跑一遍有没有变化」
    # 精确对应「还有该换没换的」，不会把故意保留的螺纹代号误报成漏换算。
    cf = convert_units(fixed, mode)
    if cf != fixed:
        note("fix", i18n.T("有英制数值没换算，已补算") if mode == UNIT_METRIC
             else i18n.T("有公制数值没换算，已补算"))
        fixed = normalize_metric_format(cf) if mode == UNIT_METRIC else cf

    # ---- 3) 复算比对 ----
    # 期望值 = 把原文按同一套规则换算后抽出来的目标单位值。
    # 原文本来就是目标单位的，convert 不动它，于是「原文数值是否忠于原值」
    # 和「换算对不对」用同一条路径覆盖了。
    expected = _extract_metric_values(convert_units(src or "", mode), mode)
    if not expected:
        return fixed, issues

    if report is not None:
        report.checked += 1

    actual = _extract_metric_values(fixed, mode)
    changes = []        # (起点, 终点, 新数值文本)

    for kind in ("mm", "kg"):
        exp_g = [x for x in expected if x[0] == kind]
        act_g = [x for x in actual if x[0] == kind]
        if not exp_g:
            continue
        if not act_g:
            note("warn", i18n.fmt(
                "原文有 {n} 处{kind}数值，译文里找不到对应数值（应为 {v} 等），需人工确认",
                n=len(exp_g), kind=_kind_cn(kind),
                v=_fmt_value(kind, exp_g[0][1], mode)))
            continue

        pairs = _pair_by_value(exp_g, act_g)
        bad = [(e, a, d) for e, a, d in pairs
               if a is not None and d > _metric_tol(kind, e[1], mode)]

        # 同句里同单位的个数必须一致才敢改数。
        # 个数不一致说明模型增删或合并了数值，配对本身就不可信 ——
        # 这时候改哪个都可能改错，只报不改。
        # 注意：数量不等这件事本身就要报，不能只在「误差超限」时才报。
        # 译文把两个尺寸合并成一个（3-3/4" x 1-1/2" → 95.25mm）时，
        # 剩下的那个值往往是准的，误差检查会全部通过，静默放行就漏了。
        if len(exp_g) != len(act_g):
            note("warn", i18n.fmt(
                "本句{kind}数量对不上（原文 {a} 处 / 译文 {b} 处），"
                "可能有数值被漏算或合并，需人工核对",
                kind=_kind_cn(kind), a=len(exp_g), b=len(act_g)))
            for e, a, d in bad:
                note("warn", i18n.fmt(
                    "译文 {got} 与复算值 {exp} 不符，未自动修改",
                    got=a[4], exp=_fmt_value(kind, e[1], mode)))
            continue

        for e, a, d in bad:
            newv = _fmt_value(kind, e[1], mode)
            changes.append((a[2], a[3], newv))
            note("fix", i18n.fmt("{got} → {new}（按原文 {src} 复算）",
                                 got=a[4], new=newv, src=e[4].strip()))

    if changes:
        # 从后往前替换，避免前面的改动把后面坐标撑歪
        for start, end, newv in sorted(changes, key=lambda c: -c[0]):
            fixed = fixed[:start] + newv + fixed[end:]

    return fixed, issues

# 词典命中率低于这个值才升级到 AI／在线。
# 定 0.7 的理由：图纸标注以专业术语为主，词典命中通常最准；
# 只有大量内容没被词典覆盖时，才值得交给整句翻译。
DICT_ACCEPT_RATIO = 0.7

# AI 单批里失败条目最多补发几轮。
# 定 2 的理由：第一轮把失败项凑一小批发，第二轮逐条单发。
# 实测第 7 页那三条「批内被带坏」的说明文字，逐条发一次就能出，
# 第三轮基本碰不到；真碰上说明模型那边有问题，再发也是白烧 token。
_LLM_RETRY_ROUNDS = 2

# 翻译方向说明，AI 和在线通道共用的语境
LANG_NAME = {"zh": "英文", "en": "中文"}


def default_config():
    return {
        "llm": {
            "enabled": False,
            "provider": "deepseek",
            "base_url": PRESETS["deepseek"]["base_url"],
            "api_key": "",
            "model": PRESETS["deepseek"]["models"][0],
            "temperature": 0.1,
            "batch_size": 30,
            "timeout": 90,
        },
        "online_fallback": True,
        "min_font_scale": 0.62,
        # 翻译方向与输出单位。老配置文件里没有这一段，
        # 默认值必须与改造前的行为逐字一致，否则用户升级完发现图纸全变了：
        #   src=auto 每条文字自己判断是中还是英
        #   dst=auto 中→英、英→中，方向相反着来（原来的行为）
        #   units=metric 英制一律换算成毫米/千克
        "translate": {
            "src": "auto",
            "dst": "auto",
            "units": UNIT_METRIC,
        },
    }


def _restore_api_key(cfg, saved):
    """
    让配置里的 key 和独立保存的那份保持一致。返回 True 表示 key 是从独立文件补回来的。

    为什么要这一步：客户丢过一次 key。他卸载后重装，卸载时被问「配置和翻译缓存
    要不要一起删」，点了「是」，config.json 连 key 一起没了；而他当初填 key 时
    没留底，丢了只能回来找我们。现在 key 在 data 的上一级另存一份（见
    paths.secret_file），卸载删不到，启动时发现配置里没有就补回来。

    两个方向都管：
      · 配置里有 key、独立文件没有或不一致 —— 补写独立文件（顺手留个底）
      · 配置里没有 —— 还要看是用户自己清空的，还是配置丢了：
        文件不存在、读不出来、连 api_key 这个键都没有，都算丢，补回来；
        文件好着、api_key 键在、值是空的，那是用户自己删的，尊重他，
        并且把独立文件也清掉，否则下次启动它又冒出来，用户会以为删不掉。

    saved 是磁盘上的原始内容，None 表示文件不存在或解析失败。
    """
    try:
        from . import secret
    except Exception:
        return False

    llm = cfg.setdefault("llm", {})
    current = (llm.get("api_key") or "").strip()
    backup = secret.load_key()

    if current:
        if backup != current:
            secret.save_key(current)
        return False

    # 用户在界面上主动清空的：配置里留着空的 api_key 键
    if saved is not None and "api_key" in (saved.get("llm") or {}):
        if backup:
            secret.clear_key()
        return False

    if not backup:
        return False

    llm["api_key"] = backup
    # 只补 key 不够，开关也得一起补。
    # cfg 这个时候是 default_config()，enabled 是默认的 False；而 available()
    # 看的是 cfg.get("enabled", True)，键存在且为 False 就直接判定不可用。
    # 后果是 key 明明回到了输入框里，一翻译却抛「AI 接口未配置完整：缺少参数」
    # ——key 和模型都不缺，用户完全看不懂（这个路径实测踩到过）。
    # 走恢复就说明配置文件已经丢了，不存在「用户自己关掉的」这种可能，直接开。
    llm["enabled"] = True
    try:
        save_config(cfg)            # 写回配置，下次启动就走正常路径了
    except Exception:
        pass
    return True


def _restore_config_backup():
    """
    config.json 整个不见了（卸载时勾了「删配置」）时，用安装器留下的备份还原。

    安装器每次覆盖安装前会把当前 config.json 复制成上一级的 config.bak.json，
    它在 data 外面，卸载删不到。用户「卸载 → 重装」之后，这份备份就是翻译方向、
    输出单位、AI 模型名这些设置的唯一存根；API key 另有 apikey.dat 专门兜。

    只在 config.json 不存在时还原，绝不覆盖用户正在用的配置 ——
    否则用户改完设置一升级，反而被还原成旧的。
    """
    bak = paths.config_backup_file()
    if os.path.exists(CONFIG_PATH) or not os.path.exists(bak):
        return False
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        shutil.copy2(bak, CONFIG_PATH)
        return True
    except Exception:
        return False


def load_config():
    """读取配置文件；不存在则返回默认配置"""
    _restore_config_backup()
    cfg = default_config()
    saved = None
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # 逐层合并，避免旧配置文件缺字段导致 KeyError
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
            # enabled 是历史遗留开关，老配置文件里没有这个键，而默认配置兜的是
            # False。只要存了 key 就说明用户是想用的，这里补成 True —— 否则界面上
            # 永远显示「AI 接口：未配置」，用户会以为自己的 key 没生效（踩过）
            #
            # 本机部署是个例外：它本来就不填 key，拿「有没有 key」判启用会把它
            # 判成未配置，用户选完本机模型、保存、重启，功能就没了。
            if "enabled" not in (saved.get("llm") or {}):
                from .llm_client import is_local_url
                llm = cfg["llm"]
                llm["enabled"] = (bool(llm.get("api_key"))
                                  or llm.get("provider") == "local"
                                  or is_local_url(llm.get("base_url") or ""))
        except Exception:
            # 文件在但读不出来（损坏、权限、写到一半被打断）。
            # 当成「没有配置」处理 —— 后面的 key 恢复要靠这个判断，
            # 不能静默当成「用户主动清空」，否则正好把要救的 key 当成用户意图。
            saved = None
            cfg = default_config()
    _restore_api_key(cfg, saved)
    return cfg


def save_config(cfg):
    """保存配置（含 API key，仅存本地，不上传任何地方）"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    # key 另存一份到卸载删不到的位置。这一步失败不影响配置已经存好，
    # 所以不往外抛：备份丢了是遗憾，存配置失败才是故障。
    key = ((cfg.get("llm") or {}).get("api_key") or "").strip()
    if key:
        try:
            from . import secret
            secret.save_key(key)
        except Exception:
            pass


class Translator:
    """统一翻译入口。界面层只调 translate_many / translate，不关心内部用哪条通道"""

    def __init__(self, config=None):
        self.cfg = config or load_config()
        self.glossary = dict_engine.Glossary()
        self.cache = self._load_cache()
        self.fixes = self._load_fixes()
        self.stats = {"dict": 0, "online": 0, "llm": 0, "cache": 0, "failed": 0}
        self._last_online_call = 0.0
        self._rate_limited = False      # 撞到 429 后置真，本批次不再重试在线接口
        self.last_llm_error = ""        # 最近一次 AI 通道的失败原因（给界面显示）
        # 译文被质检拒掉的原因统计。和上面那个分开记 ——
        # 「接口不通」和「模型答了但没通过质检」是两件完全不同的事，
        # 混在一起报，用户会跑去设置里测连接，其实接口一直是好的。
        self.last_llm_reject = ""
        self._reject_stats = {}
        self.llm = self._make_client()
        # 换算结果的校验记录（每次导出重置一次，见 reset_metric_report）
        self.metric_report = MetricReport()
        # 当前正在处理的页码，由 pdf_engine 每页写一次，只用于给校验清单标页码
        self.page_no = None
        # 方向与输出单位。三项都存在 cfg["translate"] 里，界面改完调 set_direction
        self._load_direction()

    def _load_direction(self):
        tr = (self.cfg or {}).get("translate") or {}
        self.src_lang = tr.get("src") or "auto"
        self.dst_lang = tr.get("dst") or "auto"
        self.unit_mode = normalize_unit_mode(tr.get("units"))

    def set_direction(self, src=None, dst=None, units=None, save=True):
        """
        界面上的三个下拉改了就调这里，热生效，不用重启。

        为什么单位和方向要放到同一个方法：它们共享同一条出口逻辑
        （换算 → 校验 → 标注），分开设会出现「方向已经变了、校验还按老方向算」
        的半截状态，正好落在一次转换中间时最难排查。
        """
        tr = self.cfg.setdefault("translate", {})
        if src is not None:
            self.src_lang = src
            tr["src"] = src
        if dst is not None:
            self.dst_lang = dst
            tr["dst"] = dst
        if units is not None:
            self.unit_mode = normalize_unit_mode(units)
            tr["units"] = self.unit_mode
        if save:
            try:
                save_config(self.cfg)
            except Exception:
                pass

    def _resolve(self, hint):
        """
        一条文字该从什么语言译到什么语言。返回 (src, dst)，dst 为 None 表示不翻。

        hint 是 pdf_engine 用 detect_lang 认出来的语言（zh / en / none）。
        三条规则：
          1. 源语言选「自动」→ 信 hint；
          2. 源语言选了具体语种 → 只翻这种语言的条目，别的原样留着
             （图纸里混着型号、英文缩写是常态，全翻会把代号毁掉）；
          3. 目标语言选「自动」→ 翻成**系统语言**（见 i18n.auto_target）。
             以前这里是「中译英、英译反」，等于假设用户母语非中即英；
             日本用户拿英文图纸时，那条规则会给他中文版，明显不对。
        """
        if self.src_lang == "auto":
            src = hint if hint in ("zh", "en") else ""
        else:
            src = self.src_lang
            # 选了中/英这种能认出来的语种时，按 hint 过滤；
            # 选了德/法/日这种认不出来的，只能全认（宁可多翻，不要漏翻）
            if src in ("zh", "en") and hint != src:
                return src, None
            if src not in i18n.CODES:
                src = ""

        dst = self.dst_lang
        if dst == "auto":
            dst = i18n.auto_target()
        # 原文已经是目标语言时不用翻。这里刻意不回退成「另一种语言」：
        # 英文用户拿英文图纸，正确结果就是原样不动，而不是偷偷给他中文版
        if not src or src == dst:
            return src, None
        return src, dst

    # ---------------- AI 客户端 ----------------
    def _make_client(self):
        return LLMClient(self.cfg.get("llm", {}))

    def reload_llm(self, cfg=None):
        """设置改了以后热更新客户端，不用重启程序"""
        if cfg is not None:
            self.cfg = cfg
        old = getattr(self, "llm", None)
        self.llm = self._make_client()
        # 把记账本接下来，否则用户在设置里点一次「保存」，本次消耗就被清零了
        if old is not None:
            self.llm.usage = old.usage

    def llm_ready(self):
        return self.llm.available()

    # ---------------- 术语纠错表 ----------------
    def _load_fixes(self):
        """
        加载机翻纠错表。机器翻译对专业术语会系统性翻错
        （collar → "衣领"、sleeve → "袖子"、extender posts → "扩展程序站子"），
        这些错误可预测，用一张替换表批量纠正，比换引擎划算。
        AI 时代的用途变了：主要兜住「免费机翻」和「词典拼装」的结果。
        """
        pairs = []
        if os.path.exists(FIXES_PATH):
            try:
                with open(FIXES_PATH, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.reader(f)
                    next(reader, None)
                    for row in reader:
                        if len(row) >= 2 and row[0].strip() and row[1].strip():
                            pairs.append((row[0].strip(), row[1].strip()))
            except Exception:
                pass
        # 长词优先替换，否则 "项圈" 会先命中，导致 "这个项圈" 这类长词条失效
        pairs.sort(key=lambda p: -len(p[0]))
        return pairs

    def _apply_fixes(self, text):
        """对中文译文套用术语纠错"""
        if not text or not CJK_RE.search(text):
            return text
        for wrong, right in self.fixes:
            if wrong in text:
                text = text.replace(wrong, right)
        return text

    def _is_garbage(self, text):
        """检测在线接口返回的垃圾译文（拼音注音、格式化残留等）"""
        return bool(not text or GARBAGE_RE.search(text))

    # ---------------- 缓存 ----------------
    def _data_sig(self):
        """
        术语库和纠错表的指纹。
        用户改了 glossary.csv 或 fixes.csv 之后，旧缓存里的译文就作废，
        否则会出现「明明补了词条，结果还是老样子」这种莫名其妙的情况。
        """
        h = hashlib.md5()
        for p in (dict_engine.GLOSSARY_PATH, FIXES_PATH):
            if os.path.exists(p):
                h.update(f"{os.path.getmtime(p):.3f}:{os.path.getsize(p)}".encode())
        return h.hexdigest()[:16]

    def _load_cache(self):
        if not os.path.exists(CACHE_PATH):
            return {}
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "_sig" not in data:
                return {}
            if data.get("_sig") != self._data_sig():
                return {}      # 词典改过，旧缓存整批作废
            return data.get("items", {})
        except Exception:
            return {}

    def save_cache(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            items = self.cache
            if len(items) > 20000:          # 控制文件体积
                items = dict(list(items.items())[-15000:])
                self.cache = items
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"_sig": self._data_sig(), "items": items}, f, ensure_ascii=False)
        except Exception:
            pass

    # ---------------- 第二层：本地词典 ----------------
    def _dict_translate(self, text, src, dst):
        """
        返回 (译文, 未命中占比)
        未命中占比 = 未翻译字符数 / 总有效字符数

        词典只有中↔英两张表。西班牙语、日语这些方向直接返回空 ——
        硬拿英文词典去拼只会拼出不伦不类的东西，交给 AI 更省钱。
        """
        if (src, dst) == ("zh", "en"):
            out, miss = dict_engine.zh_to_en(text, self.glossary)
            total = max(len(re.sub(r"\s", "", text)), 1)
            ratio = len(miss) / total
            return out, ratio
        if (src, dst) == ("en", "zh"):
            out, miss = dict_engine.en_to_zh(text, self.glossary)
            words = [w for w in re.findall(r"[A-Za-z]+", text)]
            total = max(len(words), 1)
            ratio = len(miss) / total
            return out, ratio
        return "", 1.0

    # ---------------- 第四层：在线免费接口 ----------------
    def _online_translate(self, text, src, dst, throttle=True):
        """
        MyMemory 免费接口。实测是国内网络下唯一免 key 且稳定的通道。
        但它有硬配额，密集请求会直接 429 封掉，所以一旦撞上就把本批全部标记为限流，
        后面的请求快速跳过，免得整份图纸挨个超时等一遍。
        """
        if self._rate_limited:
            return None

        sc = i18n.ONLINE_CODE.get(src)
        dc = i18n.ONLINE_CODE.get(dst)
        if not sc or not dc:
            return None
        q = urllib.parse.quote(text[:480])   # 接口单次有长度上限
        url = f"https://api.mymemory.translated.net/get?q={q}&langpair={sc}|{dc}"

        if throttle:
            wait = 0.6 - (time.time() - self._last_online_call)
            if wait > 0:
                time.sleep(wait)

        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=12) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            self._last_online_call = time.time()
            out = (data.get("responseData", {}) or {}).get("translatedText", "")
            if not out or out.strip().upper() == text.strip().upper():
                return None
            if "MYMEMORY WARNING" in out.upper():
                self._rate_limited = True
                return None
            out = out.strip()
            if self._is_garbage(out):
                return None
            return self._apply_fixes(out)
        except urllib.error.HTTPError as e:
            self._last_online_call = time.time()
            if e.code in (429, 403):
                self._rate_limited = True
            return None
        except Exception:
            self._last_online_call = time.time()
            return None

    # ---------------- 第三层：AI 接口 ----------------
    def _glossary_hints(self, texts, limit=60):
        """
        从术语库里挑出「本批文字里确实出现过的」术语，作为参考表喂给模型。

        这是保证术语一致性的关键：
        用户可以在 glossary.csv 里锁定译法（比如 collar 必须译成"锁紧环"），
        模型会遵守这张表，同一份图纸里同一个零件不会一会儿叫锁紧环一会儿叫卡箍。
        """
        if not self.glossary.en2zh:
            return []
        blob = " ".join(t.upper() for t in texts)
        hits = []
        for en, zh in self.glossary.en2zh.items():
            if len(en) < 2 or " " in en and len(en) > 24:
                continue
            # 只取真正出现在本批文字中的词，避免把整本词典塞进请求里烧 token
            if re.search(r"(?<![A-Z])" + re.escape(en) + r"(?![A-Z])", blob):
                hits.append((en, zh))
                if len(hits) >= limit:
                    break
        hits.sort(key=lambda p: -len(p[0]))
        return hits

    def _unit_rule_block(self):
        """
        提示词里的单位换算条款。三种输出单位各写一段。

        为什么要写进提示词而不是全靠出口的正则：AI 通道是整句重写，
        模型顺手就把「4-1/8" high」写成「4-1/8" 高」—— 它压根没算，
        等出口正则去补，量出来的数值和它写在同一句里，格式容易打架。
        提示词先说清楚，出口再兜一道，两层都不省。
        """
        en = i18n.is_en()
        if self.unit_mode == UNIT_KEEP:
            return ("4. Keep every dimension and weight exactly as written, unit included. "
                    "Convert nothing: 2000mm stays 2000mm, 4-1/8\" stays 4-1/8\"."
                    if en else
                    "4. 保持原文的计量单位与数值，一个数都不要换算："
                    "2000mm 仍是 2000mm，4-1/8\" 仍是 4-1/8\"。")
        if self.unit_mode == UNIT_IMPERIAL:
            if en:
                return (
                    "4. Convert every dimension into inches and every mass into pounds. "
                    "1 mm = 0.03937 in, 1 kg = 2.20462 lb. Write inches as the fractions used "
                    "on drawings (denominators 2/4/8/16/32/64): 104.78mm → 4-1/8\"; "
                    "19.05mm → 3/4\"; 25.40mm → 1\". Use two decimals when no clean fraction "
                    "exists (e.g. 78.74\"). Pounds to two decimals.")
            return (
                "4. 尺寸、长度一律换算成英寸，重量换算成磅。1 毫米=0.03937 英寸，"
                "1 千克=2.20462 磅。英寸用图纸惯用的分数写法，分母取 2/4/8/16/32/64 "
                "中最接近的那个：104.78mm 写 4-1/8\"；19.05mm 写 3/4\"；"
                "25.40mm 写 1\"。大尺寸凑不整齐数时保留两位小数（如 78.74\"）。"
                "磅保留两位小数。")
        if en:
            return (
                "4. Convert every dimension, length and weight into metric. Actually compute the "
                "number — do not merely swap the unit letters. Rates: 1 inch = 25.4 mm, "
                "1 foot = 304.8 mm, 1 pound = 0.45359237 kg, 1 ounce = 0.0283495 kg. "
                "Dimensions in millimetres, mass in kilograms (ounces also to kg), never metres.\n"
                "   Number format: dimensions always 2 decimals; mass 2 decimals at or above "
                "1 kg, 3 decimals below 1 kg. Keep trailing zeros: 4-1/8\" → 104.78mm; "
                "3/4\" → 19.05mm; 2' → 609.60mm; ±1/16\" → ±1.59mm; 1\" → 25.40mm; "
                "25 lb → 11.34kg; 8 oz → 0.227kg; 1 oz → 0.028kg.\n"
                "   Never render ft/inch/lb/oz as words only — that changes nothing numerically.")
        return (
            "4. 尺寸、长度、重量这些计量数据一律换算成公制，不是换掉单位字母，"
            "是把数值真算出来。换算率：1 英寸=25.4 毫米，1 英尺=304.8 毫米，"
            "1 磅=0.45359237 千克，1 盎司=0.0283495 千克。"
            "尺寸统一用毫米，质量统一用千克（盎司也要化成千克，不要用克），"
            "不要折算成米。\n"
            "   数值格式：尺寸固定两位小数；质量大于等于 1kg 用两位，"
            "小于 1kg 用三位（轻小件才不丢精度）。末尾的零必须写出来："
            "4-1/8\" 写 104.78mm；3/4\" 写 19.05mm；2' 写 609.60mm；"
            "±1/16\" 写 ±1.59mm；1\" 写 25.40mm；25 lb 写 11.34kg；"
            "8 oz 写 0.227kg；1 oz 写 0.028kg。\n"
            "   特别注意：不许把 ft、inch、lb、oz 只译成「英尺、英寸、磅、盎司」。"
            "那只是换了个说法，数值一个没动，等于没换算 —— 图纸上印着「9英尺」"
            "国内车间没法用。见到这几种单位，一律按第 4 条算成 mm 或 kg 再写。")

    def _system_prompt(self):
        """
        系统提示词。中文界面出中文提示词、英文界面出英文提示词。

        为什么跟界面语言走而不是跟翻译方向走：国际版用户多半挂着 OpenAI/Claude，
        英文提示词对它们最省事；中文用户挂 DeepSeek/通义，中文提示词最稳。
        界面语言正好就是「这个人习惯哪种语言」的最准信号。
        """
        rule = self._unit_rule_block()
        if i18n.is_en():
            return (
                "You are a senior engineering-drawing translator working on mechanical, hardware, "
                "building-material and fence/railing drawings. Your output is printed on the drawing "
                "in place of the original; readers are shop-floor workers and installers.\n"
                "Rules:\n"
                "1. Use standard industry terminology, never everyday meanings: collar=locking collar "
                "(not shirt collar), sleeve=bushing (not clothing sleeve), post=post/upright, "
                "rail=rail, brace=brace, foot=base plate, gate=gate leaf, hinge=hinge, "
                "clevis pin=clevis pin, padlock=padlock, panel=panel, channel=channel, "
                "swivel=swivel, liner=liner.\n"
                "2. Long sentences must read naturally in the target language. Rewrite in that "
                "language's word order; no word-for-word translation. It must read like "
                "installation instructions written by a local engineer.\n"
                "3. Short labels: concise term only. No articles, no trailing period, no "
                "explanations that are not in the source.\n"
                + rule + "\n"
                "5. These are part numbers and codes: never convert or rewrite them, copy verbatim — "
                "thread and fastener specs (1/4\"-20, M8x30, NPT 1/2), standards (ANSI, ASTM, DIN, "
                "GB, EN), model and drawing numbers (Rev E, A-3, DN50, SCH40), material grades, "
                "dates, page numbers.\n"
                "6. In Chinese, \"寸\" used for pipes is a nominal size, not inches — write DN25, "
                "DN32, DN40, DN50, DN80, DN100 for 1, 1.2, 1.5, 2, 3, 4 寸.\n"
                "7. Do not leave words from the source language in the output (part numbers, units, "
                "standard codes and drawing numbers excepted). When translating into Chinese, render "
                "O.D. as 外径, I.D. as 内径, THK as 厚度.\n"
                "8. Keep one consistent rendering per term across the whole drawing.\n"
                "9. Output JSON only, no prose.\n"
                "Format: [{\"id\":0,\"text\":\"translation\"}]")
        return (
            "你是资深工程图纸翻译，长期为机械、五金、建材、护栏围栏类图纸做多语种互译。"
            "你的译文会直接印在图纸上替换原文，读者是车间工人和安装人员。\n"
            "必须遵守：\n"
            "1. 术语用行业标准说法，不用日常义。例：collar=锁紧环（不是衣领/项圈）、"
            "sleeve=套管（不是袖子）、post=立柱、rail=横梁、brace=斜撑、foot=底座、"
            "upright=立杆、gate=门扇、hinge=铰链、clevis pin=U形夹销、padlock=挂锁、"
            "panel=面板、channel=槽钢、swivel=旋转节、liner=衬垫。\n"
            "2. 长句必须译成通顺的目标语言，按目标语言的语序重写，不要逐字直译。"
            "读起来要像当地工程师写的安装说明，能直接照着做。\n"
            "3. 短标注（几个词）译成简洁术语：不加助词，不加句号，不添加原文没有的解释。\n"
            + rule + "\n"
            "5. 以下是标准件代号或编号，绝对不能换算、不能改写，原样照抄："
            "螺纹与紧固件规格（1/4\"-20、M8×30、NPT 1/2）、标准号（ANSI、ASTM、DIN、GB、EN）、"
            "型号与图号（Rev E、A-3、DN50、SCH40）、材料牌号、日期、页码。\n"
            "6. 中文里的「寸」用在管材（钢管、方管、圆管）上是俗称，不是英寸，"
            "要按公称直径写：1寸=DN25、1.2寸=DN32、1.5寸=DN40、2寸=DN50、3寸=DN80、4寸=DN100。"
            "这种地方写 DN 口径，不要写换算出来的毫米数。\n"
            "7. 译文里不许残留源语言的词汇（型号、单位、标准号、图号除外）。"
            "译成中文时要把 O.D. 译成外径、I.D. 译成内径、THK 译成厚度。\n"
            "8. 同一份图纸内同一术语必须译法统一。\n"
            "9. 只输出 JSON，不要任何解释文字。\n"
            "输出格式：[{\"id\":0,\"text\":\"译文\"}]")

    def _build_messages(self, batch):
        """
        组请求。batch 的每一项是 (原文, 源语言, 目标语言)。

        目标语言放在每条的 target 字段里，而不是写死在系统提示词里：
        选「自动」时同一页可能中英混排，两个方向要同时进行，
        系统提示词只能给规则，方向得逐条说。
        """
        hints = self._glossary_hints([b[0] for b in batch])
        term_block = ""
        if hints:
            if i18n.is_en():
                term_block = ("\nReference glossary (these renderings are mandatory, do not invent "
                              "your own):\n" + "; ".join(f"{en}={zh}" for en, zh in hints))
            else:
                term_block = "\n参考术语表（这些词的译法必须严格遵守，不许自创）：\n" + \
                             "；".join(f"{en}={zh}" for en, zh in hints)

        payload = [{"id": i, "text": t, "target": i18n.prompt_name(d)}
                   for i, (t, _s, d) in enumerate(batch)]
        if i18n.is_en():
            user = ("Translate each item below into the target language given in its own "
                    "\"target\" field." + term_block +
                    "\n\nItems:\n" + json.dumps(payload, ensure_ascii=False))
        else:
            user = ("把下面每条文字翻译成它自己 target 字段里注明的目标语言。" + term_block +
                    "\n\n待译内容：\n" + json.dumps(payload, ensure_ascii=False))
        return [{"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": user}]

    @staticmethod
    def _parse_llm_json(content, count):
        """
        解析模型返回。模型经常不老实地包 ```json、或多说两句话，
        所以三级容错：整体解析 → 抠出对象 → 逐行解析。
        """
        if not content:
            return None
        text = content.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()

        def collect(objs):
            out = [None] * count
            ok = 0
            for obj in objs:
                if not isinstance(obj, dict):
                    continue
                i = obj.get("id")
                val = obj.get("text")
                if isinstance(i, str) and i.isdigit():
                    i = int(i)
                if isinstance(i, int) and 0 <= i < count and isinstance(val, str):
                    out[i] = val.strip() or None
                    ok += 1
            return out if ok else None

        # 1) 整体是数组
        try:
            data = json.loads(text)
            if isinstance(data, list):
                r = collect(data)
                if r:
                    return r
            if isinstance(data, dict):
                for key in ("items", "data", "result", "translations"):
                    if isinstance(data.get(key), list):
                        r = collect(data[key])
                        if r:
                            return r
                r = collect([data])
                if r:
                    return r
        except Exception:
            pass

        # 2) 从任意文本里抠出 JSON 对象，不管外面包了什么
        objs = []
        for m in re.finditer(r'\{[^{}]*?"id"\s*:\s*\d+[^{}]*?\}', text, re.S):
            try:
                objs.append(json.loads(m.group(0)))
            except Exception:
                continue
        if objs:
            r = collect(objs)
            if r:
                return r

        # 3) 退而求其次：解析 "0: 译文" / "0. 译文" 这种行格式
        out = [None] * count
        ok = 0
        for line in text.splitlines():
            m = re.match(r"\s*(\d+)\s*[:：.、]\s*(.+)", line)
            if m:
                i = int(m.group(1))
                if 0 <= i < count:
                    out[i] = m.group(2).strip().strip('"').strip() or None
                    ok += 1
        return out if ok else None

    def _llm_one_chunk(self, batch, retry_json_mode=True):
        """翻一小批，返回列表（失败项为 None）。batch 项为 (原文, 源语言, 目标语言)"""
        messages = self._build_messages(batch)
        # 输出预留按条数估算：一条长句的中文最多一两百字，给足空间才不会被截断
        max_tokens = min(8192, 256 + 160 * len(batch))
        try:
            content, _usage = self.llm.chat(messages, max_tokens=max_tokens)
        except Exception as e:
            self.last_llm_error = str(e)
            self.llm.usage.note_failure()
            return [None] * len(batch)

        parsed = self._parse_llm_json(content, len(batch))
        if parsed is None and retry_json_mode:
            # 有些服务商不支持 response_format，或模型没按格式回；
            # 再发一次，明确要求纯 JSON 数组
            messages2 = messages + [{"role": "user",
                                     "content": i18n.T("只输出 JSON 数组，不要任何其他文字。")}]
            try:
                content2, _ = self.llm.chat(messages2, max_tokens=max_tokens)
                parsed = self._parse_llm_json(content2, len(batch))
            except Exception as e:
                self.last_llm_error = str(e)
        if parsed is None:
            self.last_llm_error = self.last_llm_error or i18n.T("模型返回的内容无法解析为译文表")
            return [None] * len(batch)

        # 后处理：术语纠错 + 数字保真校验
        out = []
        for (src, _s, dst), got in zip(batch, parsed):
            if not got:
                out.append(None)
                continue
            got = self._apply_fixes(got)
            if not self._valid_translation(src, got, dst):
                # 记下被拒的原因。只看得到「0 条成功」是没法修的，
                # 得说清楚是被哪一条规则拦下的。
                why = self._reject_reason(src, got, dst)
                self.last_llm_reject = why
                self._reject_stats[why] = self._reject_stats.get(why, 0) + 1
                out.append(None)
            else:
                out.append(got)
        return out

    def _reject_reason(self, src, out, dst):
        """
        一条译文为什么被判不合格。顺序和 _valid_translation 的检查顺序保持一致，
        这样报出来的原因就是第一个拦下它的那条规则，不会张冠李戴。
        """
        if not out or not out.strip():
            return i18n.T("模型返回空")
        if out.strip().upper() == (src or "").strip().upper():
            return i18n.T("原样返回未翻译")
        if self._is_garbage(out):
            return i18n.T("乱码")
        if not self._is_clean(out, src, dst):
            return i18n.T("中英混杂")
        if dst == "zh" and self._has_leftover_english(out, src):
            return i18n.T("残留英文")
        if not self._numbers_preserved(src, out, dst):
            return i18n.T("数字对不上")
        return i18n.T("其他")

    def reject_summary(self):
        """把拒稿原因拼成一句人话：残留英文 9 条 · 数字对不上 1 条"""
        if not self._reject_stats:
            return self.last_llm_reject or ""
        items = sorted(self._reject_stats.items(), key=lambda kv: -kv[1])
        sep = " · "
        if i18n.is_en():
            return sep.join(f"{k} ×{v}" for k, v in items[:3])
        return sep.join(f"{k} {v} 条" for k, v in items[:3])

    def _llm_chunk_robust(self, batch):
        """
        翻一批，失败条目单独重发 —— 不重翻已经成功的。

        旧做法是「缺项就把整批对半劈开、两半全部重发」，实测有两个毛病：
          1. 30 条里有 3 条漏，会把另外 27 条也重翻一遍。按打桩测算，
             一轮要发出 90 条次才换来 30 条结果，是真该 30 条次的 3 倍；
          2. 劈到 3 条一批就停（`len(batch) <= 3`），这 3 条要是还漏就放弃。
             所以「把重试深度提上去」根本没用 —— 闸门在长度上，不在深度上。

        现在改成只围着失败项转，两轮：
          第一轮：失败项凑成一小批发一次（条数少，模型不容易再漏）
          第二轮：还失败的逐条单发（单条不存在「分不清对应哪条」的问题）

        为什么要单条收尾：第 7 页那三条中文说明在 64 条并发时反复漏译，
        单独发却能正确译出 —— 说明不是内容有问题，是被批内上下文带坏的。

        整体失败（接口不通、返回格式完全不可解析）时不重试：
        这时候重发整批只是白烧一遍 token，交给上层兜底更划算。
        """
        out = self._llm_one_chunk(batch)
        bad = [i for i, r in enumerate(out) if r is None]
        if not bad:
            return out

        # 失败过半就不当「个别漏项」处理了。留 3 条的下限，
        # 避免小批（4 条挂 3 条）被误判成整体失败。
        #
        # 这里不再往 last_llm_error 里写东西。原来写的是「本批多数条目未返回，
        # 按接口异常处理」，界面照抄成「AI 接口调用失败」，实际上接口好得很：
        # 模型答了，是我们的质检把答案拒了（原因已记在 _reject_stats 里）。
        # 让它留着接口错误的名额，真正断网、余额不足时才报。
        if len(bad) > max(3, int(len(batch) * 0.6)):
            return out

        for rnd in range(_LLM_RETRY_ROUNDS):
            todo = [batch[i] for i in bad]
            if rnd == 0 and len(todo) > 1:
                got = self._llm_one_chunk(todo)
            else:
                got = []
                for one in todo:
                    got.extend(self._llm_one_chunk([one]))
            for i, val in zip(bad, got):
                if val is not None:
                    out[i] = val
            bad = [i for i, r in enumerate(out) if r is None]
            if not bad:
                break
        return out

    def _llm_translate_batch(self, items, max_workers=3):
        """
        批量走 AI。items: [(text, lang)]
        按 batch_size 切片并发，单批内保持原有顺序（同一批的上下文更连贯）。
        返回与 items 等长的列表，未成功的位置是 None。
        """
        if not items or not self.llm.available():
            return [None] * len(items)

        size = max(5, min(self.llm.batch_size, 60))
        chunks = [(i, items[i:i + size]) for i in range(0, len(items), size)]
        results = [None] * len(items)

        def work(chunk):
            start, batch = chunk
            return start, self._llm_chunk_robust(batch)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for start, out in ex.map(work, chunks):
                for k, val in enumerate(out):
                    results[start + k] = val
        return results

    # ---------------- 译文校验 ----------------
    def _valid_translation(self, src, out, dst):
        """
        一条译文能不能用，四个条件全过才行：
          1. 非空、内容和原文不同
          2. 语言方向对（目标是中文就得有汉字，目标是别的语言就不许留汉字）
          3. 没有中英混杂残留
          4. 数字保真（原文的数字必须都在译文里）
        """
        if not out or not out.strip():
            return False
        if out.strip().upper() == (src or "").strip().upper():
            return False
        if self._is_garbage(out):
            return False
        if not self._is_clean(out, src, dst):
            return False
        if dst == "zh" and self._has_leftover_english(out, src):
            return False
        if not self._numbers_preserved(src, out, dst):
            return False
        return True

    @staticmethod
    def _num_close_val(want, out):
        """
        数值近似比对：want 这个数在 out 里有没有「数值上够接近」的数。

        容差取 max(0.05, |v|×0.1%)：放过小数位差异（104.78 写成 104.8），
        抓得住实打实算错的（4-1/8" 写成 100mm 差 4.78，照样被拦）。
        """
        tol = max(0.05, abs(want) * 0.001)
        for s in NUM_RE.findall(out or ""):
            try:
                got = float(s)
            except ValueError:
                continue
            if abs(got - want) <= tol:
                return True
        return False

    @staticmethod
    def _same_unit_equiv(src):
        """
        把原文里「带公制单位的数值」折成毫米基准，返回 {原写法: {单位: 该单位下的等值}}。

        例：'2根5毫米厚镀锌铁板2.79米长'
            → {'5': {'mm': 5.0, 'cm': 0.5, ...},
               '2.79': {'mm': 2790.0, 'cm': 279.0, 'm': 2.79, ...}}

        只记「单位 → 值」的对应，不记一堆裸数字：只记裸数字的话，
        模型把 2.79 米错写成 279 mm 也会命中（279 恰好是 279 厘米的值），
        等于把算错数放过了。带单位比对才分得清「换成毫米写」和「算错了」。
        """
        table = {}
        for m in _NUM_UNIT_RE.finditer(src or ""):
            num, raw_unit = m.group(1), m.group(2)
            canon = _UNIT_CANON.get(raw_unit, raw_unit.lower())
            factor = _METRIC_TO_MM.get(raw_unit) or _METRIC_TO_MM.get(canon)
            if not factor:
                continue
            try:
                mm = float(num) * factor
            except ValueError:
                continue
            slot = table.setdefault(num, {})
            # 同一物理量在每个公制单位下的写法，各自记清楚是谁
            for unit, f in (("km", 1_000_000.0), ("dm", 100.0),
                            ("cm", 10.0), ("mm", 1.0), ("m", 1000.0)):
                slot[unit] = mm / f
        return table

    @staticmethod
    def _unit_equiv_hit(expect, out):
        """
        译文里有没有「数值 + 单位」跟期望的某一条对得上。

        只认译文自己写明的单位：'2790.00 mm' 命中 2.79 米，'279 mm' 不命中。
        """
        if not expect:
            return False
        for m in _NUM_UNIT_OUT_RE.finditer(out or ""):
            try:
                got = float(m.group(1))
            except ValueError:
                continue
            want = expect.get(m.group(2).lower())
            if want is None:
                continue
            if abs(got - want) <= max(0.05, abs(want) * 0.001):
                return True
        return False

    @staticmethod
    def _num_close(token, out):
        """
        数值近似比对：token 这个数在 out 里有没有「数值上够接近」的数。

        为什么不能只做字符串包含：换算后我们期望 104.78，模型偶尔按一位小数
        写成 104.8，字符串对不上就整条判不合格、图纸退回英文，白白丢一条。
        """
        try:
            want = float(token)
        except ValueError:
            return False
        return Translator._num_close_val(want, out)

    def _numbers_preserved(self, src, out, dst="zh"):
        """
        原文里的数字必须一个不少地出现在译文里。
        图纸上数字就是规格，丢了 4-1/8" 里的 1/8 就是另一根管子。

        单位换算是唯一的例外：4-1/8" 变成 104.78mm 后数字必然全变了，
        直接拿原文比一定判不合格，模型再努力换也会被丢掉、图纸退回原文。
        所以先把原文按**当前的输出单位**换算一遍再比 —— 既放过正常换算，
        又能抓住「算错数」（模型写成 100mm 的话，比 104.78 就对不上）。

        为什么不再判断方向：改造前只在英译中时这么做，中译英时因为
        「8 ft 译成 8 ft fence 是正常写法」而跳过。现在单位规则由用户选，
        中译英也可能要求换成英制（或反过来），按方向判断会漏掉一半情况。
        两头都按 mode 换算，比的是「同一条标准」。

        同制内的换写法也放行：原文「2.79米」，译文「2790 mm」是对的，
        但逐字比对找不到 2.79。少了这条例外，中→英图纸里所有以「米」
        标注的长边（2.79米、1.103米……）都会被判算错数、原样退回中文。
        """
        if src:
            if self.unit_mode == UNIT_METRIC and has_imperial(src):
                src = convert_to_metric(src)
            elif self.unit_mode == UNIT_IMPERIAL and has_metric(src):
                src = convert_to_imperial(src)
        nums = NUM_RE.findall(src or "")
        if not nums:
            return True
        # 原文里带公制单位的数值 → 它在各公制单位下的等值。只在这条判定里用。
        equiv = self._same_unit_equiv(src)
        for n in nums:
            if n in out:
                continue
            # 允许 4-1/8 被写成 4-1/8 或 4 1/8，宽松匹配数字主体
            if n.replace("-", " ") in out.replace("-", " "):
                continue
            # 纯数值的近似比对（应对小数位写法差异）
            if self._num_close(n, out):
                continue
            # 同制换写法：2.79米 写成 2790mm 是正确表达，不是算错数
            if self._unit_equiv_hit(equiv.get(n), out):
                continue
            return False
        return True

    def _is_clean(self, out, src, dst):
        """
        判断译文是否「翻干净了」。
        踩过的坑：逐词替换时一部分命中一部分没命中，会拼出
        "本DRAWING所有未标注DIMENSION" 或 "match the围栏or in green" 这种垃圾，
        命中率看着不低，但绝对不能交付。

        判定按**目标语言**走，不再按「源语言是中文就要求没有汉字」：
        目标是日语时正文本来就用汉字，按汉字判会把整份图纸全判成不合格。
        """
        if not out:
            return False
        has_cn = bool(CJK_RE.search(out))

        if dst == "zh":
            if not has_cn:
                # 译文一个汉字都没有：原文本身没有字母（纯符号、纯数字）时允许，
                # 否则就是没翻
                return not re.search(r"[A-Za-z]", out)
            en_words = len(re.findall(r"[A-Za-z]{2,}", out))
            cn_chars = len(CJK_RE.findall(out))
            return en_words <= cn_chars

        if dst == "ja":
            # 日文含汉字，不能按汉字判残留；只拦串进来的韩文谚文
            return not HANGUL_RE.search(out)

        # 目标是英文或其他拉丁/谚文字母语言：译文里还剩汉字就是没翻干净
        return not has_cn

    def _has_leftover_english(self, out, src=""):
        """
        中文译文里残留的英文单词 = 没翻干净，必须判不合格。

        踩过的坑：只用 (?<![A-Za-z])[a-z]{3,} 找小写词，
        "Padlock孔" 这种首字母大写的漏网了——它被当成专有名词放行，
        结果图纸上印出「Padlock孔」这种半成品。

        规则：句子里有汉字时，出现任何非豁免英文词就算残留。豁免四类：
          - 带数字的型号（M8、DN50、4-1/8）
          - 纯大写缩写（PVC、HDPE、ISO）
          - 紧跟数字的单位（9 ft、50 mm、3 lb）
          - 原文里本来就大写的专有名词/品牌（Dynamic），且译文主体确实是中文
            （用「汉字数 ≥ 5 倍英文词数」把关，防止「Padlock孔」这种蒙混过关）
        """
        if not out or not CJK_RE.search(out):
            return False
        tokens = [t for t in LATIN_TOKEN_RE.findall(out)
                  if not re.search(r"\d", t) and not re.fullmatch(r"[A-Z]{1,8}", t)
                  and len(t) > 1]
        if not tokens:
            return False

        # 先认出「多词专有名词」：人名、公司名。它们在原文里就是连着几个
        # 首字母大写的词，译文保留原名是对的，不该算残留。
        #
        # 为什么必须单独处理：下面那条「汉字数 ≥ 5 倍专名数」的比例闸门，
        # 是按「一个词」算的。而 Jose Galvez 是两个词、Dynamic Event Co. 是三个，
        # 标题栏那句译成中文后汉字只有十几个，被压到比例线下，
        # 整条判成「没翻干净」退回英文 —— 页脚和设计者那一行 7 页全灭，
        # 统计里 AI 成功数归零，界面就报成了「AI 接口调用失败」。
        # 多词专名整体只算一个单位，比例才对得上人话。
        name_runs = []
        for run in re.findall(r"[A-Z][A-Za-z.&'\-]*(?:[ ]+[A-Z][A-Za-z.&'\-]*)+", out):
            if src and run.strip(" .") in src:
                name_runs.append(run)
        in_run = set()
        for run in name_runs:
            in_run.update(run.split())

        cn_chars = len(CJK_RE.findall(out))
        kept = 0            # 只数「单个」专有名词，多词专名不参与比例
        counted = set()
        for tok in tokens:
            if tok.lower() in UNIT_WORDS and \
                    re.search(r"\d\s*" + re.escape(tok) + r"(?![A-Za-z])", out, re.I):
                continue
            if tok in in_run:
                # 多词专名整体放行：一个名字算一个单位，不按词数摊进比例里
                run = next(r for r in name_runs if tok in r.split())
                counted.add(run)
                continue
            if src and tok[:1].isupper() and re.search(
                    r"(?<![A-Za-z])" + re.escape(tok) + r"(?![A-Za-z])", src):
                kept += 1          # 原文里就是这么写的单个专有名词/品牌
                continue
            return True
        # 只剩单个专有名词时，才看比例：汉字够多说明整句确实翻成了中文。
        # 「由 Jose Galvez 设计」这种没有单词残留的情况 kept=0，直接放行。
        return kept > 0 and cn_chars < 5 * kept

    def _dict_worthy(self, text, src, dst):
        """
        判断这段文字值不值得交给词典。
        词典是给「短标注」用的（法兰、材质、锁紧环），
        长句交给它只会被拆成半中半英，不如整句翻。

        含英制单位的标注也直接跳过词典：词典是逐词替换，
        它认得 upright 却不认得 4-1/8"，交过去会得到「4-1/8" 立杆」
        这种只翻了一半的东西。这类要整句交给 AI，让它连数值一起换算。

        非中英方向直接不交：词典只有中↔英两张表，
        拿它去拼西班牙语只会拼出一堆英文单词。
        """
        if (src, dst) not in (("zh", "en"), ("en", "zh")):
            return False
        if self.unit_mode == UNIT_METRIC and has_imperial(text):
            return False
        if self.unit_mode == UNIT_IMPERIAL and has_metric(text):
            return False
        if dst == "en":
            return len(re.findall(r"[A-Za-z]+", text)) <= 6
        return len(text) <= 20

    def _dict_ok(self, src, out, dst, miss_ratio):
        """词典结果够不够格直接用"""
        if not out:
            return False
        if not self._is_clean(out, src, dst):
            return False
        if dst == "zh" and self._has_leftover_english(out, src):
            return False
        if miss_ratio > (1 - DICT_ACCEPT_RATIO):
            return False
        return self._numbers_preserved(src, out, dst)

    # ---------------- 批量并发翻译（主入口） ----------------
    def translate_many(self, items, max_workers=12, progress=None):
        """
        批量翻译，返回与 items 等长的译文列表。items: [(text, lang), ...]

        lang 是「这条文字本身是什么语言」（pdf_engine 用 detect_lang 认的），
        真正译成什么语言由界面上的源/目标下拉决定，见 _resolve。

        在真正的翻译流程外面包一层单位换算：AI 通道靠提示词自己换算，
        词典通道和免费在线通道不会（它们是逐词替换），所以在统一出口兜一道，
        保证交到图纸上的数值都是用户选定的单位。换算只认写明了单位的写法，
        译文里本来就是目标单位的不会再动第二次。

        末尾再过一次小数位规范化（仅公制；英寸是分数写法，没有小数位可抹）。

        最后一道是校验：拿原文独立复算一遍，与译文里的数值配对比对。
        算错数的直接改成复算值，改不了的记进 metric_report 交给人看。
        """
        results = self._translate_inner(items, max_workers, progress)
        converted = 0
        for i, (raw, hint) in enumerate(items):
            out = results[i]
            if not out:
                continue
            new = out
            # 注意：这里**不能**因为「这条不用翻译」就跳过换算。
            # 「英文图纸输出英文、只把英寸换成毫米」是完全合理的用法
            # （客户要一份公制版的英文图）。翻译是翻译，换单位是换单位，
            # 两件事绑在一起，用户就做不到只换单位不换语言。
            if self.unit_mode == UNIT_IMPERIAL:
                if has_metric(new):
                    new2 = convert_to_imperial(new)
                    if new2 != new:
                        converted += 1
                        new = new2
            elif self.unit_mode == UNIT_METRIC:
                if has_imperial(new):
                    new2 = convert_to_metric(new)
                    if new2 != new:
                        converted += 1
                        new = new2
                new = normalize_metric_format(new)
            # 换算完再独立复算一遍。这一步是给「模型自己算的数」上的保险：
            # 前面的 convert 只处理文本里还留着原单位的，
            # AI 已经把 4-1/8" 写成 100mm 的情况它根本看不见，
            # 只有拿原文重算一遍比对才能抓出来。
            _src, dst = self._resolve(hint)
            new, _issues = verify_metric_output(
                raw, new, dst=dst or "",
                page=getattr(self, "page_no", None),
                report=getattr(self, "metric_report", None),
                mode=self.unit_mode)
            if new != out:
                results[i] = new
        # 单独记一个计数，不塞进 self.stats —— 那个字典的键是固定五个，
        # 界面上要按键取数，多一个键会让统计行读不到数
        self.metric_converted = getattr(self, "metric_converted", 0) + converted
        return results

    def reset_metric_report(self):
        """
        每份 PDF 开始前清空校验账本。

        不清的话多份文件连跑时，第二份的清单里会混进第一份的问题，
        用户拿着清单去改图会改错文件。
        """
        self.metric_report = MetricReport()
        self.page_no = None
        # AI 的失败/拒稿记录也一起清。不清的话，第一份文件断过网，
        # 第二份文件跑得好好的，结尾照样挂着一条「接口调用失败」吓人。
        self.last_llm_error = ""
        self.last_llm_reject = ""
        self._reject_stats = {}

    def _cache_get(self, key, src, dst, text):
        """
        查缓存。新键是「源>目标::原文」，老版本用的是「源::原文」
        （那时目标语言由源语言反推）。

        为什么要兼容老键：缓存里是用户已经花过钱的译文。升级一次就全部作废、
        重新翻一遍，等于让老用户为一次界面改版付账单。
        只要方向对得上（老键 lang=zh 就是中译英），旧译文和新的完全等价。
        """
        if key in self.cache:
            return self.cache[key]
        legacy = "zh" if dst == "en" else ("en" if dst == "zh" else None)
        if legacy and src == legacy:
            return self.cache.get(f"{legacy}::{text}")
        return None

    def _translate_inner(self, items, max_workers=12, progress=None):
        """
        批量翻译，返回与 items 等长的译文列表。items: [(text, hint), ...]

        处理顺序：缓存 → 词典 → AI → 免费在线。
        2700 行图纸实测：配了 AI 接口时 180 条标注约 20 秒翻完。
        """
        results = [None] * len(items)
        pending = []

        # ---- 第一轮：缓存与词典，纯本地，零成本 ----
        for i, (raw, hint) in enumerate(items):
            text = (raw or "").strip()
            src, dst = self._resolve(hint)
            if not text or not dst:
                # 不翻的条目原样返回。注意这里不能记进 failed ——
                # 用户选了「英→中」，中文标注本来就不该动，算失败会虚报漏译
                results[i] = text
                continue
            key = f"{src}>{dst}::{text}"
            hit = self._cache_get(key, src, dst, text)
            if hit is not None:
                self.stats["cache"] += 1
                results[i] = hit
                continue
            dict_out = ""
            if self._dict_worthy(text, src, dst):
                dict_out, miss_ratio = self._dict_translate(text, src, dst)
                if self._dict_ok(text, dict_out, dst, miss_ratio):
                    self.stats["dict"] += 1
                    self.cache[key] = dict_out
                    results[i] = dict_out
                    continue
            pending.append((i, text, src, dst, dict_out))

        if not pending:
            return results

        if progress:
            progress(i18n.fmt("{a} 条本地解决，{b} 条需联网/AI",
                              a=len(items) - len(pending), b=len(pending)))

        # ---- 第二轮：AI 接口（配了就用，质量最好，优先于免费接口） ----
        ai_alive = False        # 本轮 AI 是否至少成功译出一条（用于判断接口是否真的通）
        if self.llm.available():
            llm_before = self.stats["llm"]
            batch = [(p[1], p[2], p[3]) for p in pending]
            llm_out = self._llm_translate_batch(batch)
            rest = []
            for (idx, text, src, dst, dict_out), out in zip(pending, llm_out):
                if out:
                    self.stats["llm"] += 1
                    self.cache[f"{src}>{dst}::{text}"] = out
                    results[idx] = out
                else:
                    rest.append((idx, text, src, dst, dict_out))
            ai_alive = self.stats["llm"] > llm_before
            self.llm.usage.note_items(len(pending) - len(rest))
            pending = rest
            if not pending:
                return results

        # ---- 第三轮：免费在线接口兜底 ----
        # 只有当 AI 本轮一条都没译出来（没配、余额不足、网络不通）时才启用。
        # 如果 AI 已经正常工作，剩下这几条多半是模型也不处理的怪格式（长代号、
        # 特殊符号），再丢给免费的机器翻译不但救不回来，还会把机翻质量混进图纸——
        # 反而是倒退。这种情况直接按「保留原文」处理，图纸至少是干净可读的。
        if ai_alive:
            for idx, text, src, dst, dict_out in pending:
                self._finalize_fallback(results, idx, text, src, dst, dict_out)
            return results

        if not self.cfg.get("online_fallback", True):
            for idx, text, src, dst, dict_out in pending:
                self._finalize_fallback(results, idx, text, src, dst, dict_out)
            return results

        def work(item):
            idx, text, src, dst, dict_out = item
            return (idx, text, src, dst, dict_out,
                    self._online_translate(text, src, dst, throttle=False))

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for idx, text, src, dst, dict_out, out in ex.map(work, pending):
                if out and self._valid_translation(text, out, dst):
                    self.stats["online"] += 1
                    self.cache[f"{src}>{dst}::{text}"] = out
                    results[idx] = out
                else:
                    self._finalize_fallback(results, idx, text, src, dst, dict_out)
        return results

    def _finalize_fallback(self, results, idx, text, src, dst, dict_out):
        """
        所有通道都失败时的收尾。
        关键取舍：只翻了一半、中英混杂，比干脆不翻更糟：
        前者让整张图纸不可读，后者至少还是原始图纸。
        """
        if dict_out and self._dict_ok(text, dict_out, dst, 0.0):
            self.stats["dict"] += 1
            self.cache[f"{src}>{dst}::{text}"] = dict_out
            results[idx] = dict_out
            return
        self.stats["failed"] += 1
        results[idx] = text          # 保留原文

    # ---------------- 单条翻译（兼容旧调用） ----------------
    def translate(self, text, lang):
        """
        翻译单条文字。lang 是这条文字自己的语言（'zh' / 'en'），
        译成什么语言看界面上的目标语言设置。
        """
        out = self.translate_many([(text, lang)])
        return out[0] if out else text

    def __call__(self, text, lang="en"):
        """
        让 Translator 实例可以直接当函数用：translator(text, lang)。

        pdf_engine.process_pdf 的 translate_fn 参数就是按「可调用对象」用的。
        之前没有这个方法，传进去的实例一调用就抛 TypeError，而那边是
        `try: ... except Exception: new_texts.append(text)` —— 异常被吞掉，
        表现成「整份图纸一条都没翻译，全记进未译清单」。
        导出流程实测踩到过：161 条目标，0 条译出。
        """
        return self.translate(text, lang)
