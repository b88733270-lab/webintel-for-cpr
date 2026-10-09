"""Tests — recherche en ligne chercheur (chantier B).

ZÉRO réseau réel : les connecteurs sont mockés au niveau de
``_telecharger`` (même patron que ``tests/test_science_connecteurs.py``).
Le live n'est jamais exercé ici.
"""

from __future__ import annotations

import httpx
import pytest

from webintel.database.base import SessionLocal
from webintel.science.connecteurs.plugins.gbif import GBIFConnecteur
from webintel.science.connecteurs.plugins.pubchem import PubChemConnecteur
from webintel.science.connecteurs.plugins.pubmed import PubMedConnecteur
from webintel.science.models import (
    Compound,
    Evidence,
    ImportProvenance,
    Plant,
)


PUG_ASPIRINE = {
    "PropertyTable": {
        "Properties": [
            {
                "CID": 2244,
                "Title": "Aspirine",
                "MolecularFormula": "C9H8O4",
                "MolecularWeight": "180.16",
                "CanonicalSMILES": "CC(=O)OC1=CC=CC=C1C(=O)O",
                "IsomericSMILES": "CC(=O)OC1=CC=CC=C1C(O)=O",
                "InChIKey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            }
        ]
    }
}

GBIF_MATCH = {
    "usageKey": 8373355,
    "scientificName": "Artemisia annua L.",
    "canonicalName": "Artemisia annua",
    "rank": "SPECIES",
    "status": "ACCEPTED",
    "confidence": 97,
    "matchType": "EXACT",
    "kingdom": "Plantae",
    "family": "Asteraceae",
    "genus": "Artemisia",
}

ESEARCH_MOCK = {"esearchresult": {"idlist": ["111", "222"]}}

ESUMMARY_MOCK = {
    "result": {
        "111": {
            "uid": "111",
            "title": "Artemisinin and malaria",
            "authors": [{"name": "Dupont A"}, {"name": "Martin B"}],
            "pubdate": "2021 Mar",
            "articleids": [{"idtype": "doi", "value": "10.1000/test111"}],
        },
        "222": {
            "uid": "222",
            "title": "Plants against fever",
            "authors": [{"name": "Durand C"}],
            "pubdate": "2019",
            "articleids": [],
        },
    }
}


def _mock_pubchem_ok(monkeypatch):
    def faux_telecharger(self, url, params=None):
        return PUG_ASPIRINE

    monkeypatch.setattr(PubChemConnecteur, "_telecharger", faux_telecharger)


def _mock_gbif_ok(monkeypatch):
    def faux_telecharger(self, url, params=None):
        if "/species/match" in url:
            return dict(GBIF_MATCH)
        if "/occurrence/search" in url:
            return {"count": 12}
        raise AssertionError(f"URL inattendue : {url}")

    monkeypatch.setattr(GBIFConnecteur, "_telecharger", faux_telecharger)


def _mock_pubmed_ok(monkeypatch):
    def faux_telecharger(self, url, params=None):
        if "esearch" in url:
            return ESEARCH_MOCK
        return ESUMMARY_MOCK

    monkeypatch.setattr(PubMedConnecteur, "_telecharger", faux_telecharger)


def _mock_pubchem_down(monkeypatch):
    def faux_telecharger(self, url, params=None):
        raise httpx.ConnectError("connexion refusee (mock)")

    monkeypatch.setattr(PubChemConnecteur, "_telecharger", faux_telecharger)


def _compter(modele) -> int:
    session = SessionLocal()
    try:
        return session.query(modele).count()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# (a) Recherche locale vide -> proposition de recherche en ligne
# ---------------------------------------------------------------------------


def test_recherche_locale_vide_propose_en_ligne(client):
    r = client.get("/recherche-universelle", params={"q": "TermeSansAucunResultat"})
    assert r.status_code == 200, r.text[:500]
    html = r.text
    assert "Aucun résultat local" in html
    assert "Rechercher en ligne" in html
    assert "/recherche-en-ligne" in html
    assert "TermeSansAucunResultat" in html
    # Bouton explicite toujours visible sur la page de recherche.
    r2 = client.get("/recherche-universelle")
    assert r2.status_code == 200
    assert "Rechercher en ligne" in r2.text


def test_plante_introuvable_propose_gbif_en_ligne(client):
    r = client.get("/plante-etude", params={"q": "Plante Inconnue"})
    assert r.status_code == 200, r.text[:500]
    assert "Rechercher en ligne" in r.text
    assert "GBIF" in r.text
    assert "/recherche-en-ligne" in r.text


def test_apercu_pubchem_affiche_source_sans_importer(client, monkeypatch):
    _mock_pubchem_ok(monkeypatch)
    avant_composes = _compter(Compound)
    avant_provenances = _compter(ImportProvenance)
    r = client.get(
        "/recherche-en-ligne", params={"q": "aspirine", "source": "pubchem"}
    )
    assert r.status_code == 200, r.text[:500]
    html = r.text
    # Résultat en ligne affiché AVANT import, avec source + licence.
    assert "Aspirine" in html
    assert "C9H8O4" in html
    assert "PubChem" in html
    assert "Licence" in html
    assert "Importer depuis PubChem" in html
    assert 'action="/recherche-en-ligne/importer"' in html
    assert 'name="connecteur"' in html
    # L'aperçu seul ne crée rien en base (comptes inchangés, aucun
    # composé « Aspirine » créé — le seed éventuel est ignoré ici).
    assert _compter(Compound) == avant_composes
    assert _compter(ImportProvenance) == avant_provenances
    session = SessionLocal()
    try:
        assert session.query(Compound).filter(Compound.name == "Aspirine").first() is None
    finally:
        session.close()


def test_apercu_gbif_et_pubmed_affichent_source_sans_importer(client, monkeypatch):
    _mock_gbif_ok(monkeypatch)
    avant_plantes = _compter(Plant)
    r = client.get(
        "/recherche-en-ligne", params={"q": "Artemisia annua", "source": "gbif"}
    )
    assert r.status_code == 200, r.text[:500]
    assert "Artemisia annua" in r.text
    assert "8373355" in r.text
    assert "GBIF" in r.text
    assert "Importer depuis GBIF" in r.text
    assert _compter(Plant) == avant_plantes

    _mock_pubmed_ok(monkeypatch)
    r2 = client.get(
        "/recherche-en-ligne", params={"q": "artemisia", "source": "pubmed"}
    )
    assert r2.status_code == 200, r2.text[:500]
    assert "Artemisinin and malaria" in r2.text
    assert "Plants against fever" in r2.text
    assert "PubMed" in r2.text
    assert "Importer depuis PubMed" in r2.text
    session = SessionLocal()
    try:
        assert session.query(Evidence).filter(Evidence.pmid.in_(["111", "222"])).count() == 0
    finally:
        session.close()


def test_apercu_toutes_sources(client, monkeypatch):
    _mock_pubchem_ok(monkeypatch)
    _mock_gbif_ok(monkeypatch)
    _mock_pubmed_ok(monkeypatch)
    r = client.get(
        "/recherche-en-ligne", params={"q": "artemisia", "source": "tout"}
    )
    assert r.status_code == 200, r.text[:500]
    # Chaque source est affichée avec sa licence, même si un résultat
    # est propre à une seule d'entre elles.
    assert "PubChem" in r.text
    assert "GBIF" in r.text
    assert "PubMed" in r.text
    assert "Licence" in r.text


# ---------------------------------------------------------------------------
# (b) Import -> objet local créé avec provenance
# ---------------------------------------------------------------------------


def test_import_pubchem_cree_compound_avec_provenance(client, monkeypatch):
    _mock_pubchem_ok(monkeypatch)
    r = client.post(
        "/recherche-en-ligne/importer",
        data={"connecteur": "pubchem", "q": "aspirine"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:500]
    assert "/recherche-universelle" in r.headers["location"]
    assert "Aspirine" in r.headers["location"]

    session = SessionLocal()
    try:
        compose = session.query(Compound).filter(Compound.name == "Aspirine").one()
        assert compose.formula == "C9H8O4"
        assert compose.inchikey == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
        assert compose.external_ids["connecteur"] == "pubchem"
        assert "pubchem" in compose.external_ids
        provs = (
            session.query(ImportProvenance)
            .filter(
                ImportProvenance.connecteur_nom == "pubchem",
                ImportProvenance.item_id == compose.id,
            )
            .all()
        )
        assert len(provs) >= 1
        prov = provs[0]
        assert prov.type_donnees == "compound"
        assert "PubChem" in (prov.licence or "")
    finally:
        session.close()


def test_import_gbif_cree_plante_avec_provenance(client, monkeypatch):
    _mock_gbif_ok(monkeypatch)
    r = client.post(
        "/recherche-en-ligne/importer",
        data={"connecteur": "gbif", "q": "Artemisia annua"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:500]
    assert "/plante-etude" in r.headers["location"]

    session = SessionLocal()
    try:
        plante = (
            session.query(Plant)
            .filter(Plant.scientific_name == "Artemisia annua")
            .one()
        )
        assert plante.external_ids["gbif"]["taxon_key"] == 8373355
        provs = (
            session.query(ImportProvenance)
            .filter(
                ImportProvenance.connecteur_nom == "gbif",
                ImportProvenance.item_id == plante.id,
            )
            .all()
        )
        assert len(provs) >= 1
        assert provs[0].type_donnees == "plant"
    finally:
        session.close()

    # La fiche locale créée est affichable après redirection.
    r2 = client.get(r.headers["location"])
    assert r2.status_code == 200
    assert "Artemisia annua" in r2.text


def test_import_pubmed_cree_publications_avec_provenance(client, monkeypatch):
    _mock_pubmed_ok(monkeypatch)
    r = client.post(
        "/recherche-en-ligne/importer",
        data={"connecteur": "pubmed", "q": "artemisia"},
        follow_redirects=False,
    )
    # Import groupé : la page affiche les fiches locales créées.
    assert r.status_code == 200, r.text[:500]
    assert "Artemisinin and malaria" in r.text
    assert "Plants against fever" in r.text
    assert "importée" in r.text

    session = SessionLocal()
    try:
        evidences = (
            session.query(Evidence).filter(Evidence.pmid.in_(["111", "222"])).all()
        )
        assert len(evidences) == 2
        assert {e.pmid for e in evidences} == {"111", "222"}
        ids = {e.id for e in evidences}
        provs = (
            session.query(ImportProvenance)
            .filter(ImportProvenance.connecteur_nom == "pubmed")
            .all()
        )
        assert len(provs) >= 2
        assert ids <= {p.item_id for p in provs}
        assert all(p.type_donnees == "evidence" for p in provs if p.item_id in ids)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# (c) Connecteur en échec -> message français, aucun objet créé
# ---------------------------------------------------------------------------


def test_connecteur_injoignable_message_francais_aucun_objet(client, monkeypatch):
    _mock_pubchem_down(monkeypatch)
    avant_composes = _compter(Compound)
    avant_provenances = _compter(ImportProvenance)

    # Aperçu : message explicite, aucun faux résultat.
    r = client.get(
        "/recherche-en-ligne", params={"q": "aspirine", "source": "pubchem"}
    )
    assert r.status_code == 200, r.text[:500]
    assert "injoignable" in r.text
    assert "vos données locales restent disponibles" in r.text
    assert "Aspirine" not in r.text  # aucun résultat inventé affiché comme réel

    # Import : même message, et rien n'est créé.
    r2 = client.post(
        "/recherche-en-ligne/importer",
        data={"connecteur": "pubchem", "q": "aspirine"},
        follow_redirects=False,
    )
    assert r2.status_code == 200, r2.text[:500]
    assert "injoignable" in r2.text
    assert "vos données locales restent disponibles" in r2.text
    assert _compter(Compound) == avant_composes
    assert _compter(ImportProvenance) == avant_provenances
    session = SessionLocal()
    try:
        assert session.query(Compound).filter(Compound.name == "Aspirine").first() is None
    finally:
        session.close()
