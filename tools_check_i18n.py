# -*- coding: utf-8 -*-
"""
英文词表查漏。

做法：把代码里所有 T("...") / fmt("...") 的第一个参数抽出来（ast 解析，
相邻字符串字面量已经被 Python 合并，不会漏跨行拼接的那种），
和 core/i18n_en.py 的 EN 相比，报出「代码里有、词表里没有」的 key。

用法：
    python tools_check_i18n.py          # 只报缺
    python tools_check_i18n.py --unused # 顺带报词表里没人用的条目

为什么要这么个脚本：界面文案是逐个字符串包 T() 改过来的，
200 多条里漏一两条几乎是必然的，而漏翻的表现是「英文界面里蹦出一句中文」——
不跑一遍根本发现不了，等用户截图过来就晚了。
"""

import ast
import glob
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def collect_keys():
    """扫描所有源码，返回 {中文原文: [出现位置, ...]}"""
    keys = {}
    files = [os.path.join(APP_DIR, "app.py")] + \
        sorted(glob.glob(os.path.join(APP_DIR, "core", "*.py")))
    for path in files:
        name = os.path.basename(path)
        if name in ("i18n.py", "i18n_en.py"):
            # i18n.py 里的 T() 参数就是词表本身，算进去没有意义
            continue
        try:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except SyntaxError as e:
            print(f"语法错误，无法解析：{path} 第 {e.lineno} 行 {e.msg}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            fname = getattr(fn, "id", None)
            if fname is None and isinstance(fn, ast.Attribute):
                fname = fn.attr
            if fname not in ("T", "fmt", "_tr"):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.setdefault(arg.value, []).append(f"{name}:{node.lineno}")
    return keys


def main():
    from core.i18n_en import EN

    keys = collect_keys()
    missing = sorted(k for k in keys if k not in EN)
    unused = sorted(k for k in EN if k not in keys)

    print(f"代码里的文案：{len(keys)} 条   词表：{len(EN)} 条")
    if missing:
        print(f"\n英文词表缺 {len(missing)} 条（英文界面会露中文）：")
        for k in missing:
            where = ", ".join(keys[k][:3])
            print(f"  [{where}]  {k!r}")
    if "--unused" in sys.argv and unused:
        print(f"\n词表里没人用的 {len(unused)} 条（可以删，也可能是预留）：")
        for k in unused:
            print(f"  {k!r}")
    if not missing:
        print("\n没有漏翻的条目。")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
