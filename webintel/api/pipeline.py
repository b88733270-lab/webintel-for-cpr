"""Compatibilité : le pipeline canonique vit désormais dans `webintel.core.pipeline`.

Ce module est conservé pour ne pas casser les imports existants
(`ui/routes.py`, `scripts/seed_demo.py`, tests). Tout le code a déménagé
sans changement de comportement ; `analyze_url` garde la même signature.
"""

from __future__ import annotations

from webintel.core.pipeline import analyze_fetch_result, analyze_url

__all__ = ["analyze_fetch_result", "analyze_url"]
