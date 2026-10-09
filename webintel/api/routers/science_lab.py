"""Routeur API laboratoire / projet — Phase 8 (backend, pas d'UI).

- Notes de laboratoire : création, liste, détail, édition versionnée
  (une édition crée toujours une nouvelle ``LabNoteVersion`` — jamais de
  modification silencieuse), historique des versions.
- Publication explicite : ``POST /science/lab/publier`` crée une
  ``PublicationRecord`` (traçabilité). Un item publié devient en lecture
  seule (modification → 409).
- Modifications gardées : ``PATCH /science/lab/observations/{id}`` et
  ``PATCH /science/lab/simulations/{id}`` refusent (409) les items publiés.
- Tableau de bord : ``GET /science/lab/tableau-de-bord``.

Authentification : ``api_key_auth`` sur chaque route (401 sans clé en mode
serveur). Cloisonnement au projet via ``scoped_project_id`` /
``require_project_access`` (403 vers un projet étranger, même pattern que
``routers/science.py``). Erreurs en français : 404, 422, 409.

NOTE d'enregistrement : ce routeur n'est PAS branché ici — le coordinateur
l'enregistre dans ``webintel/main.py`` (préfixe ``/api``, tags
``["science"]``), à côté des autres routeurs scientifiques.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    require_project_access,
    require_project_access_strict,
    scoped_project_id,
)
from webintel.science import lab as lab_service
from webintel.science.models import ResearchObservation, Simulation
from webintel.science.schemas import (
    ResearchObservationOut,
    SimulationOut,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schémas
# ---------------------------------------------------------------------------

class _OrmOut(BaseModel):
    """Base des schémas de sortie (construction depuis un objet ORM)."""

    model_config = ConfigDict(from_attributes=True)


class LabNoteCreate(BaseModel):
    """Création d'une note de laboratoire (privée au projet par défaut)."""

    project_id: int | None = Field(
        default=None,
        description="Projet du laboratoire (cloisonné par la clé API).",
    )
    titre: str = Field(min_length=1, max_length=255)
    contenu: str | None = None
    auteur: str | None = Field(default=None, max_length=255)
    pieces_jointes: list[str] = Field(
        default_factory=list,
        description="Chemins des pièces jointes (relatifs au dossier du projet).",
    )


class LabNoteUpdate(BaseModel):
    """Édition d'une note : crée toujours une nouvelle version."""

    titre: str | None = Field(default=None, min_length=1, max_length=255)
    contenu: str | None = None
    pieces_jointes: list[str] | None = None


class LabNoteOut(_OrmOut):
    """Note de laboratoire exposée : donnée expérimentale (privée par défaut)."""

    id: int
    project_id: int
    auteur: str | None
    titre: str
    contenu: str | None
    pieces_jointes: list
    data_level: str
    version_courante: int
    publie: bool = Field(
        default=False,
        description="True si la note a une trace de publication (lecture seule).",
    )
    created_at: datetime
    updated_at: datetime


class LabNoteVersionOut(_OrmOut):
    """Instantané immuable d'une version antérieure d'une note."""

    id: int
    note_id: int
    version: int
    titre: str
    contenu: str | None
    pieces_jointes: list
    cree_le: datetime


class ObservationPatch(BaseModel):
    """Champs modifiables d'une observation (item non publié uniquement)."""

    protocol_ref: str | None = Field(default=None, max_length=255)
    author: str | None = Field(default=None, max_length=255)
    laboratory: str | None = Field(default=None, max_length=255)
    raw_data: dict[str, Any] | None = None
    file_path: str | None = None
    status: str | None = Field(default=None, max_length=32)
    comment: str | None = None
    is_private: bool | None = None


class SimulationPatch(BaseModel):
    """Champs modifiables d'une simulation (item non publié uniquement).

    Les champs du journal d'audit (``input_hash``, ``inputs``,
    ``parameters``, ``result``, ``engine_name``, ``engine_version``,
    ``run_at``, ``executed_by``) sont EXCLUS : une exécution enregistrée
    est immuable (append-only, Phase 13).
    """
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    target: str | None = None
    hypotheses: str | None = None
    method: str | None = None
    units: str | None = Field(default=None, max_length=64)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    references: list[str] | None = None
    status: str | None = Field(default=None, max_length=32)


