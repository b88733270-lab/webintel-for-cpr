"""Mise en file automatique des modifications locales (offline-first).

Toute création / modification / suppression d'une entité synchronisable
(registre ``GESTIONNAIRES`` : collecte, plante, échantillon, étude,
observation, simulation, note…) effectuée via SQLAlchemy est
automatiquement mise en file de synchronisation — sans que chaque route
ou service ait à y penser. L'usage local reste donc synchronisable
même quand le code qui écrit n'a pas été adapté.

Implémentation : écouteurs ``before_flush`` (capture des cibles ; les
charges de suppression sont capturées ici car les attributs sont
expirés après le DELETE) + ``after_flush`` (mise en file effective).

Garanties :
- pas de doublons : ``mettre_en_file`` fusionne (coalescence) avec une
  éventuelle entrée déjà en attente ou en erreur pour la même entité ;
- pas de récursion : les lignes de file ne sont pas des entités
  suivies ;
- les écritures internes du moteur de synchronisation (résolution de
  conflit) et de la restauration de sauvegarde ne sont PAS remises en
  file (contexte ``file_auto_suspendue``) : adopter une version distante
  ou restaurer une archive n'est pas une « nouvelle modification
  locale ».
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import event
from sqlalchemy.orm import Session

_INSTALLE = False
_CLE_INFO = "webintel_auto_file"
_SUSPENSION = 0


def _construire_registre() -> dict[type, str]:
    from webintel.offline import synchronisation as sync
    from webintel.offline.models import CollecteTerrain

    candidats: dict[str, type] = {"collecte": CollecteTerrain}
    try:
        from webintel.science import lab as lab_mod
        from webintel.science import models as sc_mod

        candidats.update({
            "plante": sc_mod.Plant,
            "echantillon": sc_mod.Echantillon,
            "etude": sc_mod.Study,
            "observation": sc_mod.ResearchObservation,
            "simulation": sc_mod.Simulation,
            "note": lab_mod.LabNote,
        })
    except Exception:
        pass
    # Ne suivre que les types réellement synchronisables.
    return {
        modele: type_entite
        for type_entite, modele in candidats.items()
        if type_entite in sync.GESTIONNAIRES
    }


def types_suivis() -> tuple[str, ...]:
    """Types d'entités dont les modifications sont mises en file."""
    return tuple(sorted(_construire_registre().values()))


def _type_de(obj: object) -> str | None:
    registre = _construire_registre()
    for modele, type_entite in registre.items():
        if isinstance(obj, modele):
            return type_entite
    return None


@contextmanager
def file_auto_suspendue() -> Iterator[None]:
    """Suspend la mise en file automatique (moteur de synchronisation,
    restauration de sauvegarde). Imbriçable."""
    global _SUSPENSION
    _SUSPENSION += 1
    try:
        yield
    finally:
        _SUSPENSION -= 1


def installer_auto_file() -> None:
    """Installe les écouteurs SQLAlchemy (idempotent).

    Appelé à l'import de ``webintel.offline.synchronisation`` — actif
    donc partout (application comme tests).
    """
    global _INSTALLE
    if _INSTALLE:
        return
    event.listen(Session, "before_flush", _avant_flush)
    event.listen(Session, "after_flush", _apres_flush)
    _INSTALLE = True


def _avant_flush(session: Session, contexte, instances) -> None:
    if _SUSPENSION:
        return
    # Même sémantique que les appels explicites : la file reçoit les
    # modifications effectuées HORS CONNEXION (en ligne, l'écriture est
    # directe — cf. routes ``/collectes``).
    from webintel.offline import reseau

    if not reseau.est_hors_ligne():
        return
    marques: list[tuple[str, str, object, int | None, dict | None]] = []
    for obj in session.new:
        type_entite = _type_de(obj)
        if type_entite is not None:
            marques.append((type_entite, "create", obj, None, None))
    for obj in session.dirty:
        type_entite = _type_de(obj)
        if type_entite is not None and session.is_modified(
            obj, include_collections=False
        ):
            marques.append((type_entite, "update", obj, None, None))
    for obj in session.deleted:
        type_entite = _type_de(obj)
        if type_entite is not None:
            eid = obj.id  # capturé AVANT le DELETE
            marques.append((type_entite, "delete", obj, eid, {"id": eid}))
    if marques:
        session.info.setdefault(_CLE_INFO, []).extend(marques)


def _apres_flush(session: Session, contexte) -> None:
    from webintel.offline import synchronisation as sync

    marques = session.info.pop(_CLE_INFO, [])
    if _SUSPENSION:
        return
    for type_entite, operation, obj, eid_capture, payload_supp in marques:
        eid = eid_capture if eid_capture is not None else getattr(
            obj, "id", None)
        if eid is None:
            continue
        if operation == "delete":
            payload = payload_supp or {"id": eid}
        else:
            # Construit depuis l'objet en mémoire : aucune requête
            # supplémentaire pendant le flush.
            payload = sync._payload_depuis_objet(obj)
        sync.mettre_en_file(
            session, type_entite, eid, operation, payload,
            base_version=None,
            # flush() interdit dans after_flush : la ligne part au
            # prochain flush/commit.
            flusher=False,
        )
