@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo 未找到 Python，请先安装 Python 3.12 并勾选 Add to PATH。
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo 正在创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo 创建虚拟环境失败。
        pause
        exit /b 1
    )
)

echo 正在安装依赖 ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo 依赖安装失败，请检查网络或 requirements.txt。
    pause
    exit /b 1
)

echo.
echo 安装完成。双击 启动网页版.bat 即可。
pause
