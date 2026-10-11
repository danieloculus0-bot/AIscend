param(
  [switch]$Once,
  [int]$IntervalSeconds = 900,
  [string]$Model = ""
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to publish BEAN's tested engineering changes."
}
Set-Location $Root
if ($Model) { $env:BEAN_OLLAMA_MODEL = $Model }

Write-Host ""
Write-Host "BEAN AUTO ENGINEER - AIscend" -ForegroundColor Green
Write-Host "Local Ollama, PowerShell, regression testing, automatic Git adoption."
Write-Host "No per-iteration human approval. Press Ctrl+C to stop."
Write-Host "Lessons: LocalAppData/AutonomousCapitalLab/bean-autonomous-engineer.jsonl"
Write-Host "A candidate must compile and pass AIscend tests before being adopted."
Write-Host ""

$ArgsList = @("-m", "scripts.bean_autonomous_engineer", "--repo", $Root,
              "--python", $Python, "--build", "--push")
if ($Once) { Write-Host "Executing one iteration"; }
else { $ArgsList += @("--loop", "--interval", "$IntervalSeconds") }

& $Python @ArgsList
exit $LASTEXITCODE
