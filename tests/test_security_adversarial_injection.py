"""Tests adversariaux — chantier SÉCURITÉ P0 : injections et fuites.

Couvre : injection SQL (recherche, chemins d'ID), XSS stocké et réfléchi,
injection de commande (adaptateur Vina : argv fixe, shell=False),
traversée de chemin dans les uploads, fuite GPS / identités dans les
exports publics, redirections ouvertes et divulgation d'erreurs.

Convention : chaque test décrit l'attaque puis affirme le comportement
sûr attendu. Tout échec = faille réelle à corriger.
"""

from __future__ import annotations

import os
import secrets

import pytest

from webintel.core.utils import sha256_hex
from webintel.database.base import SessionLocal, init_db
from webintel.database.models import ApiKey, Project
import webintel.config.settings as sm


@pytest.fixture()
def key_mode(monkeypatch):
    """Mode serveur : clé API obligatoire (comme en production)."""
    settings = sm.get_settings()
    monkeypatch.setattr(settings, "REQUIRE_API_KEY", True)
    monkeypatch.setattr(
        settings, "SECRET_KEY", "cle-secrete-test-injection-32-caracteres!!")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    return settings


@pytest.fixture()
def app(key_mode):
    import webintel.main
    return webintel.main.app


@pytest.fixture()
def projet_cle(app, db_session):
    """Un projet + une clé API valide (base de test partagée)."""
    projet = Project(name="Projet Injection")
    db_session.add(projet)
    db_session.flush()
    brut = secrets.token_urlsafe(32)
    db_session.add(ApiKey(
        project_id=projet.id, name="cle-injection",
        key_hash=sha256_hex(brut), prefix=brut[:8], quotas={},
        revoked=False))
    db_session.commit()
    return projet.id, brut


@pytest.fixture()
def api(app):
    from fastapi.testclient import TestClient
    return TestClient(app, base_url="https://testserver")


