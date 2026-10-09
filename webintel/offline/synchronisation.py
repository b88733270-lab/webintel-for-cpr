"""Moteur de file de synchronisation — WebIntel hors-ligne.

Principe
--------
Quand le mode hors-ligne est actif, chaque modification d'une entité
synchronisable est enregistrée dans ``file_synchronisation``
(``mettre_en_file``) au lieu d'être poussée immédiatement. La
synchronisation (``executer_synchronisation``) rejoue la file contre un
dépôt distant pluggable :

- ``DepotDistantMemoire`` : dépôt en mémoire (tests, démonstrations) ;
- ``DepotDistantDossier`` : dépôt sur disque (un dossier = un
  « serveur » échangeable via clé USB — cas d'usage terrain
  burkinabè : deux installations sans réseau commun).

Règle absolue : **jamais d'écrasement silencieux**. Avant d'appliquer
une opération, le moteur compare la version distante (``updated_at``)
à ``base_version`` (version vue au moment de la mise en file) :

- distant inchangé (ou contenu identique) → l'opération est appliquée,
  statut ``envoye`` ;
- distant modifié entre-temps → statut ``conflit`` + ``remote_payload``
  conservé pour affichage LOCAL vs DISTANT. La résolution est
  EXPLICITE (``resoudre_conflit`` : choix local / distant / fusion
  manuelle) et chaque résolution est journalisée dans
  ``resolutions_conflit`` (qui, quand, quoi, ancienne → nouvelle
  valeur).

Aucune résolution automatique silencieuse n'existe dans ce module.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from webintel.core.utils import utcnow
from webintel.offline.models import (
    ChoixResolution,
    CollecteTerrain,
    FileSynchronisation,
    OperationSync,
    ResolutionConflit,
    StatutSync,
)

try:
    from webintel.science.models import Plant
except Exception:  # pragma: no cover - import circulaire défensif
    Plant = None  # type: ignore[assignment]

try:
    from webintel.science.models import (
        Echantillon,
        ResearchObservation,
        Simulation,
        Study,
    )
except Exception:  # pragma: no cover - import circulaire défensif
    Echantillon = ResearchObservation = Simulation = Study = None  # type: ignore[assignment]

try:
    from webintel.science.lab import LabNote
except Exception:  # pragma: no cover - import circulaire défensif
    LabNote = None  # type: ignore[assignment]


CLES_HORODATAGE = {"created_at", "updated_at"}


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _parse_iso(valeur: str | None) -> datetime | None:
    """Parse une date ISO en ``datetime`` conscient (UTC si naïf)."""
    if not valeur:
        return None
    try:
        dt = datetime.fromisoformat(str(valeur))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _json_safe(valeur: Any) -> Any:
    """Rend une valeur sérialisable en JSON (datum → ISO)."""
    if isinstance(valeur, datetime):
        return valeur.isoformat()
    if isinstance(valeur, (dict,)):
        return {k: _json_safe(v) for k, v in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [_json_safe(v) for v in valeur]
    return valeur


def _contenus_egaux(a: dict, b: dict) -> bool:
    """Égalité de deux charges utiles hors horodatages.

    Évite les faux conflits quand le contenu est identique (ex. rejeu
    après une synchronisation partielle).
    """
    a2 = {k: v for k, v in a.items() if k not in CLES_HORODATAGE}
    b2 = {k: v for k, v in b.items() if k not in CLES_HORODATAGE}
    return a2 == b2


def _payload_depuis_objet(obj: Any) -> dict:
    """Charge utile JSON-safe depuis les colonnes d'un objet SQLAlchemy."""
    payload: dict[str, Any] = {}
    for attr in sa_inspect(obj).mapper.column_attrs:
        payload[attr.key] = _json_safe(getattr(obj, attr.key))
    return payload


# ---------------------------------------------------------------------------
# Gestionnaires d'entités synchronisables (registre générique)
# ---------------------------------------------------------------------------

@dataclass
class GestionnaireEntite:
    """Lecture/écriture locale d'un type d'entité synchronisable."""

    lire: Callable[[Session, int], dict | None]
    ecrire: Callable[[Session, int, dict], None]
    creer: Callable[[Session, dict], int]
    supprimer: Callable[[Session, int], None]
    version: Callable[[Session, int], str | None]


