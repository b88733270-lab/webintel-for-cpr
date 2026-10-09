# WebIntel — Architecture v1.1.1

> Document contrat. Tout lot de construction DOIT le lire en premier et s'y conformer.
> Principe produit : **DATA PRODUCT** — `SOURCE → COLLECTION → EXTRACTION →
> NORMALISATION → ENTITY → OBSERVATION → EVENT → ANALYSIS → ALERT → WORKFLOW → API/EXPORT`.
> Le HTML brut est une donnée **secondaire**. Chaque donnée importante porte sa **provenance**.
>
> **v1.1 (2026-09-22)** — correctifs P0 issus de l'audit utilisateur :
> 1. isolation stricte projets / clés API (§10) ;
> 2. pipeline canonique unique dans `core/pipeline.py` (§6) ;
> 3. observations métier par source `SourceObservation` (§4).
>
> **v1.1.1 (2026-09-22)** — passe solidité du noyau data (2ᵉ audit) :
> 1. authentification UI en mode serveur — session par cookie signé (§11) ;
> 2. vraie table `sources` ; `Entity.data` = identité/synthèse uniquement,
>    valeurs volatiles (prix, disponibilité, note) dans les observations
>    sourcées uniquement (§4) ;
> 3. historique avec provenance `source_id`/`source_url`/`source_domain` (§4) ;
> 4. `value_hash` + unicité portable `(entity_id, source_id, field, value_hash)`
>    + insertion idempotente sous concurrence (§4) ;
> 5. migration idempotente v1.1 → v1.1.1 (§14).

## 1. Stack v1 (simple, exécutable partout)

| Couche | Choix v1 |
|---|---|
| Langage | Python 3.10+ |
| Backend | FastAPI + Pydantic v2 |
| DB | SQLAlchemy 2.0 ; SQLite par défaut (`sqlite:///./webintel.db`), PostgreSQL via `DATABASE_URL` en prod |
| HTTP client | httpx |
| Parsing | BeautifulSoup4 + lxml |
| Frontend | Jinja2 (templates FR), CSS simple |
| Exports tableur | openpyxl |
| Workers | `ThreadPoolExecutor` en process (interface documentée, interchangeable v2) |
| Scheduler | thread en process (v2 → Celery beat) |

**Interdit en v1** : Redis, Celery, React, Playwright réel, facturation, multi-tenant complet.
**Obligatoire** : prévoir les **interfaces** d'extension (ex. `BrowserFetcher` stub avec la même
signature que l'implémentation HTTP ; worker avec la même interface qu'un futur worker Celery).
Documenter le reporté en v2 dans `docs/roadmap.md`. **Aucun code mort, aucun `TODO: implémenter`.**

## 2. Arborescence (repo `~/workspace/webintel/`)

