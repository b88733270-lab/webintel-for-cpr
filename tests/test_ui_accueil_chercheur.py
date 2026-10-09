"""Tests UI « chercheur » (v2.2.x) : accueil, fiche plante simplifiée,
recherche universelle, « Mes recherches ».

Le router ``chercheur_accueil_routes`` est enregistré dans
``webintel/main.py`` (``create_app``) ; le montage ci-dessous n'est qu'un
filet de sécurité (garde anti double-enregistrement + suivi d'un éventuel
``importlib.reload(webintel.main)`` par un autre module de la suite).
"""

import re

import pytest

import webintel.main
from webintel.ui import chercheur_accueil_routes


def _router_deja_monte(app, module: str) -> bool:
    """Détecte un routeur déjà inclus, y compris via `_IncludedRouter`
    (FastAPI récent n'aplatit plus les routes incluses : `path` n'est
    alors pas disponible sur l'entrée)."""
    for entree in app.router.routes:
        if getattr(getattr(entree, "endpoint", None), "__module__", None) == module:
            return True
        inner = getattr(entree, "original_router", None)
        for route in getattr(inner, "routes", None) or ():
            if getattr(getattr(route, "endpoint", None), "__module__", None) == module:
                return True
    return False


def _assurer_montage_accueil() -> None:
    """Monte le routeur sur l'app COURANTE (import local : `webintel.main.app`
    est résolu à chaque appel, donc un `importlib.reload(webintel.main)`
    effectué par un autre module de la suite — qui remplace l'objet `app` —
    n'enlève plus les routes : `create_app()` les ré-enregistre et, si
    l'app a été remplacée, le montage est refait ici)."""
    import webintel.main  # noqa: PLC0415

    app = webintel.main.app
    if not _router_deja_monte(app, chercheur_accueil_routes.__name__):
        app.include_router(chercheur_accueil_routes.router)


_assurer_montage_accueil()


@pytest.fixture(autouse=True)
def _montage_accueil_chercheur():
    _assurer_montage_accueil()
    yield

from webintel.science.models import (  # noqa: E402
    CombinationStudy,
    Compound,
    Plant,
    PlantActivity,
    PlantCompound,
    Simulation,
)


@pytest.fixture()
def plante_complete(db_session, make_project):
    """Moringa oleifera + composé lié + activité + étude + simulation."""
    projet_id = make_project("Dossier test chercheur")
    plante = Plant(
        scientific_name="Moringa oleifera",
        family="Moringaceae",
        genus="Moringa",
        species="oleifera",
        description="Arbre tropical d'Afrique et d'Asie.",
        distribution="Zones tropicales.",
        used_parts=["feuilles"],
        synonyms=["Moringa pterygosperma"],
        vernacular_names={"fr": ["moringa"], "wolof": ["neverdaye"]},
    )
    db_session.add(plante)
    db_session.flush()
    compose = Compound(name="Quercétine", formula="C15H10O7")
    db_session.add(compose)
    db_session.flush()
    db_session.add(PlantCompound(
        plant_id=plante.id, compound_id=compose.id, plant_part="feuilles",
        publication="Étude phytochimique de test",
        doi="10.1234/moringa-test", year=2021, confidence="rapporte",
    ))
    db_session.add(PlantActivity(
        plant_id=plante.id, activity="activité antioxydante",
        study_type="in_vitro", study_model="test DPPH",
        published_result="Résultat publié factuel de test.",
        reference="Revue de test, 2021",
    ))
    db_session.add(CombinationStudy(
        project_id=projet_id, nom="Étude test moringa",
        plant_id=plante.id, compound_ids=[compose.id],
        inputs_hash="a" * 64,
    ))
    db_session.add(Simulation(
        project_id=projet_id, name="Simulation test moringa",
        compound_ids=[compose.id], inputs={"plant_id": plante.id},
        input_hash="b" * 64,
    ))
    db_session.commit()
    return {"plante": plante, "projet_id": projet_id}


def _texte(reponse) -> str:
    return reponse.text


def _formulaires_sans_json(html: str) -> None:
    """Aucun champ de formulaire nommé 'json' ni payload JSON exigé."""
    for nom_champ in re.findall(r'name="([^"]+)"', html):
        assert "json" not in nom_champ.lower(), f"champ JSON détecté : {nom_champ}"
    assert "application/json" not in html.lower()


# ---------------------------------------------------------------------------
# 1. Accueil
# ---------------------------------------------------------------------------


def test_accueil_200_et_5_cartes(client):
    r = client.get("/accueil")
    assert r.status_code == 200, r.text[:500]
    html = _texte(r)
    assert "Que souhaitez-vous étudier" in html
    for carte in ["Une plante", "Une molécule", "Une publication",
                  "Une nouvelle étude", "Mes recherches"]:
        assert carte in html, f"carte manquante : {carte}"
    assert 'href="/plante-etude"' in html
    assert 'href="/recherche-science?type=compose"' in html
    assert 'href="/recherche-science?type=publication"' in html
    assert 'href="/etudes/nouvelle"' in html
    assert 'href="/mes-recherches"' in html
    # Barre de recherche globale incluse.
    assert "Rechercher une plante, molécule, publication ou étude" in html
    assert 'action="/recherche-universelle"' in html
    _formulaires_sans_json(html)


