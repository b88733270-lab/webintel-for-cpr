# WebIntel — Documentation de l'API REST

Base : `http://127.0.0.1:8000` — préfixe `/api`. Documentation interactive
(OpenAPI) sur `/docs`. Toutes les réponses d'erreur sont en JSON
`{"detail": "..."}` avec un message en français.

## Authentification

En-tête `X-API-Key: <clé>`.

- **Mode développement** (défaut, `REQUIRE_API_KEY=false`) : l'en-tête est
  ignoré (bypass avec log debug).
- **Production** (`REQUIRE_API_KEY=true`) : la clé doit avoir été créée via
  `POST /api/projects/{id}/api-keys` ; elle est vérifiée par son hash SHA-256.
  Sans clé valide : `401`.
- **Interface web en mode serveur** : `POST /login` avec la clé API → cookie
  de session signé (`webintel_ui_session`, 24 h, `HttpOnly`, `SameSite=Lax`) ;
  toute page sans session → `303` vers `/login` ; `GET /logout` purge la
  session. Le téléchargement d'export accepte la session du navigateur en
  plus de `X-API-Key` (v1.1.1).

```bash
curl -H "X-API-Key: wi_ma_cle" http://127.0.0.1:8000/api/health
```

### Isolation multi-projet (v1.1)

En production, chaque clé API est **rattachée à UN projet** (`api_keys.project_id`)
et ne peut accéder qu'à lui :

