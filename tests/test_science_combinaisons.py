"""Tests v2.1 — études de combinaison (MODE A).

Couvre : création (2 et 5 molécules), dossier en 12 points, absence de
formulations interdites, aucune valeur inventée, reproductibilité
(même hash → mêmes sorties), isolation projet, provenance, API.
"""

from __future__ import annotations

import json

import pytest

from webintel.database.base import SessionLocal
from webintel.database import models as modeles_noyau
from webintel.science import chimie
from webintel.science import combinaisons as comb
from webintel.science import models as science


necessite_rdkit = pytest.mark.skipif(
    not chimie.rdkit_disponible(),
    reason="RDKit absent — similarité structurelle (niveau SCIENCE) non installée",
)


@pytest.fixture()
def projet_id(db_session):
    projet = modeles_noyau.Project(name="Projet Combinaisons")
    db_session.add(projet)
    db_session.commit()
    return projet.id


def _compose(db_session, nom: str, smiles: str | None = None) -> int:
    compose = science.Compound(
        name=nom, formula="C6H12O6", smiles=smiles,
        chemical_class="test",
    )
    db_session.add(compose)
    db_session.commit()
    return compose.id


def _plante(db_session, nom: str = "Moringa oleifera") -> int:
    plante = science.Plant(scientific_name=nom, family="Moringaceae")
    db_session.add(plante)
    db_session.commit()
    return plante.id


# ---------------------------------------------------------------------------
# Construction du dossier
# ---------------------------------------------------------------------------

def test_etude_deux_molecules_douze_sections(db_session, projet_id):
    a = _compose(db_session, "Molécule A", "CCO")
    b = _compose(db_session, "Molécule B", "CCC")
    plante = _plante(db_session)
    etude = comb.creer_etude(
        db_session, project_id=projet_id, nom="Étude A+B",
        compound_ids=[a, b], plant_id=plante, plant_part="feuilles",
        target="COX-2",
    )
    db_session.commit()
    assert etude.data_level == "prediction"
    assert len(etude.inputs_hash) == 64
    sections = etude.resultats["sections"]
    assert len(sections) == 12
    titres = [s["titre"] for s in sections]
    assert titres[0].startswith("1.") and titres[11].startswith("12.")
    # Conclusion : formulations autorisées uniquement.
    conclusion = sections[11]["conclusion"]
    assert "Résultat de screening informatique" in conclusion
    assert "Hypothèse à vérifier expérimentalement" in conclusion


@necessite_rdkit
def test_etude_cinq_molecules(db_session, projet_id):
    ids = [_compose(db_session, f"Mol {i}", "CCO") for i in range(5)]
    etude = comb.creer_etude(
        db_session, project_id=projet_id, nom="Étude 5",
        compound_ids=ids,
    )
    db_session.commit()
    paires = etude.resultats["sections"][2]["paires"]
    # C(5,2) = 10 paires de similarité.
    assert len(paires) == 10


def test_aucune_formulation_interdite(db_session, projet_id):
    """Garde-fou : aucun rapport généré ne contient « efficace »/« sûr »/..."""
    ids = [_compose(db_session, f"Molécule {i}") for i in range(3)]
    etude = comb.creer_etude(
        db_session, project_id=projet_id, nom="Étude honnêteté",
        compound_ids=ids,
    )
    db_session.commit()
    textes = [
        json.dumps(etude.resultats, ensure_ascii=False),
        comb.rendre_markdown_etude(etude),
        comb.rendre_html_etude(etude),
    ]
    for texte in textes:
        violations = comb.verifier_honnetete(texte)
        assert violations == [], f"Formulations interdites : {violations}"


def test_verifier_honnetete_detecte():
    assert comb.verifier_honnetete("ce mélange est efficace") != []
    assert comb.verifier_honnetete("ce produit est sûr") != []
    assert comb.verifier_honnetete("d'une innocuité totale") != []
    assert comb.verifier_honnetete("Résultat de screening informatique") == []


