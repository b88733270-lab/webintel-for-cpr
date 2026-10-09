"""Données structurées d'une page : JSON-LD, Microdata, OpenGraph, Twitter, meta.

`extract_structured(soup, url) -> dict` retourne :
{
  "json_ld":   [ {"@type": ..., ...}, ... ],   # scripts application/ld+json parsés
  "microdata": [ {"type": ..., "props": {...}}, ... ],  # itemscope/itemtype/itemprop
  "opengraph": { "title": ..., "image": ..., ... },     # meta property="og:*" (préfixe retiré)
  "twitter":   { "card": ..., ... },                   # meta name="twitter:*"
  "meta": { "description": ..., "keywords": ..., "author": ...,
            "canonical": ..., "robots": ... },          # meta name=... + link rel=canonical
}
Chaque bloc est robuste aux erreurs de parsing (JSON invalide ignoré).
"""

from __future__ import annotations

import json

from bs4 import BeautifulSoup
from bs4.element import Tag


def _parse_json_ld(soup: BeautifulSoup) -> list[dict]:
    objects: list[dict] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                objects.append(item)
                # Aplatis le @graph (schémas imbriqués courants).
                graph = item.get("@graph")
                if isinstance(graph, list):
                    objects.extend([g for g in graph if isinstance(g, dict)])
    return objects


def _extract_microdata(soup: BeautifulSoup) -> list[dict]:
    items: list[dict] = []
    for scope in soup.find_all(attrs={"itemscope": True}):
        if not isinstance(scope, Tag):
            continue
        # Ne garde que les scopes racines (pas imbriqués dans un autre itemscope).
        if scope.find_parent(attrs={"itemscope": True}):
            continue
        props: dict[str, object] = {}
        for el in scope.find_all(attrs={"itemprop": True}):
            if not isinstance(el, Tag):
                continue
            name = el.get("itemprop")
            value = el.get("content") or el.get("href") or el.get("src") or el.get_text(strip=True)
            if not name:
                continue
            if name in props:
                existing = props[name]
                props[name] = existing + [value] if isinstance(existing, list) else [existing, value]
            else:
                props[name] = value
        items.append({"type": scope.get("itemtype"), "props": props})
    return items


def _extract_opengraph(soup: BeautifulSoup) -> dict:
    og: dict[str, str] = {}
    for meta in soup.find_all("meta", attrs={"property": True}):
        prop = (meta.get("property") or "").strip()
        if prop.lower().startswith("og:"):
            content = meta.get("content")
            if content:
                og[prop[3:]] = content.strip()
    return og


def _extract_twitter(soup: BeautifulSoup) -> dict:
    tw: dict[str, str] = {}
    for meta in soup.find_all("meta", attrs={"name": True}):
        name = (meta.get("name") or "").strip()
        if name.lower().startswith("twitter:"):
            content = meta.get("content")
            if content:
                tw[name[8:]] = content.strip()
    return tw


def _extract_meta(soup: BeautifulSoup) -> dict:
    meta: dict[str, str] = {}
    wanted = {"description", "keywords", "author", "robots", "viewport", "generator"}
    for tag in soup.find_all("meta", attrs={"name": True}):
        name = (tag.get("name") or "").strip().lower()
        if name in wanted:
            content = tag.get("content")
            if content:
                meta[name] = content.strip()
    canonical = soup.find("link", rel="canonical")
    if isinstance(canonical, Tag) and canonical.get("href"):
        meta["canonical"] = canonical.get("href").strip()
    return meta


def extract_structured(soup: BeautifulSoup, url: str) -> dict:
    """Extrait les données structurées d'une page (soup BeautifulSoup + URL source)."""
    _ = url  # réservé : traçabilité fine par source dans une évolution future
    return {
        "json_ld": _parse_json_ld(soup),
        "microdata": _extract_microdata(soup),
        "opengraph": _extract_opengraph(soup),
        "twitter": _extract_twitter(soup),
        "meta": _extract_meta(soup),
    }
