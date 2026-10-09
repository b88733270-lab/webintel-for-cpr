"""Modèles hors-ligne / synchronisation — WebIntel.

Tables (créées par ``Base.metadata.create_all``, donc sans migration) :

- ``file_synchronisation`` : file générique des modifications effectuées
  hors connexion (``entity_type`` / ``entity_id`` / ``operation`` /
  ``payload`` JSON). Statuts : ``en_attente`` → ``envoye`` | ``echoue``
  | ``conflit``. Un conflit n'est JAMAIS résolu automatiquement : il
  attend un choix explicite de l'utilisateur (page « Synchronisation »).
- ``resolutions_conflit`` : journal append-only des résolutions
  (qui, quand, quoi, ancienne → nouvelle valeur).
- ``collectes_terrain`` : saisies de terrain offline (photo, GPS,
  localité, notes, échantillon, voucher, partie utilisée). Le GPS précis
  des lignes ``confidentiel`` n'est jamais exposé sans permission
  (voir ``webintel.offline.cartographie``).

Portable SQLite / PostgreSQL : ``JSON`` et ``TZDateTime`` uniquement.
Ce module n'importe PAS ``webintel.database.models`` (convention des
modèles scientifiques : FK en chaînes).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from webintel.core.utils import utcnow
from webintel.database.base import Base, TZDateTime


class StatutSync(str, Enum):
    """Statut d'une entrée de la file de synchronisation."""

    EN_ATTENTE = "en_attente"
    ENVOYE = "envoye"
    ECHOUE = "echoue"
    CONFLIT = "conflit"


class OperationSync(str, Enum):
    """Opération enregistrée dans la file."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class NiveauConfidentialite(str, Enum):
    """Niveau de confidentialité d'une collecte de terrain.

    - ``public`` : GPS exact visible par tous ;
    - ``restreint`` : GPS flouté (grille ~0,5°) pour les non-auteurs ;
    - ``confidentiel`` : GPS masqué pour les non-auteurs (jamais exposé
      en clair — ni dans l'UI, ni dans la carte, ni dans les exports
      partagés).
    """

    PUBLIC = "public"
    RESTREINT = "restreint"
    CONFIDENTIEL = "confidentiel"


class ChoixResolution(str, Enum):
    """Choix explicite de l'utilisateur lors d'un conflit."""

    LOCAL = "local"  # garder la version locale, l'envoyer au distant
    DISTANT = "distant"  # adopter la version distante localement
    FUSION = "fusion"  # valeurs fusionnées manuellement par l'utilisateur


class FileSynchronisation(Base):
    """Une modification locale en attente de synchronisation."""

    __tablename__ = "file_synchronisation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Type d'entité générique : "collecte", "plante", ...
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    # Charge utile : représentation JSON de l'entité après modification
    # locale (create/update) ; {"id": ...} pour delete.
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Version locale au moment de la mise en file (``updated_at`` de
    # l'entité, ISO) — référence pour détecter un changement distant.
    base_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Version distante reçue lors d'un conflit (affichage LOCAL vs DISTANT).
    remote_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    auteur: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    statut: Mapped[str] = mapped_column(
        String(16), nullable=False, default=StatutSync.EN_ATTENTE.value
    )
    tentative: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    erreur: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cloisonnement projet (P0) : la file est filtrée par projet dans
    # l'UI — un projet ne voit jamais la file d'un autre. NULL =
    # entrée globale (ex. plante) ou antérieure au cloisonnement.
    project_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=True, index=True
    )
    # Résolution (renseigné après un choix explicite) :
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    choix_resolution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    auteur_resolution: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ResolutionConflit(Base):
    """Journal append-only des résolutions de conflits (qui/quand/quoi).

    Chaque résolution explicite (local / distant / fusion manuelle) y est
    consignée avec les anciennes et nouvelles valeurs — traçabilité
    complète, aucun écrasement silencieux.
    """

    __tablename__ = "resolutions_conflit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("file_synchronisation.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    auteur: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    resolved_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )
    choix: Mapped[str] = mapped_column(String(16), nullable=False)
    anciennes_valeurs: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    nouvelles_valeurs: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    commentaire: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cloisonnement projet (P0) : copié de l'entrée de file résolue.
    project_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=True, index=True
    )


#: Colonnes à ajouter idempotemment aux bases existantes (portable
#: SQLite / PostgreSQL) — voir ``_ensure_offline_sync_columns`` dans
#: ``webintel/database/base.py``.
COLONNES_MIGRATION: dict[str, dict[str, str]] = {
    "file_synchronisation": {"project_id": "INTEGER"},
    "resolutions_conflit": {"project_id": "INTEGER"},
}


class CollecteTerrain(Base):
    """Saisie de collecte de terrain, créée hors connexion.

    Niveau de données : observation de terrain (donnée expérimentale /
    d'observation au sens de ``DataLevel`` — saisie locale du chercheur,
    jamais confondue avec une donnée publiée). Les photos sont stockées
    dans un répertoire local (voir ``webintel.offline.photos``) ; la
    colonne ``photos`` ne contient que des métadonnées
    ``[{fichier, miniature, legende, taille_octets}]``.
    """

    __tablename__ = "collectes_terrain"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Cloisonnement projet (P0) : comme les échantillons de terrain,
    # une collecte appartient au projet de la session UI.
    project_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=True
    )
    plante_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("plants.id"), nullable=True
    )
    # Nom saisi librement quand la plante n'est pas (encore) référencée.
    nom_plante_saisi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    localite: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision_gps_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    echantillon_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    voucher: Mapped[str | None] = mapped_column(String(128), nullable=True)
    partie_utilisee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    photos: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    niveau_confidentialite: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NiveauConfidentialite.RESTREINT.value
    )
    auteur: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
