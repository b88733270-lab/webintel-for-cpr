"""Tests du module scientifique — modèles + service (Phase 2).

Couverture :
- création plante / composé / association (champs JSON conservés) ;
- unicité : nom scientifique, nom de composé, InChIKey (ValueError si
  réutilisée par un *autre* composé) ;
- idempotence : double lien plante-composé, double import d'un DOI ;
- observations de laboratoire (privées par défaut, rattachées au projet) ;
- simulations (input_hash déterministe = SHA-256 du JSON canonique,
  refus des entrées vides, statut « exploratoire » par défaut) ;
- recherche plein-texte (plantes, composés) ;
- séparation stricte des niveaux de données (``DataLevel`` : 5 niveaux).

Aucun réseau : tout passe par ``db_session`` (base de test). Le service ne
committe jamais (convention du module) : chaque écriture est flushée, les
lectures se font dans la même session.
"""

from __future__ import annotations

import pytest

from webintel.core.utils import sha256_hex
from webintel.database import models as modeles_noyau
from webintel.database.base import SessionLocal, init_db
from webintel.database.helpers import canonical_json
from webintel.science import models as science
from webintel.science import service

# Enregistre les tables scientifiques puis (re)crée le schéma : idempotent.
# Garantit que les tables existent même si le routeur science n'avait pas
# encore été importé par `webintel.main` au moment de l'import de conftest.
init_db()

INCHIKEY_QUERCETINE = "REFJWTPEDVJJIY-UHFFFAOYSA-N"
INCHIKEY_RUTINE = "IUBISOPJRGLHNS-UHFFFAOYSA-N"


@pytest.fixture(autouse=True)
def _nettoyage_science():
    """Vide les tables scientifiques avant chaque test (isolation complète).

    ``conftest._clean_db`` ne connaît que les tables historiques ; ce fixture
    complète pour les tables du module science (ordre respectant les FK).
    """
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


def _projet(db_session, nom: str = "Laboratoire test") -> modeles_noyau.Project:
    """Crée un projet directement en base (pour les tests du service)."""
    projet = modeles_noyau.Project(name=nom)
    db_session.add(projet)
    db_session.commit()
    return projet


# ---------------------------------------------------------------------------
# Création : plantes, composés, associations
# ---------------------------------------------------------------------------

def test_creation_plante_conserve_les_champs_json(db_session):
    """Les champs JSON d'une plante (synonymes, noms vernaculaires, taxonomie,
    parties utilisées, identifiants externes) sont conservés tels quels."""
    plante, cree = service.create_plant(
        db_session,
        "Moringa oleifera",
        synonyms=["Moringa pterygosperma"],
        vernacular_names={"fr": "Moringa", "wo": "Neverday"},
        family="Moringaceae",
        genus="Moringa",
        species="oleifera",
        taxonomy={"ordre": "Brassicales"},
        used_parts=["feuilles", "graines"],
        external_ids={"gbif": "123"},
    )
    assert cree is True
    assert plante.id is not None
    assert plante.scientific_name == "Moringa oleifera"
    assert plante.family == "Moringaceae"
    assert plante.synonyms == ["Moringa pterygosperma"]
    assert plante.vernacular_names == {"fr": "Moringa", "wo": "Neverday"}
    assert plante.taxonomy == {"ordre": "Brassicales"}
    assert plante.used_parts == ["feuilles", "graines"]
    assert plante.external_ids == {"gbif": "123"}

    # Relecture depuis la base : les JSON survivent au round-trip.
    relue = db_session.get(science.Plant, plante.id)
    assert relue.synonyms == ["Moringa pterygosperma"]
    assert relue.vernacular_names["wo"] == "Neverday"