@pytest.fixture()
def ui(app, projet_cle):
    from fastapi.testclient import TestClient
    pid, brut = projet_cle
    client = TestClient(app, base_url="https://testserver")
    r = client.post("/login", data={"api_key": brut}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return client


def _h(brut: str) -> dict:
    return {"X-API-Key": brut}


# ---------------------------------------------------------------------------
# Injection SQL
# ---------------------------------------------------------------------------

PAYLOADS_SQLI = [
    "' OR '1'='1",
    "' OR '1'='1' --",
    "\" OR \"1\"=\"1",
    "'; DROP TABLE plants; --",
    "' UNION SELECT 1,2,3 --",
]


@pytest.mark.parametrize("payload", PAYLOADS_SQLI)
def test_sqli_recherche_science(api, projet_cle, payload):
    """Une injection SQL dans la recherche ne doit ni fuir ni casser."""
    pid, brut = projet_cle
    r = api.get("/api/science/recherche",
                params={"q": payload, "project_id": pid}, headers=_h(brut))
    assert r.status_code in (200, 400, 422), r.text
    assert "sqlite3" not in r.text.lower()
    assert "traceback" not in r.text.lower()


@pytest.mark.parametrize("payload", PAYLOADS_SQLI)
def test_sqli_recherche_terrain(api, projet_cle, payload):
    pid, brut = projet_cle
    r = api.get("/api/science/terrain/recherche",
                params={"q": payload, "project_id": pid}, headers=_h(brut))
    assert r.status_code in (200, 400, 422), r.text
    assert "traceback" not in r.text.lower()


def test_sqli_chemin_id(api, projet_cle):
    """Un ID non entier ne doit pas atteindre le SQL."""
    pid, brut = projet_cle
    r = api.get("/api/science/terrain/echantillons/1%20OR%201=1",
                headers=_h(brut))
    assert r.status_code in (404, 422), r.text
    assert "traceback" not in r.text.lower()


# ---------------------------------------------------------------------------
# XSS réfléchi
# ---------------------------------------------------------------------------

PAYLOADS_XSS = [
    "<script>alert(1)</script>",
    "\"><script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "<svg onload=alert(1)>",
]


@pytest.mark.parametrize("payload", PAYLOADS_XSS)
def test_xss_reflechi_recherche_ui(ui, payload):
    """La page de recherche reflète la requête échappée, jamais brute."""
    r = ui.get("/recherche-terrain", params={"q": payload})
    assert r.status_code == 200, r.text
    assert payload not in r.text
    assert ("&lt;script&gt;" in r.text or "&lt;img" in r.text
            or "&lt;svg" in r.text)


@pytest.mark.parametrize("payload", PAYLOADS_XSS)
def test_xss_reflechi_erreur_404(ui, payload):
    """Les pages d'erreur ne reflètent pas le chemin brut."""
    r = ui.get(f"/page-qui-n-existe-pas-{payload}")
    assert r.status_code == 404, r.text
    assert "<script>alert(1)</script>" not in r.text


# ---------------------------------------------------------------------------
# XSS stocké
# ---------------------------------------------------------------------------

def test_xss_stocke_nom_vernaculaire(api, ui, projet_cle, db_session):
    """Un nom vernaculaire malveillant est échappé dans les pages HTML."""
    from webintel.science import models as m
    pid, brut = projet_cle
    plante = m.Plant(scientific_name="ADV XSS")
    db_session.add(plante)
    db_session.commit()
    plant_id = plante.id
    payload = "<script>alert('xss-stocke')</script>"
    r = api.post("/api/science/terrain/noms-vernaculaires",
                 json={"project_id": pid, "plant_id": plant_id,
                       "nom_original": payload},
                 headers=_h(brut))
    assert r.status_code in (200, 201), r.text
    # L'API JSON retourne la donnée brute (normal en JSON)…
    assert r.json()["nom_original"] == payload
    # …mais la page HTML de la plante doit l'échapper.
    r = ui.get(f"/plantes/{plant_id}/terrain")
    assert r.status_code == 200, r.text
    assert payload not in r.text
    assert "&lt;script&gt;" in r.text


# ---------------------------------------------------------------------------
# Injection de commande
# ---------------------------------------------------------------------------

def test_command_injection_argv_vina(tmp_path):
    """L'argv Vina reste une liste : les entrées hostiles ne forment
    jamais une chaîne shell, et aucun appel n'utilise de shell."""
    import ast
    from webintel.simulations.plugins import docking_adapter as da
    hostile = "ligand.pdbqt; touch /tmp/pwned-vina #"
    argv = da._construire_argv(
        "/usr/bin/vina", "/tmp/r.pdbqt", hostile, "/tmp/o.pdbqt",
        [0.0, 0.0, 0.0], [20.0, 20.0, 20.0], 8, 5, 42)
    assert isinstance(argv, list)
    # L'entrée hostile est UN seul élément d'argv, jamais interpolée.
    assert hostile in argv
    assert not any(isinstance(a, str) and "; touch" in a and a != hostile
                   for a in argv)
    # Analyse statique : aucun subprocess avec shell=True dans le module.
    import inspect
    arbre = ast.parse(inspect.getsource(da))
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Call):
            cible = noeud.func
            nom = ""
            if isinstance(cible, ast.Attribute):
                nom = cible.attr
            elif isinstance(cible, ast.Name):
                nom = cible.id
            if nom in ("run", "call", "Popen", "check_output"):
                for motcle in noeud.keywords:
                    if motcle.arg == "shell":
                        assert isinstance(motcle.value, ast.Constant) \
                            and motcle.value.value is False, \
                            f"shell=True détecté dans {nom} !"


def test_command_injection_nom_simulation(api, projet_cle, tmp_path):
    """Un nom de simulation piégé ne doit jamais exécuter de shell."""
    pid, brut = projet_cle
    marqueur = tmp_path / "pwned_simulation"
    payload = f"simu; touch {marqueur} #"
    r = api.post("/api/science/simulations/run",
                 json={"project_id": pid, "engine_name": "similarite",
                       "name": payload, "inputs": {"smiles": "CCO"}},
                 headers=_h(brut))
    assert r.status_code in (200, 400, 404, 422), r.text
    assert not marqueur.exists()


