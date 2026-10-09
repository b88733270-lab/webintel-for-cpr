# WebIntel ULTIMATE — Rapport de release finale

**Version : 2.3.0** (figée — plus aucun bump)
**Date : 2026-09-23**
**Durée de travail cumulée : ~6h30 — estimation honnête** (1h première passe
+ ~5h30 reprise d'intégration finale). Ce chiffre est une estimation, pas
une mesure.

## 1. Tests — chiffres mesurés

Suite complète finale, exécutée sur le code livré :

```
1120 passed, 17 skipped, 0 failed
```

- Baseline de départ : 1115 passed / 17 skipped / 0 failed.
- +5 tests ajoutés pendant cette intégration finale :
  - `tests/test_tableau_de_bord.py` (3) — nouveau tableau de bord chercheur ;
  - `tests/test_science_combinaisons.py::test_creer_etude_enregistre_provenance` (1) ;
  - `tests/test_ui_outils_scientifiques.py::test_provenance_etude_creee_par_wizard_ou_api` (1).
- Aucun test n'a été modifié pour masquer un échec. Deux assertions de mes
  propres nouveaux tests étaient fausses au premier passage (tiret vs espace
  dans `dossier-12-points` ; champ non affiché par le template) : ce sont
  les tests qui ont été corrigés, pas le code.

## 2. Environnement de test (mesuré, pas supposé)

| Élément | État mesuré |
|---|---|
| Python | 3.12.3 (venv du repo) |
| Base principale | SQLite (fichier local) |
| PostgreSQL | **18.6 revalidé APRÈS les correctifs adversariaux** — voir §3 |
| RDKit | 2026.03.6 (présent) |
| Meeko | présent |
| Vina | **absent** (binaire non fourni, refus explicite — aucun score simulé) |
| Réseau externe | sandbox : DNS d'egress vers 198.18.0.0/15, bloqué à juste titre par l'anti-SSRF |

## 3. PostgreSQL — revalidé après les correctifs adversariaux : OUI

Le serveur PG 18.6 était encore joignable sur `127.0.0.1:5433`
(user `webintel`). Revalidation effectuée le 2026-09-23 ~02:23 GMT :

1. **`scripts/test_postgres.py` : 12/12 vérifications réussies**
   (dialecte, init_db sur base vide, 52 tables créées, FK, index nommés,
   contraintes uniques, TIMESTAMPTZ, roundtrip JSON, upsert ON CONFLICT…).
   - Correctif nécessaire trouvé en route : le script appelait
     `Base.metadata.drop_all()` AVANT l'enregistrement des modèles
     scientifiques, ce qui faisait échouer `DROP TABLE sources` (FK de
     `plant_compounds` non enregistrée). Corrigé en important les 6
     paquets de modèles avant le drop (additif, script de test uniquement).
2. **Tests gatés `WEBINTEL_REQUIRE_PG_TESTS=1` : 8 passed**
   (`tests/test_postgres_gate.py` + `tests/test_phase0_postgres.py`,
   contre `webintel_test` sur 5433).

Note d'honnêteté : le premier lancement du script (avant correctif) a
échoué à mi-`drop_all` sur la base `webintel` de test ; le relancement
corrigé a tout recréé proprement via `init_db()`. Aucune base de
production n'est concernée (le script l'interdit dans son en-tête).

## 4. Cohérence de version — vérifiée

`2.3.0` identique partout : `pyproject.toml`, `webintel/__init__.py`
(`__version__`), `CHANGELOG.md`, `README.md`, `docs/livrable.md`, et
dynamiquement dans l'UI via `webintel.__version__` (page paramètres,
provenance). Aucune mention résiduelle 2.1.0/2.2.0 dans les templates.

Le rapport d'audit adversarial (passe 2) a été déplacé dans le repo :
`docs/rapport_audit_adversarial_passe2.md`.

## 5. Cahier des charges — les 2 points vérifiés

### (a) Rôles d'équipe : PARTIEL (honnête)

Il **n'existe pas** de système de rôles
chercheur/technicien/assistant/responsable/admin avec permissions.
L'authentification existante = clés API mono-utilisateur, périmètre projet
(session UI signée HMAC, `REQUIRE_API_KEY` en mode serveur — v1.1.1).
Un champ texte libre `responsable` existe sur les projets (nom du
responsable), ce n'est pas un rôle. Construire un vrai RBAC d'équipe
n'est ni « petit » ni « sûr » dans ce cycle : **noté PARTIEL, non bâclé.**

