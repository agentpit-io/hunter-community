@echo off
REM Hunter Community - native launcher (no Docker required)
REM Double-click this file. Everything installs into .runtime\ inside this folder.
REM Nothing is written to the registry, system PATH, or C: drive.

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\native\start.ps1"
if errorlevel 1 pause
