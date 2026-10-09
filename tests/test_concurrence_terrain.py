"""Concurrence — chantier « PERSISTANCE + PERFORMANCE ».

Threads simultanés (8) sur la même base SQLite de test :

1. Langues : ``ajouter_langue`` idempotente → 1 seule ligne par code.
2. Noms vernaculaires : upserts concurrents → 1 seul nom (clé
   d'idempotence) ; lectures concurrentes sans erreur.
3. Vouchers : créations concurrentes du même code → 1 seul voucher
   (ou N si la clé diffère : on vérifie l'absence de corruption).
4. Échantillons : upserts concurrents du même code → 1 seul échantillon.
5. Versions : 8 threads enregistrent une version de la même étude →
   8 versions numérotées 1..8 sans doublon (unicité ``(study_id, numero)``).
"""

import threading

import pytest

from webintel.database.base import SessionLocal
from webintel.database.models import Project
from webintel.science import combinaisons as comb
from webintel.science import models as m
from webintel.science import terrain as t
from webintel.science.versions import enregistrer_version, list_versions


def _projet():
    db = SessionLocal()
    try:
        projet = db.query(Project).first()
        if projet is None:
            projet = Project(name="Projet Concurrence")
            db.add(projet)
            db.commit()
            db.refresh(projet)
        return projet.id
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


def _threads(n, cible):
    """Lance ``n`` threads sur ``cible(idx)`` ; relève les erreurs."""
    erreurs = []

    def _enrobage(idx):
        try:
            cible(idx)
        except Exception as exc:  # noqa: BLE001
            erreurs.append(exc)

    fils = [threading.Thread(target=_enrobage, args=(i,)) for i in range(n)]
    for f in fils:
        f.start()
    for f in fils:
        f.join(timeout=60)
    assert not erreurs, f"{len(erreurs)} erreur(s) : {erreurs[:3]}"
    return erreurs


def test_langues_concurrentes_idempotentes(db_session):
    """8 threads ajoutent la même langue → 1 seule ligne."""
    pid = _projet()

    def _ajout(idx):
        db = SessionLocal()
        try:
            t.enregistrer_langue_db(
                db, code=f"conc-lang-{idx % 2}", libelle="Langue Conc",
                region="Burkina Faso")
            db.commit()
        finally:
            db.close()

    _threads(8, _ajout)
    for code in ("conc-lang-0", "conc-lang-1"):
        nb = db_session.query(m.Langue).filter(
            m.Langue.code == code).count()
        assert nb == 1, f"Doublon langue {code} : {nb} lignes"


def test_noms_vernaculaires_concurrents_v2(db_session):
    """Upserts concurrents du même nom → 1 seul (idempotence)."""
    from webintel.science import service as svc
    pid = _projet()

    db = SessionLocal()
    try:
        plante, _ = svc.create_plant(db, scientific_name="Concurrens lingua")
        t.enregistrer_langue_db(
            db, code="conc-nom", libelle="Langue Conc Noms",
            region="Burkina Faso")
        db.commit()
        plante_id = plante.id
    finally:
        db.close()

    def _upsert(idx):
        db = SessionLocal()
        try:
            t.ajouter_nom_vernaculaire(
                db, plant_id=plante_id, project_id=pid,
                nom_original="Toodgo-conc", langue="conc-nom",
                localite="Ouagadougou")
            db.commit()
        finally:
            db.close()

    _threads(8, _upsert)
    nb = db_session.query(m.NomVernaculaire).filter(
        m.NomVernaculaire.nom_original == "Toodgo-conc").count()
    assert nb == 1, f"Doublon nom vernaculaire : {nb} lignes"


def test_echantillons_concurrents_idempotents(db_session):
    """8 threads créent le même échantillon → 1 seul (clé = code)."""
    from webintel.science import service as svc
    pid = _projet()
    db = SessionLocal()
    try:
        plante, _ = svc.create_plant(
            db, scientific_name="Concurrens exemplum")
        db.commit()
        plante_id = plante.id
    finally:
        db.close()

    def _creer(idx):
        db = SessionLocal()
        try:
            t.creer_echantillon(
                db, project_id=pid, plant_id=plante_id,
                code_echantillon="CONC-ECH-001",
                pays="Burkina Faso")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    _threads(8, _creer)
    nb = db_session.query(m.Echantillon).filter(
        m.Echantillon.code_echantillon == "CONC-ECH-001").count()
    assert nb == 1, f"Doublon échantillon : {nb} lignes"


def test_vouchers_concurrents_idempotents(db_session):
    """8 threads créent le même voucher → 1 seul (code unique)."""
    from webintel.science import service as svc
    pid = _projet()
    db = SessionLocal()
    try:
        plante, _ = svc.create_plant(
            db, scientific_name="Concurrens vouchera")
        db.commit()
        plante_id = plante.id
    finally:
        db.close()

    def _creer(idx):
        db = SessionLocal()
        try:
            t.creer_voucher(
                db, project_id=pid,
                code_voucher="CONC-VOU-001",
                institution="Herbier National du Burkina Faso")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    _threads(8, _creer)
    nb = db_session.query(m.Voucher).filter(
        m.Voucher.code_voucher == "CONC-VOU-001").count()
    assert nb == 1, f"Doublon voucher : {nb} lignes"


def test_versions_concurrentes_numerotees_sans_doublon(db_session):
    """8 threads versionnent la même étude → numéros 1..8 uniques."""
    pid = _projet()
    db = SessionLocal()
    try:
        c1 = m.Compound(name="Conc-Caféine",
                        smiles="CN1C=NC2=C1C(=O)N(C(=O)N2C)C")
        c2 = m.Compound(name="Conc-Théophylline",
                        smiles="CN1C=NC2=C1C(=O)NC(=O)N2C")
        db.add_all([c1, c2])
        db.flush()
        etude = comb.creer_etude(
            db, project_id=pid, nom="Étude concurrence",
            compound_ids=[c1.id, c2.id], cree_par="test-concurrence")
        db.commit()
        etude_id = etude.id
    finally:
        db.close()

    def _versionner(idx):
        db = SessionLocal()
        try:
            etude = db.get(m.CombinationStudy, etude_id)
            enregistrer_version(db, etude, cree_par=f"thread-{idx}",
                                resume=f"Version thread {idx}")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    _threads(8, _versionner)
    versions = list_versions(db_session, etude_id)
    numeros = sorted(v.numero for v in versions)
    assert numeros == [1, 2, 3, 4, 5, 6, 7, 8], (
        f"Numérotation corrompue : {numeros}")
