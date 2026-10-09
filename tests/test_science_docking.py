"""Tests v2.1 — workflow docking (9 étapes).

Couvre : aperçu sans exécution (scores None), validation PDB/SMILES/
paramètres, refus propre quand le moteur est indisponible ou lève,
aucun faux score, persistance des jobs, API.
"""

from __future__ import annotations

import pytest

from webintel.database import models as modeles_noyau
from webintel.science import docking
from webintel.science import combinaisons
from webintel.science.docking import (
    MoteurIndisponible,
    apercu_workflow,
    executer_docking,
    list_docking_jobs as list_jobs,
    statut_moteur,
)


@pytest.fixture()
def projet_id(db_session):
    projet = modeles_noyau.Project(name="Projet Docking")
    db_session.add(projet)
    db_session.commit()
    return projet.id


PDB_OK = """HEADER    TEST
ATOM      1  N   ALA A   1      11.104  13.207   2.100  1.00 20.00           N
ATOM      2  CA  ALA A   1      11.639  14.545   2.100  1.00 20.00           C
ATOM      3  C   ALA A   1      10.700  15.500   2.100  1.00 20.00           C
TER
END
"""

PDBQT_OK = """REMARK  Récepteur préparé (fixture de test, PDBQT)
ROOT
ATOM      1  N   ALA A   1      11.104  13.207   2.100  1.00 20.00    0.000 N
ATOM      2  CA  ALA A   1      11.639  14.545   2.100  1.00 20.00    0.000 C
ATOM      3  C   ALA A   1      10.700  15.500   2.100  1.00 20.00    0.000 C
ENDROOT
TORSDOF 0
"""

SMILES_OK = "CCO"


def test_execution_refuse_recepteur_non_pdbqt(db_session, projet_id):
    """PDB brut (non préparé) -> ValueError explicite, rien de persisté."""
    with pytest.raises(ValueError, match="PDBQT"):
        executer_docking(db_session, project_id=projet_id, nom="test",
                         recepteur_pdb=PDB_OK, ligand_smiles=SMILES_OK)
    db_session.rollback()
    assert list_jobs(db_session, projet_id) == []


def test_est_pdbqt_detecte_marqueurs():
    from webintel.science.docking import _est_pdbqt
    assert _est_pdbqt(PDBQT_OK) is True
    assert _est_pdbqt(PDB_OK) is False
    assert _est_pdbqt("") is False


# ---------------------------------------------------------------------------
# Statut moteur
# ---------------------------------------------------------------------------

def test_statut_moteur_forme():
    disponible, raison = statut_moteur()
    assert isinstance(disponible, bool)
    assert isinstance(raison, str)
    assert not disponible or raison == ""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validation_pdb():
    assert "HEADER" in PDB_OK
    with pytest.raises(ValueError, match="PDB"):
        apercu_workflow(recepteur_pdb="pas un pdb", ligand_smiles=SMILES_OK)
    with pytest.raises(ValueError, match="ATOM/HETATM"):
        apercu_workflow(recepteur_pdb="HEADER X\nEND\n", ligand_smiles=SMILES_OK)


def test_validation_smiles():
    with pytest.raises(ValueError, match="SMILES"):
        apercu_workflow(recepteur_pdb=PDB_OK, ligand_smiles="")
    with pytest.raises(ValueError, match="SMILES"):
        apercu_workflow(recepteur_pdb=PDB_OK, ligand_smiles="   ")


def test_validation_parametres():
    apercu = apercu_workflow(recepteur_pdb=PDB_OK, ligand_smiles=SMILES_OK,
                             parametres=None)
    assert apercu["parametres"]["exhaustiveness"] == 8
    with pytest.raises(ValueError, match="taille_boite"):
        apercu_workflow(recepteur_pdb=PDB_OK, ligand_smiles=SMILES_OK,
                        parametres={"taille_boite": [200.0, 20.0, 20.0]})
    with pytest.raises(ValueError, match="exhaustiveness"):
        apercu_workflow(recepteur_pdb=PDB_OK, ligand_smiles=SMILES_OK,
                        parametres={"exhaustiveness": 999})
    with pytest.raises(ValueError, match="nb_poses"):
        apercu_workflow(recepteur_pdb=PDB_OK, ligand_smiles=SMILES_OK,
                        parametres={"nb_poses": 0})


# ---------------------------------------------------------------------------
# Aperçu (sans exécution, sans score)
# ---------------------------------------------------------------------------

def test_apercu_sans_score():
    apercu = apercu_workflow(recepteur_pdb=PDB_OK, ligand_smiles=SMILES_OK,
                             parametres={"centre_boite": [0.0, 0.0, 0.0], "taille_boite": [20.0, 20.0, 20.0]})
    assert len(apercu["etapes"]) == 9
    assert apercu["scores"] is None
    assert apercu["ligand"]["smiles"] == SMILES_OK
    assert len(apercu["recepteur"]["hash_sha256"]) == 64
    assert "aucun score" in apercu["message"].lower()
    for etape in apercu["etapes"]:
        assert comb_verifier_ok(etape["etape"] + " " + etape["description"])
    # Paramètres prêts pour l'exécution.
    assert apercu["parametres"]["taille_boite"] == [20.0, 20.0, 20.0]


