# WebIntel — Plateforme d'intelligence Web

> **Positionnement (v3.0.0)** : WebIntel est une plateforme
> d'**intelligence Web généraliste et online-first** : rechercher sur le
> Web, rechercher une entité, analyser une URL, collecter des
> informations, recouper plusieurs sources, détecter des changements,
> surveiller des sujets/entités/pages, produire des rapports sourcés et
> conserver son historique. Chaque donnée importante reste reliée à sa
> source (entité → source → observation → date → instantané) ; une
> inférence est toujours distinguée d'un fait observé.
>
> Le **domaine scientifique** (plantes médicinales, pharmacognosie,
> phytochimie — le produit v2.x, pensé pour le Burkina Faso, français
> langue première) est conservé intégralement comme domaine spécialisé
> accessible depuis la navigation.

## Ce qui est VRAI (v3.0.0, vérifié)

- **Recherche Web réelle sans aucune clé** : abstraction Providers avec
  fallback automatique (`webintel/providers/`) — Wikipedia (FR/EN),
  DuckDuckGo Instant Answers, GitHub public et RSS actualités
  fonctionnent immédiatement ; Brave Search, Tavily, SerpApi et NewsAPI
  s'ajoutent en option via des clés dans un fichier `.env` à la racine
  (voir `.env.example`), statut visible dans Paramètres → « Services &
  clés API » (jamais la valeur). Écran `/recherche-web`, API
  `GET /api/web/recherche`.
- **Design System unique + responsive réel** : sidebar desktop,
  bottom navigation mobile, états Loading/Success/Empty/Error/Retry/
  Offline, progression de recherche en 6 étapes. Captures 1366px et
  390px des écrans principaux fournies avec la livraison.
- **Favoris / Collections** et nouveaux écrans `/sources` et
  `/comparaison` (valeurs observées uniquement, jamais inventées).
- **Moteur conservé** : crawl/extraction hybride, entités, observations
  par source, snapshots, diffs, événements, moniteurs, alertes, exports,
  anti-SSRF strict sur toute URL fournie par l'utilisateur.
- **Socle** : FastAPI (`create_app()`), SQLAlchemy 2, Python 3.10+ (testé 3.12),
  Jinja2. SQLite en mode local (zéro configuration), PostgreSQL validé contre
  un **vrai serveur PG 18.6** (12/12 `scripts/test_postgres.py`, 8/8 tests
  dédiés avec gate stricte, 22/22 tests du chantier).
- **Historique v2.4.0 — ça produit dès l'ouverture** : seed automatique et
  idempotent au premier démarrage (13 plantes médicinales d'Afrique de
  l'Ouest, 5 composés, 4 liens publiés/rapportés, 2 preuves, chacun avec
  provenance « jeu de démonstration intégré ») ; bouton « Rechercher en
  ligne » (PubChem/GBIF/PubMed) depuis la recherche chercheur, avec
  aperçu sourcé et import en un clic ; RDKit installé par défaut
  (descripteurs et similarité hors ligne sans étape manuelle). Détail :
  `REPAIR_REPORT.md`.
- **Socle** : FastAPI (`create_app()`), SQLAlchemy 2, Python 3.10+ (testé 3.12),
  Jinja2. SQLite en mode local (zéro configuration), PostgreSQL validé contre
  un **vrai serveur PG 18.6** (12/12 `scripts/test_postgres.py`, 8/8 tests
  dédiés avec gate stricte, 22/22 tests du chantier).
- **Tests mesurés sur cette version : 1171 passés / 17 ignorés / 0 échec**
  (suite complète). 52 tables, index FK à 100 %, pagination
  `limit`/`offset` sur 11 listes.
- **5 niveaux de preuve strictement séparés** : `publie`, `normalise`,
  `hypothese`, `prediction`, `experimental` — jamais mélangés (testé).
- **Ethnobotanique protégée** : bandeau obligatoire
  « USAGE TRADITIONNEL RAPPORTÉ », 5 niveaux de confidentialité
  (`public`/`projet`/`chercheur`/`restreint`/`confidentiel`), noms
  vernaculaires avec langue **jamais devinée** (registre amorcé : Mooré,
  Dioula, Fulfuldé, Gulmancéma, Bissa).
- **Traçabilité terrain** : chaîne
  PLANTE → ÉCHANTILLON → VOUCHER → EXTRACTION → EXTRAIT → FRACTION →
  COMPOSÉ → ANALYSE ; GPS validé WGS84 ; cartographie avec masquage/floutage
  selon confidentialité.
- **Chimoinformatique réelle** (RDKit, installé par défaut) :
  normalisation (SMILES canonique, InChI, InChIKey ; l'invalide est **signalé,
  jamais corrigé**), empreintes de Morgan, similarité de Tanimoto réelle,
  descripteurs (logP, HBD, HBA, TPSA, liaisons rotatables, fraction Csp3,
  Lipinski). Docking **AutoDock Vina réel** si le binaire est installé —
  **jamais de faux score** (l'aperçu renvoie toujours `scores: null`).
- **Études versionnées append-only** (`study_versions`) : nouvelle exécution =
  nouvelle étude, comparaison structurée v1/v2 (5 sections + SHA-256).
  Wizard d'étude en 6 étapes, **persisté en base** (`wizard_sessions`,
  TTL 30 jours ; les études terminées survivent au redémarrage).
- **Offline-first** : file de synchronisation, statuts LOCAL / DISTANT /
  CONFLIT avec résolution explicite locale-vs-distante.
