"""Classification du type de page : `classify_page(url, soup, structured)`.

Retourne `(PageType, score 0..1, signaux)` où `signaux` est une liste de phrases
en français expliquant la décision. Les signaux combinent :
- motifs d'URL (/product/, /blog/, /jobs/, /contact...),
- JSON-LD `@type` (Product, NewsArticle/BlogPosting, JobPosting, Event, Organization...),
- OpenGraph `og:type` (product, article),
- indices DOM : prix + bouton d'achat, grille de cartes + pagination, `<article>`
  avec auteur/date, formulaire de contact + téléphone/email, etc.

Score = signaux concordants avec le type retenu / total des signaux.
`(UNKNOWN, 0.0, ["aucun signal détecté"])` si rien ne correspond.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from webintel.core.enums import PageType
from webintel.normalization.prices import parse_price

# Motif d'URL -> (PageType, libellé du signal).
_URL_PATTERNS: list[tuple[re.Pattern, PageType, str]] = [
    (re.compile(r"/produit[s]?/|/product[s]?/|/p/|/shop/|/boutique/"), PageType.PRODUCT, "motif d'URL de type produit (/product/, /shop/...)"),
    (re.compile(r"/categorie[s]?/|/categor(y|ies)/|/collection[s]?/|/rayon/"), PageType.PRODUCT_LIST, "motif d'URL de type catalogue (/category/, /collections/...)"),
    (re.compile(r"/blog/|/article[s]?/|/post[s]?/|/news/|/actualit"), PageType.ARTICLE, "motif d'URL de type article (/blog/, /news/...)"),
    (re.compile(r"/jobs?/|/emploi[s]?/|/carrieres|/careers|/recrutement"), PageType.JOB, "motif d'URL de type offre d'emploi (/jobs/, /carrieres...)"),
    (re.compile(r"/contact[s]?/|/nous-joindre|/nous-contacter"), PageType.CONTACT, "motif d'URL de type contact (/contact...)"),
    (re.compile(r"/event[s]?/|/evenement[s]?/|/agenda/"), PageType.EVENT, "motif d'URL de type événement (/events/...)"),
    (re.compile(r"/about|/a-propos|/qui-sommes-nous|/societe|/entreprise"), PageType.COMPANY, "motif d'URL de type entreprise (/about, /qui-sommes-nous...)"),
    (re.compile(r"/service[s]?/|/prestation[s]?/"), PageType.SERVICE, "motif d'URL de type service (/services/...)"),
    (re.compile(r"/search|/recherche\?|/catalogsearch"), PageType.SEARCH, "motif d'URL de type recherche"),
    (re.compile(r"/docs?/|/documentation/|/help/|/aide/|/wiki/"), PageType.DOCUMENTATION, "motif d'URL de type documentation"),
    (re.compile(r"/forum[s]?/|/community/|/communaute/"), PageType.FORUM, "motif d'URL de type forum"),
    (re.compile(r"/annuaire|/directory|/listing"), PageType.DIRECTORY, "motif d'URL de type annuaire"),
]

# JSON-LD @type -> PageType.
_JSONLD_TYPES: dict[str, PageType] = {
    "product": PageType.PRODUCT,
    "newsarticle": PageType.ARTICLE,
    "blogposting": PageType.ARTICLE,
    "article": PageType.ARTICLE,
    "blog": PageType.BLOG,
    "jobposting": PageType.JOB,
    "event": PageType.EVENT,
    "organization": PageType.COMPANY,
    "localbusiness": PageType.COMPANY,
    "corporation": PageType.COMPANY,
    "contactpage": PageType.CONTACT,
    "aboutpage": PageType.COMPANY,
    "itemlist": PageType.PRODUCT_LIST,
    "searchresultspage": PageType.SEARCH,
    "profilepage": PageType.PROFILE,
    "faqpage": PageType.DOCUMENTATION,
    "howto": PageType.DOCUMENTATION,
}

# og:type -> PageType.
_OG_TYPES: dict[str, PageType] = {
    "product": PageType.PRODUCT,
    "article": PageType.ARTICLE,
    "website": PageType.COMPANY,
    "profile": PageType.PROFILE,
}

_BUY_MARKERS = re.compile(
    r"add[\s_-]?to[\s_-]?cart|ajouter[\s_-]?au[\s_-]?panier|buy[\s_-]?now|"
    r"acheter|commander|btn-cart|addtobasket",
    re.IGNORECASE,
)
_PRICE_CLASS = re.compile(r"price|prix|amount|montant", re.IGNORECASE)
_CONTACT_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s().-]{7,}\d")


def _json_ld_types(structured: dict) -> list[str]:
    types: list[str] = []
    for obj in structured.get("json_ld", []) or []:
        t = obj.get("@type")
        if isinstance(t, list):
            types.extend(str(x).lower() for x in t)
        elif t:
            types.append(str(t).lower())
    return types


def _dom_signals(soup: BeautifulSoup) -> list[tuple[PageType, str]]:
    """Signaux issus du DOM seul (hors URL et données structurées)."""
    signals: list[tuple[PageType, str]] = []
    text = soup.get_text(separator=" ")

    # Prix + bouton d'achat -> produit.
    price_found = parse_price(text).amount is not None
    buy_found = bool(
        soup.find(attrs={"class": _BUY_MARKERS})
        or soup.find("button", string=_BUY_MARKERS)
        or soup.find("a", string=_BUY_MARKERS)
        or soup.find(attrs={"itemprop": "price"})
    )
    price_class_found = bool(soup.find(attrs={"class": _PRICE_CLASS}))
    if price_found and (buy_found or price_class_found):
        signals.append((PageType.PRODUCT, "prix détecté avec bouton d'achat ou bloc prix dans le DOM"))

    # Grille de cartes répétées + pagination -> liste (produits ou articles de blog).
    cards = soup.find_all(attrs={"class": re.compile(r"\b(card|product-card|article-card|post-card|grid-item)\b", re.I)})
    has_pagination = bool(soup.find(attrs={"class": re.compile(r"pagination|pager", re.I)}))
    if len(cards) >= 3:
        if has_pagination or any(parse_price(c.get_text()).amount is not None for c in cards[:6]):
            kind = PageType.PRODUCT_LIST if any(parse_price(c.get_text()).amount is not None for c in cards[:6]) else PageType.BLOG
            signals.append((kind, f"grille de {len(cards)} cartes répétées" + (" avec pagination" if has_pagination else "")))

    # <article> + auteur/date -> article.
    article_tag = soup.find("article")
    has_author = bool(
        soup.find("meta", attrs={"name": "author"})
        or soup.find(attrs={"class": re.compile(r"\b(author|byline|auteur)\b", re.I)})
        or soup.find(attrs={"itemprop": "author"})
    )
    has_date = bool(
        soup.find("time")
        or soup.find(attrs={"itemprop": re.compile(r"datePublished|dateModified", re.I)})
        or soup.find(attrs={"class": re.compile(r"\b(date|published)\b", re.I)})
    )
    if article_tag and (has_author or has_date):
        detail = " et ".join(x for x in ["auteur identifié" if has_author else "", "date identifiée" if has_date else ""] if x)
        signals.append((PageType.ARTICLE, f"balise <article> avec {detail}"))

    # Formulaire de contact + coordonnées -> contact.
    has_form = bool(soup.find("form"))
    has_contact_info = bool(_CONTACT_RE.search(text[:20000]))
    contact_heading = bool(soup.find(["h1", "h2"], string=re.compile(r"contact|nous joindre|nous écrire", re.I)))
    if (has_form and has_contact_info) or contact_heading:
        signals.append((PageType.CONTACT, "formulaire et/ou coordonnées de contact détectés"))

    # Offre d'emploi : vocabulaire + champs typiques.
    job_vocab = len(re.findall(r"offre d'emploi|job description|postuler|candidature|apply now|type de contrat|salaire", text, re.I))
    if job_vocab >= 3:
        signals.append((PageType.JOB, f"vocabulaire d'offre d'emploi ({job_vocab} occurrences)"))

    return signals


def classify_page(url: str, soup: BeautifulSoup, structured: dict) -> tuple[PageType, float, list[str]]:
    """Classifie la page : retourne (PageType, score 0..1, signaux en français)."""
    votes: list[tuple[PageType, str]] = []

    # 1) Motifs d'URL.
    path = (urlparse(url).path or "").lower()
    for pattern, page_type, label in _URL_PATTERNS:
        if pattern.search(path):
            votes.append((page_type, label))
            break  # un seul motif d'URL retenu

    # 2) JSON-LD @type.
    for t in _json_ld_types(structured):
        if t in _JSONLD_TYPES:
            votes.append((_JSONLD_TYPES[t], f"JSON-LD de type « {t} »"))

    # 3) OpenGraph og:type.
    og_type = (structured.get("opengraph", {}) or {}).get("type", "").lower()
    if og_type in _OG_TYPES:
        votes.append((_OG_TYPES[og_type], f"OpenGraph og:type « {og_type} »"))

    # 4) Microdata itemtype (même table que JSON-LD, dernier segment de l'URL du type).
    for item in structured.get("microdata", []) or []:
        itype = str(item.get("type") or "")
        short = itype.rstrip("/").split("/")[-1].lower()
        if short in _JSONLD_TYPES:
            votes.append((_JSONLD_TYPES[short], f"microdonnées de type « {short} »"))

    # 5) Signaux DOM.
    votes.extend(_dom_signals(soup))

    if not votes:
        return PageType.UNKNOWN, 0.0, ["aucun signal détecté"]

    # Type majoritaire ; score = part des signaux concordants.
    counts: dict[PageType, int] = {}
    for page_type, _ in votes:
        counts[page_type] = counts.get(page_type, 0) + 1
    best = max(counts, key=lambda k: counts[k])
    score = round(counts[best] / len(votes), 3)
    signals = [label for page_type, label in votes if page_type == best]
    return best, score, signals
