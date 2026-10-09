"""Normalisation moléculaire — SMILES canonique, InChI, InChIKey (RDKit).

Point d'entrée : ``normaliser_compose(session, compound_id)``.

Règles dures (honnêteté scientifique) :

- un SMILES **invalide** est SIGNALÉ (statut ``"invalide"`` + raison
  explicite) : le champ ``smiles`` d'origine n'est JAMAIS modifié ni
  « corrigé » silencieusement ;
- les formes calculées (canonique / InChI / InChIKey) ne sont écrites
  que dans des champs VIDES ; un champ déjà renseigné avec une valeur
  différente est SIGNALÉ comme divergence (``divergences``) et n'est
  JAMAIS écrasé ;
- sans RDKit (niveau CORE), aucune normalisation n'est possible : le
  rapport porte le statut ``"indisponible"`` avec la raison explicite,
  et rien n'est écrit.

Conventions (comme ``webintel.science.service``) : la session est le
premier argument ; AUCUNE fonction ne committe.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from webintel.science import chimie as _chimie
from webintel.science.models import Compound

__all__ = [
    "normaliser_compose",
    "normaliser_composes",
    "rapport_normalisation",
]

#: Statuts possibles du rapport de normalisation.
STATUTS = (
    "normalise",  # au moins un champ a été complété
    "deja_normalise",  # tout était déjà renseigné et cohérent
    "invalide",  # SMILES invalide — signalé, rien modifié
    "sans_smiles",  # aucun SMILES à normaliser
    "indisponible",  # RDKit absent — rien calculé, rien écrit
)


def _formes_calculees(smiles: str) -> tuple[dict[str, str | None], str | None]:
    """Calcule (canonique, InChI, InChIKey) via RDKit.

    Retourne ``(formes, raison)`` : ``raison`` non-None si le calcul est
    impossible (SMILES invalide ou RDKit absent).
    """
    diagnostic = _chimie.diagnostic_smiles(smiles)
    if not diagnostic["valide"]:
        return {}, diagnostic["raison"] or "SMILES invalide."
    canonique = diagnostic["smiles_canonique"]
    inchi: str | None = None
    inchikey: str | None = None
    try:
        from rdkit import Chem  # import local : RDKit optionnel

        mol = Chem.MolFromSmiles(canonique or smiles)
        if mol is not None:
            inchi = Chem.MolToInchi(mol) or None
            inchikey = Chem.MolToInchiKey(mol) or None
    except Exception as exc:  # noqa: BLE001 — calcul impossible
        return {}, f"Calcul InChI impossible : {exc}."
    return (
        {
            "smiles_canonique": canonique,
            "inchi": inchi,
            "inchikey": inchikey,
        },
        None,
    )


def normaliser_compose(
    session: Session, compound_id: int, ecrire: bool = True
) -> dict[str, Any]:
    """Normalise la structure d'un composé (rapport explicite).

    - ``ecrire=True`` (défaut) : les champs vides (``inchi``,
      ``inchikey``) sont complétés depuis le SMILES. Le SMILES
      d'origine (champ ``smiles``) n'est JAMAIS réécrit — la forme
      canonique sert uniquement à signaler une divergence de saisie
      (traçabilité de la source) ;
    - un champ non vide dont la valeur diffère du calcul → entrée dans
      ``divergences`` (``{"champ", "valeur_stockee",
      "valeur_calculee"}``), JAMAIS écrasé ;
    - SMILES invalide → statut ``"invalide"``, rien n'est écrit ;
    - sans RDKit → statut ``"indisponible"``, rien n'est écrit.

    Retourne ``{"compound_id", "nom", "statut", "smiles_origine",
    "champs_completes", "divergences", "raison"}``. Lève ``KeyError``
    si le composé est absent. Ne committe jamais.
    """
    compose = session.get(Compound, compound_id)
    if compose is None:
        raise KeyError(f"Composé introuvable : id={compound_id}")

    rapport: dict[str, Any] = {
        "compound_id": compound_id,
        "nom": compose.name,
        "statut": "sans_smiles",
        "smiles_origine": compose.smiles,
        "champs_completes": [],
        "divergences": [],
        "raison": "",
    }
    smiles = (compose.smiles or "").strip()
    if not smiles:
        rapport["raison"] = "Aucun SMILES renseigné : rien à normaliser."
        return rapport
    if not _chimie.rdkit_disponible():
        rapport["statut"] = "indisponible"
        rapport["raison"] = _chimie.RAISON_RDKIT_ABSENT
        return rapport

    formes, raison = _formes_calculees(smiles)
    if raison is not None:
        # SMILES invalide : SIGNALÉ, jamais corrigé silencieusement.
        rapport["statut"] = "invalide"
        rapport["raison"] = (
            f"SMILES invalide : {raison} Le SMILES d'origine est "
            "conservé tel quel — corrigez-le depuis la source."
        )
        return rapport

    # Correspondance champs du modèle <-> formes calculées. Le SMILES
    # d'origine (``smiles``) n'est jamais réécrit : la forme canonique
    # sert uniquement à détecter une divergence de saisie.
    cibles = {
        "inchi": formes["inchi"],
        "inchikey": formes["inchikey"],
    }
    for champ, valeur_calculee in cibles.items():
        if not valeur_calculee:
            continue
        valeur_stockee = (getattr(compose, champ) or "").strip()
        if not valeur_stockee:
            if ecrire:
                setattr(compose, champ, valeur_calculee)
                rapport["champs_completes"].append(champ)
        elif valeur_stockee != valeur_calculee:
            rapport["divergences"].append(
                {
                    "champ": champ,
                    "valeur_stockee": valeur_stockee,
                    "valeur_calculee": valeur_calculee,
                }
            )
    # Divergence éventuelle entre le SMILES saisi et sa forme canonique
    # (information, jamais une réécriture).
    canonique = formes["smiles_canonique"]
    if canonique and smiles != canonique:
        rapport["divergences"].append(
            {
                "champ": "smiles",
                "valeur_stockee": smiles,
                "valeur_calculee": canonique,
                "note": (
                    "Le SMILES saisi diffère de sa forme canonique : "
                    "l'original est conservé (traçabilité de la source)."
                ),
            }
        )
    if ecrire and rapport["champs_completes"]:
        session.flush()
    if rapport["champs_completes"]:
        rapport["statut"] = "normalise"
        rapport["raison"] = (
            "Champs complétés : "
            + ", ".join(rapport["champs_completes"])
            + "."
        )
    elif rapport["divergences"]:
        rapport["statut"] = "deja_normalise"
        rapport["raison"] = (
            "Aucun champ vide à compléter ; "
            f"{len(rapport['divergences'])} divergence(s) signalée(s) "
            "(valeurs stockées conservées, jamais écrasées)."
        )
    else:
        rapport["statut"] = "deja_normalise"
        rapport["raison"] = (
            "InChI et InChIKey déjà renseignés et cohérents avec le SMILES."
        )
    return rapport


def normaliser_composes(
    session: Session, ids: list[int] | None = None, ecrire: bool = True
) -> dict[str, Any]:
    """Normalise un lot de composés (rapport global).

    ``ids`` : restreint aux composés donnés ; ``None`` = tous les
    composés ayant un SMILES. Retourne ``{"date", "traites",
    "rapports", "resume"}`` où ``resume`` compte par statut. Ne
    committe jamais.
    """
    from webintel.core.utils import utcnow

    requete = session.query(Compound)
    if ids is not None:
        requete = requete.filter(Compound.id.in_(ids))
    composes = requete.order_by(Compound.id).all()
    rapports = [normaliser_compose(session, c.id, ecrire=ecrire) for c in composes]
    resume: dict[str, int] = {statut: 0 for statut in STATUTS}
    for rapport in rapports:
        resume[rapport["statut"]] = resume.get(rapport["statut"], 0) + 1
    return {
        "date": utcnow().isoformat(),
        "traites": len(rapports),
        "rapports": rapports,
        "resume": resume,
    }


def rapport_normalisation(rapport: dict[str, Any]) -> str:
    """Forme textuelle lisible d'un rapport (UI / logs, en français)."""
    lignes = [
        f"Composé #{rapport['compound_id']} « {rapport['nom']} » : "
        f"{rapport['statut']}.",
    ]
    if rapport["raison"]:
        lignes.append(rapport["raison"])
    for champ in rapport["champs_completes"]:
        lignes.append(f"  + {champ} complété.")
    for divergence in rapport["divergences"]:
        lignes.append(
            f"  ! divergence {divergence['champ']} : stocké "
            f"« {divergence['valeur_stockee']} » vs calculé "
            f"« {divergence['valeur_calculee']} » (stocké conservé)."
        )
    return "\n".join(lignes)
