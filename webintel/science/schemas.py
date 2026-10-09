"""DTOs Pydantic v2 du module scientifique (Phase 2).

Conventions : noms de champs en français dans les docstrings, codes
techniques en anglais. Chaque schéma ``*Out`` expose ``data_level``
(``DataLevel``) pour que le niveau épistémologique d'une donnée soit
toujours visible par le consommateur — jamais de confusion entre une
publication, une hypothèse, une prédiction de simulation et une
observation expérimentale.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from webintel.science.models import DataLevel


def data_level_for_confidence(confidence: str | None) -> DataLevel:
    """Niveau de données déduit de la confiance d'une association.

    - ``"publie"`` -> ``DataLevel.PUBLIE``
    - ``"rapporte"`` -> ``DataLevel.NORMALISE``
    - ``"incertain"`` -> ``DataLevel.HYPOTHESE``
    - toute autre valeur -> ``DataLevel.NORMALISE`` (repli prudent : une
      association rapportée sans confiance explicite reste une donnée
      normalisée, jamais une publication ni une hypothèse).
    """
    mapping = {
        "publie": DataLevel.PUBLIE,
        "rapporte": DataLevel.NORMALISE,
        "incertain": DataLevel.HYPOTHESE,
    }
    return mapping.get((confidence or "").strip().lower(), DataLevel.NORMALISE)


class _OrmOut(BaseModel):
    """Base des schémas de sortie (construction depuis un objet ORM)."""

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Plantes
# ---------------------------------------------------------------------------

class PlantCreate(BaseModel):
    """Création d'une plante (nom scientifique obligatoire, le reste optionnel)."""

    scientific_name: str = Field(min_length=1, max_length=255)
    synonyms: list[str] = Field(default_factory=list)
    vernacular_names: dict[str, Any] = Field(default_factory=dict)
    family: str | None = Field(default=None, max_length=128)
    genus: str | None = Field(default=None, max_length=128)
    species: str | None = Field(default=None, max_length=128)
    taxonomy: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    distribution: str | None = None
    used_parts: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    external_ids: dict[str, Any] = Field(default_factory=dict)
    # v2.1 — morphologie détaillée (nullable, jamais inventée)
    feuilles: str | None = None
    fleurs: str | None = None
    fruits: str | None = None
    graines: str | None = None
    racines: str | None = None
    ecorce: str | None = None
    habitat: str | None = None
    references_botaniques: list[str] = Field(default_factory=list)
    # v2.1 — pharmacognosie
    matiere_premiere: str | None = None
    preparation: str | None = None


class PlantOut(_OrmOut):
    """Plante exposée : donnée publiée (ou normalisée à l'import)."""

    id: int
    scientific_name: str
    synonyms: list
    vernacular_names: dict
    family: str | None
    genus: str | None
    species: str | None
    taxonomy: dict
    description: str | None
    distribution: str | None
    used_parts: list
    images: list
    external_ids: dict
    # v2.1 — morphologie détaillée et pharmacognosie (nullable)
    feuilles: str | None
    fleurs: str | None
    fruits: str | None
    graines: str | None
    racines: str | None
    ecorce: str | None
    habitat: str | None
    references_botaniques: list
    matiere_premiere: str | None
    preparation: str | None
    created_at: datetime
    updated_at: datetime
    data_level: DataLevel = DataLevel.PUBLIE


# ---------------------------------------------------------------------------
# Composés
# ---------------------------------------------------------------------------

class CompoundCreate(BaseModel):
    """Création d'un composé chimique (nom obligatoire, le reste optionnel)."""

    name: str = Field(min_length=1, max_length=255)
    synonyms: list[str] = Field(default_factory=list)
    formula: str | None = Field(default=None, max_length=64)
    molecular_weight: float | None = None
    smiles: str | None = None
    inchi: str | None = None
    inchikey: str | None = Field(default=None, max_length=64)
    external_ids: dict[str, Any] = Field(default_factory=dict)
    chemical_class: str | None = Field(default=None, max_length=128)
    structure_molfile: str | None = None


class CompoundOut(_OrmOut):
    """Composé exposé : donnée publiée (ou normalisée à l'import)."""

    id: int
    name: str
    synonyms: list
    formula: str | None
    molecular_weight: float | None
    smiles: str | None
    inchi: str | None
    inchikey: str | None
    external_ids: dict
    chemical_class: str | None
    structure_molfile: str | None
    created_at: datetime
    updated_at: datetime
    data_level: DataLevel = DataLevel.PUBLIE


