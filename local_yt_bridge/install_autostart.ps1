$ErrorActionPreference = "Stop"

$BridgeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartScript = Join-Path $BridgeDir "start_bridge.ps1"
$StartupDir = [Environment]::GetFolderPath("Startup")
$VbsPath = Join-Path $StartupDir "OmniVoiceYTBridge.vbs"

$vbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""$StartScript""", 0, False
"@

Set-Content -Path $VbsPath -Value $vbsContent -Encoding ASCII
Write-Host "[OK] Installed Windows Startup silent launcher: $VbsPath"

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript
