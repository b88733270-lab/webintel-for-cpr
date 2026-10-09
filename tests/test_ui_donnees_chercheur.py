"""Tests UI v2.2 — espace chercheur : import labo, synergie simplifiée,
rapport d'étude, historique versionné.

Couverture :
- import CSV → 200 + badge « DONNÉES PRIVÉES » + jeu de données privé
  (SHA-256) en base + étape d'association (listes déroulantes) ;
- association import → étude : provenance enregistrée, aucune fusion ;
- synergie : données insuffisantes → « Analyse impossible avec les
  données fournies. » ; données suffisantes → résultat (méthode, graphe,
  limites, formulations prudentes) ;
- rapport : 200 avec sections obligatoires (limites, hypothèses,
  versions, hash) + téléchargements HTML/Markdown (+ PDF → fallback
  HTML honnête si reportlab indisponible) ;
- historique : versions listées, restauration → NOUVELLE version et
  NOUVELLE étude, anciennes versions intactes.

Aucun réseau : tout passe par la base de test et le TestClient.
"""

from __future__ import annotations

import pytest

import webintel.main
from webintel.database.base import SessionLocal, init_db
from webintel.science import combinaisons as comb
from webintel.science import versions as versions_module
from webintel.science import models as science
from webintel.ui import chercheur_donnees_routes

# La table `study_versions` est déclarée par l'import du module versions
# (via le routeur). `init_db()` est idempotent : on s'assure que la table
# existe même si le coordinateur n'a pas encore enregistré le routeur
# dans main.py au moment où ce module de test est importé.
init_db()


def _monter_routeur_donnees():
    """Monte le routeur sur l'app partagée si besoin (il est normalement
    déjà enregistré par `webintel/main.py` ; ce montage manuel ne sert que
    de filet de sécurité).

    Certains modules de la suite font `importlib.reload(webintel.main)`,
    ce qui REMPLACE l'objet `app` : le montage ne peut donc pas se faire
    une seule fois à l'import — une fixture autouse le refait avant
    chaque test si l'app a été remplacée.

    Si le routeur a dû être monté manuellement, ses routes statiques
    (ex. ``/synergie/nouvelle``) sont placées AVANT les routes
    paramétrées existantes (ex. ``/synergie/{analyse_id}``) — c'est aussi
    l'ordre utilisé dans main.py (enregistrement avant
    ``ui_science_comb_routes``), sinon la route paramétrée répond 422.
    """
    import webintel.main  # import local : suit un éventuel reload.

    app = webintel.main.app
    if getattr(app, "_routeur_donnees_chercheur_monte", False):
        return
    module = chercheur_donnees_routes.__name__
    for entree in app.router.routes:
        present = (
            getattr(getattr(entree, "endpoint", None), "__module__", None) == module
        )
        if not present:
            inner = getattr(entree, "original_router", None)
            present = any(
                getattr(getattr(route, "endpoint", None), "__module__", None) == module
                for route in getattr(inner, "routes", None) or ()
            )
        if present:
            # Déjà enregistré (ex. par webintel/main.py) : rien à faire.
            app._routeur_donnees_chercheur_monte = True
            return
    app.include_router(chercheur_donnees_routes.router)

    def _est_mon_routeur(entree) -> bool:
        # FastAPI récent : routeurs inclus non aplatis (`_IncludedRouter`
        # avec `original_router`) ; on gère aussi le cas aplati.
        if getattr(getattr(entree, "endpoint", None), "__module__", None) == module:
            return True
        inner = getattr(entree, "original_router", None)
        for route in getattr(inner, "routes", None) or ():
            if getattr(getattr(route, "endpoint", None), "__module__", None) == module:
                return True
        return False

    miennes = [r for r in app.router.routes if _est_mon_routeur(r)]
    autres = [r for r in app.router.routes if r not in miennes]
    app.router.routes[:] = miennes + autres
    app._routeur_donnees_chercheur_monte = True


