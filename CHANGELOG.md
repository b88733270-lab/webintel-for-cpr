# Journal des versions — WebIntel

## v3.1.0 — Recherche scientifique sans clé : 8 nouveaux providers (2026-10-10)

- **8 providers scientifiques SANS CLÉ**, ajoutés après les 8 généralistes
  dans le registre (ordre généraliste inchangé) :
  - publications : **PubMed** (E-utilities NCBI : esearch + esummary),
    **OpenAlex** (résumé reconstruit depuis `abstract_inverted_index`),
    **Europe PMC**, **Crossref**, **Semantic Scholar** ;
  - chimie : **PubChem** (PUG-REST : formule, masse, SMILES, nom IUPAC —
    404 « composé inconnu » → liste vide, pas d'erreur), **ChEMBL**
    (JSON demandé explicitement via `Accept: application/json`) ;
  - biodiversité : **GBIF** (taxonomie : rang, famille, embranchement,
    règne).
- **Nouvel endpoint** `GET /api/web/recherche-scientifique`
  (paramètres `q`, `limite`, `langue`) : n'interroge que les providers
  déclarant la capacité `recherche_scientifique`, avec fallback
  automatique comme la recherche généraliste.
- Discipline inchangée : hôtes codés en dur (anti-SSRF),
  `self._get_json()` uniquement, jamais de résultat inventé
  (`ProviderErreur` typée en cas d'échec), messages en français,
  extraits nettoyés et bornés à 500 caractères, statuts exposés sur
  la page Paramètres via `statuts_providers()`.
- 26 tests ajoutés (`tests/test_providers_scientifiques.py`) : succès /
  erreur / requête vide par provider (MockTransport, zéro réseau réel)
  + test registre (les 8 noms, capacités, filtrage par capacité).

## v3.0.7 — Correctif d'affichage : derniers `.strftime` inline (2026-10-09)

- **4 écrans oubliés par l'audit v3.0.4** : `plante_etude.html`,
  `mes_recherches.html`, `synchronisation.html` et `collecte_detail.html`
  formataient encore leurs dates en appelant `.strftime(...)` directement
  dans le gabarit (6 occurrences), au lieu du filtre `date_courte` du
  Design System. Au-delà de la cohérence, c'est aussi une robustesse :
  le filtre accepte datetime **ou** chaîne ISO, le `.strftime` inline
  plantait sur une chaîne. Libellés de secours inchangés
  (« date inconnue », « — », vide).
- 1 test ajouté (`tests/test_ui_affichage_valeurs.py`) : balayage des 77
  gabarits — plus aucun `.strftime(` inline + assertions des 6 motifs
  convertis.

## v3.0.6 — Correctif d'affichage : libellé « Export » résiduel (2026-10-09)

- **`/synchronisation` (écran oublié par le renommage v3.0.4)** : le texte
  « Export complet du projet » utilisait encore le mot anglais ; il dit
  désormais « Exportation complète du projet », cohérent avec l'écran
  `fiche.html`. Un seul gabarit touché, aucun comportement modifié.
- 1 test ajouté (`tests/test_ui_affichage_valeurs.py`) : balayage des 77
  gabarits — le mot isolé « Export » ne doit plus apparaître nulle part.

## v3.0.5 — Correctif d'affichage : date du dernier import (2026-10-09)

- **`/connecteurs` (écran oublié par l'audit v3.0.4)** : la ligne
  « Dernier import » affichait l'horodatage ISO brut venu de l'API
  interne (ex. « 2026-10-08T14:32:11.123456 ») ; elle affiche désormais
  « 08/10/2026 14:32 » via le filtre `date_courte`, déjà utilisé par
  les 16 autres écrans. Un seul gabarit touché, aucun comportement
  modifié.
- 2 tests ajoutés (`tests/test_ui_affichage_valeurs.py`) : rendu direct
  du gabarit avec une chaîne ISO + assertion du motif dans le gabarit.

## v3.0.4 — Polish visuel : dates et valeurs lisibles (2026-10-08)

- **Validation visuelle des interfaces** (axe annoncé) : audit des écrans
  sur captures — navigateurs distants injoignables en local, audit mené
  sur les 22 captures existantes + vérification statique des gabarits.
- **Dates lisibles** : nouveau filtre Jinja `date_courte` — les écrans
  affichaient des horodatages ISO bruts (« 2026-10-04T03:43:35.481243 ») ;
  ils affichent désormais « 04/10/2026 03:43 ». Appliqué à : Entités,
  fiche entité, Recherche, Comparaison (première/dernière vue,
  observations par source), Sources, Collections, Événements, Moniteurs
  (« jamais » conservé si aucun passage), Alertes, Crawls, historique des
  études, rapports de simulation, synthèses IA.
- **Valeurs observées lisibles** : nouveau filtre `afficher_valeur` —
  la Comparaison affichait des repr Python
  (`{'amount': 44.99, 'currency': 'EUR'}`) ; elle affiche « 44,99 EUR »
  (virgule française). Prix aussi francisés dans Entités, Recherche
  et la fiche d'analyse (`fiche.html`).
- **Paramètres** : les noms de variables d'environnement ne sont plus
  coupés en milieu de mot (`word-break: break-all` → `nowrap` sur la
  colonne Variable ; nouvelle classe utilitaire `.nowrap`).
- **Francisation** : titre de section « Export » → « Exportation »
  (`fiche.html`).
- 18 tests ajoutés (`tests/test_ui_affichage_valeurs.py`, dont un test
  de rendu bout en bout avec vraies données) : 18/18 verts.

## v3.0.3 — Correctif dépréciation Pydantic (2026-10-07)

- **Dépréciation Pydantic corrigée** : `webintel/api/routers/science_terrain.py`
  accédait à `payload.model_fields` sur l'instance (PydanticDeprecatedSince211,
  deviendra une erreur avec Pydantic V3) ; accès désormais via la classe
  (`type(payload).model_fields`). Une seule ligne touchée, aucun comportement
  modifié. 42/42 tests `test_science_terrain.py` verts, y compris avec
  `PydanticDeprecatedSince211` escaladé en erreur.

## v3.0.2 — Correctif tableau de bord (2026-10-07)

- **`/tableau-de-bord` (500 corrigé)** : le détecteur `taxons_ambigus`
  (`webintel/science/qualite.py`) plantait sur les plantes dont
  `external_ids["gbif"]` est la clé taxon brute (chaîne, ex. données de
  démonstration `Hibiscus sabdariffa` → `{'gbif': '3152703'}`) :
  `AttributeError: 'str' object has no attribute 'get'` → la page
  répondait 500. Le lecteur est désormais robuste (chaîne = pas de statut
  connu, pas d'anomalie). Bonus : la clé lue passe de `status` (format
  brut API) à `statut` (format écrit par le connecteur GBIF) — la
  détection des taxons SYNONYM/DOUBTFUL/MISAPPLIED refonctionne pour les
  plantes importées via le connecteur (elle était silencieusement morte).
  2 tests de régression ajoutés (`tests/test_science_qualite.py`).
  Audit statique des 23 écrans UI : 38 liens internes, 0 problème.
- Suite complète : **1210 passed / 0 failed / 17 skipped**.

## v3.0.1 — Correctif cosmétique (2026-10-07)

- **Recherche Web** : les extraits affichaient littéralement les entités HTML
  (`&#039;`, `&amp;`…) au lieu de l'apostrophe et des caractères décodés.
  `nettoyer_extrait` (`webintel/providers/base.py`) décode désormais les
  entités après retrait des balises ; test de régression ajouté
  (`tests/test_providers_web.py`). Aucun autre écran ni comportement modifié.
- **Archive** : le zip v3.0.0 livré contenait le défaut et des caches
  (`.ruff_cache`) ; l'archive v3.0.1 est reconstruite proprement, caches exclus.

## v3.0.0 — Pivot produit : intelligence Web généraliste, online-first (2026-10-04)

Métaprompt produit appliqué : WebIntel redevient une plateforme
d'**intelligence Web généraliste** (recherche Web, entités, analyse
d'URL, collecte, recoupement, détection de changements, monitoring,
rapports, relations, historique). Le fonctionnement hors ligne complet
est abandonné : une nouvelle recherche sans réseau affiche une erreur
explicite ; seuls historique, favoris, collections et préférences
restent consultables localement. Le module science/plantes (v2.x) est
conservé intégralement comme **domaine spécialisé** accessible depuis
la navigation, sans régression de ses tests.

- **Providers + recherche Web** : nouvelle abstraction
  `webintel/providers/` (interface commune typée, erreurs normalisées,
  timeouts, budget global, statut sans jamais exposer de clé) avec
  **fallback automatique** et déduplication par URL normalisée.
  Sans clé, immédiatement fonctionnels : Wikipedia (FR/EN),
  DuckDuckGo Instant Answers, GitHub public, RSS actualités
  (Google News). À clé optionnels, sautés proprement si absents :
  Brave Search, Tavily, SerpApi, NewsAPI. Clés via variables
  `WEBINTEL_*_API_KEY` dans un fichier `.env` à la racine (voir
  `.env.example`) et statut visible dans Paramètres → « Services &
  clés API ». Endpoint `GET /api/web/recherche` + écran
  `/recherche-web`. Vérification TLS sur le magasin système (proxy
  d'entreprise honoré, nom d'hôte toujours vérifié).
- **Design System unique** : `style.css` restructuré en tokens +
  composants (boutons, cartes, badges, tableaux responsive-cartes,
  timeline, squelettes, états vides/erreur, toasts). Layout :
  sidebar desktop (≥1024px), sidebar icônes tablette, topbar +
  bottom navigation mobile (<768px). Écrans refaits : Accueil,
  Recherche (progression 6 étapes : Compréhension → Découverte →
  Collecte → Analyse → Vérification → Synthèse), Résultats, Entités,
  Analyse URL, Événements, Monitoring, Alertes, Rapports/Exports,
  Paramètres. États Loading/Success/Empty/Error/Retry/Offline
  partout ; aucun écran vide sans explication.
- **Nouveaux écrans** : `/sources` (provenance entité → source →
  observation), `/comparaison` (deux entités côte à côte, valeurs
  observées uniquement), `/recherche-web` (providers en direct).
- **Favoris / Collections** : tables `collections` et `favorites`
  (isolation stricte par projet, doublons refusés, suppression d'une
  collection sans perte des favoris), page `/collections`, ajout
  depuis la fiche entité.
- **Conservé** : le moteur v1 (crawler/fetcher hybride, extraction,
  entités/observations/snapshots/diffs/événements/moniteurs/alertes,
  exports, anti-SSRF strict sur URLs utilisateur) et tout le module
  science (plantes, composés, RDKit, connecteurs scientifiques,
  seed v2.4.0) — aucune suppression sans raison technique.

## v2.4.0 — Chantier « ÇA PRODUIT » : réparation chercheur réelle (2026-10-03)

Suite au constat de l'utilisateur (test chercheur réel sur v2.3.0 :
« ça ne produit rien », hors ligne comme en ligne), correction des
quatre causes racines prouvées sur installation fraîche :

