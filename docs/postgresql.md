# WebIntel — PostgreSQL (guide d'installation et d'exploitation)

WebIntel fonctionne avec SQLite par défaut (développement local, zéro
installation) et avec **PostgreSQL 16** en production. Ce guide couvre
l'installation, la configuration, la vérification et le dépannage du
support PostgreSQL.

> **Règle d'or : aucun mot de passe en dur.** Ni dans le code, ni dans
> `docker-compose.yml`, ni dans cette documentation : les exemples
> utilisent des placeholders (`<mot-de-passe>`, `<cle-secrete>`).
> Le mot de passe PostgreSQL est **toujours** injecté par l'environnement.

## 1. Prérequis

- Docker Engine + plugin Compose **ou** un serveur PostgreSQL 16
  accessible (local ou distant).
- Python 3.10+ (3.12 recommandé) avec les dépendances de
  `requirements.txt` — dont `psycopg[binary]>=3.1`, le driver
  PostgreSQL utilisé via le dialecte SQLAlchemy `postgresql+psycopg://`.
- Ports : `5432` (PostgreSQL), `8000` (application).

## 2. Variables d'environnement

| Variable            | Obligatoire | Rôle                                                        |
|---------------------|-------------|-------------------------------------------------------------|
| `POSTGRES_PASSWORD` | oui (prod)  | Mot de passe de l'utilisateur `webintel` (service `db`)     |
| `DATABASE_URL`      | oui (prod)  | Chaîne de connexion SQLAlchemy de l'application             |
| `SECRET_KEY`        | oui (prod)  | Clé de signature des sessions (≥ 32 caractères, aléatoire)  |
| `REQUIRE_API_KEY`   | recommandé  | `"true"` en production (auth `X-API-Key` obligatoire)       |

Format de `DATABASE_URL` pour PostgreSQL :

```
postgresql+psycopg://webintel:<mot-de-passe>@db:5432/webintel
```

- `db` est le nom du service dans `docker-compose.yml` (résolution DNS
  interne à Docker). En installation manuelle, remplacez par
  `localhost` ou l'hôte réel.
- Le dialecte `postgresql+psycopg` sélectionne psycopg v3 (binaire),
  compatible SQLAlchemy 2.x. L'ancien schéma `postgresql://` seul
  chercherait psycopg2 (non installé) : utilisez toujours la forme
  complète `postgresql+psycopg://`.
- En développement local sans PostgreSQL, laissez la valeur par défaut
  `sqlite:///./webintel.db`.

Générez les secrets ainsi (jamais à la main, jamais réutilisés) :

```bash
# Mot de passe PostgreSQL (dans un gestionnaire de secrets en production)
POSTGRES_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Clé de signature des sessions
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

## 3. Démarrage via Docker Compose (recommandé)

```bash
cd /chemin/vers/webintel

# Les deux variables sont OBLIGATOIRES : le compose file refuse de
# démarrer sans elles (aucune valeur par défaut codée en dur).
POSTGRES_PASSWORD='<mot-de-passe>' \
SECRET_KEY='<cle-secrete>' \
docker compose up --build -d
```

Ce qui se passe :

1. Le service `db` démarre PostgreSQL 16 (données persistées dans le
   volume `pgdata`), avec un `healthcheck` (`pg_isready`).
2. Le service `web` attend que `db` soit sain (`depends_on` +
   `condition: service_healthy`) puis démarre uvicorn sur le port 8000.
3. Au premier démarrage, l'application crée le schéma via
   `init_db()` (`Base.metadata.create_all`).

Vérifications :

```bash
docker compose ps
docker compose logs -f web        # logs de l'application
docker compose exec db pg_isready -U webintel
```

Arrêt / mise à jour :

```bash
docker compose down               # arrête (les volumes pgdata/webintel_data sont conservés)
docker compose down -v            # arrête ET supprime les données (irréversible)
docker compose up --build -d      # reconstruit après un changement de code
```

## 4. Installation manuelle (sans Docker)

Sur Debian/Ubuntu :

```bash
sudo apt-get update && sudo apt-get install -y postgresql
sudo systemctl enable --now postgresql
```

Créez l'utilisateur et la base (le mot de passe est demandé
interactivement, jamais en clair sur la ligne de commande) :

```bash
sudo -u postgres psql <<'EOF'
CREATE USER webintel WITH PASSWORD '<mot-de-passe>';
CREATE DATABASE webintel OWNER webintel;
EOF
```

Puis lancez l'application en pointant vers PostgreSQL :

```bash
export DATABASE_URL='postgresql+psycopg://webintel:<mot-de-passe>@localhost:5432/webintel'
export SECRET_KEY='<cle-secrete>'
export REQUIRE_API_KEY=true
uvicorn webintel.main:app --host 0.0.0.0 --port 8000
```

Le schéma est créé automatiquement au démarrage (`init_db()`).

## 5. Vérification du support PostgreSQL

### 5.1 Script de validation automatisé

`scripts/test_postgres.py` rejoue de bout en bout la validation sur une
base **vide** (il commence par `DROP` toutes les tables — ne jamais le
lancer en production) :

```bash
DATABASE_URL='postgresql+psycopg://webintel:<mot-de-passe>@localhost:5432/webintel' \
    python3 scripts/test_postgres.py
