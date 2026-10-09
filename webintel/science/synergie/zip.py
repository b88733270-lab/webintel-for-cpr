"""Méthode ZIP — Zero Interaction Potency (Yadav et al., 2015).

Formule documentée
------------------
ZIP suppose que les deux molécules ne potentialisent pas leurs
courbes dose-réponse mutuelles (les hypothèses de Loewe ET de Bliss
sont satisfaites simultanément). Étapes :

1. Ajuster les courbes de Hill de chaque molécule SEULE :
   ``f_A(d)``, ``f_B(d)`` (modèle de Hill, voir ``commun``).
2. Pour un mélange (d_A, d_B), l'attente ZIP utilise les valeurs
   AJUSTÉES (et non les observations brutes — c'est la différence avec
   Bliss, Yadav et al. 2015, éq. 16) :

       E_zip = f_A(d_A) + f_B(d_B) − f_A(d_A) · f_B(d_B)

3. Score delta :

       Δ_zip = E_obs − E_zip

Δ > 0 : signal de synergie (selon ZIP) ; Δ < 0 : signal d'antagonisme
(selon ZIP).

Données requises : courbes dose-réponse des DEUX molécules seules
(``courbe_a`` / ``courbe_b``, ≥ 4 points chacune) + au moins un point
de mélange ``{"dose_a", "dose_b", "reponse"}``. Sans ces données :
« données insuffisantes pour la méthode ZIP ».

Référence : Yadav B., Wennerberg K., Aittokallio T., Tang J. (2015)
Searching for drug synergy in complex dose-response landscapes using
an interaction potency model. Comput. Struct. Biotechnol. J. 13:504-513.
"""

from __future__ import annotations

from .commun import (
    DonneesInsuffisantes,
    INTERPRETATION_PRUDENTE,
    LIMITES_COMMUNES,
    ajuster_hill,
    graphe_svg,
    hill,
    normaliser_reponse,
    normaliser_serie,
    valider_dose,
)
from .loewe import _courbe_ou_parametres

NOM = "zip"
FORMULE = (
    "E_zip = f_A(d_A) + f_B(d_B) − f_A(d_A)·f_B(d_B) "
    "(f = courbes de Hill ajustées) ; Δ = E_obs − E_zip"
)


def donnees_requises() -> list[str]:
    return ["courbe_a", "courbe_b", "melanges"]


def analyser(donnees: dict, parametres: dict | None = None) -> dict:
    """Applique ZIP à des mélanges expérimentaux (+ courbes individuelles)."""
    parametres = parametres or {}
    courbe_a = _courbe_ou_parametres(donnees, "courbe_a", "molécule A", "ZIP")
    courbe_b = _courbe_ou_parametres(donnees, "courbe_b", "molécule B", "ZIP")
    melanges = donnees.get("melanges")
    if not isinstance(melanges, list) or not melanges:
        raise DonneesInsuffisantes(
            "données insuffisantes pour la méthode ZIP : la liste "
            "« melanges » (dose_a, dose_b, reponse) est absente ou vide."
        )
    points = []
    for i, mel in enumerate(melanges):
        if not isinstance(mel, dict) or mel.get("dose_a") is None \
                or mel.get("dose_b") is None or mel.get("reponse") is None:
            raise DonneesInsuffisantes(
                "données insuffisantes pour la méthode ZIP : "
                f"mélange n°{i + 1} incomplet (dose_a, dose_b, reponse requis)."
            )
        d_a = valider_dose(mel["dose_a"], f"mélange n°{i + 1} : dose_a")
        d_b = valider_dose(mel["dose_b"], f"mélange n°{i + 1} : dose_b")
        y = normaliser_serie([normaliser_reponse(mel["reponse"])])[0]
        f_a = hill(d_a, courbe_a["ic50"], courbe_a["pente"])
        f_b = hill(d_b, courbe_b["ic50"], courbe_b["pente"])
        e_zip = f_a + f_b - f_a * f_b
        delta = y - e_zip
        points.append({
            "melange": i + 1,
            "dose_a": d_a,
            "dose_b": d_b,
            "reponse_observee": round(y, 4),
            "f_a_ajustee": round(f_a, 4),
            "f_b_ajustee": round(f_b, 4),
            "reponse_attendue_zip": round(e_zip, 4),
            "delta": round(delta, 4),
            "signal": (
                "synergie (selon ZIP)" if delta > 0.02
                else "antagonisme (selon ZIP)" if delta < -0.02
                else "additivité (selon ZIP)"
            ),
        })
    deltas = [p["delta"] for p in points]
    graphe_pts = [
        {"dose_a": p["dose_a"], "dose_b": p["dose_b"], "delta": p["delta"]}
        for p in points
    ]
    return {
        "methode": NOM,
        "formule": FORMULE,
        "reference": "Yadav B. et al. (2015) Comput. Struct. Biotechnol. J. 13:504-513.",
        "donnees_utilisees": {
            "courbe_a": {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in courbe_a.items()},
            "courbe_b": {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in courbe_b.items()},
            "nb_melanges": len(points),
            "origine": "résultat expérimental fourni par le laboratoire",
        },
        "calcul": {
            "points": points,
            "delta_moyen": round(sum(deltas) / len(deltas), 4),
            "delta_min": round(min(deltas), 4),
            "delta_max": round(max(deltas), 4),
        },
        "graphe_svg": graphe_svg(
            graphe_pts, "Synergie — méthode ZIP (Zero Interaction Potency)"
        ),
        "limites": LIMITES_COMMUNES + [
            "ZIP repose sur l'ajustement des courbes individuelles : un "
            "mauvais ajustement (R² faible) invalide l'attente ZIP.",
            "Contrairement à Bliss (observations brutes), ZIP lisse les "
            "données par le modèle de Hill : un écart Bliss/ZIP important "
            "signale des données individuelles bruitées ou non monotones.",
        ],
        "interpretation": INTERPRETATION_PRUDENTE,
    }
