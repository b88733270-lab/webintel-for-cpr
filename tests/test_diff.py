"""Tests du diff intelligent entre snapshots."""

from webintel.core.enums import ChangeLevel
from webintel.core.types import SnapshotPayload
from webintel.core.utils import utcnow
from webintel.diff.engine import diff_snapshots

_HTML = (
    "<html><head><title>Casque Audio X200</title></head>"
    "<body><h1>Casque Audio X200</h1>"
    '<p class="prix">49,99 €</p></body></html>'
)


def _payload(*, price: float | None = 49.90, currency: str | None = "EUR",
             title: str = "Casque Audio X200", html: str = _HTML) -> SnapshotPayload:
    """Snapshot minimal d'une page produit (prix/HTML/titre ajustables)."""
    structured: dict = {
        "price": {"amount": price, "currency": currency} if price is not None else {},
        "headings": [{"level": "h1", "text": "Casque Audio X200"}],
        "meta_description": "Un casque audio remarquable.",
    }
    return SnapshotPayload(
        url="https://boutique.example.com/produit/casque-audio-x200",
        canonical_url="https://boutique.example.com/produit/casque-audio-x200",
        fetched_at=utcnow(),
        status_code=200,
        title=title,
        page_type="product",
        page_type_score=1.0,
        text_hash="hash-texte-stable",
        html_hash="hash-html-" + str(abs(hash(html)) % 10_000),
        word_count=120,
        structured=structured,
        seo={"score": 80},
        technologies=[{"technology": "Nginx"}],
        health={"overall": 75},
        links=[{"url": "https://boutique.example.com/"}],
        images=[{"url": "https://boutique.example.com/images/x200-1.jpg"}],
    )


def test_diff_baisse_prix_major():
    old = _payload(price=49.90)
    new = _payload(price=44.90)
    result = diff_snapshots(old, new)
    assert result.level == ChangeLevel.MAJOR
    assert "baisse" in result.summary_fr.lower()
    fields = [c.field for c in result.changes]
    assert "price.amount" in fields


def test_diff_hausse_prix_major():
    old = _payload(price=44.90)
    new = _payload(price=49.90)
    result = diff_snapshots(old, new)
    assert result.level == ChangeLevel.MAJOR
    assert "hausse" in result.summary_fr.lower()


def test_diff_classe_css_seule_cosmetic():
    """Un changement de classe CSS seule ne doit JAMAIS être MAJOR/MINOR."""
    old = _payload()
    new_html = _HTML.replace('class="prix"', 'class="prix promo surligner"')
    new = _payload(html=new_html)
    result = diff_snapshots(old, new, old_html=_HTML, new_html=new_html)
    assert result.level == ChangeLevel.COSMETIC
    assert "cosmétique" in result.summary_fr.lower()


def test_diff_identiques_none():
    old = _payload()
    new = _payload()
    result = diff_snapshots(old, new, old_html=_HTML, new_html=_HTML)
    assert result.level == ChangeLevel.NONE
    assert result.changes == []


def test_diff_titre_change_minor():
    old = _payload(title="Casque Audio X200")
    new = _payload(title="Casque Audio X200 — Édition 2026")
    result = diff_snapshots(old, new)
    assert result.level == ChangeLevel.MINOR
    assert "titre" in result.summary_fr.lower()