# ---------------------------------------------------------------------------
# Associations plante <-> composé
# ---------------------------------------------------------------------------

class PlantCompoundCreate(BaseModel):
    """Création d'une association plante-composé rapportée par une source."""

    plant_id: int
    compound_id: int
    plant_part: str | None = Field(default=None, max_length=128)
    association_type: str | None = Field(default=None, max_length=64)
    concentration: float | None = None
    concentration_unit: str | None = Field(default=None, max_length=32)
    extraction_method: str | None = None
    publication: str | None = None
    doi: str | None = Field(default=None, max_length=255)
    year: int | None = None
    confidence: str = Field(default="rapporte", max_length=32)
    source_id: int | None = None
    # v2.1 — méthode d'analyse, rendement rapporté, preuve pointée
    analysis_method: str | None = None
    rendement: float | None = None
    rendement_unite: str | None = Field(default=None, max_length=32)
    evidence_id: int | None = None


class PlantCompoundOut(_OrmOut):
    """Association exposée : le niveau de données suit la confiance déclarée."""

    id: int
    plant_id: int
    compound_id: int
    plant_part: str | None
    association_type: str | None
    concentration: float | None
    concentration_unit: str | None
    extraction_method: str | None
    publication: str | None
    doi: str | None
    year: int | None
    confidence: str
    source_id: int | None
    # v2.1
    analysis_method: str | None
    rendement: float | None
    rendement_unite: str | None
    evidence_id: int | None
    created_at: datetime
    updated_at: datetime
    data_level: DataLevel = DataLevel.NORMALISE

    @model_validator(mode="before")
    @classmethod
    def _derive_data_level(cls, data: Any) -> Any:
        """Déduit `data_level` de `confidence` si non fourni explicitement.

        Fonctionne pour un dictionnaire comme pour un objet ORM
        (`from_attributes=True`) : dans les deux cas, la confiance
        déclarée pilote le niveau de données via
        `data_level_for_confidence()`.
        """
        if isinstance(data, dict):
            if "data_level" not in data:
                data = dict(data)
                data["data_level"] = data_level_for_confidence(data.get("confidence"))
            return data
        # Objet ORM : on extrait les attributs publics et on force le
        # mapping confiance -> niveau de données.
        attrs = {
            cle: valeur
            for cle, valeur in vars(data).items()
            if not cle.startswith("_sa_")
        }
        attrs["data_level"] = data_level_for_confidence(attrs.get("confidence"))
        return attrs


# ---------------------------------------------------------------------------
# Preuves documentaires
# ---------------------------------------------------------------------------

class EvidenceCreate(BaseModel):
    """Création d'une preuve documentaire."""

    source_type: str = Field(default="publication", max_length=32)
    reference: str = ""
    doi: str | None = Field(default=None, max_length=255)
    pmid: str | None = Field(default=None, max_length=32)
    url: str | None = None
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    excerpt: str | None = None
    retrieved_at: datetime | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class EvidenceOut(_OrmOut):
    """Preuve exposée : donnée publiée."""

    id: int
    source_type: str
    reference: str
    doi: str | None
    pmid: str | None
    url: str | None
    title: str
    authors: list
    year: int | None
    excerpt: str | None
    retrieved_at: datetime | None
    provenance: dict
    created_at: datetime
    updated_at: datetime
    data_level: DataLevel = DataLevel.PUBLIE


# ---------------------------------------------------------------------------
# Observations de laboratoire
# ---------------------------------------------------------------------------

class ResearchObservationCreate(BaseModel):
    """Création d'une observation de laboratoire (privée par défaut)."""

    project_id: int
    protocol_ref: str | None = Field(default=None, max_length=255)
    observed_date: datetime | None = None
    author: str | None = Field(default=None, max_length=255)
    laboratory: str | None = Field(default=None, max_length=255)
    raw_data: dict[str, Any] = Field(default_factory=dict)
    file_path: str | None = None
    status: str = Field(default="brut", max_length=32)
    comment: str | None = None
    is_private: bool = True


