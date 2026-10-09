"""Tests offline-first : mise en file automatique + cloisonnement projet.

- toute création / modification / suppression d'une collecte ou d'une
  plante HORS CONNEXION est mise en file automatiquement (sans appel
  explicite) ;
- en ligne : rien n'est mis en file (sémantique « file = travail hors
  connexion ») ;
- pas de doublons : create+update → une seule entrée « create » ;
- la résolution d'un conflit (choix distant) et la restauration d'une
  sauvegarde ne remettent PAS en file ;
- la file est cloisonnée par projet : ``project_id`` renseigné,
  page /synchronisation filtrée, conflit d'un autre projet → 404,
  résolution inter-projet refusée ;
- migration idempotente : ``project_id`` ajouté aux bases existantes.
"""

from __future__ import annotations

import pytest

from webintel.database import models as core_models
from webintel.offline import reseau
from webintel.offline.auto_file import file_auto_suspendue
from webintel.offline.models import (
    CollecteTerrain,
    FileSynchronisation,
    ResolutionConflit,
)
from webintel.offline.synchronisation import (
    DepotDistantMemoire,
    _fusion_operations,
    executer_synchronisation,
    lister_file,
    mettre_en_file,
    resoudre_conflit,
)
from webintel.science.models import Plant


@pytest.fixture(autouse=True)
def _mode_reseau_propre():
    yield
    reseau.definir_mode_hors_ligne(None)


def _projet(db_session, nom="Défaut"):
    projet = db_session.query(core_models.Project).filter_by(
        name=nom).first()
    if projet is None:
        projet = core_models.Project(name=nom)
        db_session.add(projet)
        db_session.commit()
    return projet


# ---------------------------------------------------------------------------
# Mise en file automatique
# ---------------------------------------------------------------------------

def test_auto_file_creation_collecte_hors_ligne(db_session):
    reseau.definir_mode_hors_ligne(True)
    projet = _projet(db_session)
    db_session.add(CollecteTerrain(localite="Kaya", auteur="u",
                                   project_id=projet.id))
    db_session.commit()
    lignes = lister_file(db_session)
    assert len(lignes) == 1
    assert lignes[0].entity_type == "collecte"
    assert lignes[0].operation == "create"
    assert lignes[0].project_id == projet.id  # cloisonnement renseigné


def test_auto_file_update_puis_delete_coalescence(db_session):
    reseau.definir_mode_hors_ligne(True)
    projet = _projet(db_session)
    c = CollecteTerrain(localite="Kaya", auteur="u", project_id=projet.id)
    db_session.add(c)
    db_session.commit()
    # Modification puis suppression avant synchronisation : UNE entrée.
    c.localite = "Kaya (corrigé)"
    db_session.commit()
    db_session.delete(c)
    db_session.commit()
    lignes = lister_file(db_session)
    assert len(lignes) == 1
    assert lignes[0].operation == "delete"
    # Une suppression ne transporte que l'identifiant (les attributs
    # sont expirés après le DELETE — capturé avant dans before_flush).
    assert lignes[0].payload["id"] == c.id


def test_auto_file_plante_hors_ligne(db_session):
    reseau.definir_mode_hors_ligne(True)
    db_session.add(Plant(scientific_name="Azadirachta indica"))
    db_session.commit()
    lignes = lister_file(db_session)
    assert len(lignes) == 1
    assert lignes[0].entity_type == "plante"
    assert lignes[0].project_id is None  # les plantes sont globales


def test_pas_de_file_en_ligne(db_session):
    reseau.definir_mode_hors_ligne(False)
    projet = _projet(db_session)
    db_session.add(CollecteTerrain(localite="Ouaga", auteur="u",
                                   project_id=projet.id))
    db_session.add(Plant(scientific_name="Moringa oleifera"))
    db_session.commit()
    assert lister_file(db_session) == []


def test_fusion_operations():
    assert _fusion_operations("create", "update") == "create"
    assert _fusion_operations("create", "delete") == "delete"
    assert _fusion_operations("update", "delete") == "delete"
    assert _fusion_operations("delete", "create") == "create"
    assert _fusion_operations("update", "update") == "update"


def test_mettre_en_file_explicite_ne_duplique_pas(db_session):
    reseau.definir_mode_hors_ligne(True)
    projet = _projet(db_session)
    c = CollecteTerrain(localite="X", auteur="u", project_id=projet.id)
    db_session.add(c)
    db_session.commit()  # auto-enqueue → 1 entrée
    assert len(lister_file(db_session)) == 1
    # Appel explicite (route) : coalescé, pas de doublon.
    mettre_en_file(db_session, "collecte", c.id, "create",
                   {"id": c.id, "localite": "X"}, auteur="u")
    db_session.commit()
    assert len(lister_file(db_session)) == 1


def test_resolution_distant_ne_remet_pas_en_file(db_session):
    reseau.definir_mode_hors_ligne(True)
    projet = _projet(db_session)
    c = CollecteTerrain(localite="Kaya", auteur="u", project_id=projet.id)
    db_session.add(c)
    db_session.commit()
    depot = DepotDistantMemoire()
    depot.ecrire("collecte", c.id, {"id": c.id, "localite": "DISTANT"},
                 "2099-01-01T00:00:00")
    executer_synchronisation(db_session, depot)
    db_session.commit()
    conflit = lister_file(
        db_session, statut="conflit")[0]
    # Choix « distant » : la version distante est adoptée localement...
    resoudre_conflit(db_session, depot, conflit.id, "distant", auteur="u")
    db_session.commit()
    # ...sans créer une nouvelle entrée de file (pas de ping-pong).
    assert lister_file(db_session, statut="en_attente") == []
    assert db_session.get(CollecteTerrain, c.id).localite == "DISTANT"


def test_import_sauvegarde_ne_met_pas_en_file(db_session, tmp_path):
    from webintel.offline import sauvegarde

    reseau.definir_mode_hors_ligne(True)
    _projet(db_session)
    db_session.add(CollecteTerrain(localite="Kaya", auteur="u"))
    db_session.commit()
    assert len(lister_file(db_session)) == 1
    # On vide la file AVANT l'export : la sauvegarde ne contient alors
    # aucune entrée de file...
    db_session.query(FileSynchronisation).delete()
    db_session.commit()
    chemin = str(tmp_path / "s.zip")
    sauvegarde.exporter_projet(db_session, chemin)
    for _, modele in reversed(sauvegarde._modeles_exportes()):
        if modele.__tablename__ not in (
                "file_synchronisation", "resolutions_conflit"):
            db_session.query(modele).delete()
    db_session.commit()
    # ...et la restauration ne remet RIEN en file (ce n'est pas une
    # modification locale — la suspension auto_file fonctionne).
    sauvegarde.importer_projet(db_session, chemin)
    db_session.commit()
    assert lister_file(db_session) == []


def test_suspension_contextuelle(db_session):
    reseau.definir_mode_hors_ligne(True)
    with file_auto_suspendue():
        db_session.add(Plant(scientific_name="Suspendue"))
        db_session.commit()
    assert lister_file(db_session) == []


# ---------------------------------------------------------------------------
# Cloisonnement projet de la file (P0)
# ---------------------------------------------------------------------------

def test_file_cloisonnee_par_projet_ui(client, db_session):
    reseau.definir_mode_hors_ligne(True)
    defaut = _projet(db_session, "Défaut")
    autre = _projet(db_session, "Autre")
    c1 = CollecteTerrain(localite="ici", auteur="u", project_id=defaut.id)
    c2 = CollecteTerrain(localite="ailleurs", auteur="u",
                         project_id=autre.id)
    db_session.add_all([c1, c2])
    db_session.commit()
    # La session UI voit le projet « Défaut » (premier) : seule son
    # entrée apparaît.
    html = client.get("/synchronisation").text
    assert "ici" in html
    assert "ailleurs" not in html


def test_conflit_autre_projet_404_et_resolution_refusee(client, db_session):
    reseau.definir_mode_hors_ligne(True)
    _projet(db_session, "Défaut")
    autre = _projet(db_session, "Autre")
    c = CollecteTerrain(localite="secret", auteur="u", project_id=autre.id)
    db_session.add(c)
    db_session.commit()
    depot = DepotDistantMemoire()
    depot.ecrire("collecte", c.id, {"id": c.id, "localite": "X"},
                 "2099-01-01T00:00:00")
    executer_synchronisation(db_session, depot)
    db_session.commit()
    conflit = lister_file(db_session, statut="conflit")[0]
    assert conflit.project_id == autre.id
    # Page du conflit : 404 (autre projet).
    assert client.get(f"/synchronisation/conflits/{conflit.id}").status_code \
        == 404
    # Résolution directe : refusée.
    r = client.post(
        f"/synchronisation/conflits/{conflit.id}/resoudre",
        data={"choix": "local", "fusion_json": "", "commentaire": ""},
    )
    assert r.status_code == 200
    assert "autre projet" in r.text.lower()
    # Le conflit est toujours là, non résolu.
    db_session.expire_all()
    assert db_session.get(
        FileSynchronisation, conflit.id).statut == "conflit"


