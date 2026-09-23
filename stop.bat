@echo off
setlocal
title InstaIQ - Stop

REM ============================================================
REM  InstaIQ - stop backend (port 8000) + React UI (port 5173)
REM  Double-click in Explorer, or run from a terminal: stop.bat
REM ============================================================

echo.
echo   Stopping InstaIQ...

set "KILLED=0"

for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8000"') do (
    echo   [stop] Backend  ^(PID %%p^)
    taskkill /PID %%p /F >nul 2>&1
    set "KILLED=1"
)

for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":5173"') do (
    echo   [stop] React UI ^(PID %%p^)
    taskkill /PID %%p /F >nul 2>&1
    set "KILLED=1"
)

if "%KILLED%"=="0" echo   Nothing was running.
if "%KILLED%"=="1" echo   All services stopped.

echo.
pause
