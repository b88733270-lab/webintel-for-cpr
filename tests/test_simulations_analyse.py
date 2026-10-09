"""Tests des plugins d'analyse : statistics et interaction_analysis.

Vérifie : respect du contrat plugin, déterminisme, exactitude des calculs,
gestion des cas limites, et — règle d'honnêteté scientifique — qu'aucun
lien n'est inféré par interaction_analysis.
"""

import json

import pytest

from webintel.simulations.plugins import check_plugin
from webintel.simulations.plugins import interaction_analysis
from webintel.simulations.plugins import statistics


# ---------------------------------------------------------------------------
# Contrat plugin
# ---------------------------------------------------------------------------


def test_contrat_statistics_conforme():
    assert check_plugin(statistics) == []


def test_contrat_interaction_analysis_conforme():
    assert check_plugin(interaction_analysis) == []


def test_is_available_ne_leve_jamais():
    for plugin in (statistics, interaction_analysis):
        disponible, message = plugin.is_available()
        assert disponible is True
        assert isinstance(message, str) and message.strip()


def test_limitations_mentionnent_les_garde_fous():
    texte = " ".join(statistics.LIMITATIONS).lower()
    assert "descriptiv" in texte  # « descriptives » : purement descriptif
    assert "causalité" in texte

    texte = " ".join(interaction_analysis.LIMITATIONS).lower()
    assert "n'infère aucun lien" in texte
    assert "probabilité d'interaction" in texte


# ---------------------------------------------------------------------------
# Déterminisme + sérialisabilité JSON
# ---------------------------------------------------------------------------


def _inputs_statistics():
    return {
        "observations": [
            {"valeur": 1.0, "unite": "mg/mL", "libelle": "rép. 1"},
            {"valeur": 2.0, "unite": "mg/mL", "libelle": "rép. 2"},
            {"valeur": 3.0, "unite": "mg/mL", "libelle": "rép. 3"},
            {"valeur": 4.0, "unite": "mg/mL", "libelle": "rép. 4"},
            {"valeur": 5.0, "unite": "mg/mL", "libelle": "rép. 5"},
        ]
    }


def _inputs_interaction():
    return {
        "liens": [
            {"compose_nom": "curcumine", "cible": "NF-kB", "type_lien": "inhibition",
             "confiance": 0.9, "reference": "PMID:111"},
            {"compose_id": "C2", "compose_nom": "quercétine", "cible": "NF-kB",
             "type_lien": "inhibition", "confiance": 0.8, "reference": "PMID:222"},
            {"compose_nom": "pipérine", "cible": "NF-kB", "type_lien": "modulation",
             "confiance": 0.4, "reference": "PMID:333"},
            {"compose_nom": "curcumine", "cible": "COX-2", "type_lien": "inhibition",
             "confiance": 0.7, "reference": "PMID:444"},
            {"compose_nom": "quercétine", "cible": "TNF-alpha", "type_lien": "inhibition",
             "confiance": 0.6, "reference": "PMID:555"},
        ]
    }


def test_determinisme_statistics():
    premier = json.dumps(statistics.run(_inputs_statistics(), {}), sort_keys=True)
    second = json.dumps(statistics.run(_inputs_statistics(), {}), sort_keys=True)
    assert premier == second


def test_determinisme_interaction_analysis():
    premier = json.dumps(interaction_analysis.run(_inputs_interaction(), {}), sort_keys=True)
    second = json.dumps(interaction_analysis.run(_inputs_interaction(), {}), sort_keys=True)
    assert premier == second


# ---------------------------------------------------------------------------
# statistics : exactitude
# ---------------------------------------------------------------------------


def test_statistics_valeurs_reference():
    resultat = statistics.run(_inputs_statistics(), {})
    assert resultat["n"]["valeur"] == 5
    assert resultat["moyenne"]["valeur"] == pytest.approx(3.0)
    assert resultat["mediane"]["valeur"] == pytest.approx(3.0)
    # écart-type échantillon (ddof=1) de [1,2,3,4,5] = sqrt(2.5) ≈ 1.5811
    assert resultat["ecart_type"]["valeur"] == pytest.approx(1.5811, abs=1e-4)
    assert resultat["min"]["valeur"] == pytest.approx(1.0)
    assert resultat["max"]["valeur"] == pytest.approx(5.0)
    # chaque valeur porte sa méthode
    for cle in ("n", "moyenne", "mediane", "ecart_type", "min", "max"):
        assert resultat[cle]["methode"].strip(), cle


