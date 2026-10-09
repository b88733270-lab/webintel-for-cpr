"""Extraction sémantique : données structurées, classification, entités, templates."""

from webintel.extraction.article import extract_article
from webintel.extraction.classify import classify_page
from webintel.extraction.company import extract_company
from webintel.extraction.contacts import extract_contacts
from webintel.extraction.product import extract_product
from webintel.extraction.structured import extract_structured
from webintel.extraction.templates import ExtractionTemplate

__all__ = [
    "extract_structured",
    "classify_page",
    "extract_product",
    "extract_article",
    "extract_company",
    "extract_contacts",
    "ExtractionTemplate",
]
