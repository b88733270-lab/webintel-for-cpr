"""Workflows de recherche — Phase 7 (backend uniquement).

Un *workflow* enchaîne des étapes qui réutilisent les services existants
(``webintel.science.service``, ``webintel.science.fiche``,
``webintel.simulations.service``) sans jamais les contourner ni les
dupliquer.

Conventions (comme tout le module scientifique) :

- la session SQLAlchemy est TOUJOURS le premier argument ;
- AUCUNE fonction ne committe : l'appelant (routeur) committe ;
- les colonnes JSON sont réassignées (nouvelle liste / nouveau dict)
  après mutation, car SQLAlchemy ne suit pas les mutations en place ;
- une simulation n'est JAMAIS présentée comme une preuve expérimentale :
  l'étape de simulation porte l'avertissement
  « Simulation — à confirmer expérimentalement ».

Workflow ``analyse_plante`` (7 étapes) :

1. ``choisir_plante`` — charge la plante (input ``plant_id``) ;
2. ``constituants`` — recense les constituants (lignes ``PlantCompound``) ;
3. ``publications`` — collecte les preuves liées (règle DOI de
   ``fiche.build_plant_fiche`` : ``Evidence.doi`` égal au ``doi`` d'un
   lien ``PlantCompound`` de la plante) ;
4. ``choisir_molecule`` — charge le composé (input ``compound_id``),
   qui doit faire partie des constituants recensés à l'étape 2 ;
5. ``simulation`` — exécute un moteur disponible via
   ``simulations.service.run_simulation`` (``property_prediction`` par
   défaut, ``molecular_similarity`` possible) et persiste la
   ``Simulation`` ; l'avertissement figure dans les sorties ;
6. ``rapport`` — vérifie le rapport à 6 sections
   (INPUTS→METHOD→PARAMETERS→OUTPUTS→REFERENCES→LIMITATIONS) ;
7. ``export`` — assemble ``resultat`` : rapport + fiche complète de la
   plante (``fiche.build_plant_fiche``) + avertissement.

Reprise : ``avancer_workflow`` exécute la première étape non terminée ;
une étape échouée garde son message d'erreur en français et peut être
rejouée après correction des données. ``rejouer_workflow`` réinitialise
le run depuis les mêmes inputs initiaux (même ``input_hash``).
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, Session

from webintel.core.utils import sha256_hex, utcnow
from webintel.database.base import Base, TZDateTime
from webintel.database.helpers import canonical_json
from webintel.science import fiche as fiche_service
from webintel.science.models import Compound, Evidence, Plant, PlantCompound
from webintel.simulations import registry
from webintel.simulations.reports import AVERTISSEMENT, SECTIONS
from webintel.simulations.service import run_simulation

__all__ = [
    "WorkflowRun",
    "STATUTS_ETAPE",
    "STATUTS_RUN",
    "creer_workflow",
    "avancer_workflow",
    "rejouer_workflow",
    "get_workflow",
    "list_workflows",
]

#: Statuts possibles d'une étape du workflow.
STATUTS_ETAPE = ("en_attente", "en_cours", "terminee", "echouee")

#: Statuts possibles d'un run de workflow.
STATUTS_RUN = ("en_cours", "termine", "echoue")

#: Types de workflow supportés.
TYPES_WORKFLOW = ("analyse_plante",)

#: Moteurs utilisables par l'étape de simulation du workflow
#: ``analyse_plante`` (dans l'ordre de préférence par défaut).
MOTEURS_SIMULATION = ("property_prediction", "molecular_similarity")

#: Nom affiché des 7 étapes du workflow ``analyse_plante``.
_ETAPES_ANALYSE_PLANTE: tuple[tuple[str, str], ...] = (
    ("choisir_plante", "Sélection de la plante à analyser"),
    ("constituants", "Recensement des constituants phytochimiques"),
    ("publications", "Collecte des publications liées"),
    ("choisir_molecule", "Sélection de la molécule à simuler"),
    ("simulation", "Simulation in silico (prédiction)"),
    ("rapport", "Construction du rapport de simulation"),
    ("export", "Export du rapport et de la fiche"),
)


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------

class WorkflowRun(Base):
    """Exécution d'un workflow de recherche (journal d'étapes).

    ``etapes`` : liste ordonnée de dicts ``{"nom", "libelle", "statut",
    "entrees", "sorties", "erreur"}`` — ``statut`` parmi ``STATUTS_ETAPE``.
    ``input_hash`` : SHA-256 du JSON canonique des inputs initiaux
    (traçabilité / rejouabilité). ``resultat`` : export final assemblé
    par la dernière étape (rapport + fiche).
    """

    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False
    )
    type_workflow: Mapped[str] = mapped_column(String(64), nullable=False)
    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    etapes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    etape_courante: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    statut: Mapped[str] = mapped_column(
        String(32), default="en_cours", nullable=False
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resultat: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cree_par: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[Any] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_workflow_runs_project_id", "project_id"),
    )


# ---------------------------------------------------------------------------
# Construction des étapes
# ---------------------------------------------------------------------------

def _etapes_initiales(inputs: dict) -> list[dict]:
    """Construit les 7 étapes vierges du workflow ``analyse_plante``.

    Chaque étape porte une copie des inputs initiaux dans ``entrees`` :
    c'est ce qui permet ``rejouer_workflow`` (mêmes inputs, même
    ``input_hash``) sans colonne dédiée.
    """
    return [
        {
            "nom": nom,
            "libelle": libelle,
            "statut": "en_attente",
            "entrees": dict(inputs),
            "sorties": {},
            "erreur": None,
        }
        for nom, libelle in _ETAPES_ANALYSE_PLANTE
    ]


def _sorties_etape(run: WorkflowRun, nom_etape: str) -> dict:
    """Retourne les sorties enregistrées de l'étape nommée (ou {})."""
    for etape in run.etapes or []:
        if etape.get("nom") == nom_etape:
            return dict(etape.get("sorties") or {})
    return {}


def _valider_inputs_analyse_plante(inputs: dict) -> dict:
    """Valide et normalise les inputs du workflow ``analyse_plante``.

    Requis : ``plant_id`` et ``compound_id`` (entiers). Optionnel :
    ``engine_name`` (doit nommer un moteur connu du registre). Lève
    ``ValueError`` (message français) si invalide. Retourne les inputs
    normalisés.
    """
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError(
            "Inputs invalides : un objet JSON non vide est requis "
            "(« plant_id » et « compound_id » obligatoires)."
        )
    plant_id = inputs.get("plant_id")
    compound_id = inputs.get("compound_id")
    if not isinstance(plant_id, int) or isinstance(plant_id, bool):
        raise ValueError(
            "Inputs invalides : « plant_id » (entier) est requis pour "
            "le workflow analyse_plante."
        )
    if not isinstance(compound_id, int) or isinstance(compound_id, bool):
        raise ValueError(
            "Inputs invalides : « compound_id » (entier) est requis pour "
            "le workflow analyse_plante."
        )
    engine_name = inputs.get("engine_name")
    normalises: dict = {"plant_id": plant_id, "compound_id": compound_id}
    if engine_name is not None:
        if not isinstance(engine_name, str) or not engine_name.strip():
            raise ValueError(
                "Inputs invalides : « engine_name » doit être une chaîne "
                "non vide."
            )
        connus = {m["nom"] for m in registry.list_engines()}
        if engine_name not in connus:
            raise ValueError(
                f"Moteur de simulation inconnu : « {engine_name} ». "
                f"Moteurs connus : {', '.join(sorted(connus))}."
            )
        normalises["engine_name"] = engine_name.strip()
    return normalises


def _moteur_par_defaut() -> str:
    """Choisit le premier moteur supporté réellement disponible.

    Lève ``ValueError`` (français) si aucun moteur supporté n'est
    disponible — on refuse de produire un faux calcul.
    """
    for nom in MOTEURS_SIMULATION:
        for fiche_moteur in registry.list_engines():
            if fiche_moteur["nom"] == nom and fiche_moteur["disponible"]:
                return nom
    raise ValueError(
        "Aucun moteur de simulation disponible pour l'étape de simulation "
        f"({', '.join(MOTEURS_SIMULATION)} tous indisponibles). "
        "Aucun calcul n'a été effectué."
    )


