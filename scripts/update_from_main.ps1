# Run from the AIscend repository folder after changes are merged into main.
# Updates the local Python environment; does not place trades or start the bot.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$dirty = git status --porcelain
if ($LASTEXITCODE -ne 0) { throw 'Git is not available.' }
if ($dirty) { throw 'Local changes present. Save or commit them before updating.' }
$branch = (git branch --show-current).Trim()
if ($branch -ne 'main') { throw "Expected main, found $branch. Switch to main manually." }
git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) { throw 'Could not fast-forward main.' }
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 environment could not be created.' }
}
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependencies failed to install.' }
& $python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Regression tests failed. Do not start live trading.' }
& $python -m compileall -q src scripts
if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed.' }
Write-Host 'AIscend main and local Python dependencies are updated and tests passed.'
Write-Host 'Review results and restart AIscend manually when ready.'
