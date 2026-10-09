"""Diff intelligent entre deux snapshots (contrat §8 d'architecture.md).

`diff_snapshots(old, new) -> DiffResult` :
- `price.amount` / `price.currency` / `availability` changés -> MAJOR ;
- titre / H1 / meta description changés -> MINOR ;
- texte : similarité (difflib) < 0.95 -> MINOR, 0.95..0.999 -> COSMETIC
  (sans texte brut, on compare les `text_hash` : différent -> MINOR) ;
- DOM normalisé (attributs class/id/style/data-* supprimés) : si la signature
  est identique alors que `html_hash` diffère, le changement est purement
  cosmétique -> COSMETIC. Un changement de classe CSS seule ne donne JAMAIS
  MAJOR ni MINOR ;
- rien -> NONE.

`DiffResult{level, changes[{field, before, after, importance}], score, summary_fr}`.
`score` = somme des poids des champs changés, plafonné à 100.
"""

from __future__ import annotations

import json

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field

from webintel.core.enums import ChangeLevel
from webintel.core.types import SnapshotPayload
from webintel.core.utils import clean_text, sha256_hex, text_similarity, truncate
from webintel.diff.scoring import FIELD_WEIGHTS, TEXT_SIMILARITY_MINOR, field_importance

# Attributs purement présentationnels : leur seul changement est COSMETIC.
_COSMETIC_ATTRS = frozenset({"class", "id", "style"})


def build_dom_signature(html: str) -> str:
    """Signature structurelle d'un HTML : noms de balises + attributs
    non présentationnels triés. Les attributs `class`/`id`/`style`/`data-*`
    sont supprimés, de sorte que deux pages ne différant que par du CSS
    obtiennent la même signature (changement COSMETIC)."""
    soup = BeautifulSoup(html or "", "lxml")
    parts: list[str] = []
    for tag in soup.find_all(True):
        if not isinstance(tag, Tag):
            continue
        attrs = []
        for name, value in sorted(tag.attrs.items()):
            lowered = name.lower()
            if lowered in _COSMETIC_ATTRS or lowered.startswith("data-"):
                continue
            if isinstance(value, list):
                value = " ".join(str(v) for v in value)
            attrs.append(f"{lowered}={value}")
        parts.append(f"<{tag.name.lower()}{(' ' + ' '.join(attrs)) if attrs else ''}>")
    return sha256_hex("|".join(parts))


class DiffChange(BaseModel):
    field: str
    before: object | None = None
    after: object | None = None
    importance: str = "low"  # "high" | "medium" | "low"


class DiffResult(BaseModel):
    level: ChangeLevel = ChangeLevel.NONE
    changes: list[DiffChange] = Field(default_factory=list)
    score: float = 0.0  # 0..100
    summary_fr: str = "Aucun changement détecté."


def _stable(items: list | None) -> list[str]:
    """Forme triée et comparable d'une liste d'éléments hétérogènes.

    Les images/liens sont des dicts (`{"url": ...}`) : on les sérialise en
    JSON à clés triées plutôt que de trier les dicts eux-mêmes (TypeError
    en Python 3). La comparaison reste insensible à l'ordre.
    """
    return sorted(
        json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        for item in (items or [])
    )


def _first_h1(structured: dict) -> str | None:
    """Premier H1 depuis `structured["headings"]` (plusieurs formats tolérés)."""
    headings = (structured or {}).get("headings")
    if isinstance(headings, dict):  # {"h1": [...], ...}
        items = headings.get("h1") or []
        return clean_text(str(items[0])) or None if items else None
    if isinstance(headings, list):
        for item in headings:
            if isinstance(item, dict):
                level = str(item.get("level") or item.get("tag") or "").lower()
                text = clean_text(str(item.get("text") or ""))
                if level in ("h1", "1") and text:
                    return text
            elif isinstance(item, str) and clean_text(item):
                return clean_text(item)
    return None


