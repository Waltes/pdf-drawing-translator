@echo off
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

rem Prefer a full python.org install, because it ships with tkinter. The path
rem below is the developer's own install; on any other machine it is simply
rem not there, and we fall through to whatever is on PATH. That is safe even
rem when PATH points at a runtime without tkinter: app.py then relaunches
rem itself with an interpreter that has it.
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
