@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem MC ChunkLag - desktop GUI launcher.
rem Usage: double-click this file, or drag a save folder onto it (path is filled in).
rem Needs PySide6:  pip install -r requirements.txt

rem NOTE: this file is intentionally pure ASCII - cmd does not accept a UTF-8 BOM and
rem native-language text breaks depending on the active code page.

rem Check PySide6 first. When it is missing, pythonw would exit silently and the user
rem would see "nothing happened" - so print an actionable message instead.
python -c "import PySide6" >nul 2>nul
if errorlevel 1 goto noqt

where pythonw >nul 2>nul
if errorlevel 1 goto console

start "" pythonw "%~dp0chunklag\gui.py" %*
goto end

:noqt
echo.
echo PySide6 is not installed - the GUI needs it.
echo Install it with:
echo     pip install -r requirements.txt
echo.
echo (The analysis engine and the CLI keep working without PySide6:
echo  python main.py ^<save folder^> )
echo.
pause
goto end

:console
echo pythonw not found - starting with python (keep this window to see errors)
python "%~dp0chunklag\gui.py" %*
if errorlevel 1 pause

:end
endlocal
