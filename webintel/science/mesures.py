"""Mesures expérimentales — chaîne RAW → NORMALISÉ → ANALYSE → DÉRIVÉ.

Une mesure de laboratoire / terrain vit dans ``mesures_experimentales``
avec son ``niveau_chaine`` :

- ``"brut"`` (RAW) : valeur telle que saisie / importée ;
- ``"normalise"`` (NORMALISÉ) : nettoyée, unités vérifiées ;
- ``"analyse"`` (ANALYSE) : résultat d'un calcul sur des mesures
  (moyennes, ajustements...) — jamais une mesure ;
- ``"derive"`` (DÉRIVÉ) : valeur dérivée pour un usage aval
  (ex. conversion documentée).

Règles dures :
- la promotion crée TOUJOURS une nouvelle ligne (append-only) via
  ``promouvoir_mesure`` — la donnée brute n'est jamais écrasée ;
- les unités incohérentes ou inconnues sont SIGNALÉES (champ
  ``avertissements`` + contrôle qualité), **jamais corrigées
  silencieusement** ;
- mesures privées par défaut (``is_private=True``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from webintel.science.models import MesureExperimentale

__all__ = [
    "NIVEAUX_CHAINE",
    "ORDRE_CHAINE",
    "UNITES_CONCENTRATION",
    "UNITES_DOSE",
    "UNITES_TEMPS",
    "UNITES_REPONSE",
    "unite_connue",
    "dimension_unite",
    "valider_unites",
    "creer_mesure",
    "promouvoir_mesure",
    "chaine_mesure",
]

#: Niveaux de la chaîne de preuve, dans l'ordre.
NIVEAUX_CHAINE = ("brut", "normalise", "analyse", "derive")
ORDRE_CHAINE = {niveau: i for i, niveau in enumerate(NIVEAUX_CHAINE)}

#: Vocabulaires d'unités reconnus (minuscules comparées sans casse).
#: Toute unité hors vocabulaire est signalée, jamais corrigée.
UNITES_CONCENTRATION = frozenset({
    "m", "mm", "µm", "um", "nm", "pm",
    "g/l", "mg/l", "µg/l", "ug/l", "ng/l",
    "mg/ml", "µg/ml", "ug/ml", "ng/ml", "pg/ml",
    "g/kg", "mg/kg", "µg/kg", "ug/kg",
    "ppm", "ppb", "%", "mg/g", "µg/g", "ug/g",
})
UNITES_DOSE = frozenset({
    "mg/kg", "µg/kg", "ug/kg", "g/kg",
    "mg", "µg", "ug", "g", "kg",
    "ml/kg", "µl/kg", "ul/kg", "l/kg",
    "mmol/kg", "µmol/kg", "umol/kg",
})
UNITES_TEMPS = frozenset({
    "s", "sec", "seconde", "secondes",
    "min", "minute", "minutes",
    "h", "heure", "heures", "hr", "hrs",
    "j", "jour", "jours", "semaine", "semaines",
})
UNITES_REPONSE = frozenset({
    "%", "pourcent", "fraction", "ratio",
    # Les réponses de type CI50/CE50 portent des unités de concentration.
    *UNITES_CONCENTRATION,
})

_DIMENSIONS = {
    "concentration": UNITES_CONCENTRATION,
    "dose": UNITES_DOSE,
    "temps": UNITES_TEMPS,
    "reponse": UNITES_REPONSE,
}

_CHAMP_DIMENSION = {
    "concentration_unite": "concentration",
    "dose_unite": "dose",
    "time_unite": "temps",
    "response_unite": "reponse",
    "unite": None,  # générique : accepté s'il appartient à AU MOINS une dimension
}


def _canonique(unite: str | None) -> str:
    return (unite or "").strip().lower().replace("µ", "µ")


def unite_connue(unite: str | None, dimension: str | None = None) -> bool:
    """Dit si ``unite`` appartient au vocabulaire (de ``dimension`` si donnée)."""
    canon = _canonique(unite)
    if not canon:
        return False
    if dimension is not None:
        return canon in _DIMENSIONS[dimension]
    return any(canon in vocab for vocab in _DIMENSIONS.values())


def dimension_unite(unite: str | None) -> str | None:
    """Dimension physique d'une unité connue (``None`` si inconnue)."""
    canon = _canonique(unite)
    for dimension, vocab in _DIMENSIONS.items():
        if canon in vocab:
            return dimension
    return None


def valider_unites(champs: dict[str, Any]) -> list[str]:
    """Valide les unités d'une mesure — retourne des AVERTISSEMENTS.

    - unité inconnue → avertissement (jamais corrigée, jamais bloquante) ;
    - ``unite`` générique hors de tout vocabulaire → avertissement ;
    - incohérence dimensionnelle (ex. ``dose_unite`` temporelle) →
      avertissement.
    Une liste vide = unités propres ou absentes (l'absence d'unité est
    signalée par le contrôle qualité, pas ici).
    """
    avertissements: list[str] = []
    for champ, dimension in _CHAMP_DIMENSION.items():
        valeur = champs.get(champ)
        if not valeur or not str(valeur).strip():
            continue
        if dimension is None:
            if not unite_connue(valeur):
                avertissements.append(
                    f"Unité générique inconnue « {valeur} » (champ « unite ») : "
                    "vérifiez la saisie — valeur conservée telle quelle."
                )
            continue
        if not unite_connue(valeur, dimension):
            dim_reelle = dimension_unite(valeur)
            precision = (
                f" (ressemble à une unité de « {dim_reelle} »)"
                if dim_reelle else ""
            )
            avertissements.append(
                f"Unité « {valeur} » inattendue pour « {champ} »"
                f" (dimension attendue : {dimension}){precision} : "
                "valeur conservée telle quelle, à vérifier."
            )
    return avertissements


