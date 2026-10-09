"""Tests v2.1 — analyses de synergie (MODE B).

Couvre : Bliss / HSA / Loewe / ZIP sur cas connus (formules vérifiées),
refus « données insuffisantes », garde-fou anti-structures-seules,
ajustement de Hill, reproductibilité, séparation labo/public, API.
"""

from __future__ import annotations

import pytest

from webintel.database import models as modeles_noyau
from webintel.science import combinaisons as comb
from webintel.science.synergie import (
    DonneesInsuffisantes,
    analyser_synergie,
    bliss,
    hsa,
    loewe,
    zip as zip_mod,
)
from webintel.science.synergie.commun import ajuster_hill, hill


@pytest.fixture()
def projet_id(db_session):
    projet = modeles_noyau.Project(name="Projet Synergie")
    db_session.add(projet)
    db_session.commit()
    return projet.id


# ---------------------------------------------------------------------------
# Bliss — cas connu
# ---------------------------------------------------------------------------

def test_bliss_cas_connu():
    # E_A = 0.5, E_B = 0.5 → E_bliss = 0.75 ; obs = 0.9 → Δ = 0.15.
    res = bliss.analyser({
        "points": [
            {"dose_a": 10, "dose_b": 20, "reponse_a": 0.5,
             "reponse_b": 0.5, "reponse_ab": 0.9},
        ]
    })
    pt = res["calcul"]["points"][0]
    assert pt["reponse_attendue_bliss"] == pytest.approx(0.75)
    assert pt["delta"] == pytest.approx(0.15)
    assert res["calcul"]["delta_moyen"] == pytest.approx(0.15)
    assert "<svg" in res["graphe_svg"]
    assert "E_bliss" in res["formule"]
    # Interprétation prudente : pas de formulation interdite.
    assert comb.verifier_honnetete(res["interpretation"]) == []
    for limite in res["limites"]:
        assert comb.verifier_honnetete(limite) == []


def test_bliss_pourcentages():
    res = bliss.analyser({
        "points": [
            {"dose_a": 10, "dose_b": 20, "reponse_a": 50,
             "reponse_b": 50, "reponse_ab": 90},
        ]
    })
    assert res["calcul"]["points"][0]["delta"] == pytest.approx(0.15)


def test_bliss_donnees_insuffisantes():
    with pytest.raises(DonneesInsuffisantes) as exc:
        bliss.analyser({"points": [
            {"dose_a": 10, "dose_b": 20, "reponse_a": 0.5,
             "reponse_ab": 0.9},  # reponse_b manquante
        ]})
    assert "données insuffisantes pour la méthode Bliss" in str(exc.value)
    with pytest.raises(DonneesInsuffisantes):
        bliss.analyser({"points": []})
    with pytest.raises(DonneesInsuffisantes):
        bliss.analyser({})


# ---------------------------------------------------------------------------
# HSA — cas connu
# ---------------------------------------------------------------------------

def test_hsa_cas_connu():
    # max(0.3, 0.6) = 0.6 ; obs = 0.7 → Δ = 0.1.
    res = hsa.analyser({
        "points": [
            {"dose_a": 5, "dose_b": 5, "reponse_a": 0.3,
             "reponse_b": 0.6, "reponse_ab": 0.7},
        ]
    })
    pt = res["calcul"]["points"][0]
    assert pt["reponse_attendue_hsa"] == pytest.approx(0.6)
    assert pt["delta"] == pytest.approx(0.1)


def test_hsa_donnees_insuffisantes():
    with pytest.raises(DonneesInsuffisantes) as exc:
        hsa.analyser({"points": [{"dose_a": 1, "dose_b": 1}]})
    assert "données insuffisantes pour la méthode HSA" in str(exc.value)


# ---------------------------------------------------------------------------
# Loewe — cas connus (paramètres fournis → CI exact)
# ---------------------------------------------------------------------------

def _courbes(ic50_a=10.0, pente_a=1.0, ic50_b=20.0, pente_b=1.0):
    return {
        "courbe_a_parametres": {"ic50": ic50_a, "pente": pente_a},
        "courbe_b_parametres": {"ic50": ic50_b, "pente": pente_b},
    }


def test_loewe_additivite_ci_1():
    # y=0.5 → D_A=10, D_B=20 ; mélange (5, 10) → CI = 0.5+0.5 = 1.
    donnees = _courbes()
    donnees["melanges"] = [{"dose_a": 5, "dose_b": 10, "reponse": 0.5}]
    res = loewe.analyser(donnees)
    pt = res["calcul"]["points"][0]
    assert pt["ci"] == pytest.approx(1.0)
    assert pt["signal"] == "additivité (selon Loewe)"


