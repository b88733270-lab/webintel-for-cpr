"""Routeur API : modèle scientifique enrichi (v2.1).

Endpoints (tous sous ``/api`` via ``main.py``) :

- **Études structurées** (public) : ``POST /science/studies``,
  ``GET /science/studies``, ``GET /science/studies/{study_id}``
  (idempotent sur le DOI) ;
- **Cibles documentées d'un composé** (public) :
  ``POST /science/compounds/{compound_id}/targets``,
  ``GET /science/compounds/{compound_id}/targets`` — chaque cible pointe
  vers sa preuve (``evidence_id`` → ``Evidence``), exposée en ``preuve`` ;
- **Activités pharmacologiques d'une plante** (public) :
  ``POST /science/plants/{plant_id}/activities``,
  ``GET /science/plants/{plant_id}/activities`` — idem, preuve pointée ;
- **Preuve d'un lien plante-composé** (public) :
  ``POST /science/plant-compounds/{link_id}/preuve``
  (``{"evidence_id": ...}``) ;
- **Provenance** (public, journal append-only) :
  ``POST /science/provenance``, ``GET /science/provenance`` ;
- **Jeux de données du chercheur** (privé, cloisonné au projet) :
  ``POST /science/datasets``, ``GET /science/datasets``
  (privé par défaut, ``is_private=True``).

Discipline : les cibles et activités sont des données RAPPORTÉES
(``data_level=p publie`` par défaut) — jamais « efficaces », « sûres »
ou « traitant » quoi que ce soit. Les jeux de données sont privés par
défaut et cloisonnés au projet (même pattern que
``routers/science.py``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    require_project_access,
    scoped_project_id,
)
from webintel.science import service as science_service
from webintel.science.models import (
    Compound,
    CompoundTarget,
    Dataset,
    Evidence,
    Plant,
    PlantActivity,
    PlantCompound,
    Provenance,
    Study,
)
from webintel.science.schemas import (
    CompoundTargetCreate,
    CompoundTargetOut,
    DatasetCreate,
    DatasetOut,
    PlantActivityCreate,
    PlantActivityOut,
    ProvenanceCreate,
    ProvenanceOut,
    StudyCreate,
    StudyOut,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _conflit_409(exc: ValueError) -> HTTPException:
    """Convertit une `ValueError` du service en erreur 409 (message FR)."""
    detail = str(exc).strip()
    if not detail:
        detail = "Conflit : cette ressource existe déjà ou les données sont incohérentes."
    return HTTPException(status_code=409, detail=detail)


def _plante_ou_404(db: Session, plant_id: int) -> Plant:
    plant = db.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(
            status_code=404, detail=f"Plante introuvable : id={plant_id}."
        )
    return plant


def _compose_ou_404(db: Session, compound_id: int) -> Compound:
    compound = db.get(Compound, compound_id)
    if compound is None:
        raise HTTPException(
            status_code=404, detail=f"Composé introuvable : id={compound_id}."
        )
    return compound


def _preuve_ou_404(db: Session, evidence_id: int) -> Evidence:
    preuve = db.get(Evidence, evidence_id)
    if preuve is None:
        raise HTTPException(
            status_code=404, detail=f"Preuve introuvable : id={evidence_id}."
        )
    return preuve


def _preuve_dict(db: Session, evidence_id: int | None) -> dict | None:
    """Preuve pointée sous forme de dictionnaire (None si absente)."""
    if evidence_id is None:
        return None
    preuve = db.get(Evidence, evidence_id)
    if preuve is None:
        return None
    return {
        "id": preuve.id,
        "source_type": preuve.source_type,
        "reference": preuve.reference,
        "doi": preuve.doi,
        "pmid": preuve.pmid,
        "url": preuve.url,
        "title": preuve.title,
        "authors": list(preuve.authors or []),
        "year": preuve.year,
    }


def _avec_preuve(db: Session, obj, schema_out):
    """Valide `obj` avec `schema_out` et joint sa preuve pointée."""
    valide = schema_out.model_validate(obj)
    donnees = valide.model_dump()
    donnees["preuve"] = _preuve_dict(db, getattr(obj, "evidence_id", None))
    return donnees


# ---------------------------------------------------------------------------
# Études structurées (public)
# ---------------------------------------------------------------------------

@router.post("/science/studies", response_model=StudyOut, status_code=201)
def create_study(
    payload: StudyCreate,
    response: Response,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> StudyOut:
    """Crée une étude / publication structurée.

    Idempotent sur le DOI : si une étude avec ce DOI existe déjà, retourne
    l'existante (200), sinon 201. Sans DOI, l'étude est toujours créée.
    """
    data = payload.model_dump(exclude_unset=True)
    doi = data.pop("doi", None)
    try:
        obj, created = science_service.create_study(db, doi=doi, **data)
    except ValueError as exc:
        raise _conflit_409(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(obj)
    response.status_code = 201 if created else 200
    return StudyOut.model_validate(obj)


@router.get("/science/studies", response_model=list[StudyOut])
def list_studies(
    doi: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[StudyOut]:
    """Liste les études structurées, filtrables par DOI exact ou recherche
    plein-texte sur le titre (`?q=...`)."""
    query = db.query(Study)
    if doi:
        query = query.filter(Study.doi == doi)
    if q:
        motif = f"%{q.strip()}%"
        query = query.filter(Study.title.ilike(motif))
    rows = query.order_by(Study.id.desc()).limit(limit).all()
    return [StudyOut.model_validate(r) for r in rows]


@router.get("/science/studies/{study_id}", response_model=StudyOut)
def get_study(
    study_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> StudyOut:
    """Détail d'une étude structurée (404 si absente)."""
    obj = db.get(Study, study_id)
    if obj is None:
        raise HTTPException(
            status_code=404, detail=f"Étude introuvable : id={study_id}."
        )
    return StudyOut.model_validate(obj)