# ---------------------------------------------------------------------------
# Exécuteurs d'étapes (analyse_plante)
# ---------------------------------------------------------------------------

def _exec_choisir_plante(db: Session, run: WorkflowRun, etape: dict) -> dict:
    """Étape 1 : charge la plante désignée par ``plant_id``."""
    plant_id = etape["entrees"]["plant_id"]
    plante = db.get(Plant, plant_id)
    if plante is None:
        raise ValueError(f"Plante introuvable : id={plant_id}.")
    return {
        "plant_id": plante.id,
        "nom_scientifique": plante.scientific_name,
        "famille": plante.family,
        "genre": plante.genus,
    }


def _exec_constituants(db: Session, run: WorkflowRun, etape: dict) -> dict:
    """Étape 2 : recense les constituants (lignes ``PlantCompound``)."""
    plant_id = etape["entrees"]["plant_id"]
    liens = (
        db.query(PlantCompound)
        .filter(PlantCompound.plant_id == plant_id)
        .order_by(PlantCompound.id)
        .all()
    )
    ids_composes = {lien.compound_id for lien in liens}
    composes = (
        {
            c.id: c
            for c in db.query(Compound)
            .filter(Compound.id.in_(ids_composes))
            .all()
        }
        if ids_composes
        else {}
    )
    items = [
        {
            "lien_id": lien.id,
            "compound_id": lien.compound_id,
            "nom": composes[lien.compound_id].name
            if lien.compound_id in composes
            else None,
            "formule": composes[lien.compound_id].formula
            if lien.compound_id in composes
            else None,
            "partie": lien.plant_part,
            "publication": lien.publication,
            "doi": lien.doi,
            "confiance": lien.confidence,
        }
        for lien in liens
    ]
    return {"constituants": items, "nombre": len(items)}


def _exec_publications(db: Session, run: WorkflowRun, etape: dict) -> dict:
    """Étape 3 : collecte les preuves liées (règle DOI de la fiche)."""
    plant_id = etape["entrees"]["plant_id"]
    dois = {
        lien.doi
        for lien in db.query(PlantCompound.doi)
        .filter(PlantCompound.plant_id == plant_id)
        .all()
        if lien.doi
    }
    preuves = (
        db.query(Evidence)
        .filter(Evidence.doi.in_(dois))
        .order_by(Evidence.id)
        .all()
        if dois
        else []
    )
    return {
        "publications": [
            {
                "evidence_id": p.id,
                "titre": p.title,
                "auteurs": list(p.authors or []),
                "annee": p.year,
                "doi": p.doi,
                "reference": p.reference,
            }
            for p in preuves
        ],
        "nombre": len(preuves),
    }


def _exec_choisir_molecule(db: Session, run: WorkflowRun, etape: dict) -> dict:
    """Étape 4 : charge le composé désigné par ``compound_id``.

    Le composé doit exister ET figurer parmi les constituants de la
    plante recensés à l'étape 2 — on ne simule pas une molécule étrangère
    à la plante analysée.
    """
    entrees = etape["entrees"]
    compose = db.get(Compound, entrees["compound_id"])
    if compose is None:
        raise ValueError(f"Composé introuvable : id={entrees['compound_id']}.")
    lien = (
        db.query(PlantCompound)
        .filter(
            PlantCompound.plant_id == entrees["plant_id"],
            PlantCompound.compound_id == compose.id,
        )
        .first()
    )
    if lien is None:
        raise ValueError(
            f"Le composé « {compose.name} » (id={compose.id}) n'est associé "
            f"à aucune donnée de la plante (id={entrees['plant_id']}) : "
            "choisissez une molécule parmi les constituants recensés à "
            "l'étape 2."
        )
    return {
        "compound_id": compose.id,
        "nom": compose.name,
        "formule": compose.formula,
        "classe_chimique": compose.chemical_class,
        "smiles": compose.smiles,
        "inchikey": compose.inchikey,
    }


