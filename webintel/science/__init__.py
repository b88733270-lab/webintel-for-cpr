"""Modèle scientifique WebIntel — Phase 2.

Discipline des niveaux de données (règle d'or du produit) :

- ``publie`` : donnée issue d'une source publiée et vérifiable ;
- ``normalise`` : donnée rapportée, normalisée par WebIntel ;
- ``hypothese`` : hypothèse de travail, non confirmée ;
- ``prediction`` : résultat de simulation — **à confirmer
  expérimentalement, ne constitue pas une preuve expérimentale** ;
- ``experimental`` : observation de laboratoire (donnée privée par défaut).

Aucune donnée n'est jamais présentée comme « scientifiquement validée »
du seul fait qu'elle est stockée ou calculée ici.
"""

from webintel.science import chimie, models, schemas, service

__all__ = ["chimie", "models", "schemas", "service"]