def _fabriquer_gestionnaire(modele: Any, colonnes_inscriptibles: list[str]) -> GestionnaireEntite:
    """Gestionnaire générique fondé sur les colonnes SQLAlchemy."""

    def lire(session: Session, entity_id: int) -> dict | None:
        obj = session.get(modele, entity_id)
        return None if obj is None else _payload_depuis_objet(obj)

    def ecrire(session: Session, entity_id: int, payload: dict) -> None:
        obj = session.get(modele, entity_id)
        if obj is None:
            raise ValueError(
                f"Entité introuvable localement ({modele.__tablename__} #{entity_id})."
            )
        for col in colonnes_inscriptibles:
            if col in payload:
                setattr(obj, col, payload[col])
        session.flush()

    def creer(session: Session, payload: dict) -> int:
        donnees = {
            col: payload[col]
            for col in colonnes_inscriptibles
            if col in payload
        }
        obj = modele(**donnees)
        session.add(obj)
        session.flush()
        return int(obj.id)

    def supprimer(session: Session, entity_id: int) -> None:
        obj = session.get(modele, entity_id)
        if obj is not None:
            session.delete(obj)
            session.flush()

    def version(session: Session, entity_id: int) -> str | None:
        obj = session.get(modele, entity_id)
        if obj is None:
            return None
        maj = getattr(obj, "updated_at", None)
        return maj.isoformat() if maj else None

    return GestionnaireEntite(
        lire=lire, ecrire=ecrire, creer=creer, supprimer=supprimer,
        version=version,
    )


COLONNES_COLLECTE = [
    "project_id", "plante_id", "nom_plante_saisi", "localite", "latitude",
    "longitude", "precision_gps_m", "notes", "echantillon_ref", "voucher",
    "partie_utilisee", "photos", "niveau_confidentialite", "auteur",
]

COLONNES_PLANTE = [
    "scientific_name", "synonyms", "vernacular_names", "family", "genus",
    "species", "taxonomy", "description", "distribution", "used_parts",
    "images", "external_ids",
]

COLONNES_ECHANTILLON = [
    "project_id", "plant_id", "voucher_id", "code_echantillon",
    "collecteur", "date_collecte", "pays", "region", "province", "commune",
    "localite", "site", "latitude", "longitude", "precision_gps",
    "altitude_m", "habitat", "partie", "etat", "sechage", "stockage",
    "institution", "responsable", "source", "cree_par", "data_level",
    "niveau_confidentialite", "evidence_id",
]

COLONNES_ETUDE = [
    "title", "authors", "year", "doi", "pmid", "journal", "study_type",
    "abstract", "url",
]

COLONNES_OBSERVATION = [
    "project_id", "protocol_ref", "observed_date", "author", "laboratory",
    "raw_data", "file_path", "status", "comment", "is_private",
]

COLONNES_SIMULATION = [
    "project_id", "name", "description", "compound_ids", "target",
    "hypotheses", "method", "parameters", "engine_name", "engine_version",
    "result", "units", "run_at", "input_hash", "executed_by", "inputs",
    "confidence", "references", "status",
]

COLONNES_NOTE = [
    "project_id", "auteur", "titre", "contenu", "pieces_jointes",
    "data_level", "version_courante",
]


def _gestionnaires() -> dict[str, GestionnaireEntite]:
    gest: dict[str, GestionnaireEntite] = {
        "collecte": _fabriquer_gestionnaire(CollecteTerrain, COLONNES_COLLECTE),
    }
    if Plant is not None:
        gest["plante"] = _fabriquer_gestionnaire(Plant, COLONNES_PLANTE)
    # Entités chercheur suivies hors-ligne (même fabrique générique).
    optionnels = {
        "echantillon": (Echantillon, COLONNES_ECHANTILLON),
        "etude": (Study, COLONNES_ETUDE),
        "observation": (ResearchObservation, COLONNES_OBSERVATION),
        "simulation": (Simulation, COLONNES_SIMULATION),
        "note": (LabNote, COLONNES_NOTE),
    }
    for cle, (modele, colonnes) in optionnels.items():
        if modele is not None:
            gest[cle] = _fabriquer_gestionnaire(modele, colonnes)
    return gest


GESTIONNAIRES: dict[str, GestionnaireEntite] = _gestionnaires()
"""Registre des types d'entités synchronisables (extensible)."""


