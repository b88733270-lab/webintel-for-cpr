"""Extraction d'entité Produit (schéma §3 d'architecture.md).

`extract_product(url, soup, structured) -> dict` sérialisable.

Format de provenance : chaque champ important est documenté dans la clé
`"_provenance"` :
```json
"_provenance": {
  "name":  {"source": "json-ld", "selector": "script[type=ld+json] > Product.name",
            "method": "jsonpath", "confidence": 0.95},
  "price": {"source": "heuristique-dom", "selector": ".price",
            "method": "css", "confidence": 0.7}
}
```
`source` : "json-ld" | "microdata" | "opengraph" | "heuristique-dom" | "meta".
`method` : "jsonpath" | "css" | "xpath" | "regex" | "meta".
`confidence` : 0..1 (données structurées > heuristiques DOM).
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from webintel.core.utils import clean_text
from webintel.normalization.prices import parse_price

_AVAILABILITY_MAP = {
    "instock": "in_stock",
    "in_stock": "in_stock",
    "http://schema.org/instock": "in_stock",
    "https://schema.org/instock": "in_stock",
    "outofstock": "out_of_stock",
    "http://schema.org/outofstock": "out_of_stock",
    "https://schema.org/outofstock": "out_of_stock",
    "preorder": "preorder",
    "http://schema.org/preorder": "preorder",
    "https://schema.org/preorder": "preorder",
}

_IN_STOCK_WORDS = ("en stock", "disponible", "in stock", "available")
_OUT_OF_STOCK_WORDS = ("rupture", "out of stock", "épuisé", "epuise", "indisponible", "sold out")


def _json_ld_by_type(structured: dict, *types: str) -> list[dict]:
    wanted = {t.lower() for t in types}
    found: list[dict] = []
    for obj in structured.get("json_ld", []) or []:
        t = obj.get("@type")
        t_list = [str(x).lower() for x in t] if isinstance(t, list) else [str(t).lower()]
        if wanted & set(t_list):
            found.append(obj)
    return found


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> tuple[str | None, str | None]:
    """Premier texte non vide pour une liste de sélecteurs CSS ; retourne (texte, sélecteur)."""
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            text = clean_text(el.get_text(separator=" "))
            if text:
                return text, selector
    return None, None


def _offers(obj: dict) -> dict:
    offers = obj.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    return offers if isinstance(offers, dict) else {}


def _availability_from_text(text: str) -> str:
    lowered = text.lower()
    if any(w in lowered for w in _OUT_OF_STOCK_WORDS):
        return "out_of_stock"
    if any(w in lowered for w in _IN_STOCK_WORDS):
        return "in_stock"
    return "unknown"


def extract_product(url: str, soup: BeautifulSoup, structured: dict) -> dict:
    """Extrait un produit (dict sérialisable + `_provenance` par champ)."""
    og = structured.get("opengraph", {}) or {}
    meta = structured.get("meta", {}) or {}
    micro = structured.get("microdata", []) or []
    products = _json_ld_by_type(structured, "Product")

    product: dict = {
        "name": None, "brand": None, "model": None, "description": None,
        "sku": None, "ean": None, "category": None,
        "price": {"amount": None, "currency": None},
        "availability": "unknown", "rating": None, "review_count": None,
        "images": [], "specifications": {}, "seller": {},
    }
    prov: dict[str, dict] = {}

    def set_field(field: str, value, source: str, selector: str | None, method: str, confidence: float):
        if value is None or value == "" or value == [] or value == {}:
            return
        if product.get(field) not in (None, "", [], {}):
            return  # premier arrivé (source la plus fiable) gagne
        product[field] = value
        prov[field] = {"source": source, "selector": selector, "method": method, "confidence": confidence}

    # --- JSON-LD Product (source la plus fiable) ---
    if products:
        obj = products[0]
        offers = _offers(obj)
        brand = obj.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        set_field("name", clean_text(str(obj.get("name", ""))) or None, "json-ld",
                  "script[type='application/ld+json'] > Product.name", "jsonpath", 0.95)
        set_field("brand", clean_text(str(brand)) if brand else None, "json-ld",
                  "script[type='application/ld+json'] > Product.brand", "jsonpath", 0.9)
        set_field("model", clean_text(str(obj.get("model", ""))) or None, "json-ld",
                  "script[type='application/ld+json'] > Product.model", "jsonpath", 0.9)
        set_field("description", clean_text(str(obj.get("description", ""))) or None, "json-ld",
                  "script[type='application/ld+json'] > Product.description", "jsonpath", 0.9)
        set_field("sku", str(obj.get("sku") or "").strip() or None, "json-ld",
                  "script[type='application/ld+json'] > Product.sku", "jsonpath", 0.95)
        ean = obj.get("gtin13") or obj.get("gtin") or obj.get("mpn")
        set_field("ean", str(ean).strip() or None, "json-ld",
                  "script[type='application/ld+json'] > Product.gtin13", "jsonpath", 0.9)
        set_field("category", clean_text(str(obj.get("category", ""))) or None, "json-ld",
                  "script[type='application/ld+json'] > Product.category", "jsonpath", 0.85)
        if offers.get("price") is not None:
            try:
                amount = float(str(offers["price"]).replace(",", "."))
            except (ValueError, TypeError):
                amount = None
            currency = offers.get("priceCurrency")
            if amount is not None:
                product["price"] = {"amount": amount, "currency": currency}
                prov["price"] = {"source": "json-ld",
                                 "selector": "script[type='application/ld+json'] > Product.offers",
                                 "method": "jsonpath", "confidence": 0.95}
        avail = _AVAILABILITY_MAP.get(str(offers.get("availability", "")).lower().split("/")[-1])
        if avail:
            product["availability"] = avail
            prov["availability"] = {"source": "json-ld",
                                    "selector": "script[type='application/ld+json'] > Product.offers.availability",
                                    "method": "jsonpath", "confidence": 0.95}
        agg = obj.get("aggregateRating") or {}
        if isinstance(agg, dict):
            try:
                rating = float(str(agg.get("ratingValue", "")).replace(",", "."))
                set_field("rating", rating, "json-ld",
                          "script[type='application/ld+json'] > Product.aggregateRating.ratingValue",
                          "jsonpath", 0.9)
            except (ValueError, TypeError):
                pass
            try:
                set_field("review_count", int(agg.get("reviewCount", 0)), "json-ld",
                          "script[type='application/ld+json'] > Product.aggregateRating.reviewCount",
                          "jsonpath", 0.9)
            except (ValueError, TypeError):
                pass
        img = obj.get("image")
        imgs = [img] if isinstance(img, str) else (img if isinstance(img, list) else [])
        resolved = [urljoin(url, str(i)) for i in imgs if i][:10]
        if resolved:
            product["images"] = resolved
            prov["images"] = {"source": "json-ld",
                              "selector": "script[type='application/ld+json'] > Product.image",
                              "method": "jsonpath", "confidence": 0.9}
        seller = offers.get("seller") or obj.get("seller")
        if isinstance(seller, dict) and seller.get("name"):
            product["seller"] = {"name": clean_text(str(seller["name"]))}
            prov["seller"] = {"source": "json-ld",
                              "selector": "script[type='application/ld+json'] > Product.offers.seller",
                              "method": "jsonpath", "confidence": 0.85}

    # --- Microdata ---
    for item in micro:
        props = item.get("props", {}) or {}
        if not props.get("name"):
            continue
        set_field("name", clean_text(str(props.get("name", ""))) or None, "microdata",
                  "[itemscope] [itemprop=name]", "css", 0.85)
        price_raw = props.get("price")
        if price_raw and product["price"]["amount"] is None:
            parsed = parse_price(f"{price_raw} {props.get('priceCurrency', '')}")
            if parsed.amount is not None:
                product["price"] = {"amount": parsed.amount, "currency": parsed.currency}
                prov["price"] = {"source": "microdata", "selector": "[itemprop=price]",
                                 "method": "css", "confidence": 0.85}
        break  # premier itemscope produit

    # --- OpenGraph / meta ---
    set_field("name", (og.get("title") or "").strip() or None, "opengraph",
              "meta[property='og:title']", "meta", 0.8)
    set_field("description", (og.get("description") or meta.get("description") or "").strip() or None,
              "opengraph", "meta[property='og:description']", "meta", 0.8)
    if og.get("image") and not product["images"]:
        product["images"] = [urljoin(url, og["image"])]
        prov["images"] = {"source": "opengraph", "selector": "meta[property='og:image']",
                          "method": "meta", "confidence": 0.8}

    # --- Heuristiques DOM ---
    name, sel = _first_text(soup, ["h1", ".product-name", ".product-title", "[itemprop=name]"])
    set_field("name", name, "heuristique-dom", sel, "css", 0.7)

    if product["price"]["amount"] is None:
        for selector in [".price", ".product-price", ".prix", "[itemprop=price]", ".amount"]:
            el = soup.select_one(selector)
            if el:
                parsed = parse_price(el.get_text(separator=" "))
                if parsed.amount is not None:
                    product["price"] = {"amount": parsed.amount, "currency": parsed.currency}
                    prov["price"] = {"source": "heuristique-dom", "selector": selector,
                                     "method": "css", "confidence": 0.7}
                    break

    if product["availability"] == "unknown":
        avail_el = soup.select_one(".availability, .stock, [itemprop=availability]")
        avail_text = avail_el.get_text(separator=" ") if avail_el else soup.get_text(separator=" ")[:5000]
        detected = _availability_from_text(avail_text)
        if detected != "unknown":
            product["availability"] = detected
            prov["availability"] = {"source": "heuristique-dom",
                                    "selector": ".availability/.stock ou texte",
                                    "method": "css", "confidence": 0.6}

    # Fil d'Ariane -> catégorie.
    if not product["category"]:
        crumbs = soup.select(".breadcrumb a, .breadcrumbs a, nav[aria-label=breadcrumb] a, [itemtype*='BreadcrumbList'] a")
        if crumbs:
            product["category"] = " > ".join(clean_text(c.get_text()) for c in crumbs if clean_text(c.get_text()))
            prov["category"] = {"source": "heuristique-dom", "selector": ".breadcrumb a",
                                "method": "css", "confidence": 0.7}

    # Tables clé/valeur -> spécifications.
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) == 2:
                key = clean_text(cells[0].get_text())
                val = clean_text(cells[1].get_text())
                if key and val and key not in product["specifications"]:
                    product["specifications"][key] = val
    if product["specifications"]:
        prov["specifications"] = {"source": "heuristique-dom", "selector": "table tr",
                                 "method": "css", "confidence": 0.6}

    # Images (complément, max 10).
    if len(product["images"]) < 10:
        seen = set(product["images"])
        for img in soup.find_all("img", src=True):
            src = urljoin(url, img["src"].strip())
            if src not in seen and urlparse(src).scheme in ("http", "https"):
                seen.add(src)
                product["images"].append(src)
                if len(product["images"]) >= 10:
                    break
        if product["images"] and "images" not in prov:
            prov["images"] = {"source": "heuristique-dom", "selector": "img[src]",
                              "method": "css", "confidence": 0.5}

    if not product["description"]:
        desc, sel = _first_text(soup, [".product-description", ".description", "[itemprop=description]"])
        set_field("description", desc, "heuristique-dom", sel, "css", 0.6)

    product["_provenance"] = prov
    return product
