"""Modèles scientifiques SQLAlchemy — Phase 2 (pharmacognosie / phytochimie).

Règle d'or : les niveaux de données (``DataLevel``) restent strictement
séparés — donnée publiée, donnée normalisée, hypothèse, prédiction de
simulation et observation expérimentale ne sont jamais confondues.
Aucune donnée n'est présentée comme « scientifiquement validée ».

Conventions : SQLAlchemy 2.x (``Mapped`` / ``mapped_column``), clés
étrangères en chaînes (``"plants.id"``...) pour éviter tout import
circulaire — ce module n'importe PAS ``webintel.database.models``.
Portable SQLite / PostgreSQL : ``JSON`` et ``TZDateTime`` uniquement.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from webintel.core.utils import utcnow
from webintel.database.base import Base, TZDateTime


class DataLevel(str, Enum):
    """Niveau épistémologique d'une donnée (jamais mélangés)."""

    PUBLIE = "publie"
    NORMALISE = "normalise"
    HYPOTHESE = "hypothese"
    PREDICTION = "prediction"
    EXPERIMENTAL = "experimental"


class ConfidenceLevel(str, Enum):
    """Niveau de confiance d'une association plante-composé rapportée."""

    PUBLIE = "publie"
    RAPPORTE = "rapporte"
    INCERTAIN = "incertain"


class EvidenceType(str, Enum):
    """Type de preuve documentaire."""

    PUBLICATION = "publication"
    BASE_DE_DONNEES = "base_de_donnees"
    SITE_EXPERT = "site_expert"
    THESE = "these"
    AUTRE = "autre"


class ObservationStatus(str, Enum):
    """Statut de traitement d'une observation de laboratoire."""

    BRUT = "brut"
    EN_COURS = "en_cours"
    VALIDE = "valide"
    REJETE = "rejete"


class SimulationStatus(str, Enum):
    """Statut épistémologique d'une simulation (jamais une preuve)."""

    EXPLORATOIRE = "exploratoire"
    PREDICTIF = "predictif"
    VALIDE_EXPERIMENTALEMENT = "valide_experimentalement"


class StatutEpistemique(str, Enum):
    """Statut épistémique fin d'une relation composé→cible / plante→activité.

    Colonne dédiée (v2.2) : ``DataLevel`` est figé à 5 valeurs par un test
    de non-régression (``test_cinq_niveaux_distincts_et_stables``) et ne
    doit JAMAIS être étendu. Les nuances DOCUMENTÉ / INFÉRÉ / PRÉDIT /
    CALCULÉ vivent ici, sans toucher à ``DataLevel``.
    """

    DOCUMENTE = "documente"  # publication citée
    INFERE = "infere"  # déduit de liens enregistrés (jamais inventé)
    PREDIT = "predit"  # docking / similarité (data_level reste "prediction")
    CALCULE = "calcule"  # descripteur / estimation calculée


class NiveauConfidentialite(str, Enum):
    """Niveau de confidentialité d'un enregistrement de terrain / labo.

    - ``public`` : exportable dans les exports publics ;
    - ``projet`` : visible aux membres du projet (défaut) ;
    - ``chercheur`` : visible uniquement à l'auteur ;
    - ``restreint`` : masqué par défaut, même aux membres du projet ;
    - ``confidentiel`` : jamais divulgué sans autorisation explicite.

    Les exports publics n'incluent QUE les enregistrements ``public`` et
    ne divulguent JAMAIS les coordonnées GPS précises ni les identités
    (voir ``webintel.science.terrain.export_public_plante``).
    """

    PUBLIC = "public"
    PROJET = "projet"
    CHERCHEUR = "chercheur"
    RESTREINT = "restreint"
    CONFIDENTIEL = "confidentiel"


class TechniqueAnalyse(str, Enum):
    """Technique d'analyse phytochimique."""

    HPLC = "HPLC"
    HPLC_UV = "HPLC-UV"
    GC_MS = "GC-MS"
    LC_MS = "LC-MS"
    LC_MS_MS = "LC-MS/MS"
    NMR = "NMR"
    NMR_1H = "NMR-1H"
    NMR_13C = "NMR-13C"
    UV_VIS = "UV-Vis"
    TLC = "TLC"
    CCM = "CCM"
    AUTRE = "autre"


class StatutIdentification(str, Enum):
    """Statut d'identification d'un pic chromatographique / lien fraction-composé.

    - ``identifie`` : identification confirmée (standard injecté, NMR...) ;
    - ``tentative`` : identification proposée (comparaison littérature,
      bibliothèque de spectres) — reste une hypothèse ;
    - ``inconnu`` : pic non identifié — affiché comme tel, jamais deviné.
    """

    IDENTIFIE = "identifie"
    TENTATIVE = "tentative"
    INCONNU = "inconnu"


# Langues du Burkina Faso préparées pour les noms vernaculaires
# (codes normalisés en minuscules, sans accents). Extensible :
# ``enregistrer_langue(code, libelle)`` ajoute une langue au registre.
# RÈGLE ABSOLUE : la langue d'un nom vernaculaire n'est JAMAIS devinée —
# si elle n'est pas connue, elle reste ``None`` (affichée « non renseignée »).
LANGUES_BURKINA: dict[str, str] = {
    "moore": "Mooré",
    "dioula": "Dioula",
    "fulfulde": "Fulfuldé",
    "gulmancema": "Gulmancéma",
    "bissa": "Bissa",
}


def enregistrer_langue(code: str, libelle: str) -> None:
    """Ajoute une langue au registre des langues (extensibilité).

    Le code est normalisé (minuscules, sans espaces). Lève ``ValueError``
    si le code est vide.
    """
    code_norm = (code or "").strip().lower()
    if not code_norm:
        raise ValueError("Le code de langue ne peut pas être vide.")
    LANGUES_BURKINA[code_norm] = libelle


