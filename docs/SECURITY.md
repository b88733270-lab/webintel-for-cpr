# Sécurité — WebIntel v2.3.0

> **Ce qui est VRAI** : ce document décrit les surfaces **réellement
> implémentées et testées**. Il ne déclare jamais « sécurisé » au-delà des
> surfaces couvertes par les tests. Les limites honnêtes sont en §7.

## 1. Authentification et isolation

- **Clé API** en mode serveur (`REQUIRE_API_KEY=1`) ; en-tête ou paramètre
  selon la configuration. En mode local (défaut), pas de clé —
  **usage mono-utilisateur de confiance uniquement**.
- **UI** : login via clé API, **cookie de session signé** (`SECRET_KEY`).
- **Isolation stricte par projet** : chaque accès vérifie le projet de la
  requête — accès croisé → **403** (testé : `test_project_isolation.py`).
- Les sessions du wizard sont rattachées à `project_id` + `owner` : l'UUID
  seul ne suffit pas (accès croisé → 403).

## 2. Rate limits (réels, configurables)

| Surface | Défaut | Variable |
|---|---|---|
| Requêtes API | 60 requêtes / 60 s | `API_LIMITE_REQUETES` / `API_FENETRE_S` |
| Tentatives de login | 5 tentatives / 300 s | `LOGIN_MAX_TENTATIVES` / `LOGIN_FENETRE_S` |
| Activation globale | activé | `RATE_LIMIT_ENABLED=false` pour désactiver (tests) |

Testé : `test_robustesse_rate_limit.py`.

## 3. Anti-SSRF à connexion épinglée

**Ce qui est VRAI** (`webintel/security/pinned_transport.py`,
`webintel/security/ssrf.py`) : `PinnedHTTPTransport` **épinglé la connexion
à l'adresse IP validée** — suppression de la seconde résolution DNS, ce qui
ferme la faille TOCTOU / DNS-rebinding (le P1 remonté par l'audit
utilisateur en v1). Appliqué aux connecteurs, au fetcher, à robots.txt et
aux webhooks. Tests de régression DNS-rebinding inclus.

Garde-fous crawl : `FETCH_TIMEOUT_S` (20 s), `MAX_RESPONSE_BYTES`
(5 Mo), `MAX_REDIRECTS` (5), `OBEY_ROBOTS=true`, `MAX_CRAWL_PAGES`,
`MAX_CRAWL_DEPTH`, `MAX_CONCURRENCY`.

## 4. En-têtes HTTP et CSP stricte

**Ce qui est VRAI** (`webintel/main.py`) — positionnés sur chaque réponse :

```
Content-Security-Policy: default-src 'self'; script-src 'self';
  style-src 'self' 'unsafe-inline'; img-src 'self' data:;
  object-src 'none'; base-uri 'self'; form-action 'self';
  frame-ancestors 'none'
X-Frame-Options: DENY
```

- `script-src 'self'` **strict** : aucun JavaScript inline dans les
  templates (le JS vit dans `ui/static/js/`).
- `frame-ancestors 'none'` + `X-Frame-Options: DENY` : anti-clickjacking.
- `form-action 'self'` : les formulaires ne quittent jamais l'instance.

Testé : `test_robustesse_securite_web.py`.

## 5. Fichiers et uploads

- **Confinement** : `WEBINTEL_FICHIERS_BRUTS_DIR`
  (défaut `./webintel_data/fichiers-bruts`) — résolution canonique,
  symlinks résolus, sortie du répertoire **refusée**.
- **Taille max** : `WEBINTEL_FICHIER_BRUT_MAX_OCTETS` (défaut **200 Mo**).
- **Intégrité** : SHA-256 à l'import, vérifiable
  (`GET /api/science/terrain/analyses/{id}/integrite`).
- Inspection du contenu réel (ZIP déguisé en `.csv` détecté).
- Imports par paquets de 500 lignes (garde-fou mémoire) ; limite de
  fichier d'import : 10 Mo.
- Testé : `test_robustesse_uploads.py`.

## 6. Variables d'environnement sensibles

| Variable | Défaut | Recommandation |
|---|---|---|
| `SECRET_KEY` | `dev-secret-change-me` | **À changer impérativement** en mode serveur |
| `REQUIRE_API_KEY` | `0` | `1` dès exposition réseau |
| `DATABASE_URL` | `sqlite:///./webintel.db` | `postgresql://…` en mode serveur |
| `WEBINTEL_FICHIERS_BRUTS_DIR` | `./webintel_data/fichiers-bruts` | chemin dédié, sauvegardé |
| `WEBINTEL_FICHIER_BRUT_MAX_OCTETS` | `200000000` | ajuster selon disque |
| `WEBINTEL_AI_ENABLED` | `true` | `false` si aucune synthèse IA souhaitée |

Ne commitez jamais un `.env` contenant ces valeurs.

## 7. Limites honnêtes — lire avant de déployer

- **Audit partiel** : les surfaces ci-dessus sont testées ; cela ne fait
  pas de WebIntel un système « sécurisé » au sens d'un audit externe.
  **Aucun audit de sécurité tiers n'a été réalisé.**
- `SECRET_KEY` par défaut est public (dans le code) : en mode serveur sans
  changement, les sessions UI sont falsifiables.
- **Pas de chiffrement au repos** : la base SQLite et les fichiers bruts
  sont en clair — chiffrez le disque si les données sont sensibles.
- **Pas de test de charge** : les rate limits protègent des abus
  basiques, pas d'une attaque DDoS.
- Le mode local (`REQUIRE_API_KEY=0`, `127.0.0.1`) n'est sûr que sur une
  machine de confiance ; `WEBINTEL_HOST=0.0.0.0` expose l'instance au
  réseau local — activez alors `REQUIRE_API_KEY=1`.
- Les dépendances (FastAPI, RDKit…) suivent leurs propres cycles de
  sécurité : mettez à jour régulièrement (`pip install -U` dans `.venv`).
