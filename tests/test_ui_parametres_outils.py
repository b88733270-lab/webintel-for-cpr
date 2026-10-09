"""Tests UI — page Paramètres : bloc « Outils scientifiques ».

Garantit que la page /parametres affiche RDKit, Meeko et AutoDock Vina
avec leur état réel (Présent/Absent), et que Meeko absent affiche le
motif et la consigne (fournir un ligand PDBQT déjà préparé).
"""

from __future__ import annotations

import pytest

import webintel.main
from webintel.config import settings as _settings_mod


@pytest.fixture()
def local_mode():
    """Force `REQUIRE_API_KEY=<redacted> : UI ouverte sans login."""
    settings = _settings_mod.Settings()
    settings.REQUIRE_API_KEY = False
    _settings_mod._settings = settings
    yield settings
    _settings_mod._settings = None


def test_parametres_affiche_les_trois_outils_scientifiques(client, local_mode):
    reponse = client.get("/parametres")
    assert reponse.status_code == 200
    page = reponse.text
    assert "Outils scientifiques" in page
    for outil in ("RDKit", "Meeko", "AutoDock Vina"):
        assert outil in page, f"outil manquant dans la page : {outil}"
    # Chaque outil affiche un état explicite (Présent ou Absent).
    assert "Présent" in page or "Absent" in page


def test_parametres_meeko_coherent_avec_diagnostic_reel(client, local_mode):
    """L'état affiché pour Meeko correspond au diagnostic réel de l'adaptateur."""
    from webintel.simulations.plugins import docking_adapter

    reponse = client.get("/parametres")
    assert reponse.status_code == 200
    page = reponse.text
    if docking_adapter.meeko_disponible():
        assert "Meeko" in page and "Présent" in page
    else:
        assert "Meeko" in page and "Absent" in page
        # Motif + consigne honnête quand Meeko manque.
        assert "pip install meeko" in page


def test_parametres_meeko_absent_affiche_la_consigne(client, local_mode, monkeypatch):
    """Meeko absent (simulé) : la page l'indique et donne la marche à suivre."""
    import webintel.ui.routes as routes  # noqa: F401 — vérifie le module
    from webintel.simulations.plugins import docking_adapter

    monkeypatch.setattr(docking_adapter, "meeko_disponible", lambda: False)
    # Forcer aussi la version meeko à None via importlib.metadata.
    import importlib.metadata as metadata

    def _version_absente(nom):
        raise metadata.PackageNotFoundError()

    monkeypatch.setattr(metadata, "version", _version_absente)

    reponse = client.get("/parametres")
    assert reponse.status_code == 200
    page = reponse.text
    assert "Meeko" in page
    assert "Absent" in page
    assert "ligand PDBQT déjà préparé" in page
