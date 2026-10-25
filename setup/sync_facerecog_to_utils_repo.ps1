<#
sync_facerecog_to_utils_repo.ps1 -- Syncs the upgraded LockerDrive\FaceRecog-Engine
directly into https://github.com/onlykushalll/Utils/tree/main/~FaceRecog-Engine~
so you can clone/pull it into any project anytime.
#>
param(
    [string]$UtilsRepoUrl = "https://github.com/onlykushalll/Utils.git",
    [string]$CommitMessage = "feat(FaceRecog-Engine): upgrade to v2.0 (9-angle 3D burst enrollment, outlier pruning, Python 3.14 attention/rPPG, simsimd bypass, TemplateStore)"
)

$ErrorActionPreference = "Stop"
$LockerRoot = Split-Path $PSScriptRoot -Parent
$SourceEngineDir = Join-Path $LockerRoot "FaceRecog-Engine"
$TempSyncDir = Join-Path $env:TEMP "Utils_FaceRecog_Sync_$(Get-Random)"

Write-Host "=== Syncing FaceRecog-Engine v2.0 to $UtilsRepoUrl (~FaceRecog-Engine~) ===" -ForegroundColor Cyan

if (Test-Path $TempSyncDir) {
    Remove-Item -Recurse -Force $TempSyncDir
}

# 1. Sparse-clone onlykushalll/Utils (~FaceRecog-Engine~ folder) for fast sync
git clone --depth 1 --filter=blob:none --sparse $UtilsRepoUrl $TempSyncDir
Push-Location $TempSyncDir
try {
    git sparse-checkout set "~FaceRecog-Engine~"
    $TargetDir = Join-Path $TempSyncDir "~FaceRecog-Engine~"
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

    # 2. Mirror source files from LockerDrive\FaceRecog-Engine -> Utils\~FaceRecog-Engine~
    #    Excludes .pytest_cache, __pycache__, *.egg-info, and heavy .onnx model weights (downloaded via download_models.py)
    $RobocopyArgs = @(
        $SourceEngineDir,
        $TargetDir,
        "/MIR",
        "/XD", ".git", "__pycache__", ".pytest_cache", "facerecog_engine.egg-info", "models",
        "/XF", "*.pyc", "*.pyo", "*.onnx", "*.tmp"
    )
    & robocopy @RobocopyArgs | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "Robocopy failed with exit code $LASTEXITCODE"
    }

    # Ensure models directory has a .gitkeep so download_models.py works out of the box
    $ModelsDir = Join-Path $TargetDir "models"
    New-Item -ItemType Directory -Path $ModelsDir -Force | Out-Null
    Set-Content -Path (Join-Path $ModelsDir ".gitkeep") -Value "" -Encoding ASCII

    git status -s
    git add "~FaceRecog-Engine~"
    $DiffCheck = git status --porcelain
    if (-not $DiffCheck) {
        Write-Host "[OK] ~FaceRecog-Engine~ in onlykushalll/Utils is already up to date!" -ForegroundColor Green
    } else {
        git commit -m $CommitMessage
        git push origin main
        Write-Host "[SUCCESS] Pushed FaceRecog-Engine v2.0 to https://github.com/onlykushalll/Utils/tree/main/~FaceRecog-Engine~" -ForegroundColor Green
    }
} finally {
    Pop-Location
    if (Test-Path $TempSyncDir) {
        Remove-Item -Recurse -Force $TempSyncDir -ErrorAction SilentlyContinue
    }
}
