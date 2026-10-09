"""Tests du journal d'audit scientifique immuable (Phase 13).

- chaque exécution enregistre qui / quand / hash SHA-256 / paramètres /
  sorties / version du moteur (`executed_by`, `run_at`, `input_hash`,
  `inputs`, `parameters`, `result`, `engine_name`, `engine_version`) ;
- les colonnes d'audit ne sont jamais modifiables (PATCH → refus) et
  aucune route de suppression n'existe (append-only) ;
- toute simulation persistée produit un rapport reproductible exportable
  (JSON / HTML / Markdown) ;
- le rejeu (`/rejouer`) crée une nouvelle simulation avec le même hash.
"""

from __future__ import annotations

import re

import pytest

from webintel.science import lab as lab_service
from webintel.science import service as science_service
from webintel.science.models import Simulation

CORPS_STATISTIQUES = {
    "engine_name": "statistics",
    "name": "Stats IC50 quercétine (audit)",
    "inputs": {
        "observations": [
            {"valeur": 12.5, "unite": "µg/mL", "libelle": "réplicat 1"},
            {"valeur": 13.1, "unite": "µg/mL", "libelle": "réplicat 2"},
            {"valeur": 11.9, "unite": "µg/mL", "libelle": "réplicat 3"},
        ]
    },
    "parameters": {},
    "description": "Audit Phase 13",
}


def _lancer(client) -> dict:
    reponse = client.post("/api/science/simulations/run", json=CORPS_STATISTIQUES)
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _attendu_executed_by(server_bootstrap) -> str:
    if server_bootstrap is None:
        return "local"
    return f"clé:{server_bootstrap['plain_key'][:8]}"


@pytest.fixture()
def _projet_audit(db_session):
    """Projet de rattachement (P0-1 : ``project_id`` obligatoire)."""
    from webintel.database.models import Project

    projet = Project(name="Projet Audit")
    db_session.add(projet)
    db_session.commit()
    return projet.id


# ---------------------------------------------------------------------------
# Audit : qui / quand / hash / paramètres / sorties / version moteur
# ---------------------------------------------------------------------------

def test_run_enregistre_audit_complet(client, db_session, server_bootstrap):
    """L'exécution persiste tout le journal d'audit (Phase 13.1)."""
    resultat = _lancer(client)
    simulation = db_session.get(Simulation, resultat["simulation_id"])
    assert simulation is not None
    # Qui / quand.
    assert simulation.executed_by == _attendu_executed_by(server_bootstrap)
    assert simulation.run_at is not None
    assert simulation.created_at is not None
    # Hash SHA-256 des entrées.
    assert re.fullmatch(r"[0-9a-f]{64}", simulation.input_hash)
    # Entrées normalisées persistées (rejouabilité).
    assert simulation.inputs["observations"][0]["valeur"] == 12.5
    # Paramètres / sorties / version du moteur.
    assert simulation.parameters.get("niveau_confiance") == 0.95  # défaut normalisé
    assert simulation.result  # sorties du moteur statistics
    assert "moyenne" in simulation.result
    assert simulation.engine_name == "statistics"
    assert simulation.engine_version == "1.0.0"


def test_creation_manuelle_enregistre_aussi_executed_by(
    client, db_session, server_bootstrap
):
    """`POST /science/simulations` (création manuelle) trace aussi l'appelant."""
    reponse = client.post(
        "/api/science/simulations",
        json={"name": "Simulation manuelle", "target": "COX-2",
              "parameters": {"x": 1}},
    )
    assert reponse.status_code == 201, reponse.text
    simulation = db_session.get(Simulation, reponse.json()["id"])
    assert simulation.executed_by == _attendu_executed_by(server_bootstrap)
    assert simulation.inputs["target"] == "COX-2"


