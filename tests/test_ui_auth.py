"""Tests d'authentification de l'interface web (v1.1.1 — P0 UI).

Deux modes :
- mode local (`REQUIRE_API_KEY=false`) : UI ouverte, `/login` inutile ;
- mode serveur (`REQUIRE_API_KEY=true`) : sans session -> 303 vers `/login` ;
  `/login` vérifie la clé API contre le store (sha256) puis pose un cookie
  de session signé (HttpOnly, SameSite=Lax) ; toutes les routes UI exigent
  la session et restent limitées au projet de la clé.
"""

import secrets

import pytest

import webintel.config.settings as _settings_mod
from webintel.core.utils import sha256_hex
from webintel.database.models import ApiKey, Entity, Project
from webintel.ui.auth import (
    UI_SESSION_COOKIE,
    sign_session,
    verify_session,
)


@pytest.fixture()
def server_mode():
    """Active `REQUIRE_API_KEY=true` sur le singleton de settings (restauré après)."""
    settings = _settings_mod.Settings()
    settings.REQUIRE_API_KEY = True
    _settings_mod._settings = settings
    yield settings
    _settings_mod._settings = None


@pytest.fixture()
def local_mode():
    """Force `REQUIRE_API_KEY=false` (restauré après)."""
    settings = _settings_mod.Settings()
    settings.REQUIRE_API_KEY = False
    _settings_mod._settings = settings
    yield settings
    _settings_mod._settings = None
@pytest.fixture()
def https_client():
    """Client de test simulant un accès HTTPS.

    En mode serveur, le cookie de session UI porte le flag `Secure`
    (chantier 0.3) : un client HTTP en clair ne le renvoie pas — comme en
    production, où ce mode exige HTTPS (reverse-proxy TLS). Les tests qui
    vérifient un aller-retour de session authentifiée utilisent ce client.
    """
    from fastapi.testclient import TestClient

    import webintel.main

    with TestClient(webintel.main.app, base_url="https://testserver") as client:
        yield client


def _project_with_key(db_session, name: str):
    project = Project(name=name)
    db_session.add(project)
    db_session.flush()
    plain = secrets.token_urlsafe(32)
    db_session.add(ApiKey(
        project_id=project.id,
        name=f"clé-{name}",
        key_hash=sha256_hex(plain),
        prefix=plain[:8],
        quotas={},
        revoked=False,
    ))
    db_session.commit()
    return project, plain


def _login(client, plain_key: str):
    """POST /login sans suivre la redirection (pour inspecter cookie + 303)."""
    return client.post("/login", data={"api_key": plain_key},
                       follow_redirects=False)


# ---------------------------------------------------------------------------
# Mode local : UI ouverte
# ---------------------------------------------------------------------------

def test_ui_ouverte_en_mode_local(client, local_mode):
    assert client.get("/").status_code == 200
    assert client.get("/entites").status_code == 200
    # /login est inutile en mode local : redirection vers l'accueil.
    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


# ---------------------------------------------------------------------------
# Mode serveur : refus sans session
# ---------------------------------------------------------------------------

def test_ui_redirige_vers_login_sans_session(anon_client, server_mode):
    for path in ["/", "/analyser", "/crawls", "/entites", "/moniteurs",
                 "/alertes", "/evenements", "/recherche", "/exports"]:
        response = anon_client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login", path


def test_login_cle_invalide(client, server_mode, db_session):
    _project_with_key(db_session, "P login")
    response = _login(client, "cle-qui-nexiste-pas")
    assert response.status_code == 200  # formulaire réaffiché avec erreur
    assert "invalide" in response.text
    assert UI_SESSION_COOKIE not in (response.headers.get("set-cookie") or "")


def test_login_ok_puis_acces_protege(https_client, server_mode, db_session):
    project, plain = _project_with_key(db_session, "P session")
    response = _login(https_client, plain)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    set_cookie = response.headers.get("set-cookie") or ""
    assert UI_SESSION_COOKIE in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie or "samesite=lax" in set_cookie.lower()
    # La clé en clair ne doit JAMAIS apparaître dans le cookie.
    assert plain not in set_cookie

    # Le client conserve le cookie : l'UI est désormais accessible...
    assert https_client.get("/").status_code == 200
    assert https_client.get("/entites").status_code == 200
    # ...et limitée au projet de la clé.
    assert project.name in https_client.get("/").text


def test_session_falsifiee_rejetee(client, server_mode, db_session):
    project, plain = _project_with_key(db_session, "P falsif")
    response = _login(client, plain)
    assert response.status_code == 303
    # Falsification du cookie : signature invalide -> refus.
    client.cookies.set(UI_SESSION_COOKIE, "payload-falsifie.signature-bidon")
    denied = client.get("/", follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"] == "/login"


def test_session_expiree_rejetee(client, server_mode, db_session):
    from webintel.config.settings import get_settings

    project, plain = _project_with_key(db_session, "P expire")
    key = (
        db_session.query(ApiKey)
        .filter(ApiKey.key_hash == sha256_hex(plain))
        .one()
    )
    expired = sign_session(key.id, project.id, get_settings().SECRET_KEY, ttl_s=-10)
    assert verify_session(expired, get_settings().SECRET_KEY) is None
    client.cookies.set(UI_SESSION_COOKIE, expired)
    denied = client.get("/", follow_redirects=False)
    assert denied.status_code == 303


def test_cle_revoquee_invalide_la_session(https_client, server_mode, db_session):
    _project, plain = _project_with_key(db_session, "P revoque")
    assert _login(https_client, plain).status_code == 303
    assert https_client.get("/").status_code == 200
    # Révocation de la clé : la session existante ne passe plus.
    key = (
        db_session.query(ApiKey)
        .filter(ApiKey.key_hash == sha256_hex(plain))
        .one()
    )
    key.revoked = True
    db_session.commit()
    denied = https_client.get("/", follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"] == "/login"


def test_logout_purge_la_session(https_client, server_mode, db_session):
    _project, plain = _project_with_key(db_session, "P logout")
    assert _login(https_client, plain).status_code == 303
    assert https_client.get("/").status_code == 200
    response = https_client.get("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert https_client.get("/", follow_redirects=False).status_code == 303


# ---------------------------------------------------------------------------
# Isolation projet de l'UI (même règle que l'API)
# ---------------------------------------------------------------------------

def test_ui_isolee_au_projet_de_la_cle(https_client, server_mode, db_session):
    p1, k1 = _project_with_key(db_session, "P1 UI")
    p2, k2 = _project_with_key(db_session, "P2 UI")
    other = Entity(
        project_id=p2.id, entity_key="product:xyz", entity_type="product",
        name="Produit du projet 2", data={"name": "Produit du projet 2"},
        sources=["https://autre.example.com/x"],
    )
    db_session.add(other)
    db_session.commit()

    assert _login(https_client, k1).status_code == 303
    # L'entité du projet 2 n'est ni visible ni détaillée depuis la session P1.
    listing = https_client.get("/entites")
    assert listing.status_code == 200
    assert "Produit du projet 2" not in listing.text
    detail = https_client.get(f"/entites/{other.id}")
    assert detail.status_code == 200
    assert "introuvable" in detail.text
    assert "Produit du projet 2" not in detail.text
