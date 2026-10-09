"""Audit adversarial — matrice IDOR inter-projets (ressources non couvertes).

Complète `test_security_idor.py` : pour chaque ressource restante
(synergie, docking, jobs, notes de laboratoire UI, échantillons/terrain,
wizard, rapports, exports, historique d'études, collectes), la clé du
projet A ne doit ni lire, ni modifier, ni exporter les données du
projet B — par API ET par UI. 403/404 honnêtes, jamais de fuite.
"""

import secrets

import pytest
from fastapi.testclient import TestClient

import webintel.config.settings as _settings_mod
import webintel.main
from webintel.core.utils import sha256_hex
from webintel.database.models import ApiKey, Project


@pytest.fixture()
def key_mode(monkeypatch):
    settings = _settings_mod.Settings()
    settings.REQUIRE_API_KEY = True
    settings.SECRET_KEY = "cle-secrete-test-adversarial-32-caracteres!!"
    _settings_mod._settings = settings
    yield
    _settings_mod._settings = None


def _project_with_key(db_session, name: str):
    project = Project(name=name)
    db_session.add(project)
    db_session.flush()
    plain = secrets.token_urlsafe(32)
    db_session.add(ApiKey(
        project_id=project.id,
        name=f"clé-{name}",
        key_hash=sha256_hex(plain),
        prefix=plain[:8],
        quotas={},
        revoked=False,
    ))
    db_session.commit()
    return project.id, plain


@pytest.fixture()
def two_projects(db_session):
    p1, k1 = _project_with_key(db_session, "Projet ADV A")
    p2, k2 = _project_with_key(db_session, "Projet ADV B")
    return {"p1": p1, "k1": k1, "p2": p2, "k2": k2}


@pytest.fixture()
def api():
    with TestClient(webintel.main.app, base_url="https://testserver") as client:
        yield client


def _h(plain: str) -> dict:
    return {"X-API-Key": plain}


def _ui(plain: str) -> TestClient:
    client = TestClient(webintel.main.app, base_url="https://testserver")
    resp = client.post("/login", data={"api_key": plain})
    assert resp.status_code in (200, 303), resp.text
    return client