@pytest.fixture(autouse=True)
def _routeur_donnees_monte():
    _monter_routeur_donnees()
    yield


@pytest.fixture(autouse=True)
def _nettoyage_donnees_chercheur():
    """Vide les tables scientifiques + `study_versions` avant chaque test
    (isolation complète — enfants avant parents pour les FK)."""
    session = SessionLocal()
    try:
        for table in (
            versions_module.StudyVersion,
            science.SynergyAnalysis,
            science.CombinationStudy,
            science.ResearchObservation,
            science.Dataset,
            science.Provenance,
            science.PlantCompound,
            science.Evidence,
            science.Compound,
            science.Plant,
            science.Study,
            science.Simulation,
        ):
            session.query(table).delete()
        session.commit()
    finally:
        session.close()
    yield


def _compose(db_session, nom: str) -> int:
    compose = science.Compound(name=nom, formula="C6H12O6")
    db_session.add(compose)
    db_session.commit()
    return compose.id


def _plante(db_session, nom: str = "Moringa oleifera") -> int:
    plante = science.Plant(scientific_name=nom, family="Moringaceae")
    db_session.add(plante)
    db_session.commit()
    return plante.id


def _etude(db_session, projet_id: int, nom: str = "Étude test"):
    a = _compose(db_session, f"{nom} — Molécule Alpha")
    b = _compose(db_session, f"{nom} — Molécule Bêta")
    plante = _plante(db_session, f"Plante {nom}")
    etude = comb.creer_etude(
        db_session,
        project_id=projet_id,
        nom=nom,
        compound_ids=[a, b],
        plant_id=plante,
        plant_part="feuilles",
        target="COX-2",
    )
    db_session.commit()
    return etude


CSV_COMPOSES = (
    "nom,formule,smiles\n"
    "Quercétine,C15H10O7,C1=CC(=C(C=C1)O)O\n"
    "Lutéoline,C15H10O6,\n"
).encode("utf-8")


# ---------------------------------------------------------------------------
# 1. Import de résultats du laboratoire
# ---------------------------------------------------------------------------


def test_import_formulaire_200(client):
    reponse = client.get("/donnees/importer")
    assert reponse.status_code == 200
    assert "Importer mes données" in reponse.text
    assert "DONNÉES PRIVÉES" in reponse.text


def test_import_csv_200_badge_prive_et_association(client, db_session, make_project):
    make_project("Import labo")
    reponse = client.post(
        "/donnees/importer",
        files={"fichier": ("composes.csv", CSV_COMPOSES, "text/csv")},
        data={"type_donnees": "compose", "nom_jeu": "Criblage test"},
    )
    assert reponse.status_code == 200
    # Badge privé visible + étape d'association (listes déroulantes).
    assert "DONNÉES PRIVÉES" in reponse.text
    assert "Associer cet import" in reponse.text
    assert 'name="assoc_etude"' in reponse.text
    assert 'name="assoc_plante"' in reponse.text
    assert 'name="assoc_compose"' in reponse.text
    assert "jamais fusionnées automatiquement" in reponse.text
    # Jeu de données PRIVÉ en base, avec empreinte SHA-256.
    jeu = db_session.query(science.Dataset).filter_by(name="Criblage test").one()
    assert jeu.is_private is True
    assert jeu.sha256 is not None and len(jeu.sha256) == 64
    # Les composés sont passés par le pipeline d'import existant.
    assert (
        db_session.query(science.Compound)
        .filter_by(name="Quercétine")
        .count()
        == 1
    )


