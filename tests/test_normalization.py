"""Tests de normalisation : prix, dates, téléphones, URLs canoniques."""

from datetime import datetime

from webintel.normalization.dates import parse_date
from webintel.normalization.phones import parse_phones
from webintel.normalization.prices import parse_price
from webintel.normalization.urls import canonical_url


# --- Prix ---------------------------------------------------------------

def test_parse_price_euro_virgule():
    price = parse_price("19,99 €")
    assert price.amount == 19.99
    assert price.currency == "EUR"


def test_parse_price_dollar_milliers():
    price = parse_price("$1,234.56")
    assert price.amount == 1234.56
    assert price.currency == "USD"


def test_parse_price_fcfa_vers_xof():
    price = parse_price("2500 FCFA")
    assert price.amount == 2500.0
    assert price.currency == "XOF"


def test_parse_price_sans_prix():
    price = parse_price("Ce texte ne contient aucun prix.")
    assert price.amount is None
    assert price.currency is None


def test_parse_price_prix_visible_fixture():
    price = parse_price("Prix : 49,99 € seulement !")
    assert price.amount == 49.99
    assert price.currency == "EUR"


# --- Dates --------------------------------------------------------------

def test_parse_date_iso():
    assert parse_date("2026-03-15") == datetime(2026, 3, 15)


def test_parse_date_jj_mm_aaaa():
    assert parse_date("15/03/2026") == datetime(2026, 3, 15)


def test_parse_date_francais_litteral():
    assert parse_date("15 mars 2026") == datetime(2026, 3, 15)


def test_parse_date_timestamp():
    parsed = parse_date("1773700000")
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == (2026, 3, 16)


def test_parse_date_invalide():
    assert parse_date("") is None
    assert parse_date("pas une date du tout") is None


# --- Téléphones ---------------------------------------------------------

def test_parse_phones_burkina():
    phones = parse_phones("Appelez-nous au +226 70 12 34 56.")
    assert phones == ["+22670123456"]


def test_parse_phones_international_00():
    phones = parse_phones("Contact : 0033 6 12 34 56 78")
    assert phones == ["+33612345678"]


def test_parse_phones_aucun():
    assert parse_phones("Aucun numéro ici.") == []


# --- URLs canoniques ----------------------------------------------------

def test_canonical_url_supprime_utm_et_trie_params():
    canonical = canonical_url("https://example.com/page?utm_source=google&b=2&a=1")
    assert canonical == "https://example.com/page?a=1&b=2"


def test_canonical_url_conserve_params_utiles():
    canonical = canonical_url("https://example.com/p?couleur=rouge")
    assert "couleur=rouge" in canonical
    assert "utm_" not in canonical
