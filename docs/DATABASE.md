# Base de données — WebIntel v2.3.0

> **Ce qui est VRAI** : 52 tables (comptées depuis `Base.metadata`),
> index sur 100 % des clés étrangères (créés idempotents à l'initialisation,
> vérifiés sur la base réelle), pagination `limit`/`offset` sur 11 listes,
> backup/restore avec round-trip testé, SQLite vs PostgreSQL réellement
> comparés.

## 1. Modèles : les 52 tables

Domaines couverts (noms de tables principaux) :

- **Socle** : `projects`, `api_keys`, `crawl_jobs`, `entities`,
  `observations`, `sources`, `events`, `monitors`, `alerts`, `exports`.
- **Science** : `plants`, `compounds`, `plant_compounds`, `compound_targets`,
  `plant_activities`, `evidences`, `simulations`, `studies`,
  `study_versions`, `datasets`, `provenances`.
- **Terrain** : `echantillons`, `vouchers`, `extractions`, `extraits`,
  `fractions`, `analyses`, `pics_analyse`, `noms_vernaculaires`, `langues`,
  `usages_traditionnels`, `mesures_experimentales`, `notes_labo`.
- **Workflows** : `wizard_sessions`, `jobs`, `file_sync` (file de
  synchronisation), `conflits_sync`.
- **Combinaisons / synergie / docking** : `etudes_combinaison`,
  `analyses_synergie`, `docking_jobs`.

Compter soi-même :
```python
from webintel.database import Base
import webintel.main
len(Base.metadata.tables)  # 52
```

## 2. Index : 100 % des clés étrangères

**Ce qui est VRAI** (`_ensure_fk_indexes`, `webintel/database/base.py`) :
à chaque initialisation, un index est créé idem-potemment
(`CREATE INDEX IF NOT EXISTS`) sur **chaque colonne de clé étrangère**
— vérifié sur la base réelle (`ix_observations_source_id`,
`ix_plant_compounds_evidence_id`, …). Les colonnes déjà indexées
(`index=True`, contrainte UNIQUE) sont ignorées ; l'index invalide
`ix_evidences_project_id` (colonne inexistante) a été supprimé.

Gains mesurés (chantier v2.3.0) : chaîne échantillon 22 → 5 requêtes SQL,
fiche plante 9/11 requêtes, recherche 5, études 1 — constants par volume
(N+1 éliminés).

## 3. Pagination : 11 listes

`limit` (1…500, **défaut 50**) / `offset` (défaut 0) sur : noms
vernaculaires, usages, échantillons, vouchers, analyses, pics, preuves,
observations, simulations, notes labo, workflows.

Exemple : `GET /api/science/terrain/vouchers?limit=50&offset=100`.

## 4. SQLite vs PostgreSQL

|  | SQLite (défaut local) | PostgreSQL (mode serveur) |
|---|---|---|
| Configuration | Zéro — `./webintel.db` créé au démarrage | `DATABASE_URL=<redacted>` |
| Usage | Mono-utilisateur, terrain, démo | Multi-utilisateur, production |
| Validation | Suite complète 1090/17/0 | **Vrai serveur 18.6** : 12/12 `scripts/test_postgres.py`, 8/8 tests dédiés (gate `WEBINTEL_REQUIRE_PG_TESTS=1` — échoue au lieu de skipper), 22/22 tests du chantier |
| Concurrence | Limitée (verrou d'écriture) | 8 threads → idempotence vérifiée (noms, versions, vouchers…) |

**Fail-fast** : si `DATABASE_URL` pointe un PG injoignable, le démarrage
échoue explicitement (pas de bascule silencieuse vers SQLite).

## 5. Backup / restore : round-trip vérifié

**Ce qui est VRAI** (`webintel/offline/sauvegarde.py`) :
- `exporter_projet(session, chemin_zip, …)` : export complet d'un projet
  en ZIP (inclut `langues` + `wizard_sessions` depuis la v2.3.0).
- `importer_projet(session, chemin_zip)` : restauration générique (PK
  simples et composites), contrôle de version majeure.
- **Round-trip testé** (`test_sauvegarde_roundtrip_complet.py`) : export →
  base fraîche → import → **12 tables relues champ à champ**, sur SQLite
  **et** PostgreSQL.

Page UI : `/sauvegarde`. Recommandé avant chaque mission de terrain.

## 6. Migrations

Migrations **idempotentes** et rejouables (`scripts/migrate_v1_to_v1_1.py`,
`scripts/migrate_v1_1_to_v1_1_1.py`) + évolutions automatiques à
l'initialisation (`_ensure_study_versions_unique`,
amorçage idempotent du registre `langues`).

## Limites

- **Pas de test de charge** sur PostgreSQL : la validation est
  fonctionnelle (schéma, contraintes, concurrence), pas capacitaire.
- SQLite : ne convient pas à un usage multi-utilisateur simultané en
  écriture.
- Les sauvegardes ZIP ne sont **pas chiffrées** : protégez leur stockage
  si elles contiennent des données `confidentiel`.
- `install.bat` / restauration sous Windows natif : non testés sur un
  vrai Windows.
