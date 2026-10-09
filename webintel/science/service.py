"""Services du module scientifique — Phase 2.

Conventions (comme le noyau data) :

- la session SQLAlchemy est TOUJOURS le premier argument ;
- AUCUNE fonction ne committe : l'appelant (routeur) committe ;
- écritures idempotentes via ``get_or_create()`` (anti-doublon sous
  concurrence, même motif que ``webintel.database.helpers``) ;
- une simulation n'est JAMAIS présentée comme une preuve expérimentale.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from webintel.core.utils import sha256_hex
from webintel.database.helpers import canonical_json, get_or_create
from webintel.science.models import (
    Compound,
    CompoundTarget,
    Dataset,
    Evidence,
    Plant,
    PlantActivity,
    PlantCompound,
    Provenance,
    ResearchObservation,
    Simulation,
    Study,
)

__all__ = [
    "create_plant",
    "create_compound",
    "link_plant_compound",
    "add_evidence",
    "create_observation",
    "create_simulation",
    "search_plants",
    "search_compounds",
    # v2.1 — modèle scientifique enrichi
    "create_study",
    "link_compound_target",
    "add_plant_activity",
    "record_provenance",
    "register_dataset",
    "attach_link_evidence",
    "set_plant_fields",
]


def create_plant(
    session: Session, scientific_name: str, **fields: Any
) -> tuple[Plant, bool]:
    """Crée ou récupère une plante par son nom scientifique (idempotent).

    Retourne ``(plante, créée ?)``. Ne committe jamais.
    """
    return get_or_create(
        session, Plant, defaults=fields, scientific_name=scientific_name
    )


def create_compound(
    session: Session, name: str, **fields: Any
) -> tuple[Compound, bool]:
    """Crée ou récupère un composé par son nom (idempotent).

    Si un ``inchikey`` est fourni et qu'il est déjà utilisé par un AUTRE
    composé, lève ``ValueError`` (l'InChIKey est un identifiant chimique
    unique : deux composés ne peuvent pas le partager). Ne committe jamais.
    """
    inchikey = fields.get("inchikey")
    if inchikey:
        conflit = (
            session.query(Compound)
            .filter(Compound.inchikey == inchikey, Compound.name != name)
            .one_or_none()
        )
        if conflit is not None:
            raise ValueError(
                "InChIKey déjà utilisée par un autre composé."
            )
    return get_or_create(session, Compound, defaults=fields, name=name)


def link_plant_compound(
    session: Session,
    plant_id: int,
    compound_id: int,
    plant_part: str | None = None,
    publication: str | None = None,
    **fields: Any,
) -> tuple[PlantCompound, bool]:
    """Lie une plante à un composé (idempotent).

    Clé d'idempotence : ``(plant_id, compound_id, plant_part,
    publication)`` — la même association rapportée deux fois ne crée pas
    de doublon. Retourne ``(association, créée ?)``. Ne committe jamais.
    """
    return get_or_create(
        session,
        PlantCompound,
        defaults=fields,
        plant_id=plant_id,
        compound_id=compound_id,
        plant_part=plant_part,
        publication=publication,
    )


def add_evidence(
    session: Session, doi: str | None = None, **fields: Any
) -> tuple[Evidence, bool]:
    """Ajoute une preuve documentaire.

    Si un ``doi`` est fourni, l'import est idempotent : un second import
    du même DOI retourne la preuve existante (pas de doublon).
    Sans DOI, la preuve est toujours créée (journal documentaire).
    Retourne ``(preuve, créée ?)``. Ne committe jamais.
    """
    if doi:
        obj, created = get_or_create(
            session, Evidence, defaults=fields, doi=doi
        )
        return obj, created
    evidence = Evidence(**fields)
    session.add(evidence)
    session.flush()
    return evidence, True


def create_observation(
    session: Session, project_id: int, **fields: Any
) -> ResearchObservation:
    """Enregistre une observation de laboratoire (toujours créée).

    Journal d'expériences : chaque appel crée une nouvelle ligne, jamais
    de déduplication (deux mesures ne sont jamais « la même »). Donnée
    privée par défaut (``is_private=True``). Ne committe jamais.
    """
    observation = ResearchObservation(project_id=project_id, **fields)
    session.add(observation)
    session.flush()
    return observation


def create_simulation(
    session: Session, name: str, inputs: dict, *, project_id: int, **fields: Any
) -> Simulation:
    """Enregistre une simulation (prédiction — pas une preuve expérimentale).

    ``input_hash`` = SHA-256 des entrées normalisées (JSON canonique via
    ``canonical_json``) : deux simulations lancées avec les mêmes entrées
    partagent le même hachage, ce qui permet l'audit et la déduplication.

    P0-1 : ``project_id`` est OBLIGATOIRE (mot-clé, jamais ``None``) — une
    simulation est une donnée de laboratoire privée, toujours rattachée à
    un projet. Les routeurs le résolvent via ``projet_effectif_ou_404``
    (projet de la clé en mode serveur, projet « Défaut » en mode local).

    Lève ``ValueError`` si ``inputs`` est vide (une simulation sans
    entrées n'a aucun sens et son hachage serait trompeur). Ne committe
    jamais.

    Audit (Phase 13) : les entrées normalisées sont persistées dans la
    colonne ``inputs`` (rejouabilité : mêmes entrées → même
    ``input_hash``) et ``executed_by`` (``clé:<prefixe>``, ``ui:<prefixe>``
    ou ``local``) enregistre QUI a lancé le calcul.
    """
    if not inputs:
        raise ValueError(
            "Impossible d'enregistrer une simulation sans entrées "
            "(dictionnaire `inputs` vide)."
        )
    if project_id is None:
        raise ValueError(
            "Impossible d'enregistrer une simulation sans projet : "
            "`project_id` est obligatoire (cloisonnement P0-1)."
        )
    simulation = Simulation(
        name=name,
        project_id=project_id,
        input_hash=sha256_hex(canonical_json(inputs)),
        inputs=dict(inputs),
        **fields,
    )
    session.add(simulation)
    session.flush()
    return simulation


def _borne_limit(limit: int) -> int:
    """Borne `limit` dans l'intervalle [1, 200]."""
    return max(1, min(int(limit), 200))


def search_plants(
    session: Session, q: str, limit: int = 50, offset: int = 0
) -> list[Plant]:
    """Recherche des plantes par nom (insensible à la casse).

    Porte sur le nom scientifique, la famille et le genre. `limit`
    bornée à [1, 200] ; `offset` (≥ 0) pour la pagination.
    """
    motif = f"%{q.strip()}%"
    return (
        session.query(Plant)
        .filter(
            or_(
                Plant.scientific_name.ilike(motif),
                Plant.family.ilike(motif),
                Plant.genus.ilike(motif),
            )
        )
        .order_by(Plant.scientific_name)
        .limit(_borne_limit(limit))
        .offset(max(0, offset))
        .all()
    )


def search_compounds(
    session: Session, q: str, limit: int = 50, offset: int = 0
) -> list[Compound]:
    """Recherche des composés par nom (insensible à la casse).

    `limit` bornée à [1, 200] ; `offset` (≥ 0) pour la pagination.
    """
    motif = f"%{q.strip()}%"
    return (
        session.query(Compound)
        .filter(Compound.name.ilike(motif))
        .order_by(Compound.name)
        .limit(_borne_limit(limit))
        .offset(max(0, offset))
        .all()
    )


# ---------------------------------------------------------------------------
# v2.1 — Modèle scientifique enrichi
# ---------------------------------------------------------------------------
# Conventions identiques au reste du module : la session est le premier
# argument, AUCUNE fonction ne committe, écritures idempotentes via
# `get_or_create()` quand un identifiant naturel existe.


def create_study(
    session: Session, doi: str | None = None, **fields: Any
) -> tuple[Study, bool]:
    """Crée ou récupère une étude structurée (idempotent sur le DOI).

    Sans DOI, l'étude est toujours créée (pas d'identifiant naturel).
    Retourne ``(étude, créée ?)``. Ne committe jamais.
    """
    if doi:
        return get_or_create(session, Study, defaults=fields, doi=doi)
    etude = Study(**fields)
    session.add(etude)
    session.flush()
    return etude, True


def link_compound_target(
    session: Session,
    compound_id: int,
    target_name: str,
    interaction_type: str | None = None,
    **fields: Any,
) -> tuple[CompoundTarget, bool]:
    """Documente une cible biologique d'un composé (idempotent).

    Clé d'idempotence : ``(compound_id, target_name, interaction_type)``
    — la même interaction documentée deux fois ne crée pas de doublon.
    Retourne ``(cible, créée ?)``. Ne committe jamais.
    """
    return get_or_create(
        session,
        CompoundTarget,
        defaults=fields,
        compound_id=compound_id,
        target_name=target_name,
        interaction_type=interaction_type,
    )


def add_plant_activity(
    session: Session,
    plant_id: int,
    activity: str,
    study_type: str | None = None,
    study_model: str | None = None,
    **fields: Any,
) -> tuple[PlantActivity, bool]:
    """Enregistre une activité pharmacologique rapportée (idempotent).

    Clé d'idempotence : ``(plant_id, activity, study_type, study_model)``.
    Retourne ``(activité, créée ?)``. Ne committe jamais.
    """
    return get_or_create(
        session,
        PlantActivity,
        defaults=fields,
        plant_id=plant_id,
        activity=activity,
        study_type=study_type,
        study_model=study_model,
    )


def record_provenance(
    session: Session,
    entity_type: str,
    entity_id: int,
    source_name: str | None = None,
    connector: str | None = None,
    data_version: str | None = None,
    **fields: Any,
) -> tuple[Provenance, bool]:
    """Enregistre la traçabilité d'une entité (idempotent).

    Clé d'idempotence : ``(entity_type, entity_id, source_name,
    connector, data_version)`` — ré-importer la même version ne crée pas
    de doublon. Retourne ``(provenance, créée ?)``. Ne committe jamais.
    """
    return get_or_create(
        session,
        Provenance,
        defaults=fields,
        entity_type=entity_type,
        entity_id=entity_id,
        source_name=source_name,
        connector=connector,
        data_version=data_version,
    )


def register_dataset(
    session: Session,
    name: str,
    project_id: int | None = None,
    **fields: Any,
) -> tuple[Dataset, bool]:
    """Enregistre un jeu de données du chercheur (idempotent).

    Clé d'idempotence : ``(name, project_id)``. Privé par défaut
    (``is_private=True``). Retourne ``(dataset, créé ?)``. Ne committe
    jamais.
    """
    return get_or_create(
        session,
        Dataset,
        defaults=fields,
        name=name,
        project_id=project_id,
    )


def attach_link_evidence(
    session: Session, link_id: int, evidence_id: int
) -> PlantCompound:
    """Pointe un lien plante-composé vers sa preuve (``Evidence``).

    Lève ``KeyError`` si le lien ou la preuve est absent. Ne committe
    jamais.
    """
    lien = session.get(PlantCompound, link_id)
    if lien is None:
        raise KeyError(f"Lien plante-composé introuvable : id={link_id}")
    preuve = session.get(Evidence, evidence_id)
    if preuve is None:
        raise KeyError(f"Preuve introuvable : id={evidence_id}")
    lien.evidence_id = evidence_id
    session.flush()
    return lien


def set_plant_fields(
    session: Session, plant_id: int, **fields: Any
) -> Plant:
    """Met à jour les champs éditables d'une plante (botanique v2.1...).

    Seuls les attributs existants du modèle ``Plant`` sont acceptés
    (tout autre nom lève ``ValueError``) ; ``KeyError`` si la plante est
    absente. Ne committe jamais.
    """
    plante = session.get(Plant, plant_id)
    if plante is None:
        raise KeyError(f"Plante introuvable : id={plant_id}")
    for nom, valeur in fields.items():
        if not hasattr(Plant, nom):
            raise ValueError(f"Champ inconnu sur Plant : {nom!r}.")
        setattr(plante, nom, valeur)
    session.flush()
    return plante
