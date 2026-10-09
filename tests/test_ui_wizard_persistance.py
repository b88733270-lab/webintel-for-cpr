"""Tests v2.2 — persistance des sessions du wizard d'étude (P0).

Couvre : la table ``wizard_sessions`` (création, projet/owner, TTL),
la survie d'une étude en cours après « redémarrage » (nouvelle app sur
la même base), la purge TTL, l'isolation inter-projets (403), la reprise
d'une session inachevée et le rattachement projet à la finalisation.

Zéro JavaScript, zéro réseau : tout passe par le TestClient.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import webintel.config.settings as _settings_mod
import webintel.main
from webintel.core.utils import sha256_hex
from webintel.database import models
from webintel.database.base import SessionLocal
from webintel.database.models import (
    DUREE_WIZARD_SESSION_JOURS,
    ApiKey,
    Project,
    WizardSession,
)
from webintel.ui.chercheur_etudes_routes import (
    _charger_session,
    purger_sessions_expirees,
)

_SERVEUR_REEL = os.environ.get("REQUIRE_API_KEY") == "true"

CAFEINE = "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"
THEOPHYLLINE = "CN1C=NC2=C1C(=O)NC(=O)N2C"


@pytest.fixture()
def key_mode():
    """Bascule REQUIRE_API_KEY à l'exécution (motif test_project_isolation).

    Permet de tester l'isolation serveur (projet/owner obligatoires)
    quel que soit le mode réel de la suite.
    """
    settings = _settings_mod.Settings()
    settings.REQUIRE_API_KEY = True
    _settings_mod._settings = settings
    yield
    _settings_mod._settings = None


def _session_id(location: str) -> str:
    m = re.search(r"session=([0-9a-f]{32})", location or "")
    assert m, f"pas de session dans la redirection : {location!r}"
    return m.group(1)


def _compose_api(client, nom: str, smiles: str) -> int:
    r = client.post("/api/science/compounds",
                    json={"name": nom, "smiles": smiles})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _etapes_1_2(client):
    """Joue les étapes 1→2 (2 molécules cochées) ; retourne (sid, ids)."""
    a = _compose_api(client, "Caféine", CAFEINE)
    b = _compose_api(client, "Théophylline", THEOPHYLLINE)
    r = client.post("/etudes/etape1", data={"type_objet": "molecules"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    sid = _session_id(r.headers["location"])
    r = client.post(f"/etudes/etape2?session={sid}",
                    data={"action": "continuer",
                          "compound_ids": [str(a), str(b)]},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    return sid, (a, b)


def _client_ui_sur(app, plain_key: str | None):
    """Nouveau client HTTP « navigateur » sur `app` (cookies vierges).

    En mode serveur : en-tête clé + login (cookie de session UI).
    """
    headers, base_url = {}, "http://testserver"
    if _SERVEUR_REEL:
        headers["X-API-Key"] = plain_key
        base_url = "https://testserver"
    client = TestClient(app, headers=headers, base_url=base_url)
    if _SERVEUR_REEL:
        r = client.post("/login", data={"api_key": plain_key})
        assert r.status_code in (200, 303), (
            f"login impossible sur l'app redémarrée : {r.status_code}")
    return client


def _projet_avec_cle(db_session, nom: str):
    projet = Project(name=nom)
    db_session.add(projet)
    db_session.flush()
    plain = secrets.token_urlsafe(32)
    db_session.add(ApiKey(
        project_id=projet.id, name=f"clé-{nom}",
        key_hash=sha256_hex(plain), prefix=plain[:8],
        quotas={}, revoked=False))
    db_session.commit()
    return projet, plain


# ---------------------------------------------------------------------------
# Persistance : la session existe en base, rattachée au projet
# ---------------------------------------------------------------------------

def test_session_persiste_en_base_rattachee_au_projet(client, db_session):
    sid, _ids = _etapes_1_2(client)
    ligne = db_session.get(WizardSession, sid)
    assert ligne is not None, "la session du wizard doit exister en base"
    assert ligne.project_id is not None
    assert ligne.status == "en_cours"
    assert ligne.version == 1
    # TTL ≈ 30 jours glissants.
    restant = (ligne.expires_at.replace(tzinfo=timezone.utc)
               - datetime.now(timezone.utc))
    assert timedelta(days=DUREE_WIZARD_SESSION_JOURS - 1) < restant <= timedelta(
        days=DUREE_WIZARD_SESSION_JOURS + 1)
    # Payload rejouable : les molécules choisies y sont.
    noms = [m["nom"] for m in (ligne.payload.get("molecules") or [])]
    assert "Caféine" in noms and "Théophylline" in noms
    # Owner renseigné (identité de la clé ou mode local).
    assert ligne.owner, "l'identité du propriétaire doit être enregistrée"


def test_aucun_etat_en_memoire_processus(client):
    """Le dict `_ETUDES` en mémoire a disparu : tout est en base."""
    import webintel.ui.chercheur_etudes_routes as routes

    assert not hasattr(routes, "_ETUDES"), (
        "le dict en mémoire ne doit plus exister")
    sid, _ids = _etapes_1_2(client)
    assert db_ligne(sid) is not None


def db_ligne(sid: str) -> WizardSession | None:
    session = SessionLocal()
    try:
        return session.get(WizardSession, sid)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Survie au « redémarrage » : nouvelle app, même base
# ---------------------------------------------------------------------------

def test_session_survit_a_la_recreation_de_l_app(client, server_bootstrap):
    sid, _ids = _etapes_1_2(client)
    assert db_ligne(sid) is not None

    # « Redémarrage » : nouvelle application sur la même base — aucun état
    # en mémoire ne peut survivre, seule la base compte.
    nouvelle_app = webintel.main.create_app()
    plain = server_bootstrap["plain_key"] if server_bootstrap else None
    with _client_ui_sur(nouvelle_app, plain) as client2:
        r = client2.get(f"/etudes/etape2?session={sid}")
        assert r.status_code == 200, r.text
        assert "Caféine" in r.text and "Théophylline" in r.text
        # La reprise continue normalement jusqu'à l'étape 3.
        r = client2.get(f"/etudes/etape3?session={sid}")
        assert r.status_code == 200
        assert "Quel type d'analyse ?" in r.text


def test_reprise_session_inachevee_apres_fermeture_navigateur(
        client, server_bootstrap):
    """Nouveau client HTTP (cookies vierges) = navigateur fermé puis rouvert :
    l'URL `?session=` suffit à retrouver le brouillon."""
    sid, _ids = _etapes_1_2(client)
    plain = server_bootstrap["plain_key"] if server_bootstrap else None
    with _client_ui_sur(webintel.main.app, plain) as client2:
        r = client2.get(f"/etudes/etape4?session={sid}",
                        follow_redirects=False)
        # Pas encore d'analyses choisies → retour guidé vers l'étape 3.
        assert r.status_code == 303
        assert f"/etudes/etape3?session={sid}" in r.headers["location"]
        # L'étape 2 affiche toujours les molécules choisies.
        r = client2.get(f"/etudes/etape2?session={sid}")
        assert r.status_code == 200
        assert "Caféine" in r.text


