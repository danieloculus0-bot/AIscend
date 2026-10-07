$ErrorActionPreference = "Stop"

$tailscaleIp = $null
try {
    $tailscaleIp = (& tailscale ip -4 2>$null | Select-Object -First 1).Trim()
} catch {
    $tailscaleIp = $null
}

Write-Host ""
Write-Host "AIscend remote monitor"
Write-Host "----------------------"
if ($tailscaleIp) {
    Write-Host "From another PC on your Tailnet:"
    Write-Host ("http://" + $tailscaleIp + ":8000/monitor")
} else {
    Write-Host "Tailscale not detected. The app still listens on 0.0.0.0:8000."
    Write-Host "Install/connect Tailscale on both PCs for simple private remote access."
}
if ($env:AISCEND_REMOTE_TOKEN) {
    Write-Host "Remote token protection: ON"
    Write-Host "Append ?token=<your token> to the monitor URL."
} else {
    Write-Host "Remote token protection: OFF (fine for a private Tailnet)."
}
Write-Host ""

python web_app.py
