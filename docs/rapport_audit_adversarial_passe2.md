# Rapport d'audit adversarial — WebIntel 2.3.0, 2ᵉ passe

**Date :** 23 septembre 2026
**Méthode :** audit « yeux frais » — techniques d'attaque différentes de la 1ʳᵉ passe, exploitation réelle avant correction, correctifs additifs minimaux, un test adversarial par faille, suite complète relancée.
**Périmètre :** dépôt `~/workspace/webintel` (non versionné Git — modifications directes, non commitées).

> **Engagement d'honnêteté :** ce rapport ne déclare pas l'application « sécurisée ».
> Il décrit uniquement les surfaces qui ont été éprouvées, les attaques qui ont
> réussi (corrigées), celles qui ont échoué, et les limites résiduelles connues.

---

## 1. Baseline et résultat final

| Étape | Commande | Résultat |
|---|---|---|
| Baseline (avant correctifs) | `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` | **1090 passed / 17 skipped / 0 failed** (281 s) |
| Run intermédiaire (après 6 correctifs) | idem | 1102 passed, **2 failed** — deux échecs introduits par mes propres changements, détectés par la suite, analysés et corrigés (voir §5) |
| Run final (tous correctifs + 25 tests adversariaux) | idem | **1115 passed / 17 skipped / 0 failed** (182 s) |

Nouveau fichier de tests : `tests/test_audit_adversarial_passe2.py` — **25 tests adversariaux**, tous verts.

---

## 2. Failles confirmées par exploitation, puis corrigées

### Faille A — Import CSV : champ géant → HTTP 500 (DoS / crash applicatif)
- **Technique :** CSV avec un champ de 200 000 caractères (> `csv.field_size_limit`).
- **Preuve AVANT :** `POST /api/science/import` → **500**. `_csv.Error` n'était capturé qu'à la création du `DictReader`, pas pendant son itération.
- **Cause :** `webintel/science/import_export.py`.
- **Correctif :** itération complète du `DictReader` placée dans le `try/except csv.Error` ; l'erreur devient `ErreurImport` → **400** propre avec message français.
- **Preuve APRÈS :** même payload → **400** `{"detail": "…CSV…"}`.
- **Test :** `test_import_csv_champ_geant_rejete_proprement`.

### Faille D — Registre des langues : champs mal typés → HTTP 500
- **Technique :** `POST /api/science/terrain/langues` avec `"code": 123`, `"code": ["x"]`, `"region": {"a": 1}`, `"source": 42`.
- **Preuve AVANT :** **500** (`AttributeError` sur `.strip()` d'un non-chaîne).
- **Cause :** `webintel/api/routers/science_terrain.py`, `webintel/science/terrain.py` (`enregistrer_langue_db`, ~l.242).
- **Correctif :** `_chaine_ou_aucune()` (terrain.py ~l.220) — validation explicite des types `code`/`libelle`/`region`/`source` ; entrée mal typée → **422**, jamais 500.
- **Preuve APRÈS :** les 4 payloads → **422** ; création légitime → **201**.
- **Tests :** `test_langues_code_non_chaine_422`, `test_langues_creation_legitime_ok`.

### Faille E — Usurpation visuelle : contrôles bidi Unicode conservés
- **Technique :** nom vernaculaire `Quinine\u202eEVIL` (override bidirectionnel).
- **Preuve AVANT :** valeur stockée telle quelle (`'Quinine\\u202eEVIL'`) — l'échappement HTML ne neutralise pas les caractères invisibles de directionnalité (risque d'usurpation visuelle dans les fiches).
- **Cause :** `webintel/science/terrain.py` (`ajouter_nom_vernaculaire`).
- **Correctif :** `_CONTROLES_BIDI` + `retirer_controles_bidi()` (~l.114-130) appliqués avant normalisation/stockage ; un nom composé uniquement de contrôles devient vide → rejeté (`ValueError`).
- **Preuve APRÈS :** stocké `QuinineEVIL` (contenu visible conservé, contrôle retiré) ; nom bidi-only → rejeté.
- **Tests :** `test_bidi_retire_du_nom_vernaculaire`, `test_bidi_seul_rejete_comme_vide`, `test_retirer_controles_bidi_unitaire`.

