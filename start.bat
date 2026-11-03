@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 "%~dp0kicad_bom_chipdip.py" %*
) else (
    python "%~dp0kicad_bom_chipdip.py" %*
)
echo.
pause