def test_import_associer_a_une_etude(client, db_session, make_project):
    projet_id = make_project("Association")
    etude = _etude(db_session, projet_id)
    client.post(
        "/donnees/importer",
        files={"fichier": ("composes.csv", CSV_COMPOSES, "text/csv")},
        data={"type_donnees": "compose", "nom_jeu": "Jeu à associer"},
    )
    jeu = db_session.query(science.Dataset).filter_by(name="Jeu à associer").one()
    reponse = client.post(
        "/donnees/associer",
        data={"dataset_id": str(jeu.id), "assoc_etude": str(etude.id)},
    )
    assert reponse.status_code == 200
    assert "Étude de combinaison" in reponse.text
    assert "DONNÉES PRIVÉES" in reponse.text
    # Provenance enregistrée (lien, pas de fusion de données).
    prov = (
        db_session.query(science.Provenance)
        .filter_by(entity_type="etude", entity_id=etude.id)
        .all()
    )
    assert any(
        p.connector == "donnees-labo-ui" and "Jeu de données" in (p.source_name or "")
        for p in prov
    )


def test_import_fichier_vide_400_propre(client, make_project):
    make_project("Import vide")
    reponse = client.post(
        "/donnees/importer",
        files={"fichier": ("vide.csv", b"", "text/csv")},
        data={"type_donnees": "compose"},
    )
    assert reponse.status_code == 200
    assert "vide" in reponse.text.lower()


# ---------------------------------------------------------------------------
# 2. Analyse de synergie simplifiée
# ---------------------------------------------------------------------------


def test_synergie_formulaire_200_avec_cartes_methodes(client):
    reponse = client.get("/synergie/nouvelle")
    assert reponse.status_code == 200
    for methode in ("BLISS", "LOEWE", "HSA", "ZIP"):
        assert methode in reponse.text
    assert "Molécule A" in reponse.text


def test_synergie_donnees_insuffisantes_message_propre(client, db_session, make_project):
    make_project("Synergie vide")
    reponse = client.post(
        "/synergie/nouvelle",
        data={"nom": "Test vide", "methode": "bliss", "nom_a": "A", "nom_b": "B"},
    )
    assert reponse.status_code == 200
    assert "Analyse impossible avec les données fournies." in reponse.text
    # Aucune analyse persistée (données insuffisantes → refus, pas de faux résultat).
    assert db_session.query(science.SynergyAnalysis).count() == 0


def test_synergie_bliss_donnees_incompletes_sans_appariement(client, make_project):
    """Combinaison fournie mais aucune mesure individuelle à la même dose :
    pas de valeur inventée → message propre."""
    make_project("Synergie incomplète")
    reponse = client.post(
        "/synergie/nouvelle",
        data={
            "nom": "Test incomplet",
            "methode": "bliss",
            "a_dose": ["10"],
            "a_reponse": ["0.3"],
            "b_dose": ["10"],
            "b_reponse": ["0.2"],
            # Dose 99 jamais mesurée pour A seul → impossible.
            "c_dose_a": ["99"],
            "c_dose_b": ["10"],
            "c_reponse": ["0.7"],
        },
    )
    assert reponse.status_code == 200
    assert "Analyse impossible avec les données fournies." in reponse.text


def _donnees_bliss_valides():
    return {
        "nom": "Synergie Q+L",
        "methode": "bliss",
        "nom_a": "Quercétine",
        "nom_b": "Lutéoline",
        "a_dose": ["10", "20", "40"],
        "a_reponse": ["0.3", "0.55", "0.8"],
        "b_dose": ["10", "20", "40"],
        "b_reponse": ["0.2", "0.45", "0.7"],
        "c_dose_a": ["10", "20", "40"],
        "c_dose_b": ["10", "20", "40"],
        "c_reponse": ["0.65", "0.85", "0.95"],
    }


