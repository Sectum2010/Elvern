@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Uninstall-ElvernVlcOpener.ps1"
if errorlevel 1 (
  echo Uninstall failed.
  exit /b 1
)
echo Uninstall complete.
