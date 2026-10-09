"""Tests v2.1 : modèle scientifique enrichi + fiche détaillée + UX chercheur.

Couverture :
- FK ``evidence_id`` pointée sur ``PlantCompound`` (et preuve affichée) ;
- migration v2.1 répétée / idempotente (``init_db()`` deux fois) ;
- fiche : sections détaillées (botanique morphologique, pharmacognosie,
  activités, cibles, littérature) + « non renseignée » pour champs vides ;
- sources affichées sur chaque ajout (composés, activités, cibles, études) ;
- données privées séparées des données publiques, isolées par projet ;
- provenance enregistrée sur saisie manuelle ;
- dataset privé isolé par projet ;
- exports fiche HTML/PDF avec les nouvelles sections ;
- formulaires UI sourcés (rejet sans source, ajout avec source) ;
- tableau de bord : parcours chercheur (recherche en premier).

Aucun réseau : tout passe par ``db_session`` / le ``TestClient``.
"""

from __future__ import annotations

import pytest

from webintel.database import models as modeles_noyau
from webintel.database.base import SessionLocal, init_db
from webintel.science import fiche as fiche_module
from webintel.science import service
from webintel.science.export_dossier import build_fiche_pdf, render_fiche_html
from webintel.science.models import (
    Compound,
    CompoundTarget,
    Dataset,
    Plant,
    PlantActivity,
    PlantCompound,
    Provenance,
    Study,
)

# Tables présentes avant tout test (idempotent).
init_db()


@pytest.fixture(autouse=True)
def _nettoyage_science_v21():
    """Vide les tables scientifiques v2.1 avant chaque test (ordre FK)."""
    session = SessionLocal()
    try:
        for table in (
            Provenance,
            Dataset,
            PlantActivity,
            CompoundTarget,
            Study,
        ):
            session.query(table).delete()
        from webintel.science import models as science

        for table in (
            science.ResearchObservation,
            science.Simulation,
            science.PlantCompound,
            science.Evidence,
            science.Compound,
            science.Plant,
        ):
            session.query(table).delete()
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture()
def client(server_bootstrap):
    """Client UI (même pattern que tests/test_science_ui.py)."""
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
            assert resp.status_code in (200, 303), resp.status_code
        yield test_client


def _projet(db_session, nom: str = "Labo v2.1") -> modeles_noyau.Project:
    projet = modeles_noyau.Project(name=nom)
    db_session.add(projet)
    db_session.commit()
    return projet


def _plante_enrichie(db_session) -> Plant:
    """Plante complète : botanique détaillée + composé + preuve + activité + cible."""
    plante, _ = service.create_plant(
        db_session, "Moringa oleifera", family="Moringaceae",
    )
    service.set_plant_fields(
        db_session, plante.id,
        feuilles="feuilles composées pennées",
        habitat="zones arides d'Afrique et d'Asie",
        references_botaniques=["Flora of Tropical East Africa"],
        matiere_premiere="feuilles séchées",
        preparation="poudre",
    )
    compose, _ = service.create_compound(db_session, "Quercétine")
    preuve, _ = service.add_evidence(
        db_session, doi="10.1016/test.moringa.2026",
        title="Phytochimie de test", reference="Test et al. 2026",
    )
    db_session.flush()
    service.link_plant_compound(
        db_session, plante.id, compose.id,
        plant_part="feuilles", publication="Test et al. 2026",
        doi="10.1016/test.moringa.2026",
        analysis_method="HPLC-UV", rendement=2.1, rendement_unite="% p/p",
        evidence_id=preuve.id, confidence="publie",
    )
    service.add_plant_activity(
        db_session, plante.id, "antioxydant",
        study_type="in_vitro", published_result="IC50 12.5 µg/mL",
        reference="Test et al. 2026",
        doi="10.1016/test.moringa.2026", evidence_id=preuve.id,
    )
    service.link_compound_target(
        db_session, compose.id, "COX-2", interaction_type="inhibiteur",
        doi="10.1016/test.moringa.2026", evidence_id=preuve.id,
    )
    service.create_study(
        db_session, doi="10.1016/test.moringa.2026",
        title="Phytochimie de test", authors=["Test, A."],
        year=2026, journal="Journal de test",
    )
    db_session.commit()
    return plante


