param(
    [ValidateSet("base-sepolia", "base")]
    [string]$Network = "base-sepolia",

    [string]$KeyFile
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv")) {
    Write-Host "Creating Python virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host "Installing/updating dependencies..." -ForegroundColor Cyan
& $Python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

Write-Host ""
Write-Host "Launching CDP credential setup..." -ForegroundColor Cyan

$Args = @("scripts\cdp_wallet.py", "--network", $Network, "configure")
if ($KeyFile) {
    $Args += @("--key-file", $KeyFile)
}

& $Python @Args
if ($LASTEXITCODE -ne 0) { throw "CDP configuration failed." }

Write-Host ""
Write-Host "AIscend wallet configured." -ForegroundColor Green
Write-Host "Check it with:"
Write-Host "  .\.venv\Scripts\python.exe scripts\cdp_wallet.py --network $Network status"
