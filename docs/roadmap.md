# WebIntel — Roadmap v2

État v1 : plateforme fonctionnelle (collecte HTTP, extraction, normalisation,
entités, snapshots append-only, diff intelligent, monitoring, alertes log +
webhook, API REST, UI Jinja2, exports JSON/CSV/XLSX, tests verts, démo
hors-ligne). Les points ci-dessous ont été **explicitement reportés** ; pour
chacun : l'état v1, l'interface déjà en place à conserver, et ce qu'il reste
à faire.

## Correctifs P0 — livrés en v1.1 (2026-09-22)

Audit utilisateur de la v1 → trois failles corrigées, tests dédiés verts
(`tests/test_project_isolation.py`, `tests/test_canonical_pipeline.py`,
`tests/test_source_observations.py`) :

1. **Isolation stricte projets / clés API** — `api/deps.py` :
   `require_project_access()` (403 si la clé n'appartient pas au projet,
   contrôle avant toute lecture/écriture) et `scoped_project_id()` (listes et
   créations cloisonnées au projet de la clé par défaut). Appliqué à tous les
   routeurs (projets, clés, analyze, crawls + pilotage, entités + historique,
   moniteurs, événements, alertes, recherche, exports + téléchargements,
   `/api/usage`). `POST /api/projects` avec une clé → 403 ; bootstrap via
   `scripts/create_api_key.py`.
2. **Pipeline canonique unique** — `core/pipeline.py` :
   `FETCH → PARSE → CLASSIFY → EXTRACT → NORMALIZE → ENRICH → ENTITY →
   SNAPSHOT → DIFF → EVENT → OBSERVE`. `api/pipeline.py` n'est qu'un shim ;
   le monitoring (`analyze_fetch_result`) et le crawl (hook `page_pipeline`)
   utilisent le même code. Détection d'événements dans le module neutre
   `events/detector.py`.
3. **Observations métier par source** — table `source_observations`
   (`entity_id, source_url, source_domain, field, value, confidence,
   snapshot_id, observed_at`) ; une ligne par `(entité, domaine, champ)` et
   seulement si la valeur change. `Entity` reste l'identité logique stable,
   `Observation` le journal old/new. `analytics/market.py` compare désormais
   les prix réellement observés par source (`find_same_products`,
   `compare_product_prices`) ; `GET /api/entities/{id}` expose
   `observations_by_source` ; nouvel export `kind=observations`. Migration
   v1 → v1.1 : `scripts/migrate_v1_to_v1_1.py` (idempotent).

## Correctifs P0 — livrés en v1.1.1 (2026-09-22)

Second audit utilisateur → passe « solidité du noyau data » : chaque donnée
isolée, traçable, attribuée à une source, historisée, fiable sous
concurrence. 92 tests verts en mode local, 91 + 1 ignoré en mode serveur
(`REQUIRE_API_KEY=true`) ; migration v1.1 → v1.1.1 exécutée deux fois sur
une base v1.1 réelle (idempotente, zéro création au 2ᵉ passage) ; démo
hors-ligne OK ; smoke test uvicorn en mode local ET serveur (303 → login →
cookie → 200, 401/403, logout).

1. **Authentification UI en mode serveur** — `ui/auth.py` (nouveau) :
   `REQUIRE_API_KEY=false` → UI ouverte (local) ; `REQUIRE_API_KEY=true` →
   toute route UI exige une session, sinon 303 vers `/login`. Login par clé
   API (comparaison du SHA-256, clé en clair jamais persistée/loggée/exposée),
   cookie de session signé HMAC-SHA256 (`SECRET_KEY`), `HttpOnly`,
   `SameSite=Lax`, TTL 24 h. À chaque requête : signature + expiration
   vérifiées, clé relue en DB (révocation/projet). La session ne contient que
   l'id de clé, l'id de projet et l'expiration. Téléchargement d'export
   possible via session UI (`api_key_or_ui_session()`). Tests :
   `tests/test_ui_auth.py` (9 tests).
2. **`Entity.data` = identité/synthèse uniquement** — `entities/registry.py` :
   `_strip_volatile()` retire `price`, `availability`, `rating`,
   `review_count` avant stockage. `search/engine.py` ne lit plus
   `Entity.data.price` : les filtres `min_price`/`max_price` matchent via les
   dernières observations `price` par source ; les résultats exposent
   `prices` par source, jamais un prix global ambigu.
