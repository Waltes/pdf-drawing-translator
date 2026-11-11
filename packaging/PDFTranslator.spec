# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置（Windows）。

构建：
    python -m PyInstaller packaging/PDFTranslator.spec --noconfirm

产物：
    dist/PDF图纸翻译/PDF图纸翻译.exe      ← 主程序
    dist/PDF图纸翻译/_internal/           ← 运行时依赖，别删

几个刻意的取舍，改之前先看这里：

1) 用 onedir 而不是 onefile。
   onefile 每次启动都要把几百兆解到临时目录，冷启动要多等十几秒，
   杀毒软件还会反复扫描那个临时目录。onedir 启动快、也不容易被误杀。

2) 打进了 OCR 全家桶（rapidocr / onnxruntime / cv2 / numpy / PIL）。
   v1.0.1 及以前是排除的，理由是体积：这几样加起来两百多兆。
   结果客户手上那份 CAD 导出的「文字转曲」PDF 用不了图像模式 ——
   客户装的 exe 里根本没有 OCR，只能提示跳过，等于功能缺一块。
   现在随包提供，安装包从 30MB 涨到 120MB 上下，换「装上就能用」。

   程序侧已把 OCR 改成惰性加载（见 core/image_text.py 顶部）：
   不用图像模式的人不会为它付出启动时间和内存，只有真正点「图像模式」才加载。

   注意 rapidocr 的加载方式很挑打包方式，见下面 RAPIDOCR 那一段注释。

3) 不打包 data/config.json。
   那是用户自己的 API key，打进安装包等于把密钥发给所有人。
   程序第一次运行会在 %APPDATA%\\PDFDrawingTranslator\\data 建自己的配置。
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# 只读资源。可写数据（config/cache/usage）一律不打进来 —— 见上面第 3 条。
datas = [
    (os.path.join(ROOT, "data", "glossary.csv"), "data"),   # 默认术语库
    (os.path.join(ROOT, "data", "fixes.csv"), "data"),      # 默认纠错表
    (os.path.join(ROOT, "data", "done.wav"), "data"),       # 完成提示音
    (os.path.join(ROOT, "icon.ico"), "."),                  # 窗口/任务栏图标
    (os.path.join(ROOT, "icon.png"), "."),
]
datas = [d for d in datas if os.path.exists(d[0])]

# ---------------------------------------------------------------- RAPIDOCR
# rapidocr 不能只靠 import 分析收进来，它有两处特殊的加载方式：
#
#   a) 配置和模型路径来自 Path(__file__).resolve().parent，
#      即 rapidocr_onnxruntime 包目录下的 config.yaml 和 models/*.onnx。
#      这两样必须作为数据文件落到磁盘上，塞在 PYZ 里读不到。
#
#   b) 三个子包 ch_ppocr_v3_det / ch_ppocr_v3_rec / ch_ppocr_v2_cls
#      是用裸模块名（importlib.import_module('ch_ppocr_v3_det')）导入的，
#      而且导入前做了 sys.path.append(包目录)。它们不在
#      rapidocr_onnxruntime.xxx 这个命名空间下，静态分析抓不到，
#      也不该硬塞进 PYZ —— 塞进去之后 __file__ 指向的目录在磁盘上并不存在。
#
# 所以这里用 collect_all 把整个包（含 .py 源码、config.yaml、models 下的
# 三个 onnx 模型）原样铺到 _internal/rapidocr_onnxruntime/ 下，
# 保持和正常 pip 安装时一模一样的目录结构，那两处加载方式照旧成立。
# 漏掉这一段的表现是：程序能启动、能进图像模式，一识别就抛异常，
# 而报错指向 rapidocr 内部，很难看出是打包漏了文件。
_ocr_datas, _ocr_binaries, _ocr_hidden = collect_all("rapidocr_onnxruntime")
datas += _ocr_datas

# 下面这些是 rapidocr 的间接依赖，它在运行时才 import，
# 静态分析常常追不全，显式声明。少一个就是在客户机器上崩一次。
_OCR_DEPS = [
    "yaml",         # 读 config.yaml
    "cv2",          # 图像缩放与解码
    "numpy",        # 像素运算
    "PIL",          # 部分图片格式的解码
    "onnxruntime",  # 推理后端
    "shapely",      # 检测框后处理
    "pyclipper",    # 检测框膨胀
    "six",          # rapidocr 里的兼容层引用
]

# shapely 的几何运算依赖包内的库文件，缺了会在导入时找不到 dll
datas += collect_data_files("shapely")

a = Analysis(
    [os.path.join(ROOT, "app.py")],
    pathex=[ROOT],
    binaries=_ocr_binaries,
    datas=datas,
    # tkinterdnd2 靠数据文件里的 tkdnd 才认得出拖拽，必须显式声明
    # core.i18n_en 是在 T() 里按需导入的（英文界面才加载），静态分析不一定能
    # 顺着这条路径找到它 —— 漏掉的话中文界面一切正常、切成英文就崩，最难查
    hiddenimports=["tkinterdnd2", "core.i18n_en"] + _ocr_hidden + _OCR_DEPS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # onnxruntime 里的开发者工具子包，运行时推理完全用不到，
        # 合起来十几兆。推理只用 capi 那一层。
        "onnxruntime.transformers", "onnxruntime.tools",
        "onnxruntime.quantization", "onnxruntime.datasets",
        "onnxruntime.backend",
        # 装了但用不上的大件
        "matplotlib", "pandas", "pytest", "setuptools", "scipy",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
        "IPython", "notebook", "jupyter",
        "tkinter.test", "test",
    ],
    noarchive=False,
    optimize=0,
)

# 去掉 cv2 自带的视频编解码库（opencv_videoio_ffmpeg*.dll，约 31MB）。
# 我们只用 imread/resize 这类静态图像接口，从不碰 VideoCapture；
# opencv 是在首次用到视频接口时才去加载它，图像处理路径不依赖它。
# 打包后必须实测一次图像模式，确认 cv2.pyd 在没有这个 dll 时仍能正常工作。
a.binaries = [
    b for b in a.binaries
    if not os.path.basename(b[0]).startswith("opencv_videoio_ffmpeg")
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDF图纸翻译",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 压缩过的 exe 极易被杀毒软件判成木马
    console=False,      # 无控制台窗口，报错走 启动错误.log
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "icon.ico") if os.path.exists(
        os.path.join(ROOT, "icon.ico")) else None,
    version=os.path.join(SPECPATH, "version_info.txt")
    if os.path.exists(os.path.join(SPECPATH, "version_info.txt")) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PDF图纸翻译",
)
