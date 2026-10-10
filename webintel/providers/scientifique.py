"""Providers scientifiques SANS CLÉ (v3.1.0).

Huit sources publiques, gratuites et utilisables sans inscription,
pour des recherches « pointues » exploitables par un chercheur
(littérature biomédicale, chimie, biodiversité) :

- littérature / publications : PubMed (E-utilities NCBI), OpenAlex,
  Europe PMC, Crossref, Semantic Scholar ;
- chimie : PubChem (PUG-REST), ChEMBL ;
- biodiversité : GBIF (espèces).

Discipline identique aux autres providers : hôtes codés en dur
(anti-SSRF), ``self._get_json()`` uniquement, jamais de résultat
inventé (échec → ``ProviderErreur``, « non trouvé » → liste vide),
messages en français, extraits nettoyés et bornés. Aucune clé sur
aucun des huit — les quotas publics s'appliquent (requêtes
raisonnables, pas de ``mailto`` factice).
"""

from __future__ import annotations

from urllib.parse import quote

from webintel.providers.base import (
    BaseProvider,
    ProviderErreur,
    ResultatProvider,
    nettoyer_extrait,
)


def _requete_propre(requete: str | None) -> str:
    return " ".join((requete or "").split())


def _auteurs_texte(auteurs: list, *, maximum: int = 5) -> str:
    """Noms d'auteurs bornés (« A, B, C et al. ») pour les extraits."""
    noms = [str(a).strip() for a in auteurs if str(a).strip()]
    if not noms:
        return ""
    if len(noms) > maximum:
        return ", ".join(noms[:maximum]) + " et al."
    return ", ".join(noms)


def _resume_depuis_index_inverse(index) -> str:
    """Reconstruit le texte d'un résumé OpenAlex (``abstract_inverted_index``).

    OpenAlex renvoie ``{mot: [positions]}`` ; on replace chaque mot à
    sa position. Résumé borné à 400 mots (les extraits sont re-bornés
    à 500 caractères par ``nettoyer_extrait``).
    """
    if not isinstance(index, dict) or not index:
        return ""
    try:
        positions: dict[int, str] = {}
        for mot, liste_pos in index.items():
            for pos in liste_pos or []:
                positions[int(pos)] = str(mot)
        if not positions:
            return ""
        dernier = min(max(positions), 399)
        mots = [positions.get(i, "") for i in range(dernier + 1)]
        return " ".join(m for m in mots if m)
    except (TypeError, ValueError, AttributeError):
        return ""


class PubMedProvider(BaseProvider):
    """PubMed via NCBI E-utilities (esearch + esummary) — sans clé."""

    nom = "pubmed"
    capacites = ["recherche_scientifique", "publications"]
    necessite_cle = False
    hotes_autorises = frozenset({"eutils.ncbi.nlm.nih.gov"})

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        requete = _requete_propre(requete)
        if not requete:
            return []
        n = max(1, limite)
        recherche = self._get_json(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": requete,
                "retmode": "json",
                "retmax": n,
                "tool": "webintel",
            },
            timeout_s=timeout_s,
        )
        try:
            pmids = (recherche.get("esearchresult") or {}).get("idlist") or []
        except AttributeError as exc:
            raise ProviderErreur(
                "Provider « pubmed » : réponse inattendue (recherche). "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            ) from exc
        pmids = [str(p).strip() for p in pmids if str(p).strip()][:n]
        if not pmids:
            return []
        details = self._get_json(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={
                "db": "pubmed",
                "id": ",".join(pmids),
                "retmode": "json",
                "tool": "webintel",
            },
            timeout_s=timeout_s,
        )
        try:
            fiches = details.get("result") or {}
        except AttributeError as exc:
            raise ProviderErreur(
                "Provider « pubmed » : réponse inattendue (résumés). "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            ) from exc
        resultats: list[ResultatProvider] = []
        for pmid in pmids:
            fiche = fiches.get(pmid)
            if not isinstance(fiche, dict):
                continue
            titre = (fiche.get("title") or "").strip().rstrip(".")
            if not titre:
                continue
            auteurs = _auteurs_texte(
                [a.get("name") for a in (fiche.get("authors") or []) if isinstance(a, dict)]
            )
            journal = (fiche.get("source") or "").strip()
            date = (fiche.get("pubdate") or "").strip()
            morceaux = [m for m in (auteurs, journal, date) if m]
            resultats.append(
                ResultatProvider(
                    titre=titre,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    extrait=nettoyer_extrait(" · ".join(morceaux)),
                    source=self.nom,
                    date=date or None,
                    type="publication",
                )
            )
        return resultats


