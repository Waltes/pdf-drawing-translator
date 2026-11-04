"""
运行期路径：只读资源从哪读、可写数据往哪写。

为什么单独一个模块：
源码运行时，「程序目录」和「数据目录」是同一个地方（项目根 + data/），
写哪儿都行。但打包成 exe 装进 C:\\Program Files 之后，这两件事必须分开——

  · 只读资源（术语库、纠错表、音效、图标）跟程序走，PyInstaller 打包后
    落在解包目录里（sys._MEIPASS），用户看不到也不该改；
  · 可写数据（配置、缓存、用量记账）在安装目录里**写不进去**：
    Program Files 默认不允许普通用户写入，硬写会抛 PermissionError，
    表现就是「装完一打开就闪退」。而且 UAC 虚拟化会把它偷偷重定向到
    VirtualStore，用户改的配置和程序读的不是同一份，排查起来极费劲。

所以打包后把可写数据放到 %APPDATA%\\PDF图纸翻译\\data；源码运行照旧用 ./data，
开发时不用改任何习惯。
"""

import os
import shutil
import sys

# 用户数据目录用中文名，跟开始菜单、桌面快捷方式保持一致，
# 用户去翻配置的时候认得出这是什么软件
APP_TITLE = "PDF图纸翻译"

FROZEN = bool(getattr(sys, "frozen", False))


def _program_dir():
    """exe 所在目录。onedir 打包时就是安装目录；源码运行时是项目根"""
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _res_dir():
    """
    只读资源目录。

    PyInstaller 打包后一律是 sys._MEIPASS：
    onefile 解到临时目录，onedir 指向 exe 旁边的 _internal。
    两者行为一致，所以这里不用区分。
    """
    if FROZEN:
        return getattr(sys, "_MEIPASS", _program_dir())
    return _program_dir()


def _writable(path):
    """真的试着写一下。只看 os.access 在 Windows 上不可靠（ACL 判不准）"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".wtest")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        return True
    except Exception:
        return False


def _data_dir():
    """
    可写数据目录。

    源码运行：项目根 / data（和以前完全一样，不打扰开发习惯）
    打包运行：%APPDATA% / PDF图纸翻译 / data
              —— APPDATA 写不进去这种极端情况（某些域环境、漫游配置被锁），
                 退回程序目录，至少还能跑起来，不至于一装就废。

    注意探针要打在**最终那个目录**上，不能只探父目录：
    只建了 PDF图纸翻译 却没建 data，后面写配置时才会炸，
    而且错误发生在离现场很远的地方，很难查。
    """
    if not FROZEN:
        d = os.path.join(_program_dir(), "data")
        os.makedirs(d, exist_ok=True)
        return d

    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_TITLE, "data")
    if _writable(d):            # 内部已 makedirs + 真写过一次
        return d

    d = os.path.join(_program_dir(), "data")
    os.makedirs(d, exist_ok=True)
    return d


APP_DIR = _program_dir()
RES_DIR = _res_dir()
DATA_DIR = _data_dir()

# 素材目录：示例图纸之类，装完给用户直接试一试
SAMPLE_DIR = os.path.join(RES_DIR, "sample")


# 首次运行时从资源目录复制到数据目录的种子文件。
# 都是「程序自带的默认内容」，复制过去之后用户可以照着改（术语库就是要能改的）。
# 已经存在就绝不覆盖 —— 用户辛苦加的术语不能被一次升级抹掉。
SEED_FILES = ("glossary.csv", "fixes.csv", "done.wav", "prices.json")


def ensure_seed_files():
    """
    把默认数据铺到数据目录。启动时调一次，失败不影响运行（只是缺默认值）。
    """
    for name in SEED_FILES:
        dst = os.path.join(DATA_DIR, name)
        if os.path.exists(dst):
            continue
        src = os.path.join(RES_DIR, "data", name)
        if not os.path.exists(src):
            continue
        try:
            shutil.copy2(src, dst)
        except Exception:
            pass


def user_file(name):
    """数据目录下一个文件的完整路径"""
    return os.path.join(DATA_DIR, name)