- `GET /api/projects/{autre_id}`, `/api/entities/{id}` d'un autre projet, etc.
  → **403** (contrôle effectué AVANT toute lecture/écriture ; la 403 prime sur
  la 404 pour ne pas révéler l'existence des projets étrangers).
- Les endpoints de **liste** (`/api/projects`, `/api/entities`, `/api/crawls`,
  `/api/monitors`, `/api/events`, `/api/alerts`) sont **cloisonnés par défaut** :
  sans `project_id`, ils ne retournent que les données du projet de la clé ;
  avec un `project_id` étranger → **403**.
- Les endpoints de **création** (`/api/analyze`, `/api/crawls`, `/api/monitors`,
  `/api/exports`) utilisent le projet de la clé si `project_id` est absent,
  et refusent (403) un `project_id` étranger.
- **Pilotage des jobs** (`/api/crawls/{id}/cancel|pause|resume`) et
  **téléchargements** (`/api/exports/{id}/download`) : le contrôle d'accès est
  vérifié sur la ligne DB **avant** toute action (pas d'oracle sur l'existence
  du fichier).
- `POST /api/projects` avec une clé → **403** : la création d'un projet est une
  opération de bootstrap, via `python3 scripts/create_api_key.py --project "Nom"`.
- `GET /api/usage` : compteurs **cloisonnés** au projet de la clé
  (`GET /api/health` reste ouvert pour les sondes).

```bash
curl -H "X-API-Key: wi_ma_cle" http://127.0.0.1:8000/api/projects/2
# → 403 si la clé appartient au projet 1
```

## Système

### `GET /api/health`
État de l'application.

```bash
curl http://127.0.0.1:8000/api/health
```
```json
{"status": "ok", "version": "1.0.0", "db": "ok", "scheduler": "inactif"}
```

### `GET /api/usage`
Compteurs : projets, entités, snapshots, événements (24 h), alertes, jobs en cours.

## Projets

### `POST /api/projects` — créer un projet
```bash
curl -X POST http://127.0.0.1:8000/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "Veille prix"}'
```
```json
{"id": 1, "name": "Veille prix", "created_at": "2026-09-22T13:00:00"}
```

### `GET /api/projects` — lister · `GET /api/projects/{id}` — détail

### `POST /api/projects/{id}/api-keys` — créer une clé API
Body : `{"name": "robot-etl", "quotas": {"max_pages_day": 1000}}`.
⚠️ La clé en clair n'est affichée **qu'une seule fois** dans la réponse
(`{"id": 3, "name": "robot-etl", "prefix": "a1b2c3d4", "key": "wi_..."}`).
Seuls le hash et le préfixe sont stockés.

### `GET /api/projects/{id}/api-keys` — lister les clés (préfixes, révocations)

## Analyse

### `POST /api/analyze` — analyser une URL (fiche intelligence)
```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"url": "https://boutique.example.com/produit", "project_id": 1}'
```
Réponse (extraits) :
```json
{
  "project_id": 1,
  "url": "https://boutique.example.com/produit",
  "final_url": "https://boutique.example.com/produit",
  "status_code": 200,
  "page_type": "product",
  "page_type_score": 1.0,
  "title": "Casque Audio X200",
  "entities": [
    {"id": 7, "entity_key": "product:9f2c…", "entity_type": "product",
     "name": "Casque Audio X200",
     "data": {"name": "Casque Audio X200", "brand": "SonicBrand",
              "price": {"amount": 49.99, "currency": "EUR"},
              "availability": "in_stock"},
     "sources": ["https://boutique.example.com/produit"]}
  ],
  "prices": [{"amount": 49.99, "currency": "EUR", "source": "entite-produit"}],
  "contacts": {"emails": [], "phones": [], "socials": []},
  "diff": {"level": "none", "summary_fr": "Première capture de cette page."},
  "events": [],
  "quality": {"completeness_score": 83, "missing_fields": ["contenu substantiel (>= 300 mots)"]}
}
```
Erreurs : `422` si l'URL est refusée (anti-SSRF), `502` si la récupération échoue.

## Crawls (jobs asynchrones)

### `POST /api/crawls` — lancer un crawl borné
```bash
curl -X POST http://127.0.0.1:8000/api/crawls \
  -H "Content-Type: application/json" \
  -d '{"project_id": 1,
       "start_url": "https://example.com/",
       "limits": {"max_pages": 50, "max_depth": 2, "delay_s": 1.0,
                  "concurrency": 2, "obey_robots": true}}'
```
`start_url` | `start_urls` | `domain` (un seul requis). Réponse :
`{"id": 12, "status": "queued", "progress": {...}}`.
États : `queued → running → (paused ↔ running) → completed | failed | cancelled`.

### `GET /api/crawls?project_id=1` — lister les jobs
### `GET /api/crawls/{id}` — état + progression
```json
{"id": 12, "kind": "crawl", "status": "running",
 "progress": {"visited": 18, "discovered": 64, "remaining": 46, "errors": 0},
 "error": null, "created_at": "…", "updated_at": "…"}
```
### `POST /api/crawls/{id}/cancel` · `/pause` · `/resume` — piloter un job

## Entités

### `GET /api/entities` — rechercher/lister
Filtres : `project_id`, `type` (product|article|company|webpage), `q`
(texte), `min_price`, `max_price`, `domain`, `since` (ISO), `limit` (défaut 50).
```bash
curl "http://127.0.0.1:8000/api/entities?project_id=1&type=product&max_price=50&q=casque"
```
**Discipline v1.1.1** : `min_price`/`max_price` sont évalués sur les
**dernières observations `price` par source** (aucun prix global stocké).
Chaque résultat expose `prices` — la liste des prix observés par source
(`[{source_id, source_domain, amount, currency, observed_at}]`) — et jamais
un champ `price` unique ambigu. `Entity.data` ne contient que
l'identité/synthèse (nom, marque, SKU…) : ni prix, ni disponibilité, ni note.

### `GET /api/entities/{id}` — détail (+ observations par source)
Retourne l'entité **et** `observations_by_source` : les observations métier
groupées par hôte source (`{domaine: [{id, entity_id, source_id, source_name,
field, value, confidence, observed_at, snapshot_id}]}`) — v1.1, P0-3 ;
`source_id`/`entity_id` ajoutés en v1.1.1.
### `GET /api/entities/{id}/history` — journal des changements (avec provenance)
Chaque entrée expose `source_id`, `source_name`, `source_url`,
`source_domain` : le changement est directement attribué à la source qui
l'a produit (v1.1.1).

## Sources (v1.1.1)

### `GET /api/sources?project_id=1` — lister les sources du projet
Sources ayant produit ≥ 1 observation pour le projet (ordre alphabétique) :
`[{id, name, canonical_host, country, seller, observation_count,
last_observed_at}]`. Sans `project_id` : projet de la clé (mode serveur) ou
liste vide (mode local).
```bash
curl -H "X-API-Key: <clé>" "http://127.0.0.1:8000/api/sources?project_id=1"
```

### `GET /api/sources/{id}?project_id=1` — détail d'une source
`{…, observation_count, observations: [{id, entity_id, source_id,
source_name, field, value, observed_at, …}]}` — chaque observation est
rattachée à une entité **du projet demandé** (cloisonnement strict : 404 si
la source n'a rien observé pour ce projet).

## Moniteurs

### `POST /api/monitors` — créer un moniteur
```bash
curl -X POST http://127.0.0.1:8000/api/monitors \
  -H "Content-Type: application/json" \
  -d '{"project_id": 1, "name": "Prix X200", "target_type": "product",
       "target": "https://boutique.example.com/produit", "interval_minutes": 60,
       "config": {"rules": [
         {"all": [{"field": "price.amount", "op": "lt", "value": 45}],
          "channel": "log"}]}}'
```
`target_type` : `url` | `domain` | `product` | `price` | `keyword`.
Règles : `{"all"|"any": [{"field": "price.amount", "op": "lt|gt|eq|ne|contains|changed", "value": …}]}`.
Champs résolus dans l'ordre `event`, `entity`, `diff` (ex. `price.amount`,
`event.type`, `text_contains`). `channel` : `log` | `webhook`
(`webhook_url` dans la règle ou la config, URL revalidée anti-SSRF).

### `GET /api/monitors?project_id=1` · `GET /api/monitors/{id}` · `DELETE /api/monitors/{id}`
### `POST /api/monitors/{id}/run` — exécution immédiate (job `monitor_run`)
Réponse : `{"monitor_id": 4, "urls_checked": 1, "events": […], "alerts": [{"id": 9, "channel": "log", "status": "sent"}]}`.

## Événements

### `GET /api/events` — journal des changements détectés
Filtres : `project_id`, `type` (`price_drop`, `price_increase`,
`back_in_stock`, `out_of_stock`, `new_product`, `new_article`,
`article_updated`, `new_company`, `page_changed`, `seo_changed`,
`technology_changed`), `since`.
```bash
curl "http://127.0.0.1:8000/api/events?project_id=1&type=price_drop"
```

## Alertes

### `GET /api/alerts?project_id=1`
`{"id": 9, "monitor_id": 4, "channel": "log", "status": "sent", "payload": {…}, "rule": {…}}`.
Statuts : `sent` | `failed` | `pending`.

## Recherche

### `GET /api/search`
Mêmes filtres que `/api/entities` (`q`, `type`, `min_price`, `max_price`, `domain`).
```bash
curl "http://127.0.0.1:8000/api/search?q=casque&type=product&max_price=60"
```

## Exports

### `POST /api/exports` — construire un fichier
```bash
curl -X POST http://127.0.0.1:8000/api/exports \
  -H "Content-Type: application/json" \
  -d '{"project_id": 1, "kind": "entities", "format": "csv"}'
```
`kind` : `entities` | `events` | `snapshots` | `monitors` | `observations` (v1.1 :
une ligne par observation source — `entity_id`, `source_url`, `source_domain`,
`field`, `value`, `confidence`, `observed_at` ; v1.1.1 : colonnes `source_id`
et `value_hash` ajoutées).
`format` : `json` | `csv` | `xlsx`.
Réponse : `{"id": 5, "download_url": "/api/exports/5/download"}`.

### `GET /api/exports/{id}/download` — télécharger le fichier
```bash
curl -OJ http://127.0.0.1:8000/api/exports/5/download
```
Les fichiers sont écrits sous `EXPORT_DIR` (défaut `./webintel_data/exports`).
En mode serveur, le téléchargement accepte l'en-tête `X-API-Key` **ou** la
session UI du navigateur (v1.1.1).

## API scientifique (/api/science)

Données scientifiques partagées (**plantes, composés, associations,
preuves**) : publiques, sans cloisonnement par projet. Données de laboratoire
(**observations, simulations**) : cloisonnées au projet via
`scoped_project_id` / `require_project_access` (même règle que les entités).

Chaque objet scientifique porte un `data_level` qui dit ce qu'est la donnée :
`publie` | `normalise` | `hypothese` | `prediction` | `experimental`
(voir `GET /api/science/data-levels`). Une simulation n'est jamais une preuve
expérimentale : `SimulationOut` inclut toujours un `avertissement`
(« Simulation — à confirmer expérimentalement… »).

### `GET /api/science/data-levels` — référentiel des niveaux de preuve
`{"levels": [{"code", "label", "description", "badge_couleur"}]}` pour les 5
niveaux : `publie` (« Publié »), `normalise` (« Normalisé »), `hypothese`
(« Hypothèse »), `prediction` (« Prédiction »), `experimental`
(« Expérimental »).

### `POST /api/science/plants` — créer une fiche plante
Corps : `PlantCreate`. **Idempotent** sur `scientific_name` : `201` +
`PlantOut` à la création, `200` + fiche existante si déjà présente.
`data_level` retourné : selon la source déclarée (`publie` le plus souvent).

### `GET /api/science/plants?q=&limit=` — recherche de plantes
Recherche par nom scientifique ou vernaculaire. Réponse : liste de `PlantOut`
(chacun avec son `data_level`).

### `GET /api/science/plants/{plant_id}` — détail d'une plante
`PlantOut` (+ `data_level`). `404` « Plante introuvable » si absente.

### `POST /api/science/compounds` — créer une fiche composé
Corps : `CompoundCreate`. **Idempotent** sur `name` : `201` / `200`.
Un InChIKey déjà rattaché à un autre composé → `409` (message FR).
`data_level` retourné : selon la source déclarée.

### `GET /api/science/compounds?q=&limit=` — recherche de composés
Par nom, formule ou identifiant (InChIKey…). Réponse : liste de `CompoundOut`.

### `GET /api/science/compounds/{compound_id}` — détail d'un composé
`CompoundOut` (+ `data_level`). `404` « Composé introuvable » si absent.

### `POST /api/science/plants/{plant_id}/compounds` — associer un composé
Corps : `PlantCompoundCreate` (sans `plant_id`, repris du chemin :
`compound_id`, `plant_part`, `publication`…). **Idempotent** : `201` /
`200`. `404` si la plante ou le composé est absent. `data_level` retourné :
celui de l'association.

### `GET /api/science/plants/{plant_id}/compounds` — composés d'une plante
Liste des `PlantCompoundOut` de la plante. `404` si la plante est absente.

### `POST /api/science/evidences` — créer une preuve
Corps : `EvidenceCreate` (référence bibliographique, DOI…). **Idempotent**
sur le DOI : `201` / `200`. `data_level` retourné : `publie` (une preuve est
une référence publiée).

### `GET /api/science/evidences?doi=&limit=` — lister les preuves
Filtrable par DOI exact (`?doi=10.xxxx/yyyy`). Réponse : liste d'`EvidenceOut`.

### `POST /api/science/observations` — observation de laboratoire (privé)
Corps : `ResearchObservationCreate` (`project_id` résolu par
`scoped_project_id` : la clé API cloisonne sur son projet). `201` +
`ResearchObservationOut`. Le `data_level` est **forcé à `experimental`** :
une observation de laboratoire est une mesure. `400` si aucun `project_id`
n'est résolu.

### `GET /api/science/observations?project_id=&limit=` — observations (privé)
Liste antichronologique des `ResearchObservationOut` du projet autorisé
(`data_level=experimental`). `require_project_access` sur le projet demandé.

### `POST /api/science/simulations` — créer une simulation (privé)
Corps : `SimulationCreate` (`name`, `compound_ids`, `target`, `parameters`,
`method`, `project_id` optionnel). Les champs `compound_ids` / `target` /
`parameters` / `method` sont regroupés dans `inputs`. `project_id` fourni →
cloisonné (`scoped_project_id`). Entrées vides → `409`. `201` +
`SimulationOut` avec `data_level=prediction` et l'`avertissement` :
« Simulation — à confirmer expérimentalement… » (une simulation n'est pas
une preuve expérimentale).

### `GET /api/science/simulations?project_id=&limit=` — simulations (privé)
Liste des `SimulationOut` (filtrée au projet autorisé si `project_id`
donné), chacune avec son `avertissement`.

### `GET /api/science/simulations/{simulation_id}` — détail d'une simulation (privé)
`SimulationOut` (+ `avertissement`). `404` « Simulation introuvable » si
absente ; `require_project_access` si elle est rattachée à un projet.

### `POST /api/science/workflows` — créer un workflow (Phase 7)
Parcours guidé `analyse_plante` en 7 étapes : plante → constituants →
publications → molécule → simulation → rapport → export. Corps :
`{type_workflow, nom, inputs: {plant_id, compound_id, engine_name?},
project_id?}` (`project_id` résolu par `scoped_project_id`). `201` +
journal des 7 étapes (`en_attente`) + `input_hash` (SHA-256 des inputs,
traçabilité/rejouabilité). Inputs invalides → `422`.

### `POST /api/science/workflows/{id}/avancer` — exécuter l'étape suivante
Exécute la première étape non terminée (`200`). Étape impossible ou
workflow terminé → `409` (l'état d'échec est persisté avant la 409 : la
reprise reste possible). Chaque étape porte `entrees`, `sorties`,
`erreur` ; l'étape simulation inclut l'avertissement « Simulation — à
confirmer expérimentalement ». Run inconnu → `404`, projet étranger →
`403` (la 403 prime sur la 404).

### `POST /api/science/workflows/{id}/rejouer` — rejouer depuis les mêmes inputs
Réinitialise les 7 étapes à `en_attente`, efface `resultat`, conserve le
même `input_hash` (`200`).

### `GET /api/science/workflows` · `GET /api/science/workflows/{id}` — lister / détail
Runs du projet autorisé (ordre de création), `403` vers un projet étranger.

### `POST /api/science/lab/notes` — créer une note de laboratoire (Phase 8)
Journal privé du projet : `{project_id, titre, contenu, auteur?,
pieces_jointes?}`. `201`. Le `data_level` est **forcé à `experimental`** ;
la note est privée au projet (jamais exposée hors laboratoire sans
publication explicite).

### `PUT /api/science/lab/notes/{id}` — éditer une note (versionnée)
**Jamais de modification silencieuse** : l'ancien contenu est archivé
dans `lab_note_versions` et `version_courante` est incrémentée (`200`).
Note publiée → `409` (lecture seule). `GET .../notes/{id}/versions` :
historique immuable.

### `POST /api/science/lab/publier` — publication EXPLICITE (jamais automatique)
`{item_type: observation|simulation|note, item_id, publie_par?,
commentaire?}` → `201` + `PublicationRecord` (qui, quand, depuis quel
projet). L'item publié devient **en lecture seule** (`PATCH` ultérieur →
`409`) ; republication → `409` ; item inexistant → `404` ; type invalide →
`422`. Une observation privée n'apparaît dans la recherche publique
qu'après cette publication explicite.