class ResearchObservationOut(_OrmOut):
    """Observation exposée : donnée expérimentale (privée par défaut)."""

    id: int
    project_id: int
    protocol_ref: str | None
    observed_date: datetime | None
    author: str | None
    laboratory: str | None
    raw_data: dict
    file_path: str | None
    status: str
    comment: str | None
    is_private: bool
    created_at: datetime
    updated_at: datetime
    data_level: DataLevel = DataLevel.EXPERIMENTAL


# ---------------------------------------------------------------------------
# Simulations
# ---------------------------------------------------------------------------

SIMULATION_AVERTISSEMENT = (
    "Simulation — à confirmer expérimentalement. "
    "Ne constitue pas une preuve expérimentale."
)


class SimulationCreate(BaseModel):
    """Création d'une simulation (prédiction, jamais une preuve)."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    compound_ids: list[int] = Field(default_factory=list)
    target: str | None = None
    hypotheses: str | None = None
    method: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    engine_name: str | None = Field(default=None, max_length=128)
    engine_version: str | None = Field(default=None, max_length=64)
    units: str | None = Field(default=None, max_length=64)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    references: list[str] = Field(default_factory=list)
    status: str = Field(default="exploratoire", max_length=32)
    project_id: int | None = None


class SimulationOut(_OrmOut):
    """Simulation exposée : prédiction, avec avertissement obligatoire."""

    id: int
    project_id: int  # P0-1 : toujours rattachée à un projet (NOT NULL en DB)
    name: str
    description: str | None
    compound_ids: list
    target: str | None
    hypotheses: str | None
    method: str | None
    parameters: dict
    engine_name: str | None
    engine_version: str | None
    result: dict
    units: str | None
    run_at: datetime
    input_hash: str
    executed_by: str | None
    confidence: float
    references: list
    status: str
    created_at: datetime
    updated_at: datetime
    data_level: DataLevel = DataLevel.PREDICTION
    avertissement: str = SIMULATION_AVERTISSEMENT


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------

class ScienceSearchOut(BaseModel):
    """Résultat de recherche plein-texte (plantes / composés)."""

    query: str
    plants: list[PlantOut] = Field(default_factory=list)
    compounds: list[CompoundOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# v2.1 — Modèle scientifique enrichi
# ---------------------------------------------------------------------------
# Chaque schéma ``*Out`` expose ``data_level`` et, quand la relation
# pointe vers une preuve, ``preuve`` (dictionnaire structuré ou None) :
# la chaîne donnée → source → DOI est toujours traçable dans l'API.


class _PreuveOut(_OrmOut):
    """Preuve pointée par une relation (forme allégée, JSON-sérialisable)."""

    id: int
    source_type: str
    reference: str
    doi: str | None
    pmid: str | None
    url: str | None
    title: str
    authors: list
    year: int | None


class CompoundTargetCreate(BaseModel):
    """Documentation d'une cible biologique d'un composé (sourcée)."""

    compound_id: int
    target_name: str = Field(min_length=1, max_length=255)
    target_type: str | None = Field(default=None, max_length=128)
    interaction_type: str | None = Field(default=None, max_length=64)
    affinity_value: float | None = None
    affinity_unit: str | None = Field(default=None, max_length=32)
    source: str | None = None
    publication: str | None = None
    doi: str | None = Field(default=None, max_length=255)
    evidence_id: int | None = None
    data_level: str = Field(default="publie", max_length=32)


class CompoundTargetOut(_OrmOut):
    """Cible documentée : donnée publiée, avec sa preuve pointée."""

    id: int
    compound_id: int
    target_name: str
    target_type: str | None
    interaction_type: str | None
    affinity_value: float | None
    affinity_unit: str | None
    source: str | None
    publication: str | None
    doi: str | None
    evidence_id: int | None
    data_level: DataLevel = DataLevel.PUBLIE
    preuve: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class PlantActivityCreate(BaseModel):
    """Enregistrement d'une activité pharmacologique rapportée (sourcée)."""

    plant_id: int
    activity: str = Field(min_length=1, max_length=255)
    study_type: str | None = Field(default=None, max_length=32)
    study_model: str | None = None
    published_result: str | None = None
    reference: str | None = None
    doi: str | None = Field(default=None, max_length=255)
    evidence_id: int | None = None
    data_level: str = Field(default="publie", max_length=32)


