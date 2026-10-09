"""Tests de l'import de données scientifiques — Phase 9.

Couverture :
- import CSV → vérification base de données ;
- réimport strictement idempotent (2× → 0 doublon, 2e rapport tout en « ignores ») ;
- import JSON et Excel ;
- lignes invalides → erreurs détaillées {ligne_numero, champ, message},
  sans crash ni rollback total (les lignes valides sont importées) ;
- SDF minimal → composés créés avec limitation documentée ;
- SDF invalide → erreur explicite, aucun faux composé ;
- endpoint POST /api/science/import : 200, 400 (type/format), 413 (> 10 Mo).

Aucun réseau : tout passe par la base de test et le TestClient.
"""

from __future__ import annotations

import io
import json

import pytest
from openpyxl import Workbook

import webintel.main
from webintel.api.routers import science_import
from webintel.database.base import SessionLocal
from webintel.science import import_export as importeur
from webintel.science import models as science

# Monte le routeur sur l'app partagée (le coordinateur l'enregistrera dans
# main.py ; ici on le fait uniquement pour les tests d'endpoint, sans
# toucher à main.py).
#
# Certains modules de la suite (test_server_config, test_phase0_server_config)
# font `importlib.reload(webintel.main)`, ce qui REMPLACE l'objet `app` :
# le montage ne peut donc pas se faire une seule fois à l'import — une
# fixture autouse le refait avant chaque test si l'app a été remplacée.
def _monter_routeur_import():
    import webintel.main  # import local : suit un éventuel reload.

    app = webintel.main.app
    if not getattr(app, "_routeur_import_monte", False):
        app.include_router(science_import.router)
        app._routeur_import_monte = True


@pytest.fixture(autouse=True)
def _routeur_import_monte():
    _monter_routeur_import()
    yield

DOSSIER_EXEMPLES = "docs/exemples_import"


@pytest.fixture(autouse=True)
def _nettoyage_science():
    """Vide les tables scientifiques avant chaque test (isolation complète)."""
    session = SessionLocal()
    try:
        for table in (
            science.ResearchObservation,
            science.Simulation,
            science.Evidence,
            science.PlantCompound,
            science.Compound,
            science.Plant,
        ):
            session.query(table).delete()
        session.commit()
    finally:
        session.close()
    yield


def _lire_exemple(nom: str) -> bytes:
    with open(f"{DOSSIER_EXEMPLES}/{nom}", "rb") as f:
        return f.read()


def _session():
    return SessionLocal()


# ---------------------------------------------------------------------------
# Import CSV → base de données
# ---------------------------------------------------------------------------


def test_import_csv_plantes_cree_en_base():
    session = _session()
    try:
        rapport = importeur.importer_fichier(
            session, _lire_exemple("plantes.csv"), "plantes.csv", "plante"
        )
        session.commit()
    finally:
        session.close()
    assert rapport["crees"] == 3
    assert rapport["mis_a_jour"] == 0
    assert rapport["ignores"] == 0
    assert rapport["erreurs"] == []
    assert rapport["niveau_donnees"] == "publie"

    session = _session()
    try:
        plantes = {p.scientific_name: p for p in session.query(science.Plant).all()}
    finally:
        session.close()
    assert set(plantes) == {
        "Moringa oleifera",
        "Artemisia annua",
        "Adansonia digitata",
    }
    assert plantes["Moringa oleifera"].family == "Moringaceae"
    assert plantes["Moringa oleifera"].used_parts == ["feuilles", "graines"]
    assert plantes["Moringa oleifera"].vernacular_names == {
        "fr": "Moringa",
        "en": "Drumstick tree",
    }


def test_reimport_strictement_idempotent():
    contenu = _lire_exemple("plantes.csv")
    session = _session()
    try:
        rapport1 = importeur.importer_fichier(session, contenu, "plantes.csv", "plante")
        session.commit()
        rapport2 = importeur.importer_fichier(session, contenu, "plantes.csv", "plante")
        session.commit()
        nombre = session.query(science.Plant).count()
    finally:
        session.close()
    assert rapport1["crees"] == 3
    assert rapport2["crees"] == 0
    assert rapport2["mis_a_jour"] == 0
    assert rapport2["ignores"] == 3
    assert rapport2["erreurs"] == []
    assert nombre == 3, "réimport → 0 doublon"


def test_reimport_avec_changement_compte_mis_a_jour():
    contenu = _lire_exemple("plantes.csv")
    session = _session()
    try:
        importeur.importer_fichier(session, contenu, "plantes.csv", "plante")
        session.commit()
        modifie = contenu.replace(
            "Arbre tropical aux feuilles".encode("utf-8"),
            "Arbre tropical aux feuilles MODIFIÉ".encode("utf-8"),
        )
        rapport = importeur.importer_fichier(session, modifie, "plantes.csv", "plante")
        session.commit()
        plante = (
            session.query(science.Plant)
            .filter_by(scientific_name="Moringa oleifera")
            .one()
        )
        description = plante.description
    finally:
        session.close()
    assert rapport["crees"] == 0
    assert rapport["mis_a_jour"] == 1
    assert rapport["ignores"] == 2
    assert "MODIFIÉ" in description


