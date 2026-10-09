"""Authentification de l'interface web (v1.1.1 — nouveau P0 de l'audit).

Constat : les routes UI accédaient directement à la base sans aucun contrôle
alors que l'API exige une clé en mode serveur (`REQUIRE_API_KEY=true`).

Deux modes, pilotés par `REQUIRE_API_KEY` :
- mode local / single-user (`false`, défaut dev) : UI ouverte, aucun login ;
- mode serveur / SaaS (`true`) : `/login` — la clé API saisie dans le
  formulaire est vérifiée contre le store (comparaison de son sha256, la clé
  en clair n'est ni loggée, ni persistée, ni réaffichée), puis un cookie de
  session SIGNÉ est posé.

Cookie de session :
- contenu : `base64url(json({kid, pid, exp}))` + `.` + `hmac-sha256` calculé
  avec `settings.SECRET_KEY` — infalsifiable sans le secret ;
- attributs : `HttpOnly`, `SameSite=Lax`, `Path=/`, durée de vie 24 h ;
- `Secure` est ajouté en mode serveur (HTTPS obligatoire derrière le
  reverse-proxy) et volontairement omis en mode local (HTTP sur localhost).
- à chaque requête UI, la signature et l'expiration sont vérifiées, puis la
  clé est relue en base (révoquée ? projet concordant ?).

Toutes les routes UI utilisent la dépendance `ui_auth` : en mode serveur
sans session valide, redirection 303 vers `/login`. L'UI est limitée au
projet de la clé (même `require_project_access` que l'API).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from webintel.api.deps import get_db
from webintel.config.settings import get_settings
from webintel.core.logging import get_logger
from webintel.core.utils import sha256_hex
from webintel.database.models import ApiKey

logger = get_logger(__name__)

UI_SESSION_COOKIE = "webintel_ui_session"
SESSION_TTL_S = 24 * 3600  # 24 h


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_session(key_id: int, project_id: int, secret: str,
                 ttl_s: int = SESSION_TTL_S) -> str:
    """Signe une session UI : `payload_b64.signature` (HMAC-SHA256)."""
    payload = {
        "kid": int(key_id),
        "pid": int(project_id),
        "exp": int(time.time()) + int(ttl_s),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"),
                   hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_session(token: str, secret: str) -> dict | None:
    """Vérifie un cookie de session : signature + expiration. `None` si invalide."""
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return {"key_id": int(payload["kid"]), "project_id": int(payload["pid"])}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def get_session_claims(request: Request) -> dict | None:
    """Lit et vérifie le cookie de session (None si absent/invalide)."""
    token = request.cookies.get(UI_SESSION_COOKIE)
    if not token:
        return None
    return verify_session(token, get_settings().SECRET_KEY)


def authenticate_ui_key(db: Session, plain_key: str) -> ApiKey | None:
    """Valide une clé API saisie au login (sha256, non révoquée).

    La clé en clair n'est ni loggée ni persistée : seul son sha256 est
    comparé. Retourne la ligne `ApiKey` ou `None`.
    """
    raw = (plain_key or "").strip()
    if not raw:
        return None
    digest = sha256_hex(raw)
    return (
        db.query(ApiKey)
        .filter(ApiKey.key_hash == digest, ApiKey.revoked.is_(False))
        .one_or_none()
    )


def ui_auth(request: Request, db: Session = Depends(get_db)) -> ApiKey | None:
    """Dépendance FastAPI : clé API de la session UI (mode serveur) ou None.

    - mode local (`REQUIRE_API_KEY=false`) : retourne `None`, UI ouverte ;
    - mode serveur : session valide -> la ligne `ApiKey` (clé relue en base,
      révoquée ? projet concordant ?) ; sinon redirection 303 vers `/login`.
    """
    settings = get_settings()
    if not settings.REQUIRE_API_KEY:
        return None
    claims = get_session_claims(request)
    if claims is not None:
        key = db.get(ApiKey, claims["key_id"])
        if (
            key is not None
            and not key.revoked
            and key.project_id == claims["project_id"]
        ):
            return key
        logger.info("Session UI invalide/révoquée (kid=%s).", claims.get("key_id"))
    # Pas de session valide : redirection 303 vers /login. On lève une
    # HTTPException avec en-tête Location (une dépendance ne peut pas
    # retourner directement une réponse).
    raise HTTPException(status_code=303, headers={"location": "/login"})


def api_key_or_ui_session(
    request: Request, db: Session = Depends(get_db)
) -> ApiKey | None:
    """Clé API via en-tête `X-API-Key` OU session UI (cookie signé).

    Réservé aux endpoints API consommés par le navigateur en mode serveur
    (ex. `GET /api/exports/{id}/download` depuis l'UI, où le navigateur ne
    peut pas poser `X-API-Key`). Tous les autres endpoints API restent
    strictement sur en-tête (`api_key_auth`).
    """
    from webintel.api.deps import api_key_auth  # local : évite tout cycle

    try:
        key = api_key_auth(request)
    except HTTPException:
        key = None
    if key is not None:
        return key
    settings = get_settings()
    if not settings.REQUIRE_API_KEY:
        return None  # mode local : ouvert
    claims = get_session_claims(request)
    if claims is None:
        raise HTTPException(
            status_code=401,
            detail="Authentification requise : en-tête X-API-Key ou session UI.",
        )
    db_key = db.get(ApiKey, claims["key_id"])
    if (
        db_key is None
        or db_key.revoked
        or db_key.project_id != claims["project_id"]
    ):
        raise HTTPException(status_code=401, detail="Session UI invalide ou révoquée.")
    return db_key
