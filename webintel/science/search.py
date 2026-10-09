"""Recherche scientifique plein corpus — Phase 4 (backend).

``search_science()`` interroge en un seul appel les quatre corpus :
plantes, composés, preuves (publications / références) et simulations.

Honnêteté de la recherche — pas de moteur plein texte à ce stade :
- la correspondance est un simple ``LIKE %motif%`` insensible à la casse,
  portable SQLite / PostgreSQL via
  ``func.lower(cast(colonne, Text))`` (et non ``ilike``, au comportement
  variable selon les dialectes) ;
- les caractères spéciaux LIKE (``%``, ``_``) présents dans la requête sont
  échappés (caractère d'échappement ``!``) pour ne pas élargir la recherche ;
- les colonnes JSON (synonymes, noms vernaculaires, auteurs) sont converties
  en texte sérialisé (``cast(colonne, Text)``) puis comparées au motif :
  c'est une approximation assumée — elle peut produire des correspondances
  fortuites (ex. une clé du dictionnaire ``vernacular_names``) et ne gère
  ni la tokenisation, ni les accents, ni les fautes de frappe. Un vrai
  moteur plein texte (FTS5 sous SQLite, ``tsvector`` sous PostgreSQL) reste
  à brancher plus tard.

Règle d'or anti-invention : la recherche ne crée JAMAIS de lien
plante-composé. Un composé n'est rattaché à des plantes que via les lignes
``PlantCompound`` réellement enregistrées (champ ``plantes_liees``) ; un
composé sans lien expose ``plantes_liees == []``. Les simulations ne sont
interrogées que si ``project_id`` est fourni (données de laboratoire
cloisonnées au projet) et portent toujours le niveau ``prediction`` avec
un avertissement explicite : ce ne sont pas des preuves.
"""

from __future__ import annotations

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from webintel.science.models import (
    Compound,
    DataLevel,
    Evidence,
    Plant,
    PlantCompound,
    Simulation,
)

__all__ = ["search_science"]


_TYPES_VALIDES = ("plante", "compose", "publication", "simulation")

_NIVEAU_PAR_TYPE: dict[str, str] = {
    "plante": DataLevel.PUBLIE.value,
    "compose": DataLevel.PUBLIE.value,
    "publication": DataLevel.PUBLIE.value,
    "simulation": DataLevel.PREDICTION.value,
}

# Mêmes libellés / couleurs que le référentiel `GET /api/science/data-levels`.
_BADGES: dict[str, dict[str, str]] = {
    "publie": {"label": "Publié", "couleur": "#1a7f37"},
    "normalise": {"label": "Normalisé", "couleur": "#0969da"},
    "hypothese": {"label": "Hypothèse", "couleur": "#9a6700"},
    "prediction": {"label": "Prédiction", "couleur": "#8250df"},
    "experimental": {"label": "Expérimental", "couleur": "#cf222e"},
}

# Caractère d'échappement LIKE : « ! » plutôt que « \ » pour éviter toute
# ambiguïté entre SQLite et PostgreSQL (standard_conforming_strings).
_ECHAPPEMENT = "!"

_AVERTISSEMENT_SIMULATION = (
    "Simulation — à confirmer expérimentalement. N'est pas une preuve."
)

_LIMITE_MAX = 100


# ---------------------------------------------------------------------------
# Prédicats portables
# ---------------------------------------------------------------------------

def _echapper_like(texte: str) -> str:
    """Échappe ``!``, ``%`` et ``_`` pour un motif LIKE (ESCAPE ``!``)."""
    return (
        texte.replace(_ECHAPPEMENT, _ECHAPPEMENT * 2)
        .replace("%", _ECHAPPEMENT + "%")
        .replace("_", _ECHAPPEMENT + "_")
    )


def _contient(colonne, motif: str):
    """Prédicat « contient » insensible à la casse, portable SQLite/PG.

    ``colonne`` peut être une colonne JSON : ``cast(..., Text)`` la sérialise
    en texte avant comparaison (approximation documentée en en-tête).
    """
    return func.lower(cast(colonne, Text)).like(motif, escape=_ECHAPPEMENT)


# ---------------------------------------------------------------------------
# Validation des paramètres
# ---------------------------------------------------------------------------

def _valider_types(types: list[str] | None) -> tuple[str, ...]:
    """Normalise le filtre `types` ; lève `ValueError` (message FR) sinon."""
    if types is None:
        return _TYPES_VALIDES
    normalises = tuple(t.strip().lower() for t in types if t and t.strip())
    if not normalises:
        return _TYPES_VALIDES
    inconnus = [t for t in normalises if t not in _TYPES_VALIDES]
    if inconnus:
        raise ValueError(
            "Type de résultat inconnu : "
            + ", ".join(repr(t) for t in inconnus)
            + ". Valeurs acceptées : plante, compose, publication, simulation."
        )
    return normalises


