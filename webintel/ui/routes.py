"""Routes de l'interface web WebIntel (Jinja2, 100 % français).

Authentification (v1.1.1 — nouveau P0 de l'audit) :
- mode local / single-user (`REQUIRE_API_KEY=false`, défaut dev) : UI
  ouverte, projet par défaut (le premier existant, sinon « Défaut » créé) ;
- mode serveur / SaaS (`REQUIRE_API_KEY=true`) : `/login` (clé API vérifiée
  contre le store, cookie de session signé HttpOnly + SameSite=Lax), TOUTES
  les routes exigent la session (`ui_auth`), et l'UI est limitée au projet de
  la clé (même isolation que l'API : `require_project_access`).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from webintel.analytics.kpis import get_dashboard_kpis
from webintel.api import pipeline
from webintel.api.deps import get_db
from webintel.config.settings import get_settings
from webintel.core.enums import JobStatus
from webintel.core.exceptions import FetchError, SecurityError
from webintel.core.logging import get_logger
from webintel.database.models import (
    Alert,
    ApiKey,
    CrawlJob,
    Entity,
    Event,
    Export,
    Monitor,
    Observation,
    Project,
    Source,
)
from webintel.entities.registry import get_observations_by_source
from webintel.exports.writers import build_export
from webintel.jobs.manager import JobContext, get_manager
from webintel.monitoring.service import run_monitor
from webintel.search.engine import search_entities
from webintel.security.rate_limit import (
    cle_client,
    get_limiteur,
    ip_client_requete,
)
from webintel.ui.auth import (
    SESSION_TTL_S,
    UI_SESSION_COOKIE,
    authenticate_ui_key,
    sign_session,
    ui_auth,
)

router = APIRouter()

logger = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _outils_scientifiques() -> dict:
    """État des outils scientifiques optionnels (ne lève jamais)."""
    try:
        from webintel.science import chimie

        rdkit = chimie.infos_biblio()
    except Exception:  # noqa: BLE001 — la page Paramètres doit toujours s'afficher
        rdkit = {
            "bibliotheque": "RDKit",
            "version": None,
            "disponible": False,
            "raison": "État illisible.",
        }
    try:
        from webintel.simulations.plugins import docking_adapter

        vina = docking_adapter.diagnostic_vina()
        meeko_disponible = docking_adapter.meeko_disponible()
    except Exception:  # noqa: BLE001 — idem
        vina = {
            "binaire": None,
            "version": None,
            "disponible": False,
            "raison": "État illisible.",
        }
        meeko_disponible = False
    try:
        import importlib.metadata

        meeko_version = importlib.metadata.version("meeko")
    except Exception:  # noqa: BLE001 — paquet absent ou illisible
        meeko_version = None
    meeko = {
        "bibliotheque": "meeko",
        "version": meeko_version,
        "disponible": bool(meeko_disponible),
        "raison": (
            ""
            if meeko_disponible
            else "Préparation SMILES → PDBQT indisponible : `pip install meeko` "
                 "(ou ./scripts/install_science.sh). En attendant, fournissez "
                 "un ligand PDBQT déjà préparé."
        ),
    }
    return {"rdkit": rdkit, "meeko": meeko, "vina": vina}


def _render(
    name: str, ui_key: ApiKey | None = None, status_code: int = 200, **context
) -> HTMLResponse:
    """Rend un template en injectant le contexte d'authentification UI."""
    settings = get_settings()
    context.setdefault("server_mode", bool(settings.REQUIRE_API_KEY))
    context.setdefault("logged_in", ui_key is not None or not settings.REQUIRE_API_KEY)
    # Bandeau « mode hors-ligne » global (dégradation gracieuse visible).
    if "hors_ligne" not in context:
        try:
            from webintel.offline.reseau import est_hors_ligne

            context["hors_ligne"] = est_hors_ligne()
        except Exception:
            context["hors_ligne"] = False
    if name == "parametres.html":
        context.setdefault("outils_scientifiques", _outils_scientifiques())
        context.setdefault("services_api", _services_api())
    return HTMLResponse(_jinja.get_template(name).render(**context), status_code=status_code)


