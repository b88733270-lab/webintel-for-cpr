"""Tests IDOR inter-projets — correctifs P0-1 / P1-1 / P1-4.

Scénario : deux projets A et B, chacun avec sa clé API. En mode serveur
(`key_mode`), la clé du projet A ne doit ni lire, ni modifier, ni
exporter les simulations, études et dossiers du projet B — par API ET
par UI. IDs inexistants/étrangers → 403/404, jamais de fuite de données.

Le catalogue scientifique global (plantes / composés / preuves) reste
lisible par tous : c'est le design voulu, non testé ici.
"""

import secrets

import pytest
from fastapi.testclient import TestClient

import webintel.config.settings as _settings_mod
import webintel.main
from webintel.core.utils import sha256_hex
from webintel.database.models import ApiKey, Project
from webintel.science.models import CombinationStudy


@pytest.fixture()
def key_mode(monkeypatch):
    """Active `REQUIRE_API_KEY=<redacted> (même motif que
    `test_project_isolation.py`)."""
    settings = _settings_mod.Settings()
    settings.REQUIRE_API_KEY = True
    _settings_mod._settings = settings
    yield
    _settings_mod._settings = None


def _project_with_key(db_session, name: str):
    """Crée un projet + une clé API (directement en DB : bootstrap)."""
    project = Project(name=name)
    db_session.add(project)
    db_session.flush()
    plain = secrets.token_urlsafe(32)
    key = ApiKey(
        project_id=project.id,
        name=f"clé-{name}",
        key_hash=sha256_hex(plain),
        prefix=plain[:8],
        quotas={},
        revoked=False,
    )
    db_session.add(key)
    db_session.commit()
    return project, plain


@pytest.fixture()
def two_projects(db_session):
    """Deux projets isolés, chacun avec sa clé."""
    p1, k1 = _project_with_key(db_session, "Projet IDOR A")
    p2, k2 = _project_with_key(db_session, "Projet IDOR B")
    return {"p1": p1.id, "k1": k1, "p2": p2.id, "k2": k2}


@pytest.fixture()
def api():
    """Client API brut (base https : le cookie UI porte le flag Secure)."""
    with TestClient(webintel.main.app, base_url="https://testserver") as client:
        yield client


def _h(plain: str) -> dict:
    return {"X-API-Key": plain}


def _ui(plain: str) -> TestClient:
    """Client UI authentifié (session cookie) pour la clé donnée."""
    client = TestClient(webintel.main.app, base_url="https://testserver")
    resp = client.post("/login", data={"api_key": plain})
    assert resp.status_code in (200, 303), resp.text
    return client