# ---------------------------------------------------------------------------
# Traversée de chemin dans les uploads
# ---------------------------------------------------------------------------

def test_upload_import_nom_traversee(api, projet_cle, tmp_path):
    """Un nom de fichier « ../../ » à l'import ne crée rien hors base."""
    pid, brut = projet_cle
    contenu = b"col1,col2\nval1,val2\n"
    r = api.post(
        "/api/science/import",
        params={"type_donnees": "observations", "project_id": pid},
        files={"fichier": ("../../../../tmp/evil.csv", contenu,
                           "text/csv")},
        headers=_h(brut),
    )
    assert r.status_code in (200, 400, 413, 422), r.text
    assert not os.path.exists("/tmp/evil.csv")


def test_upload_import_zip_piege(api, projet_cle):
    """Un xlsx malformé / à membre piégé est rejeté sans exécution."""
    import io
    import zipfile
    pid, brut = projet_cle
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../../evil.txt", "A" * 1_000_000)
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0"?><Types></Types>')
    buf.seek(0)
    r = api.post(
        "/api/science/import",
        params={"type_donnees": "observations", "project_id": pid},
        files={"fichier": ("piege.xlsx", buf.read(),
                           "application/vnd.openxmlformats-officedocument."
                           "spreadsheetml.sheet")},
        headers=_h(brut),
    )
    assert r.status_code in (200, 400, 413, 422), r.text
    assert "traceback" not in r.text.lower()


# ---------------------------------------------------------------------------
# Confidentialité GPS / informateurs dans les exports publics
# ---------------------------------------------------------------------------

def test_export_public_masque_gps_et_identites(api, projet_cle, db_session):
    """L'export public ne divulgue ni GPS précis ni identités."""
    from webintel.science import models as m
    pid, brut = projet_cle
    plante = m.Plant(scientific_name="ADV GPS")
    db_session.add(plante)
    db_session.flush()
    ech = m.Echantillon(
        project_id=pid, plant_id=plante.id, code_echantillon="ADV-GPS-001",
        latitude=12.3712, longitude=-1.5197,
        niveau_confidentialite="restreint",
        collecteur="Informateur Secret")
    db_session.add(ech)
    db_session.commit()
    plant_id = plante.id
    r = api.get(
        f"/api/science/terrain/export-public/plantes/{plant_id}",
        headers=_h(brut))
    assert r.status_code == 200, r.text
    corps = r.text
    assert "12.3712" not in corps
    assert "-1.5197" not in corps
    assert "Informateur Secret" not in corps


# ---------------------------------------------------------------------------
# Redirections ouvertes
# ---------------------------------------------------------------------------

def test_login_sans_redirection_ouverte(api, projet_cle):
    """Le login n'accepte pas de cible de redirection externe."""
    pid, brut = projet_cle
    for cible in ("https://evil.example/", "//evil.example/"):
        r = api.post("/login", data={"api_key": brut, "next": cible},
                     follow_redirects=False)
        assert r.status_code in (200, 303, 422), r.text
        if r.status_code == 303:
            location = r.headers.get("location", "")
            assert "evil.example" not in location, location


# ---------------------------------------------------------------------------
# Divulgation d'erreurs
# ---------------------------------------------------------------------------

def test_erreurs_sans_traceback(api, projet_cle):
    """Les erreurs API ne divulguent ni traceback ni chemins internes."""
    pid, brut = projet_cle
    r = api.get("/api/route-qui-n-existe-pas-du-tout", headers=_h(brut))
    assert r.status_code == 404, r.text
    corps = r.text.lower()
    assert "traceback" not in corps
    assert "/home/" not in corps
    r = api.get("/api/science/terrain/echantillons/999999999",
                headers=_h(brut))
    assert r.status_code == 404, r.text
    assert "traceback" not in r.text.lower()