```

Il vérifie, dans l'ordre :

1. le dialecte `postgresql` + driver `psycopg` ;
2. `init_db()` crée les 10 tables (`projects`, `api_keys`,
   `crawl_jobs`, `page_snapshots`, `entities`, `observations`,
   `sources`, `source_observations`, `events`, `monitors`) ;
3. les clés étrangères (ex. `api_keys.project_id → projects.id`) ;
4. les index nommés (`ix_page_snapshots_project_canonical_fetched`,
   `ix_observations_entity_observed`, `ix_events_project_created`,
   `ix_monitors_active_last_run`) ;
5. les contraintes uniques (`uq_entities_project_key`,
   `projects.name`, `sources.canonical_host`) ;
6. `TZDateTime` → colonne `TIMESTAMPTZ` ;
7. roundtrip JSON (dict + liste) sur `entities.data` / `entities.sources` ;
8. conservation du fuseau horaire à la lecture ;
9. doublon `(project_id, entity_key)` → `IntegrityError` ;
10. upsert `ON CONFLICT DO UPDATE` (1 seule ligne, valeur mise à jour).

Sortie attendue : `10/10 vérifications réussies.` (code de sortie 0).

### 5.2 Vérifications manuelles avec psql

```bash
# Via Docker :
docker compose exec db psql -U webintel -d webintel

-- Dans psql :
\dt                          -- liste des 10 tables
\d entities                  -- colonnes, FK, index, contrainte uq_entities_project_key
SELECT column_name, data_type
  FROM information_schema.columns
 WHERE table_name = 'projects' AND column_name = 'created_at';
-- attendu : data_type = 'timestamp with time zone'