def test_creation_compose_conserve_les_champs_chimiques(db_session):
    """Les champs chimiques d'un composé (formule, InChIKey, SMILES, classe...)
    sont conservés tels quels."""
    compose, cree = service.create_compound(
        db_session,
        "Quercétine",
        formula="C15H10O7",
        molecular_weight=302.24,
        smiles="C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O)O",
        inchikey=INCHIKEY_QUERCETINE,
        chemical_class="flavonoïde",
        synonyms=["Quercetin"],
        external_ids={"pubchem": "5280343"},
    )
    assert cree is True
    assert compose.id is not None
    assert compose.name == "Quercétine"
    assert compose.formula == "C15H10O7"
    assert compose.molecular_weight == 302.24
    assert compose.inchikey == INCHIKEY_QUERCETINE
    assert compose.chemical_class == "flavonoïde"
    assert compose.synonyms == ["Quercetin"]
    assert compose.external_ids == {"pubchem": "5280343"}


def test_creation_association_plante_compose(db_session):
    """Le lien plante-composé enregistre la partie de plante, la publication
    et la confiance déclarée."""
    plante, _ = service.create_plant(db_session, "Moringa oleifera")
    compose, _ = service.create_compound(db_session, "Quercétine")
    lien, cree = service.link_plant_compound(
        db_session,
        plante.id,
        compose.id,
        plant_part="feuilles",
        publication="Journal of Ethnopharmacology, 2020",
        confidence="publie",
        concentration=0.42,
        concentration_unit="mg/g",
    )
    assert cree is True
    assert lien.plant_id == plante.id
    assert lien.compound_id == compose.id
    assert lien.plant_part == "feuilles"
    assert lien.publication == "Journal of Ethnopharmacology, 2020"
    assert lien.confidence == "publie"
    assert lien.concentration == 0.42


# ---------------------------------------------------------------------------
# Unicité
# ---------------------------------------------------------------------------

def test_deux_plantes_meme_nom_scientifique_ne_creent_qu_une_ligne(db_session):
    """Le nom scientifique est la clé d'idempotence des plantes."""
    p1, cree1 = service.create_plant(db_session, "Moringa oleifera", family="Moringaceae")
    p2, cree2 = service.create_plant(db_session, "Moringa oleifera", family="Autre")
    assert cree1 is True
    assert cree2 is False
    assert p1.id == p2.id
    assert db_session.query(science.Plant).count() == 1


def test_deux_composes_meme_nom_ne_creent_qu_une_ligne(db_session):
    """Le nom est la clé d'idempotence des composés."""
    c1, cree1 = service.create_compound(db_session, "Quercétine", formula="C15H10O7")
    c2, cree2 = service.create_compound(db_session, "Quercétine", formula="C21H20O11")
    assert cree1 is True
    assert cree2 is False
    assert c1.id == c2.id
    assert db_session.query(science.Compound).count() == 1


def test_inchikey_deja_utilisee_par_un_autre_compose_leve_valueerror(db_session):
    """Une InChIKey est un identifiant chimique unique : elle ne peut pas être
    réutilisée par un composé différent."""
    service.create_compound(db_session, "Quercétine", inchikey=INCHIKEY_QUERCETINE)
    with pytest.raises(ValueError):
        service.create_compound(db_session, "Rutine", inchikey=INCHIKEY_QUERCETINE)
    # Le composé fautif n'a pas été créé.
    assert db_session.query(science.Compound).count() == 1


def test_meme_compose_meme_inchikey_reste_idempotent(db_session):
    """Réimporter le même composé (même nom, même InChIKey) ne duplique pas."""
    c1, cree1 = service.create_compound(
        db_session, "Quercétine", inchikey=INCHIKEY_QUERCETINE
    )
    c2, cree2 = service.create_compound(
        db_session, "Quercétine", inchikey=INCHIKEY_QUERCETINE
    )
    assert cree1 is True
    assert cree2 is False
    assert c1.id == c2.id
    assert db_session.query(science.Compound).count() == 1


# ---------------------------------------------------------------------------
# Idempotence : associations et preuves
# ---------------------------------------------------------------------------

