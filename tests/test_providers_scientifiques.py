"""Tests — providers scientifiques sans clé (v3.1.0).

ZÉRO réseau réel : tous les appels HTTP passent par des
``httpx.MockTransport`` injectés. Aucun appel live n'est exercé ici.
"""

from __future__ import annotations

import httpx

from webintel.providers.base import ProviderErreur
from webintel.providers.registre import construire_registre, rechercher_avec_fallback
from webintel.providers.scientifique import (
    ChEMBLProvider,
    CrossrefProvider,
    EuropePmcProvider,
    GbifProvider,
    OpenAlexProvider,
    PubChemProvider,
    PubMedProvider,
    SemanticScholarProvider,
)

CLASSES = [
    PubMedProvider,
    OpenAlexProvider,
    EuropePmcProvider,
    CrossrefProvider,
    SemanticScholarProvider,
    PubChemProvider,
    ChEMBLProvider,
    GbifProvider,
]

NOMS_ATTENDUS = [
    "pubmed",
    "openalex",
    "europepmc",
    "crossref",
    "semanticscholar",
    "pubchem",
    "chembl",
    "gbif",
]


def _transport(gestionnaire) -> httpx.MockTransport:
    return httpx.MockTransport(gestionnaire)


def _reponse_json(donnees, statut: int = 200) -> httpx.Response:
    return httpx.Response(statut, json=donnees)


# ---------------------------------------------------------------------------
# Fixtures réalistes minimales
# ---------------------------------------------------------------------------

PUBMED_ESEARCH = {
    "header": {"type": "esearch", "version": "0.3"},
    "esearchresult": {"count": "2", "idlist": ["123", "456"]},
}

PUBMED_ESUMMARY = {
    "header": {"type": "esummary", "version": "0.3"},
    "result": {
        "uids": ["123", "456"],
        "123": {
            "uid": "123",
            "title": "Quercetin and inflammation.",
            "authors": [{"name": "Dupont A"}, {"name": "Martin B"}],
            "pubdate": "2024 Jan",
            "source": "J Ethnopharmacol",
        },
        "456": {
            "uid": "456",
            "title": "Flavonoids review",
            "authors": [],
            "pubdate": "2023",
            "source": "Nutrients",
        },
    },
}

OPENALEX_REPONSE = {
    "results": [
        {
            "id": "https://openalex.org/W123",
            "doi": "https://doi.org/10.1234/test",
            "title": "Quercetin review",
            "publication_year": 2023,
            "authorships": [{"author": {"display_name": "Smith J"}}],
            "abstract_inverted_index": {
                "Quercetin": [0],
                "is": [1],
                "a": [2],
                "flavonoid": [3],
            },
            "primary_location": {"source": {"display_name": "Nutrients"}},
        }
    ]
}

EUROPEPMC_REPONSE = {
    "resultList": {
        "result": [
            {
                "title": "Quercetin effects",
                "authorString": "Dupont A, Martin B",
                "journalInfo": {"journal": {"title": "JEP"}},
                "doi": "10.1234/epmc",
                "pubYear": "2022",
            }
        ]
    }
}

CROSSREF_REPONSE = {
    "message": {
        "items": [
            {
                "title": ["Crossref paper"],
                "DOI": "10.1234/cr",
                "URL": "https://doi.org/10.1234/cr",
                "author": [{"given": "Alice", "family": "Dupont"}],
                "published": {"date-parts": [[2021, 5]]},
                "abstract": "<jats:p>An <b>abstract</b>.</jats:p>",
            }
        ]
    }
}

SEMANTICSCHOLAR_REPONSE = {
    "data": [
        {
            "title": "S2 paper",
            "authors": [{"name": "Bob Martin"}],
            "year": 2020,
            "abstract": "Abstract text.",
            "url": "https://www.semanticscholar.org/paper/abc",
            "externalIds": {"DOI": "10.1234/s2"},
        }
    ]
}

PUBCHEM_REPONSE = {
    "PropertyTable": {
        "Properties": [
            {
                "CID": 5280343,
                "Title": "Quercetin",
                "MolecularFormula": "C15H10O7",
                "MolecularWeight": "302.23",
                "CanonicalSMILES": "C1=CC=C(C=C1)",
                "IUPACName": "2-(3,4-dihydroxyphenyl)-3,5,7-trihydroxychromen-4-one",
            }
        ]
    }
}

CHEMBL_REPONSE = {
    "molecules": [
        {
            "molecule_chembl_id": "CHEMBL50",
            "pref_name": "QUERCETIN",
            "molecule_structures": {"canonical_smiles": "C1=CC=C(C=C1)"},
            "molecule_properties": {"full_molformula": "C15H10O7", "full_mwt": "302.24"},
        }
    ]
}

