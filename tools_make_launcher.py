"""
生成「启动.bat」——必须写成纯 ASCII。

为什么不用中文：cmd.exe 按当前代码页逐字节解析批处理文件。
文件里出现中文（UTF-8 多字节序列）时会被 GBK 拆成错位字符，
行边界错乱，后面 if / for 的括号块跟着崩，用户看到的就是满屏
「'Y" (' 不是内部或外部命令」（本次实测踩到）。
即使用 GBK 保存能跑，用编辑器打开又是乱码。

结论：bat 只写 ASCII，中文提示全部由 preflight.py 输出。
改 bat 请重新跑本脚本，不要手改。
"""

import os

BAT = r"""@echo off
rem ============================================================
rem  PDF Drawing Translator - launcher
rem
rem  IMPORTANT: keep this file ASCII-only.
rem
rem  cmd.exe parses a batch file byte by byte using the active code
rem  page. Chinese text inside a .bat gets mis-split (especially
rem  around chcp), which breaks if/for blocks and floods the
rem  console with "is not recognized as an internal or external
rem  command". All Chinese messages are printed by preflight.py,
rem  which runs as a normal Python program and has no such problem.
rem ============================================================
setlocal
cd /d "%~dp0"

rem The launcher and app.py must live in the same folder.
if not exist "%~dp0app.py" goto wrongplace

rem Prefer the full Python 3.14 install, which ships with tkinter.
rem The bundled runtime used by WorkBuddy has no tkinter.
set "PY=C:\Users\stanl\AppData\Local\Programs\Python\Python314\python.exe"
set "PYW=C:\Users\stanl\AppData\Local\Programs\Python\Python314\pythonw.exe"

if exist "%PY%" goto check

rem Fall back to whatever is on PATH.
set "PY="
set "PYW="
for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%i"
if not defined PY goto nopython

:check
rem Dependency check first, so a missing package is reported here
rem instead of the window flashing open and vanishing.
"%PY%" "%~dp0preflight.py"
if errorlevel 1 goto hold

if defined PYW (
    start "" "%PYW%" "%~dp0app.py"
) else (
    start "" "%PY%" "%~dp0app.py"
)
exit /b 0

:nopython
echo.
echo   Python not found, so the program cannot start.
echo   Please install Python 3.9 or newer and enable the tcl/tk option.
echo.
pause
exit /b 1

:wrongplace
echo.
echo   app.py was not found next to this launcher.
echo   Please put this file back into the program folder and run it there.
echo   Current folder: %~dp0
echo.
pause
exit /b 1

:hold
echo.
pause
exit /b 1
"""

target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "启动.bat")
with open(target, "w", encoding="ascii", newline="\r\n") as f:
    f.write(BAT)

print(f"已写入：{target}")
print(f"编码：ASCII  大小：{os.path.getsize(target)} 字节")
