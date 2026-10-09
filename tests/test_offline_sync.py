"""Tests offline-first : file de synchronisation, conflits, résolutions.

Scénarios couverts :
- création hors connexion → mise en file (``en_attente``) ;
- synchronisation sans conflit → ``envoye`` (create / update / delete) ;
- modification distante entre-temps → ``conflit`` + ``remote_payload``
  conservé, RIEN n'est écrasé (ni local ni distant) ;
- résolution explicite ``local`` / ``distant`` / ``fusion`` →
  journalisée (qui, quand, ancienne → nouvelle valeur) ;
- relance des échecs, vidage des envoyés ;
- dépôt « dossier » (échange via clé USB) ;
- page « Synchronisation » : file visible, actions, page de conflit
  LOCAL vs DISTANT, résolution via l'UI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from webintel.database import models as core_models
from webintel.offline import reseau, synchronisation as sync
from webintel.offline.models import (
    ChoixResolution,
    CollecteTerrain,
    FileSynchronisation,
    OperationSync,
    ResolutionConflit,
    StatutSync,
)
from webintel.offline.synchronisation import (
    DepotDistantDossier,
    DepotDistantMemoire,
    comparer_versions,
    executer_synchronisation,
    lister_file,
    mettre_en_file,
    relancer_file,
    resoudre_conflit,
    statut_entite,
    vider_file,
)
from webintel.science.models import Plant


@pytest.fixture(autouse=True)
def _mode_reseau_propre():
    yield
    reseau.definir_mode_hors_ligne(None)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _collecte(db_session, **kwargs):
    kwargs.setdefault("localite", "Ouagadougou")
    kwargs.setdefault("auteur", "adama")
    c = CollecteTerrain(**kwargs)
    db_session.add(c)
    db_session.flush()
    return c


# ---------------------------------------------------------------------------
# Mise en file
# ---------------------------------------------------------------------------

def test_mise_en_file_hors_connexion(db_session):
    c = _collecte(db_session)
    ligne = mettre_en_file(
        db_session, "collecte", c.id, OperationSync.CREATE.value,
        sync._payload_depuis_objet(c), auteur="adama",
    )
    db_session.commit()
    assert ligne.statut == StatutSync.EN_ATTENTE.value
    assert ligne.entity_type == "collecte"
    assert ligne.auteur == "adama"
    assert statut_entite(db_session, "collecte", c.id) == "locale"


def test_mise_en_file_type_inconnu_refuse(db_session):
    c = _collecte(db_session)
    with pytest.raises(ValueError):
        mettre_en_file(db_session, "xwing", c.id, "create", {}, auteur="a")


def test_mise_en_file_operation_inconnue_refuse(db_session):
    c = _collecte(db_session)
    with pytest.raises(ValueError):
        mettre_en_file(db_session, "collecte", c.id, "teleporter", {})


# ---------------------------------------------------------------------------
# Synchronisation sans conflit
# ---------------------------------------------------------------------------

def test_sync_create_sans_conflit(db_session):
    c = _collecte(db_session, localite="Bobo-Dioulasso")
    mettre_en_file(db_session, "collecte", c.id, OperationSync.CREATE.value,
                   sync._payload_depuis_objet(c), auteur="adama")
    depot = DepotDistantMemoire()
    stats = executer_synchronisation(db_session, depot)
    db_session.commit()
    assert stats == {"traitees": 1, "envoyees": 1, "conflits": 0,
                     "echoues": 0}
    distant = depot.lire("collecte", c.id)
    assert distant["payload"]["localite"] == "Bobo-Dioulasso"
    assert statut_entite(db_session, "collecte", c.id) == "synchronisee"


def test_sync_update_sans_changement_distant(db_session):
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    c = _collecte(db_session)
    c.updated_at = t0
    db_session.flush()
    depot = DepotDistantMemoire()
    depot.ecrire("collecte", c.id, {"id": c.id, "localite": "Kaya"},
                 _iso(t0))
    c.localite = "Kaya (mise à jour)"
    db_session.flush()
    mettre_en_file(db_session, "collecte", c.id, OperationSync.UPDATE.value,
                   sync._payload_depuis_objet(c), auteur="adama",
                   base_version=_iso(t0))
    stats = executer_synchronisation(db_session, depot)
    assert stats["envoyees"] == 1 and stats["conflits"] == 0
    assert depot.lire("collecte", c.id)["payload"]["localite"] == \
        "Kaya (mise à jour)"


def test_sync_delete_distant_deja_absent_idempotent(db_session):
    c = _collecte(db_session)
    mettre_en_file(db_session, "collecte", c.id, OperationSync.DELETE.value,
                   {"id": c.id}, auteur="adama",
                   base_version=_iso(datetime.now(timezone.utc)))
    stats = executer_synchronisation(db_session, DepotDistantMemoire())
    assert stats["envoyees"] == 1


def test_sync_echec_acces_depot_rejouable(db_session):
    class DepotKO:
        def lire(self, *a):
            raise ConnectionError("clé USB retirée")

    c = _collecte(db_session)
    mettre_en_file(db_session, "collecte", c.id, OperationSync.CREATE.value,
                   sync._payload_depuis_objet(c), auteur="adama")
    stats = executer_synchronisation(db_session, DepotKO())
    assert stats["echoues"] == 1
    ligne = lister_file(db_session)[0]
    assert ligne.statut == StatutSync.ECHOUE.value
    assert "USB" in (ligne.erreur or "")
    assert relancer_file(db_session) == 1
    assert lister_file(db_session)[0].statut == StatutSync.EN_ATTENTE.value


def test_vider_file_ne_garde_que_les_non_envoyes(db_session):
    c1 = _collecte(db_session, localite="L1")
    c2 = _collecte(db_session, localite="L2")
    mettre_en_file(db_session, "collecte", c1.id, "create",
                   sync._payload_depuis_objet(c1))
    mettre_en_file(db_session, "collecte", c2.id, "create",
                   sync._payload_depuis_objet(c2))
    executer_synchronisation(db_session, DepotDistantMemoire())
    # c2 : on simule un conflit restant
    ligne2 = db_session.query(FileSynchronisation).filter(
        FileSynchronisation.entity_id == c2.id).one()
    ligne2.statut = StatutSync.CONFLIT.value
    db_session.flush()
    assert vider_file(db_session) == 1
    restants = lister_file(db_session)
    assert len(restants) == 1 and restants[0].statut == "conflit"


# ---------------------------------------------------------------------------
# Conflits : détection explicite, jamais d'écrasement silencieux
# ---------------------------------------------------------------------------

def _conflit_update(db_session):
    """Prépare un conflit update/update : distant modifié après la file."""
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    t1 = datetime.now(timezone.utc) - timedelta(hours=1)
    c = _collecte(db_session, localite="Ouaga", notes="note locale")
    c.updated_at = t0
    db_session.flush()
    depot = DepotDistantMemoire()
    # Le distant a été modifié par quelqu'un d'autre APRES t0.
    depot.ecrire("collecte", c.id,
                 {"id": c.id, "localite": "Ouaga",
                  "notes": "note DISTANTE modifiée"},
                 _iso(t1))
    c.notes = "note LOCALE modifiée"
    db_session.flush()
    mettre_en_file(db_session, "collecte", c.id, OperationSync.UPDATE.value,
                   sync._payload_depuis_objet(c), auteur="adama",
                   base_version=_iso(t0))
    return c, depot


def test_conflit_detecte_et_rien_n_est_ecrase(db_session):
    c, depot = _conflit_update(db_session)
    distant_avant = depot.lire("collecte", c.id)["payload"]["notes"]
    stats = executer_synchronisation(db_session, depot)
    assert stats["conflits"] == 1 and stats["envoyees"] == 0
    ligne = lister_file(db_session, "conflit")[0]
    assert ligne.remote_payload["notes"] == "note DISTANTE modifiée"
    assert "aucun" in (ligne.erreur or "").lower() or "écrasé" in (
        ligne.erreur or "")
    # Rien n'a bougé ni en local ni au distant :
    db_session.refresh(c)
    assert c.notes == "note LOCALE modifiée"
    assert depot.lire("collecte", c.id)["payload"]["notes"] == distant_avant


def test_conflit_create_si_distant_existe_deja(db_session):
    c = _collecte(db_session, localite="Koudougou")
    depot = DepotDistantMemoire()
    depot.ecrire("collecte", c.id, {"id": c.id, "localite": "AUTRE"},
                 _iso(datetime.now(timezone.utc)))
    mettre_en_file(db_session, "collecte", c.id, "create",
                   sync._payload_depuis_objet(c))
    stats = executer_synchronisation(db_session, depot)
    assert stats["conflits"] == 1


def test_conflit_delete_vs_update_distant(db_session):
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    c = _collecte(db_session)
    c.updated_at = t0
    db_session.flush()
    depot = DepotDistantMemoire()
    depot.ecrire("collecte", c.id, {"id": c.id, "localite": "modifié loin"},
                 _iso(datetime.now(timezone.utc)))
    mettre_en_file(db_session, "collecte", c.id, "delete", {"id": c.id},
                   base_version=_iso(t0))
    stats = executer_synchronisation(db_session, depot)
    assert stats["conflits"] == 1
    # Le distant n'a PAS été supprimé :
    assert depot.lire("collecte", c.id) is not None


def test_comparer_versions_champ_par_champ():
    lignes = comparer_versions({"a": 1, "b": 2, "updated_at": "x"},
                               {"a": 1, "b": 3, "updated_at": "y"})
    par_champ = {l["champ"]: l for l in lignes}
    assert "updated_at" not in par_champ  # horodatages exclus
    assert par_champ["a"]["different"] is False
    assert par_champ["b"]["different"] is True
    assert par_champ["b"]["local"] == 2 and par_champ["b"]["distant"] == 3


# ---------------------------------------------------------------------------
# Résolutions explicites (journalisées)
# ---------------------------------------------------------------------------

def test_resolution_choix_local(db_session):
    c, depot = _conflit_update(db_session)
    executer_synchronisation(db_session, depot)
    ligne = lister_file(db_session, "conflit")[0]
    journal = resoudre_conflit(db_session, depot, ligne.id,
                               ChoixResolution.LOCAL.value, auteur="adama",
                               commentaire="ma version fait foi")
    db_session.commit()
    assert ligne.statut == StatutSync.ENVOYE.value
    assert ligne.choix_resolution == "local"
    assert depot.lire("collecte", c.id)["payload"]["notes"] == \
        "note LOCALE modifiée"
    assert journal.auteur == "adama"
    assert journal.anciennes_valeurs["distant_avant"]["notes"] == \
        "note DISTANTE modifiée"
    assert journal.nouvelles_valeurs["local_applique"]["notes"] == \
        "note LOCALE modifiée"
    assert journal.commentaire == "ma version fait foi"


def test_resolution_choix_distant(db_session):
    c, depot = _conflit_update(db_session)
    executer_synchronisation(db_session, depot)
    ligne = lister_file(db_session, "conflit")[0]
    resoudre_conflit(db_session, depot, ligne.id,
                     ChoixResolution.DISTANT.value, auteur="adama")
    db_session.commit()
    db_session.refresh(c)
    assert c.notes == "note DISTANTE modifiée"
    assert ligne.statut == StatutSync.ENVOYE.value


def test_resolution_fusion_manuelle(db_session):
    c, depot = _conflit_update(db_session)
    executer_synchronisation(db_session, depot)
    ligne = lister_file(db_session, "conflit")[0]
    fusion = dict(ligne.payload)
    fusion["notes"] = "note FUSIONNÉE à la main"
    journal = resoudre_conflit(db_session, depot, ligne.id,
                               ChoixResolution.FUSION.value, auteur="adama",
                               fusion=fusion)
    db_session.commit()
    db_session.refresh(c)
    assert c.notes == "note FUSIONNÉE à la main"
    assert depot.lire("collecte", c.id)["payload"]["notes"] == \
        "note FUSIONNÉE à la main"
    assert "fusion_appliquee" in journal.nouvelles_valeurs


def test_resolution_fusion_sans_valeurs_refusee(db_session):
    c, depot = _conflit_update(db_session)
    executer_synchronisation(db_session, depot)
    ligne = lister_file(db_session, "conflit")[0]
    with pytest.raises(ValueError):
        resoudre_conflit(db_session, depot, ligne.id, "fusion", auteur="a")


def test_resolution_choix_invalide_refuse(db_session):
    c, depot = _conflit_update(db_session)
    executer_synchronisation(db_session, depot)
    ligne = lister_file(db_session, "conflit")[0]
    with pytest.raises(ValueError):
        resoudre_conflit(db_session, depot, ligne.id, "pile_ou_face")


def test_resolution_hors_conflit_refusee(db_session):
    c = _collecte(db_session)
    ligne = mettre_en_file(db_session, "collecte", c.id, "create",
                           sync._payload_depuis_objet(c))
    with pytest.raises(ValueError):
        resoudre_conflit(db_session, DepotDistantMemoire(), ligne.id,
                         "local")


def test_journal_resolutions_append_only(db_session):
    c, depot = _conflit_update(db_session)
    executer_synchronisation(db_session, depot)
    ligne = lister_file(db_session, "conflit")[0]
    resoudre_conflit(db_session, depot, ligne.id, "local", auteur="adama")
    db_session.commit()
    journaux = db_session.query(ResolutionConflit).all()
    assert len(journaux) == 1
    j = journaux[0]
    assert j.file_id == ligne.id and j.choix == "local"
    assert j.resolved_at is not None


# ---------------------------------------------------------------------------
# Dépôt « dossier » (clé USB)
# ---------------------------------------------------------------------------

def test_depot_dossier_round_trip(db_session, tmp_path):
    depot = DepotDistantDossier(str(tmp_path / "serveur"))
    c = _collecte(db_session, localite="Dori")
    mettre_en_file(db_session, "collecte", c.id, "create",
                   sync._payload_depuis_objet(c))
    stats = executer_synchronisation(db_session, depot)
    assert stats["envoyees"] == 1
    # Le fichier existe sur disque, relisible par un autre dépôt :
    depot2 = DepotDistantDossier(str(tmp_path / "serveur"))
    assert depot2.lire("collecte", c.id)["payload"]["localite"] == "Dori"
    depot2.supprimer("collecte", c.id)
    assert depot.lire("collecte", c.id) is None


# ---------------------------------------------------------------------------
# Entité « plante » synchronisable
# ---------------------------------------------------------------------------

def test_sync_plante_conflit_puis_resolution_distante(db_session):
    p = Plant(scientific_name="Moringa oleifera")
    db_session.add(p)
    db_session.flush()
    t0 = datetime.now(timezone.utc) - timedelta(hours=3)
    depot = DepotDistantMemoire()
    depot.ecrire("plante", p.id, {"id": p.id,
                                 "scientific_name": "Moringa oleifera",
                                 "family": "Moringaceae (distant)"},
                 _iso(datetime.now(timezone.utc) - timedelta(hours=1)))
    p.family = "Moringaceae (local)"
    db_session.flush()
    mettre_en_file(db_session, "plante", p.id, "update",
                   sync._payload_depuis_objet(p), base_version=_iso(t0))
    stats = executer_synchronisation(db_session, depot)
    assert stats["conflits"] == 1
    ligne = lister_file(db_session, "conflit")[0]
    resoudre_conflit(db_session, depot, ligne.id, "distant", auteur="adama")
    db_session.commit()
    db_session.refresh(p)
    assert p.family == "Moringaceae (distant)"


# ---------------------------------------------------------------------------
# UI : page Synchronisation + résolution via navigateur
# ---------------------------------------------------------------------------

def test_ui_page_synchronisation(client, db_session):
    c = _collecte(db_session)
    mettre_en_file(db_session, "collecte", c.id, "create",
                   sync._payload_depuis_objet(c), auteur="adama")
    db_session.commit()
    r = client.get("/synchronisation")
    assert r.status_code == 200
    assert "File des modifications" in r.text
    assert "En attente" in r.text
    assert "Sauvegarde" in r.text


def test_ui_action_synchroniser(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("WEBINTEL_DEPOT_SYNC", str(tmp_path / "depot"))
    c = _collecte(db_session)
    mettre_en_file(db_session, "collecte", c.id, "create",
                   sync._payload_depuis_objet(c))
    db_session.commit()
    r = client.post("/synchronisation/action", data={"action": "synchroniser"})
    assert r.status_code == 200
    assert "Synchronisation terminée" in r.text
    assert lister_file(db_session, "envoye")


def test_ui_bascule_hors_ligne(client):
    r = client.post("/synchronisation/action",
                    data={"action": "hors_ligne_on"})
    assert r.status_code == 200
    assert reseau.est_hors_ligne() is True
    assert "Mode hors-ligne actif" in client.get("/synchronisation").text
    client.post("/synchronisation/action", data={"action": "hors_ligne_off"})
    assert reseau.est_hors_ligne() is False


def test_ui_page_conflit_local_vs_distant(client, db_session):
    c, depot = _conflit_update(db_session)
    executer_synchronisation(db_session, depot)
    db_session.commit()
    ligne = lister_file(db_session, "conflit")[0]
    r = client.get(f"/synchronisation/conflits/{ligne.id}")
    assert r.status_code == 200
    assert "LOCAL" in r.text and "DISTANT" in r.text
    assert "note LOCALE modifiée" in r.text
    assert "note DISTANTE modifiée" in r.text
    assert "journalis" in r.text.lower()


def test_ui_resolution_conflit_via_formulaire(client, db_session, tmp_path,
                                             monkeypatch):
    monkeypatch.setenv("WEBINTEL_DEPOT_SYNC", str(tmp_path / "depot"))
    c, _ = _conflit_update(db_session)
    # Le dépôt de l'UI est le dossier configuré : on y rejoue le conflit.
    from webintel.ui import offline_routes

    depot_ui = offline_routes._depot()
    t1 = datetime.now(timezone.utc) - timedelta(minutes=30)
    depot_ui.ecrire("collecte", c.id,
                    {"id": c.id, "localite": "Ouaga",
                     "notes": "note DISTANTE modifiée"}, _iso(t1))
    stats = executer_synchronisation(db_session, depot_ui)
    assert stats["conflits"] == 1
    db_session.commit()
    ligne = lister_file(db_session, "conflit")[0]
    r = client.post(
        f"/synchronisation/conflits/{ligne.id}/resoudre",
        data={"choix": "distant", "commentaire": "le distant fait foi"},
    )
    assert r.status_code == 200
    db_session.refresh(c)
    assert c.notes == "note DISTANTE modifiée"
    j = db_session.query(ResolutionConflit).one()
    assert j.commentaire == "le distant fait foi"