def _recreer_avec_id(session: Session, entity_type: str, entity_id: int,
                    payload: dict) -> None:
    """Recrée une entité disparue localement avec son id d'origine.

    Utilisé lors d'une résolution « distant » quand l'opération locale
    était une suppression : on restaure la version distante à
    l'identique au lieu de perdre la donnée.
    """
    modele = {"collecte": CollecteTerrain,
              "plante": Plant}.get(entity_type)
    if modele is None:
        raise ValueError(
            f"Type d'entité non synchronisable : {entity_type!r}."
        )
    colonnes = {"collecte": COLONNES_COLLECTE,
                "plante": COLONNES_PLANTE}[entity_type]
    donnees = {col: payload[col] for col in colonnes if col in payload}
    obj = modele(id=entity_id, **donnees)
    session.add(obj)
    session.flush()


# ---------------------------------------------------------------------------
# Dépôts distants (pluggables)
# ---------------------------------------------------------------------------
class DepotDistant(Protocol):
    """Contrat d'un dépôt distant (le « serveur » de synchronisation)."""

    def lire(self, entity_type: str, entity_id: int) -> dict | None:
        """→ ``{"payload": dict, "updated_at": str|None}`` ou ``None``."""
        ...

    def ecrire(self, entity_type: str, entity_id: int, payload: dict,
               updated_at: str) -> None:
        """Crée ou remplace (upsert) la version distante."""
        ...

    def supprimer(self, entity_type: str, entity_id: int) -> None:
        ...


class DepotDistantMemoire:
    """Dépôt distant en mémoire — tests et démonstrations."""

    def __init__(self) -> None:
        self._magasin: dict[tuple[str, int], dict] = {}

    def lire(self, entity_type: str, entity_id: int) -> dict | None:
        return self._magasin.get((entity_type, entity_id))

    def ecrire(self, entity_type: str, entity_id: int, payload: dict,
               updated_at: str) -> None:
        self._magasin[(entity_type, entity_id)] = {
            "payload": json.loads(json.dumps(payload)),
            "updated_at": updated_at,
        }

    def supprimer(self, entity_type: str, entity_id: int) -> None:
        self._magasin.pop((entity_type, entity_id), None)


class DepotDistantDossier:
    """Dépôt distant sur disque : un dossier = un « serveur ».

    Chaque entité est un fichier JSON
    ``<base>/<entity_type>/<entity_id>.json`` contenant
    ``{"payload": {...}, "updated_at": "..."}``. Le dossier peut être
    copié sur clé USB pour synchroniser deux installations sans réseau
    commun (aller : copier le dossier ; retour : synchroniser).
    """

    def __init__(self, base: str) -> None:
        self.base = base

    def _chemin(self, entity_type: str, entity_id: int) -> str:
        type_sain = "".join(c for c in entity_type if c.isalnum() or c in "_-")
        return os.path.join(self.base, type_sain, f"{int(entity_id)}.json")

    def lire(self, entity_type: str, entity_id: int) -> dict | None:
        chemin = self._chemin(entity_type, entity_id)
        if not os.path.isfile(chemin):
            return None
        with open(chemin, encoding="utf-8") as fh:
            return json.load(fh)

    def ecrire(self, entity_type: str, entity_id: int, payload: dict,
               updated_at: str) -> None:
        chemin = self._chemin(entity_type, entity_id)
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "w", encoding="utf-8") as fh:
            json.dump({"payload": payload, "updated_at": updated_at}, fh,
                      ensure_ascii=False, indent=2)

    def supprimer(self, entity_type: str, entity_id: int) -> None:
        chemin = self._chemin(entity_type, entity_id)
        if os.path.isfile(chemin):
            os.remove(chemin)


# ---------------------------------------------------------------------------
# File : mise en file, exécution, relance, vidage
# ---------------------------------------------------------------------------

