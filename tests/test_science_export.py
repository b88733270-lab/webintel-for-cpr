"""Tests de la Phase 9 — EXPORT + UI import/export/connecteurs.

- Exports CSV/JSON : chaque ligne/objet porte ``data_level`` + source
  principale (DOI/URL) ;
- Fiche plante : vrai PDF (``reportlab``) ou HTML imprimable ; 404 si
  absente ; format invalide → 400 ;
- Dossier ZIP : contenu complet + MANIFEST.json valide ; données privées
  exclues par défaut (observation privée absente), incluses si
  ``inclure_prive=true`` ; simulation exportée porte l'avertissement ;
- Pages UI ``/import``, ``/export``, ``/connecteurs`` : 200.

Le routeur ``/api/science/export`` est monté ici pour les tests (le
coordinateur le montera dans ``webintel.main`` en production).
"""

import io
import json
import zipfile

import pytest

import webintel.main
from webintel.api.routers import science_export
from webintel.science import lab as lab_module
from webintel.science.models import (
    Compound,
    Evidence,
    Plant,
    PlantCompound,
    ResearchObservation,
    Simulation,
)

# Monte le routeur sur l'app partagée (le coordinateur l'enregistrera dans
# main.py ; ici on le fait uniquement pour les tests d'endpoint, sans
# toucher à main.py).
#
# Certains modules de la suite (test_server_config, test_phase0_server_config)
# font `importlib.reload(webintel.main)`, ce qui REMPLACE l'objet `app` :
# le montage ne peut donc pas se faire une seule fois à l'import — une
# fixture autouse le refait avant chaque test si l'app a été remplacée.
def _monter_routeur_export():
    import webintel.main  # import local : suit un éventuel reload.

    app = webintel.main.app
    if not getattr(app, "_phase9_export_monte", False):
        app.include_router(science_export.router, tags=["science"])
        app._phase9_export_monte = True


@pytest.fixture(autouse=True)
def _routeur_export_monte():
    _monter_routeur_export()
    yield


@pytest.fixture()
def client(server_bootstrap):
    """Client de test (même motif que tests/test_science_ui.py)."""
    from fastapi.testclient import TestClient

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
def projet_id(make_project):
    return make_project("Projet Export")


@pytest.fixture()
def jeu_donnees(db_session, projet_id):
    """Plante + composé + lien (DOI) + preuve ; items privés du projet.

    - ``obs_privee`` : observation privée NON publiée (doit être exclue
      par défaut) ;
    - ``obs_publiee`` : observation explicitement publiée (incluse même
      par défaut) ;
    - ``note_privee`` : note de laboratoire privée ;
    - ``simulation`` : simulation privée liée au composé (rend la plante
      « plante du projet »).

    Note : ``_clean_db`` (conftest) ne vide pas les tables Phase 8
    (``lab_notes``, ``publication_records``) — purge explicite ici.
    """
    db_session.query(lab_module.PublicationRecord).delete()
    db_session.query(lab_module.LabNoteVersion).delete()
    db_session.query(lab_module.LabNote).delete()
    db_session.commit()
    plante = Plant(
        scientific_name="Artemisia annua",
        family="Asteraceae",
        genus="Artemisia",
        species="annua",
        description="Plante herbacée annuelle d'Asie.",
        used_parts=["feuilles"],
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
        publication="Étude phytochimique de test",
        doi="10.1234/test-doi",
        year=2020,
        confidence="rapporte",
    ))
    db_session.add(Evidence(
        source_type="publication",
        reference="J. Test 2020",
        title="Étude pharmacologique de test",
        authors=["A. Testeur"],
        year=2020,
        doi="10.1234/test-doi",
        url="https://example.org/etude",
    ))
    obs_privee = ResearchObservation(
        project_id=projet_id,
        protocol_ref="PROTO-001",
        author="Dr Test",
        status="brut",
        comment="Observation privée secrète — ne doit pas fuiter.",
        is_private=True,
    )
    obs_publiee = ResearchObservation(
        project_id=projet_id,
        protocol_ref="PROTO-002",
        author="Dr Test",
        status="valide",
        comment="Observation publiée explicitement.",
        is_private=True,
    )
    db_session.add_all([obs_privee, obs_publiee])
    db_session.flush()
    lab_module.publier_item(
        db_session, item_type="observation", item_id=obs_publiee.id,
        project_id=projet_id, publie_par="test",
    )
    note_privee = lab_module.creer_note(
        db_session, projet_id, titre="Note privée",
        contenu="Contenu confidentiel du laboratoire.", auteur="Dr Test",
    )
    simulation = Simulation(
        project_id=projet_id,
        name="Simulation privée",
        target="Cible test",
        method="Méthode test",
        engine_name="moteur_test",
        engine_version="1.0",
        compound_ids=[compose.id],
        parameters={"seuil": 0.5},
        result={"score": 0.82},
        input_hash="0" * 64,
        status="exploratoire",
    )
    db_session.add(simulation)
    db_session.commit()
    return {
        "plante_id": plante.id,
        "compose_id": compose.id,
        "obs_privee_id": obs_privee.id,
        "obs_publiee_id": obs_publiee.id,
        "note_id": note_privee.id,
        "simulation_id": simulation.id,
    }


