<#
prepare_and_push_lockerdrive_repo.ps1 -- Final Production Audit, Cleanup & Git Force-Push
Target Repository: https://github.com/onlykushalll/LockerDrive.git

What this script does:
  1. Removes all temporary/one-off upgrade scripts, logs, build/ caches, and archive/ folders.
  2. Writes a strict production .gitignore so GitHub never rejects the push due to >100MB
     .onnx weights or dist/*.exe binaries (while keeping them untouched on local disk!).
  3. Updates README.md to document the v18.0 Chameleon Single-File Architecture.
  4. Runs the final pytest + run_all_tests.py verification gate.
  5. Creates a clean, bloat-free commit on `main` and force-pushes to origin.
#>
param(
    [string]$RemoteUrl = "https://github.com/onlykushalll/LockerDrive.git",
    [string]$CommitMessage = "feat(v18.0): production Chameleon Kushal Studios / LockerDrive Vault suite (Pure-ONNX 9-angle Face ID, 12-layer PAD, Zero-Trace RAM offload, Single-File EXE)"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "=== LockerDrive v18.0 Final Cleanup, Audit & GitHub Force-Push ===" -ForegroundColor Cyan

# 1. Remove any temporary one-off scripts in setup/ and root clutter
$KeepSetupFiles = @(
    "build_all_in_one_exe.ps1",
    "build_portable_exe.ps1",
    "check_env.py",
    "create_encrypted_volume.ps1",
    "install_face.ps1",
    "prepare_and_push_lockerdrive_repo.ps1",
    "register_vault_password.py",
    "restore_real_vault_state.py",
    "run_all_tests.py",
    "sync_facerecog_to_utils_repo.ps1",
    "test_hardening.py",
    "test_redteam_fixes.py",
    "test_session_watch.py",
    "verify_mount.ps1"
)

Get-ChildItem -Path (Join-Path $Root "setup") -File | ForEach-Object {
    if ($KeepSetupFiles -notcontains $_.Name) {
        Remove-Item -Force $_.FullName -ErrorAction SilentlyContinue
        Write-Host "[CLEANED] Removed temp setup script: $($_.Name)" -ForegroundColor Yellow
    }
}

foreach ($junk in @("archive", "build", "LockerDrive.spec", "ui_stderr.log", "ui_stdout.log")) {
    $jp = Join-Path $Root $junk
    if (Test-Path $jp) {
        Remove-Item -Recurse -Force $jp -ErrorAction SilentlyContinue
        Write-Host "[CLEANED] Removed: $junk" -ForegroundColor Yellow
    }
}

# Clean __pycache__ and .pytest_cache across source folders
Get-ChildItem -Path $Root -Include "__pycache__", ".pytest_cache", "facerecog_engine.egg-info" -Directory -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch "\\dist\\" } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 2. Write strict production .gitignore (keeps dist/ and >100MB .onnx files on disk, out of git index)
$GitIgnoreContent = @'
# Byte-compiled / cache / test artifacts
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/
*.egg-info/
*.log
*.tmp

# Compiled Executables & PyInstaller build directories (kept locally in dist/)
build/
dist/
archive/

# ONNX Model Weights (>100MB; downloaded via FaceRecog-Engine/download_models.py)
FaceRecog-Engine/models/**/*.onnx
FaceRecog-Engine/models/**/*.zip
!FaceRecog-Engine/models/.gitkeep

# Local runtime state & vault secrets (never commit user secrets/scores)
settings.json
stats.json
*.vhdx
*.secret
*.pkl
*.dpapi
RECOVERY_KEY*.txt
'@

Set-Content -Path (Join-Path $Root ".gitignore") -Value $GitIgnoreContent.TrimStart() -Encoding UTF8
New-Item -ItemType Directory -Path (Join-Path $Root "FaceRecog-Engine\models") -Force | Out-Null
Set-Content -Path (Join-Path $Root "FaceRecog-Engine\models\.gitkeep") -Value "" -Encoding ASCII
Write-Host "[OK] Updated .gitignore and FaceRecog-Engine/models/.gitkeep" -ForegroundColor Green

# 3. Ensure Kushal Studios.spec excludes *.onnx from UPX compression for fast, bit-exact builds
$SpecPath = Join-Path $Root "Kushal Studios.spec"
if (Test-Path $SpecPath) {
    $specTxt = Get-Content $SpecPath -Raw
    $specTxt = $specTxt.Replace("upx_exclude=[]", "upx_exclude=['*.onnx', 'onnxruntime*.dll']")
    Set-Content -Path $SpecPath -Value $specTxt -Encoding UTF8
}

# 4. Run Final Verification Gate (FaceRecog-Engine Pytest + LockerDrive Master Test Suite)
Write-Host "[*] Running FaceRecog-Engine v2.0 pytest suite..." -ForegroundColor Cyan
python -m pytest FaceRecog-Engine\tests -q
if ($LASTEXITCODE -ne 0) { throw "FaceRecog-Engine pytest failed!" }

Write-Host "[*] Running LockerDrive Master Verification Suite..." -ForegroundColor Cyan
python setup\run_all_tests.py
if ($LASTEXITCODE -ne 0) { throw "LockerDrive master test suite failed!" }

# 5. Check compiled Single-File Executable if present
$CompiledExe = Join-Path $Root "dist\Kushal Studios.exe"
if (Test-Path $CompiledExe) {
    $ExeMB = [math]::Round((Get-Item $CompiledExe).Length / 1MB, 1)
    Write-Host "[OK] Verified Single-File Executable on disk: dist\Kushal Studios.exe ($ExeMB MB)" -ForegroundColor Green
}

# 6. Build a 100% clean, bloat-free Git history (removes any old cached >100MB blobs from index without touching local disk)
Write-Host "[*] Preparing clean Git index & force-pushing to $RemoteUrl..." -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $Root ".git"))) {
    git init
}

$CurrentRemotes = git remote
if ($CurrentRemotes -contains "origin") {
    git remote set-url origin $RemoteUrl
} else {
    git remote add origin $RemoteUrl
}

# Create a fresh orphan branch so old cached binaries/models in previous commits never block GitHub push
git checkout --orphan _clean_v18_main
git rm -rf --cached . 2>$null | Out-Null
git add .

Write-Host "[*] Staged files for GitHub:" -ForegroundColor Cyan
git status -s

git commit -m $CommitMessage
git branch -M main
git push --force origin main

Write-Host ""
Write-Host "[SUCCESS] Force-pushed clean production LockerDrive v18.0 to $RemoteUrl !" -ForegroundColor Green
