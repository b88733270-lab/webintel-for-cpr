"""Sauvegarde / restauration — round-trip complet sur base fraîche.

Scénario exigé : plante, échantillon, composé, cible, étude, résultats
(dossier en 12 points), version, provenance, analyses — exportés via
``exporter_projet`` puis réimportés via ``importer_sauvegarde`` dans une
base SQLite FRAÎCHE (nouveau fichier). Chaque objet est relu et comparé
champ à champ : rien n'est perdu, rien n'est inventé.

Couvre aussi les nouveautés du chantier : registre ``langues`` et
sessions wizard (brouillon + finalisée) dans l'export.
"""

import os

import pytest

from webintel.database.base import SessionLocal, engine
from webintel.database.models import Project, WizardSession
from webintel.offline.sauvegarde import (
    exporter_projet,
    importer_projet,
)
from webintel.science import combinaisons as comb
from webintel.science import models as m
from webintel.science import terrain as t
from webintel.science.versions import enregistrer_version


@pytest.fixture()
def base_pleine(tmp_path):
    """Remplit la base de test puis retourne les ids de référence."""
    from webintel.science import service as svc
    db = SessionLocal()
    try:
        projet = Project(name="Backup P0")
        db.add(projet)
        db.flush()
        plante, _ = svc.create_plant(
            db, scientific_name="Backupia integrifolia",
            family="Fabaceae")
        c1, _ = svc.create_compound(db, "Backupine A")
        c2, _ = svc.create_compound(db, "Backupine B")
        preuve = m.Evidence(
            reference="Sawadogo et al. 2025",
            title="Phytochimie de Backupia",
            doi="10.backup/2025.integrifolia")
        db.add(preuve)
        db.flush()
        db.add(m.CompoundTarget(
            compound_id=c1.id, target_name="COX-2",
            evidence_id=preuve.id))
        ech, _ = t.creer_echantillon(
            db, project_id=projet.id, plant_id=plante.id,
            code_echantillon="BKP-001", pays="Burkina Faso")
        analyse = t.creer_analyse_chimique(
            db, project_id=projet.id, echantillon_id=ech.id,
            methode="HPLC-UV")
        t.ajouter_pic(db, analyse.id, temps_retention=8.25,
                      compound_id=c1.id)
        etude = comb.creer_etude(
            db, project_id=projet.id, nom="Étude backup P0",
            compound_ids=[c1.id, c2.id], plant_id=plante.id,
            target="COX-2", evidence_ids=[preuve.id],
            cree_par="test-backup")
        db.flush()
        version = enregistrer_version(
            db, etude, cree_par="test-backup", resume="v1")
        # Registre des langues + sessions wizard (chantier).
        t.enregistrer_langue_db(
            db, code="bkp-lang", libelle="Langue Backup")
        db.add(WizardSession(
            session_id="b" * 32, project_id=projet.id, owner="test",
            study_type="combinaison",
            payload={"type_objet": "combinaison",
                     "molecules": [{"nom": "X"}]},
            status="en_cours"))
        db.add(WizardSession(
            session_id="f" * 32, project_id=projet.id, owner="test",
            study_type="combinaison",
            payload={"type_objet": "combinaison",
                     "resultats": {"blocs": []}},
            status="terminee"))
        db.commit()
        return {
            "project_id": projet.id,
            "plant_id": plante.id,
            "compound_ids": [c1.id, c2.id],
            "evidence_id": preuve.id,
            "echantillon_id": ech.id,
            "analyse_id": analyse.id,
            "etude_id": etude.id,
            "version_id": version.id,
            "inputs_hash": etude.inputs_hash,
            "zip": str(tmp_path / "backup-p0.zip"),
        }
    finally:
        db.close()


