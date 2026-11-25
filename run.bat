@echo off
rem Start SquidWings without activating the venv: run.bat (web UI), run.bat chat, run.bat addon list
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Belum ada .venv, menyiapkan dulu ^(sekali saja^)...
  py -3 scripts\setup.py || python scripts\setup.py || exit /b 1
)
if "%~1"=="" (
  ".venv\Scripts\python.exe" main.py web
) else (
  ".venv\Scripts\python.exe" main.py %*
)