class PublicationCreate(BaseModel):
    """Demande de publication EXPLICITE d'un item de laboratoire."""

    item_type: str = Field(
        description="Type d'item : « observation », « simulation » ou « note ».",
    )
    item_id: int = Field(gt=0)
    commentaire: str | None = None
    publie_par: str | None = Field(
        default=None,
        max_length=255,
        description="Nom du publiant (défaut : nom de la clé API, mode serveur).",
    )


class PublicationRecordOut(_OrmOut):
    """Trace de publication explicite (traçabilité)."""

    id: int
    item_type: str
    item_id: int
    project_id: int
    publie_par: str | None
    publie_le: datetime
    commentaire: str | None


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _conflit_409(exc: ValueError) -> HTTPException:
    """Convertit une `ValueError` du service en erreur 409 (message FR)."""
    detail = str(exc).strip()
    if not detail:
        detail = "Conflit : cette ressource existe déjà ou les données sont incohérentes."
    return HTTPException(status_code=409, detail=detail)


def _erreur_modification(exc: ValueError) -> HTTPException:
    """404 si l'item est introuvable, 409 s'il est publié (lecture seule)."""
    if "introuvable" in str(exc):
        return HTTPException(status_code=404, detail=str(exc))
    return _conflit_409(exc)


def _note_out(db: Session, note: Any) -> LabNoteOut:
    """Construit la sortie d'une note (avec son statut de publication)."""
    sortie = LabNoteOut.model_validate(note)
    sortie.publie = lab_service.est_publie(db, "note", note.id)
    return sortie


def _note_ou_404(db: Session, note_id: int, project_id: int | None) -> Any:
    """Note du projet (ou 404) — 403 déjà appliquée via `project_id` cloisonné."""
    note = lab_service.get_note(db, note_id)
    if note is None or (project_id is not None and note.project_id != project_id):
        raise HTTPException(
            status_code=404,
            detail=f"Note de laboratoire introuvable : id={note_id}.",
        )
    return note


# ---------------------------------------------------------------------------
# Notes de laboratoire : CRUD versionné
# ---------------------------------------------------------------------------

