@echo off
setlocal
set PYTHONPATH=
set VIRTUAL_ENV=
cd /d "%~dp0"

echo Setting up DocMarshal...
python -m venv .venv
if errorlevel 1 goto :error

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

".venv\Scripts\python.exe" install_desktop_shortcut.py
if errorlevel 1 goto :error

echo.
echo DocMarshal setup completed successfully.
echo Double-click "Launch DocMarshal.bat" or the DocMarshal desktop shortcut to start.
pause
exit /b 0

:error
echo.
echo DocMarshal setup failed. Leave this window open for the error details.
pause
exit /b 1