def test_pas_de_valeur_inventee_sans_chimie(db_session, projet_id):
    """Sans le module chimie : sections explicites, score None, statut partiel."""
    ids = [_compose(db_session, "A", "CCO"), _compose(db_session, "B", "CCC")]
    ok, raison = comb.chimie_disponible()
    etude = comb.creer_etude(
        db_session, project_id=projet_id, nom="Étude sans chimie",
        compound_ids=ids,
    )
    db_session.commit()
    s2 = etude.resultats["sections"][1]
    s3 = etude.resultats["sections"][2]
    score = etude.resultats["sections"][8]["score_informatique"]
    if not ok:
        assert s2["chimie_disponible"] is False
        assert raison  # message explicite
        assert s3["paires"] == []
        assert score["score"] is None
        assert "inventée" in score["note"]
        assert etude.statut == "partiel"
    else:
        # Si le chantier chimie a livré : les calculs sont réels.
        assert score["score"] is not None


def test_reproductibilite_meme_hash_memes_sorties(db_session, projet_id):
    ids = [_compose(db_session, "A"), _compose(db_session, "B")]
    e1 = comb.creer_etude(
        db_session, project_id=projet_id, nom="R1", compound_ids=ids)
    e2 = comb.creer_etude(
        db_session, project_id=projet_id, nom="R2", compound_ids=list(reversed(ids)))
    db_session.commit()
    assert e1.inputs_hash == e2.inputs_hash
    assert e1.resultats == e2.resultats  # mêmes sorties (déterministe)


def test_entrees_invalides(db_session, projet_id):
    a = _compose(db_session, "A")
    with pytest.raises(ValueError, match="au moins 2 composés"):
        comb.creer_etude(db_session, project_id=projet_id, nom="X",
                         compound_ids=[a])
    with pytest.raises(ValueError, match="au moins 2 composés"):
        # Les doublons sont dédupliqués avant validation : [a, a] → 1 seul.
        comb.creer_etude(db_session, project_id=projet_id, nom="X",
                         compound_ids=[a, a])
    with pytest.raises(ValueError, match="Composé introuvable"):
        comb.creer_etude(db_session, project_id=projet_id, nom="X",
                         compound_ids=[a, 999999])


def test_isolation_projet(db_session, projet_id):
    autre = modeles_noyau.Project(name="Autre projet")
    db_session.add(autre)
    db_session.commit()
    ids = [_compose(db_session, "A"), _compose(db_session, "B")]
    comb.creer_etude(db_session, project_id=projet_id, nom="E1",
                     compound_ids=ids)
    db_session.commit()
    assert len(comb.list_etudes(db_session, projet_id)) == 1
    assert comb.list_etudes(db_session, autre.id) == []


def test_provenance_enregistree(db_session, projet_id):
    ids = [_compose(db_session, "A"), _compose(db_session, "B")]
    etude = comb.creer_etude(
        db_session, project_id=projet_id, nom="E", compound_ids=ids,
        cree_par="local",
    )
    db_session.commit()
    assert etude.provenance["cree_par"] == "local"
    assert "score_version" in etude.provenance
    assert "chimie_disponible" in etude.provenance


