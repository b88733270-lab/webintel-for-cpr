@echo off
REM WebIntel — installation locale simple sous Windows (Phase 12).
REM
REM   install.bat   -> cree l'environnement, installe les dependances,
REM                    initialise la base SQLite locale (aucun cloud).
REM   start.bat     -> demarre l'interface web sur http://127.0.0.1:8000
REM
REM Le script est idempotent : on peut le relancer sans risque.

setlocal
cd /d "%~dp0"

echo === WebIntel : installation locale ===

REM --- 1. Python 3.10+ -------------------------------------------------------
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  set "PY=py -3"
) else (
  where python >nul 2>nul
  if %ERRORLEVEL%==0 (
    set "PY=python"
  ) else (
    echo ERREUR : Python est introuvable.
    echo Installez Python 3.10+ depuis https://www.python.org/downloads/
    echo ^(cochez "Add python.exe to PATH" pendant l'installation^)
    echo puis relancez install.bat
    exit /b 1
  )
)

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if %ERRORLEVEL% neq 0 (
  echo ERREUR : Python 3.10 ou plus recent est requis.
  exit /b 1
)
echo [1/4] Python OK

REM --- 2. Environnement virtuel ----------------------------------------------
if not exist ".venv" (
  echo [2/4] Creation de l'environnement virtuel ^(.venv^)...
  %PY% -m venv .venv
) else (
  echo [2/4] Environnement virtuel deja present ^(.venv^).
)

REM --- 3. Dependances ----------------------------------------------------------
echo [3/4] Installation des dependances ^(quelques minutes^)...
.venv\Scripts\python -m pip install --quiet --upgrade pip
.venv\Scripts\python -m pip install --quiet -r requirements.txt
if %ERRORLEVEL% neq 0 (
  echo ERREUR : l'installation des dependances a echoue.
  exit /b 1
)
echo       Dependances installees.

REM --- 4. Base de donnees locale (SQLite, zero cloud) --------------------------
echo [4/4] Initialisation de la base locale ^(SQLite : .\webintel.db^)...
set "DATABASE_URL=sqlite:///./webintel.db"
set "ENABLE_SCHEDULER=false"
.venv\Scripts\python -c "from webintel.database.base import init_db; init_db()"
if %ERRORLEVEL% neq 0 (
  echo ERREUR : impossible d'initialiser la base locale.
  exit /b 1
)
echo       Base prete.

echo.
echo === Installation terminee ===
echo Demarrez WebIntel avec :  start.bat
echo Puis ouvrez :             http://127.0.0.1:8000
echo Documentation :           docs\installation.md