# ---------------------------------------------------------------------------
# Immuabilité : append-only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "champ,valeur",
    [
        ("parameters", {"n": 999}),
        ("result", {"moyenne": 0.0}),
        ("engine_name", "autre_moteur"),
        ("engine_version", "9.9.9"),
        ("input_hash", "0" * 64),
        ("inputs", {"observations": []}),
        ("executed_by", "quelquun_dautre"),
        ("run_at", "2020-01-01T00:00:00+00:00"),
    ],
)
def test_service_refuse_modification_champs_audit(
    db_session, champ, valeur, _projet_audit
):
    """`lab.modifier_simulation` refuse les colonnes d'audit (→ 409 côté API)."""
    simulation = science_service.create_simulation(
        db_session, "Audit", {"observations": [{"valeur": 1.0}]},
        project_id=_projet_audit,
    )
    db_session.commit()
    with pytest.raises(ValueError, match="[Ii]mmuable|audit"):
        lab_service.modifier_simulation(db_session, simulation.id, **{champ: valeur})
    db_session.rollback()
    # La ligne est intacte.
    db_session.refresh(simulation)
    assert simulation.input_hash != "0" * 64


def test_patch_api_refuse_champ_audit_422(client):
    """Le schéma PATCH exclut les champs d'audit : tentative → 422 explicite."""
    sim_id = _lancer(client)["simulation_id"]
    reponse = client.patch(
        f"/api/science/lab/simulations/{sim_id}",
        json={"parameters": {"n": 999}},
    )
    assert reponse.status_code == 422, reponse.text


