"""Moteurs de simulation scientifique WebIntel.

Rôle
----
Ce package regroupe les moteurs de *simulation informatique* utilisés par la
plateforme WebIntel (pharmacognosie, phytochimie, produits naturels) :
similarité moléculaire, prédiction de propriétés, analyse d'interactions,
statistiques, adaptateurs vers des moteurs externes (ex. docking).

Règle d'honnêteté scientifique (DURE)
-------------------------------------
Une simulation n'est JAMAIS présentée comme une preuve expérimentale.
Chaque résultat produit ici porte :

- l'étiquetage « Simulation — à confirmer expérimentalement »,
- la méthode de calcul utilisée (chaque valeur calculée porte sa méthode),
- les limitations explicites du moteur.

Les données publiées, normalisées, hypothèses, prédictions/simulations et
observations expérimentales restent strictement séparées : un moteur de
simulation ne lit ni ne modifie jamais la base de données — il calcule à
partir des entrées fournies et rend un résultat JSON déterministe.

Architecture
------------
- ``webintel.simulations.plugins`` : contrat que tout moteur doit respecter.
- ``webintel.simulations.registry`` : registre des moteurs disponibles.
- ``webintel.simulations.reports`` : construction et rendu des rapports
  de simulation (6 sections obligatoires).
"""
