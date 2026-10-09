# Les 5 niveaux de données scientifiques

> **Statut de ce document** : discipline produit Phase 1 (documentation).
> Ces niveaux définissent comment WebIntel étiquette et affiche toute
> information scientifique. Ils s'appliquent à la vision (fiches plantes,
> observations de laboratoire) comme aux données déjà collectées par le
> crawl et l'extraction web, qui alimentent ces niveaux en tant que
> **source parmi d'autres**.

## Principe

Toute donnée manipulée par WebIntel porte **un et un seul** niveau de
preuve. Le niveau est une propriété de la donnée, affichée partout avec
elle (badge, source, date). Deux niveaux ne se mélangent jamais dans un
même chiffre, un même tableau ou un même résumé sans distinction visible.

## Les 5 niveaux

### 1. PUBLIE — fait publié, sourcé

**Définition stricte** : information issue d'une source publiée et
vérifiable — article scientifique, ouvrage de référence, base de données
publique, monographie officielle. La source est citée précisément
(référence complète, URL ou DOI quand disponible).

**Exemples** : « les feuilles de *Moringa oleifera* contiennent des
flavonoïdes (réf. : …) » ; composition publiée d'une huile essentielle.

- **Badge UI proposé** : pastille **verte** — libellé « Publié ».
- **Règles d'affichage** : la référence de la source est toujours visible
  à côté de la donnée ; la date de publication/consultation est affichée.

### 2. NORMALISE — donnée nettoyée à partir de sources

**Définition stricte** : donnée dérivée de sources existantes par un
traitement de nettoyage et d'harmonisation — unification des noms
(nom latin vs noms vernaculaires), conversion d'unités, fusion de
doublons, correction de format. Le traitement appliqué est documenté et
les sources d'origine restent liées.

**Exemples** : prix harmonisés multi-sources d'un même produit ;
synonymes botaniques regroupés sous un nom accepté.

- **Badge UI proposé** : pastille **bleue** — libellé « Normalisé ».
- **Règles d'affichage** : mention du traitement (« unités converties en
  mg/g », « doublons fusionnés ») et lien vers les sources d'origine ;
  la donnée normalisée ne remplace jamais visuellement la donnée source.

### 3. HYPOTHESE — interprétation non vérifiée

**Définition stricte** : interprétation, extrapolation ou supposition
formulée à partir de données existantes, **qui n'a pas été vérifiée**.
Elle peut être féconde, mais elle n'est pas un fait.

**Exemples** : « ce composé pourrait expliquer l'activité observée » ;
rapprochement suggéré entre deux usages traditionnels.

- **Badge UI proposé** : pastille **ambre/orange** — libellé « Hypothèse ».
- **Règles d'affichage** : porte toujours la mention **« non vérifiée »** ;
  ne peut jamais apparaître dans un résumé de « faits établis » ; son
  auteur et sa date de formulation sont affichés.

### 4. PREDICTION — résultat informatique / simulation

**Définition stricte** : résultat produit par un calcul — simulation,
modèle statistique, docking moléculaire, prédiction de propriétés,
résumé ou extraction automatique. C'est une sortie de machine, pas une
mesure du réel.

**Exemples** : score de docking d'un composé ; propriété physico-chimique
prédite ; entité extraite automatiquement d'une page web par le pipeline
d'extraction.

- **Badge UI proposé** : pastille **violette** — libellé « Prédiction ».
- **Règles d'affichage** : porte **toujours** la mention
  **« à confirmer expérimentalement »** ; le modèle/méthode et ses
  limites connues sont indiqués ; jamais présentée comme un fait,
  jamais comptée comme une preuve.

### 5. EXPERIMENTAL — observation de laboratoire du chercheur

**Définition stricte** : observation produite par le chercheur lui-même
ou son équipe — mesure, essai, analyse de laboratoire, note de terrain.
Elle est datée, signée (auteur) et décrit son protocole ou ses
conditions quand c'est pertinent. C'est le niveau le plus fort, mais il
reste une observation singulière : il ne « valide » pas à lui seul une
théorie.

**Exemples** : résultat d'un dosage réalisé au laboratoire le 12/03/2026 ;
rendement d'extraction mesuré sur un lot donné.

- **Badge UI proposé** : pastille **rouge/terracotta** — libellé
  « Expérimental — mes données ».
