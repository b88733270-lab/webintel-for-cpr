"""Routeur recherche Web généraliste : GET /api/web/recherche, /api/web/providers.

Sans aucune clé API, la recherche fonctionne immédiatement (providers
sans clé : Wikipedia, DuckDuckGo, GitHub public, RSS). Les providers à
clé absents sont sautés et déclarés dans ``providers_en_echec`` /
statuts — jamais de faux résultat.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from webintel.api.deps import api_key_auth
from webintel.providers.registre import statuts_providers
from webintel.search.web_service import rechercher_web

router = APIRouter()


@router.get("/web/recherche")
def recherche_web_endpoint(
    q: str = Query(..., min_length=1, max_length=300, description="Requête de recherche"),
    limite: int = Query(default=10, ge=1, le=50),
    langue: str = Query(default="fr", pattern="^(fr|en)$"),
    api_key=Depends(api_key_auth),
) -> dict:
    """Recherche Web avec fallback automatique entre providers."""
    return rechercher_web(q, limite=limite, langue=langue)


@router.get("/web/providers")
def providers_endpoint(api_key=Depends(api_key_auth)) -> dict:
    """Statuts des providers (page Paramètres) — jamais de clé exposée."""
    statuts = statuts_providers()
    return {"providers": statuts, "count": len(statuts)}