def _compose_moteur(
    nom_moteur: str, compose: dict, autres: list[dict]
) -> tuple[dict, dict]:
    """Construit ``(inputs, parameters)`` pour le moteur demandé.

    - ``property_prediction`` : le composé choisi seul (formule requise) ;
    - ``molecular_similarity`` : le composé choisi comparé aux autres
      constituants de la plante disposant d'une formule (au moins 2
      composés requis par le moteur).

    Lève ``ValueError`` (français) si les données sont insuffisantes.
    """
    nom = compose["nom"]
    if nom_moteur == "property_prediction":
        formule = compose.get("formule")
        if not formule:
            raise ValueError(
                f"Le composé « {nom} » n'a pas de formule brute : la "
                "simulation property_prediction est impossible. "
                "Renseignez la formule du composé puis rejouez l'étape."
            )
        return (
            {"composes": [{"nom": nom, "formule": formule}]},
            {},
        )
    if nom_moteur == "molecular_similarity":
        candidats = [
            {"nom": c["nom"], "formule": c["formule"]}
            for c in [compose, *autres]
            if c.get("formule")
        ]
        # Déduplique sur le nom (le composé choisi peut apparaître deux fois).
        vus: set[str] = set()
        uniques = []
        for c in candidats:
            if c["nom"] not in vus:
                vus.add(c["nom"])
                uniques.append(c)
        if len(uniques) < 2:
            raise ValueError(
                "Le moteur molecular_similarity exige au moins 2 composés "
                "avec une formule brute ; la plante n'en fournit pas assez. "
                "Utilisez property_prediction ou renseignez des formules."
            )
        return ({"composes": uniques}, {"methode": "structure"})
    raise ValueError(
        f"Le moteur « {nom_moteur} » n'est pas pris en charge par le "
        f"workflow analyse_plante (supportés : {', '.join(MOTEURS_SIMULATION)})."
    )


def _exec_simulation(db: Session, run: WorkflowRun, etape: dict) -> dict:
    """Étape 5 : exécute le moteur et persiste la ``Simulation``.

    Les sorties portent l'avertissement « Simulation — à confirmer
    expérimentalement » : ce résultat est une prédiction, jamais une
    preuve expérimentale.
    """
    entrees = etape["entrees"]
    nom_moteur = entrees.get("engine_name") or _moteur_par_defaut()
    # Refuse explicitement un moteur devenu indisponible depuis la création.
    try:
        registry.get_engine(nom_moteur)
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"Moteur « {nom_moteur} » indisponible au moment de l'exécution : "
            f"{exc} Aucun calcul n'a été effectué."
        ) from exc

    compose = _sorties_etape(run, "choisir_molecule")
    constituants = _sorties_etape(run, "constituants").get("constituants", [])
    autres = [
        {"nom": c.get("nom"), "formule": c.get("formule")}
        for c in constituants
        if c.get("compound_id") != compose.get("compound_id")
    ]
    inputs_moteur, parametres = _compose_moteur(nom_moteur, compose, autres)

    nom_simulation = (
        f"Workflow « {run.nom} » — {compose.get('nom', 'molécule')}"
    )
    try:
        simulation, rapport = run_simulation(
            db,
            engine_name=nom_moteur,
            name=nom_simulation,
            inputs=inputs_moteur,
            parameters=parametres,
            project_id=run.project_id,
            description=(
                f"Simulation exécutée par le workflow « {run.nom} » "
                f"(run id={run.id}, étape simulation)."
            ),
            references=[],
        )
    except ValueError as exc:
        # run_simulation lève déjà des messages français explicites.
        raise ValueError(str(exc)) from exc

    return {
        "simulation_id": simulation.id,
        "moteur": nom_moteur,
        "avertissement": AVERTISSEMENT,
        "rapport": rapport,
    }


def _exec_rapport(db: Session, run: WorkflowRun, etape: dict) -> dict:
    """Étape 6 : vérifie le rapport à 6 sections de l'étape simulation."""
    rapport = _sorties_etape(run, "simulation").get("rapport")
    if not isinstance(rapport, dict):
        raise ValueError(
            "Aucun rapport de simulation disponible : exécutez d'abord "
            "l'étape de simulation."
        )
    manquantes = [s for s in SECTIONS if s not in rapport]
    if manquantes:
        raise ValueError(
            "Rapport de simulation incomplet : sections manquantes : "
            + ", ".join(manquantes)
            + "."
        )
    return {
        "rapport": rapport,
        "sections": list(SECTIONS),
        "avertissement": rapport.get("avertissement", AVERTISSEMENT),
    }


