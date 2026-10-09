"""Versionnement des études de combinaison — journal append-only (v2.2).

Chaque ``StudyVersion`` est un instantané (snapshot JSON) de l'état d'une
étude de combinaison (``CombinationStudy``) à un instant donné.

RÈGLE D'OR : le journal est append-only — on ne modifie ni ne supprime
jamais une version existante. Une « restauration » crée TOUJOURS une
NOUVELLE étude (nouvelles entrées = celles du snapshot, dossier
recalculé sur les données actuelles) ET une nouvelle version qui la
photographie. Aucun écrasement silencieux.

NOTE D'INTÉGRATION : ce module est importé par
``webintel/ui/chercheur_donnees_routes.py`` ; l'enregistrement du routeur
dans ``webintel/main.py`` suffit donc à déclarer la table
``study_versions`` avant ``init_db()``.
"""

from __future__ import annotations

import threading
from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, Session

from webintel.core.utils import utcnow
from webintel.database.base import Base, TZDateTime
from webintel.science.models import CombinationStudy


# Verrou intra-processus (chantier PERSISTANCE + PERFORMANCE) : en mode
# SQLite local, deux threads du même processus qui attribuent un numéro
# de version peuvent s'interbloquer (un thread détient SHARED via son
# SELECT pendant que l'autre veut RESERVED pour son INSERT, et
# inversement au COMMIT). Le verrou sérialise la section critique
# « lecture du max + insertion » — rapide, jamais bloquant longtemps.
# La contrainte unique ``(study_id, numero)`` reste la vraie garde
# inter-processus (+ le retry sur ``IntegrityError`` ci-dessous).
_VERROU_NUMEROS = threading.Lock()


class StudyVersion(Base):
    """Instantané versionné d'une étude de combinaison.

    - ``study_id`` : l'étude photographiée ;
    - ``numero`` : numéro de version au sein de l'étude (1, 2, 3…) ;
    - ``resume`` : résumé lisible (« 3 molécule(s) », « nouvelle cible »…) ;
    - ``snapshot`` : état complet de l'étude (entrées, paramètres,
      dossier en 12 points, hash, statut, provenance) ;
    - ``cree_par`` / ``cree_le`` : qui / quand.
    """

    __tablename__ = "study_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(
        ForeignKey("combination_studies.id"), nullable=False, index=True
    )
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    resume: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    cree_par: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cree_le: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False
    )

    __table_args__ = (
        # Unicité (study_id, numero) — chantier PERSISTANCE +
        # PERFORMANCE : deux exécutions concurrentes ne doivent jamais
        # produire deux versions « n° N » (course lue par le test de
        # concurrence : [1, 1, 2, 3, 4, 5, 5, 6] avant ce correctif).
        # Les bases existantes reçoivent l'index via la migration
        # ``_ensure_study_versions_unique`` (avec déduplication préalable).
        UniqueConstraint(
            "study_id", "numero",
            name="uq_study_versions_study_numero",
        ),
    )


# Champs de CombinationStudy photographiés dans le snapshot.
_CHAMPS_SNAPSHOT = (
    "nom",
    "description",
    "plant_id",
    "plant_part",
    "compound_ids",
    "target",
    "evidence_ids",
    "parametres",
    "resultats",
    "inputs_hash",
    "statut",
    "data_level",
    "provenance",
)


def _resumer(etude: CombinationStudy) -> str:
    """Résumé lisible d'une étude (« 3 molécule(s) », « nouvelle cible »…)."""
    morceaux = [f"{len(etude.compound_ids or [])} molécule(s)"]
    if etude.target:
        morceaux.append(f"cible : {etude.target}")
    if etude.plant_id:
        morceaux.append(f"plante #{etude.plant_id}")
    return " · ".join(morceaux)


def snapshot_etude(etude: CombinationStudy) -> dict:
    """Photographie l'état courant d'une étude (dict JSON-sérialisable)."""
    return {champ: getattr(etude, champ) for champ in _CHAMPS_SNAPSHOT}


