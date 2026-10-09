# Modes de déploiement WebIntel

WebIntel fonctionne selon deux modes, pilotés par la variable
`REQUIRE_API_KEY` (lire `webintel/config/settings.py` pour le détail des
variables).

| | Mode local (défaut) | Mode serveur |
|---|---|---|
| `REQUIRE_API_KEY` | `false` | `true` |
| API | ouverte, sans clé | clé `X-API-Key` exigée |
| UI | ouverte, aucun login | login par clé API + cookie de session signé |
| `SECRET_KEY` | valeur par défaut autorisée | clé forte **obligatoire** (≥ 32 car., refusée au boot sinon) |
| Cookie de session | `HttpOnly`, `SameSite=Lax` | `HttpOnly` + `Secure` + `SameSite=Lax` |
| Usage | poste de travail, localhost | exposé sur un réseau / Internet |

---

## 1. Mode local simple (défaut)

### Variables

```bash
# Rien d'obligatoire. Les défauts suffisent :
#   REQUIRE_API_KEY=false  (défaut)
#   DATABASE_URL=sqlite:///./webintel.db  (défaut)
#   SECRET_KEY=dev-secret-change-me  (défaut, autorisé en local uniquement)
```

### Démarrage

```bash
cd ~/workspace/webintel   # ou depuis le zip décompressé
pip install -r requirements.txt
uvicorn webintel.main:app --host 127.0.0.1 --port 8000
# UI : http://127.0.0.1:8000 — API : http://127.0.0.1:8000/docs
```

### Ce qui est désactivé / assoupli en local

- aucune authentification (API et UI ouvertes) ;
- `SECRET_KEY` par défaut acceptée (elle ne sert qu'à signer d'éventuels
  cookies de session, inexistants en local) ;
- cookie de session **sans** `Secure` (HTTP en clair sur localhost) ;
- SQLite par défaut (base fichier, pas de serveur à installer).

### ⚠️ Avertissement

**Ne JAMAIS exposer le mode local sur Internet** (ni même sur un réseau
local non maîtrisé) : l'API et l'UI sont ouvertes à tout le monde, sans
aucun contrôle d'accès. Si l'instance doit être joignable depuis
l'extérieur, passez au mode serveur ci-dessous.

---

## 2. Mode serveur sécurisé

### Variables OBLIGATOIRES

```bash
REQUIRE_API_KEY=true
SECRET_KEY=<clé forte, ≥ 32 caractères aléatoires>
DATABASE_URL=postgresql://webintel:<mot-de-passe>@db:5432/webintel
```

- `REQUIRE_API_KEY=true` : active l'authentification sur l'API
  (`X-API-Key`) et l'UI (login + session).
- `SECRET_KEY` : signe les cookies de session UI (HMAC-SHA256). **Le
  démarrage ÉCHOUE (RuntimeError explicite) si la clé :**
  - vaut la valeur par défaut `dev-secret-change-me` ;
  - fait moins de 32 caractères ;
  - est une valeur manifestement faible (`secret`, `password`,
    `changeme`, `admin`, `123456`, `webintel`… — comparaison insensible
    à la casse, tirets/underscores ignorés).
- `DATABASE_URL` : PostgreSQL recommandé en production (SQLite convient
  pour une démo, pas pour un usage multi-utilisateur).

### Générer une clé forte

```bash
python -c "import secrets; print(secrets.token_hex(32))"
# → 64 caractères hexadécimaux (256 bits). Exemple d'usage :
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
```

Ne jamais commiter la clé dans le dépôt : variable d'environnement, fichier
`.env` non versionné, ou gestionnaire de secrets (Vault, gestionnaire de
secrets du PaaS…).

### Démarrage (exemple docker-compose)

Le `docker-compose.yml` fourni lance l'app + PostgreSQL. Pensez à poser
les variables ci-dessus dans l'environnement du service `web`. Vérifiez
le boot : sans `SECRET_KEY` valide, le conteneur refuse de démarrer et
le message d'erreur indique exactement quoi poser.

### Reverse-proxy TLS (obligatoire)

Le cookie de session porte le flag `Secure` en mode serveur : il n'est
envoyé par le navigateur qu'en HTTPS. **L'application doit donc toujours
être servie derrière un reverse-proxy TLS** (jamais uvicorn exposé
directement en HTTP).

Exemple minimal **Caddy** (TLS automatique via Let's Encrypt) :

```
webintel.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Exemple minimal **nginx** :

```nginx
server {
    listen 443 ssl;
    server_name webintel.example.com;

    ssl_certificate     /etc/letsencrypt/live/webintel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/webintel.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        # Headers lus par l'app (journalisation, URLs absolues) :
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
# Redirection HTTP -> HTTPS
server {
    listen 80;
    server_name webintel.example.com;
    return 301 https://$host$request_uri;
}
```

### Cookies sécurisés

En mode serveur, le cookie de session UI (`webintel_ui_session`, 24 h)
porte :

- `HttpOnly` : inaccessible au JavaScript (vol via XSS impossible) ;
- `Secure` : transmis uniquement en HTTPS ;
- `SameSite=Lax` : le navigateur ne l'envoie pas lors de requêtes
  cross-site initiées par un tiers (protection CSRF). `Lax` plutôt que
  `Strict` : les navigations top-level légitimes (lien externe, favori)
  restent connectées, et le flux login (POST + redirection 303 same-site)
  fonctionne ; le vecteur CSRF dangereux (requêtes cross-site en
  sous-ressource : formulaires/fetch d'une page attaquante) reste bloqué.

### Checklist de mise en production

- [ ] `REQUIRE_API_KEY=true` et `SECRET_KEY` forte posés (boot OK) ;
- [ ] `DATABASE_URL` PostgreSQL (sauvegardes configurées) ;
- [ ] HTTPS via reverse-proxy (Caddy/nginx ci-dessus), HTTP redirigé
      vers HTTPS ;
- [ ] uvicorn lié à `127.0.0.1` (ou réseau interne), jamais `0.0.0.0`
      exposé directement ;
- [ ] `ENABLE_SCHEDULER` : un seul worker l'exécute (si plusieurs
      réplicas, `ENABLE_SCHEDULER=false` sur les autres) ;
- [ ] clés API créées par projet, révocables (`/api` → gestion des clés) ;
- [ ] journaux surveillés (`LOG_LEVEL=INFO` minimum) ;
- [ ] mises à jour régulières des dépendances (`pip-audit` / Dependabot).

---

## 3. Récapitulatif des variables

| Variable | Défaut | Local | Serveur |
|---|---|---|---|
| `REQUIRE_API_KEY` | `false` | `false` | **`true`** |
| `SECRET_KEY` | `dev-secret-change-me` | défaut OK | **forte, ≥ 32 car. (boot refusé sinon)** |
| `DATABASE_URL` | `sqlite:///./webintel.db` | SQLite OK | PostgreSQL recommandé |
| `ENABLE_SCHEDULER` | `true` | `true` | `true` (un seul worker) |
| `LOG_LEVEL` | `INFO` | `INFO` | `INFO` (ou `WARNING`) |

Voir aussi : `docs/securite.md` (modèle de menaces), `docs/api.md`
(authentification `X-API-Key`).