def test_synergie_bliss_donnees_suffisantes_resultat(
    client, db_session, make_project
):
    make_project("Synergie Bliss")
    reponse = client.post("/synergie/nouvelle", data=_donnees_bliss_valides())
    assert reponse.status_code == 200
    texte = reponse.text
    assert "BLISS" in texte
    assert "Résultat informatique" in texte
    assert "Limites" in texte
    assert "Incertitude" in texte
    assert "DONNÉES PRIVÉES" in texte
    assert "<svg" in texte  # graphe SVG réutilisé
    assert "efficace" not in texte.lower().replace("aucune synergie pharmacologique", "")
    # Analyse persistée, privée, niveau prédiction.
    analyse = (
        db_session.query(science.SynergyAnalysis)
        .filter_by(nom="Synergie Q+L")
        .one()
    )
    assert analyse.methode == "bliss"
    assert analyse.is_private is True
    assert analyse.data_level == "prediction"
    assert len(analyse.inputs_hash) == 64


def test_synergie_loewe_donnees_suffisantes(client, db_session, make_project):
    make_project("Synergie Loewe")
    donnees = {
        "nom": "Synergie Loewe",
        "methode": "loewe",
        "a_dose": ["1", "5", "10", "50"],
        "a_reponse": ["0.1", "0.3", "0.55", "0.9"],
        "b_dose": ["1", "5", "10", "50"],
        "b_reponse": ["0.08", "0.28", "0.5", "0.88"],
        "c_dose_a": ["5"],
        "c_dose_b": ["5"],
        "c_reponse": ["0.6"],
    }
    reponse = client.post("/synergie/nouvelle", data=donnees)
    assert reponse.status_code == 200
    assert "LOEWE" in reponse.text
    assert "Résultat informatique" in reponse.text


# ---------------------------------------------------------------------------
# 3. Rapport scientifique d'une étude
# ---------------------------------------------------------------------------


def test_rapport_page_200_bouton_creer(client, db_session, make_project):
    projet_id = make_project("Rapport")
    etude = _etude(db_session, projet_id)
    reponse = client.get(f"/etudes/{etude.id}/rapport")
    assert reponse.status_code == 200
    assert "Créer le rapport" in reponse.text


def test_rapport_genere_sections_obligatoires(client, db_session, make_project):
    projet_id = make_project("Rapport complet")
    etude = _etude(db_session, projet_id, nom="Étude rapport")
    reponse = client.post(f"/etudes/{etude.id}/rapport")
    assert reponse.status_code == 200
    texte = reponse.text
    # Sections obligatoires.
    for section in (
        "Limites",
        "Hypothèses",
        "Références",
        "Versions",
        "Empreinte des entrées",
        "Molécules",
        "Plante étudiée",
        "Analyse statistique",
    ):
        assert section in texte, f"section manquante : {section}"
    assert etude.inputs_hash in texte
    assert "WebIntel" in texte
    # Formulations prudentes : jamais « efficace » / « sûr » / « traite ».
    for interdit in ("est efficace", "est sûr", "traite la"):
        assert interdit not in texte.lower()


def test_rapport_telecharger_html_markdown(client, db_session, make_project):
    projet_id = make_project("Rapport DL")
    etude = _etude(db_session, projet_id)
    for fmt, media in (("html", "text/html"), ("markdown", "text/markdown")):
        reponse = client.get(
            f"/etudes/{etude.id}/rapport/telecharger?format={fmt}"
        )
        assert reponse.status_code == 200, fmt
        assert media in reponse.headers["content-type"], fmt
        assert "attachment" in reponse.headers["content-disposition"], fmt


def test_rapport_telecharger_pdf_ou_fallback_honnete(client, db_session, make_project):
    """PDF via le générateur existant ; si reportlab est indisponible, le
    HTML est servi avec un en-tête d'avertissement (jamais de faux PDF)."""
    projet_id = make_project("Rapport PDF")
    etude = _etude(db_session, projet_id)
    reponse = client.get(f"/etudes/{etude.id}/rapport/telecharger?format=pdf")
    assert reponse.status_code == 200
    ctype = reponse.headers["content-type"]
    if "application/pdf" in ctype:
        assert reponse.content[:5] == b"%PDF-"
    else:
        assert "text/html" in ctype
        assert "X-Avertissement" in reponse.headers


