@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0.elvern\Install-ElvernVlcOpener.ps1"
set "ELVERN_INSTALL_EXIT=%ERRORLEVEL%"
if not "%ELVERN_INSTALL_EXIT%"=="0" (
  echo.
  echo Elvern VLC Opener was not installed. Review the error above.
  pause
  exit /b %ELVERN_INSTALL_EXIT%
)
echo Installation complete.
