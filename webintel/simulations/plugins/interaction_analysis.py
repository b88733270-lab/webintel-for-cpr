"""Analyse DESCRIPTIVE du réseau composé↔cible à partir de liens enregistrés.

Moteur ``interaction_analysis`` — version 1.0.0.

RÈGLE DURE — aucune inférence
-----------------------------
Ce moteur n'infère aucun lien ; seuls les liens fournis sont analysés.
Il est INTERDIT de prédire un lien non enregistré ou de calculer une
« probabilité d'interaction » : chaque sortie est étiquetée
« descriptif des liens enregistrés ».

Le plugin est pur : il ne lit ni ne modifie aucune base de données. Le
service d'exécution fournit les liens déjà enregistrés dans les entrées.

Technique : bibliothèque standard uniquement, déterministe, sortie 100 %
JSON-sérialisable.
"""

from __future__ import annotations

import math

ENGINE_NAME = "interaction_analysis"
ENGINE_VERSION = "1.0.0"
DESCRIPTION = (
    "Analyse descriptive du réseau composé↔cible à partir de liens déjà "
    "enregistrés (comptages, tops, distribution des confiances, matrice de "
    "présence). N'infère aucun lien ; seuls les liens fournis sont analysés."
)

INPUTS = {
    "liens": (
        "Liste de 0 à 5000 liens composé↔cible déjà enregistrés. Chaque lien "
        "est un objet {compose_id: chaîne (optionnel si compose_nom fourni), "
        "compose_nom: chaîne (optionnel si compose_id fourni), cible: chaîne "
        "(obligatoire), type_lien: chaîne (optionnel, ex. 'inhibition'), "
        "confiance: nombre entre 0 et 1 (optionnel), reference: chaîne "
        "(optionnel, ex. 'PMID:123')}. Au moins l'un de compose_id / "
        "compose_nom doit être renseigné (compose_nom est prioritaire)."
    ),
}

PARAMETERS = {
    "seuil_confiance": {
        "type": "float",
        "default": 0.0,
        "min": 0.0,
        "max": 1.0,
        "description": (
            "Seuil minimal de confiance : seuls les liens dont la confiance "
            "est supérieure ou égale au seuil sont analysés. Les liens sans "
            "confiance renseignée sont traités comme confiance 0 (donc exclus "
            "dès que le seuil est > 0). Par défaut 0.0 : tous les liens sont "
            "analysés."
        ),
    },
}

LIMITATIONS = [
    "n'infère aucun lien ; seuls les liens fournis sont analysés — ce moteur "
    "ne prédit aucun lien non enregistré et ne calcule aucune « probabilité "
    "d'interaction ».",
    "Les comptages et la matrice reflètent uniquement les liens enregistrés "
    "fournis en entrée : un réseau incomplet ou biaisé donne une image "
    "incomplète ou biaisée, jamais une preuve d'absence d'interaction.",
    "La confiance est une métadonnée enregistrée (qualité de la source), pas "
    "une mesure d'activité biologique ni une probabilité.",
]

#: Libellé apposé sur chaque sortie, comme exigé par la règle d'honnêteté.
NATURE_SORTIE = "descriptif des liens enregistrés"

#: Avertissement anti-inférence, rappelé dans chaque résultat.
AVERTISSEMENT = "n'infère aucun lien ; seuls les liens fournis sont analysés"

#: Bornes de taille imposées par le contrat (normalisation).
_MIN_LIENS = 0
_MAX_LIENS = 5000

#: Tranches de la distribution des niveaux de confiance.
_TRANCHES_CONFIANCE = (
    "0,00 – 0,25",
    "0,25 – 0,50",
    "0,50 – 0,75",
    "0,75 – 1,00",
)


def is_available() -> tuple[bool, str]:
    """Le moteur n'utilise que la bibliothèque standard : toujours disponible."""
    return True, "Bibliothèque standard Python — toujours disponible."


def _est_nombre(valeur: object) -> bool:
    """Vrai si `valeur` est un nombre réel utilisable (booléens exclus)."""
    return isinstance(valeur, (int, float)) and not isinstance(valeur, bool)


