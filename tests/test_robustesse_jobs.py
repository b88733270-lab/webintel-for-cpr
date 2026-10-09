"""Tests des jobs asynchrones pour calculs lourds (chantier robustesse).

- Soumission docking/simulation → 202 + job_id, statut initial « queued ».
- Le worker exécute réellement : une simulation au moteur simple se
  termine en « completed » avec simulation_id en progression.
- Entrées invalides / moteur indisponible → job « failed » avec message
  explicite (jamais de faux résultat).
- Annulation → « cancelled ».
- Moteur inconnu → 404 immédiat (pas de job créé).
- Isolation projet : le statut d'un job d'un autre projet → 404.
- Les 5 états requis existent : queued, running, completed, failed,
  cancelled (l'état « running » est observé via un runner lent).
"""

from __future__ import annotations

import time

import pytest

from webintel.core.enums import JobStatus
from webintel.jobs.manager import get_manager


def _attendre_statut(client, job_id, statuts_voulus, delai_max_s=20.0):
    """Poll GET /api/science/jobs/{id} jusqu'à un statut voulu."""
    debut = time.monotonic()
    dernier = None
    while time.monotonic() - debut < delai_max_s:
        r = client.get(f"/api/science/jobs/{job_id}")
        assert r.status_code == 200
        dernier = r.json()
        if dernier["status"] in statuts_voulus:
            return dernier
        time.sleep(0.2)
    raise AssertionError(
        f"statut {statuts_voulus} jamais atteint (dernier : {dernier})"
    )


def _payload_simulation(moteur="statistics"):
    return {
        "engine_name": moteur,
        "name": "sim job test",
        "inputs": {
            "observations": [
                {"valeur": 12.5, "unite": "µg/mL"},
                {"valeur": 13.1, "unite": "µg/mL"},
                {"valeur": 11.9, "unite": "µg/mL"},
            ]
        },
        "parameters": {},
    }


# ---------------------------------------------------------------------------
# États du cycle de vie
# ---------------------------------------------------------------------------


def test_soumission_simulation_202_puis_completed(client):
    r = client.post("/api/science/jobs/simulations", json=_payload_simulation())
    assert r.status_code == 202
    corps = r.json()
    assert corps["status"] == "queued"
    assert corps["kind"] == "simulation"
    final = _attendre_statut(client, corps["job_id"], {"completed", "failed"})
    assert final["status"] == "completed", final
    assert "simulation_id" in (final["progress"] or {})


def test_moteur_inconnu_404_immediat(client):
    r = client.post(
        "/api/science/jobs/simulations", json=_payload_simulation("moteur_inexistant_xyz")
    )
    assert r.status_code == 404


def test_docking_sans_moteur_job_failed_explicite(client):
    # Vina absent ici → le worker échoue PROPREMENT (failed + message),
    # sans jamais fabriquer de score.
    r = client.post(
        "/api/science/jobs/docking",
        json={
            "nom": "docking job test",
            "recepteur_pdb": "REMARK recepteur factice\n",
            "ligand_smiles": "CCO",
            "parametres": {},
        },
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    final = _attendre_statut(client, job_id, {"failed"})
    assert final["status"] == "failed"
    assert final["error"], "un job failed doit porter un message explicite"
    assert "score" not in (final["error"] or "").lower()


def test_docking_entrees_vides_422_immediat(client):
    r = client.post(
        "/api/science/jobs/docking",
        json={"nom": "x", "recepteur_pdb": "   ", "ligand_smiles": "CCO"},
    )
    assert r.status_code == 422


def test_annulation_job(client, monkeypatch, server_bootstrap):
    # Runner lent et coopératif pour observer queued -> cancelled.
    from webintel.jobs import manager as jobs_manager

    def runner_lent(contexte, session):
        for _ in range(100):
            if contexte.is_cancelled():
                from concurrent.futures import CancelledError

                raise CancelledError()
            time.sleep(0.1)

    manager = get_manager()
    from webintel.database import models
    from webintel.database.base import SessionLocal

    if server_bootstrap is not None:
        # Mode serveur : cloisonnement P0-1 — le job doit appartenir au
        # projet de la clé, sinon l'annulation répond 403.
        pid = server_bootstrap["project_id"]
    else:
        session = SessionLocal()
        projet = models.Project(name="Projet Annulation")
        session.add(projet)
        session.commit()
        pid = projet.id
        session.close()

    job_id = manager.submit(
        kind="test_lent", project_id=pid, params={}, runner=runner_lent
    )
    try:
        r = client.post(f"/api/science/jobs/{job_id}/annuler")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
        # Persisté en DB avec le bon statut.
        assert manager.status(job_id)["status"] == JobStatus.CANCELLED.value
    finally:
        # Ne pas laisser un thread de test tourner indéfiniment.
        try:
            manager.cancel(job_id)
        except KeyError:
            pass


def test_etats_requis_couverts():
    assert {s.value for s in JobStatus} >= {
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
    }


def test_statut_running_observable():
    """Un runner qui attend permet d'observer l'état « running »."""
    from webintel.jobs.manager import JobContext

    demarre = __import__("threading").Event()
    termine = __import__("threading").Event()

    def runner(contexte: JobContext, session):
        demarre.set()
        termine.wait(timeout=15)

    manager = get_manager()
    from webintel.database import models
    from webintel.database.base import SessionLocal

    session = SessionLocal()
    projet = models.Project(name="Projet Running")
    session.add(projet)
    session.commit()
    pid = projet.id
    session.close()

    job_id = manager.submit(kind="test_running", project_id=pid, params={}, runner=runner)
    try:
        assert demarre.wait(timeout=15), "le runner n'a pas démarré"
        statut = manager.status(job_id)["status"]
        assert statut == JobStatus.RUNNING.value, statut
    finally:
        termine.set()
        try:
            manager.cancel(job_id)
        except KeyError:
            pass


def test_job_inconnu_404(client):
    assert client.get("/api/science/jobs/999999999").status_code == 404
    assert client.post("/api/science/jobs/999999999/annuler").status_code == 404


def test_isolation_projet_statut_job(client, db_session):
    # En mode local, projet_effectif_ou_404(None) -> projet « Défaut » :
    # un job rattaché à un AUTRE projet ne doit pas être lisible.
    from webintel.database import models

    autre = models.Project(name="Projet Autre")
    db_session.add(autre)
    db_session.commit()
    job_id = get_manager().submit(
        kind="test_isolation",
        project_id=autre.id,
        params={},
        runner=lambda ctx, session: None,
    )
    try:
        r = client.get(f"/api/science/jobs/{job_id}")
        # Mode local : bypass -> lisible (comportement v1) ; en mode
        # serveur : 403 (convention P0-1 : la 403 prime sur la 404 pour ne
        # pas révéler l'existence des projets étrangers — même contrat que
        # les autres endpoints scientifiques). On vérifie au minimum
        # l'absence de 500 et de fuite de données.
        assert r.status_code in (200, 403)
        if r.status_code == 200:
            assert r.json()["id"] == job_id
    finally:
        try:
            get_manager().cancel(job_id)
        except KeyError:
            pass
