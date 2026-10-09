"""Routes UI — page « Outils scientifiques » + traçabilité des résultats (v2.2).

NOUVEAU module, créé SANS modifier aucun fichier existant.

⚠️  ENREGISTREMENT : ce module expose ``router = APIRouter()`` mais ne
    s'enregistre PAS lui-même. Le coordinateur ajoute dans
    ``webintel/main.py`` (fonction ``create_app``), avec les autres
    routeurs UI : ::

        from webintel.ui import chercheur_outils_routes as ui_outils_routes
        app.include_router(ui_outils_routes.router)

    Les routes sont servies à la racine (aucun préfixe).

Routes :
- ``GET /outils-scientifiques`` — page dédiée des 4 outils scientifiques
  (RDKit, Meeko, AutoDock Vina, PostgreSQL) : nom, version détectée,
  statut (🟢 Installé / 🟠 Non installé), fonction expliquée simplement,
  pourquoi l'outil est nécessaire, licence, et marche à suivre
  d'installation ultra simple quand l'installation automatique est
  impossible.
- ``GET /resultats/{type}/{id}/provenance`` — fiche de traçabilité d'un
  résultat : source, donnée d'entrée, méthode, version du moteur,
  paramètres, date, résultat. Formulations prudentes (« Résultat
  informatique », « Prédiction », « Hypothèse »), niveau de preuve
  visible, jamais « efficace » / « sûr » / « traite », jamais de
  résultat inventé.

Détection : réutilise les fonctions existantes —
``webintel.science.chimie`` (``rdkit_disponible``, ``infos_biblio``),
``webintel.simulations.plugins.docking_adapter`` (``meeko_disponible``,
``diagnostic_vina``, ``is_available``), et la détection PostgreSQL
existante (``settings.DATABASE_URL.startswith("postgresql")``,
cf. ``webintel/main.py``).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from webintel import __version__ as _VERSION_APP
from webintel.api.deps import get_db
from webintel.config.settings import get_settings
from webintel.database.models import ApiKey
from webintel.science.models import DockingJob, Provenance, Simulation
from webintel.ui.auth import ui_auth
from webintel.ui.routes import _project_for, _render

router = APIRouter()

# Types de résultats reconnus par la fiche de traçabilité :
# type -> (libellé affiché, gabarit d'URL de retour vers le résultat).
_TYPES_RESULTATS = {
    "docking": ("Docking moléculaire", "/docking/{id}"),
    "simulation": ("Simulation", "/simulations/{id}/rapport"),
    "plant": ("Plante", "/plantes/{id}"),
    "plante": ("Plante", "/plantes/{id}"),
    "etude": ("Étude de combinaison", "/combinaisons/{id}"),
    "combinaison": ("Étude de combinaison", "/combinaisons/{id}"),
}

_PRUDENCE_PREDICTION = (
    "Résultat informatique : une prédiction n'est pas une preuve. "
    "Un score de docking n'est pas une mesure d'activité biologique — "
    "vérification expérimentale requise avant toute conclusion."
)


# ---------------------------------------------------------------------------
# Détection des outils (ne lève jamais)
# ---------------------------------------------------------------------------


def _detecter_outils(db: Session) -> list[dict]:
    """État réel des 4 outils scientifiques (nom, version, statut...).

    Chaque dict : ``id``, ``nom``, ``version``, ``installe``, ``fonction``,
    ``pourquoi``, ``licence``, ``note_install``, ``commande``,
    ``note_config``. Ne lève jamais : un outil illisible est affiché
    « Non installé » avec une raison explicite.
    """
    outils: list[dict] = []

    # -- RDKit -----------------------------------------------------------
    try:
        from webintel.science import chimie

        info_rdkit = chimie.infos_biblio()
        rdkit_ok = bool(info_rdkit.get("disponible"))
        rdkit_version = info_rdkit.get("version")
    except Exception:  # noqa: BLE001 — la page doit toujours s'afficher
        rdkit_ok, rdkit_version = False, None
    outils.append(
        {
            "id": "rdkit",
            "nom": "RDKit",
            "version": rdkit_version,
            "installe": rdkit_ok,
            "fonction": (
                "Bibliothèque de chimoinformatique : elle « comprend » les "
                "molécules (formules SMILES), calcule leurs propriétés "
                "(taille, solubilité estimée…) et compare leurs structures."
            ),
            "pourquoi": (
                "RDKit est installé par défaut avec WebIntel : il permet "
                "de comparer les molécules et de calculer leurs "
                "propriétés. S'il venait à manquer (retrait manuel), "
                "WebIntel l'indique clairement au lieu d'inventer des "
                "valeurs."
            ),
            "licence": "BSD-3-Clause (logiciel libre)",
            "note_install": (
                "RDKit est normalement déjà installé (installation "
                "standard). S'il a été retiré, demandez à votre "
                "informaticien de le réinstaller, dans le dossier "
                "de WebIntel :"
            ),
            "commande": (
                "pip install rdkit\n"
                "# ou réinstallation standard complète : "
                "pip install -r requirements.txt\n"
                "# (historique : ./scripts/install_science.sh ; "
                "ancien nom PyPI : pip install rdkit-pypi)"
            ),
            "note_config": "Aucune configuration requise une fois installé.",
        }
    )

    # -- Meeko + AutoDock Vina (même adaptateur) -------------------------
    try:
        from webintel.simulations.plugins import docking_adapter

        meeko_ok = bool(docking_adapter.meeko_disponible())
        diag_vina = docking_adapter.diagnostic_vina()
    except Exception:  # noqa: BLE001 — idem
        meeko_ok = False
        diag_vina = {
            "binaire": None,
            "version": None,
            "disponible": False,
            "raison": "État illisible.",
        }
    try:
        import importlib.metadata

        meeko_version = importlib.metadata.version("meeko")
    except Exception:  # noqa: BLE001 — paquet absent ou illisible
        meeko_version = None

    outils.append(
        {
            "id": "meeko",
            "nom": "Meeko",
            "version": meeko_version,
            "installe": meeko_ok,
            "fonction": (
                "Prépare les molécules pour le docking : transforme une "
                "formule SMILES (texte) en fichier PDBQT, le format compris "
                "par AutoDock Vina."
            ),
            "pourquoi": (
                "Sans Meeko, la conversion automatique SMILES → PDBQT est "
                "impossible : le chercheur doit fournir un ligand déjà "
                "préparé. Le logiciel le dit explicitement."
            ),
            "licence": "Apache-2.0 (logiciel libre)",
            "note_install": (
                "Demandez à votre informaticien d'exécuter, dans le dossier "
                "de WebIntel :"
            ),
            "commande": "pip install meeko\n# (nécessite RDKit, voir ci-dessus)",
            "note_config": "Aucune configuration requise une fois installé.",
        }
    )

    vina_ok = bool(diag_vina.get("disponible"))
    vina_version = diag_vina.get("version") or (
        "inconnue" if vina_ok else None
    )
    outils.append(
        {
            "id": "vina",
            "nom": "AutoDock Vina",
            "version": vina_version,
            "installe": vina_ok,
            "fonction": (
                "Moteur de docking moléculaire : simule comment une molécule "
                "pourrait se loger dans une protéine cible et estime une "
                "affinité (en kcal/mol)."
            ),
            "pourquoi": (
                "C'est le seul moyen d'obtenir un vrai score de docking. "
                "Sans Vina, WebIntel refuse le calcul — il n'invente jamais "
                "de score."
            ),
            "licence": "Apache-2.0 (logiciel libre)",
            "note_install": "Demandez à votre informaticien :",
            "commande": (
                "sudo apt install autodock-vina        # Debian/Ubuntu\n"
                "# ou : conda install -c conda-forge autodock-vina"
            ),
            "note_config": (
                f"Binaire : {diag_vina.get('binaire') or '—'} — "
                "paramètres par défaut décrits dans docs/simulations.md."
                if vina_ok
                else ""
            ),
            "raison": diag_vina.get("raison") or "",
        }
    )

    # -- PostgreSQL (détection existante : mode de DATABASE_URL) --------
    settings = get_settings()
    pg_mode = settings.DATABASE_URL.startswith("postgresql")
    pg_version: str | None = None
    if pg_mode:
        try:
            brute = db.execute(text("SELECT version()")).scalar() or ""
            # « PostgreSQL 16.2 on x86_64-... » -> « PostgreSQL 16.2 »
            pg_version = " ".join(str(brute).split()[:2]) or None
        except Exception:  # noqa: BLE001 — version optionnelle
            pg_version = None
    outils.append(
        {
            "id": "postgresql",
            "nom": "PostgreSQL",
            "version": pg_version,
            "installe": pg_mode,
            "fonction": (
                "Base de données partagée : conserve durablement les fiches, "
                "études et résultats pour plusieurs chercheurs en mode serveur."
            ),
            "pourquoi": (
                "En mode simple, WebIntel utilise SQLite (un fichier local, "
                "un seul utilisateur). PostgreSQL devient nécessaire pour un "
                "usage en équipe ou en production."
            ),
            "licence": "Licence PostgreSQL (libre, permissive)",
            "note_install": "Demandez à votre informaticien :",
            "commande": (
                "# 1. créez une base PostgreSQL, puis 2. dans le fichier .env "
                "de WebIntel :\n"
                "DATABASE_URL=postgresql://utilisateur:motdepasse@hote:5432/webintel"
            ),
            "note_config": (
                "Base active — réglages dans le fichier .env (DATABASE_URL)."
                if pg_mode
                else ""
            ),
        }
    )
    return outils


def _statut_docking() -> dict:
    """Statut du moteur de docking pour le partiel ``_docking_statut.html``.

    Ne lève jamais ; ne produit AUCUN score (statut uniquement).
    """
    try:
        from webintel.simulations.plugins import docking_adapter

        diag = docking_adapter.diagnostic_vina()
        return {
            "disponible": bool(diag.get("disponible")),
            "version": diag.get("version"),
            "binaire": diag.get("binaire"),
            "raison": diag.get("raison") or "",
        }
    except Exception:  # noqa: BLE001
        return {
            "disponible": False,
            "version": None,
            "binaire": None,
            "raison": "État du moteur illisible.",
        }


# ---------------------------------------------------------------------------
# Page « Outils scientifiques »
# ---------------------------------------------------------------------------


@router.get("/outils-scientifiques", response_class=HTMLResponse)
def outils_scientifiques(
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Page dédiée : les 4 outils, leur état réel et comment les installer."""
    outils = _detecter_outils(db)
    return _render(
        "outils_scientifiques.html",
        ui_key=ui_key,
        active="parametres",
        outils=outils,
        docking_statut=_statut_docking(),
        version_app=_VERSION_APP,
    )


