$ErrorActionPreference = "SilentlyContinue"

$BridgeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SecretsPath = Join-Path $BridgeDir ".bridge_secrets.json"
$LogDir = Join-Path $BridgeDir "logs"

$ApiPid = if (Test-Path (Join-Path $LogDir "bridge_api.pid")) { (Get-Content (Join-Path $LogDir "bridge_api.pid") -Raw).Trim() } else { "" }
$CfPid = if (Test-Path (Join-Path $LogDir "cloudflared.pid")) { (Get-Content (Join-Path $LogDir "cloudflared.pid") -Raw).Trim() } else { "" }

$ApiProc = if ($ApiPid -match '^\d+$') { Get-Process -Id ([int]$ApiPid) } else { $null }
$CfProc = if ($CfPid -match '^\d+$') { Get-Process -Id ([int]$CfPid) } else { $null }

Write-Host ("Local FastAPI (127.0.0.1:8788): " + ($(if ($ApiProc) { "RUNNING (PID $ApiPid)" } else { "STOPPED" })))
Write-Host ("Cloudflare Tunnel (cloudflared): " + ($(if ($CfProc) { "RUNNING (PID $CfPid)" } else { "STOPPED" })))

try {
    $local = Invoke-RestMethod -Uri "http://127.0.0.1:8788/local-ping" -TimeoutSec 2
    Write-Host ("Local /local-ping: ok=" + $local.ok)
} catch {
    Write-Host "Local /local-ping: UNREACHABLE"
}

if (Test-Path $SecretsPath) {
    $s = Get-Content -Path $SecretsPath -Raw | ConvertFrom-Json
    try {
        $r2 = Invoke-RestMethod -Uri ($s.r2_url + "/health") -TimeoutSec 5
        Write-Host ("R2 Gateway (" + $s.r2_url + "): ok=" + $r2.ok)
    } catch {
        Write-Host ("R2 Gateway (" + $s.r2_url + "): ERROR - " + $_.Exception.Message)
    }

    try {
        $hdr = @{
            "CF-Access-Client-Id"     = $s.access_client_id
            "CF-Access-Client-Secret" = $s.access_client_secret
            "X-Bridge-Secret"         = $s.bridge_secret
        }
        $pub = Invoke-RestMethod -Uri ($s.bridge_url + "/v1/health") -Headers $hdr -TimeoutSec 6
        Write-Host ("Tunnel + Access (" + $s.bridge_url + "): ok=" + $pub.ok + ", js=" + $pub.js_runtimes + ", ffmpeg=" + $pub.ffmpeg)
    } catch {
        Write-Host ("Tunnel + Access (" + $s.bridge_url + "): ERROR - " + $_.Exception.Message)
    }
}