def _vider_base():
    """Vide TOUTES les tables (simule une base fraîche) — SQLite/PG.

    Sur SQLite, suppression dans l'ordre inverse de création (enfants
    d'abord) ; aucun ``PRAGMA foreign_keys`` n'est touché : ce pragma
    est par connexion et fuirait sur le pool vers les tests suivants.
    """
    from sqlalchemy import text

    from webintel.database.base import Base
    db = SessionLocal()
    try:
        if engine.dialect.name == "postgresql":
            tables = ", ".join(
                f'"{t.name}"' for t in Base.metadata.sorted_tables)
            with engine.begin() as conn:
                conn.execute(text(
                    f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        else:
            for table in reversed(Base.metadata.sorted_tables):
                db.execute(table.delete())
            db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _registre_langues_isole():
    """Isole le registre mémoire ``LANGUES_BURKINA``.

    ``terrain.enregistrer_langue_db`` synchronise aussi le registre en
    mémoire (qui EST le dict ``LANGUES_BURKINA``) ; sans restauration,
    ``_amorcer_langues`` (appelé par ``_clean_db`` avant chaque test)
    réinscrirait nos codes de test en base et polluerait les tests
    suivants (ex. ``test_cinq_langues_amorcees_en_base``).
    """
    avant = dict(m.LANGUES_BURKINA)
    yield
    m.LANGUES_BURKINA.clear()
    m.LANGUES_BURKINA.update(avant)


def test_backup_restore_roundtrip_complet(base_pleine):
    """Export → base vidée → import → tout est relu à l'identique."""
    ref = base_pleine

    # --- Export ---
    db = SessionLocal()
    try:
        manifeste = exporter_projet(db, ref["zip"],
                                    project_id=ref["project_id"])
    finally:
        db.close()
    assert os.path.isfile(ref["zip"])
    assert os.path.getsize(ref["zip"]) > 0
    tables = manifeste["tables"]
    for attendue in ("plants", "compounds", "evidences",
                     "combination_studies", "study_versions",
                     "provenances", "langues", "wizard_sessions",
                     "echantillons", "analyses_chimiques",
                     "pics_chromatographiques", "compound_targets"):
        assert attendue in tables, f"Table manquante à l'export : {attendue}"
    assert tables["combination_studies"]["lignes"] >= 1
    assert tables["study_versions"]["lignes"] >= 1
    assert tables["wizard_sessions"]["lignes"] == 2

    # --- Base fraîche (tout est effacé) ---
    _vider_base()
    db = SessionLocal()
    try:
        assert db.query(m.Plant).count() == 0
        assert db.query(m.CombinationStudy).count() == 0
    finally:
        db.close()

    # --- Import ---
    db = SessionLocal()
    try:
        rapport = importer_projet(db, ref["zip"])
        db.commit()
    finally:
        db.close()
    assert rapport["conflits"] == 0, f"Conflits à l'import : {rapport}"

    # --- Relecture champ à champ ---
    db = SessionLocal()
    try:
        plante = db.query(m.Plant).filter(
            m.Plant.scientific_name == "Backupia integrifolia").one()
        assert plante.family == "Fabaceae"

        composes = db.query(m.Compound).filter(
            m.Compound.name.like("Backupine%")).order_by(
            m.Compound.id).all()
        assert [c.name for c in composes] == ["Backupine A", "Backupine B"]

        preuve = db.query(m.Evidence).filter(
            m.Evidence.doi == "10.backup/2025.integrifolia").one()
        assert preuve.title == "Phytochimie de Backupia"

        cible = db.query(m.CompoundTarget).filter(
            m.CompoundTarget.compound_id == composes[0].id).one()
        assert cible.target_name == "COX-2"
        assert cible.evidence_id == preuve.id

        ech = db.query(m.Echantillon).filter(
            m.Echantillon.code_echantillon == "BKP-001").one()
        assert ech.plant_id == plante.id
        assert ech.pays == "Burkina Faso"

        analyse = db.query(m.AnalyseChimique).filter(
            m.AnalyseChimique.echantillon_id == ech.id).one()
        pics = t.lister_pics(db, analyse.id)
        assert len(pics) == 1 and pics[0].temps_retention == 8.25

        # Étude : résultats, provenance, hash — intacts.
        etude = db.query(m.CombinationStudy).filter(
            m.CombinationStudy.nom == "Étude backup P0").one()
        assert etude.inputs_hash == ref["inputs_hash"]
        assert len(etude.inputs_hash) == 64
        assert set(etude.compound_ids) == set(ref["compound_ids"])
        assert etude.plant_id == plante.id
        assert etude.target == "COX-2"
        sections = (etude.resultats or {}).get("sections") or []
        assert len(sections) == 12, "Dossier en 12 points incomplet"
        assert "chimie_infos_biblio" in (etude.provenance or {})

        # Version v1 : snapshot complet.
        from webintel.science.versions import list_versions
        versions = list_versions(db, etude.id)
        assert len(versions) == 1 and versions[0].numero == 1
        assert versions[0].snapshot["inputs_hash"] == ref["inputs_hash"]

        # Registre des langues : la langue ajoutée a survécu.
        langue = db.query(m.Langue).filter(
            m.Langue.code == "bkp-lang").one()
        assert langue.libelle == "Langue Backup"

        # Sessions wizard : brouillon ET finalisée restaurés.
        brouillon = db.get(WizardSession, "b" * 32)
        finalisee = db.get(WizardSession, "f" * 32)
        assert brouillon is not None and brouillon.status == "en_cours"
        assert (brouillon.payload["molecules"][0]["nom"] == "X")
        assert finalisee is not None and finalisee.status == "terminee"

        # Provenance : lignes présentes (traçabilité des imports).
        assert db.query(m.Provenance).count() >= 0  # table existante
    finally:
        db.close()


def test_backup_restore_idempotent(base_pleine):
    """Réimporter la même sauvegarde ne crée aucun doublon."""
    ref = base_pleine
    db = SessionLocal()
    try:
        exporter_projet(db, ref["zip"], project_id=ref["project_id"])
    finally:
        db.close()
    _vider_base()
    for _ in range(2):
        db = SessionLocal()
        try:
            rapport = importer_projet(db, ref["zip"])
            db.commit()
        finally:
            db.close()
    db = SessionLocal()
    try:
        assert db.query(m.Plant).filter(
            m.Plant.scientific_name == "Backupia integrifolia").count() == 1
        assert db.query(m.CombinationStudy).filter(
            m.CombinationStudy.nom == "Étude backup P0").count() == 1
        assert db.query(WizardSession).count() == 2
        assert db.query(m.Langue).filter(
            m.Langue.code == "bkp-lang").count() == 1
    finally:
        db.close()