def _chaine_optionnelle(valeur: object, champ: str, numero: int) -> str:
    if valeur is None:
        return ""
    if not isinstance(valeur, str):
        raise ValueError(f"Lien n°{numero} : '{champ}' doit être une chaîne.")
    return valeur


def validate_inputs(inputs: dict, params: dict) -> dict:
    """Valide et normalise les entrées et paramètres.

    Lève ``ValueError`` avec un message explicite en français si une entrée
    est invalide. Retourne ``{"inputs": ..., "params": ...}`` normalisés.
    """
    if not isinstance(inputs, dict):
        raise ValueError("Les entrées doivent être un objet {'liens': [...]}.")
    liens = inputs.get("liens")
    if not isinstance(liens, list):
        raise ValueError("'liens' doit être une liste de liens composé↔cible.")
    if not (_MIN_LIENS <= len(liens) <= _MAX_LIENS):
        raise ValueError(
            f"'liens' doit contenir entre {_MIN_LIENS} et {_MAX_LIENS} liens "
            f"(reçu : {len(liens)})."
        )

    normalises: list[dict] = []
    for i, lien in enumerate(liens, start=1):
        if not isinstance(lien, dict):
            raise ValueError(f"Lien n°{i} : un objet est attendu, reçu {type(lien).__name__}.")
        nom = lien.get("compose_nom")
        identifiant = lien.get("compose_id")
        compose = (nom.strip() if isinstance(nom, str) else "") or (
            identifiant.strip() if isinstance(identifiant, str) else ""
        )
        if not compose:
            raise ValueError(
                f"Lien n°{i} : 'compose_nom' ou 'compose_id' doit être renseigné "
                "(chaîne non vide)."
            )
        cible = lien.get("cible")
        if not isinstance(cible, str) or not cible.strip():
            raise ValueError(f"Lien n°{i} : 'cible' doit être une chaîne non vide.")
        confiance = lien.get("confiance")
        if confiance is not None:
            if not _est_nombre(confiance):
                raise ValueError(
                    f"Lien n°{i} : 'confiance' doit être un nombre entre 0 et 1, "
                    f"reçu {confiance!r}."
                )
            confiance = float(confiance)
            if not (math.isfinite(confiance) and 0.0 <= confiance <= 1.0):
                raise ValueError(
                    f"Lien n°{i} : 'confiance' doit être entre 0 et 1 "
                    f"(reçu : {confiance})."
                )
        normalises.append(
            {
                "compose": compose,
                "cible": cible.strip(),
                "type_lien": _chaine_optionnelle(lien.get("type_lien"), "type_lien", i),
                "confiance": confiance,
                "reference": _chaine_optionnelle(lien.get("reference"), "reference", i),
            }
        )

    if not isinstance(params, dict):
        raise ValueError("Les paramètres doivent être un objet (ex. {'seuil_confiance': 0.5}).")
    spec = PARAMETERS["seuil_confiance"]
    seuil = params.get("seuil_confiance", spec["default"])
    if not _est_nombre(seuil):
        raise ValueError(f"'seuil_confiance' doit être un nombre, reçu {seuil!r}.")
    seuil = float(seuil)
    if not (spec["min"] <= seuil <= spec["max"]):
        raise ValueError(
            f"'seuil_confiance' doit être entre {spec['min']} et {spec['max']} "
            f"(reçu : {seuil})."
        )

    return {
        "inputs": {"liens": normalises},
        "params": {"seuil_confiance": seuil},
    }


def _tranche(confiance: float) -> int:
    """Indice de tranche (0..3) pour un niveau de confiance dans [0, 1]."""
    return min(int(confiance * 4), 3)