def enregistrer_version(
    db: Session,
    etude: CombinationStudy,
    *,
    cree_par: str | None = None,
    resume: str | None = None,
) -> StudyVersion:
    """Enregistre un instantané de l'étude (nouvelle version, append-only).

    Sûr en concurrence (chantier PERSISTANCE + PERFORMANCE) : le numéro
    est attribué en lecture puis insertion ; si deux threads choisissent
    le même numéro, la contrainte unique ``(study_id, numero)`` rejette
    le second et la fonction réessaie (savepoint : le travail en cours
    de l'appelant n'est JAMAIS annulé). Ne committe jamais (convention :
    c'est à l'appelant).

    Lève ``RuntimeError`` après 5 tentatives (cas pathologique).
    """
    from sqlalchemy.exc import IntegrityError

    for tentative in range(5):
        try:
            # Section critique sérialisée (voir _VERROU_NUMEROS) : en
            # SQLite, deux threads concurrents s'interbloqueraient sinon
            # (SHARED sur le SELECT vs RESERVED sur l'INSERT).
            with _VERROU_NUMEROS:
                with db.begin_nested():  # savepoint : échec localisé
                    dernier_numero = (
                        db.query(StudyVersion.numero)
                        .filter(StudyVersion.study_id == etude.id)
                        .order_by(StudyVersion.numero.desc())
                        .first()
                    )
                    numero = (dernier_numero[0] if dernier_numero else 0) + 1
                    version = StudyVersion(
                        study_id=etude.id,
                        numero=numero,
                        resume=resume if resume is not None else _resumer(etude),
                        snapshot=snapshot_etude(etude),
                        cree_par=cree_par,
                        cree_le=utcnow(),
                    )
                    db.add(version)
                    db.flush()
            return version
        except IntegrityError:
            # Course inter-processus sur le numéro : un autre processus a
            # pris ce numéro entre notre lecture et notre insertion — on
            # réessaie avec le nouveau maximum. Le savepoint a tout
            # annulé localement (le travail de l'appelant est intact).
            continue
    raise RuntimeError(
        "Impossible d'enregistrer une version après 5 tentatives "
        "(concurrence anormale sur les numéros de version)."
    )


def get_version(db: Session, version_id: int) -> StudyVersion:
    """Charge une version ou lève ``KeyError`` (404)."""
    version = db.get(StudyVersion, version_id)
    if version is None:
        raise KeyError(f"Version d'étude introuvable : id={version_id}.")
    return version


# Libellés français des champs comparés (comparaison v1/v2).
_LIBELLES_COMPARAISON = {
    "nom": "Nom de l'étude",
    "description": "Description",
    "plant_id": "Plante (id)",
    "plant_part": "Partie de plante",
    "compound_ids": "Molécules (ids)",
    "target": "Cible biologique",
    "evidence_ids": "Preuves (ids)",
    "statut": "Statut",
    "data_level": "Niveau de données",
    "inputs_hash": "Empreinte des entrées (SHA-256)",
}

# Clés de provenance comparées comme « logiciels / versions ».
_CLES_LOGICIELS = (
    "chimie_disponible",
    "chimie_raison",
    "chimie_infos_biblio",
    "score_version",
)


def _canonique(valeur) -> str:
    """Forme canonique JSON pour comparer deux valeurs (déterministe)."""
    from webintel.database.helpers import canonical_json

    try:
        return canonical_json(valeur)
    except Exception:
        return repr(valeur)


def _ligne_diff(champ: str, libelle: str, valeur_a, valeur_b) -> dict:
    """Une ligne de comparaison (identique ou différente)."""
    identique = _canonique(valeur_a) == _canonique(valeur_b)
    ligne: dict = {
        "champ": champ,
        "libelle": libelle,
        "identique": identique,
    }
    if isinstance(valeur_a, list) and isinstance(valeur_b, list):
        set_a, set_b = set(valeur_a), set(valeur_b)
        ligne["ajoutes"] = sorted(set_b - set_a, key=repr)
        ligne["retires"] = sorted(set_a - set_b, key=repr)
    if not identique:
        ligne["valeur_version_a"] = valeur_a
        ligne["valeur_version_b"] = valeur_b
    return ligne


def _info_version(version: StudyVersion) -> dict:
    """En-tête JSON d'une version (identité, auteur, date)."""
    cree_le = version.cree_le
    return {
        "id": version.id,
        "numero": version.numero,
        "resume": version.resume,
        "cree_par": version.cree_par,
        "cree_le": cree_le.isoformat() if cree_le is not None else None,
    }


