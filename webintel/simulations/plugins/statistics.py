"""Statistiques descriptives sur des observations expérimentales.

Moteur ``statistics`` — version 1.0.0.

RÈGLE DURE — honnêteté scientifique
----------------------------------
Ce moteur est PUREMENT DESCRIPTIF : il résume les observations fournies
(effectif, moyenne, médiane, dispersion, minimum, maximum, intervalle de
confiance de la moyenne). Il ne permet de conclure ni à un effet d'un composé ou
d'une plante, ni à aucune relation de causalité.

- Chaque valeur calculée porte sa méthode de calcul.
- L'intervalle de confiance de la moyenne est une approximation normale ;
  il reste approximatif pour les petits effectifs (n < 30 environ).
- Le résultat dépend entièrement des données fournies : des observations
  biaisées ou non représentatives produisent des résumés biaisés.

Technique : bibliothèque standard uniquement (module ``statistics``),
déterministe, sortie 100 % JSON-sérialisable, aucun accès base de données.
"""

from __future__ import annotations

import math
import statistics

ENGINE_NAME = "statistics"
ENGINE_VERSION = "1.0.0"
DESCRIPTION = (
    "Statistiques descriptives (n, moyenne, médiane, écart-type échantillon, "
    "min, max, intervalle de confiance de la moyenne) sur des observations "
    "expérimentales fournies en entrée. Purement descriptif : ne permet de "
    "conclure ni à un effet ni à une causalité."
)

INPUTS = {
    "observations": (
        "Liste de 1 à 5000 observations expérimentales. Chaque observation "
        "est un objet {valeur: nombre (obligatoire), unite: chaîne "
        "(optionnel, ex. 'mg/mL'), libelle: chaîne (optionnel, ex. "
        "'réplicat 1')}. Les valeurs doivent être finies (ni NaN ni infini)."
    ),
}

PARAMETERS = {
    "niveau_confiance": {
        "type": "float",
        "default": 0.95,
        "min": 0.5,
        "max": 0.99,
        "description": (
            "Niveau de confiance de l'intervalle de confiance de la moyenne "
            "(ex. 0.95 = 95 %). Le quantile z est déduit de ce niveau par "
            "l'approximation normale (z=1.96 pour 0.95)."
        ),
    },
}

LIMITATIONS = [
    "Statistiques purement descriptives : elles résument les observations "
    "fournies et ne permettent de conclure ni à un effet ni à une causalité.",
    "L'intervalle de confiance de la moyenne est une approximation normale ; "
    "il est peu fiable pour les petits effectifs (n < 30 environ) et suppose "
    "des observations approximativement indépendantes.",
    "Avec un seul point (n=1), l'écart-type et l'intervalle de confiance "
    "sont non calculables.",
    "Les résultats dépendent entièrement des données fournies : des "
    "observations biaisées ou non représentatives produisent des résumés "
    "biaisés.",
]

#: Bornes de taille imposées par le contrat (normalisation).
_MIN_OBSERVATIONS = 1
_MAX_OBSERVATIONS = 5000


def is_available() -> tuple[bool, str]:
    """Le moteur n'utilise que la bibliothèque standard : toujours disponible."""
    return True, "Bibliothèque standard Python (module statistics) — toujours disponible."


def _est_nombre(valeur: object) -> bool:
    """Vrai si `valeur` est un nombre réel utilisable (booléens exclus)."""
    return isinstance(valeur, (int, float)) and not isinstance(valeur, bool)