def langue_connue(code: str | None) -> bool:
    """La langue ``code`` est-elle enregistrée (ou ``None`` = non renseignée) ?"""
    if code is None:
        return True
    return (code or "").strip().lower() in LANGUES_BURKINA


class Langue(Base):
    """Langue du registre multilingue (persistance du registre).

    Le registre en mémoire ``LANGUES_BURKINA`` est amorcé en base au
    démarrage (voir ``_amorcer_langues``) : les langues ajoutées par
    ``enregistrer_langue`` / ``terrain.enregistrer_langue_db`` survivent
    au redémarrage. ``code`` : identifiant normalisé (minuscules, ex.
    "moore") ; ``libelle`` : nom d'affichage (ex. "Mooré").
    ``source`` : qui a déclaré la langue (ex. "registre initial",
    "chercheur : <nom>"). ``actif`` : False = langue désactivée sans
    suppression (jamais de suppression silencieuse).

    RÈGLE ABSOLUE : l'attribution d'une langue à un nom vernaculaire
    reste déclarative — cette table n'attribue JAMAIS une langue
    automatiquement à un nom (``NomVernaculaire.langue`` reste ``None``
    = « non renseignée » tant qu'elle n'est pas déclarée).
    """

    __tablename__ = "langues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    libelle: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    actif: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_langues_code", "code"),
    )


class Plant(Base):
    """Plante médicinale / étudiée (donnée publiée ou normalisée)."""

    __tablename__ = "plants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scientific_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    synonyms: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    vernacular_names: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    family: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    genus: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    species: Mapped[str | None] = mapped_column(String(128), nullable=True)
    taxonomy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    distribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    used_parts: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # --- v2.1 : morphologie détaillée (tous nullable, jamais inventés) ------
    feuilles: Mapped[str | None] = mapped_column(Text, nullable=True)
    fleurs: Mapped[str | None] = mapped_column(Text, nullable=True)
    fruits: Mapped[str | None] = mapped_column(Text, nullable=True)
    graines: Mapped[str | None] = mapped_column(Text, nullable=True)
    racines: Mapped[str | None] = mapped_column(Text, nullable=True)
    ecorce: Mapped[str | None] = mapped_column(Text, nullable=True)
    habitat: Mapped[str | None] = mapped_column(Text, nullable=True)
    references_botaniques: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    # --- v2.1 : pharmacognosie --------------------------------------------
    matiere_premiere: Mapped[str | None] = mapped_column(Text, nullable=True)
    preparation: Mapped[str | None] = mapped_column(Text, nullable=True)
    images: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    external_ids: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Compound(Base):
    """Composé chimique (donnée publiée ou normalisée)."""

    __tablename__ = "compounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    synonyms: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    formula: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    molecular_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    smiles: Mapped[str | None] = mapped_column(Text, nullable=True)
    inchi: Mapped[str | None] = mapped_column(Text, nullable=True)
    inchikey: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    external_ids: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    chemical_class: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    structure_molfile: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class PlantCompound(Base):
    """Association plante <-> composé rapportée par une source.

    Une même association vue dans plusieurs publications donne plusieurs
    lignes (une par publication) ; la confiance (``confidence``) porte sur
    la qualité du signal rapporté, pas sur une validation scientifique.
    """

    __tablename__ = "plant_compounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id"), nullable=False)
    compound_id: Mapped[int] = mapped_column(ForeignKey("compounds.id"), nullable=False)
    plant_part: Mapped[str | None] = mapped_column(String(128), nullable=True)
    association_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    concentration: Mapped[float | None] = mapped_column(Float, nullable=True)
    concentration_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[str] = mapped_column(String(32), default="rapporte", nullable=False)
    # --- v2.1 : enrichissement (nullable, ajoutés proprement) ---------------
    # Méthode d'analyse rapportée (ex. HPLC, GC-MS) — distincte de la
    # méthode d'extraction.
    analysis_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Rendement d'extraction rapporté (ex. 8,2 %).
    rendement: Mapped[float | None] = mapped_column(Float, nullable=True)
    rendement_unite: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Preuve pointée : la publication / preuve documentaire qui établit
    # cette association (chaîne plante→composé→partie→concentration→
    # méthode→publication→DOI). Nullable : un lien sans preuve reste
    # une association rapportée, jamais une donnée validée.
    evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidences.id"), nullable=True
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "plant_id", "compound_id", "plant_part", "publication",
            name="uq_plant_compound_link",
        ),
        Index("ix_plant_compounds_plant_id", "plant_id"),
        Index("ix_plant_compounds_compound_id", "compound_id"),
    )


