"""Extraction/normalisation de prix : `parse_price(text) -> Price`.

Gère les symboles (€, $, £, ¥), les codes ISO (EUR, USD, XOF...), "FCFA"/"F CFA"
-> XOF, les séparateurs de milliers (espace, point, apostrophe, insécable) et
les décimaux à virgule ou point. Retourne `Price(amount=None, currency=None)`
si aucun prix n'est trouvé.
"""

from __future__ import annotations

import re

from webintel.core.types import Price

# Symbole -> code ISO.
_SYMBOL_TO_CURRENCY = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY"}

# Code -> code ISO normalisé (FCFA/CFA -> XOF).
_CODE_TO_CURRENCY = {
    "EUR": "EUR",
    "USD": "USD",
    "GBP": "GBP",
    "CHF": "CHF",
    "JPY": "JPY",
    "XOF": "XOF",
    "FCFA": "XOF",
    "CFA": "XOF",
    "GHS": "GHS",
    "NGN": "NGN",
    "MAD": "MAD",
    "DZD": "DZD",
    "TND": "TND",
    "CAD": "CAD",
    "AUD": "AUD",
}

_NUMBER = r"\d[\d\s.,'\u00a0\u202f]*"
_CODES = "|".join(sorted(_CODE_TO_CURRENCY, key=len, reverse=True))

_PRICE_RE = re.compile(
    rf"""
    (?P<prefix_sym>[€$£¥])\s*(?P<prefix_num>{_NUMBER})          # €19.99 / $1,234.56
    |
    (?P<num>{_NUMBER})\s*(?P<suffix>[€$£¥]|(?<![A-Za-z])(?:{_CODES})(?![A-Za-z]))
    """,  # 19,99 € / 2500 FCFA
    re.IGNORECASE | re.VERBOSE,
)


def _parse_number(raw: str) -> float | None:
    """Convertit une chaîne numérique localisée en float.

    Règle : si ',' et '.' coexistent, le dernier est le séparateur décimal ;
    sinon, un séparateur unique suivi de <= 2 chiffres est décimal, les autres
    cas sont des séparateurs de milliers (ex. "1.234" -> 1234, "19.99" -> 19.99).
    """
    s = re.sub(r"[\s'\u2019\u00a0\u202f]", "", raw)  # séparateurs de milliers
    if not s or not re.search(r"\d", s):
        return None
    has_comma = "," in s
    has_dot = "." in s
    try:
        if has_comma and has_dot:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif has_comma:
            parts = s.split(",")
            if len(parts) == 2 and len(parts[1]) <= 2:
                s = parts[0] + "." + parts[1]
            else:
                s = s.replace(",", "")
        elif has_dot:
            parts = s.split(".")
            if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
                s = parts[0] + parts[1]  # "1.234" -> milliers
            else:
                s = "".join(parts[:-1]) + "." + parts[-1]  # dernier point = décimal
        return float(s)
    except ValueError:
        return None


def parse_price(text: str) -> Price:
    """Extrait le premier prix trouvé dans `text` (montant + devise ISO)."""
    if not text or not text.strip():
        return Price(amount=None, currency=None)

    # "F CFA" / "FCFA" -> XOF (avant la regex principale).
    t = re.sub(r"\bF\s*CFA\b", "XOF", text, flags=re.IGNORECASE)

    match = _PRICE_RE.search(t)
    if not match:
        return Price(amount=None, currency=None)

    if match.group("prefix_sym"):
        currency = _SYMBOL_TO_CURRENCY[match.group("prefix_sym")]
        amount = _parse_number(match.group("prefix_num"))
    else:
        suffix = match.group("suffix")
        currency = _SYMBOL_TO_CURRENCY.get(suffix, _CODE_TO_CURRENCY.get(suffix.upper()))
        amount = _parse_number(match.group("num"))

    if amount is None or currency is None:
        return Price(amount=None, currency=None)
    return Price(amount=amount, currency=currency)
