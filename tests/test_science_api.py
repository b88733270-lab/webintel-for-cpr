"""Tests de l'API scientifique — Phase 2 (`/api/science/*`).

Couverture :
- plantes : création 201 puis re-POST 200 (idempotence), recherche `?q=`,
  404 sur id inexistant ;
- composés : création, détail, 404, conflit InChIKey → 409 ;
- lien plante-composé : POST idempotent + liste ;
- preuves : double POST du même DOI → une seule preuve ;
- `data_level` présent et correct (plante → « publie », observation →
  « experimental », simulation → « prediction ») ;
- simulation : `avertissement` exigeant une confirmation expérimentale ;
- `GET /api/science/data-levels` → 5 niveaux ;
- séparation public/privé : observations cloisonnées au projet (403 en
  mode serveur vers un projet étranger, isolation stricte en local) ;
- `anon_client` : 401 en mode serveur sur les écritures, 201 en local.

Aucun réseau : tout passe par le `TestClient` et la base de test.
"""

from __future__ import annotations

import pytest

from webintel.database import models as modeles_noyau
from webintel.database.base import SessionLocal, init_db
from webintel.science import models as science

# Garantit la présence des tables scientifiques (idempotent).
init_db()

DOI_PREUVE = "10.1016/j.jep.2020.113123"
INCHIKEY_QUERCETINE = "REFJWTPEDVJJIY-UHFFFAOYSA-N"


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

