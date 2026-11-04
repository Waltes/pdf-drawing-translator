# -*- coding: utf-8 -*-
"""
界面语言（国际化）。

为什么用「中文原文当 key」而不是 ui.start_button 这种语义 key：
  1. 改造时只需要把已有字符串包一层 T(...)，中文措辞一个字都不用改，
     没来得及翻的地方界面仍然可读，不会出现 ui.xxx 这种生 string；
  2. 英文表照着中文写，一眼能对上，不会出现 key 与内容脱节；
  3. 中文用户的界面输出与改造前逐字一致，回归测试不用重录基线。

代价是改了中文措辞就得同步改词表。这一点由 tools_check_i18n.py 兜住：
它扫出代码里所有 T("...")，跟词表对差集，漏一个就报出来。
"""

import re
import sys

# 当前界面语言。只允许 "zh" / "en"
_lang = "zh"

# 支持的语言。
# code        内部代号（缓存键、配置里存这个）
# zh / en     界面上的显示名（按当前界面语言取其中一列）
# zh_prompt   给模型看的目标语言名（中文提示词里用）
# en_prompt   同上，英文提示词里用
LANGUAGES = [
    ("zh", "中文", "Chinese", "简体中文", "Simplified Chinese"),
    ("en", "英语", "English", "英语", "English"),
    ("ja", "日语", "Japanese", "日语", "Japanese"),
    ("ko", "韩语", "Korean", "韩语", "Korean"),
    ("de", "德语", "German", "德语", "German"),
    ("fr", "法语", "French", "法语", "French"),
    ("es", "西班牙语", "Spanish", "西班牙语", "Spanish"),
    ("pt", "葡萄牙语", "Portuguese", "葡萄牙语", "Portuguese"),
    ("ru", "俄语", "Russian", "俄语", "Russian"),
    ("it", "意大利语", "Italian", "意大利语", "Italian"),
]

CODES = [x[0] for x in LANGUAGES]

# 在线免费接口用的语言代码。MyMemory 认这些写法；
# 传 "zh" 它不认，必须 "zh-CN"，否则整批返回原文。
ONLINE_CODE = {"zh": "zh-CN", "en": "en", "ja": "ja", "ko": "ko",
               "de": "de", "fr": "fr", "es": "es", "pt": "pt",
               "ru": "ru", "it": "it"}

# 判断「这段文字属于哪种语言」时能真正分辨的只有中英：
# 日韩泰等文字有各自的字符区可以认出来，欧洲语言都是拉丁字母，
# 光看字符串分不出德语和法语 —— 所以源语言下拉选了非中英时，
# 一律不做检测，把所有带文字的条目都当成源语言处理。
_CJK = re.compile(r"[\u4e00-\u9fff]")
_KANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")


def set_lang(code):
    """切换界面语言。传错值就保持原样，不抛异常 —— 配置被人手改坏也得起得来"""
    global _lang
    if code in ("zh", "en"):
        _lang = code
    return _lang


def get_lang():
    return _lang


def is_en():
    return _lang == "en"


def T(s):
    """
    取当前语言的文案。s 永远是中文原文。

    英文词表里查不到就原样返回中文 —— 宁可露一句中文，
    也不能显示一个空按钮或者 KeyError 把窗口搞崩。
    """
    if _lang != "en" or not s:
        return s
    from core import i18n_en
    return i18n_en.EN.get(s, s)


def fmt(s, **kw):
    """带占位符的文案：fmt("共 {n} 页", n=3)。占位符统一用 {}，别用 %s"""
    return T(s).format(**kw)


def lang_label(code, ui_lang=None):
    """语言在界面上的显示名，跟随当前界面语言"""
    ui = ui_lang or _lang
    for row in LANGUAGES:
        if row[0] == code:
            return row[1] if ui == "zh" else row[2]
    return code


def prompt_name(code):
    """给模型看的目标语言名，跟随提示词语言（也是界面语言）"""
    for row in LANGUAGES:
        if row[0] == code:
            return row[3] if _lang == "zh" else row[4]
    return code


def code_of(name_or_code):
    """把配置里可能存的中文名/英文名折回内部代号，兼容手改配置文件"""
    if name_or_code in CODES:
        return name_or_code
    for row in LANGUAGES:
        if name_or_code in row[1:]:
            return row[0]
    return ""