class OpenAlexProvider(BaseProvider):
    """OpenAlex (métadonnées savantes ouvertes) — sans clé."""

    nom = "openalex"
    capacites = ["recherche_scientifique", "publications"]
    necessite_cle = False
    hotes_autorises = frozenset({"api.openalex.org"})

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        requete = _requete_propre(requete)
        if not requete:
            return []
        donnees = self._get_json(
            "https://api.openalex.org/works",
            params={"search": requete, "per-page": max(1, limite)},
            timeout_s=timeout_s,
        )
        if not isinstance(donnees, dict):
            raise ProviderErreur(
                "Provider « openalex » : réponse inattendue. "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            )
        resultats: list[ResultatProvider] = []
        for travail in donnees.get("results") or []:
            if not isinstance(travail, dict):
                continue
            titre = (travail.get("title") or "").strip()
            if not titre:
                continue
            doi = (travail.get("doi") or "").strip()
            identifiant = (travail.get("id") or "").strip()
            url = doi or identifiant
            if not url:
                continue
            auteurs = _auteurs_texte(
                [
                    (a.get("author") or {}).get("display_name")
                    for a in (travail.get("authorships") or [])
                    if isinstance(a, dict)
                ]
            )
            annee = travail.get("publication_year")
            revue = (
                (travail.get("primary_location") or {}).get("source") or {}
            ).get("display_name") or ""
            resume = _resume_depuis_index_inverse(travail.get("abstract_inverted_index"))
            morceaux = [m for m in (auteurs, str(revue).strip(), str(annee or "").strip()) if m]
            extrait = " · ".join(morceaux)
            if resume:
                extrait = (extrait + " — " + resume) if extrait else resume
            resultats.append(
                ResultatProvider(
                    titre=titre,
                    url=url,
                    extrait=nettoyer_extrait(extrait),
                    source=self.nom,
                    date=str(annee) if annee else None,
                    type="publication",
                )
            )
        return resultats


class EuropePmcProvider(BaseProvider):
    """Europe PMC (littérature biomédicale, texte intégral libre) — sans clé."""

    nom = "europepmc"
    capacites = ["recherche_scientifique", "publications"]
    necessite_cle = False
    hotes_autorises = frozenset({"www.ebi.ac.uk"})

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        requete = _requete_propre(requete)
        if not requete:
            return []
        donnees = self._get_json(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": requete, "format": "json", "pageSize": max(1, limite)},
            timeout_s=timeout_s,
        )
        try:
            lignes = (donnees.get("resultList") or {}).get("result") or []
        except AttributeError as exc:
            raise ProviderErreur(
                "Provider « europepmc » : réponse inattendue. "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            ) from exc
        resultats: list[ResultatProvider] = []
        for ligne in lignes:
            if not isinstance(ligne, dict):
                continue
            titre = (ligne.get("title") or "").strip()
            if not titre:
                continue
            doi = (ligne.get("doi") or "").strip()
            pmid = (ligne.get("pmid") or "").strip()
            pmcid = (ligne.get("pmcid") or "").strip()
            if doi:
                url = f"https://doi.org/{doi}"
            elif pmid:
                url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            elif pmcid:
                url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
            else:
                continue
            auteurs = (ligne.get("authorString") or "").strip()
            revue = ((ligne.get("journalInfo") or {}).get("journal") or {}).get("title") or ""
            annee = str(ligne.get("pubYear") or "").strip()
            morceaux = [m for m in (auteurs, str(revue).strip(), annee) if m]
            resultats.append(
                ResultatProvider(
                    titre=titre,
                    url=url,
                    extrait=nettoyer_extrait(" · ".join(morceaux)),
                    source=self.nom,
                    date=annee or None,
                    type="publication",
                )
            )
        return resultats