def test_cibles_documentees_si_table_presente(db_session, projet_id):
    """Si CompoundTarget existe (chantier parallèle), les cibles sont utilisées."""
    pytest.importorskip("webintel.science.models")
    from webintel.science.models import CompoundTarget  # noqa: E402
    a = _compose(db_session, "Quercétine")
    b = _compose(db_session, "Lutéoline")
    db_session.add(CompoundTarget(
        compound_id=a, target_name="COX-2", interaction_type="inhibition",
        source="Publication X", data_level="publie"))
    db_session.add(CompoundTarget(
        compound_id=b, target_name="COX-2", interaction_type="inhibition",
        source="Publication Y", data_level="publie"))
    db_session.commit()
    etude = comb.creer_etude(
        db_session, project_id=projet_id, nom="E cibles", compound_ids=[a, b])
    db_session.commit()
    s5 = etude.resultats["sections"][4]
    s6 = etude.resultats["sections"][5]
    assert s5["composes"][0]["cibles_documentees"][0]["cible"] == "COX-2"
    assert [c["cible"] for c in s6["cibles_communes"]] == ["cox-2"]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _compose_api(client, nom: str) -> int:
    r = client.post("/api/science/compounds", json={"name": nom})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_api_creer_et_lire(client, server_bootstrap):
    a = _compose_api(client, "API A")
    b = _compose_api(client, "API B")
    r = client.post("/api/science/combinaisons",
                    json={"nom": "Étude API", "compound_ids": [a, b]})
    assert r.status_code == 201, r.text
    corps = r.json()
    assert corps["data_level"] == "prediction"
    assert len(corps["inputs_hash"]) == 64
    eid = corps["id"]
    r = client.get(f"/api/science/combinaisons/{eid}")
    assert r.status_code == 200
    assert len(r.json()["dossier"]["sections"]) == 12
    r = client.get("/api/science/combinaisons")
    assert r.status_code == 200
    assert any(e["id"] == eid for e in r.json())


def test_api_rapports_formats(client):
    a = _compose_api(client, "F A")
    b = _compose_api(client, "F B")
    r = client.post("/api/science/combinaisons",
                    json={"nom": "Étude formats", "compound_ids": [a, b]})
    eid = r.json()["id"]
    for fmt, media in [("json", "application/json"),
                       ("html", "text/html"),
                       ("markdown", "text/markdown")]:
        r = client.get(f"/api/science/combinaisons/{eid}/rapport?format={fmt}")
        assert r.status_code == 200, (fmt, r.text)
        assert media in r.headers["content-type"]
        assert comb.verifier_honnetete(r.text) == []


def test_api_422_si_moins_de_deux_composes(client):
    a = _compose_api(client, "Solo")
    r = client.post("/api/science/combinaisons",
                    json={"nom": "X", "compound_ids": [a]})
    # Le schéma (min 2 items) ou la validation métier rejette : 422 dans
    # tous les cas, sans créer d'étude.
    assert r.status_code == 422


def test_api_404_etude_inconnue(client):
    r = client.get("/api/science/combinaisons/999999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Traçabilité : toute étude créée alimente le journal de provenance (v2.3)
# ---------------------------------------------------------------------------

def test_creer_etude_enregistre_provenance(db_session, projet_id):
    """`creer_etude` écrit une ligne `Provenance` (entity_type="etude") —
    la fiche `/resultats/etude/{id}/provenance` n'est plus un 404 honnête."""
    a = _compose(db_session, "Molécule Prov A", "CCO")
    b = _compose(db_session, "Molécule Prov B", "CCC")
    etude = comb.creer_etude(
        db_session, project_id=projet_id, nom="Étude provenance",
        compound_ids=[a, b], cree_par="test",
    )
    db_session.commit()
    lignes = (
        db_session.query(science.Provenance)
        .filter(
            science.Provenance.entity_type == "etude",
            science.Provenance.entity_id == etude.id,
        )
        .all()
    )
    assert len(lignes) == 1, "la création d'étude doit tracer sa provenance"
    ligne = lignes[0]
    assert ligne.connector == "webintel.science.combinaisons"
    assert ligne.niveau_preuve == "prediction"
    assert "12-points" in (ligne.data_version or "")
    # Idempotence du journal : ré-enregistrer la même traçabilité ne
    # duplique pas la ligne.
    from webintel.science import service as science_service

    _, creee = science_service.record_provenance(
        db_session, "etude", etude.id,
        source_name=ligne.source_name, connector=ligne.connector,
        data_version=ligne.data_version,
    )
    assert creee is False
