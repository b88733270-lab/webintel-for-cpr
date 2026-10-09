"""Tests v2.2 — contrôle qualité : un test par détecteur, lecture seule
(aucune correction), endpoint API.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from webintel.api.routers import science_qualite
from webintel.science import mesures, qualite, service as science_service
from webintel.science.models import (
    Compound,
    Evidence,
    MesureExperimentale,
    Plant,
    PlantCompound,
    Specimen,
)


@pytest.fixture()
def qualite_client(server_bootstrap):
    app = FastAPI()
    app.include_router(science_qualite.router, prefix="/api", tags=["science"])
    headers = {}
    if server_bootstrap is not None:
        headers["X-API-Key"] = server_bootstrap["plain_key"]
    return TestClient(app, headers=headers)


def _categories(anomalies):
    return {a["categorie"] for a in anomalies}


def test_smiles_invalide_detecte(db_session: Session):
    science_service.create_compound(
        db_session, "Compose X", smiles="C(C(C", formula="C6H6"
    )
    anomalies = qualite.detecter_smiles_invalides(db_session)
    assert len(anomalies) == 1
    assert anomalies[0]["severite"] == "avertissement"
    assert "Compose X" in anomalies[0]["titre"]
    assert "Corriger" in anomalies[0]["action_proposee"]


def test_smiles_valide_silencieux(db_session: Session):
    science_service.create_compound(
        db_session, "Quercétine", smiles="C1=CC(=C(C=C1C2=C(C(=O)C(=CC2=O)O)O)O)O"
    )
    assert qualite.detecter_smiles_invalides(db_session) == []


def test_doublons_composes_inchikey_impossible_en_base(db_session: Session):
    # La contrainte d'unicité InChIKey est la première défense : la base
    # elle-même refuse le doublon (le détecteur « critique » couvre les
    # bases héritées sans contrainte).
    db_session.add(Compound(name="Alpha", inchikey="AAAA-BBBB"))
    db_session.flush()
    db_session.add(Compound(name="Bêta", inchikey="AAAA-BBBB"))
    with pytest.raises(Exception):
        db_session.flush()
    db_session.rollback()
    assert qualite.detecter_doublons_composes(db_session) == []


def test_doublons_composes_nom_avertissement(db_session: Session):
    db_session.add(Compound(name="Curcumine", inchikey="K1"))
    db_session.add(Compound(name="curcumine ", inchikey="K2"))
    db_session.flush()
    anomalies = qualite.detecter_doublons_composes(db_session)
    assert any(
        a["severite"] == "avertissement" and "Curcumine" in a["titre"]
        for a in anomalies
    )


def test_doublons_publications_titre_sans_identifiant(db_session: Session):
    # Deux preuves sans DOI/PMID au même titre normalisé (+ année) :
    # le détecteur signale le doublon probable (avertissement).
    db_session.add(Evidence(title="Thèse sur le moringa", year=2022))
    db_session.add(Evidence(title="THESE sur le moringa !", year=2022))
    db_session.flush()
    anomalies = qualite.detecter_doublons_publications(db_session)
    assert any(
        a["categorie"] == "doublons_publications"
        and "Titre en doublon" in a["titre"]
        for a in anomalies
    )


def test_taxon_non_binomial(db_session: Session):
    science_service.create_plant(db_session, "Moringa")
    anomalies = qualite.detecter_taxons_ambigus(db_session)
    assert any("non binomial" in a["titre"] for a in anomalies)


def test_taxon_statut_gbif_synonyme(db_session: Session):
    science_service.create_plant(
        db_session,
        "Cassia occidentalis",
        external_ids={"gbif": {"status": "SYNONYM", "taxon_key": 1}},
    )
    anomalies = qualite.detecter_taxons_ambigus(db_session)
    assert any("SYNONYM" in a["titre"] for a in anomalies)


def test_taxon_gbif_statut_francais_connecteur(db_session: Session):
    # Le connecteur GBIF enregistre external_ids["gbif"] comme un dict
    # avec la clé française « statut » — le détecteur doit le lire
    # (régression : clé « status » lue, vérification silencieusement morte).
    science_service.create_plant(
        db_session,
        "Senna occidentalis",
        external_ids={"gbif": {"statut": "SYNONYM", "taxon_key": 1}},
    )
    anomalies = qualite.detecter_taxons_ambigus(db_session)
    assert any("SYNONYM" in a["titre"] for a in anomalies)


def test_taxon_gbif_cle_brute_sans_crash(db_session: Session):
    # Les données historiques/démo stockent parfois la clé taxon GBIF
    # brute (chaîne) : le détecteur ne doit pas planter — c'est ce qui
    # faisait répondre /tableau-de-bord en 500 (v3.0.1).
    science_service.create_plant(
        db_session,
        "Hibiscus sabdariffa",
        external_ids={"gbif": "3152703"},
    )
    anomalies = qualite.detecter_taxons_ambigus(db_session)
    assert not any(
        "Statut GBIF ambigu" in a["titre"] for a in anomalies
    )


def test_synonymes_contradictoires(db_session: Session):
    science_service.create_plant(
        db_session, "Moringa oleifera", synonyms=["Moringa pterygosperma"]
    )
    science_service.create_plant(db_session, "Moringa pterygosperma")
    anomalies = qualite.detecter_synonymes_contradictoires(db_session)
    assert len(anomalies) >= 1
    assert anomalies[0]["severite"] == "avertissement"
    assert "nom accepté" in anomalies[0]["titre"]


def test_relation_sans_source(db_session: Session):
    plante, _ = science_service.create_plant(db_session, "Moringa oleifera")
    compose, _ = science_service.create_compound(db_session, "Quercétine")
    db_session.add(PlantCompound(plant_id=plante.id, compound_id=compose.id))
    db_session.flush()
    anomalies = qualite.detecter_relations_sans_source(db_session)
    assert len(anomalies) == 1
    assert "intraçable" in anomalies[0]["detail"]


def test_relation_avec_doi_silencieuse(db_session: Session):
    plante, _ = science_service.create_plant(db_session, "Moringa oleifera")
    compose, _ = science_service.create_compound(db_session, "Quercétine")
    db_session.add(
        PlantCompound(
            plant_id=plante.id, compound_id=compose.id, doi="10.1/x"
        )
    )
    db_session.flush()
    assert qualite.detecter_relations_sans_source(db_session) == []


def test_specimen_sans_voucher(db_session: Session):
    plante, _ = science_service.create_plant(db_session, "Moringa oleifera")
    db_session.add(Specimen(plant_id=plante.id))
    db_session.flush()
    anomalies = qualite.detecter_echantillons_sans_voucher(db_session)
    categories = {(a["titre"], a["severite"]) for a in anomalies}
    assert any("sans code voucher" in t for t, _ in categories)
    assert any("sans herbier" in t for t, _ in categories)


def test_donnees_incompletes_compose(db_session: Session):
    science_service.create_compound(db_session, "Molécule mystère")
    anomalies = qualite.detecter_donnees_incompletes(db_session)
    assert any("sans formule ni SMILES" in a["titre"] for a in anomalies)


def test_donnees_incompletes_mesure_sans_valeur(db_session: Session):
    mesures.creer_mesure(db_session, sample="Échantillon vide")
    anomalies = qualite.detecter_donnees_incompletes(db_session)
    assert any("sans aucune valeur" in a["titre"] for a in anomalies)


def test_unites_incoherentes_inconnue_et_melangees(db_session: Session):
    mesures.creer_mesure(
        db_session, sample="S1", assay="DPPH", compound_name="Q",
        concentration=10.0, concentration_unite="gouttes",
    )
    mesures.creer_mesure(
        db_session, sample="S2", assay="DPPH", compound_name="Q",
        concentration=20.0, concentration_unite="mg/L",
    )
    mesures.creer_mesure(
        db_session, sample="S3", assay="DPPH", compound_name="Q",
        concentration=30.0, concentration_unite="µg/mL",
    )
    anomalies = qualite.detecter_unites_incoherentes(db_session)
    assert any("gouttes" in a["titre"] for a in anomalies)  # inconnue
    assert any("mélangées" in a["titre"] for a in anomalies)  # mg/L vs µg/mL


def test_unite_dimension_incorrecte(db_session: Session):
    mesures.creer_mesure(
        db_session, sample="S1", dose=5.0, dose_unite="heures"
    )
    anomalies = qualite.detecter_unites_incoherentes(db_session)
    assert any("Dimension incohérente" in a["titre"] for a in anomalies)


def test_controle_lecture_seule_ne_modifie_rien(db_session: Session):
    science_service.create_compound(db_session, "Molécule mystère")
    avant = db_session.query(Compound).count()
    resultat = qualite.executer_controle(db_session)
    assert db_session.query(Compound).count() == avant
    assert resultat["resume"]["total"] >= 1
    assert set(resultat["resume"]["par_severite"]) == {
        "info", "avertissement", "critique",
    }


def test_controle_filtre_detecteurs(db_session: Session):
    science_service.create_compound(db_session, "Molécule mystère")
    resultat = qualite.executer_controle(
        db_session, detecteurs=["donnees_incompletes"]
    )
    assert _categories(resultat["anomalies"]) == {"donnees_incompletes"}


def test_controle_detecteur_inconnu_400(qualite_client):
    reponse = qualite_client.get(
        "/api/science/qualite", params={"detecteur": "inexistant"}
    )
    assert reponse.status_code == 400
    assert "inconnu" in reponse.json()["detail"]


def test_api_qualite_rapport_complet(db_session: Session, qualite_client):
    science_service.create_compound(db_session, "Molécule mystère")
    reponse = qualite_client.get("/api/science/qualite")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert set(corps) == {
        "date_controle", "resume", "anomalies", "verifications_ignorees",
        "total_anomalies", "troncature", "rappel",
    }
    assert corps["total_anomalies"] >= 1
    assert corps["troncature"] is False
    anomalie = corps["anomalies"][0]
    assert set(anomalie) == {
        "categorie", "severite", "entite_type", "entite_id",
        "titre", "detail", "action_proposee",
    }
    assert "aucune correction automatique" in corps["rappel"]


def test_api_qualite_limite_troncature(db_session: Session, qualite_client):
    for i in range(3):
        science_service.create_compound(db_session, f"Molécule {i}")
    reponse = qualite_client.get("/api/science/qualite", params={"limite": 1})
    corps = reponse.json()
    assert len(corps["anomalies"]) == 1
    assert corps["troncature"] is True
