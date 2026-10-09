# WebIntel — Moteurs de simulation (Phase 5)

> **Règle dure** : une simulation n'est JAMAIS présentée comme une preuve
> expérimentale. Chaque rapport généré par WebIntel porte l'avertissement
> **« Simulation — à confirmer expérimentalement »**, et les données de
> simulation restent au niveau de preuve `prediction`, strictement séparées
> des données publiées, normalisées et expérimentales
> (voir [docs/donnees-scientifiques.md](donnees-scientifiques.md)).

## Les 5 moteurs

| Moteur | Type | Méthode | Entrées → Sorties | Disponible ici ? | Limitations principales |
|---|---|---|---|---|---|
| `molecular_similarity` | Exécutable (pur Python + RDKit optionnel) | Heuristique documentée (Tanimoto sur comptes d'éléments de la formule brute, 1er bloc InChIKey, classe déclarée) + — si RDKit installé et SMILES fournis — similarité **structurelle réelle** (Tanimoto sur empreintes de Morgan) et sous-structures | `composes[]` (formule / SMILES / InChIKey) → paires avec `formule_similaire`, `structure_similaire`, `classe_chimique_similaire` **explicitement distinguées** | ✅ Oui | Sans RDKit : heuristique « sans RDKit, non structurale ». Une ressemblance de formule n'est **jamais** une similarité pharmacologique (avertissement obligatoire) |
| `property_prediction` | Exécutable (pur Python + RDKit optionnel) | Calcul exact depuis la formule brute (masse molaire, atomes) + — si RDKit installé et SMILES fourni — logP, HBD/HBA, TPSA, liaisons rotatives, fraction Csp3, Lipinski **réellement calculés** | `composes[]` (formule + SMILES optionnel) → propriétés + `provenance_calcul` (méthode, bibliothèque, version, entrée, limites) | ✅ Oui | Sans RDKit/SMILES : logP, HBD/HBA, TPSA → `non_calcule` (jamais inventés) ; Lipinski `partiel` (masse seule) |
| `interaction_analysis` | Exécutable (pur Python) | Analyse descriptive d'un réseau composé↔cible déjà enregistré (comptages, tops, distribution des confiances) | `liens[]` → statistiques du réseau | ✅ Oui | N'infère **aucun** lien : ne prédit rien, décrit les liens fournis |
| `statistics` | Exécutable (pur Python, `statistics`) | Statistiques descriptives : n, moyenne, médiane, écart-type, min/max, IC de la moyenne (approx. normale) | `observations[]` (valeur, unité, libellé) → résumé statistique | ✅ Oui | Purement descriptif : ne prouve ni efficacité ni causalité ; IC approximatif si n < 30 |
| `docking_adapter` | **Adaptateur** (AutoDock Vina, externe) | VRAI docking local (énergie libre de liaison estimée, kcal/mol) — binaire `vina` détecté dans le PATH, version capturée, `argv` fixe (`shell=False`), seed fixé, hash SHA-256 des entrées | `recepteur_pdbqt` + `ligand_pdbqt` ou `ligand_smiles` (→ PDBQT via RDKit+meeko) → affinités par pose, poses PDBQT, provenance complète | ⚠️ Si Vina installé | **Refuse** de calculer tant que Vina est absent (jamais de faux docking) ; résultat **informatique** — prédiction à confirmer in vitro |

Détail par moteur : `GET /api/science/simulations/moteurs` (statut réel,
évalué à chaque appel) ou la page **Simulations** de l'interface.

## Activer plus de moteurs

Les 4 moteurs exécutables fonctionnent sans dépendance externe (le niveau
SCIENCE — RDKit — les enrichit : voir `docs/runtime_scientifique.md`).
Pour activer `docking_adapter` :

### RDKit (niveau SCIENCE — enrichit `molecular_similarity` et `property_prediction`)

```bash
./scripts/install_science.sh
# ou : pip install -e ".[science]"
# ou : conda install -c conda-forge rdkit
```

RDKit débloque les vrais calculs structuraux : empreintes de Morgan +
Tanimoto réel, descripteurs (logP, HBD/HBA, TPSA, Lipinski complet),
recherche par sous-structure. **Tant qu'un moteur n'utilise pas RDKit, ses
limitations le disent explicitement** — aucun descripteur structurel n'est
« deviné » depuis une formule brute. État visible : page **Paramètres** →
« Outils scientifiques ».

### AutoDock Vina (active `docking_adapter` — vrai docking local)

```bash
# Debian/Ubuntu
sudo apt install autodock-vina
# ou via conda
conda install -c conda-forge autodock-vina
```

Vérification : `vina --version` doit répondre. WebIntel détecte le
binaire via `shutil.which("vina")` et capture sa version :
l'adaptateur passe alors à « Disponible » et `run()` exécute le vrai
docking local — préparation PDBQT (récepteur fourni tel quel ; ligand
PDBQT fourni ou SMILES → PDBQT via RDKit + meeko, préparation documentée)
→ appel Vina (`argv` fixe, `shell=False`, seed fixé, boîte bornée,
délai max 600 s) → affinités en kcal/mol + poses PDBQT + hash SHA-256 des
entrées + version Vina + avertissement « résultat informatique ».
Tant que Vina est absent, toute exécution est **refusée** (HTTP 422) —
aucun faux résultat n'est jamais produit.

### Open Babel (conversions de formats, génération 3D)

```bash
sudo apt install openbabel
```

### Modèle QSAR externe

Aucun modèle QSAR n'est embarqué (pas de « boîte noire ») : un futur
adaptateur QSAR exigera un modèle versionné, sa documentation et son
jeu de validation — jamais un score sans méthode.

## Ajouter un moteur (contrat plugin)

Voir `webintel/simulations/plugins/__init__.py` : un module définit
`ENGINE_NAME`, `ENGINE_VERSION`, `DESCRIPTION`, `INPUTS`, `PARAMETERS`,
`LIMITATIONS`, `is_available()`, `validate_inputs(inputs, params)` et
`run(inputs, params)` (déterministe, JSON-sérialisable, sans shell, sans
réseau, sans accès base), puis s'enregistre dans `ENGINE_MODULES` de
`webintel/simulations/registry.py`. `check_plugin()` valide le contrat
mécaniquement.

## Exécution

- API : `POST /api/science/simulations/run`
  (`engine_name`, `name`, `inputs`, `parameters`, `project_id`,
  `description`, `references`) → `201` + `{simulation_id, avertissement,
  rapport}`. Moteur indisponible ou entrées invalides → `422` explicite ;
  moteur inconnu → `404` ; projet étranger → `403` (mode serveur).
- Interface : page **Simulations** (liste des moteurs avec badges
  Disponible/Indisponible, formulaire pour les moteurs disponibles,
  rapport affiché avec l'avertissement).
- Persistance : chaque exécution crée une `Simulation`
  (`engine_name`, `engine_version`, `input_hash` = SHA-256 des entrées
  normalisées, statut `exploratoire`) — requêtable via
  `GET /api/science/simulations`.

## Journal d'audit immuable (Phase 13)

Chaque exécution enregistre : **qui** (`executed_by` — `clé:<préfixe>`,
`ui:<préfixe>` ou `local`), **quand** (`run_at`), **empreinte SHA-256**
(`input_hash`), **entrées normalisées** (`inputs`), paramètres, sorties,
**version du moteur**. Immuabilité :

- aucune route de suppression des simulations ;
- `PATCH /api/science/lab/simulations/{id}` refuse (409) les colonnes
  d'audit (`CHAMPS_SIMULATION_IMMUTABLES`) ;
- page **Simulations** : tableau « Simulations précédentes » (badge
  « Prédiction », empreinte, exécutant).

## Rapport reproductible (Phase 13)

- `GET /api/science/simulations/{id}/rapport?format=json|html|markdown` :
  reconstruit INPUTS → METHOD → PARAMETERS → OUTPUTS → REFERENCES →
  LIMITATIONS depuis les champs persistés, + métadonnées d'audit et
  avertissement. `markdown` télécharge le document.
- Interface : `/simulations/{id}/rapport` (page + lien ⬇ Markdown).
- Rejeu : `POST /api/science/simulations/{id}/rejouer` relance le même
  moteur avec les mêmes entrées/paramètres → nouvelle simulation, même
  `input_hash` (moteur déterministe → mêmes sorties). 422 si le moteur
  est indisponible ou si la simulation n'a pas d'entrées rejouables.

## Limites honnêtes

- Une simulation est une **prédiction**, jamais une preuve
  expérimentale — chaque réponse et chaque rapport portent l'avertissement.
- Les moteurs doivent être **déterministes** (contrat plugin) pour que
  le rejeu soit significatif ; `docking_adapter` exige un binaire externe
  (AutoDock Vina) et apparaît indisponible sans lui — aucun faux calcul.
