@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  InterLoom launcher
REM  - Verifies Python and Node are installed
REM  - Creates .venv if missing; installs deps only when they change
REM  - Caches the embedding model before the demo, not during it
REM  - Generates the sample corpus if it isn't on disk
REM  - Starts the API and the UI, then opens a browser
REM  Safe to double-click from any Windows machine or location.
REM ============================================================

cd /d "%~dp0"

REM UTF-8 console: the scripts print box rules, arrows and a Greek delta, and
REM cp1252 cannot encode any of them. Without this they do all their work and
REM then die with a UnicodeEncodeError on the way to reporting success.
chcp 65001 >nul 2>nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "API_PORT=8000"
set "UI_PORT=5173"

echo ================================================
echo   InterLoom - Smart Shortlisting Engine
echo ================================================
echo.

REM ---- 1. Locate a Python interpreter --------------------------------
set "PYTHON_CMD="

where python >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python was not found on this system.
    echo         Install Python 3.11 or 3.12 from https://www.python.org/downloads/
    echo         ^(tick "Add python.exe to PATH" during setup^), then re-run this script.
    echo.
    pause
    exit /b 1
)

echo [OK] Found Python:
%PYTHON_CMD% --version
echo.

REM ---- 2. Locate Node ------------------------------------------------
where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js was not found on this system.
    echo         The user interface is a Vite app and needs it.
    echo         Install the LTS build from https://nodejs.org/ and re-run this script.
    echo.
    pause
    exit /b 1
)

echo [OK] Found Node:
node --version
echo.

REM ---- 3. Virtual environment ----------------------------------------
set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if exist "%VENV_PY%" (
    echo [OK] Virtual environment already exists - skipping creation.
) else (
    echo [..] Creating virtual environment in .venv ...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        echo         Make sure the "venv" module is available for your Python install.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
)

if not exist "%VENV_PY%" (
    echo [ERROR] Virtual environment python.exe not found at:
    echo         %VENV_PY%
    pause
    exit /b 1
)
echo.

REM ---- 4. Python dependencies, only when they changed -----------------
set "LOCK_FILE=%VENV_DIR%\requirements.lock"
set "NEED_INSTALL=1"

if exist "%LOCK_FILE%" (
    fc /b "%~dp0requirements.txt" "%LOCK_FILE%" >nul 2>nul
    if not errorlevel 1 set "NEED_INSTALL=0"
)

if "%NEED_INSTALL%"=="0" (
    echo [OK] Python dependencies already installed and up to date - skipping.
) else (
    echo [..] Installing Python dependencies ^(first run pulls torch - this takes a while^) ...
    "%VENV_PY%" -m pip install --upgrade pip -q
    "%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install Python dependencies.
        echo         Check your internet connection and try again.
        pause
        exit /b 1
    )
    copy /y "%~dp0requirements.txt" "%LOCK_FILE%" >nul
    echo [OK] Python dependencies installed.
)
echo.

REM ---- 5. Frontend dependencies, only when they changed ---------------
set "UI_DIR=%~dp0frontend"
set "UI_LOCK=%UI_DIR%\node_modules\.interloom-package.lock"
set "NEED_NPM=1"

if exist "%UI_DIR%\node_modules" (
    if exist "%UI_LOCK%" (
        fc /b "%UI_DIR%\package.json" "%UI_LOCK%" >nul 2>nul
        if not errorlevel 1 set "NEED_NPM=0"
    )
)

if "%NEED_NPM%"=="0" (
    echo [OK] Frontend dependencies already installed and up to date - skipping.
) else (
    echo [..] Installing frontend dependencies ...
    pushd "%UI_DIR%"
    call npm install
    if errorlevel 1 (
        echo.
        echo [ERROR] npm install failed.
        popd
        pause
        exit /b 1
    )
    popd
    copy /y "%UI_DIR%\package.json" "%UI_LOCK%" >nul
    echo [OK] Frontend dependencies installed.
)
echo.

REM ---- 6. Embedding model --------------------------------------------
REM A cold SentenceTransformer download mid-demo is the single most likely way
REM this project fails in front of an audience. Pay it here, once, where a slow
REM network is visible and recoverable rather than mysterious.
set "MODEL_STAMP=%VENV_DIR%\model.ok"

