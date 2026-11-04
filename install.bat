@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "NO_PAUSE=0"
if /i "%~1"=="/quiet" set "NO_PAUSE=1"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
title KiCad BOM to ChipDip - install
pushd "%~dp0"
if errorlevel 1 (
  echo ERROR: Cannot access the project directory.
  if "%NO_PAUSE%"=="0" pause
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
if "%NO_PAUSE%"=="0" pause
exit /b 0

:ensure_venv
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix and sys.version_info >= (3, 9) else 1)" >nul 2>nul
  if not errorlevel 1 exit /b 0
)
if exist "%~dp0.venv" (
  echo Broken Python environment found. Removing old .venv...
  rmdir /s /q "%~dp0.venv"
  if exist "%~dp0.venv" exit /b 1
)
call :try_python py -3.14
if not errorlevel 1 exit /b 0
call :try_python py -3.13
if not errorlevel 1 exit /b 0
call :try_python py -3
if not errorlevel 1 exit /b 0
call :try_python python
if not errorlevel 1 exit /b 0
if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
  call :try_python "%LocalAppData%\Programs\Python\Python314\python.exe"
  if not errorlevel 1 exit /b 0
)
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
  call :try_python "%LocalAppData%\Programs\Python\Python313\python.exe"
  if not errorlevel 1 exit /b 0
)
exit /b 1

:try_python
if exist "%~dp0.venv" (
  rmdir /s /q "%~dp0.venv" >nul 2>nul
  if exist "%~dp0.venv" exit /b 1
)
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
%* -m venv "%~dp0.venv" >nul 2>nul
if errorlevel 1 exit /b 1
"%VENV_PY%" -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)" >nul 2>nul
exit /b %errorlevel%

:manual_python
echo.
echo Python could not be installed automatically.
echo Install Python 3.9+ from https://www.python.org/downloads/windows/
echo Enable Tcl/Tk and then run install.bat again.
:failed
echo INSTALLATION FAILED.
popd
if "%NO_PAUSE%"=="0" pause
exit /b 1
