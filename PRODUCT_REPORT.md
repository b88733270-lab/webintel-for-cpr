# WebIntel v3.0.2 — Rapport produit

Date : 2026-10-04 (v3.0.0), correctifs v3.0.1 puis v3.0.2 le 2026-10-07. Déclencheur : métaprompt produit de l'utilisateur
(31 sections) — transformer WebIntel en véritable plateforme
d'**intelligence Web généraliste, online-first**, utilisable sur
mobile et desktop, avec captures d'écran pour validation.

## 1. AUDIT — conservé / corrigé / ajouté / supprimé

**Conservé (moteur v1 + science v2, aucune destruction)** :
- Chaîne collecte → extraction → normalisation → déduplication →
  entités → observations par source → snapshots → diffs → événements →
  analyse → rapports/exports (crawler hybride, fetcher, registry
  d'entités, `SourceObservation`, moniteurs, alertes, scheduler).
- Anti-SSRF strict sur toute URL fournie par l'utilisateur (crawler,
  fetcher, robots, webhooks) — inchangé et retesté.
- Module science complet (plantes/composés/RDKit, connecteurs
  PubChem/GBIF/PubMed/ChEMBL/OpenAlex, seed de démarrage v2.4.0,
  ethnobotanique « USAGE TRADITIONNEL RAPPORTÉ », 5 niveaux de
  preuve) : relégué en **domaine spécialisé** dans la navigation,
  intégralement fonctionnel et testé.

**Corrigé** :
- La recherche principale ne voyait que la base locale : ajout d'une
  vraie recherche Web généraliste (voir §3).
- Navigation plate de ~25 liens : regroupée en sidebar thématique
  (Principal / Veille / Résultats / Domaine scientifique / Compte).
- Le lien « Comparaison » pointait vers les combinaisons science :
  il ouvre désormais la comparaison d'entités ; « Combinaisons »
  garde son entrée dans le domaine scientifique.
- Deux échecs de tests attrapés à l'intégration et corrigés :
  redirection par variable dans les collections (scan sécurité
  « aucune redirection ouverte ») et formulation de l'accueil.

**Ajouté** : abstraction Providers + fallback, écrans Sources /
Comparaison / Recherche Web, Favoris & Collections, Design System,
progression de recherche en 6 étapes, section « Services & clés API »
dans Paramètres, support `.env` minimal.

**Supprimé** : rien de fonctionnel. Le positionnement « plateforme
scientifique » cesse d'être le produit principal (décision produit de
l'utilisateur) ; aucun écran science n'a été retiré.

## 2. ARCHITECTURE

```
Interface (Jinja2, Design System) → routes UI/API
  → Orchestrateur (routes / services existants)
  → Recherche Web : web_service → registre Providers (fallback)
  → Collecte : HybridFetcher (HTTP/HTML/RSS/sitemap/JSON-LD/OG)
  → Extraction → Normalisation → Déduplication (registry + URL normalisée)
  → Entités → Observations par source → Snapshots → Diffs → Événements
  → Moniteurs/Alertes → Rapports/Exports → Historique local
```
Provenance : chaque valeur suit Entity → Source → Observation →
Timestamp → Snapshot (écran `/sources`). Une inférence n'est jamais
présentée comme un fait observé ; les écrans affichent les échecs
déclarés plutôt que des résultats inventés.

## 3. PROVIDERS + CLÉS API

Interface commune (`webintel/providers/base.py`) : capacités,
timeout, budget global (25 s), erreurs normalisées FR, statut exposé
sans jamais afficher la clé. Fallback automatique ordonné :
Brave → Tavily → SerpApi → Wikipedia → DuckDuckGo → RSS actualités →
NewsAPI → GitHub, déduplication par URL normalisée (paramètres de
pistage retirés).

**Sans clé — fonctionne immédiatement** : Wikipedia (FR/EN),
DuckDuckGo Instant Answers, GitHub public, Google News RSS.

**Clés optionnelles** (le produit marche sans ; un provider sans clé
est simplement sauté) :

| Clé | Où l'obtenir | Où la mettre |
|---|---|---|
| `WEBINTEL_BRAVE_API_KEY` | brave.com/search/api | fichier `.env` à la racine du projet (copier `.env.example`) ou variable d'environnement |
| `WEBINTEL_TAVILY_API_KEY` | tavily.com | idem |
| `WEBINTEL_SERPAPI_API_KEY` | serpapi.com | idem |
| `WEBINTEL_NEWSAPI_API_KEY` | newsapi.org | idem |
| `WEBINTEL_GITHUB_TOKEN` (optionnel, quotas) | github.com/settings/tokens | idem |