if exist "%MODEL_STAMP%" (
    echo [OK] Embedding model already cached - skipping warm-up.
) else (
    echo [..] Caching the embedding model ^(first run downloads ~90MB^) ...
    "%VENV_PY%" "%~dp0scripts\warm_models.py"
    if errorlevel 1 (
        echo.
        echo [WARN] The model could not be cached or failed its sanity check.
        echo        InterLoom will still run using the offline TF-IDF fallback,
        echo        with reduced semantic quality. To force it:
        echo            set INTERLOOM_SEMANTIC=tfidf_svd
        echo.
    ) else (
        echo model cached> "%MODEL_STAMP%"
        echo [OK] Embedding model cached and verified.
    )
)
echo.

REM ---- 7. Corpus ------------------------------------------------------
REM Real PDFs in data\ win automatically; the synthetic set is the fallback so
REM the UI always opens in a working state.
if exist "%~dp0data\jd\*.pdf" (
    echo [OK] Real corpus found in data\ - it will be used in preference.
) else (
    if exist "%~dp0fixtures\synthetic\jd_technova.pdf" (
        echo [OK] Sample corpus present.
    ) else (
        echo [..] Generating the sample corpus ...
        "%VENV_PY%" "%~dp0scripts\make_synthetic_corpus.py"
        if errorlevel 1 (
            echo [WARN] Could not generate the sample corpus.
            echo        Upload a JD and resumes through the UI instead.
        ) else (
            echo [OK] Sample corpus generated.
        )
    )
)
echo.

REM ---- 8. Ports -------------------------------------------------------
REM Windows will happily let a second process bind a port that is already being
REM listened on. Both servers then look fine while requests go to whichever the
REM OS prefers - so catch it here rather than let it become a mystery.
call :check_port %API_PORT% "API"
if errorlevel 1 exit /b 1
call :check_port %UI_PORT% "user interface"
if errorlevel 1 exit /b 1

REM ---- 9. External evidence -------------------------------------------
if defined GITHUB_TOKEN (
    echo [OK] GITHUB_TOKEN set - external evidence can use the full 5000/hour rate limit.
) else (
    echo [i ] No GITHUB_TOKEN set. Live GitHub lookups are capped at 60 requests
    echo      an hour, which is not enough for a full pool. The bundled synthetic
    echo      profiles work offline regardless. To raise it:
    echo          set GITHUB_TOKEN=ghp_your_token_here
)
echo.

REM ---- 10. Start both servers -----------------------------------------
echo ================================================
echo   Starting InterLoom
echo     API : http://localhost:%API_PORT%
echo     UI  : http://localhost:%UI_PORT%
echo   Close the two server windows to stop.
echo ================================================
echo.

start "InterLoom API" cmd /k ""%VENV_PY%" -m uvicorn backend.app:app --port %API_PORT%"
start "InterLoom UI" cmd /k "cd /d "%UI_DIR%" && npm run dev"

echo [..] Waiting for the API to answer ...
set "READY="
for /l %%i in (1,1,40) do (
    if not defined READY (
        "%VENV_PY%" -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1',%API_PORT%))==0 else 1)" >nul 2>nul
        if not errorlevel 1 set "READY=1"
    )
)

if defined READY (
    echo [OK] API is up.
) else (
    echo [WARN] The API did not answer in time. Check the "InterLoom API" window.
)

REM Vite binds IPv6 loopback, so open localhost rather than 127.0.0.1 - the
REM latter can refuse the connection even though the server is running.
REM
REM ping rather than timeout: `timeout` aborts outright when stdin is
REM redirected, which happens whenever this script is driven by another tool
REM rather than double-clicked.
ping -n 4 127.0.0.1 >nul 2>nul
start "" "http://localhost:%UI_PORT%"

echo.
echo InterLoom is running. This window can be closed.
echo.
pause
endlocal
exit /b 0

REM ---------------------------------------------------------------------
:check_port
REM %1 = port, %2 = human name
"%VENV_PY%" -c "import socket,sys; s=socket.socket(); s.settimeout(0.4); sys.exit(0 if s.connect_ex(('127.0.0.1',%1))==0 else 1)" >nul 2>nul
if errorlevel 1 (
    echo [OK] Port %1 is free ^(%~2^).
    exit /b 0
)
echo [ERROR] Something is already listening on port %1 ^(%~2^).
echo         Close that program or the earlier InterLoom window, then re-run.
echo.
pause
exit /b 1
