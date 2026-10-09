# Livrable WebIntel v2.3.0 — 12 statuts factuels

Bilan de vérification du produit au 2026-09-23, après la v2.3.0
(chantiers PERSISTANCE / PERFORMANCE / BASE DE DONNÉES + MODÈLE
SCIENTIFIQUE & TERRAIN — code 100 % additif, aucune fonction
scientifique ni route existante supprimée ou remplacée). Objectif
inchangé : permettre à un chercheur en pharmacie/pharmacognosie —
non-développeur — de rechercher une plante, comprendre ses
constituants, explorer des molécules, construire des hypothèses et
lancer des analyses in silico reproductibles avant l'expérimentation
réelle. Chaque point est factuel : ce qui est vérifié par des tests ou
une exécution réelle est indiqué ; ce qui n'est pas validé est déclaré.
Succède au livrable v2.2.0.

## 1. Positionnement produit

WebIntel est une plateforme d'intelligence scientifique pour chercheurs
en pharmacognosie, phytochimie et produits naturels, pensée pour le
Burkina Faso (français langue première, registre de langues
nationales) : parcours PLANTE → COMPOSÉS → LITTÉRATURE → HYPOTHÈSE →
ANALYSE IN SILICO → ÉTUDE DE COMBINAISON → RAPPORT → EXPÉRIMENTATION
RÉELLE. Simulation et preuve expérimentale ne sont jamais confondues
(règle absolue, testée — formulations « efficace »/« sûr »/« traite »
interdites par test).

## 2. Socle technique

FastAPI (`create_app()`), Jinja2 (UI), SQLAlchemy 2, Python 3.10+
(testé 3.12). Base **SQLite en mode local** (zéro configuration),
**PostgreSQL re-validé en v2.3.0 contre un vrai serveur 18.6**
(12/12 `scripts/test_postgres.py`, 8/8 tests dédiés avec gate stricte
`WEBINTEL_REQUIRE_PG_TESTS=1`, 22/22 tests du chantier — binaires
embarqués `pgserver`). Fail-fast au démarrage si PG injoignable.
Pas de moteur maison : bibliothèques fiables réutilisées (stdlib,
RDKit optionnel, Vina externe si installé) via des adaptateurs
versionnés. Migrations idempotentes.

## 3. Modèle scientifique et niveaux de données

