"""Registre des sources d'observation (v1.1.1).

Une `Source` identifie « qui a fourni la donnée » : une boutique, un site,
un vendeur. L'identité est le `canonical_host` (hôte complet normalisé,
unique) — pas un simple domaine racine : `www.boutique.fr` et
`vendeur.marketplace.fr` sont deux sources différentes.

`get_or_create_source()` est protégé contre les courses d'insertion
(SAVEPOINT + relecture, voir `webintel.database.helpers`).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from webintel.database.helpers import get_or_create
from webintel.database.models import Source
from webintel.normalization.urls import host_of


def canonical_host_of(url: str) -> str:
    """Hôte canonique d'une URL (identité d'une source)."""
    return host_of(url or "")


def get_or_create_source(
    session: Session,
    url: str,
    *,
    name: str | None = None,
    country: str | None = None,
    seller: str | None = None,
    metadata: dict | None = None,
) -> Source:
    """Récupère ou crée la `Source` correspondant à l'hôte de `url`.

    Le `name` par défaut est l'hôte lui-même ; il peut être enrichi
    ultérieurement (le get-or-create ne l'écrase jamais).
    """
    host = canonical_host_of(url)
    if not host:
        raise ValueError(f"impossible de déterminer l'hôte source de {url!r}")
    source, _created = get_or_create(
        session,
        Source,
        defaults={
            "name": name or host,
            "country": country,
            "seller": seller,
            "meta": dict(metadata or {}),
        },
        canonical_host=host,
    )
    return source
