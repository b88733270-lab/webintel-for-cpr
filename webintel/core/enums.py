"""Énumérations du domaine (toutes héritent de `str` pour JSON/DB directs)."""

from enum import Enum


class PageType(str, Enum):
    PRODUCT = "product"
    PRODUCT_LIST = "product_list"
    ARTICLE = "article"
    BLOG = "blog"
    COMPANY = "company"
    SERVICE = "service"
    CATEGORY = "category"
    SEARCH = "search"
    DOCUMENTATION = "documentation"
    JOB = "job"
    EVENT = "event"
    OFFER = "offer"
    PROFILE = "profile"
    CONTACT = "contact"
    LANDING_PAGE = "landing_page"
    NEWS = "news"
    FORUM = "forum"
    DIRECTORY = "directory"
    DOCUMENT = "document"
    UNKNOWN = "unknown"


class EntityType(str, Enum):
    product = "product"
    article = "article"
    company = "company"
    webpage = "webpage"


class EventType(str, Enum):
    PRICE_DROP = "price_drop"
    PRICE_INCREASE = "price_increase"
    BACK_IN_STOCK = "back_in_stock"
    OUT_OF_STOCK = "out_of_stock"
    NEW_PRODUCT = "new_product"
    NEW_ARTICLE = "new_article"
    ARTICLE_UPDATED = "article_updated"
    NEW_COMPANY = "new_company"
    COMPANY_UPDATED = "company_updated"
    PAGE_CHANGED = "page_changed"
    SEO_CHANGED = "seo_changed"
    TECHNOLOGY_CHANGED = "technology_changed"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChangeLevel(str, Enum):
    NONE = "none"
    COSMETIC = "cosmetic"
    MINOR = "minor"
    MAJOR = "major"


class AlertChannel(str, Enum):
    log = "log"
    webhook = "webhook"


class MonitorTargetType(str, Enum):
    url = "url"
    domain = "domain"
    product = "product"
    price = "price"
    keyword = "keyword"
