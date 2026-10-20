@echo off
chcp 65001 >nul
cd /d "%~dp0.."

rem 网页控制台与网页版共用同一套页面，直接启动网页版即可。
call "启动网页版.bat"
