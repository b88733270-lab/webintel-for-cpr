"""Tests de classification des pages.

Les types attendus ont été vérifiés manuellement contre le comportement réel
du classifieur (sonde sur les fixtures) :
- produit.html   -> product      (JSON-LD Product + prix + bouton d'achat)
- article.html   -> article      (<article> + auteur + date)
- entreprise.html -> company     (JSON-LD Organization)
- listing.html   -> product_list (grille de cartes avec pagination)
"""

from bs4 import BeautifulSoup

from webintel.extraction import classify_page, extract_structured


def _classify(html: str, url: str):
    soup = BeautifulSoup(html, "lxml")
    structured = extract_structured(soup, url)
    return classify_page(url, soup, structured)


def test_classify_produit(html_produit):
    page_type, score, _ = _classify(
        html_produit, "https://boutique.example.com/produit/casque-audio-x200")
    assert page_type.value == "product"
    assert score > 0


def test_classify_article(html_article):
    page_type, score, _ = _classify(
        html_article, "https://journal.example.com/article/ecommerce-afrique")
    assert page_type.value == "article"
    assert score > 0


def test_classify_entreprise(html_entreprise):
    page_type, score, _ = _classify(
        html_entreprise, "https://burkitech.example.bf/")
    assert page_type.value == "company"
    assert score > 0


def test_classify_listing(html_listing):
    page_type, score, _ = _classify(
        html_listing, "https://boutique.example.com/catalogue")
    assert page_type.value == "product_list"
    assert score > 0