def mettre_en_file(
    session: Session,
    entity_type: str,
    entity_id: int,
    operation: str,
    payload: dict,
    auteur: str = "",
    base_version: str | None = None,
    project_id: int | None = None,
    flusher: bool = True,
) -> FileSynchronisation:
    """Enregistre une modification locale dans la file (flush, sans commit).

    ``base_version`` : ``updated_at`` ISO de l'entité au moment de la
    modification — référence de détection de conflit.
    ``project_id`` : cloisonnement projet (déduit du payload si présent).
    ``flusher`` : False quand l'appel a lieu DANS un ``after_flush``
    SQLAlchemy (la mise en file automatique) — ``Session.flush()`` y
    est interdit ; la ligne est alors écrite au prochain flush/commit.
    """
    if entity_type not in GESTIONNAIRES:
        raise ValueError(f"Type d'entité non synchronisable : {entity_type!r}.")
    if operation not in {op.value for op in OperationSync}:
        raise ValueError(f"Opération inconnue : {operation!r}.")
    if base_version is None:
        base_version = GESTIONNAIRES[entity_type].version(session, entity_id)
    if project_id is None:
        project_id = payload.get("project_id")
    existante = _entree_en_cours(session, entity_type, entity_id)
    if existante is not None:
        # Coalescence : une seule entrée en attente par entité — on
        # fusionne l'opération et on rafraîchit la charge utile, sans
        # créer de doublon.
        operation_fusionnee = _fusion_operations(
            existante.operation, operation)
        payload_fusionne = _json_safe(dict(payload))
        if auteur:
            auteur_fusionne = auteur
        else:
            auteur_fusionne = existante.auteur
        if flusher:
            # Appel hors flush : mutation directe de la ligne.
            existante.operation = operation_fusionnee
            existante.payload = payload_fusionne
            existante.statut = StatutSync.EN_ATTENTE.value
            existante.erreur = None
            if project_id is not None:
                existante.project_id = project_id
            existante.auteur = auteur_fusionne
            session.flush()
            return existante
        # DANS un after_flush SQLAlchemy, muter un objet persistant est
        # ignore silencieusement et delete()+add() corrompt l'identity
        # map (reutilisation d'id). On met donc a jour la ligne via une
        # requete Core, puis on expire l'objet ORM pour qu'il recharge
        # les valeurs fraiches a son prochain acces.
        from sqlalchemy import update as _sa_update

        session.execute(
            _sa_update(FileSynchronisation)
            .where(FileSynchronisation.id == existante.id)
            .values(
                operation=operation_fusionnee,
                payload=payload_fusionne,
                statut=StatutSync.EN_ATTENTE.value,
                erreur=None,
                project_id=(project_id if project_id is not None
                            else existante.project_id),
                auteur=auteur_fusionne,
            )
        )
        session.expire(existante)
        return existante
    ligne = FileSynchronisation(
        entity_type=entity_type,
        entity_id=entity_id,
        operation=operation,
        payload=_json_safe(dict(payload)),
        base_version=base_version,
        auteur=auteur,
        statut=StatutSync.EN_ATTENTE.value,
        project_id=project_id,
    )
    session.add(ligne)
    if flusher:
        session.flush()
    return ligne


def _entree_en_cours(
    session: Session, entity_type: str, entity_id: int
) -> FileSynchronisation | None:
    """Entrée déjà en attente ou en erreur pour cette entité (ou None)."""
    return (
        session.query(FileSynchronisation)
        .filter(
            FileSynchronisation.entity_type == entity_type,
            FileSynchronisation.entity_id == entity_id,
            FileSynchronisation.statut.in_([
                StatutSync.EN_ATTENTE.value,
                StatutSync.ECHOUE.value,
            ]),
        )
        .order_by(FileSynchronisation.id.desc())
        .first()
    )


def _fusion_operations(ancienne: str, nouvelle: str) -> str:
    """Opération résultante quand deux modifications se succèdent avant
    synchronisation (jamais de doublon dans la file)."""
    table = {
        ("create", "create"): "create",
        ("create", "update"): "create",
        ("create", "delete"): "delete",
        ("update", "create"): "create",
        ("update", "update"): "update",
        ("update", "delete"): "delete",
        ("delete", "create"): "create",
        ("delete", "update"): "update",
        ("delete", "delete"): "delete",
    }
    return table.get((ancienne, nouvelle), nouvelle)


# Sentinelle : aucun filtre projet (tout voir). ``None`` explicite =
# entrées globales uniquement.
_TOUT_PROJET: Any = object()


def _filtre_projet(requete, project_id):
    """Restreint une requête sur la file au projet courant.

    ``project_id`` = id du projet de la session ; les entrées globales
    (``project_id IS NULL`` : plantes, entrées antérieures au
    cloisonnement) restent visibles partout.
    """
    if project_id is _TOUT_PROJET:
        return requete
    return requete.filter(
        (FileSynchronisation.project_id == project_id)
        | (FileSynchronisation.project_id.is_(None))
    )