Cinq niveaux strictement séparés, vérifiés par tests comportementaux :
`publie`, `normalise`, `hypothese` (synthèses IA, jamais persistées),
`prediction` (simulations, badge « à confirmer expérimentalement »),
`experimental` (observations du laboratoire, privées par projet).
Référentiel exposé : `GET /api/science/data-levels`.
Relations : `CompoundTarget`, `PlantActivity`, `Study` (DOI, PMID),
`Provenance`, `Dataset`, `PlantCompound` enrichi (partie, concentration,
méthode d'extraction/analyse, `evidence_id`). L'UI affiche un badge
coloré par section ; « non renseigné » quand un champ est absent —
jamais inventé.

**Nouveau v2.3.0 (chantier terrain)** :
- **Noms vernaculaires** : forme originale + normalisée + langue source
  + localité ; **langue jamais auto-attribuée** ; registre `langues`
  amorcé idempotent : **Mooré, Dioula, Fulfuldé, Gulmancéma, Bissa**.
- **Ethnobotanique** : bandeau obligatoire « USAGE TRADITIONNEL
  RAPPORTÉ » (jamais « efficacité démontrée ») ; 5 niveaux de
  confidentialité (`public`/`projet`/`chercheur`/`restreint`/
  `confidentiel`).
- **Chaîne de traçabilité** : PLANTE → ÉCHANTILLON → VOUCHER →
  EXTRACTION → EXTRAIT → FRACTION → COMPOSÉ → ANALYSE ; GPS validé
  **WGS84** (hors plage → `ValueError`, jamais corrigé) ; fichiers bruts
  confinés (`WEBINTEL_FICHIERS_BRUTS_DIR`, SHA-256, 200 Mo max) ;
  cartographie avec **masquage** (`confidentiel`/`chercheur`) et
  **floutage** grille 0,5° (~55 km) pour `restreint`/`projet`.

## 4. API

**215 chemins OpenAPI générés depuis le code** (261 opérations : GET
145 / POST 106 / PUT 2 / DELETE 4 — liste exhaustive :
`docs/API.md`). REST sous `/api` : plantes, composés, preuves, liens,
cibles, activités, études, recherche (texte + filtres par niveau),
laboratoire, simulations exécutables + rejeu, synthèse IA éphémère
(503 explicite si désactivée), imports/exports (CSV/JSON/XLSX/SDF),
connecteurs, **combinaisons** (dossier 12 points), **synergie**
(Bliss/Loewe/HSA/ZIP, uniquement sur données expérimentales
importées), **docking** (statut réel, aperçu `scores=null`, exécution
Vina), **rapports** (Markdown/HTML/PDF/ZIP), **terrain** (32 opérations :
échantillons, vouchers, noms vernaculaires, langues, usages,
pharmacognosie, export public, intégrité des fichiers), **provenance**,
**qualité** (lecture seule). Mode serveur : clé API obligatoire,
cloisonnement par projet (accès croisé → 403), 401/403/422 explicites,
pagination `limit` (1…500, défaut 50) / `offset` sur 11 listes.

## 5. Interface web (UI)

Parcours chercheur : `/accueil` (« Que souhaitez-vous étudier ? »,
5 cartes), fiche plante 7 sections avec badges de preuve et
« Qu'est-ce que cela signifie ? » sous chaque métrique,
`/recherche-universelle`, `/mes-recherches` (historique versionné),
**wizard 6 étapes** (`/etudes/nouvelle` : objet → échantillons →
type d'analyse simple/**avancé** → paramètres → lancement →
résultats — **mêmes moteurs scientifiques** dans les deux modes),
`/outils-scientifiques` (état réel RDKit/Meeko/Vina/PostgreSQL),
`/resultats/{type}/{id}/provenance`, `/donnees/importer` (CSV/JSON/
XLSX, badge « DONNÉES PRIVÉES », SHA-256), `/synergie/nouvelle`,
`/etudes/{id}/rapport`, `/synchronisation` (file + conflits
LOCAL/DISTANT/CONFLIT), `/sauvegarde` (export/import projet),
`/carte` (masquage GPS), `/qualite`.

**Nouveau v2.3.0 — persistance réelle** : le wizard est persisté en
base (`wizard_sessions`, TTL 30 jours) — **fini la perte au
redémarrage** (v2.2.0 : état en mémoire). La purge TTL ne supprime que
les brouillons `en_cours` ; une étude terminée reste accessible même
après expiration (test vrai redémarrage en 2 processus).
**Versionnage append-only** (`study_versions`) : création → version
initiale automatique ; nouvelle exécution = nouvelle étude (jamais
d'écrasement) ; comparaison structurée v1/v2 (entrées, paramètres,
sources, logiciels, résultats + SHA-256) ; unicité `(study_id, numero)`
+ 8 threads → numéros 1..8 sans doublon (SQLite et PG).

## 6. Import / export / backup

Imports CSV/JSON/XLSX/SDF par paquets de 500 lignes (garde-fou
mémoire), rapport ligne par ligne, provenance enregistrée, inspection
du contenu réel (ZIP déguisé détecté). Exports CSV/JSON avec niveaux et
sources, fiche plante PDF/HTML, dossier ZIP, rapport de recherche
(Markdown/HTML/PDF/ZIP reproductible, 10 sections + avertissement, badges de preuve,
section « Limites » toujours présente). **Backup/restore v2.3.0** :
`exporter_projet`/`importer_projet` (inclut `langues` +
`wizard_sessions`), **round-trip vérifié par test** sur base fraîche
(12 tables relues champ à champ, SQLite et PG).

## 7. Connecteurs de données

**Implémentés : PubChem (PUG-REST), PubMed (E-utilities), OpenAlex,
ChEMBL, GBIF** — chacun enregistre nom, version, **licence et
conditions d'utilisation**, date d'import, provenance, stratégie de
mise à jour (jamais de copie aveugle ; idempotence testée). GBIF :
vérification du statut taxonomique (synonyme signalé, nom accepté
proposé). Panne réseau → erreur explicite, jamais de faux résultat.
`PinnedHTTPTransport` anti-TOCTOU sur connecteurs/fetcher/robots/
webhooks (tests de régression DNS-rebinding).

## 8. Simulations, chimoinformatique et IA

- **Niveau SCIENCE** : `scripts/install_science.sh` (RDKit + Meeko) ;
  sans RDKit, l'app tourne et indique explicitement le non-calculable.
- **RDKit 2026.03.6 (présent)** : normalisation (SMILES canonique,
  InChI, InChIKey — invalide **signalé, jamais corrigé**), empreintes
  de Morgan, **Tanimoto réel** (caféine/théophylline = 0,5294 vérifié),
  descripteurs (logP, HBD, HBA, TPSA, rotatables, Csp3, Lipinski) avec
  méthode/version/limites, recherche par sous-structure.
- **Combinaisons** : dossier 12 points, conclusion toujours « Résultat
  de screening informatique » / « Hypothèse à vérifier
  expérimentalement » ; score heuristique étiqueté « Score — méthode
  combinée » (jamais sous un en-tête « Tanimoto »).
- **Synergie** : Bliss, Loewe, HSA, ZIP sur données importées
  uniquement ; refus explicite si données insuffisantes ; SVG +
  interprétation prudente.
- **Docking** : binaire Vina **absent de cet environnement** → refus
  propre testé (« aucun score inventé ») ; validé réellement en v2.1.2
  (Vina 1.2.7 officiel, c-Abl 1IEP/imatinib, 5 poses, affinités
  reproductibles) ; `statut_moteur()` vérifié à chaque appel.
- **IA** : synthèse éphémère, jamais persistée comme fait, données
  privées exclues par défaut, désactivable (`WEBINTEL_AI_ENABLED`) ;
  SOURCES / DONNÉES UTILISÉES / HYPOTHÈSES / LIMITES affichées.
- **Audit** : simulations immuables (qui, quand, SHA-256, entrées,
  paramètres, sorties, moteur/version), rejeu append-only.

## 9. Sécurité

Clé API (mode serveur), sessions UI par cookie signé (`SECRET_KEY` —
**défaut dev à changer**), cloisonnement projets (403 testé),
rate limits (60 req/60 s API ; 5 logins/300 s), **anti-SSRF TOCTOU**
(`PinnedHTTPTransport`, DNS-rebinding testé), **CSP stricte**
(`script-src 'self'`, `frame-ancestors 'none'`, `X-Frame-Options:
DENY`), fichiers bruts confinés + SHA-256 + 200 Mo max, uploads
inspectés. Revue : `docs/SECURITY.md`.

## 10. Tests — chiffres mesurés le 2026-09-23

Suite complète exécutée dans cet environnement (Python 3.12,
**RDKit 2026.03.6 présent**, **Meeko présent**, **Vina absent**,
SQLite local, niveau SCIENCE) :

| Mesure | Valeur |
|---|---|
| **Passés** | **1090** |
| Ignorés | 17 |
| Échoués | **0** |
| Durée | ~300 s |

Détail des 17 ignorés (relevé avec `-rs`) : 8 liés à PostgreSQL
(7 tests PG + 1 gate `WEBINTEL_REQUIRE_PG_TESTS` — aucun serveur PG dans
cet environnement), 8 réservés au mode serveur (`REQUIRE_API_KEY=1`),
1 test de docking réel (binaire `vina` absent du PATH).
**Zéro régression** par rapport à la baseline v2.2.0 (1068/17/0).

Validation PostgreSQL (chantier v2.3.0, vrai serveur **18.6**,
binaires embarqués `pgserver`) : 12/12 `scripts/test_postgres.py`,
8/8 tests dédiés (gate `WEBINTEL_REQUIRE_PG_TESTS=1`), 22/22 tests du
chantier — concurrence 8 threads → idempotence vérifiée (langues,
noms vernaculaires, vouchers, échantillons, versions d'étude).

Autres mesures vérifiées : **52 tables**, index FK 100 % (créés
idempotents, constatés sur la base réelle), **215 chemins OpenAPI** /
261 opérations, pagination sur 11 listes, backup round-trip 12 tables
champ à champ (SQLite + PG).

## 11. Installation et déploiement

`install.sh` / `start.sh` (Linux/macOS) : Python 3.10+, `.venv`,
dépendances, SQLite initialisé — idempotents. `./start.sh` →
`http://127.0.0.1:8000` (`PORT=8080`, `WEBINTEL_HOST=0.0.0.0` en
option). Niveau SCIENCE : `scripts/install_science.sh`. Mode serveur :
`REQUIRE_API_KEY=1` + `SECRET_KEY` forte (+ PostgreSQL recommandé),
HTTPS via reverse-proxy exigé en production. Démo : `./scripts/demo.sh`,
`scripts/demo_science.py` (DÉMO RÉUSSIE : *Moringa oleifera*, 3
molécules réelles validées RDKit — quercétine, kaempférol, acide
chlorogénique — formules et InChIKey recalculés = déclarés).
Archive : `webintel-v2.3.0.zip`.

## 12. Limites honnêtes et roadmap

- **Pas de test de charge** PostgreSQL ni applicatif : validation
  fonctionnelle uniquement.
- **Binaire Vina non fourni** : docking réel validé en v2.1.2, refus
  propre sans lui ; affinités Vina = prédictions in silico, jamais des
  mesures d'efficacité.
- `install.bat`/`start.bat` **jamais exécutés sur un vrai Windows**.
- Fichier **LICENSE de l'application manquant** (`pyproject.toml`
  renvoie à un LICENSE inexistant) — à créer avant toute diffusion.
- Pas de chiffrement au repos (SQLite, fichiers bruts, ZIP de
  sauvegarde) : chiffrez le disque si données sensibles.
- **Aucun audit de sécurité tiers** ; rate limits = protection contre
  les abus basiques, pas contre un DDoS.
- **Ce que WebIntel ne fait pas** : aucune validation scientifique des
  contenus importés (la plateforme trace, elle ne certifie pas) ; les
  simulations sont des prédictions à confirmer expérimentalement ;
  Tanimoto ≠ similarité pharmacologique ; un score de docking n'est pas
  une mesure d'activité ; un usage traditionnel rapporté n'est pas une
  preuve clinique.
- Ce logiciel **n'est pas un dispositif médical**.
- Roadmap : exports BibTeX, langues supplémentaires au registre,
  connecteurs en lecture seule — sans jamais devenir une « usine à gaz ».