def _services_api() -> list[dict]:
    """Présence (jamais la valeur) des clés des fournisseurs Web.

    UI Paramètres uniquement : indique si chaque variable d'environnement
    est définie au démarrage, sans exposer ni conserver de secret.
    """
    import os

    definitions = [
        ("Brave Search", "WEBINTEL_BRAVE_API_KEY", "Recherche Web générale"),
        ("Tavily", "WEBINTEL_TAVILY_API_KEY", "Recherche et extraction Web"),
        ("SerpApi", "WEBINTEL_SERPAPI_API_KEY", "Résultats de moteurs de recherche"),
        ("NewsAPI", "WEBINTEL_NEWSAPI_API_KEY", "Actualités et événements"),
    ]
    return [
        {"nom": nom, "variable": variable, "usage": usage,
         "configure": bool(os.environ.get(variable))}
        for nom, variable, usage in definitions
    ]


def _default_project(db: Session) -> Project:
    """Projet par défaut de l'UI (mode local) : le premier existant, sinon « Défaut » créé."""
    project = db.query(Project).order_by(Project.id).first()
    if project is None:
        project = Project(name="Défaut")
        db.add(project)
        db.commit()
        db.refresh(project)
    return project


def _project_for(db: Session, ui_key: ApiKey | None) -> Project:
    """Projet de la session UI : celui de la clé en mode serveur (isolé),
    le projet par défaut en mode local."""
    if ui_key is not None:
        project = db.get(Project, ui_key.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Projet de la session introuvable.")
        return project
    return _default_project(db)


# ---------------------------------------------------------------------------
# Authentification (mode serveur uniquement)
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_db)):
    from webintel.ui.auth import get_session_claims

    settings = get_settings()
    if not settings.REQUIRE_API_KEY:
        return RedirectResponse(url="/", status_code=303)
    if get_session_claims(request) is not None:
        return RedirectResponse(url="/", status_code=303)
    return _render("login.html", error=None)


