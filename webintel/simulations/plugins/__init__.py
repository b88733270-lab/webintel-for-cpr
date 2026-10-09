"""Contrat PLUGIN pour les moteurs de simulation WebIntel.

Tout moteur de simulation (implémenté par les agents en charge de chaque
moteur) DOIT respecter ce contrat. Un module Python qui déclare un moteur
doit définir, au niveau du module :

Attributs descriptifs
---------------------
- ``ENGINE_NAME: str`` — nom technique du moteur (ex. ``"molecular_similarity"``).
- ``ENGINE_VERSION: str`` — version du moteur (ex. ``"1.0.0"``).
- ``DESCRIPTION: str`` — description en français du rôle du moteur.

- ``INPUTS: dict`` — description des entrées attendues, de la forme
  ``{nom_champ: description_en_francais}``.
- ``PARAMETERS: dict`` — paramètres réglables, de la forme
  ``{nom: {"type": str, "default": Any, "min": Any|None, "max": Any|None,
  "description": str}}``. ``type`` est l'un de ``"int"``, ``"float"``,
  ``"bool"``, ``"str"``, ``"list"`` ou ``"choice"`` (pour ``"choice"``,
  ``"min"``/``"max"`` ne s'appliquent pas et une clé ``"choices"`` liste
  les valeurs autorisées).
- ``LIMITATIONS: list[str]`` — limites honnêtes du moteur, rédigées en
  français (ex. « RDKit n'est pas utilisé : les empreintes sont calculées
  par une méthode interne simplifiée, moins fiable qu'un calcul chimique
  de référence. »).

Fonctions
---------
- ``is_available() -> tuple[bool, str]`` — retourne ``(True, "")`` si le
  moteur peut s'exécuter ici et maintenant, sinon ``(False, raison)`` où
  ``raison`` explique en français pourquoi (ex. dépendance optionnelle
  manquante). Ne lève JAMAIS.
- ``validate_inputs(inputs: dict, params: dict) -> dict`` — valide et
  normalise les entrées et paramètres ; retourne
  ``{"inputs": <dict normalisé>, "params": <dict normalisé>}``.
  Applique les valeurs par défaut, borne les tailles (ex. maximum
  200 composés par appel), convertit les types attendus, lève ``ValueError``
  avec un message explicite en français si une entrée est invalide.
- ``run(inputs: dict, params: dict) -> dict`` — exécute la simulation.
  Garanties OBLIGATOIRES :
    - DÉTERMINISTE : mêmes entrées + mêmes paramètres → même sortie,
      bit à bit (graines aléatoires fixes, pas d'horodatage dans la sortie) ;
    - sortie 100 % JSON-sérialisable (types : dict, list, str, int, float,
      bool, None) ;
    - AUCUNE exécution de code arbitraire (pas d'``eval``/``exec``) ;
    - AUCUN appel shell, AUCUN accès réseau, AUCUN accès fichier
      en dehors d'éventuelles données embarquées en lecture seule ;
    - ne modifie jamais la base de données.

La fonction utilitaire ``check_plugin(module)`` ci-dessous vérifie
mécaniquement qu'un module respecte ce contrat et retourne la liste des
problèmes trouvés (liste vide = conforme).
"""

from __future__ import annotations

import types

#: Taille maximale par défaut d'une collection d'entrées (ex. composés).
MAX_INPUT_ITEMS = 200

#: Types autorisés pour la clé "type" des paramètres.
PARAM_TYPES = {"int", "float", "bool", "str", "list", "choice"}


def check_plugin(module: object) -> list[str]:
    """Vérifie qu'un module respecte le contrat plugin.

    Retourne la liste des problèmes détectés (chaînes en français) ;
    une liste vide signifie que le module est conforme.
    Ne lève jamais : un module mal formé produit des problèmes, pas
    d'exceptions.
    """
    problemes: list[str] = []

    def _est_module(obj: object) -> bool:
        return isinstance(obj, types.ModuleType)

    if not _est_module(module):
        return ["Le plugin doit être un module Python."]

    # --- Attributs descriptifs ---
    for attr in ("ENGINE_NAME", "ENGINE_VERSION", "DESCRIPTION"):
        valeur = getattr(module, attr, None)
        if not isinstance(valeur, str) or not valeur.strip():
            problemes.append(
                f"Attribut {attr!r} manquant ou invalide : chaîne non vide requise."
            )

    inputs = getattr(module, "INPUTS", None)
    if not isinstance(inputs, dict) or not inputs:
        problemes.append(
            "Attribut 'INPUTS' manquant ou invalide : dict non vide requis "
            "({nom_champ: description_en_francais})."
        )
    else:
        for cle, desc in inputs.items():
            if not isinstance(cle, str) or not isinstance(desc, str) or not desc.strip():
                problemes.append(
                    f"Entrée {cle!r} mal décrite : description en français requise."
                )

    params = getattr(module, "PARAMETERS", None)
    if not isinstance(params, dict):
        problemes.append("Attribut 'PARAMETERS' manquant ou invalide : dict requis.")
    else:
        for nom, spec in params.items():
            if not isinstance(spec, dict):
                problemes.append(
                    f"Paramètre {nom!r} mal spécifié : dict requis avec les clés "
                    "'type', 'default', 'min', 'max', 'description'."
                )
                continue
            for cle in ("type", "default", "min", "max", "description"):
                if cle not in spec:
                    problemes.append(
                        f"Paramètre {nom!r} : clé {cle!r} manquante dans sa spécification."
                    )
            if spec.get("type") not in PARAM_TYPES:
                problemes.append(
                    f"Paramètre {nom!r} : 'type' doit être l'un de "
                    f"{sorted(PARAM_TYPES)}, trouvé {spec.get('type')!r}."
                )
            if not isinstance(spec.get("description"), str) or not spec.get(
                "description", ""
            ).strip():
                problemes.append(
                    f"Paramètre {nom!r} : 'description' en français requise."
                )

    limitations = getattr(module, "LIMITATIONS", None)
    if not isinstance(limitations, list) or not limitations:
        problemes.append(
            "Attribut 'LIMITATIONS' manquant ou invalide : liste non vide de "
            "chaînes en français requise."
        )
    else:
        for i, lim in enumerate(limitations):
            if not isinstance(lim, str) or not lim.strip():
                problemes.append(
                    f"LIMITATIONS[{i}] : chaîne non vide en français requise."
                )

    # --- Fonctions ---
    for nom_fonction in ("is_available", "validate_inputs", "run"):
        fonction = getattr(module, nom_fonction, None)
        if not callable(fonction):
            problemes.append(
                f"Fonction {nom_fonction!r} manquante : elle doit être définie "
                "et appelable."
            )

    # --- Vérification comportementale prudente ---
    if callable(getattr(module, "is_available", None)):
        try:
            resultat = module.is_available()
        except Exception as exc:  # noqa: BLE001 — on rapporte, on ne lève pas
            problemes.append(
                f"is_available() a levé une exception au lieu de retourner "
                f"(bool, str) : {exc!r}."
            )
        else:
            if (
                not isinstance(resultat, tuple)
                or len(resultat) != 2
                or not isinstance(resultat[0], bool)
                or not isinstance(resultat[1], str)
            ):
                problemes.append(
                    "is_available() doit retourner un tuple (bool, str)."
                )

    return problemes