GBIF_REPONSE = {
    "results": [
        {
            "scientificName": "Vitellaria paradoxa",
            "key": 2886750,
            "rank": "SPECIES",
            "kingdom": "Plantae",
            "phylum": "Tracheophyta",
            "family": "Sapotaceae",
            "canonicalName": "Vitellaria paradoxa",
        }
    ]
}


def _gestionnaire_par_hote(request: httpx.Request) -> httpx.Response:
    hote = request.url.host
    chemin = request.url.path
    if hote == "eutils.ncbi.nlm.nih.gov":
        if "esearch.fcgi" in chemin:
            return _reponse_json(PUBMED_ESEARCH)
        return _reponse_json(PUBMED_ESUMMARY)
    if hote == "api.openalex.org":
        return _reponse_json(OPENALEX_REPONSE)
    if hote == "www.ebi.ac.uk":
        if "/chembl/" in chemin:
            return _reponse_json(CHEMBL_REPONSE)
        return _reponse_json(EUROPEPMC_REPONSE)
    if hote == "api.crossref.org":
        return _reponse_json(CROSSREF_REPONSE)
    if hote == "api.semanticscholar.org":
        return _reponse_json(SEMANTICSCHOLAR_REPONSE)
    if hote == "pubchem.ncbi.nlm.nih.gov":
        return _reponse_json(PUBCHEM_REPONSE)
    if hote == "api.gbif.org":
        return _reponse_json(GBIF_REPONSE)
    return _reponse_json({})


# ---------------------------------------------------------------------------
# Succès par provider
# ---------------------------------------------------------------------------

def test_pubmed_succes():
    p = PubMedProvider(transport=_transport(_gestionnaire_par_hote))
    resultats = p.rechercher("quercetin", limite=2)
    assert len(resultats) == 2
    r = resultats[0]
    assert r.titre == "Quercetin and inflammation"
    assert r.url == "https://pubmed.ncbi.nlm.nih.gov/123/"
    assert "Dupont A" in r.extrait and "J Ethnopharmacol" in r.extrait
    assert r.source == "pubmed" and r.type == "publication"


def test_openalex_succes_et_resume_reconstruit():
    p = OpenAlexProvider(transport=_transport(_gestionnaire_par_hote))
    resultats = p.rechercher("quercetin")
    assert len(resultats) == 1
    r = resultats[0]
    assert r.titre == "Quercetin review"
    assert r.url == "https://doi.org/10.1234/test"
    assert "Quercetin is a flavonoid" in r.extrait
    assert r.source == "openalex" and r.type == "publication"


def test_europepmc_succes():
    p = EuropePmcProvider(transport=_transport(_gestionnaire_par_hote))
    resultats = p.rechercher("quercetin")
    assert len(resultats) == 1
    r = resultats[0]
    assert r.titre == "Quercetin effects"
    assert r.url == "https://doi.org/10.1234/epmc"
    assert "JEP" in r.extrait
    assert r.source == "europepmc" and r.type == "publication"


def test_crossref_succes_html_nettoye():
    p = CrossrefProvider(transport=_transport(_gestionnaire_par_hote))
    resultats = p.rechercher("quercetin")
    assert len(resultats) == 1
    r = resultats[0]
    assert r.titre == "Crossref paper"
    assert "<jats:p>" not in r.extrait and "<b>" not in r.extrait
    assert "abstract" in r.extrait
    assert r.source == "crossref" and r.type == "publication"


def test_semanticscholar_succes():
    p = SemanticScholarProvider(transport=_transport(_gestionnaire_par_hote))
    resultats = p.rechercher("quercetin")
    assert len(resultats) == 1
    r = resultats[0]
    assert r.titre == "S2 paper"
    assert r.url == "https://www.semanticscholar.org/paper/abc"
    assert r.source == "semanticscholar" and r.type == "publication"


def test_pubchem_succes():
    p = PubChemProvider(transport=_transport(_gestionnaire_par_hote))
    resultats = p.rechercher("quercetin")
    assert len(resultats) == 1
    r = resultats[0]
    assert "Quercetin" in r.titre and "5280343" in r.titre
    assert r.url == "https://pubchem.ncbi.nlm.nih.gov/compound/5280343"
    assert "C15H10O7" in r.extrait and "302.23" in r.extrait
    assert r.source == "pubchem" and r.type == "chimie"


def test_chembl_succes():
    p = ChEMBLProvider(transport=_transport(_gestionnaire_par_hote))
    resultats = p.rechercher("quercetin")
    assert len(resultats) == 1
    r = resultats[0]
    assert "CHEMBL50" in r.titre
    assert r.url == "https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL50/"
    assert "C15H10O7" in r.extrait
    assert r.source == "chembl" and r.type == "chimie"


