# Ethnobotanique — WebIntel v2.3.0

> **Ce qui est VRAI** : WebIntel traite les savoirs traditionnels comme des
> données **rapportées et protégées**, jamais comme des preuves d'efficacité.
> Trois mécanismes réels : le bandeau obligatoire « USAGE TRADITIONNEL
> RAPPORTÉ », 5 niveaux de confidentialité, et un registre de langues où la
> langue n'est **jamais devinée**.

## 1. Règle absolue : « USAGE TRADITIONNEL RAPPORTÉ »

Tout affichage d'un usage traditionnel (interface **et** API) porte
**obligatoirement** le bandeau **« USAGE TRADITIONNEL RAPPORTÉ »**
(`BANDEAU_USAGE_TRADITIONNEL`, `webintel/science/terrain.py` ;
schéma `bandeau: str = "USAGE TRADITIONNEL RAPPORTÉ"`).

**Ce que cela signifie** : l'usage a été *rapporté* par une source
identifiée (enquête, publication, informateur). Il n'a pas été
*expérimentalement démontré* par WebIntel.

**Interdit** : afficher « EFFICACITÉ DÉMONTRÉE » (ou équivalent) pour un
usage traditionnel. La confusion usage-traditionnel → preuve-clinique est
une des transformations interdites par la charte produit, avec :
simulation→preuve, prédiction→fait, similarité→activité, docking→efficacité,
corrélation→causalité, hypothèse-IA→vérité.

## 2. Cinq niveaux de confidentialité

Chaque enregistrement de terrain / laboratoire porte un
`niveau_confidentialite` (`NiveauConfidentialite`,
`webintel/science/models.py`) :

| Niveau | Valeur stockée | Qui voit quoi |
|---|---|---|
| **Public** | `public` | Visible par tous les utilisateurs de l'instance |
| **Projet** | `projet` | Membres du projet uniquement (défaut pour les enquêtes) |
| **Chercheur** | `chercheur` | Le chercheur propriétaire uniquement |
| **Restreint** | `restreint` | Accès restreint + **GPS flouté** (voir §4) |
| **Confidentiel** | `confidentiel` | **Jamais divulgué** sans autorisation explicite |

Par défaut, une donnée d'enquête sensible est classée `projet`, modifiable
vers `restreint` / `confidentiel`. Le niveau est stocké **par
enregistrement**, pas globalement : deux usages de la même plante peuvent
avoir des niveaux différents.

## 3. Protection des savoirs et des informateurs

**Ce qui est VRAI** :
- Les données `chercheur` / `confidentiel` sont **exclues des exports
  publics** (`/api/science/terrain/export-public/plantes/{id}` ne diffuse
  que le niveau autorisé).
- La **cartographie** masque ou floute les coordonnées selon le niveau
  (voir [`SAMPLE_MANAGEMENT.md`](SAMPLE_MANAGEMENT.md) §4) : un savoir
  localisé précisément ne révèle jamais la position exacte d'une
  ressource ou d'un informateur aux non-autorisés.
- Les données privées du laboratoire ne sont **jamais envoyées à un
  service externe** sans action explicite du chercheur (règle produit ;
  synthèse IA désactivable).

**Recommandation** (pratique, pas logicielle) : avant d'enregistrer une
enquête, vérifiez le **consentement éclairé** des informateurs et les règles
coutumières de partage du savoir. Le logiciel protège la donnée ; il ne
remplace pas l'éthique de terrain.

## 4. Noms vernaculaires et registre des langues

**Ce qui est VRAI** (`webintel/database/base.py::_amorcer_langues`,
table `langues`) : au premier démarrage, le registre est amorcé avec 5
langues du Burkina Faso — **Mooré, Dioula, Fulfuldé, Gulmancéma, Bissa**
(idempotent : relançable sans doublon).

Chaque nom vernaculaire enregistre : **forme originale** (orthographe de la
source), **forme normalisée**, **langue source**, **localité**. Règles :

- **La langue n'est jamais auto-attribuée** : sans donnée fiable, le champ
  reste vide / « langue inconnue » — affiché tel quel.
- Unicité anti-doublon sous concurrence (8 threads → 1 ligne, testé).
- Les noms sont listés avec pagination (`limit`/`offset`) :
  `GET /api/science/terrain/plantes/{id}/noms-vernaculaires`.

**Limites** :
- Le registre amorcé couvre 5 langues ; d'autres langues (nationales ou
  non) peuvent être ajoutées — le modèle est prêt, l'amorçage initial ne
  les contient pas.
- La forme « normalisée » est une commodité de recherche, pas une
  normalisation linguistique savante.

## 5. Ce que l'ethnobotanique ne fera jamais dans WebIntel

- Transformer un usage rapporté en allégation d'efficacité.
- Deviner la langue d'un nom vernaculaire.
- Fusionner automatiquement deux taxons ou deux savoirs en cas d'ambiguïté
  (jamais de fusion auto — voir [`DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md)).
- Exposer un savoir `confidentiel` dans un export, un rapport ou une carte.