class PlantActivityOut(_OrmOut):
    """Activité rapportée : donnée publiée, avec sa preuve pointée."""

    id: int
    plant_id: int
    activity: str
    study_type: str | None
    study_model: str | None
    published_result: str | None
    reference: str | None
    doi: str | None
    evidence_id: int | None
    data_level: DataLevel = DataLevel.PUBLIE
    preuve: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class StudyCreate(BaseModel):
    """Création d'une étude / publication structurée."""

    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = Field(default=None, max_length=255)
    pmid: str | None = Field(default=None, max_length=32)
    journal: str | None = Field(default=None, max_length=255)
    study_type: str | None = Field(default=None, max_length=32)
    abstract: str | None = None
    url: str | None = None


class StudyOut(_OrmOut):
    """Étude structurée exposée : donnée publiée."""

    id: int
    title: str
    authors: list
    year: int | None
    doi: str | None
    pmid: str | None
    journal: str | None
    study_type: str | None
    abstract: str | None
    url: str | None
    created_at: datetime
    updated_at: datetime
    data_level: DataLevel = DataLevel.PUBLIE


class ProvenanceCreate(BaseModel):
    """Enregistrement de la traçabilité d'une entité."""

    entity_type: str = Field(min_length=1, max_length=64)
    entity_id: int
    source_name: str | None = Field(default=None, max_length=255)
    connector: str | None = Field(default=None, max_length=64)
    connector_version: str | None = Field(default=None, max_length=32)
    data_version: str | None = Field(default=None, max_length=128)
    license: str | None = Field(default=None, max_length=255)


class ProvenanceOut(_OrmOut):
    """Traçabilité exposée (journal append-only)."""

    id: int
    entity_type: str
    entity_id: int
    source_name: str | None
    connector: str | None
    connector_version: str | None
    data_version: str | None
    license: str | None
    imported_at: datetime
    created_at: datetime