def test_statistics_intervalle_confiance_approximation_normale():
    resultat = statistics.run(_inputs_statistics(), {})
    ic = resultat["intervalle_confiance_moyenne"]
    assert ic["calculable"] is True
    assert ic["borne_basse"] < 3.0 < ic["borne_haute"]
    assert ic["niveau_confiance"] == pytest.approx(0.95)
    assert "approximation normale" in ic["methode"]
    assert "z=1.96" in ic["methode"]


def test_statistics_n1_non_calculable():
    resultat = statistics.run(
        {"observations": [{"valeur": 42.0, "unite": "mg", "libelle": "unique"}]}, {}
    )
    assert resultat["n"]["valeur"] == 1
    assert resultat["moyenne"]["valeur"] == pytest.approx(42.0)
    assert resultat["mediane"]["valeur"] == pytest.approx(42.0)
    assert resultat["ecart_type"]["calculable"] is False
    assert "n=1" in resultat["ecart_type"]["raison"]
    ic = resultat["intervalle_confiance_moyenne"]
    assert ic["calculable"] is False
    assert "n=1" in ic["raison"]


def test_statistics_validate_rejette_valeur_non_numerique():
    with pytest.raises(ValueError, match="doit être un nombre"):
        statistics.validate_inputs(
            {"observations": [{"valeur": "beaucoup", "unite": "mg"}]}, {}
        )


def test_statistics_validate_rejette_niveau_confiance_hors_bornes():
    with pytest.raises(ValueError, match="niveau_confiance"):
        statistics.validate_inputs(_inputs_statistics(), {"niveau_confiance": 0.2})
    with pytest.raises(ValueError, match="niveau_confiance"):
        statistics.validate_inputs(_inputs_statistics(), {"niveau_confiance": 1.5})


def test_statistics_validate_rejette_liste_vide_et_trop_grande():
    with pytest.raises(ValueError, match="entre 1 et 5000"):
        statistics.validate_inputs({"observations": []}, {})
    with pytest.raises(ValueError, match="entre 1 et 5000"):
        statistics.validate_inputs(
            {"observations": [{"valeur": 1.0}] * 5001}, {}
        )


def test_statistics_niveau_confiance_personnalise():
    resultat = statistics.run(_inputs_statistics(), {"niveau_confiance": 0.99})
    ic = resultat["intervalle_confiance_moyenne"]
    assert ic["niveau_confiance"] == pytest.approx(0.99)
    assert "z=2.58" in ic["methode"]  # z(0.99) ≈ 2.576 → "2.58"


# ---------------------------------------------------------------------------
# interaction_analysis : exactitude descriptive, aucune inférence
# ---------------------------------------------------------------------------


def test_interaction_top_cibles():
    resultat = interaction_analysis.run(_inputs_interaction(), {})
    assert resultat["nb_composes_distincts"]["valeur"] == 3
    assert resultat["nb_cibles_distinctes"]["valeur"] == 3
    assert resultat["nb_liens_analyses"]["valeur"] == 5

    top = resultat["top_cibles"]
    assert top[0]["cible"] == "NF-kB"
    assert top[0]["nb_liens"] == 3
    assert top[0]["composes"] == ["curcumine", "pipérine", "quercétine"]
    # chaque sortie est étiquetée « descriptif des liens enregistrés »
    assert top[0]["nature"] == "descriptif des liens enregistrés"


def test_interaction_top_composes():
    resultat = interaction_analysis.run(_inputs_interaction(), {})
    top = resultat["top_composes"]
    assert [(e["compose"], e["nb_cibles"]) for e in top] == [
        ("curcumine", 2),
        ("quercétine", 2),
        ("pipérine", 1),
    ]
    assert all(e["nature"] == "descriptif des liens enregistrés" for e in top)