def _valider_niveau(data_level: str | None) -> str | None:
    """Normalise le filtre `data_level` ; lève `ValueError` (message FR) sinon."""
    if data_level is None:
        return None
    normalise = data_level.strip().lower()
    valeurs = {membre.value for membre in DataLevel}
    if normalise not in valeurs:
        raise ValueError(
            f"Niveau de données inconnu : {data_level!r}. Valeurs acceptées : "
            + ", ".join(sorted(valeurs))
            + "."
        )
    return normalise


def _badge(niveau: str) -> dict[str, str]:
    """Badge UI {label, couleur} pour un niveau de données."""
    info = _BADGES.get(niveau, {"label": niveau, "couleur": "#6e7781"})
    return {"label": info["label"], "couleur": info["couleur"]}


def _fiche_base(
    type_resultat: str,
    identifiant: int,
    libelle: str,
    detail: str,
    niveau: str,
    doi: str | None,
    url: str | None,
    reference: str,
    avertissement: str | None,
) -> dict:
    """Squelette commun d'un résultat (types JSON-sérialisables uniquement)."""
    return {
        "type": type_resultat,
        "id": identifiant,
        "libelle": libelle,
        "detail": detail,
        "data_level": niveau,
        "badge": _badge(niveau),
        "source": {
            "doi": doi,
            "url": url,
            "reference": reference or "",
        },
        "avertissement": avertissement,
    }


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------

