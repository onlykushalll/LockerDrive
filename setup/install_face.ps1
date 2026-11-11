Set-Location (Join-Path $PSScriptRoot "..\FaceRecog-Engine")
Write-Output "=== Installing FaceRecog-Engine deps ==="
pip install -r requirements.txt 2>&1 | Select-Object -Last 5
Write-Output "=== Installing package ==="
pip install -e . 2>&1 | Select-Object -Last 3
Write-Output "=== Downloading models ==="
python download_models.py 2>&1 | Select-Object -Last 10
Write-Output "=== Verifying ==="
python -c "from facerecog_engine import FaceSecurityEngine, SecurityConfig; print('FaceRecog OK')" 2>&1
Write-Output "FACE_INSTALL_DONE"
