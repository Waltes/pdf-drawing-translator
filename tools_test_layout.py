# -*- coding: utf-8 -*-
"""
窗口高度回归测试：不同屏幕高度与 DPI 缩放下，主按钮和日志区必须留在窗内。

为什么单独做这个测试：
界面按 840 逻辑像素排，而 GlassWindow 会把窗口高度限制到
`min(px(840), 屏幕高 - px(90))`。1280x768 的机器、或 125% 缩放的笔记本，
窗口实际只剩 650 上下。早期版本卡片全是固定高度、pack 从上往下分，
空间不够时最后打包的「开始转换」和日志区直接拿到 0 高度 —— 客户当时
看到的就是按钮消失、无法启动转换。这个测试就是卡住这条回归线。

跑法：
    python tools_test_layout.py
    python tools_test_layout.py --verbose

需要图形环境（Tk 要能建窗口）。无显示器时会被跳过。
退出码 0 表示全部通过，1 表示有元素越界或高度为 0。
"""

import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 自检时不要弹窗，静默模式由 app.py 里读取
os.environ.setdefault("PDF_TRANSLATOR_NO_POPUP", "1")

import app as A                                    # noqa: E402
from ui_kit import GLASS_TINT                      # noqa: E402

# 被测的七块区域，从顶到底。顺序无关，全都要在窗内且高度大于 0
TARGETS = (
    ("状态条", "card_strip"),
    ("拖拽区", "card_drop"),
    ("文件列表", "card_list"),
    ("设置卡", "card_opt"),
    ("开始转换", "btn"),
    ("进度条", "pbar"),
    ("日志区", "card_log"),
)


def rebuild(app, scale, w_phys, h_phys):
    """
    按指定的缩放和窗口尺寸重建界面。

    直接改 theme.scale 不会让已建好的控件跟着变（px() 只在构造时求值），
    所以必须销毁整棵控件树重来一次，这跟设置里改字号走的是同一条路径。
    """
    app.theme.scale = scale
    for ch in list(app.win.winfo_children()):
        # 设置窗口是 Toplevel，不能跟着销毁
        if isinstance(ch, tk.Toplevel):
            continue
        ch.destroy()
    app.body = tk.Frame(app.win, bg=GLASS_TINT)
    app.body.pack(fill="both", expand=True)
    app.win.geometry(f"{w_phys}x{h_phys}")
    app.win.update()
    app._size = (w_phys, h_phys)
    app._build_ui()
    app.win.update()


def inspect(app, verbose=False):
    """检查所有目标控件是否完整落在窗口可视区内"""
    win_top = app.win.winfo_rooty()
    win_bot = win_top + app.win.winfo_height()
    rows = []
    for name, attr in TARGETS:
        w = getattr(app, attr, None)
        if w is None:
            rows.append((name, -1, False))
            continue
        top = w.winfo_rooty()
        bot = top + w.winfo_height()
        inside = top >= win_top - 2 and bot <= win_bot + 2
        rows.append((name, w.winfo_height(), inside))
    ok = all(h > 0 and ins for _n, h, ins in rows)
    if verbose:
        for n, h, ins in rows:
            flag = "OK " if (h > 0 and ins) else "!! "
            print(f"     {flag}{n:<8} 高 {h:>4}  在窗内={ins}")
    return ok, rows


def main():
    verbose = "--verbose" in sys.argv
    try:
        app = A.App()
    except Exception as exc:
        print(f"无法创建窗口，跳过布局测试：{type(exc).__name__}: {exc}")
        return 0
    app.win.update()

    # (缩放, 窗口宽, 窗口高, 说明)
    # 窗口高按 GlassWindow 的公式反算：min(px(840), 屏幕高 - px(90))
    cases = [
        (1.0,  880,  840, "1920x1080 @100%  设计高度"),
        (1.25, 1100, 968, "1920x1080 @125%"),
        (1.5,  1320, 945, "1920x1080 @150%"),
        (1.0,  880,  678, "1366x768  @100%"),
        (1.25, 1100, 656, "1366x768  @125%"),
        (1.0,  880,  600, "极端矮窗"),
    ]

    failed = []
    try:
        for scale, w, h, label in cases:
            rebuild(app, scale, w, h)
            ok, rows = inspect(app, verbose)
            print(f"[{'通过' if ok else '失败'}] {label}  ({w}x{h})")
            if not ok:
                bad = [f"{n}(高{h2},在窗内={ins})" for n, h2, ins in rows
                       if h2 <= 0 or not ins]
                print("       越界: " + ", ".join(bad))
                failed.append(label)
            if verbose:
                print()
    finally:
        try:
            app.win.destroy()
        except Exception:
            pass

    print()
    if failed:
        print(f"失败 {len(failed)} 项：" + "、".join(failed))
        return 1
    print(f"全部通过（{len(cases)} 种窗口尺寸）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
