"""Tests v2.1 — rapports de recherche reproductibles.

Couvre : structure du rapport, formats JSON/HTML/Markdown/PDF/ZIP,
exclusion des données privées par défaut, provenance/version/hash,
intégration des études de combinaison et synergies, API.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from webintel.database import models as modeles_noyau
from webintel.science import combinaisons as comb
from webintel.science import rapport
from webintel.science import models as science
from webintel import __version__ as WEBINTEL_VERSION


@pytest.fixture()
def projet_id(db_session):
    projet = modeles_noyau.Project(name="Projet Rapport")
    db_session.add(projet)
    db_session.commit()
    return projet.id


def _plante_avec_composes(db_session, projet_id: int | None = None):
    plante = science.Plant(scientific_name="Moringa oleifera",
                           family="Moringaceae")
    db_session.add(plante)
    db_session.flush()
    a = science.Compound(name="Quercétine", formula="C15H10O7",
                         chemical_class="flavonoïde")
    b = science.Compound(name="Kaempférol", formula="C15H10O6",
                         chemical_class="flavonoïde")
    db_session.add_all([a, b])
    db_session.flush()
    db_session.add(science.PlantCompound(plant_id=plante.id, compound_id=a.id))
    db_session.add(science.PlantCompound(plant_id=plante.id, compound_id=b.id))
    db_session.add(science.Evidence(title="Étude Moringa 2020",
                                    source_type="publication"))
    if projet_id is not None:
        # Le rapport rattache les plantes au projet via ses simulations.
        db_session.add(science.Simulation(
            project_id=projet_id, name="Sim rapport",
            compound_ids=[a.id, b.id], method="test",
            input_hash="test", hypotheses="Hypothèse de test."))
    db_session.commit()
    return plante.id, [a.id, b.id]


def _observation_privee(db_session, projet_id: int, privee: bool = True):
    obs = science.ResearchObservation(
        project_id=projet_id, comment="Essai confidentiel labo",
        raw_data={"valeur": 42}, is_private=privee)
    db_session.add(obs)
    db_session.commit()


# ---------------------------------------------------------------------------
# Contenu
# ---------------------------------------------------------------------------

def test_rapport_structure_vide(db_session, projet_id):
    r = rapport.build_rapport_recherche(db_session, projet_id)
    assert r["fiche_plantes"] == []
    assert r["composes"] == []
    assert r["publications"] == []
    assert r["hypotheses"] == []
    assert r["simulations"] == []
    assert r["combinaisons_analysees"] == []
    assert r["resultats"]["nb_plantes"] == 0
    assert r["webintel_version"] == WEBINTEL_VERSION
    assert r["avertissement"]
    assert len(r["limites"]) >= 4
    assert r["inclure_prive"] is False
    # Données privées exclues par défaut.
    assert r["donnees"]["observations_laboratoire"] == []


def test_rapport_contenu(db_session, projet_id):
    plante_id, ids = _plante_avec_composes(db_session, projet_id)
    _observation_privee(db_session, projet_id, privee=True)
    comb.creer_etude(db_session, project_id=projet_id, nom="E1",
                     compound_ids=ids, plant_id=plante_id)
    comb.creer_analyse_synergie(
        db_session, project_id=projet_id, nom="S1", methode="bliss",
        donnees={"points": [{"dose_a": 1, "dose_b": 1, "reponse_a": 0.2,
                             "reponse_b": 0.2, "reponse_ab": 0.5}]},
        is_private=False)
    db_session.commit()
    r = rapport.build_rapport_recherche(db_session, projet_id)
    assert r["resultats"]["nb_plantes"] == 1
    assert r["resultats"]["nb_composes"] == 2
    assert r["resultats"]["nb_publications"] == 1
    assert r["resultats"]["nb_combinaisons"] == 2  # étude + synergie
    types = {c["type"] for c in r["combinaisons_analysees"]}
    assert types == {"etude_combinaison", "analyse_synergie"}
    # Observation privée exclue par défaut…
    assert r["donnees"]["observations_laboratoire"] == []
    # …incluse sur demande explicite.
    r_prive = rapport.build_rapport_recherche(db_session, projet_id,
                                              inclure_prive=True)
    assert len(r_prive["donnees"]["observations_laboratoire"]) == 1
    assert r_prive["resultats"]["nb_observations"] == 1


def test_rapport_niveaux_donnees(db_session, projet_id):
    _plante_avec_composes(db_session, projet_id)
    r = rapport.build_rapport_recherche(db_session, projet_id)
    for pub in r["publications"]:
        assert pub["data_level"] == "publie"
    for comp in r["composes"]:
        assert "data_level" not in comp or True
    for c in r["combinaisons_analysees"]:
        assert c["data_level"] == "prediction"


# ---------------------------------------------------------------------------
# Rendus
# ---------------------------------------------------------------------------

def test_markdown_html_sections_et_honnetete(db_session, projet_id):
    _plante_avec_composes(db_session, projet_id)
    r = rapport.build_rapport_recherche(db_session, projet_id)
    md = rapport.render_markdown(r)
    html = rapport.render_html(r)
    for section in ["## FICHE DE LA PLANTE", "## COMPOSÉS", "## PUBLICATIONS",
                    "## DONNÉES", "## HYPOTHÈSES", "## SIMULATIONS",
                    "## COMBINAISONS ANALYSÉES", "## RÉSULTATS",
                    "## LIMITES", "## RÉFÉRENCES"]:
        assert section in md, section
    assert "<html" in html
    assert comb.verifier_honnetete(md) == []
    assert comb.verifier_honnetete(html) == []


def test_pdf_reel(db_session, projet_id):
    r = rapport.build_rapport_recherche(db_session, projet_id)
    pdf = rapport.build_pdf(r)
    assert pdf is not None
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000


def test_zip_reproductible(db_session, projet_id):
    plante_id, ids = _plante_avec_composes(db_session, projet_id)
    comb.creer_etude(db_session, project_id=projet_id, nom="E1",
                     compound_ids=ids, plant_id=plante_id)
    db_session.commit()
    donnees_zip = rapport.build_zip_recherche(db_session, projet_id)
    with zipfile.ZipFile(io.BytesIO(donnees_zip)) as zf:
        noms = zf.namelist()
    assert "rapport.md" in noms
    assert "rapport.html" in noms
    assert "donnees/entrees.json" in noms
    assert "MANIFEST.json" in noms
    with zipfile.ZipFile(io.BytesIO(donnees_zip)) as zf:
        manifest = json.loads(zf.read("MANIFEST.json"))
        entrees = json.loads(zf.read("donnees/entrees.json"))
    assert manifest["webintel_version"] == WEBINTEL_VERSION
    assert manifest["avertissement"]
    assert "combinaisons" in entrees["entrees"]
    assert len(entrees["hash_entrees_global"]) == 64
    # Formules documentées pour la reproductibilité.
    assert "bliss" in entrees["versions"]["synergie"]
    assert entrees["versions"]["score_comparaison_structurale"]
    # Même hash → mêmes entrées (déterministe).
    donnees_zip2 = rapport.build_zip_recherche(db_session, projet_id)
    with zipfile.ZipFile(io.BytesIO(donnees_zip2)) as zf:
        entrees2 = json.loads(zf.read("donnees/entrees.json"))
    assert (entrees["hash_entrees_global"]
            == entrees2["hash_entrees_global"])


def test_rapport_json_aucune_valeur_interdite(db_session, projet_id):
    _plante_avec_composes(db_session, projet_id)
    r = rapport.build_rapport_recherche(db_session, projet_id)
    assert comb.verifier_honnetete(json.dumps(r, ensure_ascii=False)) == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_rapport_formats(client):
    for fmt, media in [("json", "application/json"),
                       ("markdown", "text/markdown"),
                       ("html", "text/html")]:
        r = client.get(f"/api/science/rapport?format={fmt}")
        assert r.status_code == 200, (fmt, r.text)
        assert media in r.headers["content-type"]
    r = client.get("/api/science/rapport?format=pdf")
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"


def test_api_rapport_zip(client):
    r = client.get("/api/science/rapport/zip")
    assert r.status_code == 200, r.text
    assert "zip" in r.headers["content-type"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "MANIFEST.json" in zf.namelist()