- **Règles d'affichage** : auteur, date et conditions/protocole affichés ;
  visuellement séparée des données externes (section dédiée « mes
  données »).

## Règle d'or : aucune promotion sans nouvelle source

**Un niveau de preuve ne peut être relevé que par l'arrivée d'une
nouvelle source, jamais par un changement de statut interne.**

Concrètement :

- Une PREDICTION dont la « validation interne » (score, recoupement
  automatique, contrôle qualité du pipeline) est positive **reste une
  PREDICTION**. Seule une nouvelle observation EXPERIMENTAL, apportée
  comme nouvelle source, peut l'étayer — et la prédiction d'origine ne
  change pas de niveau pour autant : c'est la nouvelle donnée qui porte
  le niveau EXPERIMENTAL.
- Une HYPOTHESE confirmée par une expérience ne devient pas un fait
  publié : elle s'accompagne désormais d'une donnée EXPERIMENTAL qui la
  soutient, chacune gardant son niveau.
- Une donnée NORMALISE ne devient jamais PUBLIE : la publication est un
  acte éditorial externe, pas un traitement de données.
- Aucun score, aucun algorithme, aucun « statut validé » interne ne fait
  passer une donnée d'un niveau à un autre.

**Corollaire d'honnêteté** : le simple fait qu'une donnée soit stockée
dans WebIntel ne lui confère aucune validité scientifique. Le logiciel
n'écrit jamais « validé scientifiquement » à propos d'une donnée ; il
affiche son niveau, sa source et sa date — au lecteur de juger.

## Modèle enrichi v2.1

### Champs botaniques et pharmacognosie détaillés (`plants`)
`feuilles`, `fleurs`, `fruits`, `graines`, `racines`, `ecorce`, `habitat`,
`references_botaniques` (liste), `matiere_premiere`, `preparation`.
Migration append-only et idempotente (`_ensure_v21_science_columns` dans
`webintel/database/base.py`) : ajout de colonnes si absentes, jamais de
suppression, `init_db()` répétable sans erreur.

### Lien plante–composé documenté (`plant_compounds`)
`analysis_method` (ex. HPLC-UV), `rendement` + `rendement_unite`,
`evidence_id` (FK vers `evidences.id`) : chaque lien peut pointer sa
preuve documentaire, affichée dans la fiche (« Preuve : DOI … »).

### Nouveaux modèles
- `CompoundTarget` — cible biologique documentée d'un composé
  (`target_name`, `interaction_type`, `source`, `doi`, `evidence_id`) ;
- `PlantActivity` — activité pharmacologique rapportée d'une plante
  (`activity`, `study_type`, `study_model`, `published_result`,
  `reference`, `doi`, `evidence_id`) ;
- `Study` — étude/publication structurée (`doi`, `title`, `authors[]`,
  `year`, `journal`, `pmid`), idempotente sur le DOI ;
- `Provenance` — traçabilité (`entity_type`, `entity_id`, `source_name`,
  `connector`, `data_version`), idempotente ;
- `Dataset` — jeu de données du chercheur, **privé par défaut**
  (`is_private=True`), cloisonné au projet.

### Fiche plante détaillée
Sections : identité botanique, botanique détaillée (morphologie,
habitat, références), pharmacognosie (matière première, préparation,
extractions), phytochimie (méthode d'analyse, rendement, preuve
pointée), pharmacologie (études + activités rapportées + cibles
documentées), sécurité (bandeau « pas un avis médical » **toujours
visible**), littérature structurée, bibliographie, laboratoire privé
(séparé, avec avertissement), simulations (badge « Prédiction — à
confirmer expérimentalement »). Champs absents : « non renseignée ».

### Saisie manuelle sourcée (UI)
Les formulaires de la fiche (`/plantes/{id}/botanique`,
`/composes/ajouter`, `/activites/ajouter`, `/cibles/ajouter`,
`/etudes/ajouter`) **exigent une source** (publication ou DOI) : sans
source, rejet avec message explicite. Un DOI crée/pointe une `Evidence` ;
chaque ajout enregistre une `Provenance` (`connector: ui-manuel`).

### Parcours chercheur (UX)
Tableau de bord (`/`) : la recherche de plante est placée **en premier**,
suivie des actions visibles (nouvelle recherche / étude / simulation,
import, export). La fiche affiche la légende des 5 niveaux même sans
donnée, et un rappel permanent : données publiques rapportées vs données
privées du laboratoire, jamais mélangées.
