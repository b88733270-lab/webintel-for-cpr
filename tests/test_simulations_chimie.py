"""Tests des plugins de chimie : similarité moléculaire et propriétés.

Règle dure : une simulation n'est JAMAIS présentée comme une preuve
expérimentale ; ces tests verrouillent l'honnêteté des moteurs (aucune
valeur inventée, méthodes étiquetées, déterminisme strict).
"""

import json

import pytest

from webintel.simulations.plugins import check_plugin
from webintel.simulations.plugins import molecular_similarity as sim
from webintel.simulations.plugins import property_prediction as prop


# --- Contrat plugin -----------------------------------------------------------

def test_contrat_molecular_similarity():
    assert check_plugin(sim) == []


def test_contrat_property_prediction():
    assert check_plugin(prop) == []


def test_is_available_moteurs():
    for moteur in (sim, prop):
        disponible, message = moteur.is_available()
        assert disponible is True
        assert isinstance(message, str)


# --- Déterminisme -------------------------------------------------------------

def _entrees_sim():
    return {
        "composes": [
            {"id": "eau", "formule": "H2O", "inchikey": "XLYOFNOQVPJJNP-UHFFFAOYSA-N"},
            {"id": "glucose", "formule": "C6H12O6", "classe_chimique": "ose"},
            {"id": "fructose", "formule": "C6H12O6", "classe_chimique": "ose"},
        ]
    }


def test_determinisme_similarite():
    r1 = sim.run(_entrees_sim(), {})
    r2 = sim.run(_entrees_sim(), {})
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_determinisme_proprietes():
    entrees = {"composes": [{"id": "eau", "formule": "H2O"}]}
    r1 = prop.run(entrees, {})
    r2 = prop.run(entrees, {})
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


# --- Similarité moléculaire ---------------------------------------------------

def _paire(resultat, a, b):
    for p in resultat["paires"]:
        if {p["a"], p["b"]} == {a, b}:
            return p
    raise AssertionError(f"Paire {(a, b)} introuvable")


def test_similarite_formule_identique_vaut_un():
    resultat = sim.run(_entrees_sim(), {})
    paire = _paire(resultat, "glucose", "fructose")
    assert paire["details"]["formule"]["score"] == pytest.approx(1.0)
    assert "methode" in paire["details"]["formule"]


def test_inchikey_meme_premier_bloc_vaut_un():
    entrees = {
        "composes": [
            {"id": "a", "formule": "H2O", "inchikey": "XLYOFNOQVPJJNP-UHFFFAOYSA-N"},
            {"id": "b", "formule": "H2O2", "inchikey": "XLYOFNOQVPJJNP-UHFFFAOYSA-N"},
        ]
    }
    paire = _paire(sim.run(entrees, {}), "a", "b")
    assert paire["details"]["inchikey"]["score"] == 1.0
    assert "methode" in paire["details"]["inchikey"]


def test_inchikey_blocs_differents_vaut_zero():
    entrees = {
        "composes": [
            {"id": "a", "formule": "H2O", "inchikey": "XLYOFNOQVPJJNP-UHFFFAOYSA-N"},
            {"id": "b", "formule": "H2O", "inchikey": "AAAAAAAAAAAAAA-UHFFFAOYSA-N"},
        ]
    }
    paire = _paire(sim.run(entrees, {}), "a", "b")
    assert paire["details"]["inchikey"]["score"] == 0.0


def test_paires_triees_score_decroissant():
    # Méthode explicite : la méthode par défaut est désormais « structure »
    # (Tanimoto de Morgan — None sans SMILES) ; le tri est testé ici sur
    # l'heuristique « combinee », toujours disponible.
    resultat = sim.run(_entrees_sim(), {"methode": "combinee"})
    scores = [p["score"] for p in resultat["paires"]]
    assert scores == sorted(scores, reverse=True)


def test_methode_parametre_formule():
    resultat = sim.run(_entrees_sim(), {"methode": "formule"})
    paire = _paire(resultat, "glucose", "fructose")
    assert paire["score"] == pytest.approx(1.0)


# --- Prédiction de propriétés --------------------------------------------------

def _fiche(resultat, nom):
    for c in resultat["composes"]:
        if c["nom"] == nom:
            return c
    raise AssertionError(f"Composé {nom!r} introuvable")


def test_masse_molaire_eau():
    fiche = _fiche(prop.run({"composes": [{"id": "eau", "formule": "H2O"}]}, {}), "eau")
    masse = fiche["masse_molaire"]
    assert masse["valeur"] == pytest.approx(18.015, abs=0.01)
    assert masse["unite"] == "g/mol"
    assert "methode" in masse and masse["methode"]


def test_masse_molaire_glucose():
    fiche = _fiche(
        prop.run({"composes": [{"id": "glucose", "formule": "C6H12O6"}]}, {}),
        "glucose",
    )
    assert fiche["masse_molaire"]["valeur"] == pytest.approx(180.156, abs=0.01)
    assert fiche["atomes"]["total"] == 24
    assert fiche["atomes"]["lourds"] == 12
    assert fiche["atomes"]["par_element"] == {"C": 6, "H": 12, "O": 6}


def test_proprietes_sans_rdkit_non_calculees_jamais_inventees():
    fiche = _fiche(prop.run({"composes": [{"id": "eau", "formule": "H2O"}]}, {}), "eau")
    for cle in ("logp", "donneurs_hydrogene", "accepteurs_hydrogene", "tpsa"):
        bloc = fiche[cle]
        assert bloc["statut"] == "non_calcule"
        assert "RDKit" in bloc["raison"]
        # Aucune valeur inventée : pas de clé 'valeur' renseignée.
        assert bloc.get("valeur") is None


def test_lipinski_partiel():
    fiche = _fiche(prop.run({"composes": [{"id": "eau", "formule": "H2O"}]}, {}), "eau")
    lipinski = fiche["lipinski"]
    assert lipinski["verdict"] == "partiel"
    assert lipinski["details"]["masse_molaire"]["statut"] == "evalue"
    assert lipinski["details"]["masse_molaire"]["respecte"] is True
    for cle in ("logp", "donneurs_hydrogene", "accepteurs_hydrogene"):
        assert lipinski["details"][cle]["statut"] == "non_evaluable_sans_rdkit"


# --- Validation des entrées ---------------------------------------------------

@pytest.mark.parametrize("moteur", [sim, prop])
def test_validate_inputs_rejette_formule_invalide(moteur):
    with pytest.raises(ValueError):
        moteur.validate_inputs(
            {"composes": [{"id": "x", "formule": "C6H12O6"}, {"id": "y", "formule": "Xx9("}]},
            {},
        )


@pytest.mark.parametrize("moteur", [sim, prop])
def test_validate_inputs_rejette_plus_de_200_composes(moteur):
    composes = [{"id": f"c{i}", "formule": "H2O"} for i in range(201)]
    with pytest.raises(ValueError):
        moteur.validate_inputs({"composes": composes}, {})


def test_validate_inputs_similarite_minimum_2_composes():
    with pytest.raises(ValueError):
        sim.validate_inputs({"composes": [{"id": "seul", "formule": "H2O"}]}, {})


def test_validate_inputs_rejette_methode_inconnue():
    with pytest.raises(ValueError):
        sim.validate_inputs(_entrees_sim(), {"methode": "rdkit"})


def test_validate_inputs_format_contrat():
    valide = sim.validate_inputs(_entrees_sim(), {})
    assert set(valide.keys()) == {"inputs", "params"}
    # Défaut honnête : « structure » (Tanimoto de Morgan via RDKit).
    assert valide["params"] == {"methode": "structure"}