class Evidence(Base):
    """Preuve documentaire (publication, base de données, thèse...).

    `doi` unique : un même DOI importé deux fois ne crée pas de doublon
    (les NULL multiples sont autorisés sur SQLite comme PostgreSQL).

    Publications LOCALES (thèses, mémoires, rapports, documents sans
    DOI/PMID) : documentées au même niveau que la littérature
    internationale via ``document_type`` (``"these"``, ``"memoire"``,
    ``"rapport"``, ``"rapport_local"``, ``"communication"``,
    ``"document_local"``...), ``institution`` (université, laboratoire,
    ONG...) et ``fichier_local`` (chemin du document). Chaque preuve
    sans DOI/PMID reçoit un ``identifiant_interne`` généré
    (``WBF-2026-0001``) — **identifiant interne WebIntel, jamais un DOI
    ni un PMID** : il ne doit jamais être présenté ou cité comme un
    identifiant externe.
    """

    __tablename__ = "evidences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), default="publication", nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False, default="")
    doi: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    authors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # --- v2.2 : publications locales ---------------------------------
    #: Type fin de document : "article", "these", "memoire", "rapport",
    #: "rapport_local", "communication", "document_local",
    #: "travaux_universitaires"... (texte libre contrôlé à l'import).
    document_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Journal, université, laboratoire, ONG... (édition locale).
    institution: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Chemin du fichier local (PDF scanné, mémoire...), jamais en base.
    fichier_local: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Identifiant INTERNE WebIntel (ex. "WBF-2026-0001") — jamais un
    #: DOI/PMID. Unique, généré pour les preuves sans DOI ni PMID.
    identifiant_interne: Mapped[str | None] = mapped_column(
        String(32), unique=True, nullable=True
    )
    #: Langue du document (ISO 639-1 : "fr", "en"...).
    langue: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Pays d'édition / de soutenance (ex. "Burkina Faso").
    pays_edition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class ResearchObservation(Base):
    """Observation de laboratoire (journal d'expériences).

    Donnée EXPERIMENTALE et PRIVÉE par défaut (``is_private=True``) : elle
    n'est jamais exposée hors du laboratoire sans action explicite.
    """

    __tablename__ = "research_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    protocol_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    observed_date: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    laboratory: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="brut", nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Simulation(Base):
    """Résultat de simulation (PREDICTION — jamais une preuve expérimentale).

    `input_hash` est l'empreinte SHA-256 des entrées normalisées (JSON
    canonique) : deux simulations des mêmes entrées partagent le même
    hachage, ce qui permet la déduplication et l'audit.

    Journal d'audit immuable (Phase 13) : `executed_by` (qui a lancé le
    calcul), `run_at` (quand), `input_hash` (empreinte SHA-256 des
    entrées), `inputs` (entrées normalisées persistées — rejouabilité),
    `parameters` (paramètres), `result` (sorties), `engine_name` /
    `engine_version` (version du moteur). Ces colonnes ne sont JAMAIS
    modifiables ni supprimables via l'API : `lab.modifier_simulation`
    refuse toute écriture dessus et aucune route de suppression n'existe.
    """

    __tablename__ = "simulations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # P0-1 : project_id OBLIGATOIRE (NOT NULL). Une simulation est une
    # donnée de laboratoire privée, toujours rattachée à un projet :
    # les simulations orphelines (project_id NULL) étaient lisibles entre
    # projets par énumération d'ID. Les créations passent par
    # `projet_effectif_ou_404` (projet de la clé en mode serveur,
    # projet « Défaut » en mode local) ; les orphelins antérieurs sont
    # rattachés au premier projet par `_rattacher_simulations_orphelines`
    # dans `database/base.py` (idempotent, au démarrage).
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    compound_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    hypotheses: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    engine_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    units: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    executed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    references: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="exploratoire", nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Phase 10 — Connecteurs de sources externes (lecture seule)
# ---------------------------------------------------------------------------
# Règles dures :
# - jamais de copie aveugle d'une base externe : import sélectif, documenté,
#   avec provenance (qui, quand, quelle version, quelle licence) ;
# - licence obligatoire par connecteur, respect des CGU / robots.txt ;
# - les données importées sont NORMALISEES, jamais « scientifiquement
#   validées » (voir DataLevel).
# ---------------------------------------------------------------------------


class SourceConnecteur(Base):
    """Connecteur déclaré vers une source externe (lecture seule).

    La licence et la stratégie de mise à jour sont obligatoires : un
    connecteur sans licence documentée ne doit pas être activé.
    """

    __tablename__ = "source_connecteurs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nom: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    url: Mapped[str] = mapped_column(Text, nullable=True)
    licence: Mapped[str] = mapped_column(String(255), nullable=False)
    licence_url: Mapped[str] = mapped_column(Text, nullable=True)
    conditions_utilisation: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    version_connecteur: Mapped[str] = mapped_column(
        String(32), nullable=False, default="1.0"
    )
    statut: Mapped[str] = mapped_column(
        String(16), nullable=False, default="actif"
    )  # "actif" / "inactif"
    strategie_maj: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    dernier_import: Mapped[datetime | None] = mapped_column(
        TZDateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class ConnecteurCache(Base):
    """Cache des réponses HTTP des connecteurs (clé de requête unique).

    Évite de bombarder les sources externes : toute réponse est réutilisée
    jusqu'à ``expire_le`` (TTL, 24 h par défaut).
    """

    __tablename__ = "connecteur_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connecteur_nom: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    cle_requete: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    reponse_json: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    expire_le: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("connecteur_nom", "cle_requete",
                         name="uq_connecteur_cache_requete"),
    )


class ImportProvenance(Base):
    """Traçabilité fine des objets importés par un connecteur.

    Une ligne par objet importé : qui l'a fourni (connecteur), quand
    (date_import), quelle version des données et sous quelle licence.
    """

    __tablename__ = "import_provenance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_donnees: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )  # ex. "compound", "evidence"
    item_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True
    )
    connecteur_nom: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    date_import: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )
    version_donnees: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    licence: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )


# ---------------------------------------------------------------------------
# v2.1 — Modèle scientifique enrichi (append-only)
# ---------------------------------------------------------------------------
# Relations PROPRES (pas de gros JSON informe). Chaque relation importante
# pointe vers sa preuve via ``evidence_id`` (clé étrangère vers
# ``evidences.id``) : la chaîne
# plante → composé → partie → concentration → méthode d'extraction →
# publication → DOI est traçable de bout en bout.
#
# Discipline : les niveaux de données (``DataLevel``) restent strictement
# séparés — voir le docstring du module. Une cible documentée ou une
# activité rapportée n'est JAMAIS présentée comme « efficace », « sûre »
# ou « traitant » quoi que ce soit sans source expérimentale.
# ---------------------------------------------------------------------------


