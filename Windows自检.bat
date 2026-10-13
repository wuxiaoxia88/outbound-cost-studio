@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 检查内置依赖、DuckDB 查询和 Excel 读取...
"runtime-windows\python.exe" "scripts\check_dependencies.py"
if errorlevel 1 goto failed
echo [2/2] 运行隔离测试（不修改业务数据库）...
"runtime-windows\python.exe" -m unittest discover -s tests -v
if errorlevel 1 goto failed
echo 自检通过。请启动出港成本分析并验证实际导入、保存和导出。
pause
exit /b 0
:failed
echo 自检失败，请保留以上错误信息。
pause
exit /b 1
