# WebIntel v2.4.0 — Rapport de réparation « ça produit »

Date : 2026-10-03. Déclencheur : test chercheur réel de la v2.3.0 par
l'utilisateur — « bien architecturé, mais ça ne produit rien, hors ligne
comme en ligne ». Ce rapport distingue ce qui est **corrigé**, ce qui est
**testé en direct**, et ce qui reste **limité**. Aucune prédiction n'est
présentée comme un fait ; aucun composé ou plante n'est dit
« efficace », « sûr » ou « traite » quoi que ce soit.

## 1. Causes racines prouvées (installation fraîche v2.3.0)

1. **Base vide au premier lancement** : 0 plante, 0 composé, 0 lien.
   Les fichiers `demo/` n'étaient jamais chargés automatiquement.
2. **Recherche locale uniquement** : l'écran chercheur n'avait aucun
   repli en ligne ; les connecteurs exigeaient une page secondaire/API.
3. **Connecteurs jamais testés en réel**, et bloqués par l'anti-SSRF
   quand le DNS résout les hôtes d'API vers la plage 198.18/15
   (proxy/VPN) : l'hôte était rejeté comme « adresse privée ».
4. **RDKit optionnel** : sans installation manuelle du niveau SCIENCE,
   descripteurs et similarité étaient non calculables hors ligne.

## 2. Corrigé en v2.4.0

### A. Seed de démarrage (hors ligne, dès l'ouverture)
- `webintel/science/seed_initial.py` : `charger_donnees_initiales()`,
  branché dans `init_db()` (désactivable : `WEBINTEL_SEED_INITIAL=0`,
  utilisé par la suite de tests).
- Contenu : **13 plantes** médicinales d'Afrique de l'Ouest (Moringa
  oleifera, Artemisia annua, Hibiscus sabdariffa, Khaya senegalensis,
  Parkia biglobosa, Tamarindus indica, Adansonia digitata, Balanites
  aegyptiaca, Combretum micranthum, Vernonia amygdalina, Azadirachta
  indica, Carica papaya, Zingiber officinale), **5 composés**
  (quercétine, artémisinine, kaempférol, acide chlorogénique, gingérol),
  **4 liens plante→composé** (publiés avec DOI, ou « rapporte » sans
  publication inventée), **2 preuves** documentaires.
- Discipline : aucune concentration/posologie non revérifiée semée ;
  noms vernaculaires seulement quand certains (ex. « Bissap »), langue
  jamais devinée ; usages étiquetés « USAGE TRADITIONNEL RAPPORTÉ » ;
  provenance « jeu de démonstration intégré WebIntel » sur chaque
  enregistrement ; idempotence par clés naturelles (double démarrage
  vérifié : comptes inchangés, 0 doublon).

### B. Recherche en ligne chercheur
- Bouton « Rechercher en ligne » sur `/recherche-universelle` (toujours
  visible + proposé quand le local rend 0), carte GBIF sur une plante
  introuvable, lien depuis `/accueil`.
- `GET /recherche-en-ligne` : aperçu **avant import** par source
  (PubChem : formule/masse/InChIKey ; GBIF : statut/famille/occurrences ;
  PubMed : jusqu'à 5 publications), source et licence affichées.
- `POST /recherche-en-ligne/importer` : import en un clic via le
  connecteur existant, provenance enregistrée (`ImportProvenance`,
  `SourceConnecteur.dernier_import`), niveau de données « normalisé ».
  Échec réseau → message français explicite, rollback, aucun faux
  résultat. Seul le terme recherché est envoyé (GET, lecture seule) ;
  aucune donnée de laboratoire ne part vers l'extérieur.

### C. Anti-SSRF : liste blanche bornée
- `autoriser_hote_science` (défaut `False` partout) : seuls 5 hôtes
  codés en dur (`pubchem.ncbi.nlm.nih.gov`, `eutils.ncbi.nlm.nih.gov`,
  `api.gbif.org`, `www.ebi.ac.uk`, `api.openalex.org`), https et
  GET/HEAD obligatoires, seule la plage benchmark 198.18/15 tolérée en
  plus des IP publiques ; RFC1918, loopback, link-local/metadata restent
  bloqués même pour ces hôtes. Épinglage DNS + SNI + certificat sur le
  nom d'origine conservés.
- **Toute URL fournie par l'utilisateur** (crawler, fetcher, robots,
  webhooks) suit le chemin strict inchangé — prouvé par 34 nouveaux
  tests + la suite sécurité existante.
- Repli proxy borné aux mêmes 5 hôtes (postes derrière proxy/VPN) :
  connexion directe d'abord, second essai via le proxy configuré
  uniquement sur échec de connexion ; l'hôte cible reste celui, fixe,
  du connecteur.

### D. RDKit par défaut
- `rdkit` ajouté à `requirements.txt` et aux dépendances `pyproject`
  (extra `[science]` conservé, réduit à `meeko`). Page « Outils
  scientifiques » et textes datés harmonisés : RDKit installé par
  défaut, version affichée dynamiquement. Sans RDKit (retrait manuel),
  le code dégrade toujours proprement : descripteur « non calculable »,
  jamais de valeur inventée.