def comparer_versions(
    db: Session, version_id_a: int, version_id_b: int
) -> dict:
    """Compare deux versions d'une même étude (comparaison v1/v2).

    Sections comparées :
    - **entrées** : nom, description, plante, partie, molécules, cible,
      preuves, statut, niveau de données, empreinte des entrées ;
    - **paramètres** : union des clés des deux ``parametres`` ;
    - **logiciels** : versions issues de la provenance (module
      chimoinformatique, version du score…) ;
    - **résultats** : sections du dossier en 12 points (ajoutées /
      retirées) + empreinte SHA-256 du dossier complet.

    Retourne un dict JSON-sérialisable en français :
    ``{"etude_id", "version_a", "version_b", "sections",
    "nb_differences", "resume"}``. Chaque ligne porte ``identique`` et,
    si différente, les deux valeurs (les listes portent aussi
    ``ajoutes``/``retires``).

    Lève ``KeyError`` si une version est absente, ``ValueError`` si les
    deux versions n'appartiennent pas à la même étude.
    """
    from webintel.core.utils import sha256_hex

    version_a = get_version(db, version_id_a)
    version_b = get_version(db, version_id_b)
    if version_a.study_id != version_b.study_id:
        raise ValueError(
            "Comparaison impossible : les versions "
            f"#{version_id_a} et #{version_id_b} n'appartiennent pas à "
            "la même étude."
        )
    snap_a = dict(version_a.snapshot or {})
    snap_b = dict(version_b.snapshot or {})

    lignes_entrees = [
        _ligne_diff(
            champ, _LIBELLES_COMPARAISON[champ],
            snap_a.get(champ), snap_b.get(champ),
        )
        for champ in _LIBELLES_COMPARAISON
    ]
    cles_params = sorted(
        set((snap_a.get("parametres") or {}))
        | set((snap_b.get("parametres") or {}))
    )
    lignes_params = [
        _ligne_diff(
            cle, f"Paramètre « {cle} »",
            (snap_a.get("parametres") or {}).get(cle),
            (snap_b.get("parametres") or {}).get(cle),
        )
        for cle in cles_params
    ]
    prov_a = dict(snap_a.get("provenance") or {})
    prov_b = dict(snap_b.get("provenance") or {})
    lignes_logiciels = [
        _ligne_diff(
            cle, f"Logiciel/version « {cle} »",
            prov_a.get(cle), prov_b.get(cle),
        )
        for cle in _CLES_LOGICIELS
    ]
    dossier_a = snap_a.get("resultats") or {}
    dossier_b = snap_b.get("resultats") or {}
    sections_a = set(dossier_a) if isinstance(dossier_a, dict) else set()
    sections_b = set(dossier_b) if isinstance(dossier_b, dict) else set()
    lignes_resultats = [
        _ligne_diff(
            "sections_dossier",
            "Sections du dossier en 12 points",
            sorted(sections_a),
            sorted(sections_b),
        ),
        _ligne_diff(
            "empreinte_dossier",
            "Empreinte SHA-256 du dossier complet",
            sha256_hex(_canonique(dossier_a)),
            sha256_hex(_canonique(dossier_b)),
        ),
    ]

    sections = {
        "entrees": lignes_entrees,
        "parametres": lignes_params,
        "logiciels": lignes_logiciels,
        "resultats": lignes_resultats,
    }
    differences = [
        f"{nom_section} : {ligne['libelle']}"
        for nom_section, lignes in sections.items()
        for ligne in lignes
        if not ligne["identique"]
    ]
    nb = len(differences)
    if nb == 0:
        resume = (
            f"Versions {version_a.numero} et {version_b.numero} "
            "identiques (mêmes entrées, paramètres, logiciels, résultats)."
        )
    else:
        resume = (
            f"{nb} différence(s) entre les versions {version_a.numero} "
            f"et {version_b.numero} : " + "; ".join(differences[:8])
            + ("…" if nb > 8 else "")
        )
    return {
        "etude_id": version_a.study_id,
        "version_a": _info_version(version_a),
        "version_b": _info_version(version_b),
        "sections": sections,
        "nb_differences": nb,
        "resume": resume,
    }


def list_versions(db: Session, study_id: int) -> list[StudyVersion]:
    """Versions d'une étude, de la plus ancienne à la plus récente."""
    return (
        db.query(StudyVersion)
        .filter(StudyVersion.study_id == study_id)
        .order_by(StudyVersion.numero.asc())
        .all()
    )


def restaurer_version(
    db: Session, version_id: int, *, cree_par: str | None = None
) -> CombinationStudy:
    """Restaure une version : crée une NOUVELLE étude + une NOUVELLE version.

    La nouvelle étude reprend les entrées du snapshot (molécules, plante,
    cible, preuves, paramètres) ; son dossier en 12 points est recalculé
    sur les données actuelles de la base (honnêteté : le dossier reflète
    l'état présent, pas une copie figée). Sa provenance mentionne la
    version restaurée. Aucune version existante n'est modifiée.

    Ne committe jamais (convention : c'est à l'appelant).
    """
    from webintel.science import combinaisons as comb_module

    version = get_version(db, version_id)
    snap = dict(version.snapshot or {})
    etude_origine = db.get(CombinationStudy, version.study_id)
    if etude_origine is None:
        raise KeyError(
            f"Étude d'origine introuvable pour la version id={version_id}."
        )
    nouvelle = comb_module.creer_etude(
        db,
        project_id=etude_origine.project_id,
        nom=f"{snap.get('nom') or 'Étude'} (restauration v{version.numero})",
        compound_ids=list(snap.get("compound_ids") or []),
        plant_id=snap.get("plant_id"),
        plant_part=snap.get("plant_part"),
        target=snap.get("target"),
        evidence_ids=list(snap.get("evidence_ids") or []),
        parametres=dict(snap.get("parametres") or {}),
        description=snap.get("description"),
        cree_par=cree_par or f"restauration v{version.numero}",
    )
    provenance = dict(nouvelle.provenance or {})
    provenance["restauration_de_version"] = {
        "study_version_id": version.id,
        "numero": version.numero,
        "study_id": version.study_id,
        "inputs_hash_origine": snap.get("inputs_hash"),
    }
    nouvelle.provenance = provenance
    db.flush()
    enregistrer_version(
        db,
        nouvelle,
        cree_par=cree_par,
        resume=f"Restauration de la version {version.numero} "
               f"de l'étude #{version.study_id}",
    )
    return nouvelle
