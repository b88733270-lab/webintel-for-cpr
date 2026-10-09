"""Tests UI v2.2 — page « Outils scientifiques », statut docking, provenance.

Nouveau module : ne modifie rien d'existant. Le routeur
`webintel.ui.chercheur_outils_routes.router` est enregistré dans
`webintel/main.py` (`create_app`) ; le montage ci-dessous n'est qu'un
filet de sécurité (garde-fou anti-double-enregistrement + suivi d'un
éventuel `importlib.reload(webintel.main)` par un autre module de la
suite).
"""

from __future__ import annotations

import pytest

import webintel.main
from webintel.config import settings as _settings_mod
from webintel.ui import chercheur_outils_routes


def _router_deja_monte(app, module: str) -> bool:
    """Détecte un routeur déjà inclus, y compris via `_IncludedRouter`
    (FastAPI récent n'aplatit plus les routes incluses : le test sur
    `path` ne voit jamais les routes d'un routeur inclus)."""
    for entree in app.router.routes:
        if getattr(getattr(entree, "endpoint", None), "__module__", None) == module:
            return True
        inner = getattr(entree, "original_router", None)
        for route in getattr(inner, "routes", None) or ():
            if getattr(getattr(route, "endpoint", None), "__module__", None) == module:
                return True
    return False


def _assurer_montage_outils() -> None:
    """Monte le routeur sur l'app COURANTE (import local : `webintel.main.app`
    est résolu à chaque appel, donc un `importlib.reload(webintel.main)`
    effectué par un autre module de la suite — qui remplace l'objet `app` —
    n'enlève plus les routes : `create_app()` les ré-enregistre et, si
    l'app a été remplacée, le montage est refait ici)."""
    import webintel.main  # noqa: PLC0415

    app = webintel.main.app
    if not _router_deja_monte(app, chercheur_outils_routes.__name__):
        app.include_router(chercheur_outils_routes.router)


_assurer_montage_outils()


@pytest.fixture(autouse=True)
def _montage_outils_scientifiques():
    _assurer_montage_outils()
    yield


@pytest.fixture()
def local_mode():
    """Force `REQUIRE_API_KEY=<redacted> (même motif que test_ui_parametres_outils)."""
    settings = _settings_mod.Settings()
    settings.REQUIRE_API_KEY = False
    _settings_mod._settings = settings
    yield settings
    _settings_mod._settings = None


_VINA_ABSENT = {
    "binaire": None,
    "version": None,
    "disponible": False,
    "raison": "Binaire `vina` introuvable dans le PATH (simulation de test).",
}

_VINA_PRESENT = {
    "binaire": "/usr/bin/vina",
    "version": "1.2.5",
    "disponible": True,
    "raison": "",
}


@pytest.fixture()
def vina_absent(monkeypatch):
    from webintel.simulations.plugins import docking_adapter

    monkeypatch.setattr(
        docking_adapter, "diagnostic_vina", lambda: dict(_VINA_ABSENT)
    )
    return _VINA_ABSENT


@pytest.fixture()
def vina_present(monkeypatch):
    from webintel.simulations.plugins import docking_adapter

    monkeypatch.setattr(
        docking_adapter, "diagnostic_vina", lambda: dict(_VINA_PRESENT)
    )
    return _VINA_PRESENT


# ---------------------------------------------------------------------------
# Page /outils-scientifiques
# ---------------------------------------------------------------------------


def test_outils_page_200_et_quatre_outils(client, local_mode):
    reponse = client.get("/outils-scientifiques")
    assert reponse.status_code == 200
    page = reponse.text
    assert "Outils scientifiques" in page
    for outil in ("RDKit", "Meeko", "AutoDock Vina", "PostgreSQL"):
        assert outil in page, f"outil manquant : {outil}"
    # Les 4 infos exigées par outil : nom (ci-dessus), version, statut,
    # fonction, licence.
    assert "Version :" in page
    assert "Fonction :" in page
    assert "Licence :" in page
    assert "🟢 Installé" in page or "🟠 Non installé" in page


def test_outils_coherents_avec_detections_reelles(client, local_mode):
    """Le statut affiché correspond aux fonctions de détection réelles."""
    from webintel.science import chimie
    from webintel.simulations.plugins import docking_adapter

    page = client.get("/outils-scientifiques").text
    assert page.count("RDKit") >= 1
    if chimie.rdkit_disponible():
        assert "🟢 Installé" in page
    if not docking_adapter.meeko_disponible():
        assert "🟠 Non installé" in page
    disponible, _raison = docking_adapter.is_available()
    if not disponible:
        assert "Le moteur de docking n'est pas installé." in page


