"""Tests des écrans intelligence Web v3 (Sources, Comparaison, Recherche Web)."""

from __future__ import annotations


def _client():
    from fastapi.testclient import TestClient

    from webintel.main import create_app

    return TestClient(create_app())


def test_ecran_sources_repond():
    client = _client()
    r = client.get("/sources")
    assert r.status_code == 200
    assert "Sources" in r.text


def test_ecran_comparaison_repond():
    client = _client()
    r = client.get("/comparaison")
    assert r.status_code == 200
    assert "Comparaison" in r.text


def test_ecran_recherche_web_sans_requete():
    client = _client()
    r = client.get("/recherche-web")
    assert r.status_code == 200
    assert "Recherche Web" in r.text
    # Sans requête : aucun appel provider, pas de faux résultat.
    assert "Résultats pour" not in r.text


def test_ecran_recherche_web_avec_requete(monkeypatch):
    """La recherche Web affiche les résultats du service (mocké)."""
    import webintel.ui.web_intel_routes as routes

    def faux_service(q, limite=10, langue="fr"):
        assert q == "burkina"
        return {
            "requete": "burkina",
            "resultats": [
                {"titre": "Résultat de test", "url": "https://exemple.org/a",
                 "extrait": "Extrait.", "source": "wikipedia",
                 "date": None, "type": "connaissances"},
            ],
            "providers_utilises": ["wikipedia"],
            "providers_en_echec": [
                {"provider": "duckduckgo", "type_erreur": "timeout",
                 "message": "Délai dépassé."},
            ],
            "duree_ms": 12,
            "langue": langue,
            "limite": limite,
        }

    monkeypatch.setattr(routes, "rechercher_web", faux_service)
    client = _client()
    r = client.get("/recherche-web?q=burkina")
    assert r.status_code == 200
    assert "Résultat de test" in r.text
    assert "wikipedia" in r.text
    # L'échec du second provider est déclaré, pas masqué.
    assert "duckduckgo" in r.text
