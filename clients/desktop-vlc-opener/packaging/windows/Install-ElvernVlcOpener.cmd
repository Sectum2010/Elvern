@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-ElvernVlcOpener.ps1"
if errorlevel 1 (
  echo Installation failed.
  exit /b 1
)
echo Installation complete.