def test_outils_vina_absent_message_et_aucun_score(
    client, local_mode, vina_absent
):
    """Vina absent (simulé) : message explicite, aucun score, bouton installer."""
    page = client.get("/outils-scientifiques").text
    assert "Le moteur de docking n'est pas installé." in page
    assert "[Installer les outils scientifiques]" in page
    # Aucun score : pas de bloc « disponible », pas de résumé de résultat.
    assert "🟢 Moteur de docking disponible" not in page
    assert "Meilleure affinité" not in page


def test_outils_vina_present_bouton_preparer(client, local_mode, vina_present):
    """Vina présent (simulé) : statut vert, version, bouton [Préparer l'étude]."""
    page = client.get("/outils-scientifiques").text
    assert "🟢 Moteur de docking disponible" in page
    assert "1.2.5" in page
    assert "[Préparer l'étude]" in page
    # Avertissement prudent toujours visible quand le moteur est là.
    assert "prédiction informatique" in page


def test_outils_installation_ultra_simple(client, local_mode, monkeypatch):
    """Chaque outil non installé affiche une marche à suivre simple.

    Tous les outils sont simulés absents pour rendre le test
    indépendant de l'environnement réel.
    """
    from webintel.science import chimie
    from webintel.simulations.plugins import docking_adapter

    monkeypatch.setattr(
        chimie,
        "infos_biblio",
        lambda: {
            "bibliotheque": "RDKit",
            "version": None,
            "disponible": False,
            "raison": "absent (test)",
        },
    )
    monkeypatch.setattr(docking_adapter, "meeko_disponible", lambda: False)
    monkeypatch.setattr(
        docking_adapter, "diagnostic_vina", lambda: dict(_VINA_ABSENT)
    )
    page = client.get("/outils-scientifiques").text
    assert page.count("🟠 Non installé") == 4
    assert "Demandez à votre informaticien" in page
    assert "scripts/install_science.sh" in page
    assert "pip install meeko" in page
    assert "pip install rdkit-pypi" in page


# ---------------------------------------------------------------------------
# Traçabilité : /resultats/{type}/{id}/provenance
# ---------------------------------------------------------------------------


def _creer_provenance(db_session):
    from webintel.science import service as science_service

    prov, _creee = science_service.record_provenance(
        db_session,
        "plant",
        4242,
        source_name="Herbier de test",
        connector="ui-manuel",
        connector_version="v2.1.2",
        data_version="saisie-fiche",
    )
    db_session.commit()
    return prov


def test_provenance_200_avec_les_sept_champs(
    client, local_mode, db_session
):
    _creer_provenance(db_session)
    reponse = client.get("/resultats/plant/4242/provenance")
    assert reponse.status_code == 200
    page = reponse.text
    # Les 7 champs exigés.
    for champ in (
        "Source",
        "Donnée d'entrée",
        "Méthode",
        "Version du moteur",
        "Paramètres",
        "Date",
        "Résultat",
    ):
        assert champ in page, f"champ manquant : {champ}"
    # Valeurs réelles issues de la ligne de provenance.
    assert "Herbier de test" in page
    assert "ui-manuel" in page
    assert "saisie-fiche" in page
    # Bouton [Voir la provenance] présent (partiel réutilisable).
    assert "[Voir la provenance]" in page


def test_provenance_404_sans_ligne(client, local_mode):
    reponse = client.get("/resultats/plant/999999/provenance")
    assert reponse.status_code == 404


def test_provenance_docking_404_job_inconnu(client, local_mode):
    reponse = client.get("/resultats/docking/999999/provenance")
    assert reponse.status_code == 404


def test_provenance_etude_creee_par_wizard_ou_api(client, local_mode,
                                                  db_session, make_project):
    """Une étude de combinaison créée (wizard ou formulaire) expose sa
    fiche de traçabilité sur `/resultats/etude/{id}/provenance` (200)."""
    from webintel.science import combinaisons as comb_module
    from webintel.science import models as science_models

    projet_id = make_project("Projet provenance étude")
    ids = []
    for nom in ("Mol Prov A", "Mol Prov B"):
        compose = science_models.Compound(name=nom, smiles="CCO")
        db_session.add(compose)
        db_session.flush()
        ids.append(compose.id)
    etude = comb_module.creer_etude(
        db_session, project_id=projet_id, nom="Étude UI provenance",
        compound_ids=ids, cree_par="test-ui",
    )
    db_session.commit()

    reponse = client.get(f"/resultats/etude/{etude.id}/provenance")
    assert reponse.status_code == 200, reponse.text[:400]
    page = reponse.text
    assert "Étude de combinaison" in page
    assert "webintel.science.combinaisons" in page
    # La version de données tracée (dossier en 12 points) est affichée.
    assert "dossier-12-points" in page
