"""Tests du socle simulations : contrat plugin, registre, rapports.

Ces tests ne touchent à aucun autre fichier du projet ; ils valident
uniquement le package ``webintel.simulations``.
"""

import sys
import types

import pytest

from webintel.simulations.plugins import check_plugin
import webintel.simulations.registry as registre
from webintel.simulations.registry import ENGINE_MODULES, get_engine, list_engines
from webintel.simulations.reports import (
    AVERTISSEMENT,
    SECTIONS,
    build_report,
    render_html,
)


def _fabrique_plugin_valide() -> types.ModuleType:
    module = types.ModuleType("faux_moteur_valide")
    module.ENGINE_NAME = "faux_moteur"
    module.ENGINE_VERSION = "0.1.0"
    module.DESCRIPTION = "Moteur factice pour les tests du contrat."
    module.INPUTS = {"composes": "Liste des composés à analyser."}
    module.PARAMETERS = {
        "seuil": {
            "type": "float",
            "default": 0.5,
            "min": 0.0,
            "max": 1.0,
            "description": "Seuil de similarité.",
        }
    }
    module.LIMITATIONS = ["Moteur factice : aucun calcul réel."]
    module.is_available = lambda: (True, "")
    module.validate_inputs = lambda inputs, params: {
        "inputs": inputs,
        "params": params,
    }
    module.run = lambda inputs, params: {"score": 1.0}
    return module


def _fabrique_plugin_invalide() -> types.ModuleType:
    module = types.ModuleType("faux_moteur_invalide")
    # ENGINE_NAME manquant, DESCRIPTION vide, INPUTS absent,
    # PARAMETERS mal spécifié, LIMITATIONS vide, run manquant,
    # is_available lève au lieu de retourner (bool, str).
    module.ENGINE_VERSION = "x"
    module.DESCRIPTION = "   "
    module.PARAMETERS = {"seuil": {"type": "pas_un_type"}}
    module.LIMITATIONS = []
    module.is_available = lambda: 1 / 0
    module.validate_inputs = lambda inputs, params: {}
    return module


def test_check_plugin_valide():
    assert check_plugin(_fabrique_plugin_valide()) == []


def test_check_plugin_invalide_signale_les_problemes():
    problemes = check_plugin(_fabrique_plugin_invalide())
    assert problemes, "Un plugin invalide doit produire des problèmes."
    texte = " ".join(problemes)
    assert "ENGINE_NAME" in texte
    assert "INPUTS" in texte
    assert "Paramètre" in texte
    assert "LIMITATIONS" in texte
    assert "'run'" in texte
    assert "is_available" in texte


def test_check_plugin_objet_non_module():
    assert check_plugin(object()) == ["Le plugin doit être un module Python."]


def test_list_engines_modules_manquants_sans_exception(monkeypatch):
    # On force la présence d'un module inexistant : il doit apparaître
    # comme indisponible avec une raison, sans qu'aucune exception ne soit levée.
    faux_chemin = "webintel.simulations.plugins.module_inexistant_xyz"
    monkeypatch.setattr(registre, "ENGINE_MODULES", ENGINE_MODULES + [faux_chemin])
    moteurs = list_engines()
    assert len(moteurs) == len(ENGINE_MODULES) + 1
    moteur_manquant = moteurs[-1]
    assert moteur_manquant["nom"] == "module_inexistant_xyz"
    assert moteur_manquant["disponible"] is False
    assert moteur_manquant["raison"], "Une raison doit expliquer l'indisponibilité."
    assert isinstance(moteur_manquant["entrees"], dict)
    assert isinstance(moteur_manquant["parametres"], dict)
    assert isinstance(moteur_manquant["limitations"], list)


def test_get_engine_inconnu_leve_keyerror():
    with pytest.raises(KeyError):
        get_engine("moteur_qui_n_existe_pas")


def test_get_engine_connu_mais_manquant_leve_valueerror(monkeypatch):
    # Un module attendu par ENGINE_MODULES mais absent : ValueError explicite.
    faux_chemin = "webintel.simulations.plugins.module_inexistant_xyz"
    monkeypatch.setattr(registre, "ENGINE_MODULES", ENGINE_MODULES + [faux_chemin])
    assert faux_chemin not in sys.modules
    with pytest.raises(ValueError) as exc_info:
        get_engine("module_inexistant_xyz")
    assert "indisponible" in str(exc_info.value)


def test_build_report_six_sections_et_avertissement():
    moteur = _fabrique_plugin_valide()
    rapport = build_report(
        moteur,
        {"composes": ["mol1"]},
        {"seuil": 0.5},
        {"score": 1.0},
        references=["Doe et al., 2024"],
    )
    for section in SECTIONS:
        assert section in rapport, f"Section {section} manquante."
    assert rapport["engine_name"] == "faux_moteur"
    assert rapport["engine_version"] == "0.1.0"
    assert rapport["avertissement"] == AVERTISSEMENT
    assert "Simulation — à confirmer expérimentalement" in rapport["avertissement"]
    assert rapport["genere_le"]
    assert len(rapport["input_hash"]) == 64
    assert rapport["REFERENCES"] == ["Doe et al., 2024"]
    assert rapport["LIMITATIONS"] == ["Moteur factice : aucun calcul réel."]


def test_build_report_input_hash_deterministe():
    moteur = _fabrique_plugin_valide()
    kwargs = dict(
        inputs={"composes": ["mol1", "mol2"]},
        params={"seuil": 0.7},
        outputs={"score": 0.42},
    )
    h1 = build_report(moteur, **kwargs)["input_hash"]
    h2 = build_report(moteur, **kwargs)["input_hash"]
    assert h1 == h2
    # Des entrées différentes donnent un hash différent.
    h3 = build_report(moteur, inputs={"composes": ["mol3"]},
                      params={"seuil": 0.7}, outputs={"score": 0.42})["input_hash"]
    assert h3 != h1


def test_render_html_echappe_le_html_injecte():
    moteur = _fabrique_plugin_valide()
    rapport = build_report(
        moteur,
        {"composes": ["<script>alert('xss')</script>"]},
        {"seuil": 0.5},
        {"score": "<img src=x onerror=alert(1)>"},
    )
    page = render_html(rapport)
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
    assert "&lt;img" in page
    for section in SECTIONS:
        assert f"<h2>{section}</h2>" in page
    assert "Simulation — à confirmer expérimentalement" in page