- **Données dès le premier lancement** : base fraîche v2.3.0 = 0 plante,
  0 composé (les fichiers demo n'étaient jamais chargés). Nouveau seed
  automatique idempotent (`webintel/science/seed_initial.py`, branché
  dans `init_db`, désactivable par `WEBINTEL_SEED_INITIAL=0`) : 13
  plantes médicinales d'Afrique de l'Ouest, 5 composés, 4 liens
  plante→composé publiés/rapportés, 2 preuves — chacun avec provenance
  « jeu de démonstration intégré WebIntel ». Aucune concentration,
  posologie ou efficacité inventée ; usages étiquetés
  « USAGE TRADITIONNEL RAPPORTÉ ».
- **Recherche en ligne depuis l'écran chercheur** : bouton
  « Rechercher en ligne » sur la recherche universelle (et carte GBIF
  sur une plante introuvable) → aperçu PubChem/GBIF/PubMed avec source
  et licence, import en un clic avec provenance. Seul le terme
  recherché part vers l'extérieur (GET, lecture seule).
- **Anti-SSRF** : liste blanche stricte (5 hôtes d'API scientifiques,
  drapeau explicite `autoriser_hote_science`, GET/HEAD + https seuls,
  seule la plage benchmark 198.18/15 tolérée ; toute URL utilisateur
  reste soumise au chemin strict inchangé). Repli proxy borné aux
  mêmes hôtes pour les postes derrière proxy/VPN. Protection du
  crawler/fetcher/webhooks inchangée.
- **RDKit par défaut** : `rdkit` ajouté à `requirements.txt` et aux
  dépendances pyproject (extra `[science]` = meeko seul, conservé) —
  descripteurs, similarité et normalisation fonctionnent hors ligne
  dès l'installation standard.

Tests : **1171 passed / 0 failed / 17 skipped** (baseline v2.3.0 :
1120/0/17 ; +51 nouveaux : seed idempotent 8, whitelist SSRF 34,
recherche en ligne mockée 9). Acceptation sur installation fraîche et
tests en direct des connecteurs : voir `REPAIR_REPORT.md`.

## v2.3.0 — Chantier PERSISTANCE / PERFORMANCE / BASE DE DONNÉES (2026-09-23)

**Intégration finale (même version, sans bump)** : `GET /tableau-de-bord`
(tableau de bord chercheur : projets, plantes, échantillons, études,
expériences, simulations, publications, rapports, données à valider,
outils — 100 % comptes réels, aucune statistique artificielle) ;
`creer_etude()` écrit désormais une ligne au journal `provenances`
(idempotent, niveau `prediction`) — la fiche
`/resultats/etude/{id}/provenance` n'est plus un 404 pour les études
créées par le wizard ; `scripts/test_postgres.py` corrigé (import des
modèles scientifiques avant `drop_all`). Suite finale mesurée :
**1120 passed / 17 skipped / 0 failed**. Rapport :
`ULTIMATE_RELEASE_REPORT.md`.

Code 100 % additif : aucune fonction scientifique ni route existante
supprimée ou remplacée ; architecture inchangée.

**Persistance réelle après redémarrage**
- Le wizard ne perd plus les sessions finalisées : la purge TTL ne
  supprime que les brouillons `en_cours` ; une étude terminée reste
  accessible même après expiration (test vrai redémarrage en 2
  processus : `tests/test_persistance_redemarrage.py`).
- `detail_etude` relit l'étude complète : plante, échantillons,
  molécules, cibles, analyses, paramètres, résultats, provenance,
  limitations, logiciels, hashes, dates, statut, versions.

**Versionnage append-only**
- Création d'étude (API + wizard) → version initiale automatique.
- Nouvelle exécution = NOUVELLE étude (jamais d'écrasement), avec
  provenance vers l'étude source.
- Comparaison structurée v1/v2 : entrées, paramètres, sources,
  logiciels, résultats (+ empreinte SHA-256 du dossier, résumé FR).
- Contrainte unique `(study_id, numero)` + retry anti-course :
  8 threads → numéros 1..8 sans doublon (SQLite et PostgreSQL).
- Routes : `GET /versions/{a}/comparer/{b}`,
  `GET /{id}/versions`, `POST /{id}/versions`.

**Performance**
- Index FK dérivés de `Base.metadata` (couverture 100 %, 52 tables,
  idempotents) ; index invalide `ix_evidences_project_id` supprimé
  (colonne inexistante) ; création d'index vérifiant les colonnes.
- N+1 éliminé sur `chaine_echantillon` : 22 → 5 requêtes (constant
  par volume) ; fiche plante 9/11 requêtes ; recherche 5 ; études 1.
- Pagination `limit` (1..500, défaut 50) / `offset` sur 11 listes
  (noms vernaculaires, usages, échantillons, vouchers, analyses,
  pics, preuves, observations, simulations, notes labo, workflows).
- Imports : flush + `expunge_all()` par paquets de 500 lignes
  (garde-fou mémoire ; limite fichier 10 Mio inchangée).

**Concurrence**
- Clés d'idempotence uniques : noms vernaculaires, versions d'étude.
- 5 tests à 8 threads (langues, noms, vouchers, échantillons,
  versions) — stables sur 3 passages.

**Backup / restore**
- Export : ajout de `langues` + `wizard_sessions` ; restauration
  générique (PK simple/composite) ; round-trip complet testé sur
  base fraîche (12 tables relues champ à champ), SQLite et PG.

**PostgreSQL réel** : 12/12 `scripts/test_postgres.py`, 8/8 tests
dédiés (`WEBINTEL_REQUIRE_PG_TESTS=1`), 22/22 tests du chantier —
sur vrai serveur PostgreSQL 18.6 (binaires embarqués `pgserver`).

**Tests** : 1090 passed / 17 skipped / 0 failed (baseline v2.2.0 :
1068 / 17 / 0) — zéro régression.

## Chantier MODÈLE SCIENTIFIQUE & TERRAIN (2026-09-23) — code 100 % additif

Audit : noms vernaculaires (original/normalisé/langue jamais devinée),
ethnobotanique (bandeau « USAGE TRADITIONNEL RAPPORTÉ », confidentialité,
informateur anonymisé), chaîne PLANTE→ÉCHANTILLON→VOUCHER→EXTRACTION→
EXTRAIT→FRACTION→COMPOSÉ→ANALYSE, exports publics filtrés (ni GPS précis
ni identités), cartographie avec masquage/floutage GPS, littérature locale
(WBF-AAAA-NNNN marqué « identifiant interne WebIntel »), offline-first
(file de synchronisation, conflits LOCAL/DISTANT/CONFLIT explicites et
journalisés). Lacunes réelles comblées :

- **Validation GPS** (`terrain.valider_gps`) : latitudes/longitudes hors
  plage WGS84 refusées avec `ValueError` explicite — branchée sur
  `creer_echantillon` (service) et sur la création de collecte terrain
  offline (`ui/offline_routes.py`) ; l'API borne déjà le GPS via le
  schéma Pydantic (`ge`/`le`) — défense en profondeur ;
- **Registre des langues persistant** : nouvelle table `langues` (code
  unique, libellé, région, source, actif — jamais de suppression),
  amorçage idempotent des 5 langues au démarrage (`_amorcer_langues`) ;
  `terrain.enregistrer_langue_db` / `lister_langues` / `langue_connue_db`
  ; `ajouter_nom_vernaculaire` accepte désormais les langues déclarées
  en base (la langue reste déclarative : jamais auto-attribuée) ;
  API `GET/POST /science/terrain/langues` ;
- **Normalisation moléculaire** (`science/normalisation_moleculaire.py`) :
  `normaliser_compose` / `normaliser_composes` — SMILES → InChI/InChIKey
  via RDKit ; seuls les champs VIDES sont complétés ; SMILES invalide =
  signalé (statut `invalide`), jamais corrigé ; valeur stockée divergente
  = signalée, jamais écrasée ; sans RDKit = statut `indisponible` ;
  API `POST /science/compounds/{id}/normaliser` (rapport JSON) ;
- **Intégrité des fichiers bruts d'analyse** :
  `terrain.enregistrer_fichier_brut` (chemin + SHA-256, jamais de copie
  en base) et `verifier_integrite_fichier` (fichier corrompu/modifié =
  `correspond: false` signalé ; fichier absent ; sans empreinte =
  invérifiable) ; API `POST .../analyses/{id}/fichier` et
  `GET .../analyses/{id}/integrite` ;
- **Qualité** : `detecter_echantillons_sans_voucher` couvre désormais le
  modèle `Echantillon` (chaîne terrain v2.2, `voucher_id` NULL) en plus
  de l'ancien `Specimen`.

Tests : `tests/test_terrain_complements.py` — 40 tests (GPS, langues,
normalisation, intégrité fichiers, qualité, non-régression
confidentialité export public). `tests/conftest.py` : la table `langues`
est purgée avant chaque test comme les autres tables et réamorcée
(idempotent, comme les connecteurs) — isolation complète.
Suite complète : 979 passés / 17 ignorés / 0 échec AVANT → voir rapport
de fin de chantier pour l'APRÈS.

## v2.2.0 — Espace chercheur : intégration finale des 4 chantiers UI (2026-09-22)

**Intégration** (code 100 % additif des chantiers parallèles, aucun fichier
existant modifié sauf `parametres.html` chirurgical) :
- enregistrement des 4 routers dans `webintel/main.py` (`create_app`) :
  `chercheur_accueil_routes` (`/accueil`, `/plante-etude`, `/recherche-universelle`,
  `/mes-recherches`), `chercheur_etudes_routes` (wizard `/etudes/nouvelle` →
  6 étapes, mode simple/avancé, explications FR prudentes), `chercheur_outils_routes`
  (`/outils-scientifiques`, `/resultats/{type}/{id}/provenance`),
  `chercheur_donnees_routes` (`/donnees/importer`, `/synergie/nouvelle`,
  `/etudes/{id}/rapport`, `/etudes/{id}/historique` + table `study_versions`,
  journal append-only : la restauration crée une NOUVELLE étude, jamais
  d'écrasement) ;
- **ordre d'inclusion** : `ui_chercheur_donnees_routes` AVANT
  `ui_science_comb_routes` — sinon `GET /synergie/{analyse_id}` intercepte
  `/synergie/nouvelle` (422) ; aucun conflit de chemins avec les routes
  existantes (197 routes, vérifié programmatiquement).

**Correction cause racine — interférences suite complète** :
`tests/test_server_config.py` et `tests/test_phase0_server_config.py` font
`importlib.reload(webintel.main)`, ce qui REMPLACE l'objet `app` : les
montages de routers faits à l'import des fichiers UI étaient perdus → 404
en cascade. Corrigé à la racine : les routers sont désormais enregistrés
dans `create_app()` (tout `reload` régénère une app complète) + gardes des
fichiers de tests rendues robustes (détection par `endpoint.__module__`
— FastAPI récent n'aplatit plus les routes incluses, le test sur `path`
ne voyait rien —, `webintel.main.app` résolu à chaque appel, fixture
autouse de remontage). Chaque fichier reste vert en isolation ET en suite
complète ; aucun double-enregistrement.

**Données** :
- `tests/conftest.py` : `StudyVersion` ajoutée à `_TABLES_IN_DELETE_ORDER`
  avant `CombinationStudy` (nettoyage FK-safe, requis sous PostgreSQL) ;
- fixture artémisinine corrigée (`demo/demo_composes.csv`,
  `docs/exemples_import/composes.csv`) : le SMILES et l'InChI d'origine
  étaient invalides (RDKit : « unclosed ring » ; oxygène n° 20 absent de
  la table de connexion) et l'InChIKey ne correspondait pas. Remplacés par
  des valeurs **générées et vérifiées programmatiquement via RDKit** :
  SMILES `C[C@@H]1CC[C@H]2[C@@H](C)C(=O)O[C@@H]3O[C@@]4(C)CC[C@@H]1[C@]32OO4`,
  formule C15H22O5, InChIKey recalculée `BLUAFEHZUWYNDE-NNWCWBAJSA-N`
  (conforme à la référence publiée, l'ancienne clé ne correspondait à
  aucune structure valide),
  descripteurs calculables, pont endoperoxyde + lactone détectés par
  sous-structure. Démo officielle `scripts/demo_science.py` : RÉUSSIE
  (3/3 molécules valides).

**Tests v2.2.0 (vérifiés le 2026-09-22)** : local **656 passed / 0 failed /
16 skipped** ; serveur (`REQUIRE_API_KEY=true`) **657 passed / 0 failed /
15 skipped**. Skips légitimes : PostgreSQL indisponible localement (8),
tests réservés au mode serveur (7), binaire `vina` absent (1).
Aucune régression : l'intégralité de la suite historique reste verte.

**Limites inchangées** : PostgreSQL non re-testé en v2.2.0 (aucun serveur
disponible — le code PG n'a pas changé depuis la validation v2.1.0) ;
Vina/Meeko absents (chemins réels couverts par mocks contrôlés) ;
état wizard en mémoire (pas de persistance inter-redémarrage).

**Correction d'honnêteté scientifique** (`etude_resultats.html`) : la colonne
de similarité du wizard affichait « Tanimoto » alors que la méthode par défaut
« combinee » est une moyenne pondérée d'heuristiques (formule 0.5, inchikey 0.3,
classe 0.2 — non structurelle, documentée dans le moteur). L'en-tête affiche
désormais « Score — méthode « combinee » » (le vrai Tanimoto de Morgan reste
disponible via la méthode « structure » et la variante avancée). 26 tests
wizard toujours verts.

## v2.1.2 — Correction contrat workflow docking → docking_adapter + passage de fiabilisation (2026-09-22)

**Correctif d'intégration** : le workflow `webintel/science/docking.py`
appelait `docking_adapter` avec l'ancien contrat (`recepteur_pdb`) alors que
l'adaptateur v2.0.0 exige `recepteur_pdbqt` — avec Vina installé, toute
exécution réelle aurait fini en « refusé par le moteur ».
- `executer_docking()` transmet désormais `recepteur_pdbqt` au moteur ;
- l'exécution exige un **récepteur PDBQT préparé** (détection des marqueurs
  ROOT/BRANCH/TORSDOF) : un PDB brut est refusé par `ValueError` explicite
  (« préparez avec ADFR/AutoDockTools/OpenBabel — WebIntel ne convertit pas
  PDB → PDBQT »), jamais converti silencieusement ;
- chaîne vérifiée de bout en bout avec le vrai `validate_inputs` de
  l'adaptateur (PDBQT + SMILES acceptés, refus propre sans binaire `vina`) ;
- UI `/docking` : libellé « Récepteur — contenu PDBQT préparé » + aide ;
- tests : 2 nouveaux (`tests/test_science_docking.py` : refus PDB brut,
  détection marqueurs), 17/17 verts ; suite complète 593 local / 594
  serveur, 0 échec.

**Correctif Vina 1.2.x** (découvert lors de la validation du docking réel) :
l'adaptateur passait l'option `--log` (Vina 1.1.x) que le binaire Vina 1.2.x
refuse (« unrecognised option », code 1) — tout docking réel échouait.
`_construire_argv()` ne génère plus `--log` ; le journal est récupéré via
la sortie standard capturée et conservé dans le fichier journal (champ
`journal_vina` inchangé). Test de régression ajouté :
`test_docking_argv_compatible_vina_1_2_x` (garantit l'absence de `--log` et
la présence des options Vina 1.2.x).

**Fiabilisation (audit externe de l'archive — 3 tests en échec corrigés,
aucun problème masqué)** :
- **Logging** (`webintel/core/logging.py`) : l'ancien `propagate = False`
  sur le logger `webintel` rendait la capture des avertissements
  scientifiques (`pytest.caplog`) dépendante de l'ordre des imports — les
  warnings étaient réellement émis mais jamais récupérés par les tests.
  La propagation reste désormais active : la sortie console de production
  est inchangée (le `StreamHandler` dédié demeure), et tout gestionnaire
  racine (tests, agrégation) reçoit les enregistrements de façon
  déterministe. Test de régression ajouté
  (`test_logging_warnings_capturables_via_root_apres_import_app`).
- **Meeko optionnel** : garde-fou de test explicite (`necessite_meeko`,
  comme pour RDKit/Vina) — meeko absent → test ignoré proprement, meeko
  présent → test réellement exécuté. Nouveau `docking_adapter.meeko_disponible()`
  (public) ; nouveau test `test_docking_smiles_sans_meeko_demande_pdbqt_prepare`
  qui vérifie le vrai comportement sans meeko (erreur explicite demandant
  un ligand PDBQT déjà préparé — le logiciel reste utilisable).
- **Page Paramètres → Outils scientifiques** : affiche désormais l'état
  **RDKit / Meeko / Vina** (version + raison d'absence explicite).
- **Extra `science`** : inclut désormais `rdkit` + `meeko`
  (`pip install -e ".[science]"` ou `./scripts/install_science.sh`).
- **Versionnage nettoyé** : `2.1.2` partout (`pyproject.toml`,
  `webintel/__init__.py::__version__` affiché par l'API et l'app,
  CHANGELOG, `docs/livrable.md`) ; archive finale `webintel-v2.1.2.zip`.
- **Validation du docking RÉEL** (2026-09-22, environnement dédié) :
  binaire officiel AutoDock Vina v1.2.7, récepteur c-Abl kinase (PDB 1iep)
  préparé en PDBQT rigide via meeko, ligand imatinib (SMILES → PDBQT par la
  vraie chaîne WebIntel RDKit+meeko), seed 42, paramètres fixes — 5 poses,
  affinités [-8.517, -8.426, -8.337, -8.287, -8.227] kcal/mol, reproductibles
  (2e exécution identique), hash SHA-256 des entrées, refus propre sans
  binaire et sur entrée invalide (aucun faux score). Rapport :
  validation indépendante (script hors paquet), PAS une valeur livrée au
  chercheur : les affinités Vina restent des prédictions in silico, jamais
  des mesures d'efficacité.
- **Nouveau test réel optionnel** `test_docking_reel_binaire_vina_si_present`
  : exécute le VRAI binaire `vina` s'il est dans le PATH (mini-système
  synthétique, vérifie exécution, affinités, version, reproductibilité),
  ignoré proprement sinon (jamais simulé).
- **Nouveaux tests UI Paramètres** (`tests/test_ui_parametres_outils.py`) :
  la page `/parametres` affiche RDKit / Meeko / AutoDock Vina avec leur état
  réel ; Meeko absent → consigne « fournir un ligand PDBQT déjà préparé ».

## v2.1.0 — Modèle scientifique enrichi, fiche plante détaillée, UX chercheur (2026-09-22)

**Modèle** (migration append-only, idempotente, sans casse) :
- `Plant` : morphologie, habitat, références botaniques, matière première,
  préparation ;
- `PlantCompound` : méthode d'analyse, rendement/unité, `evidence_id` ;
- nouveaux : `CompoundTarget`, `PlantActivity`, `Study`, `Provenance`,
  `Dataset` (privé par défaut, rattachable à un projet).

**Fiche scientifique** (`webintel/science/fiche.py`) : identité botanique,
botanique détaillée, pharmacognosie, phytochimie (méthodes, rendements,
preuves pointées), activités pharmacologiques rapportées, cibles
biologiques documentées, littérature structurée, sécurité, bibliographie,
laboratoire privé, simulations. Les preuves pointées par `evidence_id`
des liens, activités et cibles sont toutes résolues et affichées.

**API** : routeur `science_relations` (études, cibles, activités, preuves,
provenance, datasets, mise à jour contrôlée des plantes).

**UX** : recherche plante en premier sur le dashboard, actions visibles
(nouvelle recherche/étude/simulation, import, export), formulaires POST →
redirection GET avec messages, légende permanente des cinq niveaux
(`publie`, `normalise`, `hypothese`, `prediction`, `experimental`),
bandeau sécurité permanent, « non renseignée » quand un champ est absent.

**Exports** : HTML/PDF/Markdown enrichis (morphologie, pharmacognosie,
méthodes, activités, cibles, littérature) ; données privées exclues par
défaut du dossier de recherche.

**Démo** `scripts/demo_science.py` : Moringa oleifera, 3 composés
(HPLC/LC-MS, rendements, DOI), cible COX-2, activité antioxydante in
vitro, analyse RDKit réelle, comparaison structurale (score
informatique, jamais pharmacologique), docking ignoré proprement
(Vina absent), simulation statistique reproductible.

**Tests** : `tests/test_science_v21.py` (20 tests), suite complète
**591 verts en local / 592 en serveur, 0 échec** ; 7 tests PostgreSQL
contre serveur réel ; installation fraîche testée depuis le ZIP.

Limites honnêtes : PostgreSQL production non validé ; SMILES
d'artémisinine de la fixture invalide pour RDKit (signalé, non inventé) ;
simulations = prédictions, jamais des preuves.

## v2.1.1 — Travaux techniques préalables : PostgreSQL réel + anti-SSRF TOCTOU (2026-09-22)

**PostgreSQL validé contre un vrai serveur** (plus « code écrit = validé ») :
- suite `tests/test_phase0_postgres.py` (7 tests) exécutée contre
  PostgreSQL 16 réel : `create_all`/`init_db`, JSON, index, contraintes
  uniques, upserts (50 € → 60 € → 50 €), `upsert_source_observation`,
  idempotence sous contention (8 threads → 1 ligne) ;
- `tests/test_postgres_gate.py` : si `WEBINTEL_REQUIRE_PG_TESTS=1` est
  posé et qu'aucun serveur n'est joignable, le test ÉCHOUE (pas de skip
  silencieux) ; sans la variable, les tests PG restent skippés
  proprement ;
- fail-fast au démarrage : quand `DATABASE_URL` pointe vers PostgreSQL,
  `verifier_demarrage_postgres()` vérifie la connexion (timeout 5 s)
  avant `init_db()` puis le schéma minimal après — `RuntimeError`
  explicite (mot de passe masqué), jamais de démarrage dégradé silencieux ;
- `psycopg[binary]` conservé dans `requirements.txt`.

**Anti-SSRF TOCTOU corrigé** (`webintel/security/pinned_transport.py`) :
- `PinnedHTTPTransport` : résolution DNS unique et validée
  (`ssrf.resoudre_ip_epinglee`), connexion directe à l'IP épinglée,
  en-tête `Host` et SNI TLS préservés, certificat vérifié contre le nom
  d'hôte d'origine — plus de seconde résolution DNS au moment de la
  connexion ;
- appliqué au fetcher, à robots.txt, aux connecteurs (`_telecharger` +
  `tester_statut()`) et aux webhooks ;
- tests de régression `tests/test_ssrf_pinning.py` : DNS simulé basculant
  public → privé entre validation et connexion → bloqué, aucune requête
  vers l'IP privée. Limites résiduelles : `docs/securite.md` §1.1.

**Documentation fidèle au code** : `docs/livrable.md` ne revendique plus
que PubChem + PubMed comme connecteurs implémentés (ChEMBL/GBIF/OpenAlex
→ roadmap honnête) ; `docs/securite.md`, `docs/roadmap.md`,
`docs/architecture.md`, `docs/connecteurs.md` et `docs/postgresql.md`
(§8) mis à jour.

## v2.1.0 — Runtime scientifique optionnel + chimoinformatique réelle (2026-09-22)

**Niveau CORE vs SCIENCE** (`docs/runtime_scientifique.md`) :
- le mode simple reste léger (aucune dépendance optionnelle) ; le niveau
  SCIENCE s'active par `pip install -e ".[science]"` (extra `science` :
  `rdkit`, voir `pyproject.toml`) ou `./scripts/install_science.sh`
  (idempotent, vérification fonctionnelle incluse) ;
- page **Paramètres** : bloc « Outils scientifiques » (RDKit présent/absent
  + version, Vina présent/absent + version, instructions d'installation) ;
- sans RDKit/Vina : l'app tourne et indique explicitement ce qui n'est pas
  calculable — **aucune valeur inventée**.

**Nouveau module `webintel/science/chimie.py`** (contrat stable pour les
autres chantiers) : `rdkit_disponible()`, `empreinte_morgan()`,
`tanimoto_fp()` (arithmétique pure, sans RDKit), `descripteurs()` (11 clés :
formule, masse_molaire, logp, hbd, hba, tpsa, rotatable_bonds,
fraction_csp3, lipinski_violations, inchi_key, smiles_canonique),
`sous_structure()`, `infos_biblio()` (+ bonus `diagnostic_smiles()`).
Entrées strictement validées ; SMILES invalide → `None` + raison
journalisée, jamais d'exception brute, jamais de valeur inventée.

**`molecular_similarity` v1.1.0** : avec RDKit + SMILES → similarité
structurelle réelle (Tanimoto sur empreintes de Morgan) + comparaison par
sous-structure ; distinction EXPLICITE `formule_similaire` /
`structure_similaire` / `classe_chimique_similaire` + avertissement
obligatoire (« une ressemblance de formule n'est PAS une similarité
pharmacologique ») ; sans RDKit : heuristique inchangée, étiquetée
« heuristique sans RDKit, non structurale ».

**`property_prediction` v1.1.0** : avec RDKit + SMILES → logP, HBD/HBA,
TPSA, liaisons rotatives, fraction Csp3, Lipinski complet réellement
calculés ; chaque résultat enregistre `provenance_calcul` (méthode,
bibliothèque, version, entrée, limites) ; sans RDKit/SMILES → `non_calcule`
+ raison (jamais inventé), Lipinski `partiel`.

**`docking_adapter` v2.0.0** (bug corrigé : `is_available()` détectait Vina
mais `run()` refusait systématiquement) : détection réelle du binaire
`vina` dans le PATH + version capturée ; si présent, `run()` exécute un
VRAI docking local (`argv` fixe, `shell=False`, boîte bornée, seed fixé =
42 par défaut, délai max 600 s, hash SHA-256 des entrées) ; si absent, refus
propre (`RuntimeError` explicite). Entrées : `recepteur_pdbqt` +
`ligand_pdbqt`/`ligand_smiles` (SMILES → PDBQT via RDKit + meeko,
préparation documentée ; sinon PDBQT exigé). Sorties : affinités, poses
PDBQT, RMSD, commande exacte, version Vina, avertissement « résultat
informatique ». Dérogation documentée au contrat plugin (subprocess sans
shell + fichiers temporaires confinés).

**Tests** : `tests/test_science_runtime.py` (32 tests) — contrat chimie,
RDKit présent/absent, similarité réelle, propriétés réelles, docking
(détection, validation stricte, run réel via sous-processus mocké de façon
contrôlée, échec Vina sans faux résultat, aucune valeur inventée),
bloc UI Paramètres.

## v2.0.0 — Phases 13–15 (2026-09-22)

**Phase 13 — audit immuable des simulations, anti-confusion**
(`docs/simulations.md`) :
- audit complet et immuable de chaque exécution : `executed_by`
  (`clé:<préfixe>`, `ui:<préfixe>` ou `local` — jamais la clé complète),
  `run_at`, `input_hash` (SHA-256), `inputs` persistées, paramètres,
  sorties, moteur/version ; migration idempotente pour les bases
  existantes ;
- champs d'audit non modifiables (`PATCH` → 422) et non supprimables
  (pas de route DELETE) ; historique append-only dans l'UI avec badge
  « Prédiction — à confirmer expérimentalement » ;
- rapport reproductible exportable (JSON/HTML/Markdown) :
  INPUTS → METHOD → PARAMETERS → OUTPUTS → REFERENCES → LIMITATIONS,
  avec avertissement obligatoire ;
- rejeu append-only (`POST …/rejouer`) : mêmes entrées → même hash →
  mêmes sorties, nouvelle ligne d'audit ;
- sécurité : `validate_url()` avant tout téléchargement de connecteur ;
  revue dans `docs/securite.md` — **anti-SSRF TOCTOU (P1) explicitement
  non implémenté** et documenté comme limite.

**Phase 14 — suite comportementale + démo reproductible** :
- `tests/test_science_comportement.py` (10 tests) : séparation stricte
  des 5 niveaux, synthèse IA `hypothese` jamais persistée, données
  privées exclues par défaut (recherche, CSV, dossier ZIP),
  réimport idempotent (0 doublon), provenance enregistrée (fichiers et
  connecteurs avec licence), rejouabilité (même hash → mêmes sorties) ;
- scénario démo hors-ligne idempotent : `./scripts/demo.sh`
  (import → fiche plante → simulation → dossier ZIP → fiche PDF),
  données dédiées `demo/`, documentation `docs/demo.md` ;
- **tests : local 451 passed / 13 skipped / 0 failed ;
  serveur (`REQUIRE_API_KEY=true`, `SECRET_KEY` forte) 452 passed /
  12 skipped / 0 failed** ; PostgreSQL non validé (aucun serveur
  disponible, 6 tests skippés).

**Phase 15 — livrable** : `docs/livrable.md` (12 statuts factuels),
archive `webintel-v2.0.0.zip` testée depuis un répertoire frais
(install, démarrage, UI 200, création d'une plante par API, nettoyage).

## Phases 11–12 (2026-09-22)

**Phase 11 — IA complètement désactivable** (`docs/ia.md`) :
- garde-fou global `WEBINTEL_AI_ENABLED` (variable d'environnement, lue au
  démarrage) + case à cocher page **Paramètres** (effet immédiat, surcharge
  la variable via `webintel_data/runtime_config.json`, réinitialisable) ;
- quand l'IA est désactivée : aucune fonction IA n'est exécutée ni affichée
  (section « Synthèse IA » absente de la fiche plante, API en 503 explicite
  « fonctionnalité désactivée », garde-fou levé *avant* tout accès aux
  données) ;
- synthèse automatique d'une fiche plante (`GET /api/science/ia/synthese/plante/{id}`,
  page `/plantes/{id}/synthese-ia`) : assemblage déterministe des preuves et
  liens enregistrés, **toujours** étiquetée `hypothese`, sources citées,
  avertissement obligatoire, **jamais persistée**, données privées du
  laboratoire jamais utilisées.

**Phase 12 — installation ultra simple** (`docs/installation.md`) :
- `install.sh` / `start.sh` (Linux/macOS) et `install.bat` / `start.bat`
  (Windows) : vérification Python 3.10+, `.venv`, dépendances, base SQLite
  locale initialisée ; scripts idempotents ;
- README et documentation à jour (modes local SQLite / serveur PostgreSQL,
  dépannage, limites honnêtes).

## v1.1.1 (2026-09-22)

Passe solidité du noyau data (2ᵉ audit) : authentification de l'UI en mode
serveur (`REQUIRE_API_KEY=true`, session par cookie signé), vraie table
`sources`, discipline stricte `Entity.data` (identité uniquement),
historique avec provenance, `value_hash` + unicité portable idempotente
sous concurrence, migration idempotente v1.1 → v1.1.1.

## v1.1 (2026-09-22)

Correctifs P0 du 1er audit : PostgreSQL via psycopg 3, docker-compose
corrigé, upsert `SourceObservation` concurrent-safe (cas 50 € → 60 € →
50 €), `SECRET_KEY` par défaut/faible refusée en mode serveur, cookies
sécurisés en HTTPS.

## Phases 1–10 (2026-09-22)

Plateforme scientifique : modèles `Plant`, `Compound`, `PlantCompound`,
`Evidence`, `ResearchObservation`, `Simulation` ; 5 niveaux de données
séparés ; fiche plante 9 sections ; recherche scientifique ; moteurs de
simulation honnêtes (rapports reproductibles) ; workflows chercheur ;
laboratoire (public/privé) ; import/export (CSV, JSON, Excel, SDF minimal,
PDF, dossier ZIP) ; connecteurs PubChem / PubMed en lecture seule.