def creer_mesure(session: Session, **champs: Any) -> MesureExperimentale:
    """Crée une mesure brute (niveau ``"brut"``).

    Les unités sont validées via ``valider_unites`` : tout écart est
    ajouté à ``avertissements`` sans modifier les valeurs. Donnée
    privée par défaut. Ne committe jamais.
    """
    avertissements = list(champs.pop("avertissements", None) or [])
    avertissements.extend(valider_unites(champs))
    mesure = MesureExperimentale(
        niveau_chaine="brut",
        avertissements=avertissements,
        **champs,
    )
    session.add(mesure)
    session.flush()
    return mesure


def promouvoir_mesure(
    session: Session,
    mesure_id: int,
    niveau_cible: str,
    *,
    modifications: dict[str, Any] | None = None,
    note: str | None = None,
) -> MesureExperimentale:
    """Promeut une mesure au niveau suivant de la chaîne (append-only).

    Crée une NOUVELLE ligne au ``niveau_cible`` pointant vers la source
    via ``normalise_depuis_id`` ; la ligne source est intouchée. Seule
    la promotion vers le niveau immédiatement supérieur est autorisée
    (``brut`` → ``normalise`` → ``analyse`` → ``derive``) : on ne saute
    jamais d'étape sans laisser de trace. ``modifications`` porte les
    valeurs ajustées (ex. unité normalisée, moyenne calculée) et
    ``note`` explique la transformation. Lève ``ValueError`` si la
    promotion est invalide. Ne committe jamais.
    """
    if niveau_cible not in ORDRE_CHAINE:
        raise ValueError(
            f"Niveau cible invalide : {niveau_cible!r}. "
            f"Niveaux : {', '.join(NIVEAUX_CHAINE)}."
        )
    source = session.get(MesureExperimentale, mesure_id)
    if source is None:
        raise ValueError(f"Mesure {mesure_id} introuvable.")
    rang_source = ORDRE_CHAINE[source.niveau_chaine]
    if rang_source >= len(NIVEAUX_CHAINE) - 1:
        raise ValueError(
            f"La mesure {mesure_id} est déjà au dernier niveau "
            f"(« {source.niveau_chaine} ») : aucune promotion possible."
        )
    attendu = rang_source + 1
    if ORDRE_CHAINE[niveau_cible] != attendu:
        raise ValueError(
            f"Promotion invalide : « {source.niveau_chaine} » → "
            f"« {niveau_cible} ». Seule la promotion vers le niveau "
            "immédiatement supérieur est autorisée "
            f"(attendu : « {NIVEAUX_CHAINE[attendu]} »)."
        )
    attributs = {
        colonne.name
        for colonne in MesureExperimentale.__table__.columns
        if colonne.name not in ("id", "created_at", "updated_at")
    }
    nouveaux = {
        nom: getattr(source, nom)
        for nom in attributs
        if nom not in ("niveau_chaine", "normalise_depuis_id")
    }
    for cle, valeur in (modifications or {}).items():
        if cle not in attributs:
            raise ValueError(f"Champ inconnu pour la promotion : {cle!r}.")
        nouveaux[cle] = valeur
    nouveaux["niveau_chaine"] = niveau_cible
    nouveaux["normalise_depuis_id"] = source.id
    avertissements = list(nouveaux.get("avertissements") or [])
    avertissements.extend(valider_unites(nouveaux))
    nouveaux["avertissements"] = avertissements
    if niveau_cible in ("analyse", "derive"):
        nouveaux["data_level"] = "prediction"
    if note:
        provenance = dict(nouveaux.get("provenance") or {})
        notes = list(provenance.get("notes_promotion") or [])
        notes.append(note)
        provenance["notes_promotion"] = notes
        nouveaux["provenance"] = provenance
    nouvelle = MesureExperimentale(**nouveaux)
    session.add(nouvelle)
    session.flush()
    return nouvelle


def chaine_mesure(session: Session, mesure_id: int) -> list[MesureExperimentale]:
    """Remonte la chaîne de preuve d'une mesure (brut → ... → mesure).

    Retourne les maillons de la chaîne, du plus brut au niveau demandé.
    Lecture seule.
    """
    mesure = session.get(MesureExperimentale, mesure_id)
    if mesure is None:
        return []
    chaine = [mesure]
    while chaine[0].normalise_depuis_id is not None:
        parent = session.get(MesureExperimentale, chaine[0].normalise_depuis_id)
        if parent is None:
            break
        chaine.insert(0, parent)
    return chaine
