# Import / Export de données scientifiques — WebIntel

Ce document décrit les formats d'**import** (Phase 9). L'export est documenté
par l'agent en charge de l'export (ne pas confondre).

Endpoint : `POST /api/science/import` (multipart : `fichier`, `type_donnees`,
`projet_id` optionnel). Authentification : même pattern que le reste de
l'API scientifique (`api_key_auth` — clé API requise en mode serveur,
ouvert en mode local).

## Principes

- **Idempotence stricte** : réimporter le même fichier ne crée aucun doublon.
  Le rapport distingue `crees` (nouvel objet), `mis_a_jour` (objet existant
  dont au moins un champ a changé) et `ignores` (objet existant inchangé).
- **Validation ligne par ligne** : une ligne invalide est rejetée avec
  `{"ligne_numero", "champ", "message"}` dans `erreurs[]`. Les lignes valides
  sont importées — jamais d'import partiel silencieux, jamais de rollback
  total à cause d'une ligne.
- **Provenance traçable** : chaque item créé ou mis à jour est enregistré via
  `enregistrer_provenance(..., connecteur_nom="fichier:<nom_fichier>")`.
- **Fichier > 10 Mo → 413.** Format inconnu → 400 explicite.
- **Niveaux de données** : les imports CSV/JSON/Excel sont au niveau
  « publie » (donnée rapportée telle quelle) ; les imports SDF sont au niveau
  « normalise » (extraction minimale, voir ci-dessous).

## Types de données

`type_donnees` vaut `plante`, `compose`, `lien` ou `evidence`.

### Plante (`plante`)

| Colonne | Requis | Format |
|---|---|---|
| `nom_scientifique` | **oui** | ex. `Moringa oleifera` |
| `famille` | non | ex. `Moringaceae` |
| `genre` | non | ex. `Moringa` |
| `espece` | non | ex. `oleifera` |
| `noms_vernaculaires` | non | `fr=Baobab;en=Baobab` ou JSON `{"fr": "Baobab"}` |
| `synonymes` | non | `nom1;nom2` (séparés par `;`) |
| `description` | non | texte libre |
| `distribution` | non | texte libre |
| `parties_utilisees` | non | `feuilles;graines` (séparés par `;`) |
| `ids_externes` | non | JSON `{"gbif": "3190532"}` |

Idempotence : clé = `nom_scientifique` (unique).

### Composé (`compose`)

| Colonne | Requis | Format |
|---|---|---|
| `nom` | **oui** | ex. `Quercétine` |
| `formule` | non | ex. `C15H10O7` |
| `poids_moleculaire` | non | nombre (ex. `302.24`) |
| `smiles` | non | SMILES |
| `inchi` | non | InChI |
| `inchikey` | non | InChIKey (unique : conflit avec un autre composé → ligne rejetée) |
| `classe_chimique` | non | ex. `Flavonoïde` |
| `synonymes` | non | `nom1;nom2` |
| `ids_externes` | non | JSON |

Idempotence : clé = `nom` (unique). L'`inchikey` n'est jamais modifiée par
un réimport (identifiant chimique unique).

### Lien plante-composé (`lien`)

| Colonne | Requis | Format |
|---|---|---|
| `plante` | **oui** | nom scientifique **exact** d'une plante existante |
| `compose` | **oui** | nom **exact** d'un composé existant |
| `partie_plante` | non | ex. `feuilles` |
| `type_association` | non | ex. `présence` |
| `concentration` | non | nombre |
| `unite_concentration` | non | ex. `mg/g` |
| `methode_extraction` | non | texte libre |
| `publication` | non | citation |
| `doi` | non | DOI |
| `annee` | non | entier |
| `confiance` | non | `publie`, `rapporte` (défaut) ou `incertain` |

Idempotence : clé = (plante, composé, partie_plante, publication). Si la
plante ou le composé n'existe pas (nom inexact), la ligne est rejetée avec
un message explicite — importez d'abord `plantes.csv` puis `composes.csv`.

### Preuve (`evidence`)

| Colonne | Requis | Format |
|---|---|---|
| `type_source` | non | `publication` (défaut), `base_de_donnees`, `site_expert`, `these`, `autre` |
| `reference` | non* | citation complète |
| `titre` | non* | titre |
| `doi` | non | DOI (unique : réimport du même DOI → pas de doublon) |
| `pmid` | non | PMID |
| `url` | non | URL |
| `auteurs` | non | `Nom A.;Nom B.` (séparés par `;`) |
| `annee` | non | entier |
| `extrait` | non | texte libre |

