"""Tests Phase 0 — configuration serveur (chantier 0.4).

Couvre `webintel.config.settings.validate_server_config()` (contrat livré
par le chantier 0.3) et les flags du cookie de session posé par l'UI en
mode serveur (`POST /login`).

DÉPENDANCE chantier 0.3 : `validate_server_config(settings)` lève
`RuntimeError` (message mentionnant `SECRET_KEY`) quand
`REQUIRE_API_KEY=true` et que `SECRET_KEY` vaut la valeur par défaut, est
trop courte (< 32 caractères) ou figure dans la liste des secrets faibles.
Si la fonction n'est pas importable (non livrée, ou fichier en cours
d'édition), les tests concernés sont SKIPPÉS avec un motif explicite.

Cookie de session UI : en mode serveur, `POST /login` pose
`webintel_ui_session` avec `HttpOnly`, `SameSite=Lax` ET `Secure`
(`secure=settings.REQUIRE_API_KEY` dans `login_submit`, chantier 0.3).
Note : un cookie `Secure` n'est pas renvoyé par un client HTTP en clair,
donc les tests inspectent l'en-tête `Set-Cookie` de la réponse 303
(`follow_redirects=False`) plutôt que de rejouer une requête authentifiée.
"""

from __future__ import annotations

import secrets

import pytest

try:
    from webintel.config import settings as _settings_mod
    from webintel.config.settings import Settings, validate_server_config

    _VALIDATE_DISPONIBLE = True
except Exception:  # chantier 0.3 : non livré ou fichier en cours d'édition
    _settings_mod = None  # type: ignore[assignment]
    Settings = None  # type: ignore[assignment]
    validate_server_config = None  # type: ignore[assignment]
    _VALIDATE_DISPONIBLE = False

_REQUIERT_VALIDATE = pytest.mark.skipif(
    not _VALIDATE_DISPONIBLE,
    reason="chantier 0.3 : webintel.config.settings.validate_server_config "
    "non importable (non livré ou fichier en cours d'édition)",
)

from webintel.core.utils import sha256_hex  # noqa: E402
from webintel.database.models import ApiKey, Project  # noqa: E402
from webintel.ui.auth import UI_SESSION_COOKIE  # noqa: E402


def _settings_for(secret: str, server: bool) -> "Settings":
    """Construit un `Settings` isolé (sans toucher au singleton global)."""
    return Settings(SECRET_KEY=secret, REQUIRE_API_KEY=server)


# ---------------------------------------------------------------------------
# validate_server_config (contrat chantier 0.3)
# ---------------------------------------------------------------------------


@_REQUIERT_VALIDATE
def test_serveur_refuse_cle_par_defaut():
    """`REQUIRE_API_KEY=true` + `SECRET_KEY` par défaut -> erreur explicite."""
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        validate_server_config(_settings_for("dev-secret-change-me", server=True))


@_REQUIERT_VALIDATE
@pytest.mark.parametrize(
    "faible",
    [
        "secret",
        "password",
        "changeme",
        "123456",
        "a" * 31,  # 31 caractères : sous le minimum de 32
        "motdepasse",
        "qwerty",
        "admin",
        "webintel",
    ],
)
def test_serveur_refuse_cles_faibles(faible):
    """Secrets de la liste noire ou trop courts refusés en mode serveur."""
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        validate_server_config(_settings_for(faible, server=True))


@_REQUIERT_VALIDATE
def test_serveur_accepte_cle_forte():
    """Une clé de 256 bits aléatoires passe la validation serveur."""
    validate_server_config(_settings_for(secrets.token_hex(32), server=True))


@_REQUIERT_VALIDATE
def test_mode_local_accepte_cle_par_defaut():
    """Sans `REQUIRE_API_KEY`, aucune exigence : le mode dev reste simple."""
    validate_server_config(_settings_for("dev-secret-change-me", server=False))


@_REQUIERT_VALIDATE
def test_mode_local_sans_exigence_de_force():
    """En mode local, même une clé faible est acceptée."""
    validate_server_config(_settings_for("secret", server=False))


