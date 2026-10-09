"""Test P0 — persistance après VRAI redémarrage du serveur.

Deux processus Python distincts (donc deux moteurs SQLAlchemy, deux
``create_app()`` + lifespan) partagent la SEULE base SQLite sur disque :

1. ``aide_redemarrage.py ecrire <db>`` : persiste un jeu de données complet
   (projet, plante, 2 molécules, preuve, liens plante-molécule/cible/
   activité, échantillon + chaîne complète + analyse + pic, étude de
   combinaison avec dossier en 12 points, version v1, brouillon wizard
   en cours, session wizard finalisée).
2. Le test vérifie que le fichier de base existe sur disque et n'est pas
   vide (= la persistance n'est pas en mémoire seule).
3. ``aide_redemarrage.py relire <db>`` : relit TOUT et vérifie chaque
   champ (étude complète : plante, échantillons, molécules, cibles,
   analyses, paramètres, résultats, provenance, limitations, logiciels,
   hashes, dates, statut, version), la session finalisée expirée survit
   à la purge TTL.

Messages en français ; aucune donnée scientifique inventée : tout est
relus depuis la base écrite en phase 1.
"""

import json
import os
import subprocess
import sys

import pytest

AIDE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "aide_redemarrage.py")
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _executer(phase: str, chemin_db: str, timeout: int = 180) -> dict:
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + chemin_db
    env["ENABLE_SCHEDULER"] = "false"
    env["REQUIRE_API_KEY"] = "false"
    env["RATE_LIMIT_ENABLED"] = "false"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Isole le processus fils du test (ignore conftest/plugins).
    resultat = subprocess.run(
        [sys.executable, "-I", AIDE, phase, chemin_db],
        capture_output=True, text=True, timeout=timeout,
        cwd=RACINE, env=env,
    )
    lignes = [l for l in (resultat.stdout or "").splitlines()
              if l.startswith("RESULTAT_JSON:")]
    assert lignes, (
        f"Le sous-processus « {phase} » n'a pas produit de résultat "
        f"(code={resultat.returncode}).\nSTDOUT:\n"
        f"{resultat.stdout[-2000:]}\nSTDERR:\n{resultat.stderr[-2000:]}"
    )
    return json.loads(lignes[-1].removeprefix("RESULTAT_JSON:"))


@pytest.mark.slow
def test_persistance_apres_redemarrage_reel(tmp_path):
    """Le redémarrage complet du serveur ne perd aucune donnée."""
    chemin_db = str(tmp_path / "redemarrage.db")

    # --- Phase 1 : écriture (processus A) ---
    ecriture = _executer("ecrire", chemin_db)
    assert ecriture.get("ok") is True, f"Écriture : {ecriture}"
    assert ecriture["etude_id"] > 0
    assert ecriture["version_id"] > 0

    # --- Preuve disque : la base existe et contient des données ---
    assert os.path.isfile(chemin_db), \
        "La base SQLite n'existe pas sur disque après écriture."
    taille = os.path.getsize(chemin_db)
    assert taille > 4096, \
        f"Base anormalement petite ({taille} octets) : persistance douteuse."

    # --- Phase 2 : relecture après redémarrage (processus B) ---
    lecture = _executer("relire", chemin_db)
    echecs = [v for v in lecture.get("verifications", []) if not v["ok"]]
    assert lecture.get("ok") is True and not echecs, (
        "Données perdues ou altérées après redémarrage :\n"
        + "\n".join(f"- {v['nom']} : {v.get('detail', '')}"
                    for v in echecs)
    )
    assert lecture["etude_id"] == ecriture["etude_id"]

    # Résumé lisible en français.
    noms_ok = [v["nom"] for v in lecture["verifications"] if v["ok"]]
    assert len(noms_ok) >= 25, f"Trop peu de vérifications : {noms_ok}"
