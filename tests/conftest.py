"""Configuration pytest WebIntel (lot e — TRANSVERSAL).

ZÉRO réseau externe : les variables d'environnement sont fixées AVANT tout
import `webintel` (la base SQLite de test et le planificateur sont configurés
à l'import), et `socket.getaddrinfo` est neutralisé par une fixture autouse
(les hôtes non-IP se résolvent vers une IP publique fictive, les IP littérales
sont renvoyées telles quelles pour que les tests anti-SSRF restent valides).
"""

import ipaddress
import os
import socket
import tempfile

# --- Environnement : AVANT tout import webintel ---------------------------
_TEST_DIR = tempfile.mkdtemp(prefix="webintel-tests-")
# PostgreSQL optionnel (v1.1.1) : `WEBINTEL_TEST_DATABASE_URL=postgresql://…`
# fait tourner TOUTE la suite contre Postgres. Sinon, SQLite temporaire.
# (PostgreSQL indisponible ici -> la suite passe sur SQLite ; ce qui n'est
# pas validé contre Postgres est documenté dans docs/roadmap.md.)
_TEST_DATABASE_URL = os.environ.get("WEBINTEL_TEST_DATABASE_URL")
if _TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
else:
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TEST_DIR, "test.db")
os.environ["ENABLE_SCHEDULER"] = "false"
# Rate-limiting HTTP désactivé par défaut dans la suite (évite les faux
# positifs sur les tests qui enchaînent les requêtes) ; les tests dédiés
# (`tests/test_robustesse_rate_limit.py`) l'activent explicitement via
# monkeypatch sur l'objet settings.
os.environ["RATE_LIMIT_ENABLED"] = "false"
# Ne pas forcer : permet `REQUIRE_API_KEY=true pytest …` pour valider la
# suite complète en mode serveur (défaut local : ouvert).
os.environ.setdefault("REQUIRE_API_KEY", "false")
os.environ["EXPORT_DIR"] = os.path.join(_TEST_DIR, "exports")
os.environ["LOG_LEVEL"] = "WARNING"
# v2.4.0 : la suite construit ses fixtures sur base vide ; le seed de
# démarrage (données de démonstration intégrées) est désactivé ici, et
# testé séparément dans tests/test_seed_initial.py.
os.environ["WEBINTEL_SEED_INITIAL"] = "0"

import secrets  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import webintel.main  # noqa: E402
from webintel.core.utils import sha256_hex  # noqa: E402
from webintel.database import models  # noqa: E402,F401
from webintel.database.base import SessionLocal, init_db  # noqa: E402
from webintel.science import models as science_models  # noqa: E402,F401
from webintel.science import workflows as science_workflows  # noqa: E402,F401
from webintel.science import lab as science_lab  # noqa: E402,F401
from webintel.offline import models as offline_models  # noqa: E402,F401
from webintel.science.versions import StudyVersion  # noqa: E402,F401

init_db()

_REAL_GETADDRINFO = socket.getaddrinfo


def _fake_getaddrinfo(host, port, *args, **kwargs):
    """DNS fictif pour les tests (aucun réseau réel).

    - IP littérale (v4/v6) : renvoyée telle quelle, comme le ferait le vrai
      `getaddrinfo` (les tests anti-SSRF sur 127.0.0.1, ::1... restent valides).
    - nom d'hôte : résolu vers une IP publique fictive (93.184.216.34), ce qui
      fait passer `validate_url` sans aucun accès réseau.
    """
    try:
        ipaddress.ip_address(str(host))
        family = socket.AF_INET6 if ":" in str(host) else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (str(host), port or 0))]
    except ValueError:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0))
        ]


