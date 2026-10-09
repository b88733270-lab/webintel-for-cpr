#!/usr/bin/env bash
# WebIntel — scénario de démonstration scientifique (Phase 14).
#
#   ./scripts/demo.sh
#
# Enchaîne, en mode local SQLite et 100 % hors-ligne :
#   import démo -> fiche plante -> simulation -> dossier ZIP -> fiche PDF
#   -> rapport Markdown.
#
# Le script est idempotent : il reconstruit /tmp/webintel-demo à chaque
# lancement. Il n'écrit jamais dans la base de développement ./webintel.db.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
  echo "ERREUR : l'environnement .venv est introuvable."
  echo "Lancez d'abord :  ./install.sh"
  exit 1
fi

exec .venv/bin/python scripts/demo_science.py