3. **Vraie table `sources`** — `database/models.py` : `id`, `name`,
   `canonical_host` (unique), `country`, `seller`, `metadata`, `created_at`.
   `get_or_create_source()` (`entities/sources.py`) normalise sur l'hôte
   complet, concurrent-safe (savepoint + reprise `IntegrityError`).
   `SourceObservation` porte `source_id` (FK) + `value_hash`
   (`database/helpers.py` : JSON canonique → SHA-256) ; unicité portable
   `(entity_id, source_id, field, value_hash)` ; insertion idempotente
   dialect-aware (`ON CONFLICT DO NOTHING` sur SQLite et PostgreSQL,
   repli Python sinon). `upsert_entity()` protégé contre la création
   concurrente. Tests : `tests/test_concurrency.py` (8 threads → 1 ligne).
4. **Historique avec provenance** — `Observation` porte `source_id`,
   `source_url`, `source_domain` ; `core/pipeline.py` les renseigne depuis la
   source de l'analyse. L'API (`ObservationOut`) et l'UI affichent la source
   de chaque changement. Tests : `tests/test_history_provenance.py`.
5. **API sources** — `GET /api/sources` (liste cloisonnée au projet),
   `GET /api/sources/{id}` (détail + observations rattachées aux entités du
   projet) ; `SourceObservationOut` expose `entity_id`, `source_id`,
   `source_name`. Tests : `tests/test_sources.py` (isolation inter-projets).
6. **Migration idempotente v1.1 → v1.1.1** —
   `scripts/migrate_v1_1_to_v1_1_1.py` : création de `sources`, ajout des
   colonnes, backfill `source_id`/`value_hash`, provenance du journal via
   `snapshot_id → url`, déduplication, retrait des champs volatils de
   `Entity.data`, index unique. Vérifiée deux fois sur une base v1.1 réelle.
   Note : sur une base migrée, les `ALTER TABLE` SQLite n'ajoutent pas les
   contraintes FK (modèle frais : FK présentes) — documenté, sans impact
   fonctionnel (intégrité garantie par le code).

### PostgreSQL — état honnête (v1.1.1)

- Le code est écrit portable : `DateTime(timezone=True)` partout,
  `ON CONFLICT DO NOTHING` dialect-aware (SQLite + PostgreSQL), JSON via
  SQLAlchemy, `value_hash` calculé en Python (aucune fonction spécifique à
  un dialecte), migration avec branches `ADD COLUMN IF NOT EXISTS` /
  `CREATE UNIQUE INDEX IF NOT EXISTS` pour PostgreSQL.
- **Non validé ici** : aucun serveur PostgreSQL disponible dans cet
  environnement ; la suite accepte `WEBINTEL_TEST_DATABASE_URL=postgresql://…`
  pour tourner entièrement contre Postgres, et la migration accepte
  `--database-url` — **à exécuter avant toute mise en production sur
  PostgreSQL**. Points à vérifier alors : contraintes FK réellement créées
  par la migration, `ON CONFLICT` sur l'index unique, comportement des
  `timestamptz`, sérialisation JSONB.
- `docker-compose.yml` (PostgreSQL 16) inchangé.

## P1. Anti-SSRF : lier l'IP validée à la connexion réelle (TOCTOU)

