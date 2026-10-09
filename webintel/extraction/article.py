"""Extraction d'entité Article (schéma §3 d'architecture.md).

`extract_article(url, soup, structured) -> dict` sérialisable :
title, author, published_at/updated_at (ISO via `normalization.parse_date`),
category, content (paragraphes nettoyés), images[], tags[] + `_provenance`.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from webintel.core.utils import clean_text
from webintel.extraction.product import _json_ld_by_type  # réutilisation interne du helper
from webintel.normalization.dates import parse_date


def _author_name(raw) -> str | None:
    if isinstance(raw, dict):
        raw = raw.get("name")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
        return _author_name(raw)
    text = clean_text(str(raw or ""))
    return text or None


def _isoformat(dt) -> str | None:
    return dt.isoformat() if dt else None


def _main_content(soup: BeautifulSoup) -> tuple[str, str | None]:
    """Contenu principal : <article>, sinon le plus gros bloc de paragraphes."""
    article = soup.find("article")
    if article:
        paragraphs = [clean_text(p.get_text(separator=" ")) for p in article.find_all("p")]
        paragraphs = [p for p in paragraphs if len(p) > 40]
        if paragraphs:
            return "\n\n".join(paragraphs), "article p"
    # Repli : conteneur avec le plus de texte paragraphe.
    best, best_len, best_sel = "", 0, None
    for selector in ["main", ".content", ".post-content", ".article-content", ".entry-content", "body"]:
        container = soup.select_one(selector)
        if not container:
            continue
        paragraphs = [clean_text(p.get_text(separator=" ")) for p in container.find_all("p")]
        text = "\n\n".join(p for p in paragraphs if len(p) > 40)
        if len(text) > best_len:
            best, best_len, best_sel = text, len(text), f"{selector} p"
    return best, best_sel


def extract_article(url: str, soup: BeautifulSoup, structured: dict) -> dict:
    """Extrait un article (dict sérialisable + `_provenance` par champ)."""
    og = structured.get("opengraph", {}) or {}
    meta = structured.get("meta", {}) or {}
    articles = _json_ld_by_type(structured, "NewsArticle", "BlogPosting", "Article")

    article: dict = {
        "title": None, "author": None, "published_at": None, "updated_at": None,
        "category": None, "content": "", "images": [], "tags": [],
    }
    prov: dict[str, dict] = {}

    def set_field(field: str, value, source: str, selector: str | None, method: str, confidence: float):
        if value is None or value == "" or value == []:
            return
        if article.get(field) not in (None, "", []):
            return
        article[field] = value
        prov[field] = {"source": source, "selector": selector, "method": method, "confidence": confidence}

    # --- JSON-LD ---
    if articles:
        obj = articles[0]
        set_field("title", clean_text(str(obj.get("headline") or obj.get("name") or "")), "json-ld",
                  "script[type='application/ld+json'] > Article.headline", "jsonpath", 0.95)
        set_field("author", _author_name(obj.get("author")), "json-ld",
                  "script[type='application/ld+json'] > Article.author", "jsonpath", 0.9)
        for raw_key, field in (("datePublished", "published_at"), ("dateModified", "updated_at")):
            raw = obj.get(raw_key)
            if raw and not article[field]:
                dt = parse_date(str(raw))
                if dt:
                    article[field] = _isoformat(dt)
                    prov[field] = {"source": "json-ld",
                                   "selector": f"script[type='application/ld+json'] > Article.{raw_key}",
                                   "method": "jsonpath", "confidence": 0.95}
        set_field("category", clean_text(str(obj.get("articleSection") or "")), "json-ld",
                  "script[type='application/ld+json'] > Article.articleSection", "jsonpath", 0.8)
        keywords = obj.get("keywords")
        if keywords and not article["tags"]:
            tags = [t.strip() for t in str(keywords).split(",") if t.strip()]
            if tags:
                article["tags"] = tags[:15]
                prov["tags"] = {"source": "json-ld",
                                "selector": "script[type='application/ld+json'] > Article.keywords",
                                "method": "jsonpath", "confidence": 0.85}

    # --- OpenGraph / meta ---
    set_field("title", (og.get("title") or "").strip() or None, "opengraph",
              "meta[property='og:title']", "meta", 0.85)
    set_field("author", (meta.get("author") or "").strip() or None, "meta",
              "meta[name=author]", "meta", 0.8)
    if og.get("image"):
        article["images"] = [urljoin(url, og["image"])]
        prov["images"] = {"source": "opengraph", "selector": "meta[property='og:image']",
                          "method": "meta", "confidence": 0.8}

    # --- Heuristiques DOM ---
    title_el = soup.find("h1") or soup.find("title")
    set_field("title", clean_text(title_el.get_text()) if title_el else None,
              "heuristique-dom", "h1", "css", 0.7)

    if not article["author"]:
        for selector in [".author", ".byline", "[itemprop=author]", "[rel=author]"]:
            el = soup.select_one(selector)
            if el and clean_text(el.get_text()):
                set_field("author", clean_text(el.get_text()), "heuristique-dom", selector, "css", 0.7)
                break

    if not article["published_at"]:
        time_el = soup.find("time", datetime=True)
        raw = time_el.get("datetime") if time_el else None
        if not raw:
            date_el = soup.select_one(".date, .published, [itemprop=datePublished]")
            raw = date_el.get_text() if date_el else None
        if raw:
            dt = parse_date(clean_text(str(raw)))
            if dt:
                article["published_at"] = _isoformat(dt)
                prov["published_at"] = {"source": "heuristique-dom",
                                        "selector": "time[datetime] ou .date",
                                        "method": "css", "confidence": 0.6}

    content, sel = _main_content(soup)
    if content:
        article["content"] = content
        prov["content"] = {"source": "heuristique-dom", "selector": sel,
                           "method": "css", "confidence": 0.7}

    if not article["tags"]:
        tags = [clean_text(a.get_text()) for a in soup.select(".tags a, .tag-list a, [rel=tag]")]
        tags = [t for t in tags if t][:15]
        if tags:
            article["tags"] = tags
            prov["tags"] = {"source": "heuristique-dom", "selector": ".tags a",
                            "method": "css", "confidence": 0.6}

    # Images du corps (max 10).
    if len(article["images"]) < 10:
        seen = set(article["images"])
        scope = soup.find("article") or soup
        for img in scope.find_all("img", src=True):
            src = urljoin(url, img["src"].strip())
            if src not in seen and urlparse(src).scheme in ("http", "https"):
                seen.add(src)
                article["images"].append(src)
                if len(article["images"]) >= 10:
                    break

    article["_provenance"] = prov
    return article
