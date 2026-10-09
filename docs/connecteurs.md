# Connecteurs de sources externes — Phase 10

Les connecteurs interrogent des sources externes en **lecture seule** et
importent **sélectivement** leurs données dans WebIntel. Règles dures :

1. **Jamais de copie aveugle** : un import porte sur un objet précis
   (un composé, quelques publications) — pas de moissonnage de base.
2. **Licence obligatoire** : chaque connecteur déclare la licence des
   données, l'URL du texte de licence et les conditions d'utilisation.
   Un connecteur sans licence documentée ne doit pas être activé.
3. **Provenance systématique** : chaque objet importé enregistre
   (connecteur, date d'import, version des données, licence) dans
   `import_provenance` (+ `licence`/`version_donnees` dans le rapport).
4. **Respect des CGU** : débit raisonnable, pas de contournement des
   limites de la source, `User-Agent: WebIntel-recherche`.
5. **Honnêteté scientifique** : les données importées sont **NORMALISEES**
   (`niveau_donnees: "normalise"`) — jamais présentées comme
   « scientifiquement validées » ni comme observations expérimentales.

## Contrat d'un connecteur

Attributs de classe (dans `webintel/science/connecteurs/plugins/`) :

| Attribut | Obligatoire | Rôle |
|---|---|---|
| `NOM` | oui | identifiant unique (ex. `"pubchem"`) |
| `DESCRIPTION` | oui | description courte (FR) |
| `URL_SOURCE` | oui | URL de base de l'API distante |
| `LICENCE` | oui | licence des données |
| `LICENCE_URL` | oui | URL du texte de la licence |
| `CONDITIONS_UTILISATION` | oui | CGU / limites de débit |
| `VERSION` | oui | version du connecteur |
| `STRATEGIE_MAJ` | oui | stratégie de mise à jour (FR) |

Méthodes : `tester_statut()` → `(bool, message)` ;
`importer(session, parametres: dict)` → rapport
`{crees, mis_a_jour, ignores, erreurs[], provenance{...}}`
(ne committe jamais — l'appelant décide).

Discipline HTTP commune (`BaseConnecteur`) : timeout 10 s, 2 retries
maximum, cache 24 h (`connecteur_cache`), journalisation, transport
épinglé anti-TOCTOU (`PinnedHTTPTransport` : résolution DNS unique
validée, Host/SNI préservés). Réseau
indisponible → `ConnecteurIndisponible` (jamais de faux résultat).

Le registre (`registry.py`) : `CONNECTEURS` (nom → classe),
`lister_connecteurs()`, `get_connecteur(nom)` (`ValueError` explicite si
inconnu), `initialiser_connecteurs(session)` (seed idempotent des lignes
`source_connecteurs`, ne committe pas).

## Connecteurs fournis

### `pubchem` — PubChem PUG-REST (implémenté)
- URL : https://pubchem.ncbi.nlm.nih.gov/rest/pug
- Licence : domaine public (U.S. National Library of Medicine)
- Usage : `importer(session, {"nom": "aspirine"})` → propriétés
  (formule, masse molaire, SMILES, InChIKey) → `Compound` idempotent
  (clé : nom canonique PubChem ; InChIKey unique).
- Limites : requêtes unitaires espacées, pas de moissonnage massif
  (service partagé NCBI).

### `pubmed` — PubMed E-utilities (implémenté)
- URL : https://eutils.ncbi.nlm.nih.gov/entrez/eutils
- Licence : données publiques (U.S. National Library of Medicine) ;
  les résumés complets restent soumis aux droits des éditeurs.
- Usage : `importer(session, {"requete": "...", "max": 5})`
  (plafond 20) → `Evidence` idempotentes sur PMID/DOI (titre, auteurs,
  année, PMID, DOI, URL).
- Limites : ≤ 3 requêtes/s sans clé (CGU NCBI) ; paramètre
  `tool=WebIntel-recherche` envoyé systématiquement.

### `openalex` — OpenAlex (v2.2, implémenté)
- URL : https://api.openalex.org/works
- Licence : **CC0** (données bibliographiques ouvertes ;
  https://openalex.org/legal).
- Usage : `importer(session, {"requete": "...", "max": 5})`
  (plafond 20) → `Evidence` idempotentes sur DOI/PMID/titre normalisé
  (titre, auteurs, année, DOI, PMID, journal, langue, type de document,
  OpenAlex ID conservé dans `external_ids` — jamais présenté comme
  identifiant de publication).
- Limites : **métadonnées uniquement** (jamais de texte intégral) ;
  requêtes unitaires espacées (courtoisie, sans clé d'API) ; pas de
  moissonnage massif.
- Conditions : identifiant externe enregistré : OpenAlex ID ;
  provenance CC0.

### `chembl` — ChEMBL (v2.2, implémenté)
- URL : https://www.ebi.ac.uk/chembl/api/data
- Licence : **CC-BY-SA 3.0** (EMBL-EBI ; https://www.ebi.ac.uk/chembl/)
  — attribution obligatoire : chaque import cite ChEMBL.
- Usage : `importer(session, {"nom": "quercétine"})` ou
  `{"chembl_id": "CHEMBL50"}` → `Compound` idempotent (clé InChIKey) :
  formule, masse, SMILES, InChIKey, identifiant ChEMBL conservé dans
  `external_ids`. Le **nombre d'activités documentées** est importé
  comme compte informatif uniquement — les valeurs expérimentales ne
  sont pas recopiées (consulter la fiche ChEMBL).
- Limites : recherche par nom = nom préféré **exact** (insensible à la
  casse) ; en cas d'ambiguïté, l'import refuse de deviner — le
  chercheur fournit l'identifiant ChEMBL. Requêtes unitaires espacées ;
  pas de moissonnage massif.

### `gbif` — GBIF (v2.2, implémenté)
- URL : https://api.gbif.org/v1
- Licence : métadonnées taxonomiques publiques ; occurrences :
  licence propre à chaque jeu de données (CC0 / CC-BY / CC-BY-NC,
  champ `license` de l'API — https://www.gbif.org/terms).
- Usage : `importer(session, {"nom_scientifique": "Moringa oleifera",
  "pays": "BF"})` → vérification taxonomique (statut, rang, nom
  accepté) + **comptes** d'occurrences (total, par pays). Seuls des
  métadonnées et des comptes sont importés — **aucune fiche
  d'occurrence n'est recopiée** (aucune licence par enregistrement due).
- Limites : requêtes espacées (~1/s, courtoisie) ; un nom synonyme est
  signalé avec le nom accepté proposé, jamais « corrigé »
  silencieusement — le chercheur décide.

## API

- `GET /api/science/connecteurs` → `200 {"connecteurs": [...]}` avec,
  pour chaque connecteur, exactement les clés :
  `nom, description, url, licence, licence_url, conditions_utilisation,
  version_connecteur, statut, dernier_import, strategie_maj`
  (contrat pour la page UI). Le seed s'exécute au premier appel si la
  table est vide.
- `POST /api/science/connecteurs/{nom}/importer`
  body `{"parametres": {...}, "projet_id": <optionnel>}` →
  `200 {"rapport": {...}, "provenance": {...}, "avertissement": "..."}`.
  Erreurs : `404` connecteur inconnu · `409` connecteur inactif ·
  `422` paramètres invalides · `503` source injoignable (explicite,
  aucune donnée importée).

## Activation d'un nouveau connecteur

1. Créer `webintel/science/connecteurs/plugins/<nom>.py` héritant de
   `BaseConnecteur` (renseigner TOUS les attributs contractuels,
   licence comprise).
2. L'ajouter à `CONNECTEURS` dans `registry.py`.
3. Ajouter des tests HTTP **mockés** dans `tests/test_science_connecteurs.py`
   (aucun appel réseau réel : la suite de tests bloque le DNS).
4. Documenter la source, sa licence et ses limites dans ce fichier.
