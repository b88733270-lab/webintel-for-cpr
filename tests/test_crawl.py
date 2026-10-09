"""Tests du moteur de crawl borné (fetch mocké depuis un dict {url: html})."""

from webintel.crawler.engine import CrawlEngine
from webintel.crawler.fetcher import HybridFetcher

from tests.helpers import make_fetcher


def _site(html_site_index, html_produit, html_article):
    base = "https://demo.example.com"
    return {
        base + "/": html_site_index,
        base + "/produit": html_produit,
        base + "/article": html_article,
        base + "/entreprise": "<html><body><h1>Entreprise</h1></body></html>",
    }


def test_crawl_borne_et_dedup(monkeypatch, html_site_index, html_produit, html_article):
    """Crawl borné : max_pages, pas de doublon (lien /produit en double)."""
    pages = _site(html_site_index, html_produit, html_article)
    monkeypatch.setattr(HybridFetcher, "fetch", make_fetcher(pages))

    result = CrawlEngine().run(
        job_id="test-1",
        start_urls=["https://demo.example.com/"],
        project_id=1,
        limits={"max_pages": 5, "max_depth": 2, "delay_s": 0,
                "concurrency": 2, "obey_robots": False},
    )
    assert result["visited"] >= 3
    assert result["visited"] <= 5
    urls = result["urls"]
    assert len(urls) == len(set(urls)), "doublon détecté malgré la déduplication"
    assert result["errors"] == 0


def test_crawl_progress_cb_appele(monkeypatch, html_site_index, html_produit, html_article):
    pages = _site(html_site_index, html_produit, html_article)
    monkeypatch.setattr(HybridFetcher, "fetch", make_fetcher(pages))
    calls: list[dict] = []

    result = CrawlEngine().run(
        job_id="test-2",
        start_urls=["https://demo.example.com/"],
        project_id=1,
        limits={"max_pages": 5, "max_depth": 2, "delay_s": 0,
                "concurrency": 2, "obey_robots": False},
        progress_cb=calls.append,
    )
    assert calls, "progress_cb n'a jamais été appelé"
    assert result["visited"] >= 3


def test_crawl_respecte_max_pages(monkeypatch, html_site_index, html_produit, html_article):
    pages = _site(html_site_index, html_produit, html_article)
    monkeypatch.setattr(HybridFetcher, "fetch", make_fetcher(pages))

    result = CrawlEngine().run(
        job_id="test-3",
        start_urls=["https://demo.example.com/"],
        project_id=1,
        limits={"max_pages": 2, "max_depth": 3, "delay_s": 0,
                "concurrency": 2, "obey_robots": False},
    )
    assert result["visited"] <= 2