def _plante(client, nom: str = "Moringa oleifera", famille: str = "Moringaceae") -> dict:
    """Crée une plante via l'API et retourne sa fiche."""
    reponse = client.post(
        "/api/science/plants",
        json={
            "scientific_name": nom,
            "family": famille,
            "vernacular_names": {"fr": "Moringa"},
            "used_parts": ["feuilles"],
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _compose(client, nom: str = "Quercétine", inchikey: str | None = None) -> dict:
    """Crée un composé via l'API et retourne sa fiche."""
    corps = {"name": nom, "formula": "C15H10O7", "chemical_class": "flavonoïde"}
    if inchikey:
        corps["inchikey"] = inchikey
    reponse = client.post("/api/science/compounds", json=corps)
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _observation(client, project_id: int, commentaire: str = "Essai 1") -> dict:
    """Crée une observation de laboratoire via l'API."""
    reponse = client.post(
        "/api/science/observations",
        json={
            "project_id": project_id,
            "protocol_ref": "PROTO-001",
            "author": "Dr Sawadogo",
            "raw_data": {"rendement_pct": 12.5},
            "comment": commentaire,
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _simulation(client, project_id: int | None = None) -> dict:
    """Crée une simulation via l'API (avec des entrées non vides)."""
    corps: dict = {
        "name": "Docking COX-2",
        "target": "COX-2",
        "method": "docking",
        "parameters": {"ligand": "quercétine", "n": 100},
    }
    if project_id is not None:
        corps["project_id"] = project_id
    reponse = client.post("/api/science/simulations", json=corps)
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


# ---------------------------------------------------------------------------
# Plantes
# ---------------------------------------------------------------------------

def test_creation_plante_201_puis_repost_200_idempotent(client):
    """POST /api/science/plants → 201 à la création, 200 + fiche existante au
    re-POST identique, une seule plante en base."""
    corps = {"scientific_name": "Moringa oleifera", "family": "Moringaceae"}
    r1 = client.post("/api/science/plants", json=corps)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/api/science/plants", json=corps)
    assert r2.status_code == 200, r2.text
    assert r1.json()["id"] == r2.json()["id"]

    liste = client.get("/api/science/plants")
    assert liste.status_code == 200
    assert len(liste.json()) == 1


def test_fiche_plante_expose_data_level_publie(client):
    """Chaque réponse plante contient `data_level` = « publie »."""
    fiche = _plante(client)
    assert fiche["data_level"] == "publie"
    detail = client.get(f"/api/science/plants/{fiche['id']}")
    assert detail.status_code == 200
    assert detail.json()["data_level"] == "publie"


def test_recherche_plante_par_q(client):
    """GET /api/science/plants?q=moringa retrouve la plante créée."""
    _plante(client, "Moringa oleifera", "Moringaceae")
    _plante(client, "Artemisia annua", "Asteraceae")
    reponse = client.get("/api/science/plants", params={"q": "moringa"})
    assert reponse.status_code == 200
    noms = [p["scientific_name"] for p in reponse.json()]
    assert "Moringa oleifera" in noms
    assert "Artemisia annua" not in noms


def test_detail_plante_inexistante_404(client):
    reponse = client.get("/api/science/plants/999999")
    assert reponse.status_code == 404


# ---------------------------------------------------------------------------
# Composés
# ---------------------------------------------------------------------------

def test_creation_compose_et_detail(client):
    """POST /api/science/compounds → 201 ; GET /{id} → 200 avec data_level."""
    fiche = _compose(client, inchikey=INCHIKEY_QUERCETINE)
    assert fiche["name"] == "Quercétine"
    assert fiche["data_level"] == "publie"

    detail = client.get(f"/api/science/compounds/{fiche['id']}")
    assert detail.status_code == 200
    assert detail.json()["inchikey"] == INCHIKEY_QUERCETINE

    liste = client.get("/api/science/compounds", params={"q": "quercétine"})
    assert liste.status_code == 200
    assert any(c["name"] == "Quercétine" for c in liste.json())


def test_detail_compose_inexistant_404(client):
    assert client.get("/api/science/compounds/999999").status_code == 404


def test_inchikey_deja_utilisee_par_un_autre_compose_409(client):
    """Un InChIKey déjà rattaché à un autre composé → 409 (conflit)."""
    _compose(client, "Quercétine", inchikey=INCHIKEY_QUERCETINE)
    reponse = client.post(
        "/api/science/compounds",
        json={"name": "Rutine", "inchikey": INCHIKEY_QUERCETINE},
    )
    assert reponse.status_code == 409, reponse.text


# ---------------------------------------------------------------------------
# Lien plante-composé
# ---------------------------------------------------------------------------

def test_lien_plante_compose_idempotent(client):
    """POST /science/plants/{id}/compounds → 201 puis 200 au re-POST ;
    la liste ne contient qu'une seule association."""
    plante = _plante(client)
    compose = _compose(client)
    corps = {
        "plant_id": plante["id"],
        "compound_id": compose["id"],
        "plant_part": "feuilles",
        "publication": "J. Ethnopharmacol. 2020",
        "confidence": "publie",
    }
    r1 = client.post(f"/api/science/plants/{plante['id']}/compounds", json=corps)
    assert r1.status_code == 201, r1.text
    r2 = client.post(f"/api/science/plants/{plante['id']}/compounds", json=corps)
    assert r2.status_code == 200, r2.text
    assert r1.json()["id"] == r2.json()["id"]

    liste = client.get(f"/api/science/plants/{plante['id']}/compounds")
    assert liste.status_code == 200
    assert len(liste.json()) == 1
    assert liste.json()[0]["plant_part"] == "feuilles"


def test_lien_vers_compose_inexistant_404(client):
    """Associer un composé absent → 404 (pas de lien orphelin)."""
    plante = _plante(client)
    reponse = client.post(
        f"/api/science/plants/{plante['id']}/compounds",
        json={"plant_id": plante["id"], "compound_id": 999999, "plant_part": "feuilles"},
    )
    assert reponse.status_code == 404


def test_liste_composes_plante_inexistante_404(client):
    assert client.get("/api/science/plants/999999/compounds").status_code == 404


# ---------------------------------------------------------------------------
# Preuves
# ---------------------------------------------------------------------------

def test_double_post_meme_doi_une_seule_preuve(client):
    """POST /api/science/evidences deux fois avec le même DOI → 201 puis 200,
    une seule preuve en base (idempotence d'import)."""
    corps = {
        "doi": DOI_PREUVE,
        "title": "Moringa oleifera : revue",
        "year": 2020,
        "authors": ["A. Diallo"],
    }
    r1 = client.post("/api/science/evidences", json=corps)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/api/science/evidences", json=corps)
    assert r2.status_code == 200, r2.text
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["data_level"] == "publie"

    liste = client.get("/api/science/evidences")
    assert liste.status_code == 200
    assert len(liste.json()) == 1

    # Filtre par DOI exact.
    filtree = client.get("/api/science/evidences", params={"doi": DOI_PREUVE})
    assert filtree.status_code == 200
    assert len(filtree.json()) == 1


# ---------------------------------------------------------------------------
# Niveaux de données
# ---------------------------------------------------------------------------

def test_data_levels_renvoie_les_cinq_niveaux(client):
    """GET /api/science/data-levels → les 5 niveaux avec leurs codes."""
    reponse = client.get("/api/science/data-levels")
    assert reponse.status_code == 200
    niveaux = reponse.json()["levels"]
    assert len(niveaux) == 5
    codes = {n["code"] for n in niveaux}
    assert codes == {"publie", "normalise", "hypothese", "prediction", "experimental"}


# ---------------------------------------------------------------------------
# Observations : data_level + séparation public/privé
# ---------------------------------------------------------------------------

def test_observation_data_level_experimental(client, make_project):
    """Une observation créée via l'API porte data_level = « experimental »."""
    projet_id = make_project("Laboratoire A")
    obs = _observation(client, projet_id)
    assert obs["data_level"] == "experimental"
    assert obs["project_id"] == projet_id
    assert obs["is_private"] is True


def test_observations_isolees_par_projet_en_local(client, make_project, server_bootstrap):
    """En local, GET ?project_id= ne retourne que les observations du projet
    demandé (aucune fuite entre projets)."""
    if server_bootstrap is not None:
        pytest.skip("réservé au mode local (voir le test 403 en mode serveur)")
    projet_a = make_project("Laboratoire A")
    projet_b = make_project("Laboratoire B")
    _observation(client, projet_a, commentaire="mesure A")

    vue_b = client.get("/api/science/observations", params={"project_id": projet_b})
    assert vue_b.status_code == 200
    assert vue_b.json() == []

    vue_a = client.get("/api/science/observations", params={"project_id": projet_a})
    assert vue_a.status_code == 200
    assert len(vue_a.json()) == 1
    assert vue_a.json()[0]["comment"] == "mesure A"


def test_observations_projet_etranger_403_en_serveur(
    client, make_project, server_bootstrap, db_session
):
    """En mode serveur, une clé ne peut pas lire les observations d'un autre
    projet : GET ?project_id=<étranger> → 403."""
    if server_bootstrap is None:
        pytest.skip("réservé au mode serveur (REQUIRE_API_KEY=true)")
    projet_bootstrap = make_project("bootstrap")
    _observation(client, projet_bootstrap, commentaire="mesure bootstrap")

    # Second projet créé directement en base (l'API interdit la création de
    # projet en mode serveur : seul le projet du bootstrap est accessible).
    autre = modeles_noyau.Project(name="Projet étranger")
    db_session.add(autre)
    db_session.commit()

    reponse = client.get(
        "/api/science/observations", params={"project_id": autre.id}
    )
    assert reponse.status_code == 403, reponse.text

    # ... mais le projet de la clé reste lisible.
    miennes = client.get(
        "/api/science/observations", params={"project_id": projet_bootstrap}
    )
    assert miennes.status_code == 200
    assert len(miennes.json()) == 1


# ---------------------------------------------------------------------------
# Simulations : data_level, avertissement, cloisonnement
# ---------------------------------------------------------------------------

def test_simulation_data_level_prediction_et_avertissement(client, make_project):
    """Une simulation créée via l'API porte data_level = « prediction » et un
    `avertissement` exigeant une confirmation expérimentale."""
    projet_id = make_project("Laboratoire A")
    sim = _simulation(client, projet_id)
    assert sim["data_level"] == "prediction"
    assert "confirmer expérimentalement" in sim["avertissement"].lower()

    detail = client.get(f"/api/science/simulations/{sim['id']}")
    assert detail.status_code == 200
    assert detail.json()["data_level"] == "prediction"
    assert "confirmer expérimentalement" in detail.json()["avertissement"].lower()


def test_simulation_cloisonnee_au_projet(client, make_project, server_bootstrap):
    """En local, les simulations sont filtrées par ?project_id= ; en serveur,
    le projet étranger est refusé (403)."""
    projet_a = make_project("Laboratoire A")
    _simulation(client, projet_a)

    if server_bootstrap is None:
        projet_b = make_project("Laboratoire B")
        vue_b = client.get("/api/science/simulations", params={"project_id": projet_b})
        assert vue_b.status_code == 200
        assert vue_b.json() == []
        vue_a = client.get("/api/science/simulations", params={"project_id": projet_a})
        assert vue_a.status_code == 200
        assert len(vue_a.json()) == 1
    else:
        assert (
            client.get("/api/science/simulations", params={"project_id": 999999}).status_code
            == 403
        )


def test_detail_simulation_inexistante_404(client):
    assert client.get("/api/science/simulations/999999").status_code == 404


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------

def test_post_plante_anonyme_401_en_serveur_201_en_local(anon_client, server_bootstrap):
    """Sans clé API : 401 en mode serveur, 201 en mode local (accès ouvert)."""
    reponse = anon_client.post(
        "/api/science/plants", json={"scientific_name": "Moringa oleifera"}
    )
    if server_bootstrap is None:
        assert reponse.status_code == 201, reponse.text
    else:
        assert reponse.status_code == 401, reponse.text
