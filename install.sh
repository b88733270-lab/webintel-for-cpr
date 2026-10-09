#!/usr/bin/env bash
# WebIntel — installation locale simple (Phase 12).
#
# Pense pour un chercheur non-développeur :
#   ./install.sh   -> crée l'environnement, installe les dépendances,
#                     initialise la base SQLite locale (aucun cloud, aucune
#                     base à installer).
#   ./start.sh     -> démarre l'interface web sur http://127.0.0.1:8000
#
# Le script est idempotent : on peut le relancer sans risque.

set -euo pipefail

cd "$(dirname "$0")"

echo "=== WebIntel : installation locale ==="

# --- 1. Python 3.10+ -------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERREUR : python3 est introuvable."
  echo "Installez Python 3.10 ou plus récent (https://www.python.org/downloads/)"
  echo "puis relancez ./install.sh"
  exit 1
fi

VERSION_OK=$(python3 -c "import sys; print(sys.version_info >= (3, 10))")
if [ "$VERSION_OK" != "True" ]; then
  echo "ERREUR : Python 3.10+ requis (détecté : $(python3 --version 2>&1))."
  exit 1
fi
echo "[1/4] Python OK : $(python3 --version 2>&1)"

# --- 2. Environnement virtuel ----------------------------------------------
if [ ! -d ".venv" ]; then
  echo "[2/4] Création de l'environnement virtuel (.venv)..."
  python3 -m venv .venv
else
  echo "[2/4] Environnement virtuel déjà présent (.venv)."
fi

# --- 3. Dépendances ----------------------------------------------------------
echo "[3/4] Installation des dépendances (quelques minutes)..."
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
echo "      Dépendances installées."

# --- 4. Base de données locale (SQLite, zéro cloud) --------------------------
echo "[4/4] Initialisation de la base locale (SQLite : ./webintel.db)..."
DATABASE_URL="sqlite:///./webintel.db" ENABLE_SCHEDULER="false" \
  .venv/bin/python -c "from webintel.database.base import init_db; init_db()"
echo "      Base prête."

echo ""
echo "=== Installation terminée ==="
echo "Démarrez WebIntel avec :  ./start.sh"
echo "Puis ouvrez :             http://127.0.0.1:8000"
echo "Documentation :           docs/installation.md"
