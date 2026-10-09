"""Application FastAPI WebIntel : API REST + interface web.

`create_app()` assemble l'application :
- routers API sous le préfixe `/api` (OpenAPI auto, docs sur `/docs`) ;
- router UI en premier sur `/` ;
- fichiers statiques sur `/static` ;
- lifespan : `init_db()` au démarrage + `Scheduler().start()` si
  `settings.ENABLE_SCHEDULER` (arrêt propre à la fermeture) ;
- gestionnaires d'exceptions : `SecurityError` -> 422, `FetchError` -> 502,
  `NotFoundError` -> 404, `WebIntelError` -> 400 (JSON `{detail}`, messages FR).

`app = create_app()` au niveau module pour `uvicorn webintel.main:app`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from webintel import __version__
from webintel.api.routers import alerts, analyze, crawls, entities, events, exports, monitors, projects, science, science_combinaisons, science_connecteurs, science_docking, science_export, science_fiche, science_ia, science_import, science_jobs, science_lab, science_qualite, science_rapport, science_relations, science_search, science_simulations, science_synergie, science_terrain, science_workflows, search, sources, system, web_search
from webintel.api.routers.system import set_scheduler
from webintel.config.settings import get_settings, validate_server_config
from webintel.core.exceptions import FetchError, NotFoundError, SecurityError, WebIntelError
from webintel.core.logging import get_logger
from webintel.database.base import init_db, verifier_demarrage_postgres
from webintel.jobs.scheduler import Scheduler
from webintel.ui import routes as ui_routes
from webintel.ui import science_routes as ui_science_routes
from webintel.ui import science_combinaisons_routes as ui_science_comb_routes
from webintel.ui import chercheur_accueil_routes as ui_accueil_routes
from webintel.ui import chercheur_etudes_routes
from webintel.ui import chercheur_outils_routes as ui_outils_routes
from webintel.ui import chercheur_donnees_routes as ui_chercheur_donnees_routes
from webintel.ui import science_terrain_routes as ui_science_terrain_routes
from webintel.ui import offline_routes as ui_offline_routes
from webintel.ui import collections_routes as ui_collections_routes
from webintel.ui import web_intel_routes as ui_web_intel_routes

logger = get_logger(__name__)

#: Content-Security-Policy de l'application (chantier robustesse).
#:
#: - ``script-src 'self'`` STRICT : aucun JavaScript inline dans les
#:   templates (les comportements UI vivent dans ``ui/static/js/``) ;
#: - ``style-src 'self' 'unsafe-inline'`` : les attributs ``style="..."``
#:   restent utilisés dans les templates (risque faible, pas d'exécution) ;
#: - ``object-src 'none'`` (pas de plugins), ``frame-ancestors 'none'``
#:   (anti-clickjacking, renforcé par ``X-Frame-Options: DENY``),
#:   ``form-action 'self'`` (les formulaires ne quittent jamais l'instance),
#:   ``base-uri 'self'`` (anti-injection de <base>).
CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

#: En-têtes de sécurité positionnés sur chaque réponse (chantier robustesse).
EN_TETES_SECURITE = {
    "Content-Security-Policy": CSP_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Démarrage : init DB + planificateur ; arrêt : arrêt propre."""
    settings = get_settings()
    if settings.DATABASE_URL.startswith("postgresql"):
        # Fail-fast : PostgreSQL injoignable ou schéma incomplet ->
        # RuntimeError explicite, jamais de démarrage silencieux dégradé.
        verifier_demarrage_postgres()
    else:
        init_db()
    scheduler: Scheduler | None = None
    if settings.ENABLE_SCHEDULER:
        scheduler = Scheduler(interval_s=settings.SCHEDULER_INTERVAL_S)
        scheduler.start()
        set_scheduler(scheduler)
        logger.info("Planificateur activé (intervalle %ss).", settings.SCHEDULER_INTERVAL_S)
    else:
        set_scheduler(None)
        logger.info("Planificateur désactivé (ENABLE_SCHEDULER=false).")
    yield
    if scheduler is not None:
        scheduler.stop()
    set_scheduler(None)