def _exec_export(db: Session, run: WorkflowRun, etape: dict) -> dict:
    """Étape 7 : assemble l'export final (rapport + fiche) dans ``resultat``."""
    entrees = etape["entrees"]
    rapport = _sorties_etape(run, "rapport").get("rapport")
    if not isinstance(rapport, dict):
        raise ValueError(
            "Aucun rapport disponible : exécutez d'abord l'étape de rapport."
        )
    fiche = fiche_service.build_plant_fiche(
        db, entrees["plant_id"], project_id=run.project_id
    )
    simulation_id = _sorties_etape(run, "simulation").get("simulation_id")
    # Affectation (pas mutation en place) : SQLAlchemy suit le changement.
    run.resultat = {
        "rapport": rapport,
        "fiche": fiche,
        "simulation_id": simulation_id,
        "avertissement": AVERTISSEMENT,
        "exporte_le": utcnow().isoformat(),
    }
    return {
        "cles_resultat": sorted(run.resultat.keys()),
        "sections_rapport": len(SECTIONS),
        "simulation_id": simulation_id,
    }


_EXECUTEURS: dict[str, Callable[[Session, WorkflowRun, dict], dict]] = {
    "choisir_plante": _exec_choisir_plante,
    "constituants": _exec_constituants,
    "publications": _exec_publications,
    "choisir_molecule": _exec_choisir_molecule,
    "simulation": _exec_simulation,
    "rapport": _exec_rapport,
    "export": _exec_export,
}


# ---------------------------------------------------------------------------
# Services (ne committent jamais)
# ---------------------------------------------------------------------------

def creer_workflow(
    db: Session,
    *,
    project_id: int,
    type_workflow: str,
    nom: str,
    inputs: dict,
    cree_par: str | None = None,
) -> WorkflowRun:
    """Crée un run de workflow (7 étapes en attente). Ne committe jamais.

    ``input_hash`` = SHA-256 du JSON canonique des inputs initiaux.
    Lève ``ValueError`` (message français) si le type est inconnu ou si
    les inputs sont invalides.
    """
    if type_workflow not in TYPES_WORKFLOW:
        raise ValueError(
            f"Type de workflow inconnu : « {type_workflow} ». "
            f"Types supportés : {', '.join(TYPES_WORKFLOW)}."
        )
    if not isinstance(nom, str) or not nom.strip():
        raise ValueError("Le nom du workflow est requis (chaîne non vide).")
    inputs_normalises = _valider_inputs_analyse_plante(inputs)
    run = WorkflowRun(
        project_id=project_id,
        type_workflow=type_workflow,
        nom=nom.strip(),
        etapes=_etapes_initiales(inputs_normalises),
        etape_courante=0,
        statut="en_cours",
        input_hash=sha256_hex(canonical_json(inputs)),
        resultat=None,
        cree_par=(cree_par or "").strip() or None,
    )
    db.add(run)
    db.flush()
    return run


def get_workflow(db: Session, run_id: int) -> WorkflowRun:
    """Retourne le run ``run_id`` ; lève ``KeyError`` si introuvable."""
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise KeyError(f"Workflow introuvable : id={run_id}")
    return run


def list_workflows(
    db: Session, project_id: int | None = None,
    limit: int | None = None, offset: int = 0,
) -> list[WorkflowRun]:
    """Liste les runs, filtrés au projet si ``project_id`` est fourni.

    ``limit``/``offset`` : pagination (``None`` = tout, historique).
    """
    requete = db.query(WorkflowRun)
    if project_id is not None:
        requete = requete.filter(WorkflowRun.project_id == project_id)
    requete = requete.order_by(WorkflowRun.id).offset(max(0, offset))
    if limit is not None:
        requete = requete.limit(max(1, limit))
    return requete.all()


