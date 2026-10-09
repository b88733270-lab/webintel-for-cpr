"""Extraction d'entité Entreprise/Organisation (schéma §3 d'architecture.md).

`extract_company(url, soup, structured) -> dict` sérialisable :
name, website (domaine), description, industry, location, phones[], emails[],
socials[], technologies[] + `_provenance`.

`technologies` est laissé à `[]` ici : il sera rempli par le lot `techdetect`
(`techdetect.detector.detect_technologies`) lors de l'assemblage de la fiche.
"""

from __future__ import annotations

from urllib.parse import urlparse

from bs4 import BeautifulSoup

from webintel.core.utils import clean_text
from webintel.extraction.contacts import extract_contacts
from webintel.extraction.product import _json_ld_by_type  # réutilisation interne du helper


def _address_text(raw) -> str | None:
    if isinstance(raw, dict):
        parts = [raw.get(k) for k in ("streetAddress", "addressLocality", "postalCode", "addressCountry")]
        text = ", ".join(clean_text(str(p)) for p in parts if p)
        return text or None
    text = clean_text(str(raw or ""))
    return text or None


def extract_company(url: str, soup: BeautifulSoup, structured: dict) -> dict:
    """Extrait une entreprise/organisation (dict sérialisable + `_provenance`)."""
    og = structured.get("opengraph", {}) or {}
    meta = structured.get("meta", {}) or {}
    orgs = _json_ld_by_type(structured, "Organization", "LocalBusiness", "Corporation")
    contacts = extract_contacts(soup)
    host = (urlparse(url).hostname or "").lower()

    company: dict = {
        "name": None, "website": host or None, "description": None, "industry": None,
        "location": None, "phones": contacts["phones"], "emails": contacts["emails"],
        "socials": contacts["socials"], "technologies": [],  # rempli par techdetect
    }
    prov: dict[str, dict] = {}

    def set_field(field: str, value, source: str, selector: str | None, method: str, confidence: float):
        if value is None or value == "" or value == []:
            return
        if company.get(field) not in (None, "", []):
            return
        company[field] = value
        prov[field] = {"source": source, "selector": selector, "method": method, "confidence": confidence}

    if orgs:
        obj = orgs[0]
        set_field("name", clean_text(str(obj.get("name") or "")), "json-ld",
                  "script[type='application/ld+json'] > Organization.name", "jsonpath", 0.9)
        set_field("description", clean_text(str(obj.get("description") or "")), "json-ld",
                  "script[type='application/ld+json'] > Organization.description", "jsonpath", 0.85)
        set_field("industry", clean_text(str(obj.get("industry") or "")), "json-ld",
                  "script[type='application/ld+json'] > Organization.industry", "jsonpath", 0.85)
        set_field("location", _address_text(obj.get("address")), "json-ld",
                  "script[type='application/ld+json'] > Organization.address", "jsonpath", 0.85)

    set_field("name", (og.get("site_name") or "").strip() or None, "opengraph",
              "meta[property='og:site_name']", "meta", 0.8)
    set_field("description", (og.get("description") or meta.get("description") or "").strip() or None,
              "opengraph", "meta[property='og:description']", "meta", 0.75)

    # Repli DOM : <title> (nettoyé des suffixes " - Accueil", " | ...").
    if not company["name"]:
        title_el = soup.find("title")
        if title_el:
            title = clean_text(title_el.get_text())
            for sep in (" | ", " - ", " – ", " :: "):
                if sep in title:
                    title = title.split(sep)[0]
                    break
            set_field("name", title.strip() or None, "heuristique-dom", "title", "css", 0.6)

    # Adresse via microdonnées.
    if not company["location"]:
        addr_el = soup.select_one("[itemprop=address]")
        if addr_el:
            set_field("location", clean_text(addr_el.get_text(separator=", ")), "microdata",
                      "[itemprop=address]", "css", 0.7)

    if contacts["phones"]:
        prov["phones"] = {"source": "regex", "selector": "texte de la page + liens tel:",
                          "method": "regex", "confidence": 0.8}
    if contacts["emails"]:
        prov["emails"] = {"source": "regex", "selector": "texte de la page + liens mailto:",
                          "method": "regex", "confidence": 0.85}
    if contacts["socials"]:
        prov["socials"] = {"source": "heuristique-dom", "selector": "a[href] (domaines sociaux)",
                           "method": "css", "confidence": 0.9}

    company["_provenance"] = prov
    return company
