"""Tests de l'API d'exécution des moteurs de simulation (Phase 5).

Couverture :
- `GET /api/science/simulations/moteurs` : les 5 moteurs listés avec
  leurs statuts réels (`docking_adapter` indisponible + raison Vina) ;
- `POST /api/science/simulations/run` sur moteur disponible → 201,
  rapport à 6 sections, avertissement « Simulation — à confirmer
  expérimentalement », `Simulation` persistée (engine_name/version,
  input_hash) ;
- run sur `docking_adapter` → 422 explicite, AUCUNE simulation créée
  (jamais de faux calcul) ;
- moteur inconnu → 404 ; entrées invalides → 422 ;
- cloisonnement : run avec le `project_id` d'un autre projet → 403 en
  mode serveur (même pattern que `test_science_api.py`) ;
- UI : `GET /simulations` → 200 (badges, formulaire), POST d'exécution
  → 200 + rapport affiché.

Aucun réseau : tout passe par le `TestClient` et la base de test.
"""

from __future__ import annotations

import re

import pytest

from webintel.database import models as modeles_noyau
from webintel.science import models as science

NOMS_ATTENDUS = {
    "molecular_similarity",
    "property_prediction",
    "interaction_analysis",
    "statistics",
    "docking_adapter",
}

CORPS_STATISTIQUES = {
    "engine_name": "statistics",
    "name": "Stats IC50 quercétine",
    "inputs": {
        "observations": [
            {"valeur": 12.5, "unite": "µg/mL", "libelle": "réplicat 1"},
            {"valeur": 13.1, "unite": "µg/mL", "libelle": "réplicat 2"},
            {"valeur": 11.9, "unite": "µg/mL", "libelle": "réplicat 3"},
        ]
    },
    "parameters": {},
    "description": "Test d'exécution",
}

CORPS_DOCKING = {
    "engine_name": "docking_adapter",
    "name": "Docking COX-2 / quercétine",
    "inputs": {
        "recepteur_pdb": "ATOM      1  N   ALA A   1      11.104  13.207   2.100",
        "ligand_smiles": "C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O)O",
    },
    "parameters": {"exhaustiveness": 8, "nb_poses": 9},
}


def _nb_simulations(db_session) -> int:
    return db_session.query(science.Simulation).count()


# ---------------------------------------------------------------------------
# Liste des moteurs
# ---------------------------------------------------------------------------

def test_moteurs_liste_5_moteurs_avec_statuts(client):
    """GET /api/science/simulations/moteurs → les 5 moteurs, statuts réels."""
    reponse = client.get("/api/science/simulations/moteurs")
    assert reponse.status_code == 200, reponse.text
    moteurs = reponse.json()["moteurs"]
    assert {m["nom"] for m in moteurs} == NOMS_ATTENDUS
    for moteur in moteurs:
        for cle in ("nom", "version", "description", "disponible",
                    "raison", "entrees", "parametres", "limitations"):
            assert cle in moteur, f"clé {cle!r} manquante pour {moteur['nom']}"
        assert isinstance(moteur["disponible"], bool)
        assert isinstance(moteur["limitations"], list) and moteur["limitations"]


def test_moteur_docking_adapter_indisponible_avec_raison(client):
    """`docking_adapter` : indisponible, raison explicite (Vina non installé)."""
    reponse = client.get("/api/science/simulations/moteurs")
    assert reponse.status_code == 200
    docking = next(
        m for m in reponse.json()["moteurs"] if m["nom"] == "docking_adapter"
    )
    assert docking["disponible"] is False
    assert "Vina" in docking["raison"]
    assert "docs/simulations.md" in docking["raison"]


# ---------------------------------------------------------------------------
# Exécution : moteur disponible
# ---------------------------------------------------------------------------

def test_run_moteur_disponible_201_rapport_6_sections(client, db_session):
    """Run `statistics` → 201, rapport 6 sections + avertissement, ligne persistée."""
    reponse = client.post("/api/science/simulations/run", json=CORPS_STATISTIQUES)
    assert reponse.status_code == 201, reponse.text
    corps = reponse.json()

    assert "simulation_id" in corps and isinstance(corps["simulation_id"], int)
    assert "Simulation — à confirmer expérimentalement" in corps["avertissement"]

    rapport = corps["rapport"]
    for section in ("INPUTS", "METHOD", "PARAMETERS", "OUTPUTS", "REFERENCES", "LIMITATIONS"):
        assert section in rapport, f"section {section} manquante du rapport"
    assert "Simulation — à confirmer expérimentalement" in rapport["avertissement"]
    assert rapport["engine_name"] == "statistics"
    assert rapport["OUTPUTS"]["n"]["valeur"] == 3  # vrai calcul, pas un faux

    simulation = db_session.get(science.Simulation, corps["simulation_id"])
    assert simulation is not None
    assert simulation.engine_name == "statistics"
    assert simulation.engine_version == "1.0.0"
    assert re.fullmatch(r"[0-9a-f]{64}", simulation.input_hash or "")
    assert simulation.status == "exploratoire"
    assert simulation.result["n"]["valeur"] == 3


