"""Tests Phase 11 — IA complètement désactivable.

Couverture :
- IA-ON (défaut) : statut, synthèse d'une plante avec sources (niveau
  `hypothese`, avertissement, sources citées, éphémère), synthèse sans
  source (message explicite, rien d'inventé), 404, aucune persistance,
  données privées du laboratoire jamais utilisées, section IA visible
  dans la fiche, page de synthèse, page Paramètres (affichage + bascule).
- IA-OFF (override d'interface) : API 503 explicite « fonctionnalité
  désactivée », statut coupé, `IaDesactiveeError` levée AVANT tout accès
  DB (pas d'appel silencieux), section IA absente de la fiche, page UI 503.
- Variable d'environnement : `is_ai_enabled(Settings(AI_ENABLED=False))`
  → désactivée, source `variable_environnement` (test unitaire : la variable
  est lue au démarrage, comme tous les réglages).

Aucun réseau : tout passe par le `TestClient` et la base de test.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from webintel.config import runtime as runtime_config
from webintel.config.settings import Settings
from webintel.database import models as modeles_noyau
from webintel.database.base import SessionLocal, init_db
from webintel.science import ia as ia_module
from webintel.science import models as science

init_db()

DOI_LIEN = "10.1016/j.jep.2021.114001"


@pytest.fixture(autouse=True)
def _nettoyage_science():
    session = SessionLocal()
    try:
        for table in (
            science.ResearchObservation,
            science.Simulation,
            science.Evidence,
            science.PlantCompound,
            science.Compound,
            science.Plant,
        ):
            session.query(table).delete()
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture()
def _override_ia(tmp_path, monkeypatch):
    """Isole le fichier d'overrides d'exécution (jamais le vrai)."""
    chemin = tmp_path / "runtime_config.json"
    monkeypatch.setenv("WEBINTEL_RUNTIME_CONFIG", str(chemin))
    yield chemin
    # monkeypatch restaure l'environnement ; le tmp_path est éphémère.


def _plante(client, nom: str = "Artemisia annua") -> dict:
    reponse = client.post(
        "/api/science/plants",
        json={"scientific_name": nom, "family": "Asteraceae"},
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _jeu_complet(client) -> dict:
    """Plante + composé + lien (DOI) + preuve (même DOI, avec extrait)."""
    plante = _plante(client)
    compose = client.post(
        "/api/science/compounds",
        json={"name": "Artémisinine", "formula": "C15H22O5"},
    ).json()
    lien = client.post(
        f"/api/science/plants/{plante['id']}/compounds",
        json={
            "plant_id": plante["id"],
            "compound_id": compose["id"],
            "plant_part": "feuilles",
            "publication": "J. Ethnopharmacol. 2021",
            "doi": DOI_LIEN,
        },
    )
    assert lien.status_code == 201, lien.text
    preuve = client.post(
        "/api/science/evidences",
        json={
            "doi": DOI_LIEN,
            "title": "Activité antipaludique d'Artemisia annua",
            "reference": "J. Ethnopharmacol. 2021",
            "excerpt": "L'extrait éthanolique montre une activité notable.",
            "authors": ["L. Traoré"],
        },
    )
    assert preuve.status_code == 201, preuve.text
    return {"plante": plante, "compose": compose}


def _compter_prive() -> dict:
    session = SessionLocal()
    try:
        return {
            "evidences": session.query(science.Evidence).count(),
            "observations": session.query(science.ResearchObservation).count(),
            "simulations": session.query(science.Simulation).count(),
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# IA-ON (état par défaut)
# ---------------------------------------------------------------------------


def test_statut_actif_par_defaut(client, _override_ia):
    reponse = client.get("/api/science/ia/statut")
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["ia_activee"] is True
    assert corps["source_decision"] in {"defaut", "variable_environnement"}


def test_synthese_plante_avec_sources(client, _override_ia):
    ids = _jeu_complet(client)
    avant = _compter_prive()
    reponse = client.get(f"/api/science/ia/synthese/plante/{ids['plante']['id']}")
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    # Étiquetage honnête : hypothèse, jamais publié.
    assert corps["niveau_donnees"] == "hypothese"
    assert "HYPOTHÈSE" in corps["avertissement"]
    assert "jamais une preuve" in corps["texte"] or "ne prouve rien" in corps["texte"]
    # Sources réellement citées, avec DOI.
    assert len(corps["sources"]) == 1
    assert corps["sources"][0]["doi"] == DOI_LIEN
    assert corps["sources"][0]["data_level"] == "publie"
    # Le composé lié est mentionné, l'extrait de la preuve est repris.
    assert "Artémisinine" in corps["texte"]
    assert "activité notable" in corps["texte"]
    # Éphémère : rien n'a été persisté.
    assert corps["persiste"] is False
    assert _compter_prive() == avant


def test_synthese_plante_sans_sources(client, _override_ia):
    plante = _plante(client)
    reponse = client.get(f"/api/science/ia/synthese/plante/{plante['id']}")
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["niveau_donnees"] == "hypothese"
    assert corps["sources"] == []
    assert "Aucune preuve documentaire" in corps["texte"]


def test_synthese_plante_absente_404(client, _override_ia):
    reponse = client.get("/api/science/ia/synthese/plante/999999")
    assert reponse.status_code == 404, reponse.text


def test_synthese_ignore_donnees_privees(client, _override_ia, server_bootstrap):
    ids = _jeu_complet(client)
    # Observation privée du laboratoire (commentaire distinctif).
    projet = client.post("/api/projects", json={"name": "Labo secret"})
    project_id = projet.json()["id"] if projet.status_code == 201 else None
    if project_id is None:
        # Mode local : le projet par défaut existe déjà.
        session = SessionLocal()
        try:
            projet_defaut = session.query(modeles_noyau.Project).order_by(
                modeles_noyau.Project.id
            ).first()
            project_id = projet_defaut.id
        finally:
            session.close()
    commentaire_secret = "RENDEMENT-CONFIDENTIEL-XYZ-123"
    obs = client.post(
        "/api/science/observations",
        json={
            "project_id": project_id,
            "protocol_ref": "PROTO-SECRET",
            "comment": commentaire_secret,
        },
    )
    assert obs.status_code == 201, obs.text
    reponse = client.get(f"/api/science/ia/synthese/plante/{ids['plante']['id']}")
    assert reponse.status_code == 200, reponse.text
    assert commentaire_secret not in reponse.json()["texte"]


def test_fiche_montre_section_ia_quand_activee(client, _override_ia):
    ids = _jeu_complet(client)
    reponse = client.get(f"/plantes/{ids['plante']['id']}")
    assert reponse.status_code == 200, reponse.text
    assert "synthese-ia" in reponse.text


def test_page_synthese_ia(client, _override_ia):
    ids = _jeu_complet(client)
    reponse = client.get(f"/plantes/{ids['plante']['id']}/synthese-ia")
    assert reponse.status_code == 200, reponse.text
    assert "Hypothèse générée par IA" in reponse.text
    assert DOI_LIEN in reponse.text


def test_parametres_affiche_etat(client, _override_ia):
    reponse = client.get("/parametres")
    assert reponse.status_code == 200, reponse.text
    assert "ia_activee" in reponse.text
    assert "Activée" in reponse.text


def test_parametres_bascule_interface(client, _override_ia):
    # Désactivation via la case (décochée → champ absent).
    reponse = client.post("/parametres", data={"action": "appliquer"})
    assert reponse.status_code == 200, reponse.text
    assert "désactivées" in reponse.text
    statut = client.get("/api/science/ia/statut").json()
    assert statut["ia_activee"] is False
    assert statut["source_decision"] == "interface"
    # Réactivation.
    reponse = client.post(
        "/parametres", data={"action": "appliquer", "ia_activee": "on"}
    )
    assert reponse.status_code == 200, reponse.text
    assert client.get("/api/science/ia/statut").json()["ia_activee"] is True
    # Réinitialisation : retour à la variable d'environnement / au défaut.
    reponse = client.post("/parametres", data={"action": "reinitialiser"})
    assert reponse.status_code == 200, reponse.text
    statut = client.get("/api/science/ia/statut").json()
    assert statut["ia_activee"] is True
    assert statut["source_decision"] in {"defaut", "variable_environnement"}


def test_env_off_desactive_unitaire(monkeypatch):
    # Simule « variable posée au démarrage » (lue à l'import en réel).
    monkeypatch.setenv("WEBINTEL_AI_ENABLED", "false")
    active, source = runtime_config.is_ai_enabled(Settings(AI_ENABLED=False))
    assert active is False
    assert source == "variable_environnement"


# ---------------------------------------------------------------------------
# IA-OFF (override d'interface)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _ia_desactivee(_override_ia):
    runtime_config.set_runtime_override(runtime_config.OVERRIDE_AI_ENABLED, False)
    yield
    runtime_config.clear_runtime_override(runtime_config.OVERRIDE_AI_ENABLED)


def test_api_503_quand_desactivee(client, _ia_desactivee):
    ids = _jeu_complet(client)
    reponse = client.get(f"/api/science/ia/synthese/plante/{ids['plante']['id']}")
    assert reponse.status_code == 503, reponse.text
    assert "désactivée" in reponse.json()["detail"]


def test_statut_desactive(client, _ia_desactivee):
    corps = client.get("/api/science/ia/statut").json()
    assert corps["ia_activee"] is False
    assert corps["source_decision"] == "interface"
    assert "désactivées" in corps["message"]


def test_garde_fou_avant_tout_acces_db(_ia_desactivee):
    """Aucun accès aux données quand l'IA est coupée (pas d'appel silencieux)."""
    fausse_session = MagicMock()
    fausse_session.get.side_effect = AssertionError("accès DB interdit")
    fausse_session.query.side_effect = AssertionError("accès DB interdit")
    with pytest.raises(ia_module.IaDesactiveeError, match="désactivée"):
        ia_module.synthese_plante(fausse_session, 1)
    fausse_session.get.assert_not_called()
    fausse_session.query.assert_not_called()


def test_fiche_cache_section_ia_quand_desactivee(client, _ia_desactivee):
    ids = _jeu_complet(client)
    reponse = client.get(f"/plantes/{ids['plante']['id']}")
    assert reponse.status_code == 200, reponse.text
    assert "synthese-ia" not in reponse.text
    assert "Synthèse IA" not in reponse.text


def test_page_ui_503_quand_desactivee(client, _ia_desactivee):
    ids = _jeu_complet(client)
    reponse = client.get(f"/plantes/{ids['plante']['id']}/synthese-ia")
    assert reponse.status_code == 503, reponse.text
