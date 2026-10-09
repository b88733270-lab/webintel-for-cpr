# Méthodes scientifiques — WebIntel v2.3.0

> **Ce qui est VRAI** : ce document décrit les méthodes réellement
> implémentées (vérifiées dans `webintel/science/`), avec pour chacune :
> ce qu'elle calcule vraiment, avec quel outil et quelle version, et ses
> **limites explicites**. Principe absolu : une prédiction reste une
> prédiction — jamais présentée comme un fait.

## 1. Normalisation moléculaire (RDKit)

**Ce qui est calculé** : à partir d'un SMILES fourni, RDKit produit le
**SMILES canonique**, l'**InChI** et l'**InChIKey** (identifiant universel).
Module : `webintel/science/normalisation_moleculaire.py`.

**Règle d'honnêteté** : un SMILES **invalide est signalé, jamais corrigé
silencieusement**. Le rapport de normalisation indique ce qui a échoué et
pourquoi.

**Limites** :
- La normalisation ne vérifie pas que la molécule *existe* réellement ni
  qu'elle a été isolée de la plante — seulement que sa représentation
  chimique est valide.
- Tautomères/stéréoisomères : la forme canonique choisie par RDKit peut
  différer de la forme naturelle.

## 2. Empreintes de Morgan et similarité de Tanimoto

**Ce qui est calculé** : empreintes binaires de Morgan (ECFP-like, via
RDKit), puis **coefficient de Tanimoto réel** entre deux empreintes
(`webintel/science/chimie.py` : `empreinte_morgan`, `tanimoto_fp`).
Vérifié : caféine/théophylline = **0,5294** (reproductible).

**Ce que ça mesure vraiment** : la proportion de fragments chimiques
sous-structurels partagés entre deux molécules. Rien de plus.

**Limites — à lire deux fois** :
- **Tanimoto ≠ similarité pharmacologique.** Deux molécules à 0,9 peuvent
  avoir des activités biologiques totalement différentes (ex. : un simple
  changement de stéréochimie).
- Le score dépend du rayon et de la taille de l'empreinte : deux études
  avec des paramètres différents ne sont pas comparables — c'est pourquoi
  chaque résultat enregistre **outil + version + paramètres**.
- La fiche distingue explicitement similarité de *formule*, de
  *structure* et de *classe* — ne les confondez pas.

## 3. Descripteurs moléculaires (RDKit)

**Ce qui est calculé** : logP, donneurs/accepteurs de liaisons H (HBD/HBA),
TPSA, nombre de liaisons rotatables, fraction de carbones sp3, **règles de
Lipinski**. Chaque valeur est accompagnée de méthode / bibliothèque /
version / entrées / sorties / limites (`descripteurs()` dans
`webintel/science/chimie.py`).

**Limites** :
- Ce sont des propriétés *calculées* d'une structure 2D, pas des mesures.
  Le logP calculé peut différer du logP expérimental.
- Les règles de Lipinski sont une **heuristique de drug-likeness orale**,
  pas un verdict : de nombreux médicaments les violent.

## 4. Études de combinaisons — dossier en 12 points

**Ce qui est produit** : pour une combinaison de composés, un **dossier
d'analyse informatique en 12 points** (structures, similarité, propriétés,
cibles, hypothèses, limites…, conclusion). La conclusion est **toujours**
libellée « Résultat de screening informatique » / « Hypothèse à vérifier
expérimentalement » (`webintel/science/combinaisons.py`).

**Garde-fou** : sans RDKit disponible, la construction du dossier lève une
erreur explicite au lieu de produire un dossier dégradé silencieusement.

**Limites** : le « score combiné » affiché est une **heuristique
informatique** — la fiche l'étiquette « Score — méthode combinée », jamais
« Tanimoto ». Il ne prédit pas l'effet réel d'un mélange.

## 5. Analyse de synergie : Bliss, Loewe, HSA, ZIP

**Ce qui est calculé** : scores de synergie selon les 4 modèles de
référence (`webintel/science/synergie/` : `bliss.py`, `loewe.py`,
`hsa.py`, `zip.py`), avec graphique SVG, méthode, données, calculs,
limites et interprétation prudente.

