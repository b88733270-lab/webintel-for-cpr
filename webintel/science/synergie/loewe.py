"""Méthode de Loewe — additivité de Loewe + indice de combinaison (Chou-Talalay).

Formules documentées
--------------------
1. Additivité de Loewe (Loewe, 1953) : pour un effet donné ``y``, si
   ``D_A`` est la dose de A seul donnant ``y`` et ``D_B`` la dose de B
   seul donnant ``y``, un mélange (d_A, d_B) est additif quand :

       d_A / D_A + d_B / D_B = 1

2. Indice de combinaison CI (Chou & Talalay, 1984) pour un point de
   mélange (d_A, d_B) d'effet observé ``y_obs`` :

       CI = d_A / D_A(y_obs) + d_B / D_B(y_obs)

   où D_A(y_obs), D_B(y_obs) sont obtenus par INVERSION des courbes
   dose-réponse ajustées (modèle de Hill, voir ``commun``).

   CI < 1 : signal de synergie (selon Loewe) ; CI = 1 : additivité ;
   CI > 1 : signal d'antagonisme (selon Loewe).

Données requises : courbes dose-réponse des DEUX molécules seules
(``courbe_a`` / ``courbe_b`` : listes de {"dose", "reponse"}, ≥ 4
points chacune) OU paramètres ajustés (``{"ic50", "pente"}``), PLUS au
moins un point de mélange ``{"dose_a", "dose_b", "reponse"}``. Sans ces
données : « données insuffisantes pour la méthode Loewe ».

Références : Loewe S. (1953) Arzneimittel-Forsch. 3:285-290 ;
Chou T.-C., Talalay P. (1984) Adv. Enzyme Regul. 22:27-55.
"""

from __future__ import annotations

from .commun import (
    DonneesInsuffisantes,
    INTERPRETATION_PRUDENTE,
    LIMITES_COMMUNES,
    ajuster_hill,
    graphe_svg,
    inverser_hill,
    normaliser_reponse,
    normaliser_serie,
    valider_dose,
)

NOM = "loewe"
FORMULE = "CI = d_A/D_A(y) + d_B/D_B(y) ; CI<1 synergie, CI=1 additivité, CI>1 antagonisme"


def donnees_requises() -> list[str]:
    return ["courbe_a", "courbe_b", "melanges"]


def _courbe_ou_parametres(
    donnees: dict, cle: str, nom: str, methode: str = "Loewe"
) -> dict:
    """Ajuste (ou valide) la courbe dose-réponse d'une molécule seule.

    Accepte ``donnees[cle]`` = liste de {"dose","reponse"} (ajustement
    Hill) ou ``donnees[cle + "_parametres"]`` = {"ic50","pente"} déjà
    ajustés (réutilisation documentée, sans ré-ajustement silencieux).
    """
    prefixe = f"données insuffisantes pour la méthode {methode} : "
    params = donnees.get(f"{cle}_parametres")
    if isinstance(params, dict) and params.get("ic50") and params.get("pente"):
        ic50 = valider_dose(params["ic50"], f"{nom} : ic50")
        pente = float(params["pente"])
        if pente <= 0:
            raise DonneesInsuffisantes(
                prefixe + f"{nom} — la pente de Hill doit être > 0."
            )
        return {"ic50": ic50, "pente": pente, "r2": None, "n": None,
                "source": "parametres fournis"}
    courbe = donnees.get(cle)
    if not isinstance(courbe, list) or len(courbe) < 4:
        raise DonneesInsuffisantes(
            prefixe + f"« {cle} » (courbe dose-réponse de {nom} seul, "
            "≥ 4 points) absente ou incomplète."
        )
    doses, reponses = [], []
    for i, pt in enumerate(courbe):
        if not isinstance(pt, dict) or pt.get("dose") is None \
                or pt.get("reponse") is None:
            raise DonneesInsuffisantes(
                prefixe + f"« {cle} » point n°{i + 1} incomplet "
                "(dose + reponse requis)."
            )
        doses.append(valider_dose(pt["dose"], f"{cle} point n°{i + 1} : dose"))
        reponses.append(normaliser_reponse(pt["reponse"]))
    ajuste = ajuster_hill(doses, reponses)
    ajuste["source"] = "ajustement Hill (Emin=0, Emax=1)"
    return ajuste


