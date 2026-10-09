#!/usr/bin/env python3
"""Scénario de démonstration WebIntel — 100 % hors-ligne.

Exécution : `python3 scripts/seed_demo.py` depuis la racine du projet.
Aucun accès réseau : les pages sont lues depuis `tests/fixtures/` et servies
par un fetcher simulé ; le DNS est neutralisé (IP publique fictive).

Étapes :
  (A) projet « Démo WebIntel » + analyse produit -> entité + prix 49.99 EUR + snapshot ;
  (B) analyse article -> entité article ;
  (C) analyse entreprise -> entité company + contacts ;
  (D) baisse de prix 49,99 € -> 44,99 € -> ré-analyse (diff MAJOR, événement
      price_drop), moniteur (règle prix < 45 €, canal log), nouvelle baisse
      39,99 € -> run_monitor -> alerte « sent » ;
  (E) crawl borné du site fictif -> >= 3 pages visitées, déduplication ;
  (F) même produit via 2 sources -> find_same_products regroupe ; export CSV ;
      recherche « casque ».

Sortie : récapitulatif avec ✅ par étape et « DÉMO RÉUSSIE » à la fin.
En cas d'échec : message explicite + exit 1.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import sys
import traceback

# --- Environnement : AVANT tout import webintel ---------------------------
_DEMO_DIR = "/tmp/webintel-demo"
if os.path.isdir(_DEMO_DIR):
    shutil.rmtree(_DEMO_DIR, ignore_errors=True)
os.makedirs(_DEMO_DIR, exist_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_DEMO_DIR, "demo.db")
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["REQUIRE_API_KEY"] = "false"
os.environ["EXPORT_DIR"] = os.path.join(_DEMO_DIR, "exports")
os.environ["LOG_LEVEL"] = "WARNING"

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


def _fake_getaddrinfo(host, port, *args, **kwargs):
    """DNS fictif : noms d'hôtes -> IP publique fictive, IP littérales inchangées."""
    try:
        ipaddress.ip_address(str(host))
        family = socket.AF_INET6 if ":" in str(host) else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (str(host), port or 0))]
    except ValueError:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0))]


socket.getaddrinfo = _fake_getaddrinfo  # neutralise le réseau pour toute la démo


def _fixture(name: str) -> str:
    with open(os.path.join(_PROJECT_ROOT, "tests", "fixtures", name),
              encoding="utf-8") as fh:
        return fh.read()


# --- Imports webintel (après l'environnement) -----------------------------
from bs4 import BeautifulSoup  # noqa: E402

from webintel.analytics.market import find_same_products  # noqa: E402
from webintel.api.pipeline import analyze_url  # noqa: E402
from webintel.core.utils import clean_text, utcnow  # noqa: E402
from webintel.crawler.engine import CrawlEngine  # noqa: E402
from webintel.crawler.fetcher import HybridFetcher  # noqa: E402
from webintel.core.types import FetchResult  # noqa: E402
from webintel.database.base import SessionLocal, init_db  # noqa: E402
from webintel.database.models import (  # noqa: E402
    Alert, Entity, Event, Monitor, PageSnapshot, Project,
)
from webintel.exports.writers import build_export  # noqa: E402
from webintel.monitoring.service import run_monitor  # noqa: E402
from webintel.search.engine import search_entities  # noqa: E402


# --- Fetcher simulé --------------------------------------------------------
PAGES: dict[str, str] = {}


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return clean_text(soup.get_text(separator=" "))


def _mock_fetch(url: str, html: str | None = None) -> FetchResult:
    body = PAGES.get(url) or PAGES.get(url.rstrip("/"))
    if body is None:
        return FetchResult(url=url, final_url=url, status_code=404,
                           fetched_at=utcnow(), error="Page inconnue du mock.")
    return FetchResult(url=url, final_url=url, status_code=200,
                       headers={"content-type": "text/html; charset=utf-8"},
                       html=body, text=_html_to_text(body),
                       fetched_at=utcnow(), duration_ms=12, method="http")


class MockFetcher:
    """Fetcher injecté : sert les fixtures locales, sans réseau."""

    def fetch(self, url: str, *, timeout_s: int | None = None) -> FetchResult:
        return _mock_fetch(url)

    def close(self) -> None:
        pass


def _patch_hybrid_fetcher():
    """Le CrawlEngine instancie HybridFetcher en interne : on patche la classe."""

    def _fetch(self, url: str, *, timeout_s: int | None = None) -> FetchResult:
        return _mock_fetch(url)

    HybridFetcher.fetch = _fetch


