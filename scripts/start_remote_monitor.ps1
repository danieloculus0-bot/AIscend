$ErrorActionPreference = "Stop"

$tailscaleIp = $null
try {
    $tailscaleIp = (& tailscale ip -4 2>$null | Select-Object -First 1).Trim()
} catch {
    $tailscaleIp = $null
}

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

python web_app.py
