"""Méthode HSA — Highest Single Agent (Borisy et al., 2003).

Formule documentée
------------------
Pour un point de mélange, avec ``E_A`` (A seul à la même dose), ``E_B``
(B seul à la même dose) et ``E_obs`` (mélange mesuré) :

    E_hsa = max(E_A, E_B)
    Δ_hsa = E_obs − E_hsa

Δ > 0 : le mélange dépasse le meilleur des deux agents seuls (signal de
synergie SELON HSA). HSA est le modèle de référence le plus simple et
le plus conservateur : il ne détecte que les gains au-delà du meilleur
agent individuel.

Données requises : identiques à Bliss (``dose_a``, ``dose_b``,
``reponse_a``, ``reponse_b``, ``reponse_ab`` par point).

Référence : Borisy A.A. et al. (2003) Systematic discovery of
multicomponent therapeutics. PNAS 100(13):7977-7982.
"""

from __future__ import annotations

from .commun import (
    DonneesInsuffisantes,
    INTERPRETATION_PRUDENTE,
    LIMITES_COMMUNES,
    graphe_svg,
    normaliser_serie,
    valider_dose,
)

NOM = "hsa"
FORMULE = "E_hsa = max(E_A, E_B) ; Δ = E_obs − E_hsa"


def donnees_requises() -> list[str]:
    return ["points"]


def analyser(donnees: dict, parametres: dict | None = None) -> dict:
    """Applique HSA à des points de mélange expérimentaux (mêmes entrées que Bliss)."""
    parametres = parametres or {}
    points = donnees.get("points")
    if not isinstance(points, list) or not points:
        raise DonneesInsuffisantes(
            "données insuffisantes pour la méthode HSA : la liste "
            "« points » (dose_a, dose_b, reponse_a, reponse_b, reponse_ab) "
            "est absente ou vide."
        )
    resultats_points = []
    for i, point in enumerate(points):
        if not isinstance(point, dict):
            raise DonneesInsuffisantes(
                "données insuffisantes pour la méthode HSA : le point "
                f"n°{i + 1} n'est pas un objet valide."
            )
        for cle in ("reponse_a", "reponse_b", "reponse_ab"):
            if point.get(cle) is None:
                raise DonneesInsuffisantes(
                    "données insuffisantes pour la méthode HSA : "
                    f"point n°{i + 1} — « {cle} » manquante."
                )
        try:
            dose_a = valider_dose(point.get("dose_a"), f"point n°{i + 1} : dose_a")
            dose_b = valider_dose(point.get("dose_b"), f"point n°{i + 1} : dose_b")
            (e_a, e_b, e_obs) = normaliser_serie(
                [point.get("reponse_a"), point.get("reponse_b"),
                 point.get("reponse_ab")]
            )
        except DonneesInsuffisantes as exc:
            raise DonneesInsuffisantes(
                "données insuffisantes pour la méthode HSA : "
                f"point n°{i + 1} incomplet — {exc}"
            ) from exc
        e_att = max(e_a, e_b)
        delta = e_obs - e_att
        resultats_points.append({
            "point": i + 1,
            "dose_a": dose_a,
            "dose_b": dose_b,
            "reponse_a": round(e_a, 4),
            "reponse_b": round(e_b, 4),
            "reponse_observee": round(e_obs, 4),
            "reponse_attendue_hsa": round(e_att, 4),
            "delta": round(delta, 4),
            "signal": (
                "synergie (selon HSA)" if delta > 0.02
                else "antagonisme (selon HSA)" if delta < -0.02
                else "additivité (selon HSA)"
            ),
        })
    deltas = [p["delta"] for p in resultats_points]
    return {
        "methode": NOM,
        "formule": FORMULE,
        "reference": "Borisy A.A. et al. (2003) PNAS 100(13):7977-7982.",
        "donnees_utilisees": {
            "nb_points": len(resultats_points),
            "champs_par_point": ["dose_a", "dose_b", "reponse_a",
                                 "reponse_b", "reponse_ab"],
            "origine": "résultat expérimental fourni par le laboratoire",
        },
        "calcul": {
            "points": resultats_points,
            "delta_moyen": round(sum(deltas) / len(deltas), 4),
            "delta_min": round(min(deltas), 4),
            "delta_max": round(max(deltas), 4),
        },
        "graphe_svg": graphe_svg(
            resultats_points, "Synergie — méthode HSA (Highest Single Agent)"
        ),
        "limites": LIMITES_COMMUNES + [
            "HSA est volontairement conservateur : un mélange simplement "
            "additif mais inférieur au meilleur agent seul sera classé "
            "« antagonisme (selon HSA) » alors que Bliss le classerait "
            "autrement.",
        ],
        "interpretation": INTERPRETATION_PRUDENTE,
    }
