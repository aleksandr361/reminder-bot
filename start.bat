@echo off
chcp 65001 >nul
cd /d "%~dp0"
py main.py
echo.
echo Bot stopped.
pause