### `PATCH /api/science/lab/observations/{id}` · `PATCH /api/science/lab/simulations/{id}` — modifier (gardé)
Modification d'un item de laboratoire non publié (`200`). Item publié →
`409` « en lecture seule ».

### `GET /api/science/lab/tableau-de-bord?project_id=` — tableau de bord du laboratoire
Comptes et derniers éléments du projet : plantes étudiées, composés,
observations, simulations, notes, workflows récents. `403` vers un projet
étranger.

### `POST /api/science/import` — importer un fichier (Phase 9)
`multipart/form-data` : `fichier` (CSV, JSON, XLSX ou SDF), `type_donnees`
parmi `plante` | `compose` | `lien` | `evidence`, `projet_id` optionnel.
`200` + `{rapport: {crees, mis_a_jour, ignores, erreurs[]}, provenance}`.
Chaque ligne invalide est rejetée avec `{ligne_numero, champ, message}` —
jamais d'import partiel silencieux. Réimport du même fichier → `0`
doublon (idempotent). Fichier > 10 Mo → `413` ; type non supporté →
`400`. Formats détaillés dans `docs/import-export.md` (+ exemples dans
`docs/exemples_import/`). SDF : parseur minimal honnête (champs texte
`Nom`/`Formule`/`SMILES`/`InChI`/`InChIKey`/`Classe` uniquement, pas de
validation chimique — sans RDKit).

