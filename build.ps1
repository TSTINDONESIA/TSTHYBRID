# Membuat TAIDEN-Setup.exe. Jalankan lewat BUILD.bat (butuh internet, hanya sekali).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PyVer = "3.12.8"
$WhisperSize = "small"      # tiny / base / small / medium (makin besar makin akurat & berat)
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Out  = Join-Path $Here "build\TAIDEN"
$Py   = Join-Path $Out "python\python.exe"
New-Item -ItemType Directory -Force -Path $Out | Out-Null

function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Run($exe) {
  & $exe @args
  if ($LASTEXITCODE -ne 0) { throw "Perintah gagal (kode $LASTEXITCODE): $exe $args" }
}

# 1. Python portable
if (-not (Test-Path $Py)) {
  Step "1/6 Mengunduh Python $PyVer portable"
  $zip = Join-Path $Here "build\python-embed.zip"
  Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip" -OutFile $zip
  Expand-Archive $zip -DestinationPath (Join-Path $Out "python") -Force
  Remove-Item $zip
  $pth = Get-ChildItem (Join-Path $Out "python") -Filter "python*._pth" | Select-Object -First 1
  Set-Content $pth.FullName -Encoding ASCII -Value @("$($pth.BaseName).zip", ".", "Lib\site-packages", "import site")
  $gp = Join-Path $Out "get-pip.py"
  Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $gp
  Run $Py $gp --no-warn-script-location
  Remove-Item $gp
}

# 2. Library
$mark = Join-Path $Out "python\.deps_ok"
if (-not (Test-Path $mark)) {
  Step "2/6 Memasang library (aiohttp, whisper, argos, dll) - agak lama"
  Run $Py -m pip install -r (Join-Path $Here "requirements-build.txt") --no-warn-script-location
  New-Item -ItemType File $mark | Out-Null
}

# 3. Cek import
Step "3/6 Memeriksa library"
Run $Py -c "import aiohttp, numpy, qrcode, sounddevice, faster_whisper, argostranslate.translate, pyttsx3; print('Semua library OK')"

# 4. Model offline
Step "4/6 Mengunduh model suara & terjemahan"
Run $Py (Join-Path $Here "fetch_models.py") $Out $WhisperSize

# 5. Aplikasi + launcher
Step "5/6 Menyalin aplikasi"
$app = Join-Path $Out "app"
New-Item -ItemType Directory -Force -Path $app | Out-Null
Copy-Item (Join-Path $Here "app\*") $app -Recurse -Force
$launcher = @'
@echo off
title TAIDEN Interpreter
cd /d "%~dp0app"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AI_MODE=local
set SRC_LANG=en
set WHISPER_MODEL=%~dp0models\whisper-__WHISPER__
if exist settings.env for /f "usebackq eol=# tokens=1,* delims==" %%a in ("settings.env") do set "%%a=%%b"
"%~dp0python\python.exe" install_offline.py
start "" http://localhost:8000/console
"%~dp0python\python.exe" server.py
echo.
echo Server berhenti.
pause
'@
Set-Content (Join-Path $Out "TAIDEN.bat") -Encoding ASCII -Value $launcher.Replace("__WHISPER__", $WhisperSize)

# 6. Installer
Step "6/6 Membuat TAIDEN-Setup.exe"
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
  Run $iscc (Join-Path $Here "TAIDEN.iss")
  Write-Host "`nSELESAI: $(Join-Path $Here 'dist\TAIDEN-Setup.exe')" -ForegroundColor Green
} else {
  Write-Host "`nInno Setup belum terpasang. Pasang gratis dari https://jrsoftware.org/isdl.php lalu jalankan BUILD.bat lagi (langkah 1-4 dilewati otomatis)." -ForegroundColor Yellow
  Write-Host "Sementara, folder siap pakai (portable): $Out  -> jalankan TAIDEN.bat di dalamnya." -ForegroundColor Yellow
}
