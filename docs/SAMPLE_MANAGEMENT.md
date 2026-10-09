# Gestion des échantillons — WebIntel v2.3.0

> **Ce qui est VRAI** : chaîne de traçabilité complète
> PLANTE → ÉCHANTILLON → VOUCHER → EXTRACTION → EXTRAIT → FRACTION →
> COMPOSÉ → ANALYSE, validation GPS WGS84, fichiers bruts d'analyse confinés
> avec SHA-256, cartographie avec masquage selon confidentialité.

## 1. La chaîne de traçabilité

Chaque maillon est un enregistrement lié au précédent (clés étrangères
indexées à 100 %) :

```
PLANTE → ÉCHANTILLON → VOUCHER → EXTRACTION → EXTRAIT → FRACTION → COMPOSÉ → ANALYSE
```

- **Échantillon** : collecte terrain (date, lieu, collecteur, GPS,
  niveau de confidentialité).
- **Voucher** : spécimen d'herbier de référence (unicité anti-doublon
  testée sous concurrence).
- **Extraction → Extrait → Fraction** : protocole, solvant, méthode,
  rendement — chaque étape enregistre **qui, quand, comment**.
- **Composé** : molécule identifiée (normalisation RDKit : SMILES
  canonique, InChI, InChIKey).
- **Analyse** : mesure instrumentale rattachée à ses **fichiers bruts**.

Lecture de la chaîne complète :
`GET /api/science/terrain/echantillons/{id}/chaine` — requête optimisée
(22 → 5 requêtes SQL, chantier v2.3.0).

## 2. GPS : validation WGS84

**Ce qui est VRAI** (`webintel/science/terrain.py`) : toute coordonnée est
validée en **degrés décimaux WGS84** (latitude −90…+90, longitude
−180…+180). Des coordonnées hors plage lèvent `ValueError` — **jamais
corrigées silencieusement** (même philosophie que la normalisation
moléculaire : l'invalide est signalé, pas deviné).

## 3. Fichiers bruts d'analyse : confinement + intégrité

**Ce qui est VRAI** :
- Les fichiers bruts (chromatogrammes, spectres…) sont stockés **uniquement**
  dans le répertoire confiné `WEBINTEL_FICHIERS_BRUTS_DIR`
  (défaut : `./webintel_data/fichiers-bruts`).
- **Confinement réel** : résolution canonique du chemin (liens symboliques
  résolus) — toute tentative de sortie du répertoire est refusée
  (`webintel/science/terrain.py`).
- **Taille maximale** : `WEBINTEL_FICHIER_BRUT_MAX_OCTETS` (défaut :
  **200 Mo**).
- **Intégrité** : SHA-256 calculé à l'import ; vérifiable via
  `GET /api/science/terrain/analyses/{id}/integrite`.
- Le contenu est inspecté à l'import (un ZIP déguisé en `.csv` est
  détecté — chantier robustesse).

## 4. Cartographie : masquage selon confidentialité

**Ce qui est VRAI** (`webintel/offline/cartographie.py`) : l'affichage d'un
point GPS dépend du `niveau_confidentialite` de l'enregistrement et du
lecteur :

| Niveau | Affichage aux non-auteurs |
|---|---|
| `confidentiel`, `chercheur` | **Masqué** — jamais exposé (coordonnées `null`) |
| `restreint`, `projet` (ou inconnu, par prudence) | **Flouté** — grille de 0,5° (~55 km) |
| `public` | Coordonnées exactes |

Statut retourné par l'API : `"exact"` / `"floute"` / `"masque"` /
`"absent"`, avec drapeau `masque: true` si le GPS précis est protégé.

## 5. Listes paginées

Échantillons, vouchers, analyses, pics : `limit` (1…500, défaut 50) /
`offset`. Ex. : `GET /api/science/terrain/vouchers?limit=50&offset=0`.

## Limites

- WebIntel ne remplace pas un **LIMS** : pas de gestion de stock physique,
  pas de code-barres, pas de traçabilité d'instrument au-delà des fichiers
  bruts importés.
- Le floutage à 0,5° protège la localisation précise, pas l'existence même
  d'une collecte dans une zone — pour les savoirs les plus sensibles,
  utilisez le niveau `confidentiel` (masquage total).
- Les fichiers bruts ne sont **pas chiffrés au repos** en mode local :
  protégez le disque si les données sont sensibles.
