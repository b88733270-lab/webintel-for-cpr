# WebIntel — Sécurité

Ce document décrit les protections mises en œuvre (v1) et la checklist de
durcissement pour la production. Référence d'implémentation :
`webintel/security/` (`ssrf.py`, `robots.py`, `limits.py`).

## 1. Protection anti-SSRF (`security/ssrf.py`)

`validate_url(url) -> str` est appelée **avant chaque requête sortante**
(analyse, crawl, moniteur, webhook) et **re-appliquée après chaque
redirection**. Elle lève `SecurityError` (→ HTTP 422 côté API, message FR) si :

- **schéma** différent de `http`/`https` (`ftp://`, `file://`, etc. refusés) ;
- **userinfo** présente (`http://user:pass@hote/` refusée d'office) ;
- **hôte vide** ou **blocklisté** : `localhost`, `*.localhost`,
  `169.254.169.254` (métadonnées cloud), `metadata.google.internal`,
  `metadata.google.com`, `instance-data`, suffixes `.internal`, `.local`
  (liste surchargeable via `BLOCKED_SUFFIXES`) ;
- **résolution DNS dangereuse** (anti *DNS rebinding*) : l'hôte est résolu
  via `socket.getaddrinfo` **avant** la requête et **toutes** les adresses
  renvoyées sont validées avec `ipaddress` — privée, loopback, link-local,
  multicast, réservée ou non spécifiée → refus.

Le fetcher HTTP (`crawler/fetcher.py`) suit les redirections **manuellement**
(`follow_redirects=False`) : chaque en-tête `Location` est résolu contre
l'URL courante puis revalidé par `validate_url` (limite : `MAX_REDIRECTS=5`).
Une redirection vers une cible interne est donc bloquée, même si l'URL de
départ était saine.

Tests : `tests/test_security_ssrf.py` (localhost, 127.0.0.1, 169.254.169.254,
`[::1]`, IP privées, userinfo, schéma ftp, DNS rebinding simulé).

## 1.1 Épinglage DNS anti-TOCTOU (`security/pinned_transport.py`)

**Problème (P1, corrigé en v2.1)** : `validate_url()` vérifiait que le DNS
résolvait vers une IP publique, mais httpx effectuait ensuite SA PROPRE
résolution au moment de la connexion. Entre les deux, un DNS menteur
pouvait rebasculer vers une IP privée (DNS rebinding / TOCTOU).

**Correction** : `PinnedHTTPTransport(httpx.HTTPTransport)` ne résout le
nom d'hôte qu'UNE seule fois par requête, via `ssrf.resoudre_ip_epinglee()`
(toutes les adresses validées), puis se connecte directement à l'IP épinglée
en préservant :

- l'en-tête `Host` d'origine COMPLET (nom + port non standard éventuel,
  ex. `example.org:8443` — routage par hôte virtuel) ;
- le SNI TLS d'origine (extension httpcore `sni_hostname`) ;
- la vérification du certificat contre le nom d'hôte d'origine (et non
  contre l'IP épinglée).

La réponse exposée à l'appelant référence la requête d'ORIGINE (URL avec le
nom d'hôte), jamais l'URL réécrite vers l'IP : ni les logs, ni
l'historique de redirections, ni les cookies ne voient l'IP interne.

Appliqué partout où une URL validée est récupérée : fetcher
(`crawler/fetcher.py`), robots.txt (`security/robots.py`), connecteurs
(`science/connecteurs/base.py` + `tester_statut()` des plugins) et
webhooks (`alerts/dispatcher.py`).

Tests de régression : `tests/test_ssrf_pinning.py` — DNS simulé qui
bascule public → privé entre validation et connexion (bloqué, aucune
requête ne part vers l'IP privée), réécriture d'URL vérifiée (IP épinglée
+ Host/SNI préservés), IP littérale non réécrite, refus si toutes les
adresses ne sont pas publiques.

**Redirections automatiques** : le transport s'applique à chaque requête,
y compris celles suivies automatiquement par httpx. Chaque cible est donc
revalidée ET re-épinglée :

- `valider_url_sans_dns()` (schéma, userinfo, blocklist d'hôtes — SANS
  DNS) est appliquée à chaque requête : une redirection vers un hôte
  blocklisté (ex. `*.internal`) est refusée même si `validate_url()` n'a vu
  que l'URL d'origine et même si l'hôte se résoudrait vers une IP publique ;
- `resoudre_ip_epinglee()` revalide la résolution au moment de la
  connexion (anti-TOCTOU sur chaque saut) ;
- après une redirection RELATIVE (même origine), l'hôte logique d'origine
  est restauré (Host, SNI, URL exposée) via un marqueur d'extension
  interne — l'appelant ne voit jamais l'IP épinglée, même dans
  `response.history`.

**Limites résiduelles (honnêtes)** :

- si un proxy HTTP est configuré (`trust_env=True` + `*_PROXY`), c'est le
  proxy qui résout le nom : l'épinglage ne s'applique qu'aux connexions
  directes (`client_http_sure()` force `trust_env=False`) ;
- la validation porte sur la résolution au moment de la requête ; chaque
  requête re-épingle (un DNS légitime à IP changeante reste accepté) ;
- les IP littérales ne passent par aucun DNS : revalidées (publiques
  uniquement), rien à épingler ;
- `validate_url()` seule (sans le transport) ne suffit toujours pas —
  tout nouveau code sortant DOIT passer par `PinnedHTTPTransport`,
  `client_http_sure()` ou `requete_sure()`.

## 2. Validation des webhooks (`alerts/dispatcher.py`)

Le canal `webhook` revalide l'URL de destination avec `validate_url` **juste
avant** l'envoi (`httpx.post`, timeout 10 s) : impossible d'utiliser une
alerte pour sonder le réseau interne. En cas de refus ou d'échec réseau,
l'alerte est persistée avec `status="failed"` et le motif en clair.

## 3. Respect de robots.txt (`security/robots.py`)

`RobotChecker` récupère `/robots.txt` (timeout court, un seul fetch par
domaine, cache mémoire) et `can_fetch(url, user_agent)` est consulté avant
chaque page d'un crawl. `OBEY_ROBOTS=true` par défaut ; désactivable par
projet via les `limits` du crawl (choix tracé en log).

## 4. Limites applicatives (`security/limits.py`, `config/settings.py`)

| Limite | Défaut | Rôle |
|---|---|---|
| `MAX_RESPONSE_BYTES` | 5 000 000 | coupe la lecture HTTP au-delà (streaming) |
| `FETCH_TIMEOUT_S` | 20 | timeout de chaque requête |
| `MAX_REDIRECTS` | 5 | plafond de redirections suivies |
| `MAX_CRAWL_PAGES` | 1000 | borne stricte du crawl (garantie testée) |
| `MAX_CRAWL_DEPTH` | 3 | profondeur BFS maximale |
| `MAX_CONCURRENCY` | 4 | threads de crawl simultanés |
| `CRAWL_DELAY_S` | 1.0 | politesse entre deux requêtes |

Toutes sont surchargeables par variables d'environnement.

## 5. Clés API (`api/deps.py`, `database/models.py`)

- Stockage : **hash SHA-256** uniquement (`key_hash`) + préfixe (8 car.)
  pour l'identification ; la clé en clair n'est **affichée qu'une seule fois**
  à la création.
- Vérification : en-tête `X-API-Key`, comparaison du hash, clé révoquée →
  refus.
- **Isolation stricte (v1.1, P0-1)** : `require_project_access(api_key, project_id)`
  → **403** si la clé n'appartient pas au projet demandé. Contrôle effectué
  AVANT toute lecture/écriture (la 403 prime sur la 404 : on ne révèle pas
  l'existence des projets étrangers). `scoped_project_id()` cloisonne les
  listes et créations au projet de la clé par défaut. Couverture : projets,
  clés, analyze, crawls (+ pilotage des jobs), entités (+ historique), moniteurs,
  événements, alertes, recherche, exports (création + téléchargement — contrôle
  avant vérification du fichier), `/api/usage`. `POST /api/projects` avec une
  clé → 403 (bootstrap via `scripts/create_api_key.py` uniquement).
- Mode dev (`REQUIRE_API_KEY=false`, défaut) : bypass avec log debug —
  **à passer à `true` en production**.
- Quotas : schéma `quotas` par clé (`{"max_pages_day": N, "max_crawls": M}`),
  enforcement de base en v1 (détaillé en v2, cf. roadmap).

## 5bis. Authentification de l'interface web (v1.1.1, mode serveur)

Deux modes (`REQUIRE_API_KEY`) :

- `false` (défaut, usage local) : UI **ouverte**, `/login` redirige vers `/`.
- `true` (production) : **toute** route UI métier exige une session valide,
  sinon **303 vers `/login`** (pages : `/`, `/analyser`, `/crawls`,
  `/entites`, `/moniteurs`, `/alertes`, `/evenements`, `/recherche`,
  `/exports`, détail entité/crawl…).

Fonctionnement (`ui/auth.py`, `ui/routes.py`) :

- **Login** : `POST /login` avec la clé API ; vérification par comparaison
  du SHA-256 contre le store. La clé en clair n'est **ni persistée, ni
  loggée, ni placée dans l'URL ou les templates**.
- **Cookie de session** `webintel_ui_session` : charge utile
  `key_id:project_id:exp` signée HMAC-SHA256 avec `SECRET_KEY` ;
  `HttpOnly`, `SameSite=Lax`, expiration 24 h. La session ne contient
  **que** l'identifiant de clé, l'identifiant de projet et l'expiration —
  aucun secret.
- **Vérification par requête** : signature + expiration contrôlées, puis
  **relecture de la clé en base** — une clé révoquée (ou supprimée) invalide
  immédiatement les sessions existantes ; le projet est imposé par la clé
  (même cloisonnement que l'API : une session ne voit que son projet).
- **Sortie** : `GET /logout` purge le cookie (expiration immédiate).
- **Exports** : le téléchargement `GET /api/exports/{id}/download` accepte
  la session UI signée en plus de `X-API-Key` (`api_key_or_ui_session()`),
  pour un usage depuis le navigateur.
- **Limites connues** : `Secure` non forcé par l'application — exiger HTTPS
  via le reverse proxy en production (cookie volé sur HTTP = session volée) ;
  pas de rotation de `SECRET_KEY` sans invalidation des sessions ; pas de
  limite de tentatives sur `/login` (durcissement v2 : rate-limit,
  verrouillage).

Tests : `tests/test_ui_auth.py` — redirection sans session, clé invalide,
cookie `HttpOnly`/`SameSite=Lax` sans clé en clair, session falsifiée et
expirée rejetées, révocation, logout, isolation projet de l'UI.

## 7. Revue de sécurité — Phases 13–15 (2026-09-22)

### 7.1 Journal d'audit scientifique immuable

Chaque exécution de simulation enregistre (table `simulations`) : **qui**
(`executed_by` — `clé:<préfixe>` en mode serveur, `ui:<préfixe>` depuis
l'interface, `local` en mode local ; jamais la clé en clair), **quand**
(`run_at`), **empreinte SHA-256 des entrées** (`input_hash`), entrées
normalisées (`inputs`, rejouabilité), paramètres, sorties, version du
moteur. Immuabilité garantie :

- aucune route de suppression n'existe pour les simulations ;
- `PATCH /science/lab/simulations/{id}` refuse (409) toute écriture sur
  les colonnes d'audit (`CHAMPS_SIMULATION_IMMUTABLES` dans
  `webintel/science/lab.py`) ;
- une correction = une nouvelle exécution (bouton « Rejouer » /
  `POST /science/simulations/{id}/rejouer`).

Tests : `tests/test_simulations_audit.py`.

### 7.2 Secrets — résultat de la revue

- Aucune clé/secret en clair dans le code : `docker-compose.yml` exige
  `POSTGRES_PASSWORD` via l'environnement (aucune valeur par défaut) ;
- `SECRET_KEY` par défaut refusée en mode serveur
  (`validate_server_config`, chantier 0.3) ;
- clés API stockées en SHA-256 (`key_hash` + préfixe), affichées une
  seule fois à la création ; sessions UI signées HMAC sans secret dans
  la charge utile ; aucune clé dans les logs, les URL ou les templates
  (vérifié par revue — `grep` sur `webintel/`).

### 7.3 Timeouts et bornes réseau (tous les appels sortants)

| Appelant | Timeout | Bornes |
|---|---|---|
| Crawler (`crawler/fetcher.py`) | `FETCH_TIMEOUT_S` (20 s) | `MAX_RESPONSE_BYTES` (5 Mo), `MAX_REDIRECTS=5` revalidés |
| Webhooks (`alerts/dispatcher.py`) | 10 s | URL revalidée juste avant l'envoi |
| robots.txt (`security/robots.py`) | court (configurable) | 1 fetch/domaine, cache mémoire |
| Connecteurs (`science/connecteurs/base.py`) | 10 s, 2 retries max | cache 24 h, URL revalidée par `validate_url` (Phase 13) |

Réseau indisponible → erreur explicite (`ConnecteurIndisponible` →
HTTP 503), jamais de faux résultat.

### 7.4 Points restants (limites honnêtes)

- **TOCTOU anti-SSRF (P1)** : CORRIGÉ en v2.1 (§1.1 ci-dessus —
  `PinnedHTTPTransport`, tests `tests/test_ssrf_pinning.py`). Limites
  résiduelles : proxy explicite (résolution par le proxy), voir §1.1.
  Pour une exposition Internet, un filtrage réseau sortant au niveau
  infra reste recommandé en défense en profondeur.
- **robots.txt** : `follow_redirects=True` ; chaque redirection est
  désormais re-épinglée et re-validée par le transport (§1.1), mais le
  contenu reste parsé comme texte (jamais exécuté).
- **`/login`** : pas de rate-limit ni de verrouillage après échecs
  répétés (durcissement v2) ; exiger HTTPS via reverse-proxy en
  production (cookie de session).

## 7bis. Audit adversarial — chantier SÉCURITÉ P0 (2026-09-23)

Audit par tests adversariaux (attaquant muni d'une clé API valide) :
`tests/test_security_adversarial_idor.py` (22 tests : IDOR sur études,
synergie, docking/jobs, laboratoire, terrain, wizard, exports, collectes)
et `tests/test_security_adversarial_injection.py` (27 tests : SQLi, XSS
stocké/réfléchi, injection de commande, traversée de chemin, fuite
GPS/identités, redirections ouvertes, divulgation d'erreurs).

### Failles corrigées

1. **Oracle de fichiers locaux via « fichier brut » (P0/P1)** —
   `POST /api/science/terrain/analyses/{id}/fichier` acceptait un chemin
   arbitraire, lisait le fichier et renvoyait son SHA-256 : un utilisateur
   authentifié pouvait sonder l'existence et le contenu de tout fichier
   lisible par le processus serveur (`/etc/passwd` → 200). **Corrigé** :
   confinement strict au répertoire `WEBINTEL_FICHIERS_BRUTS_DIR`
   (résolution canonique `realpath` : `..` et liens symboliques
   neutralisés), taille maximale `WEBINTEL_FICHIER_BRUT_MAX_OCTETS`,
   messages d'erreur sans répercussion du chemin (403 hors racine, 404
   dans la racine). `webintel/science/terrain.py`
   (`_chemin_brut_autorise`), `webintel/config/settings.py`.

2. **Écrasement inter-projets des fiches pharmacognosie (P1)** — le
   `get_or_create` sur le seul `plant_id` permettait au projet B
   d'écraser silencieusement la fiche du projet A (fausse attribution de
   provenance). **Corrigé** : `PermissionError` → 403 si la fiche
   appartient à un autre projet. De plus la **lecture** respecte
   désormais `niveau_confidentialite` : une fiche non `public` d'un autre
   projet n'est pas divulguée (réponse `null` honnête, pas de 403
   révélateur). `webintel/science/terrain.py`
   (`definir_pharmacognosie`, `lire_pharmacognosie`), routeurs API et UI.

3. **Brouillons du wizard lisibles entre utilisateurs (P2)** —
   `_charger_session` ne vérifiait jamais `WizardSession.owner` : un autre
   membre du même projet muni du `session_id` (présent dans l'URL)
   pouvait lire/modifier le brouillon. **Corrigé** : contrôle du
   propriétaire (`_identite_ui`) à chaque chargement, 403 « appartient à
   un autre utilisateur ». `webintel/ui/chercheur_etudes_routes.py`.

4. **Contrôle d'accès après modification (durcissement)** —
   `PATCH /science/lab/observations/{id}` et
   `PATCH /science/lab/simulations/{id}` modifiaient l'objet AVANT de
   vérifier le projet (sûr par rollback, mais fragile). **Corrigé** :
   chargement + contrôle d'accès strict AVANT toute écriture.
   `webintel/api/routers/science_lab.py`.

### Surfaces vérifiées saines (preuves par les tests)

- **SQL** : aucun SQL brut (`text()`/`execute()`) dans le code applicatif ;
  ORM paramétré partout ; payloads d'injection → 200/400/422 sans fuite.
- **XSS** : autoescape Jinja actif, aucun `| safe` sur données utilisateur
  (les 3 usages portent sur des SVG dont les textes sont échappés par
  `_echapper`) ; payloads stockés/réfléchis → échappés en HTML.
- **Commande** : aucun `shell=True` / `os.system` / `eval` ; Vina via
  `subprocess.run(argv_fixe, shell=False)` (vérifié par analyse AST).
- **Uploads** : import `.xlsx` limité à 10 Mo, garde ZIP bomb / membres /
  traversée ; photos de collecte : noms UUID, allowlist d'extensions,
  10 Mo max ; nom de fichier d'import neutralisé par `basename`.
- **SSRF** : `PinnedHTTPTransport` (anti-TOCTOU/DNS-rebinding) sur
  connecteurs, fetcher, robots et webhooks (`alerts/dispatcher.py`).
- **GPS/informateurs** : l'export public n'inclut que les enregistrements
  `public` et ne divulgue jamais GPS précis ni identités (testé).
- **CSRF** : pas de jeton dédié, mais architecture non vulnérable —
  API sur en-tête `X-API-Key` (non envoyée par le navigateur), UI sur
  cookie `SameSite=Lax` + `HttpOnly` (+ `Secure` en mode serveur), aucun
  endpoint à effet de bord acceptant le cookie.
- **Cookies** : session UI signée HMAC-SHA256 (`compare_digest`),
  expiration, `HttpOnly`, `SameSite=Lax`, `Secure` en mode serveur.
- **CORS** : aucun middleware CORS (défaut fermé).
- **Redirections** : aucune cible utilisateur (pas de `next` externe).
- **Erreurs** : pas de traceback ni chemins internes dans les réponses.
- **Rate limits** : login anti-brute-force (verrouillage temporaire),
  `API_LIMITE_REQUETES` global et `limite_debit_couteux` sur les
  endpoints coûteux (docking, exports, imports).

### Limites honnêtes restantes

- Unicité actuelle d'une fiche pharmacognosie par plante (tous projets
  confondus) : un second projet ne peut pas créer sa propre fiche pour
  la même plante (403 explicite). Une contrainte composite
  `(project_id, plant_id)` serait la solution structurelle (migration à
  prévoir).
- Le durcissement CSRF repose sur `SameSite=Lax` (navigateurs modernes).
- `/login` : le verrouillage anti-brute-force existe désormais
  (`LOGIN_MAX_TENTATIVES`) — §7.4 ci-dessus est obsolète sur ce point.
- Aucune revendication « application sécurisée » : les garanties
  ci-dessus sont celles couvertes par les tests cités.

## 8. Checklist production (mise à jour Phases 13–15)

- [ ] `SECRET_KEY` fort et unique (jamais la valeur `dev-secret-change-me`) —
  **obligatoire** en mode serveur : il signe les cookies de session UI.
- [ ] `REQUIRE_API_KEY=true` + clés créées par projet, stockées hors du repo.
- [ ] `DATABASE_URL=postgresql://…` (SQLite réservé au dev ; voir
      `docker-compose.yml`).
- [ ] Reverse proxy HTTPS devant uvicorn (TLS non géré par l'application).
- [ ] `EXPORT_DIR` sur un volume persistant (sinon exports perdus au redémarrage).
- [ ] `WEBINTEL_FICHIERS_BRUTS_DIR` : racine confinée des « fichiers bruts »
      de terrain — volume persistant, jamais un répertoire système.
      Taille maximale : `WEBINTEL_FICHIER_BRUT_MAX_OCTETS`
      (défaut 200 Mo).
- [ ] `LOG_LEVEL` adapté (`INFO`/`WARNING`) ; logs agrégés par l'infra.
- [ ] `POSTGRES_PASSWORD` (et tout secret) via variables d'environnement ou
      gestionnaire de secrets — **jamais** dans le code ni dans git.
- [ ] Sauvegarde régulière de la base (snapshots append-only : la perte de
      l'historique est irréversible).
- [ ] Revue des `limits` (taille max, concurrence, délais) selon la capacité
      du serveur et la politesse de crawl souhaitée.
- [ ] TOCTOU anti-SSRF corrigé en v2.1 (§7.4) ; pour une exposition
      Internet, ajouter un filtrage réseau sortant au niveau infra en
      défense en profondeur ; `WEBINTEL_AI_ENABLED` désactivé
      si l'IA n'est pas souhaitée (page Paramètres).
- [ ] Données privées du laboratoire : vérifier `inclure_prive=False` par
      défaut sur les exports automatisés ; les exports incluant le privé
      exigent une action explicite et sont tracés.
