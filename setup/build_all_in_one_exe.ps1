<#
build_all_in_one_exe.ps1 -- Builds the Production v18.0 Single-File Executable:
  dist\Kushal Studios.exe
  - ONE single .exe file (no visible _internal folder next to it!)
  - Move or delete 'Kushal Studios.exe' and 100% of its internals/models move with it!
  - Uses a persistent hidden %LOCALAPPDATA%\KushalStudios\.rt_v18 cache so startup is instant
  - Includes only the 4 active ONNX models (det_10g, w600k_r50, midas_v21_small, minifasv2)
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "=== Building v18.0 True Single-File 'dist\Kushal Studios.exe' ===" -ForegroundColor Cyan

# 1. Stop any running instance or daemon before cleaning dist/
Get-Process -Name "Kushal Studios", "LockerDrive" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
python -c "import sys; sys.path.insert(0, 'engine'); import vault_mount; vault_mount.stop_elevated_daemon()" 2>$null

# 2. Clean all old builds & folders
foreach ($d in @("build", "dist\LockerDrive", "dist\Kushal Studios", "dist\Kushal Studios.exe", "LockerDrive.spec")) {
    if (Test-Path $d) { Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue }
}

# 3. Write clean Single-File Kushal Studios.spec (bundles ONLY the 4 active ONNX models + assets)
$SpecContent = @'
# -*- mode: python ; coding: utf-8 -*-
import os
import sys
sys.setrecursionlimit(20000)

from PyInstaller.utils.hooks import collect_all

datas = [
    ('ui/index.html', 'ui'),
    ('assets', 'assets'),
    ('FaceRecog-Engine/models/antispoof_minifasv2.onnx', 'FaceRecog-Engine/models'),
    ('FaceRecog-Engine/models/midas_v21_small_256.onnx', 'FaceRecog-Engine/models'),
    ('FaceRecog-Engine/models/models/buffalo_l/det_10g.onnx', 'FaceRecog-Engine/models/models/buffalo_l'),
    ('FaceRecog-Engine/models/models/buffalo_l/w600k_r50.onnx', 'FaceRecog-Engine/models/models/buffalo_l'),
]
binaries = []
hiddenimports = [
    'webview', 'argon2', 'win32crypt', 'win32gui', 'win32ts', 'win32con', 'win32api',
    'customtkinter', 'pygame', 'onnxruntime', 'cv2', 'anti_re',
    'ai_regex', 'access_log', 'config', 'face_auth', 'local_anchor', 'pin_auth',
    'recovery_auth', 'session_watch', 'state_binding', 'vault_mount', 'vault_secret',
    '_elevated_helper', 'ui.app', 'ui.decoy_app', 'ui.appdata', 'ui.games',
    'facerecog_engine', 'facerecog_engine.enrollment', 'facerecog_engine.template_store'
]

for pkg in ('onnxruntime', 'customtkinter', 'webview'):
    d, b, h = collect_all(pkg)
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)

a = Analysis(
    ['main.py'],
    pathex=['.', 'engine', 'ui', 'FaceRecog-Engine'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'simsimd', 'albumentations', 'scipy', 'pandas', 'pyarrow',
        'numba', 'llvmlite', 'torch', 'torchvision', 'matplotlib',
        'IPython', 'notebook', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6'
    ],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [('O', None, 'OPTION'), ('O', None, 'OPTION')],
    name='Kushal Studios',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/kushal_studios_icon.ico'],
)
'@

Set-Content -Path (Join-Path $Root "Kushal Studios.spec") -Value $SpecContent -Encoding UTF8

Write-Host "[*] Compiling True Single-File 'dist\Kushal Studios.exe'..." -ForegroundColor Cyan
python -OO -m PyInstaller --noconfirm --clean "Kushal Studios.spec"
Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue

$SingleExe = Join-Path $Root "dist\Kushal Studios.exe"
if (Test-Path $SingleExe) {
    $SizeMB = [math]::Round((Get-Item $SingleExe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "[SUCCESS] Built True Single-File Portable App: $SingleExe ($SizeMB MB)" -ForegroundColor Green
    Write-Host "  -> 0 extra folders next to it -- move or delete 'Kushal Studios.exe' and everything moves with it!" -ForegroundColor Green
}