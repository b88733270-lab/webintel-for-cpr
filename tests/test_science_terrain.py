"""Tests v2.2 — extension modèle scientifique : terrain, ethnobotanique,
phytochimie, pharmacognosie.

Couverture : modèles (nouvelles tables, statuts épistémiques, DataLevel
figé), services (idempotence, langue jamais devinée, GPS jamais inventé,
soft-delete), recherche multi-motifs (neem / karité trouvés), API CRUD
cloisonnée au projet, confidentialité des exports publics (ni GPS précis
ni données restreint/confidentiel), UI (formulaires HTML, bandeau
obligatoire).
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from webintel.database import models as core_models
from webintel.science import terrain as t
from webintel.science.models import (
    AnalyseChimique,
    Compound,
    CompoundTarget,
    DataLevel,
    Echantillon,
    Extraction,
    Extrait,
    Fraction,
    FractionCompound,
    LANGUES_BURKINA,
    NiveauConfidentialite,
    NomVernaculaire,
    Pharmacognosie,
    PicChromatographique,
    Plant,
    PlantActivity,
    StatutEpistemique,
    UsageTraditionnel,
    Voucher,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def projet(db_session):
    p = core_models.Project(name="Projet Terrain")
    db_session.add(p)
    db_session.commit()
    return p.id


@pytest.fixture()
def projet_b(db_session):
    p = core_models.Project(name="Projet Terrain B")
    db_session.add(p)
    db_session.commit()
    return p.id


@pytest.fixture()
def neem(db_session):
    p = Plant(scientific_name="Azadirachta indica", family="Meliaceae")
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def karite(db_session):
    p = Plant(scientific_name="Vitellaria paradoxa", family="Sapotaceae")
    db_session.add(p)
    db_session.commit()
    return p


def _nom(db_session, plante, pid, nom, langue=None, **kw):
    obj, _ = t.ajouter_nom_vernaculaire(
        db_session, plante.id, pid, nom, langue=langue, **kw
    )
    db_session.commit()
    return obj


# ---------------------------------------------------------------------------
# 1. Modèles — nouvelles tables, statuts épistémiques, DataLevel figé
# ---------------------------------------------------------------------------


def test_data_level_reste_fige_a_cinq_valeurs():
    valeurs = sorted(n.value for n in DataLevel)
    assert valeurs == [
        "experimental", "hypothese", "normalise", "prediction", "publie",
    ]


def test_statuts_epistemiques_distincts_de_data_level():
    valeurs = {s.value for s in StatutEpistemique}
    assert valeurs == {"documente", "infere", "predit", "calcule"}
    assert not (valeurs & {n.value for n in DataLevel})


def test_statut_epistemique_defaut_documente(db_session, projet):
    cible = CompoundTarget(compound_id=1, target_name="COX-2")
    db_session.add(cible)
    db_session.commit()
    db_session.refresh(cible)
    assert cible.statut_epistemique == "documente"
    assert cible.methode_statut is None
    activite = PlantActivity(plant_id=1, activity="antipaludique")
    db_session.add(activite)
    db_session.commit()
    db_session.refresh(activite)
    assert activite.statut_epistemique == "documente"


def test_langues_burkina_preparees():
    for code in ("moore", "dioula", "fulfulde", "gulmancema", "bissa"):
        assert code in LANGUES_BURKINA
    assert LANGUES_BURKINA["moore"] == "Mooré"


def test_niveaux_confidentialite():
    assert {n.value for n in NiveauConfidentialite} == {
        "public", "projet", "chercheur", "restreint", "confidentiel",
    }


# ---------------------------------------------------------------------------
# 2. Services — noms vernaculaires (langue jamais devinée)
# ---------------------------------------------------------------------------


def test_normalisation_nom_vernaculaire():
    assert t.normaliser_nom_vernaculaire("Karité") == "karite"
    assert t.normaliser_nom_vernaculaire("  Néré  ") == "nere"
    with pytest.raises(ValueError):
        t.normaliser_nom_vernaculaire("   ")


def test_ajout_nom_vernaculaire_idempotent(db_session, neem, projet):
    n1, c1 = t.ajouter_nom_vernaculaire(
        db_session, neem.id, projet, "Neem", langue="dioula"
    )
    db_session.commit()
    n2, c2 = t.ajouter_nom_vernaculaire(
        db_session, neem.id, projet, "NEEM", langue="dioula"
    )
    db_session.commit()
    assert c1 is True and c2 is False and n1.id == n2.id
    assert n1.nom_original == "Neem"
    assert n1.nom_normalise == "neem"


def test_langue_jamais_devinee(db_session, neem, projet):
    obj, _ = t.ajouter_nom_vernaculaire(
        db_session, neem.id, projet, "Neem"
    )
    db_session.commit()
    assert obj.langue is None


def test_langue_inconnue_refusee(db_session, neem, projet):
    with pytest.raises(ValueError):
        t.ajouter_nom_vernaculaire(
            db_session, neem.id, projet, "Neem", langue="klingon"
        )


# ---------------------------------------------------------------------------
# 3. Services — usages, échantillons, chaîne, analyses
# ---------------------------------------------------------------------------


def test_usage_traditionnel_toujours_cree_et_experimental(
    db_session, neem, projet
):
    u1 = t.ajouter_usage_traditionnel(
        db_session, neem.id, projet, "décoction bue"
    )
    u2 = t.ajouter_usage_traditionnel(
        db_session, neem.id, projet, "décoction bue"
    )
    db_session.commit()
    assert u1.id != u2.id
    assert u1.data_level == "experimental"
    with pytest.raises(ValueError):
        t.ajouter_usage_traditionnel(db_session, neem.id, projet, "  ")


def test_echantillon_gps_jamais_invente(db_session, neem, projet):
    e, _ = t.creer_echantillon(
        db_session, projet, neem.id, "BF-2026-001", pays="Burkina Faso"
    )
    db_session.commit()
    assert e.latitude is None and e.longitude is None
    assert e.precision_gps is None


def test_echantillon_idempotent_par_projet(db_session, neem, projet, projet_b):
    e1, c1 = t.creer_echantillon(db_session, projet, neem.id, "BF-1")
    e2, c2 = t.creer_echantillon(db_session, projet, neem.id, "BF-1")
    e3, c3 = t.creer_echantillon(db_session, projet_b, neem.id, "BF-1")
    db_session.commit()
    assert (c1, c2, c3) == (True, False, True)
    assert e1.id == e2.id != e3.id


def test_chaine_complete_echantillon(db_session, neem, projet):
    e, _ = t.creer_echantillon(db_session, projet, neem.id, "BF-CH")
    v, _ = t.creer_voucher(
        db_session, projet, "OUA-2026-0147", institution="Herbier OUA"
    )
    t.lier_echantillon_voucher(db_session, e.id, v.id)
    x = t.creer_extraction(
        db_session, projet, e.id, nom="extraction aqueuse", solvant="eau"
    )
    xt = t.creer_extrait(db_session, projet, x.id, code_extrait="EXT-1")
    f = t.creer_fraction(
        db_session, projet, xt.id, code_fraction="F-1"
    )
    compose = Compound(name="Azadirachtine")
    db_session.add(compose)
    db_session.flush()
    lien, cree = t.lier_fraction_compose(
        db_session, projet, f.id, compose.id,
        identification_statut="tentative",
    )
    db_session.commit()
    assert cree is True
    # idempotence du lien
    _, cree2 = t.lier_fraction_compose(
        db_session, projet, f.id, compose.id
    )
    assert cree2 is False

    chaine = t.chaine_echantillon(db_session, e.id)
    assert chaine["voucher"].code_voucher == "OUA-2026-0147"
    assert len(chaine["extractions"]) == 1
    fractions = chaine["extractions"][0]["extraits"][0]["fractions"]
    assert fractions[0]["composes"][0]["nom_compose"] == "Azadirachtine"
    assert fractions[0]["composes"][0]["identification_statut"] == "tentative"


def test_analyse_exige_un_maillon(db_session, projet):
    with pytest.raises(ValueError):
        t.creer_analyse_chimique(db_session, projet, "HPLC")
    with pytest.raises(ValueError):
        t.creer_analyse_chimique(
            db_session, projet, "CHROMATO-MAGIQUE", echantillon_id=1
        )


def test_analyse_et_pics(db_session, neem, projet):
    e, _ = t.creer_echantillon(db_session, projet, neem.id, "BF-AN")
    a = t.creer_analyse_chimique(
        db_session, projet, "HPLC", echantillon_id=e.id,
        instrument="Shimadzu", laboratoire="Labo Ouaga",
    )
    p1 = t.ajouter_pic(db_session, a.id, temps_retention=4.2, aire=1200.5)
    p2 = t.ajouter_pic(
        db_session, a.id, temps_retention=7.8,
        identification_statut="tentative", commentaire="à confirmer",
    )
    db_session.commit()
    assert p1.identification_statut == "inconnu"  # jamais deviné
    assert a.data_level == "experimental"
    pics = t.lister_pics(db_session, a.id)
    assert [p.temps_retention for p in pics] == [4.2, 7.8]


def test_pharmacognosie_upsert_et_champs_inconnus(db_session, neem, projet):
    fiche, cree = t.definir_pharmacognosie(
        db_session, neem.id, projet,
        identification_macro="arbre à feuilles composées",
        sechage="à l'ombre",
    )
    assert cree is True
    fiche2, cree2 = t.definir_pharmacognosie(
        db_session, neem.id, projet, stockage="sacs aérés"
    )
    db_session.commit()
    assert cree2 is False and fiche2.id == fiche.id
    assert fiche2.sechage == "à l'ombre"  # conservé
    assert fiche2.identification_micro is None  # absent = absent
    with pytest.raises(ValueError):
        t.definir_pharmacognosie(
            db_session, neem.id, projet, champ_invente="x"
        )
    with pytest.raises(KeyError):
        t.definir_pharmacognosie(db_session, 999999, projet)


def test_statut_epistemique_service(db_session):
    cible = CompoundTarget(compound_id=1, target_name="COX-2")
    db_session.add(cible)
    db_session.commit()
    t.definir_statut_epistemique(
        db_session, cible, "predit", methode_statut="docking Vina 1.2.3"
    )
    db_session.commit()
    assert cible.statut_epistemique == "predit"
    assert cible.methode_statut == "docking Vina 1.2.3"
    with pytest.raises(ValueError):
        t.definir_statut_epistemique(db_session, cible, "prouve")
    with pytest.raises(TypeError):
        t.definir_statut_epistemique(db_session, object(), "predit")


def test_soft_delete(db_session, neem, projet):
    obj = _nom(db_session, neem, projet, "Neem", langue="dioula")
    t.supprimer_doucement(db_session, obj, supprime_par="testeur")
    db_session.commit()
    # exclu des listes...
    assert t.lister_noms_vernaculaires(db_session, neem.id, projet) == []
    # ...mais toujours en base, tracé
    relu = db_session.get(NomVernaculaire, obj.id)
    assert relu.supprime_le is not None
    assert relu.supprime_par == "testeur"


# ---------------------------------------------------------------------------
# 4. Recherche multi-motifs
# ---------------------------------------------------------------------------


@pytest.fixture()
def corpus_recherche(db_session, neem, karite, projet):
    moringa = Plant(
        scientific_name="Moringa oleifera",
        synonyms=["Moringa pterygosperma"],
    )
    db_session.add(moringa)
    cafe = Plant(scientific_name="Coffea arabica")
    db_session.add(cafe)
    db_session.commit()
    _nom(db_session, neem, projet, "Neem", langue="dioula")
    _nom(db_session, karite, projet, "Karité", langue="moore")
    return {"neem": neem, "karite": karite, "moringa": moringa}


def _motif(db_session, q, pid):
    res = t.rechercher_plantes(db_session, q, project_id=pid)
    return {r["scientific_name"]: r["motif"] for r in res}


def test_recherche_exact(db_session, corpus_recherche, projet):
    assert _motif(db_session, "Azadirachta indica", projet)[
        "Azadirachta indica"] == "exact"
    assert _motif(db_session, "azadirachta indica", projet)[
        "Azadirachta indica"] == "exact"


def test_recherche_normalise_accents(db_session, corpus_recherche, projet):
    # "Café arabica" cherché sans accent -> normalisé
    cafe = db_session.query(Plant).filter_by(
        scientific_name="Coffea arabica").one()
    cafe.scientific_name = "Caféa arabica"
    db_session.commit()
    assert _motif(db_session, "cafea arabica", projet)[
        "Caféa arabica"] == "normalise"


def test_recherche_synonyme(db_session, corpus_recherche, projet):
    assert _motif(db_session, "moringa pterygosperma", projet)[
        "Moringa oleifera"] == "synonyme"


def test_recherche_vernaculaire_neem_et_karite(
    db_session, corpus_recherche, projet
):
    res = t.rechercher_plantes(db_session, "neem", project_id=projet)
    assert res[0]["scientific_name"] == "Azadirachta indica"
    assert res[0]["motif"] == "vernaculaire"
    assert "Dioula" in res[0]["detail"]
    res2 = t.rechercher_plantes(db_session, "karité", project_id=projet)
    assert res2[0]["scientific_name"] == "Vitellaria paradoxa"
    assert res2[0]["motif"] == "vernaculaire"
    # sans accent aussi
    res3 = t.rechercher_plantes(db_session, "karite", project_id=projet)
    assert res3[0]["motif"] == "vernaculaire"


def test_recherche_partiel(db_session, corpus_recherche, projet):
    assert _motif(db_session, "moring", projet)[
        "Moringa oleifera"] == "partiel"


def test_recherche_priorite_motifs(db_session, corpus_recherche, projet):
    # le nom scientifique prime sur un vernaculaire identique ailleurs
    res = t.rechercher_plantes(
        db_session, "Azadirachta indica", project_id=projet
    )
    neem_res = [r for r in res
                if r["scientific_name"] == "Azadirachta indica"]
    assert len(neem_res) == 1 and neem_res[0]["motif"] == "exact"


def test_recherche_vide(db_session):
    assert t.rechercher_plantes(db_session, "   ") == []


# ---------------------------------------------------------------------------
# 5. API — CRUD cloisonné au projet
# ---------------------------------------------------------------------------


def test_api_creer_nom_vernaculaire(client, neem, projet):
    r = client.post(
        "/api/science/terrain/noms-vernaculaires",
        json={"plant_id": neem.id, "project_id": projet,
              "nom_original": "Neem", "langue": "dioula",
              "source": "enquête 2026"},
    )
    assert r.status_code == 201, r.text
    corps = r.json()
    assert corps["nom_normalise"] == "neem"
    assert corps["langue"] == "dioula"
    # idempotent : repost -> 201, même id
    r2 = client.post(
        "/api/science/terrain/noms-vernaculaires",
        json={"plant_id": neem.id, "project_id": projet,
              "nom_original": "Neem", "langue": "dioula"},
    )
    assert r2.status_code == 201 and r2.json()["id"] == corps["id"]


def test_api_nom_langue_inconnue_422(client, neem, projet):
    r = client.post(
        "/api/science/terrain/noms-vernaculaires",
        json={"plant_id": neem.id, "project_id": projet,
              "nom_original": "Neem", "langue": "klingon"},
    )
    assert r.status_code == 422


def test_api_isolation_inter_projets(client, neem, projet, projet_b):
    client.post(
        "/api/science/terrain/noms-vernaculaires",
        json={"plant_id": neem.id, "project_id": projet,
              "nom_original": "Neem"},
    )
    r_b = client.get(
        f"/api/science/terrain/plantes/{neem.id}/noms-vernaculaires",
        params={"project_id": projet_b},
    )
    assert r_b.status_code == 200 and r_b.json() == []
    r_a = client.get(
        f"/api/science/terrain/plantes/{neem.id}/noms-vernaculaires",
        params={"project_id": projet},
    )
    assert len(r_a.json()) == 1


def test_api_usage_et_bandeau(client, neem, projet):
    r = client.post(
        "/api/science/terrain/usages",
        json={"plant_id": neem.id, "project_id": projet,
              "usage": "décoction des feuilles bue",
              "partie_utilisee": "feuilles"},
    )
    assert r.status_code == 201, r.text
    corps = r.json()
    assert corps["bandeau"] == "USAGE TRADITIONNEL RAPPORTÉ"
    assert corps["niveau_confidentialite"] == "restreint"  # défaut sûr
    assert corps["data_level"] == "experimental"


def test_api_suppression_logique(client, neem, projet, db_session):
    r = client.post(
        "/api/science/terrain/noms-vernaculaires",
        json={"plant_id": neem.id, "project_id": projet,
              "nom_original": "Neem"},
    )
    nid = r.json()["id"]
    sup = client.delete(f"/api/science/terrain/noms-vernaculaires/{nid}")
    assert sup.status_code == 200
    liste = client.get(
        f"/api/science/terrain/plantes/{neem.id}/noms-vernaculaires",
        params={"project_id": projet},
    )
    assert liste.json() == []
    # toujours en base, avec horodatage
    relu = db_session.get(NomVernaculaire, nid)
    assert relu is not None and relu.supprime_le is not None


def test_api_chaine_echantillon(client, neem, projet):
    e = client.post(
        "/api/science/terrain/echantillons",
        json={"plant_id": neem.id, "project_id": projet,
              "code_echantillon": "BF-API-1", "pays": "Burkina Faso",
              "latitude": 12.3714, "longitude": -1.5197,
              "precision_gps": "±10 m"},
    ).json()
    assert e["latitude"] == 12.3714
    x = client.post(
        "/api/science/terrain/extractions",
        json={"project_id": projet, "echantillon_id": e["id"],
              "methode": "macération", "solvant": "eau"},
    )
    assert x.status_code == 201, x.text
    chaine = client.get(
        f"/api/science/terrain/echantillons/{e['id']}/chaine",
        params={"project_id": projet},
    )
    assert chaine.status_code == 200
    corps = chaine.json()
    assert corps["echantillon"]["code_echantillon"] == "BF-API-1"
    assert len(corps["extractions"]) == 1


def test_api_analyse_sans_maillon_422(client, projet):
    r = client.post(
        "/api/science/terrain/analyses",
        json={"project_id": projet, "methode": "HPLC"},
    )
    assert r.status_code == 422


def test_api_statut_epistemique(client, db_session, projet):
    compose = Compound(name="Quercétine")
    db_session.add(compose)
    db_session.commit()
    cible = CompoundTarget(compound_id=compose.id, target_name="COX-2")
    db_session.add(cible)
    db_session.commit()
    r = client.patch(
        f"/api/science/terrain/cibles/{cible.id}/statut",
        json={"statut_epistemique": "predit",
              "methode_statut": "docking Vina"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["statut_epistemique"] == "predit"
    r2 = client.patch(
        f"/api/science/terrain/cibles/{cible.id}/statut",
        json={"statut_epistemique": "prouve"},
    )
    assert r2.status_code == 422
    # activité
    activite = PlantActivity(plant_id=1, activity="x")
    db_session.add(activite)
    db_session.commit()
    r3 = client.patch(
        f"/api/science/terrain/activites/{activite.id}/statut",
        json={"statut_epistemique": "infere"},
    )
    assert r3.status_code == 200


def test_api_recherche_motif(client, neem, karite, projet):
    client.post(
        "/api/science/terrain/noms-vernaculaires",
        json={"plant_id": neem.id, "project_id": projet,
              "nom_original": "Neem", "langue": "dioula"},
    )
    r = client.get(
        "/api/science/terrain/recherche",
        params={"q": "neem", "project_id": projet},
    )
    assert r.status_code == 200
    res = r.json()
    assert res[0]["scientific_name"] == "Azadirachta indica"
    assert res[0]["motif"] == "vernaculaire"


def test_require_project_access_strict_unitaire():
    from fastapi import HTTPException
    from webintel.api.deps import require_project_access_strict

    cle = SimpleNamespace(project_id=10)
    # projet étranger -> 403
    with pytest.raises(HTTPException) as exc:
        require_project_access_strict(cle, 99)
    assert exc.value.status_code == 403
    # donnée orpheline + clé -> 403 (pas d'ouverture silencieuse)
    with pytest.raises(HTTPException) as exc2:
        require_project_access_strict(cle, None)
    assert exc2.value.status_code == 403
    # même projet -> OK
    require_project_access_strict(cle, 10)


# ---------------------------------------------------------------------------
# 6. Confidentialité — l'export public ne fuit rien
# ---------------------------------------------------------------------------


def _jeu_export(db_session, neem, projet):
    _nom(db_session, neem, projet, "Neem", langue="dioula",
         niveau_confidentialite="public", source="enquête")
    _nom(db_session, neem, projet, "NomSecret", langue="moore",
         niveau_confidentialite="confidentiel")
    t.ajouter_usage_traditionnel(
        db_session, neem.id, projet, "décoction bue",
        niveau_confidentialite="public", source="enquête 2026",
    )
    t.ajouter_usage_traditionnel(
        db_session, neem.id, projet, "usage secret",
        niveau_confidentialite="restreint",
    )
    e, _ = t.creer_echantillon(
        db_session, projet, neem.id, "BF-PUB",
        latitude=12.3714, longitude=-1.5197, precision_gps="±5 m",
        commune="Ouagadougou", niveau_confidentialite="public",
    )
    e2, _ = t.creer_echantillon(
        db_session, projet, neem.id, "BF-CONF",
        latitude=1.0, longitude=2.0,
        niveau_confidentialite="confidentiel",
    )
    db_session.commit()
    return e


def test_export_public_ne_fuit_ni_gps_ni_restreint(
    db_session, neem, projet
):
    _jeu_export(db_session, neem, projet)
    export = t.export_public_plante(db_session, neem.id)
    # seuls les "public"
    assert [n["nom"] for n in export["noms_vernaculaires"]] == ["Neem"]
    assert len(export["usages_traditionnels"]) == 1
    usage = export["usages_traditionnels"][0]
    assert usage["bandeau"] == "USAGE TRADITIONNEL RAPPORTÉ"
    assert "informateur_code" not in usage
    # GPS précis jamais divulgué
    assert len(export["echantillons"]) == 1
    ech = export["echantillons"][0]
    assert "latitude" not in ech and "longitude" not in ech
    assert "precision_gps" not in ech
    assert ech["commune"] == "Ouagadougou"  # contexte textuel conservé
    assert "avertissement" in export


def test_export_public_api(client, neem, projet, db_session):
    _jeu_export(db_session, neem, projet)
    r = client.get(f"/api/science/terrain/export-public/plantes/{neem.id}")
    assert r.status_code == 200, r.text
    corps = r.json()
    assert len(corps["echantillons"]) == 1
    assert "latitude" not in corps["echantillons"][0]
    assert len(corps["usages_traditionnels"]) == 1
    # plante inconnue -> 404
    assert client.get(
        "/api/science/terrain/export-public/plantes/999999"
    ).status_code == 404


def test_export_public_exclut_supprimes(db_session, neem, projet):
    obj = _nom(db_session, neem, projet, "Neem",
               niveau_confidentialite="public")
    t.supprimer_doucement(db_session, obj)
    db_session.commit()
    export = t.export_public_plante(db_session, neem.id)
    assert export["noms_vernaculaires"] == []


# ---------------------------------------------------------------------------
# 7. UI — formulaires HTML simples
# ---------------------------------------------------------------------------


def test_ui_recherche_terrain_rendu(client):
    r = client.get("/recherche-terrain", params={"q": "neem"})
    assert r.status_code == 200
    assert "Recherche par nom" in r.text


def test_ui_hub_terrain_bandeau(client, neem, projet, db_session):
    # rattache un nom + usage au premier projet (projet UI en local)
    from webintel.database import models as core
    premier = db_session.query(core.Project).order_by(
        core.Project.id).first()
    pid = premier.id if premier else projet
    _nom(db_session, neem, pid, "Neem", langue="dioula")
    t.ajouter_usage_traditionnel(
        db_session, neem.id, pid, "décoction bue",
        niveau_confidentialite="projet",
    )
    db_session.commit()
    r = client.get(f"/plantes/{neem.id}/terrain")
    assert r.status_code == 200, r.text[:500]
    assert "USAGE TRADITIONNEL RAPPORTÉ" in r.text
    assert "Neem" in r.text
    assert "Données terrain" in r.text


def test_ui_post_nom_vernaculaire_formulaire(client, neem, projet,
                                            db_session):
    from webintel.database import models as core
    premier = db_session.query(core.Project).order_by(
        core.Project.id).first()
    # le formulaire poste sans project_id : l'UI utilise le projet session
    r = client.post(
        f"/plantes/{neem.id}/terrain/noms",
        data={"nom_original": "Neem", "langue": "dioula",
              "source": "test UI"},
    )
    assert r.status_code in (200, 303), r.text[:300]
    # le nom est bien enregistré (projet de la session UI)
    pid = premier.id if premier else projet
    noms = t.lister_noms_vernaculaires(db_session, neem.id, pid)
    assert any(n.nom_original == "Neem" for n in noms)
    # et visible sur le hub
    hub = client.get(f"/plantes/{neem.id}/terrain")
    assert "Neem" in hub.text


def test_ui_post_usage_formulaire_bandeau(client, neem, projet, db_session):
    r = client.post(
        f"/plantes/{neem.id}/terrain/usages",
        data={"usage": "macération bue le matin"},
    )
    assert r.status_code in (200, 303)
    hub = client.get(f"/plantes/{neem.id}/terrain")
    assert "USAGE TRADITIONNEL RAPPORTÉ" in hub.text
    assert "macération bue le matin" in hub.text