def test_double_lien_plante_compose_ne_cree_pas_de_doublon(db_session):
    """La contrainte unique (plant_id, compound_id, plant_part, publication)
    rend le double lien idempotent ; une autre partie de plante reste une
    association distincte."""
    plante, _ = service.create_plant(db_session, "Moringa oleifera")
    compose, _ = service.create_compound(db_session, "Quercétine")
    _, cree1 = service.link_plant_compound(
        db_session, plante.id, compose.id,
        plant_part="feuilles", publication="doi:10.1/a",
    )
    lien2, cree2 = service.link_plant_compound(
        db_session, plante.id, compose.id,
        plant_part="feuilles", publication="doi:10.1/a",
    )
    assert cree1 is True
    assert cree2 is False
    assert db_session.query(science.PlantCompound).count() == 1

    _, cree3 = service.link_plant_compound(
        db_session, plante.id, compose.id,
        plant_part="graines", publication="doi:10.1/a",
    )
    assert cree3 is True
    assert db_session.query(science.PlantCompound).count() == 2
    _ = lien2  # l'objet existant est retourné, pas un doublon


def test_meme_doi_importe_deux_fois_ne_cree_pas_de_doublon(db_session):
    """L'import d'une preuve est idempotent sur le DOI."""
    e1, cree1 = service.add_evidence(
        db_session,
        doi="10.1016/j.jep.2020.113123",
        title="Moringa oleifera : revue",
        year=2020,
    )
    e2, cree2 = service.add_evidence(
        db_session,
        doi="10.1016/j.jep.2020.113123",
        title="Moringa oleifera : revue",
        year=2020,
    )
    assert cree1 is True
    assert cree2 is False
    assert e1.id == e2.id
    assert db_session.query(science.Evidence).count() == 1


def test_preuve_sans_doi_conserve_les_champs(db_session):
    """Une preuve sans DOI (thèse, document gris...) est toujours créée et
    conserve ses champs JSON."""
    preuve, cree = service.add_evidence(
        db_session,
        title="Thèse de pharmacognosie",
        source_type="these",
        authors=["A. Diallo"],
        year=2023,
        provenance={"import": "manuel"},
    )
    assert cree is True
    assert preuve.id is not None
    assert preuve.doi is None
    assert preuve.source_type == "these"
    assert preuve.authors == ["A. Diallo"]
    assert preuve.provenance == {"import": "manuel"}


# ---------------------------------------------------------------------------
# Observations de laboratoire
# ---------------------------------------------------------------------------

def test_observation_privee_par_defaut_et_rattachee_au_projet(db_session):
    """Une observation de laboratoire est privée par défaut (is_private=True)
    et cloisonnée au projet qui l'a créée."""
    projet = _projet(db_session)
    obs = service.create_observation(
        db_session,
        projet.id,
        protocol_ref="PROTO-001",
        author="Dr Sawadogo",
        laboratory="Labo Ouaga",
        raw_data={"rendement_pct": 12.5, "solvant": "éthanol"},
        comment="Premier essai d'extraction",
    )
    assert obs.id is not None
    assert obs.project_id == projet.id
    assert obs.is_private is True
    assert obs.raw_data == {"rendement_pct": 12.5, "solvant": "éthanol"}
    assert obs.status == "brut"

    # Le journal d'expériences ne déduplique jamais : deux appels = deux lignes.
    obs2 = service.create_observation(db_session, projet.id, comment="Second essai")
    assert obs2.id != obs.id
    assert (
        db_session.query(science.ResearchObservation)
        .filter(science.ResearchObservation.project_id == projet.id)
        .count()
        == 2
    )


@pytest.fixture()
def _projet_id(db_session):
    """Projet de rattachement (P0-1 : ``project_id`` obligatoire pour
    créer une simulation — jamais orpheline)."""
    projet = modeles_noyau.Project(name="Projet Tests Modèles")
    db_session.add(projet)
    db_session.commit()
    return projet.id


# ---------------------------------------------------------------------------
# Simulations
# ---------------------------------------------------------------------------