```
webintel/                    # package Python
    __init__.py              # __version__ = "1.1.1"
    main.py                  # create_app() -> FastAPI ; monte api + ui ; lifespan (scheduler)
    config/settings.py       # Settings (env), defaults dev
    core/
        enums.py             # PageType, EntityType, EventType, JobStatus, ChangeLevel, AlertChannel, MonitorTargetType
        types.py             # Pydantic: Provenance, Price, ContactInfo, HttpInfo, FetchResult, SnapshotPayload...
        exceptions.py        # WebIntelError, SecurityError, FetchError, ExtractionError, NotFoundError
        logging.py           # get_logger(name) -> logging structuré simple
        utils.py             # sha256, clean_text, truncate, utcnow, simili_texte (difflib)
    database/
        base.py              # engine, SessionLocal, get_db, init_db()
        models.py            # TOUTES les tables (ci-dessous)
    security/
        ssrf.py              # validate_url() STRICT (voir §5)
        robots.py            # fetch/parse robots.txt, can_fetch()
        limits.py            # constantes + check_limits()
    normalization/
        prices.py            # parse_price(txt) -> Price(amount: float|None, currency: str|None)
        dates.py             # parse_date(txt) -> datetime|None (FR + ISO)
        phones.py            # parse_phones(txt) -> list[str] normalisés
        urls.py              # normalize_url(), canonical_url(), same_domain()
    crawler/
        fetcher.py           # BaseFetcher (ABC), HttpFetcher (httpx), BrowserFetcher (STUB v1 documenté),
                             # HybridFetcher (HTTP d'abord, détecte besoin JS -> signale needs_js)
        engine.py            # run_crawl(job_id, ...) borné : BFS, file, dédup, progression
        urlutils.py          # helpers découverte de liens (complète normalization/urls.py si besoin)
    extraction/
        structured.py        # JSON-LD, Microdata, RDFa(léger), OpenGraph, Twitter Cards, meta tags
        classify.py          # classify_page(url, soup, structured) -> (PageType, score, signaux)
        product.py           # extract_product(...) -> Product (heuristiques + structured)
        article.py           # extract_article(...) -> Article
        company.py           # extract_company(...) -> Company (contacts, socials)
        contacts.py          # emails, téléphones, réseaux sociaux (regex)
        templates.py         # ExtractionTemplate (JSON) : règles CSS/XPath/regex, save/load/apply
    techdetect/detector.py   # detect_technologies(soup, headers, url) -> [{technology, confidence, evidence}]
    seo/analyzer.py          # analyze_seo(...) -> {score, issues[{severity, code, message, recommandation}], stats}
    health/scorer.py         # score_page(...) -> {overall, axes{technical,seo,content,structure,data_quality},
                             #                    factors[{name, score, weight, evidence, recommendation}]}
    entities/
        schemas.py           # Pydantic: Product, Article, Company, WebPage (+EntityType)
        registry.py          # entity_key stable, upsert_entity(), merge, get_entity()
    storage/
        snapshots.py         # save_snapshot() (JAMAIS d'écrasement), get_history(), get_latest()
    diff/
        scoring.py           # poids configurables par champ
        engine.py            # diff_snapshots(old, new) -> DiffResult{level, changes[], score, résumé}
    monitoring/
        service.py           # run_monitor(monitor_id) : collecte->compare->événements->règles->alertes
        rules.py             # evaluate_rule(rule, context) -> bool (conditions simples documentées)
    alerts/
        dispatcher.py        # dispatch(alert) : channel "log" (DB+logger) et "webhook" (httpx POST, URL validée SSRF)
    jobs/
        manager.py           # JobManager : submit(), status(), cancel(), pause(), resume() ; états §7
        scheduler.py         # boucle thread : monitors dus -> submit ; démarrable/arrêtable
    search/
        engine.py            # search_entities(session, q, entity_type, min_price, max_price, domain, since, ...)-> list
    exports/
        writers.py           # to_json/to_csv/to_xlsx(rows) -> bytes ; build_export(kind, format, filters)
    analytics/
        kpis.py              # get_dashboard_kpis(session, project_id) -> dict (voir §9)
        market.py            # price_distribution(), compare_sources() (helpers simples)
    api/
        deps.py              # get_db, get_current_project, api_key_auth (X-API-Key, désactivable en dev)
        routers/
            system.py        # GET /api/health, GET /api/usage
            projects.py      # CRUD projets + api-keys
            analyze.py       # POST /api/analyze {url, project_id?} -> fiche intelligence
            crawls.py        # POST/GET /api/crawls, /{id}, /{id}/cancel|pause|resume
            entities.py      # GET /api/entities (filtres), /{id}, /{id}/history
            monitors.py      # CRUD + POST /{id}/run
            events.py        # GET /api/events (filtres)
            alerts.py        # GET /api/alerts
            search.py        # GET /api/search
            exports.py       # POST /api/exports, GET /api/exports/{id}/download
    ui/
        routes.py            # pages FR : /, /analyser, /crawls, /crawls/{id}, /entites, /entites/{id},
                             # /moniteurs, /alertes, /evenements, /recherche, /exports
        templates/*.html    # base.html + pages (Jinja2, FR)
        static/style.css
tests/
    conftest.py              # fixtures DB sqlite :memory:, client TestClient
    fixtures/*.html          # produit.html, article.html, entreprise.html, listing.html (LOCAUX, pas de réseau)
    test_normalization.py, test_extraction.py, test_classify.py,
    test_diff.py, test_security_ssrf.py, test_api.py, test_crawl.py
scripts/seed_demo.py         # scénario complet SANS réseau externe (fixtures locales + TestClient)
docs/                        # architecture.md (ce fichier), api.md, securite.md, roadmap.md
docker/                      # (Dockerfile et docker-compose.yml à la racine)
```

