"""Registre d'entités : clés stables et upsert (contrat §3 d'architecture.md).

- produit : `product:` + sha256((brand|name) normalisé) tronqué à 16 car.,
  repli sur l'URL canonique si le nom manque ;
- entreprise : `company:` + domaine racine ;
- article / page : préfixe + URL canonique.

Même produit vu sur 50 sites -> UNE entité logique, N observations
(`record_source_observations`, P0-3).

Discipline Entity / Observation (v1.1.1, upsert chantier 0.2) :
- `Entity.data` = attributs d'IDENTITÉ / synthèse uniquement — les champs
  volatils listés dans `_VOLATILE_FIELDS` (prix, disponibilité, note...)
  en sont exclus et vivent UNIQUEMENT dans les observations sourcées ;
- `record_source_observations()` écrit une ligne par (source, champ, valeur
  distincte) via `upsert_source_observation()` : une valeur déjà vue ne
  crée pas de doublon, elle rafraîchit `observed_at` (dernière
  observation) — `INSERT ... ON CONFLICT DO UPDATE`, sûr sous concurrence.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from webintel.core.utils import sha256_hex, utcnow
from webintel.database.helpers import upsert_source_observation
from webintel.database.models import Entity, Source, SourceObservation
from webintel.entities.schemas import coerce_entity
from webintel.entities.sources import get_or_create_source
from webintel.normalization.urls import canonical_url, host_of, root_domain


def entity_key_for(entity_type: str, data: dict, source_url: str) -> str:
    """Calcule la clé stable d'une entité (déterministe, indépendante du site)."""
    kind = (entity_type or "").lower()
    data = data or {}

    if kind == "product":
        name = (data.get("name") or "").strip()
        brand = (data.get("brand") or "").strip()
        if name:
            digest = sha256_hex(f"{brand}|{name}".lower().strip())[:16]
            return f"product:{digest}"
        return f"product:{canonical_url(source_url)}"

    if kind == "company":
        domain = root_domain(source_url or data.get("website") or "")
        if domain:
            return f"company:{domain}"
        name = (data.get("name") or "").strip().lower()
        return f"company:{sha256_hex(name)[:16]}" if name else f"company:{canonical_url(source_url)}"

    if kind == "article":
        return f"article:{canonical_url(source_url)}"

    return f"webpage:{canonical_url(source_url)}"


def _is_empty(value) -> bool:
    """Valeur 'vide' : n'écrase jamais une valeur existante lors de la fusion."""
    return value is None or value == "" or value == [] or value == {}


def _merge_values(old, new):
    """Fusion récursive : les valeurs nouvelles non vides écrasent, les
    anciennes sont conservées sinon (dicts fusionnés clé par clé)."""
    if isinstance(old, dict) and isinstance(new, dict):
        merged = dict(old)
        for key, new_value in new.items():
            merged[key] = _merge_values(old.get(key), new_value)
        return merged
    return new if not _is_empty(new) else old


