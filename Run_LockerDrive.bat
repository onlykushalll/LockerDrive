@echo off
title LockerDrive Vault
cd /d "%~dp0"

set "PYW=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"

if /i "%~1"=="--test" (
    "%PY%" setup\run_all_tests.py
    pause
    exit /b %errorlevel%
)

if /i "%~1"=="--debug" (
    "%PY%" main.py --gui
    pause
    exit /b
)

if exist "%PYW%" (
    start "" "%PYW%" main.py --gui
) else (
    start "" "%PY%" main.py --gui
)
exit /b