def test_interaction_distribution_confiance():
    resultat = interaction_analysis.run(_inputs_interaction(), {})
    tranches = {
        t["tranche"]: t["nb_liens"]
        for t in resultat["distribution_confiance"]["tranches"]
    }
    # 0.9 et 0.8 → [0.75-1.00] ; 0.7 et 0.6 → [0.50-0.75] ; 0.4 → [0.25-0.50]
    assert tranches == {
        "0,00 – 0,25": 0,
        "0,25 – 0,50": 1,
        "0,50 – 0,75": 2,
        "0,75 – 1,00": 2,
    }
    assert resultat["distribution_confiance"]["non_renseignee"]["nb_liens"] == 0


def test_interaction_aucun_lien_invente():
    """Règle d'honnêteté : toute paire en sortie existe dans les entrées."""
    entrees = _inputs_interaction()
    resultat = interaction_analysis.run(entrees, {})
    paires_entree = {
        (lien.get("compose_nom") or lien.get("compose_id"), lien["cible"])
        for lien in entrees["liens"]
    }
    paires_sortie = {
        (compose, cible)
        for compose, ligne in resultat["matrice_compose_cible"].items()
        for cible, cellule in ligne.items()
        if cellule["presence"]
    }
    assert paires_sortie, "la matrice doit contenir les liens fournis"
    assert paires_sortie <= paires_entree, "lien inféré détecté !"
    # cohérence avec les tops : mêmes paires, aucun ajout
    paires_tops = {
        (e["compose"], cible)
        for e in resultat["top_composes"]
        for cible in e["cibles"]
    }
    assert paires_tops == paires_sortie


def test_interaction_matrice_confiance_max():
    resultat = interaction_analysis.run(_inputs_interaction(), {})
    matrice = resultat["matrice_compose_cible"]
    assert matrice["curcumine"]["NF-kB"]["presence"] is True
    assert matrice["curcumine"]["NF-kB"]["confiance_max"] == pytest.approx(0.9)
    # paire non fournie : absente de la matrice (pas d'inférence)
    assert "TNF-alpha" not in matrice["curcumine"]


def test_interaction_seuil_confiance_filtre():
    resultat = interaction_analysis.run(_inputs_interaction(), {"seuil_confiance": 0.5})
    assert resultat["nb_liens_analyses"]["valeur"] == 4  # pipérine→NF-kB (0.4) exclu
    top = resultat["top_cibles"]
    assert top[0]["cible"] == "NF-kB"
    assert top[0]["nb_liens"] == 2
    assert "pipérine" not in top[0]["composes"]
    # aucune paire filtrée ne réapparaît en sortie
    paires = {
        (c, t)
        for c, ligne in resultat["matrice_compose_cible"].items()
        for t in ligne
    }
    assert ("pipérine", "NF-kB") not in paires


def test_interaction_liste_vide_zeros_propres():
    resultat = interaction_analysis.run({"liens": []}, {})
    assert resultat["nb_liens_analyses"]["valeur"] == 0
    assert resultat["nb_composes_distincts"]["valeur"] == 0
    assert resultat["nb_cibles_distinctes"]["valeur"] == 0
    assert resultat["top_cibles"] == []
    assert resultat["top_composes"] == []
    assert resultat["matrice_compose_cible"] == {}
    tranches = resultat["distribution_confiance"]["tranches"]
    assert all(t["nb_liens"] == 0 for t in tranches)
    assert resultat["distribution_confiance"]["non_renseignee"]["nb_liens"] == 0


def test_interaction_validate_rejette_entrees_invalides():
    with pytest.raises(ValueError, match="'cible'"):
        interaction_analysis.validate_inputs(
            {"liens": [{"compose_nom": "curcumine"}]}, {}
        )
    with pytest.raises(ValueError, match="compose_nom.*compose_id"):
        interaction_analysis.validate_inputs(
            {"liens": [{"cible": "NF-kB"}]}, {}
        )
    with pytest.raises(ValueError, match="'confiance'"):
        interaction_analysis.validate_inputs(
            {"liens": [{"compose_nom": "curcumine", "cible": "NF-kB",
                       "confiance": 1.5}]},
            {},
        )
    with pytest.raises(ValueError, match="seuil_confiance"):
        interaction_analysis.validate_inputs({"liens": []}, {"seuil_confiance": 2.0})
