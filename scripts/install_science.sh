#!/usr/bin/env bash
# WebIntel — installation du niveau SCIENCE (optionnel, v2.1).
#
# Pour un chercheur non-développeur : ce script active les outils
# scientifiques réels (chimoinformatique) SANS toucher au mode simple.
#
#   ./scripts/install_science.sh
#
# Ce que fait le script :
#   1. vérifie l'environnement virtuel .venv (créé par ./install.sh) ;
#   2. installe l'extra "science" (RDKit : empreintes de Morgan,
#      descripteurs moléculaires, recherche par sous-structure) ;
#   3. installe "meeko" (préparation SMILES -> PDBQT pour le docking ;
#      recommandé, optionnel) ;
#   4. vérifie que RDKit est importable et affiche sa version.
#
# Sans ce script, WebIntel fonctionne en mode CORE (léger) et indique
# explicitement ce qui n'est pas calculable — aucune valeur inventée.
# Le script est idempotent : on peut le relancer sans risque.
#
# Alternative manuelle :  pip install -e ".[science]"
# Alternative conda    :  conda install -c conda-forge rdkit
#
# Voir docs/runtime_scientifique.md

set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== WebIntel : activation du niveau SCIENCE (optionnel) ==="
echo ""
echo "Ce niveau ajoute la chimoinformatique réelle (RDKit) :"
echo "  - similarité structurelle (empreintes de Morgan + Tanimoto) ;"
echo "  - descripteurs moléculaires (logP, HBD/HBA, TPSA, Lipinski...) ;"
echo "  - recherche par sous-structure (SMARTS) ;"
echo "  - préparation des ligands pour le docking (avec meeko)."
echo ""
echo "Le mode simple (CORE) continue de fonctionner sans RDKit."
echo ""

# --- 1. Python / venv ------------------------------------------------------
if [ -d ".venv" ]; then
  PY=".venv/bin/python"
  echo "[1/4] Environnement virtuel détecté (.venv)."
else
  echo "[1/4] Pas de .venv : utilisation de python3 système."
  echo "      (Recommandé : lancez d'abord ./install.sh pour un .venv dédié.)"
  PY="python3"
fi

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERREUR : python introuvable ($PY)."
  exit 1
fi

# --- 2. Extra "science" (RDKit) ----------------------------------------------
echo "[2/4] Installation de l'extra 'science' (RDKit, ~40 Mo)..."
"$PY" -m pip install --quiet -e ".[science]"
echo "      RDKit installé."

# --- 3. meeko (préparation SMILES -> PDBQT pour le docking) -------------------
echo "[3/4] Installation de 'meeko' (préparation des ligands, optionnel)..."
if "$PY" -m pip install --quiet meeko; then
  echo "      meeko installé."
else
  echo "      AVERTISSEMENT : meeko n'a pas pu être installé."
  echo "      Le docking acceptera uniquement des ligands déjà au format PDBQT."
fi

# --- 4. Vérification ----------------------------------------------------------
echo "[4/4] Vérification..."
"$PY" - <<'EOF'
from webintel.science import chimie

infos = chimie.infos_biblio()
print(f"      RDKit disponible : {infos['disponible']}")
print(f"      Version          : {infos['version']}")
assert infos["disponible"], "RDKit n'est pas importable après installation."

# Test fonctionnel minimal : aspirine.
fp = chimie.empreinte_morgan("CC(=O)Oc1ccccc1C(=O)O")
assert fp is not None and len(fp) == 2048, "Empreinte de Morgan HS."
d = chimie.descripteurs("CC(=O)Oc1ccccc1C(=O)O")
assert d is not None and d["formule"] == "C9H8O4", "Descripteurs HS."
print("      Test fonctionnel OK (empreinte 2048 bits + descripteurs aspirine).")

try:
    import meeko  # noqa: F401
    print("      meeko disponible : préparation SMILES -> PDBQT active.")
except ImportError:
    print("      meeko absent : le docking exigera des ligands PDBQT.")
EOF

echo ""
echo "=== Niveau SCIENCE activé ==="
echo "Vérifiez dans l'interface : page Paramètres -> bloc « Outils scientifiques »."
echo "Pour le docking moléculaire (moteur externe), installez aussi AutoDock Vina :"
echo "  sudo apt install autodock-vina   # Debian/Ubuntu"
echo "  conda install -c conda-forge autodock-vina"
