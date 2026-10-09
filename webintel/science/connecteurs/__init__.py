"""Connecteurs de sources externes (lecture seule) — Phase 10.

Usage :
    from webintel.science.connecteurs import registry

    connecteur = registry.get_connecteur("pubchem")
    rapport = connecteur.importer(session, {"nom": "aspirine"})
"""
