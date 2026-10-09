"""Configuration applicative.

`Settings` est un simple Pydantic `BaseModel` (pas de dépendance
`pydantic-settings`) : les valeurs sont lues depuis `os.environ` avec des
défauts sûrs pour le développement. `get_settings()` retourne un singleton
mis en cache (`reset_settings()` permet de le réinitialiser, utile en tests).

Mode serveur : `validate_server_config()` refuse les clés faibles.
refuse les `SECRET_KEY` faibles ou par défaut — l'application lève une
`RuntimeError` explicite au démarrage plutôt que de servir une instance
non sécurisée.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

_TRUE_VALUES = {"1", "true", "yes", "on", "oui"}


def _charger_dotenv() -> None:
    """Charge un fichier ``.env`` minimal (sans dépendance externe).

    Recherche ``.env`` à la racine du projet (dossier contenant
    ``pyproject.toml`` / ``requirements.txt``) puis dans le dossier
    courant. Format accepté : ``CLE=valeur`` par ligne, lignes vides et
    commentaires (``#``) ignorés, guillemets simples/doubles optionnels.
    Une variable déjà présente dans l'environnement n'est JAMAIS
    écrasée : l'environnement réel reste prioritaire. Appelé une seule
    fois à l'import, AVANT la définition des défauts de ``Settings``
    (dont les valeurs sont figées à la définition de la classe).
    """
    candidats: list[Path] = []
    racine = Path(__file__).resolve().parents[2]
    candidats.append(racine / ".env")
    candidats.append(Path.cwd() / ".env")
    vus: set[Path] = set()
    for chemin in candidats:
        try:
            resolu = chemin.resolve()
        except OSError:
            continue
        if resolu in vus:
            continue
        vus.add(resolu)
        try:
            lignes = resolu.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError):
            continue
        for ligne in lignes:
            texte = ligne.strip()
            if not texte or texte.startswith("#") or "=" not in texte:
                continue
            cle, _, valeur = texte.partition("=")
            cle = cle.strip()
            valeur = valeur.strip()
            if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in ("'", '"'):
                valeur = valeur[1:-1]
            if cle and cle not in os.environ:
                os.environ[cle] = valeur


_charger_dotenv()

# Valeur par défaut de développement de SECRET_KEY : interdite en mode serveur.
DEFAULT_SECRET_KEY = "dev-secret-change-me"

# Longueur minimale exigée pour SECRET_KEY en mode serveur (256 bits).
MIN_SECRET_KEY_LENGTH = 32

# Secrets manifestement faibles, refusés en mode serveur. Comparaison
# insensible à la casse, tirets/underscores/espaces ignorés. Cette liste
# n'est PAS un secret : ce sont des valeurs publiques à bannir.
_WEAK_SECRETS = frozenset({
    "secret",
    "password",
    "motdepasse",
    "changeme",
    "change-me",
    "changemoi",
    "admin",
    "administrateur",
    "root",
    "123456",
    "12345678",
    "qwerty",
    "azerty",
    "letmein",
    "webintel",
    "test",
    "testing",
    "default",
    "defaut",
    "key",
    "apikey",
    "api-key",
    "cleapi",
    "private",
    "public",
    "supersecret",
    "topsecret",
    "foobar",
    "dev",
    "development",
    "prod",
    "production",
})


def _get_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw.strip())


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return float(raw.strip())


class Settings(BaseModel):
    """Paramètres lus depuis l'environnement (contrat §12 d'architecture.md)."""

    DATABASE_URL: str = _get_str("DATABASE_URL", "sqlite:///./webintel.db")
    SECRET_KEY: str = _get_str("SECRET_KEY", "dev-secret-change-me")
    REQUIRE_API_KEY: bool = _get_bool("REQUIRE_API_KEY", False)
    ENABLE_SCHEDULER: bool = _get_bool("ENABLE_SCHEDULER", True)
    FETCH_TIMEOUT_S: int = _get_int("FETCH_TIMEOUT_S", 20)
    MAX_RESPONSE_BYTES: int = _get_int("MAX_RESPONSE_BYTES", 5_000_000)
    MAX_REDIRECTS: int = _get_int("MAX_REDIRECTS", 5)
    DEFAULT_UA: str = _get_str(
        "DEFAULT_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 WebIntel/1.0",
    )
    OBEY_ROBOTS: bool = _get_bool("OBEY_ROBOTS", True)
    CRAWL_DELAY_S: float = _get_float("CRAWL_DELAY_S", 1.0)
    MAX_CRAWL_PAGES: int = _get_int("MAX_CRAWL_PAGES", 1000)
    MAX_CRAWL_DEPTH: int = _get_int("MAX_CRAWL_DEPTH", 3)
    MAX_CONCURRENCY: int = _get_int("MAX_CONCURRENCY", 4)
    EXPORT_DIR: str = _get_str("EXPORT_DIR", "./webintel_data/exports")
    # Fichiers bruts de laboratoire (chromatogrammes, spectres…) :
    # SEUL ce répertoire est lisible via « rattacher un fichier brut ».
    # Tout chemin hors de cette racine est refusé (anti-oracle de
    # fichiers locaux — test adversarial ``test_fichier_brut``).
    FICHIERS_BRUTS_DIR: str = _get_str(
        "WEBINTEL_FICHIERS_BRUTS_DIR", "./webintel_data/fichiers-bruts"
    )
    FICHIER_BRUT_MAX_OCTETS: int = _get_int(
        "WEBINTEL_FICHIER_BRUT_MAX_OCTETS", 200_000_000
    )
    SCHEDULER_INTERVAL_S: int = _get_int("SCHEDULER_INTERVAL_S", 60)
    LOG_LEVEL: str = _get_str("LOG_LEVEL", "INFO")
    # Phase 11 — IA complètement désactivable : toute fonctionnalité
    # d'IA/générative est coupée quand ce flag est False. La valeur peut être
    # surchargée à l'exécution depuis l'interface (page « Paramètres »), qui
    # écrit un fichier d'override JSON (voir webintel/config/runtime.py).
    AI_ENABLED: bool = _get_bool("WEBINTEL_AI_ENABLED", True)
    # --- Rate-limiting HTTP (chantier robustesse) ---------------------------
    # Désactivable (tests) : RATE_LIMIT_ENABLED=false.
    RATE_LIMIT_ENABLED: bool = _get_bool("RATE_LIMIT_ENABLED", True)
    # Anti-brute-force sur POST /login : seuls les ÉCHECS comptent ;
    # un succès réinitialise le compteur du client.
    LOGIN_MAX_TENTATIVES: int = _get_int("LOGIN_MAX_TENTATIVES", 5)
    LOGIN_FENETRE_S: int = _get_int("LOGIN_FENETRE_S", 300)
    # Rate-limit générique des endpoints API coûteux (simulations, docking,
    # imports, exports PDF/ZIP) : requêtes par fenêtre glissante et par IP.
    API_LIMITE_REQUETES: int = _get_int("API_LIMITE_REQUETES", 60)
    API_FENETRE_S: int = _get_int("API_FENETRE_S", 60)
    # --- Providers de recherche Web généraliste (v3.0.0) -------------------
    # Clés API OPTIONNELLES : sans clé, les providers sans clé (Wikipedia,
    # DuckDuckGo, GitHub public, RSS) restent fonctionnels par défaut et
    # les providers à clé sont simplement sautés (jamais d'exception qui
    # casse la recherche). Les clés se posent dans `.env` à la racine du
    # projet ou dans l'environnement — jamais dans le code ni les logs.
    WEBINTEL_BRAVE_API_KEY: str = _get_str("WEBINTEL_BRAVE_API_KEY", "")
    WEBINTEL_TAVILY_API_KEY: str = _get_str("WEBINTEL_TAVILY_API_KEY", "")
    WEBINTEL_SERPAPI_API_KEY: str = _get_str("WEBINTEL_SERPAPI_API_KEY", "")
    WEBINTEL_NEWSAPI_API_KEY: str = _get_str("WEBINTEL_NEWSAPI_API_KEY", "")
    # Token GitHub OPTIONNEL (recherche publique sans token possible ;
    # le token ne sert qu'à relever les quotas d'API).
    WEBINTEL_GITHUB_TOKEN: str = _get_str("WEBINTEL_GITHUB_TOKEN", "")
    # Timeouts providers : par requête et budget global d'une recherche
    # Web complète (tous providers confondus).
    WEBINTEL_PROVIDER_TIMEOUT_S: float = _get_float("WEBINTEL_PROVIDER_TIMEOUT_S", 10.0)
    WEBINTEL_PROVIDER_BUDGET_S: float = _get_float("WEBINTEL_PROVIDER_BUDGET_S", 25.0)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Retourne le singleton `Settings` (construit une seule fois)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Réinitialise le singleton (utile en tests pour changer d'environnement)."""
    global _settings
    _settings = None


