# -*- coding: utf-8 -*-
"""设置卡横向溢出检查：不同 DPI 缩放与界面语言下，控件不许被挤出卡片。

为什么需要它：tools_test_layout.py 只查七块区域有没有纵向越界、高度为 0，
管不到「同一行里的控件被挤出卡片右边界」。设置卡右边是「选择目录」按钮，
被挤掉的表现就是这个按钮消失 —— 用户再也选不了输出目录，界面上看不出少了东西。

做法上必须借 tools_test_layout.rebuild：直接改窗口 geometry 不会让 theme.scale
跟着变，控件按旧 DPI 量出来的宽度全是错的（第一版就是这么测的，结论是假的）。
另外不能在循环里反复 A.App()：第二次建窗口会在 Tk 层崩掉且不报错，只留一个
退出码 1。一个 App 实例 + rebuild 换尺寸就够。

跑法：
    python _ui_check_doc.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PDF_TRANSLATOR_NO_POPUP", "1")

import tkinter as tk                                       # noqa: E402

import app as A                                            # noqa: E402
import tools_test_layout as L                              # noqa: E402

# 真实场景的三档，与 tools_test_layout 同源：100% 的 1366x768 最窄，
# 125% 的 1366x768 是笔记本常态，150% 的 1080p 最宽。
SCENES = [
    ("1366x768 @100%", 1.00, 880, 678),
    ("1366x768 @125%", 1.25, 1100, 656),
    ("1920x1080 @150%", 1.50, 1320, 945),
]

# 至少留这么多物理像素，否则「刚好不越界」在别的字体版本上就会翻车
MIN_MARGIN = 8


def measure(app):
    card_w = app.card_opt.winfo_width()
    rows = []
    for idx, f in enumerate(app.card_opt.body.winfo_children()):
        if not isinstance(f, tk.Frame):
            continue
        for c in f.winfo_children():
            wd = c.winfo_width()
            if wd <= 1:
                continue
            rows.append((idx, c.__class__.__name__, c.winfo_x() + wd))
    return card_w, rows


def main():
    log = []

    def out(s=""):
        log.append(s)

    A.enable_hidpi()
    app = A.App()
    app.win.update()
    bad = []

    for lang in ("zh", "en"):
        import core.i18n as I
        I.set_lang(lang)
        for tag, scale, w, h in SCENES:
            L.rebuild(app, scale, w, h)
            card_w, rows = measure(app)
            right_max = max(r[2] for r in rows)
            margin = card_w - right_max
            label = "%-16s %s" % (tag, lang)
            ok = margin >= MIN_MARGIN
            out("%s 卡片 %4d 最右 %4d 余量 %3d %s"
                % ("[通过]" if ok else "[越界]", card_w, right_max, margin, label))
            if not ok:
                bad.append(label)
        I.set_lang("zh")

    out("")
    out("结论：%s" % ("全部通过" if not bad else "有 %d 处越界" % len(bad)))
    text = "\n".join(log)
    try:
        print(text)
    except Exception:
        pass
    os.makedirs("_ui", exist_ok=True)
    with open(os.path.join("_ui", "check.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    app.win.destroy()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
