@echo off
setlocal
set PYTHONPATH=
set VIRTUAL_ENV=
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo DocMarshal is not set up yet.
    echo Run "Setup DocMarshal.bat" first.
    pause
    exit /b 1
)

if not exist "launch_gui.pyw" (
    echo Required DocMarshal program files are missing.
    echo Run "Setup DocMarshal.bat" again.
    pause
    exit /b 1
)

start "DocMarshal" ".venv\Scripts\pythonw.exe" "%~dp0launch_gui.pyw"
