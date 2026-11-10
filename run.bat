@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ---- locate python (RapidOCR needs 3.10, not 3.13) ----
set "PYW=E:/software/anaconda/envs/py310/pythonw.exe"
set "PY=E:/software/anaconda/envs/py310/python.exe"

if /i "%~1"=="--check" (
    if exist "%PYW%" echo FOUND: %PYW%
    if exist "%PY%" echo FOUND: %PY%
    if not exist "%PYW%" if not exist "%PY%" echo NOT FOUND in py310 env
    exit /b 0
)

if exist "%PYW%" goto run_pyw
if exist "%PY%" goto run_py
goto run_path

:run_pyw
start "" "%PYW%" "%~dp0app.py"
exit /b 0

:run_py
start "" "%PY%" "%~dp0app.py"
exit /b 0

:run_path
where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw "%~dp0app.py"
    exit /b 0
)
where python >nul 2>nul
if not errorlevel 1 (
    start "" python "%~dp0app.py"
    exit /b 0
)

echo.
echo Python environment not found.
echo Please run:
echo   conda create -n py310 python=3.10 -y
echo   conda activate py310
echo   pip install rapidocr onnxruntime pymupdf python-docx pillow tkinterdnd2 opencv-python
echo.
pause
exit /b 1
