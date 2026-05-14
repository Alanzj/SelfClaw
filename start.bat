@echo off
chcp 65001 >nul
title OpenClaw AI System v36.2 Hermes
echo ========================================
echo     OpenClaw AI System v36.2 Hermes
echo ========================================
echo.
set "PYTHON_HOME=C:\Program Files\python"
set "PYTHON_SCRIPTS=%PYTHON_HOME%\Scripts"
set "SYSTEM_NODE=C:\Program Files\nodejs"
set "NATIVE_NPM=C:\Users\Lenovo\AppData\Roaming\npm"
set "OPENCLAW_NATIVE_MODE=1"
set "PATH=%PYTHON_HOME%;%PYTHON_SCRIPTS%;%SYSTEM_NODE%;%NATIVE_NPM%;%PATH%"
echo [OK] Environment loaded
echo.
echo [1/6] Cleaning ports 8502 19999 29999 5230
for %%p in (8502,19999,29999,5230) do (
    netstat -ano | findstr ":%%p" | findstr "LISTENING" >nul 2>&1
    if not errorlevel 1 (
        for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%p" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
    )
)
timeout /t 2 /nobreak >nul
echo.
echo [Clear Streamlit cache]
if exist "%TEMP%\streamlit" rd /s /q "%TEMP%\streamlit"
if exist "D:\MyAI_Model\.streamlit" rd /s /q "D:\MyAI_Model\.streamlit"
echo.
echo [2/6] Loading environment variables
cd /d "D:\MyAI_Model"
if exist "keys.env" (
    for /f "usebackq tokens=1,* delims==" %%i in ("keys.env") do set "%%i=%%j"
    echo   Environment variables loaded
)
echo.

:: ========== 新增：清理 Python 模块缓存 ==========
echo [2.5/6] Clearing Python __pycache__
cd /d "D:\MyAI_Model"
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
echo   Python cache cleared
echo.
:: ================================================

echo [3/6] Starting MemOS (5230)
start /B cmd /c ""D:\MyAI_Model\memos\memos.exe" --port 5230 --data "D:\MyAI_Model\memos\data""
timeout /t 5 /nobreak >nul
echo.
echo [4/6] Starting CCR Hermes routing service
cd /d "D:\MyAI_Model\CCR"
if not exist "node_modules\@musistudio\claude-code-router" (
    echo   Installing CCR dependencies...
    call npm install --registry=https://registry.npmmirror.com >nul 2>&1
    if %errorlevel% neq 0 (
        call npm install --registry=https://registry.npmjs.org >nul 2>&1
    )
)
start /B cmd /c "npm exec claude-code-router start --port 29999 --config config-router_bak.json"
timeout /t 8 /nobreak >nul
echo.
echo [5/6] Starting Gateway (19999) + WebUI (8502)
start /B cmd /c "cd /d D:\MyAI_Model && python gateway.py"
timeout /t 5 /nobreak >nul
start /B cmd /c "cd /d D:\MyAI_Model && streamlit run DS/main.py --server.port 8502 --server.headless false"
timeout /t 8 /nobreak >nul
echo.
echo [6/6] Starting native OpenClaw Gateway (18789)
netstat -ano | findstr ":18789.*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo   18789 already running, skipping
) else (
    cd /d "D:\MyAI_Model"
    set "OPENCLAW_CONFIG_PATH=C:\Users\Lenovo\.openclaw\openclaw.json"
    start /B node "%NATIVE_NPM%\node_modules\openclaw\openclaw.mjs" gateway --port 18789
    timeout /t 3 /nobreak >nul
)
echo.
echo ========================================
echo   Hermes Agent startup complete!
echo   Web UI : http://127.0.0.1:8502
echo ========================================
pause >nul
start http://127.0.0.1:8502