def _normalize_secret(secret: str) -> str:
    """Normalise un secret pour la comparaison aux valeurs faibles."""
    return (
        secret.strip().lower()
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
    )


def validate_server_config(settings: Settings) -> None:
    """Valide la configuration pour le mode serveur.

    En mode local : aucune exigence, le mode reste simple et sans friction.

    En mode serveur : `SECRET_KEY` doit être une valeur forte
    (≥ 32 caractères, ni la valeur par défaut, ni une valeur de la liste
    des secrets faibles). Sinon, lève `RuntimeError` avec un message
    explicite indiquant comment générer une clé sûre.
    """
    server_mode = bool(settings.REQUIRE_API_KEY)
    if not server_mode:
        return
    secret = settings.SECRET_KEY or ""
    howto = (
        "Posez une clé forte : `SECRET_KEY=$(python -c "
        "\"import secrets; print(secrets.token_hex(32))\")` "
        "(au minimum 32 caractères aléatoires)."
    )
    if secret == DEFAULT_SECRET_KEY:
        raise RuntimeError(
            "Configuration serveur invalide : SECRET_KEY vaut la valeur par "
            f"défaut ({DEFAULT_SECRET_KEY!r}). Une instance exposée avec ce "
            f"secret permettrait de forger des sessions UI. {howto}"
        )
    if len(secret) < MIN_SECRET_KEY_LENGTH:
        raise RuntimeError(
            "Configuration serveur invalide : SECRET_KEY fait "
            f"{len(secret)} caractère(s), minimum {MIN_SECRET_KEY_LENGTH} "
            f"requis (256 bits). {howto}"
        )
    if _normalize_secret(secret) in _WEAK_SECRETS:
        raise RuntimeError(
            "Configuration serveur invalide : SECRET_KEY est une valeur "
            "trop prévisible (mot de la liste des secrets faibles). "
            f"{howto}"
        )
