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

if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" goto :ocr_ready
if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" goto :ocr_ready
echo Installing the Tesseract OCR engine...
where winget >nul 2>nul
if errorlevel 1 (
    echo Windows Package Manager is required to install Tesseract OCR.
    goto :error
)
winget install --id UB-Mannheim.TesseractOCR --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
if errorlevel 1 goto :error
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" goto :ocr_ready
if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" goto :ocr_ready
echo Tesseract OCR installation completed but tesseract.exe was not found.
goto :error

:ocr_ready
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
