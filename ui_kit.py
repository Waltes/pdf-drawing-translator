"""
界面工具箱 —— 让 Tkinter 长成 macOS 的样子。

Tkinter 原生控件是 90 年代的方角灰框，直接改配色也就那样。这里用四件事把它拉起来：

1. **无边框窗口**：overrideredirect + WS_EX_APPWINDOW + 清空 owner
   前两个只是去掉系统标题栏，第三个才是关键 —— 不把 owner 清掉，窗口不进任务栏，
   用户切走就再也找不回来。
2. **圆角**：Win32 的 SetWindowRgn 直接把窗口形状裁圆。Tk 自己画不了，
   因为窗口是矩形的，圆角必须由系统裁剪。
3. **毛玻璃**：未文档化的 SetWindowCompositionAttribute(亚克力)。
   实测在 Tk 窗口上有效，背景会透出后方窗口的模糊影像，文字控件仍然清晰。
4. **阴影与卡片**：PIL 生成带 alpha 的抗锯齿图贴到 Canvas 上。
   Tk 的 Canvas 不抗锯齿，硬画圆角和投影全是锯齿，只能用图片。

踩过的坑（别重复）：
- DwmExtendFrameIntoClientArea 会把整个窗口盖成白屏，Tk 的 GDI 绘制和它不兼容
- overrideredirect 窗口拿不到 DWM 投影
- 去掉 WS_CAPTION 保留 WS_THICKFRAME 也拿不到圆角，反而更难看
"""

import ctypes
from ctypes import wintypes

import tkinter as tk
from PIL import Image, ImageDraw, ImageFilter, ImageTk

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

IS_WIN = __import__("sys").platform == "win32"


# ===========================================================================
# 颜色工具
# ===========================================================================
def hex_to_rgb(s):
    """#RRGGBB → (r,g,b)"""
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    r, g, b = [max(0, min(255, int(v))) for v in rgb]
    return f"#{r:02x}{g:02x}{b:02x}"


def mix(c1, c2, t):
    """两色按比例混合，t=0 取 c1，t=1 取 c2"""
    a, b = hex_to_rgb(c1), hex_to_rgb(c2)
    return rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def lighten(c, t):
    return mix(c, "#ffffff", t)


def darken(c, t):
    return mix(c, "#000000", t)


# ===========================================================================
# 主题
# ===========================================================================
# ===========================================================================
# 主题配色方案
# ===========================================================================
# 一整套色板，而不是只换一个强调色。
#
# 为什么改成整套：之前只让用户挑强调色，结果除了按钮变个颜色，窗口底色、
# 卡片、描边全是写死的蓝灰 —— 用户点完觉得「主题色没跟着变」，这个反馈是对的。
# 配色必须成组给，选一套就是选一种气质，跟企业管理系统里的做法一致。
#
# 每套都带 on_accent（压在强调色上的文字色）。深色强调色配白字，
# 浅色强调色必须配近黑字，否则按钮上的字会糊成一团。
PALETTES = {
    "deep": {
        "label": "深空蓝", "bg": "#14161B", "card": "#232833",
        "alt": "#2B3140", "border": "#3D4557",
        "text": "#F4F6FA", "dim": "#A2AAB8", "faint": "#6E7686",
        "accent": "#4A9EFF", "on_accent": "#FFFFFF",
        "ok": "#3DDC84", "warn": "#FFB020", "err": "#FF5C5C", "shadow": 100,
    },
    "violet": {
        "label": "午夜紫", "bg": "#15121E", "card": "#231D31",
        "alt": "#2C2440", "border": "#463C5E",
        "text": "#F5F2FC", "dim": "#A9A1BC", "faint": "#756C89",
        "accent": "#A78BFA", "on_accent": "#161122",
        "ok": "#3DDC84", "warn": "#FFB020", "err": "#FF5C5C", "shadow": 110,
    },
    "cyan": {
        "label": "冰青", "bg": "#0E171A", "card": "#17262A",
        "alt": "#1F3238", "border": "#2E4C54",
        "text": "#EEF9FB", "dim": "#93AEB5", "faint": "#64828A",
        "accent": "#22D3EE", "on_accent": "#04222A",
        "ok": "#3DDC84", "warn": "#FBBF24", "err": "#FF6B6B", "shadow": 110,
    },
    "green": {
        "label": "墨松", "bg": "#101715", "card": "#1A2622",
        "alt": "#22332D", "border": "#33503F",
        "text": "#EFF7F3", "dim": "#96AFA5", "faint": "#657F75",
        "accent": "#34D399", "on_accent": "#06251A",
        "ok": "#34D399", "warn": "#FBBF24", "err": "#FF6B6B", "shadow": 110,
    },
    "amber": {
        "label": "暖沙", "bg": "#1A1613", "card": "#272019",
        "alt": "#332A20", "border": "#4C3F31",
        "text": "#FAF4EC", "dim": "#BDAF9D", "faint": "#877A68",
        "accent": "#FB923C", "on_accent": "#20150A",
        "ok": "#4ADE80", "warn": "#FBBF24", "err": "#F87171", "shadow": 100,
    },
    "graphite": {
        "label": "石墨灰", "bg": "#181819", "card": "#242427",
        "alt": "#2E2E32", "border": "#45454B",
        "text": "#F2F2F4", "dim": "#A6A6AE", "faint": "#75757E",
        "accent": "#A8B0BE", "on_accent": "#15161A",
        "ok": "#4ADE80", "warn": "#FBBF24", "err": "#F87171", "shadow": 100,
    },
    "light": {
        "label": "晨雾白", "bg": "#EDEFF3", "card": "#FFFFFF",
        "alt": "#F5F6F9", "border": "#D8DDE5",
        "text": "#161A21", "dim": "#4F5866", "faint": "#6F7887",
        "accent": "#2563EB", "on_accent": "#FFFFFF",
        "ok": "#12885B", "warn": "#B45309", "err": "#C02626", "shadow": 30,
    },
}

DEFAULT_PALETTE = "deep"

# 旧配置里只存了 accent，没有 palette。按颜色对到最接近的一套，避免升级后
# 用户原来挑的颜色被无声丢掉。（键统一存小写，查的时候也转小写）
_LEGACY_ACCENT = {
    "#4a9eff": "deep", "#a78bfa": "violet", "#22d3ee": "cyan",
    "#34d399": "green", "#fb923c": "amber", "#f472b6": "violet",
    "#f87171": "amber",
}