- **API réelle** : 215 chemins OpenAPI (GET 145 / POST 106 / PUT 2 / DELETE 4),
  clé API en mode serveur, isolation stricte par projet, rate limits
  (60 req/60 s ; 5 tentatives login/300 s), anti-SSRF à connexion épinglée,
  CSP stricte.
- **Rapports reproductibles** : Markdown / HTML / PDF / ZIP avec provenance
  QUI / QUOI / QUAND / OÙ / SOURCE / COMMENT / OUTIL / VERSION / PARAMÈTRES
  sur chaque résultat.

## Démarrage rapide (chercheur, 5 minutes)

```bash
./install.sh     # crée l'environnement, installe les dépendances, initie SQLite
./start.sh       # → http://127.0.0.1:8000
```

Puis, dans le navigateur : **Accueil → « Que souhaitez-vous étudier ? »**
(plante, molécule, publication, nouvelle étude, mes recherches).
Voir [`docs/INSTALLATION.md`](docs/INSTALLATION.md) (parcours complet) et
[`docs/RESEARCHER_GUIDE_FR.md`](docs/RESEARCHER_GUIDE_FR.md) (guide pas à pas).

```bash
./scripts/install_science.sh   # niveau SCIENCE : RDKit + Meeko (optionnel)
```

## Documentation

| Document | Contenu |
|---|---|
| [`docs/INSTALLATION.md`](docs/INSTALLATION.md) | Installation → diagnostic → niveau scientifique → lancement |
| [`docs/RESEARCHER_GUIDE_FR.md`](docs/RESEARCHER_GUIDE_FR.md) | Guide du chercheur non-développeur (accueil → rapport) |
| [`docs/SCIENTIFIC_METHODS.md`](docs/SCIENTIFIC_METHODS.md) | Méthodes : RDKit, Tanimoto, descripteurs, combinaisons, synergie, docking — et leurs limites |
| [`docs/ETHNOBOTANY.md`](docs/ETHNOBOTANY.md) | Couche ethnobotanique, confidentialité, protection des savoirs |
| [`docs/SAMPLE_MANAGEMENT.md`](docs/SAMPLE_MANAGEMENT.md) | Échantillons, vouchers, chaîne de traçabilité, GPS |
| [`docs/OFFLINE_MODE.md`](docs/OFFLINE_MODE.md) | Offline-first, file de synchronisation, conflits |
| [`docs/DATA_GOVERNANCE.md`](docs/DATA_GOVERNANCE.md) | Niveaux de preuve, provenance, qualité, règles d'intégrité |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Surfaces auditées, variables, rate limits, limites honnêtes |
| [`docs/API.md`](docs/API.md) | Endpoints réels (générés depuis l'OpenAPI du code) |
| [`docs/DATABASE.md`](docs/DATABASE.md) | Modèles, index, pagination, backup/restore, SQLite vs PG |
| [`docs/LICENSES.md`](docs/LICENSES.md) | Licences : application, RDKit, Meeko, Vina, connecteurs |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Problèmes réels et solutions |
| [`CHANGELOG.md`](CHANGELOG.md) | Journal des versions |
| [`docs/livrable.md`](docs/livrable.md) | Bilan factuel de vérification v2.3.0 |

Les anciens documents en minuscules (`docs/installation.md`, `docs/securite.md`,
…) restent consultables ; les documents en MAJUSCULES ci-dessus sont la
référence à jour pour la v2.4.0.

## Limites honnêtes (lire avant tout le reste)

- **Les simulations sont des prédictions, jamais des preuves.** Les
  formulations « efficace », « sûr », « traite » sont **interdites par test**
  dans tout contenu généré — sauf source expérimentale/clinique explicite.
  Tanimoto ≠ similarité pharmacologique ; docking ≠ efficacité.
- Le binaire **AutoDock Vina n'est pas fourni** : sans lui, le docking refuse
  proprement (aucun score inventé). RDKit/Meeko s'installent via
  `scripts/install_science.sh`.
- **PostgreSQL validé fonctionnellement** (vrai serveur 18.6), mais **pas de
  test de charge** en production ; `install.bat` jamais exécuté sur un vrai
  Windows ; les sessions navigateur expirent selon la configuration.
- Données privées du laboratoire : **jamais envoyées à un service externe**
  sans action explicite du chercheur (règle produit, `WEBINTEL_AI_ENABLED`
  désactivable).
- Ce logiciel **n'est pas un dispositif médical** et ne remplace ni
  l'expérimentation réelle ni l'avis d'un pharmacologue/toxicologue.

## Historique résumé

- **v2.4.0 (2026-10-03)** — réparation chercheur : seed de démarrage
  sourcé (13 plantes), recherche en ligne PubChem/GBIF/PubMed depuis
  l'UI, whitelist anti-SSRF scientifique, RDKit par défaut.
  Tests : **1171/17/0**. Voir `REPAIR_REPORT.md`.
- **v2.3.0 (2026-09-23)** — persistance (wizard en DB, études versionnées
  append-only), performance (index FK 100 %, N+1 éliminés, pagination),
  backup/restore round-trip vérifié, PG 18.6 réel. Tests : **1090/17/0**.
- **v2.2.0** — espace chercheur : accueil, wizard 6 étapes, outils
  scientifiques, import labo, synergie simplifiée, historique versionné.
- **v2.1.x** — plateforme scientifique : modèle enrichi, RDKit réel, docking
  Vina réel, combinaisons (dossier 12 points), synergie Bliss/Loewe/HSA/ZIP.
- **v2.0.0** — audit immuable des simulations, rapports reproductibles.
- **v1.1.1** — solidité du noyau data (auth UI, discipline Entity/observations).

Détail : [`CHANGELOG.md`](CHANGELOG.md).