def validate_inputs(inputs: dict, params: dict) -> dict:
    """Valide et normalise les entrées et paramètres.

    Lève ``ValueError`` avec un message explicite en français si une entrée
    est invalide. Retourne ``{"inputs": ..., "params": ...}`` normalisés.
    """
    if not isinstance(inputs, dict):
        raise ValueError("Les entrées doivent être un objet {'observations': [...]}.")
    observations = inputs.get("observations")
    if not isinstance(observations, list):
        raise ValueError("'observations' doit être une liste d'observations.")
    if not (_MIN_OBSERVATIONS <= len(observations) <= _MAX_OBSERVATIONS):
        raise ValueError(
            f"'observations' doit contenir entre {_MIN_OBSERVATIONS} et "
            f"{_MAX_OBSERVATIONS} observations (reçu : {len(observations)})."
        )

    normalisees: list[dict] = []
    for i, obs in enumerate(observations, start=1):
        if not isinstance(obs, dict):
            raise ValueError(f"Observation n°{i} : un objet est attendu, reçu {type(obs).__name__}.")
        valeur = obs.get("valeur")
        if not _est_nombre(valeur):
            raise ValueError(
                f"Observation n°{i} : 'valeur' doit être un nombre, reçu {valeur!r}."
            )
        if not math.isfinite(valeur):
            raise ValueError(
                f"Observation n°{i} : 'valeur' doit être finie (ni NaN ni infini)."
            )
        unite = obs.get("unite", "")
        if not isinstance(unite, str):
            raise ValueError(f"Observation n°{i} : 'unite' doit être une chaîne.")
        libelle = obs.get("libelle", f"observation n°{i}")
        if not isinstance(libelle, str):
            raise ValueError(f"Observation n°{i} : 'libelle' doit être une chaîne.")
        normalisees.append({"valeur": float(valeur), "unite": unite, "libelle": libelle})

    if not isinstance(params, dict):
        raise ValueError("Les paramètres doivent être un objet (ex. {'niveau_confiance': 0.95}).")
    spec = PARAMETERS["niveau_confiance"]
    niveau = params.get("niveau_confiance", spec["default"])
    if not _est_nombre(niveau):
        raise ValueError(
            f"'niveau_confiance' doit être un nombre, reçu {niveau!r}."
        )
    niveau = float(niveau)
    if not (spec["min"] <= niveau <= spec["max"]):
        raise ValueError(
            f"'niveau_confiance' doit être entre {spec['min']} et {spec['max']} "
            f"(reçu : {niveau})."
        )

    return {
        "inputs": {"observations": normalisees},
        "params": {"niveau_confiance": niveau},
    }


def _z_normal(niveau_confiance: float) -> float:
    """Quantile z bilatéral de la loi normale pour le niveau donné.

    Méthode : fonction de répartition inverse de la loi normale centrée
    réduite (statistics.NormalDist().inv_cdf). Pour 0.95 → z ≈ 1.96.
    """
    return statistics.NormalDist().inv_cdf((1.0 + niveau_confiance) / 2.0)


def run(inputs: dict, params: dict) -> dict:
    """Calcule les statistiques descriptives (déterministe, JSON-sérialisable)."""
    normalise = validate_inputs(inputs, params)
    observations = normalise["inputs"]["observations"]
    niveau = normalise["params"]["niveau_confiance"]
    valeurs = [obs["valeur"] for obs in observations]
    n = len(valeurs)

    z = _z_normal(niveau)
    methode_z = f"approximation normale, z={z:.2f}"

    moyenne = statistics.mean(valeurs)
    resultat: dict = {
        "nature": (
            "statistiques descriptives — résument les observations fournies, "
            "ne permettent de conclure ni à un effet ni à une causalité"
        ),
        "avertissement": (
            "Descriptif uniquement. L'intervalle de confiance est une "
            "approximation normale, approximative pour les petits effectifs."
        ),
        "n": {
            "valeur": n,
            "methode": "comptage des observations fournies",
        },
        "moyenne": {
            "valeur": moyenne,
            "methode": "moyenne arithmétique (module statistics, statistics.mean)",
        },
        "mediane": {
            "valeur": statistics.median(valeurs),
            "methode": "médiane (module statistics, statistics.median)",
        },
        "min": {
            "valeur": min(valeurs),
            "methode": "minimum observé",
        },
        "max": {
            "valeur": max(valeurs),
            "methode": "maximum observé",
        },
    }

    if n == 1:
        resultat["ecart_type"] = {
            "valeur": None,
            "calculable": False,
            "raison": "non calculables (n=1)",
            "methode": "écart-type échantillon (module statistics, statistics.stdev, ddof=1)",
        }
        resultat["intervalle_confiance_moyenne"] = {
            "calculable": False,
            "borne_basse": None,
            "borne_haute": None,
            "raison": "non calculables (n=1)",
            "methode": methode_z,
        }
    else:
        ecart_type = statistics.stdev(valeurs)
        resultat["ecart_type"] = {
            "valeur": ecart_type,
            "calculable": True,
            "methode": "écart-type échantillon (module statistics, statistics.stdev, ddof=1)",
        }
        marge = z * ecart_type / math.sqrt(n)
        resultat["intervalle_confiance_moyenne"] = {
            "calculable": True,
            "borne_basse": moyenne - marge,
            "borne_haute": moyenne + marge,
            "niveau_confiance": niveau,
            "methode": methode_z,
        }

    return resultat
