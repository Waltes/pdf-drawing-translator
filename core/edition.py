"""
发行标识：这一份代码打出来的是哪个版本。

为什么要有这个模块：
    通用版和定制版是**同一套代码**，差别只在名称、版本号、数据目录、
    专属术语库、安装包 GUID 这几项。如果靠 fork 一份代码来维护，
    通用版修一次 bug 就要去定制版再改一遍，改漏就是客户那边线上出问题 ——
    这类分叉最后一定会烂掉。所以差异全部收进一份 JSON，代码一行不分叉。

数据流：
    editions/<id>.json          ← 每版的完整配置，改配置改这里
    edition.json                ← 当前生效的那份（打包脚本复制过来）
    core/edition.py             ← 只负责读，不负责判断对错

    打包定制版：python packaging/build_edition.py dynamic
    脚本会把 editions/dynamic.json 复制成 edition.json，打完再还原成通用版。
    直接手改根目录的 edition.json 也能生效，但那不是常规流程 ——
    下次打包会被覆盖，改的东西悄悄没了。

兜底原则：
    这个文件解析失败、字段缺失、值类型不对，一律退回内置的通用版默认值。
    配置坏了导致程序打不开，是最不能接受的一种失败 —— 客户看到的会是
    「双击没反应」，而且没有任何线索指向一份 JSON。
"""

import json
import os
import sys

# 内置兜底值。和 editions/standard.json 保持同源，改一处要改两处 ——
# 这是刻意接受的冗余：程序必须能在读不到任何配置文件时正确启动。
_DEFAULT = {
    "id": "standard",
    "name_zh": "PDF 图纸翻译",
    "name_en": "PDF Drawing Translator",
    "version": "1.1.10",
    "data_dir": "PDFDrawingTranslator",
    "exe_name": "PDF图纸翻译",
    "app_id_guid": "{E07108F2-9CFA-4C91-A748-948BDE9016E8}",
    "output_prefix": "PDFTranslator_Setup",
    "glossary": "",
    "fixes": "",
    "default_dst": "auto",
    "industry": "",
    "industry_en": "",
    "strict_layout": "",
}

_FILENAME = "edition.json"


def _candidate_dirs():
    """
    按优先级列出可能放着 edition.json 的目录。

    打包后：PyInstaller 的 _MEIPASS（onedir 下就是 exe 旁边的 _internal），
            那里有 spec 打进去的那一份。
    源码运行：项目根。两种情形顺序一样，用同一个函数免得两套逻辑。
    """
    dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        dirs.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return dirs


def _read():
    """读配置。任何一步失败都返回空字典，交给 _merged 兜底"""
    for d in _candidate_dirs():
        p = os.path.join(d, _FILENAME)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                raw = json.load(f)
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        return raw
    return {}


def _merged():
    """
    读出来的值和默认值合并。

    逐个字段校验而不是整体信任：JSON 里把 version 写成数字 1.1 是很容易犯的错，
    而版本号一旦不是字符串，后面拼文件名会抛 TypeError，且报错位置离现场很远。
    """
    out = dict(_DEFAULT)
    raw = _read()
    for k in _DEFAULT:
        v = raw.get(k)
        if isinstance(v, str):
            out[k] = v.strip() if k != "industry" else v
        elif isinstance(v, (int, float)) and k == "version":
            out[k] = str(v)
    # id 为空会让后续所有分支都落到「定制版但没名字」的奇怪状态
    if not out["id"]:
        out["id"] = "standard"
    if not out["name_zh"]:
        out["name_zh"] = _DEFAULT["name_zh"]
    if not out["name_en"]:
        out["name_en"] = _DEFAULT["name_en"]
    if not out["version"]:
        out["version"] = _DEFAULT["version"]
    return out


_CFG = _merged()


# ------------------------------------------------------------------ 对外读取

def config():
    """整份配置的副本。调用方改了不影响本模块"""
    return dict(_CFG)


def edition_id():
    return _CFG["id"]


def is_custom():
    """是不是定制版。标准版之外都算定制版"""
    return _CFG["id"] != "standard"


def version():
    """
    本发行版的版本号。

    定制版从 1.0.0 重新起算，和通用版的 1.x 不是同一条线 ——
    两条线的更新节奏不同：通用版改的是通用缺陷，定制版只在
    「这个客户的图纸/词表受影响」时才跟。拿同一串号会让客户
    以为他少装了好几个版本。
    """
    return _CFG["version"]


def version_tuple():
    """给 Windows 文件属性用的四段版本号"""
    parts = []
    for seg in _CFG["version"].split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def name(ui_en=None):
    """
    软件显示名。

    ui_en 为 None 时按当前界面的语言给。定制版的中英名可能不是直译
    （中文带客户名、英文用 Edition 后缀），所以两个名字分开存，
    不做「把中文名翻成英文」这种自动推导。
    """
    if ui_en is None:
        try:
            from . import i18n
            ui_en = i18n.is_en()
        except Exception:
            ui_en = False
    return _CFG["name_en"] if ui_en else _CFG["name_zh"]


def data_dir_name():
    """%APPDATA% 下的目录名。定制版必须和通用版不同，否则两者共用配置和缓存"""
    return _CFG["data_dir"]


def exe_name():
    """exe 主文件名（不含 .exe）。安装脚本和打包脚本都从这里取"""
    return _CFG["exe_name"]


def app_id_guid():
    """安装包 GUID。两版必须不同，相同会导致装定制版顶掉通用版"""
    return _CFG["app_id_guid"]


def output_prefix():
    """安装包文件名前缀"""
    return _CFG["output_prefix"]


def extra_glossary():
    """专属术语库文件名，没有则空串"""
    return _CFG["glossary"]


def extra_fixes():
    """专属纠错表文件名，没有则空串"""
    return _CFG["fixes"]


def industry():
    """
    行业标签，用于提示词里点明领域。空串表示不特别说明。

    中英各一份：这段文字是要拼进提示词的，中文界面给中文提示词、
    英文界面给英文提示词，把中文行业名插进英文提示词里，
    模型多半能懂，但那句话读起来是坏的，而且会拉低后面几条规则的可信度。
    """
    if _CFG["industry_en"] and _is_en():
        return _CFG["industry_en"]
    return _CFG["industry"]


def _is_en():
    try:
        from . import i18n
        return bool(i18n.is_en())
    except Exception:
        return False


def default_dst():
    """
    首次启动时的翻译方向。

    定制版设成 en：这个客户的图纸是中文出的、要发到海外，方向基本固定。
    让他每次打开都去选一遍方向，是把配置工作推给用户。
    用户改过之后以配置文件为准，这里只影响「还没有配置」的那一次。
    """
    return _CFG["default_dst"]


def strict_layout():
    """
    排版是否走「严格原位」模式。开关在 editions/<id>.json 的 strict_layout。

    严格模式（true）：字号按原文墨迹高度锁死、不动；位置锁源基线、不挪；
    宽度不够只允许沿原方向延伸，延伸不下才折行。一句话——原件长什么样，
    译文就长什么样，不做任何"排得更好看"的调整。

    为什么做成开关而不是直接改掉：
        通用版的那些排版逻辑（同类同字号、重心对齐、四轮落点搜索）是给
        「中译英后英文比中文长、塞不进原框」这种场面准备的，在通用场景下
        确实能救回不少本来会缩成一道灰线的标注。客户这次的要求相反——
        他要的是"位置就是位置、字号就是字号"，宁可折行也不许挪。
        两种取向都成立，所以按发行版分开，不去改通用版的行为。
    """
    return _CFG["strict_layout"].strip().lower() in ("1", "true", "yes", "on")
