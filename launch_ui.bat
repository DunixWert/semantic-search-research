@echo off
title Literature Library UI (Semantic Search)

set "REPO_DIR=%~dp0"
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"
cd /d "%REPO_DIR%"

set "PYTHON_EXE=python"
if exist "%REPO_DIR%\..\Starting_Model\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%REPO_DIR%\..\Starting_Model\.venv\Scripts\python.exe"
) else if exist "%REPO_DIR%\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%REPO_DIR%\.venv\Scripts\python.exe"
)

"%PYTHON_EXE%" "%REPO_DIR%\lit.py" ui

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo An error occurred while launching Literature Library UI.
    pause
)
