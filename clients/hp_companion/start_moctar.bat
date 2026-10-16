@echo off
title M.O.C.T.A.R - Compagnon Vocal Natif HP
setlocal

cd /d "%~dp0..\.."
if exist "venv\Scripts\python.exe" (
    set "PYTHON_BIN=venv\Scripts\python.exe"
) else (
    set "PYTHON_BIN=python"
)

if "%MOCTAR_SERVER_URL%"=="" (
    set "MOCTAR_SERVER_URL=https://moctar.klasora.com"
)

echo ============================================================
echo   M.O.C.T.A.R - Agent Vocal Compagnon HP (Windows)
echo   Serveur cible : %MOCTAR_SERVER_URL%
echo   Mots-cles     : "Moctar", "Yo Moctar", "ELMo"
echo ============================================================

"%PYTHON_BIN%" "clients\hp_companion\moctar_hp_agent.py" --server "%MOCTAR_SERVER_URL%"
pause