\* au moins `reference` **ou** `titre` requis.

Idempotence : avec DOI → clé = DOI ; sans DOI → déduplication sur
(référence, titre, année) pour qu'un réimport ne crée pas de doublon.

## Formats de fichier

### CSV (`.csv`)

- Encodage UTF-8 (BOM accepté), première ligne = en-têtes (noms de colonnes
  ci-dessus).
- Délimiteur `,` ou `;` détecté automatiquement (un CSV français au
  point-virgule est détecté si l'en-tête contient `;` et pas de `,`).
- Un champ contenant le délimiteur doit être quoté : `"feuilles;graines"`.
- Les lignes entièrement vides sont ignorées (ni erreur, ni import).

### JSON (`.json`)

Liste d'objets avec les mêmes noms de champs :

```json
[
  {"nom_scientifique": "Moringa oleifera", "famille": "Moringaceae",
   "parties_utilisees": "feuilles;graines"},
  {"nom_scientifique": "Artemisia annua", "famille": "Asteraceae"}
]
```

`ligne_numero` = position dans la liste (à partir de 1).

### Excel (`.xlsx`)

Première feuille, ligne 1 = en-têtes (mêmes noms). Les lignes vides sont
ignorées. `ligne_numero` = numéro de ligne Excel.

### SDF (`.sdf`) — parseur MINIMAL, limites honnêtes

Il n'y a **pas de RDKit** dans l'environnement : le parseur est un pur
Python volontairement minimal.

Ce qu'il fait :
- découpe les molécules sur le séparateur `$$$$` ;
- extrait les champs texte `> <Nom>`, `> <Formule>`, `> <SMILES>`,
  `> <InChI>`, `> <InChIKey>`, `> <Classe>` (le nom peut aussi venir de la
  1re ligne d'en-tête du molfile) ;
- crée un `Compound` par molécule, au niveau de données **« normalise »**,
  avec la note de limitation stockée dans `external_ids.note_import_sdf`.

Ce qu'il NE fait PAS (documenté, pas de faux semblant) :
- **aucune interprétation de la table d'atomes/liaisons** ;
- aucune validation chimique, aucune stéréochimie ;
- aucun calcul de formule ou de masse.

Un fichier sans `$$$$`, sans molécule nommée, ou dont un bloc n'a ni table
molfile plausible ni propriété `> <...>` est rejeté avec une erreur
explicite (`SDFInvalide`) — **jamais de faux composé**.

Le SDF n'est accepté que pour `type_donnees="compose"` (sinon 400).

## Rapport d'import (réponse 200)

```json
{
  "rapport": {
    "type_donnees": "plante",
    "fichier": "plantes.csv",
    "niveau_donnees": "publie",
    "crees": 3,
    "mis_a_jour": 0,
    "ignores": 0,
    "erreurs": [
      {"ligne_numero": 5, "champ": "nom_scientifique",
       "message": "Champ requis manquant : « nom_scientifique »."}
    ],
    "avertissements": []
  },
  "provenance": {
    "source": "plantes.csv",
    "date_import": "2026-09-22T19:30:00+00:00",
    "connecteur": "fichier:plantes.csv"
  }
}
```

Pour un import SDF, `niveau_donnees` vaut `"normalise"` et un `avertissement`
rappelle les limites du parseur minimal.

## Erreurs HTTP

| Code | Cas |
|---|---|
| 400 | `type_donnees` inconnu, extension non supportée, fichier vide, JSON/CSV/Excel illisible, SDF invalide, SDF avec un autre type que `compose` |
| 413 | fichier > 10 Mo |
| 401 | mode serveur sans clé API (comme le reste de l'API) |

## Fichiers d'exemple

Voir `docs/exemples_import/` :

- `plantes.csv`, `composes.csv`, `liens.csv` — CSV au point-virgule ;
- `evidences.json` — liste d'objets preuve ;
- `exemple.xlsx` — classeur à 4 feuilles (`plantes`, `composes`, `liens`, `evidences`) ;
- `exemple.sdf` — 2 molécules (quercétine, artémisinine) avec champs texte.

Ordre d'import conseillé pour les liens : `plantes.csv` → `composes.csv` →
`liens.csv` (les liens exigent des plantes/composés existants).
