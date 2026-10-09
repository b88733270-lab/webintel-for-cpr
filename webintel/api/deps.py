"""Dépendances FastAPI : session DB, configuration, authentification par clé API."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from webintel.config.settings import Settings, get_settings as _get_app_settings
from webintel.core.logging import get_logger
from webintel.core.utils import sha256_hex
from webintel.database.base import SessionLocal, get_db as _get_db
from webintel.database.models import ApiKey, Project
from webintel.security.rate_limit import (
    cle_client,
    get_limiteur,
    ip_client_requete,
)

logger = get_logger(__name__)


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI : fournit une session SQLAlchemy, la referme ensuite.

    Réexporte `webintel.database.base.get_db`.
    """
    yield from _get_db()


def get_settings() -> Settings:
    """Dépendance FastAPI : accès à la configuration applicative."""
    return _get_app_settings()


def api_key_auth(request: Request) -> ApiKey | None:
    """Vérifie l'en-tête `X-API-Key` (contrat §10 d'architecture.md).

    - si `settings.REQUIRE_API_KEY` est False (défaut dev) : bypass avec log
      debug, retourne `None` (pas de clé associée) ;
    - sinon : sha256 de la clé fournie, recherche d'une clé non révoquée ;
      401 (message FR) si absente ou invalide.
    Retourne l'objet `ApiKey` trouvé, ou `None` en mode bypass.
    """
    settings = _get_app_settings()
    if not settings.REQUIRE_API_KEY:
        logger.debug("Authentification API désactivée (REQUIRE_API_KEY=false) : accès libre.")
        return None
    raw = request.headers.get("X-API-Key")
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Clé API manquante : fournissez l'en-tête X-API-Key.",
        )
    digest = sha256_hex(raw.strip())
    session = SessionLocal()
    try:
        key = (
            session.query(ApiKey)
            .filter(ApiKey.key_hash == digest, ApiKey.revoked.is_(False))
            .one_or_none()
        )
    finally:
        session.close()
    if key is None:
        raise HTTPException(
            status_code=401,
            detail="Clé API invalide ou révoquée.",
        )
    return key


def get_project_or_404(session: Session, project_id: int) -> Project:
    """Retourne le projet `project_id` ou lève une 404 (message FR)."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Projet introuvable : id={project_id}.",
        )
    return project


# ---------------------------------------------------------------------------
# Isolation multi-projet (P0-1)
# ---------------------------------------------------------------------------

def _forbidden(api_key: ApiKey, project_id: int) -> HTTPException:
    """403 : la clé n'est pas rattachée au projet demandé (message FR)."""
    return HTTPException(
        status_code=403,
        detail=(
            "Accès refusé : cette clé API est rattachée au projet "
            f"{api_key.project_id} et ne peut pas accéder au projet {project_id}."
        ),
    )


def require_project_access(api_key: ApiKey | None, project_id: int) -> None:
    """Vérifie que la clé API est rattachée au projet demandé (P0-1).

    - mode bypass (`REQUIRE_API_KEY=false`, `api_key=None`) : aucun contrôle
      (comportement v1 inchangé) ;
    - sinon : lève 403 si `api_key.project_id != project_id`.

    À appeler AVANT toute lecture/écriture liée au projet : la 403 prime sur
    la 404 pour ne pas révéler l'existence des projets étrangers.
    """
    if api_key is None:
        return
    if int(project_id) != int(api_key.project_id):
        raise _forbidden(api_key, project_id)


def require_project_access_strict(
    api_key: ApiKey | None, project_id: int | None
) -> None:
    """Contrôle d'accès SANS échappatoire pour les lectures (P0-1).

    Même contrat que ``require_project_access``, mais une donnée
    orpheline (``project_id=None`` — antérieure au durcissement) est
    REFUSÉE (403) en mode serveur au lieu d'être silencieusement
    ouverte : il n'existe plus aucun chemin « si project_id is None →
    pas de contrôle ». En mode bypass (local, single-user), aucun
    contrôle — comportement v1 inchangé.
    """
    if project_id is None:
        if api_key is not None:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Accès refusé : cette donnée n'est rattachée à aucun "
                    "projet (donnée antérieure au cloisonnement) et ne "
                    "peut pas être lue entre projets."
                ),
            )
        return
    require_project_access(api_key, project_id)


def scoped_project_id(api_key: ApiKey | None, project_id: int | None) -> int | None:
    """Résout le `project_id` effectif d'un endpoint de liste ou création (P0-1).

    - clé + `project_id` fourni mais différent -> 403 ;
    - clé + `project_id` absent -> celui de la clé (cloisonnement par défaut :
      une clé ne voit et ne crée que dans son projet) ;
    - bypass (`api_key=None`) -> `project_id` tel quel.
    """
    if api_key is None:
        return project_id
    if project_id is not None and int(project_id) != int(api_key.project_id):
        raise _forbidden(api_key, project_id)
    return int(api_key.project_id)


def projet_effectif_ou_404(
    db: Session, api_key: ApiKey | None, project_id: int | None
) -> int:
    """`project_id` effectif garanti existant (v2.1 : combinaisons/synergie/docking).

    - Applique ``scoped_project_id`` (403 vers un projet étranger en mode
      serveur) ;
    - en mode local sans ``project_id`` : projet « Défaut » (créé si
      absent — même motif que l'UI) ;
    - projet inexistant → 404 (évite une violation de clé étrangère en
      500 sur les modèles v2.1 où ``project_id`` est NOT NULL).
    """
    pid = scoped_project_id(api_key, project_id)
    if pid is None:
        projet = db.query(Project).order_by(Project.id).first()
        if projet is None:
            projet = Project(name="Défaut")
            db.add(projet)
            db.flush()
        return projet.id
    if db.get(Project, int(pid)) is None:
        raise HTTPException(
            status_code=404, detail=f"Projet introuvable : id={pid}."
        )
    return int(pid)


# ---------------------------------------------------------------------------
# Rate-limiting HTTP (chantier robustesse — P1-3 de l'audit backend)
# ---------------------------------------------------------------------------

def limite_debit(bucket: str):
    """Fabrique de dépendance FastAPI : rate-limit générique par (bucket, IP).

    Utilisation : ``dep: None = Depends(limite_debit("api_couteux"))`` sur
    les endpoints coûteux. Fenêtre glissante, 429 + message français +
    en-tête ``Retry-After`` en cas de dépassement. Désactivable via
    ``RATE_LIMIT_ENABLED=false`` (la suite de tests le désactive par
    défaut ; les tests dédiés l'activent explicitement).
    """

    def _verifier(request: Request) -> None:
        settings = _get_app_settings()
        if not settings.RATE_LIMIT_ENABLED:
            return
        limiteur = get_limiteur()
        cle = cle_client(bucket, ip_client_requete(request))
        decision = limiteur.enregistrer(
            cle, settings.API_LIMITE_REQUETES, float(settings.API_FENETRE_S)
        )
        if not decision.autorise:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Trop de requêtes : limite de "
                    f"{settings.API_LIMITE_REQUETES} requêtes par "
                    f"{settings.API_FENETRE_S} secondes dépassée. "
                    "Réessayez dans "
                    f"{max(1, int(decision.reessayer_dans_s))} secondes."
                ),
                headers={"Retry-After": str(max(1, int(decision.reessayer_dans_s)))},
            )

    return _verifier


# Dépendance prête à l'emploi pour les endpoints API coûteux.
limite_debit_couteux = limite_debit("api_couteux")