def test_gbif_succes():
    p = GbifProvider(transport=_transport(_gestionnaire_par_hote))
    resultats = p.rechercher("karite")
    assert len(resultats) == 1
    r = resultats[0]
    assert r.titre == "Vitellaria paradoxa"
    assert r.url == "https://www.gbif.org/species/2886750"
    assert "Sapotaceae" in r.extrait
    assert r.source == "gbif" and r.type == "biodiversite"


# ---------------------------------------------------------------------------
# Erreurs : 404 → [], HTTP → ProviderErreur, timeout → ProviderErreur
# ---------------------------------------------------------------------------

def test_pubchem_404_liste_vide():
    def gestionnaire(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"Fault": {"Code": "PUGREST.NotFound"}})

    p = PubChemProvider(transport=_transport(gestionnaire))
    assert p.rechercher("xyzinonexistant123") == []


def test_pubmed_aucun_resultat_liste_vide():
    def gestionnaire(request: httpx.Request) -> httpx.Response:
        return _reponse_json(
            {"header": {}, "esearchresult": {"count": "0", "idlist": []}}
        )

    p = PubMedProvider(transport=_transport(gestionnaire))
    assert p.rechercher("zzzintrouvable") == []


def test_openalex_http500_provider_erreur():
    def gestionnaire(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    p = OpenAlexProvider(transport=_transport(gestionnaire))
    try:
        p.rechercher("quercetin")
        assert False, "aurait dû lever ProviderErreur"
    except ProviderErreur as exc:
        assert exc.type_erreur == "http"


def test_gbif_reponse_invalide():
    def gestionnaire(request: httpx.Request) -> httpx.Response:
        return _reponse_json(["pas", "un", "dict"])

    p = GbifProvider(transport=_transport(gestionnaire))
    try:
        p.rechercher("karite")
        assert False, "aurait dû lever ProviderErreur"
    except ProviderErreur as exc:
        assert exc.type_erreur == "reponse_invalide"


def test_chembl_timeout():
    def gestionnaire(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("délai dépassé (test)")

    p = ChEMBLProvider(transport=_transport(gestionnaire))
    try:
        p.rechercher("quercetin", timeout_s=1)
        assert False, "aurait dû lever ProviderErreur"
    except ProviderErreur as exc:
        assert exc.type_erreur == "timeout"


# ---------------------------------------------------------------------------
# Requête vide : aucun appel réseau, liste vide
# ---------------------------------------------------------------------------

def test_requete_vide_aucun_appel():
    appels = []

    def gestionnaire(request: httpx.Request) -> httpx.Response:
        appels.append(request.url)
        return _reponse_json({})

    transport = _transport(gestionnaire)
    for classe in CLASSES:
        assert classe(transport=transport).rechercher("   ") == []
        assert classe(transport=transport).rechercher("") == []
    assert appels == []


# ---------------------------------------------------------------------------
# Registre : ordre, capacités, filtrage par capacité
# ---------------------------------------------------------------------------

def test_registre_contient_les_8_apres_les_generalistes():
    registre = construire_registre(transport=_transport(_gestionnaire_par_hote))
    noms = [p.nom for p in registre]
    assert noms[:8] == [
        "brave", "tavily", "serpapi", "wikipedia",
        "duckduckgo", "rss_actualites", "newsapi", "github",
    ]
    assert noms[8:] == NOMS_ATTENDUS


def test_capacites_scientifiques():
    par_nom = {p.nom: p for p in construire_registre()}
    for nom in ("pubmed", "openalex", "europepmc", "crossref", "semanticscholar"):
        assert par_nom[nom].capacites == ["recherche_scientifique", "publications"], nom
    for nom in ("pubchem", "chembl"):
        assert par_nom[nom].capacites == ["recherche_scientifique", "chimie"], nom
    assert par_nom["gbif"].capacites == ["recherche_scientifique", "biodiversite"]
    for nom in NOMS_ATTENDUS:
        assert par_nom[nom].necessite_cle is False, nom
        assert par_nom[nom].statut()["statut"] == "pret", nom


def test_fallback_capacite_scientifique_uniquement():
    registre = construire_registre(transport=_transport(_gestionnaire_par_hote))
    sortie = rechercher_avec_fallback(
        "quercetin",
        limite=20,
        providers=registre,
        capacite="recherche_scientifique",
        timeout_s=5,
        budget_s=60,
    )
    assert sortie["providers_utilises"] == NOMS_ATTENDUS
    sources = {r["source"] for r in sortie["resultats"]}
    assert sources <= set(NOMS_ATTENDUS), sources
    assert len(sortie["resultats"]) >= 8
