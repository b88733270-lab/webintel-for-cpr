"""Tests v2.2 — littérature : publications sans DOI, identifiants internes,
déduplication DOI → PMID → titre normalisé.

Règle d'or : jamais d'identifiant interne présenté comme DOI/PMID.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy.orm import Session

from webintel.science import litterature
from webintel.science.models import Evidence


def _these(db_session: Session, **kwargs) -> Evidence:
    kwargs.setdefault("titre", "Plantes médicinales du Burkina Faso")
    kwargs.setdefault("auteurs", ["Ouédraogo A."])
    kwargs.setdefault("annee", 2022)
    kwargs.setdefault("type_document", "these")
    kwargs.setdefault("journal_ou_institution", "Université Joseph Ki-Zerbo")
    preuve, _ = litterature.ajouter_publication_locale(db_session, **kwargs)
    return preuve


def test_publication_sans_doi_recoit_identifiant_interne(db_session: Session):
    preuve = _these(db_session)
    assert preuve.doi is None and preuve.pmid is None
    assert re.fullmatch(r"WBF-2022-\d{4}", preuve.identifiant_interne or "")
    assert preuve.document_type == "these"
    assert preuve.institution == "Université Joseph Ki-Zerbo"


def test_identifiant_interne_sequence_annuelle(db_session: Session):
    p1 = _these(db_session, titre="Thèse un", annee=2022)
    p2 = _these(db_session, titre="Thèse deux", annee=2022)
    p3 = _these(db_session, titre="Thèse trois", annee=2023)
    assert p1.identifiant_interne == "WBF-2022-0001"
    assert p2.identifiant_interne == "WBF-2022-0002"
    assert p3.identifiant_interne == "WBF-2023-0001"  # compteur annuel


def test_citation_marque_identifiant_interne(db_session: Session):
    preuve = _these(db_session)
    citation = litterature.reference_citee(preuve)
    assert "identifiant interne WebIntel" in citation
    assert "ne pas citer comme DOI/PMID" in citation
    assert "DOI:" not in citation and "PMID:" not in citation


def test_citation_doi_classique(db_session: Session):
    preuve = _these(db_session, doi="10.1234/test", titre="Article test")
    assert preuve.identifiant_interne is None  # pas de WBF si DOI
    citation = litterature.reference_citee(preuve)
    assert "DOI: 10.1234/test" in citation
    assert "identifiant interne" not in citation


def test_dedup_doi(db_session: Session):
    p1 = _these(db_session, doi="10.1234/x", titre="Titre A")
    preuve, creee = litterature.ajouter_publication_locale(
        db_session, titre="Titre B différent", doi="10.1234/x"
    )
    assert not creee and preuve.id == p1.id


def test_dedup_pmid(db_session: Session):
    p1 = _these(db_session, pmid="12345", titre="Titre A")
    preuve, creee = litterature.ajouter_publication_locale(
        db_session, titre="Titre B différent", pmid="12345"
    )
    assert not creee and preuve.id == p1.id


def test_dedup_titre_normalise(db_session: Session):
    p1 = _these(db_session, titre="  Les PLANTES médicinales : synthèse ! ")
    preuve, creee = litterature.ajouter_publication_locale(
        db_session,
        titre="les plantes medicinales synthese",
        annee=2022,
    )
    assert not creee and preuve.id == p1.id


def test_titre_different_meme_annee_pas_de_dedup(db_session: Session):
    _these(db_session, titre="Thèse sur le moringa")
    preuve, creee = litterature.ajouter_publication_locale(
        db_session, titre="Thèse sur le baobab", annee=2022
    )
    assert creee


def test_titre_sans_contenu_rejete(db_session: Session):
    with pytest.raises(ValueError, match="au moins un titre"):
        litterature.ajouter_publication_locale(db_session, titre="  ")


def test_type_document_inconnu_replie_sans_rejet(db_session: Session):
    preuve = _these(db_session, type_document="papyrus")
    assert preuve.document_type == "document_local"


def test_normaliser_titre_unicode():
    assert (
        litterature.normaliser_titre("  L’Étude : plantes (médicinales) ! ")
        == "l etude plantes medicinales"
    )


def test_est_identifiant_interne():
    assert litterature.est_identifiant_interne("WBF-2022-0001")
    assert not litterature.est_identifiant_interne("10.1234/x")
    assert not litterature.est_identifiant_interne("WBF-22-1")
