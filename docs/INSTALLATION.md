# Installation — WebIntel v2.3.0

> **Ce qui est VRAI** : installation locale en deux scripts (`install.sh`,
> `start.sh`), base SQLite créée automatiquement, aucune base à installer,
> aucune connaissance Python/JSON/terminal requise **après** l'installation.
> Parcours vérifié : installation → diagnostic → niveau scientifique →
> lancement → navigateur.

## 1. Prérequis

- **Python 3.10 ou plus récent** (testé avec Python 3.12). Vérifier :
  `python3 --version`.
- Un navigateur web récent (l'interface est une application web locale).
- ~500 Mo d'espace disque (environnement virtuel + dépendances).
- Internet **uniquement pendant l'installation** (téléchargement des
  dépendances). Après, l'application fonctionne hors-ligne en mode local
  (voir [`OFFLINE_MODE.md`](OFFLINE_MODE.md)).

**Windows** : `install.bat` / `start.bat` existent à la racine, mais n'ont
**jamais été exécutés sur un vrai Windows** — en cas de doute, préférez
WSL2 ou une machine Linux. C'est une limite déclarée, pas une promesse.

## 2. Installation (3 commandes)

```bash
cd webintel
./install.sh
```

Ce que fait `install.sh` (idempotent — relançable sans risque) :
1. Vérifie Python 3.10+.
2. Crée l'environnement virtuel `.venv`.
3. Installe les dépendances du niveau **CORE** (léger : FastAPI, SQLAlchemy…).
4. Initialise la base SQLite locale `./webintel.db`.

Puis :

```bash
./start.sh        # → http://127.0.0.1:8000
```

Options de `start.sh` :
- `PORT=8080 ./start.sh` — changer le port (si le 8000 est occupé).
- `WEBINTEL_HOST=0.0.0.0 ./start.sh` — exposer sur le réseau local
  (réservé à un usage de confiance ; voir [`SECURITY.md`](SECURITY.md)).

**Mode local par défaut : SQLite, sans clé API** (usage mono-utilisateur de
confiance). Le mode serveur (PostgreSQL + clé API obligatoire) se configure
via les variables d'environnement — voir §5 et [`SECURITY.md`](SECURITY.md).

## 3. Diagnostic : vérifier que tout fonctionne

Page **« Outils scientifiques »** (`/outils-scientifiques`) dans
l'application : elle affiche l'état réel de chaque composant —
**RDKit, Meeko, Vina, PostgreSQL** — détecté à l'exécution, jamais supposé.

En ligne de commande (optionnel) :

```bash
.venv/bin/python -c "import rdkit; print(rdkit.__version__)"  # niveau SCIENCE
```

## 4. Niveau scientifique (optionnel mais recommandé)

Sans lui, WebIntel fonctionne en **mode CORE** et indique explicitement ce
qui n'est pas calculable — **aucune valeur n'est jamais inventée**.

```bash
./scripts/install_science.sh
```

Ce que fait le script (idempotent) :
1. Installe l'extra `science` : **RDKit** (empreintes de Morgan, descripteurs,
   recherche par sous-structure, normalisation SMILES/InChI/InChIKey).
2. Installe **Meeko** (préparation SMILES → PDBQT pour le docking).
3. Vérifie que RDKit est importable et affiche sa version.

**AutoDock Vina n'est PAS installé par ce script** (binaire externe, licence
Apache-2.0 — voir [`LICENSES.md`](LICENSES.md)). Sans lui, le docking
**refuse proprement** : l'aperçu du workflow s'affiche, les scores restent
`null`, aucun faux résultat. Pour l'installer, suivez la documentation
officielle d'AutoDock Vina, puis vérifiez dans « Outils scientifiques ».

## 5. Mode serveur (optionnel — multi-utilisateur)

Variables d'environnement (voir [`SECURITY.md`](SECURITY.md) pour le détail) :

| Variable | Effet |
|---|---|
| `REQUIRE_API_KEY=1` | Clé API obligatoire (défaut : `0` en local) |
| `SECRET_KEY=<secret-long>` | Signature des sessions UI (défaut dev — **à changer**) |
| `DATABASE_URL=<redacted> | Bascule sur PostgreSQL |

La page « Paramètres » de l'application expose ces réglages. PostgreSQL a
été validé contre un **vrai serveur 18.6** (12/12 `scripts/test_postgres.py`,
8/8 tests dédiés) — mais **pas de test de charge** : dimensionnez avant une
mise en production réelle.

## 6. Ce que le chercheur n'a PAS besoin de connaître

Après `./start.sh`, **aucune** des compétences suivantes n'est requise :

- Python, le terminal, JSON, SQL.
- La structure de la base de données (52 tables — voir
  [`DATABASE.md`](DATABASE.md) pour les curieux).
- L'API REST (documentée dans [`API.md`](API.md) pour les intégrations).
- RDKit, Vina, Meeko : la page « Outils scientifiques » dit en langage
  simple ce qui est disponible et ce qui ne l'est pas.
- Les niveaux de preuve : l'interface affiche des **badges en français**
  (« Publié », « Prédiction », « Hypothèse »…) avec l'explication
  « Qu'est-ce que cela signifie ? » sous chaque métrique.

Le parcours chercheur complet est décrit dans
[`RESEARCHER_GUIDE_FR.md`](RESEARCHER_GUIDE_FR.md).

## Limites

- `install.sh` suppose un système de type Unix (Linux/macOS). Windows natif :
  scripts `.bat` fournis **non testés sur un vrai Windows**.
- L'installation télécharge des paquets depuis PyPI (internet requis une fois).
- Le mode local SQLite est mono-utilisateur : ne l'exposez pas sur internet
  sans passer en mode serveur (`REQUIRE_API_KEY=1`, `SECRET_KEY` fort).
- Les sessions du wizard expirent après **30 jours** d'inactivité
  (brouillons `en_cours` purgés ; études terminées conservées).
