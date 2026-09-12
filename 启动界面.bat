@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem MC 存档卡顿扫描器 · 图形界面
rem 用法：双击本文件；也可把存档文件夹直接拖到本文件上（路径会自动填进界面）

where pythonw >nul 2>nul
if errorlevel 1 goto console

start "" pythonw "%~dp0chunklag\gui.py" %*
goto end

:console
echo 没找到 pythonw，改用 python 启动（保留这个窗口可以看到报错）
python "%~dp0chunklag\gui.py" %*
if errorlevel 1 pause

:end
endlocal
