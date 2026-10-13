@echo off
chcp 65001 >nul
cd /d "%~dp0"
"runtime-windows\python.exe" "scripts\check_dependencies.py"
if errorlevel 1 goto failed
"runtime-windows\python.exe" "server.py" --port 8888
if errorlevel 1 goto failed
exit /b 0
:failed
pause
exit /b 1