Après ajout : redémarrer l'application. La page **Paramètres →
« Services & clés API »** affiche pour chaque service si la clé est
présente (« Configurée » / « Non configurée »), jamais sa valeur, et
aucune clé n'est saisie ni stockée dans l'interface.

**Vérifié en direct** (environnement de build) : recherche « Burkina
Faso » → 8 résultats Wikipedia réels en 795 ms via `GET
/api/web/recherche`, affichés dans l'écran `/recherche-web`
(voir captures). Correctif intégré au passage : vérification TLS sur
le magasin de certificats système, indispensable derrière un proxy
qui intercepte TLS (nom d'hôte toujours vérifié).

## 4. DESIGN SYSTEM

`webintel/ui/static/style.css` restructuré en tokens CSS (couleurs
sobre « recherche/données », typographie, espacements, rayons,
ombres) + composants : boutons, champs, cartes/KPI, badges, tableaux
responsive-cartes, timeline, progression 6 étapes, modales/drawer/
bottom-sheet, squelettes, états vides et erreurs avec Réessayer,
spinners, toasts, bandeau hors-ligne. Accessibilité : focus visible,
contraste, zones tactiles ≥ 44 px, ARIA sur la progression.

- Desktop ≥1024 px : sidebar sombre 264 px, multi-colonnes, densité.
- Tablette 768–1023 px : sidebar icônes 76 px.
- Mobile <768 px : topbar compacte + **bottom navigation** fixe
  (Accueil, Recherche, Entités, Alertes, Plus), tableaux en cartes.

## 5. ÉCRANS LIVRÉS

Refaits au Design System : Accueil (recherche principale, raccourcis,
recherches récentes/favoris en états vides propres), Recherche
(progression Compréhension → Découverte → Collecte → Analyse →
Vérification → Synthèse), Résultats, Entités (+ détail), Analyse URL,
Événements (timeline), Monitoring, Alertes, Rapports/Exports,
Paramètres (+ clés API). **Nouveaux** : Recherche Web, Sources,
Comparaison, Favoris/Collections. Les écrans science héritent du
même socle (base + CSS). Captures desktop 1366 px et mobile 390 px
des écrans principaux : dossier `webintel-v3-ecrans/` (données de
démonstration explicitement marquées « démo » ; résultats Recherche
Web = vraies données Wikipedia obtenues en direct).

## 6. TESTS

- Baseline v2.4.0 : 1171 passed / 0 failed / 17 skipped.
- Nouveaux tests : providers & fallback (17), collections (15),
  écrans v3 (4) — dont E2E Accueil→recherche→résultats→rapport→
  sauvegarde→historique couverts par les parcours existants + nouveaux.
- Suite complète v3.0.2 (après correctif /tableau-de-bord 500) : **1210 passed /
  0 failed / 17 skipped** en ~128 s — zéro régression, +2 tests de régression
  (v3.0.1 : 1208 passed).
- Correctif v3.0.2 : `/tableau-de-bord` répondait 500 (détecteur
  `taxons_ambigus` plantant sur `external_ids["gbif"]` chaîne) ; lecteur
  GBIF robuste + clé « statut » lue correctement ; audit statique des
  23 écrans UI : 38 liens internes, 0 problème.
- Ruff : passé sur les nouveaux modules ; les remarques restantes
  sont les idiomes déjà présents dans le code historique (défauts
  `Depends` FastAPI, tris d'imports), pas des défauts fonctionnels.

## 7. LIMITES HONNÊTES

- Pas d'application native Android/iOS ni d'emballage desktop
  (Electron/Tauri) : la même plateforme Web responsive sert mobile et
  PC via le navigateur ; l'expérience est adaptée (bottom navigation,
  sidebar), pas un produit natif installé.
- La recherche Web dépend du réseau du poste : dans l'environnement
  de build, seuls les hôtes autorisés par le proxy répondent
  (Wikipedia vérifié en direct) ; GitHub y a expiré en timeout.
  Sur un poste en accès Internet standard, le chemin direct suffit.
- DuckDuckGo Instant Answers répond surtout aux sujets
  encyclopédiques ; GitHub ne cherche que des dépôts.
- `/tableau-de-bord` (ancien écran chercheur science) peut échouer
  sur certaines données démo historiques (bug préexistant
  `external_ids` en chaîne dans le module qualité) : hors périmètre
  v3, les tests de la suite passent.
- L'écran Rapport reste l'écran Exports existant (génération de
  fichiers sourcés) ; pas de nouveau moteur de rapport narratif.
