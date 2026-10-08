$ErrorActionPreference = "Stop"

if (-not $env:AISCEND_REMOTE_TOKEN) {
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $env:AISCEND_REMOTE_TOKEN = ([BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
}

$tailscaleIp = $null
try {
    $tailscaleIp = (& tailscale ip -4 2>$null | Select-Object -First 1).Trim()
} catch {
    $tailscaleIp = $null
}

$lanIp = $null
try {
    $lanIp = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object {
            $_.IPAddress -notmatch '^127\.' -and
            $_.IPAddress -notmatch '^169\.254\.' -and
            $_.InterfaceAlias -notmatch 'Loopback'
        } |
        Sort-Object InterfaceMetric |
        Select-Object -ExpandProperty IPAddress -First 1
} catch {
    $lanIp = $null
}

Write-Host ""
Write-Host "AIscend read-only remote monitor"
Write-Host "--------------------------------"
if ($tailscaleIp) {
    Write-Host ("Tailnet: http://" + $tailscaleIp + ":8000/monitor?token=" + $env:AISCEND_REMOTE_TOKEN)
}
if ($lanIp) {
    Write-Host ("Home LAN: http://" + $lanIp + ":8000/monitor?token=" + $env:AISCEND_REMOTE_TOKEN)
}
Write-Host ""
Write-Host "Remote requests are restricted to the monitor/status/candle endpoints."
Write-Host "Trading, bridge, credential, start/stop, and full-dashboard routes are blocked remotely."
Write-Host "For access away from home, use a private Tailnet or an HTTPS tunnel in front of /monitor."
Write-Host ""

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython (Join-Path $repoRoot "web_app.py")
} else {
    python (Join-Path $repoRoot "web_app.py")
}
