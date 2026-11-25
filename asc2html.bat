@echo off
setlocal
rem asc2html launcher. Keep this file pure ASCII: cmd.exe reads .bat in the
rem OEM code page, so non-ASCII text here becomes garbage commands.

if "%~1"=="" goto usage

set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py -3"

"%PY%" "%~dp0asc2html.py" %* --open
if errorlevel 1 (
  echo.
  echo [ERROR] failed. Check the messages above.
  pause
)
exit /b 0

:usage
echo Usage:
echo   1. Drag an .asc file onto this file  ^(optionally also drag its .dbc^)
echo   2. Or run:  asc2html.bat  log.asc  matrix.dbc
echo.
pause
exit /b 1