def test_simulation_input_hash_deterministe(db_session, _projet_id):
    """Mêmes entrées (quel que soit l'ordre des clés) → même input_hash ;
    entrées différentes → hash différent. Le hash est un SHA-256 hexadécimal."""
    s1 = service.create_simulation(
        db_session, "Docking Q", {"cible": "COX-2", "ligand": "quercétine", "n": 100},
        project_id=_projet_id,
    )
    s2 = service.create_simulation(
        db_session, "Docking Q (bis)", {"n": 100, "ligand": "quercétine", "cible": "COX-2"},
        project_id=_projet_id,
    )
    s3 = service.create_simulation(
        db_session, "Docking R", {"cible": "COX-2", "ligand": "rutine", "n": 100},
        project_id=_projet_id,
    )
    assert s1.input_hash == s2.input_hash
    assert s1.input_hash != s3.input_hash
    assert len(s1.input_hash) == 64
    int(s1.input_hash, 16)  # bien de l'hexadécimal


def test_simulation_input_hash_est_le_sha256_du_json_canonique(db_session, _projet_id):
    """Conformité exacte : input_hash = sha256(canonical_json(inputs))."""
    entrees = {"cible": "COX-2", "ligand": "quercétine", "n": 100}
    sim = service.create_simulation(db_session, "Docking", entrees, project_id=_projet_id)
    assert sim.input_hash == sha256_hex(canonical_json(entrees))


def test_simulation_sans_entrees_leve_valueerror(db_session, _projet_id):
    """Une simulation sans entrées n'a aucun sens : elle est refusée."""
    with pytest.raises(ValueError):
        service.create_simulation(db_session, "Simulation vide", {}, project_id=_projet_id)


def test_simulation_sans_projet_leve_erreur(db_session):
    """P0-1 : une simulation sans projet (orpheline) est refusée —
    argument obligatoire (TypeError) ou `None` explicite (ValueError)."""
    with pytest.raises(TypeError):
        service.create_simulation(db_session, "Orpheline", {"cible": "COX-2"})
    with pytest.raises(ValueError, match="[Pp]rojet"):
        service.create_simulation(
            db_session, "Orpheline", {"cible": "COX-2"}, project_id=None
        )


def test_simulation_statut_par_defaut_exploratoire(db_session, _projet_id):
    """Une simulation naît « exploratoire » : ce n'est jamais une preuve."""
    sim = service.create_simulation(
        db_session, "Criblage", {"cible": "COX-2"}, project_id=_projet_id
    )
    assert sim.status == "exploratoire"
    assert sim.confidence == 0.5


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------

def test_recherche_plantes_trouve_par_nom(db_session):
    """search_plants retrouve « Moringa oleifera » via « moringa » (et ignore
    les plantes non correspondantes)."""
    service.create_plant(db_session, "Moringa oleifera")
    service.create_plant(db_session, "Artemisia annua")
    resultats = service.search_plants(db_session, "moringa", 10)
    noms = [p.scientific_name for p in resultats]
    assert "Moringa oleifera" in noms
    assert "Artemisia annua" not in noms


def test_recherche_plantes_insensible_a_la_casse(db_session):
    service.create_plant(db_session, "Moringa oleifera")
    trouves = service.search_plants(db_session, "MORINGA", 10)
    assert any(p.scientific_name == "Moringa oleifera" for p in trouves)


def test_recherche_composes_trouve_par_nom(db_session):
    """search_compounds retrouve « Quercétine » via « quercétine »."""
    service.create_compound(db_session, "Quercétine")
    service.create_compound(db_session, "Rutine")
    resultats = service.search_compounds(db_session, "quercétine", 10)
    noms = [c.name for c in resultats]
    assert "Quercétine" in noms
    assert "Rutine" not in noms


# ---------------------------------------------------------------------------
# Niveaux de données
# ---------------------------------------------------------------------------

def test_data_level_compte_cinq_niveaux_strictement_separes():
    """Les cinq niveaux épistémologiques existent avec leurs codes exacts."""
    assert science.DataLevel.PUBLIE.value == "publie"
    assert science.DataLevel.NORMALISE.value == "normalise"
    assert science.DataLevel.HYPOTHESE.value == "hypothese"
    assert science.DataLevel.PREDICTION.value == "prediction"
    assert science.DataLevel.EXPERIMENTAL.value == "experimental"
    assert len(science.DataLevel) == 5