class CompoundTarget(Base):
    """Cible biologique documentée d'un composé.

    Ex. quercétine → COX-2 (inhibition, IC50 rapportée...). La cible est
    DOCUMENTÉE (publication citée), jamais « validée » par WebIntel.
    ``evidence_id`` pointe vers la preuve (``Evidence``) qui établit le
    lien — la chaîne composé → cible → publication → DOI est traçable.
    """

    __tablename__ = "compound_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    compound_id: Mapped[int] = mapped_column(
        ForeignKey("compounds.id"), nullable=False, index=True
    )
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )  # ex. "enzyme", "récepteur", "canal ionique"
    interaction_type: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # ex. "inhibition", "activation", "modulation", "liaison"
    affinity_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    affinity_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Preuve pointée (nullable) : la publication qui documente l'interaction.
    evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidences.id"), nullable=True
    )
    data_level: Mapped[str] = mapped_column(
        String(32), default=DataLevel.PUBLIE.value, nullable=False
    )
    # --- v2.2 : statut épistémique (colonne dédiée — DataLevel est figé) ---
    # "documente" : publication citée ; "infere" : déduit de liens
    # enregistrés ; "predit" : docking / similarité (data_level reste
    # "prediction") ; "calcule" : descripteur / estimation calculée.
    # Jamais NULL en pratique : les lignes antérieures sont migrées vers
    # "documente" par `_ensure_v22_science_columns()` (database/base.py).
    statut_epistemique: Mapped[str] = mapped_column(
        String(16), default=StatutEpistemique.DOCUMENTE.value, nullable=False
    )
    # Comment le statut a été établi (texte libre, ex. "docking Vina 1.2.3",
    # "déduit de l'analyse d'interactions du 2026-09-20").
    methode_statut: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "compound_id", "target_name", "interaction_type",
            name="uq_compound_target_link",
        ),
    )


class PlantActivity(Base):
    """Activité pharmacologique rapportée pour une plante.

    Ex. *Artemisia annua* → activité antipaludique (in vivo, modèle murin,
    résultat publié...). Donnée RAPPORTÉE : l'activité est décrite telle
    que publiée, avec son modèle et son type d'étude — jamais présentée
    comme « efficace » ou « traitant » une pathologie.
    ``evidence_id`` pointe vers la preuve qui établit l'activité.
    """

    __tablename__ = "plant_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id"), nullable=False, index=True
    )
    activity: Mapped[str] = mapped_column(String(255), nullable=False)
    # Type d'étude : "in_vitro", "in_vivo", "clinique", "ex_vivo", "autre".
    study_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Modèle d'étude : ex. "souris BALB/c infectées", "lignée MCF-7".
    study_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Résultat tel que publié (texte factuel, sans interprétation).
    published_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Preuve pointée (nullable) : la publication qui rapporte l'activité.
    evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidences.id"), nullable=True
    )
    data_level: Mapped[str] = mapped_column(
        String(32), default=DataLevel.PUBLIE.value, nullable=False
    )
    # --- v2.2 : statut épistémique (colonne dédiée — DataLevel est figé) ---
    # Voir CompoundTarget : même sémantique (documente / infere / predit /
    # calcule). Les lignes antérieures sont migrées vers "documente".
    statut_epistemique: Mapped[str] = mapped_column(
        String(16), default=StatutEpistemique.DOCUMENTE.value, nullable=False
    )
    methode_statut: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "plant_id", "activity", "study_type", "study_model",
            name="uq_plant_activity_link",
        ),
    )


class Study(Base):
    """Publication / étude scientifique structurée.

    Référence bibliographique normalisée (titre, auteurs, année, DOI,
    PMID, journal, type) — distincte de ``Evidence`` (preuve documentaire
    brute, qui peut être un extrait, une page web, une thèse...). Les deux
    se rejoignent par le DOI.
    """

    __tablename__ = "studies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    authors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    journal: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Type : "article", "revue", "these", "rapport", "preprint", "autre".
    study_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_studies_doi", "doi"),
        Index("ix_studies_year", "year"),
    )


class Provenance(Base):
    """Traçabilité fine d'une entité scientifique (v2.1).

    Complète ``ImportProvenance`` (orientée connecteurs) par une
    traçabilité générique : n'importe quelle entité (plante, composé,
    lien, cible, activité, étude) peut pointer vers sa source d'origine
    — qui l'a fournie, via quel connecteur et quelle version, quand, sous
    quelle licence. Journal append-only : on n'écrase jamais une ligne.
    """

    __tablename__ = "provenances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Entité tracée : ex. "plant", "compound", "plant_compound",
    # "compound_target", "plant_activity", "study", "evidence".
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Source déclarée par le chercheur ou le connecteur.
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connector_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    data_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    license: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )
    # --- v2.2 : dignité de provenance locale --------------------------
    # Une donnée de chercheur / labo / université / terrain remplit les
    # mêmes champs QUI/QUOI/QUAND/OÙ/SOURCE/COMMENT/OUTIL/VERSION/
    # PARAMÈTRES/NIVEAU DE PREUVE qu'une donnée internationale.
    #: QUI — personne / équipe ayant produit la donnée.
    acteur: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: OÙ — lieu (laboratoire, site de collecte, université...).
    lieu: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: COMMENT — commentaire libre du producteur.
    commentaire: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: OUTIL — instrument / logiciel utilisé (ex. "HPLC Agilent 1260").
    outil: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: PARAMÈTRES — paramètres de l'outil / du protocole (JSON).
    parametres: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: NIVEAU DE PREUVE — lecture du producteur (ex. "observation",
    #: "mesure répliquée", "résultat publié").
    niveau_preuve: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_provenances_entity", "entity_type", "entity_id"),
    )


class Dataset(Base):
    """Jeu de données importé par le chercheur.

    Conteneur de données de laboratoire : nom, description, empreinte
    SHA-256 du fichier importé, chemin de stockage. PRIVÉ par défaut
    (``is_private=True``) : un jeu de données n'est jamais exposé hors
    du projet sans action explicite — même discipline que les
    observations de laboratoire.
    """

    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# v2.2 — Échantillons / vouchers d'herbier + mesures expérimentales
# (append-only : nouvelles tables, FK nullables, aucune colonne
# non-nullable ajoutée aux tables existantes)
# ---------------------------------------------------------------------------


