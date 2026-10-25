@echo off
title LockerDrive Vault
cd /d "%~dp0"

set "PYW=C:\Users\Default.L-HCG-9FVVGS3\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
set "PY=C:\Users\Default.L-HCG-9FVVGS3\AppData\Local\Python\pythoncore-3.14-64\python.exe"

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
    start "" python main.py --gui
)
exit /b
