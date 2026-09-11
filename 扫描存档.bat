@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MC 存档卡顿热力图 - 批量扫描
set PYTHONIOENCODING=utf-8
set "TARGET=%~1"
if not defined TARGET set /p "TARGET=请把存档文件夹拖到这里再回车（或粘贴路径）: "
if not defined TARGET exit /b 1

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo 未找到 python，请先安装 Python 3.10+ 并勾选 Add to PATH。
  pause
  exit /b 1
)

echo.
echo 开始扫描: %TARGET%
echo 每个世界会生成一份热力图 HTML，跑完自动打开输出目录。
echo.
python scan.py "%TARGET%" --out output --top 20 --open
echo.
pause