def comb_verifier_ok(texte: str) -> bool:
    from webintel.science import combinaisons
    return combinaisons.verifier_honnetete(texte) == []


def test_apercu_honnete():
    apercu = apercu_workflow(recepteur_pdb=PDB_OK, ligand_smiles=SMILES_OK)
    from webintel.science import combinaisons
    assert combinaisons.verifier_honnetete(apercu["message"]) == []
    for limite in apercu["limites"]:
        assert combinaisons.verifier_honnetete(limite) == []


# ---------------------------------------------------------------------------
# Exécution : refus propre, JAMAIS de faux score
# ---------------------------------------------------------------------------

def test_execution_refuse_si_moteur_indisponible(monkeypatch, db_session,
                                                projet_id):
    monkeypatch.setattr(docking, "statut_moteur",
                        lambda: (False, "raison simulée"))
    with pytest.raises(MoteurIndisponible) as exc:
        executer_docking(db_session, project_id=projet_id, nom="test",
                                 recepteur_pdb=PDBQT_OK, ligand_smiles=SMILES_OK)
    assert "raison simulée" in str(exc.value)
    db_session.commit()  # le routeur committe le job "refuse" pour l'audit
    # Job persisté en refus, AUCUN score.
    jobs = list_jobs(db_session, projet_id)
    assert len(jobs) == 1
    assert jobs[0].statut == "refuse"
    assert jobs[0].resultats["scores"] is None
    assert "aucun score" in jobs[0].resultats["message"].lower()


def test_execution_refuse_si_run_echoue(monkeypatch, db_session, projet_id):
    """Moteur annoncé disponible mais run() qui lève → refus propre."""
    class _MoteurCassee:
        ENGINE_VERSION = "test"
        def validate_inputs(self, inputs, params):
            return {"inputs": inputs, "params": params}
        def run(self, inputs, params):
            raise RuntimeError("Vina échoue (simulation de test)")
    class _RegistreStub:
        def get_engine(self, nom):
            return _MoteurCassee()
    monkeypatch.setattr(docking, "statut_moteur", lambda: (True, ""))
    monkeypatch.setattr(docking, "_registre", lambda: _RegistreStub())
    with pytest.raises(MoteurIndisponible) as exc:
        executer_docking(db_session, project_id=projet_id, nom="test",
                                 recepteur_pdb=PDBQT_OK, ligand_smiles=SMILES_OK)
    assert "refusé l'exécution" in str(exc.value)
    db_session.commit()
    jobs = list_jobs(db_session, projet_id)
    assert len(jobs) == 1
    assert jobs[0].statut == "refuse"
    assert jobs[0].resultats["scores"] is None  # jamais de score inventé


def test_validation_avant_persistance(db_session, projet_id):
    with pytest.raises(ValueError, match="SMILES"):
        executer_docking(db_session, project_id=projet_id, nom="test",
                                 recepteur_pdb=PDB_OK, ligand_smiles="")
    db_session.rollback()
    assert list_jobs(db_session, projet_id) == []


def test_list_jobs_isolation(monkeypatch, db_session, projet_id):
    autre = modeles_noyau.Project(name="Autre")
    db_session.add(autre)
    db_session.commit()
    monkeypatch.setattr(docking, "statut_moteur",
                        lambda: (False, "indisponible (test)"))
    with pytest.raises(MoteurIndisponible):
        executer_docking(db_session, project_id=projet_id, nom="test",
                         recepteur_pdb=PDBQT_OK, ligand_smiles=SMILES_OK)
    db_session.commit()
    assert len(list_jobs(db_session, projet_id)) == 1
    assert list_jobs(db_session, autre.id) == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_statut(client):
    r = client.get("/api/science/docking/statut")
    assert r.status_code == 200
    corps = r.json()
    assert corps["disponible"] is False or isinstance(corps["disponible"], bool)
    assert "raison" in corps and "moteur" in corps


def test_api_apercu(client):
    r = client.post("/api/science/docking/apercu",
                    json={"recepteur_pdb": PDB_OK, "ligand_smiles": SMILES_OK})
    assert r.status_code == 200, r.text
    corps = r.json()
    assert len(corps["etapes"]) == 9
    assert corps["scores"] is None


def test_api_apercu_422(client):
    r = client.post("/api/science/docking/apercu",
                    json={"recepteur_pdb": "invalide", "ligand_smiles": SMILES_OK})
    assert r.status_code == 422


def test_api_executer_refus(client):
    r = client.post("/api/science/docking/executer",
                    json={"nom": "Test refus", "recepteur_pdb": PDB_OK,
                          "ligand_smiles": SMILES_OK})
    # 422 = refus explicite (moteur indisponible ou run() non implémenté).
    assert r.status_code == 422, r.text
    assert "score" in r.json()["detail"].lower()


def test_api_liste_jobs(client):
    r = client.get("/api/science/docking")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
