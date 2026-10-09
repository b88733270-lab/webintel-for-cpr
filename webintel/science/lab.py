"""Laboratoire / projet — Phase 8 (backend).

Journal de laboratoire privé, versionné, et publication explicite.

Trois modèles :

- ``LabNote`` : note de laboratoire (journal d'expériences, protocole,
  résultat brut...). Donnée EXPERIMENTALE et PRIVÉE au projet par défaut.
- ``LabNoteVersion`` : historique immuable des versions d'une note.
  Règle d'or : une note n'est JAMAIS modifiée en silence —
  ``editer_note()`` stocke un instantané de l'ancien contenu dans une
  nouvelle ``LabNoteVersion`` et incrémente ``version_courante``.
- ``PublicationRecord`` : traçabilité des publications EXPLICITES.
  Rendre public un item (observation, simulation ou note) ne se fait
  jamais automatiquement : seul ``publier_item()`` crée une
  ``PublicationRecord``, et un item publié devient en lecture seule
  (toute modification lève ``ValueError`` → 409, converti en 409 HTTP
  par le routeur).

Conventions (même discipline que ``webintel.science.service``) :

- la session SQLAlchemy est TOUJOURS le premier argument ;
- AUCUNE fonction ne committe : l'appelant (routeur) committe ;
- écritures idempotentes quand un identifiant naturel existe ;
- niveaux de données strictement séparés : une note de laboratoire est
  une donnée ``experimental``, jamais une donnée publiée.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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
from webintel.science.models import (
    DataLevel,
    PlantCompound,
    ResearchObservation,
    Simulation,
)

__all__ = [
    "LabNote",
    "LabNoteVersion",
    "PublicationRecord",
    "TYPES_PUBLIABLES",
    "creer_note",
    "editer_note",
    "get_note",
    "list_notes",
    "versions_note",
    "get_item",
    "publier_item",
    "est_publie",
    "modifier_observation",
    "modifier_simulation",
    "tableau_de_bord",
]


class LabNote(Base):
    """Note de laboratoire (journal d'expériences).

    Donnée EXPERIMENTALE et PRIVÉE au projet (``project_id`` obligatoire) :
    elle n'est jamais exposée hors du laboratoire sans publication
    explicite (``publier_item()``). Le contenu courant porte toujours la
    version ``version_courante`` ; les anciennes versions sont conservées
    dans ``LabNoteVersion`` (jamais de modification silencieuse).
    """

    __tablename__ = "lab_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False
    )
    auteur: Mapped[str | None] = mapped_column(String(255), nullable=True)
    titre: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    contenu: Mapped[str | None] = mapped_column(Text, nullable=True)
    pieces_jointes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Niveau de données : toujours « experimental » — une note de
    # laboratoire est une mesure / une trace, jamais une donnée publiée.
    data_level: Mapped[str] = mapped_column(
        String(32), default=DataLevel.EXPERIMENTAL.value, nullable=False
    )
    version_courante: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_lab_notes_project_id", "project_id"),
    )


class LabNoteVersion(Base):
    """Instantané immuable d'une version antérieure d'une note.

    Créée par ``editer_note()`` AVANT d'écraser le contenu courant :
    l'historique complet d'une note est consultable via
    ``versions_note()``. Une version existante n'est jamais modifiée.
    """

    __tablename__ = "lab_note_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note_id: Mapped[int] = mapped_column(
        ForeignKey("lab_notes.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    titre: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    contenu: Mapped[str | None] = mapped_column(Text, nullable=True)
    pieces_jointes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    cree_le: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("note_id", "version", name="uq_lab_note_version"),
        Index("ix_lab_note_versions_note_id", "note_id"),
    )


class PublicationRecord(Base):
    """Trace d'une publication EXPLICITE d'un item de laboratoire.

    Un item publié (``item_type`` ∈ observation | simulation | note) devient
    en lecture seule ; cette ligne en garde la preuve (qui, quand, depuis
    quel projet, avec quel commentaire). La publication n'est JAMAIS
    automatique : seul ``publier_item()`` crée cette ligne.
    """

    __tablename__ = "publication_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # « observation » | « simulation » | « note »
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Projet source : d'où provient l'item au moment de la publication.
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False
    )
    publie_par: Mapped[str | None] = mapped_column(String(255), nullable=True)
    publie_le: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    commentaire: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Un item n'est publié qu'une fois : la republication lève 409.
        UniqueConstraint("item_type", "item_id", name="uq_publication_item"),
        Index("ix_publication_records_project_id", "project_id"),
    )


#: Types d'items publiables (et modifiables avec garde-fou).
TYPES_PUBLIABLES = ("observation", "simulation", "note")

_MODELES_PAR_TYPE = {
    "observation": ResearchObservation,
    "simulation": Simulation,
    "note": LabNote,
}


def _modele_ou_422(item_type: str):
    """Retourne le modèle d'un type d'item, ou lève `ValueError` (422)."""
    try:
        return _MODELES_PAR_TYPE[item_type]
    except KeyError:
        raise ValueError(
            "Type d'item invalide : « "
            + str(item_type)
            + " » (attendu : "
            + ", ".join(TYPES_PUBLIABLES)
            + ")."
        ) from None


def _item_ou_404(session, item_type: str, item_id: int):
    """Retourne l'item demandé, ou lève `ValueError` (404 côté routeur)."""
    modele = _modele_ou_422(item_type)
    item = session.get(modele, item_id)
    if item is None:
        raise ValueError(
            f"Item introuvable : type={item_type}, id={item_id}."
        )
    return item


def get_item(session, item_type: str, item_id: int):
    """Retourne l'item publiable demandé (observation, simulation ou note).

    Lève ``ValueError`` si le type est invalide (→ 422) ou si l'item est
    introuvable (→ 404). Ne committe jamais.
    """
    return _item_ou_404(session, item_type, item_id)


def _garde_lecture_seule(session, item_type: str, item_id: int) -> None:
    """Refuse la modification d'un item publié (donnée publiée en lecture seule)."""
    if est_publie(session, item_type, item_id):
        raise ValueError(
            "Donnée publiée en lecture seule : cet item a été publié et "
            "ne peut plus être modifié."
        )


# ---------------------------------------------------------------------------
# Notes de laboratoire
# ---------------------------------------------------------------------------

def creer_note(
    session,
    project_id: int,
    *,
    titre: str = "",
    contenu: str | None = None,
    auteur: str | None = None,
    pieces_jointes: list | None = None,
) -> LabNote:
    """Crée une note de laboratoire (version 1, privée au projet).

    Donnée ``experimental`` par défaut. Ne committe jamais.
    """
    note = LabNote(
        project_id=project_id,
        titre=titre or "",
        contenu=contenu,
        auteur=auteur,
        pieces_jointes=list(pieces_jointes or []),
        data_level=DataLevel.EXPERIMENTAL.value,
        version_courante=1,
    )
    session.add(note)
    session.flush()
    return note


def editer_note(
    session,
    note_id: int,
    *,
    titre: str | None = None,
    contenu: str | None = None,
    pieces_jointes: list | None = None,
) -> LabNote:
    """Édite une note SANS jamais modifier en silence.

    Crée d'abord une ``LabNoteVersion`` (instantané de l'ancien contenu,
    numéro = ``version_courante``), puis écrase le contenu courant et
    incrémente ``version_courante``. Lève ``ValueError`` si la note est
    introuvable, ou si elle est publiée (lecture seule). Ne committe jamais.
    """
    note = session.get(LabNote, note_id)
    if note is None:
        raise ValueError(f"Note de laboratoire introuvable : id={note_id}.")
    _garde_lecture_seule(session, "note", note_id)

    snapshot = LabNoteVersion(
        note_id=note.id,
        version=note.version_courante,
        titre=note.titre,
        contenu=note.contenu,
        pieces_jointes=list(note.pieces_jointes or []),
    )
    session.add(snapshot)
    if titre is not None:
        note.titre = titre
    if contenu is not None:
        note.contenu = contenu
    if pieces_jointes is not None:
        note.pieces_jointes = list(pieces_jointes)
    note.version_courante = note.version_courante + 1
    session.flush()
    return note


def get_note(session, note_id: int) -> LabNote | None:
    """Retourne la note ``note_id`` (ou ``None``)."""
    return session.get(LabNote, note_id)


def list_notes(
    session, project_id: int | None,
    limit: int | None = None, offset: int = 0,
) -> list[LabNote]:
    """Liste les notes du projet (antichronologique).

    ``project_id=None`` : toutes les notes (mode local sans cloisonnement),
    comme ``list_observations`` dans ``routers/science.py``.
    ``limit``/``offset`` : pagination (``None`` = tout, historique).
    """
    requete = session.query(LabNote)
    if project_id is not None:
        requete = requete.filter(LabNote.project_id == project_id)
    requete = requete.order_by(LabNote.id.desc()).offset(max(0, offset))
    if limit is not None:
        requete = requete.limit(max(1, limit))
    return requete.all()


def versions_note(session, note_id: int) -> list[LabNoteVersion]:
    """Historique des versions d'une note (ordre croissant).

    Ne contient que les instantanés des versions antérieures : le contenu
    courant est la version ``version_courante`` de la note elle-même.
    """
    return (
        session.query(LabNoteVersion)
        .filter(LabNoteVersion.note_id == note_id)
        .order_by(LabNoteVersion.version.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# Publication explicite
# ---------------------------------------------------------------------------

def est_publie(session, item_type: str, item_id: int) -> bool:
    """``True`` si l'item a une trace de publication (lecture seule)."""
    _modele_ou_422(item_type)
    return (
        session.query(PublicationRecord)
        .filter(
            PublicationRecord.item_type == item_type,
            PublicationRecord.item_id == item_id,
        )
        .first()
        is not None
    )


def publier_item(
    session,
    *,
    item_type: str,
    item_id: int,
    project_id: int,
    publie_par: str | None = None,
    commentaire: str | None = None,
) -> PublicationRecord:
    """Publie EXPLICITEMENT un item de laboratoire (jamais automatique).

    Crée une ``PublicationRecord`` (traçabilité : qui, quand, depuis quel
    projet). Lève ``ValueError`` si :

    - le type est invalide (→ 422 côté routeur) ;
    - l'item est introuvable (→ 404) ;
    - l'item appartient à un autre projet (→ 403) ;
    - l'item est déjà publié (→ 409).

    Ne committe jamais.
    """
    item = _item_ou_404(session, item_type, item_id)

    # Cloisonnement : un item de laboratoire ne se publie que depuis son
    # propre projet (les simulations sans projet ne sont pas publiables).
    projet_item = getattr(item, "project_id", None)
    if projet_item is None or int(projet_item) != int(project_id):
        raise ValueError(
            f"Publication refusée : l'item {item_type} id={item_id} "
            "n'appartient pas au projet "
            f"{project_id}."
        )

    if est_publie(session, item_type, item_id):
        raise ValueError(
            f"Item déjà publié : type={item_type}, id={item_id}."
        )

    record = PublicationRecord(
        item_type=item_type,
        item_id=item_id,
        project_id=project_id,
        publie_par=publie_par,
        commentaire=commentaire,
    )
    session.add(record)
    session.flush()
    return record


# ---------------------------------------------------------------------------
# Modifications gardées (items publiés en lecture seule)
# ---------------------------------------------------------------------------

def modifier_observation(
    session, observation_id: int, **champs: Any
) -> ResearchObservation:
    """Modifie une observation de laboratoire.

    Lève ``ValueError`` si l'observation est introuvable, ou si elle est
    publiée (« donnée publiée en lecture seule » → 409). Les items non
    publiés restent modifiables normalement. Ne committe jamais.
    """
    observation = session.get(ResearchObservation, observation_id)
    if observation is None:
        raise ValueError(f"Observation introuvable : id={observation_id}.")
    _garde_lecture_seule(session, "observation", observation_id)
    for champ, valeur in champs.items():
        if hasattr(observation, champ):
            setattr(observation, champ, valeur)
    session.flush()
    return observation


#: Colonnes d'audit d'une simulation — JAMAIS modifiables via l'API
#: (journal append-only, Phase 13) : qui (``executed_by``), quand
#: (``run_at`` / ``created_at``), empreinte des entrées (``input_hash``),
#: entrées (``inputs``), paramètres (``parameters``), sorties
#: (``result``), version du moteur (``engine_name`` / ``engine_version``).
CHAMPS_SIMULATION_IMMUTABLES = frozenset(
    {
        "executed_by",
        "run_at",
        "created_at",
        "input_hash",
        "inputs",
        "parameters",
        "result",
        "engine_name",
        "engine_version",
    }
)


def modifier_simulation(
    session, simulation_id: int, **champs: Any
) -> Simulation:
    """Modifie une simulation.

    Lève ``ValueError`` si la simulation est introuvable, si elle est
    publiée (« donnée publiée en lecture seule » → 409), ou si l'un des
    champs demandés appartient au journal d'audit immuable
    (``CHAMPS_SIMULATION_IMMUTABLES`` → 409 : une exécution enregistrée
    ne se réécrit pas, on en lance une nouvelle). Les items non publiés
    restent modifiables sur les champs descriptifs (nom, description,
    hypothèses, références, statut…). Ne committe jamais.
    """
    simulation = session.get(Simulation, simulation_id)
    if simulation is None:
        raise ValueError(f"Simulation introuvable : id={simulation_id}.")
    _garde_lecture_seule(session, "simulation", simulation_id)
    interdits = CHAMPS_SIMULATION_IMMUTABLES.intersection(champs)
    if interdits:
        raise ValueError(
            "Champs d'audit immuables (journal append-only) : "
            + ", ".join(sorted(interdits))
            + ". Une exécution enregistrée ne se modifie pas — "
            "lancez une nouvelle simulation."
        )
    for champ, valeur in champs.items():
        if hasattr(simulation, champ):
            setattr(simulation, champ, valeur)
    session.flush()
    return simulation


# ---------------------------------------------------------------------------
# Tableau de bord
# ---------------------------------------------------------------------------

def _ids_composes_etudies(session, project_id: int) -> set[int]:
    """Ids de composés distincts étudiés via les simulations du projet."""
    ids: set[int] = set()
    lignes = (
        session.query(Simulation.compound_ids)
        .filter(Simulation.project_id == project_id)
        .all()
    )
    for (liste,) in lignes:
        for identifiant in liste or []:
            try:
                ids.add(int(identifiant))
            except (TypeError, ValueError):
                continue
    return ids


def tableau_de_bord(session, project_id: int) -> dict:
    """Tableau de bord du laboratoire pour un projet.

    Retourne ``{"project_id", "comptes", "derniers"}`` :

    - ``comptes`` : observations, simulations, notes, publications
      (traces de publication), composes distincts étudiés (via les
      ``compound_ids`` des simulations du projet) et plantes distinctes
      étudiées (plantes liées à ces composés via ``plant_compounds``) ;
    - ``derniers`` : les 5 derniers items de chaque type (observations,
      simulations, notes), chacun avec son ``data_level``.

    Les workflows (module de l'équipe parallèle, ``webintel.science.workflows``)
    ne sont pas encore livrés dans cet arbre : la clé ``workflows`` vaut
    ``None`` et sera renseignée dès que le module existe.
    """
    comptes_observations = (
        session.query(ResearchObservation)
        .filter(ResearchObservation.project_id == project_id)
        .count()
    )
    comptes_simulations = (
        session.query(Simulation)
        .filter(Simulation.project_id == project_id)
        .count()
    )
    comptes_notes = (
        session.query(LabNote).filter(LabNote.project_id == project_id).count()
    )
    comptes_publications = (
        session.query(PublicationRecord)
        .filter(PublicationRecord.project_id == project_id)
        .count()
    )

    ids_composes = _ids_composes_etudies(session, project_id)
    if ids_composes:
        ids_plantes = {
            identifiant
            for (identifiant,) in session.query(PlantCompound.plant_id)
            .filter(PlantCompound.compound_id.in_(ids_composes))
            .distinct()
            .all()
        }
    else:
        ids_plantes = set()

    # Workflows : module de l'équipe parallèle (pas encore livré ici).
    comptes_workflows = None
    try:
        from webintel.science import workflows as _workflows

        modele = getattr(_workflows, "Workflow", None)
        if modele is not None:
            comptes_workflows = (
                session.query(modele)
                .filter(getattr(modele, "project_id") == project_id)
                .count()
            )
    except ImportError:
        comptes_workflows = None

    def _derniers(modele, data_level: str, colonnes: tuple[str, ...]) -> list[dict]:
        lignes = (
            session.query(modele)
            .filter(modele.project_id == project_id)
            .order_by(modele.id.desc())
            .limit(5)
            .all()
        )
        items = []
        for ligne in lignes:
            item = {"id": ligne.id, "data_level": data_level}
            for colonne in colonnes:
                item[colonne] = getattr(ligne, colonne, None)
            items.append(item)
        return items

    return {
        "project_id": project_id,
        "comptes": {
            "observations": comptes_observations,
            "simulations": comptes_simulations,
            "notes": comptes_notes,
            "publications": comptes_publications,
            "composes": len(ids_composes),
            "plantes": len(ids_plantes),
            "workflows": comptes_workflows,
        },
        "derniers": {
            "observations": _derniers(
                ResearchObservation,
                DataLevel.EXPERIMENTAL.value,
                ("auteur", "status", "created_at"),
            ),
            "simulations": _derniers(
                Simulation,
                DataLevel.PREDICTION.value,
                ("name", "status", "created_at"),
            ),
            "notes": _derniers(
                LabNote,
                DataLevel.EXPERIMENTAL.value,
                ("titre", "auteur", "version_courante", "created_at"),
            ),
        },
    }