def lister_file(
    session: Session,
    statut: str | None = None,
    project_id: int | None = _TOUT_PROJET,  # type: ignore[assignment]
) -> list[FileSynchronisation]:
    """Liste la file (plus récent d'abord), éventuellement filtrée par
    statut et/ou cloisonnée par projet (P0)."""
    requete = session.query(FileSynchronisation).order_by(
        FileSynchronisation.id.desc()
    )
    if statut:
        requete = requete.filter(FileSynchronisation.statut == statut)
    requete = _filtre_projet(requete, project_id)
    return list(requete.all())


def statut_entite(session: Session, entity_type: str, entity_id: int) -> str:
    """« locale » si une entrée non envoyée existe, sinon « synchronisee ».

    Sert au badge visuel « données locales » vs « données
    synchronisées » (aucune colonne d'état à maintenir : la file fait
    foi).
    """
    en_cours = (
        session.query(FileSynchronisation.id)
        .filter(
            FileSynchronisation.entity_type == entity_type,
            FileSynchronisation.entity_id == entity_id,
            FileSynchronisation.statut.in_(
                [StatutSync.EN_ATTENTE.value, StatutSync.ECHOUE.value,
                 StatutSync.CONFLIT.value]
            ),
        )
        .first()
    )
    return "locale" if en_cours else "synchronisee"


def _distant_a_change(ligne: FileSynchronisation, distant: dict | None) -> bool:
    """Le distant a-t-il changé depuis ``base_version`` ?"""
    if distant is None:
        return False
    distant_maj = _parse_iso(distant.get("updated_at"))
    base = _parse_iso(ligne.base_version)
    if distant_maj is None or base is None:
        # Sans horodatage fiable on ne peut pas prouver l'absence de
        # changement : prudence → conflit si les contenus diffèrent.
        return not _contenus_egaux(
            ligne.payload, distant.get("payload", {})
        )
    return distant_maj > base and not _contenus_egaux(
        ligne.payload, distant.get("payload", {})
    )


def executer_synchronisation(
    session: Session, depot: DepotDistant, auteur: str = "systeme",
    project_id: int | None = _TOUT_PROJET,  # type: ignore[assignment]
) -> dict:
    """Rejoue la file contre le dépôt distant (flush, sans commit).

    Retourne ``{"traitees", "envoyees", "conflits", "echoues"}``.
    Toute erreur d'accès au dépôt → statut ``echoue`` + message
    (rejouable via ``relancer_file``). Tout changement distant
    détecté → statut ``conflit`` (résolution explicite requise).

    Cloisonné par projet (P0) : seules les entrées du projet courant
    (ou globales, ``project_id`` NULL) sont rejouées ; un projet ne
    synchronise jamais la file d'un autre projet.
    """
    stats = {"traitees": 0, "envoyees": 0, "conflits": 0, "echoues": 0}
    lignes = _filtre_projet(
        session.query(FileSynchronisation)
        .filter(
            FileSynchronisation.statut.in_(
                [StatutSync.EN_ATTENTE.value, StatutSync.ECHOUE.value]
            )
        )
        .order_by(FileSynchronisation.id),
        project_id,
    ).all()
    for ligne in lignes:
        stats["traitees"] += 1
        ligne.tentative += 1
        ligne.erreur = None
        try:
            distant = depot.lire(ligne.entity_type, ligne.entity_id)
            maintenant = utcnow().isoformat()
            if ligne.operation == OperationSync.CREATE.value:
                if distant is not None and not _contenus_egaux(
                    ligne.payload, distant.get("payload", {})
                ):
                    _marquer_conflit(ligne, distant)
                    stats["conflits"] += 1
                else:
                    depot.ecrire(ligne.entity_type, ligne.entity_id,
                                 ligne.payload, maintenant)
                    ligne.statut = StatutSync.ENVOYE.value
                    stats["envoyees"] += 1
            elif ligne.operation == OperationSync.DELETE.value:
                if distant is None:
                    # Déjà absent du distant : idempotent, rien à faire.
                    ligne.statut = StatutSync.ENVOYE.value
                    stats["envoyees"] += 1
                elif _distant_a_change(ligne, distant):
                    _marquer_conflit(ligne, distant)
                    stats["conflits"] += 1
                else:
                    depot.supprimer(ligne.entity_type, ligne.entity_id)
                    ligne.statut = StatutSync.ENVOYE.value
                    stats["envoyees"] += 1
            else:  # update
                if distant is None:
                    # Le distant ne connaît pas l'entité : soit elle a été
                    # supprimée là-bas → conflit explicite, soit elle n'y
                    # a jamais été → on la crée (pas de perte silencieuse).
                    if ligne.base_version is not None:
                        _marquer_conflit(
                            ligne, {"payload": {"__supprime__": True},
                                    "updated_at": None})
                        stats["conflits"] += 1
                    else:
                        depot.ecrire(ligne.entity_type, ligne.entity_id,
                                     ligne.payload, maintenant)
                        ligne.statut = StatutSync.ENVOYE.value
                        stats["envoyees"] += 1
                elif _distant_a_change(ligne, distant):
                    _marquer_conflit(ligne, distant)
                    stats["conflits"] += 1
                else:
                    depot.ecrire(ligne.entity_type, ligne.entity_id,
                                 ligne.payload, maintenant)
                    ligne.statut = StatutSync.ENVOYE.value
                    stats["envoyees"] += 1
        except Exception as exc:  # accès dépôt : rejouable, jamais bloquant
            ligne.statut = StatutSync.ECHOUE.value
            ligne.erreur = str(exc)[:500]
            stats["echoues"] += 1
        session.flush()
    return stats