# ---------------------------------------------------------------------------
# Traçabilité / provenance d'un résultat
# ---------------------------------------------------------------------------


def _formater_date(valeur) -> str:
    if isinstance(valeur, datetime):
        return valeur.strftime("%d/%m/%Y %H:%M")
    return str(valeur) if valeur is not None else "—"


def _fiche_docking(job: DockingJob) -> dict:
    """Fiche de traçabilité d'un job de docking (données réelles uniquement).

    Aucun score inventé : le résumé du résultat reprend les poses
    enregistrées, ou indique explicitement qu'aucun calcul n'a eu lieu
    (aperçu / refus).
    """
    prov = job.provenance or {}
    parametres = dict(job.parametres or {})
    resultats = job.resultats or {}
    poses = resultats.get("poses") or []
    if job.statut == "termine" and poses:
        meilleure = poses[0] or {}
        affinite = meilleure.get("affinite_kcal_mol")
        resultat = (
            f"Prédiction — {len(poses)} pose(s) calculée(s). "
            + (
                f"Meilleure affinité estimée : {affinite} kcal/mol "
                "(un score de docking n'est pas une mesure d'activité)."
                if affinite is not None
                else "Poses calculées (affinités non résumables ici)."
            )
        )
    elif job.statut == "termine":
        resultat = "Prédiction — calcul terminé, détail des poses sur la fiche du résultat."
    else:
        resultat = (
            "Aucun calcul effectué (statut : "
            f"{job.statut}) — aucun score produit, aucun résultat inventé."
        )
    return {
        "titre": f"Docking « {job.nom} »",
        "type_label": "Docking moléculaire",
        "type": "docking",
        "id": job.id,
        "retour_url": f"/docking/{job.id}",
        "provenance_url": f"/resultats/docking/{job.id}/provenance",
        "source": prov.get("source") or "Non déclarée",
        "entree": (
            f"Ligand : {job.ligand_nom or job.ligand_smiles or '—'} — "
            f"récepteur : {job.recepteur_nom or '—'}"
        ),
        "methode": job.methode or "Docking moléculaire (workflow en 9 étapes)",
        "moteur": job.moteur or "AutoDock Vina",
        "version_moteur": job.moteur_version or "inconnue",
        "parametres": parametres or None,
        "date": _formater_date(job.created_at),
        "resultat": resultat,
        "niveau": "prediction",
        "niveau_label": "Prédiction",
        "prudence": _PRUDENCE_PREDICTION,
    }


