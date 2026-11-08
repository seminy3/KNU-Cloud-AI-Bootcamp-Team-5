@echo off
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js 24 LTS is required.
  pause
  exit /b 1
)
start "SignMed Mock" cmd /k "node mock/server.mjs"
start "SignMed Frontend" cmd /k "npm run dev -- --port 5175 --strictPort"
timeout /t 4 /nobreak >nul
start "" http://localhost:5175/session?role=patient
