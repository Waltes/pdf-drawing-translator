"""
创建桌面 / 开始菜单快捷方式，带图标。

为什么要这个脚本：程序是绿色版，没有安装程序。用户拿到的是一个文件夹，
双击「启动.bat」能用，但桌面上只有一个写着 BAT 的白色文件图标，不像个软件。
这个脚本把快捷方式建到桌面和开始菜单，指向 pythonw.exe + app.py，
图标换成 icon.ico，看起来才像个装好的程序。

指向 pythonw 而不是「启动.bat」：后者每次双击都要弹一下黑色命令行窗口。
代价是跳过 preflight.py 的依赖检查，但 app.py 自己会捕获导入失败并弹窗
说明少了什么包，不会无声退出。

中文路径的处理：PowerShell 5.1 的 -Command 参数按当前代码页解码，
直接塞中文会乱码。所以脚本内容先编成 UTF-16LE 再 base64，用
-EncodedCommand 传进去，这是唯一不会踩编码坑的传参方式。

重跑本脚本会覆盖已有快捷方式，放心执行。
"""

import base64
import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(APP_DIR, "icon.ico")
APP = os.path.join(APP_DIR, "app.py")
LINK_NAME = "PDF 图纸翻译"

# 跟 启动.bat 保持同一套解释器优先级：装了完整版 Python 就用它，
# 否则退回 PATH 里的 pythonw
PREFERRED = [
    r"C:\Users\stanl\AppData\Local\Programs\Python\Python314\pythonw.exe",
    r"C:\Users\stanl\AppData\Local\Programs\Python\Python314\python.exe",
]


def find_pythonw():
    """找一个带窗口的 Python 解释器。找不到就失败，不要瞎猜"""
    for p in PREFERRED:
        if os.path.exists(p):
            return p
    import shutil
    for name in ("pythonw.exe", "pythonw", "python.exe", "python"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _ps_literal(path):
    """转成 PowerShell 单引号字符串。单引号内只需要把 ' 写成 ''"""
    return "'" + path.replace("'", "''") + "'"


def make_shortcuts(target, args, workdir, icon):
    ps = f"""
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$ws = New-Object -ComObject WScript.Shell
$made = @()
foreach ($dir in @($desktop, $programs)) {{
    if (-not $dir) {{ continue }}
    $path = Join-Path $dir ({_ps_literal(LINK_NAME)} + '.lnk')
    $lnk = $ws.CreateShortcut($path)
    $lnk.TargetPath = {_ps_literal(target)}
    $lnk.Arguments = {_ps_literal(args)}
    $lnk.WorkingDirectory = {_ps_literal(workdir)}
    $lnk.IconLocation = {_ps_literal(icon + ',0')}
    $lnk.Description = 'PDF 图纸翻译 - 保持版式的图纸翻译工具'
    $lnk.WindowStyle = 1
    $lnk.Save()
    $made += $path
}}
$made | ForEach-Object {{ Write-Output $_ }}
"""
    # UTF-16LE + base64，中文路径才不会在传参时被拆坏
    enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", enc],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r


def refresh_icon_cache():
    """通知资源管理器重读图标。不做的话快捷方式可能还是白纸图标，
    要等它自己慢慢刷（有时候得十几分钟），用户会以为没生效"""
    try:
        from ctypes import windll
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass


def main():
    if not os.path.exists(APP):
        print(f"找不到主程序：{APP}")
        return 1
    if not os.path.exists(ICON):
        print("找不到 icon.ico，先运行 tools_make_icon.py 生成图标")
        return 1

    pyw = find_pythonw()
    if not pyw:
        print("没有找到 Python 解释器。请先安装 Python 3.9 或更新版本。")
        return 1

    r = make_shortcuts(pyw, f'"{APP}"', APP_DIR, ICON)
    if r.returncode != 0:
        print("创建快捷方式失败：")
        print((r.stderr or r.stdout or "").strip()[:800])
        return 1

    lines = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    if not lines:
        print("PowerShell 没有回报任何路径，快捷方式可能没建成。")
        return 1

    refresh_icon_cache()
    print("解释器：" + pyw)
    for p in lines:
        print("已创建：" + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
