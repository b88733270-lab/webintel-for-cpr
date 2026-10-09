# Licences — WebIntel v2.3.0

> **Ce qui est VRAI** : licences relevées le 2026-09-23 dans les
> **métadonnées des paquets installés** (`.venv`). Règle : ne jamais
> déclarer libre ce qui ne l'est pas — et ne jamais déclarer ce qu'on
> n'a pas vérifié.

## 1. L'application WebIntel elle-même

⚠️ **Point ouvert** : `pyproject.toml` indique `license = { text = "Voir
LICENSE / README.md" }`, mais **aucun fichier LICENSE n'existe à la
racine du dépôt** au jour de la rédaction. En l'absence de ce fichier,
**aucune licence n'est formellement accordée** — ne considérez pas le code
comme libre de droits tant que ce n'est pas clarifié. (Action recommandée :
ajouter un fichier `LICENSE` explicite.)

## 2. Dépendances scientifiques clés (vérifiées)

| Paquet | Version installée | Licence (métadonnées) | Rôle | Restriction réelle |
|---|---|---|---|---|
| **RDKit** | 2026.3.6 | **BSD-3-Clause** | Empreintes Morgan, Tanimoto, descripteurs, normalisation | Permissive ; citation scientifique recommandée |
| **Meeko** | 0.8.0 | **LGPL-2.1** | Préparation SMILES → PDBQT (docking) | Copyleft faible : modification de Meeko lui-même → redistribution du code modifié ; simple usage via l'API : pas de contamination du code WebIntel |
| **AutoDock Vina** | binaire externe, **non fourni** | **Apache-2.0** (licence officielle du projet Vina) | Docking réel | Le binaire s'installe côté chercheur ; vérifiez la licence sur le site officiel d'AutoDock Vina avant redistribution |

**Non déclarés libres** : Meeko est LGPL (pas « libre sans condition ») ;
Vina n'est pas embarqué — sa licence s'applique à son installation
séparée.

## 3. Socle technique (vérifié)

| Paquet | Version | Licence |
|---|---|---|
| FastAPI | 0.141.1 | MIT |
| SQLAlchemy | 2.0.54 | MIT |
| Uvicorn | 0.53.0 | BSD-3-Clause |
| Pydantic | 2.13.5 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| openpyxl | 3.1.5 | MIT (lecture Excel — pas de pandas requis) |

Toutes permissives (MIT/BSD) : utilisation commerciale et redistribution
permises avec conservation des notices.

## 4. Connecteurs : licences des SOURCES de données

**Ce qui est VRAI** : chaque connecteur enregistre **licence, conditions
d'utilisation, date d'import, version et stratégie de mise à jour**
(tables `Source`/`Dataset`/`Provenance` — jamais de copie aveugle) :
PubChem, PubMed, OpenAlex, ChEMBL, GBIF.

⚠️ **Restriction réelle** : les données importées restent soumises aux
**conditions de leurs sources** (ex. : certaines bases exigent citation ou
interdisent la republication brute). Avant de republier un export contenant
des données de connecteurs, **relisez la fiche du connecteur**
(`GET /api/science/connecteurs`) et les conditions de la source.

## 5. Données ethnobotaniques : droit des savoirs

Les savoirs traditionnels enregistrés ne sont pas une « licence
logicielle » : leur réutilisation relève du **consentement des
détenteurs** et des cadres applicables (dont le Protocole de Nagoya pour
les ressources génétiques). WebIntel fournit la **protection technique**
(5 niveaux de confidentialité, masquage GPS) — voir
[`ETHNOBOTANY.md`](ETHNOBOTANY.md) — pas l'autorisation juridique.

## Limites

- Les licences ci-dessus sont celles des **versions installées** : une
  mise à jour (`pip install -U`) peut changer de version — revérifiez.
- Ce document ne constitue pas un avis juridique. En cas d'usage
  commercial ou de redistribution, faites valider par un juriste.
- Fichier `LICENSE` de l'application **manquant** : à créer avant toute
  diffusion.
