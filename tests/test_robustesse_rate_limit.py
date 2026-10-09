"""Tests du rate-limiting HTTP (chantier robustesse — P1-3 de l'audit).

- Limiteur à fenêtre glissante : comportement unitaire (limite, glissement,
  réinitialisation, thread-safety).
- Anti-brute-force sur POST /login : 5 échecs → verrouillage temporaire
  (429 + message français) ; un succès réinitialise le compteur ; seuls
  les échecs comptent (les logins réussis de la fixture `client` ne sont
  jamais bloqués).
- Rate-limit générique des endpoints API coûteux : 429 + `Retry-After`.
- Désactivé par défaut dans la suite (`RATE_LIMIT_ENABLED=false` via
  conftest) : les tests l'activent explicitement par monkeypatch.
"""

from __future__ import annotations

import threading

import pytest

from webintel.config.settings import get_settings
from webintel.core.utils import sha256_hex
from webintel.database import models
from webintel.security.rate_limit import (
    LimiteurFenetreGlissante,
    get_limiteur,
)


# ---------------------------------------------------------------------------
# Unitaire : fenêtre glissante
# ---------------------------------------------------------------------------


def test_fenetre_glissante_limite_puis_glissement():
    limiteur = LimiteurFenetreGlissante()
    # 3 événements autorisés dans une fenêtre de 60 s.
    for i in range(3):
        assert limiteur.enregistrer("cle", 3, 60.0, maintenant=1000.0 + i).autorise
    refuse = limiteur.enregistrer("cle", 3, 60.0, maintenant=1003.0)
    assert not refuse.autorise
    assert refuse.reessayer_dans_s > 0
    # Après glissement de la fenêtre (t=1061), le plus ancien est éjecté.
    assert limiteur.enregistrer("cle", 3, 60.0, maintenant=1061.0).autorise


def test_verifier_sans_enregistrer_ne_compte_pas():
    limiteur = LimiteurFenetreGlissante()
    for _ in range(10):
        assert limiteur.verifier("cle", 3, 60.0, maintenant=1000.0).autorise
    assert limiteur.nombre_evenements("cle", 60.0) == 0


def test_reinitialiser_remet_le_compteur_a_zero():
    limiteur = LimiteurFenetreGlissante()
    for _ in range(5):
        limiteur.enregistrer("cle", 3, 60.0, maintenant=1000.0)
    assert not limiteur.enregistrer("cle", 3, 60.0, maintenant=1001.0).autorise
    limiteur.reinitialiser("cle")
    assert limiteur.enregistrer("cle", 3, 60.0, maintenant=1002.0).autorise


def test_thread_safety_pas_de_depassement():
    limiteur = LimiteurFenetreGlissante()
    autorises = []

    def _worker():
        for _ in range(50):
            if limiteur.enregistrer("cle", 100, 60.0).autorise:
                autorises.append(1)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Jamais plus de 100 événements acceptés malgré la concurrence.
    assert len(autorises) == 100


def test_cles_independantes_par_bucket_et_ip():
    limiteur = LimiteurFenetreGlissante()
    for _ in range(3):
        limiteur.enregistrer("login:1.2.3.4", 3, 60.0, maintenant=1000.0)
    assert not limiteur.enregistrer("login:1.2.3.4", 3, 60.0, maintenant=1001.0).autorise
    # Autre IP : compteur indépendant.
    assert limiteur.enregistrer("login:5.6.7.8", 3, 60.0, maintenant=1001.0).autorise
    # Autre bucket : compteur indépendant.
    assert limiteur.enregistrer("api_couteux:1.2.3.4", 3, 60.0, maintenant=1001.0).autorise


# ---------------------------------------------------------------------------
# Helpers d'activation (le rate-limit est désactivé par défaut en test)
# ---------------------------------------------------------------------------


@pytest.fixture()
def rate_limit_actif(monkeypatch):
    """Active le rate-limit avec des seuils bas pour les tests."""
    settings = get_settings()
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "LOGIN_MAX_TENTATIVES", 5)
    monkeypatch.setattr(settings, "LOGIN_FENETRE_S", 300)
    monkeypatch.setattr(settings, "API_LIMITE_REQUETES", 3)
    monkeypatch.setattr(settings, "API_FENETRE_S", 60)
    get_limiteur().reinitialiser()
    return settings