# ---------------------------------------------------------------------------
# JSON et Excel
# ---------------------------------------------------------------------------


def test_import_json_composes():
    donnees = [
        {"nom": "Quercétine", "formule": "C15H10O7", "poids_moleculaire": 302.24},
        {"nom": "Artémisinine", "smiles": "C[C@@H]1CC[C@H]2"},
    ]
    session = _session()
    try:
        rapport = importeur.importer_fichier(
            session,
            json.dumps(donnees).encode("utf-8"),
            "composes.json",
            "compose",
        )
        session.commit()
        compose = session.query(science.Compound).filter_by(name="Quercétine").one()
    finally:
        session.close()
    assert rapport["crees"] == 2
    assert rapport["erreurs"] == []
    assert compose.formula == "C15H10O7"
    assert compose.molecular_weight == 302.24


def test_import_excel_exemple_xlsx():
    session = _session()
    try:
        rapport = importeur.importer_fichier(
            session, _lire_exemple("exemple.xlsx"), "exemple.xlsx", "plante"
        )
        session.commit()
        nombre = session.query(science.Plant).count()
    finally:
        session.close()
    # Première feuille = plantes (3 lignes).
    assert rapport["crees"] == 3
    assert rapport["erreurs"] == []
    assert nombre == 3


def test_import_excel_composes_construit():
    classeur = Workbook()
    feuille = classeur.active
    feuille.append(["nom", "formule", "poids_moleculaire"])
    feuille.append(["Caféine", "C8H10N4O2", 194.19])
    feuille.append(["", "", ""])  # ligne vide → ignorée silencieusement
    tampon = io.BytesIO()
    classeur.save(tampon)
    session = _session()
    try:
        rapport = importeur.importer_fichier(
            session, tampon.getvalue(), "composes.xlsx", "compose"
        )
        session.commit()
    finally:
        session.close()
    assert rapport["crees"] == 1
    assert rapport["erreurs"] == []


# ---------------------------------------------------------------------------
# Lignes invalides : erreurs détaillées, pas de rollback total
# ---------------------------------------------------------------------------


def test_lignes_invalides_rejetees_sans_rollback_total():
    csv_melange = (
        "nom_scientifique;famille\n"
        "Moringa oleifera;Moringaceae\n"
        ";FamilleSansNom\n"
        "   ;   \n"
        "Artemisia annua;Asteraceae\n"
    ).encode("utf-8")
    session = _session()
    try:
        rapport = importeur.importer_fichier(session, csv_melange, "melange.csv", "plante")
        session.commit()
        nombre = session.query(science.Plant).count()
    finally:
        session.close()
    assert rapport["crees"] == 2, "les lignes valides sont importées"
    assert nombre == 2
    assert len(rapport["erreurs"]) == 1
    erreur = rapport["erreurs"][0]
    assert erreur["ligne_numero"] == 3
    assert erreur["champ"] == "nom_scientifique"
    assert "requis" in erreur["message"]


def test_champ_numerique_invalide_detaille():
    csv_mauvais = (
        "nom;formule;poids_moleculaire\n"
        "Quercétine;C15H10O7;302.24\n"
        "Faux;C1;pasunnombre\n"
    ).encode("utf-8")
    session = _session()
    try:
        rapport = importeur.importer_fichier(session, csv_mauvais, "c.csv", "compose")
        session.commit()
        nombre = session.query(science.Compound).count()
    finally:
        session.close()
    assert rapport["crees"] == 1
    assert nombre == 1
    assert len(rapport["erreurs"]) == 1
    assert rapport["erreurs"][0]["ligne_numero"] == 3
    assert rapport["erreurs"][0]["champ"] == "poids_moleculaire"


def test_lien_plante_inconnue_rejete_explicitement():
    csv_lien = (
        "plante;compose;partie_plante\n"
        "Plante Inexistante;Quercétine;feuilles\n"
    ).encode("utf-8")
    session = _session()
    try:
        rapport = importeur.importer_fichier(session, csv_lien, "liens.csv", "lien")
        session.commit()
        nombre = session.query(science.PlantCompound).count()
    finally:
        session.close()
    assert rapport["crees"] == 0
    assert nombre == 0
    assert len(rapport["erreurs"]) == 1
    assert "introuvable" in rapport["erreurs"][0]["message"]


