"""Registre des moteurs de simulation WebIntel.

Le registre connaît la liste des modules de moteurs attendus
(``ENGINE_MODULES``) et expose :

- ``list_engines() -> list[dict]`` : description de chaque moteur
  (nom, version, description, disponible, raison, entrées, paramètres,
  limitations). Un moteur dont le module est manquant ou ne respecte pas
  le contrat apparaît comme *indisponible* avec une raison explicite :
  cette fonction ne lève JAMAIS d'exception.
- ``get_engine(name) -> module`` : retourne le module du moteur demandé ;
  lève ``KeyError`` si le nom est inconnu et ``ValueError`` si le moteur
  est connu mais indisponible (module manquant ou contrat invalide).
"""

from __future__ import annotations

import importlib
import types

from webintel.simulations.plugins import check_plugin

#: Modules de moteurs attendus. Les moteurs seront implémentés par les
#: agents dédiés ; un module absent rend simplement le moteur indisponible.
ENGINE_MODULES = [
    "webintel.simulations.plugins.molecular_similarity",
    "webintel.simulations.plugins.property_prediction",
    "webintel.simulations.plugins.interaction_analysis",
    "webintel.simulations.plugins.statistics",
    "webintel.simulations.plugins.docking_adapter",
]


def _charger_module(chemin: str) -> tuple[types.ModuleType | None, str]:
    """Importe un module moteur ; retourne (module, "") ou (None, raison)."""
    try:
        module = importlib.import_module(chemin)
    except ModuleNotFoundError:
        return None, f"Module {chemin!r} introuvable (moteur non implémenté)."
    except Exception as exc:  # noqa: BLE001 — moteur dégradé, pas d'exception
        return None, f"Échec de l'import du module {chemin!r} : {exc}."
    problemes = check_plugin(module)
    if problemes:
        return None, "Contrat plugin non respecté : " + " ".join(problemes)
    return module, ""


def list_engines() -> list[dict]:
    """Liste tous les moteurs connus avec leur statut.

    Ne lève jamais : un moteur problématique est signalé comme
    indisponible avec sa raison.
    """
    moteurs: list[dict] = []
    for chemin in ENGINE_MODULES:
        module, raison = _charger_module(chemin)
        if module is None:
            moteurs.append(
                {
                    "nom": chemin.rsplit(".", 1)[-1],
                    "version": "",
                    "description": "",
                    "disponible": False,
                    "raison": raison,
                    "entrees": {},
                    "parametres": {},
                    "limitations": [],
                }
            )
            continue
        try:
            disponible, raison_dispo = module.is_available()
        except Exception as exc:  # noqa: BLE001 — check_plugin a déjà validé
            disponible, raison_dispo = False, (
                f"is_available() a levé une exception : {exc}."
            )
        moteurs.append(
            {
                "nom": module.ENGINE_NAME,
                "version": module.ENGINE_VERSION,
                "description": module.DESCRIPTION,
                "disponible": bool(disponible),
                "raison": raison_dispo if not disponible else "",
                "entrees": dict(module.INPUTS),
                "parametres": dict(module.PARAMETERS),
                "limitations": list(module.LIMITATIONS),
            }
        )
    return moteurs


def get_engine(name: str) -> types.ModuleType:
    """Retourne le module du moteur nommé ``name``.

    Lève ``KeyError`` si aucun moteur ne porte ce nom, ``ValueError``
    si le moteur est connu mais indisponible (module manquant, contrat
    invalide ou ``is_available()`` négatif).
    """
    for chemin in ENGINE_MODULES:
        module, raison = _charger_module(chemin)
        nom_candidat = (
            module.ENGINE_NAME if module is not None else chemin.rsplit(".", 1)[-1]
        )
        if nom_candidat != name:
            continue
        if module is None:
            raise ValueError(
                f"Moteur {name!r} indisponible : {raison}"
            )
        try:
            disponible, raison_dispo = module.is_available()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"Moteur {name!r} indisponible : is_available() a levé {exc!r}."
            ) from exc
        if not disponible:
            raise ValueError(
                f"Moteur {name!r} indisponible : {raison_dispo or 'raison inconnue'}."
            )
        return module
    raise KeyError(f"Moteur inconnu : {name!r}.")