def _meta_description(structured: dict) -> str | None:
    structured = structured or {}
    desc = structured.get("meta_description")
    if desc:
        return clean_text(str(desc)) or None
    meta = structured.get("meta") or {}
    return clean_text(str(meta.get("description") or "")) or None


def _tech_names(technologies: list) -> list[str]:
    names = []
    for tech in technologies or []:
        if isinstance(tech, dict):
            name = tech.get("technology") or tech.get("name")
        else:
            name = tech
        if name:
            names.append(str(name))
    return sorted(set(names))


def _format_amount(value) -> str:
    """Montant formaté à la française : 4990 -> '4 990,00'."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:,.2f}"
    return text.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", " ")


_CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "XOF": "F CFA", "CHF": "CHF"}


def _format_price(value, currency: str | None) -> str:
    symbol = _CURRENCY_SYMBOLS.get((currency or "").upper(), currency or "")
    amount = _format_amount(value)
    return f"{amount} {symbol}".strip() if symbol else amount


def diff_snapshots(
    old: SnapshotPayload,
    new: SnapshotPayload,
    *,
    old_text: str | None = None,
    new_text: str | None = None,
    old_html: str | None = None,
    new_html: str | None = None,
) -> DiffResult:
    """Compare deux snapshots et retourne un `DiffResult` (contrat §8).

    `old_text`/`new_text` (optionnels) permettent d'affiner un changement de
    texte en MINOR (< 0.95) vs COSMETIC (>= 0.95) ; `old_html`/`new_html`
    permettent la comparaison du DOM normalisé.
    """
    old_s, new_s = old.structured or {}, new.structured or {}
    changes: list[DiffChange] = []

    def add(field: str, before, after, importance: str | None = None):
        changes.append(
            DiffChange(
                field=field,
                before=before,
                after=after,
                importance=importance or field_importance(field),
            )
        )

    # --- Prix et disponibilité (MAJOR) ---
    old_price, new_price = old_s.get("price") or {}, new_s.get("price") or {}
    old_amount, new_amount = old_price.get("amount"), new_price.get("amount")
    if old_amount != new_amount and not (old_amount is None and new_amount is None):
        add("price.amount", old_amount, new_amount)
    old_cur, new_cur = old_price.get("currency"), new_price.get("currency")
    if old_cur != new_cur and not (old_cur is None and new_cur is None):
        add("price.currency", old_cur, new_cur)
    old_avail, new_avail = old_s.get("availability"), new_s.get("availability")
    if old_avail != new_avail and not (old_avail is None and new_avail is None):
        add("availability", old_avail, new_avail)

    # --- Titre / H1 / meta description (MINOR) ---
    if (old.title or "") != (new.title or ""):
        add("title", truncate(old.title or "", 120), truncate(new.title or "", 120))
    old_h1, new_h1 = _first_h1(old_s), _first_h1(new_s)
    if old_h1 != new_h1:
        add("h1", truncate(old_h1 or "", 120), truncate(new_h1 or "", 120))
    old_meta, new_meta = _meta_description(old_s), _meta_description(new_s)
    if old_meta != new_meta:
        add("meta_description", truncate(old_meta or "", 160), truncate(new_meta or "", 160))

    # --- Texte (MINOR si similarité < 0.95, COSMETIC sinon) ---
    if old.text_hash != new.text_hash:
        if old_text is not None and new_text is not None:
            similarity = text_similarity(old_text or "", new_text or "")
            if similarity < 1.0:
                importance = "medium" if similarity < TEXT_SIMILARITY_MINOR else "low"
                add(
                    "text",
                    truncate(clean_text(old_text or ""), 80),
                    truncate(clean_text(new_text or ""), 80),
                    importance=importance,
                )
        else:
            add("text", old.text_hash[:12], new.text_hash[:12])

    # --- DOM normalisé : changement cosmétique pur ? ---
    dom_changed = False
    if old_html is not None and new_html is not None and old.html_hash != new.html_hash:
        if build_dom_signature(old_html) == build_dom_signature(new_html):
            dom_changed = True
            add("dom", old.html_hash[:12], new.html_hash[:12], importance="low")
    elif old.html_hash != new.html_hash and not changes:
        # Sans HTML brut, un html_hash différent sans autre changement détecté
        # est traité comme cosmétique (jamais MAJOR/MINOR sur cette seule base).
        dom_changed = True
        add("dom", old.html_hash[:12], new.html_hash[:12], importance="low")

    # --- Images / liens / technologies / SEO (low) ---
    # Comparaison insensible à l'ordre via `_stable` (les éléments sont des
    # dicts : tri direct impossible en Python 3).
    if _stable(old.images) != _stable(new.images):
        add("images", len(old.images or []), len(new.images or []))
    if _stable(old.links) != _stable(new.links):
        add("links", len(old.links or []), len(new.links or []))
    old_tech, new_tech = _tech_names(old.technologies), _tech_names(new.technologies)
    if old_tech != new_tech:
        add("technologies", old_tech, new_tech)
    old_seo = (old.seo or {}).get("score")
    new_seo = (new.seo or {}).get("score")
    if old_seo != new_seo:
        add("seo_score", old_seo, new_seo)

    # --- Niveau, score, résumé ---
    if not changes:
        return DiffResult()
    if any(c.importance == "high" for c in changes):
        level = ChangeLevel.MAJOR
    elif any(c.importance == "medium" for c in changes):
        level = ChangeLevel.MINOR
    else:
        level = ChangeLevel.COSMETIC
    score = min(100.0, sum(FIELD_WEIGHTS.get(c.field, 0) for c in changes))

    return DiffResult(
        level=level,
        changes=changes,
        score=score,
        summary_fr=_summary_fr(changes, new_price.get("currency")),
    )


def _summary_fr(changes: list[DiffChange], currency: str | None) -> str:
    """Résumé lisible en français des changements détectés."""
    sentences: list[str] = []
    by_field = {c.field: c for c in changes}

    price = by_field.get("price.amount")
    if price is not None:
        before, after = price.before, price.after
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            if after < before:
                sentences.append(
                    f"Baisse de prix : {_format_price(before, currency)} → "
                    f"{_format_price(after, currency)}"
                )
            elif after > before:
                sentences.append(
                    f"Hausse de prix : {_format_price(before, currency)} → "
                    f"{_format_price(after, currency)}"
                )
            else:
                sentences.append("Prix inchangé.")
        elif after is not None:
            sentences.append(f"Prix : {_format_price(after, currency)}")

    avail = by_field.get("availability")
    if avail is not None:
        if avail.after == "in_stock":
            sentences.append("Retour en stock.")
        elif avail.after == "out_of_stock":
            sentences.append("Rupture de stock.")
        else:
            sentences.append(f"Disponibilité : {avail.before} → {avail.after}.")

    if "title" in by_field:
        sentences.append("Titre de la page modifié.")
    if "h1" in by_field:
        sentences.append("Titre principal (H1) modifié.")
    if "meta_description" in by_field:
        sentences.append("Meta description modifiée.")
    if "text" in by_field:
        sentences.append(
            "Contenu textuel légèrement modifié."
            if by_field["text"].importance == "low"
            else "Contenu textuel modifié."
        )
    if "dom" in by_field:
        sentences.append("Mise en page / styles modifiés (cosmétique).")
    if "technologies" in by_field:
        sentences.append("Technologies détectées modifiées.")
    if "seo_score" in by_field:
        sentences.append("Score SEO modifié.")
    if "images" in by_field:
        sentences.append("Images de la page modifiées.")
    if "links" in by_field:
        sentences.append("Liens de la page modifiés.")

    return " ".join(sentences) if sentences else "Changements détectés."
