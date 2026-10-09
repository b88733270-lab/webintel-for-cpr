"""Versionnage des études — garantie « nouvelle exécution = nouvelle version ».

- Chaque création d'étude (API ou assistant) crée automatiquement sa
  version initiale (v1) — jamais d'écrasement silencieux.
- ``comparer_versions`` produit une comparaison structurée v1/v2 :
  entrées, paramètres, logiciels/versions, résultats.
- Route API ``GET /api/science/combinaisons/versions/{a}/comparer/{b}`` :
  200 avec sections, 404 si version absente, 422 si études différentes.
- ``restaurer_version`` ne modifie JAMAIS l'étude source : elle crée une
  nouvelle étude (nouvelle version).
"""

import uuid

import pytest

from webintel.database.models import Project


def _projet(db):
    """Projet de test (créé si absent)."""
    projet = db.query(Project).first()
    if projet is None:
        projet = Project(name="Projet Versionnage")
        db.add(projet)
        db.flush()
    return projet


from webintel.science import combinaisons as comb
from webintel.science import models as m
from webintel.science.versions import (
    comparer_versions,
    enregistrer_version,
    get_version,
    list_versions,
    restaurer_version,
)


def _etude_minimale(db, project_id, nom="Étude versionnée", **kwargs):
    suffixe = uuid.uuid4().hex[:6]
    c1 = m.Compound(name=f"V-Caféine-{suffixe}",
                    smiles="CN1C=NC2=C1C(=O)N(C(=O)N2C)C")
    c2 = m.Compound(name=f"V-Théophylline-{suffixe}",
                    smiles="CN1C=NC2=C1C(=O)NC(=O)N2C")
    db.add_all([c1, c2])
    db.flush()
    params = {"seuil_similarite": 0.5}
    params.update(kwargs.pop("parametres", {}) or {})
    return comb.creer_etude(
        db, project_id=project_id, nom=nom,
        compound_ids=[c1.id, c2.id], parametres=params,
        cree_par="test-versionnage", **kwargs,
    )


def test_creation_cree_version_initiale(db_session):
    """Créer une étude (service) + enregistrer_version → v1 présente."""
    projet = _projet(db_session)
    etude = _etude_minimale(db_session, projet.id)
    db_session.flush()
    version = enregistrer_version(db_session, etude, cree_par="test",
                                  resume="Création")
    db_session.flush()
    versions = list_versions(db_session, etude.id)
    assert len(versions) == 1 and versions[0].numero == 1
    assert versions[0].snapshot["inputs_hash"] == etude.inputs_hash
    assert versions[0].snapshot["nom"] == etude.nom


def test_nouvelle_execution_cree_nouvelle_version_pas_ecrasement(db_session):
    """Réexécuter (nouvelle étude) ne modifie jamais l'étude existante."""
    projet = _projet(db_session)
    etude1 = _etude_minimale(db_session, projet.id, nom="Étude A")
    db_session.flush()
    v1 = enregistrer_version(db_session, etude1, cree_par="test")
    db_session.flush()
    hash_avant = etude1.inputs_hash
    nom_avant = etude1.nom

    # « Nouvelle exécution » = nouvelle étude persistée.
    etude2 = _etude_minimale(
        db_session, projet.id, nom="Étude A (réexécution)",
        parametres={"seuil_similarite": 0.8})
    db_session.flush()
    v2 = enregistrer_version(db_session, etude2, cree_par="test")
    db_session.flush()

    assert etude2.id != etude1.id
    assert etude1.nom == nom_avant and etude1.inputs_hash == hash_avant
    assert v2.id != v1.id


def test_comparer_versions_sections(db_session):
    """Comparaison v1/v2 : entrées, paramètres, logiciels, résultats."""
    projet = _projet(db_session)
    etude = _etude_minimale(db_session, projet.id,
                            parametres={"seuil_similarite": 0.5})
    db_session.flush()
    v1 = enregistrer_version(db_session, etude, cree_par="test",
                             resume="v1")
    db_session.flush()

    # v2 = même étude, paramètres modifiés → nouvelle version.
    etude.parametres = {"seuil_similarite": 0.8}
    v2 = enregistrer_version(db_session, etude, cree_par="test",
                             resume="v2")
    db_session.flush()

    comparaison = comparer_versions(db_session, v1.id, v2.id)
    assert comparaison["etude_id"] == etude.id
    assert comparaison["version_a"]["numero"] == 1
    assert comparaison["version_b"]["numero"] == 2
    sections = comparaison["sections"]
    assert set(sections) == {"entrees", "parametres", "logiciels",
                             "resultats"}

    lignes_params = {l["champ"]: l for l in sections["parametres"]}
    assert "seuil_similarite" in lignes_params
    diff = lignes_params["seuil_similarite"]
    assert diff["identique"] is False
    assert diff["valeur_version_a"] == 0.5
    assert diff["valeur_version_b"] == 0.8

    # Le nom n'a pas changé → ligne identique.
    lignes_entrees = {l["champ"]: l for l in sections["entrees"]}
    assert lignes_entrees["nom"]["identique"] is True
    assert "valeur_version_a" not in lignes_entrees["nom"]

    # Sections résultats : lignes présentes (empreinte dossier).
    champs_res = {l["champ"] for l in sections["resultats"]}
    assert "empreinte_dossier" in champs_res

    # Résumé français lisible.
    assert comparaison["nb_differences"] >= 1
    assert "différence" in comparaison["resume"]