SELECT indexname FROM pg_indexes WHERE tablename = 'observations';
SELECT conname, contype FROM pg_constraint WHERE conrelid = 'entities'::regclass;
```

### 5.3 Test rapide en Python

```bash
DATABASE_URL='postgresql+psycopg://webintel:<mot-de-passe>@localhost:5432/webintel' \
python3 - <<'EOF'
import os
from sqlalchemy import create_engine, text
e = create_engine(os.environ["DATABASE_URL"])
print("dialecte :", e.dialect.name, "| driver :", e.dialect.driver)
with e.connect() as c:
    print("serveur :", c.execute(text("SELECT version()")).scalar().split(",")[0])
    print("tables  :", c.execute(text(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='public'")).scalar())
EOF
```

## 6. Particularités de l'implémentation

- **Driver** : `psycopg[binary]>=3.1` (psycopg v3, wheel binaire : pas
  de compilation, pas de libpq système requise côté client).
- **`webintel/database/base.py`** : `_make_engine()` applique
  `pool_pre_ping=True` pour les URL `postgresql*` (détecte les
  connexions mortes du pool après un redémarrage du serveur) et
  `check_same_thread=False` uniquement pour SQLite. Aucun autre
  comportement ne change entre les deux moteurs.
- **Types** : `TZDateTime` (`DateTime(timezone=True)`) devient
  `TIMESTAMPTZ` sous PostgreSQL ; les colonnes `JSON` utilisent le
  type natif `JSONB`-compatible de PostgreSQL via SQLAlchemy.
- **Idempotence** : `init_db()` utilise `create_all(checkfirst=True)` :
  relancer l'application sur une base existante ne détruit rien.
- **Tests existants** : la suite `pytest` tourne sur SQLite et reste la
  référence de non-régression (`python3 -m pytest tests/ -q`). Les tests
  PostgreSQL (`tests/test_phase0_postgres.py`, 7 tests) sont validés
  contre un vrai serveur (§8) ; sans serveur, ils sont skippés
  proprement — sauf si `WEBINTEL_REQUIRE_PG_TESTS=1` (§8).

## 7. Dépannage courant

| Symptôme | Cause probable | Solution |
|---|---|---|
| `docker compose up` refuse de démarrer : `Définissez POSTGRES_PASSWORD avant de démarrer` | variable manquante | Exportez `POSTGRES_PASSWORD` (et `SECRET_KEY`) avant la commande |
| `could not translate host name "db"` | `DATABASE_URL` pointe vers `db` hors Docker | En manuel, utilisez `localhost` (ou l'hôte réel), pas `db` |
| `No module named 'psycopg'` | dépendances non installées | `pip install -r requirements.txt` (contient `psycopg[binary]>=3.1`) |
| `password authentication failed for user "webintel"` | mauvais mot de passe / URL mal formée | Vérifiez `POSTGRES_PASSWORD` et l'URL (caractères spéciaux → encodez en `%XX`) |
| `connection refused` sur `localhost:5432` | PostgreSQL arrêté ou firewall | `sudo systemctl status postgresql` ; vérifiez `pg_hba.conf` / `listen_addresses` |
| `FATAL: database "webintel" does not exist` | base non créée | `CREATE DATABASE webintel OWNER webintel;` (cf. §4) |
| Erreurs `SSL` intermittentes / connexions mortes | pool avec connexions coupées | Déjà mitigé par `pool_pre_ping=True` ; sinon augmentez les timeouts côté serveur |
| Le script de validation échoue sur une base existante | tables déjà présentes avec d'autres données | Utilisez une base **vide** dédiée aux tests (`createdb webintel_test`) |

> Note : un mot de passe contenant `@`, `/`, `:` ou `?` doit être
> encodé (URL-encoded) dans `DATABASE_URL`, ex. `p@ss` → `p%40ss`.
> Préférez des mots de passe générés sans ces caractères.

## 8. Validation par les tests contre un vrai serveur (v2.1)

Le support PostgreSQL n'est plus « code écrit = validé » : la suite
`tests/test_phase0_postgres.py` tourne contre un **vrai serveur
PostgreSQL 16** et couvre `create_all`/`init_db`, le type JSON, les index,
les contraintes uniques, les upserts (dont le cas 50 € → 60 € → 50 €),
la fonction `upsert_source_observation` et l'idempotence sous contention
(8 threads → 1 seule ligne).

| Variable | Rôle |
|---|---|
| `WEBINTEL_TEST_PG_URL` | URL du serveur de test (défaut : `postgresql+psycopg://webintel:webintel@127.0.0.1:5432/webintel_test`) |
| `WEBINTEL_REQUIRE_PG_TESTS=1` | **Exige** la validation : si aucun serveur n'est joignable, `tests/test_postgres_gate.py` **échoue** au lieu de skipper |

```bash
# 1. Démarrer PostgreSQL 16 et créer rôle + base de test
#    (exemple : binaires embarqués via `pip install pgserver`, ou paquet système)
createuser -P webintel          # mot de passe : webintel (test uniquement)
createdb -O webintel webintel_test

# 2. Lancer la suite PG (+ le garde-fou en mode strict)
WEBINTEL_REQUIRE_PG_TESTS=1 \
WEBINTEL_TEST_PG_URL="postgresql+psycopg://webintel:webintel@127.0.0.1:5432/webintel_test" \
python3 -m pytest tests/test_phase0_postgres.py tests/test_postgres_gate.py -q

# 3. (optionnel) TOUTE la suite contre PostgreSQL au lieu de SQLite
WEBINTEL_TEST_DATABASE_URL="postgresql+psycopg://webintel:webintel@127.0.0.1:5432/webintel_test" \
python3 -m pytest tests/ -q
```

Sans `WEBINTEL_REQUIRE_PG_TESTS`, le comportement par défaut reste le
skip propre quand aucun serveur n'est joignable (la suite verte sur
SQLite ne prétend alors rien sur PostgreSQL).

**Fail-fast au démarrage** : quand `DATABASE_URL` pointe vers
PostgreSQL, le lifespan de l'application appelle
`verifier_demarrage_postgres()` (`webintel/database/base.py`) —
connexion vérifiée (timeout 5 s) AVANT `init_db()`, schéma minimal
(`projects`, `sources`, `entities`, `observations`,
`source_observations`) vérifié APRÈS. Serveur injoignable ou schéma
incomplet → `RuntimeError` explicite (mot de passe masqué), jamais de
démarrage silencieux dégradé.