@router.post("/science/lab/notes", response_model=LabNoteOut, status_code=201)
def creer_note(
    payload: LabNoteCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> LabNoteOut:
    """Crée une note de laboratoire (version 1, privée au projet).

    Le `project_id` est résolu par `scoped_project_id` (cloisonnement par
    défaut sur le projet de la clé API). Le `data_level` exposé est toujours
    `experimental` : une note de laboratoire est une trace de mesure, jamais
    une donnée publiée.
    """
    project_id = scoped_project_id(api_key, payload.project_id)
    if project_id is None:
        raise HTTPException(
            status_code=400,
            detail="Le champ project_id est requis pour créer une note de laboratoire.",
        )
    note = lab_service.creer_note(
        db,
        project_id,
        titre=payload.titre,
        contenu=payload.contenu,
        auteur=payload.auteur,
        pieces_jointes=payload.pieces_jointes,
    )
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(note)
    return _note_out(db, note)


@router.get("/science/lab/notes", response_model=list[LabNoteOut])
def lister_notes(
    project_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[LabNoteOut]:
    """Liste les notes de laboratoire du projet autorisé (antichronologique).

    Sans `project_id`, le projet de la clé API est utilisé (cloisonnement).
    `require_project_access` est appliqué sur le projet demandé.
    """
    effectif = scoped_project_id(api_key, project_id)
    if effectif is not None:
        require_project_access(api_key, effectif)
    notes = lab_service.list_notes(db, effectif, limit=limit, offset=offset)
    return [_note_out(db, note) for note in notes]


@router.get("/science/lab/notes/{note_id}", response_model=LabNoteOut)
def lire_note(
    note_id: int,
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> LabNoteOut:
    """Détail d'une note (404 si absente ; 403 si projet étranger)."""
    effectif = scoped_project_id(api_key, project_id)
    if effectif is not None:
        require_project_access(api_key, effectif)
    return _note_out(db, _note_ou_404(db, note_id, effectif))


@router.put("/science/lab/notes/{note_id}", response_model=LabNoteOut)
def editer_note(
    note_id: int,
    payload: LabNoteUpdate,
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> LabNoteOut:
    """Édite une note : crée toujours une nouvelle version (historique gardé).

    Une note publiée est en lecture seule → 409. Jamais de modification
    silencieuse : l'ancien contenu est conservé dans `LabNoteVersion`.
    """
    effectif = scoped_project_id(api_key, project_id)
    if effectif is not None:
        require_project_access(api_key, effectif)
    note = _note_ou_404(db, note_id, effectif)
    if lab_service.est_publie(db, "note", note.id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Donnée publiée en lecture seule : cette note a été publiée "
                "et ne peut plus être modifiée."
            ),
        )
    donnees = payload.model_dump(exclude_unset=True)
    if not donnees:
        raise HTTPException(
            status_code=422,
            detail="Aucun champ à modifier : fournissez titre, contenu ou pieces_jointes.",
        )
    try:
        note = lab_service.editer_note(db, note.id, **donnees)
    except ValueError as exc:
        raise _erreur_modification(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(note)
    return _note_out(db, note)


@router.get(
    "/science/lab/notes/{note_id}/versions",
    response_model=list[LabNoteVersionOut],
)
def historique_note(
    note_id: int,
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[LabNoteVersionOut]:
    """Historique des versions d'une note (instantanés, ordre croissant)."""
    effectif = scoped_project_id(api_key, project_id)
    if effectif is not None:
        require_project_access(api_key, effectif)
    _note_ou_404(db, note_id, effectif)  # 404 / 403 avant de lire l'historique
    versions = lab_service.versions_note(db, note_id)
    return [LabNoteVersionOut.model_validate(v) for v in versions]


# ---------------------------------------------------------------------------
# Publication explicite
# ---------------------------------------------------------------------------

@router.post(
    "/science/lab/publier",
    response_model=PublicationRecordOut,
    status_code=201,
)
def publier(
    payload: PublicationCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> PublicationRecordOut:
    """Publie EXPLICITEMENT un item de laboratoire (jamais automatique).

    Crée une `PublicationRecord` (traçabilité : qui, quand, depuis quel
    projet, commentaire). L'item publié devient en lecture seule.

    - `item_type` invalide → 422 ;
    - item introuvable → 404 ;
    - item d'un autre projet → 403 ;
    - item déjà publié → 409.
    """
    if payload.item_type not in lab_service.TYPES_PUBLIABLES:
        raise HTTPException(
            status_code=422,
            detail=(
                "Type d'item invalide : « "
                + payload.item_type
                + " » (attendu : "
                + ", ".join(lab_service.TYPES_PUBLIABLES)
                + ")."
            ),
        )
    # Le projet source est celui de l'item lui-même (pas celui de la clé :
    # en mode local il n'y a pas de clé). 404 si l'item est introuvable.
    try:
        item = lab_service.get_item(db, payload.item_type, payload.item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    projet_source = getattr(item, "project_id", None)
    if projet_source is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Publication impossible : cet item n'est rattaché à aucun "
                "projet (seuls les items de laboratoire rattachés à un "
                "projet sont publiables)."
            ),
        )
    # 403 en mode serveur si la clé n'est pas rattachée au projet source.
    require_project_access(api_key, projet_source)
    try:
        record = lab_service.publier_item(
            db,
            item_type=payload.item_type,
            item_id=payload.item_id,
            project_id=projet_source,
            publie_par=payload.publie_par
            or (api_key.name if api_key is not None else "mode local"),
            commentaire=payload.commentaire,
        )
    except ValueError as exc:
        raise _conflit_409(exc)  # déjà publié → 409
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(record)
    return PublicationRecordOut.model_validate(record)


# ---------------------------------------------------------------------------
# Modifications gardées (items publiés en lecture seule)
# ---------------------------------------------------------------------------

@router.patch(
    "/science/lab/observations/{observation_id}",
    response_model=ResearchObservationOut,
)
def modifier_observation(
    observation_id: int,
    payload: ObservationPatch,
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> ResearchObservationOut:
    """Modifie une observation de laboratoire (item non publié uniquement).

    Une observation publiée est en lecture seule → 409. Les items non
    publiés restent modifiables normalement. 404 si l'observation est
    absente, 403 si elle appartient à un autre projet.
    """
    effectif = scoped_project_id(api_key, project_id)
    if effectif is not None:
        require_project_access(api_key, effectif)
    donnees = payload.model_dump(exclude_unset=True)
    if not donnees:
        raise HTTPException(
            status_code=422,
            detail="Aucun champ à modifier.",
        )
    # P0-1 : contrôle d'accès AVANT toute modification — une observation
    # d'un autre projet ne doit même pas être chargée en écriture.
    observation = db.get(ResearchObservation, observation_id)
    if observation is None:
        raise HTTPException(
            status_code=404,
            detail=f"Observation introuvable : id={observation_id}.",
        )
    if effectif is not None and observation.project_id != effectif:
        raise HTTPException(
            status_code=404,
            detail=f"Observation introuvable : id={observation_id}.",
        )
    require_project_access_strict(api_key, observation.project_id)
    try:
        observation = lab_service.modifier_observation(
            db, observation_id, **donnees
        )
    except ValueError as exc:
        raise _erreur_modification(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(observation)
    return ResearchObservationOut.model_validate(observation)


@router.patch(
    "/science/lab/simulations/{simulation_id}",
    response_model=SimulationOut,
)
def modifier_simulation(
    simulation_id: int,
    payload: SimulationPatch,
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> SimulationOut:
    """Modifie une simulation (item non publié uniquement).

    Une simulation publiée est en lecture seule → 409. Les items non
    publiés restent modifiables normalement. 404 si la simulation est
    absente, 403 si elle appartient à un autre projet.
    """
    effectif = scoped_project_id(api_key, project_id)
    if effectif is not None:
        require_project_access(api_key, effectif)
    donnees = payload.model_dump(exclude_unset=True)
    if not donnees:
        raise HTTPException(
            status_code=422,
            detail="Aucun champ à modifier.",
        )
    # P0-1 : contrôle d'accès AVANT toute modification — une simulation
    # d'un autre projet ne doit même pas être chargée en écriture.
    # Même une éventuelle simulation orpheline antérieure au
    # durcissement est refusée (403 en mode serveur), jamais ouverte.
    simulation = db.get(Simulation, simulation_id)
    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail=f"Simulation introuvable : id={simulation_id}.",
        )
    if effectif is not None and simulation.project_id != effectif:
        raise HTTPException(
            status_code=404,
            detail=f"Simulation introuvable : id={simulation_id}.",
        )
    require_project_access_strict(api_key, simulation.project_id)
    try:
        simulation = lab_service.modifier_simulation(db, simulation_id, **donnees)
    except ValueError as exc:
        raise _erreur_modification(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(simulation)
    return SimulationOut.model_validate(simulation)


# ---------------------------------------------------------------------------
# Tableau de bord
# ---------------------------------------------------------------------------

@router.get("/science/lab/tableau-de-bord")
def tableau_de_bord(
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Tableau de bord du laboratoire pour le projet autorisé.

    Comptes (observations, simulations, notes, publications, composés et
    plantes distincts étudiés) + 5 derniers items de chaque type, chacun
    avec son `data_level`.
    """
    effectif = scoped_project_id(api_key, project_id)
    if effectif is None:
        raise HTTPException(
            status_code=400,
            detail="Le projet est requis pour afficher le tableau de bord.",
        )
    require_project_access(api_key, effectif)
    return lab_service.tableau_de_bord(db, effectif)
