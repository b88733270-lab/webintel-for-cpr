"""Routes de l'interface web scientifique (Jinja2, 100 % français).

- ``/plantes`` : liste paginée des plantes étudiées ;
- ``/plantes/{plant_id}`` : fiche scientifique complète d'une plante
  (identité, botanique, pharmacognosie, phytochimie, pharmacologie,
  sécurité, bibliographie, données de laboratoire privées, simulations) ;
- ``/recherche-science`` : recherche multi-types (plantes, composés,
  publications, simulations) avec filtres ;
- ``/simulations`` : moteurs et exécution ;
- ``/workflows`` (PHASE 7) : workflows chercheur — création, pas-à-pas en
  7 étapes, avancement, rejeu, rapport final ;
- ``/laboratoire`` et ``/journal`` (PHASE 8) : tableau de bord du
  laboratoire, journal de laboratoire versionné, publication explicite.

Les fiches et la recherche sont construites par les contrats backend
``webintel.science.fiche.build_plant_fiche`` et
``webintel.science.search.search_science`` (importés paresseusement :
les modules sont développés en parallèle, l'UI reste importable
même s'ils ne sont pas encore présents).

Contrats backend des PHASES 7/8 (import paresseux, même motif) :

- ``webintel.science.workflows`` (PHASE 7, équipe parallèle) :
  ``creer_workflow(db, *, project_id, type_workflow, nom, inputs,
  cree_par=None)`` avec ``inputs = {"plant_id": int, "compound_id": int,
  "engine_name"?: str}``, ``get_workflow(db, run_id)`` (``KeyError`` si
  absent), ``list_workflows(db, project_id=None)``,
  ``avancer_workflow(db, run_id)`` et ``rejouer_workflow(db, run_id)``.
  Un run expose ``id``, ``nom``, ``type_workflow``, ``statut``
  ("en_cours"/"termine"/"echoue"), ``etape_courante`` (index 0-based),
  ``etapes`` (dicts {"nom","libelle","statut","entrees","sorties",
  "erreur"}) et ``resultat`` (export final : "rapport" à 6 sections,
  "fiche", "simulation_id", "avertissement").
- ``webintel.science.lab`` (PHASE 8) : ``creer_note(session, project_id,
  *, titre, contenu, auteur)``, ``editer_note(session, note_id, *,
  titre, contenu)``, ``get_note(session, note_id)``,
  ``list_notes(session, project_id)``, ``versions_note(session,
  note_id)``, ``publier_item(session, *, item_type, item_id, project_id,
  publie_par, commentaire)``, ``est_publie(session, item_type, item_id)``
  et ``tableau_de_bord(session, project_id)``.
"""

from __future__ import annotations

import re

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from webintel.api.deps import get_db
from webintel.database.models import ApiKey
from webintel.science import service as science_service
from webintel.science.models import Compound, Plant, PlantCompound, Simulation
from webintel.simulations import registry as simulations_registry
from webintel.simulations.reports import build_report_from_simulation, render_markdown
from webintel.simulations.service import run_simulation
from webintel.ui.auth import ui_auth
from webintel.ui.routes import _project_for, _render

# Enregistrement des modèles des PHASES 7/8 AVANT `init_db()` du lifespan :
# `webintel.ui.science_routes` est importé par `webintel.main` au
# démarrage, donc ces imports garantissent la création des tables
# `lab_notes`, `lab_note_versions`, `publication_records` et
# `workflow_runs` (les routeurs API des équipes parallèles ne sont pas
# encore câblés dans `main.py` — ne pas retirer sans vérifier ce câblage).
# Protégé : l'UI reste importable même si un module manque.
try:
    from webintel.science import lab as _lab_models  # noqa: F401
except ImportError:  # pragma: no cover
    _lab_models = None
try:
    from webintel.science import workflows as _workflows_models  # noqa: F401
except ImportError:  # pragma: no cover
    _workflows_models = None


def _attr(obj, nom: str, defaut=None):
    """Lecture tolérante : fonctionne sur un dict comme sur un objet."""
    if isinstance(obj, dict):
        return obj.get(nom, defaut)
    return getattr(obj, nom, defaut)


def _format_date(valeur) -> str:
    """Date lisible pour un non-développeur (jj/mm/aaaa hh:mm)."""
    if valeur is None:
        return "—"
    formater = getattr(valeur, "strftime", None)
    if callable(formater):
        try:
            return formater("%d/%m/%Y %H:%M")
        except Exception:
            return str(valeur)
    return str(valeur)


router = APIRouter()

_PLANTES_PAR_PAGE = 50
_RECHERCHE_PAR_PAGE = 20


def _load_backend(nom_module: str, nom_fonction: str):
    """Import paresseux d'un contrat backend scientifique.

    Lève une 503 explicite (français) si le module ou la fonction du
    contrat n'est pas encore disponible : l'UI ne doit jamais exploser
    en 500 brute.
    """
    try:
        module = __import__(f"webintel.science.{nom_module}", fromlist=[nom_fonction])
        return getattr(module, nom_fonction)
    except (ImportError, AttributeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Module scientifique en cours d'intégration — réessayez plus tard.",
        ) from exc