def avancer_workflow(db: Session, run_id: int) -> WorkflowRun:
    """Exécute la prochaine étape non terminée du run. Ne committe jamais.

    - run introuvable → ``KeyError`` ;
    - run déjà terminé → ``ValueError`` (« étape impossible ») ;
    - échec d'étape → l'étape est marquée ``echouee`` avec son message
      d'erreur en français, le run passe à ``echoue``, et ``ValueError``
      est levée (l'état est persisté avant la levée : reprise possible) ;
    - après la dernière étape, le run passe à ``termine``.

    Retourne le run mis à jour.
    """
    run = get_workflow(db, run_id)
    if run.statut == "termine":
        raise ValueError(
            "Le workflow est déjà terminé : aucune étape à exécuter."
        )
    etapes = [dict(e) for e in (run.etapes or [])]
    index = next(
        (i for i, e in enumerate(etapes) if e.get("statut") != "terminee"),
        None,
    )
    if index is None:
        # Toutes les étapes sont déjà terminées (cas limite).
        run.statut = "termine"
        run.etape_courante = len(etapes)
        db.flush()
        return run

    etape = etapes[index]
    executeur = _EXECUTEURS.get(etape.get("nom", ""))
    if executeur is None:
        etape["statut"] = "echouee"
        etape["erreur"] = (
            f"Étape inconnue : « {etape.get('nom')} » — exécution impossible."
        )
        run.statut = "echoue"
        run.etapes = [dict(e) for e in etapes]
        db.flush()
        raise ValueError(etape["erreur"])

    etape["statut"] = "en_cours"
    etape["erreur"] = None
    run.etape_courante = index
    try:
        sorties = executeur(db, run, etape)
    except ValueError as exc:
        etape["statut"] = "echouee"
        etape["sorties"] = {}
        etape["erreur"] = str(exc).strip() or (
            "Échec de l'étape « "
            f"{etape.get('nom')} » (raison non précisée)."
        )
        run.statut = "echoue"
        # Nouvelle liste : garantit la détection du changement JSON.
        run.etapes = [dict(e) for e in etapes]
        db.flush()
        raise
    except Exception as exc:  # noqa: BLE001 — on ne laisse jamais fuir une trace brute
        etape["statut"] = "echouee"
        etape["sorties"] = {}
        etape["erreur"] = (
            f"Erreur inattendue à l'étape « {etape.get('nom')} » : {exc}"
        )
        run.statut = "echoue"
        run.etapes = [dict(e) for e in etapes]
        db.flush()
        raise ValueError(etape["erreur"]) from exc

    etape["statut"] = "terminee"
    etape["sorties"] = sorties if isinstance(sorties, dict) else {}
    suivant = next(
        (i for i, e in enumerate(etapes) if e.get("statut") != "terminee"),
        None,
    )
    if suivant is None:
        run.statut = "termine"
        run.etape_courante = len(etapes)
    else:
        run.statut = "en_cours"
        run.etape_courante = suivant
    run.etapes = [dict(e) for e in etapes]
    db.flush()
    return run


def rejouer_workflow(db: Session, run_id: int) -> WorkflowRun:
    """Réinitialise le run depuis les mêmes inputs initiaux. Ne committe jamais.

    Les étapes repartent à ``en_attente`` avec les inputs d'origine, le
    ``resultat`` est effacé et le statut repasse à ``en_cours``. Le
    ``input_hash`` est vérifié puis reste identique (mêmes inputs).
    Lève ``KeyError`` si le run est introuvable.
    """
    run = get_workflow(db, run_id)
    if run.type_workflow not in TYPES_WORKFLOW:
        raise ValueError(
            f"Type de workflow inconnu : « {run.type_workflow} »."
        )
    etapes = run.etapes or []
    inputs_initiaux = dict(etapes[0].get("entrees") or {}) if etapes else {}
    if sha256_hex(canonical_json(inputs_initiaux)) != run.input_hash:
        raise ValueError(
            "Incohérence détectée : les inputs stockés ne correspondent "
            "plus à l'empreinte du run — rejouement refusé."
        )
    run.etapes = _etapes_initiales(inputs_initiaux)
    run.etape_courante = 0
    run.statut = "en_cours"
    run.resultat = None
    db.flush()
    return run
