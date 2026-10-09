"""Tests de la fiche scientifique complète — Phase 3
(`GET /api/science/plants/{plant_id}/fiche`).

Couverture :
- fiche complète : toutes les sections présentes, badges et `data_level`
  corrects par section ;
- liaison pharmacologie : une preuve n'est rattachée à la plante que si
  `Evidence.doi` égale le `doi` d'un lien `PlantCompound` de la plante ;
- section sécurité : heuristique textuelle (mot-clé FR dans le titre ou
  l'extrait) — jamais un avis médical ;
- bloc laboratoire : absent (`[]`) sans `project_id`, présent avec
  (observations + simulations, cloisonnement au projet) ;
- 403 vers un projet étranger en mode serveur (même pattern que
  `test_science_api.py`) ;
- 404 sur plante absente (message FR) ;
- aucun lien inventé : plante sans lien → `phytochimie` vide ;
- le dictionnaire retourné est JSON-sérialisable.

Aucun réseau : tout passe par le `TestClient` et la base de test.
"""

from __future__ import annotations

import json

import pytest

from webintel.database import models as modeles_noyau
from webintel.database.base import SessionLocal, init_db
from webintel.science import models as science
from webintel.science.fiche import build_plant_fiche

# Garantit la présence des tables scientifiques (idempotent).
init_db()

DOI_LIEN = "10.1016/j.jep.2021.114001"
DOI_ETRANGER = "10.1000/xyz999"


