"""Helpers de persistance : idempotence et concurrence (v1.1.1).

Problème traité : les motifs « check-then-insert » (SELECT puis INSERT
conditionnel) sont incorrects sous concurrence — deux workers peuvent
insérer deux fois la même ligne logique. Ces helpers appliquent la
stratégie du noyau data :

- `value_hash_for()` : empreinte stable (sha256) d'une valeur normalisée
  (JSON canonique : clés triées, séparateurs compacts) ;
- `get_or_create()` : SELECT, puis INSERT dans un SAVEPOINT ; en cas
  d'`IntegrityError` (un concurrent a inséré entre-temps), on annule le
  savepoint et on relit la ligne gagnante — jamais de doublon, jamais
  d'exception qui remonte ;
- `upsert_source_observation()` (chantier 0.2) : vrai upsert sur la
  contrainte unique `(entity_id, source_id, field, value_hash)` —
  `INSERT ... ON CONFLICT DO UPDATE` (SQLite / PostgreSQL), repli
  savepoint + relecture pour les autres dialectes. Une valeur déjà vue
  ne crée pas de doublon : elle rafraîchit `observed_at` (date de
  dernière observation) et les métadonnées associées.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from webintel.core.utils import sha256_hex, utcnow
from webintel.database.models import SourceObservation

T = TypeVar("T")


def canonical_json(value: Any) -> str:
    """Forme canonique stable d'une valeur JSON.

    Clés triées, séparateurs compacts, `ensure_ascii=False` ; les objets non
    JSON-natifs (ex. `datetime`) sont convertis via `str` pour ne jamais
    lever. Deux valeurs logiquement égales donnent toujours la même chaîne.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def value_hash_for(value: Any) -> str:
    """Empreinte SHA-256 hexadécimale de la valeur normalisée.

    Utilisée avec la contrainte unique
    `(entity_id, source_id, field, value_hash)` pour rendre l'écriture des
    observations idempotente sous concurrence.
    """
    return sha256_hex(canonical_json(value))


def get_or_create(
    session: Session,
    model: type[T],
    defaults: dict | None = None,
    **lookup: Any,
) -> tuple[T, bool]:
    """Récupère ou crée une ligne, à l'abri des courses d'insertion.

    Retourne `(objet, créé?)`. Si deux sessions tentent la création
    simultanée, une seule gagne l'INSERT ; l'autre attrape l'`IntegrityError`
    sur le savepoint et relit la ligne existante.
    """
    obj = session.query(model).filter_by(**lookup).one_or_none()
    if obj is not None:
        return obj, False
    candidate = model(**{**lookup, **(defaults or {})})
    try:
        with session.begin_nested():
            session.add(candidate)
            session.flush()
    except IntegrityError:
        # Un concurrent a inséré la même clé entre le SELECT et le flush :
        # on relit la ligne gagnante.
        existing = session.query(model).filter_by(**lookup).one()
        return existing, False
    return candidate, True


def _refresh_observation(obs: SourceObservation, refresh: dict) -> None:
    """Applique les champs « rafraîchissables » d'une ré-observation.

    Utilisé quand la valeur a déjà été vue : pas de nouvelle ligne, on
    met à jour `observed_at` (devient la date de dernière observation),
    `confidence`, `snapshot_id`, `source_url`, `source_domain` et `value`
    avec les données de l'observation la plus récente. Ne committe jamais.
    """
    for attr, val in refresh.items():
        setattr(obs, attr, val)


def upsert_source_observation(
    session: Session,
    *,
    entity_id: int,
    source_id: int,
    field: str,
    value: Any,
    source_url: str,
    source_domain: str,
    confidence: float = 1.0,
    snapshot_id: int | None = None,
    observed_at: datetime | None = None,
) -> tuple[SourceObservation, bool]:
    """Crée ou rafraîchit une observation par source (chantier 0.2).

    Clé d'unicité : `(entity_id, source_id, field, value_hash)` avec
    `value_hash = value_hash_for(value)` (réutilisé, jamais recalculé
    autrement). Conforme à la docstring du modèle `SourceObservation` :
    la répétition d'une valeur déjà vue ne crée pas de doublon — elle
    rafraîchit `observed_at`, qui devient la date de dernière observation.

    - ligne absente -> INSERT, retourne `(objet, True)` ;
    - ligne présente -> UPDATE de `observed_at`, `confidence`,
      `snapshot_id`, `source_url`, `source_domain` et `value` avec les
      valeurs fournies, retourne `(objet, False)`.

    Sûr sous concurrence : sur PostgreSQL et SQLite, un seul ordre
    `INSERT ... ON CONFLICT (entity_id, source_id, field, value_hash)
    DO UPDATE` absorbe la course entre deux workers (une seule ligne
    existe toujours) ; sur tout autre dialecte, repli savepoint +
    relecture calqué sur `get_or_create()` (SELECT, INSERT dans
    `begin_nested`, `IntegrityError` -> re-SELECT puis UPDATE).

    Le drapeau « créé » est un meilleur effort sous concurrence : deux
    workers qui insèrent la même valeur au même instant peuvent tous
    deux rapporter `True`, mais la contrainte unique garantit qu'une
    seule ligne existe. Ne committe jamais : l'appelant committe.
    """
    value_hash = value_hash_for(value)
    now = observed_at or utcnow()
    lookup = {
        "entity_id": entity_id,
        "source_id": source_id,
        "field": field,
        "value_hash": value_hash,
    }
    refresh = {
        "observed_at": now,
        "confidence": float(confidence),
        "snapshot_id": snapshot_id,
        "source_url": source_url or "",
        "source_domain": source_domain or "",
        "value": value,
    }

    existing = session.query(SourceObservation).filter_by(**lookup).one_or_none()
    if existing is not None:
        _refresh_observation(existing, refresh)
        session.flush()
        return existing, False

    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        insert = pg_insert
    elif dialect_name == "sqlite":
        insert = sqlite_insert
    else:
        insert = None

    if insert is not None:
        stmt = (
            insert(SourceObservation)
            .values(**lookup, **refresh)
            .on_conflict_do_update(
                index_elements=["entity_id", "source_id", "field", "value_hash"],
                set_=dict(refresh),
            )
        )
        session.execute(stmt)
        obj = session.query(SourceObservation).filter_by(**lookup).one()
        return obj, True

    # Repli portable (dialecte inconnu) : même motif que `get_or_create()`.
    candidate = SourceObservation(**lookup, **refresh)
    try:
        with session.begin_nested():
            session.add(candidate)
            session.flush()
    except IntegrityError:
        # Un concurrent a inséré la même observation entre le SELECT et
        # le flush : on relit la ligne gagnante et on la rafraîchit.
        winner = session.query(SourceObservation).filter_by(**lookup).one()
        _refresh_observation(winner, refresh)
        session.flush()
        return winner, False
    return candidate, True