def test_run_moteur_disponible_rattache_au_projet(client, db_session, make_project):
    """Run avec `project_id` → la simulation est rattachée à ce projet."""
    projet_id = make_project("Laboratoire Phyto")
    corps = dict(CORPS_STATISTIQUES, project_id=projet_id, name="Stats projet")
    reponse = client.post("/api/science/simulations/run", json=corps)
    assert reponse.status_code == 201, reponse.text
    simulation = db_session.get(science.Simulation, reponse.json()["simulation_id"])
    assert simulation.project_id == projet_id


# ---------------------------------------------------------------------------
# Exécution : refus honnêtes (422 / 404)
# ---------------------------------------------------------------------------

def test_run_docking_adapter_422_pas_de_faux_calcul(client, db_session):
    """Run `docking_adapter` → 422 explicite, AUCUNE simulation créée."""
    avant = _nb_simulations(db_session)
    reponse = client.post("/api/science/simulations/run", json=CORPS_DOCKING)
    assert reponse.status_code == 422, reponse.text
    detail = reponse.json()["detail"]
    assert "indisponible" in detail
    assert "Vina" in detail
    assert _nb_simulations(db_session) == avant  # rien n'a été persisté


def test_run_moteur_inconnu_404(client, db_session):
    """Run sur un moteur inconnu → 404, rien en base."""
    avant = _nb_simulations(db_session)
    corps = dict(CORPS_STATISTIQUES, engine_name="moteur_fantome")
    reponse = client.post("/api/science/simulations/run", json=corps)
    assert reponse.status_code == 404, reponse.text
    assert "moteur_fantome" in reponse.json()["detail"]
    assert _nb_simulations(db_session) == avant


def test_run_entrees_invalides_422(client, db_session):
    """Entrées invalides (observations vides, entrées absentes) → 422."""
    avant = _nb_simulations(db_session)
    corps = dict(CORPS_STATISTIQUES, inputs={"observations": []})
    reponse = client.post("/api/science/simulations/run", json=corps)
    assert reponse.status_code == 422, reponse.text

    corps_vide = dict(CORPS_STATISTIQUES, inputs={})
    reponse = client.post("/api/science/simulations/run", json=corps_vide)
    assert reponse.status_code == 422, reponse.text
    assert _nb_simulations(db_session) == avant


# ---------------------------------------------------------------------------
# Cloisonnement par projet
# ---------------------------------------------------------------------------

def test_run_projet_etranger_403_en_serveur(
    client, make_project, server_bootstrap, db_session
):
    """En mode serveur : run avec le project_id d'un autre projet → 403.

    Même pattern que `test_science_api.py::test_observations_projet_etranger_403_en_serveur`.
    """
    if server_bootstrap is None:
        pytest.skip("réservé au mode serveur (cloisonnement par clé API)")
    projet_bootstrap = make_project("bootstrap")
    autre = modeles_noyau.Project(name="Autre laboratoire")
    db_session.add(autre)
    db_session.commit()
    db_session.refresh(autre)
    assert autre.id != projet_bootstrap

    avant = _nb_simulations(db_session)
    corps = dict(CORPS_STATISTIQUES, project_id=autre.id)
    reponse = client.post("/api/science/simulations/run", json=corps)
    assert reponse.status_code == 403, reponse.text
    assert _nb_simulations(db_session) == avant


# ---------------------------------------------------------------------------
# Interface web
# ---------------------------------------------------------------------------

def test_ui_simulations_200_avec_badges_et_formulaire(client):
    """GET /simulations → 200 : badges de statut, avertissement, formulaire."""
    reponse = client.get("/simulations")
    assert reponse.status_code == 200, reponse.text
    html = reponse.text
    assert "docking_adapter" in html
    assert "Disponible" in html and "Indisponible" in html
    assert "Simulation — à confirmer expérimentalement" in html
    assert 'action="/simulations/run"' in html
    assert "statistics" in html  # moteur disponible proposé dans le formulaire


def test_ui_simulations_execution_affiche_rapport(client, db_session):
    """POST /simulations/run → 200 : le rapport s'affiche avec l'avertissement."""
    reponse = client.post(
        "/simulations/run",
        data={
            "moteur": "statistics",
            "nom": "Stats via UI",
            "description": "",
            "inputs": '{"observations": [{"valeur": 10.0}, {"valeur": 12.0}]}',
            "parameters": "{}",
        },
    )
    assert reponse.status_code == 200, reponse.text
    assert "Rapport de simulation" in reponse.text
    assert "Simulation — à confirmer expérimentalement" in reponse.text
    assert db_session.query(science.Simulation).count() == 1


def test_ui_simulations_json_invalide_sans_500(client):
    """POST /simulations/run avec JSON invalide → 200 + message, pas de 500."""
    reponse = client.post(
        "/simulations/run",
        data={
            "moteur": "statistics",
            "nom": "Stats cassées",
            "description": "",
            "inputs": "{pas du json",
            "parameters": "{}",
        },
    )
    assert reponse.status_code == 200, reponse.text
    assert "JSON invalide" in reponse.text
