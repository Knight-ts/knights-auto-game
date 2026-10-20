@echo off
chcp 65001 >nul
cd /d "%~dp0.."

set "PY=python"
if exist "python\python.exe" set "PY=python\python.exe"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

:menu
cls
echo ================================================
echo   任务包  tasks\  ^(每项任务一个文件夹^)
echo ================================================
echo   1. 导出全部任务     interface.json -^> tasks\（覆盖）
echo   2. 重建配置         tasks\ -^> interface.json
echo   3. 检查任务/选项/pipeline 引用
echo   4. 列出任务
echo   5. 打包一个任务为 zip
echo   6. 导入任务包 zip（自动重建）
echo   7. 单独运行一个任务（测试）
echo   0. 退出
echo.
set /p "choice=请选择: "

if "%choice%"=="1" goto do_export
if "%choice%"=="2" goto do_build
if "%choice%"=="3" goto do_check
if "%choice%"=="4" goto do_list
if "%choice%"=="5" goto do_pack
if "%choice%"=="6" goto do_unpack
if "%choice%"=="7" goto do_run
if "%choice%"=="0" exit /b 0
goto menu

:do_export
"%PY%" tools\taskpack.py export --force
pause
goto menu

:do_build
"%PY%" tools\taskpack.py build
pause
goto menu

:do_check
"%PY%" tools\taskpack.py check
pause
goto menu

:do_list
"%PY%" tools\taskpack.py list
pause
goto menu

:do_pack
set /p "entry=任务 entry（例如 CastleMarryTest）: "
"%PY%" tools\taskpack.py pack "%entry%"
pause
goto menu

:do_unpack
set /p "zip=zip 路径（可把文件拖进来）: "
set "zip=%zip:"=%"
"%PY%" tools\taskpack.py unpack "%zip%" --build
pause
goto menu

:do_run
set /p "entry=任务 entry（例如 CastleMarryTest）: "
set /p "sets=选项（可空，例如 联姻方式=联姻）: "
if "%sets%"=="" (
    "%PY%" tools\taskpack.py run "%entry%"
) else (
    "%PY%" tools\taskpack.py run "%entry%" --set "%sets%"
)
pause
goto menu
