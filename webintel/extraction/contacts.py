"""Extraction de contacts : emails, téléphones, réseaux sociaux.

`extract_contacts(soup) -> {emails: [...], phones: [...], socials: [{network, url}]}`.
- emails : regex robuste sur le texte + attributs `mailto:` (dédupliqués, minuscules),
- phones : via `normalization.parse_phones` sur tout le texte + liens `tel:`,
- socials : liens dont le domaine correspond à un réseau connu.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from webintel.core.utils import clean_text
from webintel.normalization.phones import parse_phones

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Domaine (ou fragment) -> nom du réseau.
_SOCIAL_DOMAINS: dict[str, str] = {
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "linkedin.com": "linkedin",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "instagram.com": "instagram",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
    "whatsapp.com": "whatsapp",
    "wa.me": "whatsapp",
    "telegram.me": "telegram",
    "t.me": "telegram",
    "github.com": "github",
    "pinterest.com": "pinterest",
    "snapchat.com": "snapchat",
    "threads.net": "threads",
}


def _social_network(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    for domain, network in _SOCIAL_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return network
    return None


def extract_contacts(soup: BeautifulSoup) -> dict:
    """Extrait emails, téléphones et réseaux sociaux d'une page."""
    emails: list[str] = []
    socials: list[dict] = []
    seen_social: set[str] = set()

    text = soup.get_text(separator=" ")
    for match in _EMAIL_RE.finditer(text):
        email = match.group(0).lower()
        if email not in emails:
            emails.append(email)

    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        lowered = href.lower()
        if lowered.startswith("mailto:"):
            address = clean_text(lowered[7:].split("?")[0])
            if _EMAIL_RE.fullmatch(address) and address not in emails:
                emails.append(address)
        network = _social_network(href)
        if network and href not in seen_social:
            seen_social.add(href)
            socials.append({"network": network, "url": href})

    phones = parse_phones(text)
    # Complète avec les liens tel: (souvent mieux formatés).
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if href.lower().startswith("tel:"):
            for phone in parse_phones(href[4:]):
                if phone not in phones:
                    phones.append(phone)

    return {"emails": emails, "phones": phones, "socials": socials}
