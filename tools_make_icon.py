"""
生成应用图标 —— icon.ico / icon.png。

设计思路（为什么长这样）：
- 底色深空蓝渐变，跟程序默认主题「深空蓝」同源，桌面上一眼能跟产品对上。
- 主体是一张白纸：这个工具干的事就是把图纸上的注释换成另一种语言，
  纸是唯一不会被误解的符号。
- 纸面留白多、只用「上半灰线 + 下半蓝线」两组短横表示原文与译文，
  不画细节。桌面图标实际大多以 32px 显示，线一多就糊成一团灰块。
- 右下角压一枚圆形徽章，里面一个粗体 A，点题「译成英文」，同时把
  圆形这个图形元素从方正的纸里拉出来，小尺寸下也能看出是个「有东西」
  的图标，而不是一张空白文档。

输出：
- icon.ico  多尺寸（16/24/32/48/64/128/256），给系统与快捷方式用
- icon.png  512px，给窗口标题栏和文档配图用
- icon_preview.png  各尺寸并排的预览图，用来目检 16px 是否还认得出

改配色/改形状就改下面的常量，重跑本脚本即可，不要直接改 ico。
"""

import io
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SS = 4                      # 超采样倍数：先按 SS 倍画，再缩回去，边角才不锯齿
BASE = 256                  # 设计基准尺寸，下面所有坐标都按 256 写
S = BASE * SS               # 实际绘制画布边长

# ---- 配色（与 ui_kit.PALETTES["deep"] 同源，改主题时这里跟着改）----
TOP = (48, 79, 148)         # 底板渐变上端 #304F94
BOTTOM = (30, 41, 70)       # 底板渐变下端 #1E2946
EDGE = (140, 170, 226)      # 底板内描边
PAPER = (250, 252, 255)     # 纸
INK = (46, 76, 143)         # 纸上标题条
LINE_DIM = (186, 198, 220)  # 纸上原文线（灰）
LINE_HI = (74, 158, 255)    # 纸上译文线（主题强调色 #4A9EFF）
BADGE_TOP = (108, 176, 255)
BADGE_BOT = (52, 132, 246)

# ---- 布局（按 256 基准写）----
PLATE = (7, 7, 249, 249)    # 底板外框
PLATE_R = 58                # 底板圆角
PAPER_BOX = (60, 48, 198, 208)
PAPER_R = 14
BADGE_C = (189, 191)        # 徽章圆心
BADGE_R = 45


def _lin_gradient(size, c1, c2):
    """竖直线性渐变。逐行填，比 numpy 少一个依赖，256*4 的行数完全够快"""
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = (
            round(c1[0] + (c2[0] - c1[0]) * t),
            round(c1[1] + (c2[1] - c1[1]) * t),
            round(c1[2] + (c2[2] - c1[2]) * t),
        )
    return img.resize((w, h), Image.NEAREST)


def _vgrad_mask(size, radius, box):
    """圆角矩形蒙版，用来把渐变裁成圆角底板"""
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle(box, radius=radius, fill=255)
    return mask


