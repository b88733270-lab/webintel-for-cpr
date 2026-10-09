@echo off
REM WebIntel — demarrage local simple sous Windows (Phase 12).
REM
REM   start.bat                 -> http://127.0.0.1:8000
REM   set PORT=8080 ^& start.bat -> http://127.0.0.1:8080
REM
REM Mode local par defaut : SQLite, sans cle API (usage mono-utilisateur).
REM Pour le mode serveur (PostgreSQL + cle API), voir docs\installation.md.

setlocal
cd /d "%~dp0"

if not exist ".venv" (
  echo ERREUR : l'environnement .venv est introuvable.
  echo Lancez d'abord :  install.bat
  exit /b 1
)

if "%PORT%"=="" set "PORT=8000"

REM La base SQLite est creee/verifiee a chaque demarrage (idempotent).
set "DATABASE_URL=sqlite:///./webintel.db"
.venv\Scripts\python -c "from webintel.database.base import init_db; init_db()" >nul 2>&1
if %ERRORLEVEL% neq 0 (
  echo ERREUR : impossible d'initialiser la base locale .\webintel.db
  exit /b 1
)

echo === WebIntel demarre ===
echo Interface web :  http://127.0.0.1:%PORT%
echo API ^(docs^)    :  http://127.0.0.1:%PORT%/docs
echo Arret         :  Ctrl+C
echo.

.venv\Scripts\python -m uvicorn webintel.main:app --host 127.0.0.1 --port %PORT%