def run(inputs: dict, params: dict) -> dict:
    """Analyse descriptive des liens fournis (déterministe, JSON-sérialisable).

    Aucun lien n'est inféré : seules des paires (composé, cible) présentes
    dans les entrées apparaissent dans les sorties.
    """
    normalise = validate_inputs(inputs, params)
    seuil = normalise["params"]["seuil_confiance"]
    # Filtrage documenté : confiance manquante = traitée comme 0.
    liens = [
        lien
        for lien in normalise["inputs"]["liens"]
        if (lien["confiance"] if lien["confiance"] is not None else 0.0) >= seuil
    ]

    composes_tries = sorted({lien["compose"] for lien in liens})
    cibles_triees = sorted({lien["cible"] for lien in liens})

    # --- Comptages par cible (tri déterministe : nb décroissant, nom croissant)
    par_cible: dict[str, dict] = {}
    for lien in liens:
        entree = par_cible.setdefault(lien["cible"], {"nb_liens": 0, "composes": set()})
        entree["nb_liens"] += 1
        entree["composes"].add(lien["compose"])
    top_cibles = [
        {
            "cible": cible,
            "nb_liens": donnees["nb_liens"],
            "composes": sorted(donnees["composes"]),
            "methode": "comptage des liens enregistrés pour cette cible",
            "nature": NATURE_SORTIE,
        }
        for cible, donnees in sorted(
            par_cible.items(), key=lambda kv: (-kv[1]["nb_liens"], kv[0])
        )
    ]

    # --- Comptages par composé (tri déterministe : nb décroissant, nom croissant)
    par_compose: dict[str, set[str]] = {}
    for lien in liens:
        par_compose.setdefault(lien["compose"], set()).add(lien["cible"])
    top_composes = [
        {
            "compose": compose,
            "nb_cibles": len(cibles),
            "cibles": sorted(cibles),
            "methode": "comptage des cibles distinctes liées à ce composé",
            "nature": NATURE_SORTIE,
        }
        for compose, cibles in sorted(
            par_compose.items(), key=lambda kv: (-len(kv[1]), kv[0])
        )
    ]

    # --- Distribution des niveaux de confiance (liens renseignés uniquement)
    comptes_tranches = [0, 0, 0, 0]
    non_renseignee = 0
    for lien in liens:
        if lien["confiance"] is None:
            non_renseignee += 1
        else:
            comptes_tranches[_tranche(lien["confiance"])] += 1
    distribution_confiance = {
        "tranches": [
            {
                "tranche": libelle,
                "nb_liens": comptes_tranches[i],
                "methode": "comptage des liens enregistrés par tranche de confiance",
            }
            for i, libelle in enumerate(_TRANCHES_CONFIANCE)
        ],
        "non_renseignee": {
            "nb_liens": non_renseignee,
            "methode": "comptage des liens enregistrés sans confiance renseignée",
        },
        "nature": NATURE_SORTIE,
    }

    # --- Matrice composé × cible : uniquement les paires ENREGISTRÉES.
    # Aucune paire absente des entrées n'est créée (aucune inférence).
    matrice: dict[str, dict[str, dict]] = {}
    for compose in composes_tries:
        ligne: dict[str, dict] = {}
        for cible in cibles_triees:
            confiances = [
                lien["confiance"]
                for lien in liens
                if lien["compose"] == compose
                and lien["cible"] == cible
                and lien["confiance"] is not None
            ]
            if any(
                lien["compose"] == compose and lien["cible"] == cible for lien in liens
            ):
                ligne[cible] = {
                    "presence": True,
                    "confiance_max": max(confiances) if confiances else None,
                    "methode": "présence relevée dans les liens fournis ; confiance max enregistrée",
                }
        if ligne:
            matrice[compose] = ligne

    return {
        "nature": NATURE_SORTIE,
        "avertissement": AVERTISSEMENT,
        "seuil_confiance_applique": {
            "valeur": seuil,
            "methode": "filtrage des liens fournis (confiance manquante traitée comme 0)",
        },
        "nb_liens_analyses": {
            "valeur": len(liens),
            "methode": "comptage des liens fournis après filtrage par seuil",
            "nature": NATURE_SORTIE,
        },
        "nb_composes_distincts": {
            "valeur": len(composes_tries),
            "methode": "comptage des identifiants de composés distincts",
            "nature": NATURE_SORTIE,
        },
        "nb_cibles_distinctes": {
            "valeur": len(cibles_triees),
            "methode": "comptage des cibles distinctes",
            "nature": NATURE_SORTIE,
        },
        "top_cibles": top_cibles,
        "top_composes": top_composes,
        "distribution_confiance": distribution_confiance,
        "matrice_compose_cible": matrice,
    }