class Specimen(Base):
    """Échantillon / voucher d'herbier (donnée de terrain du chercheur).

    Ancre physique d'une donnée : une mesure expérimentale peut pointer
    vers le spécimen réel (``specimen_id``). ``data_level`` vaut toujours
    ``"experimental"`` ; privé par défaut (``is_private=True``) comme
    toute donnée de terrain. ``code_voucher`` (ex. "OUA-2026-0147")
    identifie le dépôt en herbier — un spécimen sans voucher est
    signalé par le contrôle qualité, jamais inventé.
    """

    __tablename__ = "specimens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id"), nullable=False, index=True
    )
    code_voucher: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    herbier_depot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    collecteur: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date_collecte: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    pays: Mapped[str | None] = mapped_column(String(64), nullable=True)
    localite: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    altitude_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    partie_collectee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phenologie: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes_terrain: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_paths: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidences.id"), nullable=True
    )
    is_private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    data_level: Mapped[str] = mapped_column(
        String(16), default="experimental", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class MesureExperimentale(Base):
    """Mesure expérimentale de laboratoire / terrain (chaîne de preuve).

    Chaîne RAW → NORMALISÉ → ANALYSE → DÉRIVÉ portée par
    ``niveau_chaine`` (``"brut"`` | ``"normalise"`` | ``"analyse"`` |
    ``"derive"``) : la promotion crée TOUJOURS une nouvelle ligne
    (append-only) pointant vers sa source via ``normalise_depuis_id`` —
    la donnée brute n'est jamais écrasée ni corrigée silencieusement.
    Unités incohérentes ou inconnues : signalées dans
    ``avertissements``, jamais corrigées.

    Privée par défaut (``is_private=True``), ``data_level`` vaut
    ``"experimental"`` pour ``brut``/``normalise``.
    """

    __tablename__ = "mesures_experimentales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    #: Chaîne de preuve : "brut" | "normalise" | "analyse" | "derive".
    niveau_chaine: Mapped[str] = mapped_column(
        String(16), default="brut", nullable=False, index=True
    )
    #: Ligne source (niveau précédent) — NULL pour une mesure brute.
    normalise_depuis_id: Mapped[int | None] = mapped_column(
        ForeignKey("mesures_experimentales.id"), nullable=True
    )
    sample: Mapped[str | None] = mapped_column(String(255), nullable=True)
    compound_id: Mapped[int | None] = mapped_column(
        ForeignKey("compounds.id"), nullable=True
    )
    compound_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dose: Mapped[float | None] = mapped_column(Float, nullable=True)
    dose_unite: Mapped[str | None] = mapped_column(String(32), nullable=True)
    concentration: Mapped[float | None] = mapped_column(Float, nullable=True)
    concentration_unite: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response: Mapped[float | None] = mapped_column(Float, nullable=True)
    response_unite: Mapped[str | None] = mapped_column(String(32), nullable=True)
    replicate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assay: Mapped[str | None] = mapped_column(String(255), nullable=True)
    organism: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cell_line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    time_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_unite: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Unité générique (colonne "unit" des fichiers d'import).
    unite: Mapped[str | None] = mapped_column(String(32), nullable=True)
    control: Mapped[str | None] = mapped_column(String(255), nullable=True)
    specimen_id: Mapped[int | None] = mapped_column(
        ForeignKey("specimens.id"), nullable=True
    )
    evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidences.id"), nullable=True
    )
    #: Avertissements non bloquants (unités inconnues/incohérentes...).
    avertissements: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    #: Provenance locale : QUI/QUOI/QUAND/OÙ/SOURCE/COMMENT/OUTIL/
    #: VERSION/PARAMÈTRES/NIVEAU DE PREUVE.
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    statut: Mapped[str] = mapped_column(String(32), default="brut", nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    data_level: Mapped[str] = mapped_column(
        String(16), default="experimental", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Phase v2.1 — Études de combinaison, analyses de synergie, docking (append-only)
# ---------------------------------------------------------------------------
# Règle d'or (honnêteté scientifique ABSOLUE) : ces modèles stockent des
# RÉSULTATS INFORMATIQUES (niveau ``prediction``) ou des analyses de
# DONNÉES EXPÉRIMENTALES fournies par le laboratoire. Aucun champ, aucun
# libellé ne prétend une efficacité thérapeutique, une innocuité ou une
# validation scientifique. Les formulations interdites (« efficace »,
# « sûr »...) sont bannies des rapports générés par
# ``webintel.science.combinaisons`` (testé automatiquement).
# ---------------------------------------------------------------------------


class CombinationStudy(Base):
    """Étude de combinaison de composés (MODE A : screening structural).

    Le chercheur sélectionne une plante (+ partie), plusieurs composés,
    une cible biologique éventuelle et des publications de référence ; le
    système produit un dossier d'analyse informatique en 12 points
    (``resultats``), toujours étiqueté « Résultat de screening
    informatique » / « Hypothèse à vérifier expérimentalement ».

    - ``inputs_hash`` : empreinte SHA-256 des entrées normalisées (JSON
      canonique) — mêmes entrées → mêmes sorties (reproductibilité).
    - ``data_level`` : toujours ``"prediction"`` (niveau épistémologique).
    - ``provenance`` : qui / quand / versions des bibliothèques.
    - Isolation projet : ``project_id`` obligatoire (FK ``projects.id``).
    """

    __tablename__ = "combination_studies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    plant_id: Mapped[int | None] = mapped_column(
        ForeignKey("plants.id"), nullable=True
    )
    plant_part: Mapped[str | None] = mapped_column(String(128), nullable=True)
    compound_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    parametres: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    resultats: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    statut: Mapped[str] = mapped_column(
        String(32), default="termine", nullable=False
    )  # "termine" | "partiel" (données manquantes) | "echoue"
    data_level: Mapped[str] = mapped_column(
        String(32), default="prediction", nullable=False
    )
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class SynergyAnalysis(Base):
    """Analyse de synergie (MODE B) sur DONNÉES EXPÉRIMENTALES du laboratoire.

    GARDE-FOU : une analyse de synergie ne peut JAMAIS être créée à partir
    de simples structures moléculaires — ``donnees`` doit contenir des
    mesures réelles (molécule A, molécule B, dose A, dose B, réponse
    mesurée). Sans les données requises par la méthode, l'analyse est
    refusée avec un message explicite (« données insuffisantes pour la
    méthode X ») — jamais de valeur inventée.

    - ``methode`` : "bliss" | "loewe" | "hsa" | "zip" (une méthode =
      un module ``webintel.science.synergie.<methode>``).
    - ``is_private`` : les données de laboratoire restent privées par
      défaut (séparation labo / public).
    - ``data_level`` : "prediction" — un score de synergie calculé reste
      une analyse informatique, pas une preuve.
    """

    __tablename__ = "synergy_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    methode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    donnees: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    parametres: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    resultats: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    statut: Mapped[str] = mapped_column(
        String(32), default="termine", nullable=False
    )  # "termine" | "refuse" (données insuffisantes)
    data_level: Mapped[str] = mapped_column(
        String(32), default="prediction", nullable=False
    )
    is_private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class DockingJob(Base):
    """Exécution (ou aperçu) d'un workflow de docking moléculaire.

    Étapes : récepteur → validation → ligand → préparation → paramètres →
    calcul → poses → scores → rapport. Enregistre le récepteur (empreinte
    SHA-256 du contenu PDB, jamais le contenu brut volumineux en clair
    seul), le ligand, les paramètres, la version de Vina, la version des
    bibliothèques, la méthode exacte, les résultats et le hash des entrées.

    - ``statut`` : "apercu" (moteur absent — scores=None, aucun faux
      score), "termine" (calcul réel via le moteur), "refuse" (entrées
      invalides ou moteur indisponible au moment de l'exécution).
    - ``data_level`` : "prediction" — un score de docking reste une
      prédiction informatique.
    """

    __tablename__ = "docking_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    recepteur_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recepteur_nom: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ligand_smiles: Mapped[str | None] = mapped_column(Text, nullable=True)
    ligand_nom: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parametres: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    moteur: Mapped[str | None] = mapped_column(String(128), nullable=True)
    moteur_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    versions_biblio: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    methode: Mapped[str | None] = mapped_column(Text, nullable=True)
    resultats: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    statut: Mapped[str] = mapped_column(
        String(32), default="apercu", nullable=False
    )  # "apercu" | "termine" | "refuse"
    data_level: Mapped[str] = mapped_column(
        String(32), default="prediction", nullable=False
    )
    execute_par: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# v2.2 — Extension modèle scientifique : terrain, ethnobotanique,
# phytochimie, pharmacognosie (append-only)
# ---------------------------------------------------------------------------
# RÈGLES DURES (mission WEBINTEL ULTIMATE — Burkina Faso) :
#
# - jamais de données inventées : toute donnée absente est affichée comme
#   absente (colonnes NULLABLE, jamais de valeur par défaut « devinée ») ;
# - la langue d'un nom vernaculaire n'est JAMAIS devinée (``langue``
#   nullable, registre ``LANGUES_BURKINA`` extensible) ;
# - jamais de fusion automatique de taxons ambigus (les noms vernaculaires
#   sont des lignes distinctes rattachées à une plante identifiée) ;
# - jamais de suppression silencieuse : ``supprime_le`` (soft-delete) sur
#   chaque table ; les suppressions sont tracées (qui, quand) ;
# - chaque enregistrement porte : source, auteur (``cree_par``), date
#   (``created_at``), niveau de preuve (``data_level``) et niveau de
#   confidentialité (``niveau_confidentialite``) ;
# - ``DataLevel`` n'est PAS modifié (figé par
#   ``test_cinq_niveaux_distincts_et_stables``) : les nuances fines vivent
#   dans ``statut_epistemique`` (v2.2, colonne dédiée sur CompoundTarget /
#   PlantActivity).
#
# Chaîne modélisée : PLANTE → ÉCHANTILLON → VOUCHER → EXTRACTION →
# EXTRAIT → FRACTION → COMPOSÉ (via ``FractionCompound``), et
# ÉCHANTILLON / EXTRAIT / FRACTION → ANALYSE CHIMIQUE → PICS.
# ---------------------------------------------------------------------------


class _TracabiliteTerrain:
    """Mixin : traçabilité + confidentialité + soft-delete.

    Chaque enregistrement de terrain porte : son projet (cloisonnement
    P0-1), sa source, son auteur, son niveau de preuve (``data_level``),
    son niveau de confidentialité et sa date de suppression logique
    (``supprime_le`` — ``None`` = actif). Les requêtes de lecture
    filtrent ``supprime_le IS NULL`` (jamais de suppression silencieuse).
    """

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False
    )
    # Source déclarée (texte libre : "enquête terrain Ouagadougou 2026",
    # "Flore du Burkina Faso", "littérature"...).
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Auteur de l'enregistrement (qui a saisi / collecté).
    cree_par: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Niveau de preuve (valeurs de DataLevel : publie / normalise /
    # hypothese / prediction / experimental).
    data_level: Mapped[str] = mapped_column(
        String(32), default=DataLevel.NORMALISE.value, nullable=False
    )
    # Niveau de confidentialité (valeurs de NiveauConfidentialite).
    niveau_confidentialite: Mapped[str] = mapped_column(
        String(32),
        default=NiveauConfidentialite.PROJET.value,
        nullable=False,
    )
    # Preuve documentaire pointée (publication, rapport d'enquête...).
    evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidences.id"), nullable=True
    )
    # Soft-delete : horodatage de suppression logique (None = actif).
    supprime_le: Mapped[datetime | None] = mapped_column(
        TZDateTime, nullable=True
    )
    # Qui a supprimé (traçabilité de la suppression).
    supprime_par: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class NomVernaculaire(_TracabiliteTerrain, Base):
    """Nom vernaculaire d'une plante, structuré et sourcé.

    ``nom_original`` : graphie telle que rapportée (jamais « corrigée »).
    ``nom_normalise`` : forme normalisée (minuscules, sans accents,
    espaces réduits) calculée à l'insertion — sert à la recherche.
    ``langue`` : code du registre ``LANGUES_BURKINA`` ou ``None``
    (JAMAIS devinée — « non renseignée » à l'affichage).
    ``orthographes_variantes`` : autres graphies attestées (liste).
    """

    __tablename__ = "noms_vernaculaires"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id"), nullable=False
    )
    nom_original: Mapped[str] = mapped_column(String(255), nullable=False)
    nom_normalise: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    langue: Mapped[str | None] = mapped_column(String(64), nullable=True)
    orthographes_variantes: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    region: Mapped[str | None] = mapped_column(String(255), nullable=True)
    localite: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_noms_vernaculaires_plant_id", "plant_id"),
        Index("ix_noms_vernaculaires_langue", "langue"),
        # Unicité de la clé d'idempotence (chantier PERSISTANCE +
        # PERFORMANCE) : sans elle, deux insertions concurrentes du même
        # nom créaient des doublons (course constatée :
        # ``MultipleResultsFound``). ``get_or_create`` attrape déjà
        # l'``IntegrityError`` et relit la ligne gagnante — il lui
        # manquait juste la contrainte pour se déclencher. Note : en
        # SQLite/PostgreSQL, deux ``langue`` NULL ne se heurtent pas
        # dans l'index unique (cas « non renseignée » : la protection
        # reste le SELECT initial de ``get_or_create``).
        UniqueConstraint(
            "plant_id", "nom_normalise", "langue",
            name="uq_noms_vernaculaires_unicite",
        ),
    )


