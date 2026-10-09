"""Chimoinformatique WebIntel — RDKit (installé par défaut depuis la v2.4.0).

Deux niveaux de runtime, jamais imposés :

- **CORE** (toujours disponible) : Python pur, aucune dépendance externe.
  Les moteurs de simulation fonctionnent en heuristique documentée.
- **SCIENCE** (optionnel) : RDKit, activé par
  ``pip install -e ".[science]"`` (ou ``./scripts/install_science.sh``).
  Débloque les calculs structuraux réels : empreintes de Morgan,
  similarité de Tanimoto structurelle, descripteurs moléculaires,
  recherche par sous-structure.

RÈGLE DURE — honnêteté scientifique
----------------------------------
Sans RDKit, les fonctions retournent ``None`` et journalisent la raison
explicite (``logging``, jamais d'exception brute pour une entrée
invalide). **Aucune valeur n'est jamais inventée** : un descripteur non
calculable vaut ``None``, pas une estimation déguisée.

Ce module est le CONTRAT utilisé par les autres chantiers (moteurs de
simulation, docking) : les six fonctions publiques ci-dessous gardent
leurs signatures exactes.

Installation du niveau SCIENCE (chercheur non-développeur) ::

    pip install -e ".[science]"
    # ou
    ./scripts/install_science.sh

Voir ``docs/runtime_scientifique.md``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = [
    "rdkit_disponible",
    "infos_biblio",
    "empreinte_morgan",
    "tanimoto_fp",
    "descripteurs",
    "sous_structure",
    "diagnostic_smiles",
]

#: Raison standard quand RDKit est absent (réutilisée partout).
RAISON_RDKIT_ABSENT = (
    "RDKit n'est pas installé : le niveau SCIENCE n'est pas activé. "
    "Installez-le avec `pip install -e \".[science]\"` ou "
    "`./scripts/install_science.sh` (voir docs/runtime_scientifique.md). "
    "Aucune valeur n'a été calculée ni inventée."
)

#: Bornes de validation des entrées.
_SMILES_MAX = 10000
_SMARTS_MAX = 10000

try:  # pragma: no cover — dépend de l'environnement
    from rdkit import Chem, rdBase
    from rdkit.Chem import Descriptors, Lipinski, rdMolDescriptors

    try:
        from rdkit.Chem import rdFingerprintGenerator as _fpg

        _GENERATEUR_MORGAN = _fpg.GetMorganGenerator
    except ImportError:  # pragma: no cover — vieux RDKit
        _GENERATEUR_MORGAN = None
        from rdkit.Chem import AllChem as _AllChem
    else:
        _AllChem = None

    _RDKIT_DISPONIBLE = True
    _RDKIT_VERSION: str | None = rdBase.rdkitVersion
except ImportError:
    Chem = None  # type: ignore[assignment]
    Descriptors = None  # type: ignore[assignment]
    Lipinski = None  # type: ignore[assignment]
    rdMolDescriptors = None  # type: ignore[assignment]
    rdBase = None  # type: ignore[assignment]
    _GENERATEUR_MORGAN = None
    _AllChem = None
    _RDKIT_DISPONIBLE = False
    _RDKIT_VERSION = None


# ---------------------------------------------------------------------------
# Disponibilité / diagnostic
# ---------------------------------------------------------------------------

def rdkit_disponible() -> bool:
    """Vrai si RDKit est importable ici et maintenant (niveau SCIENCE actif)."""
    return bool(_RDKIT_DISPONIBLE)


def infos_biblio() -> dict:
    """État de la bibliothèque scientifique (toujours appelable).

    Retourne ``{"bibliotheque": "RDKit", "version": str|None,
    "disponible": bool, "raison": str}`` — ``raison`` est vide quand
    disponible, explicite sinon.
    """
    return {
        "bibliotheque": "RDKit",
        "version": _RDKIT_VERSION,
        "disponible": bool(_RDKIT_DISPONIBLE),
        "raison": "" if _RDKIT_DISPONIBLE else RAISON_RDKIT_ABSENT,
    }


def _valider_smiles(smiles: object) -> tuple[object | None, str | None]:
    """Valide un SMILES ; retourne ``(mol, None)`` ou ``(None, raison)``.

    Ne lève jamais : toute entrée invalide donne une raison explicite.
    """
    if not isinstance(smiles, str):
        return None, (
            f"SMILES invalide : une chaîne de caractères est requise "
            f"(reçu {type(smiles).__name__})."
        )
    texte = smiles.strip()
    if not texte:
        return None, "SMILES invalide : chaîne vide."
    if len(texte) > _SMILES_MAX:
        return None, (
            f"SMILES invalide : trop long ({len(texte)} caractères, "
            f"maximum {_SMILES_MAX})."
        )
    if not _RDKIT_DISPONIBLE:
        return None, RAISON_RDKIT_ABSENT
    try:
        mol = Chem.MolFromSmiles(texte)
    except Exception as exc:  # noqa: BLE001 — entrée invalide, pas un bug
        return None, f"SMILES invalide {texte!r} : RDKit a refusé l'analyse ({exc})."
    if mol is None:
        return None, (
            f"SMILES invalide {texte!r} : RDKit n'a pas pu l'interpréter "
            "(notation incorrecte ou chimie impossible)."
        )
    return mol, None


def diagnostic_smiles(smiles: object) -> dict:
    """Diagnostic explicite d'un SMILES (utile à l'UI et aux autres chantiers).

    Retourne ``{"smiles": ..., "valide": bool, "raison": str|None,
    "rdkit_disponible": bool, "smiles_canonique": str|None}``. Ne lève jamais.
    """
    mol, raison = _valider_smiles(smiles)
    canonique = None
    if mol is not None:
        try:
            canonique = Chem.MolToSmiles(mol)
        except Exception:  # noqa: BLE001 — garde-fou
            canonique = None
    return {
        "smiles": smiles if isinstance(smiles, str) else None,
        "valide": mol is not None,
        "raison": raison,
        "rdkit_disponible": bool(_RDKIT_DISPONIBLE),
        "smiles_canonique": canonique,
    }


# ---------------------------------------------------------------------------
# Empreintes & similarité
# ---------------------------------------------------------------------------

def empreinte_morgan(
    smiles: str, rayon: int = 2, taille: int = 2048
) -> list[int] | None:
    """Empreinte de Morgan (ECFP, bits) d'un SMILES — ``None`` si impossible.

    Paramètres validés : ``rayon`` entier 0..4, ``taille`` entière
    64..8192. Retourne une liste de ``taille`` entiers 0/1. Sans RDKit ou
    avec un SMILES invalide : ``None`` + raison journalisée, jamais
    d'exception, jamais de valeur inventée.
    """
    if not isinstance(rayon, int) or isinstance(rayon, bool) or not 0 <= rayon <= 4:
        logger.warning("chimie.empreinte_morgan: rayon invalide (%r) — entier 0..4 requis.", rayon)
        return None
    if not isinstance(taille, int) or isinstance(taille, bool) or not 64 <= taille <= 8192:
        logger.warning("chimie.empreinte_morgan: taille invalide (%r) — entier 64..8192 requis.", taille)
        return None
    mol, raison = _valider_smiles(smiles)
    if raison is not None:
        logger.warning("chimie.empreinte_morgan: %s", raison)
        return None
    try:
        if _GENERATEUR_MORGAN is not None:
            generateur = _GENERATEUR_MORGAN(radius=rayon, fpSize=taille)
            fp = generateur.GetFingerprint(mol)
        else:  # pragma: no cover — vieux RDKit
            fp = _AllChem.GetMorganFingerprintAsBitVect(mol, rayon, nBits=taille)
        bits = [1 if c == "1" else 0 for c in fp.ToBitString()]
    except Exception as exc:  # noqa: BLE001 — calcul impossible
        logger.warning("chimie.empreinte_morgan: calcul impossible (%s).", exc)
        return None
    if len(bits) != taille:  # garde-fou, ne devrait jamais arriver
        logger.warning(
            "chimie.empreinte_morgan: taille inattendue (%d ≠ %d).", len(bits), taille
        )
        return None
    return bits


def _valider_empreinte(fp: object, nom: str) -> tuple[list[int] | None, str | None]:
    """Valide une empreinte (liste/tuple d'entiers 0/1 non vide)."""
    if not isinstance(fp, (list, tuple)) or not fp:
        return None, f"{nom} invalide : liste non vide d'entiers 0/1 requise."
    bits: list[int] = []
    for i, b in enumerate(fp):
        if isinstance(b, bool) or not isinstance(b, int) or b not in (0, 1):
            return None, (
                f"{nom} invalide : l'élément #{i} ({b!r}) n'est pas un bit 0/1."
            )
        bits.append(b)
    return bits, None


def tanimoto_fp(fp1: list[int], fp2: list[int]) -> float | None:
    """Coefficient de Tanimoto entre deux empreintes binaires.

    Pur calcul arithmétique (``|A∩B| / |A∪B|``) — **ne nécessite pas RDKit**.
    Retourne ``None`` + raison journalisée si les empreintes sont invalides,
    de tailles différentes, ou toutes deux vides (similarité non définie).
    """
    bits1, raison1 = _valider_empreinte(fp1, "fp1")
    if raison1 is not None:
        logger.warning("chimie.tanimoto_fp: %s", raison1)
        return None
    bits2, raison2 = _valider_empreinte(fp2, "fp2")
    if raison2 is not None:
        logger.warning("chimie.tanimoto_fp: %s", raison2)
        return None
    assert bits1 is not None and bits2 is not None
    if len(bits1) != len(bits2):
        logger.warning(
            "chimie.tanimoto_fp: tailles différentes (%d ≠ %d).",
            len(bits1), len(bits2),
        )
        return None
    inter = sum(a & b for a, b in zip(bits1, bits2))
    union = sum(a | b for a, b in zip(bits1, bits2))
    if union == 0:
        logger.warning(
            "chimie.tanimoto_fp: empreintes vides (aucun bit actif) — "
            "similarité non définie."
        )
        return None
    return inter / union


# ---------------------------------------------------------------------------
# Descripteurs moléculaires
# ---------------------------------------------------------------------------

#: Clés garanties du dictionnaire retourné par ``descripteurs()``.
CLES_DESCRIPTEURS = (
    "formule",
    "masse_molaire",
    "logp",
    "hbd",
    "hba",
    "tpsa",
    "rotatable_bonds",
    "fraction_csp3",
    "lipinski_violations",
    "inchi_key",
    "smiles_canonique",
)


def _essaie(nom: str, fonction) -> object | None:
    """Exécute ``fonction()`` ; ``None`` + log si le calcul échoue."""
    try:
        return fonction()
    except Exception as exc:  # noqa: BLE001 — descripteur non calculable
        logger.warning("chimie.descripteurs: %s non calculable (%s).", nom, exc)
        return None


def descripteurs(smiles: str) -> dict | None:
    """Descripteurs moléculaires réels via RDKit — ``None`` si impossible.

    Clés : ``formule``, ``masse_molaire`` (g/mol), ``logp`` (Wildman-Crippen),
    ``hbd`` / ``hba`` (donneurs/accepteurs de liaisons H, Lipinski),
    ``tpsa`` (Å²), ``rotatable_bonds``, ``fraction_csp3``,
    ``lipinski_violations`` (0..4 : masse>500, logP>5, HBD>5, HBA>10),
    ``inchi_key``, ``smiles_canonique``.

    Chaque valeur individuellement non calculable vaut ``None`` (jamais
    inventée). Sans RDKit ou SMILES invalide : ``None`` + raison journalisée.
    """
    mol, raison = _valider_smiles(smiles)
    if raison is not None:
        logger.warning("chimie.descripteurs: %s", raison)
        return None

    formule = _essaie("formule", lambda: rdMolDescriptors.CalcMolFormula(mol))
    masse = _essaie("masse_molaire", lambda: round(float(Descriptors.MolWt(mol)), 3))
    logp = _essaie("logp", lambda: round(float(Descriptors.MolLogP(mol)), 3))
    hbd = _essaie("hbd", lambda: int(Lipinski.NumHDonors(mol)))
    hba = _essaie("hba", lambda: int(Lipinski.NumHAcceptors(mol)))
    tpsa = _essaie("tpsa", lambda: round(float(rdMolDescriptors.CalcTPSA(mol)), 2))
    rot = _essaie(
        "rotatable_bonds", lambda: int(Lipinski.NumRotatableBonds(mol))
    )
    csp3 = _essaie(
        "fraction_csp3", lambda: round(float(rdMolDescriptors.CalcFractionCSP3(mol)), 3)
    )
    inchi_key = _essaie(
        "inchi_key", lambda: Chem.MolToInchiKey(mol) or None
    )
    canonique = _essaie("smiles_canonique", lambda: Chem.MolToSmiles(mol))

    violations: int | None = None
    if masse is not None and logp is not None and hbd is not None and hba is not None:
        violations = (
            (1 if masse > 500 else 0)
            + (1 if logp > 5 else 0)
            + (1 if hbd > 5 else 0)
            + (1 if hba > 10 else 0)
        )

    resultat = {
        "formule": formule,
        "masse_molaire": masse,
        "logp": logp,
        "hbd": hbd,
        "hba": hba,
        "tpsa": tpsa,
        "rotatable_bonds": rot,
        "fraction_csp3": csp3,
        "lipinski_violations": violations,
        "inchi_key": inchi_key,
        "smiles_canonique": canonique,
    }
    assert set(resultat) == set(CLES_DESCRIPTEURS)
    return resultat


# ---------------------------------------------------------------------------
# Sous-structure
# ---------------------------------------------------------------------------

def sous_structure(smiles: str, motif_smarts: str) -> bool | None:
    """Vrai si ``smiles`` contient le motif ``motif_smarts`` (SMARTS).

    ``None`` + raison journalisée si RDKit est absent, si le SMILES est
    invalide, ou si le motif SMARTS est invalide. Ne lève jamais.
    """
    mol, raison = _valider_smiles(smiles)
    if raison is not None:
        logger.warning("chimie.sous_structure: %s", raison)
        return None
    if not isinstance(motif_smarts, str) or not motif_smarts.strip():
        logger.warning(
            "chimie.sous_structure: motif SMARTS invalide — chaîne non vide requise."
        )
        return None
    motif_texte = motif_smarts.strip()
    if len(motif_texte) > _SMARTS_MAX:
        logger.warning("chimie.sous_structure: motif SMARTS trop long.")
        return None
    if not _RDKIT_DISPONIBLE:  # déjà vérifié par _valider_smiles, garde-fou
        logger.warning("chimie.sous_structure: %s", RAISON_RDKIT_ABSENT)
        return None
    try:
        motif = Chem.MolFromSmarts(motif_texte)
    except Exception as exc:  # noqa: BLE001 — motif invalide
        logger.warning(
            "chimie.sous_structure: motif SMARTS invalide %r (%s).",
            motif_texte, exc,
        )
        return None
    if motif is None:
        logger.warning(
            "chimie.sous_structure: motif SMARTS invalide %r — "
            "RDKit n'a pas pu l'interpréter.",
            motif_texte,
        )
        return None
    try:
        return bool(mol.HasSubstructMatch(motif))
    except Exception as exc:  # noqa: BLE001 — garde-fou
        logger.warning("chimie.sous_structure: recherche impossible (%s).", exc)
        return None