def analyser(donnees: dict, parametres: dict | None = None) -> dict:
    """Applique Loewe (indice de combinaison) à des mélanges expérimentaux."""
    parametres = parametres or {}
    courbe_a = _courbe_ou_parametres(donnees, "courbe_a", "molécule A")
    courbe_b = _courbe_ou_parametres(donnees, "courbe_b", "molécule B")
    melanges = donnees.get("melanges")
    if not isinstance(melanges, list) or not melanges:
        raise DonneesInsuffisantes(
            "données insuffisantes pour la méthode Loewe : la liste "
            "« melanges » (dose_a, dose_b, reponse) est absente ou vide."
        )
    points, ignores = [], []
    for i, mel in enumerate(melanges):
        if not isinstance(mel, dict) or mel.get("dose_a") is None \
                or mel.get("dose_b") is None or mel.get("reponse") is None:
            raise DonneesInsuffisantes(
                "données insuffisantes pour la méthode Loewe : "
                f"mélange n°{i + 1} incomplet (dose_a, dose_b, reponse requis)."
            )
        d_a = valider_dose(mel["dose_a"], f"mélange n°{i + 1} : dose_a")
        d_b = valider_dose(mel["dose_b"], f"mélange n°{i + 1} : dose_b")
        y = normaliser_serie([normaliser_reponse(mel["reponse"])])[0]
        d_a_equiv = inverser_hill(y, courbe_a["ic50"], courbe_a["pente"])
        d_b_equiv = inverser_hill(y, courbe_b["ic50"], courbe_b["pente"])
        if d_a_equiv is None or d_b_equiv is None:
            ignores.append({
                "melange": i + 1,
                "raison": "effet observé hors de ]0, 1[ : inversion de la "
                          "courbe de Hill impossible (pas d'extrapolation).",
            })
            continue
        ci = d_a / d_a_equiv + d_b / d_b_equiv
        points.append({
            "melange": i + 1,
            "dose_a": d_a,
            "dose_b": d_b,
            "reponse_observee": round(y, 4),
            "dose_a_equivalente": round(d_a_equiv, 4),
            "dose_b_equivalente": round(d_b_equiv, 4),
            "ci": round(ci, 4),
            "signal": (
                "synergie (selon Loewe)" if ci < 0.95
                else "antagonisme (selon Loewe)" if ci > 1.05
                else "additivité (selon Loewe)"
            ),
        })
    if not points:
        raise DonneesInsuffisantes(
            "données insuffisantes pour la méthode Loewe : aucun point de "
            "mélange exploitable (tous les effets observés sont hors de "
            "]0, 1[, inversion impossible)."
        )
    ci_vals = [p["ci"] for p in points]
    # Graphe : on trace (1 − CI) comme « delta » pour une lecture homogène
    # avec les autres méthodes (positif = signal de synergie).
    graphe_pts = [
        {"dose_a": p["dose_a"], "dose_b": p["dose_b"],
         "delta": 1.0 - p["ci"]}
        for p in points
    ]
    return {
        "methode": NOM,
        "formule": FORMULE,
        "reference": "Loewe S. (1953) ; Chou T.-C., Talalay P. (1984).",
        "donnees_utilisees": {
            "courbe_a": {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in courbe_a.items()},
            "courbe_b": {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in courbe_b.items()},
            "nb_melanges_exploites": len(points),
            "nb_melanges_ignores": len(ignores),
            "origine": "résultat expérimental fourni par le laboratoire",
        },
        "calcul": {
            "points": points,
            "points_ignores": ignores,
            "ci_moyen": round(sum(ci_vals) / len(ci_vals), 4),
            "ci_min": round(min(ci_vals), 4),
            "ci_max": round(max(ci_vals), 4),
        },
        "graphe_svg": graphe_svg(
            graphe_pts,
            "Synergie — méthode de Loewe (1 − CI affiché : > 0 = signal de synergie)",
        ),
        "limites": LIMITES_COMMUNES + [
            "Loewe suppose des courbes dose-réponse de même forme "
            "(pentes de Hill comparables) : des pentes très différentes "
            "rendent l'indice de combinaison fragile.",
            "La qualité du CI dépend de l'ajustement des courbes "
            "individuelles (voir R² ci-dessus) : un mauvais ajustement "
            "invalide le CI.",
            "Les points à effet 0 ou 1 (saturation) sont ignorés, pas "
            "extrapolés.",
        ],
        "interpretation": INTERPRETATION_PRUDENTE,
    }
