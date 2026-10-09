"""Routeur API scientifique : plantes, composés, preuves, observations, simulations.

Deux zones :

- **Données scientifiques partagées** (plantes, composés, associations
  plante-composé, preuves, niveaux de données) : publiques, SANS cloisonnement
  par projet.
- **Données de laboratoire** (observations de recherche, simulations) :
  cloisonnées au projet via `scoped_project_id` / `require_project_access`
  (même pattern que `routers/entities.py`).

Discipline des niveaux de preuve : chaque objet scientifique porte un
`data_level` (`publie`, `normalise`, `hypothese`, `prediction`,
`experimental`) qui dit ce qu'est la donnée — jamais plus. Une simulation
n'est JAMAIS présentée comme une preuve expérimentale :
`SimulationOut.avertissement` le rappelle explicitement à chaque réponse.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    projet_effectif_ou_404,
    require_project_access,
    require_project_access_strict,
    scoped_project_id,
)
from webintel.science.models import (
    Compound,
    DataLevel,
    Evidence,
    Plant,
    PlantCompound,
    ResearchObservation,
    Simulation,
)
from webintel.science.schemas import (
    CompoundCreate,
    CompoundOut,
    EvidenceCreate,
    EvidenceOut,
    PlantCompoundCreate,
    PlantCompoundOut,
    PlantCreate,
    PlantOut,
    ResearchObservationCreate,
    ResearchObservationOut,
    SimulationCreate,
    SimulationOut,
)
from webintel.science import service as science_service

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
    """Retourne la plante `plant_id` ou lève une 404 (message FR)."""
    plant = db.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(
            status_code=404,
            detail=f"Plante introuvable : id={plant_id}.",
        )
    return plant


def _compose_ou_404(db: Session, compound_id: int) -> Compound:
    """Retourne le composé `compound_id` ou lève une 404 (message FR)."""
    compound = db.get(Compound, compound_id)
    if compound is None:
        raise HTTPException(
            status_code=404,
            detail=f"Composé introuvable : id={compound_id}.",
        )
    return compound


# ---------------------------------------------------------------------------
# Niveaux de données (public)
# ---------------------------------------------------------------------------

_NIVEAUX_INFO: dict[str, dict[str, str]] = {
    "PUBLIE": {
        "label": "Publié",
        "description": (
            "Donnée issue d'une publication scientifique (article, ouvrage, "
            "base de données) — traçable via sa référence (DOI, citation)."
        ),
        "badge_couleur": "#1a7f37",
    },
    "NORMALISE": {
        "label": "Normalisé",
        "description": (
            "Donnée publiée, nettoyée et harmonisée par WebIntel "
            "(taxonomie, unités, identifiants chimiques)."
        ),
        "badge_couleur": "#0969da",
    },
    "HYPOTHESE": {
        "label": "Hypothèse",
        "description": (
            "Interprétation proposée par un chercheur — reste à confirmer "
            "par des données publiées ou expérimentales."
        ),
        "badge_couleur": "#9a6700",
    },
    "PREDICTION": {
        "label": "Prédiction",
        "description": (
            "Résultat d'un modèle ou d'une analyse prédictive — ce n'est "
            "pas une mesure, à confirmer expérimentalement."
        ),
        "badge_couleur": "#8250df",
    },
    "EXPERIMENTAL": {
        "label": "Expérimental",
        "description": (
            "Observation ou mesure réalisée en laboratoire ou sur le terrain, "
            "rattachée à un projet."
        ),
        "badge_couleur": "#cf222e",
    },
}


@router.get("/science/data-levels")
def get_data_levels(
    api_key=Depends(api_key_auth),
) -> dict:
    """Référentiel des 5 niveaux de preuve (`{"levels": [...]}`).

    Chaque niveau : `code` (valeur stockée), `label` (FR), `description` (FR),
    `badge_couleur` (pour l'UI). Les niveaux séparent strictement donnée
    publiée, donnée normalisée, hypothèse, prédiction et observation
    expérimentale.
    """
    levels = []
    for member in DataLevel:
        info = _NIVEAUX_INFO.get(
            member.name,
            {"label": member.name, "description": "", "badge_couleur": "#6e7781"},
        )
        levels.append({"code": member.value, **info})
    return {"levels": levels}


# ---------------------------------------------------------------------------
# Plantes (public)
# ---------------------------------------------------------------------------

@router.post("/science/plants", response_model=PlantOut, status_code=201)
def create_plant(
    payload: PlantCreate,
    response: Response,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> PlantOut:
    """Crée une fiche plante (`data_level` selon la source déclarée).

    Idempotent sur `scientific_name` : si la plante existe déjà, retourne
    la fiche existante avec un statut 200 (201 à la création).
    """
    data = payload.model_dump(exclude_unset=True)
    scientific_name = data.pop("scientific_name")
    try:
        obj, created = science_service.create_plant(db, scientific_name, **data)
    except ValueError as exc:
        raise _conflit_409(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(obj)  # recharge tous les attributs : le validateur `mode="before"`
    # des schémas lit `vars(obj)` et les attributs expirés par le commit
    # seraient vus comme manquants
    response.status_code = 201 if created else 200
    return PlantOut.model_validate(obj)


@router.get("/science/plants", response_model=list[PlantOut])
def search_plants(
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, description="Pagination : nombre de résultats sautés."),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[PlantOut]:
    """Recherche de plantes par nom (scientifique ou vernaculaire)."""
    rows = science_service.search_plants(db, q, limit, offset)
    return [PlantOut.model_validate(r) for r in rows]


@router.get("/science/plants/{plant_id}", response_model=PlantOut)
def get_plant(
    plant_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> PlantOut:
    """Détail d'une fiche plante (404 si absente)."""
    return PlantOut.model_validate(_plante_ou_404(db, plant_id))


# ---------------------------------------------------------------------------
# Composés (public)
# ---------------------------------------------------------------------------

@router.post("/science/compounds", response_model=CompoundOut, status_code=201)
def create_compound(
    payload: CompoundCreate,
    response: Response,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> CompoundOut:
    """Crée une fiche composé (`data_level` selon la source déclarée).

    Idempotent sur `name` : 200 + fiche existante si déjà présente, 201 sinon.
    Un InChIKey déjà rattaché à un autre composé → 409.
    """
    data = payload.model_dump(exclude_unset=True)
    name = data.pop("name")
    try:
        obj, created = science_service.create_compound(db, name, **data)
    except ValueError as exc:
        raise _conflit_409(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(obj)  # recharge tous les attributs : le validateur `mode="before"`
    # des schémas lit `vars(obj)` et les attributs expirés par le commit
    # seraient vus comme manquants
    response.status_code = 201 if created else 200
    return CompoundOut.model_validate(obj)


@router.get("/science/compounds", response_model=list[CompoundOut])
def search_compounds(
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, description="Pagination : nombre de résultats sautés."),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[CompoundOut]:
    """Recherche de composés par nom, formule ou identifiant (InChIKey...)."""
    rows = science_service.search_compounds(db, q, limit, offset)
    return [CompoundOut.model_validate(r) for r in rows]


@router.get("/science/compounds/{compound_id}", response_model=CompoundOut)
def get_compound(
    compound_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> CompoundOut:
    """Détail d'une fiche composé (404 si absente)."""
    return CompoundOut.model_validate(_compose_ou_404(db, compound_id))


@router.post("/science/compounds/{compound_id}/normaliser")
def normaliser_compound(
    compound_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Normalise la structure d'un composé (SMILES → InChI / InChIKey).

    Complète UNIQUEMENT les champs vides (``inchi``, ``inchikey``) ;
    un SMILES invalide est signalé (statut ``"invalide"``) et le SMILES
    d'origine n'est jamais modifié ; une valeur stockée différente du
    calcul est signalée comme divergence et n'est jamais écrasée.
    Retourne le rapport de normalisation. 404 si le composé est absent.
    """
    from webintel.science import normalisation_moleculaire as _norm

    _compose_ou_404(db, compound_id)
    rapport = _norm.normaliser_compose(db, compound_id, ecrire=True)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    return rapport


# ---------------------------------------------------------------------------
# Associations plante-composé (public)
# ---------------------------------------------------------------------------

@router.post("/science/plants/{plant_id}/compounds", response_model=PlantCompoundOut, status_code=201)
def link_plant_compound(
    plant_id: int,
    payload: PlantCompoundCreate,
    response: Response,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> PlantCompoundOut:
    """Associe un composé à une plante (partie de plante, publication...).

    Idempotent : si l'association existe déjà, retourne l'existante (200),
    sinon 201. 404 si la plante ou le composé est absent.
    """
    _plante_ou_404(db, plant_id)
    data = payload.model_dump(exclude_unset=True)
    # Le schéma porte aussi `plant_id` : il doit concorder avec le chemin.
    body_plant_id = data.pop("plant_id", None)
    if body_plant_id is not None and int(body_plant_id) != int(plant_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "Incohérence : le plant_id du corps "
                f"({body_plant_id}) diffère de celui du chemin ({plant_id})."
            ),
        )
    compound_id = data.pop("compound_id")
    _compose_ou_404(db, compound_id)
    plant_part = data.pop("plant_part", None)
    publication = data.pop("publication", None)
    try:
        obj, created = science_service.link_plant_compound(
            db, plant_id, compound_id,
            plant_part=plant_part, publication=publication, **data,
        )
    except ValueError as exc:
        raise _conflit_409(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(obj)  # recharge tous les attributs : le validateur `mode="before"`
    # des schémas lit `vars(obj)` et les attributs expirés par le commit
    # seraient vus comme manquants
    response.status_code = 201 if created else 200
    return PlantCompoundOut.model_validate(obj)


@router.get("/science/plants/{plant_id}/compounds", response_model=list[PlantCompoundOut])
def list_plant_compounds(
    plant_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[PlantCompoundOut]:
    """Liste les composés associés à une plante (404 si la plante est absente)."""
    _plante_ou_404(db, plant_id)
    rows = (
        db.query(PlantCompound)
        .filter(PlantCompound.plant_id == plant_id)
        .order_by(PlantCompound.id)
        .limit(limit)
        .all()
    )
    return [PlantCompoundOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Preuves / références (public)
# ---------------------------------------------------------------------------

@router.post("/science/evidences", response_model=EvidenceOut, status_code=201)
def create_evidence(
    payload: EvidenceCreate,
    response: Response,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> EvidenceOut:
    """Crée une preuve (référence bibliographique, DOI...).

    Idempotent sur le DOI : si une preuve avec ce DOI existe déjà, retourne
    l'existante (200), sinon 201.
    """
    data = payload.model_dump(exclude_unset=True)
    doi = data.pop("doi", None)
    try:
        obj, created = science_service.add_evidence(db, doi=doi, **data)
    except ValueError as exc:
        raise _conflit_409(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(obj)  # recharge tous les attributs : le validateur `mode="before"`
    # des schémas lit `vars(obj)` et les attributs expirés par le commit
    # seraient vus comme manquants
    response.status_code = 201 if created else 200
    return EvidenceOut.model_validate(obj)


@router.get("/science/evidences", response_model=list[EvidenceOut])
def list_evidences(
    doi: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[EvidenceOut]:
    """Liste les preuves, filtrables par DOI exact (`?doi=...`)."""
    query = db.query(Evidence)
    if doi:
        query = query.filter(Evidence.doi == doi)
    rows = query.order_by(Evidence.id.desc()).offset(offset).limit(limit).all()
    return [EvidenceOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Observations de laboratoire (privé, cloisonné au projet)
# ---------------------------------------------------------------------------

@router.post("/science/observations", response_model=ResearchObservationOut, status_code=201)
def create_observation(
    payload: ResearchObservationCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> ResearchObservationOut:
    """Crée une observation de laboratoire dans le projet autorisé.

    Le `project_id` est résolu par `scoped_project_id` (cloisonnement par
    défaut sur le projet de la clé API). Le `data_level` exposé est toujours
    `experimental` (défaut du schéma) : une observation de laboratoire est
    une mesure, jamais une donnée publiée ni une prédiction.
    """
    data = payload.model_dump(exclude_unset=True)
    project_id = scoped_project_id(api_key, data.pop("project_id", None))
    if project_id is None:
        raise HTTPException(
            status_code=400,
            detail="Le champ project_id est requis pour créer une observation de laboratoire.",
        )
    try:
        obj = science_service.create_observation(db, project_id, **data)
    except ValueError as exc:
        raise _conflit_409(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(obj)  # recharge tous les attributs : le validateur `mode="before"`
    # des schémas lit `vars(obj)` et les attributs expirés par le commit
    # seraient vus comme manquants
    return ResearchObservationOut.model_validate(obj)


@router.get("/science/observations", response_model=list[ResearchObservationOut])
def list_observations(
    project_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[ResearchObservationOut]:
    """Liste les observations de laboratoire du projet autorisé (antichronologique).

    Sans `project_id`, le projet de la clé API est utilisé (cloisonnement).
    `require_project_access` est appliqué sur le projet demandé.
    """
    effective = scoped_project_id(api_key, project_id)
    if effective is not None:
        require_project_access(api_key, effective)
    query = db.query(ResearchObservation)
    if effective is not None:
        query = query.filter(ResearchObservation.project_id == effective)
    rows = query.order_by(ResearchObservation.id.desc()).offset(offset).limit(limit).all()
    return [ResearchObservationOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Simulations (privé, cloisonné au projet si project_id fourni)
# ---------------------------------------------------------------------------

@router.post("/science/simulations", response_model=SimulationOut, status_code=201)
def create_simulation(
    payload: SimulationCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> SimulationOut:
    """Crée une simulation (docking, QSAR, prédiction d'activité...).

    Les champs `compound_ids`, `target`, `parameters`, `method` sont regroupés
    dans le dictionnaire `inputs` transmis au service (empreinte `input_hash`
    pour l'audit) ET conservés comme champs persistés du modèle.

    P0-1 : `project_id` OBLIGATOIRE à la création — résolu via
    `projet_effectif_ou_404` : projet de la clé (403 vers un projet
    étranger) en mode serveur, projet « Défaut » en mode local. Une
    simulation n'est JAMAIS orpheline (`project_id` NOT NULL).
    Entrées vides → 409.

    La réponse inclut toujours `avertissement` : une simulation N'EST PAS
    une preuve expérimentale — résultat à confirmer en laboratoire.
    """
    data = payload.model_dump(exclude_unset=True)
    name = data.pop("name")
    raw_project_id = data.pop("project_id", None)
    # P0-1 : jamais d'orphelin — le projet effectif est toujours résolu.
    project_id = projet_effectif_ou_404(db, api_key, raw_project_id)
    # `inputs` : copie dérivée pour le hachage — les champs restent aussi
    # dans `data` car ce sont des colonnes persistées du modèle Simulation.
    inputs: dict = dict(data.get("inputs", None) or {})
    for key in ("compound_ids", "target", "parameters", "method"):
        if key in data:
            inputs[key] = data[key]
    try:
        obj = science_service.create_simulation(
            db,
            name,
            inputs,
            project_id=project_id,
            executed_by=(
                f"clé:{api_key.prefix}"
                if api_key is not None and getattr(api_key, "prefix", None)
                else "local"
            ),
            **data,
        )
    except ValueError as exc:
        raise _conflit_409(exc)
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(obj)  # recharge tous les attributs : le validateur `mode="before"`
    # des schémas lit `vars(obj)` et les attributs expirés par le commit
    # seraient vus comme manquants
    return SimulationOut.model_validate(obj)


@router.get("/science/simulations", response_model=list[SimulationOut])
def list_simulations(
    project_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[SimulationOut]:
    """Liste les simulations (filtrées au projet autorisé si `project_id` donné).

    `require_project_access` est appliqué sur le projet demandé. Chaque
    élément porte son `avertissement` : une simulation n'est pas une preuve
    expérimentale.
    """
    effective = scoped_project_id(api_key, project_id)
    if effective is not None:
        require_project_access(api_key, effective)
    query = db.query(Simulation)
    if effective is not None:
        query = query.filter(Simulation.project_id == effective)
    rows = query.order_by(Simulation.id.desc()).offset(offset).limit(limit).all()
    return [SimulationOut.model_validate(r) for r in rows]


@router.get("/science/simulations/{simulation_id}", response_model=SimulationOut)
def get_simulation(
    simulation_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> SimulationOut:
    """Détail d'une simulation (404 si absente).

    P0-1 : `require_project_access_strict` est appliqué sur TOUTES les
    lectures — y compris une éventuelle simulation orpheline antérieure
    au durcissement (403 en mode serveur, jamais de fuite inter-projets).
    L'`avertissement` rappelle qu'il s'agit d'une simulation, à confirmer
    expérimentalement.
    """
    obj = db.get(Simulation, simulation_id)
    if obj is None:
        raise HTTPException(
            status_code=404,
            detail=f"Simulation introuvable : id={simulation_id}.",
        )
    require_project_access_strict(api_key, obj.project_id)
    return SimulationOut.model_validate(obj)
