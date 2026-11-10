@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PY=E:/software/anaconda/envs/py310/python.exe"
if not exist "%PY%" set "PY=python"

echo [1/2] Installing PyInstaller ...
"%PY%" -m pip install pyinstaller --quiet
if errorlevel 1 goto fail

echo [2/2] Building exe (takes a few minutes, large output) ...
"%PY%" -m PyInstaller --noconfirm --clean --windowed --name "DocOCR" --collect-all rapidocr --collect-all tkinterdnd2 --hidden-import onnxruntime app.py
if errorlevel 1 goto fail

echo.
echo Done: dist\DocOCR\
echo Copy the whole folder to another machine to run.
pause
exit /b 0

:fail
echo.
echo Build failed. See messages above.
pause
exit /b 1
