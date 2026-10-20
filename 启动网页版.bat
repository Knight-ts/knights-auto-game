@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=python"
if exist "python\python.exe" set "PY=python\python.exe"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

where %PY% >nul 2>nul
if errorlevel 1 (
    if not exist "%PY%" (
        echo 未找到 Python。请先运行 安装依赖.bat，或安装 Python 3.12 并加入 PATH。
        pause
        exit /b 1
    )
)

"%PY%" -c "import maa, cv2, loguru" >nul 2>nul
if errorlevel 1 (
    echo 依赖未安装或 Python 环境不正确。
    echo 请先双击 安装依赖.bat 安装依赖，然后再启动。
    pause
    exit /b 1
)

"%PY%" webapp\server.py %*
echo.
pause