# ---------------------------------------------------------------------------
# Exports CSV / JSON
# ---------------------------------------------------------------------------

def test_export_plantes_csv(client, jeu_donnees):
    resp = client.get("/api/science/export/plantes?format=csv")
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers["content-type"]
    assert ".csv" in resp.headers["content-disposition"]
    texte = resp.text
    assert "data_level" in texte.splitlines()[0]
    assert "source_principale" in texte.splitlines()[0]
    assert "Artemisia annua" in texte
    assert "publie" in texte
    assert "10.1234/test-doi" in texte


def test_export_composes_json(client, jeu_donnees):
    resp = client.get("/api/science/export/composes?format=json")
    assert resp.status_code == 200, resp.text
    objets = resp.json()
    assert len(objets) == 1
    objet = objets[0]
    assert objet["data_level"] == "publie"
    assert objet["source_principale"] == "10.1234/test-doi"
    assert objet["nom"] == "Artémisinine"


def test_export_evidences_json_source_principale(client, jeu_donnees):
    resp = client.get("/api/science/export/evidences?format=json")
    assert resp.status_code == 200, resp.text
    objets = resp.json()
    assert len(objets) == 1
    assert objets[0]["data_level"] == "publie"
    assert objets[0]["source_principale"] == "10.1234/test-doi"


def test_export_format_invalide_400(client, jeu_donnees):
    resp = client.get("/api/science/export/plantes?format=xml")
    assert resp.status_code == 400, resp.text
    assert "Format invalide" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Fiche plante : PDF réel / HTML
# ---------------------------------------------------------------------------

def test_fiche_pdf_reel(client, jeu_donnees):
    resp = client.get(
        f"/api/science/export/fiche/{jeu_donnees['plante_id']}?format=pdf"
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF"), (
        "Le contenu servi en format=pdf doit être un vrai PDF."
    )
    assert ".pdf" in resp.headers["content-disposition"]


def test_fiche_html_imprimable(client, jeu_donnees):
    resp = client.get(
        f"/api/science/export/fiche/{jeu_donnees['plante_id']}?format=html"
    )
    assert resp.status_code == 200, resp.text
    assert "text/html" in resp.headers["content-type"]
    assert "Artemisia annua" in resp.text
    assert "@media print" in resp.text


def test_fiche_plante_absente_404(client):
    resp = client.get("/api/science/export/fiche/999999?format=pdf")
    assert resp.status_code == 404, resp.text