# ---------------------------------------------------------------------------
# Cibles documentées d'un composé (public)
# ---------------------------------------------------------------------------

@router.post(
    "/science/compounds/{compound_id}/targets",
    response_model=CompoundTargetOut,
    status_code=201,
)
def link_compound_target(
    compound_id: int,
    payload: CompoundTargetCreate,
    response: Response,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Documente une cible biologique d'un composé (interaction sourcée).

    Idempotent : si la cible est déjà documentée pour ce composé (même
    nom, même type d'interaction), retourne l'existante (200), sinon 201.
    404 si le composé ou la preuve (`evidence_id`) est absent. La preuve
    pointée est exposée dans la réponse (`preuve`).
    """
    _compose_ou_404(db, compound_id)
    data = payload.model_dump(exclude_unset=True)
    body_compound_id = data.pop("compound_id", None)
    if body_compound_id is not None and int(body_compound_id) != int(compound_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "Incohérence : le compound_id du corps "
                f"({body_compound_id}) diffère de celui du chemin ({compound_id})."
            ),
        )
    evidence_id = data.pop("evidence_id", None)
    if evidence_id is not None:
        _preuve_ou_404(db, evidence_id)
    target_name = data.pop("target_name")
    interaction_type = data.pop("interaction_type", None)
    try:
        obj, created = science_service.link_compound_target(
            db,
            compound_id,
            target_name,
            interaction_type=interaction_type,
            evidence_id=evidence_id,
            **data,
        )
    except ValueError as exc:
        raise _conflit_409(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(obj)
    response.status_code = 201 if created else 200
    return _avec_preuve(db, obj, CompoundTargetOut)


@router.get(
    "/science/compounds/{compound_id}/targets",
    response_model=list[CompoundTargetOut],
)
def list_compound_targets(
    compound_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[dict]:
    """Liste les cibles documentées d'un composé (404 si absent).

    Chaque cible expose sa preuve pointée (`preuve`) : la chaîne
    composé → cible → publication → DOI est traçable.
    """
    _compose_ou_404(db, compound_id)
    rows = (
        db.query(CompoundTarget)
        .filter(CompoundTarget.compound_id == compound_id)
        .order_by(CompoundTarget.id)
        .limit(limit)
        .all()
    )
    return [_avec_preuve(db, r, CompoundTargetOut) for r in rows]


# ---------------------------------------------------------------------------
# Activités pharmacologiques d'une plante (public)
# ---------------------------------------------------------------------------

@router.post(
    "/science/plants/{plant_id}/activities",
    response_model=PlantActivityOut,
    status_code=201,
)
def add_plant_activity(
    plant_id: int,
    payload: PlantActivityCreate,
    response: Response,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Enregistre une activité pharmacologique rapportée pour une plante.

    Donnée RAPPORTÉE (modèle et type d'étude exigés pour la traçabilité) :
    jamais présentée comme « efficace » ni « traitant » quoi que ce soit.
    Idempotent : retourne l'existante (200) si déjà enregistrée, sinon 201.
    404 si la plante ou la preuve (`evidence_id`) est absente.
    """
    _plante_ou_404(db, plant_id)
    data = payload.model_dump(exclude_unset=True)
    body_plant_id = data.pop("plant_id", None)
    if body_plant_id is not None and int(body_plant_id) != int(plant_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "Incohérence : le plant_id du corps "
                f"({body_plant_id}) diffère de celui du chemin ({plant_id})."
            ),
        )
    evidence_id = data.pop("evidence_id", None)
    if evidence_id is not None:
        _preuve_ou_404(db, evidence_id)
    activity = data.pop("activity")
    study_type = data.pop("study_type", None)
    study_model = data.pop("study_model", None)
    try:
        obj, created = science_service.add_plant_activity(
            db,
            plant_id,
            activity,
            study_type=study_type,
            study_model=study_model,
            evidence_id=evidence_id,
            **data,
        )
    except ValueError as exc:
        raise _conflit_409(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(obj)
    response.status_code = 201 if created else 200
    return _avec_preuve(db, obj, PlantActivityOut)


@router.get(
    "/science/plants/{plant_id}/activities",
    response_model=list[PlantActivityOut],
)
def list_plant_activities(
    plant_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[dict]:
    """Liste les activités pharmacologiques rapportées d'une plante
    (404 si absente). Chaque activité expose sa preuve pointée."""
    _plante_ou_404(db, plant_id)
    rows = (
        db.query(PlantActivity)
        .filter(PlantActivity.plant_id == plant_id)
        .order_by(PlantActivity.id)
        .limit(limit)
        .all()
    )
    return [_avec_preuve(db, r, PlantActivityOut) for r in rows]


# ---------------------------------------------------------------------------
# Preuve d'un lien plante-composé (public)
# ---------------------------------------------------------------------------

@router.post("/science/plant-compounds/{link_id}/preuve")
def attach_link_evidence(
    link_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Pointe un lien plante-composé vers sa preuve (`{"evidence_id": ...}`).

    Complète la chaîne plante → composé → partie → concentration →
    méthode d'extraction → publication → DOI. 404 si le lien ou la preuve
    est absent ; 400 si `evidence_id` est manquant.
    """
    evidence_id = (payload or {}).get("evidence_id")
    if evidence_id is None:
        raise HTTPException(
            status_code=400,
            detail="Le champ `evidence_id` est requis.",
        )
    _preuve_ou_404(db, int(evidence_id))
    try:
        lien = science_service.attach_link_evidence(db, link_id, int(evidence_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(lien)
    return {
        "link_id": lien.id,
        "evidence_id": lien.evidence_id,
        "preuve": _preuve_dict(db, lien.evidence_id),
    }


@router.get("/science/plant-compounds/{link_id}/preuve")
def get_link_evidence(
    link_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Preuve pointée par un lien plante-composé (404 si le lien est
    absent ; `preuve` vaut null si aucune preuve n'est pointée)."""
    lien = db.get(PlantCompound, link_id)
    if lien is None:
        raise HTTPException(
            status_code=404,
            detail=f"Lien plante-composé introuvable : id={link_id}.",
        )
    return {
        "link_id": lien.id,
        "evidence_id": lien.evidence_id,
        "preuve": _preuve_dict(db, lien.evidence_id),
    }


# ---------------------------------------------------------------------------
# Provenance (public, journal append-only)
# ---------------------------------------------------------------------------

#: Types d'entités traçables → modèle (audit adversarial 2e passe :
#: la provenance ne peut plus être rattachée à une entité inexistante
#: ni à l'entité d'un autre projet).
_ENTITES_PROVENANCE = {
    "plant": Plant,
    "compound": Compound,
    "plant_compound": PlantCompound,
    "compound_target": CompoundTarget,
    "plant_activity": PlantActivity,
    "evidence": Evidence,
    "study": Study,
}


@router.post("/science/provenance", response_model=ProvenanceOut, status_code=201)
def create_provenance(
    payload: ProvenanceCreate,
    response: Response,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> ProvenanceOut:
    """Enregistre la traçabilité d'une entité scientifique (qui, quoi,
    quand, quelle version, quelle licence).

    Idempotent : ré-enregistrer la même version ne crée pas de doublon
    (200), sinon 201. Journal append-only : on n'écrase jamais une ligne.

    P1 honnêteté (audit adversarial 2e passe) : l'entité référencée doit
    EXISTER (404 sinon — pas de provenance « flottante » falsifiable) et,
    si elle est rattachée à un projet, ce projet doit être celui de la
    clé (403 — pas de pollution de la provenance d'autrui).
    """
    data = payload.model_dump(exclude_unset=True)
    entity_type = (data.get("entity_type") or "").strip().lower()
    entity_id = data.get("entity_id")
    modele = _ENTITES_PROVENANCE.get(entity_type)
    if modele is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Type d'entité inconnu : {data.get('entity_type')!r} "
                f"(attendus : {', '.join(sorted(_ENTITES_PROVENANCE))})."
            ),
        )
    entite = db.get(modele, entity_id)
    if entite is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Entité « {entity_type} » #{entity_id} introuvable : "
                "provenance refusée (une provenance ne peut être "
                "rattachée qu'à une entité existante)."
            ),
        )
    projet_entite = getattr(entite, "project_id", None)
    if projet_entite is not None:
        require_project_access(api_key, projet_entite)
    try:
        obj, created = science_service.record_provenance(
            db,
            data.pop("entity_type"),
            data.pop("entity_id"),
            **data,
        )
    except ValueError as exc:
        raise _conflit_409(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(obj)
    response.status_code = 201 if created else 200
    return ProvenanceOut.model_validate(obj)


@router.get("/science/provenance", response_model=list[ProvenanceOut])
def list_provenance(
    entity_type: str | None = Query(default=None),
    entity_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[ProvenanceOut]:
    """Liste la traçabilité, filtrable par entité
    (`?entity_type=plant&entity_id=3`)."""
    query = db.query(Provenance)
    if entity_type:
        query = query.filter(Provenance.entity_type == entity_type)
    if entity_id is not None:
        query = query.filter(Provenance.entity_id == entity_id)
    rows = query.order_by(Provenance.id.desc()).limit(limit).all()
    return [ProvenanceOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Jeux de données du chercheur (privé, cloisonné au projet)
# ---------------------------------------------------------------------------

@router.post("/science/datasets", response_model=DatasetOut, status_code=201)
def create_dataset(
    payload: DatasetCreate,
    response: Response,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> DatasetOut:
    """Enregistre un jeu de données du chercheur (privé par défaut).

    Le `project_id` est résolu par `scoped_project_id` (cloisonnement par
    défaut sur le projet de la clé API). `is_private` vaut True par défaut :
    un jeu de données n'est jamais exposé hors du projet sans action
    explicite. Idempotent sur `(name, project_id)` : 200 + existant si
    déjà enregistré, 201 sinon.
    """
    data = payload.model_dump(exclude_unset=True)
    project_id = scoped_project_id(api_key, data.pop("project_id", None))
    if project_id is None:
        raise HTTPException(
            status_code=400,
            detail="Le champ project_id est requis pour enregistrer un jeu de données.",
        )
    try:
        obj, created = science_service.register_dataset(
            db, data.pop("name"), project_id=project_id, **data
        )
    except ValueError as exc:
        raise _conflit_409(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(obj)
    response.status_code = 201 if created else 200
    return DatasetOut.model_validate(obj)


@router.get("/science/datasets", response_model=list[DatasetOut])
def list_datasets(
    project_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[DatasetOut]:
    """Liste les jeux de données du projet autorisé (antichronologique).

    Sans `project_id`, le projet de la clé API est utilisé (cloisonnement).
    `require_project_access` est appliqué sur le projet demandé : aucune
    fuite entre laboratoires.
    """
    effective = scoped_project_id(api_key, project_id)
    if effective is not None:
        require_project_access(api_key, effective)
    query = db.query(Dataset)
    if effective is not None:
        query = query.filter(Dataset.project_id == effective)
    rows = query.order_by(Dataset.id.desc()).limit(limit).all()
    return [DatasetOut.model_validate(r) for r in rows]