def upsert_entity(
    session: Session, project_id: int, entity_type: str, data: dict, source_url: str
) -> Entity:
    """Crée ou met à jour une entité (recherche par `(project_id, entity_key)`).

    Discipline v1.1.1 : `Entity.data` ne reçoit que les attributs d'identité
    — les champs volatils (`_VOLATILE_FIELDS`) sont filtrés AVANT stockage.
    - création : `first_seen = last_seen = utcnow()`, `sources = [source_url]` ;
    - mise à jour : fusion de `data` (nouvelles valeurs non vides gagnent),
      `last_seen = utcnow()`, `source_url` ajoutée à `sources` (dédupliquée).
    L'insertion est protégée contre les courses (SAVEPOINT + relecture).
    Retourne l'objet `Entity` (flushé, jamais commité : l'appelant committe).
    """
    kind = (entity_type or "webpage").lower()
    validated = coerce_entity(kind, dict(data or {}))
    clean = _strip_volatile(kind, validated.model_dump(mode="json"))
    key = entity_key_for(kind, dict(data or {}), source_url)

    entity = (
        session.query(Entity)
        .filter(Entity.project_id == project_id, Entity.entity_key == key)
        .one_or_none()
    )
    name = clean.get("name") or clean.get("title") or key

    if entity is None:
        candidate = Entity(
            project_id=project_id,
            entity_key=key,
            entity_type=kind,
            name=str(name or ""),
            data=clean,
            first_seen=utcnow(),
            last_seen=utcnow(),
            sources=[source_url] if source_url else [],
        )
        try:
            with session.begin_nested():
                session.add(candidate)
                session.flush()
        except IntegrityError:
            # Un concurrent a créé la même clé entre le SELECT et le flush :
            # on repart de la ligne gagnante et on lui applique la mise à jour.
            entity = (
                session.query(Entity)
                .filter(Entity.project_id == project_id, Entity.entity_key == key)
                .one()
            )
        else:
            return candidate
    entity.data = _merge_values(entity.data or {}, clean)
    if name and str(name) != key:
        entity.name = str(name)
    entity.last_seen = utcnow()
    sources = list(entity.sources or [])
    if source_url and source_url not in sources:
        sources.append(source_url)
    entity.sources = sources  # réassignation : suivi JSON SQLAlchemy
    session.flush()
    return entity


def get_entity(session: Session, project_id: int, entity_key: str) -> Entity | None:
    """Retourne l'entité `(project_id, entity_key)` ou `None`."""
    return (
        session.query(Entity)
        .filter(Entity.project_id == project_id, Entity.entity_key == entity_key)
        .one_or_none()
    )


# ---------------------------------------------------------------------------
# Observations par source (P0-3, durci en v1.1.1)
# ---------------------------------------------------------------------------

# Champs métier suivis par source : valeurs susceptibles de varier d'une
# source à l'autre pour une même entité logique (prix, disponibilité...).
# Un champ suivi peut aussi être un attribut d'identité (ex. `name`) : la
# discipline ne va que dans un sens — voir `_VOLATILE_FIELDS`.
_TRACKED_FIELDS: dict[str, tuple[str, ...]] = {
    "product": ("price", "availability", "rating", "review_count", "name", "brand"),
    "article": ("title", "author", "published_at"),
    "company": ("name", "phones", "emails"),
    "webpage": ("title",),
}

# Champs VOLATILS (v1.1.1) : valeurs commerciales/mesurées qui changent dans
# le temps et d'une source à l'autre. Ils sont EXCLUS de `Entity.data`
# (identité/synthèse uniquement) et vivent UNIQUEMENT dans les observations
# sourcées (`SourceObservation`). `upsert_entity` les filtre via
# `_strip_volatile()` avant tout stockage.
_VOLATILE_FIELDS: dict[str, frozenset[str]] = {
    "product": frozenset({"price", "availability", "rating", "review_count"}),
    "article": frozenset(),
    "company": frozenset(),
    "webpage": frozenset(),
}


def _strip_volatile(entity_type: str, clean: dict) -> dict:
    """Retire les champs volatils d'un dict d'entité validé (discipline v1.1.1).

    `Entity.data` reste limité aux attributs d'identité/synthèse ; les
    valeurs volatiles (prix, disponibilité, note...) sont observées par
    source via `record_source_observations()` et n'ont pas leur place ici.
    """
    volatile = _VOLATILE_FIELDS.get((entity_type or "").lower(), frozenset())
    if not volatile:
        return clean
    return {k: v for k, v in clean.items() if k not in volatile}


def volatile_fields_for(entity_type: str) -> frozenset[str]:
    """Champs volatils exclus de `Entity.data` pour un type d'entité."""
    return _VOLATILE_FIELDS.get((entity_type or "").lower(), frozenset())


