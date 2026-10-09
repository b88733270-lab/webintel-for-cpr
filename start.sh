#!/usr/bin/env bash
# WebIntel — démarrage local simple (Phase 12).
#
#   ./start.sh                 -> http://127.0.0.1:8000
#   PORT=8080 ./start.sh       -> http://127.0.0.1:8080
#   WEBINTEL_HOST=0.0.0.0 ./start.sh -> accessible sur le réseau local
#                                      (réservé à un usage de confiance)
#
# Mode local par défaut : SQLite, sans clé API (usage mono-utilisateur).
# Pour le mode serveur (PostgreSQL + clé API), voir docs/installation.md.

set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "ERREUR : l'environnement .venv est introuvable."
  echo "Lancez d'abord :  ./install.sh"
  exit 1
fi

PORT="${PORT:-8000}"
HOST="${WEBINTEL_HOST:-127.0.0.1}"

# La base SQLite est créée/vérifiée à chaque démarrage (idempotent).
DATABASE_URL="sqlite:///./webintel.db" \
  .venv/bin/python -c "from webintel.database.base import init_db; init_db()" \
  >/dev/null 2>&1 || {
    echo "ERREUR : impossible d'initialiser la base locale ./webintel.db"
    exit 1
  }

echo "=== WebIntel démarre ==="
echo "Interface web :  http://${HOST}:${PORT}"
echo "API (docs)    :  http://${HOST}:${PORT}/docs"
echo "Arrêt         :  Ctrl+C"
echo ""

exec .venv/bin/python -m uvicorn webintel.main:app --host "$HOST" --port "$PORT"
