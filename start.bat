@echo off
setlocal
title InstaIQ - Launcher

REM ============================================================
REM  InstaIQ - start backend (FastAPI) + frontend (Vite)
REM  Double-click in Explorer, or run from a terminal: start.bat
REM  Safe to run repeatedly - already-running services are skipped.
REM ============================================================

set "ROOT=%~dp0"

echo.
echo   InstaIQ - starting services
echo   ---------------------------

where python >nul 2>&1
if errorlevel 1 (
    echo   [error] Python not found on PATH. Install Python 3.11+ first.
    pause
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo   [error] npm not found on PATH. Install Node.js 18+ first.
    pause
    exit /b 1
)

REM --- Backend (FastAPI on port 8000) --------------------------
netstat -ano | findstr "LISTENING" | findstr ":8000" >nul 2>&1
if %errorlevel%==0 (
    echo   [skip] Backend already running on port 8000
) else (
    echo   [start] Backend: checking dependencies, then launching...
    python -c "import fastapi, httpx, langchain_core, langchain_openai" >nul 2>&1
    if errorlevel 1 (
        echo           Installing backend dependencies ^(one-time^)...
        pip install -r "%ROOT%backend\requirements.txt" >nul 2>&1
    )
    start "InstaIQ backend" /MIN /D "%ROOT%backend" cmd /c "python -m uvicorn main:app --host 127.0.0.1 --port 8000 > uvicorn.log 2>&1"
)

REM --- Frontend (Vite dev server on port 5173) -----------------
netstat -ano | findstr "LISTENING" | findstr ":5173" >nul 2>&1
if %errorlevel%==0 (
    echo   [skip] Frontend already running on port 5173
) else (
    echo   [start] Frontend: checking dependencies, then launching...
    if not exist "%ROOT%frontend\node_modules" (
        echo           Installing frontend dependencies ^(one-time, a few minutes^)...
        pushd "%ROOT%frontend"
        call npm install
        popd
    )
    start "InstaIQ frontend" /MIN /D "%ROOT%frontend" cmd /c "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort > vite.log 2>&1"
)

REM --- Wait, then verify ---------------------------------------
echo   [wait]  Giving services a few seconds to boot...
timeout /t 8 /nobreak >nul

set "BACKEND_OK=0"
set "FRONTEND_OK=0"
where curl >nul 2>&1
if not errorlevel 1 (
    curl -s -m 5 http://127.0.0.1:8000/health 2>nul | findstr "healthy" >nul 2>&1 && set "BACKEND_OK=1"
    curl -s -m 5 -o nul -w "%%{http_code}" http://127.0.0.1:5173/ 2>nul | findstr "200" >nul 2>&1 && set "FRONTEND_OK=1"
)

echo.
if "%BACKEND_OK%"=="1" (echo   [ ok ] Backend  http://localhost:8000   ^(API docs at /docs^)) else (echo   [ !! ] Backend not responding - see backend\uvicorn.log)
if "%FRONTEND_OK%"=="1" (echo   [ ok ] Frontend http://localhost:5173) else (echo   [ !! ] Frontend not responding - see frontend\vite.log)

start "" http://localhost:5173
echo.
echo   Done. Run stop.bat to shut both services down.
pause
