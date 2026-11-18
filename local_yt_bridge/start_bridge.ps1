$ErrorActionPreference = "Stop"

$BridgeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $BridgeDir
$BackendDir = Join-Path $RepoRoot "backend"
$LogDir = Join-Path $BridgeDir "logs"
$SecretsPath = Join-Path $BridgeDir ".bridge_secrets.json"
$CfExe = Join-Path $BridgeDir "bin\cloudflared.exe"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path $SecretsPath)) {
    throw "Missing $SecretsPath. Provision Cloudflare Tunnel secrets first."
}
if (-not (Test-Path $CfExe)) {
    throw "Missing $CfExe. Download cloudflared-windows-amd64.exe first."
}

$Secrets = Get-Content -Path $SecretsPath -Raw | ConvertFrom-Json
if (-not $Secrets.tunnel_token -or -not $Secrets.bridge_secret) {
    throw "Incomplete credentials in .bridge_secrets.json"
}

$ApiPidFile = Join-Path $LogDir "bridge_api.pid"
$ApiOutLog = Join-Path $LogDir "bridge_api.log"
$ApiErrLog = Join-Path $LogDir "bridge_api.err.log"

$CfPidFile = Join-Path $LogDir "cloudflared.pid"
$CfLogFile = Join-Path $LogDir "cloudflared.log"
$CfErrLog = Join-Path $LogDir "cloudflared.err.log"

# 1. Start local FastAPI YouTube Bridge if not already healthy
$ApiAlive = $false
try {
    $ping = Invoke-RestMethod -Uri "http://127.0.0.1:8788/local-ping" -TimeoutSec 2
    if ($ping.ok -eq $true) { $ApiAlive = $true }
} catch {
    $ApiAlive = $false
}

if (-not $ApiAlive) {
    $PythonExe = (Get-Command python -ErrorAction Stop).Source
    $env:PYTHONPATH = "$RepoRoot;$BackendDir"
    $env:YT_BRIDGE_SERVER_MODE = "1"
    $env:PYTHONUTF8 = "1"

    $apiProc = Start-Process -FilePath $PythonExe `
        -ArgumentList @("-m", "uvicorn", "local_yt_bridge.app:app", "--host", "127.0.0.1", "--port", "8788", "--log-level", "warning") `
        -WorkingDirectory $BackendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $ApiOutLog `
        -RedirectStandardError $ApiErrLog `
        -PassThru

    Set-Content -Path $ApiPidFile -Value $apiProc.Id -Encoding ASCII

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $ping = Invoke-RestMethod -Uri "http://127.0.0.1:8788/local-ping" -TimeoutSec 2
            if ($ping.ok -eq $true) {
                $ready = $true
                break
            }
        } catch {
        }
    }
    if (-not $ready) {
        throw "FastAPI YouTube Bridge failed to listen on 127.0.0.1:8788. Inspect $ApiErrLog"
    }
    Write-Host "[OK] FastAPI YouTube Bridge started (PID=$($apiProc.Id), port=8788)"
} else {
    Write-Host "[OK] FastAPI YouTube Bridge is already running on 127.0.0.1:8788"
}

# 2. Start Cloudflare Named Tunnel if not already running
$CfRunning = $false
if (Test-Path $CfPidFile) {
    $existingCfPid = (Get-Content -Path $CfPidFile -Raw).Trim()
    if ($existingCfPid -match '^\d+$') {
        $p = Get-Process -Id ([int]$existingCfPid) -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -like "cloudflared*") {
            $CfRunning = $true
            Write-Host "[OK] cloudflared tunnel is already running (PID=$existingCfPid)"
        }
    }
}

if (-not $CfRunning) {
    # Pass TUNNEL_TOKEN strictly via process environment, never CLI arguments
    $env:TUNNEL_TOKEN = $Secrets.tunnel_token
    try {
        $cfProc = Start-Process -FilePath $CfExe `
            -ArgumentList @("tunnel", "--no-autoupdate", "--loglevel", "info", "--logfile", $CfLogFile, "--pidfile", $CfPidFile, "run") `
            -WorkingDirectory $BridgeDir `
            -WindowStyle Hidden `
            -RedirectStandardError $CfErrLog `
            -PassThru
    } finally {
        Remove-Item Env:TUNNEL_TOKEN -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path $CfPidFile)) {
        Set-Content -Path $CfPidFile -Value $cfProc.Id -Encoding ASCII
    }
    Start-Sleep -Seconds 3
    $alive = Get-Process -Id $cfProc.Id -ErrorAction SilentlyContinue
    if (-not $alive) {
        throw "cloudflared exited unexpectedly. Inspect $CfErrLog and $CfLogFile"
    }
    Write-Host "[OK] cloudflared Named Tunnel started (PID=$($cfProc.Id))"
}
