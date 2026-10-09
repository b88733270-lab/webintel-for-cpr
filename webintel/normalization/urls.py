"""Normalisation d'URL : `normalize_url()`, `canonical_url()`, `same_domain()`.

- `normalize_url` : hôte en minuscules, fragment supprimé, slash final
  supprimé sauf à la racine, schéma en minuscules.
- `canonical_url` : idem + suppression des paramètres de tracking
  (utm_*, fbclid, gclid...) et tri des paramètres restants (dédup de crawl).
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_TRACKING_PARAMS = frozenset(
    {
        "fbclid", "gclid", "gclsrc", "dclid", "wbraid", "gbraid",
        "msclkid", "mc_cid", "mc_eid", "igshid", "vero_id",
        "_ga", "_gl", "yclid", "twclid", "ref", "ref_src",
    }
)

_DEFAULT_PORTS = {80, 443}


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("utm_") or lowered in _TRACKING_PARAMS


def normalize_url(url: str) -> str:
    """Normalise une URL (hôte minuscule, sans fragment, slash final nettoyé)."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = host if (port is None or port in _DEFAULT_PORTS) else f"{host}:{port}"
    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def canonical_url(url: str) -> str:
    """URL canonique : normalisée, sans tracking, paramètres triés."""
    normalized = normalize_url(url)
    parsed = urlparse(normalized)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    kept = sorted(
        ((k, v) for k, v in params if not _is_tracking_param(k)),
        key=lambda kv: (kv[0], kv[1]),
    )
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", urlencode(kept), ""))


def same_domain(a: str, b: str) -> bool:
    """True si `a` et `b` partagent le même nom d'hôte (insensible à la casse)."""
    return (urlparse(a).hostname or "").lower() == (urlparse(b).hostname or "").lower()


def root_domain(url: str) -> str:
    """Domaine racine simplifié (2 derniers labels, ex. 'example.com')."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def host_of(url: str) -> str:
    """Hôte complet d'une URL en minuscules (ex. 'www.boutique.fr').

    Granularité des observations par source (P0-3) : deux boutiques
    distinctes, même sur des sous-domaines du même domaine racine, sont
    deux sources différentes.
    """
    return (urlparse(url or "").hostname or "").lower().rstrip(".")