class CrossrefProvider(BaseProvider):
    """Crossref (métadonnées DOI) — sans clé."""

    nom = "crossref"
    capacites = ["recherche_scientifique", "publications"]
    necessite_cle = False
    hotes_autorises = frozenset({"api.crossref.org"})

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        requete = _requete_propre(requete)
        if not requete:
            return []
        donnees = self._get_json(
            "https://api.crossref.org/works",
            params={
                "query": requete,
                "rows": max(1, limite),
                "select": "title,author,published,DOI,URL,abstract",
            },
            timeout_s=timeout_s,
        )
        try:
            lignes = (donnees.get("message") or {}).get("items") or []
        except AttributeError as exc:
            raise ProviderErreur(
                "Provider « crossref » : réponse inattendue. "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            ) from exc
        resultats: list[ResultatProvider] = []
        for ligne in lignes:
            if not isinstance(ligne, dict):
                continue
            titres = ligne.get("title") or []
            titre = (titres[0] if titres else "").strip()
            if not titre:
                continue
            doi = (ligne.get("DOI") or "").strip()
            url = (ligne.get("URL") or "").strip() or (f"https://doi.org/{doi}" if doi else "")
            if not url:
                continue
            auteurs = _auteurs_texte(
                [
                    " ".join(
                        p for p in (a.get("given"), a.get("family")) if p
                    ).strip()
                    for a in (ligne.get("author") or [])
                    if isinstance(a, dict)
                ]
            )
            publie = ligne.get("published") or {}
            parties = publie.get("date-parts") or []
            annee = str(parties[0][0]) if parties and parties[0] else ""
            morceaux = [m for m in (auteurs, annee) if m]
            extrait = " · ".join(morceaux)
            resume = (ligne.get("abstract") or "").strip()
            if resume:
                extrait = (extrait + " — " + resume) if extrait else resume
            resultats.append(
                ResultatProvider(
                    titre=titre,
                    url=url,
                    extrait=nettoyer_extrait(extrait),
                    source=self.nom,
                    date=annee or None,
                    type="publication",
                )
            )
        return resultats


class SemanticScholarProvider(BaseProvider):
    """Semantic Scholar (papiers, citations) — sans clé (quota public)."""

    nom = "semanticscholar"
    capacites = ["recherche_scientifique", "publications"]
    necessite_cle = False
    hotes_autorises = frozenset({"api.semanticscholar.org"})

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        requete = _requete_propre(requete)
        if not requete:
            return []
        donnees = self._get_json(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": requete,
                "limit": max(1, limite),
                "fields": "title,authors,year,abstract,url,externalIds",
            },
            timeout_s=timeout_s,
        )
        if not isinstance(donnees, dict):
            raise ProviderErreur(
                "Provider « semanticscholar » : réponse inattendue. "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            )
        resultats: list[ResultatProvider] = []
        for papier in donnees.get("data") or []:
            if not isinstance(papier, dict):
                continue
            titre = (papier.get("title") or "").strip()
            if not titre:
                continue
            url = (papier.get("url") or "").strip()
            doi = ((papier.get("externalIds") or {}).get("DOI") or "").strip()
            if not url and doi:
                url = f"https://doi.org/{doi}"
            if not url:
                continue
            auteurs = _auteurs_texte(
                [
                    a.get("name")
                    for a in (papier.get("authors") or [])
                    if isinstance(a, dict)
                ]
            )
            annee = papier.get("year")
            morceaux = [m for m in (auteurs, str(annee or "").strip()) if m]
            extrait = " · ".join(morceaux)
            resume = (papier.get("abstract") or "").strip()
            if resume:
                extrait = (extrait + " — " + resume) if extrait else resume
            resultats.append(
                ResultatProvider(
                    titre=titre,
                    url=url,
                    extrait=nettoyer_extrait(extrait),
                    source=self.nom,
                    date=str(annee) if annee else None,
                    type="publication",
                )
            )
        return resultats


class PubChemProvider(BaseProvider):
    """PubChem PUG-REST (composés chimiques) — sans clé.

    Recherche par nom : un nom inconnu renvoie HTTP 404 → liste vide
    (pas d'erreur), conformément au contrat.
    """

    nom = "pubchem"
    capacites = ["recherche_scientifique", "chimie"]
    necessite_cle = False
    hotes_autorises = frozenset({"pubchem.ncbi.nlm.nih.gov"})

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        requete = _requete_propre(requete)
        if not requete:
            return []
        try:
            donnees = self._get_json(
                "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
                f"{quote(requete, safe='')}/property/"
                "Title,MolecularFormula,MolecularWeight,CanonicalSMILES,IUPACName/JSON",
                timeout_s=timeout_s,
            )
        except ProviderErreur as exc:
            if exc.type_erreur == "http" and "404" in str(exc):
                return []
            raise
        try:
            lignes = (donnees.get("PropertyTable") or {}).get("Properties") or []
        except AttributeError as exc:
            raise ProviderErreur(
                "Provider « pubchem » : réponse inattendue. "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            ) from exc
        resultats: list[ResultatProvider] = []
        for ligne in lignes[: max(1, limite)]:
            if not isinstance(ligne, dict):
                continue
            cid = ligne.get("CID")
            titre = (ligne.get("Title") or "").strip()
            if not cid or not titre:
                continue
            formule = (ligne.get("MolecularFormula") or "").strip()
            masse = (ligne.get("MolecularWeight") or "").strip()
            smiles = (
                ligne.get("CanonicalSMILES") or ligne.get("ConnectivitySMILES") or ""
            ).strip()
            iupac = (ligne.get("IUPACName") or "").strip()
            morceaux = []
            if formule:
                morceaux.append(f"Formule : {formule}")
            if masse:
                morceaux.append(f"Masse molaire : {masse} g/mol")
            if smiles:
                morceaux.append(f"SMILES : {smiles}")
            if iupac:
                morceaux.append(f"Nom IUPAC : {iupac}")
            resultats.append(
                ResultatProvider(
                    titre=f"{titre} (CID {cid})",
                    url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
                    extrait=nettoyer_extrait(" · ".join(morceaux)),
                    source=self.nom,
                    date=None,
                    type="chimie",
                )
            )
        return resultats


