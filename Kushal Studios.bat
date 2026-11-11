@echo off
title Kushal Studios
cd /d "%~dp0"

set "PYW=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"

if /i "%~1"=="--debug" (
    "%PY%" main.py --decoy
    pause
    exit /b
)

if exist "%PYW%" (
    start "" "%PYW%" main.py --decoy
) else (
    start "" "%PY%" main.py --decoy
)
exit /b