### (b) Dashboard chercheur : FAIT (cette intégration)

Nouvelle route **`GET /tableau-de-bord`** (100 % additive) : projets,
plantes, échantillons, études, expériences, simulations, publications,
rapports, données à valider, outils — chaque chiffre est calculé en temps
réel par de vraies requêtes COUNT (isolation projet respectée pour les
entités projet ; plantes/publications globales, indiqué explicitement).
Aucune statistique artificielle : « Rapports » et « Outils » s'affichent
avec « — » et des liens, jamais un chiffre inventé. Lien ajouté dans la
navigation. 3 tests dédiés, tous verts.

## 6. Correctifs apportés pendant cette intégration finale

1. **Provenance des études de combinaison** : `creer_etude()`
   (`webintel/science/combinaisons.py`) écrit désormais une ligne dans le
   journal `provenances` (idempotent, niveau `prediction`, connecteur et
   version du moteur). Avant : `/resultats/etude/{id}/provenance`
   retournait un 404 honnête pour toute étude créée par le wizard ou le
   formulaire — un vrai manque de traçabilité. 2 tests ajoutés.
2. **`scripts/test_postgres.py`** : import des modèles scientifiques avant
   `drop_all` (voir §3).
3. **`/tableau-de-bord`** : voir §5(b).
4. **Déplacement** du rapport d'audit adversarial vers `docs/`.

Rien d'autre n'a été modifié : aucune fonction scientifique existante
retirée ou remplacée.

## 7. Fonctionnalités terminées (vérifiées)

- Plateforme scientifique plantes médicinales / pharmacognosie /
  phytochimie / produits naturels, pensée pour le Burkina Faso
  (français langue primaire ; registre de 5 langues : Mooré, Dioula,
  Fulfuldé, Gulmancéma, Bissa ; noms vernaculaires avec langue et
  localité, jamais auto-attribués).
- Fiche plante structurée (7 sections), wizard d'étude en 6 étapes
  (sessions **persistées en base**, `wizard_sessions` — la purge TTL ne
  touche que les brouillons), études de combinaison (dossier 12 points,
  versionnées append-only), analyse de synergie (Bliss/Loewe/HSA/ZIP sur
  données expérimentales importées uniquement), docking réel si Vina
  installé (refus explicite sinon), similarité moléculaire réelle
  (Morgan/ECFP + Tanimoto), propriétés moléculaires réelles (logP, HBD,
  HBA, TPSA, Lipinski… avec méthode et limites).
- Gestion terrain : échantillons avec GPS (jamais (0,0) par défaut),
  vouchers, usages traditionnels (**toujours** « USAGE TRADITIONNEL
  RAPPORTÉ », jamais « efficacité démontrée »), carte, exports publics
  sans coordonnées précises.
- Laboratoire : import de mesures expérimentales (données privées par
  défaut, jamais envoyées à un service externe sans action explicite),
  qualité (9 détecteurs, lecture seule, aucune correction automatique).
- Rapports : 14 sections (HTML/Markdown/PDF/ZIP reproductible),
  provenance par résultat (`/resultats/{type}/{id}/provenance`).
- Persistance prouvée après vrai redémarrage ; 52 tables ; pagination
  sur les listes ; mode hors-ligne (file de synchronisation).
- Sécurité : 12 failles réelles corrigées en 2 passes adversariales
  (dont anti-SSRF avec transport à connexion épinglée + tests
  DNS-rebinding ; cloisonnement projet strict P0-1 ; sessions UI signées).
  **Garantie limitée** : prouvé par tests, pas une certification ;
  aucun audit externe indépendant.

## 8. Outils : disponibles / absents (mesuré)

| Outil | État |
|---|---|
| RDKit 2026.03.6 | présent (calculs réels) |
| Meeko | présent (SMILES→PDBQT) |
| Vina | **absent** — refus explicite, aucun score simulé |
| PostgreSQL 16/18 | support validé sur 18.6 réel (ce cycle) |
| Windows `install.bat` | **jamais exécuté sur un vrai Windows** |

## 9. Connecteurs (mesuré)