def _fiche_simulation(simulation: Simulation) -> dict:
    """Fiche de traçabilité d'une simulation (audit immuable, Phase 13)."""
    return {
        "titre": f"Simulation « {simulation.name} »",
        "type_label": "Simulation",
        "type": "simulation",
        "id": simulation.id,
        "retour_url": f"/simulations/{simulation.id}/rapport",
        "provenance_url": f"/resultats/simulation/{simulation.id}/provenance",
        "source": "Moteur de simulation interne",
        "entree": (
            "Empreinte des entrées (SHA-256) : "
            f"{getattr(simulation, 'input_hash', '—') or '—'}"
        ),
        "methode": simulation.method or "Non renseignée",
        "moteur": simulation.engine_name or "Non renseigné",
        "version_moteur": simulation.engine_version or "inconnue",
        "parametres": dict(simulation.parameters or {}) or None,
        "date": _formater_date(getattr(simulation, "run_at", None)),
        "resultat": (
            "Prédiction — voir le rapport complet. "
            "Une simulation n'est jamais une preuve expérimentale."
        ),
        "niveau": "prediction",
        "niveau_label": "Prédiction",
        "prudence": _PRUDENCE_PREDICTION,
    }


def _fiches_provenance_generiques(
    type_resultat: str, resultat_id: int, lignes: list[Provenance]
) -> list[dict]:
    """Fiches issues de la table ``provenances`` (journal append-only)."""
    label, gabarit = _TYPES_RESULTATS.get(
        type_resultat, (type_resultat, "")
    )
    retour_url = gabarit.format(id=resultat_id) if gabarit else ""
    fiches = []
    for ligne in lignes:
        fiches.append(
            {
                "titre": f"{label} n°{resultat_id}",
                "type_label": label,
                "type": type_resultat,
                "id": resultat_id,
                "retour_url": retour_url,
                "provenance_url": (
                    f"/resultats/{type_resultat}/{resultat_id}/provenance"
                ),
                "source": ligne.source_name or "Non déclarée",
                "entree": ligne.data_version or f"{label} n°{resultat_id}",
                "methode": ligne.connector or "Non renseignée",
                "moteur": "WebIntel",
                "version_moteur": (
                    ligne.connector_version or f"v{_VERSION_APP}"
                ),
                "parametres": None,
                "date": _formater_date(ligne.imported_at),
                "resultat": (
                    "Consulter le résultat depuis sa fiche "
                    + (f"({retour_url})" if retour_url else "d'origine")
                    + "."
                ),
                "niveau": None,
                "niveau_label": "Traçabilité",
                "prudence": None,
            }
        )
    return fiches


