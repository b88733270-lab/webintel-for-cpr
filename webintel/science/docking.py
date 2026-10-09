"""Workflow de docking moléculaire (v2.1).

Étapes : récepteur → validation → ligand → préparation → paramètres →
calcul → poses → scores → rapport.

RÈGLE DURE — honnêteté scientifique
----------------------------------
Le calcul réel passe par le moteur ``docking_adapter`` du registre des
simulations (chantier parallèle — accès DÉFENSIF). Quand le moteur est
absent ou indisponible (cas actuel : AutoDock Vina non installé) :

- ``executer_docking`` lève ``MoteurIndisponible`` avec un message
  explicite (ou persiste un job ``statut="refuse"`` selon l'option) ;
- ``apercu_workflow`` affiche les étapes et les paramètres SANS
  produire de faux score (``scores = None`` + message explicite).

AUCUN score de docking n'est jamais inventé.

Traçabilité : chaque job enregistre le récepteur (empreinte SHA-256 du
contenu PDB), le ligand, les paramètres validés, la version de Vina, la
version des bibliothèques, la méthode exacte, les résultats et le hash
des entrées (reproductibilité : même hash → mêmes paramètres).
"""

from __future__ import annotations

import platform

from sqlalchemy.orm import Session

from webintel import __version__ as _webintel_version
from webintel.core.utils import sha256_hex, utcnow
from webintel.science.combinaisons import hacher_entrees
from webintel.science.honnetete import _exiger_honnete
from webintel.science.models import DockingJob

MOTEUR_ATTENDU = "docking_adapter"

ETAPES = [
    ("recepteur", "Récepteur : structure PDB fournie et hachée (SHA-256)."),
    ("validation", "Validation : format PDB, paramètres de boîte, ligand."),
    ("ligand", "Ligand : SMILES validé (canonique si RDKit disponible)."),
    ("preparation", "Préparation : conversion PDBQT (récepteur + ligand)."),
    ("parametres", "Paramètres : boîte de recherche, exhaustivité, poses."),
    ("calcul", "Calcul : exécution AutoDock Vina via docking_adapter."),
    ("poses", "Poses : conformations retenues (format PDBQT)."),
    ("scores", "Scores : affinités estimées (kcal/mol) par pose."),
    ("rapport", "Rapport : méthode exacte, versions, résultats, limites."),
]

LIMITES_DOCKING = [
    "Un score de docking reste une PRÉDICTION informatique : l'affinité "
    "estimée (kcal/mol) n'est pas une mesure d'activité — à confirmer "
    "expérimentalement (tests in vitro).",
    "La qualité dépend de la structure du récepteur (résolution, état de "
    "protonation) et du placement de la boîte de recherche.",
    "Le score approxime l'énergie libre de liaison ; il ne prédit ni la "
    "cinétique, ni la sélectivité, ni la sécurité d'emploi.",
]

MESSAGE_APERCU = (
    "Mode aperçu : le moteur de docking est indisponible, aucun calcul "
    "n'est lancé et aucun score n'est produit (scores = None). Les "
    "étapes et paramètres ci-dessous décrivent ce qui serait exécuté une "
    "fois le moteur installé. Note : l'exécution réelle exige un récepteur "
    "au format PDBQT préparé (voir étape « préparation »)."
)

MESSAGE_RECEPTEUR_PDBQT = (
    "Récepteur non préparé : l'exécution exige un récepteur au format "
    "PDBQT préparé (charges et types d'atomes AutoDock — préparé avec un "
    "outil externe : ADFR, AutoDockTools ou OpenBabel). WebIntel ne convertit "
    "pas PDB → PDBQT afin de ne jamais fausser un calcul. Aucun score "
    "produit (aucune valeur inventée)."
)


def _est_pdbqt(contenu: str) -> bool:
    """Détecte un contenu PDBQT préparé (marqueurs ROOT/BRANCH/TORSDOF).

    Un PDBQT produit par ADFR/AutoDockTools/OpenBabel contient ces
    marqueurs ; un PDB brut ne les contient jamais.
    """
    for ligne in contenu.splitlines():
        mot = ligne.strip().split(" ", 1)[0] if ligne.strip() else ""
        if mot in ("ROOT", "ENDROOT", "BRANCH", "ENDBRANCH", "TORSDOF"):
            return True
    return False


class MoteurIndisponible(ValueError):
    """Le moteur de docking est absent ou indisponible (refus explicite)."""


def _registre():
    try:
        from webintel.simulations import registry

        return registry
    except ImportError:
        return None