## 3. Contrats de données (Pydantic — `core/types.py`, `entities/schemas.py`)

```python
class Provenance(BaseModel):
    source: str            # ex. "json-ld", "heuristique-dom", "opengraph"
    url: str
    selector: str | None = None
    method: str            # ex. "css", "xpath", "regex", "jsonpath"
    captured_at: datetime
    confidence: float = 1.0  # 0..1

class Price(BaseModel):
    amount: float | None
    currency: str | None   # code ISO "EUR", "USD", "XOF"...

class FetchResult(BaseModel):
    url: str; final_url: str; status_code: int
    headers: dict = {}
    html: str = ""; text: str = ""
    fetched_at: datetime; duration_ms: int
    method: str = "http"   # "http" | "browser"
    needs_js: bool = False # True si la page semble nécessiter un navigateur (v2)
    error: str | None = None

class SnapshotPayload(BaseModel):  # ce qu'on stocke par snapshot
    url: str; canonical_url: str; fetched_at: datetime; status_code: int
    title: str; page_type: str; page_type_score: float
    text_hash: str; html_hash: str; word_count: int
    structured: dict = {}  # extraction structurée + entités (dicts sérialisés)
    seo: dict = {}; technologies: list = {}; health: dict = {}
    links: list = []; images: list = []
```

**Entités** (`entities/schemas.py`) — champs métier + `provenance: Provenance`, `source_url`, `captured_at`.
- `Product` : name, brand, model, description, sku, ean, category, price: Price, availability (enum str: in_stock/out_of_stock/preorder/unknown), rating, review_count, images[], specifications{}, seller{}
- `Article` : title, author, published_at, updated_at, category, content, images[], tags[]
- `Company` : name, website, description, industry, location, phones[], emails[], socials[], technologies[]
- `WebPage` : entité générique (title, description, headings, word_count)

**Clés stables** (`entities/registry.py`) :
- produit : `product:` + sha1((brand|"" + "|" + name).lower().strip()) ; repli : canonical_url
- entreprise : `company:` + domaine racine
- article : `article:` + canonical_url
- page : `webpage:` + canonical_url
Même produit sur 50 sites → **une** entité logique, N observations (table `observations`).

## 4. Modèle de données (SQLAlchemy — `database/models.py`)

Toutes les tables ont `id` (Integer PK), `created_at` (DateTime UTC).
Colonnes JSON : `sqlalchemy.JSON` (portable SQLite/PostgreSQL).

- `projects` : id, name (unique), created_at
- `api_keys` : id, project_id FK, name, key_hash (sha256), prefix (8 car.), quotas JSON default {}, revoked bool default False
- `crawl_jobs` : id, project_id FK, kind ("crawl"|"monitor_run"|"analyze"), status (JobStatus str),
  params JSON, progress JSON default {discovered:0, visited:0, remaining:0, errors:0, started_at, elapsed_s, speed_ppm, bytes}, error TEXT nullable, created_at, updated_at
- `page_snapshots` : id, project_id FK, url, canonical_url, fetched_at, status_code, title,
  page_type, page_type_score float, html_hash, text_hash, word_count, payload JSON (SnapshotPayload),
  keep_html bool default False, raw_html TEXT nullable (seulement si keep_html)
