"""Analyse SEO d'une page : `analyze_seo(url, soup, structured)`.

Retourne `{score: 0..100, issues: [{severity, code, message_fr, recommandation_fr}],
stats: {...}}`.
Règles : title absent/trop court (<30)/trop long (>60), meta description
absente/>160, 0 ou >1 h1, canonical absent, robots noindex, og:*/twitter:*
absents, images sans alt, contenu <300 mots.
Score = 100 − pénalités (error=15, warning=5, info=1, plancher 0).
"""

from __future__ import annotations

from urllib.parse import urlparse

from bs4 import BeautifulSoup

from webintel.core.utils import clean_text

_PENALTIES = {"error": 15, "warning": 5, "info": 1}


def _add(issues: list, severity: str, code: str, message_fr: str, recommandation_fr: str) -> None:
    issues.append({
        "severity": severity, "code": code,
        "message_fr": message_fr, "recommandation_fr": recommandation_fr,
    })


def analyze_seo(url: str, soup: BeautifulSoup, structured: dict) -> dict:
    """Analyse SEO : score 0..100, liste d'issues FR, statistiques."""
    issues: list[dict] = []
    meta = structured.get("meta", {}) or {}
    og = structured.get("opengraph", {}) or {}
    tw = structured.get("twitter", {}) or {}

    title_el = soup.find("title")
    title = clean_text(title_el.get_text()) if title_el else ""
    title_len = len(title)
    if not title:
        _add(issues, "error", "title_manquant",
             "La page n'a pas de balise <title>.",
             "Ajoutez un <title> unique et descriptif (50-60 caractères).")
    elif title_len < 30:
        _add(issues, "warning", "title_trop_court",
             f"Le <title> est trop court ({title_len} caractères).",
             "Visez 50 à 60 caractères avec le mot-clé principal en tête.")
    elif title_len > 60:
        _add(issues, "warning", "title_trop_long",
             f"Le <title> est trop long ({title_len} caractères, tronqué dans Google).",
             "Raccourcissez le <title> à 60 caractères maximum.")

    description = meta.get("description", "")
    desc_len = len(description)
    if not description:
        _add(issues, "warning", "description_manquante",
             "La meta description est absente.",
             "Ajoutez une meta description attractive (120-160 caractères).")
    elif desc_len > 160:
        _add(issues, "warning", "description_trop_longue",
             f"La meta description dépasse 160 caractères ({desc_len}).",
             "Raccourcissez-la : Google la tronque au-delà.")

    h1s = soup.find_all("h1")
    h1_count = len(h1s)
    if h1_count == 0:
        _add(issues, "warning", "h1_manquant",
             "Aucune balise <h1> sur la page.",
             "Ajoutez un <h1> unique décrivant le sujet principal.")
    elif h1_count > 1:
        _add(issues, "warning", "h1_multiples",
             f"{h1_count} balises <h1> détectées.",
             "Ne gardez qu'un seul <h1> ; utilisez <h2>/<h3> pour la hiérarchie.")

    if not meta.get("canonical"):
        _add(issues, "info", "canonical_manquant",
             "Aucune URL canonique déclarée.",
             "Ajoutez <link rel=\"canonical\"> pour éviter le contenu dupliqué.")

    robots = (meta.get("robots") or "").lower()
    if "noindex" in robots:
        _add(issues, "error", "noindex",
             "La page demande aux moteurs de ne pas l'indexer (noindex).",
             "Retirez « noindex » si la page doit apparaître dans Google.")

    if not og:
        _add(issues, "info", "opengraph_manquant",
             "Aucune balise OpenGraph (og:*) détectée.",
             "Ajoutez og:title, og:description et og:image pour les partages sociaux.")
    if not tw:
        _add(issues, "info", "twitter_card_manquante",
             "Aucune Twitter Card détectée.",
             "Ajoutez les balises twitter:card, twitter:title, twitter:description.")

    images = soup.find_all("img")
    sans_alt = [img for img in images if not (img.get("alt") or "").strip()]
    if images and sans_alt:
        _add(issues, "warning", "images_sans_alt",
             f"{len(sans_alt)} image(s) sur {len(images)} sans attribut alt.",
             "Renseignez l'attribut alt de chaque image (accessibilité + SEO).")

    # Liens internes / externes.
    host = (urlparse(url).hostname or "").lower()
    internes, externes = 0, 0
    for anchor in soup.find_all("a", href=True):
        href_host = (urlparse(anchor["href"]).hostname or "").lower()
        if not href_host:
            internes += 1  # relatif -> interne
        elif href_host == host or href_host.endswith("." + host):
            internes += 1
        else:
            externes += 1

    text = clean_text(soup.get_text(separator=" "))
    mots = len(text.split())
    if mots < 300:
        _add(issues, "warning", "contenu_faible",
             f"Contenu textuel faible ({mots} mots, seuil : 300).",
             "Enrichissez le contenu : Google favorise les pages substantielles.")

    score = max(0, 100 - sum(_PENALTIES[i["severity"]] for i in issues))
    return {
        "score": score,
        "issues": issues,
        "stats": {
            "title_len": title_len, "desc_len": desc_len, "h1_count": h1_count,
            "images_total": len(images), "images_sans_alt": len(sans_alt),
            "liens_internes": internes, "liens_externes": externes, "mots": mots,
        },
    }