# 当前生效的配色（下面这些全局名会被 apply_palette 就地改写，
# 老代码里直接引用 GLASS_TINT / CARD_BG 的地方就能自动跟着变）
_P = PALETTES[DEFAULT_PALETTE]
GLASS_TINT = _P["bg"]          # 窗口底色（最底层）
CARD_BG = _P["card"]           # 卡片（内容区）
CARD_BG_ALT = _P["alt"]        # 输入框 / 列表 / 次级卡片
BORDER = _P["border"]          # 卡片描边
TEXT = _P["text"]
TEXT_DIM = _P["dim"]
TEXT_FAINT = _P["faint"]
OK = _P["ok"]
WARN = _P["warn"]
ERR = _P["err"]

GLASS_ENABLED = False        # 是否启用 DWM 真透视毛玻璃

# 中文显示效果好的字体，按优先级
FONT_CHOICES = [
    "Microsoft YaHei UI",
    "微软雅黑",
    "Microsoft YaHei",
    "思源黑体 CN",
    "Source Han Sans SC",
    "HarmonyOS Sans SC",
    "PingFang SC",
    "Segoe UI",
    "SimHei",
]
DEFAULT_FONT = FONT_CHOICES[0]

# 这些字体不承载正文：@ 开头是 CJK 竖排变体，选进去整窗文字会竖着排、
# 行高全乱；Wingdings 一类是符号字体，中文进去全是方块。
_BAD_FONT_PREFIX = ("@",)
_BAD_FONT_NAMES = {"wingdings", "wingdings 2", "wingdings 3", "webdings",
                   "marlett", "symbol", "ms outlook", "bookshelf symbol 7"}


def is_usable_font(name):
    """能不能拿来当界面字体"""
    if not name or not str(name).strip():
        return False
    n = str(name).strip()
    if n.startswith(_BAD_FONT_PREFIX):
        return False
    return n.lower() not in _BAD_FONT_NAMES


def clean_font(name):
    """把配错的字体名纠正回默认值。读配置时用，防止整个界面被搞成竖排"""
    return str(name).strip() if is_usable_font(name) else DEFAULT_FONT


def palette_globals(theme):
    """当前配色对应的全局变量表，app 那边用它同步自己的模块级名字"""
    return {
        "GLASS_TINT": theme.bg, "CARD_BG": theme.card, "CARD_BG_ALT": theme.card_alt,
        "BORDER": theme.border, "TEXT": theme.text, "TEXT_DIM": theme.text_dim,
        "TEXT_FAINT": theme.text_faint, "OK": theme.ok, "WARN": theme.warn,
        "ERR": theme.err,
    }


def apply_palette(theme):
    """把配色写进本模块的全局名。必须在重建控件之前调用"""
    globals().update(palette_globals(theme))
    clear_image_cache()


class Theme:
    """界面外观配置：配色方案、字体、字号、透明度。所有绘制都从这里取值"""

    def __init__(self, cfg=None):
        cfg = cfg or {}
        key = cfg.get("palette")
        if key not in PALETTES:
            # 老配置只有 accent，按颜色归到最近的一套
            key = _LEGACY_ACCENT.get(str(cfg.get("accent") or "").strip().lower(),
                                     DEFAULT_PALETTE)
        self.palette_key = key
        p = PALETTES[key]
        self.label = p["label"]
        self.bg = p["bg"]
        self.card = p["card"]
        self.card_alt = p["alt"]
        self.border = p["border"]
        self.text = p["text"]
        self.text_dim = p["dim"]
        self.text_faint = p["faint"]
        self.accent = p["accent"]
        self.on_accent = p["on_accent"]
        self.ok, self.warn, self.err = p["ok"], p["warn"], p["err"]
        self.shadow_alpha = p["shadow"]
        self.is_light = key == "light"

        # 字体名必须过滤：@ 开头的是竖排变体，写进配置会让整个界面竖排
        self.font_family = clean_font(cfg.get("font"))
        try:
            self.font_size = max(8, min(16, int(cfg.get("font_size") or 10)))
        except Exception:
            self.font_size = 10
        try:
            self.opacity = max(0.70, min(1.0, float(cfg.get("opacity") or 1.0)))
        except Exception:
            self.opacity = 1.0
        self.scale = 1.0        # DPI 缩放，由 init_scale 填充

    # ---------------- 派生色 ----------------
    @property
    def accent_hover(self):
        return lighten(self.accent, 0.16)

    @property
    def accent_press(self):
        return darken(self.accent, 0.14)

    @property
    def accent_soft(self):
        """低饱和版本，用于选中的列表项背景"""
        return mix(self.card, self.accent, 0.22)

    @property
    def shadow_rgba(self):
        """浅色底上的投影要用灰而不是纯黑，纯黑会脏"""
        return (0, 0, 0, self.shadow_alpha)

    # ---------------- 尺寸与字体 ----------------
    def px(self, n):
        """96dpi 设计值 → 当前屏幕物理像素"""
        return int(round(n * self.scale))

    def font(self, size=None, bold=False):
        """字号按用户设置缩放，bold 用 weight 描述符"""
        base = self.font_size if size is None else size
        return (self.font_family, base, "bold") if bold else (self.font_family, base)

    def init_scale(self, root):
        try:
            self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        except Exception:
            self.scale = 1.0
        return self.scale



# ===========================================================================
# 图片绘制（带缓存）
# ===========================================================================
_img_cache = {}
# 投影蒙版缓存：跟配色无关，换配色时不清，靠它把重绘时间从 900ms 压到 200ms 级
_shadow_cache = {}


def _cache_key(*args):
    return args


