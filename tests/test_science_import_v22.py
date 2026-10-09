"""Tests v2.2 — pipeline d'import : nouvelles colonnes de preuves,
import de mesures expérimentales, idempotence de la migration v2.2.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from webintel.database.base import engine, init_db
from webintel.science import import_export
from webintel.science.models import Evidence, MesureExperimentale


def test_import_evidence_nouvelles_colonnes(db_session: Session):
    lignes = [
        (
            2,
            {
                "type_source": "these",
                "type_document": "these",
                "institution": "Université Joseph Ki-Zerbo",
                "fichier_local": "/docs/these_ouedraogo.pdf",
                "langue": "fr",
                "pays_edition": "Burkina Faso",
                "titre": "Phytochimie du moringa",
                "auteurs": "Ouédraogo A.",
                "annee": "2021",
            },
        )
    ]
    rapport = import_export.importer_lignes(
        db_session, "evidence", lignes, "theses.csv"
    )
    assert rapport["crees"] == 1
    assert rapport["erreurs"] == []
    preuve = db_session.query(Evidence).one()
    assert preuve.document_type == "these"
    assert preuve.institution == "Université Joseph Ki-Zerbo"
    assert preuve.fichier_local == "/docs/these_ouedraogo.pdf"
    assert preuve.langue == "fr"
    assert preuve.pays_edition == "Burkina Faso"
    # Sans DOI/PMID : identifiant interne généré.
    assert (preuve.identifiant_interne or "").startswith("WBF-2021-")


def test_import_evidence_type_document_invalide(db_session: Session):
    lignes = [(2, {"titre": "X", "type_document": "papyrus"})]
    rapport = import_export.importer_lignes(
        db_session, "evidence", lignes, "x.csv"
    )
    assert rapport["crees"] == 0
    assert any(
        e["champ"] == "type_document" for e in rapport["erreurs"]
    )


def test_import_evidence_type_source_publication_devient_article(
    db_session: Session,
):
    lignes = [(2, {"titre": "Article Y", "type_source": "publication"})]
    rapport = import_export.importer_lignes(
        db_session, "evidence", lignes, "y.csv"
    )
    assert rapport["crees"] == 1
    preuve = db_session.query(Evidence).one()
    assert preuve.document_type == "article"


def test_import_evidence_dedup_titre_normalise(db_session: Session):
    lignes = [
        (2, {"titre": "Phytochimie du Moringa", "annee": "2021"}),
        (3, {"titre": "phytochimie du moringa !", "annee": "2021"}),
    ]
    rapport = import_export.importer_lignes(
        db_session, "evidence", lignes, "z.csv"
    )
    assert rapport["crees"] == 1
    assert rapport["ignores"] == 1
    assert db_session.query(Evidence).count() == 1


def test_import_mesure_unites_signalees(db_session: Session):
    lignes = [
        (
            2,
            {
                "sample": "Extrait aqueux",
                "compound": "Quercétine",
                "concentration": "100",
                "concentration_unite": "µg/mL",
                "response": "42",
                "response_unite": "%",
                "assay": "DPPH",
            },
        ),
        (
            3,
            {
                "sample": "Extrait méthanolique",
                "concentration": "50",
                "concentration_unite": "pincées",
            },
        ),
        (
            4,
            {
                # Ni sample ni compound : rejet explicite.
                "concentration": "10",
            },
        ),
    ]
    rapport = import_export.importer_lignes(
        db_session, "mesure", lignes, "mesures.csv"
    )
    assert rapport["crees"] == 2
    assert len(rapport["erreurs"]) == 1
    assert any(
        "pincées" in a and "Ligne 3" in a for a in rapport["avertissements"]
    )
    mesures = (
        db_session.query(MesureExperimentale)
        .order_by(MesureExperimentale.id)
        .all()
    )
    assert all(m.niveau_chaine == "brut" for m in mesures)
    # Unité conservée telle quelle (jamais corrigée silencieusement).
    assert mesures[1].concentration_unite == "pincées"


def test_import_mesure_type_inconnu_rejete(db_session: Session):
    with pytest.raises(import_export.ErreurImport):
        import_export.importer_lignes(
            db_session, "mesure_inexistante", [], "x.csv"
        )


def test_migration_v22_idempotente():
    # init_db() peut être rappelé sans doublons ni erreurs : les
    # colonnes v2.2 existent et un second passage ne fait rien.
    init_db()
    init_db()
    colonnes_evidence = {
        c["name"] for c in inspect(engine).get_columns("evidences")
    }
    colonnes_provenance = {
        c["name"] for c in inspect(engine).get_columns("provenances")
    }
    for colonne in (
        "document_type", "institution", "fichier_local",
        "identifiant_interne", "langue", "pays_edition",
    ):
        assert colonne in colonnes_evidence, colonne
    for colonne in (
        "acteur", "lieu", "commentaire", "outil",
        "parametres", "niveau_preuve",
    ):
        assert colonne in colonnes_provenance, colonne
    assert "specimens" in inspect(engine).get_table_names()
    assert "mesures_experimentales" in inspect(engine).get_table_names()