def test_import_liens_apres_plantes_et_composes():
    session = _session()
    try:
        importeur.importer_fichier(session, _lire_exemple("plantes.csv"), "p.csv", "plante")
        importeur.importer_fichier(session, _lire_exemple("composes.csv"), "c.csv", "compose")
        rapport = importeur.importer_fichier(session, _lire_exemple("liens.csv"), "l.csv", "lien")
        session.commit()
        lien = session.query(science.PlantCompound).first()
    finally:
        session.close()
    assert rapport["crees"] == 3
    assert rapport["erreurs"] == []
    assert lien.confidence == "publie"
    # Idempotence des liens.
    session = _session()
    try:
        rapport2 = importeur.importer_fichier(
            session, _lire_exemple("liens.csv"), "l.csv", "lien"
        )
        session.commit()
        nombre = session.query(science.PlantCompound).count()
    finally:
        session.close()
    assert rapport2["ignores"] == 3
    assert nombre == 3


def test_conflit_inchikey_ligne_rejetee_sans_crash():
    session = _session()
    try:
        importeur.importer_fichier(
            session,
            b"nom;inchikey\nQuercetine;REFJWTPEDVJJIY-UHFFFAOYSA-N\n",
            "a.csv",
            "compose",
        )
        session.commit()
        rapport = importeur.importer_fichier(
            session,
            b"nom;inchikey\nAutre Nom;REFJWTPEDVJJIY-UHFFFAOYSA-N\n",
            "b.csv",
            "compose",
        )
        session.commit()
        nombre = session.query(science.Compound).count()
    finally:
        session.close()
    assert rapport["crees"] == 0
    assert len(rapport["erreurs"]) == 1
    assert "InChIKey" in rapport["erreurs"][0]["message"]
    assert nombre == 1


def test_evidences_json_idempotent_avec_et_sans_doi():
    session = _session()
    try:
        contenu = _lire_exemple("evidences.json")
        r1 = importeur.importer_fichier(session, contenu, "evidences.json", "evidence")
        session.commit()
        r2 = importeur.importer_fichier(session, contenu, "evidences.json", "evidence")
        session.commit()
        nombre = session.query(science.Evidence).count()
    finally:
        session.close()
    assert r1["crees"] == 2
    assert r2["crees"] == 0
    assert r2["ignores"] == 2
    assert r2["erreurs"] == []
    assert nombre == 2


def test_evidence_sans_reference_ni_titre_rejetee():
    session = _session()
    try:
        rapport = importeur.importer_fichier(
            session,
            json.dumps([{"doi": "10.1234/x", "annee": 2020}]).encode(),
            "e.json",
            "evidence",
        )
        session.commit()
    finally:
        session.close()
    assert rapport["crees"] == 0
    assert len(rapport["erreurs"]) == 1
    assert "reference" in rapport["erreurs"][0]["message"]


# ---------------------------------------------------------------------------
# SDF minimal
# ---------------------------------------------------------------------------


def test_sdf_minimal_cree_composes_avec_limitation():
    session = _session()
    try:
        rapport = importeur.importer_fichier(
            session, _lire_exemple("exemple.sdf"), "exemple.sdf", "compose"
        )
        session.commit()
        composes = {c.name: c for c in session.query(science.Compound).all()}
    finally:
        session.close()
    assert rapport["crees"] == 2
    assert rapport["erreurs"] == []
    assert rapport["niveau_donnees"] == "normalise"
    assert rapport["avertissements"], "avertissement SDF minimal requis"
    quercetine = composes["Quercétine"]
    assert quercetine.formula == "C15H10O7"
    assert quercetine.smiles.startswith("C1=CC")
    assert quercetine.inchikey == "REFJWTPEDVJJIY-UHFFFAOYSA-N"
    assert quercetine.chemical_class == "Flavonoïde"
    note = quercetine.external_ids.get("note_import_sdf", "")
    assert "atomes/liaisons" in note, "limitation documentée dans external_ids"
    assert "RDKit" in note


def test_sdf_reimport_idempotent():
    contenu = _lire_exemple("exemple.sdf")
    session = _session()
    try:
        importeur.importer_fichier(session, contenu, "exemple.sdf", "compose")
        session.commit()
        rapport = importeur.importer_fichier(session, contenu, "exemple.sdf", "compose")
        session.commit()
        nombre = session.query(science.Compound).count()
    finally:
        session.close()
    assert rapport["ignores"] == 2
    assert nombre == 2


def test_sdf_invalide_erreur_explicite_sans_faux_compose():
    session = _session()
    try:
        with pytest.raises(importeur.SDFInvalide, match="$$$$"):
            importeur.importer_fichier(
                session, b"ceci n'est pas du SDF du tout", "faux.sdf", "compose"
            )
        nombre = session.query(science.Compound).count()
    finally:
        session.close()
    assert nombre == 0, "aucun faux composé créé"


