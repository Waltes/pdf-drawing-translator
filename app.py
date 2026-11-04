"""
PDF 图纸翻译工具 —— 桌面窗口（macOS 风格界面）

用法：双击「启动.bat」，把 PDF 拖进窗口，点开始转换。

界面与逻辑的分工：
- ui_kit.py  负责长相：无边框窗口、圆角、毛玻璃、三色按钮、卡片、按钮
- core/      负责翻译和改 PDF
- 本文件      只做三件事：摆界面、转发用户操作、显示进度与花费

窗口为什么是无边框的：用户要 macOS 那种三色圆点标题栏。Tk 没有这个能力，
只能自己去系统标题栏、用 Canvas 重画一条。代价是拖动、缩放、最小化都要自己实现，
细节见 ui_kit.py 里的注释。
"""

import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback

# ===========================================================================
# 启动兜底（pythonw 没有控制台，出错必须自己找地方说）
# ===========================================================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

# 打包成 exe 之后，「程序目录」和「数据目录」不是同一个地方了：
# 装进 Program Files 时那里没有写权限，配置、缓存、日志都必须挪到 %APPDATA%。
# 具体规则见 core/paths.py 的说明。
try:
    from core.paths import APP_DIR as _APP_DIR
    from core.paths import DATA_DIR as _DATA_DIR
    from core.paths import RES_DIR, ensure_seed_files
except Exception:
    # core 目录缺失或损坏。这时候不能直接崩 —— 下面 _fatal 要能把原因说出来，
    # 所以先退回旧路径，让程序能走到报错那一步
    _APP_DIR = APP_DIR
    _DATA_DIR = os.path.join(APP_DIR, "data")
    RES_DIR = APP_DIR

    def ensure_seed_files():
        pass

LOG_PATH = os.path.join(_DATA_DIR, "启动错误.log")
ICON_ICO = os.path.join(RES_DIR, "icon.ico")     # 图标由 tools_make_icon.py 生成
ICON_PNG = os.path.join(RES_DIR, "icon.png")

for _name in ("stdout", "stderr"):
    if getattr(sys, _name, None) is None:
        try:
            setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
        except Exception:
            pass


def _log_crash(exc_type, exc, tb):
    try:
        import datetime
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 60 + "\n")
            f.write(f"时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write(f"Python：{sys.version}\n")
            f.write(f"工作目录：{os.getcwd()}\n")
            f.write("".join(traceback.format_exception(exc_type, exc, tb)))
    except Exception:
        pass


def _popup(title, msg):
    try:
        from ctypes import windll
        windll.user32.MessageBoxW(None, msg, title, 0x10)
    except Exception:
        try:
            print(f"{title}: {msg}")
        except Exception:
            pass


def _fatal(msg):
    _log_crash(*sys.exc_info())
    _popup("PDF 图纸翻译 · 启动失败", msg + f"\n\n详细信息已写入：\n{LOG_PATH}")
    sys.exit(1)


sys.excepthook = _log_crash

try:
    import tkinter as tk
    from tkinter import colorchooser, filedialog, font as tkfont, messagebox
except Exception:
    _fatal("缺少图形界面组件 tkinter，程序无法显示窗口。\n\n"
           "解决办法：重新安装 Python，安装时勾选 tcl/tk 组件。")

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:
    HAS_DND = False

try:
    from core import pdf_engine
    from core.llm_client import (GENERIC_KEY_HINT, PRESETS, key_guide,
                                 preset_for)
    from core.sound import play as play_sound
    from core.translate import DATA_DIR, Translator, load_config, save_config
    from ui_kit import (BORDER, CARD_BG, CARD_BG_ALT, DEFAULT_PALETTE, ERR,
                        FONT_CHOICES, GLASS_TINT, OK, PALETTES, TEXT, TEXT_DIM,
                        TEXT_FAINT, WARN, Card, GlassWindow, RoundButton, Theme,
                        TitleBar, apply_palette, clean_font, clear_image_cache,
                        is_usable_font, palette_globals, palette_preview_image,
                        rounded_rect_image, SelectBox)
except ImportError as e:
    _fatal(f"缺少运行所需的组件：{e}\n\n"
           "解决办法：打开命令行执行以下命令安装依赖后重试：\n"
           "python -m pip install pymupdf tkinterdnd2 pillow")

# 首次运行把自带术语库、纠错表、音效铺到数据目录。
# 已经存在的绝不覆盖 —— 用户加的术语不能被一次升级抹掉。
try:
    ensure_seed_files()
except Exception:
    pass


def enable_hidpi():
    """Windows 高分屏下避免界面模糊。必须在创建窗口之前调用"""
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    # 任务栏身份。不声明的话 Windows 会把 pythonw 当成 python 的子窗口，
    # 任务栏显示的是 python 那个蓝黄图标，不是本程序的图标，右键菜单也挂错。
    # 这个调用必须在创建任何窗口之前。
    try:
        from ctypes import windll
        windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "PDFDrawingTranslator.Desktop.1")
    except Exception:
        pass


def default_model(provider):
    """
    取某个服务商的默认模型名。

    不能直接写 PRESETS[p]["models"][0]：custom（自定义接口）的 models 是空列表，
    点一下服务商下拉就会 IndexError，设置面板整个空白（踩过）。
    """
    models = (PRESETS.get(provider) or {}).get("models") or []
    return models[0] if models else ""


def write_metric_report(name, out_path, rep, path=None):
    """
    把计量数据校验的结果写成一份清单，放在导出的图纸旁边。

    path 传了就按它写（现在的做法：输出文件夹内、跟其它产出同样命名风格）；
    不传就按 out_path 推导，兼容旧调用。

    为什么单独出文件而不是只写日志：这份东西是给下料和检验的人看的。
    图纸上某个尺寸被程序改过（可能是模型算错了 4-1/8"），改对了要能查得到；
    程序判不准的（数量对不上），要有人对着原图核一遍。
    只写进滚动日志里，用户翻不到，等于没做。

    返回写好的文件路径，失败返回 None —— 清单写不出来不能影响出图。
    """
    if not rep.issues:
        return None
    if not path:
        path = os.path.splitext(out_path)[0] + "_数据校验清单.txt"

    # 同一句可能同时触发两条（先补小数位、再改数值），分开写会让用户
    # 以为有两处问题、对着原图找半天。按「级别 + 页码 + 原文」合并成一条。
    groups, order = {}, []
    for lvl, pg, s, o, d in rep.issues:
        key = (lvl, pg, s, o)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(d)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{name} 计量数据校验清单\n")
            f.write(f"{rep.summary()}\n\n")
            f.write("怎么读这份清单：\n"
                    "  已纠正 —— 程序按原文重新算过并改正的，不用处理，核对一眼即可；\n"
                    "  待确认 —— 程序判不准（多半是同句里数量对不上），请对照原图核实。\n\n")
            for key in order:
                lvl, pg, s, o = key
                tag = "已纠正" if lvl == "fix" else "待确认"
                loc = f"第{pg}页" if pg else "——"
                f.write(f"[{tag}] {loc}  " + "；".join(groups[key]) + "\n")
                f.write(f"        原文：{s}\n")
                f.write(f"        原译：{o}\n\n")
        return path
    except Exception:
        return None


# ===========================================================================
# 拖拽区
# ===========================================================================
class DropZone(tk.Canvas):
    """虚线框拖拽区。Tk 没有原生虚框，用线段拼，并按屏幕缩放定尺寸"""

    def __init__(self, master, theme, on_click=None, on_drop=None):
        self.theme = theme
        self._on_click = on_click
        self._hover = False
        super().__init__(master, bg=CARD_BG, highlightthickness=0, bd=0,
                         height=theme.px(132), cursor="hand2")
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", lambda e: self._on_click and self._on_click())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        if HAS_DND and on_drop:
            # 没有 tkdnd 扩展时（例如根窗口不是 TkinterDnD.Tk）会抛 TclError，
            # 这时退化成「点一下选文件」，不影响主流程
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", on_drop)
            except Exception:
                pass

    def _set_hover(self, on):
        self._hover = on
        self.draw()

    def draw(self):
        t = self.theme
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20:
            return

        # 背景：比卡片略亮的圆角块，鼠标悬停时透出主题色
        fill = t.accent_soft if self._hover else CARD_BG_ALT
        photo, pad = rounded_rect_image(w, h, t.px(12), fill,
                                        outline=t.accent if self._hover else BORDER,
                                        outline_w=1, scale=2)
        self.create_image(-pad, -pad, image=photo, anchor="nw")
        self._photo = photo

        color = t.accent if self._hover else TEXT_FAINT
        cy = h // 2

        # 下载箭头图标（手绘，避免依赖字体里的符号）
        ax, ay = w // 2, cy - t.px(26)
        self.create_line(ax, ay - t.px(11), ax, ay + t.px(5),
                         fill=color, width=t.px(2), capstyle="round")
        self.create_line(ax - t.px(5), ay, ax, ay + t.px(5),
                         fill=color, width=t.px(2), capstyle="round")
        self.create_line(ax + t.px(5), ay, ax, ay + t.px(5),
                         fill=color, width=t.px(2), capstyle="round")
        self.create_line(ax - t.px(9), ay + t.px(10), ax + t.px(9), ay + t.px(10),
                         fill=color, width=t.px(2), capstyle="round")

        self.create_text(w // 2, cy + t.px(4), text="把 PDF 图纸拖到这里",
                         fill=TEXT if self._hover else TEXT_DIM,
                         font=t.font(t.font_size + 3, bold=True))
        hint = "也可以点这里选择文件" if HAS_DND else "点击选择文件"
        self.create_text(w // 2, cy + t.px(28),
                         text=f"{hint} · 支持矢量图纸与含位图标注的图纸",
                         fill=TEXT_FAINT, font=t.font(t.font_size - 1))