# --- Cadre d'exécution -----------------------------------------------------
_results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    """Exécute une étape, affiche ✅/❌ et enregistre le résultat."""
    try:
        fn()
    except AssertionError as exc:
        _results.append((name, False, str(exc) or "assertion échouée"))
        print(f"❌ {name} — échec : {exc}", flush=True)
    except Exception as exc:  # noqa: BLE001
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        _results.append((name, False, detail))
        print(f"❌ {name} — erreur : {detail}", flush=True)
    else:
        _results.append((name, True, ""))
        print(f"✅ {name}", flush=True)


# --- Scénario --------------------------------------------------------------
PRODUCT_URL = "https://boutique.example.com/produit/casque-audio-x200"
ARTICLE_URL = "https://journal.example.com/article/ecommerce-afrique"
COMPANY_URL = "https://burkitech.example.bf/"
BASE_URL = "https://demo.example.com"


def _current_price(session, project_id: int, entity_key: str) -> dict:
    """Prix courant d'une entité via ses observations par source (v1.1.1).

    Discipline v1.1.1 : le prix ne vit PLUS dans `Entity.data` (identité
    uniquement) — il est lu dans les `SourceObservation` de l'entité.
    """
    from webintel.entities import registry
    entity = registry.get_entity(session, project_id, entity_key)
    assert entity is not None, f"entité introuvable : {entity_key}"
    for _domain, rows in registry.get_observations_by_source(session, entity.id).items():
        for row in rows:
            if row.field == "price" and isinstance(row.value, dict):
                return row.value
    return {}


