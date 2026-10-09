"""Constantes HTML des fixtures locales (chargées depuis `tests/fixtures/`).

Utilisables via `from tests.fixtures_html import PRODUIT_HTML, ...` ou via
les fixtures pytest `html_produit`, ... définies dans `conftest.py`.
"""

from pathlib import Path

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


PRODUIT_HTML = _load("produit.html")
ARTICLE_HTML = _load("article.html")
ENTREPRISE_HTML = _load("entreprise.html")
LISTING_HTML = _load("listing.html")
SITE_INDEX_HTML = _load("site_index.html")
