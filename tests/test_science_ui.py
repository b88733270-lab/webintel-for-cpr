"""Tests de l'interface web scientifique (PHASE 3/4-UI).

- ``/plantes`` : 200, liste les plantes avec leur nombre de composés ;
- ``/plantes/{id}`` : 200 avec les sections de la fiche, 404 (FR) si absente ;
- ``/recherche-science`` : 200, groupes Plantes / Composés / Publications /
  Simulations, pagination.

Les tests de la fiche et de la recherche exercent les contrats backend
``webintel.science.fiche.build_plant_fiche`` et
``webintel.science.search.search_science`` : ils sont ignorés
(``importorskip``) tant que ces modules — développés en parallèle —
ne sont pas présents.
"""

import pytest

from webintel.science.models import Compound, Plant, PlantCompound


@pytest.fixture()
def client(server_bootstrap):
    """Variante du fixture `client` de conftest : en mode serveur, le client
    passe par ``https://testserver`` pour que le cookie de session UI
    (flag ``Secure`` depuis le chantier 0.3) soit renvoyé — pattern repris
    de ``tests/test_ui_auth.py::https_client``. En mode local, identique
    au fixture d'origine.
    """
    from fastapi.testclient import TestClient

    import webintel.main

    headers = {}
    base_url = "http://testserver"
    if server_bootstrap is not None:
        headers["X-API-Key"] = server_bootstrap["plain_key"]
        base_url = "https://testserver"
    with TestClient(webintel.main.app, headers=headers, base_url=base_url) as test_client:
        if server_bootstrap is not None:
            resp = test_client.post(
                "/login", data={"api_key": server_bootstrap["plain_key"]}
            )
            assert resp.status_code in (200, 303), (
                f"login de test impossible en mode serveur : {resp.status_code}"
            )
        yield test_client


@pytest.fixture()
def plante_db(db_session):
    """Plante + composé lié, avec publication et DOI (données publiées)."""
    plante = Plant(
        scientific_name="Artemisia annua",
        family="Asteraceae",
        genus="Artemisia",
        species="annua",
        description="Plante herbacée annuelle d'Asie.",
        distribution="Asie du Sud-Est.",
        used_parts=["feuilles"],
        synonyms=["Artemisia chamomilla"],
        vernacular_names={"fr": ["armoise annuelle"]},
    )
    db_session.add(plante)
    db_session.flush()
    compose = Compound(
        name="Artémisinine",
        formula="C15H22O5",
        chemical_class="Lactone sesquiterpénique",
    )
    db_session.add(compose)
    db_session.flush()
    db_session.add(PlantCompound(
        plant_id=plante.id,
        compound_id=compose.id,
        plant_part="feuilles",
        concentration=0.5,
        concentration_unit="%",
        publication="Étude phytochimique de test",
        doi="10.1234/test-doi",
        year=2020,
        confidence="rapporte",
    ))
    db_session.commit()
    return plante


def test_plantes_liste_ok(client, plante_db):
    resp = client.get("/plantes")
    assert resp.status_code == 200
    html = resp.text
    assert "Artemisia annua" in html
    assert "Asteraceae" in html
    assert f"/plantes/{plante_db.id}" in html
    # lien de navigation présent
    assert 'href="/plantes"' in html


def test_plante_fiche_ok(client, plante_db):
    pytest.importorskip("webintel.science.fiche")
    resp = client.get(f"/plantes/{plante_db.id}")
    assert resp.status_code == 200
    html = resp.text
    assert "Artemisia annua" in html
    # sections attendues de la fiche
    for section in ("Identité", "Botanique", "Pharmacognosie", "Phytochimie",
                    "Pharmacologie", "Bibliographie"):
        assert section in html, f"section manquante : {section}"
    # contenu phytochimique issu du lien plante-composé
    assert "Artémisinine" in html
    assert "10.1234/test-doi" in html
    assert "https://doi.org/10.1234/test-doi" in html


def test_plante_fiche_404(client):
    pytest.importorskip("webintel.science.fiche")
    resp = client.get("/plantes/999999")
    assert resp.status_code == 404


def test_recherche_science_ok(client, plante_db):
    pytest.importorskip("webintel.science.search")
    resp = client.get("/recherche-science", params={"q": "artemisia"})
    assert resp.status_code == 200
    html = resp.text
    # les quatre groupes sont toujours affichés
    for groupe in ("Plantes", "Composés", "Publications", "Simulations"):
        assert groupe in html, f"groupe manquant : {groupe}"
    assert "artemisia" in html


def test_recherche_science_sans_requete(client):
    """Sans mot-clé : 200, formulaire seul, pas d'appel backend."""
    resp = client.get("/recherche-science")
    assert resp.status_code == 200
    assert "Recherche scientifique" in resp.text


# ---------------------------------------------------------------------------
# Extensions — audit Phase 3+4 UI (bugs nav / filtre niveau / rendu fiche)
# ---------------------------------------------------------------------------

def test_nav_sans_doublons(client):
    """Bug (a) : « Plantes » et « Recherche scientifique » figurent une
    seule fois chacun dans la navigation."""
    html = client.get("/").text
    assert html.count('href="/plantes"') == 1
    assert html.count('href="/recherche-science"') == 1


def test_recherche_filtre_niveau_alias(client, plante_db):
    """Bug (c) : `niveau=` est accepté comme alias de `data_level=` et le
    filtre reste sélectionné dans le formulaire."""
    resp = client.get(
        "/recherche-science", params={"q": "artemisia", "niveau": "publie"})
    assert resp.status_code == 200
    assert 'value="publie" selected' in resp.text


def test_fiche_sections_badges_et_labo(client, plante_db):
    """Fiche : sections attendues, pastille de niveau, section labo privée
    visuellement séparée."""
    resp = client.get(f"/plantes/{plante_db.id}")
    assert resp.status_code == 200
    html = resp.text
    for section in ("Données de laboratoire", "Simulations"):
        assert section in html, f"section manquante : {section}"
    # Pastille de niveau (style inline du template : background-color).
    assert "background-color:" in html


def test_plante_fiche_404_message_francais(client):
    """Plante inconnue : 404 avec message en français."""
    resp = client.get("/plantes/999999")
    assert resp.status_code == 404
    assert "introuvable" in resp.text.lower()
