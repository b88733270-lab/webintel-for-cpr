# Gouvernance des données — WebIntel v2.3.0

> **Ce qui est VRAI** : 5 niveaux de preuve strictement séparés, provenance
> complète QUI/QUOI/QUAND/OÙ/SOURCE/COMMENT/OUTIL/VERSION/PARAMÈTRES sur
> chaque résultat, contrôles qualité automatiques, et 4 règles d'intégrité
> non négociables (jamais de fusion auto, jamais de suppression silencieuse…).

## 1. Les 5 niveaux de preuve (DataLevel)

`webintel/science/models.py` — `DataLevel` (str, Enum) :

| Code stocké | Label FR | Signification |
|---|---|---|
| `publie` | Publié | Donnée issue d'une source publiée (publication, base publique) |
| `normalise` | Normalisé | Donnée publiée, normalisée par WebIntel (ex. SMILES canonique) |
| `hypothese` | Hypothèse | Proposition du chercheur ou de l'IA — à vérifier |
| `prediction` | Prédiction | Sortie d'un calcul / d'une simulation |
| `experimental` | Expérimental | Observation du laboratoire (donnée privée, par projet) |

**Règles** :
- Les niveaux ne sont **jamais mélangés** : un export sépare les 5 niveaux
  (`webintel/science/export_dossier.py`).
- Référentiel exposé par l'API : `GET /api/science/data-levels` (code,
  label, description, couleur du badge).
- La fiche plante n'inclut les sections `experimental` et `prediction`
  **que si un `project_id` est fourni** — sans projet, aucune donnée de
  laboratoire ne fuite.
- Suite comportementale testée : séparation des 5 niveaux, IA jamais
  persistée comme fait, données privées exclues par défaut.

## 2. Provenance : QUI / QUOI / QUAND / OÙ / SOURCE / COMMENT / OUTIL / VERSION / PARAMÈTRES

**Ce qui est VRAI** : chaque résultat (simulation, docking, combinaison,
synergie, analyse) enregistre sa provenance complète :

- **QUI** : utilisateur / clé API à l'origine ;
- **QUOI** : données d'entrée (+ leur hash SHA-256) ;
- **QUAND** : horodatage ; **OÙ** : projet (isolation stricte) ;
- **SOURCE** : publication, connecteur (+ version, date d'import), enquête ;
- **COMMENT** : méthode ; **OUTIL + VERSION** : ex. RDKit 2026.3.6,
  Vina 1.2.7 ; **PARAMÈTRES** : seed, rayon d'empreinte, boîte de docking…

Consultation : `GET /resultats/{type}/{id}/provenance` (UI) et
`GET /api/science/provenance` (API). Si la provenance est absente, l'API
répond un **404 honnête** — pas une provenance inventée.

Les simulations sont en outre **immuables** : champs d'audit (qui, quand,
hash SHA-256, entrées, paramètres, sorties, moteur/version) **non
modifiables ni supprimables** ; rejeu append-only.

## 3. Qualité des données

**Ce qui est VRAI** (`webintel/science/qualite.py`,
`GET /api/science/qualite` — lecture seule) : contrôles automatiques
(ex. : SMILES invalides signalés, doublons potentiels, champs manquants).
Page UI : `/qualite`.

## 4. Les 4 règles d'intégrité (non négociables)

1. **Jamais de fusion automatique** : en cas d'ambiguïté (deux taxons, deux
   molécules, deux versions en conflit), WebIntel **signale** et laisse le
   chercheur trancher. Testé (unicité anti-doublon sous concurrence :
   8 threads → 1 ligne).
2. **Jamais de suppression silencieuse** : les études sont versionnées
   append-only (`study_versions`) ; une nouvelle exécution crée une
   nouvelle étude. Les enregistrements sensibles utilisent le soft-delete
   (date de suppression logique conservée).
3. **Jamais de correction silencieuse** : SMILES invalide, GPS hors plage,
   langue inconnue — **signalé, jamais deviné**.
4. **Jamais de donnée inventée** : sans RDKit, pas de descripteurs ; sans
   Vina, pas de score ; sans données suffisantes, pas de synergie ; sans
   provenance, 404. Les formulations « efficace » / « sûr » / « traite »
   sont interdites par test.

## 5. Données privées vs données publiques

- Les observations de laboratoire et les simulations sont **privées par
  projet** (isolation testée : accès croisé → 403).
- Elles ne sont **jamais envoyées à un service externe** sans action
  explicite du chercheur (règle produit).
- La synthèse IA les **exclut par défaut** et est désactivable
  (`WEBINTEL_AI_ENABLED=false`).

## Limites

- La gouvernance garantit la **traçabilité**, pas la **véracité** des
  sources externes : une donnée `publie` peut être erronée à la source —
  la provenance permet de remonter jusqu'à elle, c'est son rôle.
- Les contrôles qualité sont des **filets**, pas une relecture experte :
  la validation scientifique finale appartient au chercheur.
