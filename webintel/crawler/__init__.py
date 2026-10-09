"""Collecte : fetchers HTTP, navigateur (stub v2), hybride et moteur de crawl borné."""

from webintel.crawler.engine import CrawlEngine
from webintel.crawler.fetcher import BaseFetcher, BrowserFetcher, HybridFetcher, HttpFetcher
from webintel.crawler.urlutils import extract_links

__all__ = [
    "CrawlEngine",
    "BaseFetcher",
    "BrowserFetcher",
    "HybridFetcher",
    "HttpFetcher",
    "extract_links",
]