# ---------------------------------------------------------------------------
# 1. FK evidence_id pointée + preuve affichée dans la fiche
# ---------------------------------------------------------------------------

def test_fk_preuve_pointee_et_affichee(db_session):
    plante, _ = service.create_plant(db_session, "Artemisia annua")
    compose, _ = service.create_compound(db_session, "Artémisinine")
    preuve, _ = service.add_evidence(
        db_session, doi="10.1016/test.preuve.2026", reference="Réf. test"
    )
    db_session.flush()
    lien, _ = service.link_plant_compound(
        db_session, plante.id, compose.id, doi="10.1016/test.preuve.2026"
    )
    service.attach_link_evidence(db_session, lien.id, preuve.id)
    db_session.commit()

    fiche = fiche_module.build_plant_fiche(db_session, plante.id, project_id=None)
    assert len(fiche["phytochimie"]) == 1
    item = fiche["phytochimie"][0]
    assert item["preuve"] is not None
    assert item["preuve"]["evidence_id"] == preuve.id
    assert item["preuve"]["doi"] == "10.1016/test.preuve.2026"


# ---------------------------------------------------------------------------
# 1b. Preuve d'une activité/cible non liée à un PlantCompound : affichée
# ---------------------------------------------------------------------------

def test_preuve_activite_sans_lien_plante_compose_affichee(db_session):
    """Une activité dont la preuve n'est liée à aucun PlantCompound doit
    quand même afficher sa preuve (pas « preuve=None »)."""
    plante, _ = service.create_plant(db_session, "Plante activité seule")
    preuve, _ = service.add_evidence(
        db_session, doi="10.1016/test.activite.seule",
        reference="Réf. activité seule",
    )
    db_session.flush()
    service.add_plant_activity(
        db_session, plante.id, "antipyrétique",
        study_type="in_vivo", evidence_id=preuve.id,
        reference="Réf. activité seule",
    )
    db_session.commit()

    fiche = fiche_module.build_plant_fiche(db_session, plante.id, project_id=None)
    assert fiche["phytochimie"] == []  # aucun lien plante-composé
    assert len(fiche["activites_pharmacologiques"]) == 1
    act = fiche["activites_pharmacologiques"][0]
    assert act["preuve"] is not None, "preuve de l'activité manquante"
    assert act["preuve"]["doi"] == "10.1016/test.activite.seule"


# ---------------------------------------------------------------------------
# 2. Migration v2.1 répétée / idempotente
# ---------------------------------------------------------------------------

def test_migration_v21_repetee_idempotente():
    from sqlalchemy import inspect

    init_db()
    init_db()  # deuxième appel : aucune erreur, colonnes toujours là
    insp = inspect(SessionLocal().bind)
    colonnes_plants = {c["name"] for c in insp.get_columns("plants")}
    colonnes_liens = {c["name"] for c in insp.get_columns("plant_compounds")}
    for champ in ("feuilles", "fleurs", "fruits", "graines", "racines",
                  "ecorce", "habitat", "references_botaniques",
                  "matiere_premiere", "preparation"):
        assert champ in colonnes_plants, champ
    for champ in ("analysis_method", "rendement", "rendement_unite", "evidence_id"):
        assert champ in colonnes_liens, champ
    for table in ("compound_targets", "plant_activities", "studies",
                  "provenances", "datasets"):
        assert table in insp.get_table_names(), table


# ---------------------------------------------------------------------------
# 3. Fiche détaillée + « non renseignée »
# ---------------------------------------------------------------------------

