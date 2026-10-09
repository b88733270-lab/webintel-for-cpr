"""Tests Phase 8 — laboratoire / projet (backend) : journal versionné,
publication explicite, tableau de bord.

Branchement test-only du routeur ``science_lab`` : reproduit exactement
ce que le coordinateur fera dans ``webintel/main.py``
(``app.include_router(science_lab.router, prefix="/api")``) sans toucher
au fichier. Les tables ``lab_*`` / ``publication_records`` sont créées
ici (le coordinateur les enregistrera dans ``init_db``) et nettoyées
avant chaque test (la fixture globale de ``conftest.py`` ne les connaît
pas encore).
"""

import pytest

import webintel.main
from webintel.api.routers import science_lab
from webintel.database import models as modeles_noyau
from webintel.database.base import Base, engine
from webintel.science import lab as modeles_lab  # noqa: F401 (enregistre les tables)

# Enregistrement du routeur (test-only) : le coordinateur fera la même
# chose dans webintel/main.py.
webintel.main.app.include_router(science_lab.router, prefix="/api", tags=["science"])

# Création des tables lab_* / publication_records pour la base de test
# (le coordinateur les enregistrera dans init_db ; create_all est idempotent).
Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def _nettoyage_lab(db_session):
    """Vide les tables du laboratoire avant chaque test (isolation)."""
    for modele in (
        modeles_lab.PublicationRecord,
        modeles_lab.LabNoteVersion,
        modeles_lab.LabNote,
    ):
        db_session.query(modele).delete()
    db_session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _note(client, project_id, titre="Journal essai 1", contenu="Mesures du jour."):
    """Crée une note de laboratoire via l'API."""
    reponse = client.post(
        "/api/science/lab/notes",
        json={
            "project_id": project_id,
            "titre": titre,
            "contenu": contenu,
            "auteur": "Dr Sawadogo",
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _observation(client, project_id, commentaire="Essai labo 1"):
    """Crée une observation de laboratoire via l'API."""
    reponse = client.post(
        "/api/science/observations",
        json={
            "project_id": project_id,
            "protocol_ref": "PROTO-LAB-001",
            "author": "Dr Sawadogo",
            "raw_data": {"rendement_pct": 12.5},
            "comment": commentaire,
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _simulation(client, project_id):
    """Crée une simulation via l'API (avec des entrées non vides)."""
    reponse = client.post(
        "/api/science/simulations",
        json={
            "name": "Docking COX-2",
            "target": "COX-2",
            "method": "docking",
            "parameters": {"ligand": "quercétine", "n": 100},
            "project_id": project_id,
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _publier(client, item_type, item_id, commentaire="À partager."):
    """Publie explicitement un item via l'API."""
    reponse = client.post(
        "/api/science/lab/publier",
        json={
            "item_type": item_type,
            "item_id": item_id,
            "commentaire": commentaire,
            "publie_par": "chercheur-test",
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


# ---------------------------------------------------------------------------
# Journal : création privée par défaut, édition versionnée
# ---------------------------------------------------------------------------

def test_creation_note_privee_data_level_experimental(client, make_project):
    """Une note créée via l'API est privée au projet, data_level experimental."""
    projet_id = make_project("Laboratoire A")
    note = _note(client, projet_id)
    assert note["project_id"] == projet_id
    assert note["data_level"] == "experimental"
    assert note["version_courante"] == 1
    assert note["publie"] is False
    assert note["titre"] == "Journal essai 1"

    liste = client.get("/api/science/lab/notes", params={"project_id": projet_id})
    assert liste.status_code == 200, liste.text
    assert len(liste.json()) == 1
    assert liste.json()[0]["id"] == note["id"]


def test_edition_note_cree_version_et_garde_historique(client, make_project):
    """Éditer une note → version 2 ; l'ancien contenu reste consultable.

    Une note n'est jamais modifiée en silence : l'édition crée un
    instantané (version 1) avant d'écraser le contenu courant.
    """
    projet_id = make_project("Laboratoire A")
    note = _note(client, projet_id, contenu="Mesures du jour.")

    modifiee = client.put(
        f"/api/science/lab/notes/{note['id']}",
        json={"contenu": "Mesures corrigées après relecture."},
    )
    assert modifiee.status_code == 200, modifiee.text
    corps = modifiee.json()
    assert corps["version_courante"] == 2
    assert corps["contenu"] == "Mesures corrigées après relecture."

    historique = client.get(f"/api/science/lab/notes/{note['id']}/versions")
    assert historique.status_code == 200, historique.text
    versions = historique.json()
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["contenu"] == "Mesures du jour."  # ancien contenu gardé
    assert versions[0]["titre"] == "Journal essai 1"

    # Deuxième édition → deux instantanés, version 3.
    client.put(
        f"/api/science/lab/notes/{note['id']}",
        json={"titre": "Journal essai 1 (revu)", "contenu": "Version finale."},
    )
    historique2 = client.get(f"/api/science/lab/notes/{note['id']}/versions").json()
    assert len(historique2) == 2
    assert [v["version"] for v in historique2] == [1, 2]
    assert historique2[1]["contenu"] == "Mesures corrigées après relecture."


def test_detail_note_inexistante_404(client):
    assert client.get("/api/science/lab/notes/999999").status_code == 404


def test_edition_sans_champ_422(client, make_project):
    projet_id = make_project("Laboratoire A")
    note = _note(client, projet_id)
    reponse = client.put(f"/api/science/lab/notes/{note['id']}", json={})
    assert reponse.status_code == 422, reponse.text


# ---------------------------------------------------------------------------
# Cloisonnement : 403 projet étranger, 401 sans clé
# ---------------------------------------------------------------------------

def test_notes_projet_etranger_403_en_serveur(
    client, make_project, server_bootstrap, db_session
):
    """En mode serveur, une clé ne lit pas les notes d'un autre projet → 403."""
    if server_bootstrap is None:
        pytest.skip("réservé au mode serveur (REQUIRE_API_KEY=<redacted>")
    projet_bootstrap = make_project("bootstrap")
    note = _note(client, projet_bootstrap)

    # Second projet créé directement en base (création interdite par API
    # en mode serveur : seul le projet du bootstrap est accessible).
    autre = modeles_noyau.Project(name="Projet étranger")
    db_session.add(autre)
    db_session.commit()

    reponse = client.get("/api/science/lab/notes", params={"project_id": autre.id})
    assert reponse.status_code == 403, reponse.text

    # Détail d'une note du projet bootstrap via un project_id étranger → 403.
    reponse = client.get(
        f"/api/science/lab/notes/{note['id']}", params={"project_id": autre.id}
    )
    assert reponse.status_code == 403, reponse.text

    # ... mais le projet de la clé reste lisible.
    miennes = client.get(
        "/api/science/lab/notes", params={"project_id": projet_bootstrap}
    )
    assert miennes.status_code == 200
    assert len(miennes.json()) == 1


def test_notes_isolees_par_projet_en_local(client, make_project, server_bootstrap):
    """En local, ?project_id= ne retourne que les notes du projet demandé."""
    if server_bootstrap is not None:
        pytest.skip("réservé au mode local (voir le test 403 en mode serveur)")
    projet_a = make_project("Laboratoire A")
    projet_b = make_project("Laboratoire B")
    _note(client, projet_a, titre="Note A")

    vue_b = client.get("/api/science/lab/notes", params={"project_id": projet_b})
    assert vue_b.status_code == 200
    assert vue_b.json() == []

    vue_a = client.get("/api/science/lab/notes", params={"project_id": projet_a})
    assert vue_a.status_code == 200
    assert len(vue_a.json()) == 1
    assert vue_a.json()[0]["titre"] == "Note A"


def test_ecriture_note_anonyme_401_en_serveur_201_en_local(
    anon_client, server_bootstrap, make_project
):
    """Sans clé API : 401 en mode serveur, 201 en mode local (accès ouvert)."""
    projet_id = make_project("Laboratoire A")
    reponse = anon_client.post(
        "/api/science/lab/notes",
        json={"project_id": projet_id, "titre": "Note anonyme"},
    )
    if server_bootstrap is None:
        assert reponse.status_code == 201, reponse.text
    else:
        assert reponse.status_code == 401, reponse.text


def test_tableau_de_bord_anonyme_401_en_serveur(anon_client, server_bootstrap):
    if server_bootstrap is None:
        pytest.skip("réservé au mode serveur (REQUIRE_API_KEY=<redacted>")
    reponse = anon_client.get("/api/science/lab/tableau-de-bord")
    assert reponse.status_code == 401, reponse.text


# ---------------------------------------------------------------------------
# Publication explicite + lecture seule des items publiés
# ---------------------------------------------------------------------------

def test_publication_observation_cree_trace(client, make_project):
    """Publier → 201 + PublicationRecord (qui, quand, projet source)."""
    projet_id = make_project("Laboratoire A")
    observation = _observation(client, projet_id)

    record = _publier(client, "observation", observation["id"], "Résultat à partager.")
    assert record["item_type"] == "observation"
    assert record["item_id"] == observation["id"]
    assert record["project_id"] == projet_id  # projet source tracé
    assert record["publie_par"] == "chercheur-test"
    assert record["publie_le"]  # horodaté
    assert record["commentaire"] == "Résultat à partager."


def test_publication_simulation_et_note(client, make_project):
    """Les trois types d'items (observation, simulation, note) sont publiables."""
    projet_id = make_project("Laboratoire A")
    simulation = _simulation(client, projet_id)
    note = _note(client, projet_id)

    rec_sim = _publier(client, "simulation", simulation["id"])
    assert rec_sim["item_type"] == "simulation"
    assert rec_sim["project_id"] == projet_id

    rec_note = _publier(client, "note", note["id"])
    assert rec_note["item_type"] == "note"

    # La note détaille désormais son statut publié.
    detail = client.get(f"/api/science/lab/notes/{note['id']}").json()
    assert detail["publie"] is True


def test_republication_409(client, make_project):
    """Publier deux fois le même item → 409 (un item n'est publié qu'une fois)."""
    projet_id = make_project("Laboratoire A")
    observation = _observation(client, projet_id)
    _publier(client, "observation", observation["id"])

    reponse = client.post(
        "/api/science/lab/publier",
        json={"item_type": "observation", "item_id": observation["id"]},
    )
    assert reponse.status_code == 409, reponse.text
    assert "déjà publié" in reponse.json()["detail"]


def test_publication_item_inexistant_404_et_type_invalide_422(client, make_project):
    make_project("Laboratoire A")
    reponse = client.post(
        "/api/science/lab/publier",
        json={"item_type": "observation", "item_id": 999999},
    )
    assert reponse.status_code == 404, reponse.text

    reponse = client.post(
        "/api/science/lab/publier",
        json={"item_type": "plantes_magiques", "item_id": 1},
    )
    assert reponse.status_code == 422, reponse.text


def test_item_publie_lecture_seule_409(client, make_project):
    """Un item publié ne peut plus être modifié : observation, simulation, note."""
    projet_id = make_project("Laboratoire A")
    observation = _observation(client, projet_id)
    simulation = _simulation(client, projet_id)
    note = _note(client, projet_id)

    _publier(client, "observation", observation["id"])
    _publier(client, "simulation", simulation["id"])
    _publier(client, "note", note["id"])

    reponse = client.patch(
        f"/api/science/lab/observations/{observation['id']}",
        json={"comment": "Tentative de réécriture."},
    )
    assert reponse.status_code == 409, reponse.text
    assert "lecture seule" in reponse.json()["detail"]

    reponse = client.patch(
        f"/api/science/lab/simulations/{simulation['id']}",
        json={"name": "Nom réécrit"},
    )
    assert reponse.status_code == 409, reponse.text

    reponse = client.put(
        f"/api/science/lab/notes/{note['id']}",
        json={"contenu": "Réécriture interdite."},
    )
    assert reponse.status_code == 409, reponse.text


def test_item_non_publie_reste_modifiable(client, make_project):
    """Un item non publié se modifie normalement (200)."""
    projet_id = make_project("Laboratoire A")
    observation = _observation(client, projet_id)
    simulation = _simulation(client, projet_id)

    reponse = client.patch(
        f"/api/science/lab/observations/{observation['id']}",
        json={"comment": "Commentaire mis à jour.", "status": "en_cours"},
    )
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["comment"] == "Commentaire mis à jour."
    assert reponse.json()["status"] == "en_cours"

    reponse = client.patch(
        f"/api/science/lab/simulations/{simulation['id']}",
        json={"name": "Docking COX-2 (revu)"},
    )
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["name"] == "Docking COX-2 (revu)"


def test_modification_item_inexistant_404(client, make_project):
    make_project("Laboratoire A")
    assert (
        client.patch(
            "/api/science/lab/observations/999999", json={"comment": "x"}
        ).status_code
        == 404
    )
    assert (
        client.patch(
            "/api/science/lab/simulations/999999", json={"name": "x"}
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# PAS DE FUITE : une observation privée non publiée n'apparaît jamais dans
# la recherche publique
# ---------------------------------------------------------------------------

def test_observation_privee_absente_de_la_recherche_publique(client, make_project):
    """GET /api/science/recherche (sans project_id) n'expose jamais une
    observation privée non publiée, même en cherchant son contenu exact."""
    projet_id = make_project("Laboratoire A")
    marqueur = "zxq_secret_labo_confidentiel_789"
    _observation(client, projet_id, commentaire=f"Mesure privée {marqueur}")

    reponse = client.get("/api/science/recherche", params={"q": marqueur})
    assert reponse.status_code == 200, reponse.text
    resultats = reponse.json()
    # Aucun résultat ne contient le contenu privé (le marqueur n'apparaît
    # que dans le champ "q" renvoyé en écho, jamais dans les données).
    assert resultats["total"] == 0
    for groupe in ("plantes", "composes", "publications", "simulations"):
        assert resultats[groupe] == []
        assert marqueur not in str(resultats[groupe])

    # Même après publication d'un AUTRE item, l'observation privée reste invisible.
    autre = _observation(client, projet_id, commentaire="Autre mesure")
    _publier(client, "observation", autre["id"])
    reponse = client.get("/api/science/recherche", params={"q": marqueur})
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["total"] == 0


# ---------------------------------------------------------------------------
# Tableau de bord
# ---------------------------------------------------------------------------

def test_tableau_de_bord_comptes_coherents(client, make_project):
    """Le dashboard compte juste et expose le data_level de chaque item."""
    projet_id = make_project("Laboratoire A")

    obs1 = _observation(client, projet_id, commentaire="Mesure 1")
    _observation(client, projet_id, commentaire="Mesure 2")
    sim = _simulation(client, projet_id)
    _note(client, projet_id, titre="Note 1")
    _note(client, projet_id, titre="Note 2")
    _publier(client, "observation", obs1["id"])

    # Une simulation avec des composés → compte « composes » et « plantes ».
    compose = client.post(
        "/api/science/compounds", json={"name": "Quercétine"}
    ).json()
    plante = client.post(
        "/api/science/plants", json={"scientific_name": "Moringa oleifera"}
    ).json()
    lien = client.post(
        f"/api/science/plants/{plante['id']}/compounds",
        json={"compound_id": compose["id"], "plant_id": plante["id"]},
    )
    assert lien.status_code in (200, 201), lien.text
    _simulation_avec = client.post(
        "/api/science/simulations",
        json={
            "name": "QSAR quercétine",
            "compound_ids": [compose["id"]],
            "target": "COX-2",
            "parameters": {"x": 1},
            "project_id": projet_id,
        },
    )
    assert _simulation_avec.status_code == 201, _simulation_avec.text

    tableau = client.get(
        "/api/science/lab/tableau-de-bord", params={"project_id": projet_id}
    )
    assert tableau.status_code == 200, tableau.text
    corps = tableau.json()

    assert corps["project_id"] == projet_id
    comptes = corps["comptes"]
    assert comptes["observations"] == 2
    assert comptes["simulations"] == 2  # docking COX-2 + QSAR quercétine
    assert comptes["notes"] == 2
    assert comptes["publications"] == 1
    assert comptes["composes"] == 1  # quercétine étudiée via la simulation QSAR
    assert comptes["plantes"] == 1  # moringa liée à la quercétine

    derniers = corps["derniers"]
    assert len(derniers["observations"]) == 2
    assert len(derniers["simulations"]) == 2
    assert len(derniers["notes"]) == 2
    for item in derniers["observations"]:
        assert item["data_level"] == "experimental"
    for item in derniers["simulations"]:
        assert item["data_level"] == "prediction"
    for item in derniers["notes"]:
        assert item["data_level"] == "experimental"
    # Ordre antichronologique.
    ids_obs = [item["id"] for item in derniers["observations"]]
    assert ids_obs == sorted(ids_obs, reverse=True)
    # Le dashboard ne mélange pas les projets : sim sans projet rattaché exclus.
    assert sim["id"] in [item["id"] for item in derniers["simulations"]]


def test_tableau_de_bord_vide_et_403_etranger(
    client, make_project, server_bootstrap, db_session
):
    """Dashboard vide pour un projet neuf ; 403 vers un projet étranger (serveur)."""
    projet_id = make_project("Laboratoire A")
    tableau = client.get(
        "/api/science/lab/tableau-de-bord", params={"project_id": projet_id}
    )
    assert tableau.status_code == 200, tableau.text
    comptes = tableau.json()["comptes"]
    assert comptes == {
        "observations": 0,
        "simulations": 0,
        "notes": 0,
        "publications": 0,
        "composes": 0,
        "plantes": 0,
        "workflows": None,  # module de l'équipe parallèle non livré
    }
    assert tableau.json()["derniers"] == {
        "observations": [],
        "simulations": [],
        "notes": [],
    }

    if server_bootstrap is None:
        pytest.skip("la partie 403 est réservée au mode serveur")
    reponse = client.get(
        "/api/science/lab/tableau-de-bord", params={"project_id": 999999}
    )
    assert reponse.status_code == 403, reponse.text