### `GET /api/science/export/plantes|composes|evidences?format=csv|json` (Phase 9)
Téléchargement du fichier. Chaque ligne/objet porte `data_level` et la
source principale (DOI/URL). Format invalide → `400`.

### `GET /api/science/export/fiche/{plant_id}?format=html|pdf` (Phase 9)
Fiche scientifique complète. `format=pdf` → vrai PDF (reportlab) avec
sections, tableaux phytochimie, badges en texte et avertissements ;
sans reportlab, repli HTML imprimable avec bandeau explicite (jamais de
faux PDF). Plante absente → `404` en français.

### `POST /api/science/export/dossier` — dossier de recherche (Phase 9)
`{projet_id, inclure_prive: false, inclure_simulations: true}` → ZIP :
fiches JSON+HTML par plante, `bibliographie.txt` + `.bib` (BibTeX),
rapports de simulation (avec avertissement « à confirmer
expérimentalement »), journal du projet, `MANIFEST.json`, `README.txt`.
Par défaut les données privées sont **exclues** ; `inclure_prive=true`
exige l'accès projet (`403` sinon).

### `GET /api/science/connecteurs` — registre des connecteurs (Phase 10)
`200` + `{connecteurs: [{nom, description, url, licence, licence_url,
conditions_utilisation, version_connecteur, statut, dernier_import,
strategie_maj}]}`. Chaque connecteur est en **lecture seule** et déclare
obligatoirement sa licence.