def detect(text):
    """
    认文字属于哪种语言。只用于「源语言=自动」时的分流。

    认不出的（欧洲语言、纯符号）返回 ""，交给上层按「有没有字母」处理。
    """
    t = text or ""
    if _CJK.search(t):
        return "zh"
    if _HANGUL.search(t):
        return "ko"
    if _KANA.search(t):
        return "ja"
    if re.search(r"[A-Za-z]", t):
        # 拉丁字母：分不出具体语种，交给调用方按默认源语言处理
        return "en"
    return ""


# Windows 的 LANGID 主语言 ID → 本程序的语种代号。
# 只收本程序认识的那 10 种，其余（荷兰语、泰语……）认不出来就退英文。
_WIN_PRIMARY = {0x04: "zh", 0x09: "en", 0x11: "ja", 0x12: "ko",
                0x07: "de", 0x0C: "fr", 0x0A: "es", 0x16: "pt",
                0x19: "ru", 0x10: "it"}

_sys_cache = None


def system_code():
    """
    系统界面语言 → 本程序的语种代号，认不出的一律给 "en"。

    为什么要它：目标语言选「自动」时，正确的语义是「翻成我这台机器的语言」。
    以前的实现是「中译英、英译反」，那对一个日本用户就是错的 ——
    他拿到英文图纸，想要的显然是日文版，而不是中文版。
    系统语言是这台机器上唯一可靠的「用户母语」信号。
    """
    global _sys_cache
    if _sys_cache:
        return _sys_cache
    code = ""
    if sys.platform == "win32":
        try:
            from ctypes import windll
            # 低 10 位是主语言 ID，后面的次语言 ID（简繁、地区）不影响语种判断
            code = _WIN_PRIMARY.get(windll.kernel32.GetUserDefaultUILanguage() & 0x3FF, "")
        except Exception:
            code = ""
    if not code:
        try:
            import locale as _locale
            head = ((_locale.getlocale()[0] or "")[:2]).lower()
            code = head if head in CODES else ""
        except Exception:
            code = ""
    _sys_cache = code or "en"
    return _sys_cache


def auto_target():
    """
    目标语言选「自动」时，实际翻成哪种语言。

    就是系统语言；系统语言不在支持列表里（荷兰语、泰语等）退英文 ——
    翻不了总比翻错强，而且英文在图纸语境里是最安全的公共语。
    """
    code = system_code()
    return code if code in CODES else "en"


def src_options():
    """源语言下拉项。第一项是自动识别，value 用 "auto"

    这里刻意不把「自动识别」做成函数参数：带默认值的话，T() 的入参就成了变量，
    tools_check_i18n.py 扫不到它，漏翻也不会有人发现（就踩过一次：
    英文界面下源语言下拉第一项一直显示中文）。
    """
    items = [("auto", T("自动识别"))]
    for row in LANGUAGES:
        items.append((row[0], row[1] if _lang == "zh" else row[2]))
    return items

def dst_options(src="auto"):
    """
    目标语言下拉项。

    「自动」的语义是「翻成这台机器的语言」（见 auto_target）：
    日文系统的用户把英文图纸丢进来，出来的是日文版。
    标签里直接把实际语种写出来 —— 只写「自动」用户没法确认会翻成什么，
    也解释不清为什么他的机器上出来的是日语。

    跟源语言同语种的那一项去掉 ——「英语译成英语」没有意义，
    留在下拉里只会让用户白点一次。
    """
    items = []
    if src == "auto":
        items.append(("auto", fmt("自动（{lang}）",
                                  lang=lang_label(auto_target()))))
    for row in LANGUAGES:
        if src != "auto" and row[0] == src:
            continue
        items.append((row[0], row[1] if _lang == "zh" else row[2]))
    return items


def unit_options():
    """输出单位下拉项。key 与 core.translate 的 UNIT_* 常量对应"""
    return [("metric", T("公制（毫米·千克）")),
            ("imperial", T("英制（英寸·磅）")),
            ("keep", T("保持原样，不换算"))]


def ui_options():
    """界面语言下拉项。语言名永远用自己的语言写，不跟着界面语言变 ——
    界面已经是看不懂的外语时，用户得能认出「中文」这两个字才切得回来。"""
    return [("zh", "中文"), ("en", "English")]