@router.post("/login")
async def login_submit(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.REQUIRE_API_KEY:
        return RedirectResponse(url="/", status_code=303)
    form = await request.form()
    # --- Anti-brute-force (chantier robustesse — P1-3) : seuls les ÉCHECS
    # comptent ; un succès réinitialise le compteur. Verrouillage temporaire
    # avec message français après LOGIN_MAX_TENTATIVES échecs. ---
    ip = ip_client_requete(request)
    limiteur = get_limiteur()
    cle_login = cle_client("login", ip)
    if settings.RATE_LIMIT_ENABLED:
        decision = limiteur.verifier(
            cle_login, settings.LOGIN_MAX_TENTATIVES, float(settings.LOGIN_FENETRE_S)
        )
        if not decision.autorise:
            minutes = max(1, int(decision.reessayer_dans_s // 60) + 1)
            logger.warning(
                "Verrouillage anti-brute-force actif pour %s "
                "(%s échecs de login dans la fenêtre).",
                ip, settings.LOGIN_MAX_TENTATIVES,
            )
            return _render(
                "login.html",
                error=(
                    "Trop de tentatives de connexion. Par sécurité, les "
                    "tentatives sont temporairement bloquées : réessayez "
                    f"dans environ {minutes} minute(s)."
                ),
                status_code=429,
            )
    # La clé en clair n'est ni loggée, ni persistée, ni réaffichée.
    key = authenticate_ui_key(db, str(form.get("api_key") or ""))
    if key is None:
        if settings.RATE_LIMIT_ENABLED:
            limiteur.enregistrer(
                cle_login, settings.LOGIN_MAX_TENTATIVES, float(settings.LOGIN_FENETRE_S)
            )
        # Échec journalisé (IP uniquement — jamais la clé tentée).
        logger.warning("Échec de connexion (clé API invalide) depuis %s.", ip)
        return _render("login.html", error="Clé API invalide ou révoquée.")
    # Succès : le compteur d'échecs est réinitialisé.
    if settings.RATE_LIMIT_ENABLED:
        limiteur.reinitialiser(cle_login)
    token = sign_session(key.id, key.project_id, settings.SECRET_KEY)
    response = RedirectResponse(url="/", status_code=303)
    # En mode serveur : Secure (l'instance est servie en HTTPS derrière un
    # reverse-proxy TLS — le cookie ne doit jamais transiter en clair).
    # En mode local : pas de Secure (HTTP en clair sur localhost).
    response.set_cookie(
        UI_SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_S,
        path="/",
        httponly=True,
        secure=settings.REQUIRE_API_KEY,
        samesite="lax",
    )
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse(
        url="/login" if get_settings().REQUIRE_API_KEY else "/",
        status_code=303,
    )
    response.delete_cookie(UI_SESSION_COOKIE, path="/")
    return response


# ---------------------------------------------------------------------------
# Tableau de bord
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def dashboard(db: Session = Depends(get_db), ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    kpis = get_dashboard_kpis(db, project.id)
    return _render("dashboard.html", ui_key=ui_key, active="dashboard",
                   kpis=kpis, project=project)


# ---------------------------------------------------------------------------
# Analyser
# ---------------------------------------------------------------------------

@router.get("/analyser", response_class=HTMLResponse)
def analyser_form(db: Session = Depends(get_db), ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    return _render("analyser.html", ui_key=ui_key, active="analyser",
                   project=project, fiche=None, error=None, url="")


@router.post("/analyser", response_class=HTMLResponse)
async def analyser_submit(request: Request, db: Session = Depends(get_db),
                          ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    form = await request.form()
    url = str(form.get("url") or "").strip()
    if not url:
        return _render("analyser.html", ui_key=ui_key, active="analyser",
                       project=project, fiche=None,
                       error="Veuillez saisir une URL.", url=url)
    try:
        fiche = pipeline.analyze_url(db, project.id, url)
    except (SecurityError, FetchError) as exc:
        return _render("analyser.html", ui_key=ui_key, active="analyser",
                       project=project, fiche=None, error=str(exc), url=url)
    except Exception as exc:  # garde-fou : message FR plutôt qu'une 500 brute
        return _render("analyser.html", ui_key=ui_key, active="analyser",
                       project=project, fiche=None,
                       error=f"Analyse impossible : {exc}", url=url)
    return _render("fiche.html", ui_key=ui_key, active="analyser",
                   project=project, fiche=fiche)


# ---------------------------------------------------------------------------
# Crawls
# ---------------------------------------------------------------------------

def _crawl_runner(start_urls: list[str], project_id: int, limits: dict):
    from webintel.crawler.engine import CrawlEngine

    def runner(ctx: JobContext, session: Session) -> None:
        engine = CrawlEngine()
        result = engine.run(
            str(ctx.job_id),
            start_urls=start_urls,
            project_id=project_id,
            limits=limits,
            progress_cb=lambda p: ctx.update_progress(
                **{**p, "urls": list((p.get("urls") or []))[-20:]}
            ),
            ctx=ctx,
        )
        ctx.update_progress(status="done", summary={
            "visited": result.get("visited"),
            "discovered": result.get("discovered"),
            "errors": result.get("errors"),
            "elapsed_s": result.get("elapsed_s"),
        })

    return runner


@router.get("/crawls", response_class=HTMLResponse)
def crawls_list(db: Session = Depends(get_db), ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    jobs = (
        db.query(CrawlJob)
        .filter(CrawlJob.project_id == project.id)
        .order_by(CrawlJob.id.desc())
        .limit(50)
        .all()
    )
    return _render("crawls.html", ui_key=ui_key, active="crawls",
                   project=project, jobs=jobs)


@router.post("/crawls", response_class=HTMLResponse)
async def crawls_create(request: Request, db: Session = Depends(get_db),
                        ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    form = await request.form()
    start_url = str(form.get("start_url") or "").strip()
    domain = str(form.get("domain") or "").strip()
    if domain and "://" not in domain:
        domain = "https://" + domain
    start_urls = [start_url] if start_url else ([domain] if domain else [])
    limits: dict = {}
    try:
        if str(form.get("max_pages") or "").strip():
            limits["max_pages"] = int(form.get("max_pages"))
        if str(form.get("max_depth") or "").strip():
            limits["max_depth"] = int(form.get("max_depth"))
    except ValueError:
        pass
    if not start_urls:
        jobs = db.query(CrawlJob).filter(CrawlJob.project_id == project.id).order_by(CrawlJob.id.desc()).limit(50).all()
        return _render("crawls.html", ui_key=ui_key, active="crawls",
                       project=project, jobs=jobs,
                       error="Indiquez une URL de départ ou un domaine.")
    job_id = get_manager().submit(
        "crawl", project.id,
        {"start_urls": start_urls, "limits": limits},
        runner=_crawl_runner(start_urls, project.id, limits),
    )
    return RedirectResponse(url=f"/crawls/{job_id}", status_code=303)


@router.get("/crawls/{job_id}", response_class=HTMLResponse)
def crawl_detail(job_id: int, db: Session = Depends(get_db),
                 ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    # Isolation : le job doit appartenir au projet de la session (on ne
    # révèle ni son existence ni son état sinon).
    job = db.get(CrawlJob, job_id)
    if job is None or job.project_id != project.id:
        jobs = db.query(CrawlJob).filter(CrawlJob.project_id == project.id).order_by(CrawlJob.id.desc()).limit(50).all()
        return _render("crawls.html", ui_key=ui_key, active="crawls",
                       project=project, jobs=jobs,
                       error=f"Job introuvable : id={job_id}.")
    try:
        state = get_manager().status(job_id)
    except KeyError:
        state = {"id": job.id, "kind": job.kind, "status": job.status,
                 "progress": dict(job.progress or {}), "error": job.error}
    running = state.get("status") in (JobStatus.RUNNING.value, JobStatus.QUEUED.value, JobStatus.PAUSED.value)
    progress = state.get("progress") or {}
    visited = int(progress.get("visited") or 0)
    discovered = int(progress.get("discovered") or 0)
    total = visited + int(progress.get("remaining") or 0)
    pct = round(100 * visited / total) if total > 0 else 0
    return _render("crawl_detail.html", ui_key=ui_key, active="crawls",
                   project=project, state=state, running=running,
                   pct=pct, progress=progress)


# ---------------------------------------------------------------------------
# Entités
# ---------------------------------------------------------------------------

@router.get("/entites", response_class=HTMLResponse)
def entites_list(request: Request, db: Session = Depends(get_db),
                 ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    params = request.query_params
    results = search_entities(
        db,
        project_id=project.id,
        q=params.get("q") or None,
        entity_type=params.get("type") or None,
        limit=100,
    )
    return _render("entites.html", ui_key=ui_key, active="entites",
                   project=project, results=results,
                   q=params.get("q") or "", type=params.get("type") or "")


@router.get("/entites/{entity_id}", response_class=HTMLResponse)
def entite_detail(entity_id: int, db: Session = Depends(get_db),
                  ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    entity = db.get(Entity, entity_id)
    if entity is None or entity.project_id != project.id:
        return _render("entites.html", ui_key=ui_key, active="entites",
                       project=project, results=[], q="", type="",
                       error=f"Entité introuvable : id={entity_id}.")
    observations = (
        db.query(Observation)
        .filter(Observation.entity_id == entity_id)
        .order_by(Observation.observed_at.desc(), Observation.id.desc())
        .limit(100)
        .all()
    )
    # Observations métier par source (v1.1.1) : valeurs volatiles attribuées.
    grouped = get_observations_by_source(db, entity_id)
    source_ids = {r.source_id for rows in grouped.values() for r in rows if r.source_id}
    source_names = (
        {r.id: r.name for r in db.query(Source).filter(Source.id.in_(source_ids)).all()}
        if source_ids else {}
    )
    return _render("entite_detail.html", ui_key=ui_key, active="entites",
                   project=project, entity=entity, observations=observations,
                   observations_by_source=grouped, source_names=source_names)


# ---------------------------------------------------------------------------
# Moniteurs
# ---------------------------------------------------------------------------

@router.get("/moniteurs", response_class=HTMLResponse)
def moniteurs_list(db: Session = Depends(get_db), ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    monitors = (
        db.query(Monitor)
        .filter(Monitor.project_id == project.id)
        .order_by(Monitor.id.desc())
        .all()
    )
    return _render("moniteurs.html", ui_key=ui_key, active="moniteurs",
                   project=project, monitors=monitors)


@router.post("/moniteurs", response_class=HTMLResponse)
async def moniteurs_create(request: Request, db: Session = Depends(get_db),
                           ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    form = await request.form()
    name = str(form.get("name") or "").strip()
    target = str(form.get("target") or "").strip()
    target_type = str(form.get("target_type") or "url").strip()
    try:
        interval = int(form.get("interval_minutes") or 60)
    except ValueError:
        interval = 60
    if not name or not target:
        monitors = db.query(Monitor).filter(Monitor.project_id == project.id).all()
        return _render("moniteurs.html", ui_key=ui_key, active="moniteurs",
                       project=project, monitors=monitors,
                       error="Nom et cible sont obligatoires.")
    db.add(Monitor(project_id=project.id, name=name, target_type=target_type,
                   target=target, interval_minutes=max(1, interval), active=True, config={}))
    db.commit()
    return RedirectResponse(url="/moniteurs", status_code=303)


@router.post("/moniteurs/{monitor_id}/run")
async def moniteurs_run(monitor_id: int, db: Session = Depends(get_db),
                        ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    monitor = db.get(Monitor, monitor_id)
    if monitor is None or monitor.project_id != project.id:
        return RedirectResponse(url="/moniteurs", status_code=303)

    def runner(ctx: JobContext, session: Session) -> None:
        ctx.update_progress(phase="collecte")
        result = run_monitor(session, monitor_id)
        session.commit()
        ctx.update_progress(phase="terminé", events=len(result.get("events", [])))

    get_manager().submit("monitor_run", project.id, {"monitor_id": monitor_id}, runner=runner)
    return RedirectResponse(url="/evenements", status_code=303)


@router.post("/moniteurs/{monitor_id}/delete")
async def moniteurs_delete(monitor_id: int, db: Session = Depends(get_db),
                           ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    monitor = db.get(Monitor, monitor_id)
    if monitor is not None and monitor.project_id == project.id:
        db.delete(monitor)
        db.commit()
    return RedirectResponse(url="/moniteurs", status_code=303)


# ---------------------------------------------------------------------------
# Alertes & événements
# ---------------------------------------------------------------------------

@router.get("/alertes", response_class=HTMLResponse)
def alertes_list(db: Session = Depends(get_db), ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    alerts = (
        db.query(Alert)
        .filter(Alert.project_id == project.id)
        .order_by(Alert.created_at.desc(), Alert.id.desc())
        .limit(100)
        .all()
    )
    return _render("alertes.html", ui_key=ui_key, active="alertes",
                   project=project, alerts=alerts)


@router.get("/evenements", response_class=HTMLResponse)
def evenements_list(db: Session = Depends(get_db), ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    events = (
        db.query(Event)
        .filter(Event.project_id == project.id)
        .order_by(Event.created_at.desc(), Event.id.desc())
        .limit(100)
        .all()
    )
    return _render("evenements.html", ui_key=ui_key, active="evenements",
                   project=project, events=events)


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------

@router.get("/recherche", response_class=HTMLResponse)
def recherche(request: Request, db: Session = Depends(get_db),
              ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    params = request.query_params
    q = params.get("q") or ""
    results = []
    if q.strip():
        def _f(name, cast=float):
            raw = params.get(name)
            if raw is None or not str(raw).strip():
                return None
            try:
                return cast(str(raw).strip())
            except ValueError:
                return None

        results = search_entities(
            db, project_id=project.id, q=q.strip(),
            entity_type=params.get("type") or None,
            min_price=_f("min_price"), max_price=_f("max_price"),
            domain=params.get("domain") or None, limit=100,
        )
    return _render("recherche.html", ui_key=ui_key, active="recherche",
                   project=project, q=q, results=results, count=len(results),
                   type=params.get("type") or "", min_price=params.get("min_price") or "",
                   max_price=params.get("max_price") or "", domain=params.get("domain") or "")


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

@router.get("/exports", response_class=HTMLResponse)
def exports_page(db: Session = Depends(get_db), ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    return _render("exports.html", ui_key=ui_key, active="exports", project=project)


@router.post("/exports", response_class=HTMLResponse)
async def exports_create(request: Request, db: Session = Depends(get_db),
                         ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    form = await request.form()
    kind = str(form.get("kind") or "entities").strip()
    format = str(form.get("format") or "csv").strip()
    try:
        export_id, _bytes = build_export(db, project.id, kind, format, {})
        db.commit()
        export = db.get(Export, export_id)
        message = f"Export prêt : {len(_bytes)} octets."
        download_url = f"/api/exports/{export_id}/download"
    except ValueError as exc:
        export, message, download_url = None, str(exc), None
    return _render("exports.html", ui_key=ui_key, active="exports",
                   project=project, message=message, export=export,
                   download_url=download_url)


# ---------------------------------------------------------------------------
# Paramètres (Phase 11 : IA désactivable)
# ---------------------------------------------------------------------------

@router.get("/parametres", response_class=HTMLResponse)
def parametres_form(request: Request, db: Session = Depends(get_db),
                    ui_key: ApiKey | None = Depends(ui_auth)):
    from webintel.config import runtime as runtime_config

    active, source = runtime_config.is_ai_enabled()
    return _render("parametres.html", ui_key=ui_key, active="parametres",
                   ia_activee=active, source_decision=source, message=None)


@router.post("/parametres", response_class=HTMLResponse)
async def parametres_appliquer(request: Request, db: Session = Depends(get_db),
                               ui_key: ApiKey | None = Depends(ui_auth)):
    from webintel.config import runtime as runtime_config

    form = await request.form()
    action = str(form.get("action") or "appliquer")
    if action == "reinitialiser":
        runtime_config.clear_runtime_override(runtime_config.OVERRIDE_AI_ENABLED)
        message = ("Préférence d'interface effacée : la variable d'environnement "
                   "WEBINTEL_AI_ENABLED (ou le défaut) s'applique à nouveau.")
    else:
        souhaitee = str(form.get("ia_activee") or "") == "on"
        runtime_config.set_runtime_override(runtime_config.OVERRIDE_AI_ENABLED, souhaitee)
        message = ("Fonctionnalités d'IA désactivées : elles n'apparaissent plus "
                   "dans l'interface et l'API répond 503."
                   if not souhaitee else
                   "Fonctionnalités d'IA activées.")
    active, source = runtime_config.is_ai_enabled()
    return _render("parametres.html", ui_key=ui_key, active="parametres",
                   ia_activee=active, source_decision=source, message=message)


def _job_badge(status: str) -> str:
    return {
        JobStatus.QUEUED.value: "muted", JobStatus.RUNNING.value: "",
        JobStatus.PAUSED.value: "warn", JobStatus.COMPLETED.value: "ok",
        JobStatus.FAILED.value: "bad", JobStatus.CANCELLED.value: "muted",
    }.get(status, "muted")


_jinja.globals["job_badge"] = _job_badge
_jinja.filters["badge_importance"] = lambda v: {"high": "bad", "medium": "warn"}.get(v, "muted")


_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _date_courte(valeur) -> str:
    """Affiche une date/heure de façon lisible : « 04/10/2026 03:43 ».

    Accepte un datetime ou une chaîne ISO (avec ou sans microsecondes).
    """
    if valeur is None or valeur == "":
        return "—"
    dt = valeur
    if isinstance(valeur, str):
        try:
            dt = datetime.fromisoformat(valeur.strip().replace("Z", "+00:00"))
        except ValueError:
            return valeur
    formater = getattr(dt, "strftime", None)
    if formater is None:
        return str(valeur)
    return formater("%d/%m/%Y %H:%M")


def _afficher_valeur(valeur) -> str:
    """Affiche une valeur observée de façon lisible pour un non-technicien.

    - dict prix {amount, currency} -> « 44,99 EUR » (virgule française) ;
    - date/datetime (objet ou chaîne ISO) -> « 04/10/2026 03:43 » ;
    - autres dicts/listes -> rendu aplati lisible, jamais de repr Python.
    """
    if isinstance(valeur, dict):
        montant = valeur.get("amount")
        devise = valeur.get("currency")
        if montant is not None:
            try:
                texte_montant = f"{float(montant):,.2f}".replace(",", " ").replace(".", ",")
            except (TypeError, ValueError):
                texte_montant = str(montant)
            return f"{texte_montant} {devise}".strip() if devise else texte_montant
        return ", ".join(f"{cle} : {_afficher_valeur(v)}" for cle, v in valeur.items())
    if isinstance(valeur, (list, tuple)):
        return ", ".join(_afficher_valeur(v) for v in valeur)
    if hasattr(valeur, "strftime"):
        return _date_courte(valeur)
    if isinstance(valeur, str):
        texte = valeur.strip()
        if _ISO_DATETIME_RE.match(texte):
            return _date_courte(texte)
        return texte
    return str(valeur)


_jinja.filters["date_courte"] = _date_courte
_jinja.filters["afficher_valeur"] = _afficher_valeur