@pytest.fixture(autouse=True)
def _no_network_dns(monkeypatch):
    """Neutralise la résolution DNS réelle pour tous les tests."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Isolation du rate-limiting : compteurs remis à zéro avant chaque test.

    Le singleton `LimiteurFenetreGlissante` est partagé par tout le
    processus ; sans ce reset, un test activant le rate-limit polluerait
    les suivants (dépendance à l'ordre d'exécution).
    """
    from webintel.security.rate_limit import get_limiteur

    get_limiteur().reinitialiser()
    yield
    get_limiteur().reinitialiser()


# Ordre de suppression respectant les clés étrangères (enfants avant
# parents — tri topologique des FK ; vérifié contre `Base.metadata`).
# Sur SQLite les FK ne sont pas appliquées par défaut, mais PostgreSQL
# les fait respecter : toute table manquante ici provoque des
# `IntegrityError` au nettoyage entre les tests.
_TABLES_IN_DELETE_ORDER = [
    models.Alert,
    models.ApiKey,
    StudyVersion,
    science_models.CombinationStudy,
    science_models.CompoundTarget,
    science_models.ConnecteurCache,
    models.CrawlJob,
    science_models.Dataset,
    science_models.DockingJob,
    models.Event,
    models.Export,
    science_models.ImportProvenance,
    science_lab.LabNoteVersion,
    models.Observation,
    science_models.PlantActivity,
    science_models.PlantCompound,
    # v2.2 — littérature/connecteurs/qualité : mesures (enfant de
    # spécimens/composés/preuves/projets) avant spécimens (enfant de
    # plantes/preuves/composés).
    science_models.MesureExperimentale,
    science_models.Specimen,
    # v2.2 — extension terrain (enfants avant parents : pics -> analyses ->
    # fractions -> extraits -> extractions -> échantillons -> vouchers ;
    # usages / noms / pharmacognosie -> plantes).
    science_models.PicChromatographique,
    science_models.FractionCompound,
    science_models.AnalyseChimique,
    science_models.Fraction,
    science_models.Extrait,
    science_models.Extraction,
    science_models.Echantillon,
    science_models.UsageTraditionnel,
    science_models.NomVernaculaire,
    science_models.Pharmacognosie,
    science_models.Voucher,
    # v2.2 — chantier terrain : registre des langues (table `langues`,
    # réamorcée après chaque purge comme les connecteurs).
    science_models.Langue,
    science_models.Provenance,
    science_lab.PublicationRecord,
    science_models.ResearchObservation,
    science_models.Simulation,
    science_models.SourceConnecteur,
    models.SourceObservation,
    offline_models.ResolutionConflit,
    offline_models.FileSynchronisation,
    science_models.Study,
    science_models.SynergyAnalysis,
    science_workflows.WorkflowRun,
    science_models.Compound,
    models.Entity,
    science_models.Evidence,
    science_lab.LabNote,
    models.Monitor,
    models.PageSnapshot,
    models.WizardSession,
    offline_models.CollecteTerrain,
    science_models.Plant,
    models.Source,
    models.Project,
]


@pytest.fixture(autouse=True)
def _clean_db():
    """Base de test vide avant chaque test (isolation complète).

    Les lignes ``SourceConnecteur`` sont des DONNÉES DE RÉFÉRENCE
    (amorcées au démarrage par ``init_db``, jamais dans un GET — correctif
    P2-1) : elles sont réamorcées après chaque purge pour que le contrat
    ``GET /api/science/connecteurs`` reste stable. Idem pour le registre
    des ``Langue`` (5 langues du Burkina Faso, jamais de suppression).
    """
    session = SessionLocal()
    try:
        for model in _TABLES_IN_DELETE_ORDER:
            session.query(model).delete()
        session.commit()
        from webintel.science.connecteurs import registry as _registry
        if _registry.initialiser_connecteurs(session):
            session.commit()
        from webintel.database.base import _amorcer_langues
        _amorcer_langues()  # session propre, idempotent
    finally:
        session.close()
    yield


