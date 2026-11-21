@echo off
rem qwensese launcher entry point (Windows, double-click friendly)
rem This file is pure ASCII on purpose: cmd.exe reads batch files using the
rem console code page, and PowerShell 5.1 reads no-BOM .ps1 as ANSI, so
rem non-ASCII text here would be unreliable. The real logic is scripts\start.ps1.
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [error] launcher exited with code %RC%
    pause
)
endlocal & exit /b %RC%