def _json_key(value) -> str:
    """Clé de comparaison canonique d'une valeur (JSON trié, stable)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def record_source_observations(
    session: Session,
    entity: Entity,
    entity_type: str,
    data: dict,
    source_url: str,
    snapshot_id: int | None = None,
    confidence: float = 1.0,
    *,
    source: Source | None = None,
    observed_at: datetime | None = None,
) -> int:
    """Enregistre les observations par source d'une entité (P0-3, durci v1.1.1,
    upsert chantier 0.2).

    Pour chaque champ suivi du type d'entité, écrit UNE `SourceObservation`
    `(entité, source, champ, valeur)` via `upsert_source_observation()` :
    une valeur déjà vue pour (entité, source, champ) — identifiée par
    `value_hash = sha256(valeur normalisée)` — ne crée pas de doublon,
    elle rafraîchit `observed_at` (qui devient la date de dernière
    observation) et les métadonnées (`confidence`, `snapshot_id`,
    `source_url`, `source_domain`). L'historique par source reste une
    chronologie compacte des valeurs réellement observées, avec une date
    de dernière observation toujours à jour.

    La source est la `Source` rattachée à l'**hôte complet** de `source_url`
    (ex. `www.boutique.fr`) : deux boutiques distinctes, même sur des
    sous-domaines du même domaine racine, sont deux sources différentes
    (cas d'usage : comparateur de prix).

    Idempotence sous concurrence (v1.1.1) : l'écriture utilise la contrainte
    unique `(entity_id, source_id, field, value_hash)` avec
    `INSERT ... ON CONFLICT DO UPDATE` (SQLite et PostgreSQL via le
    dialecte SQLAlchemy adapté ; repli savepoint + relecture pour les
    autres dialectes, voir `upsert_source_observation()`). Deux workers
    qui observent la même valeur simultanément ne créent qu'UNE ligne.

    Les données sont normalisées via `coerce_entity` (comme `upsert_entity`)
    pour garantir des valeurs JSON-sérialisables. Ne committe jamais :
    l'appelant committe.

    Retourne le nombre de NOUVELLES valeurs distinctes écrites (meilleur
    effort sous concurrence : la contrainte garantit l'absence de doublon,
    le compteur peut surcompter si deux workers écrivent en même temps).
    """
    kind = (entity_type or "webpage").lower()
    fields = _TRACKED_FIELDS.get(kind, ("title",))
    clean = coerce_entity(kind, dict(data or {})).model_dump(mode="json")
    source = source or get_or_create_source(session, source_url or "")
    domain = source.canonical_host
    now = observed_at or utcnow()

    pending: list[tuple[str, Any]] = []
    for field in fields:
        value = clean.get(field)
        if _is_empty(value):
            continue
        if isinstance(value, dict):
            value = dict(value)
        pending.append((field, value))
    if not pending:
        return 0

    # Vrai upsert par (champ, valeur) : les valeurs déjà vues rafraîchissent
    # leur `observed_at` au lieu d'être ignorées (chantier 0.2).
    new_count = 0
    for field, value in pending:
        _obs, created = upsert_source_observation(
            session,
            entity_id=entity.id,
            source_id=source.id,
            field=field,
            value=value,
            source_url=source_url or "",
            source_domain=domain,
            confidence=confidence,
            snapshot_id=snapshot_id,
            observed_at=now,
        )
        if created:
            new_count += 1
    return new_count


def get_observations_by_source(
    session: Session, entity_id: int
) -> dict[str, list[SourceObservation]]:
    """Dernière observation par (source, champ), groupée par hôte source.

    Retourne `{hôte: [SourceObservation, ...]}` (champs triés par nom).
    Les lignes portent `source_id` (FK vers `Source`, v1.1.1).
    Sert l'API `GET /api/entities/{id}` (P0-3).
    """
    rows = (
        session.query(SourceObservation)
        .filter(SourceObservation.entity_id == entity_id)
        .order_by(SourceObservation.observed_at.desc(), SourceObservation.id.desc())
        .all()
    )
    latest: dict[tuple[str, str], SourceObservation] = {}
    for row in rows:
        key = (row.source_domain or "", row.field or "")
        if key not in latest:
            latest[key] = row
    grouped: dict[str, list[SourceObservation]] = {}
    for (domain, _field), row in sorted(latest.items()):
        grouped.setdefault(domain, []).append(row)
    return grouped