## 3. Testé en direct (réseau réel, dans l'environnement de build)

Les 5 hôtes d'API résolvent ici vers 198.18.56.167–171 et la sortie
directe est interceptée par un proxy imposé. Résultats réels, obtenus
via le connecteur WebIntel complet (whitelist + repli proxy) :

| Connecteur | Requête live | Résultat réel |
|---|---|---|
| PubChem | import composé « caffeine » | **1 composé créé** |
| GBIF | import « Moringa oleifera » | **plante mise à jour** (taxon GBIF apparié) |
| PubMed | import « Moringa oleifera » | **5 publications créées** (preuves PMID/DOI) |
| PubChem (contrôle) | quercétine → InChIKey | HTTP 200, `REFJWTPEDVJJIY-UHFFFAOYSA-N` |
| GBIF (contrôle) | Moringa oleifera | HTTP 200, appariement exact 97 % |

Sur un poste en accès internet direct, le même code passe par le
transport épinglé (les hôtes y résolvent en IP publiques) ; le repli
proxy ne sert que lorsqu'un proxy est configuré.

## 4. Chiffres de tests

| Suite | Résultat |
|---|---|
| Suite complète v2.4.0 | **1171 passed / 0 failed / 17 skipped** |
| Baseline v2.3.0 | 1120 passed / 0 failed / 17 skipped |
| Nouveaux tests | +51 (seed 8, whitelist SSRF 34, recherche en ligne 9) |

Calculs RDKit vérifiés dans le venv : quercétine — logP 1,988, TPSA
131,36 Å², HBD 5 / HBA 7, masse 302,238 g/mol, 0 violation Lipinski ;
Tanimoto Morgan caféine/théophylline = 0,5294. Ce sont des
**descripteurs calculés** et une **similarité structurale** — pas des
preuves d'activité.

## 5. Acceptation sur installation fraîche

Archive `webintel-v2.4.0.zip` dézippée dans `/tmp`, venv de test,
`pip install -r requirements.txt`, serveur démarré (SQLite neuve),
parcours HTTP réel vérifié le 2026-10-03 :

| Étape | Résultat réel |
|---|---|
| Premier démarrage (base neuve) | seed automatique : 13 plantes, 5 composés, 4 liens, 2 preuves |
| Second démarrage | comptes inchangés (idempotence, 0 doublon) |
| `GET /accueil` | HTTP 200 |
| Recherche « Moringa oleifera » | fiche complète : composés (dont quercétine), mention « USAGE TRADITIONNEL RAPPORTÉ » |
| Recherche « Bissap » (nom vernaculaire) | fiche *Hibiscus sabdariffa* trouvée (voir correctif ci-dessous) |
| Recherche « Artemisia » | fiche *Artemisia annua* trouvée |
| `GET /recherche-universelle?q=Moringa` | HTTP 200, résultats sourcés |
| `GET /recherche-en-ligne?q=quercetin` | HTTP 200 (aperçu par source) |
| Fiche API `/api/science/plants/1/fiche` | HTTP 200, phytochimie et littérature présentes |
| Descripteurs RDKit (quercétine) | logP 1,988 ; TPSA 131,36 Å² ; HBD 5 ; HBA 7 ; masse 302,238 g/mol |
| Similarité Morgan caféine/théophylline | Tanimoto 0,5294 (calculée, revérifiée) |
| Page rapport `/rapport` | HTTP 200 |
| Suite pytest complète après correctifs | **1171 passed / 0 failed / 17 skipped** |

**Correctif d'acceptation (2026-10-03, après le premier build)** : le
premier essai a montré que « Bissap » restait « Plante introuvable »
sur l'écran d'étude — `_chercher_plantes` ne cherchait pas dans les
noms vernaculaires, contrairement à sa propre documentation. Corrigé
(recherche dans `vernacular_names` et `synonyms`, même méthode que la
recherche universelle), retesté en HTTP réel, suite complète relancée
sans régression, archive régénérée.

## 6. Limites restantes (non maquillées)

- Le seed est un **jeu de démonstration sourcé**, pas une base
  exhaustive : 13 plantes de départ, à enrichir par imports connecteurs
  ou par le laboratoire. Les 2 DOI et identifiants externes repris du
  corpus demo n'ont pas été revérifiés en ligne un par un.
- Les connecteurs dépendent du réseau du poste : sans proxy configuré
  et sans accès direct, ils renvoient une erreur explicite (aucun mode
  dégradé silencieux). Rate-limits NCBI/GBIF inchangés.
- Meeko reste optionnel (conversion PDBQT pour le docking) ; le binaire
  AutoDock Vina n'est toujours pas fourni — aucun score de docking
  n'est produit sans lui.
- Descripteurs RDKit = calculs informatiques ; similarité structurale
  ≠ activité pharmacologique ; les publications importées sont au
  niveau « normalisé », jamais présentées comme des preuves validées.
- PostgreSQL : non re-testé sur serveur réel dans ce chantier (le
  chemin SQLite local, défaut chercheur, est celui de l'acceptation) ;
  hérité de la validation v2.3.0.
