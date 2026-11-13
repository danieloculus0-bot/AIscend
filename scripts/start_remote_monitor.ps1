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
if (-not $tailscaleIp) {
    throw "Tailscale must be connected before exposing the AIscend monitor."
}
if ([string]::IsNullOrWhiteSpace($env:AISCEND_REMOTE_TOKEN)) {
    throw "Set AISCEND_REMOTE_TOKEN before starting the remote monitor. Remote access must be authenticated."
}

# Never publish trading controls on the LAN. The Flask app also rejects
# non-loopback requests except token-authenticated, read-only monitor GETs.
$env:AISCEND_BIND_HOST = $tailscaleIp

Write-Host ""
Write-Host "AIscend remote monitor (read-only)"
Write-Host "----------------------------------"
Write-Host ("Tailnet monitor: http://" + $tailscaleIp + ":8000/monitor")
Write-Host "Append ?token=<your token> to the monitor URL."
Write-Host "Trading controls remain local-only."
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