def test_fiche_format_invalide_400(client, jeu_donnees):
    resp = client.get(
        f"/api/science/export/fiche/{jeu_donnees['plante_id']}?format=docx"
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Dossier de recherche (ZIP)
# ---------------------------------------------------------------------------

def _lire_zip(resp) -> zipfile.ZipFile:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert ".zip" in resp.headers["content-disposition"]
    return zipfile.ZipFile(io.BytesIO(resp.content))


def test_dossier_zip_contenu_complet(client, projet_id, jeu_donnees):
    resp = client.post(
        "/api/science/export/dossier", json={"projet_id": projet_id}
    )
    archive = _lire_zip(resp)
    noms = archive.namelist()
    assert "README.txt" in noms
    assert "MANIFEST.json" in noms
    assert "bibliographie.txt" in noms
    assert "bibliographie.bib" in noms
    assert "journal.txt" in noms
    fiches_json = [n for n in noms if n.startswith("fiches/fiche_") and n.endswith(".json")]
    fiches_html = [n for n in noms if n.startswith("fiches/fiche_") and n.endswith(".html")]
    assert len(fiches_json) == 1 and len(fiches_html) == 1
    fiche = json.loads(archive.read(fiches_json[0]))
    assert fiche["plante"]["nom_scientifique"] == "Artemisia annua"

    manifeste = json.loads(archive.read("MANIFEST.json"))
    for cle in ("contenu", "genere_le", "niveaux_donnees", "genere_par",
                "webintel_version", "inclure_prive", "inclure_simulations"):
        assert cle in manifeste, f"MANIFEST.json : clé manquante « {cle} »"
    assert manifeste["genere_par"] == "webintel"
    # La version suit le paquet (2.1.0 depuis le chantier v2.1) : on la
    # compare au numéro de version déclaré, pas à une valeur figée.
    from webintel import __version__ as _version_paquet

    assert manifeste["webintel_version"] == _version_paquet
    assert manifeste["inclure_prive"] is False
    assert manifeste["projet_id"] == projet_id
    assert isinstance(manifeste["contenu"], list) and manifeste["contenu"]
    assert "publie" in manifeste["niveaux_donnees"]


def test_dossier_prive_exclu_par_defaut(client, projet_id, jeu_donnees):
    resp = client.post(
        "/api/science/export/dossier", json={"projet_id": projet_id}
    )
    archive = _lire_zip(resp)
    noms = archive.namelist()
    # Aucun rapport de simulation : la simulation privée est exclue.
    assert not [n for n in noms if n.startswith("simulations/simulation_")]
    journal = archive.read("journal.txt").decode("utf-8")
    # L'observation privée non publiée ne fuite pas...
    assert "Observation privée secrète" not in journal
    # ...mais l'observation explicitement publiée est incluse.
    assert "Observation publiée explicitement" in journal
    # La note privée non publiée ne fuite pas.
    assert "Contenu confidentiel du laboratoire" not in journal


def test_dossier_prive_inclus_si_demande(client, projet_id, jeu_donnees):
    resp = client.post(
        "/api/science/export/dossier",
        json={"projet_id": projet_id, "inclure_prive": True},
    )
    archive = _lire_zip(resp)
    noms = archive.namelist()
    journal = archive.read("journal.txt").decode("utf-8")
    assert "Observation privée secrète" in journal
    assert "Contenu confidentiel du laboratoire" in journal
    rapports = [n for n in noms if n.startswith("simulations/simulation_")]
    assert len(rapports) == 1
    # Toute simulation exportée porte l'avertissement.
    assert "Simulation — à confirmer expérimentalement" in (
        archive.read(rapports[0]).decode("utf-8")
    )
    manifeste = json.loads(archive.read("MANIFEST.json"))
    assert manifeste["inclure_prive"] is True
    assert "prediction" in manifeste["niveaux_donnees"]
    assert "experimental" in manifeste["niveaux_donnees"]


def test_dossier_sans_simulations_si_desactive(client, projet_id, jeu_donnees):
    resp = client.post(
        "/api/science/export/dossier",
        json={"projet_id": projet_id, "inclure_prive": True,
              "inclure_simulations": False},
    )
    archive = _lire_zip(resp)
    assert not [n for n in archive.namelist()
                if n.startswith("simulations/simulation_")]


def test_dossier_projet_absent_404(client, server_bootstrap):
    # P1-1 : la 403 prime sur la 404 — en mode serveur, un projet
    # inexistant est refusé (403) avant même la vérification d'existence.
    resp = client.post(
        "/api/science/export/dossier", json={"projet_id": 999999}
    )
    if server_bootstrap is not None:
        assert resp.status_code == 403, resp.text
    else:
        assert resp.status_code == 404, resp.text


def test_dossier_projet_etranger_403_meme_sans_prive(client, db_session,
                                                    server_bootstrap,
                                                    projet_id, jeu_donnees):
    """P1-1 : tout export du dossier d'un autre projet est refusé (403),
    que `inclure_prive` soit vrai ou faux (en mode local, le bypass
    désactive le cloisonnement)."""
    from webintel.database import models

    autre = models.Project(name="Autre projet P1-1")
    db_session.add(autre)
    db_session.commit()
    for corps in (
        {"projet_id": autre.id},
        {"projet_id": autre.id, "inclure_prive": False},
        {"projet_id": autre.id, "inclure_prive": True},
    ):
        resp = client.post("/api/science/export/dossier", json=corps)
        if server_bootstrap is not None:
            assert resp.status_code == 403, (corps, resp.text)
        else:
            assert resp.status_code == 200, (corps, resp.text)


def test_dossier_prive_projet_etranger_403(client, db_session,
                                           server_bootstrap, projet_id,
                                           jeu_donnees):
    """inclure_prive=true sur un autre projet → 403 en mode serveur
    (en mode local, le bypass désactive le cloisonnement)."""
    from webintel.database import models

    autre = models.Project(name="Autre projet")
    db_session.add(autre)
    db_session.commit()
    resp = client.post(
        "/api/science/export/dossier",
        json={"projet_id": autre.id, "inclure_prive": True},
    )
    if server_bootstrap is not None:
        assert resp.status_code == 403, resp.text
    else:
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Pages UI
# ---------------------------------------------------------------------------

def test_ui_pages_200(client):
    for chemin in ("/import", "/export", "/connecteurs"):
        resp = client.get(chemin)
        assert resp.status_code == 200, f"{chemin} → {resp.status_code}"


def test_ui_connecteurs_integres(client):
    """Le backend des connecteurs étant monté (intégration finale), la page
    /connecteurs liste les connecteurs réels avec leur licence."""
    resp = client.get("/connecteurs")
    assert resp.status_code == 200
    assert "pubchem" in resp.text
    assert "en cours d'intégration" not in resp.text


def test_ui_import_type_invalide(client):
    resp = client.post(
        "/import",
        files={"fichier": ("test.csv", b"a,b\n1,2\n", "text/csv")},
        data={"type_donnees": "inconnu"},
    )
    assert resp.status_code == 200
    assert "Type de données invalide" in resp.text


def test_ui_export_lot_zip(client, jeu_donnees):
    resp = client.post(
        "/export/lot",
        data={"jeux": ["plantes", "composes"], "format": "csv"},
    )
    assert resp.status_code == 200, resp.text
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    noms = archive.namelist()
    assert "webintel_plantes.csv" in noms
    assert "webintel_composes.csv" in noms
    assert "data_level" in archive.read("webintel_plantes.csv").decode("utf-8")


def test_ui_export_lot_sans_jeu(client):
    resp = client.post("/export/lot", data={"format": "csv"})
    assert resp.status_code == 200
    assert "Cochez au moins un jeu" in resp.text


def test_ui_export_fiche_pdf(client, jeu_donnees):
    resp = client.post(
        "/export/fiche",
        data={"plant_id": str(jeu_donnees["plante_id"]), "format": "pdf"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_ui_export_fiche_absente(client):
    resp = client.post(
        "/export/fiche", data={"plant_id": "999999", "format": "pdf"}
    )
    assert resp.status_code == 200
    assert "introuvable" in resp.text


def test_ui_export_dossier_zip(client, projet_id, jeu_donnees):
    resp = client.post(
        "/export/dossier", data={"inclure_simulations": "oui"}
    )
    assert resp.status_code == 200, resp.text
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    manifeste = json.loads(archive.read("MANIFEST.json"))
    assert manifeste["inclure_prive"] is False
    journal = archive.read("journal.txt").decode("utf-8")
    assert "Observation privée secrète" not in journal


def test_ui_export_dossier_prive_explicite(client, projet_id, jeu_donnees):
    """Case « inclure les données privées » cochée = acte explicite :
    les données privées du projet sont incluses."""
    resp = client.post(
        "/export/dossier",
        data={"inclure_simulations": "oui", "inclure_prive": "oui"},
    )
    assert resp.status_code == 200, resp.text
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    journal = archive.read("journal.txt").decode("utf-8")
    assert "Observation privée secrète" in journal