def test_sdf_sans_molecule_erreur_explicite():
    session = _session()
    try:
        with pytest.raises(importeur.SDFInvalide, match="aucune mol"):
            importeur.importer_fichier(session, b"$$$$\n$$$$\n", "vide.sdf", "compose")
    finally:
        session.close()


def test_sdf_refuse_pour_autre_type_que_compose():
    session = _session()
    try:
        with pytest.raises(importeur.ErreurImport, match="compose"):
            importeur.importer_fichier(
                session, _lire_exemple("exemple.sdf"), "exemple.sdf", "plante"
            )
    finally:
        session.close()


def test_format_inconnu_erreur_400_explicite():
    session = _session()
    try:
        with pytest.raises(importeur.ErreurImport, match="non supporté"):
            importeur.importer_fichier(session, b"xxx", "fichier.txt", "plante")
    finally:
        session.close()


def test_type_donnees_inconnu_erreur_explicite():
    session = _session()
    try:
        with pytest.raises(importeur.ErreurImport, match="Type de données"):
            importeur.importer_fichier(session, b"a,b\nc,d\n", "f.csv", "animal")
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_provenance_enregistree_pour_items_importes(monkeypatch):
    appels = []

    def fausse_provenance(session, *, type_donnees, item_id, connecteur_nom,
                          version_donnees=None, licence=None):
        appels.append(
            {
                "type_donnees": type_donnees,
                "item_id": item_id,
                "connecteur_nom": connecteur_nom,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(importeur, "enregistrer_provenance", fausse_provenance)
    session = _session()
    try:
        rapport = importeur.importer_fichier(
            session, _lire_exemple("plantes.csv"), "plantes.csv", "plante"
        )
        session.commit()
        # Réimport : tout est ignoré → aucun nouvel appel de provenance.
        importeur.importer_fichier(session, _lire_exemple("plantes.csv"), "plantes.csv", "plante")
        session.commit()
    finally:
        session.close()
    assert rapport["crees"] == 3
    assert len(appels) == 3
    assert all(a["connecteur_nom"] == "fichier:plantes.csv" for a in appels)
    assert all(a["type_donnees"] == "plante" for a in appels)
    assert all(isinstance(a["item_id"], int) for a in appels)


# ---------------------------------------------------------------------------
# Endpoint POST /api/science/import
# ---------------------------------------------------------------------------


def test_endpoint_import_csv_200(client):
    reponse = client.post(
        "/api/science/import/",
        files={"fichier": ("plantes.csv", _lire_exemple("plantes.csv"), "text/csv")},
        data={"type_donnees": "plante"},
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["rapport"]["crees"] == 3
    assert corps["rapport"]["erreurs"] == []
    assert corps["provenance"]["source"] == "plantes.csv"
    assert "date_import" in corps["provenance"]
    assert "avertissement" not in corps

    session = _session()
    try:
        assert session.query(science.Plant).count() == 3
    finally:
        session.close()


def test_endpoint_sdf_200_avec_avertissement(client):
    reponse = client.post(
        "/api/science/import/",
        files={"fichier": ("exemple.sdf", _lire_exemple("exemple.sdf"), "chemical/x-mdl-sdfile")},
        data={"type_donnees": "compose"},
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["rapport"]["crees"] == 2
    assert corps["rapport"]["niveau_donnees"] == "normalise"
    assert "avertissement" in corps


def test_endpoint_400_type_donnees_inconnu(client):
    reponse = client.post(
        "/api/science/import/",
        files={"fichier": ("plantes.csv", _lire_exemple("plantes.csv"), "text/csv")},
        data={"type_donnees": "animal"},
    )
    assert reponse.status_code == 400
    assert "animal" in reponse.json()["detail"]


def test_endpoint_400_format_non_supporte(client):
    reponse = client.post(
        "/api/science/import/",
        files={"fichier": ("notes.txt", b"du texte", "text/plain")},
        data={"type_donnees": "plante"},
    )
    assert reponse.status_code == 400
    assert "non supporté" in reponse.json()["detail"]


def test_endpoint_413_fichier_trop_volumineux(client):
    gros = b"nom_scientifique;famille\n" + b"x" * (importeur.TAILLE_MAX_FICHIER + 1)
    reponse = client.post(
        "/api/science/import/",
        files={"fichier": ("gros.csv", gros, "text/csv")},
        data={"type_donnees": "plante"},
    )
    assert reponse.status_code == 413
    assert "10 Mo" in reponse.json()["detail"]


def test_endpoint_400_fichier_vide(client):
    reponse = client.post(
        "/api/science/import/",
        files={"fichier": ("vide.csv", b"", "text/csv")},
        data={"type_donnees": "plante"},
    )
    assert reponse.status_code == 400