**Garde-fous (bloquants)** : le calcul **refuse** si les données
expérimentales importées sont insuffisantes (ex. : pas de courbe
dose-réponse complète pour Loewe). Chaque résultat rappelle la méthode
et ses conditions de validité.

**Limites** :
- La synergie *in vitro* ne prédit pas l'effet *in vivo*, encore moins
  l'effet clinique.
- Les 4 modèles peuvent **se contredire** sur les mêmes données : le
  dossier les présente ensemble, sans trancher à votre place.

## 6. Docking moléculaire (AutoDock Vina)

**Ce qui est VRAI** : workflow en 9 étapes, exécution du **binaire officiel
AutoDock Vina réel** (validé : Vina 1.2.7, kinase c-Abl 1IEP préparée via
Meeko, imatinib, seed 42, 5 poses, affinités
[−8,517, −8,426, −8,337, −8,287, −8,227] kcal/mol — reproductible).
Préparation du récepteur PDBQT **exigée** ; l'aperçu du workflow affiche
étapes + paramètres validés avec `scores: null`.

**Ce qui est REFUSÉ** : sans binaire Vina, le calcul **refuse proprement**
avec la raison explicite — **aucun score n'est jamais inventé**
(`statut_moteur()` vérifié à chaque appel ; page « Outils scientifiques »).

**Limites — fondamentales** :
- Un score de docking est une **prédiction d'affinité de liaison**, pas
  une preuve d'activité biologique, et encore moins d'efficacité
  thérapeutique.
- Le résultat dépend du récepteur préparé, de la boîte de recherche et du
  seed : sans ces paramètres (enregistrés dans la provenance), il n'est
  pas reproductible.
- Le binaire Vina **n'est pas fourni** avec WebIntel (installation
  chercheur-side, voir [`INSTALLATION.md`](INSTALLATION.md)).

## 7. Connecteurs de données externes

**Ce qui est VRAI** : 5 connecteurs enregistrés avec **licence, conditions
d'utilisation, date d'import, provenance, version et stratégie de mise à
jour** (table `Source`/`Dataset`/`Provenance` — jamais de copie aveugle) :
**PubChem, PubMed, OpenAlex, ChEMBL, GBIF**. Chaque import enregistre sa
source ; l'anti-SSRF à connexion épinglée protège les appels réseau.

**Limites** :
- Les connecteurs interrogent des bases **publiques** : leurs données sont
  étiquetées `publie`, jamais `experimental`.
- GBIF sert à **vérifier un nom scientifique** (statut taxonomique, nom
  accepté proposé en cas de synonyme) — pas à valider une activité.
- ChEMBL/OpenAlex/GBIF : vérifiez les **conditions de réutilisation**
  avant toute republication — voir [`LICENSES.md`](LICENSES.md).
- Sans internet, les connecteurs sont indisponibles (dégradation propre,
  voir [`OFFLINE_MODE.md`](OFFLINE_MODE.md)).

## 8. Synthèse IA (désactivable)

**Ce qui est VRAI** : une synthèse en langage naturel peut être générée
pour une plante (`GET /api/science/ia/synthese/plante/{id}`), **seulement
si `WEBINTEL_AI_ENABLED=true`** (désactivable aussi depuis la page
Paramètres).

**Garde-fous testés** :
- La synthèse IA **n'est jamais persistée comme un fait** (suite
  comportementale).
- Les données privées du laboratoire sont **exclues par défaut**.
- Les formulations interdites (« efficace », « sûr », « traite ») sont
  bannies par test.

**Limites** : une synthèse IA est une reformulation, pas une source. Elle
ne remplace ni la lecture des publications ni l'expertise du chercheur.

## Tableau récapitulatif : ce que chaque méthode NE prouve PAS

| Méthode | Ne prouve pas |
|---|---|
| Tanimoto / Morgan | Une similarité d'effets |
| Descripteurs / Lipinski | Qu'une molécule est un médicament |
| Dossier 12 points (combinaisons) | L'effet réel d'un mélange |
| Synergie Bliss/Loewe/HSA/ZIP | Un effet in vivo ou clinique |
| Docking Vina | Une activité biologique, une efficacité |
| Connecteurs (PubChem…) | Une validation expérimentale |
| Synthèse IA | Quoi que ce soit (reformulation) |
