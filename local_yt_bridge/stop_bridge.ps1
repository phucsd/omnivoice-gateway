$ErrorActionPreference = "SilentlyContinue"

$BridgeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $BridgeDir "logs"

foreach ($pidFileName in @("cloudflared.pid", "bridge_api.pid")) {
    $pidPath = Join-Path $LogDir $pidFileName
    if (Test-Path $pidPath) {
        $pidVal = (Get-Content -Path $pidPath -Raw).Trim()
        if ($pidVal -match '^\d+$') {
            Stop-Process -Id ([int]$pidVal) -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -Path $pidPath -Force -ErrorAction SilentlyContinue
    }
}

# Cleanup any orphaned bridge uvicorn or local cloudflared processes under this folder
$CfExeNorm = (Join-Path $BridgeDir "bin\cloudflared.exe").ToLowerInvariant()
Get-CimInstance Win32_Process | ForEach-Object {
    $cmd = ($_.CommandLine | Out-String).Trim()
    $exe = ($_.ExecutablePath | Out-String).Trim().ToLowerInvariant()
    if ($exe -eq $CfExeNorm -or $cmd -match "local_yt_bridge\.app") {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "[OK] Stopped Windows YouTube Bridge and cloudflared tunnel."