class DatasetCreate(BaseModel):
    """Enregistrement d'un jeu de données du chercheur (privé par défaut)."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    is_private: bool = True
    sha256: str | None = Field(default=None, max_length=64)
    file_path: str | None = None
    project_id: int | None = None


class DatasetOut(_OrmOut):
    """Jeu de données exposé (cloisonné au projet via l'API)."""

    id: int
    project_id: int | None
    name: str
    description: str | None
    is_private: bool
    sha256: str | None
    file_path: str | None
    imported_at: datetime
    created_at: datetime
    updated_at: datetime
    data_level: DataLevel = DataLevel.EXPERIMENTAL


# ---------------------------------------------------------------------------
# v2.2 — Extension terrain / ethnobotanique / phytochimie / pharmacognosie
# ---------------------------------------------------------------------------
# Chaque schéma ``*Create`` accepte ``project_id`` (résolu par
# ``scoped_project_id`` côté routeur : cloisonnement par défaut sur le
# projet de la clé API). Chaque ``*Out`` expose ``data_level``,
# ``niveau_confidentialite``, ``source``, ``cree_par`` et les dates —
# traçabilité complète, jamais de donnée orpheline.


class _TerrainCreate(BaseModel):
    """Base des créations terrain : projet + traçabilité."""

    project_id: int | None = None
    source: str | None = None
    cree_par: str | None = Field(default=None, max_length=255)
    niveau_confidentialite: str = Field(default="projet", max_length=32)
    evidence_id: int | None = None


class _TerrainOut(_OrmOut):
    """Base des sorties terrain : traçabilité exposée."""

    id: int
    project_id: int
    source: str | None
    cree_par: str | None
    data_level: str
    niveau_confidentialite: str
    evidence_id: int | None
    created_at: datetime
    updated_at: datetime


class NomVernaculaireCreate(_TerrainCreate):
    """Nom vernaculaire : langue jamais devinée (None = non renseignée)."""

    plant_id: int
    nom_original: str = Field(min_length=1, max_length=255)
    langue: str | None = Field(default=None, max_length=64)
    orthographes_variantes: list[str] = Field(default_factory=list)
    region: str | None = Field(default=None, max_length=255)
    localite: str | None = Field(default=None, max_length=255)


class NomVernaculaireOut(_TerrainOut):
    """Nom vernaculaire exposé (forme originale + normalisée)."""

    plant_id: int
    nom_original: str
    nom_normalise: str
    langue: str | None
    orthographes_variantes: list
    region: str | None
    localite: str | None


class UsageTraditionnelCreate(_TerrainCreate):
    """Usage traditionnel rapporté (texte verbatim, jamais reformulé)."""

    plant_id: int
    usage: str = Field(min_length=1)
    partie_utilisee: str | None = Field(default=None, max_length=255)
    preparation: str | None = None
    forme: str | None = Field(default=None, max_length=255)
    voie_administration: str | None = Field(default=None, max_length=255)
    contexte: str | None = None
    localite: str | None = Field(default=None, max_length=255)
    groupe_population: str | None = Field(default=None, max_length=255)
    publication: str | None = None
    date_enquete: datetime | None = None
    informateur_code: str | None = Field(default=None, max_length=128)
    consentement: bool | None = None
    niveau_confidentialite: str = Field(default="restreint", max_length=32)


class UsageTraditionnelOut(_TerrainOut):
    """Usage traditionnel exposé — l'UI ajoute le bandeau obligatoire."""

    plant_id: int
    usage: str
    partie_utilisee: str | None
    preparation: str | None
    forme: str | None
    voie_administration: str | None
    contexte: str | None
    localite: str | None
    groupe_population: str | None
    publication: str | None
    date_enquete: datetime | None
    informateur_code: str | None
    consentement: bool | None
    bandeau: str = "USAGE TRADITIONNEL RAPPORTÉ"


class EchantillonCreate(_TerrainCreate):
    """Échantillon de terrain : GPS nullable, jamais inventé."""

    plant_id: int
    voucher_id: int | None = None
    code_echantillon: str = Field(min_length=1, max_length=128)
    collecteur: str | None = Field(default=None, max_length=255)
    date_collecte: datetime | None = None
    pays: str | None = Field(default=None, max_length=128)
    region: str | None = Field(default=None, max_length=128)
    province: str | None = Field(default=None, max_length=128)
    commune: str | None = Field(default=None, max_length=128)
    localite: str | None = Field(default=None, max_length=255)
    site: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    precision_gps: str | None = Field(default=None, max_length=64)
    altitude_m: float | None = None
    habitat: str | None = None
    partie: str | None = Field(default=None, max_length=255)
    etat: str | None = Field(default=None, max_length=128)
    sechage: str | None = None
    stockage: str | None = None
    institution: str | None = Field(default=None, max_length=255)
    responsable: str | None = Field(default=None, max_length=255)


class EchantillonOut(_TerrainOut):
    """Échantillon exposé (coordonnées précises réservées aux exports autorisés)."""

    plant_id: int
    voucher_id: int | None
    code_echantillon: str
    collecteur: str | None
    date_collecte: datetime | None
    pays: str | None
    region: str | None
    province: str | None
    commune: str | None
    localite: str | None
    site: str | None
    latitude: float | None
    longitude: float | None
    precision_gps: str | None
    altitude_m: float | None
    habitat: str | None
    partie: str | None
    etat: str | None
    sechage: str | None
    stockage: str | None
    institution: str | None
    responsable: str | None


class VoucherCreate(_TerrainCreate):
    """Voucher d'herbier (code unique)."""

    code_voucher: str = Field(min_length=1, max_length=128)
    institution: str | None = Field(default=None, max_length=255)
    code_herbier: str | None = Field(default=None, max_length=32)


class VoucherOut(_TerrainOut):
    """Voucher exposé."""

    code_voucher: str
    institution: str | None
    code_herbier: str | None


class ExtractionCreate(_TerrainCreate):
    """Extraction réalisée sur un échantillon."""

    echantillon_id: int
    nom: str | None = Field(default=None, max_length=255)
    methode: str | None = None
    solvant: str | None = Field(default=None, max_length=255)
    date_extraction: datetime | None = None
    operateur: str | None = Field(default=None, max_length=255)
    rendement: float | None = None
    rendement_unite: str | None = Field(default=None, max_length=32)
    notes: str | None = None


class ExtractionOut(_TerrainOut):
    """Extraction exposée."""

    echantillon_id: int
    nom: str | None
    methode: str | None
    solvant: str | None
    date_extraction: datetime | None
    operateur: str | None
    rendement: float | None
    rendement_unite: str | None
    notes: str | None


class ExtraitCreate(_TerrainCreate):
    """Extrait obtenu d'une extraction."""

    extraction_id: int
    code_extrait: str | None = Field(default=None, max_length=128)
    masse: float | None = None
    masse_unite: str | None = Field(default=None, max_length=32)
    stockage: str | None = None
    notes: str | None = None


class ExtraitOut(_TerrainOut):
    """Extrait exposé."""

    extraction_id: int
    code_extrait: str | None
    masse: float | None
    masse_unite: str | None
    stockage: str | None
    notes: str | None


class FractionCreate(_TerrainCreate):
    """Fraction issue d'un extrait."""

    extrait_id: int
    code_fraction: str | None = Field(default=None, max_length=128)
    methode_fractionnement: str | None = None
    masse: float | None = None
    masse_unite: str | None = Field(default=None, max_length=32)
    notes: str | None = None


class FractionOut(_TerrainOut):
    """Fraction exposée."""

    extrait_id: int
    code_fraction: str | None
    methode_fractionnement: str | None
    masse: float | None
    masse_unite: str | None
    notes: str | None


class FractionComposeCreate(_TerrainCreate):
    """Lien fraction → composé (statut d'identification, jamais deviné)."""

    fraction_id: int
    compound_id: int
    identification_statut: str = Field(default="inconnu", max_length=32)
    commentaire: str | None = None


class FractionComposeOut(_TerrainOut):
    """Lien fraction → composé exposé."""

    fraction_id: int
    compound_id: int
    identification_statut: str
    commentaire: str | None


class AnalyseChimiqueCreate(_TerrainCreate):
    """Analyse phytochimique rattachée à au moins un maillon de la chaîne."""

    echantillon_id: int | None = None
    extrait_id: int | None = None
    fraction_id: int | None = None
    methode: str = Field(min_length=1, max_length=32)
    instrument: str | None = Field(default=None, max_length=255)
    conditions: dict[str, Any] = Field(default_factory=dict)
    date_analyse: datetime | None = None
    operateur: str | None = Field(default=None, max_length=255)
    laboratoire: str | None = Field(default=None, max_length=255)
    fichier_brut_path: str | None = None
    fichier_brut_sha256: str | None = Field(default=None, max_length=64)
    publication: str | None = None


class AnalyseChimiqueOut(_TerrainOut):
    """Analyse chimique exposée."""

    echantillon_id: int | None
    extrait_id: int | None
    fraction_id: int | None
    methode: str
    instrument: str | None
    conditions: dict
    date_analyse: datetime | None
    operateur: str | None
    laboratoire: str | None
    fichier_brut_path: str | None
    fichier_brut_sha256: str | None
    publication: str | None


class PicChromatographiqueCreate(BaseModel):
    """Pic d'analyse : mesures brutes, identification jamais inventée."""

    analyse_id: int
    temps_retention: float | None = None
    aire: float | None = None
    hauteur: float | None = None
    mz: float | None = None
    identification_statut: str = Field(default="inconnu", max_length=32)
    compound_id: int | None = None
    commentaire: str | None = None


class PicChromatographiqueOut(_OrmOut):
    """Pic exposé."""

    id: int
    analyse_id: int
    temps_retention: float | None
    aire: float | None
    hauteur: float | None
    mz: float | None
    identification_statut: str
    compound_id: int | None
    commentaire: str | None
    created_at: datetime


class PharmacognosieCreate(_TerrainCreate):
    """Fiche pharmacognosie d'une plante (sections, jamais inventées)."""

    plant_id: int
    identification_macro: str | None = None
    identification_micro: str | None = None
    organoleptique: str | None = None
    partie_utilisee: str | None = None
    authentification: str | None = None
    substitution_adulteration: str | None = None
    sechage: str | None = None
    stockage: str | None = None
    preparation: str | None = None


class PharmacognosieOut(_TerrainOut):
    """Fiche pharmacognosie exposée (sections absentes = None)."""

    plant_id: int
    identification_macro: str | None
    identification_micro: str | None
    organoleptique: str | None
    partie_utilisee: str | None
    authentification: str | None
    substitution_adulteration: str | None
    sechage: str | None
    stockage: str | None
    preparation: str | None


class StatutEpistemiquePatch(BaseModel):
    """Mise à jour du statut épistémique d'une cible / activité."""

    statut_epistemique: str = Field(min_length=1, max_length=16)
    methode_statut: str | None = None


class RecherchePlanteOut(BaseModel):
    """Résultat de recherche avec motif de correspondance affiché."""

    plant_id: int
    scientific_name: str
    motif: str
    detail: str
