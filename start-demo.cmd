@echo off
REM Double-clickable launcher. Bypasses execution policy for this one script
REM only, which is why the .ps1 exists separately rather than being inlined.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" %*
if errorlevel 1 pause