def test_comparer_versions_identiques(db_session):
    """Deux snapshots identiques → résumé « identiques », 0 différence."""
    projet = _projet(db_session)
    etude = _etude_minimale(db_session, projet.id)
    db_session.flush()
    v1 = enregistrer_version(db_session, etude, cree_par="test")
    v2 = enregistrer_version(db_session, etude, cree_par="test")
    db_session.flush()
    comparaison = comparer_versions(db_session, v1.id, v2.id)
    assert comparaison["nb_differences"] == 0
    assert "identiques" in comparaison["resume"]


def test_comparer_versions_erreurs(db_session):
    """404 (KeyError) si version absente ; ValueError si études ≠."""
    projet = _projet(db_session)
    e1 = _etude_minimale(db_session, projet.id, nom="E1")
    e2 = _etude_minimale(db_session, projet.id, nom="E2")
    db_session.flush()
    v1 = enregistrer_version(db_session, e1, cree_par="t")
    v2 = enregistrer_version(db_session, e2, cree_par="t")
    db_session.flush()
    with pytest.raises(KeyError):
        comparer_versions(db_session, 999_999, v1.id)
    with pytest.raises(ValueError, match="même étude"):
        comparer_versions(db_session, v1.id, v2.id)


def test_restaurer_version_cree_nouvelle_etude(db_session):
    """Restaurer une version ne modifie JAMAIS l'étude source."""
    projet = _projet(db_session)
    etude = _etude_minimale(db_session, projet.id)
    db_session.flush()
    v1 = enregistrer_version(db_session, etude, cree_par="test")
    etude.parametres = {"seuil_similarite": 0.9}
    v2 = enregistrer_version(db_session, etude, cree_par="test")
    db_session.flush()
    nom_avant = etude.nom
    params_avant = dict(etude.parametres)

    restauree = restaurer_version(db_session, v1.id, cree_par="test")
    db_session.flush()

    assert restauree.id != etude.id  # nouvelle étude persistée
    assert etude.nom == nom_avant  # étude source intacte
    assert etude.parametres == params_avant
    # La restaurée reprend les paramètres de v1.
    assert restauree.parametres.get("seuil_similarite") == 0.5
    versions = list_versions(db_session, restauree.id)
    assert len(versions) >= 1


def test_api_comparer_versions(client):
    """Route API de comparaison v1/v2 : 200, sections, erreurs."""
    headers = {}
    # En mode serveur, la fixture `client` envoie déjà X-API-Key ; en
    # mode local aucune clé n'est requise. Le projet effectif est celui
    # du bootstrap serveur, sinon le projet par défaut.
    r = client.post("/api/science/compounds", headers=headers,
                    json={"name": "API-Caféine",
                          "smiles": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"})
    assert r.status_code in (200, 201), r.text
    c1 = r.json()["id"]
    r = client.post("/api/science/compounds", headers=headers,
                    json={"name": "API-Théophylline",
                          "smiles": "CN1C=NC2=C1C(=O)NC(=O)N2C"})
    assert r.status_code in (200, 201), r.text
    c2 = r.json()["id"]

    r = client.post("/api/science/combinaisons", headers=headers,
                    json={"nom": "Étude API comparaison",
                          "compound_ids": [c1, c2]})
    assert r.status_code in (200, 201), r.text
    etude_id = r.json()["id"]

    # La création API crée la v1 automatiquement.
    r = client.get(f"/api/science/combinaisons/{etude_id}/versions",
                   headers=headers)
    assert r.status_code == 200, r.text
    versions = r.json()
    assert len(versions) >= 1
    v1 = versions[0]["id"]

    # Comparaison d'une version avec elle-même → 0 différence.
    r = client.get(
        f"/api/science/combinaisons/versions/{v1}/comparer/{v1}",
        headers=headers)
    assert r.status_code == 200, r.text
    comp = r.json()
    assert comp["nb_differences"] == 0
    assert set(comp["sections"]) == {"entrees", "parametres",
                                     "logiciels", "resultats"}

    # 404 si version absente.
    r = client.get(
        f"/api/science/combinaisons/versions/999999/comparer/{v1}",
        headers=headers)
    assert r.status_code == 404

    # 422 si études différentes.
    r = client.post("/api/science/combinaisons", headers=headers,
                    json={"nom": "Étude B", "compound_ids": [c1, c2]})
    assert r.status_code in (200, 201), r.text
    etude_b = r.json()["id"]
    r = client.get(f"/api/science/combinaisons/{etude_b}/versions",
                   headers=headers)
    v_b = r.json()[0]["id"]
    r = client.get(
        f"/api/science/combinaisons/versions/{v1}/comparer/{v_b}",
        headers=headers)
    assert r.status_code == 422, r.text