# ===========================================================================
# 设置窗口
# ===========================================================================
class SettingsDialog:
    """
    AI 接口 + 外观设置。

    做成独立窗口而不是弹个对话框，是因为这里要放两类东西：
    接口配置（地址、模型、密钥）和外观配置（字体、字号、配色、提示音）。
    混在一张长表单里会很乱，所以分两页。
    """

    def __init__(self, app):
        self.app = app
        self.theme = app.theme
        self.cfg = load_config()
        self.llm_cfg = dict(self.cfg.get("llm", {}))
        self.ui_cfg = dict(self.cfg.get("ui", {}))

        self.win = tk.Toplevel(app.win)
        self.win.title("设置")          # 任务栏和 Alt+Tab 靠它识别，不设会是「tk」
        self.win.overrideredirect(True)
        self.win.configure(bg=GLASS_TINT)
        w, h = self.theme.px(760), self.theme.px(760)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")
        # 不要用 transient：无边框窗口设了 transient 之后，在 Windows 上会变成
        # 「有 owner 但不显示」的状态，点了设置什么都不弹
        self.win.update_idletasks()

        # 设置窗口只做圆角，不开 DWM 亚克力。
        #
        # 两个原因，都是实测出来的：
        # 1) 主窗口是不透明的，设置窗口透了会把背后的正文和按钮糊出来，
        #    两层字叠在一起既脏又难读；
        # 2) 亚克力 + 整窗 -alpha 叠在一起，每次重绘都要重新合成整块区域，
        #    鼠标划过按钮会明显顿一下。
        try:
            from ui_kit import apply_round_region
            hwnd = self.win.winfo_id()
            apply_round_region(hwnd, w, h, self.theme.px(16))
        except Exception:
            pass

        self._style = None
        self._pending = None          # 正在跑的请求：None / "test" / "bal"
        self._pending_at = 0.0
        # 模型候选：先用预设，测试连接成功后换成服务商真实返回的列表
        self._choices_provider = self.llm_cfg.get("provider") or "deepseek"
        self._model_choices = list(
            (PRESETS.get(self._choices_provider) or {}).get("models") or [])
        self._build()
        # 设置窗口固定不透明。
        #
        # 原来跟着主窗口一起设 -alpha，而这个窗口正好压在主窗口正中间：
        # 半透明之后背后的日志、按钮全透上来，两层字叠在一起根本读不了。
        # 透明度只在主窗口上演示，设置窗保持实心。
        try:
            self.win.attributes("-alpha", 1.0)
        except Exception:
            pass
        self.win.bind("<Escape>", lambda e: self._close())
        # 无边框窗口不会被系统自动前置，必须自己顶上来
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.attributes("-topmost", True)
            self.win.after(400, lambda: self.win.attributes("-topmost", False))
            self.win.focus_force()
            self.win.grab_set()
        except Exception:
            pass

    # ---------------- 布局 ----------------
    def _build(self):
        t = self.theme
        # 无标题栏窗口，自己画一条：只有关闭键
        top = tk.Frame(self.win, bg=GLASS_TINT, height=t.px(52))
        top.pack(fill="x")
        top.pack_propagate(False)
        cv = tk.Canvas(top, bg=GLASS_TINT, highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True)
        r = t.px(6)
        cx, cy = t.px(24), t.px(26)
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#FF5F57", outline="")
        cv.create_text(t.px(52), cy, text="设置", anchor="w", fill=TEXT,
                       font=t.font(t.font_size + 1, bold=True))
        cv.tag_bind(cv.create_rectangle(cx - r, cy - r, cx + r, cy + r, fill="",
                                       outline=""),
                    "<Button-1>", lambda e: self._close())

        # 分页按钮
        tabs = tk.Frame(self.win, bg=GLASS_TINT)
        tabs.pack(fill="x", padx=t.px(20), pady=(0, t.px(8)))
        self._tab_btns = {}
        for key, label in (("api", "AI 接口"), ("look", "外观")):
            b = RoundButton(tabs, t, label, kind="ghost", height=32, width=96,
                            command=lambda k=key: self._show_tab(k))
            b.pack(side="left", padx=(0, t.px(8)))
            self._tab_btns[key] = b

        self.page_host = tk.Frame(self.win, bg=GLASS_TINT)
        self.page_host.pack(fill="both", expand=True, padx=t.px(20),
                            pady=(0, t.px(14)))

        self.page_api = tk.Frame(self.page_host, bg=GLASS_TINT)
        self.page_look = tk.Frame(self.page_host, bg=GLASS_TINT)
        self._build_api(self.page_api)
        self._build_look(self.page_look)
        self._show_tab("api")

    def _show_tab(self, key):
        self._tab = key
        for f in (self.page_api, self.page_look):
            f.pack_forget()
        (self.page_api if key == "api" else self.page_look).pack(fill="both", expand=True)
        # 选中的页签用主题色实心，未选中的是半透明。不加这一步两个页签
        # 长得一模一样，用户看不出自己在哪一页
        for k, b in self._tab_btns.items():
            b.set_kind("primary" if k == key else "ghost")

    # ---------------- AI 接口页 ----------------
    def _build_api(self, parent):
        t = self.theme
        card = Card(parent, t, radius=14, padding=18)
        card.pack(fill="both", expand=True)
        b = card.body

        def row(label, widget_factory, hint=""):
            wrap = tk.Frame(b, bg=CARD_BG)
            wrap.pack(fill="x", pady=(0, t.px(10)))
            tk.Label(wrap, text=label, bg=CARD_BG, fg=TEXT_DIM, anchor="w",
                     font=t.font(t.font_size - 1), width=10).pack(side="left")
            w = widget_factory(wrap)
            w.pack(side="left", fill="x", expand=True, ipady=t.px(4))
            if hint:
                tk.Label(wrap, text=hint, bg=CARD_BG, fg=TEXT_FAINT,
                         font=t.font(t.font_size - 2)).pack(side="left", padx=(t.px(8), 0))
            return w

        def entry(parent, show=None, value=""):
            e = tk.Entry(parent, bg=CARD_BG_ALT, fg=TEXT, insertbackground=t.accent,
                         relief="flat", bd=0, font=t.font(t.font_size),
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=t.accent)
            e.insert(0, value)
            if show:
                e.config(show=show)
            return e

        # ---- 服务商 ----
        prov = self.llm_cfg.get("provider") or "deepseek"
        if prov not in PRESETS:
            prov = "deepseek"
        self.var_provider = tk.StringVar(value=prov)
        names = [(k, v["label"]) for k, v in PRESETS.items()]

        def prov_factory(p):
            # 用自己的 SelectBox，不用 tk.OptionMenu：原生那个右侧指示器
            # 在自定义字体下会渲染成「⊟」方块，看着像控件坏了
            self.sel_provider = SelectBox(p, t, items=names, value=prov,
                                          command=self._on_provider_pick)
            return self.sel_provider
        row("服务商", prov_factory)

        # ---- 接口地址 ----
        # 配置里存的是空串时也要给出预设地址，否则切到自定义再切回来是一片空白
        self.ent_base = row("接口地址", lambda p: entry(
            p, value=(self.llm_cfg.get("base_url") or PRESETS[prov]["base_url"])),
            hint="以 /v1 结尾")

        # ---- 模型名 + 候选下拉 ----
        # 模型名不该让用户手敲：写错一个字就是 404。右侧 ▾ 列出该服务商的常用型号，
        # 测试连接成功后还会换成服务商真实返回的模型列表。
        def model_factory(p):
            f = tk.Frame(p, bg=CARD_BG)
            self.ent_model = entry(
                f, value=(self.llm_cfg.get("model") or default_model(prov)))
            self.ent_model.pack(side="left", fill="x", expand=True)
            self.btn_models = RoundButton(f, t, "▾", kind="ghost", height=26,
                                          width=30, font_size=t.font_size - 2,
                                          radius=7, command=self._show_models)
            self.btn_models.pack(side="left", padx=(t.px(6), 0))
            return f
        row("模型名", model_factory, hint="可下拉选")

        # ---- API Key ----
        def key_factory(p):
            # 同理：Entry 和「显示」勾选框都在 f 里，必须返回 f 本身
            f = tk.Frame(p, bg=CARD_BG)
            e = entry(f, show="●", value=self.llm_cfg.get("api_key", ""))
            e.pack(side="left", fill="x", expand=True, ipady=t.px(4))
            self.ent_key = e
            self.var_show = tk.BooleanVar(value=False)
            cb = tk.Checkbutton(f, text="显示", variable=self.var_show,
                                command=self._toggle_key, bg=CARD_BG, fg=TEXT_DIM,
                                activebackground=CARD_BG, activeforeground=TEXT,
                                selectcolor=CARD_BG_ALT, bd=0,
                                font=t.font(t.font_size - 1))
            cb.pack(side="left", padx=(t.px(8), 0))
            return f
        row("API Key", key_factory)

        self.ent_batch = row("每批条数", lambda p: entry(
            p, value=str(self.llm_cfg.get("batch_size", 30))), hint="1–40，越大越省请求")

        tk.Label(b, text="密钥只保存在本机 data/config.json，不会上传到任何地方。",
                 bg=CARD_BG, fg=TEXT_FAINT, font=t.font(t.font_size - 1),
                 anchor="w", justify="left").pack(fill="x", pady=(0, t.px(8)))

        # ---- 结果输出框 ----
        box = tk.Text(b, height=4, bg=CARD_BG_ALT, fg=TEXT_DIM, relief="flat",
                      bd=0, font=t.font(t.font_size - 1), wrap="word",
                      highlightthickness=1, highlightbackground=BORDER,
                      padx=t.px(8), pady=t.px(6))
        box.pack(fill="x")
        box.insert("1.0", "填好上面的三项，点下面的「测试连接」先确认能通。\n"
                          "常见错误：401 密钥无效 / 402 余额不足 / 404 地址或模型名写错。")
        box.config(state="disabled")
        self.txt_status = box

        # ---- 密钥引导（跟着服务商变）----
        # 原来这里只写了 DeepSeek 一家怎么注册，用户手里是别家的 key 就只能干瞪眼。
        # 现在分两层：一句通用的（任何 OpenAI 兼容接口都适用）+ 当前服务商的申请入口。
        tk.Label(b, text="还没有 API Key？怎么拿", bg=CARD_BG, fg=TEXT,
                 anchor="w", font=t.font(t.font_size, bold=True)).pack(
            fill="x", pady=(t.px(10), t.px(4)))
        self._guide_common = tk.Label(
            b, text=GENERIC_KEY_HINT, bg=CARD_BG, fg=TEXT_FAINT, anchor="w",
            justify="left", wraplength=t.px(660),
            font=t.font(t.font_size - 2))
        self._guide_common.pack(fill="x", pady=(0, t.px(4)))
        self._guide_body = tk.Label(
            b, text="", bg=CARD_BG, fg=TEXT_DIM, anchor="w", justify="left",
            wraplength=t.px(660), font=t.font(t.font_size - 1))
        self._guide_body.pack(fill="x")

        self._guide_row = tk.Frame(b, bg=CARD_BG)
        self._guide_row.pack(fill="x")
        self._guide_btn = RoundButton(self._guide_row, t, "打开申请页面",
                                      kind="ghost", height=30, width=150,
                                      font_size=t.font_size - 1,
                                      command=self._open_apply_page)
        self._guide_btn.pack(side="left", pady=(t.px(6), 0))

        # 剩下的高度用弹簧吃掉，把路径说明压到卡片底部
        tk.Frame(b, bg=CARD_BG).pack(fill="both", expand=True)
        tk.Label(b, text=f"配置文件：{os.path.join(DATA_DIR, 'config.json')}",
                 bg=CARD_BG, fg=TEXT_FAINT, font=t.font(t.font_size - 2),
                 anchor="w").pack(fill="x", pady=(t.px(6), 0))

        # ---- 底部动作 ----
        btns = tk.Frame(parent, bg=GLASS_TINT)
        btns.pack(fill="x", pady=(t.px(10), 0))
        self.btn_test = RoundButton(btns, t, "测试连接", kind="ghost", height=36,
                                    width=110, command=self._test)
        self.btn_test.pack(side="left", padx=(0, t.px(8)))
        self.btn_bal = RoundButton(btns, t, "查询余额", kind="ghost", height=36,
                                   width=110, command=self._balance)
        self.btn_bal.pack(side="left", padx=(0, t.px(8)))
        RoundButton(btns, t, "保存并关闭", kind="primary", height=36, width=120,
                    command=self._save).pack(side="right")

        self._sync_guide()
        self._sync_model_btn()

    # ---------------- 服务商 / 模型 ----------------
    def _on_provider_pick(self, key):
        """
        换服务商：地址、模型、密钥引导一起换。

        原来只换地址和模型，引导区还写着 DeepSeek 的注册步骤，选别家等于误导。
        key 是 SelectBox 传来的服务商 id，不是显示名 —— 早先用名字反查，
        改名字就找不到。
        """
        val = PRESETS.get(key)
        if not val:
            return
        self.var_provider.set(key)
        # 显示层也同步一次：这条路径可能不是从下拉框点进来的
        # （比如重建后恢复配置），只改 var 不改控件，界面就会和实际值对不上
        try:
            self.sel_provider.set_value(key)
        except Exception:
            pass
        self.ent_base.delete(0, "end")
        self.ent_base.insert(0, val.get("base_url") or "")
        self.ent_model.delete(0, "end")
        self.ent_model.insert(0, default_model(key))
        # 候选模型也跟着换；顺手把之前从服务器读到的列表作废
        self._model_choices = list(val.get("models") or [])
        self._choices_provider = key
        self._sync_model_btn()
        self._sync_guide()
        if key == "custom":
            self._write("已切到自定义接口。地址填到 /v1 为止，模型名照对方文档抄；\n"
                        "本机跑的 ollama / vLLM 也能接。")
        else:
            self._write(f"已带入「{val['label']}」的接口地址和默认模型。\n"
                        f"接下来把 API Key 粘进去，点「测试连接」验证。")

    def _sync_model_btn(self):
        """没有候选模型时（自定义接口）把 ▾ 变灰，避免点了没反应"""
        try:
            self.btn_models.set_enabled(bool(self._model_choices))
        except Exception:
            pass

    def _show_models(self):
        """在 ▾ 按钮下方弹出候选模型菜单"""
        choices = self._model_choices or []
        if not choices:
            self._write("这个服务商没有预置模型名，按文档手动填写即可。")
            return
        cur = self.ent_model.get().strip()
        m = tk.Menu(self.win, tearoff=0, bg=CARD_BG_ALT, fg=TEXT,
                    activebackground=self.theme.accent,
                    activeforeground=self.theme.on_accent, bd=0,
                    font=self.theme.font(self.theme.font_size))
        for name in choices[:40]:
            mark = "✓ " if name == cur else "   "
            m.add_command(label=mark + name,
                          command=lambda n=name: self._pick_model(n))
        try:
            x = self.btn_models.winfo_rootx()
            y = self.btn_models.winfo_rooty() + self.btn_models.winfo_height()
            m.tk_popup(x, y)
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _pick_model(self, name):
        self.ent_model.delete(0, "end")
        self.ent_model.insert(0, name)

    # ---------------- 密钥引导 ----------------
    def _sync_guide(self):
        """把引导区换成当前服务商的内容"""
        g = key_guide(self.var_provider.get())
        try:
            self._guide_common.config(text=GENERIC_KEY_HINT)
            self._guide_body.config(text=g["steps"])
        except Exception:
            pass
        try:
            if g.get("url"):
                self._guide_btn.set_text(g["btn"])
                self._guide_btn.set_enabled(True)
                if not self._guide_btn.winfo_ismapped():
                    self._guide_btn.pack(side="left", pady=(self.theme.px(6), 0))
            else:
                self._guide_btn.pack_forget()
        except Exception:
            pass

    def _open_apply_page(self):
        """用系统浏览器打开申请页。打不开也要给个能复制的地址"""
        g = key_guide(self.var_provider.get())
        url = g.get("url")
        if not url:
            return
        try:
            import webbrowser
            webbrowser.open(url)
            self._write(f"已在浏览器打开：{url}\n申请到密钥后粘到上面的 API Key 里。")
        except Exception:
            self._write(f"没能自动打开浏览器，请手动访问：{url}")

    def _toggle_key(self):
        self.ent_key.config(show="" if self.var_show.get() else "●")

    # ---------------- 外观页 ----------------
    def _build_look(self, parent):
        t = self.theme
        card = Card(parent, t, radius=14, padding=18)
        card.pack(fill="both", expand=True)
        b = card.body

        def section(title, hint=""):
            f = tk.Frame(b, bg=CARD_BG)
            f.pack(fill="x", pady=(0, t.px(8)))
            tk.Label(f, text=title, bg=CARD_BG, fg=TEXT, anchor="w",
                     font=t.font(t.font_size, bold=True)).pack(side="left")
            if hint:
                tk.Label(f, text=hint, bg=CARD_BG, fg=TEXT_FAINT,
                         font=t.font(t.font_size - 2)).pack(side="left",
                                                            padx=(t.px(8), 0))

        def line(label):
            f = tk.Frame(b, bg=CARD_BG)
            f.pack(fill="x", pady=(0, t.px(14)))
            tk.Label(f, text=label, bg=CARD_BG, fg=TEXT_DIM, anchor="w", width=10,
                     font=t.font(t.font_size - 1)).pack(side="left")
            return f

        # ---- 主题配色：七套整包色板，点一下换一套 ----
        # 不再让用户单独挑强调色：只换一个色，窗口底色和卡片还是原来那套，
        # 看着就像「没生效」。整包换，选哪套就是哪种气质。
        section("主题配色", "底色、卡片、描边、强调色一起换")
        self.var_palette = tk.StringVar(value=t.palette_key)
        grid = tk.Frame(b, bg=CARD_BG)
        grid.pack(fill="x", pady=(0, t.px(16)))
        self._pal_cells = {}
        # 一排七个：两排会把下面的字号、透明度挤到窗口外面去
        sw_w, sw_h = t.px(74), t.px(46)
        for i, (key, pal) in enumerate(PALETTES.items()):
            cell = tk.Frame(grid, bg=CARD_BG)
            cell.grid(row=0, column=i, padx=(0, t.px(8)),
                      pady=(0, t.px(6)), sticky="w")
            cv = tk.Canvas(cell, width=sw_w, height=sw_h, bg=CARD_BG,
                           highlightthickness=t.px(2), highlightbackground=CARD_BG,
                           bd=0, cursor="hand2")
            photo, _ = palette_preview_image(pal, sw_w, sw_h, radius=9, scale=2)
            cv.create_image(0, 0, image=photo, anchor="nw")
            cv._photo = photo          # 不存引用图会被回收，界面变空白
            cv.pack()
            name = tk.Label(cell, text=pal["label"], bg=CARD_BG, fg=TEXT_DIM,
                            font=t.font(t.font_size - 2))
            name.pack(pady=(t.px(3), 0))
            for w in (cv, name):
                w.bind("<Button-1>", lambda e, k=key: self._pick_palette(k))
            self._pal_cells[key] = (cv, name)

        # ---- 字体与字号 ----
        section("字体与字号")
        f1 = line("字体")
        families = self._font_families()
        self.var_font = tk.StringVar(value=t.font_family)
        # 同样用 SelectBox：原生 OptionMenu 的箭头指示器在这儿会显示成
        # 一个「⊟」方块，深色界面里很显眼
        self.sel_font = SelectBox(
            f1, t, items=[(x, x) for x in families], value=t.font_family,
            command=self._pick_font, height=30)
        self.sel_font.pack(side="left", fill="x", expand=True)

        f2 = line("字号")
        self.var_size = tk.IntVar(value=t.font_size)
        sz = tk.Spinbox(f2, from_=8, to=16, textvariable=self.var_size, width=5,
                        bg=CARD_BG_ALT, fg=TEXT, relief="flat", bd=0,
                        buttonbackground=CARD_BG_ALT, insertbackground=t.accent,
                        font=t.font(t.font_size), highlightthickness=1,
                        highlightbackground=BORDER)
        sz.pack(side="left", ipady=t.px(2))
        tk.Label(f2, text="8 – 16，默认 10", bg=CARD_BG, fg=TEXT_FAINT,
                 font=t.font(t.font_size - 2)).pack(side="left", padx=(t.px(10), 0))

        # ---- 窗口透明度 ----
        # 分档而不是滑杆：Tk 原生 Scale 在深色界面里是条亮灰槽，很扎眼；
        # 四档够用，点一下立刻能看到效果（主窗口跟着变，不用等保存）
        section("窗口透明度", "调低能和背后的窗口叠着看")
        f5 = line("透明度")
        self.var_opacity = tk.DoubleVar(value=t.opacity)
        self._op_btns = {}
        for label, val in (("不透明", 1.0), ("轻微", 0.96),
                           ("明显", 0.92), ("通透", 0.85)):
            btn = RoundButton(f5, t, label, kind="ghost", height=32, width=78,
                              font_size=t.font_size - 1,
                              command=lambda v=val: self._pick_opacity(v))
            btn.pack(side="left", padx=(0, t.px(8)))
            self._op_btns[val] = btn

        # ---- 完成提示音 ----
        section("完成提示音")
        f4 = line("提示音")
        self.var_sound = tk.StringVar(value=self.ui_cfg.get("sound", "soft"))
        for label, val in (("柔和提示音", "soft"), ("系统提示音", "system"), ("不响", "none")):
            tk.Radiobutton(f4, text=label, value=val, variable=self.var_sound,
                           bg=CARD_BG, fg=TEXT_DIM, activebackground=CARD_BG,
                           activeforeground=TEXT, selectcolor=CARD_BG_ALT, bd=0,
                           font=t.font(t.font_size - 1),
                           command=lambda v=val: play_sound(v)).pack(
                side="left", padx=(0, t.px(14)))

        # ---- 预览：颜色 + 字体一次看全 ----
        section("预览", "选配色和字号时会跟着变")
        pv = tk.Frame(b, bg=CARD_BG_ALT, highlightthickness=1,
                      highlightbackground=BORDER, height=t.px(96))
        # 定高而不是 expand：让它吃掉剩余高度的话，整块会撑到两百多像素，
        # 里面只放两行字，看着像没画完
        pv.pack(fill="x")
        pv.pack_propagate(False)
        self._pv = pv
        self._preview = tk.Label(
            pv, text="把 PDF 图纸拖到这里\n翻译后文字位置保持原样，只换文字不换版式",
            bg=CARD_BG_ALT, fg=TEXT, justify="left", anchor="nw",
            padx=t.px(16), pady=t.px(14),
            font=(self.var_font.get(), self.var_size.get()))
        self._preview.pack(fill="both", expand=True)
        # 剩余高度丢给弹簧，把底部按钮区压在窗口下沿
        tk.Frame(b, bg=CARD_BG).pack(fill="both", expand=True)

        # 先按当前配色点亮选中态，再挂改动监听
        self._pick_palette(t.palette_key, apply_alpha=False)
        self._pick_opacity(t.opacity, apply_alpha=False)
        self.var_font.trace_add("write", lambda *a: self._refresh_preview())
        self.var_size.trace_add("write", lambda *a: self._refresh_preview())

        btns = tk.Frame(parent, bg=GLASS_TINT)
        btns.pack(fill="x", pady=(t.px(10), 0))
        RoundButton(btns, t, "恢复默认", kind="ghost", height=36, width=110,
                    command=self._reset_look).pack(side="left")
        RoundButton(btns, t, "保存并关闭", kind="primary", height=36, width=120,
                    command=self._save).pack(side="right")

    # ---------------- 外观页的交互 ----------------
    def _pick_palette(self, key, apply_alpha=True):
        """
        选中一套配色。

        改配色必须让整个界面跟着换。之前只点亮色板、给预览框染个色，
        用户点完看到的是「除了那块预览，别的一点没变」—— 等于没生效。
        所以这里直接重建主窗口和设置窗口本人。

        重建丢进 after：此刻正在色板自己的点击回调里，同步销毁会把自己的
        回调宿主拆掉，容易留下半截状态。
        """
        if key not in PALETTES:
            return
        pal = PALETTES[key]
        self.var_palette.set(key)
        for k, (cv, name) in self._pal_cells.items():
            on = (k == key)
            # 边框粗细始终不变，只换颜色 —— 改粗细会让整排色板跟着抖一下
            cv.config(highlightbackground=pal["accent"] if on else CARD_BG,
                      highlightcolor=pal["accent"] if on else CARD_BG)
            name.config(fg=pal["accent"] if on else TEXT_DIM,
                        font=self.theme.font(self.theme.font_size - 2, bold=on))
        # 预览块用这套配色自己的色，保存前也能看出实际效果
        try:
            self._pv.config(bg=pal["alt"], highlightbackground=pal["border"])
            self._preview.config(bg=pal["alt"], fg=pal["text"])
        except Exception:
            pass
        if apply_alpha:
            self.win.after(1, self._live_apply)

    def _live_apply(self):
        """把当前表单里的配色立刻套到两个窗口上，输入框里没保存的内容不能丢"""
        cfg = self._collect()          # 现成的收表逻辑，含 llm 与 ui
        self.llm_cfg = dict(cfg.get("llm", {}))
        self.ui_cfg = dict(cfg.get("ui", {}))
        self.app.apply_ui_config(self.ui_cfg)
        self.theme = self.app.theme
        self._rebuild()
        self._persist_ui()

    def _persist_ui(self):
        """
        只把 ui 段写盘。

        配色和透明度都是所见即所得，点了就生效，那就得记住 —— 不写盘的话
        用户关掉重开发现又变回去了，会当成 bug。
        接口配置不在这里写，仍要用户点「保存并关闭」，免得半截的 key 被顺手存进去。
        """
        try:
            saved = load_config()
            saved["ui"] = dict(self.ui_cfg)
            save_config(saved)
        except Exception:
            pass

    def _rebuild(self):
        """按新配色重建设置窗口自己的控件。窗框尺寸不动，只换皮"""
        tab = getattr(self, "_tab", "api")
        for ch in list(self.win.winfo_children()):
            ch.destroy()
        self.win.configure(bg=GLASS_TINT)
        self._build()
        self._show_tab(tab)
        # 重建设置窗仍然保持不透明（原因见 __init__ 里的说明）
        try:
            self.win.attributes("-alpha", 1.0)
        except Exception:
            pass
        try:
            from ui_kit import apply_round_region
            apply_round_region(self.win.winfo_id(),
                              self.win.winfo_width(), self.win.winfo_height(),
                              self.theme.px(16))
        except Exception:
            pass

    def _pick_opacity(self, val, apply_alpha=True):
        self.var_opacity.set(float(val))
        for v, btn in getattr(self, "_op_btns", {}).items():
            btn.set_kind("primary" if abs(v - float(val)) < 1e-6 else "ghost")
        if apply_alpha:
            self._apply_alpha(float(val))
            # 透明度也是点了就生效，一并记住，不然下次打开又回到不透明
            try:
                self.ui_cfg["opacity"] = round(float(val), 2)
                self._persist_ui()
            except Exception:
                pass

    def _apply_alpha(self, val=None):
        """
        透明度只作用于主窗口。

        以前两个窗口一起设，结果这个 760×760 的设置窗正好盖在主窗口中间，
        半透明之后背后的日志和按钮全透上来，两层字叠着根本读不了。
        设置窗保持实心，主窗口四周露出来的部分仍然能看出通透程度。
        """
        try:
            v = float(self.var_opacity.get()) if val is None else float(val)
        except Exception:
            v = 1.0
        win = getattr(self.app, "win", None)
        if win is not None:
            try:
                win.attributes("-alpha", v)
            except Exception:
                pass

    def _pick_font(self, name):
        """下拉里选了字体：写进 var_font，预览靠它的 trace 自动重画"""
        self.var_font.set(name)

    def _refresh_preview(self):
        """预览区跟着字体、字号实时变。手敲字号时可能敲出非法值，容忍掉"""
        try:
            size = int(self.var_size.get())
        except Exception:
            return
        size = max(8, min(16, size))
        try:
            self._preview.config(font=(clean_font(self.var_font.get()), size))
        except Exception:
            pass

    def _reset_look(self):
        self._pick_palette(DEFAULT_PALETTE)
        self._pick_opacity(1.0)
        self.var_font.set("Microsoft YaHei UI")
        self.var_size.set(10)
        self.var_sound.set("soft")
        # 下拉控件自己也要回到默认项，否则显示和实际值对不上
        try:
            self.sel_font.set_value("Microsoft YaHei UI")
        except Exception:
            pass

    def _font_families(self):
        """
        只列能当界面字体的中文字体。

        必须滤掉三样东西，不然选进去界面直接废：
        - @ 开头的是 CJK 竖排变体（@新宋体、@黑体），中文会一个一个字竖着排，
          行高全部算错，界面立刻挤成一团
        - Wingdings / Webdings / Symbol 这类符号字体，中文进去全是方块
        - 当前配置里的字体如果不在系统里，也要补进列表，否则下拉框显示空白
        """
        try:
            have = set(tkfont.families(self.win))
        except Exception:
            return FONT_CHOICES[:4]

        def ok(name):
            return is_usable_font(name) and name in have

        out = [f for f in FONT_CHOICES if ok(f)]
        extra = sorted(x for x in have
                       if any(k in x for k in ("黑体", "雅黑", "宋体", "楷", "圆", "等线",
                                               "Hei", "Song", "Kai", "Yuan"))
                       and is_usable_font(x))
        for x in extra:
            if x not in out:
                out.append(x)
        cur = self.theme.font_family
        if cur and is_usable_font(cur) and cur in have and cur not in out:
            out.insert(0, cur)
        return out or FONT_CHOICES[:4]

    # ---------------- 动作 ----------------
    def _collect(self):
        try:
            batch = max(1, min(40, int(self.ent_batch.get().strip() or 12)))
        except ValueError:
            batch = 12
        self.llm_cfg = {
            "provider": self.var_provider.get(),
            "base_url": self.ent_base.get().strip(),
            "model": self.ent_model.get().strip(),
            "api_key": self.ent_key.get().strip(),
            "batch_size": batch,
            # 填了 key 就是启用。不写这个字段的话读回来会落到默认的 False，
            # 界面上又变成「未配置」
            "enabled": bool(self.ent_key.get().strip()),
        }
        try:
            size = max(8, min(16, int(self.var_size.get())))
        except Exception:
            size = 10
        try:
            opacity = max(0.70, min(1.0, float(self.var_opacity.get())))
        except Exception:
            opacity = 1.0
        self.ui_cfg = {
            "palette": self.var_palette.get() or DEFAULT_PALETTE,
            "font": clean_font(self.var_font.get()),
            "font_size": size,
            "opacity": round(opacity, 2),
            "sound": self.var_sound.get(),
        }
        cfg = load_config()
        cfg["llm"] = self.llm_cfg
        cfg["ui"] = self.ui_cfg
        return cfg

    # ---------------- 测试连接 / 查询余额 ----------------
    #
    # 这两个功能以前会永远停在「正在测试连接…」「正在查询余额…」，根因有两层：
    #
    # 1) core 里 test() / query_balance() 早就改成返回 **字典** 了，这里还在按
    #    元组解包（ok, msg = ...）。字典解包成键名，于是抛
    #    ValueError: too many values to unpack —— 请求其实发出去并且回来了。
    # 2) 解包异常发生在 daemon 线程里，没人接，被 threading 默认钩子吞掉；
    #    而 pythonw 下 stderr 指向 devnull，所以既看不到报错，也永远等不到回写。
    #
    # 现在统一走 _request()：结构化回传 + 异常兜底 + 看门狗。哪怕真卡住，
    # 也会在超时后给一句明确的话，不会让用户对着「正在…」干等。

    def _test(self):
        self.app.translator.reload_llm(self._collect())
        self._begin_request("test")

    def _balance(self):
        self.app.translator.reload_llm(self._collect())
        self._begin_request("bal")

    def _begin_request(self, kind):
        """发一个后台请求，并把按钮置成「进行中」"""
        self._pending = kind
        self._pending_at = time.time()
        try:
            btn = self.btn_test if kind == "test" else self.btn_bal
            btn.set_enabled(False)
            btn.set_text("测试中…" if kind == "test" else "查询中…")
        except Exception:
            pass
        self._write("正在测试连接…（一般 1–5 秒）" if kind == "test"
                    else "正在查询余额…（一般 1–5 秒）")

        # 服务商那头的超时比这更短就会先返回；这里是最后一道保险
        self._arm_watchdog(kind, 45 if kind == "test" else 30)

        def work():
            try:
                if kind == "test":
                    r = self.app.translator.llm.test()          # 返回字典
                    self.app.msg_q.put(("cfglog", (
                        bool(r.get("ok")), r.get("msg") or "",
                        list(r.get("models") or []),
                        float(r.get("latency") or 0))))
                else:
                    r = self.app.translator.llm.query_balance()  # 返回字典
                    self.app.msg_q.put(("cfgbal", (
                        bool(r.get("ok")), r.get("text") or "",
                        r.get("amount"), r.get("currency") or "")))
            except Exception as e:
                # 线程里的异常必须自己捞，否则界面永远停在「正在…」
                tip = ("测试连接时出错了" if kind == "test" else "查询余额时出错了")
                self.app.msg_q.put(("cfglog" if kind == "test" else "cfgbal",
                                    (False, f"{tip}：{type(e).__name__} {e}", [], 0)
                                    if kind == "test" else
                                    (False, f"{tip}：{type(e).__name__} {e}", None, "")))

        threading.Thread(target=work, daemon=True).start()

    def _arm_watchdog(self, kind, seconds):
        """等了太久就自己收场，别让状态框一直挂着「正在…」"""
        def check():
            if getattr(self, "_pending", None) != kind:
                return              # 已经有结果了
            if time.time() - self._pending_at < seconds:
                self.win.after(500, check)
                return
            self._pending = None
            self._reset_btns()
            self._write(f"等了 {seconds} 秒还没收到回应，多半是网络不通或接口地址不对。\n"
                        "可以先把这个地址复制到浏览器里打开试试：能打开说明地址通，"
                        "打不开就是网络或代理的问题。\n"
                        "国内网络访问 api.openai.com 通常不通，换 DeepSeek 或硅基流动即可。")
        self.win.after(seconds * 1000, check)

    def _reset_btns(self):
        try:
            self.btn_test.set_enabled(True)
            self.btn_test.set_text("测试连接")
            self.btn_bal.set_enabled(True)
            self.btn_bal.set_text("查询余额")
        except Exception:
            pass

    def _save(self):
        cfg = self._collect()
        save_config(cfg)
        self.app.translator.reload_llm(cfg)
        self.app.apply_ui_config(cfg.get("ui", {}))
        self._close()

    def _write(self, text, tag="dim"):
        try:
            self.txt_status.config(state="normal")
            self.txt_status.delete("1.0", "end")
            self.txt_status.insert("1.0", text)
            self.txt_status.config(state="disabled")
            self._fit_status()
        except Exception:
            pass

    def _fit_status(self):
        """
        结果框按内容自动调高。

        固定 4 行时，失败提示（带四条排查建议）会被截掉一半，而 Text 又没有
        滚动条，等于把最要紧的排查步骤藏起来。这里让它在 3–8 行之间自适应。
        """
        try:
            self.txt_status.update_idletasks()
            n = int(self.txt_status.count("1.0", "end", "displaylines")[0])
        except Exception:
            return
        h = max(3, min(8, n))
        try:
            if int(self.txt_status.cget("height")) != h:
                self.txt_status.config(height=h)
        except Exception:
            pass

    def on_test_result(self, ok, text, models=None, latency=0.0):
        self._pending = None
        self._reset_btns()
        if ok:
            head = "✓ 连接正常"
            if latency:
                head += f"，往返 {latency:.1f} 秒"
            # msg 自己就以「连接正常」开头，别重复两遍
            detail = (text or "").replace("连接正常，", "", 1).strip()
            body = head + ("\n" + detail if detail else "")
            # 服务商真实返回了模型列表，就拿来当候选 —— 比预设的准，
            # 型号改名、新上线的模型都能跟着走。但预设排前面：
            # 那些是确认过能稳定出译文的，服务商列表里可能混着推理型模型
            if models:
                preset = list((PRESETS.get(self.var_provider.get()) or {}).get("models") or [])
                merged = preset + [m for m in models if m not in preset]
                self._model_choices = merged[:40]
                self._choices_provider = self.var_provider.get()
                self._sync_model_btn()
                body += (f"\n服务商一共报了 {len(models)} 个模型，点「模型名」右侧的 ▾ "
                         "可以直接选。")
            body += "\n确认无误后点「保存并关闭」，配置才会写进文件。"
        else:
            body = "✕ " + (text or "连接失败")
            body += ("\n\n按这个顺序排查：\n"
                     "① key 是否复制完整，前后有没有多带空格；\n"
                     "② 接口地址是否以 /v1 结尾（末尾别再带 /chat/completions）；\n"
                     "③ 模型名大小写和横杠是否和文档一致；\n"
                     "④ 换服务商时 key 也要换，各家密钥不通用。")
        self._write(body)

    def on_balance_result(self, ok, text, amount=None, currency=""):
        self._pending = None
        self._reset_btns()
        self._write(text)
        if ok and amount is not None:
            try:
                self.app.set_balance_text(text, float(amount))
            except Exception:
                pass

    def _close(self):
        try:
            self.win.grab_release()
        except Exception:
            pass
        self.app.dlg = None
        try:
            self.win.destroy()
        except Exception:
            pass


