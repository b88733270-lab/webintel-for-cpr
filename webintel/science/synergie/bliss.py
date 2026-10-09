"""Méthode de Bliss — indépendance de Bliss (Bliss, 1939).

Formule documentée
------------------
Pour un point de mélange (dose A, dose B), avec :
- ``E_A`` : réponse de la molécule A SEULE à la même dose,
- ``E_B`` : réponse de la molécule B SEULE à la même dose,
- ``E_obs`` : réponse mesurée du mélange,

l'attente sous indépendance de Bliss (effets probabilistes indépendants) :

    E_bliss = E_A + E_B − E_A · E_B

et le score delta :

    Δ_bliss = E_obs − E_bliss

Δ > 0 : effet observé supérieur à l'attente (signal de synergie SELON
Bliss) ; Δ < 0 : effet inférieur (signal d'antagonisme SELON Bliss).

Données requises (TOUTES obligatoires, sinon « données insuffisantes
pour la méthode Bliss ») : pour chaque point, ``dose_a``, ``dose_b``,
``reponse_a`` (A seul), ``reponse_b`` (B seul), ``reponse_ab``
(mélange). Réponses en fractions [0, 1] ou pourcentages [0, 100].

Référence : Bliss C.I. (1939) The toxicity of poisons applied jointly.
Ann. Appl. Biol. 26:585-615.
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

NOM = "bliss"
FORMULE = "E_bliss = E_A + E_B − E_A·E_B ; Δ = E_obs − E_bliss"


def donnees_requises() -> list[str]:
    """Champs requis dans ``donnees`` pour appliquer Bliss."""
    return ["points"]


def analyser(donnees: dict, parametres: dict | None = None) -> dict:
    """Applique Bliss à des points de mélange expérimentaux.

    ``donnees = {"points": [{"dose_a", "dose_b", "reponse_a",
    "reponse_b", "reponse_ab", ...}]}``. Chaque point sans les 5 valeurs
    numériques requises fait échouer l'analyse entière avec un message
    explicite (jamais de calcul partiel silencieux).
    """
    parametres = parametres or {}
    points = donnees.get("points")
    if not isinstance(points, list) or not points:
        raise DonneesInsuffisantes(
            "données insuffisantes pour la méthode Bliss : la liste "
            "« points » (dose_a, dose_b, reponse_a, reponse_b, reponse_ab) "
            "est absente ou vide."
        )
    resultats_points = []
    for i, point in enumerate(points):
        if not isinstance(point, dict):
            raise DonneesInsuffisantes(
                "données insuffisantes pour la méthode Bliss : le point "
                f"n°{i + 1} n'est pas un objet valide."
            )
        for cle in ("reponse_a", "reponse_b", "reponse_ab"):
            if point.get(cle) is None:
                raise DonneesInsuffisantes(
                    "données insuffisantes pour la méthode Bliss : "
                    f"point n°{i + 1} — « {cle} » manquante (réponse de A "
                    "seul, de B seul et du mélange requises)."
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
                "données insuffisantes pour la méthode Bliss : "
                f"point n°{i + 1} incomplet — {exc}"
            ) from exc
        e_att = e_a + e_b - e_a * e_b
        delta = e_obs - e_att
        resultats_points.append({
            "point": i + 1,
            "dose_a": dose_a,
            "dose_b": dose_b,
            "reponse_a": round(e_a, 4),
            "reponse_b": round(e_b, 4),
            "reponse_observee": round(e_obs, 4),
            "reponse_attendue_bliss": round(e_att, 4),
            "delta": round(delta, 4),
            "signal": (
                "synergie (selon Bliss)" if delta > 0.02
                else "antagonisme (selon Bliss)" if delta < -0.02
                else "additivité (selon Bliss)"
            ),
        })
    deltas = [p["delta"] for p in resultats_points]
    return {
        "methode": NOM,
        "formule": FORMULE,
        "reference": "Bliss C.I. (1939) Ann. Appl. Biol. 26:585-615.",
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
            resultats_points, "Synergie — méthode de Bliss"
        ),
        "limites": LIMITES_COMMUNES + [
            "Bliss suppose des modes d'action indépendants (probabilités "
            "multiplicatives) : inadapté si les deux molécules partagent "
            "la même cible.",
        ],
        "interpretation": INTERPRETATION_PRUDENTE,
    }