def _creer_etude_combinaison(api, headers, project_id):
    resp = api.post(
        "/api/science/combinaisons",
        json={"nom": "Étude ADV", "project_id": project_id,
              "compound_ids": [1, 2]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Études de combinaison : lecture / rapport / suppression inter-projets
# ---------------------------------------------------------------------------

def test_etude_combinaison_rapport_api_projet_etranger_403(
        api, db_session, two_projects, key_mode):
    from webintel.science.models import CombinationStudy
    etude = CombinationStudy(
        project_id=two_projects["p2"], nom="Étude B",
        compound_ids=[1, 2], inputs_hash="i" * 64, statut="brouillon")
    db_session.add(etude)
    db_session.commit()
    eid = etude.id
    # Détail
    r = api.get(f"/api/science/combinaisons/{eid}",
                headers=_h(two_projects["k1"]))
    assert r.status_code in (403, 404), r.text
    # Rapport API
    r = api.get(f"/api/science/combinaisons/{eid}/rapport",
                headers=_h(two_projects["k1"]))
    assert r.status_code in (403, 404), r.text
    # Suppression (si l'endpoint existe)
    r = api.delete(f"/api/science/combinaisons/{eid}",
                   headers=_h(two_projects["k1"]))
    assert r.status_code in (403, 404, 405), r.text
    # UI : page combinaison
    ui = _ui(two_projects["k1"])
    r = ui.get(f"/combinaisons/{eid}")
    assert r.status_code == 404, r.text
    # UI : rapport d'étude + téléchargement
    for url in (f"/etudes/{eid}/rapport",
                f"/etudes/{eid}/rapport/telecharger",
                f"/etudes/{eid}/historique"):
        r = ui.get(url)
        assert r.status_code == 404, (url, r.text)
    # UI : restauration d'historique (écriture !)
    r = ui.post(f"/etudes/{eid}/historique/restaurer")
    assert r.status_code == 404, r.text
    # Les données du projet B restent intactes.
    assert db_session.get(CombinationStudy, eid) is not None


# ---------------------------------------------------------------------------
# Laboratoire : notes privées inter-projets
# ---------------------------------------------------------------------------

def test_lab_note_projet_etranger_403(api, db_session, two_projects, key_mode):
    """Les notes de laboratoire d'un autre projet sont inaccessibles."""
    from webintel.science import lab as lab_service
    note = lab_service.creer_note(
        db_session, two_projects["p2"], titre="Note B", contenu="secret")
    db_session.commit()
    nid = note.id
    h1 = _h(two_projects["k1"])
    r = api.get(f"/api/science/lab/notes/{nid}", headers=h1)
    assert r.status_code in (403, 404), r.text
    r = api.put(f"/api/science/lab/notes/{nid}",
                json={"titre": "Pirate"}, headers=h1)
    assert r.status_code in (403, 404), r.text
    db_session.refresh(note)
    assert note.titre == "Note B"


# ---------------------------------------------------------------------------
# Synergie : lecture inter-projets
# ---------------------------------------------------------------------------

def test_synergie_detail_projet_etranger_403(api, db_session, two_projects,
                                            key_mode):
    from webintel.science.models import SynergyAnalysis
    analyse = SynergyAnalysis(
        project_id=two_projects["p2"], nom="Syn B", methode="bliss",
        inputs_hash="s" * 64, statut="termine", data_level="prediction",
        is_private=True, donnees={}, parametres={}, resultats={})
    db_session.add(analyse)
    db_session.commit()
    aid = analyse.id
    r = api.get(f"/api/science/synergie/{aid}",
                headers=_h(two_projects["k1"]))
    assert r.status_code in (403, 404), r.text
    # La liste ne doit contenir que les analyses du projet de la clé.
    r = api.get("/api/science/synergie", headers=_h(two_projects["k1"]))
    assert r.status_code == 200, r.text
    ids = [a["id"] for a in r.json()]
    assert aid not in ids


# ---------------------------------------------------------------------------
# Docking + jobs : lecture / annulation inter-projets
# ---------------------------------------------------------------------------

def test_docking_job_projet_etranger_403(api, db_session, two_projects,
                                         key_mode):
    from webintel.science.models import DockingJob
    job = DockingJob(
        project_id=two_projects["p2"], nom="Dock B", statut="refuse",
        inputs_hash="d" * 64, data_level="simulation")
    db_session.add(job)
    db_session.commit()
    jid = job.id
    r = api.get(f"/api/science/docking/{jid}",
                headers=_h(two_projects["k1"]))
    assert r.status_code in (403, 404), r.text
    ui = _ui(two_projects["k1"])
    r = ui.get(f"/docking/{jid}")
    assert r.status_code == 404, r.text
    r = ui.get(f"/resultats/docking/{jid}/provenance")
    assert r.status_code == 404, r.text


def test_science_job_annuler_projet_etranger_403(api, db_session, two_projects,
                                                key_mode):
    from webintel.database.models import CrawlJob
    job = CrawlJob(
        project_id=two_projects["p2"], kind="docking",
        status="queued", params={})
    db_session.add(job)
    db_session.commit()
    jid = job.id
    # Le job est inconnu du gestionnaire en mémoire (pas soumis) → 404
    # honnête ; l'important est qu'aucune fuite/annulation n'ait lieu.
    r = api.get(f"/api/science/jobs/{jid}", headers=_h(two_projects["k1"]))
    assert r.status_code in (403, 404), r.text
    r = api.post(f"/api/science/jobs/{jid}/annuler",
                 headers=_h(two_projects["k1"]))
    assert r.status_code in (403, 404), r.text
    db_session.refresh(job)
    assert job.status == "queued"


# ---------------------------------------------------------------------------
# Wizard : même projet, autre utilisateur (IDOR intra-projet)
# ---------------------------------------------------------------------------

def _cle_supplementaire(db_session, project_id: int):
    from webintel.core.utils import sha256_hex
    from webintel.database.models import ApiKey
    import secrets as _secrets
    brut = _secrets.token_urlsafe(32)
    cle = ApiKey(
        project_id=project_id, name="Cle B (même projet)",
        key_hash=sha256_hex(brut), prefix=brut[:8], quotas={},
        revoked=False)
    db_session.add(cle)
    db_session.commit()
    return brut


def _session_wizard(ui) -> str:
    import re
    r = ui.post("/etudes/etape1", data={"type_objet": "molecule"},
                follow_redirects=False)
    assert r.status_code == 303, r.text
    m = re.search(r"session=([0-9a-f]{32})", r.headers["location"])
    assert m, "session_id introuvable dans la redirection étape 1"
    return m.group(1)


def test_wizard_session_autre_utilisateur_meme_projet_403(
        two_projects, key_mode, db_session):
    """Un autre membre du même projet ne peut ni lire ni modifier le brouillon."""
    ui_a = _ui(two_projects["k1"])
    cle_b = _cle_supplementaire(db_session, two_projects["p1"])
    ui_b = _ui(cle_b)
    sid = _session_wizard(ui_a)
    # B tente de lire le brouillon de A.
    r = ui_b.get(f"/etudes/etape2?session={sid}")
    assert r.status_code == 403, r.text
    assert "autre utilisateur" in r.text
    # B tente de le modifier (POST étape 2).
    r = ui_b.post(f"/etudes/etape2?session={sid}",
                  data={"molecules": "eau"}, follow_redirects=False)
    assert r.status_code == 403, r.text
    # A conserve l'accès à son brouillon.
    r = ui_a.get(f"/etudes/etape2?session={sid}")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Laboratoire : PATCH observations / simulations inter-projets
# ---------------------------------------------------------------------------

def test_lab_patch_observation_projet_etranger_403(api, db_session,
                                                   two_projects, key_mode):
    from webintel.science.models import ResearchObservation
    obs = ResearchObservation(
        project_id=two_projects["p2"], author="labo-b",
        comment="Obs B", raw_data={"secret": True})
    db_session.add(obs)
    db_session.commit()
    oid = obs.id
    r = api.patch(f"/api/science/lab/observations/{oid}",
                  json={"comment": "Pirate"}, headers=_h(two_projects["k1"]))
    assert r.status_code in (403, 404), r.text
    db_session.refresh(obs)
    assert obs.comment == "Obs B"


def test_lab_patch_simulation_projet_etranger_403(api, db_session, two_projects,
                                                  key_mode):
    from webintel.science.models import Simulation
    sim = Simulation(
        project_id=two_projects["p2"], name="Sim B", target="COX-2",
        method="test", status="brouillon", input_hash="x" * 64)
    db_session.add(sim)
    db_session.commit()
    sid = sim.id
    r = api.patch(f"/api/science/lab/simulations/{sid}",
                  json={"name": "Pirate"}, headers=_h(two_projects["k1"]))
    assert r.status_code in (403, 404), r.text
    db_session.refresh(sim)
    assert sim.name == "Sim B"


# ---------------------------------------------------------------------------
# Journal UI (notes de laboratoire privées) : lecture/écriture inter-projets
# ---------------------------------------------------------------------------

def test_journal_ui_projet_etranger_404(db_session, two_projects, key_mode):
    from webintel.science import lab as lab_module
    note = lab_module.creer_note(
        db_session, project_id=two_projects["p2"], titre="Note privée B",
        contenu="donnée confidentielle du laboratoire B")
    db_session.commit()
    nid = note.id
    ui = _ui(two_projects["k1"])
    assert ui.get(f"/journal/{nid}").status_code == 404
    assert ui.get(f"/journal/{nid}/versions").status_code == 404
    r = ui.post(f"/journal/{nid}", data={"titre": "Pirate",
                                         "contenu": "modifié"})
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Terrain : lectures unitaires inter-projets (échantillons, chaîne, pharma)
# ---------------------------------------------------------------------------

def test_terrain_lectures_projet_etranger_403(api, db_session, two_projects,
                                              key_mode):
    from webintel.science import models as m
    plante = m.Plant(scientific_name="ADV Planta")
    db_session.add(plante)
    db_session.flush()
    ech = m.Echantillon(project_id=two_projects["p2"], plant_id=plante.id,
                        code_echantillon="ADV-001",
                        niveau_confidentialite="restreint")
    db_session.add(ech)
    db_session.commit()
    eid = ech.id
    h1 = _h(two_projects["k1"])
    r = api.get(f"/api/science/terrain/echantillons/{eid}", headers=h1)
    assert r.status_code in (403, 404), r.text
    r = api.get(f"/api/science/terrain/echantillons/{eid}/chaine", headers=h1)
    assert r.status_code in (403, 404), r.text
    r = api.delete(f"/api/science/terrain/echantillons/{eid}", headers=h1)
    assert r.status_code in (403, 404), r.text
    # L'échantillon du projet B est intact (soft-delete exclu).
    db_session.expire_all()
    assert db_session.get(m.Echantillon, eid).supprime_le is None


def test_terrain_creation_projet_etranger_403(api, db_session, two_projects,
                                              key_mode):
    from webintel.science import models as m
    plante = m.Plant(scientific_name="ADV Planta Nom")
    db_session.add(plante)
    db_session.commit()
    h1 = _h(two_projects["k1"])
    r = api.post(
        "/api/science/terrain/noms-vernaculaires",
        json={"project_id": two_projects["p2"], "plant_id": plante.id,
              "nom_original": "test-nom", "langue": "mooré"},
        headers=h1,
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Wizard : session d'un autre projet → 403 ; inconnue/expirée → 404
# ---------------------------------------------------------------------------

def test_wizard_session_autre_projet_403(two_projects, key_mode):
    ui_a = _ui(two_projects["k1"])
    ui_b = _ui(two_projects["k2"])
    # A crée une session (POST étape 1 → 303 avec session= dans Location).
    r = ui_a.post("/etudes/etape1", data={"type_objet": "molecule"},
                  follow_redirects=False)
    assert r.status_code == 303, r.text
    import re
    m = re.search(r"session=([0-9a-f]{32})", r.headers["location"])
    assert m, "session_id introuvable dans la redirection étape 1"
    sid = m.group(1)
    # B tente d'utiliser la session de A sur plusieurs étapes.
    for url in (f"/etudes/etape2?session={sid}",
                f"/etudes/etape3?session={sid}",
                f"/etudes/etape4?session={sid}",
                f"/etudes/{sid}/resultats"):
        r = ui_b.get(url)
        assert r.status_code == 403, (url, r.status_code)
    r = ui_b.post(f"/etudes/etape2?session={sid}",
                  data={"action": "continuer"})
    assert r.status_code == 403, r.text
    # Session inexistante → 404 (pas 500).
    r = ui_b.get("/etudes/etape2?session=" + "0" * 32)
    assert r.status_code == 404, r.text


def test_wizard_session_expiree_purgee_404(db_session, two_projects, key_mode):
    from datetime import datetime, timedelta, timezone
    from webintel.database.models import WizardSession
    ligne = WizardSession(
        session_id="f" * 32,
        project_id=two_projects["p1"],
        owner="test",
        status="en_cours",
        payload={},
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db_session.add(ligne)
    db_session.commit()
    ui = _ui(two_projects["k1"])
    r = ui.get("/etudes/etape2?session=" + "f" * 32)
    assert r.status_code == 404, r.text
    # La session expirée a été purgée (rafraîchit le cache d'identité).
    db_session.expire_all()
    assert db_session.get(WizardSession, "f" * 32) is None


# ---------------------------------------------------------------------------
# Exports / rapports : pas de fuite inter-projets
# ---------------------------------------------------------------------------

def test_export_dossier_inclure_prive_projet_etranger_403(api, two_projects,
                                                         key_mode):
    for inclure_prive in (False, True):
        r = api.post(
            "/api/science/export/dossier",
            json={"projet_id": two_projects["p2"],
                  "inclure_prive": inclure_prive},
            headers=_h(two_projects["k1"]),
        )
        assert r.status_code == 403, (inclure_prive, r.text)


def test_rapport_api_projet_etranger_403(api, two_projects, key_mode):
    for url in ("/api/science/rapport",
                "/api/science/rapport/zip"):
        r = api.get(url, params={"project_id": two_projects["p2"]},
                    headers=_h(two_projects["k1"]))
        assert r.status_code == 403, (url, r.text)


def test_export_fichier_projet_etranger_403(api, db_session, two_projects,
                                            key_mode):
    # Crée un export pour le projet B via sa clé.
    r = api.post(
        "/api/exports",
        json={"project_id": two_projects["p2"], "kind": "entities",
              "format": "json", "filters": {}},
        headers=_h(two_projects["k2"]),
    )
    assert r.status_code in (200, 201), r.text
    eid = r.json()["id"]
    # La clé A ne peut pas le télécharger.
    r = api.get(f"/api/exports/{eid}/download",
                headers=_h(two_projects["k1"]))
    assert r.status_code in (403, 404), r.text
    # Ni via la session UI de A.
    ui = _ui(two_projects["k1"])
    r = ui.get(f"/api/exports/{eid}/download")
    assert r.status_code in (401, 403, 404), r.text


# ---------------------------------------------------------------------------
# Fiches pharmacognosie inter-projets (P1 — intégrité + confidentialité)
# ---------------------------------------------------------------------------

def test_pharmacognosie_ecrasement_inter_projet_403(api, db_session,
                                                   two_projects, key_mode):
    """Un projet ne peut pas écraser la fiche pharmacognosie d'un autre."""
    from webintel.science import models as m
    plante = m.Plant(scientific_name="ADV Pharma")
    db_session.add(plante)
    db_session.commit()
    pid = plante.id
    h1, h2 = _h(two_projects["k1"]), _h(two_projects["k2"])
    p1, p2 = two_projects["p1"], two_projects["p2"]
    r = api.put(f"/api/science/terrain/plantes/{pid}/pharmacognosie",
                json={"project_id": p1, "plant_id": pid,
                      "sechage": "a l'ombre (projet 1)"}, headers=h1)
    assert r.status_code == 200, r.text
    # Le projet 2 tente d'écraser la fiche du projet 1.
    r = api.put(f"/api/science/terrain/plantes/{pid}/pharmacognosie",
                json={"project_id": p2, "plant_id": pid,
                      "sechage": "au soleil (pirate)"}, headers=h2)
    assert r.status_code == 403, r.text
    assert "autre projet" in r.json()["detail"]
    # La fiche du projet 1 est intacte.
    db_session.expire_all()
    fiche = db_session.query(m.Pharmacognosie).filter(
        m.Pharmacognosie.plant_id == pid).one()
    assert fiche.sechage == "a l'ombre (projet 1)"
    assert fiche.project_id == p1


def test_pharmacognosie_lecture_inter_projet_masquee(api, db_session,
                                                     two_projects, key_mode):
    """Une fiche non publique d'un autre projet n'est pas divulguée."""
    from webintel.science import models as m
    plante = m.Plant(scientific_name="ADV Pharma 2")
    db_session.add(plante)
    db_session.commit()
    pid = plante.id
    h1, h2 = _h(two_projects["k1"]), _h(two_projects["k2"])
    p1, p2 = two_projects["p1"], two_projects["p2"]
    r = api.put(f"/api/science/terrain/plantes/{pid}/pharmacognosie",
                json={"project_id": p1, "plant_id": pid,
                      "sechage": "secret projet 1"}, headers=h1)
    assert r.status_code == 200, r.text
    # Le projet 2 ne voit rien (null honnête, pas de 403 révélateur).
    r = api.get(f"/api/science/terrain/plantes/{pid}/pharmacognosie",
                params={"project_id": p2}, headers=h2)
    assert r.status_code == 200, r.text
    assert r.json() is None
    # Le projet 1 voit sa fiche.
    r = api.get(f"/api/science/terrain/plantes/{pid}/pharmacognosie",
                params={"project_id": p1}, headers=h1)
    assert r.status_code == 200, r.text
    assert r.json()["sechage"] == "secret projet 1"
    # Une fiche explicitement publique reste lisible entre projets.
    r = api.put(f"/api/science/terrain/plantes/{pid}/pharmacognosie",
                json={"project_id": p1, "plant_id": pid,
                      "niveau_confidentialite": "public"}, headers=h1)
    assert r.status_code == 200, r.text
    r = api.get(f"/api/science/terrain/plantes/{pid}/pharmacognosie",
                params={"project_id": p2}, headers=h2)
    assert r.status_code == 200, r.text
    assert r.json() is not None


# ---------------------------------------------------------------------------
# Oracle de fichiers locaux via « fichier brut » (P0/P1 — confinement)
# ---------------------------------------------------------------------------

def _analyse_terrain(db_session, project_id: int) -> int:
    from webintel.science import models as m
    plante = m.Plant(scientific_name="ADV Fichier")
    db_session.add(plante)
    db_session.flush()
    ech = m.Echantillon(project_id=project_id, plant_id=plante.id,
                        code_echantillon="ADV-F-001",
                        niveau_confidentialite="restreint")
    db_session.add(ech)
    db_session.flush()
    ana = m.AnalyseChimique(project_id=project_id,
                            echantillon_id=ech.id, methode="HPLC")
    db_session.add(ana)
    db_session.commit()
    return ana.id


def test_fichier_brut_hors_racine_403(api, db_session, two_projects, key_mode):
    """Un chemin hors du répertoire autorisé est refusé (403)."""
    aid = _analyse_terrain(db_session, two_projects["p1"])
    h = _h(two_projects["k1"])
    for chemin in ("/etc/passwd", "/etc/hostname",
                   "/tmp/fichier-adversarial-inexistant-xyz"):
        r = api.post(f"/api/science/terrain/analyses/{aid}/fichier",
                     json={"chemin": chemin}, headers=h)
        assert r.status_code == 403, (chemin, r.status_code, r.text)
        # Le message ne répercute pas le chemin (pas d'oracle).
        assert "passwd" not in r.text and "hostname" not in r.text


def test_fichier_brut_traversee_symlink_403(api, db_session, two_projects,
                                           key_mode, tmp_path):
    """« .. » et liens symboliques sortants sont neutralisés (403)."""
    aid = _analyse_terrain(db_session, two_projects["p1"])
    h = _h(two_projects["k1"])
    r = api.post(f"/api/science/terrain/analyses/{aid}/fichier",
                 json={"chemin": "../../etc/passwd"}, headers=h)
    assert r.status_code == 403, r.text
    # Symlink déposé hors racine mais pointant vers /etc/passwd :
    # la résolution canonique doit le rejeter.
    lien = tmp_path / "lien-passwd"
    lien.symlink_to("/etc/passwd")
    r = api.post(f"/api/science/terrain/analyses/{aid}/fichier",
                 json={"chemin": str(lien)}, headers=h)
    assert r.status_code == 403, r.text


def test_fichier_brut_dans_racine_ok(api, db_session, two_projects, key_mode,
                                     tmp_path, monkeypatch):
    """Un fichier déposé dans la racine autorisée reste accepté."""
    import os
    racine = tmp_path / "fichiers-bruts"
    racine.mkdir()
    brut = racine / "chromatogramme.dat"
    brut.write_bytes(b"donnees brutes de test")
    from webintel.config import settings as sm
    monkeypatch.setattr(sm.get_settings(), "FICHIERS_BRUTS_DIR", str(racine))
    aid = _analyse_terrain(db_session, two_projects["p1"])
    h = _h(two_projects["k1"])
    r = api.post(f"/api/science/terrain/analyses/{aid}/fichier",
                 json={"chemin": str(brut)}, headers=h)
    assert r.status_code == 200, r.text
    corps = r.json()
    assert corps["sha256"]
    assert corps["chemin"].startswith(os.path.realpath(str(racine)))
    # … mais /etc/passwd reste refusé même avec la racine redéfinie.
    r = api.post(f"/api/science/terrain/analyses/{aid}/fichier",
                 json={"chemin": "/etc/passwd"}, headers=h)
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Collectes terrain UI : cloisonnement
# ---------------------------------------------------------------------------

def test_collecte_ui_projet_etranger_404(db_session, two_projects, key_mode):
    from webintel.offline.models import CollecteTerrain
    collecte = CollecteTerrain(
        project_id=two_projects["p2"], nom_plante_saisi="Plante B",
        localite="Ouagadougou",
        latitude=12.3, longitude=-1.5,
        niveau_confidentialite="restreint", auteur="enqueteur-b")
    db_session.add(collecte)
    db_session.commit()
    cid = collecte.id
    ui = _ui(two_projects["k1"])
    assert ui.get(f"/collectes/{cid}").status_code == 404
    assert ui.get(f"/collectes/{cid}/photos/orig_x.jpg").status_code == 404
    r = ui.post(f"/collectes/{cid}/supprimer")
    assert r.status_code == 404, r.text
    # Donnée intacte.
    assert db_session.get(CollecteTerrain, cid) is not None