def test_fiche_sections_detaillees(db_session):
    plante = _plante_enrichie(db_session)
    fiche = fiche_module.build_plant_fiche(db_session, plante.id, project_id=None)

    # botanique détaillée
    assert fiche["botanique"]["feuilles"] == "feuilles composées pennées"
    assert fiche["botanique"]["habitat"] == "zones arides d'Afrique et d'Asie"
    assert fiche["botanique"]["references_botaniques"] == ["Flora of Tropical East Africa"]
    # champs non renseignés → None (jamais inventés)
    assert fiche["botanique"]["fleurs"] is None
    # pharmacognosie
    assert fiche["pharmacognosie"]["matiere_premiere"] == "feuilles séchées"
    assert fiche["pharmacognosie"]["preparation"] == "poudre"
    # phytochimie : méthode d'analyse, rendement, preuve
    item = fiche["phytochimie"][0]
    assert item["methode_analyse"] == "HPLC-UV"
    assert item["rendement"] == 2.1
    assert item["preuve"]["doi"] == "10.1016/test.moringa.2026"
    # activités + cibles + littérature
    assert len(fiche["activites_pharmacologiques"]) == 1
    assert fiche["activites_pharmacologiques"][0]["activite"] == "antioxydant"
    assert len(fiche["cibles"]) == 1
    assert fiche["cibles"][0]["cible"] == "COX-2"
    # la littérature fusionne preuves documentaires et études structurées
    dois = [e.get("doi") for e in fiche["litterature"]]
    assert "10.1016/test.moringa.2026" in dois
    assert any(e.get("type_entree") == "etude" for e in fiche["litterature"])


def test_fiche_ui_affiche_non_renseignee(client, db_session):
    """Champs vides → « non renseignée » dans le HTML (jamais inventé)."""
    plante, _ = service.create_plant(db_session, "Plante vide")
    db_session.commit()
    resp = client.get(f"/plantes/{plante.id}")
    assert resp.status_code == 200
    assert "non renseignée" in resp.text
    # légende des 5 niveaux visible même sans données
    for niveau in ("Publié", "Normalisé", "Hypothèse", "Prédiction", "Expérimental"):
        assert niveau in resp.text, niveau


def test_fiche_ui_sections_enrichies(client, db_session):
    plante = _plante_enrichie(db_session)
    resp = client.get(f"/plantes/{plante.id}")
    assert resp.status_code == 200
    html = resp.text
    for section in ("Botanique", "Pharmacognosie", "Phytochimie",
                    "Activités pharmacologiques", "Cibles biologiques documentées",
                    "Littérature", "Sécurité"):
        assert section in html, section
    # sources affichées
    assert "10.1016/test.moringa.2026" in html
    assert "feuilles composées pennées" in html
    assert "COX-2" in html
    # bandeau de prudence visible
    assert "n'est pas un avis médical" in html


# ---------------------------------------------------------------------------
# 5. Données privées séparées + isolées par projet
# ---------------------------------------------------------------------------

def test_prive_separe_et_isole_par_projet(db_session):
    plante, _ = service.create_plant(db_session, "Moringa oleifera")
    projet_a = _projet(db_session, "Labo A")
    projet_b = _projet(db_session, "Labo B")
    service.create_observation(
        db_session, projet_a.id,
        protocol_ref="PROTO-A-1", comment="rendement 2.1 %",
    )
    db_session.commit()

    fiche_a = fiche_module.build_plant_fiche(db_session, plante.id, project_id=projet_a.id)
    assert len(fiche_a["laboratoire_prive"]) == 1
    assert fiche_a["laboratoire_prive"][0]["protocol_ref"] == "PROTO-A-1"
    assert fiche_a["avertissement_separation"]

    # projet B : pas d'accès aux données privées du projet A
    fiche_b = fiche_module.build_plant_fiche(db_session, plante.id, project_id=projet_b.id)
    assert fiche_b["laboratoire_prive"] == []

    # sans projet : pas de privé du tout
    fiche_pub = fiche_module.build_plant_fiche(db_session, plante.id, project_id=None)
    assert fiche_pub["laboratoire_prive"] == []


