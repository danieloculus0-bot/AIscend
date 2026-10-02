$ErrorActionPreference = "Stop"

Write-Host "Building Windows executable..." -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --noconsole `
  --name "AutonomousCapitalLab" `
  main.py

Write-Host ""
Write-Host "Build complete: dist\AutonomousCapitalLab.exe" -ForegroundColor Green