def _marquer_conflit(ligne: FileSynchronisation, distant: dict) -> None:
    """Passe une entrée en conflit (aucune donnée n'est écrasée)."""
    ligne.statut = StatutSync.CONFLIT.value
    ligne.remote_payload = _json_safe(distant.get("payload") or {})
    ligne.erreur = (
        "Le dépôt distant a été modifié après votre saisie locale "
        "(ou l'entité y a été supprimée). Choisissez explicitement : "
        "garder la version locale, adopter la version distante, ou "
        "fusionner manuellement. Rien n'a été écrasé."
    )


def relancer_file(
    session: Session,
    ids: list[int] | None = None,
    project_id: int | None = _TOUT_PROJET,  # type: ignore[assignment]
) -> int:
    """Remet les entrées ``echoue`` (ou ``ids``) en ``en_attente``.

    Cloisonné par projet (P0) : on ne relance que sa propre file.
    """
    requete = session.query(FileSynchronisation).filter(
        FileSynchronisation.statut == StatutSync.ECHOUE.value
    )
    if ids:
        requete = requete.filter(FileSynchronisation.id.in_(ids))
    requete = _filtre_projet(requete, project_id)
    compte = 0
    for ligne in requete.all():
        ligne.statut = StatutSync.EN_ATTENTE.value
        ligne.erreur = None
        compte += 1
    session.flush()
    return compte


def vider_file(
    session: Session,
    project_id: int | None = _TOUT_PROJET,  # type: ignore[assignment]
) -> int:
    """Supprime les entrées ``envoye`` (historique de file allégé).

    Seules les entrées déjà synchronisées sont supprimées ; les
    ``en_attente`` / ``echoue`` / ``conflit`` sont conservées.
    Cloisonné par projet (P0).
    """
    requete = session.query(FileSynchronisation).filter(
        FileSynchronisation.statut == StatutSync.ENVOYE.value
    )
    requete = _filtre_projet(requete, project_id)
    compte = requete.delete(synchronize_session=False)
    session.flush()
    return int(compte)


# ---------------------------------------------------------------------------
# Résolution explicite des conflits (journalisée)
# ---------------------------------------------------------------------------

def comparer_versions(local: dict, distant: dict) -> list[dict]:
    """Compare champ par champ pour l'affichage LOCAL vs DISTANT."""
    champs = sorted(set(local) | set(distant))
    lignes = []
    for champ in champs:
        if champ in CLES_HORODATAGE:
            continue
        v_local = local.get(champ)
        v_distant = distant.get(champ)
        lignes.append({
            "champ": champ,
            "local": v_local,
            "distant": v_distant,
            "different": v_local != v_distant,
        })
    return lignes


