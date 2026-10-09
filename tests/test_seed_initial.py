"""Tests du seed de démarrage — ``webintel.science.seed_initial``.

Couverture (chantier A) :

- (a) premier chargement : au moins 10 plantes créées (13 semées) ;
- (b) idempotence stricte : un second chargement ne crée AUCUN
  doublon (compteurs à zéro, comptes en base inchangés) ;
- (c) liens plante→composé sourcés présents pour *Moringa oleifera*
  → Quercétine et *Artemisia annua* → Artémisinine ;
- (d) honnêteté : aucun mot interdit (« efficace », « sûr »,
  « traite ») dans les descriptions des plantes semées, et mention
  de provenance « jeu de démonstration intégré WebIntel » présente.

Aucun réseau : base de test SQLite via ``db_session`` (conftest).
"""

from __future__ import annotations

import pytest

from webintel.database.base import SessionLocal, init_db
from webintel.science import models as science
from webintel.science.seed_initial import (
    COMPOSES_SEED,
    LIENS_SEED,
    PLANTES_SEED,
    charger_donnees_initiales,
)

# Garantit que les tables scientifiques existent (idempotent).
init_db()

MOTS_INTERDITS = ("efficace", "sûr", "traite")


@pytest.fixture(autouse=True)
def _nettoyage_seed():
    """Vide les tables touchées par le seed avant chaque test."""
    session = SessionLocal()
    try:
        for modele in (
            science.PlantCompound,
            science.Provenance,
            science.Evidence,
            science.Compound,
            science.Plant,
        ):
            session.query(modele).delete()
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture()
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _comptes(session) -> dict[str, int]:
    return {
        "plants": session.query(science.Plant).count(),
        "compounds": session.query(science.Compound).count(),
        "evidences": session.query(science.Evidence).count(),
        "plant_compounds": session.query(science.PlantCompound).count(),
        "provenances": session.query(science.Provenance).count(),
    }


# --- (a) Premier chargement -------------------------------------------------


def test_premier_chargement_cree_au_moins_dix_plantes(db_session):
    compteurs = charger_donnees_initiales(db_session)
    db_session.commit()

    assert compteurs["plants"] >= 10
    assert compteurs["plants"] == len(PLANTES_SEED)
    assert compteurs["compounds"] == len(COMPOSES_SEED)
    assert compteurs["plant_compounds"] == len(LIENS_SEED)
    assert compteurs["evidences"] == 2

    comptes = _comptes(db_session)
    assert comptes["plants"] == len(PLANTES_SEED)
    assert comptes["compounds"] == len(COMPOSES_SEED)
    assert comptes["plant_compounds"] == len(LIENS_SEED)
    # Traçabilité : au moins une ligne de provenance par entité semée.
    assert comptes["provenances"] >= (
        comptes["plants"] + comptes["compounds"] + comptes["plant_compounds"]
    )


def test_plantes_ouest_africaines_presentes(db_session):
    charger_donnees_initiales(db_session)
    db_session.commit()

    noms = {
        plante.scientific_name
        for plante in db_session.query(science.Plant).all()
    }
    for attendu in (
        "Moringa oleifera",
        "Artemisia annua",
        "Hibiscus sabdariffa",
        "Khaya senegalensis",
        "Parkia biglobosa",
        "Tamarindus indica",
        "Adansonia digitata",
        "Balanites aegyptiaca",
        "Combretum micranthum",
        "Vernonia amygdalina",
        "Azadirachta indica",
        "Carica papaya",
        "Zingiber officinale",
    ):
        assert attendu in noms, attendu
    # Chaque plante semée a une famille (identité botanique de base).
    for plante in db_session.query(science.Plant).all():
        assert plante.family, plante.scientific_name


# --- (b) Idempotence ----------------------------------------------------------


def test_double_chargement_sans_doublon(db_session):
    premiers = charger_donnees_initiales(db_session)
    db_session.commit()
    comptes_avant = _comptes(db_session)
    assert premiers["plants"] == len(PLANTES_SEED)

    seconds = charger_donnees_initiales(db_session)
    db_session.commit()

    assert seconds == {
        "plants": 0,
        "compounds": 0,
        "evidences": 0,
        "plant_compounds": 0,
        "provenances": 0,
    }
    assert _comptes(db_session) == comptes_avant


def test_triple_chargement_sans_doublon(db_session):
    charger_donnees_initiales(db_session)
    charger_donnees_initiales(db_session)
    troisiemes = charger_donnees_initiales(db_session)
    db_session.commit()

    assert all(valeur == 0 for valeur in troisiemes.values())
    assert _comptes(db_session)["plants"] == len(PLANTES_SEED)


# --- (c) Liens plante→composé sourcés -----------------------------------------


def _lien(db_session, nom_plante: str, nom_compose: str):
    return (
        db_session.query(science.PlantCompound)
        .join(science.Plant, science.Plant.id == science.PlantCompound.plant_id)
        .join(
            science.Compound,
            science.Compound.id == science.PlantCompound.compound_id,
        )
        .filter(
            science.Plant.scientific_name == nom_plante,
            science.Compound.name == nom_compose,
        )
        .one_or_none()
    )


def test_liens_moringa_et_artemisia_presentes(db_session):
    charger_donnees_initiales(db_session)
    db_session.commit()

    lien_moringa = _lien(db_session, "Moringa oleifera", "Quercétine")
    assert lien_moringa is not None
    assert lien_moringa.confidence == "publie"
    assert lien_moringa.doi == "10.3390/molecules14062167"
    # La preuve documentaire est pointée (chaîne de traçabilité).
    assert lien_moringa.evidence_id is not None

    lien_artemisia = _lien(db_session, "Artemisia annua", "Artémisinine")
    assert lien_artemisia is not None
    assert lien_artemisia.confidence == "publie"
    assert lien_artemisia.doi == "10.1016/j.phytochem.2019.03.001"
    assert lien_artemisia.evidence_id is not None

    # Aucune concentration semée (non revérifiée : jamais inventée).
    for lien in db_session.query(science.PlantCompound).all():
        assert lien.concentration is None


def test_lien_zingiber_gingerol_structure_verifiee(db_session):
    charger_donnees_initiales(db_session)
    db_session.commit()

    lien = _lien(db_session, "Zingiber officinale", "Gingérol")
    assert lien is not None
    gingerol = (
        db_session.query(science.Compound)
        .filter_by(name="Gingérol")
        .one()
    )
    # Structure revérifiée par RDKit (formule / InChIKey recalculés).
    assert gingerol.formula == "C17H26O4"
    assert gingerol.inchikey == "NLDDIKRKFXEWBK-UHFFFAOYSA-N"
    assert gingerol.smiles


# --- (d) Honnêteté du vocabulaire --------------------------------------------


def test_aucun_mot_interdit_dans_les_descriptions(db_session):
    charger_donnees_initiales(db_session)
    db_session.commit()

    for plante in db_session.query(science.Plant).all():
        description = (plante.description or "").lower()
        for mot in MOTS_INTERDITS:
            assert mot not in description, (plante.scientific_name, mot)
        # Provenance textuelle obligatoire dans chaque description.
        assert "jeu de démonstration intégré webintel" in description


def test_usages_traditionnels_etiquetes(db_session):
    charger_donnees_initiales(db_session)
    db_session.commit()

    for plante in db_session.query(science.Plant).all():
        description = plante.description or ""
        if "traditionnel" in description.lower():
            assert "USAGE TRADITIONNEL RAPPORTÉ" in description, (
                plante.scientific_name
            )