def test_patch_api_champ_descriptif_toujours_possible(client, db_session):
    """Les champs descriptifs (nom, description, statut) restent modifiables."""
    sim_id = _lancer(client)["simulation_id"]
    reponse = client.patch(
        f"/api/science/lab/simulations/{sim_id}",
        json={"name": "Nom corrigé", "status": "predictif"},
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["name"] == "Nom corrigé"
    assert corps["status"] == "predictif"
    # L'audit n'a pas bougé.
    simulation = db_session.get(Simulation, sim_id)
    assert simulation.engine_name == "statistics"
    assert re.fullmatch(r"[0-9a-f]{64}", simulation.input_hash)


def test_aucune_route_suppression_simulation(client):
    """Aucune route DELETE n'existe pour les simulations (append-only)."""
    sim_id = _lancer(client)["simulation_id"]
    for chemin in (
        f"/api/science/simulations/{sim_id}",
        f"/api/science/lab/simulations/{sim_id}",
    ):
        reponse = client.delete(chemin)
        assert reponse.status_code in (404, 405), (chemin, reponse.status_code)


# ---------------------------------------------------------------------------
# Rapport reproductible exportable
# ---------------------------------------------------------------------------

SECTIONS = ["INPUTS", "METHOD", "PARAMETERS", "OUTPUTS", "REFERENCES", "LIMITATIONS"]


def test_rapport_json_6_sections_et_audit(client):
    """`GET .../rapport` : 6 sections + métadonnées d'audit + avertissement."""
    sim_id = _lancer(client)["simulation_id"]
    reponse = client.get(f"/api/science/simulations/{sim_id}/rapport")
    assert reponse.status_code == 200, reponse.text
    rapport = reponse.json()
    for section in SECTIONS:
        assert section in rapport, section
    assert rapport["engine_name"] == "statistics"
    assert rapport["engine_version"] == "1.0.0"
    assert re.fullmatch(r"[0-9a-f]{64}", rapport["input_hash"])
    assert "executed_by" in rapport and rapport["executed_by"]
    assert rapport["run_at"]
    assert "confirmer exp" in rapport["avertissement"]
    assert rapport["INPUTS"]["observations"][0]["valeur"] == 12.5
    assert "moyenne" in rapport["OUTPUTS"]


def test_rapport_html_et_markdown(client):
    """Formats HTML (page) et Markdown (téléchargement)."""
    sim_id = _lancer(client)["simulation_id"]
    html = client.get(
        f"/api/science/simulations/{sim_id}/rapport", params={"format": "html"}
    )
    assert html.status_code == 200, html.text
    assert "text/html" in html.headers["content-type"]
    assert "confirmer exp" in html.text

    md = client.get(
        f"/api/science/simulations/{sim_id}/rapport", params={"format": "markdown"}
    )
    assert md.status_code == 200, md.text
    assert "text/markdown" in md.headers["content-type"]
    assert "attachment" in md.headers["content-disposition"]
    assert md.text.startswith("# Rapport de simulation")
    for section in SECTIONS:
        assert f"## {section}" in md.text


def test_rapport_404_si_absente(client):
    assert client.get("/api/science/simulations/999999/rapport").status_code == 404


# ---------------------------------------------------------------------------
# Rejeu : mêmes entrées → même hash
# ---------------------------------------------------------------------------

def test_rejouer_cree_nouvelle_simulation_meme_hash(client, db_session):
    """`/rejouer` : nouvelle ligne, même `input_hash`, sorties identiques."""
    sim_id = _lancer(client)["simulation_id"]
    original = db_session.get(Simulation, sim_id)
    reponse = client.post(f"/api/science/simulations/{sim_id}/rejouer")
    assert reponse.status_code == 201, reponse.text
    corps = reponse.json()
    assert corps["simulation_id"] != sim_id
    assert corps["input_hash"] == original.input_hash
    assert "confirmer exp" in corps["avertissement"]
    nouvelle = db_session.get(Simulation, corps["simulation_id"])
    assert nouvelle.result == original.result  # moteur déterministe
    assert nouvelle.executed_by == original.executed_by
    assert "(rejeu)" in nouvelle.name


def test_rejouer_sans_moteur_422(client):
    """Une simulation manuelle (sans moteur) n'est pas rejouable → 422."""
    reponse = client.post(
        "/api/science/simulations",
        json={"name": "Manuelle", "target": "COX-2", "parameters": {"x": 1}},
    )
    assert reponse.status_code == 201, reponse.text
    sim_id = reponse.json()["id"]
    reponse = client.post(f"/api/science/simulations/{sim_id}/rejouer")
    assert reponse.status_code == 422, reponse.text


# ---------------------------------------------------------------------------
# UI : badge prédiction + historique immuable
# ---------------------------------------------------------------------------

def test_ui_simulations_badge_et_historique(client):
    """La page /simulations affiche le badge Prédiction et l'historique."""
    page = client.get("/simulations")
    assert page.status_code == 200, page.text
    # Formulaire exécuté via l'UI.
    import json as _json

    reponse = client.post(
        "/simulations/run",
        data={
            "moteur": "statistics",
            "nom": "Stats UI audit",
            "description": "",
            "inputs": _json.dumps(CORPS_STATISTIQUES["inputs"]),
            "parameters": "{}",
        },
    )
    assert reponse.status_code == 200, reponse.text
    assert "Prédiction" in reponse.text
    assert "confirmer exp" in reponse.text
    # L'historique liste la simulation avec son empreinte.
    page = client.get("/simulations")
    assert page.status_code == 200
    assert "Stats UI audit" in page.text
    assert "append-only" in page.text


def test_ui_page_rapport(client):
    """`/simulations/{id}/rapport` : page HTML + téléchargement Markdown."""
    import json as _json

    client.post(
        "/simulations/run",
        data={
            "moteur": "statistics",
            "nom": "Stats rapport UI",
            "description": "",
            "inputs": _json.dumps(CORPS_STATISTIQUES["inputs"]),
            "parameters": "{}",
        },
    )
    from webintel.database.base import SessionLocal

    session = SessionLocal()
    try:
        sim_id = (
            session.query(Simulation)
            .filter(Simulation.name == "Stats rapport UI")
            .one()
            .id
        )
    finally:
        session.close()
    page = client.get(f"/simulations/{sim_id}/rapport")
    assert page.status_code == 200, page.text
    assert "Prédiction" in page.text
    assert "SHA-256" in page.text
    md = client.get(f"/simulations/{sim_id}/rapport?format=markdown")
    assert md.status_code == 200
    assert "attachment" in md.headers["content-disposition"]
    assert "## OUTPUTS" in md.text