def statut_moteur() -> tuple[bool, str]:
    """Disponibilité réelle du moteur de docking (ne lève jamais).

    Retourne ``(disponible, raison)`` — raison explicite si indisponible
    (module manquant, contrat invalide, Vina non installé...).
    """
    registre = _registre()
    if registre is None:
        return False, (
            "Registre des moteurs de simulation indisponible : impossible "
            "de vérifier le moteur de docking."
        )
    try:
        moteur = registre.get_engine(MOTEUR_ATTENDU)
    except KeyError:
        return False, (
            f"Moteur « {MOTEUR_ATTENDU} » inconnu du registre des simulations."
        )
    except ValueError as exc:
        return False, f"Moteur « {MOTEUR_ATTENDU} » indisponible : {exc}"
    except Exception as exc:  # pragma: no cover — défensif
        return False, f"Vérification du moteur impossible : {exc}."
    try:
        disponible, raison = moteur.is_available()
    except Exception as exc:  # pragma: no cover — défensif
        return False, f"is_available() a levé une exception : {exc}."
    if disponible:
        return True, ""
    return False, raison or "Moteur indisponible (raison non précisée)."


def _valider_recepteur(recepteur_pdb: str) -> dict:
    """Valide la structure du récepteur (format PDB minimal)."""
    if not isinstance(recepteur_pdb, str) or not recepteur_pdb.strip():
        raise ValueError(
            "Récepteur invalide : contenu PDB vide (chaîne non vide requise)."
        )
    lignes = recepteur_pdb.splitlines()
    if not any(
        l.startswith(("ATOM  ", "HETATM")) for l in lignes
    ):
        raise ValueError(
            "Récepteur invalide : aucune ligne ATOM/HETATM détectée "
            "(format PDB attendu)."
        )
    return {
        "nb_lignes": len(lignes),
        "nb_atomes": sum(1 for l in lignes if l.startswith(("ATOM  ", "HETATM"))),
        "hash_sha256": sha256_hex(recepteur_pdb),
    }


def _valider_ligand(ligand_smiles: str) -> dict:
    """Valide le ligand (SMILES non vide ; canonique si chimie dispo)."""
    if not isinstance(ligand_smiles, str) or not ligand_smiles.strip():
        raise ValueError(
            "Ligand invalide : SMILES vide (chaîne non vide requise)."
        )
    smiles = ligand_smiles.strip()
    canonique = None
    try:
        from webintel.science import combinaisons as _comb

        ok, _ = _comb.chimie_disponible()
        if ok:
            desc_fn = _comb._fonction_chimie("descripteurs")
            if desc_fn:
                desc = desc_fn(smiles) or {}
                canonique = desc.get("smiles_canonique")
    except Exception:
        canonique = None
    return {"smiles": smiles, "smiles_canonique": canonique}


def _valider_parametres(parametres: dict | None) -> dict:
    """Valide/normalise les paramètres Vina (bornes du contrat moteur)."""
    parametres = dict(parametres or {})
    try:
        from webintel.simulations import registry as _reg

        moteur = _reg.get_engine(MOTEUR_ATTENDU)
        contrat = getattr(moteur, "PARAMETERS", {})
    except Exception:
        contrat = {}

    def _defaut(nom, valeur):
        return contrat.get(nom, {}).get("default", valeur)

    def _vecteur(nom, defaut):
        val = parametres.get(nom, defaut)
        if not isinstance(val, (list, tuple)) or len(val) != 3:
            raise ValueError(
                f"Paramètre « {nom} » invalide : liste de 3 nombres attendue."
            )
        return [float(x) for x in val]

    centre = _vecteur("centre_boite", _defaut("centre_boite", [0.0, 0.0, 0.0]))
    taille = _vecteur("taille_boite", _defaut("taille_boite", [20.0, 20.0, 20.0]))
    for i, dim in enumerate(taille):
        if not 5.0 <= dim <= 60.0:
            raise ValueError(
                f"Paramètre « taille_boite » invalide : l'axe {i} vaut "
                f"{dim} Å (bornes : 5 à 60 Å)."
            )
    exhaustivite = int(parametres.get(
        "exhaustiveness", _defaut("exhaustiveness", 8)))
    if not 1 <= exhaustivite <= 32:
        raise ValueError(
            "Paramètre « exhaustiveness » invalide : entier entre 1 et 32 requis."
        )
    nb_poses = int(parametres.get("nb_poses", _defaut("nb_poses", 9)))
    if not 1 <= nb_poses <= 20:
        raise ValueError(
            "Paramètre « nb_poses » invalide : entier entre 1 et 20 requis."
        )
    return {
        "centre_boite": centre,
        "taille_boite": taille,
        "exhaustiveness": exhaustivite,
        "nb_poses": nb_poses,
    }


