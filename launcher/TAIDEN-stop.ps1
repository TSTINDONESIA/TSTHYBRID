# Hentikan TAIDEN: minta server berhenti baik-baik, lalu matikan sisa proses (termasuk watchdog) milik folder ini.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
try { Invoke-WebRequest -UseBasicParsing -Method Post -Headers @{ "X-TAIDEN" = "1" } -Uri "http://127.0.0.1:8000/api/shutdown" -TimeoutSec 3 | Out-Null } catch {}
Start-Sleep -Seconds 2
$me = $PID
Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $me -and $_.CommandLine -and $_.CommandLine -like "*$root*" -and
  ($_.CommandLine -like "*server.py*" -or $_.CommandLine -like "*TAIDEN.bat*")
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
