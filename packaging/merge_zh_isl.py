# -*- coding: utf-8 -*-
"""
合并 Inno Setup 中文语言包。

背景：原来那份 ChineseSimplified.isl 只写了向导上「看得见」的几十句，
其余两百多条标准消息没写，编译器会逐条告警「回落到英文 Default.isl」。
结果是中文安装包在「选择开始菜单文件夹」「准备安装摘要」「安装进度状态」
这些页面上突然冒出英文，看着像程序出错。

做法：以 Inno Setup 6.5 官方简体中文翻译为底（完整覆盖 [Messages]），
再用我们自己写的产品化文案覆盖同名条目 —— 欢迎语、完成页、退出提示
这些是照这个工具的实际用法写的，比通用译文更贴切，不能丢。
"""
import re
from collections import OrderedDict

# 输入 1：Inno Setup 6.5 官方简体中文翻译，提供完整的 [Messages] 全集
OFFICIAL = "official_zh_6.5.isl"
# 输入 2：本项目自己写的产品化文案（只覆盖需要的几十条）
# 刻意和产出文件分开命名 —— 曾经让 OURS 也指向产出文件，
# 重跑一次就把官方译文覆盖掉自己，产出越跑越"通用"。
OURS = "zh_overrides.isl"
# 产出：安装脚本引用这一份
OUT = "ChineseSimplified.isl"

# 我们把向导文案写得更具体的那些条目，优先于官方译文。
# 只覆盖同名 key，不新增 —— 新增未定义的 key 编译会报错。
OVERRIDE_KEYS = {
    "WelcomeLabel2",
    "FinishedLabel", "FinishedLabelNoIcons",
    "ReadyLabel1", "ReadyLabel2a", "ReadyLabel2b",
    "InstallingLabel", "SelectDirDesc", "SelectDirLabel3",
    "SelectDirBrowseLabel", "SelectTasksDesc", "SelectTasksLabel2",
    "PreparingDesc", "ExitSetupMessage",
}


def parse_sections(path):
    """把 .isl 拆成 {'[段名]': [(key, value), ...]}，保留顺序，丢弃注释。"""
    sections = OrderedDict()
    cur = None
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.rstrip("\n").rstrip("\r")
            s = line.strip()
            if not s or s.startswith(";"):
                continue
            if s.startswith("[") and s.endswith("]"):
                cur = s
                sections.setdefault(cur, [])
                continue
            if cur is None or "=" not in line:
                continue
            k, v = line.split("=", 1)
            sections[cur].append((k.strip(), v.strip()))
    return sections


official = parse_sections(OFFICIAL)
ours = parse_sections(OURS)

off_msgs = OrderedDict(official["[Messages]"])
off_cust = OrderedDict(official.get("[CustomMessages]", []))
our_msgs = OrderedDict(ours.get("[Messages]", []))
our_cust = OrderedDict(ours.get("[CustomMessages]", []))

if len(our_msgs) > 100:
    raise SystemExit(
        f"覆盖源 {OURS} 里有 {len(our_msgs)} 条 [Messages]，明显是整段官方译文，"
        "不是本项目文案。检查是否把产出文件当成了输入。")

# 只接受官方表里存在的 key，否则编译会报「未定义的消息名」
applied, ignored = 0, []
for k, v in our_msgs.items():
    if k in off_msgs:
        off_msgs[k] = v
        applied += 1
    else:
        ignored.append(k)

for k, v in our_cust.items():
    if k in off_cust:
        off_cust[k] = v
    elif k not in off_msgs:
        # 我们独有的自定义消息（安装脚本里 [CustomMessages] 用到的）直接并入
        off_cust[k] = v

lines = []
lines.append("; *** Inno Setup 6.5 简体中文语言包（官方译文 + 本项目文案） ***")
lines.append(";")
lines.append("; [Messages] 取官方 jrsoftware/issrc 的 6.5 简体中文翻译，整段完整，")
lines.append("; 这样「选择开始菜单」「准备安装摘要」「安装进度」「错误提示」等页面")
lines.append("; 不会中途冒出英文。中文安装包出现英文，用户会以为程序坏了。")
lines.append("; 其中若干条（欢迎语、完成页、退出提示等）换成本项目自己写的文案，")
lines.append("; 因为那些话要贴合这个工具的用法，通用译文说不清。")
lines.append(";")
lines.append("; 文件必须以 UTF-8 带 BOM 保存，否则 Inno 会按系统代码页解 UTF-8 字节，")
lines.append("; 中文会变成乱码。")
lines.append("")
lines.append("[LangOptions]")
lines.append("LanguageName=简体中文")
lines.append("LanguageID=$0804")
lines.append("LanguageCodePage=936")
lines.append("; 中文字形在默认的 Segoe UI 下会发虚，显式指定雅黑")
lines.append("DialogFontName=Microsoft YaHei")
lines.append("DialogFontSize=9")
lines.append("WelcomeFontName=Microsoft YaHei")
lines.append("WelcomeFontSize=12")
# TitleFontName / TitleFontSize 是 Inno 5 的指令，6.x 已废弃并会编译告警，不再写
lines.append("")
lines.append("[Messages]")
for k, v in off_msgs.items():
    lines.append(f"{k}={v}")
lines.append("")
lines.append("[CustomMessages]")
lines.append("; 安装脚本里 [CustomMessages] 与 [Messages] 用的是同一个名字空间：")
lines.append("; NameAndVersion 这类条目属于官方 Default.isl 的自定义段，")
lines.append("; AddonHostProgramNotFound、AutoStartProgram 也在这一段，")
lines.append("; 写成 [Messages] 编译不报错，但运行时仍弹英文。")
for k, v in off_cust.items():
    lines.append(f"{k}={v}")

with open(OUT, "w", encoding="utf-8-sig", newline="\r\n") as f:
    f.write("\n".join(lines) + "\n")

print(f"官方 [Messages] {len(off_msgs)} 条，[CustomMessages] {len(off_cust)} 条")
print(f"覆盖为产品化文案 {applied} 条")
if ignored:
    print("以下 key 官方没有、已跳过（避免编译报错）：", ", ".join(ignored))
print(f"输出 {OUT}：{len(lines)} 行")