def test_loewe_synergie_ci_inferieur_1():
    # Même effet 0.5 avec 2× moins de chaque → CI = 0.5 → synergie.
    donnees = _courbes()
    donnees["melanges"] = [{"dose_a": 2.5, "dose_b": 5, "reponse": 0.5}]
    res = loewe.analyser(donnees)
    assert res["calcul"]["points"][0]["ci"] == pytest.approx(0.5)
    assert res["calcul"]["points"][0]["signal"] == "synergie (selon Loewe)"


def test_loewe_donnees_insuffisantes():
    with pytest.raises(DonneesInsuffisantes) as exc:
        loewe.analyser({"melanges": [{"dose_a": 1, "dose_b": 1,
                                      "reponse": 0.5}]})
    assert "données insuffisantes pour la méthode Loewe" in str(exc.value)


def test_loewe_ignore_points_satures():
    donnees = _courbes()
    donnees["melanges"] = [
        {"dose_a": 5, "dose_b": 10, "reponse": 0.5},
        {"dose_a": 100, "dose_b": 100, "reponse": 1.0},  # saturé → ignoré
    ]
    res = loewe.analyser(donnees)
    assert len(res["calcul"]["points"]) == 1
    assert len(res["calcul"]["points_ignores"]) == 1


# ---------------------------------------------------------------------------
# ZIP — cas connu (Yadav et al. 2015, éq. 16)
# ---------------------------------------------------------------------------

def test_zip_cas_connu():
    # f_A(10)=0.5 (ic50=10, pente=2), f_B(30)=0.5 (ic50=30, pente=1)
    # E_zip = 0.5+0.5-0.25 = 0.75 ; obs=0.9 → Δ=0.15.
    donnees = {
        "courbe_a_parametres": {"ic50": 10.0, "pente": 2.0},
        "courbe_b_parametres": {"ic50": 30.0, "pente": 1.0},
        "melanges": [{"dose_a": 10, "dose_b": 30, "reponse": 0.9}],
    }
    res = zip_mod.analyser(donnees)
    pt = res["calcul"]["points"][0]
    assert pt["f_a_ajustee"] == pytest.approx(0.5)
    assert pt["f_b_ajustee"] == pytest.approx(0.5)
    assert pt["reponse_attendue_zip"] == pytest.approx(0.75)
    assert pt["delta"] == pytest.approx(0.15)


def test_zip_donnees_insuffisantes():
    with pytest.raises(DonneesInsuffisantes) as exc:
        zip_mod.analyser({"courbe_a_parametres": {"ic50": 1, "pente": 1}})
    assert "données insuffisantes pour la méthode ZIP" in str(exc.value)


# ---------------------------------------------------------------------------
# Ajustement de Hill (déterministe)
# ---------------------------------------------------------------------------

def test_ajustement_hill_retrouve_parametres():
    doses = [1, 3, 10, 30, 100]
    reponses = [hill(d, 10.0, 1.5) for d in doses]
    ajuste = ajuster_hill(doses, reponses)
    assert ajuste["ic50"] == pytest.approx(10.0, rel=0.3)
    assert ajuste["pente"] == pytest.approx(1.5, rel=0.3)
    assert ajuste["r2"] > 0.99


def test_ajustement_hill_donnees_insuffisantes():
    with pytest.raises(DonneesInsuffisantes):
        ajuster_hill([1, 2, 3], [0.1, 0.2, 0.3])


def test_ajustement_hill_deterministe():
    doses = [1, 3, 10, 30, 100]
    reponses = [hill(d, 5.0, 2.0) for d in doses]
    assert ajuster_hill(doses, reponses) == ajuster_hill(doses, reponses)


# ---------------------------------------------------------------------------
# Garde-fous du répartiteur
# ---------------------------------------------------------------------------

def test_methode_inconnue():
    with pytest.raises(ValueError, match="inconnue"):
        analyser_synergie("foobar", {"points": []})


def test_garde_fou_structures_seules():
    """JAMAIS de synergie calculée depuis de simples structures."""
    with pytest.raises(DonneesInsuffisantes) as exc:
        analyser_synergie("bliss", {"smiles_a": "CCO", "smiles_b": "CCC"})
    assert "structures moléculaires" in str(exc.value)
    with pytest.raises(DonneesInsuffisantes):
        analyser_synergie("zip", {"nom_a": "A", "nom_b": "B"})


# ---------------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------------