def _radial_highlight(size, center, radius, peak):
    """左上角柔光。桌面图标有光感才不像死板的色块"""
    small = 64                                   # 小画布算完再放大，快到几乎不耗时
    img = Image.new("L", (small, small), 0)
    px = img.load()
    cx, cy = center[0] * small, center[1] * small
    r = radius * small
    for y in range(small):
        for x in range(small):
            dx, dy = x - cx, y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < r:
                # 平方衰减，边缘过渡更自然
                v = (1.0 - dist / r) ** 2 * peak
                px[x, y] = int(max(0, min(255, v)))
    return img.resize(size, Image.BICUBIC).filter(ImageFilter.GaussianBlur(size[0] // 90))


def _load_font(px):
    """粗体 A。Arial Bold 在 Windows 上必有；找不到就退回雅黑粗体"""
    for path in (r"C:\Windows\Fonts\arialbd.ttf",
                 r"C:\Windows\Fonts\seguisb.ttf",
                 r"C:\Windows\Fonts\msyhbd.ttc"):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, px)
            except Exception:
                continue
    return ImageFont.load_default()


def _draw_badge(base, cx, cy, r):
    """右下角圆形徽章：渐变圆 + 白色粗体 A。

    参数是**画布像素**，不是设计坐标。调用方先乘好缩放再传进来 ——
    这里曾经按设计坐标乘超采样倍数算，两者在 256 时恰好相等，
    到 64px 帧就开始各走各的，徽章飞出画布，任务栏上只剩一张白纸。

    外圈留一道白边，把徽章从纸上「浮」起来，否则蓝底蓝章会糊在一起。
    """
    ring = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ring)
    d.ellipse([cx - r - 5, cy - r - 5, cx + r + 5, cy + r + 5], fill=(255, 255, 255, 255))
    base.alpha_composite(ring)

    disc_mask = Image.new("L", base.size, 0)
    ImageDraw.Draw(disc_mask).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)

    grad = _lin_gradient(base.size, BADGE_TOP, BADGE_BOT).convert("RGBA")
    base.paste(grad, (0, 0), disc_mask)

    # 徽章上沿一圈更亮的高光，模拟球面受光
    gloss_mask = Image.new("L", base.size, 0)
    ImageDraw.Draw(gloss_mask).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    gloss = Image.new("RGBA", base.size, (255, 255, 255, 0))
    ImageDraw.Draw(gloss).ellipse(
        [cx - r * 0.82, cy - r * 1.02, cx + r * 0.82, cy + r * 0.28],
        fill=(255, 255, 255, 32))
    base.paste(gloss, (0, 0),
               Image.composite(gloss.split()[3], Image.new("L", base.size, 0), gloss_mask))

    # 字母 A：手动居中。字体基线不能靠 getbbox 一把量准，
    # 用 anchor="mm" 让 PIL 按字形视觉中心对齐
    font = _load_font(max(8, int(r * 1.30)))
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((cx, cy + r * 0.04), "A",
                               font=font, fill=(255, 255, 255, 255), anchor="mm")
    base.alpha_composite(layer)


def draw_icon(size=BASE):
    """画一张 size×size 的图标，返回 RGBA。

    每个尺寸单独画、单独超采样，不靠缩母版。原因：16px 缩下来只有十几像素
    可用，纸上五条线会糊成一整块灰；小尺寸必须换一套更粗、更少的线条。
    """
    ss = max(4, int(round(600 / size)))
    simple = size <= 32          # 小尺寸走简化版：只留三根粗线
    canvas = size * ss
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))

    # ---- 1. 圆角底板：渐变 + 左上柔光 + 内描边 ----
    scale = canvas / BASE
    plate_box = tuple(v * scale for v in PLATE)
    plate_mask = _vgrad_mask((canvas, canvas), PLATE_R * scale, plate_box)

    grad = _lin_gradient((canvas, canvas), TOP, BOTTOM).convert("RGBA")
    img.paste(grad, (0, 0), plate_mask)

    light = _radial_highlight((canvas, canvas), (0.30, 0.12), 0.62, 46)
    light = Image.composite(light, Image.new("L", (canvas, canvas), 0), plate_mask)
    img.paste(Image.new("RGBA", (canvas, canvas), (255, 255, 255, 255)), (0, 0), light)

    edge = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle(
        plate_box, radius=PLATE_R * scale, outline=EDGE + (115,),
        width=max(1, round(2 * scale)))
    img.alpha_composite(edge)

    # ---- 2. 纸张投影 ----
    # 单独一层画完再模糊，直接在大图上模糊太慢，而且会把纸边一起糊
    sh_pad = 40
    sh = Image.new("L", (canvas + sh_pad * 2, canvas + sh_pad * 2), 0)
    ImageDraw.Draw(sh).rounded_rectangle(
        [PAPER_BOX[0] * scale + sh_pad, PAPER_BOX[1] * scale + sh_pad + 9 * scale,
         PAPER_BOX[2] * scale + sh_pad, PAPER_BOX[3] * scale + sh_pad + 9 * scale],
        radius=PAPER_R * scale, fill=150)
    sh = sh.filter(ImageFilter.GaussianBlur(10 * scale))
    sh = sh.crop((sh_pad, sh_pad, sh_pad + canvas, sh_pad + canvas))

    shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 255))
    img.paste(shadow, (0, 0), sh)

    # ---- 3. 纸 ----
    paper = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    pd = ImageDraw.Draw(paper)
    pd.rounded_rectangle(tuple(v * scale for v in PAPER_BOX),
                         radius=PAPER_R * scale, fill=PAPER + (255,))
    img.alpha_composite(paper)

    # ---- 4. 纸上的内容 ----
    d = ImageDraw.Draw(img)

    def bar(x1, y1, x2, y2, color, r=None):
        """纸上的一段线条。统一封装是为了让上下两组的圆角、粗细一致"""
        d.rounded_rectangle([x1 * scale, y1 * scale, x2 * scale, y2 * scale],
                            radius=(r if r is not None else 3) * scale,
                            fill=color + (255,))

    if simple:
        # 小尺寸版：三根粗线。细线在 16px 下等于没有，不如直接减量
        bar(78, 62, 152, 76, INK, r=6)
        bar(78, 96, 182, 110, LINE_DIM, r=6)
        bar(78, 146, 162, 160, LINE_HI, r=6)
    else:
        bar(78, 64, 150, 73, INK, r=4)              # 标题条
        bar(78, 92, 182, 100, LINE_DIM)             # 原文（灰）
        bar(78, 110, 150, 118, LINE_DIM)
        bar(78, 142, 166, 150, LINE_HI)             # 译文（蓝）右端收短，
        bar(78, 160, 138, 168, LINE_HI)             # 免得被徽章盖掉半截

    # ---- 5. 右下角徽章 ----
    _draw_badge(img, BADGE_C[0] * scale, BADGE_C[1] * scale, BADGE_R * scale)

    return img