# ---------------------------------------------------------------------------
# Cookie de session UI (mode serveur simulé, sans toucher l'environnement)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _mode_serveur():
    """Active `REQUIRE_API_KEY=true` sur le singleton (puis restaure).

    Même pattern que `key_mode` dans `test_project_isolation.py` : on force
    l'attribut sur l'instance plutôt que la variable d'environnement, car
    `Settings` lit l'environnement à la construction.
    """
    if _settings_mod is None:  # pragma: no cover - module non importable
        pytest.skip("webintel.config.settings non importable (chantier 0.3)")
    settings = Settings()
    settings.REQUIRE_API_KEY = True
    _settings_mod._settings = settings
    yield
    _settings_mod._settings = None


@pytest.fixture()
def _cle_serveur(db_session):
    """Projet + clé API créés directement en DB (bootstrap, sans API)."""
    project = Project(name="Projet Cookie")
    db_session.add(project)
    db_session.flush()
    plain = secrets.token_urlsafe(32)
    db_session.add(
        ApiKey(
            project_id=project.id,
            name="clé-cookie",
            key_hash=sha256_hex(plain),
            prefix=plain[:8],
            quotas={},
            revoked=False,
        )
    )
    db_session.commit()
    return plain


def test_cookie_session_flags_securises(anon_client, _mode_serveur, _cle_serveur):
    """En mode serveur, le cookie de session UI est `Secure` + `HttpOnly`
    + `SameSite=Lax` (contrat livré par le chantier 0.3)."""
    resp = anon_client.post("/login", data={"api_key": _cle_serveur},
                            follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    set_cookie = resp.headers.get("set-cookie", "")
    assert UI_SESSION_COOKIE in set_cookie, set_cookie
    lowered = set_cookie.lower()
    assert "httponly" in lowered, set_cookie
    assert "samesite=lax" in lowered.replace(" ", ""), set_cookie
    assert "secure" in lowered, set_cookie


def test_cookie_session_ne_fuit_pas_la_cle(anon_client, _mode_serveur, _cle_serveur):
    """La clé API en clair n'apparaît jamais dans le cookie de session."""
    resp = anon_client.post("/login", data={"api_key": _cle_serveur},
                            follow_redirects=False)
    assert resp.status_code == 303
    set_cookie = resp.headers.get("set-cookie", "")
    assert _cle_serveur not in set_cookie


def test_login_cle_invalide_sans_cookie(anon_client, _mode_serveur, _cle_serveur):
    """Clé invalide en mode serveur : formulaire réaffiché, pas de cookie."""
    resp = anon_client.post("/login", data={"api_key": "cle-qui-nexiste-pas"},
                            follow_redirects=False)
    assert resp.status_code == 200
    assert UI_SESSION_COOKIE not in (resp.headers.get("set-cookie") or "")


def test_login_mode_local_sans_cookie():
    """En mode local, `/login` redirige sans poser de cookie de session.

    Reconstruit une app en mode local même si la suite tourne avec
    `REQUIRE_API_KEY=true` (le singleton `Settings` fige l'environnement à
    la construction : on le remplace puis on recharge `webintel.main`).
    """
    import importlib

    from fastapi.testclient import TestClient

    import webintel.main as main_mod

    if _settings_mod is None:  # pragma: no cover - module non importable
        pytest.skip("webintel.config.settings non importable (chantier 0.3)")
    ancien = _settings_mod._settings
    _settings_mod._settings = Settings(
        SECRET_KEY="dev-secret-change-me", REQUIRE_API_KEY=False
    )
    try:
        importlib.reload(main_mod)  # create_app() -> validation mode local OK
        with TestClient(main_mod.app) as client_local:
            resp = client_local.get("/login", follow_redirects=False)
            assert resp.status_code == 303
            assert resp.headers["location"] == "/"
            assert UI_SESSION_COOKIE not in (resp.headers.get("set-cookie") or "")
    finally:
        _settings_mod._settings = ancien
        importlib.reload(main_mod)  # restaure l'app du mode de la suite
