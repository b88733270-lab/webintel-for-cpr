"""Évaluation des règles de surveillance (contrat §9 d'architecture.md).

Règle JSON : `{"all": [cond...]}` (toutes vraies) ou `{"any": [cond...]}`
(au moins une vraie).
Condition : `{"field": "price.amount", "op": "lt|gt|eq|ne|contains|changed", "value": ...}`.

`context = {"event": {...}, "entity": {...}, "diff": {...}}`.
Le champ est résolu par chemin pointé (`get_path`) en cherchant dans l'ordre
`event`, `entity`, puis `diff` — sauf préfixe explicite (`event.`, `entity.`,
`diff.`). Alias : `event_type` -> `event.type`, `text_contains` -> recherche
dans `diff.summary_fr` (ou le texte fourni dans le contexte).
"""

from __future__ import annotations


def get_path(obj: dict, path: str):
    """Résout un chemin pointé (`a.b.c`) dans des dicts imbriqués.

    Retourne `(trouvé: bool, valeur)`. Les clés manquantes ou les valeurs
    intermédiaires non-dict donnent `(False, None)`."""
    current = obj
    for part in (path or "").split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _resolve_field(field: str, context: dict):
    """Résout `field` dans le contexte (ordre : event, entity, diff)."""
    context = context or {}
    if field == "event_type":
        return get_path(context.get("event") or {}, "type")
    for prefix, scope in (("event.", "event"), ("entity.", "entity"), ("diff.", "diff")):
        if field.startswith(prefix):
            return get_path(context.get(scope) or {}, field[len(prefix):])
    for scope in ("event", "entity", "diff"):
        found, value = get_path(context.get(scope) or {}, field)
        if found:
            return True, value
    return False, None


def _coerce_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _evaluate_condition(cond: dict, context: dict) -> bool:
    field = cond.get("field", "")
    op = (cond.get("op") or "eq").lower()
    expected = cond.get("value")

    if field == "text_contains":
        haystacks = []
        diff = context.get("diff") or {}
        if diff.get("summary_fr"):
            haystacks.append(str(diff["summary_fr"]))
        if context.get("text"):
            haystacks.append(str(context["text"]))
        needle = str(expected or "").lower()
        return any(needle in h.lower() for h in haystacks) if needle else False

    if op == "changed":
        # Le champ a changé : présent dans les changes du diff, ou valeur non nulle.
        diff = context.get("diff") or {}
        for change in diff.get("changes") or []:
            if isinstance(change, dict) and change.get("field") == field:
                return True
        found, value = _resolve_field(field, context)
        return found and value is not None

    found, actual = _resolve_field(field, context)
    if not found:
        return False

    if op == "lt":
        a, b = _coerce_number(actual), _coerce_number(expected)
        return a is not None and b is not None and a < b
    if op == "gt":
        a, b = _coerce_number(actual), _coerce_number(expected)
        return a is not None and b is not None and a > b
    if op == "eq":
        if isinstance(actual, str) and isinstance(expected, str):
            return actual.lower() == expected.lower()
        return actual == expected
    if op == "ne":
        if isinstance(actual, str) and isinstance(expected, str):
            return actual.lower() != expected.lower()
        return actual != expected
    if op == "contains":
        needle = str(expected or "")
        if isinstance(actual, (list, tuple, set)):
            return any(str(item).lower() == needle.lower() or needle.lower() in str(item).lower() for item in actual)
        return needle.lower() in str(actual or "").lower()
    return False


def evaluate_rule(rule: dict, context: dict) -> bool:
    """Évalue une règle JSON contre un contexte. Règle vide/invalide -> False."""
    rule = rule or {}
    context = context or {}
    if "all" in rule:
        conditions = rule.get("all") or []
        return bool(conditions) and all(_evaluate_condition(c, context) for c in conditions)
    if "any" in rule:
        conditions = rule.get("any") or []
        return any(_evaluate_condition(c, context) for c in conditions)
    # Forme courte : une condition seule {"field":..., "op":..., "value":...}.
    if "field" in rule:
        return _evaluate_condition(rule, context)
    return False