class UsageTraditionnel(_TracabiliteTerrain, Base):
    """Usage traditionnel rapporté d'une plante (ethnobotanique).

    Donnée RAPPORTÉE telle qu'enquêtée ou publiée : l'affichage porte
    OBLIGATOIREMENT le bandeau « USAGE TRADITIONNEL RAPPORTÉ » — jamais
    « efficacité démontrée », jamais une allégation thérapeutique.
    ``data_level`` par défaut : ``experimental`` (enquête de terrain).
    Données d'enquête sensibles : ``niveau_confidentialite`` par défaut
    ``projet`` (modifiable vers ``restreint`` / ``confidentiel``).
    """

    __tablename__ = "usages_traditionnels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id"), nullable=False
    )
    # Usage tel que rapporté (texte verbatim, jamais reformulé en
    # allégation).
    usage: Mapped[str] = mapped_column(Text, nullable=False)
    partie_utilisee: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    preparation: Mapped[str | None] = mapped_column(Text, nullable=True)
    forme: Mapped[str | None] = mapped_column(String(255), nullable=True)
    voie_administration: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    contexte: Mapped[str | None] = mapped_column(Text, nullable=True)
    localite: Mapped[str | None] = mapped_column(String(255), nullable=True)
    groupe_population: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    publication: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Date de l'enquête ou de la publication (nullable : jamais inventée).
    date_enquete: Mapped[datetime | None] = mapped_column(
        TZDateTime, nullable=True
    )
    # Enquête de terrain : informateur anonymisé (code) et consentement.
    informateur_code: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    consentement: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )

    __table_args__ = (
        Index("ix_usages_traditionnels_plant_id", "plant_id"),
    )


