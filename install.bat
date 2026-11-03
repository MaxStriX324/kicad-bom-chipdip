@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
title KiCad BOM to ChipDip - install
pushd "%~dp0"
if errorlevel 1 (
  echo ERROR: Cannot access the project directory.
  pause
  exit /b 1
)

if not exist "%~dp0kicad_bom_chipdip.py" (
  echo ERROR: kicad_bom_chipdip.py was not found.
  echo Extract the entire GitHub ZIP first.
  goto :failed
)
echo ================================================
echo     KiCad BOM to ChipDip - LOCAL INSTALL
 echo ================================================
echo.
call :ensure_venv
if not errorlevel 1 goto :verify

echo Python 3.9+ not found. Trying winget for current user...
where winget >nul 2>&1
if errorlevel 1 goto :manual_python
winget install --id Python.Python.3.13 --exact --source winget --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :manual_python
call :ensure_venv
if errorlevel 1 goto :manual_python

:verify
"%VENV_PY%" "%~dp0kicad_bom_chipdip.py" --help >nul
if errorlevel 1 goto :failed
"%VENV_PY%" -c "import tkinter"
if errorlevel 1 (
  echo WARNING: Tkinter is not available; start.bat will request a CSV path in the console.
) else (
  echo Tkinter available; start.bat opens a file picker.
)
echo.
echo Local environment: %~dp0.venv
echo No third-party Python packages are needed.
echo INSTALLATION COMPLETE. Use start.bat to select ANY KiCad CSV.
echo You can also drag a CSV onto start.bat.
popd
pause
exit /b 0

:ensure_venv
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
  if not errorlevel 1 exit /b 0
)
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
  "%LocalAppData%\Programs\Python\Python313\python.exe" -m venv "%~dp0.venv"
  if not errorlevel 1 exit /b 0
)
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 (
  py -3 -m venv "%~dp0.venv"
  if not errorlevel 1 exit /b 0
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 (
  python -m venv "%~dp0.venv"
  if not errorlevel 1 exit /b 0
)
exit /b 1

:manual_python
echo.
echo Python could not be installed automatically.
echo Install Python 3.9+ from https://www.python.org/downloads/windows/
echo Enable Tcl/Tk and then run install.bat again.
:failed
echo INSTALLATION FAILED.
popd
pause
exit /b 1