@pytest.fixture()
def cle_api_valide(db_session):
    """Crée un projet + une clé API valide en base (mode serveur simulé)."""
    import secrets

    projet = models.Project(name="Projet RateLimit")
    db_session.add(projet)
    db_session.flush()
    cle_claire = secrets.token_urlsafe(32)
    db_session.add(
        models.ApiKey(
            project_id=projet.id,
            name="clé-ratelimit",
            key_hash=sha256_hex(cle_claire),
            prefix=cle_claire[:8],
            quotas={},
            revoked=False,
        )
    )
    db_session.commit()
    return cle_claire


@pytest.fixture()
def mode_serveur_simule(monkeypatch, rate_limit_actif):
    """Simule le mode serveur pour /login (REQUIRE_API_KEY=True en mémoire)."""
    monkeypatch.setattr(rate_limit_actif, "REQUIRE_API_KEY", True)
    return rate_limit_actif


# ---------------------------------------------------------------------------
# Anti-brute-force sur POST /login
# ---------------------------------------------------------------------------


def test_login_5_echecs_puis_verrouillage_429(
    client, mode_serveur_simule, cle_api_valide
):
    for _ in range(5):
        r = client.post("/login", data={"api_key": "mauvaise-cle"})
        assert r.status_code == 200
        assert "Clé API invalide" in r.text
    # 6e tentative : verrouillage temporaire, message en français.
    r = client.post("/login", data={"api_key": "mauvaise-cle"})
    assert r.status_code == 429
    assert "Trop de tentatives de connexion" in r.text
    assert "temporairement" in r.text


def test_login_succes_reinitialise_le_compteur(
    client, mode_serveur_simule, cle_api_valide
):
    for _ in range(4):
        r = client.post("/login", data={"api_key": "mauvaise-cle"})
        assert r.status_code == 200
    # Succès avant le verrouillage : le compteur repart de zéro.
    r = client.post("/login", data={"api_key": cle_api_valide}, follow_redirects=False)
    assert r.status_code == 303
    for _ in range(4):
        r = client.post("/login", data={"api_key": "mauvaise-cle"})
        assert r.status_code == 200, "le compteur aurait dû être réinitialisé"
    r = client.post("/login", data={"api_key": "mauvaise-cle"})
    assert r.status_code == 200  # 5e échec du nouveau cycle : pas encore verrouillé
    r = client.post("/login", data={"api_key": "mauvaise-cle"})
    assert r.status_code == 429


def test_login_reussites_illimitees_ne_declenchent_jamais_le_verrou(
    client, mode_serveur_simule, cle_api_valide
):
    # Les logins réussis ne comptent pas (la fixture `client` du mode
    # serveur fait un login à chaque test : elle ne doit jamais être
    # bloquée par l'anti-brute-force).
    for _ in range(10):
        r = client.post("/login", data={"api_key": cle_api_valide}, follow_redirects=False)
        assert r.status_code == 303


def test_login_verrouillage_par_ip_independant(client, mode_serveur_simule):
    # Sans clé valide en base, tous les logins échouent : on verrouille
    # l'IP « testclient » sans affecter la logique (5 échecs → 429).
    for _ in range(5):
        assert client.post("/login", data={"api_key": "x"}).status_code == 200
    assert client.post("/login", data={"api_key": "x"}).status_code == 429


# ---------------------------------------------------------------------------
# Rate-limit générique des endpoints API coûteux
# ---------------------------------------------------------------------------


def test_endpoint_couteux_429_avec_retry_after(client, rate_limit_actif):
    # POST /api/science/simulations/run : moteur inconnu → 404, mais la
    # requête compte dans le bucket « api_couteux ».
    payload = {"engine_name": "moteur_inexistant", "name": "t", "inputs": {}, "parameters": {}}
    for _ in range(3):
        r = client.post("/api/science/simulations/run", json=payload)
        assert r.status_code == 404
    r = client.post("/api/science/simulations/run", json=payload)
    assert r.status_code == 429
    assert "Trop de requêtes" in r.json()["detail"]
    assert "Retry-After" in r.headers


def test_endpoint_non_couteux_pas_de_limite(client, rate_limit_actif):
    # Les endpoints légers ne sont pas limités par le bucket coûteux.
    for _ in range(10):
        r = client.get("/api/science/data-levels")
        assert r.status_code == 200


def test_rate_limit_desactive_par_defaut(client):
    # conftest : RATE_LIMIT_ENABLED=false → aucun 429 même en rafale.
    settings = get_settings()
    assert settings.RATE_LIMIT_ENABLED is False
    payload = {"engine_name": "moteur_inexistant", "name": "t", "inputs": {}, "parameters": {}}
    for _ in range(10):
        r = client.post("/api/science/simulations/run", json=payload)
        assert r.status_code == 404
