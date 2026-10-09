"""Modèles SQLAlchemy — schéma exact du contrat §4 d'architecture.md.

Toutes les tables ont `id` (Integer PK) et `created_at` (DateTime, UTC).
Colonnes JSON : `sqlalchemy.JSON` (portable SQLite/PostgreSQL).
Horodatages : `TZDateTime` = `DateTime(timezone=True)` — TIMESTAMPTZ sous
PostgreSQL, DATETIME ISO sous SQLite (valeurs UTC tz-aware partout).
Historique append-only : on n'écrase jamais snapshots/observations/événements.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
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

# Alias de compatibilité : TZDateTime reste importable depuis
# `webintel.database.models` (définition déplacée dans `database.base`
# pour que `webintel.science` l'utilise sans import circulaire).


def _default_crawl_progress() -> dict:
    return {
        "discovered": 0,
        "visited": 0,
        "remaining": 0,
        "errors": 0,
        "started_at": None,
        "elapsed_s": 0.0,
        "speed_ppm": 0.0,
        "bytes": 0,
    }


# Horodatage UTC portable : voir `webintel.database.base.TZDateTime`
# (alias réexporté ici pour compatibilité avec les imports existants).


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    quotas: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # "crawl"|"monitor_run"|"analyze"
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # JobStatus (str)
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    progress: Mapped[dict] = mapped_column(JSON, default=_default_crawl_progress, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class PageSnapshot(Base):
    __tablename__ = "page_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    page_type: Mapped[str] = mapped_column(String(32), nullable=False)
    page_type_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    html_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # SnapshotPayload
    keep_html: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_html: Mapped[str | None] = mapped_column(Text, nullable=True)  # seulement si keep_html
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_page_snapshots_project_canonical_fetched", "project_id", "canonical_url", "fetched_at"),
    )


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(255), nullable=False)  # clé stable, unique/projet
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)  # EntityType (str)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    sources: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # liste d'URLs
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "entity_key", name="uq_entities_project_key"),
    )


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), nullable=False)
    field: Mapped[str] = mapped_column(String(255), nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("page_snapshots.id"), nullable=True
    )
    # Provenance directe (v1.1.1) : chaque changement est attribué à la
    # source qui l'a produit (ex. baisse de prix vue sur le site A, puis B).
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id"), nullable=True
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_domain: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_observations_entity_observed", "entity_id", "observed_at"),
    )


class Source(Base):
    """Source d'observation (v1.1.1) : une boutique, un site, un vendeur.

    Une source est identifiée par son `canonical_host` (hôte normalisé,
    unique) — pas par un simple domaine racine. Les observations
    (`SourceObservation`) et l'historique (`Observation`) y sont rattachés
    via `source_id`, ce qui rend chaque donnée traçable jusqu'à sa source.
    """

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_host: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seller: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # `metadata` est un nom réservé par l'API déclarative de SQLAlchemy :
    # la colonne s'appelle `metadata` en base, l'attribut `meta`.
    meta: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)


class SourceObservation(Base):
    """Observation d'une valeur métier PAR SOURCE (P0-3, durci en v1.1.1).

    Une entité logique (ex. un produit identifié par sa clé stable) observée
    sur N sources donne N observations par champ suivi : on peut comparer
    « 499 € sur site A, 450 € sur site B, 430 € sur site C » au lieu d'une
    valeur globale fusionnée.

    - `field` : champ suivi (`price`, `availability`, `name`, ...) ;
    - `value` : valeur observée (JSON ; ex. `{"amount": 499.0, "currency": "EUR"}`) ;
    - `value_hash` : sha256 de la valeur normalisée (JSON canonique) —
      avec la contrainte unique `(entity_id, source_id, field, value_hash)`,
      l'écriture est idempotente sous concurrence (`ON CONFLICT DO NOTHING`)
      : deux workers ne peuvent pas créer deux fois la même observation ;
    - `source_id` : FK vers `Source` (rattachement fort) ; `source_domain`
      reste dénormalisé pour l'affichage et les regroupements ;
    - écriture « compacte » : une ligne par valeur DISTINCTE — la répétition
      d'une valeur déjà vue ne crée pas de doublon (elle rafraîchit
      `observed_at`, qui devient la date de dernière observation).

    À ne pas confondre avec `Observation` (journal des CHANGEMENTS de champs
    au niveau de l'entité, avec old/new value, utilisé par `/history`).
    """

    __tablename__ = "source_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), nullable=False)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id"), nullable=True
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    field: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[dict | list | str | float | int | bool | None] = mapped_column(
        JSON, nullable=True
    )
    value_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("page_snapshots.id"), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index(
            "ix_source_observations_entity_field_domain",
            "entity_id", "field", "source_domain",
        ),
        Index(
            "ix_source_observations_entity_source",
            "entity_id", "source_id",
        ),
        UniqueConstraint(
            "entity_id", "source_id", "field", "value_hash",
            name="uq_source_observations_entity_source_field_vhash",
        ),
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)  # EventType (str)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    importance: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_events_project_created", "project_id", "created_at"),
    )


class Monitor(Base):
    __tablename__ = "monitors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)  # url|domain|product|price|keyword
    target: Mapped[str] = mapped_column(Text, nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # rules, webhook_url...
    last_run_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_monitors_active_last_run", "active", "last_run_at"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    monitor_id: Mapped[int | None] = mapped_column(ForeignKey("monitors.id"), nullable=True)
    rule: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)  # "log"|"webhook"
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # "sent"|"failed"|"pending"
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # entities|events|snapshots
    format: Mapped[str] = mapped_column(String(16), nullable=False)  # "json"|"csv"|"xlsx"
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # "ready"|"failed"
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)


# Durée de vie d'une session du wizard d'étude (assistant /etudes/*).
# Fenêtre glissante : chaque écriture dans la session repousse l'échéance.
DUREE_WIZARD_SESSION_JOURS = 30


def _expiration_wizard_session() -> datetime:
    return utcnow() + timedelta(days=DUREE_WIZARD_SESSION_JOURS)


class WizardSession(Base):
    """État persistant du wizard de création d'étude (``/etudes/*``).

    Remplace l'ancien dict en mémoire du processus : une étude en cours
    survit au rechargement de page, à la fermeture du navigateur et au
    redémarrage du serveur. Isolation stricte : la session est rattachée
    à un ``project_id`` (NOT NULL) + une identité ``owner`` — l'UUID seul
    ne suffit plus, chaque accès vérifie le projet de la requête
    (accès croisé → 403). Les sessions expirées sont purgées
    (``purger_sessions_expirees``).
    """

    __tablename__ = "wizard_sessions"

    session_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    study_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="en_cours", nullable=False
    )  # "en_cours"|"terminee"
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=_expiration_wizard_session, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        Index("ix_wizard_sessions_project", "project_id"),
        Index("ix_wizard_sessions_expires", "expires_at"),
    )


# ---------------------------------------------------------------------------
# Favoris & collections (domaine web généraliste — organisation locale des
# résultats : entités, URLs analysées, rapports, recherches).
# ---------------------------------------------------------------------------

#: Types d'objets pouvant être mis en favori / rangés dans une collection.
TYPES_OBJET_FAVORI = ("entite", "url", "rapport", "recherche")


class Collection(Base):
    """Collection nommée d'un projet : regroupe des favoris.

    Isolation stricte : chaque collection appartient à UN projet
    (``project_id`` NOT NULL). Le nom est unique par projet.
    Supprimer une collection ne supprime jamais ses favoris : ils
    redeviennent des favoris hors collection (``collection_id`` NULL,
    géré par le service UI — aucune suppression silencieuse).
    """

    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    couleur: Mapped[str | None] = mapped_column(String(32), nullable=True)
    icone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("project_id", "nom", name="uq_collections_project_nom"),
        Index("ix_collections_project", "project_id"),
    )


class Favorite(Base):
    """Favori (élément de collection) : référence à un objet du projet.

    - ``collection_id`` NULLABLE : un favori peut exister hors collection ;
    - ``type_objet`` : ``entite`` | ``url`` | ``rapport`` | ``recherche`` ;
    - ``reference`` : identifiant de l'objet (id) ou URL, en texte ;
    - unicité : pas deux fois le même objet (type + référence) dans la
      MÊME collection. Pour les favoris hors collection (``collection_id``
      NULL), SQL ne contraint pas les NULL : le service UI refuse aussi
      les doublons hors collection (vérification applicative).
    """

    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_id: Mapped[int | None] = mapped_column(
        ForeignKey("collections.id"), nullable=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    type_objet: Mapped[str] = mapped_column(String(32), nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    titre: Mapped[str] = mapped_column(String(500), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "collection_id", "type_objet", "reference",
            name="uq_favorites_collection_objet",
        ),
        Index("ix_favorites_project", "project_id"),
        Index("ix_favorites_collection", "collection_id"),
    )


# Alias explicite : un favori rangé dans une collection en est un élément.
CollectionItem = Favorite