- `entities` : id, project_id FK, entity_key (unique par projet), entity_type, name,
  data JSON, first_seen, last_seen, sources JSON (liste d'URLs)
- `observations` : id, entity_id FK, field, old_value JSON, new_value JSON, observed_at, snapshot_id FK nullable,
  **source_id FK nullable, source_url, source_domain** (v1.1.1) — **journal des CHANGEMENTS**
  (ancien → nouveau), append-only, **chaque changement attribué à sa source**.
- `sources` (v1.1.1) : id, name, canonical_host **unique**, country, seller, metadata JSON, created_at
  — **la source d'observation en tant qu'entité** (une ligne par hôte observé, dédupliquée par
  `canonical_host` = hôte complet, ex. `www.boutique.fr`).
- `source_observations` (v1.1, P0-3 ; durci v1.1.1) : id, entity_id FK, **source_id FK → sources**
  (nullable pendant la migration, non nul ensuite), source_url, source_domain,
  field, value JSON, **value_hash** (SHA-256 du JSON canonique de `value`),
  confidence float nullable, snapshot_id FK nullable, observed_at, created_at,
  index unique portable **(entity_id, source_id, field, value_hash)**
  — **valeur métier observée SUR UNE SOURCE PRÉCISE**. Une ligne n'est écrite que si la valeur
  a changé pour `(entity_id, source_id, field)` ; réinsertion de la même valeur = **no-op idempotent**
  (`ON CONFLICT DO NOTHING` sur SQLite/PostgreSQL, repli Python sinon) — sûr sous concurrence.
  `source_domain` = **hôte complet** (ex. `www.boutique.fr`) : deux boutiques distinctes, même en
  sous-domaines du même domaine racine, sont deux sources différentes. Champs suivis par type :
  produit → `price, availability, name, brand, rating, review_count` ; autres types → `title`.
  Distinction structurante (v1.1.1) :
  - `Entity` = identité logique stable (clé `marque|nom` pour un produit) ;
  - `Entity.data` = **identité/synthèse UNIQUEMENT** (nom, marque, SKU…) — JAMAIS de prix,
    disponibilité ou note (`_strip_volatile()` les retire avant stockage) ;
  - `Observation` = journal old/new des changements, **avec provenance source** ;
  - `SourceObservation` = vérité terrain par boutique/domaine (comparateurs de prix,
    `analytics/market.py` : `find_same_products`, `compare_product_prices`) ;
  - `Source` = la boutique/domaine en tant qu'entité référençable (FK).
- `events` : id, project_id FK, type (EventType str), entity_id FK nullable, url,
  before JSON, after JSON, importance ("high"|"medium"|"low"), evidence JSON, created_at
- `monitors` : id, project_id FK, name, target_type (url|domain|product|price|keyword),
  target TEXT, interval_minutes int, active bool default True, config JSON default {} (rules, webhook_url...),
  last_run_at nullable, created_at
- `alerts` : id, project_id FK, monitor_id FK nullable, rule JSON, channel ("log"|"webhook"),
  payload JSON, status ("sent"|"failed"|"pending"), error TEXT nullable, created_at
- `exports` : id, project_id FK, kind, format ("json"|"csv"|"xlsx"), params JSON,
  status ("ready"|"failed"), file_path nullable, created_at

Index : `entities(project_id, entity_key)` unique, `page_snapshots(project_id, canonical_url, fetched_at)`,
`events(project_id, created_at)`, `monitors(active, last_run_at)`,
`source_observations(entity_id, field, source_domain)`.

## 5. Sécurité (`security/`) — NON NÉGOCIABLE

`validate_url(url: str) -> str` (retourne l'URL normalisée) lève `SecurityError` si :
- schéma non http/https ; présence d'userinfo (`user@host`) ; port non standard listé (autoriser 80/443 + explicites, bloquer le reste sauf config)
- hostname vide, ou se résout (via `socket.getaddrinfo`, **toutes** les IPs) vers :
  IP privée / loopback / link-local / multicast / réservée / non spécifiée (`ipaddress`)
- host dans la blocklist : `localhost`, `*.localhost`, `169.254.169.254`, `metadata.google.internal`,
  `metadata.google.com`, `instance-data`, suffixes `.internal`, `.local` (configurable)
- protection **DNS rebinding** : on résout AVANT la requête et on valide TOUTES les adresses ;
  le fetcher doit se connecter en validant que l'IP reste autorisée (au minimum : revalider l'URL finale après redirections)
- **anti-TOCTOU (v2.1)** : `security/pinned_transport.py::PinnedHTTPTransport` — résolution DNS
  unique et validée (`resoudre_ip_epinglee`), connexion directe à l'IP épinglée, en-tête `Host`
  et SNI TLS préservés, certificat vérifié contre le nom d'hôte d'origine. Utilisé par le fetcher,
  robots.txt, les connecteurs et les webhooks : plus de seconde résolution DNS au moment de la connexion.
- chaque **redirection** est revalidée avec `validate_url`
- limites (`security/limits.py`, surchargeables par Settings) :
  `MAX_RESPONSE_BYTES=5_000_000`, `FETCH_TIMEOUT_S=20`, `MAX_REDIRECTS=5`,
  `MAX_CRAWL_PAGES=1000`, `MAX_CRAWL_DEPTH=3`, `MAX_CONCURRENCY=4`, `CRAWL_DELAY_S=1.0`

`robots.py` : `RobotChecker` — récupère `/robots.txt` (timeout court, 1 par domaine, cache mémoire),
`can_fetch(url, user_agent)` ; `obey_robots=True` par défaut (désactivable par projet, tracé en log).

## 6. Pipeline canonique UNIQUE (`core/pipeline.py`) — P0-2 v1.1

**Une seule implémentation.** Étapes documentées dans le code :

```
FETCH → PARSE → CLASSIFY → EXTRACT → NORMALIZE → ENRICH
  → ENTITY → SNAPSHOT → DIFF → EVENT → OBSERVE
```

Deux points d'entrée :
- `analyze_url(session, project_id, url, ...)` — FETCH puis pipeline complet ;
- `analyze_fetch_result(session, project_id, fetch_result, ...)` — pipeline seul,
  depuis un `FetchResult` déjà collecté (évite le double téléchargement).

Consommateurs (aucune duplication de logique) :
- `POST /api/analyze` → `analyze_url` ;
- `monitoring/service.py::run_monitor` → `analyze_fetch_result` (règles et alertes
  restent spécifiques au monitoring) ;
- `crawler/engine.py` (hook optionnel `page_pipeline`) et `api/routers/crawls.py`
  → `analyze_fetch_result` par page crawlée (session SQLAlchemy dédiée au thread) ;
- `api/pipeline.py` n'est qu'un **shim de compatibilité** qui réexporte
  `core.pipeline` (pas de logique).

`events/detector.py` (module neutre) : `detect_events(...)` — détection
old/new entre snapshots, utilisée par le pipeline ; réexportée par
`monitoring` pour compatibilité.

Contrat : chaque valeur extraite porte `provenance`. En cas d'échec :
`FetchError`/`SecurityError` → message FR clair. `analyze_fetch_result`
committe les écritures (snapshot, entités, événements, observations).

## 7. Jobs (`jobs/manager.py`)

États : `QUEUED → RUNNING → (PAUSED ↔ RUNNING) → COMPLETED | FAILED | CANCELLED`.
`JobManager` (singleton `get_manager()`) :
- `submit(kind, project_id, params, runner: Callable[[JobContext], None]) -> job_id`
- `status(job_id) -> dict`, `cancel(job_id)`, `pause(job_id)`, `resume(job_id)`
- `JobContext` : `job_id`, `is_cancelled()`, `is_paused()` (boucle d'attente), `update_progress(**kwargs)`
- persistance en DB (`crawl_jobs`), exécution `ThreadPoolExecutor(max_workers=4)`
- **Interface documentée** pour substitution Celery v2 (mêmes noms de méthodes).

`jobs/scheduler.py` : `Scheduler(interval_s=60)` thread démon ; à chaque tick :
`monitors` actifs dus (`last_run_at + interval <= now`) → `submit("monitor_run", ...)`.
Démarré dans le lifespan FastAPI si `settings.ENABLE_SCHEDULER`.

## 8. Diff intelligent (`diff/`)

`diff_snapshots(old: SnapshotPayload, new: SnapshotPayload) -> DiffResult` :
- `price.amount` changé → **MAJOR** (événement PRICE_DROP/PRICE_INCREASE)
- `availability` changé → **MAJOR** (BACK_IN_STOCK/OUT_OF_STOCK)
- titre/H1/meta description changés → **MINOR**
- similarité texte (difflib) < 0.95 → **MINOR**, 0.95–0.999 → **COSMETIC**
- seuls classes/ids/whitespace/ordre attributs → **COSMETIC** (comparer sur DOM normalisé)
- rien → **NONE**
`DiffResult{level: ChangeLevel, changes: [{field, before, after, importance}], score: float, summary_fr: str}`.
Poids dans `diff/scoring.py` (dict `FIELD_WEIGHTS`, surchargeable).

## 9. Monitoring & alertes

`monitoring/service.run_monitor(monitor_id)` :
1. selon `target_type` : fetch URL(s) (domaine → on surveille la page d'accueil + sitemap si petit)
2. snapshot + diff vs dernier
3. `detect_events(diff, entity)` → `EventType` : PRICE_DROP, PRICE_INCREASE, BACK_IN_STOCK,
   OUT_OF_STOCK, NEW_PRODUCT, NEW_ARTICLE, ARTICLE_UPDATED, NEW_COMPANY, PAGE_CHANGED,
   SEO_CHANGED, TECHNOLOGY_CHANGED (chaque event : type, entity, url, before/after, timestamp, importance, evidence)
4. `rules.evaluate_rule(rule, context)` — règles JSON : `{"all"|"any": [cond...]}`,
   cond : `{"field": "price.amount", "op": "lt|gt|eq|contains|changed", "value": ...}`
5. `alerts.dispatcher.dispatch(...)` → channel `log` (ligne DB + logger) ou `webhook`
   (POST JSON httpx 10s, URL revalidée SSRF) ; statut sent/failed tracé.

## 10. API REST (préfixe `/api`, OpenAPI auto)

- `GET /api/health` → {status, version, db, scheduler} ; `GET /api/usage` → compteurs
- Projets : `POST /api/projects {name}`, `GET /api/projects`, `GET /api/projects/{id}`
- Clés API : `POST /api/projects/{id}/api-keys {name, quotas?}` → **clé affichée une seule fois** ; `GET .../api-keys`
- Analyse : `POST /api/analyze {url, project_id?, keep_html?}` → fiche intelligence
- Crawls : `POST /api/crawls {project_id, start_url|start_urls|domain, limits{max_pages, max_depth, delay_s, concurrency, allow_domains, exclude_paths, obey_robots}}`
  → `{job_id}` ; `GET /api/crawls`, `GET /api/crawls/{id}`, `POST /api/crawls/{id}/cancel|pause|resume`
- Entités : `GET /api/entities?type=&q=&min_price=&max_price=&domain=&since=` ; `GET /api/entities/{id}` (+ `observations_by_source` groupées par domaine, P0-3) ; `GET /api/entities/{id}/history`
- Moniteurs : `POST /api/monitors {project_id, name, target_type, target, interval_minutes, config?}` ; `GET /api/monitors` ; `GET /api/monitors/{id}` ; `DELETE /api/monitors/{id}` ; `POST /api/monitors/{id}/run`
- Événements : `GET /api/events?type=&since=&project_id=`
- Alertes : `GET /api/alerts?project_id=`
- Recherche : `GET /api/search?q=&type=&min_price=&max_price=&domain=`
- Exports : `POST /api/exports {project_id, kind: entities|events|snapshots|observations, format: json|csv|xlsx, filters?}` → `{export_id, download_url}` ; `GET /api/exports/{id}/download`

Auth : header `X-API-Key` ; si `settings.REQUIRE_API_KEY=false` (défaut dev) → bypass avec log debug.
En mode clé : 401 si clé absente/invalide.
- **Isolation multi-projet — P0-1 (v1.1), `api/deps.py`** : chaque clé est rattachée à
  UN `project_id`. `require_project_access(api_key, project_id)` lève **403** si la clé
  n'appartient pas au projet demandé — contrôle effectué AVANT toute lecture/écriture
  (la 403 prime sur la 404 pour ne pas révéler l'existence des projets étrangers).
  `scoped_project_id(api_key, project_id?)` : en mode clé, un `project_id` absent vaut
  celui de la clé (cloisonnement par défaut) et un `project_id` étranger vaut 403 ;
  en mode bypass (`REQUIRE_API_KEY=false`), comportement v1 inchangé.
  Appliqué à : projets, clés, analyze, crawls (+ pilotage cancel/pause/resume vérifié
  sur la ligne DB **avant** toute action du gestionnaire), entités (+ historique),
  moniteurs (+ exécution immédiate), événements, alertes, recherche, exports
  (création ET téléchargement — contrôle avant vérification du fichier, pas d'oracle),
  compteurs `/api/usage` (cloisonnés au projet de la clé ; `/api/health` reste ouvert).
  `POST /api/projects` avec une clé → 403 (création de projet = bootstrap via
  `scripts/create_api_key.py`).
Quotas : schéma `quotas` sur api_keys (`{"max_pages_day": N, "max_crawls": M}`) — enforcement de base
(compteur simple), détaillé en v2.

## 11. UI (FR, Jinja2)

Pages : `/` (dashboard KPIs : pages surveillées, changements aujourd'hui, produits détectés,
changements de prix, erreurs, nouvelles entités, alertes, temps moyen de crawl),
`/analyser` (formulaire URL → fiche intelligence : identité, type, entités, prix, contacts,
technologies, SEO, structured data, liens, images, qualité, exports),
`/crawls`, `/crawls/{id}` (progression temps réel), `/entites` (+ `/entites/{id}` historique
avec source de chaque changement), `/moniteurs`, `/alertes`, `/evenements`, `/recherche`
(prix affichés **par source**, jamais fusionnés), `/exports`, `/login`, `/logout`.
Mode SIMPLE par défaut (coller URL → analyser) ; sections avancées repliées (`<details>`).

**Authentification (v1.1.1)** : `REQUIRE_API_KEY=false` → UI ouverte (local).
`REQUIRE_API_KEY=true` → toute route métier exige une session (`ui_auth`),
sinon 303 vers `/login`. Login par clé API (SHA-256) ; cookie de session
signé HMAC-SHA256 (`SECRET_KEY`), `HttpOnly`, `SameSite=Lax`, 24 h ; à
chaque requête : signature + expiration vérifiées, clé relue en DB
(révocation/projet). Projet imposé par la clé — même cloisonnement que
l'API. Détail : `docs/securite.md` §5bis.

## 12. Config (`config/settings.py`)

`Settings` : `DATABASE_URL` (défaut `sqlite:///./webintel.db`), `SECRET_KEY` (défaut dev, **jamais en prod**),
`REQUIRE_API_KEY=false`, `ENABLE_SCHEDULER=true`, `FETCH_TIMEOUT_S=20`, `MAX_RESPONSE_BYTES=5_000_000`,
`MAX_REDIRECTS=5`, `DEFAULT_UA`, `OBEY_ROBOTS=true`, `CRAWL_DELAY_S=1.0`, `MAX_CRAWL_PAGES=1000`,
`MAX_CRAWL_DEPTH=3`, `MAX_CONCURRENCY=4`, `EXPORT_DIR=./webintel_data/exports`,
`SCHEDULER_INTERVAL_S=60`, `LOG_LEVEL=INFO`. Lecture depuis l'environnement, valeurs par défaut sûres.

## 13. Tests & démo

- `tests/` : pytest, fixtures HTML **locales** (`tests/fixtures/`), **zéro réseau** (mocker httpx).
  Couverture exigée : normalization (prix/dates/téléphones), extraction (produit/article/entreprise),
  classify, diff (MAJOR sur prix vs COSMETIC sur classe CSS), SSRF (localhost, IP privée,
  169.254.169.254, DNS rebinding simulé), API (analyze via fixture, CRUD), crawl (borné, dédup),
  **auth UI** (`test_ui_auth.py` : 303 sans session, cookie signé, révocation, isolation projet),
  **discipline Entity/observations** (`test_entity_observation_discipline.py`),
  **provenance historique** (`test_history_provenance.py`), **API sources**
  (`test_sources.py`), **concurrence** (`test_concurrency.py` : 8 threads → 1 ligne).
  La suite tourne en mode local **et** en mode serveur
  (`REQUIRE_API_KEY=true`, clé + session de test injectées par `conftest.py`) ;
  `WEBINTEL_TEST_DATABASE_URL` permet de la faire tourner contre PostgreSQL.
- `scripts/seed_demo.py` : scénario **sans réseau externe** — fixtures locales → analyze →
  entités → snapshot → modification fixture → re-analyze → diff → event → monitor → alerte →
  export ; valide les scénarios A–F du cahier des charges. Doit passer en vert.

## 14. Migrations

- `scripts/migrate_v1_to_v1_1.py` : v1 → v1.1 (idempotent).
- `scripts/migrate_v1_1_to_v1_1_1.py` : v1.1 → v1.1.1 (idempotent, réexécutable —
  second passage : zéro création). Étapes : création de `sources` ; ajout des colonnes
  (`source_observations.source_id`, `value_hash` ; `observations.source_id`,
  `source_url`, `source_domain`) ; une `Source` par hôte observé ; backfill
  `source_id` + `value_hash` ; provenance du journal via `snapshot_id → url` ;
  déduplication `(entity_id, source_id, field, value_hash)` ; retrait des
  champs volatils de `Entity.data` (prix/disponibilité/notes → observations
  uniquement) ; index unique portable. Branches SQLite / PostgreSQL
  (`ADD COLUMN IF NOT EXISTS`, `CREATE UNIQUE INDEX IF NOT EXISTS`).
  Usage : `python3 scripts/migrate_v1_1_to_v1_1_1.py [--database-url URL]`.

## 15. Docker

`Dockerfile` (python:3.12-slim, uvicorn) + `docker-compose.yml` (service `web` ; service `db`
postgres **optionnel**, `DATABASE_URL` injectée). `README.md` : install dev, usage, prod.

## 16. Règles de code (tous les lots)

- Code **réel et exécutable**, **aucun** `TODO`/`pass`/`NotImplementedError` sauf le stub
  `BrowserFetcher` (qui doit lever une erreur **explicite** "prévu v2").
- Commentaires sobres, en français là où ça aide ; **UI 100% français**.
- **Aucun secret** dans le code. Gestion d'erreurs propre, messages FR côté UI.
- Ne jamais écraser l'historique : snapshots append-only, observations/event pour chaque changement.
- Chaque lot vérifie : `python -c "import webintel.<module>"` sans erreur + mini smoke test.