def test_journal_resolution_herite_projet(db_session):
    reseau.definir_mode_hors_ligne(True)
    projet = _projet(db_session)
    c = CollecteTerrain(localite="Kaya", auteur="u", project_id=projet.id)
    db_session.add(c)
    db_session.commit()
    depot = DepotDistantMemoire()
    depot.ecrire("collecte", c.id, {"id": c.id, "localite": "D"},
                 "2099-01-01T00:00:00")
    executer_synchronisation(db_session, depot)
    db_session.commit()
    conflit = lister_file(db_session, statut="conflit")[0]
    resoudre_conflit(db_session, depot, conflit.id, "local", auteur="u")
    db_session.commit()
    journal = db_session.query(ResolutionConflit).one()
    assert journal.project_id == projet.id


def test_migration_project_id_idempotente(db_session):
    """Une base existante SANS la colonne reçoit ``project_id`` sans
    perdre ses lignes ; rejouer est sans effet."""
    from sqlalchemy import JSON, Column, MetaData, Table, inspect, text
    from sqlalchemy import Integer, String

    from webintel.database.base import _ensure_offline_sync_columns, engine

    # Simule une base antérieure : table SANS project_id (toutes les
    # autres colonnes du modèle, fidèlement).
    db_session.execute(text("DROP TABLE file_synchronisation"))
    db_session.commit()
    vieux = Table(
        "file_synchronisation", MetaData(),
        *(c.copy() for c in FileSynchronisation.__table__.columns
          if c.name != "project_id"),
    )
    vieux.create(engine)
    db_session.execute(text(
        "INSERT INTO file_synchronisation "
        "(entity_type, entity_id, operation, payload, auteur, created_at,"
        " statut, tentative)"
        " VALUES ('plante', 1, 'create', '{}', 'u',"
        " '2026-01-01T00:00:00', 'en_attente', 0)"))
    db_session.commit()

    _ensure_offline_sync_columns()
    # Rejouer est sans effet (idempotent).
    _ensure_offline_sync_columns()

    colonnes = {c["name"]
                for c in inspect(engine).get_columns("file_synchronisation")}
    assert "project_id" in colonnes
    # Données conservées.
    assert db_session.execute(
        text("SELECT COUNT(*) FROM file_synchronisation")).scalar() == 1
    # La colonne est utilisable via l'ORM.
    ligne = FileSynchronisation(
        entity_type="plante", entity_id=2, operation="create", payload={},
        statut="en_attente", project_id=9)
    db_session.add(ligne)
    db_session.commit()
    assert ligne.id is not None
    db_session.refresh(ligne)
    assert ligne.project_id == 9

def test_synchroniser_ne_traite_que_son_projet(db_session):
    """Un projet ne synchronise jamais les entrées d'un autre projet ;
    les entrées globales (NULL) sont rejouées partout."""
    from webintel.offline.synchronisation import (
        DepotDistantMemoire, executer_synchronisation)

    p1 = _projet(db_session, "Projet un")
    p2 = _projet(db_session, "Projet deux")
    for pid in (p1.id, p2.id, None):
        mettre_en_file(
            db_session, "plante", 100 + (pid or 0), "create", {"n": pid},
            auteur="u", project_id=pid, flusher=True)
    db_session.commit()

    depot = DepotDistantMemoire()
    stats = executer_synchronisation(
        db_session, depot, auteur="u", project_id=p1.id)
    db_session.commit()

    assert stats["traitees"] == 2  # p1 + globale, jamais p2
    assert stats["envoyees"] == 2
    assert depot.lire("plante", 100 + p1.id) is not None
    assert depot.lire("plante", 100) is not None  # globale
    assert depot.lire("plante", 100 + p2.id) is None
    restantes = db_session.query(FileSynchronisation).filter(
        FileSynchronisation.statut == "en_attente").all()
    assert [e.project_id for e in restantes] == [p2.id]


