<#
build_portable_exe.ps1 -- Builds a standalone portable Windows .exe for LockerDrive v5.0
Usage:
  powershell -ExecutionPolicy Bypass -File setup\build_portable_exe.ps1
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "=== LockerDrive v5.0 Portable .EXE Builder ===" -ForegroundColor Cyan

python -m pip install pyinstaller --quiet

$PyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onedir",
    "--windowed",
    "--name", "KushalStudios",
    "--add-data", "ui;ui",
    "--add-data", "engine;engine",
    "--add-data", "assets;assets",
    "--add-data", "FaceRecog-Engine;FaceRecog-Engine",
    "--hidden-import", "webview",
    "--hidden-import", "argon2",
    "--hidden-import", "win32crypt",
    "--hidden-import", "win32gui",
    "--hidden-import", "win32ts",
    "--hidden-import", "customtkinter",
    "--hidden-import", "pygame",
    "main.py"
)

Write-Host "[*] Compiling portable bundle via PyInstaller..." -ForegroundColor Cyan
python -m PyInstaller @PyInstallerArgs

Write-Host ""
Write-Host "[SUCCESS] Portable executable built at: $Root\dist\KushalStudios\KushalStudios.exe" -ForegroundColor Green
Write-Host "  - Launch Decoy Hub : dist\KushalStudios\KushalStudios.exe --decoy" -ForegroundColor Gray
Write-Host "  - Launch Vault GUI : dist\KushalStudios\KushalStudios.exe --gui" -ForegroundColor Gray