def write_ico(path, images, sizes):
    """
    手写 ICO 文件，每帧存 PNG 数据。

    为什么不用 PIL 的 im.save(..., format="ICO")：
    ICO 除了 32 位带 alpha 的位图，还有一个 1 位的 AND 掩码（AND mask）。
    Pillow 写这个掩码时只按纯透明/纯不透明两档处理，结果把纸的白色
    写成了「透明」，Windows 任务栏渲染时白纸整片被挖空，只剩深蓝底和
    几根线条 —— 现象是图标变成一个认不出的深蓝方块（本次实测踩到）。

    Vista 以后的 ICO 允许每帧直接放 PNG 流，PNG 自带完整 alpha，
    完全绕开掩码。目录项里 256 要写成 0，这是格式规定。
    """
    import struct

    blobs = []
    for img, s in zip(images, sizes):
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        blobs.append(buf.getvalue())

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = b""
    for blob, s in zip(blobs, sizes):
        dim = 0 if s >= 256 else s          # 256 在格式里必须写成 0
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)

    with open(path, "wb") as f:
        f.write(header)
        f.write(entries)
        for blob in blobs:
            f.write(blob)


def build(out_dir=APP_DIR):
    master = draw_icon(256)                      # 1024×1024
    master.resize((512, 512), Image.LANCZOS).save(os.path.join(out_dir, "icon.png"))

    # 每个尺寸单独画、单独缩，别让 Windows 自己缩
    sizes = [256, 128, 64, 48, 32, 24, 16]
    frames = [draw_icon(s).resize((s, s), Image.LANCZOS) for s in sizes]
    write_ico(os.path.join(out_dir, "icon.ico"), frames, sizes)

    # 预览图：深底与白底各排一行，检查两种壁纸下都看得清
    row_h = 256
    preview = Image.new("RGB",
                        (sum(sizes) + 20 * (len(sizes) + 1), 2 * row_h + 50),
                        (255, 255, 255))
    dp = ImageDraw.Draw(preview)
    dp.rectangle([0, 0, preview.width, row_h + 25], fill=(24, 26, 33))
    x = 20
    for s, fr in zip(sizes, frames):
        preview.paste(fr, (x, 12 + (row_h - s) // 2), fr)
        preview.paste(fr, (x, 25 + row_h + (row_h - s) // 2), fr)
        x += s + 20
    preview.save(os.path.join(out_dir, "icon_preview.png"))

    print(f"icon.ico          {os.path.getsize(os.path.join(out_dir, 'icon.ico')):,} 字节"
          f"  尺寸 {sizes}")
    print("icon.png          512×512")
    print("icon_preview.png  深/浅底并排预览")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else APP_DIR)
