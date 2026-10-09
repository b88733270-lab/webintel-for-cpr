"""Outils partagés des méthodes d'analyse de synergie (MODE B).

RÈGLE DURE — honnêteté scientifique
-----------------------------------
Ces fonctions ne calculent QUE sur des données expérimentales réellement
fournies par le laboratoire. Aucune valeur de synergie n'est jamais créée
à partir de simples structures moléculaires : les fonctions de validation
lèvent ``DonneesInsuffisantes`` (message explicite « données
insuffisantes pour la méthode X ») dès qu'une entrée requise manque.

Conventions
-----------
- Les réponses sont des fractions d'effet dans [0, 1] (0 = aucun effet,
  1 = effet maximal, ex. fraction d'inhibition). Les pourcentages
  (0-100) sont normalisés automatiquement (détecté si une valeur > 1).
- Ajustement des courbes dose-réponse : modèle de Hill à 2 paramètres
  (IC50, pente), Emin = 0, Emax = 1 après normalisation, ajusté par
  recherche sur grille DÉTERMINISTE (moindres carrés, pas d'aléatoire) —
  mêmes entrées → mêmes sorties (reproductibilité).
"""

from __future__ import annotations

import math


class DonneesInsuffisantes(ValueError):
    """Données d'entrée insuffisantes pour appliquer une méthode.

    Message toujours de la forme « données insuffisantes pour la méthode
    X : <détail> » — jamais de calcul partiel ou inventé.
    """


def normaliser_reponse(valeur: float) -> float:
    """Normalise une réponse mesurée vers une fraction d'effet [0, 1].

    Accepte les fractions (0-1) et les pourcentages (0-100, détectés dès
    qu'une valeur > 1 est vue au niveau de l'appelant). Lève
    ``DonneesInsuffisantes`` si la valeur n'est pas un nombre fini.
    """
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        raise DonneesInsuffisantes(
            "données insuffisantes : réponse mesurée non numérique "
            f"(reçu : {valeur!r})."
        )
    if not math.isfinite(valeur):
        raise DonneesInsuffisantes(
            "données insuffisantes : réponse mesurée non finie "
            f"(reçu : {valeur!r})."
        )
    return float(valeur)


def normaliser_serie(reponses: list[float]) -> list[float]:
    """Normalise une série de réponses (fraction ou pourcentage) vers [0, 1]."""
    valeurs = [normaliser_reponse(r) for r in reponses]
    if any(v > 1.0 for v in valeurs):
        # Pourcentages 0-100 → fractions.
        valeurs = [v / 100.0 for v in valeurs]
    return [min(1.0, max(0.0, v)) for v in valeurs]