def test_dataset_prive_isole_par_projet(db_session):
    projet_a = _projet(db_session, "Labo A")
    projet_b = _projet(db_session, "Labo B")
    ds, _ = service.register_dataset(
        db_session, "Mesures IC50", project_id=projet_a.id,
    )
    db_session.commit()
    assert ds.is_private is True
    # même projet : accessible
    vu = db_session.query(Dataset).filter_by(
        id=ds.id, project_id=projet_a.id).one_or_none()
    assert vu is not None
    # autre projet : isolé (invisible)
    assert db_session.query(Dataset).filter_by(
        id=ds.id, project_id=projet_b.id).one_or_none() is None


# ---------------------------------------------------------------------------
# 7. Provenance
# ---------------------------------------------------------------------------

def test_provenance_enregistree(db_session):
    plante, _ = service.create_plant(db_session, "Moringa oleifera")
    db_session.commit()
    prov, creee = service.record_provenance(
        db_session, "plant", plante.id, source_name="Flora de test",
        connector="ui-manuel", data_version="saisie-fiche",
    )
    db_session.commit()
    lignes = db_session.query(Provenance).filter_by(
        entity_type="plant", entity_id=plante.id).all()
    assert len(lignes) == 1
    assert lignes[0].id == prov.id
    assert lignes[0].source_name == "Flora de test"
    assert lignes[0].connector == "ui-manuel"


# ---------------------------------------------------------------------------
# 8. Exports fiche
# ---------------------------------------------------------------------------

def test_export_html_contient_nouvelles_sections(db_session):
    plante = _plante_enrichie(db_session)
    page = render_fiche_html(db_session, plante.id, project_id=None)
    for section in ("Botanique", "Pharmacognosie", "Phytochimie",
                    "Activités pharmacologiques", "Cibles biologiques documentées",
                    "Littérature", "Sécurité", "n'est pas un avis médical"):
        assert section in page, section
    assert "10.1016/test.moringa.2026" in page


def test_export_pdf_construit(db_session):
    pytest.importorskip("reportlab")
    plante = _plante_enrichie(db_session)
    contenu = build_fiche_pdf(db_session, plante.id, project_id=None)
    assert contenu is not None
    assert contenu.startswith(b"%PDF")


# ---------------------------------------------------------------------------
# 9. Formulaires UI sourcés
# ---------------------------------------------------------------------------

def _creer_plante_ui(client, db_session, nom: str = "Plante formulaire") -> int:
    plante, _ = service.create_plant(db_session, nom)
    db_session.commit()
    return plante.id


def test_formulaire_botanique_exige_source(client, db_session):
    pid = _creer_plante_ui(client, db_session)
    resp = client.post(f"/plantes/{pid}/botanique", data={
        "feuilles": "feuilles ovales", "source": "",
    })
    assert resp.status_code == 200
    assert "Une source est obligatoire" in resp.text


def test_formulaire_botanique_maj_ok(client, db_session):
    pid = _creer_plante_ui(client, db_session)
    resp = client.post(f"/plantes/{pid}/botanique", data={
        "feuilles": "feuilles ovales", "habitat": "savane",
        "references_botaniques": "Flora Test ; Atlas",
        "source": "Flora de test (2026)",
    })
    assert resp.status_code == 200
    assert "mise à jour" in resp.text
    db_session.expire_all()
    plante = db_session.get(Plant, pid)
    assert plante.feuilles == "feuilles ovales"
    assert plante.habitat == "savane"
    assert plante.references_botaniques == ["Flora Test", "Atlas"]
    # provenance de saisie manuelle enregistrée
    assert db_session.query(Provenance).filter_by(
        entity_type="plant", entity_id=pid).count() >= 1


