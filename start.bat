@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix and sys.version_info >= (3, 9) else 1)" >nul 2>nul
  if not errorlevel 1 goto :venv_ready
)
echo Python environment is missing or broken. Repairing automatically...
call "%~dp0install.bat" /quiet
if errorlevel 1 (
  echo ERROR: Could not repair Python environment.
  pause
  exit /b 1
)
:venv_ready
"%VENV_PY%" "%~dp0kicad_bom_chipdip.py" %*
set "RUN_CODE=%errorlevel%"
if not "%RUN_CODE%"=="0" echo Conversion failed with exit code %RUN_CODE%.
echo.
pause
exit /b %RUN_CODE%
