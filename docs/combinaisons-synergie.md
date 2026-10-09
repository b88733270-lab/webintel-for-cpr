# Combinaisons, synergie, docking et rapports (v2.1)

Ce document décrit les fonctionnalités scientifiques v2.1 : études de
combinaison (MODE A), analyses de synergie (MODE B), workflow docking et
rapports de recherche reproductibles.

## Discipline scientifique (rappel)

- **Séparation des niveaux** : `publie`, `normalise`, `hypothese`,
  `prediction`, `experimental` — jamais mélangés, jamais confondus.
- **Formulations autorisées** : « association observée dans la littérature »,
  « résultat informatique », « prédiction », « hypothèse », « résultat
  expérimental fourni par le laboratoire ».
- **Formulations interdites** (rejetées automatiquement par
  `verifier_honnetete()`, testé en CI) : « efficace », « sûr », « innocuité »,
  « prouvé », « validé scientifiquement » appliqués aux résultats.
- **Jamais de score inventé** : sans moteur disponible, sans mesures
  expérimentales ou sans base connectée, le système produit un message
  explicite, pas une valeur.

## MODE A — Études de combinaison (screening structural)

`POST /api/science/combinaisons` — crée une étude (≥ 2 composés distincts).
Le dossier généré comporte **12 points** :

1. Identité des composés · 2. Structures · 3. Similarité (Tanimoto si le
   module chimie est disponible, sinon section explicite) · 4. Propriétés
   (descripteurs si disponibles) · 5. Cibles documentées (table
   `compound_targets`, avec sources) · 6. Cibles communes · 7. Littérature
   (preuves citées) · 8. Docking (aperçu ou jobs existants) · 9. Comparaison
   (score informatique) · 10. Limites · 11. Hypothèses · 12. Conclusion
   informatique.

**Score de comparaison informatique** (documenté, versionné
`SCORE_VERSION`) :

```
score = 0,6 × moyenne(profils normalisés) + 0,4 × diversité structurale
```

Si les similarités sont indisponibles, seul le profil disponible est
utilisé (limite indiquée) ; si aucun descripteur n'existe, le score vaut
`None` avec la note « aucune valeur inventée ».

**Reproductibilité** : entrées normalisées (ids triés, paramètres
canoniques) → hash SHA-256 ; mêmes entrées → mêmes sorties (déterministe).

**Endpoints** :

| Méthode | Chemin | Description |
|---|---|---|
| POST | `/api/science/combinaisons` | Créer une étude (201) |
| GET | `/api/science/combinaisons` | Lister (projet cloisonné) |
| GET | `/api/science/combinaisons/{id}` | Dossier complet |
| GET | `/api/science/combinaisons/{id}/rapport?format=json\|html\|markdown` | Rapport |

**UI** : `/combinaisons` (liste + 4 actions), `/combinaisons/nouvelle`
(formulaire), `/combinaisons/{id}` (les 12 points).

## MODE B — Analyses de synergie (données expérimentales)

`POST /api/science/synergie/analyser` — **exige des mesures réelles**.
Sans mesures (structures seules, noms seuls) → `422` « données
insuffisantes pour la méthode … ». Les analyses sont **privées par
défaut** (`is_private=True`) et exclues des exports publics.

| Méthode | Formule | Données requises |
|---|---|---|
| Bliss | `E_bliss = E_A + E_B − E_A·E_B` ; `Δ = E_obs − E_bliss` | points `{dose_a, dose_b, reponse_a, reponse_b, reponse_ab}` |
| HSA | `E_hsa = max(E_A, E_B)` ; `Δ = E_obs − E_hsa` | idem Bliss |
| Loewe | `CI = d_A/D_A(y) + d_B/D_B(y)` (courbes de Hill ajustées, déterministe) | courbes dose-réponse (≥ 4 points, ajustement auto) ou `{ic50, pente}` + `melanges` |
| ZIP | `E_zip = f_A + f_B − f_A·f_B` (Yadav et al. 2015, éq. 16) | idem Loewe |

Interprétation : seuils documentés (|Δ| > 0,1 ; CI < 0,9 / > 1,1) avec la
mention « selon la méthode » — ce sont des descriptions d'écarts, pas des
preuves. Graphes en SVG pur (aucune dépendance graphique).

**Référence** : Yadav B. et al., « Searching for drug synergy in complex
dose–response landscapes using an interaction potency model », *Comput.
Struct. Biotechnol. J.* 13:504–513 (2015).

**Endpoints** : `GET /api/science/synergie/methodes`,
`POST /api/science/synergie/analyser`, `GET /api/science/synergie`,
`GET /api/science/synergie/{id}`.

**UI** : `/synergie/importer` (« Importer mes résultats »),
`/synergie/{id}` (formule, graphe, limites).

## Docking moléculaire (workflow en 9 étapes)

`POST /api/science/docking/apercu` — valide les entrées et affiche les
9 étapes **sans calcul** (`scores = None`).
`POST /api/science/docking/executer` — exécution réelle via
`docking_adapter` (AutoDock Vina) ; si le moteur est indisponible ou si
`run()` échoue, un job `statut="refuse"` est persisté pour l'audit et une
`422` explicite est retournée — **jamais de faux score**.

Étapes : récepteur → validation → ligand → préparation → paramètres →
calcul → poses → scores → rapport. Chaque job enregistre : hash du
récepteur, hash global des entrées, moteur/version, versions des
bibliothèques, méthode, provenance.

**Endpoints** : `GET /api/science/docking/statut`,
`POST /api/science/docking/apercu`, `POST /api/science/docking/executer`,
`GET /api/science/docking`, `GET /api/science/docking/{id}`.

**UI** : `/docking` (aperçu / exécution), `/docking/{id}`.

## Rapports de recherche reproductibles

`GET /api/science/rapport?format=json|markdown|html|pdf` et
`GET /api/science/rapport/zip`.

Sections : plante, composés, publications, données, hypothèses,
simulations, combinaisons, résultats, limites, références. Le ZIP
contient `rapport.md`, `rapport.html`, `donnees/entrees.json` (entrées
normalisées, hash SHA-256, paramètres, versions, formules) et
`MANIFEST.json`. Données privées **exclues par défaut**
(`inclure_prive=true` pour les inclure explicitement).

**UI** : `/rapport` (« Exporter mon dossier »),
`/rapport/telecharger?format=…`.

## Limites connues (v2.1)

- Le module `science/chimie.py` n'est pas livré dans ce périmètre : les
  sections similarité/propriétés affichent un message explicite et le
  score de comparaison peut être partiel — aucune valeur n'est inventée.
- Le binaire AutoDock Vina n'est pas installé dans l'environnement de
  test : le workflow docking est vérifié en mode aperçu + refus propre.
- La normalisation des réponses (fraction vs pourcentage) se fait par
  série de points ; une série hétérogène doit être harmonisée en amont.
- PostgreSQL : support codé mais non testé sur serveur réel (comme v2.0).