def create_app() -> FastAPI:
    """Fabrique l'application FastAPI WebIntel."""
    settings = get_settings()
    # Garde-fou au démarrage : en mode serveur, une SECRET_KEY faible ou par
    # défaut fait ÉCHOUER le boot (RuntimeError) plutôt que de servir une
    # instance non sécurisée. En mode local, aucun contrôle (zéro friction).
    validate_server_config(settings)

    app = FastAPI(
        title="WebIntel",
        version=__version__,
        description="WebIntel — Plateforme d'intelligence scientifique pour les "
                    "plantes médicinales, la pharmacognosie et les produits "
                    "naturels. Collecte web, extraction, surveillance — et "
                    "fiches scientifiques traçables.",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def middleware_en_tetes_securite(request: Request, call_next):
        """Positionne les en-têtes de sécurité sur chaque réponse.

        - ``Strict-Transport-Security`` : UNIQUEMENT en mode serveur
          (``REQUIRE_API_KEY=true`` ⇒ déploiement HTTPS attendu) ; jamais
          en mode local (HTTP sur localhost, le HSTS y bloquerait l'accès).
          Choix revu (chantier robustesse) : on NE dépend PAS du schéma
          de la requête (`request.url.scheme`), car le déploiement serveur
          documenté est DERIÈRE un reverse-proxy TLS — le schéma vu par
          l'app serait alors `http` et le HSTS ne serait jamais envoyé.
          Envoyer HSTS sur une réponse HTTP claire est sans effet (les
          navigateurs l'ignorent, RFC 6797) : le signal `REQUIRE_API_KEY`
          (« mode serveur = proxy TLS exigé ») est donc le bon déclencheur
          disponible. `X-Forwarded-Proto` n'est pas pris en compte : sans
          `ProxyHeadersMiddleware` configuré il serait falsifiable, et il
          n'apporterait rien (HSTS sur HTTP = ignoré de toute façon).
        - Les réponses ``StreamingResponse`` (PDF, ZIP, exports) reçoivent
          les mêmes en-têtes.
        """
        reponse = await call_next(request)
        for nom, valeur in EN_TETES_SECURITE.items():
            reponse.headers[nom] = valeur
        if get_settings().REQUIRE_API_KEY:
            reponse.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return reponse

    # UI en premier sur "/" (pas de préfixe).
    app.include_router(ui_routes.router)
    app.include_router(ui_science_routes.router)
    # NOTE : ui_chercheur_donnees_routes AVANT ui_science_comb_routes — sinon
    # la route GET /synergie/{analyse_id} (paramètre) intercepte
    # /synergie/nouvelle (statique) et répond 422 au lieu de laisser passer.
    app.include_router(ui_chercheur_donnees_routes.router)
    app.include_router(ui_science_comb_routes.router)
    app.include_router(ui_accueil_routes.router)
    app.include_router(chercheur_etudes_routes.router)
    app.include_router(ui_outils_routes.router)
    # v2.2 : UI données terrain (formulaires HTML simples par plante).
    app.include_router(ui_science_terrain_routes.router)
    app.include_router(ui_offline_routes.router)
    app.include_router(ui_collections_routes.router)
    app.include_router(ui_web_intel_routes.router)

    # API REST sous /api.
    app.include_router(system.router, prefix="/api", tags=["système"])
    app.include_router(projects.router, prefix="/api", tags=["projets"])
    app.include_router(analyze.router, prefix="/api", tags=["analyse"])
    app.include_router(crawls.router, prefix="/api", tags=["crawls"])
    app.include_router(entities.router, prefix="/api", tags=["entités"])
    app.include_router(monitors.router, prefix="/api", tags=["moniteurs"])
    app.include_router(events.router, prefix="/api", tags=["événements"])
    app.include_router(alerts.router, prefix="/api", tags=["alertes"])
    app.include_router(search.router, prefix="/api", tags=["recherche"])
    app.include_router(web_search.router, prefix="/api", tags=["recherche-web"])
    app.include_router(sources.router, prefix="/api", tags=["sources"])
    app.include_router(exports.router, prefix="/api", tags=["exports"])
    # NOTE : science_simulations AVANT science — sinon la route
    # GET /science/simulations/{simulation_id} (paramètre int) intercepte
    # /science/simulations/moteurs et répond 422 au lieu de laisser passer.
    app.include_router(science_simulations.router, prefix="/api", tags=["science"])
    app.include_router(science.router, prefix="/api", tags=["science"])
    app.include_router(science_fiche.router, prefix="/api", tags=["science"])
    app.include_router(science_search.router, prefix="/api", tags=["science"])
    app.include_router(science_lab.router, prefix="/api", tags=["science"])
    app.include_router(science_workflows.router, prefix="/api", tags=["science"])
    # v2.1 : modèle scientifique enrichi (études, cibles, activités,
    # preuves pointées, provenance, jeux de données).
    app.include_router(science_relations.router, prefix="/api", tags=["science"])
    # v2.2 : extension terrain (noms vernaculaires, ethnobotanique,
    # échantillons/vouchers, chaîne extraction→fraction, analyses
    # phytochimiques, pharmacognosie, statuts épistémiques, recherche
    # multi-motifs, export public).
    app.include_router(science_terrain.router, prefix="/api", tags=["science"])
    # NOTE : les routeurs import/export portent déjà leur préfixe complet
    # ("/api/science/import", "/api/science/export") — ne pas re-préfixer.
    app.include_router(science_import.router, tags=["science"])
    app.include_router(science_export.router, tags=["science"])
    app.include_router(
        science_connecteurs.router, prefix="/api/science/connecteurs", tags=["science"]
    )
    # Phase 11 : le routeur IA porte déjà son préfixe complet
    # ("/api/science/ia") — ne pas re-préfixer. Quand l'IA est désactivée,
    # ses routes répondent 503 explicite et l'UI n'affiche plus la fonction.
    app.include_router(science_ia.router, tags=["science"])
    # Phase v2.1 : les routeurs combinaisons / synergie / docking /
    # rapport portent déjà leur préfixe complet ("/api/science/...") —
    # ne pas re-préfixer (même motif que science_import/export/ia).
    app.include_router(science_combinaisons.router, tags=["science"])
    app.include_router(science_synergie.router, tags=["science"])
    app.include_router(science_docking.router, tags=["science"])
    # v2.2 (chantier robustesse) : jobs asynchrones pour les calculs
    # lourds (le routeur porte déjà son préfixe "/api/science/jobs").
    app.include_router(science_jobs.router, tags=["science"])
    app.include_router(science_rapport.router, tags=["science"])
    # v2.2 (littérature/connecteurs/qualité) : contrôle qualité des données
    # (lecture seule — aucune correction automatique).
    app.include_router(science_qualite.router, prefix="/api", tags=["science"])

    # Fichiers statiques de l'UI.
    static_dir = Path(__file__).parent / "ui" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Gestionnaires d'exceptions métier -> JSON FR.
    app.add_exception_handler(SecurityError, _handler(422))
    app.add_exception_handler(FetchError, _handler(502))
    app.add_exception_handler(NotFoundError, _handler(404))
    app.add_exception_handler(WebIntelError, _handler(400))
    return app


def _handler(status_code: int):
    async def _handle(_request: Request, exc: WebIntelError) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return _handle


app = create_app()
