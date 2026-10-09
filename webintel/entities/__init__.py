"""Entités métier : schémas Pydantic + registre (clés stables, upsert)."""

from webintel.entities.registry import entity_key_for, get_entity, upsert_entity
from webintel.entities.schemas import (
    Article,
    BaseEntity,
    Company,
    Product,
    WebPage,
    coerce_entity,
)

__all__ = [
    "Article",
    "BaseEntity",
    "Company",
    "Product",
    "WebPage",
    "coerce_entity",
    "entity_key_for",
    "get_entity",
    "upsert_entity",
]
