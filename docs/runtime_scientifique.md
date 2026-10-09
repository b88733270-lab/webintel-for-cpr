# WebIntel — Runtime scientifique optionnel (CORE vs SCIENCE)

> **Principe** : le mode simple reste léger et ne dépend de rien d'optionnel.
> Le niveau SCIENCE ajoute la chimoinformatique réelle (RDKit) et le docking
> (AutoDock Vina). **Jamais imposé** : sans ces outils, l'application tourne
> et indique explicitement ce qui n'est pas calculable — **aucune valeur
> n'est jamais inventée**.

## Les deux niveaux

| | CORE (toujours) | SCIENCE (optionnel) |
|---|---|---|
| Installation | `./install.sh` (requirements.txt) | `./scripts/install_science.sh` ou `pip install -e ".[science]"` |
| Dépendances | Python pur (stdlib + requirements de base) | CORE + **RDKit** (extra `science`) |
| Similarité moléculaire | Heuristique documentée : Tanimoto sur comptes d'éléments de la formule brute, 1er bloc InChIKey, bonus de classe — étiquetée « heuristique sans RDKit, non structurale » | + similarité **structurelle réelle** : Tanimoto sur empreintes de Morgan (rayon 2, 2048 bits), comparaison par sous-structure |
| Propriétés | Masse molaire + comptes d'atomes depuis la formule brute ; logP, HBD/HBA, TPSA → `non_calcule` | + logP, HBD/HBA, TPSA, liaisons rotatives, fraction Csp3, violations de Lipinski **réellement calculés** par RDKit |
| Docking | Refus explicite (Vina absent) | Vrai docking local si le binaire `vina` est installé |
| Lipinski | Verdict `partiel` (masse seule) | Verdict `conforme` / `non_conforme` complet |

## Installation (chercheur non-développeur)

```bash
# 1. Installation simple (comme d'habitude)
./install.sh

# 2. Activer le niveau SCIENCE (optionnel, idempotent)
./scripts/install_science.sh
```

Le script installe l'extra `science` (`rdkit`) dans le `.venv`, ajoute
`meeko` (préparation SMILES → PDBQT pour le docking, recommandé), puis
vérifie fonctionnellement RDKit (empreinte de Morgan + descripteurs de
l'aspirine).

Alternatives manuelles :

```bash
pip install -e ".[science]"          # depuis la racine du projet
# ou dans un requirements.txt :
#   webintel[science]
# ou via conda :
conda install -c conda-forge rdkit
```

Pour le docking moléculaire (moteur externe, indépendant de l'extra) :

```bash
sudo apt install autodock-vina        # Debian/Ubuntu
# ou
conda install -c conda-forge autodock-vina
```

## Vérifier l'état : page Paramètres → « Outils scientifiques »

L'interface (page **Paramètres**) affiche un bloc « Outils scientifiques »
avec, pour RDKit et AutoDock Vina : présent/absent, version capturée, ce que
l'outil change, et les instructions d'installation quand il est absent.

Équivalent programmatique :

```python
from webintel.science import chimie
chimie.infos_biblio()
# {"bibliotheque": "RDKit", "version": "2026.03.6",
#  "disponible": True, "raison": ""}

from webintel.simulations.plugins import docking_adapter
docking_adapter.diagnostic_vina()
# {"binaire": "/usr/bin/vina", "version": "AutoDock Vina 1.2.7",
#  "disponible": True, "raison": ""}
```

## Module `webintel/science/chimie.py` (contrat stable)

Pont optionnel vers RDKit, utilisé par les moteurs de simulation et le
docking. Fonctions publiques (signatures garanties) :

- `rdkit_disponible() -> bool`
- `empreinte_morgan(smiles, rayon=2, taille=2048) -> list[int] | None`
- `tanimoto_fp(fp1, fp2) -> float | None` (arithmétique pure — fonctionne sans RDKit)
- `descripteurs(smiles) -> dict | None` (11 clés : `formule`, `masse_molaire`, `logp`, `hbd`, `hba`, `tpsa`, `rotatable_bonds`, `fraction_csp3`, `lipinski_violations`, `inchi_key`, `smiles_canonique` ; valeur `None` si non calculable)
- `sous_structure(smiles, motif_smarts) -> bool | None`
- `infos_biblio() -> dict`
- bonus : `diagnostic_smiles(smiles) -> dict` (diagnostic explicite)

Règles : entrées strictement validées (SMILES non vide, type `str`) ;
SMILES invalide → `None` + raison explicite journalisée (`logging`) ;
**jamais d'exception brute, jamais de valeur inventée**.

## Honnêteté scientifique (règles dures)

1. **Distinction explicite des sorties** du moteur `molecular_similarity` :
   `formule_similaire` (ressemblance de formule brute — heuristique),
   `structure_similaire` (Tanimoto de Morgan — structurelle réelle),
   `classe_chimique_similaire` (étiquette déclarative). **Ne jamais présenter
   une ressemblance de formule comme une similarité pharmacologique** —
   l'avertissement est obligatoire dans chaque rapport.
2. **Aucune valeur inventée** : sans RDKit, les descripteurs portent
   `"statut": "non_calcule"` + raison ; sans Vina, le docking est refusé
   (`RuntimeError` explicite), jamais simulé.
3. **Provenance enregistrée** : chaque résultat de `property_prediction`
   porte `methode`, `bibliotheque`, `version_bibliotheque`, `entree`,
   `limites` ; chaque docking enregistre version Vina, paramètres, hash
   SHA-256 des entrées, sorties, limites.
4. **Résultat informatique** : tout docking porte l'avertissement
   « RÉSULTAT INFORMATIQUE — PRÉDICTION, PAS UNE MESURE » (seed fixé pour
   la reproductibilité).
5. Les simulations restent au niveau de preuve `prediction`, strictement
   séparées des données publiées, normalisées et expérimentales
   (voir `docs/donnees-scientifiques.md`).

## Limites connues

- RDKit : les descripteurs (ex. logP de Wildman-Crippen) sont des
  **estimations théoriques**, pas des mesures ; qualité dépendante du SMILES
  (tautomère, protonation, stéréochimie).
- Docking : préparation SMILES → PDBQT = un seul conformère de départ
  (ETKDGv3, seed fixé), protonation pH neutre par défaut, récepteur utilisé
  tel quel (WebIntel ne prépare pas le récepteur) ; nécessite RDKit + meeko,
  sinon fournir directement un ligand PDBQT.
- Dérogation documentée au contrat plugin : exécution du seul binaire local
  `vina` via `subprocess` (`argv` fixe, `shell=False`) + fichiers temporaires
  confinés — aucune commande arbitraire (voir `LIMITATIONS` de
  `docking_adapter`).