def test_accueil_formulations_prudentes(client):
    html = _texte(client.get("/accueil"))
    for interdit in ["efficace", "sûr", "traite"]:
        assert interdit not in html.lower(), f"formulation interdite : {interdit}"


# ---------------------------------------------------------------------------
# 2. Plante : saisie + fiche
# ---------------------------------------------------------------------------


def test_plante_etude_saisie_200(client):
    r = client.get("/plante-etude")
    assert r.status_code == 200, r.text[:500]
    html = _texte(r)
    assert "Étudier une plante" in html
    assert 'name="q"' in html
    _formulaires_sans_json(html)


def test_plante_etude_fiche_sections(client, plante_complete):
    r = client.get("/plante-etude", params={"q": "Moringa"})
    assert r.status_code == 200, r.text[:500]
    html = _texte(r)
    for section in ["IDENTITÉ", "BOTANIQUE", "PHYTOCHIMIE", "PHARMACOLOGIE",
                    "PUBLICATIONS", "SÉCURITÉ", "SIMULATIONS"]:
        assert section in html, f"section manquante : {section}"
    assert "Moringa oleifera" in html
    assert "Quercétine" in html
    assert "activité antioxydante" in html
    assert "Voir les composés" in html
    assert "Voir les études" in html
    assert "Créer une étude" in html
    assert "/plantes/" in html  # lien vers la fiche complète existante
    for interdit in ["efficace", "sûr", "traite"]:
        assert interdit not in html.lower(), f"formulation interdite : {interdit}"


def test_plante_etude_introuvable_propose_creation(client):
    r = client.get("/plante-etude", params={"q": "PlanteQuiNexistePas du tout"})
    assert r.status_code == 200, r.text[:500]
    html = _texte(r)
    assert "Créer la fiche" in html
    assert 'action="/plante-etude/creer"' in html
    assert 'name="nom_scientifique"' in html
    _formulaires_sans_json(html)


def test_plante_etude_creer_fiche(client):
    r = client.post(
        "/plante-etude/creer",
        data={
            "nom_scientifique": "Adansonia digitata",
            "famille": "Malvaceae",
            "langue": "français",
            "noms_locaux": "baobab, pain de singe",
            "description": "Grand arbre africain.",
            "source": "Flore de test, 2020",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:500]
    assert "/plante-etude" in r.headers["location"]
    r2 = client.get(r.headers["location"])
    assert r2.status_code == 200
    html = _texte(r2)
    assert "Adansonia digitata" in html
    assert "baobab" in html
    assert "IDENTITÉ" in html


def test_plante_etude_creer_exige_nom_et_source(client):
    r = client.post("/plante-etude/creer", data={"nom_scientifique": ""})
    assert r.status_code == 200
    assert "obligatoire" in _texte(r).lower()
    r = client.post(
        "/plante-etude/creer",
        data={"nom_scientifique": "Xyz abc", "source": ""},
    )
    assert r.status_code == 200
    assert "source" in _texte(r).lower()


# ---------------------------------------------------------------------------
# 3. Recherche universelle
# ---------------------------------------------------------------------------


def test_recherche_universelle_200_formulaire(client):
    r = client.get("/recherche-universelle")
    assert r.status_code == 200, r.text[:500]
    html = _texte(r)
    assert "Recherche universelle" in html
    assert 'name="q"' in html
    _formulaires_sans_json(html)


def test_recherche_universelle_resultats_sources(client, plante_complete):
    r = client.get("/recherche-universelle", params={"q": "Quercétine"})
    assert r.status_code == 200, r.text[:500]
    html = _texte(r)
    assert "Quercétine" in html
    # Niveau de données + source visibles pour chaque résultat.
    assert "Publié" in html
    assert "Source" in html
    # Groupes affichés.
    assert "Molécules" in html

    r = client.get("/recherche-universelle", params={"q": "Moringa"})
    assert r.status_code == 200, r.text[:500]
    html = _texte(r)
    assert "Moringa oleifera" in html
    assert "Plantes" in html


def test_recherche_universelle_requete_trop_courte(client):
    r = client.get("/recherche-universelle", params={"q": "x"})
    assert r.status_code == 200
    assert "au moins 2 caractères" in _texte(r)


# ---------------------------------------------------------------------------
# 4. Mes recherches
# ---------------------------------------------------------------------------


def test_mes_recherches_200_et_dossier(client, plante_complete, server_bootstrap):
    r = client.get("/mes-recherches")
    assert r.status_code == 200, r.text[:500]
    html = _texte(r)
    assert "Mes recherches" in html
    # En mode serveur, make_project réutilise le projet du bootstrap.
    nom_attendu = (
        "Dossier test chercheur" if server_bootstrap is None else "Projet Test"
    )
    assert nom_attendu in html
    assert "Moringa oleifera" in html
    assert "Quercétine" in html
    assert "Ouvrir" in html
    # Dernière analyse = la plus récente (ici la simulation, créée après
    # l'étude de combinaison dans la fixture) — jamais une valeur inventée.
    assert "Simulation test moringa" in html
    assert "résultat informatique" in html
    for interdit in ["efficace", "sûr", "traite"]:
        assert interdit not in html.lower(), f"formulation interdite : {interdit}"


def test_mes_recherches_vide_200(client):
    r = client.get("/mes-recherches")
    assert r.status_code == 200, r.text[:500]
