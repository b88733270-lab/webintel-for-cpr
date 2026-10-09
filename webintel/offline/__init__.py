"""WebIntel hors-ligne : file de synchronisation, collecte de terrain,
cartographie locale, sauvegarde/restauration et dégradation gracieuse.

Contexte : chercheurs burkinabè, faible connectivité, terrain sans
internet. TOUT fonctionne en local (SQLite) ; la synchronisation vers
un dépôt distant ne s'effectue qu'à la demande et **jamais** avec un
écrasement silencieux : tout conflit exige une résolution explicite
de l'utilisateur, journalisée.

Modules :
- ``models`` : ``FileSynchronisation``, ``ResolutionConflit``,
  ``CollecteTerrain`` (+ énumérations de statuts) ;
- ``reseau`` : drapeau « mode hors-ligne » (simulé par
  ``WEBINTEL_HORS_LIGNE=1`` ou bascule d'exécution) et message
  français explicite ;
- ``synchronisation`` : file générique + moteur de synchronisation
  (dépôts distant « mémoire » et « dossier ») + résolutions
  journalisées ;
- ``photos`` : stockage local des photos de terrain + miniatures ;
- ``cartographie`` : projection SVG locale + floutage/masquage GPS
  selon confidentialité et permissions ;
- ``sauvegarde`` : export/import ZIP versionné avec vérification
  d'intégrité (SHA-256) et rapport de restauration.
"""
