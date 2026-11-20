@echo off
rem Pakai:  TAIDEN.bat        -> jendela terlihat (debug)
rem         TAIDEN.bat bg     -> latar belakang + watchdog (dipanggil TAIDEN.vbs)
rem         TAIDEN.bat bg silent -> sama, tanpa membuka browser
title TAIDEN Interpreter
cd /d "%~dp0app"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AI_MODE=local
set SRC_LANG=en
set WHISPER_MODEL=%~dp0models\whisper-__WHISPER__
if exist settings.env for /f "usebackq eol=# tokens=1,* delims==" %%a in ("settings.env") do set "%%a=%%b"
if /i "%~1"=="bg" goto bg
"%~dp0python\python.exe" install_offline.py
"%~dp0python\python.exe" server.py
echo.
echo Server berhenti.
pause
goto :eof

:bg
set NOBROWSER=
if /i "%~2"=="silent" set NOBROWSER=--no-browser
"%~dp0python\python.exe" install_offline.py >> taiden.log 2>&1
:run
"%~dp0python\python.exe" server.py --background %NOBROWSER%
rem kode 0 = berhenti normal / sudah ada instance lain -> selesai. Selain itu (crash) -> nyalakan ulang.
if errorlevel 1 (
  set NOBROWSER=--no-browser
  ping -n 4 127.0.0.1 >nul
  goto run
)
