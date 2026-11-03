@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo Local Python environment is missing. Please run install.bat first.
  pause
  exit /b 1
)
"%VENV_PY%" "%~dp0kicad_bom_chipdip.py" %*
set "RUN_CODE=%errorlevel%"
if not "%RUN_CODE%"=="0" echo Conversion failed with exit code %RUN_CODE%.
echo.
pause
exit /b %RUN_CODE%