class Voucher(_TracabiliteTerrain, Base):
    """Spécimen d'herbier (voucher) déposé dans une institution.

    ``code_voucher`` : cote du spécimen (ex. "OUA-2026-0147").
    ``institution`` : herbier de dépôt (ex. "Herbier National du Burkina
    Faso"). ``code_herbier`` : acronyme de l'herbier (ex. "OUA").
    """

    __tablename__ = "vouchers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_voucher: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    institution: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    code_herbier: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )


class Echantillon(_TracabiliteTerrain, Base):
    """Échantillon de plante collecté sur le terrain.

    ``code_echantillon`` : identifiant de l'échantillon (unique par
    projet). Localisation : pays / région / province / commune /
    localité / site (textes, jamais inventés). GPS : ``latitude`` /
    ``longitude`` NULLABLES — jamais (0, 0) par défaut ; ``precision_gps``
    conserve la précision déclarée (ex. "±10 m") ; les exports publics ne
    divulguent JAMAIS les coordonnées précises (voir
    ``terrain.export_public_plante``).
    """

    __tablename__ = "echantillons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id"), nullable=False
    )
    voucher_id: Mapped[int | None] = mapped_column(
        ForeignKey("vouchers.id"), nullable=True
    )
    code_echantillon: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    collecteur: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    date_collecte: Mapped[datetime | None] = mapped_column(
        TZDateTime, nullable=True
    )
    pays: Mapped[str | None] = mapped_column(String(128), nullable=True)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    province: Mapped[str | None] = mapped_column(String(128), nullable=True)
    commune: Mapped[str | None] = mapped_column(String(128), nullable=True)
    localite: Mapped[str | None] = mapped_column(String(255), nullable=True)
    site: Mapped[str | None] = mapped_column(Text, nullable=True)
    # GPS : nullable, jamais inventé. Précision conservée telle que déclarée.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision_gps: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    altitude_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    habitat: Mapped[str | None] = mapped_column(Text, nullable=True)
    partie: Mapped[str | None] = mapped_column(String(255), nullable=True)
    etat: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sechage: Mapped[str | None] = mapped_column(Text, nullable=True)
    stockage: Mapped[str | None] = mapped_column(Text, nullable=True)
    institution: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    responsable: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", "code_echantillon",
            name="uq_echantillon_projet_code",
        ),
        Index("ix_echantillons_plant_id", "plant_id"),
        Index("ix_echantillons_voucher_id", "voucher_id"),
    )