def test_synchroniser_ui_cloisonnee_par_projet(client, db_session):
    """L'action UI « synchroniser » ne rejoue que la file du projet
    courant (POST /synchronisation/action)."""
    from webintel.offline.synchronisation import StatutSync
    from webintel.ui.routes import _default_project

    mien = _default_project(db_session)  # projet de la session UI locale
    autre = _projet(db_session, "Autre projet")
    for pid in (mien.id, autre.id):
        mettre_en_file(
            db_session, "plante", 200 + pid, "create", {"n": pid},
            auteur="u", project_id=pid, flusher=True)
    db_session.commit()

    r = client.post("/synchronisation/action", data={"action": "synchroniser"})
    assert r.status_code in (200, 303)

    db_session.expire_all()
    mienne = db_session.query(FileSynchronisation).filter(
        FileSynchronisation.project_id == mien.id).one()
    autre_ligne = db_session.query(FileSynchronisation).filter(
        FileSynchronisation.project_id == autre.id).one()
    # La mienne est synchronisée, celle de l'autre projet reste en attente.
    assert mienne.statut == StatutSync.ENVOYE.value
    assert autre_ligne.statut == StatutSync.EN_ATTENTE.value


# ---------------------------------------------------------------------------
# Mise en file automatique : entités chercheur étendues
# ---------------------------------------------------------------------------

def test_types_suivis_couvre_entites_chercheur():
    """Le registre couvre les entités chercheur (pas seulement
    collectes/plantes)."""
    from webintel.offline.auto_file import types_suivis

    suivis = set(types_suivis())
    assert {"collecte", "plante", "echantillon", "etude", "observation",
            "simulation", "note"} <= suivis


def test_auto_file_echantillon_hors_ligne(db_session):
    """Un échantillon créé hors connexion part en file avec le bon
    projet."""
    from webintel.science.models import Echantillon, Plant

    projet = _projet(db_session)
    reseau.definir_mode_hors_ligne(True)
    plante = Plant(scientific_name="Artemisia annua")
    db_session.add(plante)
    db_session.flush()
    ech = Echantillon(code_echantillon="BF-2026-001", plant_id=plante.id,
                      project_id=projet.id, localite="Ouaga")
    db_session.add(ech)
    db_session.commit()

    ligne = db_session.query(FileSynchronisation).filter_by(
        entity_type="echantillon", entity_id=ech.id).one()
    assert ligne.operation == "create"
    assert ligne.statut == "en_attente"
    assert ligne.project_id == projet.id
    assert ligne.payload["code_echantillon"] == "BF-2026-001"


def test_auto_file_etude_globale_sans_projet(db_session):
    """Une étude (donnée bibliographique globale, sans project_id) est
    mise en file comme entrée globale."""
    from webintel.science.models import Study

    reseau.definir_mode_hors_ligne(True)
    etude = Study(title="Artemisia annua : revue", year=2024)
    db_session.add(etude)
    db_session.commit()

    ligne = db_session.query(FileSynchronisation).filter_by(
        entity_type="etude", entity_id=etude.id).one()
    assert ligne.operation == "create"
    assert ligne.project_id is None


def test_auto_file_observation_simulation_note(db_session):
    """Observation, simulation et note de laboratoire : création hors
    connexion → file, avec projet."""
    from webintel.science.lab import LabNote
    from webintel.science.models import ResearchObservation, Simulation

    projet = _projet(db_session)
    reseau.definir_mode_hors_ligne(True)
    objets = [
        ("observation", ResearchObservation(
            project_id=projet.id, author="adama", comment="Pousse verte")),
        ("simulation", Simulation(
            project_id=projet.id, name="Similarité caféine",
            input_hash="abc123", method="tanimoto")),
        ("note", LabNote(
            project_id=projet.id, auteur="adama", titre="Terrain jour 3",
            contenu="Récolte feuilles.")),
    ]
    for _, obj in objets:
        db_session.add(obj)
    db_session.commit()

    for type_entite, obj in objets:
        ligne = db_session.query(FileSynchronisation).filter_by(
            entity_type=type_entite, entity_id=obj.id).one()
        assert ligne.operation == "create", type_entite
        assert ligne.project_id == projet.id, type_entite


def test_auto_file_update_echantillon_coalescence(db_session):
    """Create + update d'un échantillon avant commit : une seule entrée
    « create » au contenu frais."""
    from webintel.science.models import Echantillon, Plant

    projet = _projet(db_session)
    reseau.definir_mode_hors_ligne(True)
    plante = Plant(scientific_name="Moringa oleifera")
    db_session.add(plante)
    db_session.flush()
    ech = Echantillon(code_echantillon="BF-2026-002", plant_id=plante.id,
                      project_id=projet.id, localite="Ouaga")
    db_session.add(ech)
    db_session.flush()
    ech.localite = "Bobo-Dioulasso"
    db_session.commit()

    lignes = db_session.query(FileSynchronisation).filter_by(
        entity_type="echantillon", entity_id=ech.id).all()
    assert len(lignes) == 1
    assert lignes[0].operation == "create"
    assert lignes[0].payload["localite"] == "Bobo-Dioulasso"
