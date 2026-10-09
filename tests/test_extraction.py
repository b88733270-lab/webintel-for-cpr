"""Tests d'extraction sémantique sur les fixtures HTML locales."""

from bs4 import BeautifulSoup

from webintel.extraction import (
    extract_article,
    extract_company,
    extract_product,
    extract_structured,
)
from webintel.extraction.contacts import extract_contacts


def _soup_and_structured(html: str, url: str):
    soup = BeautifulSoup(html, "lxml")
    structured = extract_structured(soup, url)
    return soup, structured


# --- Produit ------------------------------------------------------------

def test_extract_product(html_produit):
    url = "https://boutique.example.com/produit/casque-audio-x200"
    soup, structured = _soup_and_structured(html_produit, url)
    product = extract_product(url, soup, structured)
    assert product["name"] == "Casque Audio X200"
    assert product["price"]["amount"] == 49.99
    assert product["price"]["currency"] == "EUR"
    assert product["availability"] == "in_stock"
    assert product["brand"] == "SonicBrand"


def test_structured_json_ld_product(html_produit):
    url = "https://boutique.example.com/produit/casque-audio-x200"
    soup, structured = _soup_and_structured(html_produit, url)
    json_ld = structured.get("json_ld") or []
    types = [obj.get("@type") for obj in json_ld if isinstance(obj, dict)]
    assert "Product" in types
    offers = [obj.get("offers") for obj in json_ld
              if isinstance(obj, dict) and obj.get("@type") == "Product"]
    assert offers and offers[0].get("price") == "49.99"


# --- Article ------------------------------------------------------------

def test_extract_article(html_article):
    url = "https://journal.example.com/article/ecommerce-afrique"
    soup, structured = _soup_and_structured(html_article, url)
    article = extract_article(url, soup, structured)
    assert article["author"] == "Marie Dupont"
    assert article["published_at"] is not None
    assert "2026-03-15" in str(article["published_at"])
    assert "commerce électronique" in (article["title"] or "")


# --- Entreprise ---------------------------------------------------------

def test_extract_company(html_entreprise):
    url = "https://burkitech.example.bf/"
    soup, structured = _soup_and_structured(html_entreprise, url)
    company = extract_company(url, soup, structured)
    assert company["name"] == "BurkiTech"
    assert "contact@example.bf" in (company.get("emails") or [])
    assert "+22670123456" in (company.get("phones") or [])


def test_extract_contacts_sociaux(html_entreprise):
    soup = BeautifulSoup(html_entreprise, "lxml")
    contacts = extract_contacts(soup)
    assert "contact@example.bf" in contacts.get("emails", [])
    assert "+22670123456" in contacts.get("phones", [])
    networks = {s.get("network") for s in contacts.get("socials", [])}
    assert {"facebook", "linkedin"} <= networks
