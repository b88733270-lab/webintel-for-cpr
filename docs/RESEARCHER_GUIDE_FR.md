# Guide du chercheur — WebIntel v2.3.0

> **Ce qui est VRAI** : ce guide décrit le parcours réel d'un chercheur
> **non-développeur** dans l'application : accueil → plante → étude →
> résultats → rapport. Chaque écran existe ; chaque formulation prudente
> citée est réellement affichée. Aucune étape ne demande Python, JSON ou le
> terminal.

## 1. L'accueil : « Que souhaitez-vous étudier ? »

La page d'accueil (`/accueil`) propose **5 grandes cartes** :

1. **Étudier une plante** — recherche par nom scientifique ou vernaculaire.
2. **Explorer une molécule** — par nom, formule ou structure (SMILES).
3. **Consulter une publication** — littérature enregistrée.
4. **Nouvelle étude** — lance l'assistant en 6 étapes (voir §3).
5. **Mes recherches** — historique de vos études, versionné.

En dessous : les actions visibles « Nouvelle recherche », « Nouvelle étude »,
« Importer des résultats », « Exporter un dossier ».

## 2. La fiche plante : 7 sections structurées

La recherche d'une plante ouvre sa **fiche en 7 sections** (identité
botanique, noms vernaculaires, phytochimie, pharmacologie rapportée,
sécurité, usages traditionnels, données de laboratoire). Règles affichées :

- Chaque information porte un **badge de niveau de preuve** :
  « Publié », « Normalisé », « Hypothèse », « Prédiction », « Expérimental ».
- Sous chaque métrique, un lien **« Qu'est-ce que cela signifie ? »**
  explique en langage simple (ex. : « Tanimoto = 0,53 : les deux molécules
  partagent environ la moitié de leurs fragments chimiques — cela ne dit
  rien de leurs effets »).
- La section usages traditionnels affiche **toujours** le bandeau
  **« USAGE TRADITIONNEL RAPPORTÉ »** — jamais « efficacité démontrée ».
- Les noms vernaculaires affichent leur **langue d'origine** (Mooré, Dioula,
  Fulfuldé, Gulmancéma, Bissa…) ; si la langue est inconnue, c'est écrit
  noir sur blanc — **jamais devinée**.

## 3. L'assistant d'étude : 6 étapes, 2 modes

« Nouvelle étude » ouvre le **wizard en 6 étapes** avec barre de progression.
Aucun JSON n'est jamais demandé.

1. **Plante / sujet** — choisissez la plante (ou créez sa fiche).
2. **Échantillons** — rattachez des échantillons de terrain (avec GPS,
   vouchers — voir [`SAMPLE_MANAGEMENT.md`](SAMPLE_MANAGEMENT.md)).
3. **Type d'analyse** — ici se choisit le **mode simple** (valeurs par défaut
   sûres, explications en français) ou le **mode avancé** (paramètres
   réglables, **mêmes moteurs scientifiques**).
4. **Molécules / paramètres** — sélection des composés, réglages.
5. **Lancement** — l'étude s'exécute (jobs suivis : en attente, en cours,
   terminé, échoué, annulé).
6. **Résultats** — dossier structuré avec provenance complète.

**Votre progression est sauvegardée automatiquement** : vous pouvez fermer
le navigateur, éteindre l'ordinateur, redémarrer le serveur — le brouillon
est conservé **30 jours** (`wizard_sessions`). Une étude terminée reste
accessible même après expiration.

## 4. Les résultats : lire sans se tromper

Chaque résultat affiche sa **provenance** : QUI l'a produit, QUOI (données
d'entrée), QUAND, OÙ (projet), SOURCE, COMMENT (méthode), OUTIL + VERSION,
PARAMÈTRES. Bouton **« Voir la provenance »** → `/resultats/{type}/{id}/provenance`.

**Formulations prudentes — ce que vous lirez (et ne lirez jamais)** :

| Vous lirez | Vous ne lirez jamais |
|---|---|
| « Résultat de screening informatique » | « Ce mélange est efficace » |
| « Hypothèse à vérifier expérimentalement » | « Ce composé traite [maladie] » |
| « Association observée dans la littérature » | « Ce mélange est sûr » |
| « Prédiction — à confirmer au laboratoire » | Un score de docking sans Vina installé |

Ces interdictions sont **testées automatiquement** : aucun contenu généré
ne peut contenir « efficace », « sûr » ou « traite » sans source
expérimentale explicite.

Points de vigilance :
- **Similarité de Tanimoto** : mesure une ressemblance de *structure
  chimique*, pas une ressemblance d'effets. Deux molécules à 0,9 peuvent
  avoir des activités totalement différentes.
- **Docking** : un bon score d'affinité (ex. −8,5 kcal/mol) est une
  *prédiction d'interaction*, pas une preuve d'efficacité. Sans le binaire
  Vina installé, **aucun score n'est affiché** (refus propre, pas de zéro
  déguisé).
- **Synergie (Bliss/Loewe/HSA/ZIP)** : calculée **uniquement** sur des
  données expérimentales que *vous* avez importées, et seulement si elles
  sont suffisantes — sinon la méthode refuse avec l'explication.

## 5. « Mes recherches » : l'historique versionné

Chaque étude est **versionnée** (append-only) : une nouvelle exécution crée
une **nouvelle** étude — jamais d'écrasement silencieux. Vous pouvez :

- comparer deux versions (entrées, paramètres, sources, logiciels,
  résultats — avec empreinte SHA-256 du dossier) ;
- restaurer une version antérieure (crée une nouvelle version, l'historique
  est préservé).

## 6. Le rapport : « Créer le rapport »

Le bouton **« Créer le rapport »** produit un rapport scientifique structuré
(fiche plantes, composés, publications, données, hypothèses, simulations,
combinaisons, résultats, **limites**, références), exportable en
**HTML, Markdown, PDF** et **ZIP reproductible** (données + rapport +
provenance). Chaque section porte ses badges de niveau de preuve ; la
section « Limites » est toujours présente.

## 7. Importer vos données de laboratoire

« Importer des résultats » accepte **CSV, JSON, Excel (.xlsx)** (et .sdf
pour les structures). Les fichiers sont lus par paquets de 500 lignes
(pas de saturation mémoire). Vos données privées restent **dans votre
projet** : elles ne sont jamais envoyées à un service externe sans votre
action explicite, et la synthèse IA est désactivable
(`WEBINTEL_AI_ENABLED=false` ou page Paramètres).

## Limites (à connaître avant de commencer)

- WebIntel **prépare** l'expérimentation réelle, il ne la remplace pas.
  Aucune décision thérapeutique ne doit reposer sur ses prédictions.
- Les données ethnobotaniques sont des **savoirs rapportés** : leur
  confidentialité (5 niveaux) protège les informateurs — voir
  [`ETHNOBOTANY.md`](ETHNOBOTANY.md).
- Sans internet : le mode local et la file de synchronisation prennent le
  relais — voir [`OFFLINE_MODE.md`](OFFLINE_MODE.md).
- En cas de problème : [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).
