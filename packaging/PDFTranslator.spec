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
   onefile 每次启动都要把上百兆解到临时目录，冷启动要多等三四秒，
   杀毒软件还会反复扫描那个临时目录。onedir 启动快、也不容易被误杀。

2) 不打包 OCR（rapidocr / onnxruntime / numpy）。
   光 onnxruntime 就一百多兆，而它只影响「烧在图片里的字」这一种情况，
   正常图纸的文字都是可选中文本，用不上。想用的人装完自己 pip 装即可，
   core/image_text.py 里是 try 导入，缺了会自动跳过，不会报错。

3) 不打包 data/config.json。
   那是用户自己的 API key，打进安装包等于把密钥发给所有人。
   程序第一次运行会在 %APPDATA%\\PDFDrawingTranslator\\data 建自己的配置。
"""

import os

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

a = Analysis(
    [os.path.join(ROOT, "app.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    # tkinterdnd2 靠数据文件里的 tkdnd 才认得出拖拽，必须显式声明
    # core.i18n_en 是在 T() 里按需导入的（英文界面才加载），静态分析不一定能
    # 顺着这条路径找到它 —— 漏掉的话中文界面一切正常、切成英文就崩，最难查
    hiddenimports=["tkinterdnd2", "core.i18n_en"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # OCR 全家桶，见上面第 2 条
        "rapidocr_onnxruntime", "onnxruntime", "numpy", "scipy",
        # 装了但用不上的大件
        "matplotlib", "pandas", "pytest", "setuptools",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
        "IPython", "notebook", "jupyter",
        "tkinter.test", "test",
    ],
    noarchive=False,
    optimize=0,
)

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