# ===========================================================================
# 主窗口
# ===========================================================================
class App(GlassWindow):
    def __init__(self):
        cfg = load_config()
        self._cfg = cfg
        self.theme = Theme(cfg.get("ui", {}))
        # 配色必须先落地再去建窗口：GlassWindow 建 body 时读的是模块级的
        # GLASS_TINT，晚一步就是旧颜色
        self._apply_palette_names()
        # 根窗口必须交给 TkinterDnD 创建，拖拽才可用；装不上就退回普通窗口
        root_cls = TkinterDnD.Tk if HAS_DND else None
        # theme 一定要传进去：GlassWindow 里是 `theme or Theme()`，
        # 不传就把上面刚按配置建好的主题覆盖成默认值，字体和配色全白设
        super().__init__(size=(880, 840), theme=self.theme, root_cls=root_cls,
                         icon_path=ICON_ICO)

        self.win.title("PDF 图纸翻译")
        self._set_window_icon()
        self.files = []
        self.out_dir = ""
        self.msg_q = queue.Queue()
        self.busy = False
        self.dlg = None
        self.balance_text = ""
        self.balance_amount = None
        self.translator = Translator()

        self._build_ui()
        self._fill_list()
        self.refresh_strip()
        self._poll_queue()
        self._apply_opacity()
        self._bring_to_front()

    def _set_window_icon(self):
        """
        非 Windows 平台才补一次 iconphoto。

        Windows 上图标已经在 GlassWindow._apply_icon 里用 Win32 设好了，
        这里千万不能再调 iconphoto —— Tk 转 HICON 会丢 alpha，把刚才
        设好的图标覆盖成一个没有圆角的实心方块（实测踩到过）。
        """
        if sys.platform == "win32":
            return
        try:
            if os.path.exists(ICON_PNG):
                from PIL import Image, ImageTk
                # 必须存成实例属性：Tk 只持有弱引用，局部变量被回收后图标又会变空
                self._icon_photo = ImageTk.PhotoImage(Image.open(ICON_PNG))
                self.win.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _apply_palette_names(self):
        """
        把当前配色同步到两处全局名。

        app.py 里到处直接写 GLASS_TINT / CARD_BG，ui_kit 里也一样。这些名字在
        import 时就绑定成了值，光改 Theme 对象不会影响它们，所以要显式刷一遍。
        换配色后必须重建控件（apply_ui_config 负责），否则旧控件还挂着旧颜色。
        """
        apply_palette(self.theme)
        globals().update(palette_globals(self.theme))

    def _apply_opacity(self):
        """整窗透明度。Windows 上 -alpha 对 Tk 顶层窗口有效，含子控件一起变"""
        try:
            self.win.attributes("-alpha", float(self.theme.opacity))
        except Exception:
            pass

    def _bring_to_front(self):
        """顶一下前台。无边框窗口不置顶容易被别的窗口盖住，用户以为没启动"""
        try:
            self.win.lift()
            self.win.attributes("-topmost", True)
            self.win.after(600, lambda: self.win.attributes("-topmost", False))
            self.win.focus_force()
        except Exception:
            pass

    # ----------------------------- 界面 -----------------------------
    def _build_ui(self):
        t = self.theme
        # 所有内容都放进 GlassWindow 提供的 self.body，别直接 pack 到 self.win ——
        # body 已经占满窗口，再往 win 上放会互相挤掉
        parent = self.body

        # 标题栏（三色圆点 + 标题 + 右侧设置入口）
        self.title_bar = TitleBar(parent, t, self, title="PDF 图纸翻译",
                                  on_settings=self._open_settings,
                                  icon_path=ICON_PNG)
        self.title_bar.pack(fill="x")

        host = tk.Frame(parent, bg=GLASS_TINT)
        host.pack(fill="both", expand=True, padx=t.px(16), pady=(0, t.px(12)))

        # 状态条：模型 / 余额 / 本次花费
        self.card_strip = Card(host, t, radius=12, padding=14, shadow=12,
                               shadow_alpha=100, height=78)
        self.card_strip.pack(fill="x", pady=(0, t.px(12)))
        # 用 Text 而不是 Label：一行里要分三种颜色（标签灰、状态绿、模型白），
        # Label 只能整段一个颜色，全绿会刺眼
        self.strip = tk.Text(self.card_strip.body, height=2, bg=CARD_BG,
                             fg=TEXT_DIM, bd=0, relief="flat", wrap="word",
                             highlightthickness=0, cursor="arrow",
                             font=t.font(t.font_size - 1), padx=0, pady=0,
                             spacing1=0, spacing3=2)
        self.strip.pack(fill="x")
        self.strip.tag_config("dim", foreground=TEXT_DIM)
        self.strip.tag_config("ok", foreground=OK)
        self.strip.tag_config("val", foreground=TEXT)
        self.strip.tag_config("warn", foreground=WARN)
        self.strip.config(state="disabled")

        # 拖拽区
        self.card_drop = Card(host, t, radius=14, padding=0, shadow=12,
                              shadow_alpha=100, height=132)
        self.card_drop.pack(fill="x", pady=(0, t.px(12)))
        self.drop = DropZone(self.card_drop.body, t, on_click=self._pick_files,
                             on_drop=self._on_drop)
        self.drop.pack(fill="both", expand=True)

        # 文件列表。不放滚动条：Windows 的原生滚动条是亮色，在深色界面上
        # 像贴了块白胶布；列表本来也不长，滚轮够用
        self.card_list = Card(host, t, radius=14, padding=12, shadow=12,
                              shadow_alpha=100, height=186)
        self.card_list.pack(fill="x", pady=(0, t.px(12)))
        lb = self.card_list.body
        head = tk.Frame(lb, bg=CARD_BG)
        head.pack(fill="x", pady=(0, t.px(6)))
        self.lbl_count = tk.Label(head, text="待翻译文件（0）", bg=CARD_BG,
                                  fg=TEXT_DIM, font=t.font(t.font_size - 1))
        self.lbl_count.pack(side="left")
        RoundButton(head, t, "清空", kind="ghost", height=26, width=72,
                    font_size=t.font_size - 2,
                    command=self._clear_files).pack(side="right")

        wrap = tk.Frame(lb, bg=CARD_BG_ALT)
        wrap.pack(fill="both", expand=True)
        self._list_wrap = wrap
        self.listbox = tk.Listbox(wrap, bg=CARD_BG_ALT, fg=TEXT, bd=0,
                                  highlightthickness=0, activestyle="none",
                                  selectbackground=t.accent_soft,
                                  selectforeground=TEXT,
                                  font=t.font(t.font_size), relief="flat")
        self.listbox.pack(fill="both", expand=True, padx=t.px(8), pady=t.px(6))
        self.listbox.bind("<Delete>", lambda e: self._remove_selected())
        # 空列表要有个说法。一片空白会让人怀疑是不是坏了
        self.lbl_empty = tk.Label(
            wrap, text="还没有添加文件\n把 PDF 图纸拖进上面的方框，或点它选择",
            bg=CARD_BG_ALT, fg=TEXT_FAINT, justify="center",
            font=t.font(t.font_size - 1))

        # 输出目录
        row = tk.Frame(host, bg=GLASS_TINT)
        row.pack(fill="x", pady=(0, t.px(12)))
        tk.Label(row, text="输出到", bg=GLASS_TINT, fg=TEXT_DIM,
                 font=t.font(t.font_size - 1)).pack(side="left")
        self.lbl_out = tk.Label(row, text="源文件旁（每份一个文件夹）", bg=GLASS_TINT, fg=TEXT,
                                font=t.font(t.font_size - 1))
        self.lbl_out.pack(side="left", padx=(t.px(10), 0))
        RoundButton(row, t, "选择目录", kind="ghost", height=30, width=86,
                    font_size=t.font_size - 1,
                    command=self._pick_outdir).pack(side="right")

        # 主按钮 + 进度条
        self.btn = RoundButton(host, t, "开始转换", kind="primary", height=46,
                               width=200, font_size=t.font_size + 2,
                               command=self._start)
        self.btn.pack(pady=(0, t.px(8)))

        self.pbar = tk.Canvas(host, height=t.px(5), bg=GLASS_TINT,
                              highlightthickness=0, bd=0)
        self.pbar.pack(fill="x", pady=(0, t.px(8)))
        self._pbar_value = 0
        self._pbar_max = 1
        self.pbar.bind("<Configure>", lambda e: self._draw_progress())

        # 日志
        self.card_log = Card(host, t, radius=14, padding=12, shadow=12,
                             shadow_alpha=100)
        self.card_log.pack(fill="both", expand=True)
        self.log = tk.Text(self.card_log.body, bg=CARD_BG, fg=TEXT_DIM, bd=0,
                           relief="flat", highlightthickness=0, wrap="word",
                           font=t.font(t.font_size - 1), padx=t.px(2),
                           pady=t.px(2), state="disabled", cursor="arrow")
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("dim", foreground=TEXT_DIM)
        self.log.tag_config("ok", foreground=OK)
        self.log.tag_config("warn", foreground=WARN)
        self.log.tag_config("err", foreground=ERR)
        self.log.tag_config("hi", foreground=TEXT)
        self.log.tag_config("accent", foreground=t.accent)

        self._say("准备就绪。把 PDF 图纸拖进来，或点击上方区域选择文件。", "dim")

    def _draw_progress(self):
        """自绘进度条：Tk 原生 ttk 进度条在高分屏上又丑又不跟主题"""
        t = self.theme
        self.pbar.delete("all")
        w = self.pbar.winfo_width()
        h = t.px(5)
        if w < 4:
            return
        # 没开始跑的时候不画轨道：一条通长的空灰线看着像根莫名其妙的分隔线，
        # 宁可空着。控件本身留着占位，进度开始时不会跳版。
        if self._pbar_max <= 0 or self._pbar_value <= 0:
            return
        frac = min(1.0, self._pbar_value / self._pbar_max)
        y = h / 2
        # 用圆头粗线代替矩形：两端自带圆角，细长条也能看出是进度条不是分割线
        self.pbar.create_line(0, y, w, y, width=h, fill=CARD_BG_ALT,
                              capstyle="round")
        if frac > 0:
            self.pbar.create_line(0, y, max(h, int(w * frac)), y, width=h,
                                  fill=t.accent, capstyle="round")

    def set_progress(self, done, total):
        self._pbar_value = done
        self._pbar_max = max(total, 1)
        self._draw_progress()

    # ----------------------------- 外观应用 -----------------------------
    def apply_ui_config(self, ui_cfg):
        """设置里改完外观，就地重建界面，不要求用户重启"""
        self.theme = Theme(ui_cfg)
        self.theme.init_scale(self.win)
        # 先换全局颜色名，再重建控件 —— 顺序反了的话新控件拿到的还是旧色
        self._apply_palette_names()
        # 换主题后，之前生成的圆角图、按钮底色都是旧颜色，必须整块重画。
        # body 本身也要重建：它上面挂着所有子控件，销毁后原对象已经没用了。
        for child in list(self.win.winfo_children()):
            # 设置窗口是主窗口的 Toplevel 子窗口，不能跟着一起销毁：
            # 点「保存并关闭」时它正在自己的回调里，销毁掉之后就再也关不掉了
            if isinstance(child, tk.Toplevel):
                continue
            child.destroy()
        self.body = tk.Frame(self.win, bg=GLASS_TINT)
        self.body.pack(fill="both", expand=True)
        self.win.configure(bg=GLASS_TINT)
        self._build_ui()
        self._refresh_count()
        self._fill_list()
        self.refresh_strip()
        self._draw_progress()
        self._apply_opacity()
        self._apply_shape()

    # ----------------------------- 状态条 -----------------------------
    def refresh_strip(self):
        t = self.translator
        usage = t.llm.usage
        w = self.strip
        w.config(state="normal")
        w.delete("1.0", "end")
        if t.llm_ready():
            p = preset_for(t.cfg.get("llm", {}).get("provider"))
            name = p["label"].split("（")[0]
            w.insert("end", "AI 接口：", "dim")
            w.insert("end", "已启用", "ok")
            w.insert("end", f" · {name} · {t.llm.model}", "val")
            if self.balance_amount is not None:
                w.insert("end", f" · 余额 ¥{self.balance_amount:.2f}", "ok")
            w.insert("end", "\n" + usage.summary().replace("本次 ", "本次消耗："), "dim")
        else:
            w.insert("end", "AI 接口：", "dim")
            w.insert("end", "未配置", "warn")
            w.insert("end", " —— 只能靠本地术语库 + 免费机翻，长句译不了多少，"
                            "且免费机翻每天有配额、容易失败。", "dim")
            w.insert("end", "\n点右上角「设置」填自己的 key 就能解决。", "dim")
        w.config(state="disabled")

    def set_balance_text(self, text, amount):
        self.balance_text = text
        self.balance_amount = amount
        self.refresh_strip()

    def _open_settings(self):
        if self.dlg is not None:
            try:
                self.dlg.win.lift()
            except Exception:
                pass
            return
        self.dlg = SettingsDialog(self)

    # ----------------------------- 文件 -----------------------------
    @staticmethod
    def _short_name(path, limit=40):
        """列表里只显示文件名，过长就截断 —— 图纸名往往又长又是英文，
        整串塞进 Listbox 会横向溢出，看着像界面坏了"""
        n = os.path.basename(path)
        return n if len(n) <= limit else n[:limit - 1] + "…"

    def _fill_list(self):
        """按 self.files 重画列表，并处理空态"""
        self.listbox.delete(0, "end")
        for p in self.files:
            self.listbox.insert("end", "  " + self._short_name(p))
        self._refresh_count()
        try:
            if self.files:
                self.lbl_empty.place_forget()
            else:
                self.lbl_empty.place(relx=0.5, rely=0.5, anchor="center")
        except Exception:
            pass

    def _add_paths(self, paths):
        added = 0
        for p in paths:
            if p and p.lower().endswith(".pdf") and os.path.isfile(p) \
                    and p not in self.files:
                self.files.append(p)
                added += 1
        if added:
            self._fill_list()
        return added

    def _on_drop(self, event):
        paths = [(a or b).strip() for a, b in re.findall(r"\{([^}]+)\}|(\S+)", event.data)]
        added = self._add_paths(paths)
        if added:
            self._say(f"已加入 {added} 个文件。", "ok")

    def _pick_files(self):
        if self.busy:
            return
        picked = filedialog.askopenfilenames(title="选择 PDF 图纸",
                                             filetypes=[("PDF 文件", "*.pdf")])
        self._add_paths(list(picked))

    def _remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
            self.files.pop(i)
        self._fill_list()

    def _clear_files(self):
        if self.busy or not self.files:
            return
        # 清空会丢掉用户攒了一路的清单，先确认一下
        if not messagebox.askokcancel("清空列表",
                                      f"要移除列表里的 {len(self.files)} 个文件吗？\n"
                                      "只是从列表里拿掉，磁盘上的 PDF 不会被删除。",
                                      parent=self.win):
            return
        self.files.clear()
        self._fill_list()

    def _refresh_count(self):
        self.lbl_count.config(text=f"待翻译文件（{len(self.files)}）")

    def _pick_outdir(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.out_dir = d
            self.lbl_out.config(text=d)

    # ----------------------------- 日志 -----------------------------
    def _say(self, text, tag="dim"):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    self._say(*payload)
                elif kind == "cfglog":
                    if self.dlg:
                        self.dlg.on_test_result(*payload)
                elif kind == "cfgbal":
                    if self.dlg:
                        self.dlg.on_balance_result(*payload)
                elif kind == "progress":
                    self.set_progress(*payload)
                elif kind == "strip":
                    self.refresh_strip()
                elif kind == "done":
                    self.busy = False
                    self.btn.set_enabled(True)
                    self.btn.set_text("开始转换")
                    self.set_progress(0, 1)
                    self._finish(payload)
        except queue.Empty:
            pass
        self.win.after(80, self._poll_queue)

    # ----------------------------- 转换 -----------------------------
    def _start(self):
        if self.busy:
            return
        if not self.files:
            self._say("还没有文件。先拖一份 PDF 进来。", "warn")
            return

        self.busy = True
        self.btn.set_enabled(False)
        self.btn.set_text("转换中，请稍候…")
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self._say(f"开始处理 {len(self.files)} 个文件\n", "hi")
        if not self.translator.llm_ready():
            self._say("提示：没有配置 AI 接口，长句只能交给免费机翻；它每天有配额，"
                      "用完后该句保留原文。\n", "warn")

        self.translator.llm.usage.reset_session()
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        outputs = []      # 各份的主交付物（翻译版），只用于记账归属
        folders = []      # 本轮生成的输出文件夹，完成时按文件夹展示
        try:
            for idx, path in enumerate(self.files):
                name = os.path.basename(path)
                self.msg_q.put(("log", (f"── {name}", "accent")))

                info = pdf_engine.analyze_pdf(path)
                self.msg_q.put(("log", (
                    f"   共 {info['pages']} 页 · 可提取文字 {info['chars']} 个字符 · "
                    f"位图 {info['images']} 张", "dim")))
                if info["scan_like"]:
                    self.msg_q.put(("log", (
                        "   注意：这份文件几乎没有可选中的文字（疑似扫描件），"
                        "将只依赖位图 OCR，效果可能有限。", "warn")))

                # ---- 输出文件夹：一份源文件一个文件夹，文件夹名就是原文件名 ----
                plan = self._plan_outputs(path)
                folder = plan["folder"]
                out_path = plan["translated"]
                mark_path = plan["annotated"]
                try:
                    os.makedirs(folder, exist_ok=True)
                    # 源文件复制一份进文件夹：整个文件夹能独立拿出去，
                    # 对方拿到就能对照原文看译文，不用回头再要原图。
                    shutil.copy2(path, plan["source"])
                    self.msg_q.put(("log", (f"   输出文件夹：{folder}", "dim")))
                except Exception as exc:
                    # 建文件夹失败就别装了，直接说清楚，别再往里写文件
                    self.msg_q.put(("log", (
                        f"   无法创建输出文件夹：{folder}\n"
                        f"     {type(exc).__name__}: {exc}\n"
                        f"     请换一个输出目录（点「选择目录」）后重试。", "err")))
                    continue

                self.msg_q.put(("progress", (idx, len(self.files))))

                def on_page(done, total, msg):
                    self.msg_q.put(("log", ("    " + msg, "dim")))

                stats = {}
                # 每份文件单独记账，否则多份连跑时清单会串
                self.translator.reset_metric_report()
                # 必须显式传 translate_batch：不传的话 pdf_engine 会退化成
                # 一条一条调 translate_fn(text, lang)，句子之间不再并发，
                # 一份 2700 行的图纸能从二十几秒拖到几分钟
                #
                # render_preview 关掉：引擎默认会把预览图命名成
                # 「【翻译】xxx_预览.png」贴在 PDF 旁边，那是旧的散放做法。
                # 现在由下面自己调 render_comparison 输出到规定位置。
                pdf_engine.process_pdf(path, out_path, self.translator,
                                       progress=on_page, stats_out=stats,
                                       translate_batch=self.translator.translate_many,
                                       annotate_path=mark_path,
                                       render_preview=False)
                self.msg_q.put(("log", (
                    f"   完成：{stats.get('translated', 0)}/{stats.get('targets', 0)} 条已译 · "
                    f"段落合并 {stats.get('paragraphs', 0)} 处 · "
                    f"位图内文字 {stats.get('image_texts', 0)} 条", "ok")))
                # 翻译版是主交付物，记账时用它做归属
                outputs.append(out_path)

                # ---- 标注版 ----
                marks = stats.get("metric_marks") or 0
                if stats.get("annotate_error"):
                    self.msg_q.put(("log", (
                        f"   标注版没能生成（{stats['annotate_error']}）。"
                        f"翻译版是好的，可以正常用。", "warn")))
                elif marks:
                    self.msg_q.put(("log", (
                        f"   标注版：{marks} 处换算过的数值已标黄", "ok")))
                    outputs.append(mark_path)
                else:
                    self.msg_q.put(("log", (
                        "   这份文件没有英制换算，未生成标注版（两版内容会完全一样）",
                        "dim")))

                # ---- 预览图：渲染译文的首页，不打开 PDF 也能一眼看效果 ----
                try:
                    pdf_engine.render_comparison(out_path, plan["preview"])
                except Exception as exc:
                    self.msg_q.put(("log", (
                        f"   预览图没能生成（{type(exc).__name__}），不影响其它文件。",
                        "dim")))

                fixed = stats.get("unit_decl_fixed") or 0
                if fixed:
                    self.msg_q.put(("log", (
                        f"   单位声明栏已改写 {fixed} 处：改成毫米，并注明原图英寸已换算",
                        "ok")))
                miss = stats.get("untranslated") or []
                # 未译清单始终写一份：没有未译就写「全部译出」。
                # 结构保持稳定，用户不用猜「这次是没漏，还是漏了没出清单」。
                miss_file = plan["untranslated"]
                try:
                    with open(miss_file, "w", encoding="utf-8") as f:
                        f.write(f"{name} 未译条目（共 {len(miss)} 条）\n\n")
                        if miss:
                            f.write("下面这些位置保留原文，没有译出。\n"
                                    "多为纯代号、人名、特殊符号或中英混排，硬翻反而毁图纸。\n\n")
                            for pg, txt in miss:
                                f.write(f"第{pg}页  {txt}\n")
                        else:
                            f.write("全部译出，没有未译条目。\n")
                except Exception:
                    pass
                if miss:
                    for pg, txt in miss[:12]:
                        self.msg_q.put(("log", (f"     未译 第{pg}页：{txt[:60]}", "warn")))
                    if len(miss) > 12:
                        self.msg_q.put(("log", (
                            f"     …另有 {len(miss) - 12} 处，"
                            f"清单见文件夹内「{os.path.basename(miss_file)}」", "warn")))
                # ---- 计量数据校验结果 ----
                # 这一步在图纸之外单独交代：换算对不对、有没有被程序改过数。
                # 用户要拿去下料的东西，不能只在日志里留一行「已处理」。
                rep = self.translator.metric_report
                self.msg_q.put(("log", ("   " + rep.summary(),
                                        "warn" if rep.has_warning() else "ok")))
                if rep.issues:
                    check_file = write_metric_report(name, out_path, rep,
                                                     path=plan["check"])
                    if check_file and rep.has_warning():
                        self.msg_q.put(("log", (
                            f"      判不准的条目已写入 {os.path.basename(check_file)}，"
                            "请对照原图核实后再下料。", "warn")))

                folders.append(folder)

        except Exception:
            self.msg_q.put(("log", ("转换出错：\n" + traceback.format_exc(), "err")))
        finally:
            st = self.translator.stats
            usage = self.translator.llm.usage
            self.msg_q.put(("log", (
                f"\n翻译来源：本地术语库 {st['dict']} · AI 模型 {st['llm']} · "
                f"免费机翻 {st['online']} · 缓存命中 {st['cache']} · 保留原文 {st['failed']}",
                "dim")))
            if usage.session["calls"]:
                self.msg_q.put(("log", (usage.summary(), "hi")))
                self.msg_q.put(("log", (usage.cumulative_text(), "dim")))
                usage.add_run(outputs[0] if outputs else "", usage.session["items"])
            if self.translator.last_llm_error and st["llm"] == 0:
                self.msg_q.put(("log", (
                    "AI 接口调用失败：" + self.translator.last_llm_error +
                    "\n     请到「设置」里点「测试连接」查看具体原因。", "err")))
            elif st["llm"] == 0 and self.translator.last_llm_reject:
                # 接口是通的，是模型答的东西没过质检。这两件事必须分开说：
                # 报成「接口调用失败」会把人支到设置里测连接，白折腾。
                self.msg_q.put(("log", (
                    "AI 有回应，但没有一条通过质检："
                    + self.translator.reject_summary() +
                    "\n     这些条目已保留原文。多为译文里残留了英文、或数字对不上，"
                    "明细见输出文件夹内的「未译清单」。", "warn")))
            elif st["llm"] and st["failed"]:
                self.msg_q.put(("log", (
                    f"{st['failed']} 条没能译好，已保留原文 —— 多为纯代号、特殊符号或"
                    f"混排内容，硬翻反而毁图纸。明细见输出文件夹内的「未译清单」。", "warn")))
            if self.translator._rate_limited and not self.translator.llm_ready():
                self.msg_q.put(("log", (
                    "免费在线翻译（MyMemory）今日配额已用完 —— 它是没配 AI 接口时的备用机翻，"
                    "配额按天重置。配好 AI 接口后就不再依赖它。", "warn")))
            self.translator.save_cache()
            self.msg_q.put(("strip", None))
            self.msg_q.put(("done", folders))

    def _plan_outputs(self, src):
        """
        为一份源文件规划一个独立的输出文件夹，并把里面每个文件的位置算好。

        为什么不再往用户指定目录里直接扔文件：
        一次翻译会产出翻译版、标注版、未译清单、预览图（有换算异常时还有一份
        数据校验清单）。用户连跑几份图纸之后，那个目录里全是【翻译】xxx、
        【标注】xxx 混在一起，谁对应谁分不清，发文件时还容易漏发。
        改成一份源文件一个文件夹、文件夹名就是原文件名：
        要发给谁直接把整个文件夹拖走，里面是齐的。

        文件夹重名就加序号，绝不覆盖上一轮的结果 ——
        用户反复调参数时要能拿两版对比。
        """
        root = self.out_dir or os.path.dirname(src)
        filename = os.path.basename(src)
        base, ext = os.path.splitext(filename)
        if not ext:                      # 没扩展名的极端情况，补一个免得建出怪文件
            ext = ".pdf"
            filename = filename + ext

        folder = os.path.join(root, base)
        n = 1
        while os.path.exists(folder):
            folder = os.path.join(root, f"{base}_{n}")
            n += 1

        return {
            "folder": folder,
            # 源文件也复制一份进来：文件夹要能独立拿出去，
            # 别人拿到就能对照原文看译文，不用回头跟你要原图。
            "source": os.path.join(folder, filename),
            "translated": os.path.join(folder, f"【翻译】{base}.pdf"),
            "annotated": os.path.join(folder, f"【标注】{base}.pdf"),
            "untranslated": os.path.join(folder, f"【未译清单】{base}.txt"),
            "preview": os.path.join(folder, f"【预览】{base}.png"),
            "check": os.path.join(folder, f"【数据校验清单】{base}.txt"),
        }

    def _finish(self, folders):
        """
        收尾展示。folders 是本轮生成的输出文件夹列表。

        按文件夹展示而不是按文件平铺：一份图纸的五六个产出本来就是一整套，
        平铺成一串路径反而让人对不上号。每个文件夹下把实际生成的文件列出来，
        用户能当场看出这套是不是齐的（比如标注版没出，就知道这份图纸没换算）。
        """
        if not folders:
            self._say("\n没有产出文件。可能是 PDF 里没有可翻译的文字。", "warn")
            return
        self._say(f"\n全部完成，{len(folders)} 套图纸已生成：", "ok")
        for d in folders:
            self._say("  " + os.path.basename(d) + os.sep, "hi")
            try:
                names = sorted(os.listdir(d))
            except Exception:
                names = []
            for fn in names:
                self._say("    " + fn, "dim")
            # 纯公制图纸不会有标注版（两份会一模一样）。这里要说一句，
            # 否则用户看到少一项会怀疑是不是漏出了。
            if names and not any(n.startswith("【标注】") for n in names):
                self._say("    （没有英制换算，标注版与翻译版完全一样，未单独生成）",
                          "dim")
        # 完成提示音，音色由外观设置决定
        try:
            play_sound(self._cfg.get("ui", {}).get("sound", "soft"))
        except Exception:
            pass
        # 只开第一个文件夹：开了五六个资源管理器窗口是灾难
        try:
            os.startfile(folders[0])
        except Exception:
            pass

    def run(self):
        self.win.mainloop()


def _selftest():
    """
    冒烟自检：不联网、不花钱，把「读 PDF → 单位换算 → 写回 → 出预览」跑一遍。

    为什么要这个：打包成 exe 之后出问题，表现往往只是「窗口一闪就没了」
    或者「点开始一直不动」，用户描述不出来，开发也复现不了。而这条链路上
    最容易在打包后失效的，恰恰都是沉默的事 —— 缺模块、目录不可写、
    系统字体找不到。跑一遍就能当场定位是哪一环断的。

    用法：
        PDF图纸翻译.exe --selftest
    结果写进数据目录的 selftest.log，同时弹窗显示结论。
    """
    import tempfile
    lines, ok = [], True

    def check(name, passed, detail=""):
        nonlocal ok
        ok = ok and passed
        lines.append(f"[{'通过' if passed else '失败'}] {name}" + (f"    {detail}" if detail else ""))

    check("数据目录可写", os.access(_DATA_DIR, os.W_OK), _DATA_DIR)

    # 配置读写：用临时文件名，绝不碰用户正在用的 config.json
    try:
        probe = os.path.join(_DATA_DIR, "_selftest.json")
        with open(probe, "w", encoding="utf-8") as f:
            f.write('{"ok": 1}')
        with open(probe, encoding="utf-8") as f:
            got = f.read()
        os.remove(probe)
        check("配置读写", got == '{"ok": 1}')
    except Exception as exc:
        check("配置读写", False, f"{type(exc).__name__}: {exc}")

    # 系统字体：写入中文全靠它，缺了会出一堆空白方块
    cjk_font = None
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "C:/Windows/Fonts/simsun.ttc"):
        if os.path.exists(p):
            cjk_font = p
            break
    check("系统中文字体", bool(cjk_font), cjk_font or "一个都没找到")

    tmp = tempfile.mkdtemp(prefix="selftest_")
    try:
        import pymupdf
        from core import pdf_engine
        from core.translate import Translator, load_config

        cfg = load_config()
        cfg["online_fallback"] = False       # 自检绝不允许联网
        cfg["llm"] = dict(cfg.get("llm") or {})
        cfg["llm"]["enabled"] = False
        tr = Translator(cfg)

        src = os.path.join(tmp, "selftest.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=420, height=300)
        y = 60
        for s in ('OD 4"', "THK 1-1/2''", "Panel 25 lb", "Brass bushing"):
            page.insert_text((40, y), s, fontsize=12)
            y += 26
        doc.save(src)
        doc.close()
        check("读取 PDF", True, pymupdf.__doc__.split("\n")[0] if pymupdf.__doc__ else "")

        dst = os.path.join(tmp, "out.pdf")
        stats = {}
        pdf_engine.process_pdf(src, dst, None, render_preview=False,
                               translate_batch=tr.translate_many,
                               scan_images=False, stats_out=stats)

        d2 = pymupdf.open(dst)
        text = d2[0].get_text()
        d2.close()

        # 三条各考一种单位：英寸符号、两个单引号当英寸、磅
        for want, note in (("101.60mm", '4" → 101.60mm'),
                           ("38.10mm", "1-1/2'' → 38.10mm"),
                           ("11.34kg", "25 lb → 11.34kg")):
            check(f"换算 {note}", want in text)

        png = os.path.join(tmp, "preview.png")
        pdf_engine.render_comparison(dst, png)
        check("生成预览图", os.path.exists(png) and os.path.getsize(png) > 1000,
              f"{os.path.getsize(png) // 1024} KB" if os.path.exists(png) else "没生成")
    except Exception as exc:
        check("端到端流程", False, f"{type(exc).__name__}: {exc}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    head = "自检全部通过 —— 这个版本可以正常用。" if ok else \
           "自检发现问题，请把下面内容发给开发者。"
    report = head + "\n\n" + "\n".join(lines) + f"\n\n数据目录：{_DATA_DIR}"
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(os.path.join(_DATA_DIR, "selftest.log"), "w",
                  encoding="utf-8") as f:
            f.write(report + "\n")
    except Exception:
        pass
    _popup("PDF 图纸翻译 · 自检", report)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        sys.exit(0)
    try:
        enable_hidpi()
        _app = App()
        _app.win.report_callback_exception = lambda *a: _log_crash(*a)
        _app.run()
    except SystemExit:
        raise
    except Exception:
        _fatal("程序启动时出错，窗口无法打开。")