def resoudre_conflit(
    session: Session,
    depot: DepotDistant,
    file_id: int,
    choix: str,
    auteur: str = "",
    fusion: dict | None = None,
    commentaire: str | None = None,
) -> ResolutionConflit:
    """Résout explicitement un conflit (flush, sans commit).

    - ``local`` : la version locale est poussée au distant (force) ;
    - ``distant`` : la version distante est adoptée localement ;
    - ``fusion`` : ``fusion`` (dict fourni par l'utilisateur) est écrit
      des deux côtés.

    Chaque résolution est journalisée (``resolutions_conflit``) avec
    anciennes et nouvelles valeurs. Lève ``ValueError`` si l'entrée
    n'est pas en conflit ou si le choix est invalide.
    """
    ligne = session.get(FileSynchronisation, file_id)
    if ligne is None:
        raise ValueError(f"Entrée de file introuvable (#{file_id}).")
    if ligne.statut != StatutSync.CONFLIT.value:
        raise ValueError(
            f"L'entrée #{file_id} n'est pas en conflit "
            f"(statut : {ligne.statut})."
        )
    if choix not in {c.value for c in ChoixResolution}:
        raise ValueError(f"Choix de résolution invalide : {choix!r}.")
    gestionnaire = GESTIONNAIRES.get(ligne.entity_type)
    if gestionnaire is None:
        raise ValueError(
            f"Type d'entité non synchronisable : {ligne.entity_type!r}."
        )
    distant = ligne.remote_payload or {}
    maintenant = utcnow()

    # Les écritures locales ci-dessous appliquent une DÉCISION de
    # synchronisation : elles ne doivent pas être remises en file comme
    # de « nouvelles modifications locales ».
    from webintel.offline.auto_file import file_auto_suspendue

    with file_auto_suspendue():
        if choix == ChoixResolution.LOCAL.value:
            depot.ecrire(ligne.entity_type, ligne.entity_id, ligne.payload,
                         maintenant.isoformat())
            anciennes = {"distant_avant": distant}
            nouvelles = {"local_applique": ligne.payload}
        elif choix == ChoixResolution.DISTANT.value:
            if distant.get("__supprime__"):
                gestionnaire.supprimer(session, ligne.entity_id)
                nouvelles = {"entite_supprimee_localement": True}
            elif gestionnaire.lire(session, ligne.entity_id) is None:
                # Cas « delete local vs update distant » : l'entité n'existe
                # plus localement — on la recrée à l'identique (même id)
                # plutôt que d'échouer silencieusement.
                _recreer_avec_id(session, ligne.entity_type, ligne.entity_id,
                                 distant)
                nouvelles = {"distant_applique": distant,
                             "entite_recreee_localement": True}
            else:
                gestionnaire.ecrire(session, ligne.entity_id, distant)
                nouvelles = {"distant_applique": distant}
            anciennes = {"local_avant": ligne.payload}
        else:  # fusion manuelle
            if not fusion:
                raise ValueError(
                    "La fusion manuelle exige les valeurs fusionnées "
                    "(paramètre ``fusion``)."
                )
            fusion_safe = _json_safe(dict(fusion))
            gestionnaire.ecrire(session, ligne.entity_id, fusion_safe)
            depot.ecrire(ligne.entity_type, ligne.entity_id, fusion_safe,
                         maintenant.isoformat())
            anciennes = {"local_avant": ligne.payload,
                         "distant_avant": distant}
            nouvelles = {"fusion_appliquee": fusion_safe}

    journal = ResolutionConflit(
        file_id=ligne.id,
        entity_type=ligne.entity_type,
        entity_id=ligne.entity_id,
        auteur=auteur,
        choix=choix,
        anciennes_valeurs=_json_safe(anciennes),
        nouvelles_valeurs=_json_safe(nouvelles),
        commentaire=commentaire,
        project_id=ligne.project_id,  # cloisonnement hérité de la file
    )
    session.add(journal)
    ligne.statut = StatutSync.ENVOYE.value
    ligne.remote_payload = None
    ligne.erreur = None
    ligne.choix_resolution = choix
    ligne.auteur_resolution = auteur
    ligne.resolved_at = maintenant
    session.flush()
    return journal


# --- Mise en file automatique -------------------------------------------------
# Installée à l'import : toute modification d'une entité synchronisable via
# SQLAlchemy est mise en file (voir webintel/offline/auto_file.py).
from webintel.offline.auto_file import installer_auto_file as _installer

_installer()
del _installer