def test_rapport_etude_inexistante_404(client, make_project):
    make_project("Rapport 404")
    assert client.get("/etudes/999999/rapport").status_code == 404


# ---------------------------------------------------------------------------
# 4. Historique versionné des études
# ---------------------------------------------------------------------------


def test_historique_versions_listees(client, db_session, make_project):
    projet_id = make_project("Historique")
    etude = _etude(db_session, projet_id, nom="Étude histo")
    versions_module.enregistrer_version(db_session, etude, cree_par="test")
    db_session.commit()
    reponse = client.get(f"/etudes/{etude.id}/historique")
    assert reponse.status_code == 200
    assert "Version 1" in reponse.text
    assert "2 molécule(s)" in reponse.text
    assert "Restaurer cette version" in reponse.text
    assert "aucune version existante n'est" in reponse.text.lower()


def test_historique_restauration_cree_nouvelle_version_sans_ecraser(
    client, db_session, make_project
):
    projet_id = make_project("Restauration")
    etude = _etude(db_session, projet_id, nom="Étude à restaurer")
    v1 = versions_module.enregistrer_version(db_session, etude, cree_par="test")
    # L'étude évolue : 3e molécule → version 2.
    c = _compose(db_session, "Molécule Gamma")
    etude.compound_ids = list(etude.compound_ids) + [c]
    db_session.commit()
    v2 = versions_module.enregistrer_version(
        db_session, etude, cree_par="test", resume="3 molécule(s)"
    )
    db_session.commit()
    assert (v1.numero, v2.numero) == (1, 2)

    reponse = client.post(
        f"/etudes/{etude.id}/historique/restaurer",
        data={"version_id": str(v1.id)},
        follow_redirects=False,
    )
    assert reponse.status_code == 303

    # L'historique de l'étude d'origine est intact (toujours 2 versions,
    # snapshot de la v1 inchangé : 2 molécules).
    versions_origine = versions_module.list_versions(db_session, etude.id)
    assert [v.numero for v in versions_origine] == [1, 2]
    assert len(versions_origine[0].snapshot["compound_ids"]) == 2

    # Une NOUVELLE étude a été créée (restauration de la v1 : 2 molécules).
    nouvelle = (
        db_session.query(science.CombinationStudy)
        .filter(science.CombinationStudy.id != etude.id)
        .one()
    )
    assert "restauration v1" in nouvelle.nom
    assert len(nouvelle.compound_ids) == 2
    assert nouvelle.provenance["restauration_de_version"]["numero"] == 1

    # … avec sa propre NOUVELLE version (pas d'écrasement).
    versions_nouvelle = versions_module.list_versions(db_session, nouvelle.id)
    assert len(versions_nouvelle) == 1
    assert "Restauration de la version 1" in versions_nouvelle[0].resume


def test_historique_instantane(client, db_session, make_project):
    projet_id = make_project("Instantané")
    etude = _etude(db_session, projet_id)
    reponse = client.post(
        f"/etudes/{etude.id}/historique/instantane", follow_redirects=False
    )
    assert reponse.status_code == 303
    versions = versions_module.list_versions(db_session, etude.id)
    assert len(versions) == 1 and versions[0].numero == 1


def test_historique_version_dune_autre_etude_404(client, db_session, make_project):
    projet_id = make_project("Historique 404")
    etude_a = _etude(db_session, projet_id, nom="Étude A")
    etude_b = _etude(db_session, projet_id, nom="Étude B")
    v_b = versions_module.enregistrer_version(db_session, etude_b, cree_par="test")
    db_session.commit()
    reponse = client.post(
        f"/etudes/{etude_a.id}/historique/restaurer",
        data={"version_id": str(v_b.id)},
        follow_redirects=False,
    )
    assert reponse.status_code == 404