- **État v2.1 (FAIT)** : `security/pinned_transport.py::PinnedHTTPTransport`
  — résolution DNS unique via `ssrf.resoudre_ip_epinglee()` (toutes les
  adresses validées), connexion directe à l'IP épinglée, en-tête `Host`
  et SNI TLS préservés, certificat vérifié contre le nom d'hôte
  d'origine. Appliqué au fetcher, à robots.txt, aux connecteurs et aux
  webhooks. Tests de régression : `tests/test_ssrf_pinning.py` (DNS
  menteur simulé : bascule public → privé entre validation et connexion
  → bloqué, aucune requête vers l'IP privée).
- **Limites résiduelles** : proxy explicite (résolution par le proxy),
  cf. `docs/securite.md` §1.1.

## 1. Rendu navigateur réel (Playwright)

- **État v1** : `crawler/fetcher.py` — `BrowserFetcher` est un **stub
  documenté** qui lève `FetchError("…prévu en v2…")`. `HybridFetcher`
  détecte le besoin JS (`needs_js=True` : page quasi vide + marqueurs SPA)
  via `_needs_js()`, sans rendu réel.
- **Interface à conserver** : `BaseFetcher.fetch(url, *, timeout_s=None)
  -> FetchResult` (même signature pour `HttpFetcher`, `BrowserFetcher`,
  `HybridFetcher`) ; `fetch_with_fallback()` deviendra le point d'entrée
  « tente le navigateur si `needs_js` ».
- **Reste à faire** : implémenter `BrowserFetcher` avec Playwright
  (Chromium headless, timeout, user-agent), brancher le repli automatique
  dans `fetch_with_fallback`, gérer le cycle de vie du navigateur
  (pool d'instances), ajouter Playwright aux dépendances et à l'image Docker.

## 2. Workers Celery / Redis

- **État v1** : `jobs/manager.py` — `JobManager` en process
  (`ThreadPoolExecutor`, 4 workers), persistance en DB (`crawl_jobs`).
- **Interface à conserver** : `submit(kind, project_id, params, runner)`,
  `status(job_id)`, `cancel()`, `pause()`, `resume()` et `JobContext`
  (`job_id`, `is_cancelled()`, `is_paused()`, `wait_if_paused()`,
  `update_progress(**kwargs)`) — noms et signatures inchangés.
- **Reste à faire** : broker Redis, workers Celery exécutant les mêmes
  `runner`, beat pour le scheduler, sérialisation du `JobContext`
  (callbacks de progression via backend de résultats), idempotence des
  runners, reprise après redémarrage.

## 3. Frontend React

- **État v1** : UI Jinja2 en français (dashboard, fiche d'analyse, crawls,
  entités, moniteurs, alertes, événements, recherche, exports).
- **Interface à conserver** : l'**API REST est déjà API-first**
  (`/api/*`, schémas Pydantic stables, OpenAPI sur `/docs`) — le frontend
  v2 ne consomme que l'API existante.
- **Reste à faire** : application React (tableaux filtrables, graphiques de
  prix, progression temps réel des crawls via polling/SSE), sans toucher
  aux contrats d'API.

## 4. Recherche plein-texte (FTS)

- **État v1** : `search/engine.py` — filtrage en Python (LIKE / bornes de
  prix / domaine / date), portable SQLite/PostgreSQL.
- **Interface à conserver** : `search_entities(session, q, entity_type,
  min_price, max_price, domain, since, limit) -> list[dict]`.
- **Reste à faire** : index FTS5 (SQLite) / tsvector (PostgreSQL) sur
  nom + description + contenu, classement par pertinence, surlignage des
  extraits, facettes (type, domaine, devise).

## 5. Enforcement des quotas API

- **État v1** : schéma `quotas` sur `api_keys`
  (`{"max_pages_day": N, "max_crawls": M}`), enforcement de base.
- **Interface à conserver** : le champ `quotas` (JSON) et la vérification
  dans `api/deps.py`.
- **Reste à faire** : compteurs persistés (pages crawlées / jour / clé,
  analyses / jour / clé), fenêtre glissante, réponses `429` avec
  `Retry-After`, tableau de bord de consommation par clé.

## 6. Multi-tenant complet

- **État v1.1** : isolation stricte livrée (P0-1, voir section « Correctifs P0 »
  ci-dessus) — `project_id` FK partout, `require_project_access` / 403 sur tous
  les routeurs, listes cloisonnées, compteurs `/api/usage` scopés.
- **Interface à conserver** : la colonne `project_id` et les helpers
  `get_project_or_404`, `require_project_access`, `scoped_project_id`.
- **Reste à faire** : notion d'organisation au-dessus des projets, rôles
  (lecture/écriture/admin), clés API scopées par organisation, quotas par
  tenant.

## 7. Crawl sitemap complet

- **État v1** : `monitoring/service.py` — pour `target_type="domain"`, seule
  la page d'accueil est surveillée (documenté dans le code).
- **Interface à conserver** : `_target_urls(monitor)` et les limites de
  crawl existantes.
- **Reste à faire** : découverte et parsing de `sitemap.xml` (+ index de
  sitemaps), file de priorité par `changefreq`/`lastmod`, surveillance
  incrémentale des URLs du sitemap.

## 8. Exports programmés

- **État v1** : exports à la demande (`POST /api/exports`) ; le besoin est
  **documenté** mais non implémenté (aucun stub trompeur).
- **Interface à conserver** : `exports/writers.py` — `build_export(session,
  project_id, kind, format, filters) -> (export_id, bytes)`.
- **Reste à faire** : planification (quotidienne/hebdo) adossée au
  scheduler, envoi par email/webhook ou dépôt SFTP, historique et
  rétention des fichiers générés.

## 9. Notifications SMS

- **État v1** : `alerts/dispatcher.py` — canaux `log` (DB + logger) et
  `webhook` (POST JSON, URL revalidée anti-SSRF).
- **Interface à conserver** : `dispatch(session, project_id, channel, rule,
  payload, monitor_id=None) -> Alert` ; ajout d'un canal = une valeur de
  `AlertChannel` + une branche d'envoi.
- **Reste à faire** : canal `sms` (fournisseur type Vonage/Orange SMS API),
  gabarits de messages FR, gestion des coûts et des numéros (opt-in),
  suivi des statuts de remise.
