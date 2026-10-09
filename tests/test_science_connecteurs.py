"""Tests Phase 10 — connecteurs de sources externes.

ZÉRO réseau réel : chaque appel HTTP est mocké (la suite bloque le DNS).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from webintel.api.routers import science_connecteurs
from webintel.database.base import SessionLocal
from webintel.science import models as science_models
from webintel.science.connecteurs import registry
from webintel.science.connecteurs.base import (
    BaseConnecteur,
    ConnecteurIndisponible,
    ParametresInvalides,
)
from webintel.science.connecteurs.plugins.pubchem import PubChemConnecteur
from webintel.science.connecteurs.plugins.pubmed import PubMedConnecteur
from webintel.science.models import (
    Compound,
    ConnecteurCache,
    Evidence,
    ImportProvenance,
    SourceConnecteur,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TABLES_PHASE10 = [ImportProvenance, ConnecteurCache, SourceConnecteur]


@pytest.fixture(autouse=True)
def _clean_phase10():
    """Nettoie les tables Phase 10 avant chaque test (conftest ne les
    connaît pas). Les lignes ``SourceConnecteur`` sont des données de
    référence (amorcées au démarrage, jamais dans un GET — correctif
    P2-1) : réamorcées après la purge."""
    session = SessionLocal()
    try:
        for model in _TABLES_PHASE10:
            session.query(model).delete()
        session.query(Compound).delete()
        session.query(Evidence).delete()
        session.commit()
        if registry.initialiser_connecteurs(session):
            session.commit()
    finally:
        session.close()
    yield


@pytest.fixture()
def api_client(server_bootstrap):
    """Client HTTP sur une app minimale portant le routeur connecteurs.

    En mode serveur (`REQUIRE_API_KEY=true`), injecte l'en-tête `X-API-Key`
    du bootstrap — même motif que la fixture `client` du conftest.
    """
    app = FastAPI()
    app.include_router(
        science_connecteurs.router, prefix="/api/science/connecteurs"
    )
    headers = {}
    if server_bootstrap is not None:
        headers["X-API-Key"] = server_bootstrap["plain_key"]
    return TestClient(app, headers=headers)


# ---------------------------------------------------------------------------
# Données mockées
# ---------------------------------------------------------------------------

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


def _mock_pubchem_ok(monkeypatch, compteur: dict | None = None):
    def faux_telecharger(self, url, params=None):
        if compteur is not None:
            compteur["n"] += 1
        return PUG_ASPIRINE

    monkeypatch.setattr(
        PubChemConnecteur, "_telecharger", faux_telecharger
    )


def _mock_reseau_down(monkeypatch, classe):
    def faux_telecharger(self, url, params=None):
        raise httpx.ConnectError("connexion refusee (mock)")

    monkeypatch.setattr(classe, "_telecharger", faux_telecharger)


# ---------------------------------------------------------------------------
# Registre
# ---------------------------------------------------------------------------

def test_registre_contient_pubchem_et_pubmed():
    noms = {c.NOM for c in registry.lister_connecteurs()}
    assert {"pubchem", "pubmed"} <= noms


def test_get_connecteur_inconnu_leve_valueerror_explicite():
    with pytest.raises(ValueError, match="Connecteur inconnu"):
        registry.get_connecteur("inexistant")


def test_initialiser_connecteurs_idempotent(db_session: Session):
    # La table est amorcée par `init_db` / la purge de test (donnée de
    # référence) : on repart d'une table vide pour vérifier l'amorçage.
    db_session.query(SourceConnecteur).delete()
    db_session.commit()
    assert registry.initialiser_connecteurs(db_session) == 5
    db_session.commit()
    assert registry.initialiser_connecteurs(db_session) == 0
    db_session.commit()
    assert db_session.query(SourceConnecteur).count() == 5
    ligne = (
        db_session.query(SourceConnecteur)
        .filter(SourceConnecteur.nom == "pubchem")
        .one()
    )
    assert ligne.licence  # licence obligatoire renseignée
    assert ligne.statut == "actif"


# ---------------------------------------------------------------------------
# PubChem
# ---------------------------------------------------------------------------

def test_pubchem_import_cree_compound_et_provenance(
    db_session: Session, monkeypatch
):
    _mock_pubchem_ok(monkeypatch)
    connecteur = registry.get_connecteur("pubchem")
    rapport = connecteur.importer(db_session, {"nom": "aspirine"})
    db_session.commit()

    assert rapport["crees"] == 1
    assert rapport["mis_a_jour"] == 0
    assert rapport["erreurs"] == []

    compose = (
        db_session.query(Compound)
        .filter(Compound.name == "Aspirine")
        .one()
    )
    assert compose.formula == "C9H8O4"
    assert compose.inchikey == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    assert compose.smiles == "CC(=O)OC1=CC=CC=C1C(=O)O"
    assert abs(compose.molecular_weight - 180.16) < 1e-6

    prov = db_session.query(ImportProvenance).one()
    assert prov.type_donnees == "compound"
    assert prov.item_id == compose.id
    assert prov.connecteur_nom == "pubchem"
    assert prov.version_donnees == "PUG-REST"
    assert "PubChem" in (prov.licence or "")
    assert prov.date_import is not None


def test_pubchem_reimport_sans_doublon(
    db_session: Session, monkeypatch
):
    _mock_pubchem_ok(monkeypatch)
    connecteur = registry.get_connecteur("pubchem")
    rapport1 = connecteur.importer(db_session, {"nom": "aspirine"})
    db_session.commit()
    rapport2 = connecteur.importer(db_session, {"nom": "aspirine"})
    db_session.commit()

    assert rapport1["crees"] == 1
    assert rapport2["crees"] == 0
    assert rapport2["mis_a_jour"] == 0
    assert db_session.query(Compound).count() == 1


def test_pubchem_parametre_nom_obligatoire(db_session: Session):
    connecteur = registry.get_connecteur("pubchem")
    with pytest.raises(ParametresInvalides, match="nom"):
        connecteur.importer(db_session, {})


def test_pubchem_compose_introuvable_404(
    db_session: Session, monkeypatch
):
    def faux_404(self, url, params=None):
        reponse = httpx.Response(404, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("Not Found", request=reponse.request,
                                    response=reponse)

    monkeypatch.setattr(PubChemConnecteur, "_telecharger", faux_404)
    connecteur = registry.get_connecteur("pubchem")
    rapport = connecteur.importer(db_session, {"nom": "xyzintrouvable"})
    assert rapport["crees"] == 0
    assert rapport["ignores"] == 1
    assert any("introuvable" in e for e in rapport["erreurs"])


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------

def test_pubmed_import_cree_evidences_idempotentes(
    db_session: Session, monkeypatch
):
    def faux_telecharger(self, url, params=None):
        if "esearch" in url:
            return ESEARCH_MOCK
        return ESUMMARY_MOCK

    monkeypatch.setattr(PubMedConnecteur, "_telecharger", faux_telecharger)
    connecteur = registry.get_connecteur("pubmed")

    rapport1 = connecteur.importer(
        db_session, {"requete": "artemisia", "max": 2}
    )
    db_session.commit()
    assert rapport1["crees"] == 2
    assert rapport1["erreurs"] == []

    evidences = db_session.query(Evidence).all()
    assert len(evidences) == 2
    par_pmid = {e.pmid: e for e in evidences}
    assert par_pmid["111"].title == "Artemisinin and malaria"
    assert par_pmid["111"].doi == "10.1000/test111"
    assert par_pmid["111"].year == 2021
    assert par_pmid["111"].authors == ["Dupont A", "Martin B"]
    assert par_pmid["111"].url == "https://pubmed.ncbi.nlm.nih.gov/111/"
    assert par_pmid["222"].doi is None

    provs = db_session.query(ImportProvenance).all()
    assert len(provs) == 2
    assert all(p.connecteur_nom == "pubmed" for p in provs)
    assert all(p.version_donnees == "E-utilities" for p in provs)

    # Réimport : aucun doublon (idempotence PMID/DOI).
    rapport2 = connecteur.importer(
        db_session, {"requete": "artemisia", "max": 2}
    )
    db_session.commit()
    assert rapport2["crees"] == 0
    assert rapport2["ignores"] == 2
    assert db_session.query(Evidence).count() == 2


def test_pubmed_parametre_requete_obligatoire(db_session: Session):
    connecteur = registry.get_connecteur("pubmed")
    with pytest.raises(ParametresInvalides, match="requete"):
        connecteur.importer(db_session, {})


# ---------------------------------------------------------------------------
# Réseau indisponible / cache
# ---------------------------------------------------------------------------

def test_reseau_down_leve_connecteur_indisponible(
    db_session: Session, monkeypatch
):
    _mock_reseau_down(monkeypatch, PubChemConnecteur)
    connecteur = registry.get_connecteur("pubchem")
    with pytest.raises(ConnecteurIndisponible) as exc_info:
        connecteur.requete_json(db_session, "https://exemple.test/x")
    message = str(exc_info.value).lower()
    assert "injoignable" in message
    assert "aucune donnée" in message


def test_cache_evite_second_appel_http(
    db_session: Session, monkeypatch
):
    compteur = {"n": 0}
    _mock_pubchem_ok(monkeypatch, compteur)
    connecteur = registry.get_connecteur("pubchem")

    r1 = connecteur.requete_json(db_session, "https://exemple.test/y")
    db_session.commit()
    r2 = connecteur.requete_json(db_session, "https://exemple.test/y")
    db_session.commit()

    assert compteur["n"] == 1  # un seul vrai appel HTTP
    assert r1 == r2 == PUG_ASPIRINE
    assert db_session.query(ConnecteurCache).count() == 1


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

CLES_CONTRAT = {
    "nom",
    "description",
    "url",
    "licence",
    "licence_url",
    "conditions_utilisation",
    "version_connecteur",
    "statut",
    "dernier_import",
    "strategie_maj",
}


def test_api_get_contrat_cles_exactes(api_client):
    reponse = api_client.get("/api/science/connecteurs")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert set(corps.keys()) == {"connecteurs"}
    assert len(corps["connecteurs"]) == 5
    for fiche in corps["connecteurs"]:
        assert set(fiche.keys()) == CLES_CONTRAT, fiche.keys()
    noms = {f["nom"] for f in corps["connecteurs"]}
    assert noms == {"pubchem", "pubmed", "openalex", "chembl", "gbif"}
    pubchem = next(f for f in corps["connecteurs"] if f["nom"] == "pubchem")
    assert pubchem["licence"]  # licence obligatoire exposée
    assert pubchem["statut"] == "actif"


def test_api_post_import_pubchem_ok(api_client, monkeypatch):
    _mock_pubchem_ok(monkeypatch)
    reponse = api_client.post(
        "/api/science/connecteurs/pubchem/importer",
        json={"parametres": {"nom": "aspirine"}},
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["rapport"]["crees"] == 1
    assert corps["provenance"]["connecteur"] == "pubchem"
    assert corps["provenance"]["version_donnees"] == "PUG-REST"
    assert "avertissement" in corps

    # dernier_import renseigné après un import réussi.
    reponse2 = api_client.get("/api/science/connecteurs")
    pubchem = next(
        f for f in reponse2.json()["connecteurs"] if f["nom"] == "pubchem"
    )
    assert pubchem["dernier_import"] is not None


def test_api_post_404_connecteur_inconnu(api_client):
    reponse = api_client.post(
        "/api/science/connecteurs/inexistant/importer",
        json={"parametres": {}},
    )
    assert reponse.status_code == 404
    assert "inexistant" in reponse.json()["detail"]


def test_api_post_422_parametres_invalides(api_client):
    reponse = api_client.post(
        "/api/science/connecteurs/pubchem/importer",
        json={"parametres": {}},
    )
    assert reponse.status_code == 422


def test_api_post_503_reseau_down_explicite(api_client, monkeypatch):
    _mock_reseau_down(monkeypatch, PubChemConnecteur)
    reponse = api_client.post(
        "/api/science/connecteurs/pubchem/importer",
        json={"parametres": {"nom": "aspirine"}},
    )
    assert reponse.status_code == 503
    assert "injoignable" in reponse.json()["detail"].lower()


def test_base_connecteur_attributs_contractuels():
    for classe in registry.lister_connecteurs():
        for attr in (
            "NOM", "DESCRIPTION", "URL_SOURCE", "LICENCE", "LICENCE_URL",
            "CONDITIONS_UTILISATION", "VERSION", "STRATEGIE_MAJ",
        ):
            assert getattr(classe, attr), f"{classe.NOM}.{attr} manquant"
        assert issubclass(classe, BaseConnecteur)


# ---------------------------------------------------------------------------
# OpenAlex (v2.2)
# ---------------------------------------------------------------------------

from webintel.science.connecteurs.plugins.openalex import OpenAlexConnecteur  # noqa: E402
from webintel.science.connecteurs.plugins.chembl import ChEMBLConnecteur  # noqa: E402
from webintel.science.connecteurs.plugins.gbif import GBIFConnecteur  # noqa: E402

OPENALEX_WORKS = {
    "meta": {"count": 2},
    "results": [
        {
            "id": "https://openalex.org/W2741809807",
            "doi": "https://doi.org/10.1234/test-doi",
            "title": "Artemisinin pharmacology review",
            "publication_year": 2020,
            "authorships": [{"author": {"display_name": "Dupont A"}}],
            "primary_location": {"source": {"display_name": "Journal Test"}},
            "type": "article",
            "language": "en",
            "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345/"},
        },
        {
            "id": "https://openalex.org/W999",
            "doi": None,
            "title": "Thèse locale sur les plantes médicinales",
            "publication_year": 2019,
            "authorships": [],
            "primary_location": {"source": {"display_name": "Université Test"}},
            "type": "dissertation",
            "language": "fr",
            "pmids": [],
        },
    ],
}


def _mock_openalex_ok(monkeypatch):
    def faux_telecharger(self, url, params=None):
        return OPENALEX_WORKS

    monkeypatch.setattr(OpenAlexConnecteur, "_telecharger", faux_telecharger)


def test_openalex_import_donnees_bibliographiques(db_session: Session, monkeypatch):
    _mock_openalex_ok(monkeypatch)
    connecteur = registry.get_connecteur("openalex")
    rapport = connecteur.importer(db_session, {"requete": "artemisinin"})
    assert rapport["crees"] == 2
    assert rapport["erreurs"] == []
    evidences = db_session.query(Evidence).all()
    assert len(evidences) == 2
    avec_doi = next(e for e in evidences if e.doi == "10.1234/test-doi")
    assert avec_doi.pmid == "12345"
    assert avec_doi.document_type == "article"
    assert avec_doi.provenance["identifiant_externe"] == "OpenAlex:W2741809807"
    # Licence CC0 en provenance.
    prov = db_session.query(ImportProvenance).filter(
        ImportProvenance.connecteur_nom == "openalex"
    ).all()
    assert len(prov) == 2
    assert "CC0" in (prov[0].licence or "")


def test_openalex_sans_doi_ni_pmid_pas_de_wbf(db_session: Session, monkeypatch):
    # Décision documentée : une référence OpenAlex sans DOI/PMID possède
    # déjà un identifiant externe (OpenAlex ID) — aucun identifiant
    # interne WBF n'est généré (réservé aux documents sans AUCUN
    # identifiant externe, ex. thèses locales saisies à la main).
    _mock_openalex_ok(monkeypatch)
    connecteur = registry.get_connecteur("openalex")
    connecteur.importer(db_session, {"requete": "plantes"})
    these = (
        db_session.query(Evidence)
        .filter(Evidence.title == "Thèse locale sur les plantes médicinales")
        .one()
    )
    assert these.doi is None and these.pmid is None
    assert these.document_type == "these"  # « dissertation » OpenAlex
    assert these.identifiant_interne is None
    assert these.provenance["identifiant_externe"] == "OpenAlex:W999"
    assert these.url == "https://openalex.org/W999"
    from webintel.science import litterature

    citation = litterature.reference_citee(these)
    assert "ne pas citer comme DOI/PMID" in citation
    assert "DOI:" not in citation and "PMID:" not in citation


def test_openalex_idempotent(db_session: Session, monkeypatch):
    _mock_openalex_ok(monkeypatch)
    connecteur = registry.get_connecteur("openalex")
    r1 = connecteur.importer(db_session, {"requete": "artemisinin"})
    assert r1["crees"] == 2
    r2 = connecteur.importer(db_session, {"requete": "artemisinin"})
    assert r2["crees"] == 0
    assert r2["ignores"] == 2
    assert db_session.query(Evidence).count() == 2


def test_openalex_parametres_invalides():
    connecteur = OpenAlexConnecteur()
    with pytest.raises(ParametresInvalides, match="requete"):
        connecteur.importer(None, {})


def test_openalex_timeout_devient_indisponible_explicite(db_session: Session, monkeypatch):
    def faux_timeout(self, url, params=None):
        raise httpx.ConnectTimeout("timeout (mock)")

    monkeypatch.setattr(OpenAlexConnecteur, "_telecharger", faux_timeout)
    connecteur = registry.get_connecteur("openalex")
    with pytest.raises(ConnecteurIndisponible, match="injoignable"):
        connecteur.importer(db_session, {"requete": "x"})
    assert db_session.query(Evidence).count() == 0  # aucune donnée importée


def test_openalex_429_puis_succes(db_session: Session, monkeypatch):
    import webintel.security.pinned_transport as transport_module

    appels = {"n": 0}
    sleeps = []

    def faux_requete_sure(methode, url, **kwargs):
        appels["n"] += 1
        requete = httpx.Request(methode, url)
        if appels["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=requete)
        return httpx.Response(200, json=OPENALEX_WORKS, request=requete)

    monkeypatch.setattr(transport_module, "requete_sure", faux_requete_sure)
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    connecteur = registry.get_connecteur("openalex")
    rapport = connecteur.importer(db_session, {"requete": "artemisinin"})
    assert appels["n"] == 2  # 429 puis succès
    assert sleeps and max(sleeps) <= 30  # Retry-After borné à 30 s
    assert rapport["crees"] == 2


def test_openalex_500_devient_indisponible_explicite(db_session: Session, monkeypatch):
    import webintel.security.pinned_transport as transport_module

    def faux_500(methode, url, **kwargs):
        return httpx.Response(500, request=httpx.Request(methode, url))

    monkeypatch.setattr(transport_module, "requete_sure", faux_500)
    connecteur = registry.get_connecteur("openalex")
    with pytest.raises(ConnecteurIndisponible, match="HTTP 500"):
        connecteur.importer(db_session, {"requete": "x"})


def test_openalex_max_plafonne_a_20(db_session: Session, monkeypatch):
    # Le paramètre « max » est plafonné à 20 (pas d'erreur, pas de
    # moissonnage) : on vérifie le per-page réellement envoyé.
    per_page_vus = []

    def faux_telecharger(self, url, params=None):
        per_page_vus.append((params or {}).get("per-page"))
        return OPENALEX_WORKS

    monkeypatch.setattr(OpenAlexConnecteur, "_telecharger", faux_telecharger)
    connecteur = registry.get_connecteur("openalex")
    rapport = connecteur.importer(db_session, {"requete": "x", "max": 25})
    assert per_page_vus == ["20"]
    assert rapport["crees"] == 2


def test_api_openalex_import_ok(api_client, monkeypatch):
    _mock_openalex_ok(monkeypatch)
    reponse = api_client.post(
        "/api/science/connecteurs/openalex/importer",
        json={"parametres": {"requete": "artemisinin"}},
    )
    assert reponse.status_code == 200
    assert reponse.json()["rapport"]["crees"] == 2


def test_api_openalex_503_reseau_down(api_client, monkeypatch):
    _mock_reseau_down(monkeypatch, OpenAlexConnecteur)
    reponse = api_client.post(
        "/api/science/connecteurs/openalex/importer",
        json={"parametres": {"requete": "x"}},
    )
    assert reponse.status_code == 503


# ---------------------------------------------------------------------------
# ChEMBL (v2.2)
# ---------------------------------------------------------------------------

CHEMBL_MOLECULE = {
    "molecule_chembl_id": "CHEMBL50",
    "pref_name": "QUERCETIN",
    "max_phase": 3.0,
    "molecule_structures": {
        "canonical_smiles": "C1=CC(=C(C=C1C2=C(C(=O)C(=CC2=O)O)O)O)O",
        "standard_inchi": "InChI=1S/C15H10O7/c16-8-4-11(19)...",
        "standard_inchi_key": "REFJWTPEDVJJIY-UHFFFAOYSA-N",
    },
    "molecule_properties": {
        "full_molformula": "C15H10O7",
        "full_mwt": "302.24",
    },
}


def _mock_chembl(monkeypatch, *, recherche=None, direct=None, nb_activites=3):
    def faux_telecharger(self, url, params=None):
        if "/molecule.json" in url:
            return {"molecules": recherche if recherche is not None else []}
        if "/activity.json" in url:
            return {"page_meta": {"total_count": nb_activites}}
        if "/molecule/CHEMBL" in url:
            if direct is None:
                reponse = httpx.Response(404, request=httpx.Request("GET", url))
                raise httpx.HTTPStatusError(
                    "Not Found", request=reponse.request, response=reponse
                )
            return direct
        raise AssertionError(f"URL inattendue : {url}")

    monkeypatch.setattr(ChEMBLConnecteur, "_telecharger", faux_telecharger)


def test_chembl_import_par_nom_cree_compound(db_session: Session, monkeypatch):
    _mock_chembl(monkeypatch, recherche=[CHEMBL_MOLECULE])
    connecteur = registry.get_connecteur("chembl")
    rapport = connecteur.importer(db_session, {"nom": "quercetine"})
    assert rapport["crees"] == 1
    assert rapport["erreurs"] == []
    compose = db_session.query(Compound).one()
    assert compose.formula == "C15H10O7"
    assert compose.inchikey == "REFJWTPEDVJJIY-UHFFFAOYSA-N"
    assert compose.external_ids["chembl"]["chembl_id"] == "CHEMBL50"
    assert rapport["provenance"]["licence"] == connecteur.LICENCE
    # Le compte d'activités est signalé, pas les valeurs.
    assert any("activité" in a for a in rapport["avertissements"])


def test_chembl_import_par_id(db_session: Session, monkeypatch):
    _mock_chembl(monkeypatch, direct=CHEMBL_MOLECULE)
    connecteur = registry.get_connecteur("chembl")
    rapport = connecteur.importer(db_session, {"chembl_id": "chembl50"})
    assert rapport["crees"] == 1
    assert db_session.query(Compound).one().external_ids["chembl"]["chembl_id"] == "CHEMBL50"


def test_chembl_idempotent(db_session: Session, monkeypatch):
    _mock_chembl(monkeypatch, recherche=[CHEMBL_MOLECULE])
    connecteur = registry.get_connecteur("chembl")
    assert connecteur.importer(db_session, {"nom": "quercetine"})["crees"] == 1
    r2 = connecteur.importer(db_session, {"nom": "quercetine"})
    assert r2["crees"] == 0 and r2["ignores"] == 1
    assert db_session.query(Compound).count() == 1


def test_chembl_nom_ambigu_refuse_de_deviner(db_session: Session, monkeypatch):
    _mock_chembl(monkeypatch, recherche=[CHEMBL_MOLECULE, dict(CHEMBL_MOLECULE)])
    connecteur = registry.get_connecteur("chembl")
    rapport = connecteur.importer(db_session, {"nom": "quercetine"})
    assert rapport["ignores"] == 1
    assert any("ambigu" in e for e in rapport["erreurs"])
    assert db_session.query(Compound).count() == 0


def test_chembl_id_introuvable_404(db_session: Session, monkeypatch):
    _mock_chembl(monkeypatch, direct=None)
    connecteur = registry.get_connecteur("chembl")
    rapport = connecteur.importer(db_session, {"chembl_id": "CHEMBL999999"})
    assert rapport["ignores"] == 1
    assert any("introuvable" in e for e in rapport["erreurs"])


def test_chembl_parametres_invalides():
    connecteur = ChEMBLConnecteur()
    with pytest.raises(ParametresInvalides):
        connecteur.importer(None, {})


def test_chembl_timeout_devient_indisponible(db_session: Session, monkeypatch):
    def faux_timeout(self, url, params=None):
        raise httpx.ConnectTimeout("timeout (mock)")

    monkeypatch.setattr(ChEMBLConnecteur, "_telecharger", faux_timeout)
    connecteur = registry.get_connecteur("chembl")
    with pytest.raises(ConnecteurIndisponible, match="injoignable"):
        connecteur.importer(db_session, {"nom": "x"})
    assert db_session.query(Compound).count() == 0


def test_api_chembl_import_ok(api_client, monkeypatch):
    _mock_chembl(monkeypatch, recherche=[CHEMBL_MOLECULE])
    reponse = api_client.post(
        "/api/science/connecteurs/chembl/importer",
        json={"parametres": {"nom": "quercetine"}},
    )
    assert reponse.status_code == 200
    assert reponse.json()["rapport"]["crees"] == 1


# ---------------------------------------------------------------------------
# GBIF (v2.2)
# ---------------------------------------------------------------------------

from webintel.science.models import Plant  # noqa: E402

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

GBIF_SYNONYME = {
    "usageKey": 5354381,
    "scientificName": "Cassia occidentalis L.",
    "canonicalName": "Cassia occidentalis",
    "rank": "SPECIES",
    "status": "SYNONYM",
    "confidence": 97,
    "matchType": "EXACT",
    "family": "Fabaceae",
    "acceptedUsageKey": 2957966,
}


def _mock_gbif(monkeypatch, *, match=None, occurrences=12):
    def faux_telecharger(self, url, params=None):
        if "/species/match" in url:
            return dict(match) if match is not None else {"matchType": "NONE"}
        if "/species/2957966" in url:
            return {"canonicalName": "Senna occidentalis"}
        if "/occurrence/search" in url:
            return {"count": occurrences}
        raise AssertionError(f"URL inattendue : {url}")

    monkeypatch.setattr(GBIFConnecteur, "_telecharger", faux_telecharger)


def test_gbif_import_taxonomie_et_occurrences(db_session: Session, monkeypatch):
    _mock_gbif(monkeypatch, match=GBIF_MATCH, occurrences=12)
    connecteur = registry.get_connecteur("gbif")
    rapport = connecteur.importer(
        db_session, {"nom_scientifique": "Artemisia annua", "pays": "bf"}
    )
    assert rapport["crees"] == 1
    assert rapport["avertissements"] == []
    plante = db_session.query(Plant).one()
    assert plante.scientific_name == "Artemisia annua"
    assert plante.taxonomy["gbif_family"] == "Asteraceae"
    gbif = plante.external_ids["gbif"]
    assert gbif["taxon_key"] == 8373355
    assert gbif["statut"] == "ACCEPTED"
    assert gbif["occurrences_pays"] == 12 and gbif["occurrences_total"] == 12


def test_gbif_synonyme_signale_pas_corrige(db_session: Session, monkeypatch):
    _mock_gbif(monkeypatch, match=GBIF_SYNONYME)
    connecteur = registry.get_connecteur("gbif")
    rapport = connecteur.importer(db_session, {"nom_scientifique": "Cassia occidentalis"})
    assert rapport["crees"] == 1
    plante = db_session.query(Plant).one()
    assert plante.scientific_name == "Cassia occidentalis"  # nom saisi conservé tel quel
    assert any("SYNONYM" in a and "Senna occidentalis" in a for a in rapport["avertissements"])


def test_gbif_sans_appariement(db_session: Session, monkeypatch):
    _mock_gbif(monkeypatch, match=None)
    connecteur = registry.get_connecteur("gbif")
    rapport = connecteur.importer(db_session, {"nom_scientifique": "Planta ficta"})
    assert rapport["ignores"] == 1
    assert any("Aucun taxon" in e for e in rapport["erreurs"])
    assert db_session.query(Plant).count() == 0


def test_gbif_pays_invalide():
    connecteur = GBIFConnecteur()
    with pytest.raises(ParametresInvalides, match="pays"):
        connecteur.importer(None, {"nom_scientifique": "Artemisia annua", "pays": "BFA"})


def test_gbif_timeout_devient_indisponible(db_session: Session, monkeypatch):
    def faux_timeout(self, url, params=None):
        raise httpx.ConnectTimeout("timeout (mock)")

    monkeypatch.setattr(GBIFConnecteur, "_telecharger", faux_timeout)
    connecteur = registry.get_connecteur("gbif")
    with pytest.raises(ConnecteurIndisponible, match="injoignable"):
        connecteur.importer(db_session, {"nom_scientifique": "x"})
    assert db_session.query(Plant).count() == 0


def test_api_gbif_import_ok(api_client, monkeypatch):
    _mock_gbif(monkeypatch, match=GBIF_MATCH)
    reponse = api_client.post(
        "/api/science/connecteurs/gbif/importer",
        json={"parametres": {"nom_scientifique": "Artemisia annua"}},
    )
    assert reponse.status_code == 200
    assert reponse.json()["rapport"]["crees"] == 1
