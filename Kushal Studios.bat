@echo off
title Kushal Studios
cd /d "%~dp0"

set "PYW=C:\Users\Default.L-HCG-9FVVGS3\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
set "PY=C:\Users\Default.L-HCG-9FVVGS3\AppData\Local\Python\pythoncore-3.14-64\python.exe"

if /i "%~1"=="--debug" (
    "%PY%" main.py --decoy
    pause
    exit /b
)

if exist "%PYW%" (
    start "" "%PYW%" main.py --decoy
) else (
    start "" python main.py --decoy
)
exit /b
