@echo off
SET "SCRIPT_DIR=%~dp0"
IF "%1"=="web" GOTO :open_web
IF "%1"=="desktop" GOTO :open_desktop
IF "%1"=="gui" GOTO :open_desktop
IF NOT "%1"=="" GOTO :usage
:open_desktop
python "%SCRIPT_DIR%desktop.py"
EXIT /B
:open_web
python "%SCRIPT_DIR%desktop.py" --web
EXIT /B
:usage
echo Uso: claudio [desktop^|web] 1>&2
EXIT /B 2