@pytest.fixture()
def db_session():
    """Session SQLAlchemy sur la base de test (tables créées)."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _server_mode() -> bool:
    """La suite tourne-t-elle avec `REQUIRE_API_KEY=true` ?"""
    return os.environ.get("REQUIRE_API_KEY") == "true"


@pytest.fixture()
def server_bootstrap(db_session):
    """En mode serveur : projet + clé API créés en DB (bootstrap).

    Retourne `{"project_id", "plain_key"}` ou `None` en mode local.
    """
    if not _server_mode():
        return None
    project = models.Project(name="Projet Test")
    db_session.add(project)
    db_session.flush()
    plain_key = secrets.token_urlsafe(32)
    db_session.add(models.ApiKey(
        project_id=project.id,
        name="clé-test",
        key_hash=sha256_hex(plain_key),
        prefix=plain_key[:8],
        quotas={},
        revoked=False,
    ))
    db_session.commit()
    return {"project_id": project.id, "plain_key": plain_key}


@pytest.fixture()
def client(server_bootstrap):
    """Client de test FastAPI (lifespan exécuté, planificateur désactivé).

    En mode serveur, le client est authentifié : en-tête `X-API-Key` +
    session UI (cookie signé via `/login`), tous deux rattachés au projet
    du bootstrap — comme un vrai client du mode SaaS.

    Le client utilise `https://testserver` en mode serveur : le cookie de
    session UI porte le flag `Secure` (chantier 0.3) et un client HTTP en
    clair ne le renverrait pas — comme en production, où ce mode exige
    HTTPS (reverse-proxy TLS). Même motif que la fixture `https_client`
    de `tests/test_ui_auth.py`.
    """
    headers = {}
    base_url = "http://testserver"
    if server_bootstrap is not None:
        headers["X-API-Key"] = server_bootstrap["plain_key"]
        base_url = "https://testserver"
    with TestClient(webintel.main.app, headers=headers,
                    base_url=base_url) as test_client:
        if server_bootstrap is not None:
            # Session UI : nécessaire pour les routes navigateur (`/`, …).
            resp = test_client.post(
                "/login", data={"api_key": server_bootstrap["plain_key"]}
            )
            assert resp.status_code in (200, 303), (
                f"login de test impossible en mode serveur : {resp.status_code}"
            )
        yield test_client


@pytest.fixture()
def anon_client():
    """Client sans authentification (ni clé API ni session UI).

    En mode serveur (`REQUIRE_API_KEY=true`), permet de tester le refus
    401/303 sans session ; en mode local, équivalent au client standard.
    """
    with TestClient(webintel.main.app) as test_client:
        yield test_client


@pytest.fixture()
def make_project(client, server_bootstrap):
    """Crée un projet et retourne son id.

    Mode local : via `POST /api/projects`. Mode serveur : la création de
    projet par API est interdite (403, bootstrap-only) — on réutilise le
    projet du bootstrap, seul accessible à la clé du client.
    """
    def _make(name: str) -> int:
        if server_bootstrap is not None:
            return server_bootstrap["project_id"]
        response = client.post("/api/projects", json={"name": name})
        assert response.status_code in (200, 201), response.text
        return response.json()["id"]
    return _make


@pytest.fixture()
def fixtures_dir():
    """Dossier des fixtures HTML locales."""
    from pathlib import Path

    return Path(__file__).parent / "fixtures"


@pytest.fixture()
def html_produit(fixtures_dir):
    return (fixtures_dir / "produit.html").read_text(encoding="utf-8")


@pytest.fixture()
def html_article(fixtures_dir):
    return (fixtures_dir / "article.html").read_text(encoding="utf-8")


@pytest.fixture()
def html_entreprise(fixtures_dir):
    return (fixtures_dir / "entreprise.html").read_text(encoding="utf-8")


@pytest.fixture()
def html_listing(fixtures_dir):
    return (fixtures_dir / "listing.html").read_text(encoding="utf-8")


@pytest.fixture()
def html_site_index(fixtures_dir):
    return (fixtures_dir / "site_index.html").read_text(encoding="utf-8")