@router.get(
    "/resultats/{type_resultat}/{resultat_id}/provenance",
    response_class=HTMLResponse,
)
def resultat_provenance(
    type_resultat: str,
    resultat_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Fiche de traçabilité d'un résultat (404 FR si introuvable).

    Affiche : source, donnée d'entrée, méthode, version du moteur,
    paramètres, date, résultat — avec le niveau de preuve visible.
    """
    project = _project_for(db, ui_key)
    type_resultat = (type_resultat or "").strip().lower()
    fiches: list[dict] = []
    retour_url = ""

    if type_resultat == "docking":
        job = db.get(DockingJob, resultat_id)
        if job is None or job.project_id != project.id:
            raise HTTPException(
                status_code=404, detail="Résultat de docking introuvable."
            )
        fiches = [_fiche_docking(job)]
        retour_url = f"/docking/{job.id}"
    elif type_resultat == "simulation":
        simulation = db.get(Simulation, resultat_id)
        # P0-1 : cloisonnement strict — une orpheline ne correspond à aucun
        # projet → 404 (plus aucun chemin « si project_id is None →
        # pas de contrôle »).
        if simulation is None or simulation.project_id != project.id:
            raise HTTPException(
                status_code=404, detail="Résultat de simulation introuvable."
            )
        fiches = [_fiche_simulation(simulation)]
        retour_url = f"/simulations/{simulation.id}/rapport"
    else:
        lignes = (
            db.query(Provenance)
            .filter(
                Provenance.entity_type == type_resultat,
                Provenance.entity_id == resultat_id,
            )
            .order_by(Provenance.imported_at.asc())
            .all()
        )
        if not lignes:
            raise HTTPException(
                status_code=404,
                detail="Aucune provenance enregistrée pour ce résultat.",
            )
        fiches = _fiches_provenance_generiques(
            type_resultat, resultat_id, lignes
        )
        retour_url = fiches[0]["retour_url"]

    return _render(
        "provenance.html",
        ui_key=ui_key,
        active="",
        fiches=fiches,
        retour_url=retour_url,
        type_resultat=type_resultat,
        resultat_id=resultat_id,
    )
