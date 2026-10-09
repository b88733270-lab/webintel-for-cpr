"""Adaptateur de docking moléculaire — VRAI AutoDock Vina local (v2.0.0).

Moteur ``docking_adapter`` — version 2.0.0.

RÈGLE DURE — honnêteté scientifique
----------------------------------
Cet adaptateur est un PONT vers le moteur externe **AutoDock Vina**,
explicitement installé par l'utilisateur (binaire ``vina`` dans le PATH).

- ``is_available()`` détecte RÉELLEMENT le binaire via ``shutil.which("vina")``
  et capture sa version (``vina --version``) ;
- si Vina est présent, ``run()`` exécute un VRAI docking local ;
- si Vina est absent, ``run()`` lève ``RuntimeError`` avec un message
  explicite : il ne simule JAMAIS un docking, ne fabrique JAMAIS de
  fausses affinités.

Un docking simulé serait une tromperie scientifique : mieux vaut un
refus explicite qu'un faux résultat.

DÉROGATION DOCUMENTÉE au contrat plugin
---------------------------------------
Le contrat plugin interdit les appels shell ; cet adaptateur y déroge de
façon strictement encadrée, car un docking exige l'exécution du binaire
externe :

- exécution via ``subprocess.run`` avec ``argv`` FIXE (liste), ``shell=False``
  explicite — JAMAIS de commande arbitraire, JAMAIS de ``shell=True`` ;
- ``argv`` construit uniquement depuis des flottants/entiers VALIDÉS et des
  chemins de fichiers temporaires générés par WebIntel (aucune interpolation
  de chaîne fournie par l'utilisateur) ;
- fichiers temporaires confinés dans un ``TemporaryDirectory`` éphémère,
  nettoyé après exécution ;
- seul binaire autorisé : celui résolu par ``shutil.which("vina")``
  (moteur local explicitement installé — frontière de confiance documentée).

Contrat d'entrée/sortie
-----------------------
ENTRÉES (``INPUTS``) :

- ``recepteur_pdbqt`` (str, obligatoire) : récepteur au format PDBQT — soit
  le contenu textuel, soit un chemin vers un fichier ``.pdbqt`` lisible par
  le serveur. WebIntel NE convertit PAS PDB → PDBQT (préparez le récepteur
  avec AutoDockTools, OpenBabel ou meeko).
- ``ligand_pdbqt`` (str, optionnel) : ligand au format PDBQT (contenu ou
  chemin ``.pdbqt``).
- ``ligand_smiles`` (str, optionnel) : ligand en SMILES, converti en PDBQT
  par la préparation documentée ci-dessous.
- EXACTEMENT UN des deux champs ligand est requis.

Préparation des ligands (SMILES → PDBQT, documentée)
----------------------------------------------------
1. RDKit : ``Chem.MolFromSmiles`` (sanitization), ajout des hydrogènes ;
2. conformère 3D : ETKDGv3 avec ``randomSeed`` = seed du run (reproductible),
   optimisation MMFF best-effort (200 itérations) ;
3. meeko ``MoleculePreparation`` : attribution des types d'atomes AutoDock,
   charges partielles Gasteiger, définition des liaisons rotatives ;
4. écriture PDBQT via ``meeko.PDBQTWriterLegacy``.

Limites documentées de la préparation : un seul conformère de départ ;
protonation à pH neutre par défaut (tautomères/protonations alternatifs non
explorés) ; le récepteur est utilisé TEL QUEL (aucune préparation).
Sans RDKit ou sans meeko : la conversion est impossible — fournissez
directement ``ligand_pdbqt`` (erreur explicite, jamais de résultat inventé).

PARAMÈTRES (``PARAMETERS``) : ``centre_boite`` / ``taille_boite`` (bornée
5..60 Å par axe), ``exhaustiveness`` (1..32), ``nb_poses`` (1..20),
``seed`` (entier, défaut 42 — reproductibilité).

SORTIES (``run()`` → dict) : ``affinites_kcal_mol``, ``rmsd_bornes``,
``nb_poses``, ``poses_pdbqt``, ``unites``, ``commande_vina`` (argv exact à
titre de traçabilité), ``vina`` (binaire + version), ``seed``,
``entrees_hash`` (SHA-256 du récepteur et du ligand réellement dockés),
``parametres``, ``journal_vina``, ``avertissement`` (« résultat
informatique »), ``limites``.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile

try:
    from webintel.science import chimie as _chimie
except Exception:  # noqa: BLE001 — adaptateur dégradé proprement
    _chimie = None

ENGINE_NAME = "docking_adapter"
ENGINE_VERSION = "2.0.0"
DESCRIPTION = (
    "Adaptateur de docking moléculaire vers AutoDock Vina (binaire local). "
    "Détecte réellement le binaire `vina` dans le PATH et exécute un VRAI "
    "docking local quand il est installé ; refuse explicitement sinon "
    "(jamais de faux score). Récepteur PDBQT + ligand PDBQT/SMILES, boîte "
    "bornée, seed fixé (reproductible). Résultat informatique : prédiction "
    "à confirmer expérimentalement."
)

INPUTS = {
    "recepteur_pdbqt": (
        "Récepteur au format PDBQT : contenu textuel du fichier PDBQT ou "
        "chemin vers un fichier .pdbqt lisible par le serveur. Chaîne non "
        "vide obligatoire. (WebIntel ne convertit pas PDB → PDBQT : préparez "
        "le récepteur avec AutoDockTools, OpenBabel ou meeko.)"
    ),
    "ligand_pdbqt": (
        "Ligand au format PDBQT : contenu textuel ou chemin vers un fichier "
        ".pdbqt. EXACTEMENT UN des deux champs 'ligand_pdbqt' / "
        "'ligand_smiles' est requis."
    ),
    "ligand_smiles": (
        "Ligand en notation SMILES (ex. 'CC(=O)Oc1ccccc1C(=O)O'), converti "
        "en PDBQT par la préparation documentée (RDKit + meeko). EXACTEMENT "
        "UN des deux champs 'ligand_pdbqt' / 'ligand_smiles' est requis."
    ),
}

PARAMETERS = {
    "centre_boite": {
        "type": "list",
        "default": [0.0, 0.0, 0.0],
        "min": None,
        "max": None,
        "description": (
            "Centre de la boîte de recherche [x, y, z] en angströms "
            "(coordonnées du repère PDB)."
        ),
    },
    "taille_boite": {
        "type": "list",
        "default": [20.0, 20.0, 20.0],
        "min": None,
        "max": None,
        "description": (
            "Dimensions de la boîte de recherche [sx, sy, sz] en angströms "
            "(5 à 60 par axe — boîte bornée)."
        ),
    },
    "exhaustiveness": {
        "type": "int",
        "default": 8,
        "min": 1,
        "max": 32,
        "description": (
            "Exhaustivité de la recherche conformationnelle "
            "(--exhaustiveness de Vina ; 8 = défaut Vina)."
        ),
    },
    "nb_poses": {
        "type": "int",
        "default": 9,
        "min": 1,
        "max": 20,
        "description": (
            "Nombre de poses conservées (--num_modes de Vina)."
        ),
    },
    "seed": {
        "type": "int",
        "default": 42,
        "min": 1,
        "max": 2147483647,
        "description": (
            "Graine aléatoire (--seed de Vina) : mêmes entrées + même seed "
            "→ même résultat (reproductibilité). Défaut 42."
        ),
    },
}

LIMITATIONS = [
    "DÉROGATION DOCUMENTÉE au contrat plugin (« aucun appel shell ») : "
    "exécution du SEUL binaire local `vina` (résolu par shutil.which) via "
    "subprocess avec argv fixe, shell=False, fichiers temporaires confinés. "
    "Aucune commande arbitraire n'est jamais exécutée.",
    "Moteur externe : sans le binaire `vina` explicitement installé, "
    "l'adaptateur REFUSE de calculer (jamais de faux docking).",
    "RÉSULTAT INFORMATIQUE : l'affinité estimée (kcal/mol) est une "
    "PRÉDICTION — pas une mesure d'activité. À confirmer expérimentalement "
    "(tests in vitro). Elle ne prédit ni la cinétique, ni la sélectivité, "
    "ni la toxicité.",
    "La qualité du résultat dépend entièrement du récepteur fourni TEL QUEL "
    "(résolution, protonation — WebIntel ne le prépare pas) et du placement "
    "de la boîte de recherche.",
    "Préparation SMILES → PDBQT : un seul conformère de départ (ETKDGv3, "
    "seed fixé), protonation à pH neutre par défaut ; nécessite RDKit + "
    "meeko, sinon fournissez directement un ligand PDBQT.",
]

#: Message unique d'indisponibilité (testé par la suite de tests).
RAISON_INDISPONIBLE = (
    "AutoDock Vina non installé (binaire `vina` introuvable dans le PATH) — "
    "voir docs/simulations.md pour l'installation. Aucun calcul effectué, "
    "aucun résultat produit."
)

#: Délai maximal d'exécution de Vina (secondes) — garde-fou anti-blocage.
_DELAI_MAX_S = 600

#: Tailles maximales des entrées PDBQT (garde-fou).
_TAILLE_MAX_RECEPTEUR = 10 * 1024 * 1024
_TAILLE_MAX_LIGAND = 2 * 1024 * 1024

_REMARQUE_RESULTAT = re.compile(
    r"^REMARK VINA RESULT:\s+(\S+)\s+(\S+)\s+(\S+)", re.MULTILINE
)
_MODELE = re.compile(r"^MODEL\s+\d+", re.MULTILINE)


# ---------------------------------------------------------------------------
# Détection réelle de Vina
# ---------------------------------------------------------------------------

def _localiser_vina() -> str | None:
    """Chemin du binaire `vina` dans le PATH (None si absent)."""
    return shutil.which("vina")


def _version_vina(binaire: str) -> str | None:
    """Capture la version via `vina --version` (None si illisible)."""
    try:
        proc = subprocess.run(
            [binaire, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    lignes = (proc.stdout or "").strip().splitlines()
    if not lignes:
        return None
    return lignes[0].strip()[:200]


def diagnostic_vina() -> dict:
    """Diagnostic complet de Vina (toujours appelable, ne lève jamais).

    Retourne ``{"binaire": str|None, "version": str|None,
    "disponible": bool, "raison": str}``.
    """
    binaire = _localiser_vina()
    if not binaire:
        return {
            "binaire": None,
            "version": None,
            "disponible": False,
            "raison": RAISON_INDISPONIBLE,
        }
    version = _version_vina(binaire)
    return {
        "binaire": binaire,
        "version": version,
        "disponible": True,
        "raison": (
            "" if version
            else "Binaire `vina` trouvé mais version illisible — enregistrée comme « inconnue »."
        ),
    }


def is_available() -> tuple[bool, str]:
    """Disponibilité réelle : le binaire `vina` est-il dans le PATH ?

    Ne lève jamais. ``(True, "")`` si présent (version capturée via
    ``diagnostic_vina()``), ``(False, RAISON_INDISPONIBLE)`` sinon.
    """
    diag = diagnostic_vina()
    if diag["disponible"]:
        return True, ""
    return False, diag["raison"]


# ---------------------------------------------------------------------------
# Validation stricte des entrées
# ---------------------------------------------------------------------------

def _est_nombre(valeur: object) -> bool:
    """Vrai si `valeur` est un nombre réel (booléens exclus)."""
    return isinstance(valeur, (int, float)) and not isinstance(valeur, bool)


def _valide_vecteur(
    nom: str, valeur: object, borne_min: float | None, borne_max: float | None
) -> list[float]:
    """Valide un vecteur [x, y, z] de nombres (message d'erreur en français)."""
    if not isinstance(valeur, (list, tuple)) or len(valeur) != 3:
        raise ValueError(
            f"'{nom}' doit être une liste de 3 nombres [x, y, z] "
            f"(reçu : {valeur!r})."
        )
    vecteur = []
    for composante in valeur:
        if not _est_nombre(composante):
            raise ValueError(
                f"'{nom}' doit contenir uniquement des nombres "
                f"(reçu : {valeur!r})."
            )
        nombre = float(composante)
        if borne_min is not None and nombre < borne_min:
            raise ValueError(
                f"'{nom}' : chaque composante doit être >= {borne_min} "
                f"(reçu : {valeur!r})."
            )
        if borne_max is not None and nombre > borne_max:
            raise ValueError(
                f"'{nom}' : chaque composante doit être <= {borne_max} "
                f"(reçu : {valeur!r})."
            )
        vecteur.append(nombre)
    return vecteur


def _valide_entier(nom: str, valeur: object, vmin: int, vmax: int, defaut: int) -> int:
    """Valide un paramètre entier borné."""
    if valeur is None:
        valeur = defaut
    if isinstance(valeur, bool) or not isinstance(valeur, int):
        raise ValueError(f"'{nom}' doit être un entier (reçu : {valeur!r}).")
    if not vmin <= valeur <= vmax:
        raise ValueError(f"'{nom}' doit être entre {vmin} et {vmax} (reçu : {valeur}).")
    return valeur


def _charger_pdbqt(valeur: object, nom: str, taille_max: int) -> tuple[str, str]:
    """Matérialise une entrée PDBQT (contenu textuel ou chemin .pdbqt).

    Retourne ``(contenu, description_source)``. Lève ``ValueError`` (français)
    si invalide. Les chemins sont résolus strictement : fichier existant,
    suffixe ``.pdbqt``, taille bornée.
    """
    if not isinstance(valeur, str) or not valeur.strip():
        raise ValueError(f"'{nom}' doit être une chaîne PDBQT non vide.")
    texte = valeur.strip()
    if len(texte) > taille_max and not (
        texte.lower().endswith(".pdbqt") and os.path.isfile(texte)
    ):
        raise ValueError(
            f"'{nom}' : contenu trop volumineux "
            f"({len(texte)} caractères, maximum {taille_max})."
        )
    # Chemin vers un fichier .pdbqt lisible par le serveur ?
    if texte.lower().endswith(".pdbqt") and os.path.isfile(texte):
        taille = os.path.getsize(texte)
        if taille > taille_max:
            raise ValueError(
                f"'{nom}' : fichier trop volumineux ({taille} octets, "
                f"maximum {taille_max})."
            )
        try:
            with open(texte, "r", encoding="utf-8", errors="strict") as fh:
                contenu = fh.read()
        except OSError as exc:
            raise ValueError(f"'{nom}' : impossible de lire {texte!r} ({exc}).")
        source = f"fichier {os.path.basename(texte)}"
    else:
        contenu = texte
        source = "contenu fourni"
    if not any(
        ligne.startswith(("ATOM", "HETATM", "REMARK"))
        for ligne in contenu.splitlines()
    ):
        raise ValueError(
            f"'{nom}' : contenu PDBQT invalide — aucune ligne "
            "ATOM/HETATM/REMARK détectée."
        )
    return contenu, source


def _rdkit_pret_pour_docking() -> tuple[bool, str]:
    """Vrai si la préparation SMILES → PDBQT est possible (RDKit + meeko)."""
    if _chimie is None or not _chimie.rdkit_disponible():
        return False, (
            "RDKit absent (niveau SCIENCE non installé : "
            "`pip install -e \".[science]\"`)"
        )
    try:
        import meeko  # noqa: F401
    except ImportError:
        return False, "'meeko' absent (`pip install meeko`)"
    return True, ""


def meeko_disponible() -> bool:
    """Vrai si le paquet optionnel ``meeko`` est importable.

    Garde-fou public (utilisé par les tests et la page Paramètres) :
    sans meeko, la préparation SMILES → PDBQT est impossible et le
    logiciel demande explicitement un ligand PDBQT déjà préparé —
    il continue de fonctionner normalement par ailleurs.
    """
    try:
        import meeko  # noqa: F401
    except ImportError:
        return False
    return True


def validate_inputs(inputs: dict, params: dict) -> dict:
    """Valide la FORME des entrées/paramètres (sans aucun calcul chimique).

    Vérifie les types, les bornes et la présence des champs obligatoires ;
    ne tente ni conversion de format ni exécution. Lève ``ValueError``
    (français) si invalide. Retourne ``{"inputs": ..., "params": ...}``.
    """
    if not isinstance(inputs, dict):
        raise ValueError(
            "Les entrées doivent être un objet {'recepteur_pdbqt': ..., "
            "'ligand_pdbqt'|'ligand_smiles': ...}."
        )

    # Compatibilité ascendante : l'ancien champ `recepteur_pdb` (PDB brut)
    # n'est plus accepté — message de migration explicite.
    if "recepteur_pdb" in inputs and "recepteur_pdbqt" not in inputs:
        raise ValueError(
            "Le champ 'recepteur_pdb' (PDB brut) n'est plus accepté : "
            "fournissez 'recepteur_pdbqt' (récepteur préparé au format "
            "PDBQT — WebIntel ne convertit pas PDB → PDBQT ; voir "
            "docs/simulations.md)."
        )

    recepteur = inputs.get("recepteur_pdbqt")
    recepteur_contenu, recepteur_source = _charger_pdbqt(
        recepteur, "recepteur_pdbqt", _TAILLE_MAX_RECEPTEUR
    )

    ligand_pdbqt = inputs.get("ligand_pdbqt")
    ligand_smiles = inputs.get("ligand_smiles")
    a_pdbqt = isinstance(ligand_pdbqt, str) and ligand_pdbqt.strip() != ""
    a_smiles = isinstance(ligand_smiles, str) and ligand_smiles.strip() != ""
    if a_pdbqt and a_smiles:
        raise ValueError(
            "Un seul ligand : fournissez 'ligand_pdbqt' OU 'ligand_smiles', "
            "pas les deux."
        )
    if not a_pdbqt and not a_smiles:
        raise ValueError(
            "Ligand manquant : fournissez 'ligand_pdbqt' (PDBQT) ou "
            "'ligand_smiles' (SMILES, converti via RDKit + meeko)."
        )
    ligand_contenu: str | None = None
    ligand_source = ""
    smiles_normalise: str | None = None
    if a_pdbqt:
        ligand_contenu, ligand_source = _charger_pdbqt(
            ligand_pdbqt, "ligand_pdbqt", _TAILLE_MAX_LIGAND
        )
    else:
        assert isinstance(ligand_smiles, str)
        smiles_normalise = ligand_smiles.strip()
        if len(smiles_normalise) > 2000:
            raise ValueError("'ligand_smiles' : SMILES trop long (maximum 2000).")
        pret, raison = _rdkit_pret_pour_docking()
        if not pret:
            raise ValueError(
                "Conversion SMILES → PDBQT impossible : "
                f"{raison}. Fournissez directement 'ligand_pdbqt'."
            )
        ligand_source = "smiles (conversion RDKit + meeko)"

    if not isinstance(params, dict):
        raise ValueError("Les paramètres doivent être un objet (ex. {'exhaustiveness': 8}).")
    centre = _valide_vecteur(
        "centre_boite", params.get("centre_boite", PARAMETERS["centre_boite"]["default"]),
        None, None,
    )
    taille = _valide_vecteur(
        "taille_boite", params.get("taille_boite", PARAMETERS["taille_boite"]["default"]),
        5.0, 60.0,
    )
    exhaustiveness = _valide_entier(
        "exhaustiveness",
        params.get("exhaustiveness", PARAMETERS["exhaustiveness"]["default"]),
        1, 32, PARAMETERS["exhaustiveness"]["default"],
    )
    nb_poses = _valide_entier(
        "nb_poses",
        params.get("nb_poses", PARAMETERS["nb_poses"]["default"]),
        1, 20, PARAMETERS["nb_poses"]["default"],
    )
    seed = _valide_entier(
        "seed",
        params.get("seed", PARAMETERS["seed"]["default"]),
        1, 2147483647, PARAMETERS["seed"]["default"],
    )

    return {
        "inputs": {
            "recepteur_pdbqt": recepteur_contenu,
            "recepteur_source": recepteur_source,
            "ligand_pdbqt": ligand_contenu,
            "ligand_smiles": smiles_normalise,
            "ligand_source": ligand_source,
        },
        "params": {
            "centre_boite": centre,
            "taille_boite": taille,
            "exhaustiveness": exhaustiveness,
            "nb_poses": nb_poses,
            "seed": seed,
        },
    }


# ---------------------------------------------------------------------------
# Préparation du ligand : SMILES → PDBQT (documentée)
# ---------------------------------------------------------------------------

def _smiles_vers_pdbqt(smiles: str, seed: int) -> str:
    """Convertit un SMILES en PDBQT (RDKit + meeko) — préparation documentée.

    Étapes : RDKit (sanitization, +H) → conformère 3D ETKDGv3 (randomSeed =
    seed, reproductible) → optimisation MMFF best-effort → meeko
    (types AutoDock, charges Gasteiger, liaisons rotatives) → PDBQT.
    Lève ``RuntimeError`` explicite si impossible (jamais de valeur inventée).
    """
    if _chimie is None or not _chimie.rdkit_disponible():  # garde-fou
        raise RuntimeError(
            "Préparation SMILES → PDBQT impossible : RDKit absent — "
            "fournissez 'ligand_pdbqt'."
        )
    try:
        from meeko import MoleculePreparation, PDBQTWriterLegacy
    except ImportError as exc:
        raise RuntimeError(
            "Préparation SMILES → PDBQT impossible : 'meeko' absent "
            "(`pip install meeko`) — fournissez 'ligand_pdbqt'."
        ) from exc
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise RuntimeError(
            f"SMILES invalide pour le docking : {smiles[:80]!r} "
            "(rejeté par RDKit) — aucun calcul effectué."
        )
    try:
        mol = Chem.AddHs(mol)
        # RDKit ≥ 2023 : la graine se règle sur l'objet de paramètres.
        parametres_etkdg = AllChem.ETKDGv3()
        parametres_etkdg.randomSeed = seed
        code = AllChem.EmbedMolecule(mol, parametres_etkdg)
    except Exception as exc:  # noqa: BLE001 — préparation impossible
        raise RuntimeError(
            f"Échec de génération du conformère 3D (ETKDG) : {exc}."
        ) from exc
    if code != 0:
        raise RuntimeError(
            "Échec de génération du conformère 3D (ETKDG n'a pas convergé) — "
            "aucun calcul effectué."
        )
    try:
        AllChem.MMFFOptimizeMolecule(mol, maxIters=200)
    except Exception:  # noqa: BLE001 — optimisation best-effort
        pass
    try:
        configurations = MoleculePreparation().prepare(mol)
        if not configurations:
            raise RuntimeError("meeko n'a produit aucune configuration.")
        ecrit = PDBQTWriterLegacy.write_string(configurations[0])
        pdbqt = ecrit[0] if isinstance(ecrit, (list, tuple)) else ecrit
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 — préparation impossible
        raise RuntimeError(
            f"Échec de préparation PDBQT (meeko) : {exc} — aucun calcul effectué."
        ) from exc
    if not pdbqt or "REMARK" not in pdbqt:
        raise RuntimeError(
            "Préparation PDBQT invalide (sortie meeko vide) — aucun calcul effectué."
        )
    return pdbqt


# ---------------------------------------------------------------------------
# Exécution réelle de Vina
# ---------------------------------------------------------------------------

def _sha256(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def _extraire_poses(pdbqt_sortie: str) -> list[dict]:
    """Extrait les poses de la sortie PDBQT de Vina (REMARK VINA RESULT).

    Retourne ``[{"affinite": float, "rmsd_borne_inf": float,
    "rmsd_borne_sup": float, "pdbqt": str}]`` trié par affinité croissante.
    """
    positions = [m.start() for m in _MODELE.finditer(pdbqt_sortie)]
    poses = []
    for i, debut in enumerate(positions):
        fin = positions[i + 1] if i + 1 < len(positions) else len(pdbqt_sortie)
        bloc = pdbqt_sortie[debut:fin].strip()
        m = _REMARQUE_RESULTAT.search(bloc)
        if not m:
            continue
        try:
            affinite = float(m.group(1))
            borne_inf = float(m.group(2))
            borne_sup = float(m.group(3))
        except ValueError:
            continue
        poses.append(
            {
                "affinite": affinite,
                "rmsd_borne_inf": borne_inf,
                "rmsd_borne_sup": borne_sup,
                "pdbqt": bloc,
            }
        )
    poses.sort(key=lambda p: p["affinite"])
    return poses


def _construire_argv(
    binaire: str, recepteur: str, ligand: str, sortie: str,
    centre: list[float], taille: list[float],
    exhaustiveness: int, nb_poses: int, seed: int,
) -> list[str]:
    """Construit l'argv FIXE de Vina (aucune interpolation shell).

    Tous les paramètres numériques proviennent de valeurs validées
    (flottants/entiers) ; les chemins sont générés par WebIntel.

    Nota : Vina 1.2.x n'accepte plus l'option ``--log`` (présente en 1.1.x) ;
    le journal est récupéré via la sortie standard capturée.
    """
    return [
        binaire,
        "--receptor", recepteur,
        "--ligand", ligand,
        "--center_x", repr(centre[0]),
        "--center_y", repr(centre[1]),
        "--center_z", repr(centre[2]),
        "--size_x", repr(taille[0]),
        "--size_y", repr(taille[1]),
        "--size_z", repr(taille[2]),
        "--exhaustiveness", str(exhaustiveness),
        "--num_modes", str(nb_poses),
        "--seed", str(seed),
        "--out", sortie,
    ]


def run(inputs: dict, params: dict) -> dict:
    """Exécute un VRAI docking local via le binaire `vina`.

    Refuse explicitement (``RuntimeError``) si Vina est absent — ne simule
    JAMAIS un docking, ne fabrique JAMAIS de fausses affinités. En cas
    d'échec de Vina (code retour, délai, sortie illisible), lève
    ``RuntimeError`` explicite : aucun faux résultat n'est retourné.
    """
    diag = diagnostic_vina()
    if not diag["disponible"]:
        raise RuntimeError(
            "Moteur de docking indisponible : " + diag["raison"]
        )
    binaire = diag["binaire"]
    assert binaire is not None

    valide = validate_inputs(inputs, params)
    entrees = valide["inputs"]
    parametres = valide["params"]

    # Préparation du ligand (PDBQT fourni tel quel, ou SMILES → PDBQT).
    if entrees["ligand_pdbqt"] is not None:
        ligand_pdbqt = entrees["ligand_pdbqt"]
    else:
        assert entrees["ligand_smiles"] is not None
        ligand_pdbqt = _smiles_vers_pdbqt(
            entrees["ligand_smiles"], parametres["seed"]
        )

    recepteur_pdbqt = entrees["recepteur_pdbqt"]
    hash_recepteur = _sha256(recepteur_pdbqt)
    hash_ligand = _sha256(ligand_pdbqt)

    with tempfile.TemporaryDirectory(prefix="webintel-docking-") as tmp:
        chemin_recepteur = os.path.join(tmp, "recepteur.pdbqt")
        chemin_ligand = os.path.join(tmp, "ligand.pdbqt")
        chemin_sortie = os.path.join(tmp, "poses.pdbqt")
        chemin_journal = os.path.join(tmp, "vina.log")
        with open(chemin_recepteur, "w", encoding="utf-8") as fh:
            fh.write(recepteur_pdbqt)
        with open(chemin_ligand, "w", encoding="utf-8") as fh:
            fh.write(ligand_pdbqt)

        argv = _construire_argv(
            binaire,
            chemin_recepteur, chemin_ligand, chemin_sortie,
            parametres["centre_boite"], parametres["taille_boite"],
            parametres["exhaustiveness"], parametres["nb_poses"],
            parametres["seed"],
        )
        # Dérogation documentée : subprocess SANS shell, argv fixe.
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=_DELAI_MAX_S,
                shell=False,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"AutoDock Vina : délai dépassé ({_DELAI_MAX_S} s) — calcul "
                "interrompu, aucun résultat retourné."
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"AutoDock Vina : impossible d'exécuter {binaire!r} ({exc}) — "
                "aucun résultat retourné."
            ) from exc

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-2000:]
            raise RuntimeError(
                f"AutoDock Vina a échoué (code {proc.returncode}) : {detail} — "
                "aucun faux résultat retourné."
            )
        if not os.path.isfile(chemin_sortie):
            raise RuntimeError(
                "AutoDock Vina n'a produit aucun fichier de poses — "
                "aucun résultat retourné."
            )
        with open(chemin_sortie, "r", encoding="utf-8", errors="replace") as fh:
            sortie_pdbqt = fh.read()
        # Vina 1.2.x écrit son journal sur stdout (plus d'option --log) :
        # on le conserve dans le fichier journal pour traçabilité.
        with open(chemin_journal, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(proc.stdout or "")
        journal = proc.stdout or ""

    poses = _extraire_poses(sortie_pdbqt)
    if not poses:
        raise RuntimeError(
            "AutoDock Vina : aucune pose exploitable dans la sortie "
            "(REMARK VINA RESULT introuvable) — aucun résultat retourné."
        )
    poses = poses[: parametres["nb_poses"]]

    return {
        "moteur": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "vina": {
            "binaire": binaire,
            "version": diag["version"] or "inconnue",
        },
        "affinites_kcal_mol": [round(p["affinite"], 3) for p in poses],
        "rmsd_bornes": [
            [p["rmsd_borne_inf"], p["rmsd_borne_sup"]] for p in poses
        ],
        "nb_poses": len(poses),
        "poses_pdbqt": [p["pdbqt"] for p in poses],
        "unites": "kcal/mol",
        "commande_vina": " ".join(argv),
        "seed": parametres["seed"],
        "entrees_hash": {
            "recepteur_pdbqt_sha256": hash_recepteur,
            "ligand_pdbqt_sha256": hash_ligand,
            "ligand_source": entrees["ligand_source"],
            "recepteur_source": entrees["recepteur_source"],
        },
        "parametres": parametres,
        "journal_vina": journal[-5000:],
        "avertissement": (
            "RÉSULTAT INFORMATIQUE — PRÉDICTION, PAS UNE MESURE : l'affinité "
            "estimée par AutoDock Vina (kcal/mol) approxime l'énergie libre "
            "de liaison ; elle ne prouve aucune activité biologique et doit "
            "être confirmée expérimentalement (tests in vitro). Ne prédit ni "
            "la cinétique, ni la sélectivité, ni la toxicité."
        ),
        "limites": list(LIMITATIONS),
    }
