"""
API key 的独立持久化：让「删配置」不再等于「删密钥」。

为什么要在 config.json 之外再存一份
-----------------------------------
客户丢过 key。卸载程序会问「配置和翻译缓存要不要一起删」，他点了「是」，
config.json 连人带 key 一起没了。他当初填 key 时没留底，丢了就只能回来找我们。

覆盖安装本身不碰 %APPDATA%，但「先卸载再装」这条路躲不开那个询问 ——
只要 key 和配置躺在同一个文件里，迟早会被连着删掉。所以 key 单独存一份，
放在卸载不会碰的位置，程序启动时发现配置里的 key 是空的就自动补回来。

存在哪、为什么是这个位置，见 paths.secret_file() 的说明。

为什么要加密
------------
明文 api key 放在 %APPDATA% 里，这个账户下任何一个程序都能读走。
用 Windows 自带的 DPAPI（CryptProtectData）加密，密钥由系统按「当前用户」
派生：同一台机器、同一个账户才解得开。把 apikey.dat 拷给别人也没用。
不引第三方加密库，也不用自己保管解密密钥。

DPAPI 不可用时（理论上只在非 Windows 或系统调用异常）退回带标记的明文，
程序照常能恢复 key —— 宁可加密等级降一档，也不能因为这个功能缺一块就
让用户重新去申请 key。
"""

import ctypes
import os
from ctypes import wintypes

from . import paths

# 明文兜底的标记前缀。正常情况文件里是 DPAPI 密文，读不出这个前缀。
_MAGIC_PLAIN = b"PLAIN:"


# ---------------------------------------------------------------------------
# DPAPI 调用
# ---------------------------------------------------------------------------
class _DATA_BLOB(ctypes.Structure):
    """Win32 的 DATA_BLOB：一段带长度的字节缓冲"""

    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data):
    """
    把 bytes 包成 DATA_BLOB。

    返回 (blob, buf)：buf 必须由调用方持有到系统调用结束，
    否则 Python 提前回收了缓冲，系统读到的就是野指针。
    """
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def _dpapi(data, protect):
    """
    调 DPAPI 加密或解密。失败返回 None（调用方决定怎么兜底，这里不抛）。

    protect=True  → CryptProtectData
    protect=False → CryptUnprotectData

    dwFlags 用 0x1（CRYPTPROTECT_UI_FORBIDDEN）：
    加密时不弹任何对话框。默认设置下 DPAPI 本来也不弹，但用户配了强制项
    或者企业策略时可能会，而我们的调用发生在后台线程里，弹窗会卡死界面。
    """
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return None                     # 非 Windows

    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.restype = wintypes.BOOL
    if protect:
        fn.argtypes = [ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR,
                       ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD,
                       ctypes.POINTER(_DATA_BLOB)]
    else:
        # 解密的第二个参数是 POINTER(LPWSTR)（返回描述串），传 None 即 NULL
        fn.argtypes = [ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
                       ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD,
                       ctypes.POINTER(_DATA_BLOB)]

    in_blob, _buf = _to_blob(data)
    out_blob = _DATA_BLOB()
    ok = fn(ctypes.byref(in_blob), None, None, None, None, 0x1,
            ctypes.byref(out_blob))
    if not ok:
        return None
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        # DPAPI 用 LocalAlloc 分配的输出必须由我们 LocalFree，
        # 否则每调用一次就漏一小块
        if out_blob.pbData:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def load_key():
    """
    读出独立保存的 api key。没有文件、解不开、内容为空都返回 ""——
    这个函数在启动路径上，任何异常都不该让程序起不来。
    """
    path = paths.secret_file()
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        return ""
    if not raw:
        return ""

    if raw.startswith(_MAGIC_PLAIN):
        try:
            return raw[len(_MAGIC_PLAIN):].decode("utf-8", "replace").strip()
        except Exception:
            return ""

    plain = _dpapi(raw, protect=False)
    if plain is None:
        # 解不开的常见原因：换了 Windows 账户、换了机器、文件被别的东西改过。
        # 都不用报错，用户重新填一次 key 就会覆盖掉这份坏的。
        return ""
    try:
        return plain.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def save_key(key):
    """
    把 api key 写进独立文件。返回是否成功。

    先写临时文件再 os.replace：写一半断电也不会留下半截密文，
    下次启动读到坏文件只会当成「没有 key」，不会把好文件毁掉。
    """
    key = (key or "").strip()
    path = paths.secret_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        blob = _dpapi(key.encode("utf-8"), protect=True)
        if blob is None:
            # 加密不可用：退回带标记的明文，功能不能因为加密失败就没了
            blob = _MAGIC_PLAIN + key.encode("utf-8")
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def clear_key():
    """删掉独立保存的 key。只有在用户主动清空密钥时才调"""
    try:
        os.remove(paths.secret_file())
        return True
    except Exception:
        return False
