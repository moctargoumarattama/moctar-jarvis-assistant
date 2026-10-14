@echo off
cd /d "%~dp0"
if exist venv\Scripts\python.exe (
    venv\Scripts\python.exe main_jarvis.py %*
) else (
    call venv\Scripts\activate.bat 2>nul
    python main_jarvis.py %*
)
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERREUR] Le processus s'est arrete avec le code %ERRORLEVEL%.
    pause
)