class Extraction(_TracabiliteTerrain, Base):
    """Extraction réalisée sur un échantillon (maillon de la chaîne).

    Relie l'échantillon de terrain à l'extrait obtenu : méthode, solvant,
    rendement — jamais de valeur inventée (tout est nullable sauf les
    clés).
    """

    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    echantillon_id: Mapped[int] = mapped_column(
        ForeignKey("echantillons.id"), nullable=False
    )
    nom: Mapped[str | None] = mapped_column(String(255), nullable=True)
    methode: Mapped[str | None] = mapped_column(Text, nullable=True)
    solvant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date_extraction: Mapped[datetime | None] = mapped_column(
        TZDateTime, nullable=True
    )
    operateur: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    rendement: Mapped[float | None] = mapped_column(Float, nullable=True)
    rendement_unite: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_extractions_echantillon_id", "echantillon_id"),
    )


class Extrait(_TracabiliteTerrain, Base):
    """Extrait obtenu d'une extraction (maillon de la chaîne)."""

    __tablename__ = "extraits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    extraction_id: Mapped[int] = mapped_column(
        ForeignKey("extractions.id"), nullable=False
    )
    code_extrait: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    masse: Mapped[float | None] = mapped_column(Float, nullable=True)
    masse_unite: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    stockage: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_extraits_extraction_id", "extraction_id"),
    )


class Fraction(_TracabiliteTerrain, Base):
    """Fraction issue du fractionnement d'un extrait (maillon de la chaîne)."""

    __tablename__ = "fractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    extrait_id: Mapped[int] = mapped_column(
        ForeignKey("extraits.id"), nullable=False
    )
    code_fraction: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    methode_fractionnement: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    masse: Mapped[float | None] = mapped_column(Float, nullable=True)
    masse_unite: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_fractions_extrait_id", "extrait_id"),
    )


class FractionCompound(_TracabiliteTerrain, Base):
    """Lien fraction → composé identifié (ou tenté) dans la fraction.

    ``identification_statut`` : "identifie" (standard injecté / NMR...),
    "tentative" (comparaison littérature — reste une hypothèse),
    "inconnu" (jamais deviné).
    """

    __tablename__ = "fraction_compounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fraction_id: Mapped[int] = mapped_column(
        ForeignKey("fractions.id"), nullable=False
    )
    compound_id: Mapped[int] = mapped_column(
        ForeignKey("compounds.id"), nullable=False
    )
    identification_statut: Mapped[str] = mapped_column(
        String(32),
        default=StatutIdentification.INCONNU.value,
        nullable=False,
    )
    commentaire: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "fraction_id", "compound_id",
            name="uq_fraction_compound_link",
        ),
        Index("ix_fraction_compounds_fraction_id", "fraction_id"),
        Index("ix_fraction_compounds_compound_id", "compound_id"),
    )


class AnalyseChimique(_TracabiliteTerrain, Base):
    """Analyse phytochimique (HPLC, GC-MS, LC-MS, NMR, UV-Vis, TLC...).

    Rattachée à AU MOINS un maillon de la chaîne : ``echantillon_id``,
    ``extrait_id`` ou ``fraction_id`` (service : ``ValueError`` si les
    trois sont absents). ``conditions`` : paramètres instrumentaux (JSON).
    Le fichier brut n'est jamais stocké en base : seul son chemin et son
    SHA-256 sont conservés (traçabilité). ``data_level`` par défaut :
    ``experimental``.
    """

    __tablename__ = "analyses_chimiques"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    echantillon_id: Mapped[int | None] = mapped_column(
        ForeignKey("echantillons.id"), nullable=True
    )
    extrait_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraits.id"), nullable=True
    )
    fraction_id: Mapped[int | None] = mapped_column(
        ForeignKey("fractions.id"), nullable=True
    )
    methode: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    conditions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    date_analyse: Mapped[datetime | None] = mapped_column(
        TZDateTime, nullable=True
    )
    operateur: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    laboratoire: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    fichier_brut_path: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    fichier_brut_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    publication: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_analyses_chimiques_echantillon_id", "echantillon_id"),
        Index("ix_analyses_chimiques_extrait_id", "extrait_id"),
        Index("ix_analyses_chimiques_fraction_id", "fraction_id"),
    )


class PicChromatographique(Base):
    """Pic d'un chromatogramme / signal d'une analyse chimique.

    ``temps_retention`` (min), ``aire``, ``hauteur``, ``mz`` (m/z) :
    mesures brutes, NULLABLES si non mesurées. ``identification_statut``
    : "identifie" / "tentative" / "inconnu" (défaut "inconnu" — jamais
    d'identification inventée). ``compound_id`` : composé identifié, si
    l'identification est établie.
    """

    __tablename__ = "pics_chromatographiques"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analyse_id: Mapped[int] = mapped_column(
        ForeignKey("analyses_chimiques.id"), nullable=False
    )
    temps_retention: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    aire: Mapped[float | None] = mapped_column(Float, nullable=True)
    hauteur: Mapped[float | None] = mapped_column(Float, nullable=True)
    mz: Mapped[float | None] = mapped_column(Float, nullable=True)
    identification_statut: Mapped[str] = mapped_column(
        String(32),
        default=StatutIdentification.INCONNU.value,
        nullable=False,
    )
    compound_id: Mapped[int | None] = mapped_column(
        ForeignKey("compounds.id"), nullable=True
    )
    commentaire: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_pics_chromatographiques_analyse_id", "analyse_id"),
    )


class Pharmacognosie(_TracabiliteTerrain, Base):
    """Fiche pharmacognosie d'une plante (une ligne par plante).

    Sections : identification macroscopique / microscopique,
    organoleptique, partie utilisée, authentification,
    substitution / adultération, séchage, stockage, préparation.
    Toute section absente reste ``None`` et est affichée « non
    renseignée » — jamais inventée.
    """

    __tablename__ = "pharmacognosie"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id"), nullable=False, unique=True
    )
    identification_macro: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    identification_micro: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    organoleptique: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    partie_utilisee: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    authentification: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    substitution_adulteration: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    sechage: Mapped[str | None] = mapped_column(Text, nullable=True)
    stockage: Mapped[str | None] = mapped_column(Text, nullable=True)
    preparation: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_pharmacognosie_plant_id", "plant_id"),
    )
