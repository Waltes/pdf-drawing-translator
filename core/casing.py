# -*- coding: utf-8 -*-
"""
大小写风格：定制版全大写，通用版按一般翻译规则（原样保留模型输出）

为什么需要单独一层
------------------
中文源图纸没有大小写可跟。中文「边框方管」译成英文，是 OUTER SQ. TUBE 还是
Outer sq. tube，源文件给不出答案 —— 早先的做法是「看原文大写字母占比，
占比高就整条转大写」，在中文源上这条永远走到 else 分支，于是同一页里
MATERIAL 是全大写、hot-dip galv. material 是全小写，客户看到的是一份
自己打自己脸的图纸。

两种取向，按发行版分流（见 core/edition.py 的 upper_case）
---------------------------------------------------------
定制版（客户：外贸五金建材，图纸发车间与海外买家）—— 硬规则**全部大写**，
只留两类例外，两类都有硬依据，不是风格偏好：

1. 国际单位制符号
   mm 不是 MM —— SI 里 M 是「兆」，MM 在计量上是错的；同理 kg、Pa、N、m。
   客户把这份图纸发给车间或海外买家，单位写法错了就是规格歧义。

2. 客户指定写法
   对照表里新增的「指定写法」列。客户对自己的产品叫法有最终解释权，
   比如坚持 rack 用小写、DN50 不能变成 dn50。

通用版 —— **不做任何大小写转换**，模型译成什么样就写什么样。理由：
    全大写是机械制图标注的行业惯例，不是翻译规则。通用版面对的是各种图纸
    和各种用途，一页产品说明、一段技术要求被整段大写，读起来全是重音，
    用户会当成排版事故。通用版按一般翻译规则走（句首大写、专有名词大写、
    其余小写），这也是模型默认就会给的形态。

为什么要保护缩写以外的词
------------------------
不是所有词都能无脑 upper：`pcs` 是 piece 的缩写而非单位，图纸惯例小写；
`galv.` 缩写成全大写 GALV. 是对的（图纸本来就大写）；`Ø50` 里的 Ø 是符号，
upper 不动它。

判定顺序：先切出受保护片段（单位、指定写法、已定型的符号串），
其余一律大写，最后拼回去。
"""

import re

from . import edition

# SI 单位符号与制图通用单位，**必须小写**。
# 按长度降序排列，保证 mm 不被 m 先吃掉。
LOWER_UNITS = (
    "µmm", "μm", "µm", "mm", "cm", "dm", "km",
    "kg", "mg", "kpa", "mpa", "pa",
    "kw", "w", "hz", "ml", "min",
    "pcs", "pc", "sets", "set",
    "mm2", "mm3", "m2", "m3",
)

# 只在**紧跟数字**时才保护的单位。
# 这些单个字母同时也是普通字母、型号前缀或冠词，独立出现时不能当单位处理：
#   m —— 也是「米」的符号，但单独一个 M 可能是型号后缀
#   g —— 克，也可能是尺寸代号
#   s / t / n / a / v / l —— 单独出现歧义太大
_NUM_UNITS = ("mm", "cm", "dm", "km", "kg", "mg", "g", "m", "t", "s",
              "min", "h", "pa", "kpa", "mpa", "n", "w", "kw", "j",
              "hz", "v", "a", "l", "ml", "rpm", "pcs", "pc")

# 紧跟数字的单位：3.05m / 20mm / 750pcs
_UNIT_AFTER_NUM = re.compile(
    r"(?<=[0-9])\s*(?:" + "|".join(
        sorted({u for u in _NUM_UNITS}, key=len, reverse=True)) + r")(?![\w])",
    re.IGNORECASE)

# 独立成词的单位：只有这几个不会有歧义
_STANDALONE_UNITS = re.compile(
    r"(?<![\w])(?:mm|cm|km|kg|kpa|mpa|pa|pcs|pc|min)(?![\w])",
    re.IGNORECASE)

# 私有区字符做占位符：upper() 不会改动它们，还原时也不会与正文冲突
_PLACE_BASE = 0xE000
_PLACE_RE = re.compile("[\ue000-\ue0ff]")


def _protect(text, overrides):
    """
    把该保持原样的片段换成占位符，返回 (占位化文本, 还原表)。

    顺序有讲究：指定写法优先于单位表。客户明确指定了写法，就按客户的来，
    不再走单位小写的默认规则。
    """
    table = []
    out = text

    def stash(m, keep):
        idx = len(table)
        table.append(keep)
        return chr(_PLACE_BASE + idx % 256)

    # 1) 客户指定写法（按长度降序，长词先匹配，避免 rack 抢了 racking）
    if overrides:
        for word in sorted(overrides, key=len, reverse=True):
            if not word:
                continue
            pat = re.compile(r"(?<![\w])" + re.escape(word) + r"(?![\w])",
                             re.IGNORECASE)
            out = pat.sub(lambda m, k=overrides[word]: stash(m, k), out)

    # 2) 紧跟数字的单位
    out = _UNIT_AFTER_NUM.sub(lambda m: stash(m, m.group(0).lower()), out)
    # 3) 独立成词的单位
    out = _STANDALONE_UNITS.sub(lambda m: stash(m, m.group(0).lower()), out)
    return out, table


def _restore(text, table):
    """占位符还原成原片段"""
    if not table:
        return text
    return _PLACE_RE.sub(lambda m: table[ord(m.group(0)) - _PLACE_BASE], text)


def normalize_case(text, overrides=None):
    """
    把一条译文规范成图纸写法：全大写 + 单位小写 + 客户指定写法。

    text      译文原文
    overrides {英文词: 指定写法}，来自对照表「指定写法」列

    已全大写的输入会原样返回（快速通道）：图纸上大部分译文本来就是这个形态，
    每次都走一遍正则没有意义，而这份图纸一页五十多条。
    """
    if not text:
        return text
    if not overrides and text == text.upper():
        return text
    protected, table = _protect(text, overrides)
    return _restore(protected.upper(), table)


def apply_style(text, overrides=None):
    """
    按当前发行版套用大小写风格。**译文出口只调这一个函数。**

    定制版：全大写 + 单位小写 + 客户指定写法（normalize_case）。
    通用版：原样返回 —— 一般翻译规则由模型自己保证（句首大写、专有名词大写），
            这里再动一下就变成「替用户决定风格」了。

    为什么做成统一入口而不是在各出口写 if：
        译文有三条通道（AI / 词典 / 在线免费接口）加一条重译旁路，
        每条各有自己的出口。逐个加条件必然漏掉一个 —— 2026-11-19 实测就是
        重译那条旁路漏了大小写和标点。条件集中在这里，出口只需调一次。
    """
    if not text:
        return text
    if not edition.upper_case():
        return text
    return normalize_case(text, overrides)


def case_report(text, overrides=None):
    """
    这条文本被改了哪些地方，给自检和报告用。
    返回 [(原片段, 改后片段), ...]，没改返回空列表。
    """
    got = normalize_case(text, overrides)
    if got == text:
        return []
    return [(text, got)]
