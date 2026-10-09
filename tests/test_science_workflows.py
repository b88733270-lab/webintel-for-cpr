"""Tests du workflow chercheur — Phase 7 (`/api/science/workflows`).

Couverture :
- workflow complet ``analyse_plante`` : création 201, 7 avancées (statuts
  par étape), rapport à 6 sections + fiche dans ``resultat``, avertissement
  « Simulation — à confirmer expérimentalement » dans les sorties de
  l'étape simulation, 409 quand on avance un workflow terminé ;
- reprise après échec simulé : étape 4 ``echouee`` (molécule non associée
  à la plante) → 409, étapes précédentes consultables, correction des
  données puis ``avancer`` reprend et le workflow se termine ;
- ``rejouer`` : mêmes inputs → même ``input_hash``, étapes réinitialisées ;
- inputs invalides → 422, run inconnu → 404 ;
- cloisonnement : 403 vers un projet étranger et 401 sans clé (mode
  serveur ``REQUIRE_API_KEY=true``) ;
- liste cloisonnée au projet.

Note : le routeur est AUSSI enregistré dans ``webintel/main.py``
(``/api/science/workflows``) pour l'application réelle ; les tests
ci-dessous le montent en outre sur une app FastAPI locale dédiée
(``_app``) afin de tester le routeur de façon isolée, sans lifespan.
Aucun réseau : tout passe par le ``TestClient`` et la base de test.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import webintel.config.settings as _settings_mod
from webintel.api.routers import science_workflows
from webintel.core.utils import sha256_hex
from webintel.database.base import init_db
from webintel.database.models import ApiKey, Project
from webintel.science import service as science_service
from webintel.science import workflows
from webintel.simulations.reports import SECTIONS

# Enregistre la table workflow_runs (idempotent, comme test_science_api.py).
init_db()

DOI_LIEN = "10.1016/j.jep.2020.113123"

_app = FastAPI()
_app.include_router(science_workflows.router, prefix="/api")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def wf_client(server_bootstrap):
    """Client HTTP monté sur le routeur des workflows.

    En mode serveur (``REQUIRE_API_KEY=<redacted>), la clé du bootstrap est
    injectée (même motif que la fixture ``client`` de conftest) : sans
    elle, ``api_key_auth`` répondrait 401.
    """
    headers = {}
    if server_bootstrap is not None:
        headers["X-API-Key"] = server_bootstrap["plain_key"]
    return TestClient(_app, headers=headers)


@pytest.fixture()
def projet(db_session, server_bootstrap) -> int:
    """Un projet de test, retourne son id.

    En mode serveur, la création de projet par API est interdite
    (bootstrap-only) : on réutilise le projet du bootstrap, seul
    accessible à la clé du client (même motif que ``make_project``).
    """
    if server_bootstrap is not None:
        return server_bootstrap["project_id"]
    obj = Project(name="Projet Workflow")
    db_session.add(obj)
    db_session.commit()
    return obj.id


def _jeu_de_donnees(db_session, avec_doi: bool = True) -> dict:
    """Plante + composé (+ lien + preuve). Retourne les ids.

    Si ``avec_doi`` est False, le composé n'est PAS lié à la plante
    (sert au test de reprise après échec).
    """
    plante, _ = science_service.create_plant(
        db_session, "Moringa oleifera", family="Moringaceae"
    )
    compose, _ = science_service.create_compound(
        db_session, "Quercétine", formula="C15H10O7",
        chemical_class="flavonoïde",
    )
    if avec_doi:
        science_service.link_plant_compound(
            db_session, plante.id, compose.id,
            plant_part="feuilles",
            publication="Journal of Ethnopharmacology",
            doi=DOI_LIEN,
        )
        science_service.add_evidence(
            db_session, doi=DOI_LIEN,
            title="Moringa oleifera : revue phytochimique",
            authors=["A. Sawadogo", "B. Traoré"],
            year=2020,
            reference="J. Ethnopharmacol. 2020",
        )
    db_session.commit()
    return {"plant_id": plante.id, "compound_id": compose.id}


def _creer_workflow(wf_client, project_id: int, ids: dict,
                    nom: str = "Analyse Moringa") -> dict:
    """Crée un workflow analyse_plante via l'API (assert 201)."""
    reponse = wf_client.post(
        "/api/science/workflows",
        json={
            "type_workflow": "analyse_plante",
            "nom": nom,
            "inputs": {
                "plant_id": ids["plant_id"],
                "compound_id": ids["compound_id"],
            },
            "project_id": project_id,
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _avancer(wf_client, run_id: int) -> dict:
    """Avance d'une étape via l'API (assert 200)."""
    reponse = wf_client.post(f"/api/science/workflows/{run_id}/avancer")
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


# ---------------------------------------------------------------------------
# Workflow complet : plante → rapport
# ---------------------------------------------------------------------------

def test_creation_workflow_201(wf_client, projet, db_session):
    """POST /api/science/workflows → 201, 7 étapes en attente, input_hash."""
    ids = _jeu_de_donnees(db_session)
    run = _creer_workflow(wf_client, projet, ids)

    assert run["statut"] == "en_cours"
    assert run["etape_courante"] == 0
    assert run["project_id"] == projet
    assert run["type_workflow"] == "analyse_plante"
    assert len(run["input_hash"]) == 64
    assert len(run["etapes"]) == 7
    assert [e["nom"] for e in run["etapes"]] == [
        "choisir_plante", "constituants", "publications", "choisir_molecule",
        "simulation", "rapport", "export",
    ]
    assert {e["statut"] for e in run["etapes"]} == {"en_attente"}
    assert run["resultat"] is None


def test_workflow_complet_7_etapes(wf_client, projet, db_session):
    """7 avancées → run terminé, rapport à 6 sections + fiche dans resultat."""
    ids = _jeu_de_donnees(db_session)
    run = _creer_workflow(wf_client, projet, ids)
    run_id = run["id"]

    statuts_attendus = ["en_cours"] * 6 + ["termine"]
    for numero in range(7):
        run = _avancer(wf_client, run_id)
        assert run["statut"] == statuts_attendus[numero], run["statut"]
        assert run["etape_courante"] == numero + 1
        terminees = [e for e in run["etapes"] if e["statut"] == "terminee"]
        assert len(terminees) == numero + 1
        # L'étape qui vient de s'exécuter porte des sorties.
        assert run["etapes"][numero]["sorties"], (
            f"étape {run['etapes'][numero]['nom']} sans sorties"
        )

    etapes = {e["nom"]: e for e in run["etapes"]}

    # Étape 1 : la plante est identifiée.
    assert etapes["choisir_plante"]["sorties"]["nom_scientifique"] == (
        "Moringa oleifera"
    )
    # Étape 2 : le constituant est recensé.
    constituants = etapes["constituants"]["sorties"]["constituants"]
    assert etapes["constituants"]["sorties"]["nombre"] == 1
    assert constituants[0]["nom"] == "Quercétine"
    # Étape 3 : la publication liée par DOI est collectée.
    assert etapes["publications"]["sorties"]["nombre"] == 1
    assert etapes["publications"]["sorties"]["publications"][0]["doi"] == DOI_LIEN
    # Étape 4 : la molécule choisie.
    assert etapes["choisir_molecule"]["sorties"]["formule"] == "C15H10O7"
    # Étape 5 : simulation persistée + avertissement dans les sorties.
    sim = etapes["simulation"]["sorties"]
    assert sim["moteur"] == "property_prediction"
    assert sim["simulation_id"] > 0
    assert "Simulation — à confirmer expérimentalement" in sim["avertissement"]
    # Étape 6 : rapport à 6 sections.
    rapport = etapes["rapport"]["sorties"]["rapport"]
    for section in SECTIONS:
        assert section in rapport, f"section manquante : {section}"
    assert etapes["rapport"]["sorties"]["sections"] == list(SECTIONS)

    # Étape 7 / export : resultat = rapport + fiche + avertissement.
    resultat = run["resultat"]
    assert resultat is not None
    assert "Simulation — à confirmer expérimentalement" in resultat["avertissement"]
    assert set(resultat["rapport"].keys()) >= set(SECTIONS)
    fiche = resultat["fiche"]
    assert fiche["plante"]["nom_scientifique"] == "Moringa oleifera"
    assert len(fiche["phytochimie"]) == 1
    assert len(fiche["pharmacologie"]) == 1
    # La simulation du workflow figure dans la fiche (données du projet).
    assert len(fiche["simulations"]) == 1
    assert resultat["simulation_id"] == sim["simulation_id"]

    # Le run est terminé : avancer à nouveau → 409.
    reponse = wf_client.post(f"/api/science/workflows/{run_id}/avancer")
    assert reponse.status_code == 409, reponse.text
    assert "terminé" in reponse.json()["detail"]


def test_avancer_run_inexistant_404(wf_client):
    """GET et POST avancer sur un run inconnu → 404 (message français)."""
    assert wf_client.get("/api/science/workflows/999999").status_code == 404
    reponse = wf_client.post("/api/science/workflows/999999/avancer")
    assert reponse.status_code == 404, reponse.text
    assert "introuvable" in reponse.json()["detail"]


# ---------------------------------------------------------------------------
# Reprise après échec
# ---------------------------------------------------------------------------

def test_reprise_apres_echec_etape(wf_client, projet, db_session):
    """Étape 4 échoue (molécule non associée) → 409, reprise après correction."""
    ids = _jeu_de_donnees(db_session, avec_doi=False)
    run = _creer_workflow(wf_client, projet, ids)
    run_id = run["id"]

    # Étapes 1 à 3 : OK.
    for _ in range(3):
        _avancer(wf_client, run_id)

    # Étape 4 : le composé n'est pas lié à la plante → 409.
    reponse = wf_client.post(f"/api/science/workflows/{run_id}/avancer")
    assert reponse.status_code == 409, reponse.text
    assert "associé" in reponse.json()["detail"]

    # L'état d'échec est consultable : run « echoue », étape 4 « echouee »
    # avec son message, étapes 1-3 toujours « terminee ».
    detail = wf_client.get(f"/api/science/workflows/{run_id}").json()
    assert detail["statut"] == "echoue"
    etapes = {e["nom"]: e for e in detail["etapes"]}
    assert etapes["choisir_molecule"]["statut"] == "echouee"
    assert etapes["choisir_molecule"]["erreur"]
    assert etapes["choisir_plante"]["statut"] == "terminee"
    assert etapes["constituants"]["sorties"]["nombre"] == 0

    # Correction des données : on lie le composé à la plante.
    science_service.link_plant_compound(
        db_session, ids["plant_id"], ids["compound_id"],
        plant_part="feuilles", publication="Note de laboratoire",
    )
    db_session.commit()

    # Reprise : avancer rejoue l'étape 4 puis continue jusqu'au bout.
    run = _avancer(wf_client, run_id)
    assert run["statut"] == "en_cours"
    etapes = {e["nom"]: e for e in run["etapes"]}
    assert etapes["choisir_molecule"]["statut"] == "terminee"
    assert etapes["choisir_molecule"]["erreur"] is None

    for _ in range(3):
        run = _avancer(wf_client, run_id)
    assert run["statut"] == "termine"
    assert run["resultat"]["fiche"]["plante"]["nom_scientifique"] == (
        "Moringa oleifera"
    )


# ---------------------------------------------------------------------------
# Rejouer : mêmes inputs → même input_hash
# ---------------------------------------------------------------------------

def test_rejouer_meme_input_hash(wf_client, projet, db_session):
    """POST .../rejouer → 200, input_hash identique, étapes réinitialisées."""
    ids = _jeu_de_donnees(db_session)
    run = _creer_workflow(wf_client, projet, ids)
    run_id = run["id"]
    hash_initial = run["input_hash"]

    # Exécution partielle puis rejouement.
    _avancer(wf_client, run_id)
    _avancer(wf_client, run_id)

    reponse = wf_client.post(f"/api/science/workflows/{run_id}/rejouer")
    assert reponse.status_code == 200, reponse.text
    run = reponse.json()
    assert run["input_hash"] == hash_initial
    assert run["statut"] == "en_cours"
    assert run["etape_courante"] == 0
    assert run["resultat"] is None
    assert {e["statut"] for e in run["etapes"]} == {"en_attente"}
    # Les inputs initiaux sont conservés dans les étapes.
    assert run["etapes"][0]["entrees"]["plant_id"] == ids["plant_id"]
    assert run["etapes"][3]["entrees"]["compound_id"] == ids["compound_id"]

    # Le run rejoue normalement depuis le début.
    run = _avancer(wf_client, run_id)
    assert run["etapes"][0]["statut"] == "terminee"
    assert run["etape_courante"] == 1


def test_rejouer_run_inexistant_404(wf_client):
    """POST .../rejouer sur un run inconnu → 404."""
    reponse = wf_client.post("/api/science/workflows/999999/rejouer")
    assert reponse.status_code == 404, reponse.text


# ---------------------------------------------------------------------------
# Inputs invalides → 422
# ---------------------------------------------------------------------------

def test_creation_inputs_invalides_422(wf_client, projet, db_session):
    """Type inconnu, inputs incomplets ou mal typés → 422 (français)."""
    ids = _jeu_de_donnees(db_session)

    cas = [
        # type inconnu
        {"type_workflow": "inexistant", "nom": "X",
         "inputs": {"plant_id": 1, "compound_id": 2}},
        # compound_id manquant
        {"type_workflow": "analyse_plante", "nom": "X",
         "inputs": {"plant_id": ids["plant_id"]}},
        # plant_id mal typé
        {"type_workflow": "analyse_plante", "nom": "X",
         "inputs": {"plant_id": "abc", "compound_id": ids["compound_id"]}},
        # inputs vides
        {"type_workflow": "analyse_plante", "nom": "X", "inputs": {}},
        # moteur inconnu
        {"type_workflow": "analyse_plante", "nom": "X",
         "inputs": {"plant_id": ids["plant_id"],
                    "compound_id": ids["compound_id"],
                    "engine_name": "moteur_magique"}},
    ]
    for corps in cas:
        reponse = wf_client.post(
            "/api/science/workflows",
            json={**corps, "project_id": projet},
        )
        assert reponse.status_code == 422, (corps, reponse.text)
        assert reponse.json()["detail"], "message d'erreur attendu"


# ---------------------------------------------------------------------------
# Liste cloisonnée au projet (mode local)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("REQUIRE_API_KEY") == "true",
    reason="test intrinsèquement local : il crée deux projets et liste "
    "l'un depuis l'autre, impossible avec une clé cloisonnée en mode serveur",
)
def test_liste_workflows_cloisonnee_projet(wf_client, db_session):
    """GET /science/workflows?project_id=… ne retourne que le projet demandé."""
    ids = _jeu_de_donnees(db_session)
    p1 = Project(name="P1")
    p2 = Project(name="P2")
    db_session.add_all([p1, p2])
    db_session.commit()

    _creer_workflow(wf_client, p1.id, ids, nom="Run P1")
    _creer_workflow(wf_client, p2.id, ids, nom="Run P2")

    reponse = wf_client.get(f"/api/science/workflows?project_id={p1.id}")
    assert reponse.status_code == 200, reponse.text
    runs = reponse.json()
    assert len(runs) == 1
    assert runs[0]["nom"] == "Run P1"
    assert runs[0]["project_id"] == p1.id


# ---------------------------------------------------------------------------
# Mode serveur : 403 projet étranger, 401 sans clé
# ---------------------------------------------------------------------------

@pytest.fixture()
def key_mode(monkeypatch):
    """Active REQUIRE_API_KEY=true sur le singleton de configuration.

    Même motif que tests/test_project_isolation.py : `Settings` lit
    l'environnement à l'import, on force donc l'attribut sur l'instance.
    """
    settings = _settings_mod.Settings()
    settings.REQUIRE_API_KEY = True
    _settings_mod._settings = settings
    yield
    _settings_mod._settings = None


def _projet_avec_cle(db_session, nom: str):
    """Crée un projet + une clé API (directement en DB : bootstrap)."""
    projet = Project(name=nom)
    db_session.add(projet)
    db_session.flush()
    cle_claire = "cle-test-" + nom.replace(" ", "-") + "-x" * 8
    db_session.add(ApiKey(
        project_id=projet.id,
        name=f"clé-{nom}",
        key_hash=sha256_hex(cle_claire),
        prefix=cle_claire[:8],
        quotas={},
        revoked=False,
    ))
    db_session.commit()
    return projet.id, cle_claire


@pytest.fixture()
def deux_projets_cles(key_mode, db_session):
    """Deux projets isolés, chacun avec sa clé (mode serveur)."""
    p1, k1 = _projet_avec_cle(db_session, "Projet 1")
    p2, k2 = _projet_avec_cle(db_session, "Projet 2")
    return {"p1": p1, "k1": k1, "p2": p2, "k2": k2}


def _client_avec_cle(cle: str) -> TestClient:
    return TestClient(_app, headers={"X-API-Key": cle})


def test_projet_etranger_403_en_serveur(deux_projets_cles, db_session):
    """En mode serveur, la clé du projet 1 ne touche pas aux runs du projet 2."""
    ctx = deux_projets_cles
    ids = _jeu_de_donnees(db_session)
    client1 = _client_avec_cle(ctx["k1"])
    client2 = _client_avec_cle(ctx["k2"])

    # Création cloisonnée : la clé 1 crée dans son projet (project_id omis).
    reponse = client1.post(
        "/api/science/workflows",
        json={"type_workflow": "analyse_plante", "nom": "Run P1",
              "inputs": {"plant_id": ids["plant_id"],
                         "compound_id": ids["compound_id"]}},
    )
    assert reponse.status_code == 201, reponse.text
    run_id = reponse.json()["id"]
    assert reponse.json()["project_id"] == ctx["p1"]

    # La clé 2 ne peut ni lire, ni avancer, ni rejouer ce run → 403.
    assert client2.get(f"/api/science/workflows/{run_id}").status_code == 403
    assert client2.post(
        f"/api/science/workflows/{run_id}/avancer").status_code == 403
    assert client2.post(
        f"/api/science/workflows/{run_id}/rejouer").status_code == 403

    # Liste avec un project_id étranger → 403.
    reponse = client1.get(f"/api/science/workflows?project_id={ctx['p2']}")
    assert reponse.status_code == 403, reponse.text

    # Création explicite dans le projet étranger → 403.
    reponse = client1.post(
        "/api/science/workflows",
        json={"type_workflow": "analyse_plante", "nom": "Run P2",
              "inputs": {"plant_id": ids["plant_id"],
                         "compound_id": ids["compound_id"]},
              "project_id": ctx["p2"]},
    )
    assert reponse.status_code == 403, reponse.text


def test_sans_cle_401_en_serveur(key_mode, db_session):
    """En mode serveur, sans X-API-Key → 401 sur toutes les routes."""
    anon = TestClient(_app)
    assert anon.post("/api/science/workflows", json={}).status_code == 401
    assert anon.get("/api/science/workflows").status_code == 401
    assert anon.get("/api/science/workflows/1").status_code == 401
    assert anon.post("/api/science/workflows/1/avancer").status_code == 401
    assert anon.post("/api/science/workflows/1/rejouer").status_code == 401


# ---------------------------------------------------------------------------
# Services directs (sans HTTP) : discipline « ne committe jamais »
# ---------------------------------------------------------------------------

def test_service_ne_committe_jamais(db_session, projet):
    """creer_workflow / avancer_workflow / rejouer_workflow ne committent pas."""
    ids = _jeu_de_donnees(db_session)
    run = workflows.creer_workflow(
        db_session, project_id=projet, type_workflow="analyse_plante",
        nom="Service direct", inputs={"plant_id": ids["plant_id"],
                                     "compound_id": ids["compound_id"]},
        cree_par="test",
    )
    # Pas de commit : un rollback fait disparaître le run.
    db_session.rollback()
    assert db_session.get(workflows.WorkflowRun, run.id) is None

    # get_workflow / list_workflows.
    run = workflows.creer_workflow(
        db_session, project_id=projet, type_workflow="analyse_plante",
        nom="Service direct", inputs={"plant_id": ids["plant_id"],
                                     "compound_id": ids["compound_id"]},
    )
    db_session.commit()
    assert workflows.get_workflow(db_session, run.id).nom == "Service direct"
    assert [r.id for r in workflows.list_workflows(db_session, projet)] == [run.id]
    assert workflows.list_workflows(db_session, projet + 9999) == []
    with pytest.raises(KeyError):
        workflows.get_workflow(db_session, 999999)