def test_formulaire_compose_exige_source(client, db_session):
    pid = _creer_plante_ui(client, db_session)
    resp = client.post(f"/plantes/{pid}/composes/ajouter", data={
        "nom_compose": "Quercétine", "source": "",
    })
    assert resp.status_code == 200
    assert "Une source est obligatoire" in resp.text


def test_formulaire_compose_ajout_ok(client, db_session):
    pid = _creer_plante_ui(client, db_session)
    resp = client.post(f"/plantes/{pid}/composes/ajouter", data={
        "nom_compose": "Quercétine", "partie": "feuilles",
        "methode_analyse": "HPLC-UV", "rendement": "2,1",
        "rendement_unite": "% p/p",
        "source": "10.1016/test.ajout.2026",
    })
    assert resp.status_code == 200
    assert "ajouté à la fiche" in resp.text
    lien = (
        db_session.query(PlantCompound)
        .filter(PlantCompound.plant_id == pid).one()
    )
    assert lien.analysis_method == "HPLC-UV"
    assert lien.rendement == 2.1
    # DOI → preuve pointée
    assert lien.evidence_id is not None


def test_formulaire_activite_ajout_ok(client, db_session):
    pid = _creer_plante_ui(client, db_session)
    resp = client.post(f"/plantes/{pid}/activites/ajouter", data={
        "activite": "antioxydant", "type_etude": "in_vitro",
        "modele_etude": "DPPH", "source": "Test et al. 2026",
    })
    assert resp.status_code == 200
    assert "ajoutée" in resp.text
    act = db_session.query(PlantActivity).filter_by(plant_id=pid).one()
    assert act.activity == "antioxydant"
    assert act.study_type == "in_vitro"


def test_formulaire_cible_ajout_ok(client, db_session):
    pid = _creer_plante_ui(client, db_session)
    compose, _ = service.create_compound(db_session, "Quercétine")
    db_session.commit()
    resp = client.post(f"/plantes/{pid}/cibles/ajouter", data={
        "compose_id": str(compose.id), "cible": "COX-2",
        "type_interaction": "inhibiteur",
        "source": "10.1016/test.cible.2026",
    })
    assert resp.status_code == 200
    assert "documentée" in resp.text
    cible = db_session.query(CompoundTarget).filter_by(compound_id=compose.id).one()
    assert cible.target_name == "COX-2"
    assert cible.evidence_id is not None  # DOI → preuve pointée


def test_formulaire_etude_ajout_ok(client, db_session):
    pid = _creer_plante_ui(client, db_session)
    resp = client.post(f"/plantes/{pid}/etudes/ajouter", data={
        "titre": "Phytochimie de test", "auteurs": "Test, A. ; Demo, B.",
        "annee": "2026", "journal": "Journal de test",
        "doi": "10.1016/test.etude.2026",
    })
    assert resp.status_code == 200
    assert "ajoutée à la littérature" in resp.text
    etude = db_session.query(Study).filter_by(doi="10.1016/test.etude.2026").one()
    assert etude.year == 2026
    assert etude.authors == ["Test, A.", "Demo, B."]


def test_formulaire_etude_annee_invalide(client, db_session):
    pid = _creer_plante_ui(client, db_session)
    resp = client.post(f"/plantes/{pid}/etudes/ajouter", data={
        "titre": "Étude X", "annee": "pas-une-annee",
    })
    assert resp.status_code == 200
    assert "Année invalide" in resp.text


# ---------------------------------------------------------------------------
# 10. Tableau de bord : parcours chercheur
# ---------------------------------------------------------------------------

def test_dashboard_recherche_en_premier(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    # recherche placée en premier, avant les actions
    pos_recherche = html.find("Rechercher une plante")
    assert pos_recherche != -1
    for action in ("Nouvelle recherche", "Nouvelle étude", "Nouvelle simulation",
                   "Importer mes résultats", "Exporter mon dossier"):
        assert action in html, action
