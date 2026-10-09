"""Cartographie locale des collectes — WebIntel hors-ligne.

- **Zéro dépendance réseau** : la carte est un SVG généré localement
  (aucune tuile externe). Le fond est un tracé **schématique** simplifié
  du Burkina Faso (polygone approximatif, mention explicite
  « fond schématique ») avec projection équirectangulaire des
  coordonnées GPS.
- **Protection du GPS** (``coordonnees_affichees``) selon le niveau de
  confidentialité de la collecte / de l'échantillon et le demandeur :
  - ``public`` : coordonnées exactes pour tous ;
  - auteur du point : exactes dans tous les cas ;
  - ``confidentiel`` / ``chercheur`` : **masquées** (jamais exposées en
    clair — ni coordonnées, ni SVG, ni HTML) pour les non-auteurs ;
  - ``restreint`` / ``projet`` (ou niveau inconnu → prudent) :
    **floutées** (grille 0,5°, ~55 km) pour les non-auteurs.
  Les deux vocabulaires de confidentialité sont acceptés : celui du
  module ``offline`` (public/restreint/confidentiel) et celui de
  ``webintel.science.models.NiveauConfidentialite``
  (public/projet/chercheur/restreint/confidentiel).

Le demandeur est identifié par son nom d'auteur (chaîne) ; en mode
local mono-utilisateur, l'auteur voit ses propres points en exact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from webintel.offline.models import NiveauConfidentialite

# Emprise schématique du Burkina Faso (degrés décimaux).
LON_MIN, LON_MAX = -5.6, 2.6
LAT_MIN, LAT_MAX = 9.4, 15.2
CENTROIDE = (12.3, -1.7)  # (lat, lon) — Ouagadougou approx., point de repli

# Polygone schématique (lon, lat) — TRACÉ SIMPLIFIÉ, pas une frontière
# officielle. Mention obligatoire dans l'UI : « fond schématique ».
CONTOUR_BF = [
    (-4.9, 15.05), (-3.9, 15.05), (-2.8, 14.7), (-1.8, 14.9),
    (-0.9, 14.7), (-0.2, 14.5), (0.6, 14.2), (1.5, 13.8),
    (2.1, 13.4), (2.4, 12.7), (2.1, 12.0), (1.9, 11.4),
    (1.5, 10.9), (1.1, 10.3), (0.5, 9.9), (-0.3, 9.7),
    (-1.2, 10.0), (-2.0, 10.2), (-2.8, 10.0), (-3.6, 10.3),
    (-4.3, 10.6), (-4.9, 11.2), (-5.3, 11.9), (-5.4, 12.8),
    (-5.2, 13.7),
]

# Grille de floutage (degrés) pour le niveau « restreint ».
GRILLE_FLOUTAGE_DEG = 0.5


@dataclass
class PointAffiche:
    """Un point tel qu'il peut être affiché à un demandeur donné."""

    lat: float | None
    lon: float | None
    statut: str  # "exact" | "floute" | "masque" | "absent"
    masque: bool  # True si le GPS précis est protégé


def coordonnees_affichees(
    lat: float | None,
    lon: float | None,
    niveau: str,
    demandeur: str,
    auteur: str,
) -> PointAffiche:
    """Applique la règle de confidentialité GPS.

    Retourne les coordonnées affichables + le statut. Le GPS précis
    d'une donnée ``confidentiel`` n'est JAMAIS retourné à un
    non-auteur (``lat``/``lon`` valent alors ``None``).
    """
    if lat is None or lon is None:
        return PointAffiche(None, None, "absent", False)
    est_auteur = bool(demandeur) and demandeur == auteur
    niveau_norm = (niveau or "").strip().lower()
    if niveau_norm == NiveauConfidentialite.PUBLIC.value or est_auteur:
        return PointAffiche(lat, lon, "exact", False)
    if niveau_norm in {NiveauConfidentialite.CONFIDENTIEL.value, "chercheur"}:
        return PointAffiche(None, None, "masque", True)
    # « restreint », « projet » ou niveau inconnu → flouté (prudent).
    lat_f = round(lat / GRILLE_FLOUTAGE_DEG) * GRILLE_FLOUTAGE_DEG
    lon_f = round(lon / GRILLE_FLOUTAGE_DEG) * GRILLE_FLOUTAGE_DEG
    return PointAffiche(lat_f, lon_f, "floute", True)


def _projeter(lat: float, lon: float, largeur: float,
              hauteur: float, marge: float = 24.0) -> tuple[float, float]:
    """Projection équirectangulaire (suffit à cette latitude)."""
    x = marge + (lon - LON_MIN) / (LON_MAX - LON_MIN) * (largeur - 2 * marge)
    y = marge + (1 - (lat - LAT_MIN) / (LAT_MAX - LAT_MIN)) * (hauteur - 2 * marge)
    return x, y


def _echapper(texte: str) -> str:
    return (
        str(texte)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generer_carte_svg(points: list[dict], largeur: int = 760,
                      hauteur: int = 560) -> str:
    """Génère le SVG de la carte (fond schématique + points).

    ``points`` : liste de ``{"lat", "lon", "statut", "titre",
    "couleur", "masque"}``. Les points ``masque`` sont placés au
    centroïde avec un contour distinct ; aucun GPS précis masqué
    n'apparaît dans le SVG (les coordonnées utilisées sont déjà
    filtrées par ``coordonnees_affichees`` en amont).
    """
    polygone = " ".join(
        f"{_projeter(lat, lon, largeur, hauteur)[0]:.1f},"
        f"{_projeter(lat, lon, largeur, hauteur)[1]:.1f}"
        for lon, lat in CONTOUR_BF
    )
    morceaux = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largeur} {hauteur}"',
        ' role="img" aria-label="Carte schématique des collectes au Burkina Faso"',
        ' style="width:100%;height:auto;background:#eef4f8;border:1px solid #c9d6e0;border-radius:8px;">',
        f'<polygon points="{polygone}" fill="#f7f3e8" stroke="#8a7a4d" stroke-width="2"/>',
        f'<text x="{largeur/2:.0f}" y="{hauteur - 8:.0f}" text-anchor="middle"',
        ' font-size="11" fill="#6b7a89">Burkina Faso — fond schématique simplifié '
        "(aucune tuile externe, tracé non officiel)</text>",
    ]
    for pt in points:
        lat, lon = pt.get("lat"), pt.get("lon")
        if lat is None or lon is None:
            cx, cy = _projeter(CENTROIDE[0], CENTROIDE[1], largeur, hauteur)
        else:
            cx, cy = _projeter(lat, lon, largeur, hauteur)
        couleur = pt.get("couleur", "#1f7a3a")
        titre = _echapper(pt.get("titre", ""))
        statut = pt.get("statut", "")
        if pt.get("masque"):
            morceaux.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="9" fill="none" '
                f'stroke="{couleur}" stroke-width="2" stroke-dasharray="4 2">'
                f"<title>{titre} — GPS masqué (confidentiel)</title></circle>"
            )
        else:
            morceaux.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="7" fill="{couleur}" '
                f'stroke="#fff" stroke-width="1.5">'
                f"<title>{titre} — GPS {statut}</title></circle>"
            )
    morceaux.append("</svg>")
    return "".join(morceaux)


def distance_km(lat: float, lon: float, lat2: float, lon2: float) -> float:
    """Distance haversine (utile aux tests de floutage)."""
    r = 6371.0
    p1, p2 = math.radians(lat), math.radians(lat2)
    dp = math.radians(lat2 - lat)
    dl = math.radians(lon2 - lon)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
