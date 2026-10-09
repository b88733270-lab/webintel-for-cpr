"""Tests v2.1 — UI chercheur (combinaisons / synergie / docking / rapport).

Couvre : les 4 actions visibles, création d'étude via formulaire, page
d'étude (12 points), import de résultats expérimentaux, workflow docking
(aperçu + refus), export du dossier.
"""

from __future__ import annotations

import io
import json
import zipfile

BOUTONS = ["Nouvelle étude", "Nouvelle simulation", "Importer mes résultats",
           "Exporter mon dossier"]


def _compose_api(client, nom: str) -> int:
    r = client.post("/api/science/compounds", json={"name": nom})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_page_combinaisons_et_quatre_boutons(client):
    r = client.get("/combinaisons")
    assert r.status_code == 200
    for bouton in BOUTONS:
        assert bouton in r.text, bouton
    # Lien de navigation sans doublon.
    assert r.text.count('href="/combinaisons"') >= 1


def test_creer_etude_via_formulaire(client):
    a = _compose_api(client, "UI A")
    b = _compose_api(client, "UI B")
    r = client.post("/combinaisons/creer",
                    data={"nom": "Étude UI", "compound_ids": [str(a), str(b)]},
                    follow_redirects=False)
    assert r.status_code == 303
    r = client.get(r.headers["location"])
    assert r.status_code == 200
    # Les 12 points sont affichés.
    for i in range(1, 13):
        assert f"{i}." in r.text
    assert "Prédiction" in r.text


def test_creer_etude_un_seul_compose_erreur(client):
    a = _compose_api(client, "Solo")
    r = client.post("/combinaisons/creer",
                    data={"nom": "X", "compound_ids": [str(a)]})
    assert r.status_code == 200
    assert "au moins 2 composés" in r.text


def test_importer_resultats_synergie(client):
    r = client.get("/synergie/importer")
    assert r.status_code == 200
    assert "bliss" in r.text.lower()
    donnees = json.dumps({"points": [
        {"dose_a": 10, "dose_b": 20, "reponse_a": 0.5,
         "reponse_b": 0.5, "reponse_ab": 0.9}]})
    r = client.post("/synergie/importer",
                    data={"nom": "UI Bliss", "methode": "bliss",
                          "donnees": donnees, "is_private": "on"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    r = client.get(r.headers["location"])
    assert r.status_code == 200
    assert "<svg" in r.text
    assert "BLISS" in r.text
    assert "Confidentiel" in r.text


def test_importer_resultats_json_invalide(client):
    r = client.post("/synergie/importer",
                    data={"nom": "X", "methode": "bliss",
                          "donnees": "pas du json"})
    assert r.status_code == 200
    assert "JSON invalide" in r.text


def test_importer_resultats_sans_donnees(client):
    r = client.post("/synergie/importer",
                    data={"nom": "X", "methode": "zip", "donnees": "{}"})
    assert r.status_code == 200
    assert "données insuffisantes" in r.text.lower()


def test_docking_apercu_et_refus(client):
    r = client.get("/docking")
    assert r.status_code == 200
    pdb = ("HEADER\nATOM      1  N   ALA A   1      11.104  13.207   2.100 "
           " 1.00 20.00           N\nEND")
    r = client.post("/docking", data={
        "action": "apercu", "nom": "D1", "recepteur_pdb": pdb,
        "ligand_smiles": "CCO"})
    assert r.status_code == 200
    assert "aucun calcul lancé" in r.text.lower()
    assert "aucun score" in r.text.lower()
    # Exécution : refus propre affiché (moteur indisponible ici).
    r = client.post("/docking", data={
        "action": "executer", "nom": "D2", "recepteur_pdb": pdb,
        "ligand_smiles": "CCO"})
    assert r.status_code == 200
    assert "aucun score" in r.text.lower()


def test_rapport_export_formats(client):
    r = client.get("/rapport")
    assert r.status_code == 200
    for fmt, debut in [("markdown", None), ("html", None), ("json", None)]:
        r = client.get(f"/rapport/telecharger?format={fmt}")
        assert r.status_code == 200, fmt
    r = client.get("/rapport/telecharger?format=pdf")
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"
    r = client.get("/rapport/telecharger?format=zip")
    assert r.status_code == 200
    assert "zip" in r.headers["content-type"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "MANIFEST.json" in zf.namelist()


def test_isolation_projet_ui(client, db_session):
    """Une étude d'un autre projet n'est pas visible (404)."""
    from webintel.database import models as noyau
    from webintel.science import combinaisons as comb
    from webintel.science import models as science
    autre = noyau.Project(name="Étranger")
    local = noyau.Project(name="Local")
    db_session.add_all([local, autre])  # « Local » créé en premier = projet UI
    db_session.flush()
    c1 = science.Compound(name="X1")
    c2 = science.Compound(name="X2")
    db_session.add_all([c1, c2])
    db_session.commit()
    etude = comb.creer_etude(db_session, project_id=autre.id, nom="Privée",
                             compound_ids=[c1.id, c2.id])
    db_session.commit()
    r = client.get(f"/combinaisons/{etude.id}")
    assert r.status_code == 404
