# Démo scientifique WebIntel (Phase 14, enrichie v2.1)

Scénario de démonstration **reproductible, 100 % hors-ligne, idempotent**
(mode local SQLite). Il enchaîne toute la chaîne de valeur scientifique :

```
import démo → fiche plante → enrichissement v2.1 → simulation →
dossier ZIP → fiche PDF → fiche HTML enrichie → rapport Markdown
```

## Exécution

```bash
./scripts/demo.sh
```

Pré-requis : `./install.sh` une fois (crée `.venv`).

## Données

Les fichiers `demo/` sont dédiés à la démo (ne touchent pas à
`docs/exemples_import/` ni à votre base de travail `webintel.db`) :

| Fichier | Contenu |
|---|---|
| `demo_plantes.csv` | 3 plantes (Moringa oleifera, Artemisia annua, Hibiscus sabdariffa) |
| `demo_composes.csv` | 4 composés (Quercétine, Artémisinine, Kaempférol, Acide chlorogénique — identifiants vérifiés RDKit) |
| `demo_liens.csv` | 3 liens plante–composé (niveau `publie`, DOI cités) |
| `demo_evidences.json` | 2 preuves documentaires |

Idempotence : chaque lancement reconstruit `/tmp/webintel-demo` depuis
zéro. La démo écrit **uniquement** dans `/tmp/webintel-demo/demo.db` —
jamais dans `./webintel.db`.

## Sortie attendue

```
  import demo_plantes.csv : 3 créés, 0 ignorés (niveau « publie »)
  import demo_composes.csv : 3 créés, 0 ignorés (niveau « publie »)
  import demo_liens.csv : 3 créés, 0 ignorés (niveau « publie »)
  import demo_evidences.json : 2 créés, 0 ignorés (niveau « publie »)

=== Démo scientifique WebIntel ===
✅ projet « Démo WebIntel — plantes médicinales » créé
✅ import démo : 11 objets créés (0 erreur)
✅ fiche *Moringa oleifera* : 1 composé(s), 0 référence(s), niveaux séparés ✅
✅ botanique détaillée (feuilles/fleurs/fruits/graines/habitat)
✅ 3 composés documentés (HPLC/LC-MS, rendements, preuve DOI) — preuve attachée (id=3)
✅ cible documentée : quercétine → COX-2 (inhibiteur, preuve DOI)
✅ activité rapportée : antioxydant (in vitro, DPPH)
✅ étude structurée ajoutée à la littérature
✅ analyse structurale réelle (RDKit) : 3/3 descripteurs calculés (Quercétine : 302.238 g/mol, Kaempférol : 286.239 g/mol, Acide chlorogénique : 354.311 g/mol)
✅ comparaison structurale de démonstration (« Quercétine, Kaempférol, Acide chlorogénique ») : score informatique calculé — indicateur de comparaison, PAS une mesure d'effet pharmacologique
✅ étape ignorée (non disponible) : docking : Moteur « docking_adapter » indisponible : AutoDock Vina non installé (binaire `vina` introuvable dans le PATH) — voir docs/simulations.md pour l'installation. Aucun calcul effectué, aucun résultat produit.
✅ fiche enrichie v2.1 vérifiée (botanique, 3 composés, cibles, activités, littérature) → export HTML /tmp/webintel-demo/fiche_plante_1_20260922.html
✅ simulation « statistics » : moyenne IC50 = 12.50 µg/mL, hash 4c43b1308f6e… (rejeu → même hash)
✅ dossier de recherche : /tmp/webintel-demo/dossier_recherche_demo.zip (1850 octets)
✅ fiche PDF : /tmp/webintel-demo/fiche_plante_1_20260922.pdf (5305 octets)
✅ rapport Markdown : /tmp/webintel-demo/rapport_simulation_demo.md

Fichiers produits dans /tmp/webintel-demo :
  - demo.db
  - dossier_recherche_demo.zip
  - fiche_plante_1_20260922.html
  - fiche_plante_1_20260922.pdf
  - rapport_simulation_demo.md

DÉMO RÉUSSIE
```

