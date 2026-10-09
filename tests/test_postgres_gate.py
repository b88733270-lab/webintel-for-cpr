"""Garde-fou : PostgreSQL n'est « validé » que si ses tests ont VRAIMENT tourné.

Par défaut (`WEBINTEL_REQUIRE_PG_TESTS` non posé), les tests de
`tests/test_phase0_postgres.py` sont skippés proprement quand aucun serveur
n'est joignable — la suite reste verte sur SQLite.

Mais si `WEBINTEL_REQUIRE_PG_TESTS=1` est posé (CI, recette, validation
explicite), un serveur injoignable fait ÉCHOUER ce test au lieu de laisser
passer des skips silencieux : impossible de prétendre que « PostgreSQL est
validé » alors qu'aucun test PG n'a tourné.

Ce module n'a PAS de `skipif` au niveau module : le test décide lui-même.
"""

from __future__ import annotations

import os


def _pg_url() -> str:
    return os.environ.get(
        "WEBINTEL_TEST_PG_URL",
        "postgresql+psycopg://webintel:webintel@127.0.0.1:5432/webintel_test",
    )


def _pg_joignable() -> bool:
    """Un serveur PostgreSQL répond-il sur l'URL de test (timeout court) ?"""
    try:
        import psycopg
    except Exception:
        return False
    dsn = _pg_url()
    if "://" in dsn:
        scheme, rest = dsn.split("://", 1)
        scheme = {
            "postgresql+psycopg": "postgresql",
            "postgresql+psycopg2": "postgresql",
        }.get(scheme, scheme)
        dsn = f"{scheme}://{rest}"
    try:
        conn = psycopg.connect(dsn, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


def test_postgres_requis_si_variable_posee():
    """`WEBINTEL_REQUIRE_PG_TESTS=1` + PG injoignable => ÉCHEC explicite."""
    import pytest

    if os.environ.get("WEBINTEL_REQUIRE_PG_TESTS") != "1":
        pytest.skip(
            "WEBINTEL_REQUIRE_PG_TESTS non posé : la validation PostgreSQL "
            "reste optionnelle (tests PG skippés si aucun serveur)."
        )
    assert _pg_joignable(), (
        "WEBINTEL_REQUIRE_PG_TESTS=1 mais aucun serveur PostgreSQL n'est "
        f"joignable sur {_pg_url()}. Démarrez PostgreSQL ou corrigez "
        "WEBINTEL_TEST_PG_URL — sans serveur, la validation PG ne peut "
        "pas être revendiquée."
    )
