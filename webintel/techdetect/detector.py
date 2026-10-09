"""Détection de technologies : `detect_technologies(soup, headers, url)`.

Retourne `[{technology, confidence (0..1), evidence}]` en croisant ~20 signatures :
meta generator, scripts src, liens (wp-content, cdnjs...), en-têtes HTTP
(x-powered-by, server) et motifs e-commerce (WooCommerce, PrestaShop...).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

# Chaque signature : (nom, confiance, liste de motifs).
# Un motif est un tuple (portée, regex) ; portée ∈ {"meta_generator", "script",
# "link", "header", "html", "cookie"}.
_SIGNATURES: list[tuple[str, float, list[tuple[str, str]]]] = [
    ("WordPress", 0.95, [("meta_generator", r"wordpress"), ("link", r"wp-content/"), ("html", r"wp-json/")]),
    ("WooCommerce", 0.9, [("link", r"woocommerce"), ("html", r"wc-cart|woocommerce_params")]),
    ("Joomla", 0.9, [("meta_generator", r"joomla"), ("script", r"/media/jui/")]),
    ("Drupal", 0.9, [("meta_generator", r"drupal"), ("link", r"/sites/default/files"), ("html", r"drupalSettings")]),
    ("Shopify", 0.95, [("link", r"cdn\.shopify\.com"), ("html", r"Shopify\.theme|myshopify\.com")]),
    ("PrestaShop", 0.9, [("meta_generator", r"prestashop"), ("link", r"/themes/.*/prestashop|prestashop")]),
    ("Magento", 0.9, [("link", r"/static/version.*magento|mage/cookies"), ("html", r"Magento_|mage\.cookies")]),
    ("React", 0.8, [("html", r"data-reactroot|react-dom|__REACT"), ("script", r"react(\.min|\.production)?\.js")]),
    ("Next.js", 0.9, [("html", r"__NEXT_DATA__"), ("link", r"/_next/static/")]),
    ("Vue.js", 0.85, [("html", r"data-v-[a-f0-9]{8}|__VUE__"), ("script", r"vue(\.min|\.global)?\.js")]),
    ("Nuxt.js", 0.9, [("html", r"__NUXT__"), ("link", r"/_nuxt/")]),
    ("Angular", 0.85, [("html", r"ng-version|ng-app"), ("script", r"angular(\.min)?\.js")]),
    ("jQuery", 0.9, [("script", r"jquery([.-]\d|\.min)?\.js"), ("html", r"jQuery\.fn\.jquery")]),
    ("Bootstrap", 0.85, [("link", r"bootstrap(\.min)?\.css"), ("script", r"bootstrap(\.min|\.bundle)?\.js")]),
    ("Google Analytics", 0.9, [("script", r"googletagmanager\.com/gtag|google-analytics\.com/analytics"), ("html", r"gtag\(['\"]config|_gaq\.push")]),
    ("Google Tag Manager", 0.9, [("script", r"googletagmanager\.com/gtm\.js"), ("html", r"GTM-[A-Z0-9]+")]),
    ("Matomo", 0.85, [("script", r"matomo\.js"), ("html", r"_paq\.push")]),
    ("Cloudflare", 0.85, [("header", r"server:\s*cloudflare"), ("html", r"__cf_bm|cf_chl")]),
    ("Nginx", 0.7, [("header", r"server:\s*nginx")]),
    ("Apache", 0.7, [("header", r"server:\s*apache")]),
    ("PHP", 0.8, [("header", r"x-powered-by:\s*php")]),
    ("ASP.NET", 0.8, [("header", r"x-powered-by:\s*asp\.net"), ("header", r"x-aspnet-version")]),
    ("Tailwind CSS", 0.8, [("link", r"tailwind"), ("html", r"cdn\.tailwindcss\.com")]),
    ("Font Awesome", 0.75, [("link", r"font-?awesome"), ("html", r"fa-[a-z-]+")]),
    ("Yoast SEO", 0.85, [("html", r"yoast|_yoast_wpseo")]),
]

_COOKIE_HINTS = (("wordpress", "WordPress"), ("prestashop", "PrestaShop"))


def _collect_sources(soup: BeautifulSoup, headers: dict) -> dict[str, list[str]]:
    """Regroupe les observables par portée pour les signatures."""
    meta_generators = [
        m.get("content", "") for m in soup.find_all("meta", attrs={"name": "generator"})
        if m.get("content")
    ]
    scripts = [s.get("src", "") for s in soup.find_all("script", src=True)]
    links = []
    for tag in soup.find_all("link", href=True):
        links.append(tag.get("href", ""))
    for tag in soup.find_all("img", src=True):
        links.append(tag.get("src", ""))
    header_lines = [f"{k}: {v}" for k, v in (headers or {}).items()]
    return {
        "meta_generator": meta_generators,
        "script": scripts,
        "link": links,
        "header": header_lines,
        "html": [str(soup)[:200000]],  # borné : évite le scan de pages géantes
        "cookie": [c for c in (headers or {}).get("set-cookie", "").split(",") if c.strip()]
        if isinstance((headers or {}).get("set-cookie"), str)
        else list((headers or {}).get("set-cookie", []) or []),
    }


def detect_technologies(soup: BeautifulSoup, headers: dict, url: str) -> list[dict]:
    """Détecte les technologies d'une page (CMS, frameworks, analytics, serveur...)."""
    _ = url  # réservé : règles par domaine dans une évolution future
    sources = _collect_sources(soup, headers or {})
    detected: list[dict] = []
    seen: set[str] = set()

    for name, confidence, patterns in _SIGNATURES:
        evidence: str | None = None
        for scope, regex in patterns:
            compiled = re.compile(regex, re.IGNORECASE)
            for observable in sources.get(scope, []):
                match = compiled.search(observable)
                if match:
                    snippet = match.group(0)
                    if scope == "meta_generator":
                        snippet = f'meta generator="{observable.strip()}"'
                    elif scope == "header":
                        snippet = observable.strip()
                    evidence = f"{scope}: {snippet[:120]}"
                    break
            if evidence:
                break
        if evidence and name not in seen:
            seen.add(name)
            detected.append({"technology": name, "confidence": confidence, "evidence": evidence})

    # Indices via cookies (faible confiance, complément).
    cookie_blob = " ".join(sources["cookie"]).lower()
    for hint, name in _COOKIE_HINTS:
        if hint in cookie_blob and name not in seen:
            seen.add(name)
            detected.append({"technology": name, "confidence": 0.6,
                             "evidence": f"cookie: trace « {hint} » dans Set-Cookie"})

    # Domaine e-commerce Shopify via l'hôte.
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("myshopify.com") and "Shopify" not in seen:
        detected.append({"technology": "Shopify", "confidence": 0.95,
                         "evidence": f"url: sous-domaine {host}"})

    detected.sort(key=lambda d: d["confidence"], reverse=True)
    return detected