### `POST /api/science/connecteurs/{nom}/importer` (Phase 10)
`{parametres: {...}, projet_id?}` → `200` `{rapport, provenance:
{connecteur, date_import, version_donnees, licence}}`. Connecteurs
disponibles : `pubchem` (PUG-REST : `{"nom": "..."}` → propriétés vers
`Compound` idempotent), `pubmed` (E-utilities : `{"requete": "...",
"max": N≤20}` → `Evidence` idempotentes). Timeout 10 s, 2 retries, cache
24 h. Réseau indisponible → `503` explicite (jamais de faux résultat) ;
connecteur inconnu → `404` ; paramètres invalides → `422`. Chaque donnée
importée est tracée dans `import_provenance` (connecteur, date, version,
licence).

### `GET /api/science/ia/statut` — état du garde-fou IA (Phase 11)
`200` + `{ia_activee: bool, source_decision: "interface" |
"variable_environnement" | "defaut", message}`. Indique si les fonctions
d'IA sont actives et d'où vient la décision.

### `GET /api/science/ia/synthese/plante/{plant_id}` — synthèse automatique (Phase 11)
`200` + `{plante_id, titre, texte, sources: [{evidence_id, type_source,
reference, doi, pmid, url, titre, auteurs, annee, extrait,
data_level: "publie"}], niveau_donnees: "hypothese", badge, moteur,
avertissement, genere_le, persiste: false}`. Assemblage déterministe des
preuves et liens enregistrés (données privées du laboratoire jamais
utilisées) — **toujours** étiqueté `hypothese` avec avertissement, **jamais
persisté**. Plante absente → `404`. IA désactivée → `503` explicite
(« Fonctionnalité d'IA désactivée »), aucun calcul effectué.

## Relations scientifiques v2.1 (`/api/science`)

Modèle enrichi : études structurées, cibles documentées par composé,
activités pharmacologiques par plante, preuve pointée sur un lien
plante-composé (`evidence_id`), provenance et jeux de données privés
isolés par projet. Toute donnée importante est sourcée ; les champs
absents sont rendus comme « non renseignée », jamais inventés.

### `POST /api/science/studies` — créer une étude structurée
Corps : `StudyCreate` (`doi`, `title`, `authors[]`, `year`, `journal`,
`pmid`). **Idempotent** sur le DOI : `201` / `200`.

### `GET /api/science/studies` · `GET /api/science/studies/{study_id}`
Liste (filtrable par `doi`) et détail (`404` si absente).

### `POST /api/science/compounds/{compound_id}/targets` — documenter une cible
Corps : `CompoundTargetCreate` (`target`, `interaction_type`, `source`,
`publication`, `doi`, `evidence_id`). Une cible est *documentée*
(publication citée), jamais « validée » par WebIntel. `404` si le composé
est absent.

### `GET /api/science/compounds/{compound_id}/targets` — cibles d'un composé

### `POST /api/science/plants/{plant_id}/activities` — activité rapportée
Corps : `PlantActivityCreate` (`activity`, `study_type`
[`in_vitro`|`in_vivo`|`clinique`|`ex_vivo`|`autre`], `study_model`,
`published_result`, `reference`, `doi`, `evidence_id`). Donnée *rapportée*
telle que publiée — jamais une allégation d'efficacité thérapeutique.

### `GET /api/science/plants/{plant_id}/activities` — activités d'une plante

### `POST /api/science/plant-compounds/{link_id}/preuve` — pointer une preuve
Corps : `{"evidence_id": N}`. Attache une `Evidence` existante au lien
plante-composé (`evidence_id`, FK). `404` si le lien ou la preuve est
absent.

### `GET /api/science/plant-compounds/{link_id}/preuve` — lire la preuve pointée
`404` si le lien ne pointe aucune preuve (réponse explicite, pas de
valeur inventée).

### `POST /api/science/provenance` — enregistrer une provenance
Corps : `ProvenanceCreate` (`entity_type`, `entity_id`, `source_name`,
`connector`, `data_version`). **Idempotent** sur
(`entity_type`, `entity_id`, `source_name`, `connector`, `data_version`).

### `GET /api/science/provenance?entity_type=&entity_id=` — lister la provenance

### `POST /api/science/datasets` — créer un jeu de données (privé par défaut)
Corps : `DatasetCreate` (`name`, `project_id` requis, `description`).
**Privé par défaut** (`is_private=True`), cloisonné au projet :
invisible depuis un autre projet (`GET` → `404`).

### `GET /api/science/datasets?project_id=` — lister les jeux de données
Cloisonné au projet demandé (`require_project_access`).

### Limites honnêtes (v2.1)
- Les formulaires UI exigent une source (publication ou DOI) ; l'API
  accepte une source optionnelle pour les imports automatisés — une
  donnée sans source reste étiquetée selon son niveau mais sa traçabilité
  est alors incomplète (visible dans la provenance).
- La section « Sécurité » de la fiche repose sur une heuristique
  textuelle appliquée aux sources citées : elle ne remplace ni un avis
  médical, ni une évaluation toxicologique. Le bandeau de prudence est
  toujours affiché, même sans alerte.
- PostgreSQL : support codé mais **non validé sur serveur réel**
  (état durable) — valider avant toute mise en production PG.
