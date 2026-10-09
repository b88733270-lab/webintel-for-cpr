"""Orchestration des exécutions de simulation — Phase 5.

``run_simulation()`` exécute un moteur du registre et persiste le
résultat comme ``Simulation`` (niveau de preuve ``prediction``) :

1. récupère le moteur via ``registry.get_engine()`` — nom inconnu →
   ``ValueError`` ;
2. vérifie ``is_available()`` — moteur indisponible (ex. adaptateur
   sans son binaire externe) → ``ValueError`` explicite : AUCUN calcul
   n'est effectué, AUCUN faux résultat n'est produit ;
3. ``validate_inputs(inputs, params)`` — entrées/paramètres invalides →
   ``ValueError`` ;
4. ``run()`` — calcul réel du moteur ;
5. ``build_report()`` — rapport à 6 sections
   (INPUTS, METHOD, PARAMETERS, OUTPUTS, REFERENCES, LIMITATIONS) +
   avertissement « Simulation — à confirmer expérimentalement » ;
6. ``science_service.create_simulation()`` — persistance (statut
   ``"exploratoire"``, limitations rappelées dans ``hypotheses``).

RÈGLE DURE : une simulation n'est JAMAIS présentée comme une preuve
expérimentale — chaque réponse et chaque rapport portent l'avertissement.

Ne committe jamais : c'est l'appelant (routeur API, route UI) qui
committe, comme pour tout le module scientifique.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from webintel.science import service as science_service
from webintel.science.models import Simulation
from webintel.simulations import registry
from webintel.simulations.reports import build_report

__all__ = ["run_simulation"]


def run_simulation(
    session: Session,
    *,
    engine_name: str,
    name: str,
    inputs: dict,
    parameters: dict | None = None,
    project_id: int,
    description: str | None = None,
    references: list[str] | None = None,
    executed_by: str | None = None,
) -> tuple[Simulation, dict]:
    """Exécute le moteur ``engine_name`` et enregistre la simulation.

    Retourne ``(simulation, rapport)``. ``simulation`` est l'objet
    ``Simulation`` persisté (non committé) ; ``rapport`` est le
    dictionnaire à 6 sections construit par ``reports.build_report``.

    P0-1 : ``project_id`` est OBLIGATOIRE (mot-clé) — la simulation
    enregistrée est toujours rattachée à un projet (donnée de
    laboratoire privée), jamais orpheline.

    ``executed_by`` (Phase 13) : identité de l'appelant enregistrée dans
    le journal d'audit (``"clé:<préfixe>"``, ``"ui:<préfixe>"`` ou
    ``"local"``).

    Lève ``ValueError`` (message français) si : le moteur est inconnu, le
    moteur est indisponible (aucun faux calcul), les entrées ou les
    paramètres sont invalides, ou l'exécution échoue.
    """
    try:
        moteur = registry.get_engine(engine_name)
    except KeyError as exc:
        connus = ", ".join(m["nom"] for m in registry.list_engines())
        raise ValueError(
            f"Moteur de simulation inconnu : « {engine_name} ». "
            f"Moteurs connus : {connus}."
        ) from exc
    except ValueError as exc:
        # Moteur connu mais indisponible (ex. docking_adapter sans Vina) :
        # on refuse explicitement — JAMAIS de faux calcul.
        raise ValueError(
            f"{exc} Aucun calcul n'a été effectué, aucun résultat produit."
        ) from exc

    if not isinstance(inputs, dict) or not inputs:
        raise ValueError(
            "Les entrées de la simulation sont vides : un calcul sans "
            "entrées n'a aucun sens."
        )
    params = parameters if parameters is not None else {}
    if not isinstance(params, dict):
        raise ValueError("Les paramètres doivent être un objet JSON.")

    try:
        normalise = moteur.validate_inputs(inputs, params)
    except ValueError as exc:
        raise ValueError(
            f"Entrées invalides pour le moteur « {engine_name} » : {exc}"
        ) from exc
    entrees = normalise["inputs"]
    parametres = normalise["params"]

    try:
        sorties = moteur.run(entrees, parametres)
    except RuntimeError as exc:
        raise ValueError(
            f"Échec d'exécution du moteur « {engine_name} » : {exc}"
        ) from exc

    rapport = build_report(
        moteur, entrees, parametres, sorties,
        references=list(references or []),
    )

    limitations = "\n".join(f"- {lim}" for lim in moteur.LIMITATIONS)
    simulation = science_service.create_simulation(
        session,
        name,
        entrees,
        description=description,
        method=f"{moteur.ENGINE_NAME} {moteur.ENGINE_VERSION} — {moteur.DESCRIPTION}",
        parameters=parametres,
        engine_name=moteur.ENGINE_NAME,
        engine_version=moteur.ENGINE_VERSION,
        result=sorties,
        references=list(references or []),
        executed_by=executed_by,
        hypotheses=(
            "Limitations du moteur — à lire avant toute interprétation "
            "(simulation exploratoire, à confirmer expérimentalement) :\n"
            + limitations
        ),
        status="exploratoire",
        project_id=project_id,
    )
    return simulation, rapport
