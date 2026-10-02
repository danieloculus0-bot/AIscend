param(
    [switch]$SkipInstall,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Version = (Get-Content "$Root\VERSION" -Raw).Trim()
if (-not $Version) {
    throw "VERSION is empty."
}

$AppName = if ($env:AUTOCAPITAL_BUILD_NAME) {
    $env:AUTOCAPITAL_BUILD_NAME
} else {
    "AutonomousCapitalLab"
}

$SafeName = ($AppName -replace '[^A-Za-z0-9._-]', '')
if (-not $SafeName) {
    throw "AUTOCAPITAL_BUILD_NAME produced an invalid artifact name."
}

$BuildRoot = "$Root\build"
$WorkDir = "$BuildRoot\pyinstaller"
$SpecDir = "$BuildRoot\spec"
$PackageDir = "$BuildRoot\package"
$DistDir = "$Root\dist"
$ExePath = "$DistDir\$SafeName.exe"
$ZipPath = "$DistDir\$SafeName-v$Version-windows-x64.zip"
$HashPath = "$ZipPath.sha256"
$ManifestPath = "$PackageDir\build-manifest.txt"

Write-Host ""
Write-Host "$AppName v$Version" -ForegroundColor Cyan
Write-Host "Windows build pipeline" -ForegroundColor DarkGray
Write-Host ""

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found on PATH."
}

Write-Host "[1/6] Cleaning previous build output..." -ForegroundColor Cyan
Remove-Item $WorkDir, $SpecDir, $PackageDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $ExePath, $ZipPath, $HashPath -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $WorkDir, $SpecDir, $PackageDir, $DistDir | Out-Null

if (-not $SkipInstall) {
    Write-Host "[2/6] Installing build dependencies..." -ForegroundColor Cyan
    python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }

    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
} else {
    Write-Host "[2/6] Dependency install skipped." -ForegroundColor DarkGray
}

if (-not $SkipTests) {
    Write-Host "[3/6] Running regression tests..." -ForegroundColor Cyan
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "Regression tests failed." }

    Write-Host "[4/6] Compiling Python sources..." -ForegroundColor Cyan
    python -m compileall -q main.py src tests
    if ($LASTEXITCODE -ne 0) { throw "Python compile check failed." }
} else {
    Write-Host "[3/6] Regression tests skipped." -ForegroundColor Yellow
    Write-Host "[4/6] Compile check skipped." -ForegroundColor Yellow
}

Write-Host "[5/6] Building single-file Windows executable..." -ForegroundColor Cyan
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noconsole `
    --name $SafeName `
    --workpath $WorkDir `
    --specpath $SpecDir `
    --distpath $DistDir `
    main.py

if ($LASTEXITCODE -ne 0 -or -not (Test-Path $ExePath)) {
    throw "PyInstaller did not produce $ExePath"
}

Write-Host "[6/6] Packaging artifact and checksum..." -ForegroundColor Cyan
Copy-Item $ExePath "$PackageDir\$SafeName.exe"
Copy-Item "$Root\README.md" "$PackageDir\README.md"
Copy-Item "$Root\ARCHITECTURE.md" "$PackageDir\ARCHITECTURE.md"
Copy-Item "$Root\VERSION" "$PackageDir\VERSION"

$Commit = "unknown"
try {
    $Commit = (git rev-parse --short HEAD 2>$null).Trim()
} catch {
    $Commit = "unknown"
}

@"
Application: $AppName
Artifact: $SafeName.exe
Version: $Version
Platform: Windows x64
Commit: $Commit
Built UTC: $([DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"))
"@ | Set-Content -Path $ManifestPath -Encoding UTF8

Compress-Archive -Path "$PackageDir\*" -DestinationPath $ZipPath -Force

$Hash = (Get-FileHash -Path $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $(Split-Path $ZipPath -Leaf)" | Set-Content -Path $HashPath -Encoding ASCII

Write-Host ""
Write-Host "BUILD PASSED" -ForegroundColor Green
Write-Host "EXE:    $ExePath"
Write-Host "ZIP:    $ZipPath"
Write-Host "SHA256: $HashPath"
