"""Tests offline-first : dégradation gracieuse hors connexion.

- le drapeau hors-ligne (env ``WEBINTEL_HORS_LIGNE`` + bascule) ;
- les connecteurs externes lèvent ``ConnecteurIndisponible`` avec un
  message français explicite SANS tentative réseau (ni ``requete_json``
  ni ``tester_statut`` ne touchent le réseau) ;
- l'UI affiche le message au lieu de casser : page des connecteurs,
  import d'un connecteur → erreur explicite, usage local intact ;
- bandeau « Mode hors-ligne » global ;
- les calculs locaux (RDKit si présent, carte, rapports) restent
  utilisables hors connexion.
"""

from __future__ import annotations

import pytest

from webintel.offline import reseau
from webintel.offline.models import CollecteTerrain
from webintel.science.connecteurs.base import ConnecteurIndisponible
from webintel.science.connecteurs.plugins.pubchem import PubChemConnecteur
from webintel.science.connecteurs.plugins.pubmed import PubMedConnecteur


@pytest.fixture(autouse=True)
def _mode_reseau_propre():
    yield
    reseau.definir_mode_hors_ligne(None)


# ---------------------------------------------------------------------------
# Drapeau hors-ligne
# ---------------------------------------------------------------------------

def test_drapeau_env(monkeypatch):
    reseau.definir_mode_hors_ligne(None)
    monkeypatch.setenv("WEBINTEL_HORS_LIGNE", "1")
    assert reseau.est_hors_ligne() is True
    monkeypatch.setenv("WEBINTEL_HORS_LIGNE", "0")
    assert reseau.est_hors_ligne() is False


def test_bascule_prioritaire_sur_env(monkeypatch):
    monkeypatch.setenv("WEBINTEL_HORS_LIGNE", "0")
    reseau.definir_mode_hors_ligne(True)
    assert reseau.est_hors_ligne() is True
    reseau.definir_mode_hors_ligne(None)
    assert reseau.est_hors_ligne() is False


def test_message_francais_explicite():
    assert "hors-ligne" in reseau.MESSAGE_HORS_LIGNE.lower()
    assert "local" in reseau.MESSAGE_HORS_LIGNE.lower()


# ---------------------------------------------------------------------------
# Connecteurs : aucun accès réseau, message explicite
# ---------------------------------------------------------------------------

def test_requete_json_hors_ligne_sans_reseau(db_session):
    reseau.definir_mode_hors_ligne(True)
    connecteur = PubChemConnecteur()
    with pytest.raises(ConnecteurIndisponible) as exc_info:
        connecteur.requete_json(
            db_session,
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/eau/JSON",
        )
    assert "hors-ligne" in str(exc_info.value).lower()


def test_tester_statut_hors_ligne_message_fr(db_session):
    reseau.definir_mode_hors_ligne(True)
    for plugin in (PubChemConnecteur(), PubMedConnecteur()):
        ok, message = plugin.tester_statut()
        assert ok is False
        assert "hors-ligne" in message.lower()


def test_lever_si_hors_ligne_inactif_en_ligne():
    reseau.definir_mode_hors_ligne(False)
    reseau.lever_si_hors_ligne("test")  # ne lève pas


# ---------------------------------------------------------------------------
# UI : message explicite, usage local jamais bloqué
# ---------------------------------------------------------------------------

def test_ui_import_connecteur_hors_ligne_message(client):
    reseau.definir_mode_hors_ligne(True)
    r = client.post("/connecteurs/pubchem/importer")
    assert r.status_code == 200
    texte = r.text.lower()
    assert "hors-ligne" in texte
    # La page reste utilisable (pas de 500, pas de page blanche).
    assert "connecteur" in texte


def test_ui_bandeau_hors_ligne_global(client):
    reseau.definir_mode_hors_ligne(True)
    for page in ("/", "/collectes", "/carte", "/synchronisation"):
        html = client.get(page).text
        assert "Mode hors-ligne actif" in html, page


def test_ui_pas_de_bandeau_en_ligne(client):
    reseau.definir_mode_hors_ligne(False)
    assert "Mode hors-ligne actif" not in client.get("/collectes").text


def test_usage_local_intact_hors_ligne(client, db_session):
    """Hors connexion : saisie, liste, carte, synchronisation (file)."""
    from webintel.database import models as core_models

    reseau.definir_mode_hors_ligne(True)
    if db_session.query(core_models.Project).count() == 0:
        db_session.add(core_models.Project(name="Défaut"))
        db_session.commit()
    r = client.post("/collectes/nouvelle",
                    data={"localite": "Ouaga",
                          "niveau_confidentialite": "public"}, files=[])
    assert r.status_code == 200
    assert client.get("/collectes").status_code == 200
    assert client.get("/carte").status_code == 200
    assert client.get("/synchronisation").status_code == 200
    collecte = db_session.query(CollecteTerrain).one()
    assert collecte.localite == "Ouaga"


def test_distinction_donnees_locales_vs_synchronisees(client, db_session):
    from webintel.database import models as core_models
    from webintel.offline.synchronisation import (
        DepotDistantMemoire,
        executer_synchronisation,
        mettre_en_file,
        statut_entite,
    )

    if db_session.query(core_models.Project).count() == 0:
        db_session.add(core_models.Project(name="Défaut"))
        db_session.commit()
    projet = db_session.query(core_models.Project).first()
    c = CollecteTerrain(localite="Ziniaré", auteur="u", project_id=projet.id)
    db_session.add(c)
    db_session.flush()
    # Modification hors connexion → mise en file → badge « locale ».
    from webintel.offline import synchronisation as sync_mod

    mettre_en_file(db_session, "collecte", c.id, "create",
                   sync_mod._payload_depuis_objet(c))
    db_session.commit()
    assert statut_entite(db_session, "collecte", c.id) == "locale"
    assert "Donnée locale" in client.get("/collectes").text
    # Après synchronisation (dépôt simulé) : badge « synchronisée ».
    executer_synchronisation(db_session, DepotDistantMemoire())
    db_session.commit()
    assert statut_entite(db_session, "collecte", c.id) == "synchronisee"
    assert "Donnée synchronisée" in client.get("/collectes").text