# ---------------------------------------------------------------------------
# TTL / purge
# ---------------------------------------------------------------------------

def test_session_expiree_repond_404_et_est_supprimee(client, db_session):
    sid, _ids = _etapes_1_2(client)
    ligne = db_session.get(WizardSession, sid)
    ligne.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    r = client.get(f"/etudes/etape2?session={sid}", follow_redirects=False)
    assert r.status_code == 404
    assert "expirée" in r.text or "introuvable" in r.text
    db_session.expire_all()
    assert db_session.get(WizardSession, sid) is None, (
        "une session expirée accédée doit être supprimée")


def test_purger_sessions_expirees(db_session):
    from webintel.ui.routes import _default_project

    projet = _default_project(db_session)
    maintenant = datetime.now(timezone.utc)
    expiree = WizardSession(
        session_id="a" * 32, project_id=projet.id, owner="ui-local",
        payload={}, status="en_cours",
        expires_at=maintenant - timedelta(days=1))
    valide = WizardSession(
        session_id="b" * 32, project_id=projet.id, owner="ui-local",
        payload={}, status="en_cours",
        expires_at=maintenant + timedelta(days=10))
    db_session.add_all([expiree, valide])
    db_session.commit()

    purgees = purger_sessions_expirees(db_session)
    assert purgees == 1
    assert db_session.get(WizardSession, "a" * 32) is None
    assert db_session.get(WizardSession, "b" * 32) is not None


