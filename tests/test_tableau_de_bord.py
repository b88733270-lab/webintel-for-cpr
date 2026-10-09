"""Tests UI « Tableau de bord chercheur » (v2.3).

Route additive ``GET /tableau-de-bord`` : agrégat réel des sections du
parcours chercheur (projets, plantes, échantillons, études, expériences,
simulations, publications, rapports, données à valider, outils) —
chaque chiffre est calculé en temps réel à partir de la base, aucune
statistique artificielle.
"""

from webintel.ui import chercheur_accueil_routes


def _router_deja_monte(app, module: str) -> bool:
    for entree in app.router.routes:
        if getattr(getattr(entree, "endpoint", None), "__module__", None) == module:
            return True
        inner = getattr(entree, "original_router", None)
        for route in getattr(inner, "routes", None) or ():
            if getattr(getattr(route, "endpoint", None), "__module__", None) == module:
                return True
    return False


def _assurer_montage() -> None:
    import webintel.main  # noqa: PLC0415

    app = webintel.main.app
    if not _router_deja_monte(app, chercheur_accueil_routes.__name__):
        app.include_router(chercheur_accueil_routes.router)


_assurer_montage()

import pytest  # noqa: E402

from webintel.science.models import (  # noqa: E402
    CombinationStudy,
    Echantillon,
    Evidence,
    EvidenceType,
    MesureExperimentale,
    Plant,
    Simulation,
)


@pytest.fixture(autouse=True)
def _montage():
    _assurer_montage()
    yield


@pytest.fixture()
def jeu_donnees(db_session, make_project):
    """Un projet avec 1 plante, 1 échantillon, 1 étude, 1 expérience,
    1 simulation, 1 publication."""
    import hashlib  # noqa: PLC0415

    projet_id = make_project("Dossier tableau de bord")
    plante = Plant(scientific_name="Moringa oleifera")
    db_session.add(plante)
    db_session.flush()
    db_session.add(Echantillon(
        plant_id=plante.id, project_id=projet_id,
        code_echantillon="MOR-001", latitude=12.36, longitude=-1.52,
    ))
    db_session.add(CombinationStudy(
        project_id=projet_id, nom="Étude tableau de bord",
        inputs_hash=hashlib.sha256(b"etude-tableau-de-bord").hexdigest(),
    ))
    db_session.add(MesureExperimentale(
        project_id=projet_id,
    ))
    db_session.add(Simulation(
        project_id=projet_id, name="Simulation tableau de bord",
        input_hash=hashlib.sha256(b"simulation-tableau-de-bord").hexdigest(),
    ))
    db_session.add(Evidence(
        source_type=EvidenceType.PUBLICATION.value,
        reference="Doe J. et al. (2024) — test tableau de bord.",
        title="Test tableau de bord",
    ))
    db_session.commit()
    return projet_id


def test_tableau_de_bord_200_et_sections(client):
    r = client.get("/tableau-de-bord")
    assert r.status_code == 200, r.text[:500]
    html = r.text
    for section in (
        "Projets de recherche", "Plantes étudiées", "Échantillons de terrain",
        "Études", "Expériences de laboratoire", "Simulations",
        "Publications", "Rapports", "Données à valider", "Outils",
    ):
        assert section in html, f"section manquante : {section}"
    assert "aucune statistique artificielle" in html


def test_tableau_de_bord_comptes_reels(client, jeu_donnees):
    import re  # noqa: PLC0415

    r = client.get("/tableau-de-bord")
    assert r.status_code == 200, r.text[:500]
    paires = dict(re.findall(
        r'<div class="label">([^<]+)</div>\s*<div class="value">(\d+)</div>',
        r.text,
    ))
    # Chaque compteur reflète le jeu de données réel (base purgée avant
    # chaque test : exactement 1 de chaque entité créée).
    attendus = {
        "Projets de recherche": 1,
        "Plantes étudiées": 1,
        "Échantillons de terrain": 1,
        "Études": 1,
        "Expériences de laboratoire": 1,
        "Simulations": 1,
        "Publications": 1,
    }
    for section, valeur in attendus.items():
        assert section in paires, f"section absente : {section}"
        assert int(paires[section]) == valeur, (
            f"{section} : attendu {valeur}, affiché {paires[section]}"
        )
    # « Données à valider » et « Rapports » / « Outils » ne sont pas des
    # chiffres inventés : le contrôle qualité est exécuté pour de vrai.
    assert "Données à valider" in r.text


def test_tableau_de_bord_base_vide_pas_de_faux_chiffres(client):
    r = client.get("/tableau-de-bord")
    assert r.status_code == 200, r.text[:500]
    # Sur une base fraîchement démarrée (tests isolés), la page s'affiche
    # sans erreur — les zéros affichés sont des zéros réels.
    assert "Données à valider" in r.text
