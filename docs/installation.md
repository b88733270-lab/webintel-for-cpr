# Installation de WebIntel (Phase 12)

WebIntel s'installe en **deux commandes**, sans base de données à configurer
et sans cloud obligatoire. Le mode local utilise SQLite ; le mode serveur
(multi-utilisateurs) utilise PostgreSQL via Docker.

## Installation locale (recommandée pour découvrir)

### Linux / macOS

```bash
cd webintel
./install.sh
./start.sh
```

Ouvrez ensuite **http://127.0.0.1:8000** (interface web) et
**http://127.0.0.1:8000/docs** (documentation interactive de l'API).

Prérequis : **Python 3.10+** (3.12 recommandé). C'est tout.

### Windows

Double-cliquez sur `install.bat`, puis sur `start.bat` (ou en ligne de
commande : `install.bat` puis `start.bat`). Même résultat :
http://127.0.0.1:8000.

Prérequis : **Python 3.10+** depuis https://www.python.org/downloads/
(cocher « Add python.exe to PATH » pendant l'installation).

### Ce que font les scripts

| Script | Actions |
|---|---|
| `install.sh` / `install.bat` | vérifie Python 3.10+, crée `.venv`, installe les dépendances (`requirements.txt`), initialise la base SQLite `./webintel.db` |
| `start.sh` / `start.bat` | vérifie `.venv` (sinon propose `./install.sh`), (re)crée la base si besoin, lance l'interface web |

Les scripts sont **idempotents** : on peut les relancer sans risque.

Options de `start.sh` :

```bash
PORT=8080 ./start.sh          # écoute sur http://127.0.0.1:8080
WEBINTEL_HOST=0.0.0.0 ./start.sh   # accessible sur le réseau local
                                   # (réservé à un usage de confiance ;
                                   # pour un vrai multi-utilisateurs,
                                   # utilisez le mode serveur ci-dessous)
```

## Mode serveur (PostgreSQL + clé API)

Pour un usage partagé / exposé sur le réseau :

```bash
# 1. Clé secrète forte OBLIGATOIRE (refusée si faible ou par défaut) :
export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
# 2. Mot de passe PostgreSQL (jamais en dur dans le dépôt) :
export POSTGRES_PASSWORD="<redacted>"
# 3. Démarrage :
docker compose up --build -d
```

L'application écoute alors sur http://localhost:8000 avec
`REQUIRE_API_KEY=true` : chaque appel API exige `X-API-Key`, et l'interface
web exige une connexion par clé API (session signée, cookie `Secure` en
HTTPS). Créez une clé avec `scripts/create_api_key.py`.

Détails : [docs/modes-deploiement.md](modes-deploiement.md),
[docs/postgresql.md](postgresql.md), [docs/securite.md](securite.md).

## Où sont les données ?

| Élément | Mode local | Mode serveur (Docker) |
|---|---|---|
| Base de données | `./webintel.db` (SQLite) | volume `pgdata` (PostgreSQL 16) |
| Exports, uploads | `./webintel_data/` | volume `webintel_data` |
| Préférences d'interface (ex. IA on/off) | `./webintel_data/runtime_config.json` | volume `webintel_data` |

Sauvegardez `./webintel.db` et `./webintel_data/` pour conserver vos
données (mode local).

## Dépannage

- **`python3 est introuvable`** : installez Python 3.10+ puis relancez
  `./install.sh`.
- **Port déjà utilisé** : `PORT=8080 ./start.sh`.
- **Après une mise à jour du code** : relancez `./install.sh` (met à jour
  les dépendances sans toucher à vos données).
- **Repartir de zéro (mode local)** : arrêtez le serveur, supprimez
  `./webintel.db`, relancez `./start.sh`.
- **L'IA ne s'affiche plus** : vérifiez la page **Paramètres**
  (`WEBINTEL_AI_ENABLED`, voir [docs/ia.md](ia.md)).

## Vérification rapide

```bash
curl http://127.0.0.1:8000/api/science/ia/statut
# {"ia_activee": true, "source_decision": "defaut", ...}
```

## Limites honnêtes

- `install.bat` / `start.bat` sont relus et réalistes mais **non exécutés
  sur Windows** dans cette livraison (tests effectués sur Linux uniquement).
- Le mode serveur suppose Docker / Docker Compose installés.
- Aucune donnée d'exemple n'est chargée par `install.sh` : la base démarre
  vide (utilisez l'import CSV/JSON, page **Import**, ou les connecteurs
  PubChem/PubMed, page **Connecteurs**).
