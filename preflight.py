"""
启动前体检 —— 由「启动.bat」调用。

目的：把「双击后窗口闪一下就没了」变成一句能看懂的话。
依赖缺失、Python 版本不对、组件损坏，都在这里先报出来，
不用等 GUI 起不来再猜。

退出码 0 = 可以启动；1 = 有问题（bat 会停住让用户看提示）。
"""

import os
import sys
import warnings

# PyMuPDF 1.28 起 `import fitz` 会打一条英文 FutureWarning。
# 这条警告对用户没有意义，只会让体检窗口看起来像出了错，直接压掉。
warnings.filterwarnings("ignore", category=FutureWarning)

# 控制台代码页千奇百怪（简体中文是 936/GBK），万一遇到编不出来的符号，
# print 会直接抛 UnicodeEncodeError，把体检本身弄崩，反而看不到真正的问题。
# 这里统一改成「编不出来就替换成问号」，宁可难看也要把信息说全。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ok = True


def bad(msg, fix=""):
    """记录一条问题，统一格式输出"""
    global ok
    ok = False
    print(f"  [×] {msg}")
    if fix:
        print(f"      → {fix}")


def good(msg):
    print(f"  [√] {msg}")


print()
print("  PDF 图纸翻译 · 启动检查")
print("  " + "-" * 46)

# 1) Python 版本：3.9 以下语法都不保证能跑
if sys.version_info < (3, 9):
    bad(f"Python 版本过低（当前 {sys.version.split()[0]}）", "请安装 3.9 或更高版本")
else:
    good(f"Python {sys.version.split()[0]}")

print(f"  解释器：{sys.executable}")

# 2) 逐个检查依赖。分开检查是为了告诉用户到底缺哪个，
#    而不是笼统丢一句 ImportError。
deps = [
    ("tkinter", "tkinter", "Python 安装时未勾选 tcl/tk，需重装 Python 并勾选该组件"),
    ("PyMuPDF", "fitz", "python -m pip install pymupdf"),
    ("numpy", "numpy", "python -m pip install numpy"),
    ("Pillow（界面圆角与阴影要用）", "PIL", "python -m pip install pillow"),
]

for label, mod, fix in deps:
    try:
        __import__(mod)
        good(label)
    except Exception as e:
        bad(f"{label} 不可用（{type(e).__name__}）", fix)

# 3) 可选依赖：装不上也能用，只是功能降级
try:
    __import__("tkinterdnd2")
    good("tkinterdnd2（支持拖拽）")
except Exception:
    print("  [!] tkinterdnd2 未安装 —— 拖拽不可用，可点按钮选文件")
    print("      → python -m pip install tkinterdnd2")

# OCR 用于识别图片里的文字；缺了只影响图纸里栅格化的标注
try:
    __import__("rapidocr_onnxruntime")
    good("rapidocr（识别图片内文字）")
except Exception:
    print("  [!] rapidocr 未安装 —— 无法翻译图片里的文字，可执行安装：")
    print("      → python -m pip install rapidocr-onnxruntime")

# 4) 项目自身文件完整性：core 目录丢文件的话，界面起来也会崩
sys.path.insert(0, APP_DIR)
try:
    from core import pdf_engine  # noqa: F401
    from core.translate import Translator  # noqa: F401
    from core.llm_client import LLMClient  # noqa: F401
    from core.sound import play as _play  # noqa: F401
    import ui_kit  # noqa: F401  界面工具箱，缺了窗口画不出来
    good("程序文件完整")
except Exception as e:
    bad(f"程序文件有问题：{type(e).__name__}: {e}",
        "确认 core 目录和 ui_kit.py 都在，没有被删除或改名")

# 5) 图标：缺了不影响翻译，只是窗口和桌面快捷方式会退回系统默认图标，
#    所以只提示不拦。图标由 tools_make_icon.py 生成。
if not os.path.exists(os.path.join(APP_DIR, "icon.ico")):
    print("  [!] icon.ico 缺失 —— 窗口和快捷方式会显示系统默认图标")
    print("      → 运行 tools_make_icon.py 可重新生成")

# 6) 数据目录可写性：配置和用量记录要写在这里，只读会导致启动即崩
data_dir = os.path.join(APP_DIR, "data")
try:
    os.makedirs(data_dir, exist_ok=True)
    probe = os.path.join(data_dir, ".write_test")
    with open(probe, "w", encoding="utf-8") as f:
        f.write("ok")
    os.remove(probe)
    good("配置目录可写")
except Exception as e:
    bad(f"配置目录不可写（{type(e).__name__}）", f"检查权限：{data_dir}")

# 7) 清掉上次的崩溃日志，避免旧错误干扰本次判断
log = os.path.join(APP_DIR, "启动错误.log")
if os.path.exists(log):
    try:
        os.remove(log)
    except Exception:
        pass

print("  " + "-" * 46)
if ok:
    print("  检查通过，正在启动窗口…\n")
else:
    print("  发现问题，请按上面的提示处理。\n")

sys.exit(0 if ok else 1)