def test_ttl_repousse_a_chaque_ecriture(client, db_session):
    """Fenêtre glissante : chaque étape repousse l'échéance à ~30 jours."""
    sid, _ids = _etapes_1_2(client)
    ligne = db_session.get(WizardSession, sid)
    # Vieillit artificiellement la session (10 jours déjà consommés).
    ligne.expires_at = datetime.now(timezone.utc) + timedelta(days=20)
    db_session.commit()

    r = client.post(f"/etudes/etape2?session={sid}",
                    data={"action": "choisir_plante", "plante_id": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    db_session.expire_all()
    ligne = db_session.get(WizardSession, sid)
    restant = (ligne.expires_at.replace(tzinfo=timezone.utc)
               - datetime.now(timezone.utc))
    assert restant > timedelta(days=29), (
        "l'écriture doit repousser le TTL à ~30 jours")


# ---------------------------------------------------------------------------
# Isolation inter-projets : l'UUID seul ne suffit plus
# ---------------------------------------------------------------------------

def test_charger_session_403_si_autre_projet(db_session):
    from webintel.ui.routes import _default_project

    projet_a = _default_project(db_session)
    projet_b = Project(name="Projet B")
    db_session.add(projet_b)
    db_session.flush()
    ligne = WizardSession(
        session_id="c" * 32, project_id=projet_b.id, owner="ui:xyz",
        payload={"molecules": []}, status="en_cours",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    db_session.add(ligne)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        _charger_session(db_session, projet_a, "c" * 32)
    assert exc.value.status_code == 403
    # Même projet : OK.
    _ligne, etat = _charger_session(db_session, projet_b, "c" * 32)
    assert etat["id"] == "c" * 32
    # Session inconnue : 404 (pas d'oracle sur l'existence).
    with pytest.raises(HTTPException) as exc2:
        _charger_session(db_session, projet_a, "0" * 32)
    assert exc2.value.status_code == 404


def test_acces_croise_http_403(key_mode, db_session):
    """Deux projets, deux clés : la session du projet A est illisible
    depuis le projet B (403), même avec l'UUID exact."""
    projet_a, cle_a = _projet_avec_cle(db_session, "Wizard A")
    _projet_b, cle_b = _projet_avec_cle(db_session, "Wizard B")

    with TestClient(webintel.main.app,
                    base_url="https://testserver") as client_a:
        assert client_a.post(
            "/login", data={"api_key": cle_a}).status_code in (200, 303)
        r = client_a.post("/etudes/etape1",
                          data={"type_objet": "molecules"},
                          follow_redirects=False)
        assert r.status_code == 303, r.text
        sid = _session_id(r.headers["location"])
        # La session est bien rattachée au projet A en base.
        ligne = db_session.get(WizardSession, sid)
        assert ligne is not None and ligne.project_id == projet_a.id

    with TestClient(webintel.main.app,
                    base_url="https://testserver") as client_b:
        assert client_b.post(
            "/login", data={"api_key": cle_b}).status_code in (200, 303)
        r = client_b.get(f"/etudes/etape2?session={sid}")
        assert r.status_code == 403, (
            f"accès croisé inter-projets : attendu 403, reçu {r.status_code}")
        r = client_b.post(f"/etudes/etape2?session={sid}",
                          data={"action": "continuer"},
                          follow_redirects=False)
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Finalisation : l'étude créée est rattachée au projet
# ---------------------------------------------------------------------------

def test_finalisation_rattachee_au_projet(client, db_session):
    from webintel.science.models import CombinationStudy

    sid, _ids = _etapes_1_2(client)
    r = client.post(f"/etudes/etape3?session={sid}",
                    data={"analyses": ["combinaison"], "mode": "simple"},
                    follow_redirects=False)
    assert r.status_code == 303
    r = client.post(f"/etudes/lancer?session={sid}", follow_redirects=False)
    assert r.status_code == 200
    assert "Analyses en cours" in r.text

    db_session.expire_all()
    ligne = db_session.get(WizardSession, sid)
    assert ligne is not None and ligne.status == "terminee"
    etude = (db_session.query(CombinationStudy)
             .filter(CombinationStudy.project_id == ligne.project_id)
             .order_by(CombinationStudy.id.desc()).first())
    assert etude is not None, "l'étude doit exister, rattachée au projet"
    assert etude.project_id == ligne.project_id
    # Les résultats restent consultables via la session terminée.
    r = client.get(f"/etudes/{sid}/resultats")
    assert r.status_code == 200
    assert "Étude de combinaison" in r.text