### Faille F — Intégrité des fichiers bruts : oracle de SHA-256 via symlink substitué
- **Technique :** rattacher un fichier légitime dans la racine autorisée, le supprimer, le remplacer par un symlink vers `/etc/hostname`, puis appeler `verifier_integrite_fichier`.
- **Preuve AVANT :** la fonction calculait et retournait **exactement le SHA-256 de `/etc/hostname`** — oracle de lecture/empreinte indirecte après rattachement.
- **Cause :** `webintel/science/terrain.py` (`verifier_integrite_fichier`, ~l.1018).
- **Correctif (2 couches) :**
  1. `_chemin_brut_a_verifier()` (~l.1000) : re-résolution `realpath` avant lecture ; tout lien sortant de la racine → `correspond=False`, `hash_calcule=None`, alerte explicite — **le fichier cible n'est jamais lu** ;
  2. `_sha256_fichier_sans_suivi()` (~l.931) : ouverture `O_NOFOLLOW` (POSIX) + contrôle `S_ISREG` — referme la course validation→ouverture : un symlink substitué dans l'intervalle est refusé au lieu d'être suivi.
- **Preuve APRÈS :** `correspond=False`, `hash_calcule=None`, raison « hors du répertoire autorisé » ; aucun hash du fichier cible ne fuit.
- **Tests :** `test_integrite_symlink_hors_racine_sans_oracle`, `test_lecture_sans_suivi_refuse_symlink_substitue`, `test_integrite_fichier_legitime_ok`.
- **Limite honnête :** le remplacement d'un *répertoire parent* par un lien reste une opération privilégiée du système de fichiers, hors modèle de menace (accès local requis dans tous les cas). Repli classique (sans `O_NOFOLLOW`, ex. Windows) : le contrôle `realpath` préalable reste actif.

### Faille G — Restauration : archive aux champs inconnus → 500 après écritures partielles
- **Technique :** archive ZIP valide et correctement hashée contenant une ligne `langues` avec `name`/`cle_inconnue`.
- **Preuve AVANT :** `TypeError: 'name' is an invalid keyword argument for Langue` non gérée → 500 potentiel, après écritures partelles.
- **Cause :** `webintel/offline/sauvegarde.py` (`importer_projet`).
- **Correctif :** `_valider_contenus()` (~l.247) — chaque ligne restaurée est vérifiée contre les colonnes SQLAlchemy **avant toute écriture** ; champ inconnu ou ligne non-objet → `ValueError` français (400), zéro écriture partielle.
- **Preuve APRÈS :** `ValueError("champs inconnus")`, aucune ligne créée.
- **Test :** `test_restore_champs_inconnus_refuse_avant_ecriture`.

### Faille W — Wizard : session expirée d'un autre projet supprimée (mutation avant contrôle d'accès)
- **Technique :** projet B charge l'UUID exact d'une session expirée appartenant au projet A.
- **Preuve AVANT :** réponse 404, **mais la session du projet A était effectivement supprimée** — même classe que la faille P0 corrigée dans `science_lab`.
- **Cause :** `webintel/ui/chercheur_etudes_routes.py` (`_charger_session`, l.181).
- **Correctif :** contrôle **projet** déplacé avant toute vérification TTL/suppression → 403 sans toucher à la ligne. Le contrôle *propriétaire* garde son ordre historique (après la purge TTL) : une session expirée du même projet — même d'un autre utilisateur — reste purgée en 404 (comportement historique conservé, le projet est le périmètre de confiance).
- **Preuve APRÈS :** projet B → **403**, session du projet A intacte ; même projet → 404 + purge comme avant.
- **Tests :** `test_session_expiree_autre_projet_non_supprimee`, `test_session_expiree_propre_projet_purgee_404`, `test_session_expiree_meme_projet_autre_utilisateur_purgee_404`.