def _versions_biblio() -> dict:
    versions = {
        "webintel": _webintel_version,
        "python": platform.python_version(),
    }
    for nom in ("rdkit",):
        try:
            module = __import__(nom)
            versions[nom] = getattr(module, "__version__", "?")
        except ImportError:
            versions[nom] = "indisponible"
    return versions


def apercu_workflow(
    *,
    recepteur_pdb: str,
    ligand_smiles: str,
    parametres: dict | None = None,
    recepteur_nom: str | None = None,
    ligand_nom: str | None = None,
) -> dict:
    """Aperçu du workflow SANS calcul (moteur absent ou non).

    Valide les entrées et les paramètres, affiche les 9 étapes, mais ne
    produit AUCUN score (``scores = None`` + message explicite).
    """
    info_recepteur = _valider_recepteur(recepteur_pdb)
    info_ligand = _valider_ligand(ligand_smiles)
    params = _valider_parametres(parametres)
    disponible, raison = statut_moteur()
    # Auto-contrôle d'honnêteté sur le texte généré de l'aperçu (les noms
    # de récepteur/ligand fournis par le chercheur ne sont pas scannés).
    _exiger_honnete(
        "\n".join([MESSAGE_APERCU, *[d for _, d in ETAPES], *LIMITES_DOCKING]),
        "aperçu docking",
    )
    return {
        "mode": "apercu",
        "message": MESSAGE_APERCU,
        "moteur_disponible": disponible,
        "moteur_raison": raison if not disponible else None,
        "etapes": [
            {"etape": nom, "description": desc} for nom, desc in ETAPES
        ],
        "recepteur": {
            "nom": recepteur_nom,
            "hash_sha256": info_recepteur["hash_sha256"],
            "nb_atomes": info_recepteur["nb_atomes"],
        },
        "ligand": {
            "nom": ligand_nom,
            "smiles": info_ligand["smiles"],
            "smiles_canonique": info_ligand["smiles_canonique"],
        },
        "parametres": params,
        "scores": None,
        "limites": LIMITES_DOCKING,
    }


