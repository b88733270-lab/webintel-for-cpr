"""Surcouches de configuration modifiables à l'exécution (Phase 11).

La variable d'environnement (ex. ``WEBINTEL_AI_ENABLED``) reste la source de
vérité par défaut. La page « Paramètres » de l'interface peut écrire un
fichier JSON d'overrides (``runtime_config.json``) qui prend le pas sur la
variable d'environnement — cela permet d'activer/désactiver l'IA sans
redémarrer ni toucher à l'environnement. Un bouton « réinitialiser » efface
l'override et redonne la priorité à la variable d'environnement.

Le fichier vit à côté des données de l'application (``webintel_data/`` par
défaut, jamais versionné) ; son chemin est configurable via
``WEBINTEL_RUNTIME_CONFIG`` (utile en tests).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from webintel.config.settings import get_settings

OVERRIDE_AI_ENABLED = "ai_enabled"


def runtime_config_path() -> Path:
    """Chemin du fichier d'overrides (configurable en tests)."""
    chemin = os.environ.get("WEBINTEL_RUNTIME_CONFIG")
    if chemin:
        return Path(chemin)
    return Path("webintel_data") / "runtime_config.json"


def get_runtime_overrides() -> dict:
    """Lit les overrides à l'exécution (``{}`` si le fichier est absent)."""
    chemin = runtime_config_path()
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def set_runtime_override(cle: str, valeur) -> dict:
    """Écrit un override (écriture atomique) et retourne tous les overrides."""
    chemin = runtime_config_path()
    overrides = get_runtime_overrides()
    overrides[cle] = valeur
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(chemin)
    return overrides


def clear_runtime_override(cle: str) -> dict:
    """Efface un override (retour à la variable d'environnement / au défaut)."""
    chemin = runtime_config_path()
    overrides = get_runtime_overrides()
    overrides.pop(cle, None)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(chemin)
    return overrides


def is_ai_enabled(settings=None) -> tuple[bool, str]:
    """Indique si les fonctionnalités d'IA sont actives, et pourquoi.

    Ordre de priorité : override de l'interface > variable d'environnement
    ``WEBINTEL_AI_ENABLED`` > défaut (True).

    Retourne ``(active, source)`` où ``source`` vaut ``"interface"``,
    ``"variable_environnement"`` ou ``"defaut"``.
    """
    if settings is None:
        settings = get_settings()
    overrides = get_runtime_overrides()
    if OVERRIDE_AI_ENABLED in overrides:
        return bool(overrides[OVERRIDE_AI_ENABLED]), "interface"
    if "WEBINTEL_AI_ENABLED" in os.environ:
        return bool(settings.AI_ENABLED), "variable_environnement"
    return bool(settings.AI_ENABLED), "defaut"