@router.get("/plantes", response_class=HTMLResponse)
def plantes_liste(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Liste des plantes étudiées, triées par nom scientifique (50 par page)."""
    project = _project_for(db, ui_key)
    try:
        page = max(1, int(request.query_params.get("page") or 1))
    except ValueError:
        page = 1

    total = db.query(func.count(Plant.id)).scalar() or 0
    plantes = (
        db.query(Plant)
        .order_by(Plant.scientific_name)
        .offset((page - 1) * _PLANTES_PAR_PAGE)
        .limit(_PLANTES_PAR_PAGE)
        .all()
    )
    # Une seule requête groupée (pas de N+1) : nb de composés distincts.
    nb_composes: dict[int, int] = {}
    if plantes:
        ids = [p.id for p in plantes]
        rows = (
            db.query(
                PlantCompound.plant_id,
                func.count(func.distinct(PlantCompound.compound_id)),
            )
            .filter(PlantCompound.plant_id.in_(ids))
            .group_by(PlantCompound.plant_id)
            .all()
        )
        nb_composes = dict(rows)
    nb_pages = max(1, -(-total // _PLANTES_PAR_PAGE))
    return _render(
        "plantes_liste.html",
        ui_key=ui_key,
        active="plantes",
        project=project,
        plantes=plantes,
        nb_composes=nb_composes,
        page=page,
        nb_pages=nb_pages,
        total=total,
    )


@router.get("/plantes/{plant_id}", response_class=HTMLResponse)
def plante_fiche(
    request: Request,
    plant_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Fiche scientifique complète d'une plante (404 FR si absente)."""
    project = _project_for(db, ui_key)
    return _rendre_fiche(db, ui_key, project, plant_id)


def _rendre_fiche(
    db: Session,
    ui_key: ApiKey | None,
    project,
    plant_id: int,
    erreur: str | None = None,
    message: str | None = None,
) -> HTMLResponse:
    """Construit la fiche backend et rend le template (avec feedback)."""
    build_plant_fiche = _load_backend("fiche", "build_plant_fiche")
    try:
        fiche = build_plant_fiche(db, plant_id, project_id=project.id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Plante introuvable (id={plant_id})."
        ) from exc
    # Normalisation défensive : le contrat dit laboratoire_prive=[...] avec
    # avertissement_separation au niveau fiche ; si le backend renvoie un
    # dict {"observations": [...], "avertissement_separation": ...},
    # on remet la forme du contrat avant le rendu.
    labo = fiche.get("laboratoire_prive")
    if isinstance(labo, dict):
        fiche["avertissement_separation"] = (
            fiche.get("avertissement_separation") or labo.get("avertissement_separation")
        )
        fiche["laboratoire_prive"] = labo.get("observations") or []
    return _render(
        "plante_fiche.html",
        ui_key=ui_key,
        active="plantes",
        project=project,
        fiche=fiche,
        plant_id=plant_id,
        erreur=erreur,
        message=message,
        # Phase 11 : la section « Synthèse IA » n'est rendue que si l'IA est
        # active ; sinon elle n'apparaît pas du tout dans la fiche.
        ia_activee=ia_activee(),
    )


# ---------------------------------------------------------------------------
# Édition manuelle sourcée de la fiche (v2.1)
# ---------------------------------------------------------------------------
# Chaque ajout exige une source (publication ou DOI) : sans source, le
# formulaire est rejeté avec un message explicite. Les ajouts sont
# étiquetés « publié » avec leur source — jamais validés par WebIntel.

_RE_DOI = re.compile(r"^10\.\d{4,}/.+", re.IGNORECASE)

_CHAMPS_BOTANIQUES = (
    "feuilles", "fleurs", "fruits", "graines", "racines", "ecorce",
    "habitat", "matiere_premiere", "preparation",
)

_TYPES_ETUDE = {"in_vitro", "in_vivo", "clinique", "ex_vivo", "autre"}


def _ressemble_doi(texte: str) -> bool:
    """Heuristique : le texte ressemble-t-il à un DOI (10.xxxx/...) ?"""
    return bool(_RE_DOI.match(texte.strip()))


def _preuve_pour_source(db: Session, source: str):
    """Crée (ou récupère) une `Evidence` pour une source en forme de DOI.

    Retourne `(evidence_id, doi, publication)` : si la source ressemble à
    un DOI, une preuve est créée et pointée ; sinon la source reste un
    texte de publication libre.
    """
    source = source.strip()
    if _ressemble_doi(source):
        preuve, _ = science_service.add_evidence(
            db, doi=source, title="", reference=source
        )
        db.flush()
        return preuve.id, source, None
    return None, None, source


def _flottant(valeur: str | None, champ: str) -> float | None:
    """Parse un flottant de formulaire (virgule acceptée) ou lève ValueError."""
    texte = (valeur or "").strip().replace(",", ".")
    if not texte:
        return None
    try:
        return float(texte)
    except ValueError:
        raise ValueError(
            f"Le champ « {champ} » doit être un nombre (reçu : {valeur!r})."
        ) from None


@router.post("/plantes/{plant_id}/botanique", response_class=HTMLResponse)
async def plante_botanique_modifier(
    request: Request,
    plant_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Édition manuelle sourcée : botanique & pharmacognosie (v2.1)."""
    project = _project_for(db, ui_key)
    form = await request.form()
    source = (form.get("source") or "").strip()
    if not source:
        return _rendre_fiche(
            db, ui_key, project, plant_id,
            erreur="Une source est obligatoire (publication ou DOI) : "
                   "précisez d'où vient cette information.",
        )
    champs: dict = {}
    for nom in _CHAMPS_BOTANIQUES:
        valeur = (form.get(nom) or "").strip()
        if valeur:
            champs[nom] = valeur
    references = (form.get("references_botaniques") or "").strip()
    if references:
        champs["references_botaniques"] = [
            r.strip() for r in references.split(";") if r.strip()
        ]
    if not champs:
        return _rendre_fiche(
            db, ui_key, project, plant_id,
            erreur="Aucun champ renseigné : rien à enregistrer.",
        )
    try:
        science_service.set_plant_fields(db, plant_id, **champs)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Plante introuvable (id={plant_id})."
        )
    except ValueError as exc:
        return _rendre_fiche(db, ui_key, project, plant_id, erreur=str(exc))
    science_service.record_provenance(
        db, "plant", plant_id, source_name=source,
        connector="ui-manuel", data_version="saisie-fiche",
    )
    db.commit()
    return _rendre_fiche(
        db, ui_key, project, plant_id,
        message="Botanique / pharmacognosie mise à jour — source enregistrée.",
    )


@router.post("/plantes/{plant_id}/composes/ajouter", response_class=HTMLResponse)
async def plante_compose_ajouter(
    request: Request,
    plant_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Ajout manuel sourcé d'un composé à la fiche (v2.1)."""
    project = _project_for(db, ui_key)
    form = await request.form()
    nom = (form.get("nom_compose") or "").strip()
    source = (form.get("source") or "").strip()
    if not nom:
        return _rendre_fiche(
            db, ui_key, project, plant_id,
            erreur="Le nom du composé est obligatoire.",
        )
    if not source:
        return _rendre_fiche(
            db, ui_key, project, plant_id,
            erreur="Une source est obligatoire (publication ou DOI) : "
                   "chaque composé ajouté doit être sourcé.",
        )
    try:
        concentration = _flottant(form.get("concentration"), "concentration")
        rendement = _flottant(form.get("rendement"), "rendement")
    except ValueError as exc:
        return _rendre_fiche(db, ui_key, project, plant_id, erreur=str(exc))
    _plante_ou_404_ui(db, plant_id)
    compose, _ = science_service.create_compound(db, nom)
    evidence_id, doi, publication = _preuve_pour_source(db, source)
    try:
        lien, _ = science_service.link_plant_compound(
            db,
            plant_id,
            compose.id,
            plant_part=(form.get("partie") or "").strip() or None,
            publication=publication,
            doi=doi,
            concentration=concentration,
            concentration_unit=(form.get("unite") or "").strip() or None,
            extraction_method=(form.get("methode_extraction") or "").strip() or None,
            analysis_method=(form.get("methode_analyse") or "").strip() or None,
            rendement=rendement,
            rendement_unite=(form.get("rendement_unite") or "").strip() or None,
            evidence_id=evidence_id,
            confidence="publie" if _ressemble_doi(source) else "rapporte",
        )
    except ValueError as exc:
        return _rendre_fiche(db, ui_key, project, plant_id, erreur=str(exc))
    science_service.record_provenance(
        db, "plant_compound", lien.id, source_name=source,
        connector="ui-manuel", data_version="saisie-fiche",
    )
    db.commit()
    return _rendre_fiche(
        db, ui_key, project, plant_id,
        message=f"Composé « {nom} » ajouté à la fiche — source enregistrée.",
    )


@router.post("/plantes/{plant_id}/activites/ajouter", response_class=HTMLResponse)
async def plante_activite_ajouter(
    request: Request,
    plant_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Ajout manuel sourcé d'une activité pharmacologique (v2.1)."""
    project = _project_for(db, ui_key)
    form = await request.form()
    activite = (form.get("activite") or "").strip()
    source = (form.get("source") or "").strip()
    if not activite:
        return _rendre_fiche(
            db, ui_key, project, plant_id,
            erreur="L'activité est obligatoire.",
        )
    if not source:
        return _rendre_fiche(
            db, ui_key, project, plant_id,
            erreur="Une source est obligatoire (publication ou DOI) : "
                   "chaque activité ajoutée doit être sourcée.",
        )
    type_etude = (form.get("type_etude") or "").strip() or None
    if type_etude and type_etude not in _TYPES_ETUDE:
        return _rendre_fiche(
            db, ui_key, project, plant_id,
            erreur=f"Type d'étude invalide : {type_etude!r}.",
        )
    _plante_ou_404_ui(db, plant_id)
    evidence_id, doi, reference = _preuve_pour_source(db, source)
    science_service.add_plant_activity(
        db,
        plant_id,
        activite,
        study_type=type_etude,
        study_model=(form.get("modele_etude") or "").strip() or None,
        published_result=(form.get("resultat_publie") or "").strip() or None,
        reference=reference,
        doi=doi,
        evidence_id=evidence_id,
    )
    db.commit()
    return _rendre_fiche(
        db, ui_key, project, plant_id,
        message=f"Activité « {activite} » ajoutée — donnée rapportée, source enregistrée.",
    )


@router.post("/plantes/{plant_id}/cibles/ajouter", response_class=HTMLResponse)
async def plante_cible_ajouter(
    request: Request,
    plant_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Ajout manuel sourcé d'une cible documentée (v2.1)."""
    project = _project_for(db, ui_key)
    form = await request.form()
    cible = (form.get("cible") or "").strip()
    source = (form.get("source") or "").strip()
    try:
        compose_id = int(form.get("compose_id") or 0)
    except (TypeError, ValueError):
        compose_id = 0
    if not compose_id:
        return _rendre_fiche(
            db, ui_key, project, plant_id,
            erreur="Choisissez le composé concerné.",
        )
    if not cible:
        return _rendre_fiche(
            db, ui_key, project, plant_id,
            erreur="La cible biologique est obligatoire.",
        )
    if not source:
        return _rendre_fiche(
            db, ui_key, project, plant_id,
            erreur="Une source est obligatoire (publication ou DOI) : "
                   "chaque cible ajoutée doit être sourcée.",
        )
    _plante_ou_404_ui(db, plant_id)
    compose = db.get(Compound, compose_id)
    if compose is None:
        return _rendre_fiche(
            db, ui_key, project, plant_id, erreur="Composé introuvable."
        )
    evidence_id, doi, publication = _preuve_pour_source(db, source)
    science_service.link_compound_target(
        db,
        compose_id,
        cible,
        interaction_type=(form.get("type_interaction") or "").strip() or None,
        source=source,
        publication=publication,
        doi=doi,
        evidence_id=evidence_id,
    )
    db.commit()
    return _rendre_fiche(
        db, ui_key, project, plant_id,
        message=f"Cible « {cible} » documentée pour « {compose.name} » — source enregistrée.",
    )


@router.post("/plantes/{plant_id}/etudes/ajouter", response_class=HTMLResponse)
async def plante_etude_ajouter(
    request: Request,
    plant_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Ajout manuel d'une étude / publication structurée (v2.1)."""
    project = _project_for(db, ui_key)
    form = await request.form()
    titre = (form.get("titre") or "").strip()
    if not titre:
        return _rendre_fiche(
            db, ui_key, project, plant_id,
            erreur="Le titre de l'étude est obligatoire.",
        )
    _plante_ou_404_ui(db, plant_id)
    auteurs = [
        a.strip() for a in (form.get("auteurs") or "").split(";") if a.strip()
    ]
    annee_texte = (form.get("annee") or "").strip()
    annee = None
    if annee_texte:
        try:
            annee = int(annee_texte)
        except ValueError:
            return _rendre_fiche(
                db, ui_key, project, plant_id,
                erreur=f"Année invalide : {annee_texte!r}.",
            )
    doi = (form.get("doi") or "").strip() or None
    science_service.create_study(
        db,
        doi=doi,
        title=titre,
        authors=auteurs,
        year=annee,
        journal=(form.get("journal") or "").strip() or None,
        pmid=(form.get("pmid") or "").strip() or None,
    )
    # La fiche relie les études par DOI : si l'étude cite un DOI déjà lié
    # à la plante, elle apparaîtra en « Littérature ».
    db.commit()
    return _rendre_fiche(
        db, ui_key, project, plant_id,
        message=f"Étude « {titre[:60]} » ajoutée à la littérature.",
    )


def _plante_ou_404_ui(db: Session, plant_id: int) -> Plant:
    """Plante ou 404 (variante UI des formulaires de la fiche)."""
    plante = db.get(Plant, plant_id)
    if plante is None:
        raise HTTPException(
            status_code=404, detail=f"Plante introuvable (id={plant_id})."
        )
    return plante


def ia_activee() -> bool:
    """Garde-fou IA pour l'UI : True seulement si l'IA est activée."""
    from webintel.config import runtime as runtime_config

    active, _source = runtime_config.is_ai_enabled()
    return active


@router.get("/plantes/{plant_id}/synthese-ia", response_class=HTMLResponse)
def synthese_ia_page(
    request: Request,
    plant_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Page de synthèse automatique d'une plante (Phase 11).

    503 explicite si l'IA est désactivée : la page n'est alors jamais
    générée. La synthèse reste éphémère, étiquetée « hypothèse », avec ses
    sources et son avertissement — jamais persistée.
    """
    from webintel.science import ia as ia_module

    project = _project_for(db, ui_key)
    if not ia_activee():
        raise HTTPException(
            status_code=503,
            detail="Fonctionnalité d'IA désactivée : activez-la depuis la "
                   "page « Paramètres » pour générer une synthèse.",
        )
    try:
        synthese = ia_module.synthese_plante(db, plant_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _render(
        "synthese_ia.html",
        ui_key=ui_key,
        active="plantes",
        project=project,
        synthese=synthese,
        plant_id=plant_id,
    )


@router.get("/recherche-science", response_class=HTMLResponse)
def recherche_science(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Recherche scientifique multi-types avec filtres et pagination."""
    project = _project_for(db, ui_key)
    params = request.query_params
    q = (params.get("q") or "").strip()
    types = [t for t in params.getlist("type") if t]
    # Le formulaire historique envoyait `niveau=` : on accepte les deux noms.
    data_level = params.get("data_level") or params.get("niveau") or None
    partie = (params.get("partie") or "").strip() or None
    try:
        page = max(1, int(params.get("page") or 1))
    except ValueError:
        page = 1

    resultats = None
    total = 0
    erreur = None
    if q:
        search_science = _load_backend("search", "search_science")
        offset = (page - 1) * _RECHERCHE_PAR_PAGE
        try:
            resultats = search_science(
                db,
                q,
                types=types or None,
                data_level=data_level,
                partie=partie,
                project_id=project.id,
                limit=_RECHERCHE_PAR_PAGE,
                offset=offset,
            )
        except ValueError as exc:
            # Requête trop courte ou paramètre invalide : message propre
            # en français plutôt qu'une erreur 500.
            erreur = str(exc)
            resultats = None
        else:
            total = int(resultats.get("total", 0) or 0)

    # Chaîne de requête conservée pour les liens précédent/suivant.
    qs_parts: list[tuple[str, str]] = [("q", q)]
    qs_parts += [("type", t) for t in types]
    if data_level:
        qs_parts.append(("data_level", data_level))
    if partie:
        qs_parts.append(("partie", partie))
    base_qs = urlencode(qs_parts)

    return _render(
        "recherche_science.html",
        ui_key=ui_key,
        active="recherche-science",
        project=project,
        q=q,
        types=types,
        data_level=data_level or "",
        partie=partie or "",
        resultats=resultats,
        total=total,
        page=page,
        limit=_RECHERCHE_PAR_PAGE,
        base_qs=base_qs,
        erreur=erreur,
    )


# ---------------------------------------------------------------------------
# Simulations : moteurs + exécution
# ---------------------------------------------------------------------------

_FORM_CHAMPS = ("moteur", "nom", "description", "inputs", "parameters")


def _identite_ui(ui_key) -> str:
    """Identité d'audit pour une exécution depuis l'UI (``executed_by``)."""
    if ui_key is None:
        return "local"
    prefixe = getattr(ui_key, "prefix", None) or "?"
    return f"ui:{prefixe}"


def _contexte_simulations(ui_key, project, moteurs, form=None, rapport=None,
                          simulation_id=None, erreur=None, db=None):
    """Contexte commun du template ``simulations.html``."""
    precedentes = []
    if db is not None and project is not None:
        lignes = (
            db.query(Simulation)
            .filter(Simulation.project_id == project.id)
            .order_by(Simulation.id.desc())
            .limit(20)
            .all()
        )
        for sim in lignes:
            precedentes.append(
                {
                    "id": sim.id,
                    "name": sim.name,
                    "engine_name": sim.engine_name or "—",
                    "engine_version": sim.engine_version or "",
                    "input_hash": (sim.input_hash or "")[:12],
                    "executed_by": sim.executed_by or "—",
                    "run_at": sim.run_at,
                    "status": sim.status,
                }
            )
    return dict(
        ui_key=ui_key,
        active="simulations",
        project=project,
        moteurs=moteurs,
        moteurs_disponibles=[m for m in moteurs if m["disponible"]],
        form=form or {champ: "" for champ in _FORM_CHAMPS},
        rapport=rapport,
        simulation_id=simulation_id,
        erreur=erreur,
        simulations_precedentes=precedentes,
    )


@router.get("/simulations", response_class=HTMLResponse)
def simulations_page(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Moteurs de simulation : statuts réels + formulaire d'exécution.

    Chaque moteur affiche son statut RÉEL (disponible / indisponible +
    raison) et ses limitations. Le formulaire n'est proposé que pour les
    moteurs disponibles : un moteur indisponible ne peut pas être lancé
    (aucun faux calcul).
    """
    project = _project_for(db, ui_key)
    moteurs = simulations_registry.list_engines()
    return _render(
        "simulations.html",
        **_contexte_simulations(ui_key, project, moteurs, db=db),
    )


@router.post("/simulations/run", response_class=HTMLResponse)
async def simulations_executer(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Exécute un moteur disponible depuis le formulaire et affiche le rapport.

    Les champs ``inputs`` / ``parameters`` sont du JSON saisi à la main :
    toute erreur de saisie est réaffichée en français, sans 500. La
    simulation est rattachée au projet de la session et le rapport
    affiché porte l'avertissement « Simulation — à confirmer
    expérimentalement ».
    """
    import json

    project = _project_for(db, ui_key)
    moteurs = simulations_registry.list_engines()
    donnees_form = await request.form()
    form = {champ: (donnees_form.get(champ) or "") for champ in _FORM_CHAMPS}

    def _echec(message: str) -> HTMLResponse:
        return _render(
            "simulations.html",
            **_contexte_simulations(ui_key, project, moteurs, form=form, erreur=message, db=db),
        )

    moteur_nom = form["moteur"].strip()
    nom = form["nom"].strip()
    if not moteur_nom:
        return _echec("Choisissez un moteur de simulation.")
    if not nom:
        return _echec("Donnez un nom à la simulation (champ « Nom »).")
    try:
        entrees = json.loads(form["inputs"] or "{}")
        parametres = json.loads(form["parameters"] or "{}")
    except ValueError as exc:
        return _echec(f"JSON invalide : {exc}.")
    if not isinstance(entrees, dict) or not isinstance(parametres, dict):
        return _echec("Les entrées et les paramètres doivent être des objets JSON {...}.")

    try:
        simulation, rapport = run_simulation(
            db,
            engine_name=moteur_nom,
            name=nom,
            inputs=entrees,
            parameters=parametres,
            project_id=project.id,
            description=form["description"].strip() or None,
            executed_by=_identite_ui(ui_key),
        )
    except ValueError as exc:
        return _echec(str(exc))
    db.commit()  # le service ne committe jamais : c'est à la route de le faire
    db.refresh(simulation)
    return _render(
        "simulations.html",
        **_contexte_simulations(
            ui_key, project, moteurs, form=form,
            rapport=rapport, simulation_id=simulation.id, db=db,
        ),
    )


@router.get("/simulations/{simulation_id}/rapport", response_class=HTMLResponse)
def simulation_rapport_page(
    simulation_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Page du rapport reproductible d'une simulation persistée (Phase 13).

    Reconstruit les 6 sections depuis les champs persistés (audit :
    qui, quand, empreinte SHA-256, version du moteur), avec l'avertissement
    « Simulation — à confirmer expérimentalement » et un lien de
    téléchargement Markdown. ``?format=markdown`` télécharge le document.

    P0-1 : cloisonnement strict — en mode serveur, une simulation
    n'appartenant pas au projet de la session (orpheline y compris)
    répond 404, jamais de fuite inter-projets.
    """
    from fastapi.responses import Response

    project = _project_for(db, ui_key)
    simulation = db.get(Simulation, simulation_id)
    # P0-1 : plus aucun chemin « si project_id is None → pas de contrôle » —
    # une orpheline ne correspond à aucun projet → 404.
    if simulation is None or simulation.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail=f"Simulation introuvable : id={simulation_id}.",
        )
    rapport = build_report_from_simulation(simulation)
    if request.query_params.get("format") == "markdown":
        nom = f"rapport-simulation-{simulation_id}.md"
        return Response(
            content=render_markdown(rapport),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{nom}"'},
        )
    return _render(
        "simulation_rapport.html",
        ui_key=ui_key,
        active="simulations",
        project=project,
        simulation=simulation,
        rapport=rapport,
    )


# ---------------------------------------------------------------------------
# PHASE 7 — Workflows chercheur
# ---------------------------------------------------------------------------
# Backend : `webintel.science.workflows` (livré par l'équipe parallèle).
# Signatures réelles — convention scientifique : session en premier
# argument, jamais de commit côté service, `ValueError` en français pour
# les cas métier :
#   creer_workflow(db, *, project_id, type_workflow, nom, inputs,
#                  cree_par=None)
#       inputs = {"plant_id": int, "compound_id": int,
#                 "engine_name"?: str} — plant_id et compound_id requis.
#   get_workflow(db, run_id)            -> WorkflowRun (`KeyError` si absent)
#   list_workflows(db, project_id=None) -> list[WorkflowRun]
#   avancer_workflow(db, run_id)        -> WorkflowRun ; `ValueError` si le
#       run est terminé ou si l'étape échoue (l'état d'échec, message
#       français inclus, est flushé AVANT la levée : la route doit
#       committer pour l'afficher, la reprise reste possible).
#   rejouer_workflow(db, run_id)        -> WorkflowRun réinitialisé
#       (mêmes inputs initiaux, même `input_hash`).
# Un run expose : `id`, `nom`, `type_workflow`, `statut` ("en_cours" /
# "termine" / "echoue"), `etape_courante` (index 0-based de la prochaine
# étape), `etapes` (liste de dicts {"nom", "libelle", "statut", "entrees",
# "sorties", "erreur"} — statut parmi "en_attente" / "en_cours" /
# "terminee" / "echouee"), `resultat` (export final : {"rapport", "fiche",
# "simulation_id", "avertissement", "exporte_le"}), `created_at`.

_TYPES_WORKFLOW = {
    "analyse_plante": "Analyse d'une plante médicinale",
}

# statut backend -> (label affiché, clé de pastille du template _macros.html)
_STATUTS_ETAPE = {
    "en_attente": ("En attente", "en_attente"),
    "en_cours": ("En cours", "en_cours"),
    "terminee": ("Terminée", "terminee"),
    "echouee": ("Échouée", "echouee"),
}

_STATUTS_RUN = {
    "en_cours": ("En cours", ""),
    "termine": ("Terminé", "ok"),
    "echoue": ("Échoué", "bad"),
}


def _moteurs_workflow() -> list[dict]:
    """Moteurs proposés pour l'étape de simulation du workflow.

    Les moteurs supportés par le workflow (`MOTEURS_SIMULATION`), chacun
    avec sa disponibilité RÉELLE du registre — on ne propose jamais un
    faux calcul.
    """
    try:
        module = __import__(
            "webintel.science.workflows", fromlist=["MOTEURS_SIMULATION"]
        )
        supportes = list(module.MOTEURS_SIMULATION)
    except (ImportError, AttributeError):
        supportes = []
    try:
        fiches = {
            m["nom"]: m for m in simulations_registry.list_engines()
        }
    except Exception:  # pragma: no cover — le registre est toujours là
        fiches = {}
    moteurs = []
    for nom in supportes:
        fiche = fiches.get(nom, {})
        moteurs.append({
            "nom": nom,
            "disponible": bool(fiche.get("disponible")),
            "description": fiche.get("description") or "",
        })
    return moteurs


def _vue_run(run) -> dict:
    """Normalise un `WorkflowRun` pour les templates."""
    etapes = []
    for i, etape in enumerate(run.etapes or []):
        etape = etape or {}
        statut = str(etape.get("statut") or "")
        label, cle = _STATUTS_ETAPE.get(statut, (statut or "—", "en_attente"))
        sorties = etape.get("sorties") or {}
        etapes.append({
            "nom": etape.get("nom") or f"etape_{i}",
            "libelle": etape.get("libelle") or etape.get("nom") or f"Étape {i + 1}",
            "statut_cle": cle,
            "statut_label": label,
            "sorties": sorties,
            "erreur": etape.get("erreur"),
            "avertissement": (
                sorties.get("avertissement")
                if isinstance(sorties, dict) else None
            ),
        })
    nb = len(etapes)
    if run.statut == "termine":
        etape_affichee = f"{nb} / {nb}"
    elif nb:
        etape_affichee = f"{(run.etape_courante or 0) + 1} / {nb}"
    else:
        etape_affichee = "—"
    statut_label, statut_classe = _STATUTS_RUN.get(
        run.statut, (run.statut or "—", "muted")
    )
    resultat = run.resultat if isinstance(run.resultat, dict) else {}
    return {
        "id": run.id,
        "nom": run.nom,
        "type_label": _TYPES_WORKFLOW.get(run.type_workflow, run.type_workflow),
        "statut_cle": run.statut,
        "statut_label": statut_label,
        "statut_classe": statut_classe,
        "etapes": etapes,
        "etape_affichee": etape_affichee,
        "date": _format_date(run.created_at),
        "rapport": resultat.get("rapport"),
        "avertissement_export": resultat.get("avertissement"),
        "simulation_id": resultat.get("simulation_id"),
    }


def _get_run_ou_404(db: Session, project, workflow_id: int):
    """Récupère un run du projet (404 FR si absent ou d'un autre projet)."""
    get_workflow = _load_backend("workflows", "get_workflow")
    try:
        run = get_workflow(db, workflow_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Workflow introuvable (id={workflow_id})."
        ) from exc
    if int(run.project_id) != int(project.id):
        raise HTTPException(
            status_code=404, detail=f"Workflow introuvable (id={workflow_id})."
        )
    return run


@router.get("/workflows", response_class=HTMLResponse)
def workflows_liste(
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Liste des workflows du projet + formulaire de création."""
    project = _project_for(db, ui_key)
    list_workflows = _load_backend("workflows", "list_workflows")
    runs = [_vue_run(run) for run in list_workflows(db, project.id)]
    return _render(
        "workflows.html",
        ui_key=ui_key,
        active="workflows",
        project=project,
        runs=runs,
        moteurs=_moteurs_workflow(),
        form={"type": "analyse_plante", "nom": "", "plant_id": "",
              "compound_id": "", "engine_name": ""},
        erreur=None,
    )


@router.post("/workflows")
async def workflows_creer(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Crée un workflow `analyse_plante` puis redirige vers son détail (303).

    Le backend exige `plant_id` et `compound_id` (entiers) ; `engine_name`
    est optionnel (choix automatique sinon).
    """
    project = _project_for(db, ui_key)
    creer_workflow = _load_backend("workflows", "creer_workflow")
    donnees = await request.form()
    nom = (donnees.get("nom") or "").strip()
    type_workflow = (donnees.get("type") or "analyse_plante").strip()
    plant_id_raw = (donnees.get("plant_id") or "").strip()
    compound_id_raw = (donnees.get("compound_id") or "").strip()
    engine_name = (donnees.get("engine_name") or "").strip() or None

    def _echec(message: str) -> HTMLResponse:
        list_workflows = _load_backend("workflows", "list_workflows")
        runs = [_vue_run(run) for run in list_workflows(db, project.id)]
        return _render(
            "workflows.html",
            ui_key=ui_key,
            active="workflows",
            project=project,
            runs=runs,
            moteurs=_moteurs_workflow(),
            form={"type": type_workflow, "nom": nom,
                  "plant_id": plant_id_raw, "compound_id": compound_id_raw,
                  "engine_name": engine_name or ""},
            erreur=message,
        )

    if not nom:
        return _echec("Donnez un nom au workflow (champ « Nom »).")
    if type_workflow not in _TYPES_WORKFLOW:
        return _echec("Type de workflow inconnu.")
    try:
        plant_id = int(plant_id_raw)
        if plant_id < 1:
            raise ValueError
    except ValueError:
        return _echec("Le n° de plante est requis : un nombre entier positif.")
    try:
        compound_id = int(compound_id_raw)
        if compound_id < 1:
            raise ValueError
    except ValueError:
        return _echec("Le n° de composé est requis : un nombre entier positif.")
    inputs: dict = {"plant_id": plant_id, "compound_id": compound_id}
    if engine_name:
        inputs["engine_name"] = engine_name
    try:
        run = creer_workflow(
            db,
            project_id=project.id,
            type_workflow=type_workflow,
            nom=nom,
            inputs=inputs,
            cree_par=(ui_key.name if ui_key is not None and ui_key.name else None),
        )
    except ValueError as exc:
        return _echec(str(exc))
    db.commit()  # le service ne committe jamais : c'est à la route de le faire
    db.refresh(run)
    return RedirectResponse(url=f"/workflows/{run.id}", status_code=303)


@router.get("/workflows/{workflow_id}", response_class=HTMLResponse)
def workflow_detail(
    workflow_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Pas-à-pas d'un workflow : 7 étapes, sorties, rapport final."""
    project = _project_for(db, ui_key)
    run = _get_run_ou_404(db, project, workflow_id)
    return _render(
        "workflow_detail.html",
        ui_key=ui_key,
        active="workflows",
        project=project,
        run=_vue_run(run),
        erreur=None,
    )


@router.post("/workflows/{workflow_id}/avancer")
def workflow_avancer(
    workflow_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Exécute la prochaine étape non terminée (ou réessaie l'étape
    échouée). En cas d'échec, l'état d'échec est persisté puis réaffiché
    avec le message français du backend — jamais de 500 brut."""
    project = _project_for(db, ui_key)
    run = _get_run_ou_404(db, project, workflow_id)
    avancer_workflow = _load_backend("workflows", "avancer_workflow")
    try:
        avancer_workflow(db, workflow_id)
    except ValueError as exc:
        # Le backend a flushé l'étape échouée AVANT de lever : on persiste
        # pour afficher l'erreur en français sur la page de détail.
        db.commit()
        db.refresh(run)
        return _render(
            "workflow_detail.html",
            ui_key=ui_key,
            active="workflows",
            project=project,
            run=_vue_run(run),
            erreur=str(exc),
        )
    db.commit()
    return RedirectResponse(url=f"/workflows/{workflow_id}", status_code=303)


@router.post("/workflows/{workflow_id}/rejouer")
def workflow_rejouer(
    workflow_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Réinitialise le workflow depuis ses inputs initiaux (même
    `input_hash`), puis redirige vers le détail (303)."""
    project = _project_for(db, ui_key)
    _get_run_ou_404(db, project, workflow_id)
    rejouer_workflow = _load_backend("workflows", "rejouer_workflow")
    try:
        rejouer_workflow(db, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return RedirectResponse(url=f"/workflows/{workflow_id}", status_code=303)

# PHASE 8 — Laboratoire : tableau de bord, journal, publication explicite
# ---------------------------------------------------------------------------
# Backend : `webintel.science.lab` (livré). Conventions du module : session
# en premier argument, jamais de commit côté service, ValueError en français
# pour les cas métier (introuvable, type invalide, déjà publié...).


def _vue_note(note, publie: bool) -> dict:
    """Normalise une note de laboratoire pour les templates."""
    return {
        "id": note.id,
        "titre": note.titre,
        "auteur": note.auteur,
        "contenu": note.contenu,
        "data_level": note.data_level,
        "version_courante": note.version_courante,
        "created_at": _format_date(note.created_at),
        "updated_at": _format_date(note.updated_at),
        "publie": publie,
    }


def _vue_version(version) -> dict:
    """Normalise un instantané de version pour le template."""
    return {
        "version": version.version,
        "titre": version.titre,
        "contenu": version.contenu,
        "cree_le": _format_date(version.cree_le),
    }


def _note_ou_404(db: Session, project, note_id: int):
    """Récupère une note du projet (404 FR si absente ou d'un autre projet)."""
    get_note = _load_backend("lab", "get_note")
    try:
        note = get_note(db, note_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail=f"Note de laboratoire introuvable (id={note_id})."
        ) from exc
    if note is None or int(note.project_id) != int(project.id):
        raise HTTPException(
            status_code=404, detail=f"Note de laboratoire introuvable (id={note_id})."
        )
    return note


def _rendre_note_detail(db: Session, project, ui_key, note, erreur=None, message=None):
    """Rend `note_detail.html` avec historique, statut de publication et trace."""
    est_publie = _load_backend("lab", "est_publie")
    versions_note = _load_backend("lab", "versions_note")
    publie = bool(est_publie(db, "note", note.id))
    versions = [_vue_version(v) for v in versions_note(db, note.id)]
    trace = None
    if publie:
        try:
            lab_mod = __import__("webintel.science.lab", fromlist=["PublicationRecord"])
            trace = (
                db.query(lab_mod.PublicationRecord)
                .filter(
                    lab_mod.PublicationRecord.item_type == "note",
                    lab_mod.PublicationRecord.item_id == note.id,
                )
                .first()
            )
        except ImportError:
            trace = None
    return _render(
        "note_detail.html",
        ui_key=ui_key,
        active="journal",
        project=project,
        note=_vue_note(note, publie),
        publie=publie,
        versions=versions,
        trace_publication={
            "publie_le": _format_date(getattr(trace, "publie_le", None)),
            "publie_par": getattr(trace, "publie_par", None),
            "commentaire": getattr(trace, "commentaire", None),
        } if trace is not None else None,
        erreur=erreur,
        message=message,
    )


@router.get("/laboratoire", response_class=HTMLResponse)
def laboratoire(
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Tableau de bord « Mon laboratoire » : comptes + derniers items."""
    project = _project_for(db, ui_key)
    tableau_de_bord = _load_backend("lab", "tableau_de_bord")
    dash = tableau_de_bord(db, project.id) or {}
    comptes = dash.get("comptes", {}) or {}
    derniers = dash.get("derniers", {}) or {}
    # Le backend lab date d'avant la livraison du module workflows par
    # l'équipe parallèle : sa clé ``comptes["workflows"]`` vaut ``None``.
    # Complément défensif côté UI (sans toucher au backend) via le contrat
    # canonique ``list_workflows(db, project_id)``.
    if comptes.get("workflows") is None:
        try:
            list_workflows = _load_backend("workflows", "list_workflows")
            comptes["workflows"] = len(list_workflows(db, project.id) or [])
        except Exception:
            pass  # le gabarit affiche « — » si le compte reste indisponible
    # Dates lisibles dans les derniers items.
    for groupe in derniers.values():
        for item in groupe or []:
            if isinstance(item, dict) and "created_at" in item:
                item["created_at"] = _format_date(item.get("created_at"))
    return _render(
        "laboratoire.html",
        ui_key=ui_key,
        active="laboratoire",
        project=project,
        comptes=comptes,
        derniers=derniers,
    )


@router.get("/journal", response_class=HTMLResponse)
def journal_liste(
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Journal de laboratoire : liste des notes + formulaire de création."""
    project = _project_for(db, ui_key)
    list_notes = _load_backend("lab", "list_notes")
    est_publie = _load_backend("lab", "est_publie")
    notes = [
        _vue_note(note, bool(est_publie(db, "note", note.id)))
        for note in list_notes(db, project.id)
    ]
    return _render(
        "journal.html",
        ui_key=ui_key,
        active="journal",
        project=project,
        notes=notes,
        form={"titre": "", "contenu": "", "auteur": ""},
        erreur=None,
    )


@router.post("/journal")
async def journal_creer(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Crée une note de laboratoire puis redirige vers sa page (303)."""
    project = _project_for(db, ui_key)
    creer_note = _load_backend("lab", "creer_note")
    donnees = await request.form()
    titre = (donnees.get("titre") or "").strip()
    contenu = (donnees.get("contenu") or "").strip() or None
    auteur = (donnees.get("auteur") or "").strip() or None

    def _echec(message: str) -> HTMLResponse:
        list_notes = _load_backend("lab", "list_notes")
        est_publie = _load_backend("lab", "est_publie")
        notes = [
            _vue_note(note, bool(est_publie(db, "note", note.id)))
            for note in list_notes(db, project.id)
        ]
        return _render(
            "journal.html",
            ui_key=ui_key,
            active="journal",
            project=project,
            notes=notes,
            form={"titre": titre, "contenu": donnees.get("contenu") or "", "auteur": auteur or ""},
            erreur=message,
        )

    if not titre and not contenu:
        return _echec("Donnez au moins un titre ou un contenu à la note.")
    note = creer_note(db, project.id, titre=titre, contenu=contenu, auteur=auteur)
    db.commit()  # le service ne committe jamais : c'est à la route de le faire
    db.refresh(note)
    return RedirectResponse(url=f"/journal/{note.id}", status_code=303)


@router.get("/journal/{note_id}", response_class=HTMLResponse)
def note_detail(
    note_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Note + historique des versions + édition + publication."""
    project = _project_for(db, ui_key)
    note = _note_ou_404(db, project, note_id)
    return _rendre_note_detail(db, project, ui_key, note)


@router.get("/journal/{note_id}/versions", response_class=HTMLResponse)
def note_versions(
    note_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Historique complet des versions d'une note (section #versions de la page)."""
    project = _project_for(db, ui_key)
    note = _note_ou_404(db, project, note_id)
    return _rendre_note_detail(db, project, ui_key, note)


@router.post("/journal/{note_id}")
async def journal_editer(
    note_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Édite une note : crée une NOUVELLE version (jamais de modification
    silencieuse), puis redirige vers la note (303)."""
    project = _project_for(db, ui_key)
    note = _note_ou_404(db, project, note_id)
    est_publie = _load_backend("lab", "est_publie")
    if est_publie(db, "note", note.id):
        return _rendre_note_detail(
            db, project, ui_key, note,
            erreur="Donnée publiée en lecture seule : cette note a été publiée "
                   "et ne peut plus être modifiée.",
        )
    donnees = await request.form()
    titre = (donnees.get("titre") or "").strip() or None
    contenu = donnees.get("contenu")  # None si absent, "" si vidé
    editer_note = _load_backend("lab", "editer_note")
    try:
        note = editer_note(db, note.id, titre=titre, contenu=contenu)
    except ValueError as exc:
        return _rendre_note_detail(db, project, ui_key, note, erreur=str(exc))
    db.commit()
    db.refresh(note)
    return RedirectResponse(url=f"/journal/{note.id}", status_code=303)


@router.post("/laboratoire/publier")
async def laboratoire_publier(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Publication EXPLICITE d'un item (note, observation, simulation).

    Exige la case « Je confirme la publication » : sans elle, rien n'est
    publié. Les erreurs métier du backend sont converties en statuts HTTP
    français : 422 (type invalide), 404 (item introuvable), 403 (autre
    projet), 409 (déjà publié).
    """
    project = _project_for(db, ui_key)
    donnees = await request.form()
    item_type = (donnees.get("item_type") or "").strip()
    item_id_raw = (donnees.get("item_id") or "").strip()
    publie_par = (donnees.get("publie_par") or "").strip() or None
    commentaire = (donnees.get("commentaire") or "").strip() or None

    try:
        item_id = int(item_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="N° d'item invalide.")

    # Confirmation explicite obligatoire.
    if not donnees.get("confirmation"):
        message = "Cochez la case « Je confirme la publication » pour publier cet item."
        if item_type == "note":
            try:
                note = _note_ou_404(db, project, item_id)
            except HTTPException:
                raise HTTPException(status_code=422, detail=message)
            return _rendre_note_detail(db, project, ui_key, note, erreur=message)
        raise HTTPException(status_code=422, detail=message)

    publier_item = _load_backend("lab", "publier_item")
    try:
        publier_item(
            db,
            item_type=item_type,
            item_id=item_id,
            project_id=project.id,
            publie_par=publie_par,
            commentaire=commentaire,
        )
    except ValueError as exc:
        detail = str(exc)
        if "Type d'item invalide" in detail:
            raise HTTPException(status_code=422, detail=detail) from exc
        if "Publication refusée" in detail:
            raise HTTPException(status_code=403, detail=detail) from exc
        if "déjà publié" in detail:
            raise HTTPException(status_code=409, detail=detail) from exc
        raise HTTPException(status_code=404, detail=detail) from exc
    db.commit()
    if item_type == "note":
        return RedirectResponse(url=f"/journal/{item_id}", status_code=303)
    return RedirectResponse(url="/laboratoire", status_code=303)


# ---------------------------------------------------------------------------
# PHASE 9 — Import / Export / Connecteurs (UI)
# ---------------------------------------------------------------------------
# L'import (``/api/science/import``) et les connecteurs
# (``/api/science/connecteurs``) sont livrés par les équipes parallèles
# (contrats HTTP). L'UI les appelle via ``_appel_api_interne`` (ASGI
# interne, sans HTTP externe) : si l'endpoint n'est pas encore monté, la
# page affiche un message français au lieu d'exploser en 500. L'export
# (CSV/JSON, fiche PDF/HTML, dossier ZIP) est implémenté ici via le
# contrat ``webintel.science.export_dossier``.

_TYPES_IMPORT = ("plante", "compose", "lien", "evidence")

_JEUX_EXPORT = ("plantes", "composes", "evidences")


async def _appel_api_interne(
    request: Request, methode: str, chemin: str, **kwargs
):
    """Appelle l'API de l'application elle-même (transport ASGI interne).

    La clé API de la requête entrante est retransmise si présente.
    Retourne ``(reponse, None)`` ou ``(None, message_erreur_fr)`` :
    l'appelant affiche le message en français, jamais de 500 brut.
    """
    import httpx
    import webintel.main

    headers = {}
    cle = request.headers.get("X-API-Key")
    if cle:
        headers["X-API-Key"] = cle
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=webintel.main.app),
            base_url="http://interne",
        ) as client:
            reponse = await client.request(
                methode, chemin, headers=headers,
                cookies=request.cookies, **kwargs
            )
    except Exception as exc:  # pragma: no cover — garde-fou réseau
        return None, f"Appel interne impossible : {exc}."
    return reponse, None


def _detail_erreur_api(reponse) -> str:
    """Extrait un message français d'une réponse d'erreur API."""
    try:
        detail = reponse.json().get("detail")
    except Exception:
        detail = None
    return str(detail or "erreur inconnue")


# ---------------------------------------------------------------------------
# /import — formulaire d'upload + affichage du rapport
# ---------------------------------------------------------------------------

@router.get("/import", response_class=HTMLResponse)
def import_page(
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Formulaire d'import : fichier + type de données (plante, composé,
    lien, preuve)."""
    project = _project_for(db, ui_key)
    return _render(
        "import.html",
        ui_key=ui_key,
        active="import",
        project=project,
        rapport=None,
        erreur=None,
        type_donnees="plante",
    )


@router.post("/import", response_class=HTMLResponse)
async def import_executer(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Transmet le fichier au contrat ``POST /api/science/import``
    (multipart ``fichier`` + ``type_donnees``) et affiche le rapport
    (créés / mis à jour / ignorés + erreurs ligne par ligne)."""
    project = _project_for(db, ui_key)
    donnees = await request.form()
    type_donnees = (donnees.get("type_donnees") or "").strip()
    fichier = donnees.get("fichier")

    def _echec(message: str) -> HTMLResponse:
        return _render(
            "import.html",
            ui_key=ui_key,
            active="import",
            project=project,
            rapport=None,
            erreur=message,
            type_donnees=type_donnees or "plante",
        )

    if type_donnees not in _TYPES_IMPORT:
        return _echec(
            "Type de données invalide : choisissez plante, composé, "
            "lien ou preuve."
        )
    nom_fichier = getattr(fichier, "filename", "") or ""
    if not nom_fichier:
        return _echec("Choisissez un fichier à importer.")
    contenu = await fichier.read()
    if not contenu:
        return _echec("Le fichier est vide : rien à importer.")

    reponse, erreur_appel = await _appel_api_interne(
        request,
        "POST",
        "/api/science/import",
        files={
            "fichier": (
                nom_fichier,
                contenu,
                getattr(fichier, "content_type", None) or "application/octet-stream",
            )
        },
        data={"type_donnees": type_donnees},
    )
    if erreur_appel is not None:
        return _echec(erreur_appel)
    if reponse.status_code == 404:
        return _echec("Module d'import en cours d'intégration — réessayez plus tard.")
    if reponse.status_code != 200:
        return _echec(
            f"Import refusé ({reponse.status_code}) : "
            f"{_detail_erreur_api(reponse)}."
        )
    try:
        rapport = reponse.json().get("rapport") or {}
    except Exception:
        return _echec("Réponse d'import illisible.")
    return _render(
        "import.html",
        ui_key=ui_key,
        active="import",
        project=project,
        rapport=rapport,
        erreur=None,
        type_donnees=type_donnees,
    )


# ---------------------------------------------------------------------------
# /export — cases à cocher, liens de téléchargement, dossier ZIP
# ---------------------------------------------------------------------------

@router.get("/export", response_class=HTMLResponse)
def export_page(
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Page d'export : sélection des jeux (ZIP), fiche plante, dossier de
    recherche. La case « données privées » est décochée par défaut, avec
    avertissement explicite."""
    project = _project_for(db, ui_key)
    return _render(
        "export.html",
        ui_key=ui_key,
        active="export",
        project=project,
        erreur=None,
        message=None,
    )


@router.post("/export/lot")
async def export_lot(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """ZIP des jeux de données cochés (plantes / composés / preuves),
    au format choisi (CSV ou JSON). Données publiques de référence."""
    import io
    import zipfile

    from webintel.science import export_dossier as export_mod

    project = _project_for(db, ui_key)
    donnees = await request.form()
    jeux = [j for j in donnees.getlist("jeux") if j in _JEUX_EXPORT]
    format_choisi = (donnees.get("format") or "csv").strip().lower()
    if format_choisi not in ("csv", "json"):
        format_choisi = "csv"

    def _echec(message: str) -> HTMLResponse:
        return _render(
            "export.html",
            ui_key=ui_key,
            active="export",
            project=project,
            erreur=message,
            message=None,
        )

    if not jeux:
        return _echec("Cochez au moins un jeu de données à exporter.")

    constructeurs = {
        "plantes": (export_mod.lignes_plantes, export_mod._COLONNES_PLANTES),
        "composes": (export_mod.lignes_composes, export_mod._COLONNES_COMPOSES),
        "evidences": (export_mod.lignes_evidences, export_mod._COLONNES_EVIDENCES),
    }
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        for jeu in jeux:
            construire, colonnes = constructeurs[jeu]
            lignes = construire(db)
            if format_choisi == "csv":
                contenu = export_mod.export_csv(lignes, colonnes)
                nom = f"webintel_{jeu}.csv"
            else:
                import json

                contenu = json.dumps(lignes, ensure_ascii=False, indent=2)
                nom = f"webintel_{jeu}.json"
            archive.writestr(nom, contenu)
    return StreamingResponse(
        io.BytesIO(tampon.getvalue()),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="webintel_export.zip"'},
    )


@router.post("/export/fiche")
async def export_fiche_ui(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Télécharge la fiche d'une plante (vrai PDF ou HTML imprimable)."""
    import io

    from webintel.science import export_dossier as export_mod

    project = _project_for(db, ui_key)
    donnees = await request.form()

    def _echec(message: str) -> HTMLResponse:
        return _render(
            "export.html",
            ui_key=ui_key,
            active="export",
            project=project,
            erreur=message,
            message=None,
        )

    try:
        plant_id = int((donnees.get("plant_id") or "").strip())
        if plant_id < 1:
            raise ValueError
    except (ValueError, AttributeError):
        return _echec("N° de plante invalide : un nombre entier positif.")
    format_choisi = (donnees.get("format") or "pdf").strip().lower()
    try:
        contenu, type_mime, nom = export_mod.exporter_fiche(
            db, plant_id, format=format_choisi
        )
    except ValueError as exc:
        return _echec(str(exc))
    except KeyError:
        return _echec(f"Plante introuvable (n° {plant_id}).")
    return StreamingResponse(
        io.BytesIO(contenu),
        media_type=type_mime,
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )


@router.post("/export/dossier")
async def export_dossier_ui(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Télécharge le dossier de recherche du projet (ZIP).

    La case « inclure les données privées » est un acte explicite de
    l'utilisateur sur SON projet (déjà cloisonné par ``_project_for``).
    """
    import io

    from webintel.science import export_dossier as export_mod

    project = _project_for(db, ui_key)
    donnees = await request.form()
    archive = export_mod.build_dossier_zip(
        db,
        project.id,
        inclure_prive=bool(donnees.get("inclure_prive")),
        inclure_simulations=bool(donnees.get("inclure_simulations")),
    )
    return StreamingResponse(
        io.BytesIO(archive),
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="dossier_recherche_projet_{project.id}.zip"'
        },
    )


# ---------------------------------------------------------------------------
# /connecteurs — cartes des connecteurs + bouton d'import
# ---------------------------------------------------------------------------

def _rendre_connecteurs(
    ui_key, project, connecteurs, erreur=None, message=None
) -> HTMLResponse:
    """Rend ``connecteurs.html`` (liste éventuellement indisponible)."""
    return _render(
        "connecteurs.html",
        ui_key=ui_key,
        active="connecteurs",
        project=project,
        connecteurs=connecteurs,
        erreur=erreur,
        message=message,
    )


async def _liste_connecteurs(request: Request):
    """Récupère la liste des connecteurs (contrat de l'équipe parallèle).

    Retourne ``(connecteurs, erreur)`` : ``connecteurs`` vaut ``None`` si
    l'endpoint n'est pas encore monté (message « en cours d'intégration »
    affiché par le template).
    """
    reponse, erreur_appel = await _appel_api_interne(
        request, "GET", "/api/science/connecteurs"
    )
    if erreur_appel is not None:
        return None, erreur_appel
    if reponse.status_code == 404:
        return None, None
    if reponse.status_code != 200:
        return None, (
            f"Connecteurs indisponibles ({reponse.status_code}) : "
            f"{_detail_erreur_api(reponse)}."
        )
    try:
        return reponse.json().get("connecteurs") or [], None
    except Exception:
        return None, "Réponse des connecteurs illisible."


@router.get("/connecteurs", response_class=HTMLResponse)
async def connecteurs_page(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Liste des connecteurs en cartes (licence, statut, stratégie de
    mise à jour) avec bouton « Importer » par connecteur actif."""
    project = _project_for(db, ui_key)
    connecteurs, erreur = await _liste_connecteurs(request)
    return _rendre_connecteurs(ui_key, project, connecteurs, erreur=erreur)


@router.post("/connecteurs/{nom}/importer")
async def connecteur_importer(
    nom: str,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Déclenche l'import d'un connecteur (``POST
    /api/science/connecteurs/{nom}/importer``) puis réaffiche la liste
    avec le résultat."""
    project = _project_for(db, ui_key)
    reponse, erreur_appel = await _appel_api_interne(
        request, "POST", f"/api/science/connecteurs/{nom}/importer"
    )
    if erreur_appel is not None:
        message, erreur = None, erreur_appel
    elif reponse.status_code == 404:
        message, erreur = (
            None, "Module des connecteurs en cours d'intégration — réessayez plus tard."
        )
    elif reponse.status_code != 200:
        message, erreur = (
            None,
            f"Import refusé ({reponse.status_code}) : {_detail_erreur_api(reponse)}.",
        )
    else:
        try:
            corps = reponse.json()
            resume = corps.get("message") or corps.get("resume") or "Import terminé."
        except Exception:
            resume = "Import terminé."
        message, erreur = f"Connecteur « {nom} » : {resume}", None
    connecteurs, erreur_liste = await _liste_connecteurs(request)
    return _rendre_connecteurs(
        ui_key, project, connecteurs,
        erreur=erreur or erreur_liste, message=message,
    )


# ---------------------------------------------------------------------------
# /qualite — Contrôle qualité des données (v2.2, lecture seule)
# ---------------------------------------------------------------------------


@router.get("/qualite", response_class=HTMLResponse)
async def qualite_page(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Contrôle qualité : anomalies, sévérités, actions recommandées.

    Lecture seule (aucune correction automatique) : le chercheur examine
    les anomalies et décide. Appelle l'API interne ``/api/science/qualite``
    pour rester sur les contrats HTTP.
    """
    project = _project_for(db, ui_key)
    reponse, erreur_appel = await _appel_api_interne(
        request, "GET", "/api/science/qualite?limite=100"
    )
    resume, anomalies, rappel = {}, [], None
    if erreur_appel is not None:
        erreur = erreur_appel
    elif reponse.status_code != 200:
        erreur = f"Contrôle qualité indisponible ({reponse.status_code}) : " f"{_detail_erreur_api(reponse)}."
    else:
        try:
            corps = reponse.json()
            resume = corps.get("resume") or {}
            anomalies = corps.get("anomalies") or []
            rappel = corps.get("rappel")
            erreur = None
        except Exception:
            erreur = "Réponse du contrôle qualité illisible."
    return _render(
        "qualite.html",
        ui_key,
        active="qualite",
        project=project,
        resume=resume,
        anomalies=anomalies,
        rappel=rappel,
        erreur=erreur,
    )