class ChEMBLProvider(BaseProvider):
    """ChEMBL (molécules bioactives, EBI) — sans clé.

    L'API renvoie du XML par défaut : on demande explicitement du JSON
    (``Accept: application/json``).
    """

    nom = "chembl"
    capacites = ["recherche_scientifique", "chimie"]
    necessite_cle = False
    hotes_autorises = frozenset({"www.ebi.ac.uk"})

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        requete = _requete_propre(requete)
        if not requete:
            return []
        donnees = self._get_json(
            "https://www.ebi.ac.uk/chembl/api/data/molecule/search",
            params={"q": requete, "limit": max(1, limite)},
            headers={"Accept": "application/json"},
            timeout_s=timeout_s,
        )
        if not isinstance(donnees, dict):
            raise ProviderErreur(
                "Provider « chembl » : réponse inattendue. "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            )
        resultats: list[ResultatProvider] = []
        for molecule in (donnees.get("molecules") or [])[: max(1, limite)]:
            if not isinstance(molecule, dict):
                continue
            chembl_id = (molecule.get("molecule_chembl_id") or "").strip()
            nom_mol = (molecule.get("pref_name") or "").strip() or chembl_id
            if not chembl_id:
                continue
            structures = molecule.get("molecule_structures") or {}
            proprietes = molecule.get("molecule_properties") or {}
            formule = (proprietes.get("full_molformula") or "").strip()
            masse = (proprietes.get("full_mwt") or "").strip()
            smiles = (structures.get("canonical_smiles") or "").strip()
            morceaux = []
            if formule:
                morceaux.append(f"Formule : {formule}")
            if masse:
                morceaux.append(f"Masse molaire : {masse} g/mol")
            if smiles:
                morceaux.append(f"SMILES : {smiles}")
            resultats.append(
                ResultatProvider(
                    titre=f"{nom_mol} ({chembl_id})",
                    url=f"https://www.ebi.ac.uk/chembl/compound_report_card/{chembl_id}/",
                    extrait=nettoyer_extrait(" · ".join(morceaux)),
                    source=self.nom,
                    date=None,
                    type="chimie",
                )
            )
        return resultats


class GbifProvider(BaseProvider):
    """GBIF (taxonomie et espèces) — sans clé."""

    nom = "gbif"
    capacites = ["recherche_scientifique", "biodiversite"]
    necessite_cle = False
    hotes_autorises = frozenset({"api.gbif.org"})

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        requete = _requete_propre(requete)
        if not requete:
            return []
        donnees = self._get_json(
            "https://api.gbif.org/v1/species/search",
            params={"q": requete, "limit": max(1, limite)},
            timeout_s=timeout_s,
        )
        if not isinstance(donnees, dict):
            raise ProviderErreur(
                "Provider « gbif » : réponse inattendue. "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            )
        resultats: list[ResultatProvider] = []
        for taxon in donnees.get("results") or []:
            if not isinstance(taxon, dict):
                continue
            nom_sci = (taxon.get("scientificName") or "").strip()
            cle = taxon.get("key")
            if not nom_sci or not cle:
                continue
            rang = (taxon.get("rank") or "").strip()
            regne = (taxon.get("kingdom") or "").strip()
            embranchement = (taxon.get("phylum") or "").strip()
            famille = (taxon.get("family") or "").strip()
            morceaux = []
            if rang:
                morceaux.append(f"Rang : {rang}")
            if famille:
                morceaux.append(f"Famille : {famille}")
            if embranchement:
                morceaux.append(f"Embranchement : {embranchement}")
            if regne:
                morceaux.append(f"Règne : {regne}")
            resultats.append(
                ResultatProvider(
                    titre=nom_sci,
                    url=f"https://www.gbif.org/species/{cle}",
                    extrait=nettoyer_extrait(" · ".join(morceaux)),
                    source=self.nom,
                    date=None,
                    type="biodiversite",
                )
            )
        return resultats