def search_science(
    db: Session,
    q: str,
    *,
    types: list[str] | None = None,
    data_level: str | None = None,
    partie: str | None = None,
    project_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Recherche plein corpus scientifique (plantes, composés, preuves, simulations).

    Paramètres :
    - ``q`` : requête (≥ 2 caractères après nettoyage, sinon `ValueError`) ;
    - ``types`` : sous-ensemble de ``["plante", "compose", "publication",
      "simulation"]`` (``None`` = tout) ;
    - ``data_level`` : ne garde que les groupes portant ce niveau
      (ex. ``"publie"`` exclut les simulations, ``"prediction"`` ne garde
      que les simulations) ;
    - ``partie`` : filtre sur ``PlantCompound.plant_part`` (insensible à la
      casse) — les plantes retournées sont celles ayant un lien réel avec
      cette partie, et chaque plante expose ``parties_trouvees`` ;
    - ``project_id`` : requis pour interroger les simulations (données de
      laboratoire cloisonnées) ; le contrôle d'accès (403) est du ressort
      de l'appelant (routeur) ;
    - ``limit`` (borné 1..100), ``offset`` (≥ 0) : pagination appliquée
      à chaque groupe.

    Retour (JSON-sérialisable) : ``{"q", "plantes", "composes",
    "publications", "simulations", "total", "limit", "offset"}`` où ``total``
    est la somme simple des groupes retournés (pagination appliquée).

    Lève `ValueError` (messages en français) si ``q`` est trop courte, si
    un type est inconnu ou si ``data_level`` est inconnu.
    """
    requete = (q or "").strip()
    if len(requete) < 2:
        raise ValueError("La recherche nécessite au moins 2 caractères.")
    limite = max(1, min(_LIMITE_MAX, limit))
    decalage = max(0, offset)

    types_actifs = _valider_types(types)
    niveau_filtre = _valider_niveau(data_level)
    motif = f"%{_echapper_like(requete.lower())}%"
    partie_nettoyee = (partie or "").strip()
    motif_partie = (
        f"%{_echapper_like(partie_nettoyee.lower())}%"
        if partie_nettoyee
        else None
    )

    def _niveau_ok(type_resultat: str) -> bool:
        return niveau_filtre is None or _NIVEAU_PAR_TYPE[type_resultat] == niveau_filtre

    plantes: list[dict] = []
    composes: list[dict] = []
    publications: list[dict] = []
    simulations: list[dict] = []

    # --- Plantes ---------------------------------------------------------
    if "plante" in types_actifs and _niveau_ok("plante"):
        requete_plantes = db.query(Plant).filter(
            or_(
                _contient(Plant.scientific_name, motif),
                _contient(Plant.synonyms, motif),
                _contient(Plant.vernacular_names, motif),
                _contient(Plant.family, motif),
                _contient(Plant.genus, motif),
                # Plantes dont une partie de plante liée contient la requête
                # (liens réels uniquement — la recherche n'en crée aucun).
                Plant.id.in_(
                    select(PlantCompound.plant_id).where(
                        _contient(PlantCompound.plant_part, motif)
                    )
                ),
            )
        )
        if motif_partie is not None:
            requete_plantes = requete_plantes.filter(
                Plant.id.in_(
                    select(PlantCompound.plant_id).where(
                        _contient(PlantCompound.plant_part, motif_partie)
                    )
                )
            )
        lignes = (
            requete_plantes.order_by(Plant.scientific_name)
            .offset(decalage)
            .limit(limite)
            .all()
        )
        ids_plantes = [p.id for p in lignes]
        parties_par_plante: dict[int, set[str]] = {}
        if ids_plantes:
            requete_liens = db.query(
                PlantCompound.plant_id, PlantCompound.plant_part
            ).filter(PlantCompound.plant_id.in_(ids_plantes))
            if motif_partie is not None:
                requete_liens = requete_liens.filter(
                    _contient(PlantCompound.plant_part, motif_partie)
                )
            for pid, nom_partie in requete_liens.all():
                if nom_partie:
                    parties_par_plante.setdefault(pid, set()).add(nom_partie)
        for plante in lignes:
            detail = (
                f"Famille : {plante.family}"
                if plante.family
                else (f"Genre : {plante.genus}" if plante.genus else "")
            )
            fiche = _fiche_base(
                "plante",
                plante.id,
                plante.scientific_name,
                detail,
                DataLevel.PUBLIE.value,
                None,
                None,
                "",
                None,
            )
            fiche["parties_trouvees"] = sorted(
                parties_par_plante.get(plante.id, set())
            )
            plantes.append(fiche)

    # --- Composés --------------------------------------------------------
    if "compose" in types_actifs and _niveau_ok("compose"):
        lignes = (
            db.query(Compound)
            .filter(
                or_(
                    _contient(Compound.name, motif),
                    _contient(Compound.synonyms, motif),
                    _contient(Compound.chemical_class, motif),
                )
            )
            .order_by(Compound.name)
            .offset(decalage)
            .limit(limite)
            .all()
        )
        ids_composes = [c.id for c in lignes]
        plantes_liees: dict[int, list[str]] = {}
        if ids_composes:
            requete_liens = (
                db.query(PlantCompound.compound_id, Plant.scientific_name)
                .join(Plant, Plant.id == PlantCompound.plant_id)
                .filter(PlantCompound.compound_id.in_(ids_composes))
                .order_by(PlantCompound.id)
            )
            if motif_partie is not None:
                # Avec un filtre de partie : seules les plantes liées VIA
                # cette partie sont citées (liens réels uniquement).
                requete_liens = requete_liens.filter(
                    _contient(PlantCompound.plant_part, motif_partie)
                )
            for cid, nom_plante in requete_liens.all():
                liste = plantes_liees.setdefault(cid, [])
                if nom_plante and nom_plante not in liste:
                    liste.append(nom_plante)
        for compose in lignes:
            morceaux = [m for m in (compose.formula, compose.chemical_class) if m]
            fiche = _fiche_base(
                "compose",
                compose.id,
                compose.name,
                " · ".join(morceaux),
                DataLevel.PUBLIE.value,
                None,
                None,
                "",
                None,
            )
            # Anti-invention : uniquement les liens PlantCompound réellement
            # enregistrés ; [] si le composé n'est lié à aucune plante.
            fiche["plantes_liees"] = plantes_liees.get(compose.id, [])
            composes.append(fiche)

    # --- Preuves / publications ------------------------------------------
    if "publication" in types_actifs and _niveau_ok("publication"):
        lignes = (
            db.query(Evidence)
            .filter(
                or_(
                    _contient(Evidence.title, motif),
                    _contient(Evidence.excerpt, motif),
                    _contient(Evidence.authors, motif),
                    _contient(Evidence.doi, motif),
                )
            )
            .order_by(Evidence.id.desc())
            .offset(decalage)
            .limit(limite)
            .all()
        )
        for preuve in lignes:
            morceaux = []
            if preuve.authors:
                morceaux.append(
                    ", ".join(str(a) for a in preuve.authors if a)
                )
            if preuve.year:
                morceaux.append(f"({preuve.year})")
            citation = " ".join(m for m in morceaux if m) or (
                (preuve.reference or "")[:160]
            )
            publications.append(
                _fiche_base(
                    "publication",
                    preuve.id,
                    preuve.title or "(sans titre)",
                    citation,
                    DataLevel.PUBLIE.value,
                    preuve.doi,
                    preuve.url,
                    preuve.reference or "",
                    None,
                )
            )

    # --- Simulations (données de laboratoire : project_id requis) --------
    if (
        "simulation" in types_actifs
        and project_id is not None
        and _niveau_ok("simulation")
    ):
        lignes = (
            db.query(Simulation)
            .filter(
                Simulation.project_id == project_id,
                or_(
                    _contient(Simulation.name, motif),
                    _contient(Simulation.description, motif),
                    _contient(Simulation.target, motif),
                    _contient(Simulation.method, motif),
                ),
            )
            .order_by(Simulation.id.desc())
            .offset(decalage)
            .limit(limite)
            .all()
        )
        for simulation in lignes:
            morceaux = [
                m
                for m in (
                    simulation.method,
                    f"cible : {simulation.target}" if simulation.target else "",
                )
                if m
            ]
            simulations.append(
                _fiche_base(
                    "simulation",
                    simulation.id,
                    simulation.name,
                    " · ".join(morceaux),
                    DataLevel.PREDICTION.value,
                    None,
                    None,
                    f"Simulation de laboratoire — projet {project_id}",
                    _AVERTISSEMENT_SIMULATION,
                )
            )

    total = len(plantes) + len(composes) + len(publications) + len(simulations)
    return {
        "q": requete,
        "plantes": plantes,
        "composes": composes,
        "publications": publications,
        "simulations": simulations,
        "total": total,
        "limit": limite,
        "offset": decalage,
    }
