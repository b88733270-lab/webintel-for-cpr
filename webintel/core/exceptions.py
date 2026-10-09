"""Hiérarchie d'exceptions WebIntel (messages destinés à l'UI en français)."""


class WebIntelError(Exception):
    """Erreur racine de l'application."""


class SecurityError(WebIntelError):
    """URL ou action bloquée par une règle de sécurité (SSRF, robots...)."""


class FetchError(WebIntelError):
    """Échec de récupération HTTP (timeout, statut, taille...)."""


class ExtractionError(WebIntelError):
    """Échec d'extraction/structuration du contenu."""


class NotFoundError(WebIntelError):
    """Ressource introuvable (projet, job, entité...)."""