def executer_docking(
    db: Session,
    *,
    project_id: int,
    nom: str,
    recepteur_pdb: str,
    ligand_smiles: str,
    parametres: dict | None = None,
    recepteur_nom: str | None = None,
    ligand_nom: str | None = None,
    execute_par: str | None = None,
) -> DockingJob:
    """Exécute le workflow complet via le moteur (ou refuse proprement).

    - Moteur disponible : validation → appel moteur → persistance du job
      ``statut="termine"`` (résultat = prédiction informatique).
    - Moteur indisponible : persistance d'un job ``statut="refuse"``
      (résultats=None, raison explicite) + levée de
      ``MoteurIndisponible``. AUCUN faux score n'est jamais produit.
    - Entrées invalides : ``ValueError`` explicite, rien n'est persisté.

    Ne committe jamais (convention : c'est au routeur).
    """
    info_recepteur = _valider_recepteur(recepteur_pdb)
    info_ligand = _valider_ligand(ligand_smiles)
    params = _valider_parametres(parametres)
    if not _est_pdbqt(recepteur_pdb):
        raise ValueError(MESSAGE_RECEPTEUR_PDBQT)
    entrees = {
        "recepteur_hash": info_recepteur["hash_sha256"],
        "ligand_smiles": info_ligand["smiles"],
        "parametres": params,
    }
    hash_entrees = hacher_entrees(entrees)
    provenance = {
        "execute_par": execute_par,
        "execute_le": utcnow().isoformat(),
        "versions_biblio": _versions_biblio(),
    }

    disponible, raison = statut_moteur()
    if not disponible:
        job = DockingJob(
            project_id=project_id,
            nom=nom,
            recepteur_hash=info_recepteur["hash_sha256"],
            recepteur_nom=recepteur_nom,
            ligand_smiles=info_ligand["smiles"],
            ligand_nom=ligand_nom,
            parametres=params,
            moteur=MOTEUR_ATTENDU,
            moteur_version=None,
            versions_biblio=provenance["versions_biblio"],
            methode=(
                "Workflow docking en 9 étapes (récepteur → validation → "
                "ligand → préparation → paramètres → calcul → poses → "
                "scores → rapport) via le moteur docking_adapter "
                "(AutoDock Vina)."
            ),
            resultats={
                "statut": "refuse",
                "raison": raison,
                "scores": None,
                "message": (
                    "Calcul refusé : moteur de docking indisponible. "
                    "Aucun score produit (aucune valeur inventée)."
                ),
            },
            inputs_hash=hash_entrees,
            statut="refuse",
            data_level="prediction",
            execute_par=execute_par,
            provenance=provenance,
        )
        db.add(job)
        db.flush()
        raise MoteurIndisponible(
            f"Docking impossible : {raison} Aucun calcul n'a été lancé, "
            "aucun score produit."
        )

    # Moteur disponible : exécution réelle via le contrat du plugin.
    # Défensif : si le moteur échoue malgré is_available() positif
    # (ex. run() qui refuse), on persiste un refus propre — JAMAIS de
    # faux score.
    registre = _registre()
    moteur = registre.get_engine(MOTEUR_ATTENDU)
    moteur_version = getattr(moteur, "ENGINE_VERSION", "?")
    try:
        validation = moteur.validate_inputs(
            {"recepteur_pdbqt": recepteur_pdb,
             "ligand_smiles": info_ligand["smiles"]},
            {
                "centre_boite": params["centre_boite"],
                "taille_boite": params["taille_boite"],
                "exhaustiveness": params["exhaustiveness"],
                "nb_poses": params["nb_poses"],
            },
        )
        sorties = moteur.run(validation["inputs"], validation["params"])
    except Exception as exc:
        raison = (
            f"Le moteur a refusé l'exécution : {exc} "
            "Aucun score produit (aucune valeur inventée)."
        )
        job = DockingJob(
            project_id=project_id,
            nom=nom,
            recepteur_hash=info_recepteur["hash_sha256"],
            recepteur_nom=recepteur_nom,
            ligand_smiles=info_ligand["smiles"],
            ligand_nom=ligand_nom,
            parametres=params,
            moteur=MOTEUR_ATTENDU,
            moteur_version=moteur_version,
            versions_biblio=provenance["versions_biblio"],
            methode=(
                "Workflow docking en 9 étapes via docking_adapter — "
                "exécution refusée par le moteur."
            ),
            resultats={"statut": "refuse", "raison": raison, "scores": None},
            inputs_hash=hash_entrees,
            statut="refuse",
            data_level="prediction",
            execute_par=execute_par,
            provenance=provenance,
        )
        db.add(job)
        db.flush()
        raise MoteurIndisponible(f"Docking impossible : {raison}")
    resultats = {
        "affinites_kcal_mol": sorties.get("affinites_kcal_mol"),
        "nb_poses": sorties.get("nb_poses"),
        "rmsd_bornes": sorties.get("rmsd_bornes"),
        "unites": sorties.get("unites", "kcal/mol"),
        "commande_vina": sorties.get("commande_vina"),
        "avertissement": (
            "Résultat informatique (prédiction) — à confirmer "
            "expérimentalement. Ne constitue pas une mesure d'activité."
        ),
        "limites": LIMITES_DOCKING,
    }
    job = DockingJob(
        project_id=project_id,
        nom=nom,
        recepteur_hash=info_recepteur["hash_sha256"],
        recepteur_nom=recepteur_nom,
        ligand_smiles=info_ligand["smiles"],
        ligand_nom=ligand_nom,
        parametres=params,
        moteur=MOTEUR_ATTENDU,
        moteur_version=moteur_version,
        versions_biblio=provenance["versions_biblio"],
        methode=(
            "Workflow docking en 9 étapes via docking_adapter "
            f"(version {moteur_version}) — calcul AutoDock Vina réel."
        ),
        resultats=resultats,
        inputs_hash=hash_entrees,
        statut="termine",
        data_level="prediction",
        execute_par=execute_par,
        provenance=provenance,
    )
    db.add(job)
    db.flush()
    return job


def get_docking_job(db: Session, job_id: int) -> DockingJob:
    """Charge un job de docking ou lève ``KeyError`` (404)."""
    job = db.get(DockingJob, job_id)
    if job is None:
        raise KeyError(f"Job de docking introuvable : id={job_id}.")
    return job


def list_docking_jobs(
    db: Session,
    project_id: int,
    limit: int | None = None,
    offset: int = 0,
) -> list[DockingJob]:
    """Jobs de docking d'un projet (isolation projet stricte).

    `limit=None` (défaut) conserve le comportement historique (tout) ;
    `limit`/`offset` activent la pagination.
    """
    requete = (
        db.query(DockingJob)
        .filter(DockingJob.project_id == project_id)
        .order_by(DockingJob.id.desc())
        .offset(max(0, offset))
    )
    if limit is not None:
        requete = requete.limit(max(1, limit))
    return requete.all()
