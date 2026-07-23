@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0.elvern\Install-ElvernVlcOpener.ps1"
if errorlevel 1 (
  echo.
  echo Elvern VLC Opener was not installed. Review the error above.
  pause
  exit /b 1
)
echo Installation complete.
