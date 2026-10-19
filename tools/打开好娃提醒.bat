@echo off
chcp 65001 >nul
cd /d "%~dp0.."
start "" "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File "%~dp0好娃提醒.ps1"