@pytest.fixture(autouse=True)
def _nettoyage_science():
    """Vide les tables scientifiques avant chaque test (isolation complète)."""
    session = SessionLocal()
    try:
        for table in (
            science.ResearchObservation,
            science.Simulation,
            science.Evidence,
            science.PlantCompound,
            science.Compound,
            science.Plant,
        ):
            session.query(table).delete()
        session.commit()
    finally:
        session.close()
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plante(client, nom: str = "Artemisia annua") -> dict:
    reponse = client.post(
        "/api/science/plants",
        json={
            "scientific_name": nom,
            "family": "Asteraceae",
            "vernacular_names": {"fr": "Armoise annuelle"},
            "used_parts": ["feuilles", "sommités fleuries"],
            "description": "Plante herbacée annuelle.",
            "distribution": "Asie tempérée.",
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _compose(client, nom: str = "Artémisinine") -> dict:
    reponse = client.post(
        "/api/science/compounds",
        json={"name": nom, "formula": "C15H22O5", "chemical_class": "sesquiterpène"},
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _lien(client, plante_id: int, compose_id: int, doi: str | None = DOI_LIEN) -> dict:
    corps = {
        "plant_id": plante_id,
        "compound_id": compose_id,
        "plant_part": "feuilles",
        "extraction_method": "macération éthanolique",
        "publication": "J. Ethnopharmacol. 2021",
        "year": 2021,
        "confidence": "publie",
    }
    if doi is not None:
        corps["doi"] = doi
    reponse = client.post(f"/api/science/plants/{plante_id}/compounds", json=corps)
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _preuve(
    client,
    doi: str | None,
    titre: str,
    extrait: str | None = None,
) -> dict:
    corps = {
        "doi": doi,
        "title": titre,
        "reference": "J. Ethnopharmacol. 2021",
        "year": 2021,
        "authors": ["L. Traoré", "A. Diallo"],
    }
    if extrait is not None:
        corps["excerpt"] = extrait
    reponse = client.post("/api/science/evidences", json=corps)
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _observation(client, project_id: int) -> dict:
    reponse = client.post(
        "/api/science/observations",
        json={
            "project_id": project_id,
            "protocol_ref": "PROTO-ART-01",
            "author": "Dr Sawadogo",
            "laboratory": "Labo Ouaga",
            "raw_data": {"rendement_pct": 8.2},
            "comment": "Extraction feuilles, rendement 8,2 %.",
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _simulation(client, project_id: int) -> dict:
    reponse = client.post(
        "/api/science/simulations",
        json={
            "name": "Docking artémisinine",
            "project_id": project_id,
            "target": "PfATP6",
            "method": "docking",
            "parameters": {"ligand": "artémisinine", "n": 50},
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _fiche(client, plante_id: int, project_id: int | None = None) -> dict:
    params = {"project_id": project_id} if project_id is not None else {}
    reponse = client.get(f"/api/science/plants/{plante_id}/fiche", params=params)
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


# ---------------------------------------------------------------------------
# Fiche complète
# ---------------------------------------------------------------------------

def test_fiche_complete_toutes_sections_et_badges(client):
    """La fiche contient les 9 sections, chacune avec son `data_level` et
    son badge ; sans `project_id`, les sections laboratoire sont vides."""
    plante = _plante(client)
    compose = _compose(client)
    _lien(client, plante["id"], compose["id"])
    _preuve(client, DOI_LIEN, "Artémisinine : revue phytochimique")

    fiche = _fiche(client, plante["id"])

    for cle in (
        "plante", "botanique", "pharmacognosie", "phytochimie",
        "pharmacologie", "securite", "bibliographie",
        "laboratoire_prive", "simulations",
    ):
        assert cle in fiche, f"section manquante : {cle}"

    # Identité de la plante.
    assert fiche["plante"]["nom_scientifique"] == "Artemisia annua"
    assert fiche["plante"]["famille"] == "Asteraceae"
    assert fiche["plante"]["data_level"] == "publie"
    assert fiche["plante"]["badge"] == {"label": "Publié", "couleur": "#1a7f37"}

    # Botanique.
    assert fiche["botanique"]["description"] == "Plante herbacée annuelle."
    assert fiche["botanique"]["parties_utilisees"] == ["feuilles", "sommités fleuries"]
    assert fiche["botanique"]["data_level"] == "publie"
    assert fiche["botanique"]["badge"]["couleur"] == "#1a7f37"

    # Pharmacognosie : méthodes d'extraction rapportées, note de prudence.
    pharmaco = fiche["pharmacognosie"]
    assert pharmaco["methodes_extraction_rapporteess"] == ["macération éthanolique"]
    assert "non validées par WebIntel" in pharmaco["note"]
    assert pharmaco["data_level"] == "publie"

    # Phytochimie : un item par lien enregistré, avec les champs du composé.
    assert len(fiche["phytochimie"]) == 1
    item = fiche["phytochimie"][0]
    assert item["nom"] == "Artémisinine"
    assert item["formule"] == "C15H22O5"
    assert item["classe_chimique"] == "sesquiterpène"
    assert item["partie"] == "feuilles"
    assert item["doi"] == DOI_LIEN
    assert item["annee"] == 2021
    assert item["data_level"] == "publie"
    assert item["badge"]["label"] == "Publié"

    # Bibliographie : la preuve liée y figure.
    assert any(DOI_LIEN in citation for citation in fiche["bibliographie"])
    assert any("Traoré" in citation for citation in fiche["bibliographie"])

    # Sans project_id : aucune donnée de laboratoire.
    assert fiche["laboratoire_prive"] == []
    assert fiche["simulations"] == []


def test_fiche_plante_sans_lien_aucun_lien_invente(client):
    """Plante sans aucun lien plante-composé : `phytochimie`,
    `pharmacologie`, `securite` et `bibliographie` sont vides — rien
    n'est inventé."""
    plante = _plante(client)
    _preuve(client, DOI_ETRANGER, "Preuve sans rapport avec la plante")

    fiche = _fiche(client, plante["id"])

    assert fiche["phytochimie"] == []
    assert fiche["pharmacologie"] == []
    assert fiche["securite"] == []
    assert fiche["bibliographie"] == []


# ---------------------------------------------------------------------------
# Liaison pharmacologie par DOI
# ---------------------------------------------------------------------------

def test_pharmacologie_liee_par_doi_uniquement(client):
    """Seule la preuve dont le DOI égale celui du lien plante-composé
    apparaît en pharmacologie ; la preuve au DOI différent est exclue."""
    plante = _plante(client)
    compose = _compose(client)
    _lien(client, plante["id"], compose["id"], doi=DOI_LIEN)
    _preuve(client, DOI_LIEN, "Revue phytochimique liée")
    _preuve(client, DOI_ETRANGER, "Preuve au DOI différent")

    fiche = _fiche(client, plante["id"])

    assert len(fiche["pharmacologie"]) == 1
    preuve = fiche["pharmacologie"][0]
    assert preuve["doi"] == DOI_LIEN
    assert preuve["titre"] == "Revue phytochimique liée"
    assert preuve["data_level"] == "publie"
    assert preuve["badge"] == {"label": "Publié", "couleur": "#1a7f37"}
    # La bibliographie ne reprend que la preuve liée.
    assert len(fiche["bibliographie"]) == 1
    assert DOI_ETRANGER not in " ".join(fiche["bibliographie"])


def test_bibliographie_reprend_publication_lien_sans_doi(client):
    """Un lien sans DOI contribue sa `publication` à la bibliographie."""
    plante = _plante(client)
    compose = _compose(client)
    _lien(client, plante["id"], compose["id"], doi=None)

    fiche = _fiche(client, plante["id"])

    assert fiche["pharmacologie"] == []
    assert len(fiche["bibliographie"]) == 1
    assert "J. Ethnopharmacol. 2021" in fiche["bibliographie"][0]


# ---------------------------------------------------------------------------
# Sécurité : heuristique textuelle
# ---------------------------------------------------------------------------

def test_securite_heuristique_mot_cle(client):
    """Une preuve liée dont le titre contient un mot-clé de prudence
    (« toxique ») apparaît en `securite` avec l'avertissement explicite ;
    la preuve sans mot-clé n'y figure pas (heuristique, pas un avis
    médical)."""
    plante = _plante(client)
    compose = _compose(client)
    _lien(client, plante["id"], compose["id"], doi=DOI_LIEN)
    _preuve(client, DOI_LIEN, "Effets toxiques rapportés de l'artémisinine")
    _preuve(
        client,
        "10.1016/j.jep.2021.114002",
        "Dosage usuel en phytothérapie",
        extrait="Aucun effet indésirable notable aux doses usuelles.",
    )
    # Relie la seconde preuve à la plante via un second lien au même DOI.
    compose2 = _compose(client, "Dihydroartémisinine")
    _lien(client, plante["id"], compose2["id"], doi="10.1016/j.jep.2021.114002")

    fiche = _fiche(client, plante["id"])

    assert len(fiche["pharmacologie"]) == 2
    assert len(fiche["securite"]) == 2  # « toxique » + « effet indésirable »
    for alerte in fiche["securite"]:
        assert alerte["avertissement"] == (
            "Information de sécurité rapportée — consulter la source originale."
        )
        assert alerte["data_level"] == "publie"

    # Sans mot-clé : rien en sécurité.
    plante2 = _plante(client, "Moringa oleifera")
    comp2 = _compose(client, "Quercétine")
    _lien(client, plante2["id"], comp2["id"], doi="10.1234/sur")
    _preuve(client, "10.1234/sur", "Revue nutritionnelle du moringa")
    fiche2 = _fiche(client, plante2["id"])
    assert fiche2["securite"] == []
    assert len(fiche2["pharmacologie"]) == 1


# ---------------------------------------------------------------------------
# Bloc laboratoire : privé, cloisonné au projet
# ---------------------------------------------------------------------------

def test_laboratoire_prive_present_avec_project_id(client, make_project):
    """Avec `project_id` : observations (`experimental`, badge rouge) et
    simulations (`prediction`, badge violet) du projet, avec l'avertissement
    de séparation public/privé."""
    projet_id = make_project("Laboratoire Phyto")
    plante = _plante(client)
    obs = _observation(client, projet_id)
    sim = _simulation(client, projet_id)

    fiche = _fiche(client, plante["id"], project_id=projet_id)

    labo = fiche["laboratoire_prive"]
    assert fiche["avertissement_separation"] == (
        "Données du laboratoire — jamais mélangées aux données publiques."
    )
    # En mode serveur, le projet est partagé (bootstrap unique) : d'autres
    # tests peuvent y avoir laissé des observations — on vérifie la présence
    # de la nôtre, pas un comptage exact (anti-flaky).
    ids_labo = [o["id"] for o in labo]
    assert obs["id"] in ids_labo
    observation = next(o for o in labo if o["id"] == obs["id"])
    assert observation["id"] == obs["id"]
    assert observation["protocol_ref"] == "PROTO-ART-01"
    assert observation["author"] == "Dr Sawadogo"
    assert observation["laboratory"] == "Labo Ouaga"
    assert observation["data_level"] == "experimental"
    assert observation["badge"] == {"label": "Expérimental", "couleur": "#cf222e"}

    sims = fiche["simulations"]
    ids_sims = [s["id"] for s in sims]
    assert sim["id"] in ids_sims
    simulation = next(s for s in sims if s["id"] == sim["id"])
    assert simulation["id"] == sim["id"]
    assert simulation["nom"] == "Docking artémisinine"
    assert simulation["cible"] == "PfATP6"
    assert simulation["methode"] == "docking"
    assert simulation["data_level"] == "prediction"
    assert simulation["badge"] == {"label": "Prédiction", "couleur": "#8250df"}
    assert "confirmer expérimentalement" in simulation["avertissement"].lower()


def test_laboratoire_isole_par_projet_en_local(client, make_project, server_bootstrap):
    """En local, la fiche d'un projet B ne montre pas les données du
    projet A (aucune fuite entre laboratoires)."""
    if server_bootstrap is not None:
        pytest.skip("réservé au mode local (voir le test 403 en mode serveur)")
    projet_a = make_project("Laboratoire A")
    projet_b = make_project("Laboratoire B")
    plante = _plante(client)
    _observation(client, projet_a)
    _simulation(client, projet_a)

    fiche_b = _fiche(client, plante["id"], project_id=projet_b)
    assert fiche_b["laboratoire_prive"] == []
    assert "avertissement_separation" in fiche_b
    assert fiche_b["simulations"] == []

    fiche_a = _fiche(client, plante["id"], project_id=projet_a)
    assert len(fiche_a["laboratoire_prive"]) == 1
    assert fiche_a["laboratoire_prive"][0]["data_level"] == "experimental"
    assert len(fiche_a["simulations"]) == 1


def test_fiche_projet_etranger_403_en_serveur(
    client, make_project, server_bootstrap, db_session
):
    """En mode serveur, demander la fiche avec le `project_id` d'un projet
    étranger → 403 (le cloisonnement prime)."""
    if server_bootstrap is None:
        pytest.skip("réservé au mode serveur (REQUIRE_API_KEY=true)")
    projet_bootstrap = make_project("bootstrap")
    plante = _plante(client)

    autre = modeles_noyau.Project(name="Projet étranger")
    db_session.add(autre)
    db_session.commit()

    reponse = client.get(
        f"/api/science/plants/{plante['id']}/fiche",
        params={"project_id": autre.id},
    )
    assert reponse.status_code == 403, reponse.text

    # Le projet de la clé reste accessible.
    ok = client.get(
        f"/api/science/plants/{plante['id']}/fiche",
        params={"project_id": projet_bootstrap},
    )
    assert ok.status_code == 200, ok.text


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------

def test_fiche_plante_inexistante_404(client):
    """GET /api/science/plants/999999/fiche → 404 avec un message en
    français."""
    reponse = client.get("/api/science/plants/999999/fiche")
    assert reponse.status_code == 404
    assert "introuvable" in reponse.json()["detail"].lower()


def test_build_plant_fiche_leve_keyerror_si_plante_absente(db_session):
    """`build_plant_fiche` lève `KeyError` avec le message attendu."""
    with pytest.raises(KeyError, match=r"Plante introuvable : id=424242"):
        build_plant_fiche(db_session, 424242)


# ---------------------------------------------------------------------------
# Sérialisation JSON
# ---------------------------------------------------------------------------

def test_fiche_json_serialisable(client, make_project):
    """Le dictionnaire retourné par `build_plant_fiche` passe
    `json.dumps` sans erreur (dates en ISO, aucun objet SQLAlchemy)."""
    projet_id = make_project("Laboratoire JSON")
    plante = _plante(client)
    compose = _compose(client)
    _lien(client, plante["id"], compose["id"])
    _preuve(client, DOI_LIEN, "Revue phytochimique")
    _observation(client, projet_id)
    _simulation(client, projet_id)

    session = SessionLocal()
    try:
        fiche = build_plant_fiche(session, plante["id"], projet_id)
        texte = json.dumps(fiche, ensure_ascii=False)
        assert "Artemisia annua" in texte
        assert "experimental" in texte
        assert "prediction" in texte
    finally:
        session.close()