def _simulation_a(api, headers: dict, **extra) -> dict:
    corps = {
        "name": "Sim IDOR",
        "target": "COX-2",
        "method": "test",
        "parameters": {"x": 1},
    }
    corps.update(extra)
    resp = api.post("/api/science/simulations", json=corps, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _etude_a(db_session, project_id: int) -> CombinationStudy:
    etude = CombinationStudy(
        project_id=project_id,
        nom="Étude IDOR",
        compound_ids=[1, 2],
        inputs_hash="i" * 64,
        statut="brouillon",
        data_level="prediction",
        resultats={},
        provenance={},
    )
    db_session.add(etude)
    db_session.commit()
    return etude


# ---------------------------------------------------------------------------
# P0-1 — création : project_id obligatoire, scopé à la clé
# ---------------------------------------------------------------------------

def test_creation_simulation_scoped_au_projet_de_la_cle(api, two_projects, key_mode):
    """Sans `project_id`, la simulation naît dans le projet de la clé —
    jamais orpheline (`project_id` NOT NULL)."""
    sim = _simulation_a(api, _h(two_projects["k1"]))
    assert sim["project_id"] == two_projects["p1"]
    assert sim["project_id"] is not None


def test_creation_simulation_projet_etranger_403(api, two_projects, key_mode):
    """`project_id` vers un projet étranger → 403 ; vers son projet → 201."""
    h1 = _h(two_projects["k1"])
    resp = api.post(
        "/api/science/simulations",
        json={"name": "X", "parameters": {"x": 1},
              "project_id": two_projects["p2"]},
        headers=h1,
    )
    assert resp.status_code == 403, resp.text
    sim = _simulation_a(api, _h(two_projects["k2"]),
                        project_id=two_projects["p2"])
    assert sim["project_id"] == two_projects["p2"]


def test_run_moteur_projet_etranger_403(api, two_projects, key_mode):
    """`POST /run` vers un projet étranger → 403 ; sans projet → 201
    dans le projet de la clé."""
    corps = {
        "engine_name": "statistics",
        "name": "Run IDOR",
        "inputs": {"observations": [{"valeur": 1.0}]},
        "project_id": two_projects["p2"],
    }
    resp = api.post("/api/science/simulations/run", json=corps,
                    headers=_h(two_projects["k1"]))
    assert resp.status_code == 403, resp.text
    del corps["project_id"]
    resp = api.post("/api/science/simulations/run", json=corps,
                    headers=_h(two_projects["k1"]))
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# P0-1 — lectures : 403 inter-projets, 404 si inexistant, jamais de fuite
# ---------------------------------------------------------------------------

def test_lecture_simulation_projet_etranger_403(api, two_projects, key_mode):
    """Détail, rapport et rejeu d'une simulation étrangère → 403 (pas 200,
    pas de fuite) ; le propriétaire lit en 200."""
    h1, h2 = _h(two_projects["k1"]), _h(two_projects["k2"])
    sim = _simulation_a(api, h1)
    sid = sim["id"]

    assert api.get(f"/api/science/simulations/{sid}", headers=h1).status_code == 200
    for methode, url in (
        ("get", f"/api/science/simulations/{sid}"),
        ("get", f"/api/science/simulations/{sid}/rapport"),
        ("post", f"/api/science/simulations/{sid}/rejouer"),
    ):
        resp = getattr(api, methode)(url, headers=h2)
        assert resp.status_code == 403, (methode, url, resp.status_code)
        # Aucune donnée métier ne fuit dans une 403.
        assert sim["name"] not in resp.text


def test_simulation_inexistante_404(api, two_projects, key_mode):
    """ID inexistant → 404 pour les deux clés (pas d'oracle 403/404)."""
    h1, h2 = _h(two_projects["k1"]), _h(two_projects["k2"])
    for headers in (h1, h2):
        assert api.get("/api/science/simulations/999999",
                       headers=headers).status_code == 404
        assert api.get("/api/science/simulations/999999/rapport",
                       headers=headers).status_code == 404


def test_liste_simulations_isolee(api, two_projects, key_mode):
    """Chaque clé ne liste que ses simulations ; le filtre vers le projet
    étranger → 403."""
    h1, h2 = _h(two_projects["k1"]), _h(two_projects["k2"])
    _simulation_a(api, h1)
    assert api.get("/api/science/simulations", headers=h2).json() == []
    assert len(api.get("/api/science/simulations", headers=h1).json()) == 1
    assert api.get("/api/science/simulations",
                   params={"project_id": two_projects["p1"]},
                   headers=h2).status_code == 403


def test_require_project_access_strict_refuse_orpheline(db_session, two_projects,
                                                         key_mode):
    """Garde-fou P0-1 : une donnée orpheline (``project_id=None`` —
    antérieure au durcissement, normalement rattachée par la migration
    ``init_db``) est REFUSÉE en mode serveur : il n'existe plus aucun
    chemin « si project_id is None → pas de contrôle »."""
    from fastapi import HTTPException

    from webintel.api.deps import require_project_access_strict

    cle_a = db_session.query(ApiKey).filter_by(
        name="clé-Projet IDOR A").one()
    # Orpheline + clé serveur → 403.
    with pytest.raises(HTTPException) as exc_info:
        require_project_access_strict(cle_a, None)
    assert exc_info.value.status_code == 403
    # Projet étranger → 403 ; projet de la clé → OK.
    with pytest.raises(HTTPException):
        require_project_access_strict(cle_a, two_projects["p2"])
    require_project_access_strict(cle_a, two_projects["p1"])
    # Mode bypass (local) : aucun contrôle, comportement v1 inchangé.
    require_project_access_strict(None, None)


# ---------------------------------------------------------------------------
# P0-1 — modification : PATCH inter-projets refusé
# ---------------------------------------------------------------------------

def test_patch_simulation_projet_etranger_refuse(api, two_projects, key_mode):
    """PATCH d'une simulation étrangère : ni 200 ni modification — et le
    propriétaire peut modifier la sienne."""
    h1, h2 = _h(two_projects["k1"]), _h(two_projects["k2"])
    sim = _simulation_a(api, h1, description="originale")
    sid = sim["id"]

    resp = api.patch(f"/api/science/lab/simulations/{sid}",
                     json={"description": "piratée"}, headers=h2)
    assert resp.status_code in (403, 404), resp.status_code
    assert "piratée" not in resp.text

    resp = api.patch(f"/api/science/lab/simulations/{sid}",
                     json={"description": "mise à jour"}, headers=h1)
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "mise à jour"


# ---------------------------------------------------------------------------
# P1-1 — export de dossier : 403 inter-projets quel que soit le flag
# ---------------------------------------------------------------------------

def test_export_dossier_projet_etranger_403(api, two_projects, key_mode):
    """Le dossier d'un projet étranger est refusé (403) avec
    `inclure_prive` absent, faux OU vrai ; le propriétaire l'obtient."""
    h1, h2 = _h(two_projects["k1"]), _h(two_projects["k2"])
    pid1 = two_projects["p1"]
    for corps in ({"projet_id": pid1},
                  {"projet_id": pid1, "inclure_prive": False},
                  {"projet_id": pid1, "inclure_prive": True}):
        resp = api.post("/api/science/export/dossier", json=corps, headers=h2)
        assert resp.status_code == 403, (corps, resp.status_code)
    resp = api.post("/api/science/export/dossier",
                    json={"projet_id": pid1}, headers=h1)
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# P1-4 — import : projet_id validé, import rattaché au projet contrôlé
# ---------------------------------------------------------------------------

_CSV_PLANTES = (
    "nom_scientifique;famille;genre;espece;noms_vernaculaires;synonymes;"
    "description;distribution;parties_utilisees;ids_externes\n"
    "Moringa oleifera;Moringaceae;Moringa;oleifera;;;Arbre test.;Afrique;"
    "feuilles;\n"
).encode("utf-8")


def _import(api, headers, **data):
    fichiers = {"fichier": ("plantes.csv", _CSV_PLANTES, "text/csv")}
    donnees = {"type_donnees": "plante"}
    donnees.update(data)
    return api.post("/api/science/import/", files=fichiers, data=donnees,
                    headers=headers)


def test_import_projet_etranger_403(api, two_projects, key_mode):
    """Un `projet_id` étranger est refusé (403), pas ignoré."""
    resp = _import(api, _h(two_projects["k1"]),
                   projet_id=str(two_projects["p2"]))
    assert resp.status_code == 403, resp.text


def test_import_rattache_au_projet_declare_et_controle(api, two_projects,
                                                      key_mode):
    """L'import s'écrit dans le projet déclaré et contrôlé : le rapport
    et la provenance portent son id, jamais un autre."""
    pid1 = two_projects["p1"]
    for data in ({}, {"projet_id": str(pid1)}):
        resp = _import(api, _h(two_projects["k1"]), **data)
        assert resp.status_code == 200, resp.text
        corps = resp.json()
        assert corps["rapport"]["contexte"]["projet_id"] == pid1
        assert corps["provenance"]["projet_id"] == pid1


# ---------------------------------------------------------------------------
# Études de combinaison : 403 inter-projets
# ---------------------------------------------------------------------------

def test_etude_combinaison_projet_etranger_403(api, db_session, two_projects,
                                              key_mode):
    """Détail et rapport d'une étude étrangère → 403 ; propriétaire → 200."""
    h1, h2 = _h(two_projects["k1"]), _h(two_projects["k2"])
    etude = _etude_a(db_session, two_projects["p1"])

    assert api.get(f"/api/science/combinaisons/{etude.id}",
                   headers=h1).status_code == 200
    for url in (f"/api/science/combinaisons/{etude.id}",
                f"/api/science/combinaisons/{etude.id}/rapport"):
        resp = api.get(url, headers=h2)
        assert resp.status_code == 403, (url, resp.status_code)
        assert "Étude IDOR" not in resp.text


# ---------------------------------------------------------------------------
# UI : cloisonnement par la session
# ---------------------------------------------------------------------------

def test_ui_rapport_simulation_projet_etranger_404(api, two_projects, key_mode):
    """La page UI du rapport d'une simulation étrangère → 404 ; le
    propriétaire la voit en 200."""
    sim = _simulation_a(api, _h(two_projects["k1"]))
    ui_a, ui_b = _ui(two_projects["k1"]), _ui(two_projects["k2"])

    assert ui_a.get(f"/simulations/{sim['id']}/rapport").status_code == 200
    resp = ui_b.get(f"/simulations/{sim['id']}/rapport")
    assert resp.status_code == 404, resp.status_code
    assert sim["name"] not in resp.text


def test_ui_export_dossier_scoped_a_la_session(api, two_projects, key_mode):
    """L'export UI télécharge le dossier du projet DE LA SESSION,
    jamais celui d'un autre projet."""
    _simulation_a(api, _h(two_projects["k1"]))
    ui_b = _ui(two_projects["k2"])
    resp = ui_b.post("/export/dossier", data={"inclure_simulations": "oui"})
    assert resp.status_code == 200, resp.text
    disposition = resp.headers.get("content-disposition", "")
    assert f"dossier_recherche_projet_{two_projects['p2']}.zip" in disposition
    assert f"projet_{two_projects['p1']}" not in disposition