def _shadow_mask(W, H, x0, y0, bw, bh, radius, blur, offset):
    """
    模糊后的投影蒙版（灰度图）。

    关键点：投影只跟尺寸、圆角、模糊半径有关，**跟配色无关**。
    高斯模糊是整个绘制里最贵的一步（一张 1700×470 的卡片要 50ms 左右），
    每换一次配色就把所有卡片重新模糊一遍，光这一项就要 600ms。
    所以把模糊结果单独缓存，换配色时只往上面套不同的透明度。

    缓存按尺寸分组，窗口缩放会不断产生新 key，加个上限兜住内存。
    """
    key = (W, H, x0, y0, bw, bh, radius, blur, offset)
    m = _shadow_cache.get(key)
    if m is None:
        sh = Image.new("L", (W, H), 0)
        ImageDraw.Draw(sh).rounded_rectangle(
            [x0, y0 + offset, x0 + bw, y0 + bh + offset], radius=radius, fill=255)
        m = sh.filter(ImageFilter.GaussianBlur(blur))
        if len(_shadow_cache) > 80:
            _shadow_cache.clear()
        _shadow_cache[key] = m
    return m


def rounded_rect_image(w, h, radius, fill, outline=None, outline_w=1,
                       shadow=0, shadow_alpha=90, shadow_offset=0, scale=2):
    """
    生成圆角矩形（可选带投影）的 RGBA 图。

    先按 scale 倍绘制再缩小，等于做了超采样，边缘抗锯齿比直接画细得多。
    shadow 为投影模糊半径（0 表示不画），shadow_offset 让投影略微下沉，
    更接近真实光照。
    """
    key = _cache_key("rr", w, h, radius, fill, outline, outline_w,
                     shadow, shadow_alpha, shadow_offset, scale)
    if key in _img_cache:
        return _img_cache[key]

    pad = int((shadow * 2.2 + shadow_offset) * scale) if shadow else 0
    W, H = (w * scale + pad * 2), (h * scale + pad * 2)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    w2, h2, r2 = w * scale, h * scale, radius * scale
    x0, y0 = pad, pad

    if shadow:
        mask = _shadow_mask(W, H, x0, y0, w2, h2, r2,
                            min(shadow * scale * 0.55, 12), int(shadow_offset * scale))
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        # 灰度蒙版乘上这套配色自己的透明度。point 内部走 256 项查找表，
        # 比重新高斯模糊快两个数量级
        sh.putalpha(mask.point(lambda v: v * shadow_alpha // 255))
        img.alpha_composite(sh)

    body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(body)
    fill_rgb = hex_to_rgb(fill) if isinstance(fill, str) else fill
    d.rounded_rectangle([x0, y0, x0 + w2, y0 + h2], radius=r2, fill=fill_rgb,
                        outline=(hex_to_rgb(outline) + (255,)) if outline else None,
                        width=int(outline_w * scale) if outline else 0)
    img.alpha_composite(body)

    if scale != 1:
        # 用 BOX 而不是 LANCZOS：整数倍缩小时 BOX 就是精确的 n×n 均值，
        # 抗锯齿效果一样，但没有 LANCZOS 的卷积开销，大面板快一倍
        img = img.resize((W // scale, H // scale), Image.BOX)

    photo = ImageTk.PhotoImage(img)
    _img_cache[key] = (photo, pad // scale)
    return _img_cache[key]


def shadow_image(w, h, radius, blur=20, alpha=120, scale=2):
    """只生成投影（透明面板用），返回 (PhotoImage, 内边距)"""
    key = _cache_key("sh", w, h, radius, blur, alpha, scale)
    if key in _img_cache:
        return _img_cache[key]
    pad = int(blur * 2.2 * scale)
    W, H = w * scale + pad * 2, h * scale + pad * 2
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([pad, pad + int(blur * 0.3 * scale),
                         pad + w * scale, pad + h * scale + int(blur * 0.3 * scale)],
                        radius=radius * scale, fill=(0, 0, 0, alpha))
    img = img.filter(ImageFilter.GaussianBlur(blur * scale * 0.55))
    img = img.resize((W // scale, H // scale), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    _img_cache[key] = (photo, pad // scale)
    return _img_cache[key]


def clear_image_cache():
    """换主题色或字号后要清缓存，否则旧颜色的图还在用"""
    _img_cache.clear()


def palette_preview_image(pal, w, h, radius=10, scale=2):
    """
    画一块「迷你界面」当配色样本：底色 + 一张卡片 + 一根强调色按钮 + 两行灰条。
    设置里选配色时用它，用户看到的是这套颜色用起来的样子，不是一颗孤零零的色点。
    pal 直接传 PALETTES 里的一项，不依赖当前生效的主题，所以能一次画七套。
    """
    key = _cache_key("pal", pal["label"], w, h, radius, scale)
    if key in _img_cache:
        return _img_cache[key]

    W, H = w * scale, h * scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = radius * scale

    # 窗口底色
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=r, fill=pal["bg"])
    # 卡片
    m = int(W * 0.10)
    ch = int(H * 0.52)
    d.rounded_rectangle([m, m, W - m, m + ch], radius=int(r * 0.55),
                        fill=pal["card"], outline=pal["border"], width=scale)
    # 卡片里的两行「文字」
    lh = max(2, int(H * 0.055))
    d.rounded_rectangle([m + int(W * 0.06), m + int(ch * 0.22),
                         W - m - int(W * 0.30), m + int(ch * 0.22) + lh],
                        radius=lh // 2, fill=pal["dim"])
    d.rounded_rectangle([m + int(W * 0.06), m + int(ch * 0.54),
                         W - m - int(W * 0.46), m + int(ch * 0.54) + lh],
                        radius=lh // 2, fill=pal["faint"])
    # 卡片下面的强调色按钮
    by = m + ch + int(H * 0.09)
    bh = max(4, int(H * 0.13))
    d.rounded_rectangle([m, by, W - m, min(by + bh, H - m)],
                        radius=bh // 2, fill=pal["accent"])

    img = img.resize((w, h), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    _img_cache[key] = (photo, 0)
    return _img_cache[key]



# ===========================================================================
# 窗口外壳
# ===========================================================================
class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int)]


class _WCAD(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(ctypes.c_int)),
                ("SizeOfData", ctypes.c_size_t)]


def apply_acrylic(hwnd, tint=None, alpha=0xF2):
    """
    给窗口加亚克力毛玻璃。

    alpha 是关键参数，实测踩过坑：给低了（0xE8 以下）DWM 会把窗口里
    所有不透明填充一起冲淡，卡片变成半透明、深色底变灰，文字对比度跟不住。
    0xF2 以上基本只剩一点点环境色透过来，既保留玻璃感又不伤可读性。
    """
    if not IS_WIN:
        return False
    try:
        rgb = hex_to_rgb(tint or GLASS_TINT)
        # GradientColor 是 AABBGGRR，注意和 RGB 顺序相反
        grad = (alpha << 24) | (rgb[2] << 16) | (rgb[1] << 8) | rgb[0]
        accent = _ACCENT_POLICY(4, 0, grad, 0)   # 4 = ACCENT_ENABLE_ACRYLICBLURBEHIND
        data = _WCAD(19, ctypes.cast(ctypes.pointer(accent), ctypes.POINTER(ctypes.c_int)),
                     ctypes.sizeof(accent))
        fn = user32.SetWindowCompositionAttribute
        fn.argtypes = [wintypes.HWND, ctypes.POINTER(_WCAD)]
        return bool(fn(hwnd, ctypes.byref(data)))
    except Exception:
        return False


def apply_round_region(hwnd, w, h, radius):
    """把窗口裁成圆角矩形（radius=0 取消裁剪）"""
    if not IS_WIN:
        return
    try:
        if radius <= 0:
            user32.SetWindowRgn(hwnd, 0, True)
        else:
            rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius * 2, radius * 2)
            user32.SetWindowRgn(hwnd, rgn, True)
    except Exception:
        pass


class GlassWindow:
    """
    无边框窗口外壳：圆角 + 毛玻璃 + 三色按钮 + 拖动 + 八向缩放。

    子类只需要实现 build()，用 self.canvas / self.content 往里面放东西。
    """

    RADIUS = 18          # 窗口圆角（96dpi 设计值）
    MARGIN = 0           # 窗口边距（阴影画在这里）

    def __init__(self, master=None, theme=None, size=(880, 900), resizable=True,
                 root_cls=None, icon_path=None):
        self.theme = theme or Theme()
        self._icon_path = icon_path      # .ico 绝对路径，None 就用系统默认图标
        # root_cls 允许外部指定窗口类：tkinterdnd2 的拖拽功能要求根窗口由
        # TkinterDnD.Tk 创建，用普通 tk.Tk 会报 invalid command name "tkdnd::drop_target"
        if master is None:
            self._tk = (root_cls or tk.Tk)()
        else:
            self._tk = tk.Toplevel(master)
        self.win = self._tk
        self._resizable = resizable
        self._maximized = False
        self._restore_geo = None
        self._drag_from = None
        self._resize_mode = None
        self._last_shape_size = None
        self._shape_job = None
        # 标题栏占掉的高度。这一条不当缩放边：它整条都是拖动区，
        # 若还兼作「上边缘缩放」，用户想拖窗口会变成拉窗口高度。
        self._title_band = 0

        self.win.overrideredirect(True)
        self.win.configure(bg=GLASS_TINT)

        # 必须先算 DPI 缩放，后面所有 px() 都依赖它，顺序错了窗口会被压扁
        self.theme.init_scale(self.win)

        # 按设计尺寸摆好，再套系统特效
        self._design_w, self._design_h = size
        w, h = self.theme.px(size[0]), self.theme.px(size[1])
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        w = min(w, sw - self.theme.px(40))
        h = min(h, sh - self.theme.px(90))
        self._size = (w, h)
        x, y = (sw - w) // 2, max(0, (sh - h) // 3)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.update_idletasks()
        self.win.update()

        self.hwnd = self._hwnd()
        self._setup_win32()

        # 内容容器。不铺一层全窗 Canvas —— 那样每张卡片都得手动摆坐标；
        # 用 Frame + pack 更省事，玻璃背景由窗口自身透出来。
        self.body = tk.Frame(self.win, bg=GLASS_TINT)
        self.body.pack(fill="both", expand=True)

        self._bind_resize()
        self.win.bind("<Configure>", self._on_configure)

    # ---------------- Win32 ----------------
    def _hwnd(self):
        try:
            return user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
        except Exception:
            return self.win.winfo_id()

    def _setup_win32(self):
        if not IS_WIN:
            return
        GWL_EXSTYLE = -20
        GWLP_HWNDPARENT = -8
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080
        try:
            ex = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                                  (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW)
            # 清 owner，否则窗口不进任务栏（切走就找不回来）
            user32.SetWindowLongW(self.hwnd, GWLP_HWNDPARENT, 0)
        except Exception:
            pass

        if GLASS_ENABLED:
            apply_acrylic(self.hwnd, GLASS_TINT)
        self._apply_shape()
        self._apply_icon()
        # 重新显示一次，任务栏才会刷新出图标
        try:
            user32.ShowWindow(self.hwnd, 0)
            user32.ShowWindow(self.hwnd, 5)
            user32.SetForegroundWindow(self.hwnd)
        except Exception:
            pass

    def _apply_icon(self):
        """
        用 Win32 直接给窗口挂图标。

        为什么不用 Tk 自带的两个接口（都实测失败过）：
        - wm iconphoto：Tk 在 Windows 上把 PhotoImage 转 HICON 时丢 alpha，
          圆角外的透明区域变成实心方块，深蓝底会糊成一整块白。
        - wm iconbitmap：对 overrideredirect 窗口不生效，wm 查询返回空串，
          任务栏于是退回 Windows 的「通用程序」灰蓝占位图标。

        LoadImage 直接读 .ico（保留每帧 alpha），WM_SETICON 又是任务栏、
        Alt+Tab、Alt+Tab 预览三处读图标的唯一来源，一步到位。

        ICON_BIG 按当前 DPI 取 64/32，避免高分屏拿 32px 帧放大发虚。
        """
        if not IS_WIN or not self._icon_path:
            return
        import os
        if not os.path.exists(self._icon_path):
            return
        try:
            LR_LOADFROMFILE = 0x0010
            IMAGE_ICON = 1
            WM_SETICON = 0x0080
            ICON_SMALL, ICON_BIG = 0, 1

            load = ctypes.windll.user32.LoadImageW
            load.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_uint,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            load.restype = ctypes.c_void_p
            send = ctypes.windll.user32.SendMessageW
            send.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_ssize_t]
            send.restype = ctypes.c_ssize_t

            big = max(32, self.theme.px(32))
            small = max(16, self.theme.px(16))
            self._hicons = []          # 句柄必须留着，否则被系统回收后又变回默认图标
            for idx, side in ((ICON_BIG, big), (ICON_SMALL, small)):
                h = load(None, self._icon_path, IMAGE_ICON, side, side, LR_LOADFROMFILE)
                if h:
                    self._hicons.append(h)
                    send(self.hwnd, WM_SETICON, idx, h)
        except Exception:
            pass

    def _apply_shape(self):
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        if w < 2 or h < 2:
            return
        apply_round_region(self.hwnd, w, h, self.theme.px(self.RADIUS))

    def _on_configure(self, event=None):
        """
        窗口尺寸变了就重设圆角区域。

        必须做两件事，否则会把主循环拖死：
        1. 尺寸没变就跳过 —— SetWindowRgn 本身会触发一次窗口尺寸消息，
           回调里再调用它就成了死循环，界面一片空白、连 after 都不执行
        2. 变化时延迟 60ms 合并 —— 拖边缘缩放时每像素都会触发 Configure
        """
        if event is not None and event.widget is not self.win:
            return
        size = (self.win.winfo_width(), self.win.winfo_height())
        if size == self._last_shape_size:
            return
        self._last_shape_size = size
        try:
            if self._shape_job:
                self.win.after_cancel(self._shape_job)
        except Exception:
            pass
        self._shape_job = self.win.after(60, self._apply_shape)

    # ---------------- 拖动 ----------------
    def start_drag(self, e):
        """
        开始拖动窗口。由标题栏空白区调用，不绑在整窗上 ——
        否则点卡片、拖列表都会把窗口拽走。
        """
        if self._maximized or self._resize_mode:
            return
        self._drag_from = (e.x_root, e.y_root,
                           self.win.winfo_x(), self.win.winfo_y())

    def drag_move(self, e):
        if not self._drag_from or self._maximized:
            return
        x0, y0, wx, wy = self._drag_from
        self.win.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    # ---------------- 缩放 ----------------
    def set_title_band(self, h):
        """告诉窗口「顶部这么高是标题栏」。由 TitleBar 建好时自己登记"""
        self._title_band = h or 0

    def _bind_resize(self):
        """八向缩放：靠近边缘时改光标，按住拖动改尺寸"""
        if not self._resizable:
            return
        self.win.bind("<Motion>", self._hover_edge, add="+")
        self.win.bind("<ButtonPress-1>", self._resize_start, add="+")
        self.win.bind("<B1-Motion>", self._resize_move, add="+")
        # 松手必须收尾。原来没有这一条，_resize_mode 一旦被置上就再也不清，
        # 后果是：之后悬停永远不再更新光标（一直挂着 ↕），
        # 每次按鼠标也都还按上一次的方向缩放，窗口就再也拖不动了。
        self.win.bind("<ButtonRelease-1>", self._resize_end, add="+")

    EDGE = 8   # 边缘热区宽度（设计值）

    def _win_xy(self, e):
        """
        把事件坐标换算成「相对窗口」的坐标。

        必须换算：事件可能来自任意子控件，而 e.x / e.y 是那个控件的局部坐标。
        标题栏贴在窗口最上面，它的 e.y 天生就是十几像素，直接拿去和窗口比，
        每次都会被判成「上边缘」—— 于是光标变 ↕、按下变成拉高度。
        用屏幕坐标减窗口原点，不管事件从哪个控件冒上来都一致。
        """
        rx, ry = getattr(e, "x_root", None), getattr(e, "y_root", None)
        if rx is None or ry is None:
            return e.x, e.y
        try:
            return (rx - self.win.winfo_rootx(), ry - self.win.winfo_rooty())
        except Exception:
            return e.x, e.y

    def _edge_at(self, x, y):
        w, h = self.win.winfo_width(), self.win.winfo_height()
        e = self.theme.px(self.EDGE)
        L, R, T, B = x <= e, x >= w - e, y <= e, y >= h - e
        # 标题栏那一条只做拖动。否则窗口最上面那 8px 永远是缩放边，
        # 想抓标题栏很容易抓偏，横向一拖发现窗口不动、反而变高了。
        if y <= self._title_band:
            T = False
        if T and L: return "nw"
        if T and R: return "ne"
        if B and L: return "sw"
        if B and R: return "se"
        if T: return "n"
        if B: return "s"
        if L: return "w"
        if R: return "e"
        return None

    _CURSORS = {"nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw",
                "sw": "size_ne_sw", "n": "size_ns", "s": "size_ns",
                "w": "size_we", "e": "size_we"}

    def _hover_edge(self, e):
        if self._maximized:
            return
        # 正在拖缩放时别抢光标；松手后 _resize_end 会清掉状态，
        # 所以这里不会像原来那样一卡到底
        if self._resize_mode:
            return
        mode = self._edge_at(*self._win_xy(e))
        try:
            self.win.config(cursor=self._CURSORS.get(mode, ""))
        except Exception:
            pass

    def _resize_start(self, e):
        mode = self._edge_at(*self._win_xy(e))
        if not mode or self._maximized:
            return
        # 真按在边上了，才算缩放。同时把标题栏刚才启动的拖动取消掉，
        # 否则两个动作会同时改窗口几何，位置和尺寸互相打架。
        self._drag_from = None
        self._resize_mode = mode
        self._resize_from = (e.x_root, e.y_root, self.win.winfo_width(),
                             self.win.winfo_height(), self.win.winfo_x(),
                             self.win.winfo_y())

    def _resize_end(self, e=None):
        self._resize_mode = None
        self._resize_from = None
        try:
            self.win.config(cursor="")
        except Exception:
            pass

    def _resize_move(self, e):
        if not self._resize_mode or not self._resize_from:
            return
        x0, y0, w0, h0, wx, wy = self._resize_from
        dx, dy = e.x_root - x0, e.y_root - y0
        minw, minh = self.theme.px(640), self.theme.px(480)
        m = self._resize_mode
        w, h, x, y = w0, h0, wx, wy
        if "e" in m:
            w = max(minw, w0 + dx)
        if "s" in m:
            h = max(minh, h0 + dy)
        if "w" in m:
            w = max(minw, w0 - dx)
            x = wx + (w0 - w)
        if "n" in m:
            h = max(minh, h0 - dy)
            y = wy + (h0 - h)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    # ---------------- 窗口按钮 ----------------
    def minimize(self):
        """真最小化。窗口已加入任务栏，点任务栏图标可还原"""
        if not IS_WIN:
            self.win.iconify()
            return
        try:
            user32.ShowWindow(self.hwnd, 6)     # SW_MINIMIZE
        except Exception:
            pass

    def toggle_max(self):
        """最大化 / 还原"""
        if self._maximized:
            if self._restore_geo:
                self.win.geometry(self._restore_geo)
            self._maximized = False
        else:
            self._restore_geo = self.win.geometry()
            sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
            self.win.geometry(f"{sw}x{sh - self.theme.px(60)}+0+0")
            self._maximized = True
        # 最大化后圆角会显得怪，收缩一点
        self.win.after(30, self._apply_shape)

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


# ===========================================================================
# 控件
# ===========================================================================
class RoundButton(tk.Canvas):
    """圆角按钮。底色用 PIL 画（抗锯齿），文字用 Tk 画（字体清晰）"""

    def __init__(self, master, theme, text, command=None, kind="primary",
                 width=None, height=34, font_size=None, radius=None, bg=None):
        self.theme = theme
        self.kind = kind
        self._command = command
        self._enabled = True
        self._bhover = False
        self._pressed = False

        f = theme.font(font_size, bold=(kind == "primary"))
        # 宽度按文字估算，避免中文被裁
        if width is None:
            width = max(88, len(text) * (theme.font_size + 4) + 34)
        self._bw, self._bh = theme.px(width), theme.px(height)
        self._radius = theme.px(radius if radius is not None else height // 2)

        # 按钮的 Canvas 本身是矩形，底色必须跟父容器一致，否则圆角外面会
        # 露出一圈方角的实色块（深色背景上尤其明显）。默认向父容器取色。
        if bg is None:
            try:
                bg = master.cget("bg")
            except Exception:
                bg = CARD_BG
        self._bg = bg

        super().__init__(master, width=self._bw, height=self._bh,
                         highlightthickness=0, bd=0, bg=bg,
                         takefocus=True, cursor="hand2")
        self._img_id = self.create_image(0, 0, anchor="nw")
        self._txt_id = self.create_text(self._bw // 2, self._bh // 2, text=text,
                                        fill=theme.on_accent, font=f)
        self._focused = False
        self._redraw()

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        # 键盘可达性：Canvas 没有原生按钮行为，Tab 聚焦、回车/空格触发都得自己接
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<Return>", lambda e: self._fire())
        self.bind("<space>", lambda e: self._fire())

    def _set_focus(self, on):
        self._focused = on
        self._redraw()

    def _fire(self):
        if self._enabled and self._command:
            self._command()

    def _colors(self):
        """按状态和类型决定底色与文字色"""
        t = self.theme
        if not self._enabled:
            return (mix(t.card, t.text, 0.06), t.text_faint)
        if self.kind == "primary":
            bg = t.accent
            if self._pressed:
                bg = t.accent_press
            elif self._bhover:
                bg = t.accent_hover
            return (bg, t.on_accent)
        if self.kind == "danger":
            bg = t.err if (self._bhover or self._pressed) else mix(t.card, t.err, 0.18)
            return (bg, "#ffffff" if self._bhover else t.err)
        # ghost：透明感按钮。底色由所在容器和文字色推导 —— 深色主题下 text 是
        # 近白，混上去变亮；浅色主题下 text 是近黑，混上去变暗。两边都对。
        base = self._bg
        amt = 0.12 if (self._bhover or self._pressed) else 0.06
        bg = mix(base, t.text, amt)
        return (bg, t.text if (self._bhover or self._pressed) else t.text_dim)

    def _redraw(self):
        bg, fg = self._colors()
        t = self.theme
        # 有焦点时描一圈强调色 —— 没有焦点环，键盘用户根本不知道选中了哪个
        outline = t.accent if self._focused else (t.border if self.kind == "ghost" else None)
        photo, pad = rounded_rect_image(self._bw, self._bh, self._radius, bg,
                                        outline=outline,
                                        outline_w=2 if self._focused else 1, scale=2)
        self.itemconfig(self._img_id, image=photo)
        self._photo = photo
        self.itemconfig(self._txt_id, fill=fg)
        self.coords(self._img_id, -pad, -pad + 0)

    def _on_enter(self, e):
        self._bhover = True
        self._redraw()

    def _on_leave(self, e):
        self._bhover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, e):
        if not self._enabled:
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, e):
        if not self._enabled:
            return
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was and self._command:
            self._command()

    def set_enabled(self, on):
        self._enabled = bool(on)
        self._redraw()

    def set_kind(self, kind):
        """换按钮类型（分页签用它表示当前选中项）"""
        if kind == self.kind:
            return
        self.kind = kind
        self._redraw()

    def set_text(self, text):
        self.itemconfig(self._txt_id, text=text)


class SelectBox(tk.Canvas):
    """
    自绘下拉框（替代 tk.OptionMenu）。

    为什么不用原生 OptionMenu：它在深色底上右侧那个箭头指示器由系统主题绘制，
    配上自定义字体后经常渲染成一个「⊟」方块，看着像控件坏了。而且菜单是独立
    Toplevel，配色要单独设一遍，默认是系统亮色。

    这里自己画：圆角底 + 左对齐文字 + 右端一个 ▾ 字符（用字体里的字符，
    不依赖系统位图），点一下弹出同样配色的菜单。
    """

    def __init__(self, master, theme, items=None, value=None, command=None,
                 height=34, bg=None, pad=None):
        self.theme = theme
        self._items = list(items or [])      # [(key, label), ...]
        self._value = value if value is not None else (
            self._items[0][0] if self._items else None)
        self._command = command
        self._hover = False
        self._bh = theme.px(height)
        self._pad = theme.px(pad if pad is not None else 10)

        if bg is None:
            try:
                bg = master.cget("bg")
            except Exception:
                bg = CARD_BG
        self._bg = bg

        super().__init__(master, height=self._bh, highlightthickness=0, bd=0,
                         bg=bg, takefocus=True, cursor="hand2")
        self._img_id = self.create_image(0, 0, anchor="nw")
        self._txt_id = self.create_text(self._pad, self._bh // 2, anchor="w",
                                        text="", fill=TEXT,
                                        font=theme.font(theme.font_size))
        self._chev_id = self.create_text(0, self._bh // 2, anchor="e",
                                         text="▾", fill=TEXT_DIM,
                                         font=theme.font(theme.font_size))
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self._popup())
        self.bind("<space>", lambda e: self._popup())
        self.bind("<Return>", lambda e: self._popup())
        self._redraw()

    # ---------------- 取值 ----------------
    def get(self):
        return self._value

    def label(self):
        for k, v in self._items:
            if k == self._value:
                return v
        return ""

    def set_value(self, key):
        self._value = key
        self._redraw()

    def set_items(self, items):
        """换候选列表；当前值不在新列表里就落到第一项"""
        self._items = list(items or [])
        if self._value not in [k for k, _ in self._items]:
            self._value = self._items[0][0] if self._items else None
        self._redraw()

    # ---------------- 绘制 ----------------
    def _set_hover(self, on):
        if self._hover == on:
            return
        self._hover = on
        self._redraw()

    def _redraw(self):
        w = max(self.winfo_width(), 60)
        h = self._bh
        # 底色跟旁边的输入框取同一个值（CARD_BG_ALT），否则一行里深一块浅一块；
        # 圆角也压到 6 像素，和方角输入框并排时不显得突兀
        fill = mix(CARD_BG_ALT, "#ffffff", 0.05) if self._hover else CARD_BG_ALT
        photo, pad = rounded_rect_image(
            w, h, self.theme.px(6), fill,
            outline=self.theme.accent if self._hover else BORDER,
            outline_w=1, scale=2)
        self.itemconfig(self._img_id, image=photo)
        self._photo = photo
        self.coords(self._img_id, -pad, -pad)
        self.coords(self._txt_id, self._pad, h // 2)
        self.itemconfig(self._txt_id, text=self.label())
        self.coords(self._chev_id, w - self._pad, h // 2)
        self.itemconfig(self._chev_id,
                        fill=TEXT_DIM if self._hover else TEXT_FAINT)

    # ---------------- 弹出菜单 ----------------
    def _popup(self):
        if not self._items:
            return
        t = self.theme
        m = tk.Menu(self, tearoff=0, bg=CARD_BG_ALT, fg=TEXT,
                    activebackground=t.accent, activeforeground=t.on_accent,
                    bd=0, relief="flat", font=t.font(t.font_size))
        for key, label in self._items:
            mark = "✓ " if key == self._value else "   "
            m.add_command(label=mark + label,
                          command=lambda k=key: self._choose(k))
        try:
            m.tk_popup(self.winfo_rootx(),
                       self.winfo_rooty() + self._bh)
        finally:
            # 窗口在菜单还开着时被销毁的话，grab_release 会抛 TclError，
            # 不能让它冒到事件循环里去
            try:
                m.grab_release()
            except Exception:
                pass

    def _choose(self, key):
        self._value = key
        self._redraw()
        if self._command:
            self._command(key)


class Card(tk.Canvas):
    """
    圆角卡片：带投影的实色面板，内容放进 self.body。

    为什么内部用实色而不是半透明：卡片里是正文和列表，压在模糊背景上
    会糊成一片，读起来累。玻璃感交给窗口背景，卡片负责清楚。
    """

    def __init__(self, master, theme, radius=16, fill=None,
                 shadow=14, shadow_alpha=None, padding=16, bg=None,
                 height=None):
        self.theme = theme
        self._radius_cfg = radius
        # fill / bg 不能写成默认参数：默认值在函数定义时就求值了，
        # 换配色之后这里还捏着旧颜色，整块卡片不会跟着变
        self._fill = fill or theme.card
        self._bgcol = bg or theme.bg
        self._shadow = shadow
        self._shadow_alpha = (theme.shadow_alpha if shadow_alpha is None
                              else shadow_alpha)
        self._padding = padding
        kw = {"height": theme.px(height)} if height else {}
        super().__init__(master, highlightthickness=0, bd=0, bg=self._bgcol, **kw)
        self._img_id = self.create_image(0, 0, anchor="nw")
        self.body = tk.Frame(self, bg=self._fill)
        self._win_id = self.create_window(0, 0, anchor="nw", window=self.body)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        photo, pad = rounded_rect_image(w, h, self.theme.px(self._radius_cfg),
                                        self._fill, outline=self.theme.border,
                                        outline_w=1,
                                        shadow=self.theme.px(self._shadow),
                                        shadow_alpha=self._shadow_alpha, scale=2)
        self.itemconfig(self._img_id, image=photo)
        self._photo = photo
        # 图比控件大一圈（阴影），所以要偏移，让卡片本体贴住控件边缘
        self.coords(self._img_id, -pad, -pad)
        p = self.theme.px(self._padding)
        self.coords(self._win_id, p, p)
        self.itemconfig(self._win_id, width=max(1, w - p * 2), height=max(1, h - p * 2))


class TitleBar(tk.Canvas):
    """自绘标题栏：左侧三色圆点（关闭/最小化/最大化）+ 中间「图标 + 标题」+ 右侧设置入口"""

    def __init__(self, master, theme, window, title="", subtitle="",
                 height=52, on_settings=None, icon_path=None):
        self.theme = theme
        self.window = window
        self._on_settings = on_settings
        self._icon_path = icon_path
        self._icon_photo = None
        self._icon_id = None
        self._h = theme.px(height)
        super().__init__(master, height=self._h, highlightthickness=0, bd=0, bg=GLASS_TINT)
        # 登记「顶部这一条是标题栏」。窗口拿它把上边缘的缩放判定让开 ——
        # 否则标题栏最上面几个像素永远是缩放边，抓标题栏会抓到缩放
        try:
            window.set_title_band(self._h)
        except Exception:
            pass
        self._title = title
        self._subtitle = subtitle
        self._dots = []
        self._hot = None
        self._build()
        self.bind("<Configure>", lambda e: self._redraw())
        # 首帧 Configure 不一定在窗口映射前到达，先排一次，免得标题停在初始位置
        self.after_idle(self._redraw)

    def _build(self):
        t = self.theme
        r = t.px(6)
        gap = t.px(20)
        x0 = t.px(18)
        y = self._h // 2
        self._dots = []
        # 三色按钮：红关闭、黄最小化、绿最大化
        for i, (color, name) in enumerate([("#FF5F57", "close"),
                                           ("#FEBC2E", "min"),
                                           ("#28C840", "max")]):
            cx = x0 + i * gap
            self.create_oval(cx - r, y - r, cx + r, y + r, fill=color, outline="")
            # 符号一次画好、平时隐藏，悬停只切 state。
            #
            # 绝对不要在 <Enter> 里 create/delete 符号：新画的线正好压在指针底下，
            # 画布立刻判定「指针离开圆点」→ 删掉符号 → 指针又回到圆点 → 再画一次……
            # 这个循环会把 CPU 跑满，整个程序直接卡死。改成预画 + 切显隐，
            # 悬停期间没有任何图元增减，循环就没有产生的条件。
            items = []
            s = t.px(3)
            col = darken(color, 0.55)
            lw = t.px(1.4)
            if name == "close":
                items.append(self.create_line(cx - s, y - s, cx + s, y + s,
                                              fill=col, width=lw, capstyle="round"))
                items.append(self.create_line(cx - s, y + s, cx + s, y - s,
                                              fill=col, width=lw, capstyle="round"))
            elif name == "min":
                items.append(self.create_line(cx - s, y, cx + s, y,
                                              fill=col, width=lw, capstyle="round"))
            else:
                items.append(self.create_line(cx - s, y, cx + s, y,
                                              fill=col, width=lw, capstyle="round"))
                items.append(self.create_line(cx, y - s, cx, y + s,
                                              fill=col, width=lw, capstyle="round"))
            for it in items:
                self.itemconfig(it, state="hidden")
            self._dots.append((name, cx, y, items))

        # 命中半径比圆本身大一圈，肉眼看着小、点起来才不费劲
        self._hit_r = r + t.px(6)

        # 空白区可拖动窗口。必须排除按钮区，否则拖标题栏会顺带点到按钮
        self._drag_limit = x0 + 2 * gap + r + t.px(10)

        # 标题和图标先建出来，具体位置由 _redraw 统一算成「整体居中」
        self._title_id = self.create_text(
            self._drag_limit, y, text=self._title, anchor="w",
            fill=TEXT_DIM, font=t.font(t.font_size, bold=False))
        self._load_icon()

        self._dragging = False

        # 右上角设置入口。用文字不用齿轮符号：不同字体对符号的渲染差别太大
        self._set_id = None
        if self._on_settings:
            self._set_id = self.create_text(0, y, text="设置", anchor="e",
                                            fill=TEXT_DIM,
                                            font=t.font(t.font_size, bold=False))

        # 统一在画布层面处理悬停与点击：图元不再挂 Enter/Leave/Button，
        # 少一层「指针到底在哪个图元上」的判定，也就不会自己触发自己
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hot(None))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_dragging", False))

    # ---------------- 命中判定 ----------------
    def _dot_at(self, x, y):
        """指针落在哪个圆点上，没有就返回 None"""
        for name, cx, cy, _items in self._dots:
            if abs(x - cx) <= self._hit_r and abs(y - cy) <= self._hit_r:
                return name
        return None

    def _over_settings(self, x, y):
        if self._set_id is None:
            return False
        bb = self.bbox(self._set_id)
        if not bb:
            return False
        pad = self.theme.px(6)
        return (bb[0] - pad <= x <= bb[2] + pad
                and bb[1] - pad <= y <= bb[3] + pad)

    def _set_hot(self, name):
        """切换悬停圆点：只改已有图元的显隐，不新建也不删除"""
        if name == self._hot:
            return
        self._hot = name
        for n, _cx, _cy, items in self._dots:
            state = "normal" if n == name else "hidden"
            for it in items:
                self.itemconfig(it, state=state)

    # ---------------- 事件 ----------------
    def _on_motion(self, e):
        self._set_hot(self._dot_at(e.x, e.y))
        if self._set_id is not None:
            over = self._over_settings(e.x, e.y)
            self.itemconfig(self._set_id, fill=TEXT if over else TEXT_DIM)

    def _on_press(self, e):
        name = self._dot_at(e.x, e.y)
        if name:
            self._click(name)
            return
        if self._over_settings(e.x, e.y):
            if self._on_settings:
                self._on_settings()
            return
        # 剩下的空白区才是拖窗口的地方
        if e.x > self._drag_limit:
            self._dragging = True
            self.window.start_drag(e)

    def _on_drag(self, e):
        if self._dragging:
            self.window.drag_move(e)

    def _click(self, name):
        w = self.window
        if name == "close":
            w.close()
        elif name == "min":
            w.minimize()
        else:
            w.toggle_max()

    def _load_icon(self):
        """
        标题左侧的小图标。

        用 PNG 不用 ico：Tk 的 PhotoImage 读不了 ico，只有 Win32 的 LoadImage 认。
        缩到标题栏高度的一半左右（20 设计像素）—— 再大就把标题挤走了，再小看不清。
        """
        if not self._icon_path:
            return
        import os
        if not os.path.exists(self._icon_path):
            return
        try:
            side = self.theme.px(20)
            im = Image.open(self._icon_path).convert("RGBA")
            im = im.resize((side, side), Image.LANCZOS)
            self._icon_photo = ImageTk.PhotoImage(im)
            self._icon_id = self.create_image(self._drag_limit, self._h // 2,
                                              image=self._icon_photo)
        except Exception:
            self._icon_photo = None
            self._icon_id = None

    def _redraw(self, event=None):
        """窗口大小变化时把「图标 + 标题」整体居中、设置入口靠右"""
        try:
            w = self.winfo_width()
            cy = self._h // 2
            bb = self.bbox(self._title_id)
            tw = (bb[2] - bb[0]) if bb else 0
            iw = self.theme.px(20) if self._icon_id is not None else 0
            gap = self.theme.px(8) if iw else 0
            # 整体居中，但不许压到左边的三色按钮
            sx = max(self._drag_limit, (w - (iw + gap + tw)) // 2)
            if self._icon_id is not None:
                self.coords(self._icon_id, sx + iw // 2, cy)
            self.coords(self._title_id, sx + iw + gap, cy)
            self.itemconfig(self._title_id, justify="left")
            if self._set_id is not None:
                self.coords(self._set_id, w - self.theme.px(20), cy)
        except Exception:
            pass