def test_persistance_analyse(db_session, projet_id):
    analyse = comb.creer_analyse_synergie(
        db_session, project_id=projet_id, nom="Bliss 1", methode="bliss",
        donnees={"points": [
            {"dose_a": 10, "dose_b": 20, "reponse_a": 0.5,
             "reponse_b": 0.5, "reponse_ab": 0.9}]},
        cree_par="local",
    )
    db_session.commit()
    assert analyse.methode == "bliss"
    assert analyse.data_level == "prediction"
    assert analyse.is_private is True  # labo privé par défaut
    assert len(analyse.inputs_hash) == 64
    assert analyse.resultats["calcul"]["delta_moyen"] == pytest.approx(0.15)
    assert analyse.provenance["formule"] == bliss.FORMULE


def test_persistance_refuse_sans_donnees(db_session, projet_id):
    with pytest.raises(DonneesInsuffisantes):
        comb.creer_analyse_synergie(
            db_session, project_id=projet_id, nom="X", methode="bliss",
            donnees={},
        )
    db_session.rollback()
    assert comb.list_analyses_synergie(db_session, projet_id,
                                       inclure_prive=True) == []


def test_separation_labo_public(db_session, projet_id):
    donnees = {"points": [{"dose_a": 1, "dose_b": 1, "reponse_a": 0.2,
                           "reponse_b": 0.2, "reponse_ab": 0.5}]}
    comb.creer_analyse_synergie(
        db_session, project_id=projet_id, nom="Privée", methode="hsa",
        donnees=donnees, is_private=True)
    comb.creer_analyse_synergie(
        db_session, project_id=projet_id, nom="Publique", methode="hsa",
        donnees=donnees, is_private=False)
    db_session.commit()
    assert len(comb.list_analyses_synergie(db_session, projet_id)) == 1
    assert len(comb.list_analyses_synergie(db_session, projet_id,
                                           inclure_prive=True)) == 2


def test_reproductibilite_synergie(db_session, projet_id):
    donnees = {"points": [{"dose_a": 10, "dose_b": 20, "reponse_a": 0.5,
                           "reponse_b": 0.5, "reponse_ab": 0.9}]}
    a1 = comb.creer_analyse_synergie(
        db_session, project_id=projet_id, nom="S1", methode="bliss",
        donnees=donnees)
    a2 = comb.creer_analyse_synergie(
        db_session, project_id=projet_id, nom="S2", methode="bliss",
        donnees=donnees)
    db_session.commit()
    assert a1.inputs_hash == a2.inputs_hash
    assert a1.resultats == a2.resultats


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_methodes(client):
    r = client.get("/api/science/synergie/methodes")
    assert r.status_code == 200
    noms = {m["nom"] for m in r.json()["methodes"]}
    assert noms == {"bliss", "loewe", "hsa", "zip"}
    for m in r.json()["methodes"]:
        assert m["formule"] and m["donnees_requises"]


def test_api_analyser_bliss(client):
    r = client.post("/api/science/synergie/analyser", json={
        "nom": "Test Bliss", "methode": "bliss",
        "donnees": {"points": [
            {"dose_a": 10, "dose_b": 20, "reponse_a": 0.5,
             "reponse_b": 0.5, "reponse_ab": 0.9}]},
    })
    assert r.status_code == 201, r.text
    analyse_id = r.json()["id"]
    r = client.get(f"/api/science/synergie/{analyse_id}")
    assert r.status_code == 200
    corps = r.json()
    assert corps["resultat"]["calcul"]["delta_moyen"] == pytest.approx(0.15)
    assert "<svg" in corps["resultat"]["graphe_svg"]
    assert comb.verifier_honnetete(
        corps["resultat"]["interpretation"]) == []


def test_api_422_donnees_insuffisantes(client):
    r = client.post("/api/science/synergie/analyser", json={
        "nom": "X", "methode": "loewe", "donnees": {},
    })
    assert r.status_code == 422
    assert "données insuffisantes pour la méthode loewe" in r.json()["detail"]


def test_api_404_methode_inconnue(client):
    r = client.post("/api/science/synergie/analyser", json={
        "nom": "X", "methode": "inexistante", "donnees": {},
    })
    assert r.status_code == 404


def test_api_liste_exclut_prive_par_defaut(client):
    donnees = {"points": [{"dose_a": 1, "dose_b": 1, "reponse_a": 0.2,
                           "reponse_b": 0.2, "reponse_ab": 0.5}]}
    r = client.post("/api/science/synergie/analyser", json={
        "nom": "Priv", "methode": "hsa", "donnees": donnees,
        "is_private": True})
    assert r.status_code == 201
    r = client.get("/api/science/synergie")
    assert r.status_code == 200
    assert r.json() == []
    r = client.get("/api/science/synergie?inclure_prive=true")
    assert len(r.json()) == 1