## Section v2.1 : modèle enrichi (étape 2b)

L'étape « enrichissement v2.1 » démontre le modèle scientifique enrichi :

- **Botanique détaillée** : feuilles, fleurs, fruits, graines, racines,
  écorce, habitat, références botaniques (champs absents = non affichés,
  jamais inventés).
- **3 composés documentés** avec méthode d'analyse (HPLC-UV, LC-MS),
  rendement et preuve DOI attachée (`evidence_id` pointée).
- **Cibles biologiques documentées** (quercétine → COX-2, inhibiteur)
  et **activités pharmacologiques rapportées** (antioxydant, in vitro,
  modèle DPPH) — données *rapportées*, jamais des allégations
  d'efficacité.
- **Littérature structurée** : étude enregistrée (titre, auteurs, année,
  journal, DOI).
- **Analyse structurale réelle** : descripteurs moléculaires calculés
  par RDKit à partir des SMILES importés (formule vérifiée :
  quercétine = C15H10O7).
- **Comparaison structurale** : score informatique (empreintes de Morgan
  + Tanimoto) calculé par le moteur de combinaisons si présent —
  affiché comme *indicateur de comparaison*, pas comme mesure d'effet.
- **Docking** : si le moteur est installé, son statut est rapporté ;
  sinon l'étape est proprement ignorée avec le message du moteur
  (« Aucun calcul effectué, aucun résultat produit ») — jamais un faux
  résultat inventé.
- **Fiche enrichie exportée** (HTML vérifiable) avec les nouvelles
  sections et le bandeau « ce n'est pas un avis médical ».

## Fichiers produits (vérifiables)

| Fichier | Vérification |
|---|---|
| `demo.db` | base SQLite de la démo (ouvrable avec tout client SQLite) |
| `dossier_recherche_demo.zip` | dossier de recherche complet, **sans** données privées (clé privée exclue par défaut) |
| `fiche_plante_1_20260922.pdf` | vraie fiche PDF (badge « Publication », sections botanique → bibliographie) |
| `fiche_plante_1_20260922.html` | fiche HTML enrichie v2.1 (botanique détaillée, activités, cibles, littérature, bandeau « pas un avis médical ») |
| `rapport_simulation_demo.md` | rapport reproductible : INPUTS → METHOD → PARAMETERS → OUTPUTS → REFERENCES → LIMITATIONS |

## Points scientifiques démontrés

- Les imports CSV/JSON sont étiquetés `publie` avec provenance
  (`connecteur: fichier:<nom>`) ; le SDF est étiqueté `normalise`
  avec avertissement de normalisation.
- La fiche plante sépare les 5 niveaux : sections publiques `publie`,
  bloc « Laboratoire privé » séparé avec avertissement explicite.
- La simulation `statistics` est déterministe et rejouable :
  mêmes entrées → même hash SHA-256 → mêmes sorties. Le rapport
  affiche le badge **Prédiction — à confirmer expérimentalement**.
- Le dossier ZIP et les exports excluent les données privées par défaut.
- **v2.1** : chaque ajout manuel (botanique, composé, activité, cible,
  étude) est sourcé (publication ou DOI) et provenancé
  (`connecteur: ui-manuel`) ; sans source, le formulaire est rejeté avec
  un message explicite.
- **v2.1** : la section « Sécurité » affiche toujours son bandeau de
  prudence (« ce n'est pas un avis médical… »), même vide ; l'absence
  d'alerte ne prouve jamais l'innocuité.
- **v2.1** : les étapes optionnelles (combinaisons, chimie, docking)
  sont soit exécutées réellement, soit proprement ignorées avec un
  message — jamais de faux résultat inventé.

## Réseau

Le script neutralise toute résolution DNS (`socket.getaddrinfo` factice) :
la démo prouve qu'elle ne dépend d'aucune connexion. Un connecteur réseau
interrogé échouerait avec une erreur explicite, jamais un faux résultat.