def main() -> int:
    init_db()
    session = SessionLocal()
    fetcher = MockFetcher()

    print("=== Démo WebIntel (hors-ligne) ===", flush=True)

    # (A) Projet + analyse produit -----------------------------------------
    PAGES[PRODUCT_URL] = _fixture("produit.html")

    def etape_a():
        project = Project(name="Démo WebIntel")
        session.add(project)
        session.commit()
        session.refresh(project)
        fiche = analyze_url(session, project.id, PRODUCT_URL, fetcher=fetcher)
        assert fiche["page_type"] == "product", f"type={fiche['page_type']}"
        products = [e for e in fiche["entities"] if e["entity_type"] == "product"]
        assert products, "aucune entité produit"
        price = _current_price(session, project.id, products[0]["entity_key"])
        assert price.get("amount") == 49.99, f"prix={price}"
        assert price.get("currency") == "EUR", f"devise={price}"
        assert session.query(PageSnapshot).filter_by(project_id=project.id).count() >= 1
        etape_a.project_id = project.id
        etape_a.product_key = products[0]["entity_key"]

    check("A — projet créé, produit analysé (49,99 €, snapshot enregistré)", etape_a)
    pid = getattr(etape_a, "project_id", None)
    if pid is None:
        return _finish(session)

    # (B) Analyse article ----------------------------------------------------
    PAGES[ARTICLE_URL] = _fixture("article.html")

    def etape_b():
        fiche = analyze_url(session, pid, ARTICLE_URL, fetcher=fetcher)
        articles = [e for e in fiche["entities"] if e["entity_type"] == "article"]
        assert articles, "aucune entité article"
        author = (articles[0]["data"] or {}).get("author")
        assert author == "Marie Dupont", f"auteur={author}"

    check("B — article analysé (auteur Marie Dupont)", etape_b)

    # (C) Analyse entreprise -------------------------------------------------
    PAGES[COMPANY_URL] = _fixture("entreprise.html")

    def etape_c():
        fiche = analyze_url(session, pid, COMPANY_URL, fetcher=fetcher)
        companies = [e for e in fiche["entities"] if e["entity_type"] == "company"]
        assert companies, "aucune entité entreprise"
        data = companies[0]["data"] or {}
        assert "contact@example.bf" in (data.get("emails") or []), data.get("emails")
        assert "+22670123456" in (data.get("phones") or []), data.get("phones")

    check("C — entreprise analysée (email + téléphone +246…)", etape_c)

    # (D) Baisse de prix -> diff MAJOR + événement + moniteur + alerte -------
    baisse_1 = (_fixture("produit.html")
                .replace("49,99 €", "44,99 €")
                .replace("49.99", "44.99"))
    baisse_2 = (_fixture("produit.html")
                .replace("49,99 €", "39,99 €")
                .replace("49.99", "39.99"))

    def etape_d1():
        PAGES[PRODUCT_URL] = baisse_1
        fiche = analyze_url(session, pid, PRODUCT_URL, fetcher=fetcher)
        assert fiche["diff"]["level"] == "major", fiche["diff"]
        assert "baisse" in fiche["diff"]["summary_fr"].lower(), fiche["diff"]["summary_fr"]
        types = [e["type"] for e in fiche["events"]]
        assert "price_drop" in types, f"événements={types}"

    check("D1 — baisse 49,99 € → 44,99 € : diff MAJOR + événement price_drop", etape_d1)

    def etape_d2():
        monitor = Monitor(
            project_id=pid,
            name="Surveillance prix X200",
            target_type="product",
            target=PRODUCT_URL,
            interval_minutes=30,
            active=True,
            config={"rules": [
                {"all": [{"field": "price.amount", "op": "lt", "value": 45}],
                 "channel": "log"},
            ]},
        )
        session.add(monitor)
        session.commit()
        session.refresh(monitor)
        PAGES[PRODUCT_URL] = baisse_2  # nouvelle baisse : 44,99 € -> 39,99 €
        result = run_monitor(session, monitor.id, fetcher=fetcher)
        session.commit()
        assert result["urls_checked"] >= 1, result
        sent = (session.query(Alert)
                .filter(Alert.monitor_id == monitor.id, Alert.status == "sent")
                .count())
        assert sent >= 1, f"alertes envoyées={sent}, résultat={result['alerts']}"

    check("D2 — moniteur prix<45 € : run_monitor → alerte « sent » (canal log)", etape_d2)

    # (E) Crawl borné du site fictif ----------------------------------------
    def etape_e():
        PAGES[BASE_URL + "/"] = _fixture("site_index.html")
        PAGES[BASE_URL + "/produit"] = _fixture("produit.html")
        PAGES[BASE_URL + "/article"] = _fixture("article.html")
        PAGES[BASE_URL + "/entreprise"] = _fixture("entreprise.html")
        _patch_hybrid_fetcher()
        calls: list[dict] = []
        summary = CrawlEngine().run(
            "demo-crawl", [BASE_URL + "/"], pid,
            limits={"max_pages": 10, "max_depth": 2, "delay_s": 0,
                    "concurrency": 2, "obey_robots": False},
            progress_cb=calls.append,
        )
        assert summary["visited"] >= 3, summary
        assert len(summary["urls"]) == len(set(summary["urls"])), "doublons visités"
        assert calls, "progress_cb jamais appelé"

    check("E — crawl borné : ≥3 pages visitées, déduplication, progression", etape_e)

    # (F) Multi-sources + export + recherche --------------------------------
    def etape_f():
        variant_url = "https://autre-boutique.example.com/casque-x200"
        variant = (_fixture("produit.html")
                   .replace("49,99 €", "52,50 €")
                   .replace("49.99", "52.50"))
        PAGES[variant_url] = variant
        analyze_url(session, pid, variant_url, fetcher=fetcher)
        entity = (session.query(Entity)
                  .filter(Entity.project_id == pid,
                          Entity.entity_key == etape_a.product_key)
                  .one())
        assert PRODUCT_URL in (entity.sources or []), entity.sources
        assert variant_url in (entity.sources or []), entity.sources

        groups = find_same_products(session, pid)
        casque = [g for g in groups if "casque" in (g.get("name") or "").lower()]
        assert casque, f"groupes={groups}"
        assert len(casque[0]["observations"]) >= 2, casque[0]["observations"]

        _eid, csv_bytes = build_export(session, pid, "entities", "csv", {})
        assert csv_bytes, "export CSV vide"
        assert "Casque" in csv_bytes.decode("utf-8", errors="replace")

        found = search_entities(session, pid, q="casque")
        assert any("casque" in (e.get("name") or "").lower() for e in found), found

    check("F — multi-sources regroupées, export CSV, recherche « casque »", etape_f)

    return _finish(session)


def _finish(session) -> int:
    session.close()
    failed = [name for name, ok, _ in _results if not ok]
    print("\n=== Récapitulatif ===", flush=True)
    for name, ok, detail in _results:
        status = "✅" if ok else "❌"
        print(f"{status} {name}" + (f" — {detail}" if detail and not ok else ""),
              flush=True)
    if failed:
        print(f"\nDÉMO ÉCHOUÉE : {len(failed)} étape(s) en échec.", flush=True)
        return 1
    print("\nDÉMO RÉUSSIE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
