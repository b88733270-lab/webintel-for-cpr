"""Tests de configuration serveur sécurisée (chantier 0.3).

- `validate_server_config()` refuse la SECRET_KEY par défaut, les clés
  trop courtes et les valeurs manifestement faibles en mode serveur ;
- le mode local reste sans friction (défaut accepté) ;
- le boot de l'application (`webintel.main`) échoue explicitement si la
  configuration serveur est invalide ;
- le cookie de session UI porte `Secure` en mode serveur uniquement.
"""

from __future__ import annotations

import importlib
import secrets

import pytest

import webintel.config.settings as _settings_mod
from webintel.config.settings import (
    DEFAULT_SECRET_KEY,
    MIN_SECRET_KEY_LENGTH,
    Settings,
    _normalize_secret,
    reset_settings,
    validate_server_config,
)


def _strong_key() -> str:
    return secrets.token_hex(32)  # 64 caractères hexadécimaux


def _server_settings(secret: str) -> Settings:
    s = Settings()
    setattr(s, "REQUIRE_API_KEY", True)
    s.SECRET_KEY = secret
    return s


def _local_settings(secret: str = DEFAULT_SECRET_KEY) -> Settings:
    s = Settings()
    setattr(s, "REQUIRE_API_KEY", False)
    s.SECRET_KEY = secret
    return s


# ---------------------------------------------------------------------------
# validate_server_config()
# ---------------------------------------------------------------------------

def test_serveur_refuse_cle_par_defaut():
    with pytest.raises(RuntimeError) as exc_info:
        validate_server_config(_server_settings(DEFAULT_SECRET_KEY))
    msg = str(exc_info.value)
    assert "SECRET_KEY" in msg
    assert "secrets.token_hex" in msg  # aide : comment générer une clé forte


@pytest.mark.parametrize("short", ["x", "trop-courte", "a" * 31])
def test_serveur_refuse_cle_trop_courte(short):
    with pytest.raises(RuntimeError) as exc_info:
        validate_server_config(_server_settings(short))
    assert str(MIN_SECRET_KEY_LENGTH) in str(exc_info.value)


@pytest.mark.parametrize("weak", [
    "secret", "SECRET", "Password", "change-me", "Change_Me", "admin",
    "123456", "qwerty", "letmein", "webintel", "WebIntel", "test",
    "default", "apikey", "api-key", "key",
])
def test_serveur_refuse_valeurs_faibles(weak):
    # Toutes ces valeurs sont rejetées (longueur insuffisante et/ou liste
    # des secrets faibles) : l'important est le refus explicite.
    with pytest.raises(RuntimeError):
        validate_server_config(_server_settings(weak))


def test_normalisation_secrets_faibles():
    # La comparaison à la liste faible ignore casse, tirets, underscores
    # et espaces.
    assert _normalize_secret(" SeCrEt ") == "secret"
    assert _normalize_secret("change-me") == "changeme"
    assert _normalize_secret("change_me") == "changeme"
    assert _normalize_secret("  API-KEY__") == "apikey"
    assert _normalize_secret(_strong_key()) not in _settings_mod._WEAK_SECRETS


def test_serveur_accepte_cle_forte():
    validate_server_config(_server_settings(_strong_key()))  # ne lève pas


def test_local_accepte_defaut_sans_friction():
    validate_server_config(_local_settings())  # ne lève pas


def test_local_accepte_n_importe_quelle_cle():
    s = _local_settings("x")
    validate_server_config(s)  # ne lève pas, même une clé ridicule


def test_reset_settings_reinitialise_le_singleton():
    first = _settings_mod.get_settings()
    try:
        reset_settings()
        second = _settings_mod.get_settings()
        assert first is not second
    finally:
        reset_settings()  # état propre pour la suite de la session


# ---------------------------------------------------------------------------
# Validation appelée au boot (webintel.main)
# ---------------------------------------------------------------------------

def _reload_main_with(require_api_key: bool, secret_key: str):
    """Recharge `webintel.main` avec un singleton settings modifié.

    `Settings` fige l'environnement à la définition de la classe : on mute
    donc le singleton directement (pattern déjà utilisé par test_ui_auth).
    Restaure l'objet `app` d'origine et vide le cache après rechargement,
    pour ne pas perturber les autres fichiers de tests.
    """
    import webintel.main as main_mod
    import webintel.config.settings as settings_mod

    old_app = main_mod.app
    settings = settings_mod.get_settings()
    setattr(settings, "REQUIRE_API_KEY", require_api_key)
    setattr(settings, "SECRET_KEY", secret_key)
    try:
        importlib.reload(main_mod)
    finally:
        main_mod.app = old_app
        settings_mod.reset_settings()


def test_boot_echoue_avec_cle_faible_en_mode_serveur():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _reload_main_with(True, DEFAULT_SECRET_KEY)


def test_boot_reussit_avec_cle_forte_en_mode_serveur():
    _reload_main_with(True, _strong_key())  # ne lève pas


def test_boot_reussit_en_mode_local_avec_defaut():
    _reload_main_with(False, DEFAULT_SECRET_KEY)  # ne lève pas



# ---------------------------------------------------------------------------
# Cookie de session : Secure en mode serveur uniquement
# ---------------------------------------------------------------------------

@pytest.fixture()
def server_mode():
    settings = Settings()
    setattr(settings, "REQUIRE_API_KEY", True)
    settings.SECRET_KEY = _strong_key()
    _settings_mod._settings = settings
    yield settings
    _settings_mod._settings = None


@pytest.fixture()
def local_mode():
    settings = Settings()
    setattr(settings, "REQUIRE_API_KEY", False)
    _settings_mod._settings = settings
    yield settings
    _settings_mod._settings = None


def _project_with_key(db_session, name: str):
    from webintel.core.utils import sha256_hex
    from webintel.database.models import ApiKey, Project

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


def test_cookie_secure_en_mode_serveur(client, server_mode, db_session):
    _, plain = _project_with_key(db_session, "P secure")
    response = client.post("/login", data={"api_key": plain},
                           follow_redirects=False)
    assert response.status_code == 303
    cookie_header = response.headers.get("set-cookie") or ""
    assert "Secure" in cookie_header
    assert "HttpOnly" in cookie_header


def test_cookie_sans_secure_en_mode_local(client, local_mode, db_session):
    # En mode local, /login est inutile : redirection sans poser de cookie.
    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 303
    cookie_header = response.headers.get("set-cookie") or ""
    assert "Secure" not in cookie_header
