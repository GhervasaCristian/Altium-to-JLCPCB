@echo off
setlocal enabledelayedexpansion

:: Check Python installation
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not found in PATH.
    echo Please install Python to use this converter.
    pause
    exit /b 1
)

set "SCRIPT_DIR=%~dp0"
set "PYTHON_SCRIPT=%SCRIPT_DIR%altium_to_jlcpcb.py"

if not exist "%PYTHON_SCRIPT%" (
    echo [ERROR] Python script not found at "%PYTHON_SCRIPT%"
    pause
    exit /b 1
)

:: Run script passing any dragged-and-dropped arguments, or start interactive mode
if "%~1" neq "" (
    python "%PYTHON_SCRIPT%" %*
) else (
    python "%PYTHON_SCRIPT%"
)
