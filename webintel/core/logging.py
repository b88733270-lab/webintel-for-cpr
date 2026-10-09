"""Journalisation : `get_logger(name)` configuré une seule fois (niveau via settings)."""

from __future__ import annotations

import logging

_configured = False


def get_logger(name: str) -> logging.Logger:
    """Retourne un logger standard ; configure le handler `webintel` une fois."""
    global _configured
    if not _configured:
        # Import local : évite tout cycle au chargement du package core.
        from webintel.config.settings import get_settings

        level_name = get_settings().LOG_LEVEL.upper()
        level = getattr(logging, level_name, logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        wi_logger = logging.getLogger("webintel")
        wi_logger.setLevel(level)
        wi_logger.addHandler(handler)
        # IMPORTANT (fiabilisation v2.1.x) : la propagation reste ACTIVE.
        # L'ancien `propagate = False` rendait la capture des journaux
        # dépendante de l'ordre des imports : le gestionnaire de pytest
        # (`caplog`) est attaché au logger racine, et pytest ne rattache
        # son gestionnaire aux loggers non-propageants que s'ils existent
        # DÉJÀ au démarrage de chaque phase de test. Un import tardif de
        # l'application coupait donc silencieusement la capture des
        # avertissements scientifiques (ex. `webintel.science.chimie`).
        # Avec la propagation active, la sortie console de production est
        # inchangée (le StreamHandler ci-dessus reste le seul gestionnaire
        # en production : uvicorn n'ajoute pas de gestionnaire au logger
        # racine), et tout gestionnaire racine (tests, agrégation centrale)
        # reçoit les enregistrements de façon déterministe.
        _configured = True
    return logging.getLogger(name)