**Implémentés et enregistrés (5)** : PubChem (domaine public, NLM),
PubMed (données publiques, NLM), OpenAlex, ChEMBL (CC-BY-SA 3.0),
GBIF — chacun avec URL source, licence et stratégie d'import
documentées ; import idempotent avec provenance (licence, date,
version). **Non testés en direct dans ce sandbox** (egress DNS vers
198.18.0.0/15, bloqué à juste titre par l'anti-SSRF — comportement
correct, pas un bug produit). **Roadmap honnête** : Kew, LOTUS, IMPPAT
(non implémentés). Note : le brief de mission supposait
ChEMBL/GBIF/OpenAlex en roadmap, mais le code et `docs/connecteurs.md`
les documentent comme implémentés en v2.2 — c'est l'état mesuré du repo
qui fait foi ici.

## 10. Archive finale

- Chemin : `~/workspace/your_files/webintel-v2.3.0.zip`
- Contenu vérifié par `unzip -l` : 440 fichiers, **aucun** `.venv/`,
  `__pycache__/`, `*.db`, `.pytest_cache`.
- Construite depuis l'arbre courant APRÈS tous les correctifs, rapport
  inclus, re-vérifiée une dernière fois.

## 11. Test d'installation fraîche RÉEL (instance /tmp, code de l'archive)

`unzip` → `./install.sh` (OK, venv + dépendances + init SQLite) →
`./start.sh` (port 8902) :

| Étape | Méthode | Résultat |
|---|---|---|
| `GET /`, `/accueil`, `/docs`, `/tableau-de-bord` | curl | **200 ×4** |
| Créer un projet | `POST /api/projects` | **201** |
| Créer une plante (nom scientifique + nom vernaculaire) | `POST /plante-etude/creer` | **303** → fiche affichée (nom + « neverdaye, nebday » wolof) |
| Créer un échantillon avec GPS | `POST /plantes/{id}/terrain/echantillons` | **303** → GPS 12.3714 / -1.5197 affichés |
| Wizard d'étude 6 étapes (formulaires) | `/etudes/nouvelle` → etape1/2/3 → `/etudes/lancer` → resultats | **200**, dossier « 12 points » |
| Vérifier les résultats | `GET /etudes/{uuid}/resultats` | **200**, « Résultat informatique », logP |
| Générer un rapport | `GET` puis `POST /etudes/{id}/rapport` | **200**, hypothèses + sources |
| Vérifier la provenance | `GET /resultats/etude/{id}/provenance` | **200**, fiche de traçabilité (connecteur, version, niveau) |
| Tableau de bord | `GET /tableau-de-bord` | **200**, comptes réels (2 projets, 1 plante, 1 échantillon, 1 étude, 2 données à valider) |

Incident trouvé et corrigé pendant le test : la provenance d'une étude
créée par le wizard retournait 404 (aucune ligne au journal). Corrigé
(§6.1), archive reconstruite, **test fraîche recommencé de zéro** :
tout est vert ci-dessus sur la seconde instance.

## 12. Problèmes connus restants (non masqués)

1. **LICENSE manquant à la racine** : `docs/LICENSES.md` documente les
   licences des dépendances, mais il n'y a pas de fichier LICENSE
   racine déclarant la licence du projet lui-même.
2. **`install.bat` jamais testé sur un vrai Windows.**
3. **Pas de test de charge** (comportement sous volume non mesuré).
4. **Pas de chiffrement au repos** (SQLite/PostgreSQL en clair).
5. **Rôles d'équipe** : voir §5(a) — PARTIEL.
6. Connecteurs externes non testés en direct ici (voir §9).
7. Vina non fourni (Apache-2.0) — installation côté chercheur via
   Paramètres → Outils scientifiques.
8. Sécurité : garantie limitée aux tests (voir §7) — pas d'audit externe.

## 13. Honnêteté scientifique — règles appliquées et testées

- Une prédiction reste une prédiction : formulations interdites
  (« efficace », « sûr », « traite ») bannies par tests automatiques.
- Données publiées / normalisées / hypothèse / prédiction-simulation /
  observation expérimentale strictement séparées et étiquetées.
- Jamais de donnée, publication, score ou mesure inventés ; jamais de
  fusion silencieuse de taxons ; historique conservé (restaurer = nouvelle
  version).
- Usage traditionnel ≠ preuve clinique, affiché comme tel partout.