### Faille P — Provenance : falsification (entité inexistante acceptée)
- **Technique :** `POST /api/science/provenance` avec `entity_type="plant"`, `entity_id=424242` (inexistant), `source_name` inventé.
- **Preuve AVANT :** **201** — le service n'a jamais vérifié l'existence de l'entité (lecture de code : aucun contrôle dans `record_provenance` ni le routeur).
- **Cause :** `webintel/api/routers/science_relations.py` (`create_provenance`, ~l.432).
- **Correctif :** table `_ENTITES_PROVENANCE` (~l.428) ; type inconnu → **422**, entité inexistante → **404** (« une provenance ne peut être rattachée qu'à une entité existante »), entité project-scopée d'un autre projet → **403** (défensif).
- **Preuve APRÈS :** entité inexistante → **404** ; type inconnu → **422** ; provenance légitime → **201**.
- **Tests :** `test_provenance_entite_inexistante_404`, `test_provenance_type_inconnu_422`, `test_provenance_legitime_ok`.
- **Note :** l'ajout d'une *seconde* provenance pour une même entité reste possible — c'est le design append-only du journal (plusieurs sources peuvent documenter une entité), pas une faille.

### Faille Z — Restauration ZIP : bombe de décompression (DoS mémoire)
- **Technique :** archive de 194 Ko annonçant un membre de 200 Mo (ratio 1029:1).
- **Preuve AVANT :** `zf.testzip()` + `zf.read()` décompressaient **tout en mémoire sans aucune borne** — quelques Mo suffisent à provoquer un OOM du serveur.
- **Cause :** `webintel/offline/sauvegarde.py` (`importer_projet`, ~l.470).
- **Correctif :**
  - `_verifier_membres_zip()` (~l.56) : bornes sur les métadonnées **avant** toute décompression — 100 000 membres max, 256 Mo/membre, 1 Go total, ratio max 100:1, membres chiffrés refusés, tailles incohérentes refusées ;
  - `_lire_membre_borne()` (~l.104) : lecture en flux bornée + vérification CRC (remplace `testzip()`/`read()` partout, y compris les photos).
- **Preuve APRÈS :** la bombe est refusée sur les métadonnées (`ValueError` « ratio de compression anormal »), jamais décompressée.
- **Tests :** `test_zip_bomb_ratio_refuse_sans_oom`, `test_zip_membre_geant_refuse`, `test_zip_membre_chiffre_refuse`.

---

## 3. Attaques tentées SANS succès (surfaces éprouvées, résistent)

| # | Attaque | Résultat |
|---|---|---|
| 1 | JSON profondément imbriqué (5 000 niveaux) | **400**, pas de 500 |
| 2 | Chemin avec octet nul | `ValueError` propre, pas de lecture |
| 3 | Traversées `../`, doubles slash, `./` | canonisées si dans la racine, refusées sinon |
| 4 | SDF vide / sans séparateur / malformé | `SDFInvalide` (sous-classe d'`ErreurImport`) → 400 |
| 5 | Pagination négative / énorme / injection | bornes `Query(ge/le)`, paramètres liés SQLAlchemy |
| 6 | Upload photo avec `..` dans le nom | nom stocké = UUID (`orig_<hex>.jpg`), pas de traversée |
| 7 | Comparaison de versions inter-projets | **403** — pas d'IDOR (contrôle `require_project_access` sur l'étude) |
| 8 | Export public avec données restreint/confidentiel | exclues ; GPS précis et identités jamais divulgués (test `test_export_public_ne_fuit_pas_le_restreint`) |
| 9 | GPS hors bornes (±91/±181, `NaN`, `inf`) via API **et** formulaire UI | rejetés ; bornes exactes ±90/±180 acceptées |
| 10 | InChIKey fausse mais au bon format | divergence signalée (`valeur_stockee` vs `valeur_calculee`), **jamais écrasée ni présentée comme calculée** |
| 11 | Course d'insertion pharmacognosie (PUT concurrents) | gérée par `get_or_create` (savepoint + `IntegrityError`) — 1 seule ligne |
| 12 | `FORMULATIONS_INTERDITES` (ReDoS) | alternation simple, pas de quantificateurs imbriqués |
| 13 | SMILES géant | borné à 10 000 caractères (`_SMILES_MAX`) |
| 14 | Recherche `q` avec `%`, `_`, Unicode extrêmes | motif LIKE échappé (`_echapper_like`), pas de regex utilisateur |
| 15 | `compound_id` non-entier sur `/normaliser` | **422** automatique (typage FastAPI) |

**Durcissement défensif ajouté** (pas une faille confirmée) : `q` de recherche borné à 200 caractères (`max_length` sur `/api/science/recherche` et `/search`) — tests `test_recherche_q_demesuree_422`, `test_recherche_q_normale_ok`.

---

## 4. Fichiers modifiés (correctifs)

| Fichier | Changement |
|---|---|
| `webintel/science/import_export.py` | itération `DictReader` dans le `try` (faille A) |
| `webintel/science/terrain.py` | `_chaine_ou_aucune` (D, ~l.220) ; `_CONTROLES_BIDI` + `retirer_controles_bidi` (E, ~l.114) ; `_chemin_brut_a_verifier` (F, ~l.1000) ; `_sha256_fichier_sans_suivi` + `O_NOFOLLOW` (F, ~l.931) |
| `webintel/offline/sauvegarde.py` | `_valider_contenus` (G, ~l.247) ; `_verifier_membres_zip` + `_lire_membre_borne` + bornes (Z, ~l.50-130) |
| `webintel/ui/chercheur_etudes_routes.py` | `_charger_session` : contrôle projet avant TTL/suppression (W, l.181) |
| `webintel/api/routers/science_relations.py` | `_ENTITES_PROVENANCE` + contrôles 422/404/403 (P, ~l.428) |
| `webintel/api/routers/science_search.py`, `webintel/api/routers/search.py` | `max_length=200` sur `q` (défensif) |
| `tests/test_audit_adversarial_passe2.py` | **25 tests adversariaux** (nouveau) |

---

## 5. Incidents de parcours (honnêteté)

Deux échecs sont apparus dans un run intermédiaire — **causés par mes propres changements, pas par l'application**, détectés par la suite existante puis corrigés :

1. `test_wizard_session_expiree_purgee_404` : ma première version du correctif W avait déplacé *aussi* le contrôle propriétaire avant le TTL, cassant le contrat historique (session expirée du même projet purgée en 404). **Corrigé** : seul le contrôle projet précède la mutation ; l'ordre propriétaire-après-TTL est conservé, avec un test verrouillant ce contrat.
2. `test_cinq_langues_amorcees_en_base` : mon test créait une langue via l'API, ce qui mute le registre global `LANGUES_BURKINA` (design documenté : le registre survit au redémarrage), réinsérée en base par `_amorcer_langues()` avant chaque test. **Corrigé** : nettoyage explicite du registre en fin de test.

---

## 6. Limites honnêtes (non corrigé / non vérifié)

- **PostgreSQL non revalidé après correctifs** : la suite tourne sur SQLite (la validation PostgreSQL réelle du 22/09/2026 pré-date cette passe).
- **Windows non testé** : le durcissement `O_NOFOLLOW` a un repli documenté sans lui ; les chemins Windows n'ont pas été exécutés.
- **Oracle 403/404 sur la comparaison de versions** : le design « 403 d'abord » (ne pas révéler l'existence des études étrangères) est intentionnel et documenté.
- **Course sur répertoire parent** (fichiers bruts) : opération privilégiée du système de fichiers, hors modèle de menace.
- **Registre `LANGUES_BURKINA` global et mutable** : design documenté (extensibilité), pas une faille.
- **Provenance concurrente** (2ᵉ ligne pour la même entité) : design append-only du journal.
- **Aucune donnée scientifique inventée** : les tests utilisent des valeurs factices explicites (« Test Langue », fausse InChIKey `AAAAAAAAAAAAAA-BBBBBBBBBB-C` signalée comme divergente, jamais comme vraie).

## 7. Conclusion

8 failles réelles ont été **exploitées avec succès puis corrigées** (dont 1 critique : oracle de hash via symlink ; 1 DoS : zip bomb), chacune verrouillée par au moins un test adversarial. 15+ techniques d'attaque ont **échoué** contre les protections existantes. La suite complète est verte : **1115 passed / 17 skipped / 0 failed**, sans régression.
