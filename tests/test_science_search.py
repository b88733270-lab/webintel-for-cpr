"""Tests de la recherche scientifique plein corpus — Phase 4 (backend).

Couverture (`GET /api/science/recherche` + contrat `search_science`) :
- recherche par nom vernaculaire → la plante (insensible à la casse) ;
- recherche par nom de composé → le composé + `plantes_liees` via les liens
  réellement enregistrés (AUCUN lien inventé : composé sans lien →
  `plantes_liees == []`) ;
- filtre `partie` : seules les plantes ayant un lien réel avec cette partie ;
- filtres `data_level` et `type` (dont 400 sur valeur inconnue) ;
- simulation (avec `project_id`) → groupe `simulations`, `data_level`
  "prediction", avertissement explicite — jamais présentée comme un fait ;
  sans `project_id`, aucune simulation n'est exposée ;
- pagination `limit` / `offset` par groupe, `total` = somme des groupes ;
- `q` trop court → 400 (message FR) ;
- 403 vers un projet étranger en mode serveur ;
- contrat exact de `search_science` (signature importée par l'agent UI).

Aucun réseau : tout passe par le `TestClient` et la base de test.
"""

from __future__ import annotations

import pytest

from webintel.database import models as modeles_noyau
from webintel.science.search import search_science


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plante(client, nom: str, famille: str = "FamilleTest",
            vernaculaires: dict | None = None) -> dict:
    """Crée une plante via l'API et retourne sa fiche."""
    corps: dict = {"scientific_name": nom, "family": famille}
    if vernaculaires is not None:
        corps["vernacular_names"] = vernaculaires
    reponse = client.post("/api/science/plants", json=corps)
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _compose(client, nom: str, classe: str = "classe-test") -> dict:
    """Crée un composé via l'API et retourne sa fiche."""
    reponse = client.post(
        "/api/science/compounds",
        json={"name": nom, "formula": "C6H12O6", "chemical_class": classe},
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _lien(client, plante_id: int, compose_id: int, partie: str) -> dict:
    """Crée un lien plante-composé réel via l'API."""
    reponse = client.post(
        f"/api/science/plants/{plante_id}/compounds",
        json={
            "plant_id": plante_id,
            "compound_id": compose_id,
            "plant_part": partie,
            "publication": "Revue de test 2026",
        },
    )
    assert reponse.status_code in (200, 201), reponse.text
    return reponse.json()


def _preuve(client, titre: str, doi: str) -> dict:
    """Crée une preuve (publication) via l'API."""
    reponse = client.post(
        "/api/science/evidences",
        json={
            "doi": doi,
            "title": titre,
            "authors": ["A. Testeur"],
            "year": 2026,
            "reference": "Revue de test",
        },
    )
    assert reponse.status_code in (200, 201), reponse.text
    return reponse.json()


def _simulation(client, projet_id: int, nom: str = "Docking COX-2") -> dict:
    """Crée une simulation de laboratoire via l'API."""
    reponse = client.post(
        "/api/science/simulations",
        json={
            "name": nom,
            "project_id": projet_id,
            "target": "COX-2",
            "method": "docking",
            "parameters": {"n": 10},
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _recherche(client, **params) -> dict:
    """GET /api/science/recherche, 200 attendu, retourne le JSON."""
    reponse = client.get("/api/science/recherche", params=params)
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


# ---------------------------------------------------------------------------
# Recherche de base : plantes, insensibilité à la casse, noms vernaculaires
# ---------------------------------------------------------------------------

def test_recherche_par_nom_vernaculaire_trouve_la_plante(client):
    """Le nom vernaculaire (colonne JSON) est cherché : q=« armoise »
    retrouve Artemisia annua, avec badge « publie » et sans avertissement."""
    _plante(client, "Artemisia annua", "Asteraceae",
            vernaculaires={"fr": "armoise annuelle"})
    resultat = _recherche(client, q="armoise")

    libelles = [p["libelle"] for p in resultat["plantes"]]
    assert "Artemisia annua" in libelles
    fiche = next(p for p in resultat["plantes"]
                 if p["libelle"] == "Artemisia annua")
    assert fiche["type"] == "plante"
    assert fiche["data_level"] == "publie"
    assert fiche["badge"] == {"label": "Publié", "couleur": "#1a7f37"}
    assert fiche["avertissement"] is None
    assert "parties_trouvees" in fiche


def test_recherche_insensible_a_la_casse(client):
    """q=« ARMOISE » (majuscules) retrouve la plante en minuscules."""
    _plante(client, "Artemisia annua", "Asteraceae",
            vernaculaires={"fr": "armoise annuelle"})
    resultat = _recherche(client, q="ARMOISE")
    assert any(p["libelle"] == "Artemisia annua"
               for p in resultat["plantes"])


def test_recherche_par_famille(client):
    """La famille fait partie des champs cherchés."""
    _plante(client, "Moringa oleifera", "Moringaceae")
    resultat = _recherche(client, q="moringaceae")
    assert any(p["libelle"] == "Moringa oleifera"
               for p in resultat["plantes"])


def test_caracteres_speciaux_like_echappes(client):
    """« % » et « _ » dans la requête sont échappés : pas d'erreur, pas de
    correspondance fortuite élargie."""
    _plante(client, "Moringa oleifera")
    resultat = _recherche(client, q="100%_test")
    assert resultat["total"] == 0
    assert resultat["plantes"] == []
    assert resultat["composes"] == []


# ---------------------------------------------------------------------------
# Composés : plantes_liees via liens réels, AUCUN lien inventé
# ---------------------------------------------------------------------------

def test_recherche_compose_avec_plantes_liees_reelles(client):
    """q=« quercétine » → le composé, avec `plantes_liees` issu du lien
    PlantCompound réellement enregistré (formule + classe en détail)."""
    plante = _plante(client, "Moringa oleifera")
    compose = _compose(client, "Quercétine", classe="flavonoïde")
    _lien(client, plante["id"], compose["id"], "feuilles")

    resultat = _recherche(client, q="quercétine")
    assert len(resultat["composes"]) == 1
    fiche = resultat["composes"][0]
    assert fiche["type"] == "compose"
    assert fiche["libelle"] == "Quercétine"
    assert "flavonoïde" in fiche["detail"]
    assert fiche["data_level"] == "publie"
    assert fiche["badge"] == {"label": "Publié", "couleur": "#1a7f37"}
    assert fiche["avertissement"] is None
    assert fiche["plantes_liees"] == ["Moringa oleifera"]


def test_aucun_lien_invente_compose_sans_lien(client):
    """Un composé sans aucun lien PlantCompound expose `plantes_liees == []` :
    la recherche n'invente jamais d'association."""
    _plante(client, "Moringa oleifera")
    _compose(client, "Caféine", classe="alcaloïde")

    resultat = _recherche(client, q="caféine")
    assert len(resultat["composes"]) == 1
    assert resultat["composes"][0]["plantes_liees"] == []


def test_recherche_compose_par_classe_chimique(client):
    """La classe chimique fait partie des champs cherchés."""
    _compose(client, "Rutine", classe="flavonoïde")
    resultat = _recherche(client, q="flavonoïde")
    assert any(c["libelle"] == "Rutine" for c in resultat["composes"])


# ---------------------------------------------------------------------------
# Filtre `partie`
# ---------------------------------------------------------------------------

def test_filtre_partie_ne_garde_que_les_plantes_liees(client):
    """Avec partie=« feuilles », seules les plantes ayant un lien réel avec
    cette partie sont retournées, avec `parties_trouvees`."""
    alpha = _plante(client, "Partietest alpha")
    beta = _plante(client, "Partietest beta")
    _lien(client, alpha["id"], _compose(client, "Composé alpha")["id"], "feuilles")
    _lien(client, beta["id"], _compose(client, "Composé beta")["id"], "racines")

    resultat = _recherche(client, q="partietest", partie="feuilles")
    libelles = [p["libelle"] for p in resultat["plantes"]]
    assert libelles == ["Partietest alpha"]
    assert resultat["plantes"][0]["parties_trouvees"] == ["feuilles"]

    # Sans filtre : les deux plantes sont retournées.
    sans_filtre = _recherche(client, q="partietest")
    assert len(sans_filtre["plantes"]) == 2


def test_filtre_partie_insensible_a_la_casse(client):
    """partie=« FEUILLES » (majuscules) filtre comme « feuilles »."""
    alpha = _plante(client, "Partietest alpha")
    _lien(client, alpha["id"], _compose(client, "Composé alpha")["id"], "feuilles")
    resultat = _recherche(client, q="partietest", partie="FEUILLES")
    assert [p["libelle"] for p in resultat["plantes"]] == ["Partietest alpha"]


# ---------------------------------------------------------------------------
# Filtres `data_level` et `type`
# ---------------------------------------------------------------------------

def test_filtre_data_level_publie_exclut_les_simulations(client, make_project):
    """data_level=« publie » : les simulations (prediction) sont exclues."""
    projet_id = make_project("Laboratoire A")
    _plante(client, "Moringa oleifera")
    _simulation(client, projet_id)

    resultat = _recherche(client, q="moringa", data_level="publie",
                          project_id=projet_id)
    assert any(p["libelle"] == "Moringa oleifera"
               for p in resultat["plantes"])
    assert resultat["simulations"] == []


def test_filtre_data_level_prediction_ne_garde_que_les_simulations(
    client, make_project
):
    """data_level=« prediction » : seuls les groupes de ce niveau restent."""
    projet_id = make_project("Laboratoire A")
    _plante(client, "Moringa oleifera")
    _simulation(client, projet_id)

    resultat = _recherche(client, q="docking", data_level="prediction",
                          project_id=projet_id)
    assert len(resultat["simulations"]) == 1
    assert resultat["plantes"] == []
    assert resultat["composes"] == []
    assert resultat["publications"] == []


def test_data_level_inconnu_400(client):
    """Un niveau de données inconnu → 400 (message FR)."""
    reponse = client.get("/api/science/recherche",
                         params={"q": "moringa", "data_level": "bidon"})
    assert reponse.status_code == 400, reponse.text
    assert "bidon" in reponse.json()["detail"]


def test_filtre_type_plante_uniquement(client):
    """type=« plante » : seul le groupe plantes est interrogé."""
    _plante(client, "Moringa oleifera")
    _preuve(client, "Moringa oleifera : revue", "10.9999/type-test")

    resultat = _recherche(client, q="moringa", type="plante")
    assert len(resultat["plantes"]) == 1
    assert resultat["composes"] == []
    assert resultat["publications"] == []
    assert resultat["simulations"] == []


def test_filtre_type_repetable(client):
    """`type` est répétable : ?type=plante&type=publication."""
    _plante(client, "Moringa oleifera")
    _preuve(client, "Moringa oleifera : revue", "10.9999/type-test")

    reponse = client.get(
        "/api/science/recherche",
        params=[("q", "moringa"), ("type", "plante"), ("type", "publication")],
    )
    assert reponse.status_code == 200, reponse.text
    resultat = reponse.json()
    assert len(resultat["plantes"]) == 1
    assert len(resultat["publications"]) == 1
    assert resultat["composes"] == []


def test_type_inconnu_400(client):
    """Un type inconnu → 400 (message FR)."""
    reponse = client.get("/api/science/recherche",
                         params={"q": "moringa", "type": "bidon"})
    assert reponse.status_code == 400, reponse.text
    assert "bidon" in reponse.json()["detail"]


# ---------------------------------------------------------------------------
# Publications
# ---------------------------------------------------------------------------

def test_recherche_publication(client):
    """Une preuve est retrouvée par son titre, avec citation courte et DOI."""
    _preuve(client, "Moringa oleifera : revue systématique",
            "10.9999/recherche-test")

    resultat = _recherche(client, q="systématique")
    assert len(resultat["publications"]) == 1
    fiche = resultat["publications"][0]
    assert fiche["type"] == "publication"
    assert fiche["data_level"] == "publie"
    assert fiche["badge"] == {"label": "Publié", "couleur": "#1a7f37"}
    assert "A. Testeur" in fiche["detail"]
    assert "2026" in fiche["detail"]
    assert fiche["source"]["doi"] == "10.9999/recherche-test"
    assert fiche["avertissement"] is None


# ---------------------------------------------------------------------------
# Simulations : prediction + avertissement, jamais un fait, cloisonnées
# ---------------------------------------------------------------------------

def test_simulation_avec_project_id_prediction_et_avertissement(
    client, make_project
):
    """Avec `project_id`, la simulation apparaît : data_level « prediction »,
    badge violet, avertissement exigeant une confirmation expérimentale —
    jamais présentée comme un fait (ni « publie », ni « experimental »)."""
    projet_id = make_project("Laboratoire A")
    _simulation(client, projet_id)

    resultat = _recherche(client, q="docking", project_id=projet_id)
    assert len(resultat["simulations"]) == 1
    fiche = resultat["simulations"][0]
    assert fiche["type"] == "simulation"
    assert fiche["libelle"] == "Docking COX-2"
    assert fiche["data_level"] == "prediction"
    assert fiche["data_level"] not in ("publie", "experimental")
    assert fiche["badge"] == {"label": "Prédiction", "couleur": "#8250df"}
    assert fiche["avertissement"] is not None
    assert "confirmer expérimentalement" in fiche["avertissement"].lower()


def test_simulation_sans_project_id_jamais_exposee(client, make_project):
    """Sans `project_id`, les simulations ne sont PAS cherchées (données de
    laboratoire cloisonnées)."""
    projet_id = make_project("Laboratoire A")
    _simulation(client, projet_id)

    resultat = _recherche(client, q="docking")
    assert resultat["simulations"] == []


def test_simulation_autre_projet_non_exposee(client, make_project,
                                             server_bootstrap):
    """En local, les simulations d'un autre projet ne fuient pas."""
    if server_bootstrap is not None:
        pytest.skip("réservé au mode local (voir le test 403 en mode serveur)")
    projet_a = make_project("Laboratoire A")
    projet_b = make_project("Laboratoire B")
    _simulation(client, projet_a, nom="Docking COX-2 A")

    resultat = _recherche(client, q="docking", project_id=projet_b)
    assert resultat["simulations"] == []


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_pagination_limit_offset(client):
    """`limit`/`offset` s'appliquent par groupe ; `total` = somme des groupes
    retournés ; la structure de réponse est complète."""
    for nom in ("Pagtest alpha", "Pagtest beta", "Pagtest gamma"):
        _plante(client, nom)

    page1 = _recherche(client, q="pagtest", limit=2)
    assert [p["libelle"] for p in page1["plantes"]] == [
        "Pagtest alpha", "Pagtest beta"]
    assert page1["total"] == 2
    assert page1["limit"] == 2
    assert page1["offset"] == 0
    assert set(page1) == {"q", "plantes", "composes", "publications",
                          "simulations", "total", "limit", "offset"}

    page2 = _recherche(client, q="pagtest", limit=2, offset=2)
    assert [p["libelle"] for p in page2["plantes"]] == ["Pagtest gamma"]
    assert page2["total"] == 1
    assert page2["offset"] == 2

    page3 = _recherche(client, q="pagtest", limit=2, offset=10)
    assert page3["plantes"] == []
    assert page3["total"] == 0


# ---------------------------------------------------------------------------
# Validation : q trop court → 400
# ---------------------------------------------------------------------------

def test_q_trop_court_400(client):
    """q d'un seul caractère → 400 avec message en français."""
    reponse = client.get("/api/science/recherche", params={"q": "a"})
    assert reponse.status_code == 400, reponse.text
    assert "2 caractères" in reponse.json()["detail"]


def test_q_espaces_uniquement_400(client):
    """q vide ou espaces uniquement → 400 (espaces non comptés)."""
    reponse = client.get("/api/science/recherche", params={"q": "   "})
    assert reponse.status_code == 400, reponse.text


# ---------------------------------------------------------------------------
# Sécurité : 403 vers un projet étranger en mode serveur
# ---------------------------------------------------------------------------

def test_recherche_projet_etranger_403_en_serveur(
    client, make_project, server_bootstrap, db_session
):
    """En mode serveur, ?project_id=<projet étranger> → 403 (même pattern
    que test_science_api.py)."""
    if server_bootstrap is None:
        pytest.skip("réservé au mode serveur (REQUIRE_API_KEY=true)")
    make_project("bootstrap")

    autre = modeles_noyau.Project(name="Projet étranger")
    db_session.add(autre)
    db_session.commit()

    reponse = client.get("/api/science/recherche",
                         params={"q": "docking", "project_id": autre.id})
    assert reponse.status_code == 403, reponse.text


# ---------------------------------------------------------------------------
# Contrat direct de `search_science` (signature importée par l'agent UI)
# ---------------------------------------------------------------------------

def test_search_science_signature_exacte(client, db_session):
    """`search_science` expose exactement la signature contractuelle et
    retourne des types JSON-sérialisables."""
    import inspect
    import json

    signature = inspect.signature(search_science)
    parametres = list(signature.parameters.values())
    assert [p.name for p in parametres] == [
        "db", "q", "types", "data_level", "partie", "project_id",
        "limit", "offset",
    ]
    # `db` et `q` positionnels, tout le reste keyword-only avec défauts.
    assert parametres[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parametres[1].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    for p in parametres[2:]:
        assert p.kind == inspect.Parameter.KEYWORD_ONLY, p.name
    assert signature.parameters["types"].default is None
    assert signature.parameters["limit"].default == 20
    assert signature.parameters["offset"].default == 0

    _plante(client, "Moringa oleifera")
    resultat = search_science(db_session, "moringa", types=["plante"],
                              limit=10, offset=0)
    # JSON-sérialisable (contrat UI).
    json.dumps(resultat)
    assert resultat["q"] == "moringa"
    assert [p["libelle"] for p in resultat["plantes"]] == ["Moringa oleifera"]
    assert resultat["total"] == 1


def test_search_science_valeurs_invalides_levent_valueerror(db_session):
    """Appel direct : q trop courte, type ou niveau inconnus → ValueError."""
    with pytest.raises(ValueError, match="2 caractères"):
        search_science(db_session, "x")
    with pytest.raises(ValueError, match="Type de résultat inconnu"):
        search_science(db_session, "moringa", types=["bidon"])
    with pytest.raises(ValueError, match="Niveau de données inconnu"):
        search_science(db_session, "moringa", data_level="bidon")


def test_search_science_limit_bornee(db_session, client):
    """`limit` est bornée 1..100, `offset` ≥ 0 (même en appel direct)."""
    _plante(client, "Moringa oleifera")
    resultat = search_science(db_session, "moringa", limit=500, offset=-5)
    assert resultat["limit"] == 100
    assert resultat["offset"] == 0