def valider_dose(valeur, nom: str) -> float:
    """Valide une dose (nombre fini > 0)."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        raise DonneesInsuffisantes(
            f"données insuffisantes : {nom} non numérique (reçu : {valeur!r})."
        )
    if not math.isfinite(valeur) or valeur <= 0:
        raise DonneesInsuffisantes(
            f"données insuffisantes : {nom} doit être un nombre fini > 0 "
            f"(reçu : {valeur!r})."
        )
    return float(valeur)


def hill(dose: float, ic50: float, pente: float) -> float:
    """Réponse prédite par le modèle de Hill (Emin=0, Emax=1).

    ``hill(d, IC50, h) = d^h / (IC50^h + d^h)`` — fraction d'effet dans
    ]0, 1[. Formule documentée (Hill, 1910).
    """
    if dose <= 0:
        return 0.0
    rapport = (dose / ic50) ** pente
    return rapport / (1.0 + rapport)


def inverser_hill(effet: float, ic50: float, pente: float) -> float | None:
    """Dose donnant l'effet ``effet`` selon la courbe de Hill ajustée.

    Inversion : ``D = IC50 · (y / (1 − y))^(1/h)``. Retourne ``None`` si
    l'effet est hors de ]0, 1[ (non inversible — le point est alors
    ignoré avec une note explicite, jamais extrapolé en silence).
    """
    if not 0.0 < effet < 1.0:
        return None
    return ic50 * (effet / (1.0 - effet)) ** (1.0 / pente)


def ajuster_hill(
    doses: list[float], reponses: list[float]
) -> dict:
    """Ajuste une courbe de Hill (IC50, pente) sur des données dose-réponse.

    - ``doses`` : ≥ 4 doses distinctes > 0 (sinon ``DonneesInsuffisantes``).
    - ``reponses`` : réponses normalisées [0, 1] (même longueur).
    - Méthode : recherche sur grille déterministe (log10(IC50) × pente),
      critère des moindres carrés. Pas d'aléatoire : mêmes entrées →
      mêmes paramètres.
    - Retourne ``{"ic50", "pente", "r2", "n", "sse"}`` ; ``r2`` = qualité
      de l'ajustement (1 = parfait). Un ``r2`` faible (< 0,5) est signalé
      dans les limites — l'ajustement reste un calcul, pas une vérité.
    """
    if len(doses) != len(reponses) or len(doses) < 4:
        raise DonneesInsuffisantes(
            "données insuffisantes pour la méthode : l'ajustement d'une "
            "courbe dose-réponse exige au moins 4 points dose-réponse "
            f"(reçu : {len(doses)})."
        )
    couples = sorted(
        (valider_dose(d, "dose"), r)
        for d, r in zip(doses, normaliser_serie(reponses))
    )
    if len({d for d, _ in couples}) < 4:
        raise DonneesInsuffisantes(
            "données insuffisantes pour la méthode : au moins 4 doses "
            "distinctes sont requises pour ajuster une courbe dose-réponse."
        )
    log_doses = [math.log10(d) for d, _ in couples]
    lo, hi = min(log_doses), max(log_doses)
    meilleur = None  # (sse, log_ic50, pente)
    # Grille grossière puis raffinage (déterministe).
    for _passe in range(3):
        pas_log = (hi - lo) / 20.0 or 0.05
        pas_pente = 0.2 if _passe == 0 else 0.05
        centre_log = (lo + hi) / 2.0 if meilleur is None else meilleur[1]
        centre_pente = 1.0 if meilleur is None else meilleur[2]
        for i in range(-10, 11):
            log_ic50 = centre_log + i * pas_log
            ic50 = 10.0 ** log_ic50
            for j in range(-10, 11):
                pente = max(0.1, centre_pente + j * pas_pente)
                sse = sum(
                    (r - hill(d, ic50, pente)) ** 2 for d, r in couples
                )
                if meilleur is None or sse < meilleur[0]:
                    meilleur = (sse, log_ic50, pente)
        lo, hi = meilleur[1] - pas_log * 10, meilleur[1] + pas_log * 10
    sse, log_ic50, pente = meilleur
    ic50 = 10.0 ** log_ic50
    moyenne = sum(r for _, r in couples) / len(couples)
    sst = sum((r - moyenne) ** 2 for _, r in couples)
    r2 = 1.0 - sse / sst if sst > 0 else 0.0
    return {
        "ic50": ic50,
        "pente": pente,
        "r2": max(0.0, min(1.0, r2)),
        "n": len(couples),
        "sse": sse,
    }


def graphe_svg(
    points: list[dict],
    titre: str,
    unite_delta: str = "Δ (fraction d'effet)",
) -> str:
    """Construit un graphique SVG simple des scores par point de mélange.

    ``points`` : dicts {"dose_a", "dose_b", "delta"} — barres horizontales
    (vert = Δ > 0, rouge = Δ < 0, gris = Δ ≈ 0). Pur SVG, sans dépendance
    externe (matplotlib n'est pas dans les dépendances du projet).
    """
    largeur, hauteur_ligne, marge = 520, 26, 150
    hauteur = max(120, 60 + len(points) * hauteur_ligne)
    echelle = (largeur - marge - 40) / 2.0  # Δ dans [-1, 1]
    lignes = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{largeur}" '
        f'height="{hauteur}" role="img" aria-label="{_echapper(titre)}">',
        f'<text x="10" y="22" font-size="13" font-family="sans-serif" '
        f'font-weight="bold">{_echapper(titre)}</text>',
        f'<text x="10" y="40" font-size="11" font-family="sans-serif" '
        f'fill="#555">{_echapper(unite_delta)} — Δ &gt; 0 au-dessus de '
        "l'attente d'additivité, Δ &lt; 0 en dessous.</text>",
    ]
    zero_x = marge + echelle
    lignes.append(
        f'<line x1="{zero_x:.1f}" y1="55" x2="{zero_x:.1f}" y2="{hauteur - 15}" '
        'stroke="#888" stroke-width="1"/>'
    )
    for i, point in enumerate(points):
        y = 70 + i * hauteur_ligne
        delta = max(-1.0, min(1.0, float(point.get("delta", 0.0))))
        x1 = zero_x if delta >= 0 else zero_x + delta * echelle
        x2 = zero_x + delta * echelle if delta >= 0 else zero_x
        couleur = "#2a7f3f" if delta > 0.02 else ("#b03a2e" if delta < -0.02 else "#999")
        etiquette = (
            f'A={point.get("dose_a", "?")} / B={point.get("dose_b", "?")} '
            f'(Δ={delta:+.3f})'
        )
        lignes.append(
            f'<text x="10" y="{y + 4}" font-size="10" font-family="sans-serif">'
            f'{_echapper(etiquette)}</text>'
            f'<rect x="{x1:.1f}" y="{y - 6}" width="{max(1.0, x2 - x1):.1f}" '
            f'height="12" fill="{couleur}"/>'
        )
    lignes.append("</svg>")
    return "\n".join(lignes)


def _echapper(texte: object) -> str:
    return (
        str(texte)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


INTERPRETATION_PRUDENTE = (
    "Interprétation prudente : un Δ positif indique un effet observé "
    "supérieur à l'attente du modèle de référence (signal de synergie "
    "SELON CE MODÈLE), un Δ négatif un effet inférieur (signal "
    "d'antagonisme SELON CE MODÈLE). Il s'agit d'une analyse informatique "
    "de données expérimentales fournies par le laboratoire : elle ne "
    "permet pas de conclure à un effet pharmacologique, ni de se "
    "prononcer sur la sécurité d'emploi d'une association. À confirmer "
    "par des réplicats expérimentaux indépendants."
)

LIMITES_COMMUNES = [
    "Le score dépend du modèle de référence choisi (Bliss, Loewe, HSA, "
    "ZIP) : des modèles différents peuvent classer différemment le même "
    "jeu de données.",
    "Les réponses doivent être des fractions d'effet comparables "
    "(même essai, mêmes conditions, même temps de mesure) ; des données "
    "hétérogènes invalident l'analyse.",
    "Aucun intervalle de confiance n'est calculé sans réplicats : un "
    "Δ isolé peut refléter du bruit expérimental.",
    "Cette analyse ne permet pas de conclure à un effet pharmacologique "
    "ni de se prononcer sur la sécurité d'emploi d'une association : "
    "elle quantifie un écart à un modèle d'additivité.",
]
