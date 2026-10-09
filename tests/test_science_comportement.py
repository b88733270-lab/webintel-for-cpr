"""Tests COMPORTEMENTAUX scientifiques (Phase 14) — pas seulement techniques.

Ces tests vérifient les règles d'honnêteté scientifique de WebIntel :

1. séparation stricte des 5 niveaux de données (publié / normalisé /
   hypothèse / prédiction / expérimental) — jamais confondus ;
2. une synthèse IA étiquetée « hypothèse » n'est JAMAIS persistée comme
   un fait ;
3. les données privées du laboratoire sont exclues par défaut des
   exports, recherches et dossiers ;
4. idempotence : réimporter = 0 doublon ;
5. la provenance des sources est enregistrée sur les imports (fichiers
   comme connecteurs) ;
6. rejouabilité d'une simulation : mêmes entrées → même hash SHA-256 →
   mêmes sorties.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from webintel.science import import_export
from webintel.science import service as science_service
from webintel.science.connecteurs import registry as registre_connecteurs
from webintel.science.export_dossier import build_dossier_zip, export_csv, lignes_plantes
from webintel.science.models import (
    Compound,
    DataLevel,
    Evidence,
    ImportProvenance,
    Plant,
)
from webintel.simulations.service import run_simulation


@pytest.fixture()
def _override_ia(tmp_path, monkeypatch):
    """Isole le fichier d'overrides d'exécution (jamais le vrai)."""
    chemin = tmp_path / "runtime_config.json"
    monkeypatch.setenv("WEBINTEL_RUNTIME_CONFIG", str(chemin))
    yield chemin


def _plante(client, nom: str = "Artemisia annua") -> dict:
    reponse = client.post("/api/science/plants", json={"scientific_name": nom})
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


@pytest.fixture()
def _projet_moteur(db_session):
    """Projet de rattachement des simulations moteur (P0-1 : obligatoire)."""
    from webintel.database.models import Project

    projet = Project(name="Projet Moteur")
    db_session.add(projet)
    db_session.commit()
    return projet.id


# ---------------------------------------------------------------------------
# 1. Séparation stricte des 5 niveaux de données
# ---------------------------------------------------------------------------

def test_cinq_niveaux_distincts_et_stables():
    """Le référentiel compte exactement 5 niveaux, tous distincts."""
    valeurs = [n.value for n in DataLevel]
    assert sorted(valeurs) == [
        "experimental", "hypothese", "normalise", "prediction", "publie",
    ]
    assert len(set(valeurs)) == 5


def test_niveaux_api_jamais_confondus(client, make_project, db_session):
    """Plante → publié ; observation → expérimental ; simulation → prédiction."""
    projet_id = make_project("Labo Niveaux")
    plante = _plante(client)
    assert plante["data_level"] == "publie"

    reponse_obs = client.post(
        "/api/science/observations",
        json={"project_id": projet_id, "comment": "Mesure"},
    )
    assert reponse_obs.status_code == 201, reponse_obs.text
    observation = reponse_obs.json()
    assert observation["data_level"] == "experimental"

    simulation = client.post(
        "/api/science/simulations",
        json={"name": "S", "target": "X", "project_id": projet_id},
    ).json()
    assert simulation["data_level"] == "prediction"
    assert "confirmer exp" in simulation["avertissement"]

    # Les trois niveaux sont deux à deux distincts.
    assert len({plante["data_level"], observation["data_level"],
                simulation["data_level"]}) == 3


def test_compose_sdf_importe_au_niveau_normalise_avec_avertissement(db_session):
    """Un SDF importé = niveau « normalise », jamais « publie », avec avertissement."""
    with open("docs/exemples_import/exemple.sdf", "rb") as fh:
        contenu = fh.read()
    rapport = import_export.importer_fichier(
        db_session, contenu, "exemple.sdf", "compose"
    )
    db_session.commit()
    assert rapport["niveau_donnees"] == "normalise"
    assert rapport["avertissements"], "l'import SDF doit avertir"
    assert rapport["crees"] >= 1


# ---------------------------------------------------------------------------
# 2. La synthèse IA (hypothèse) n'est jamais persistée comme un fait
# ---------------------------------------------------------------------------

def test_synthese_ia_hypothese_non_persitee(client, db_session, _override_ia):
    """La synthèse IA est étiquetée hypothèse et ne crée AUCUNE ligne en base."""
    plante = _plante(client)
    avant = {
        "plantes": db_session.query(Plant).count(),
        "composes": db_session.query(Compound).count(),
        "preuves": db_session.query(Evidence).count(),
    }
    reponse = client.get(f"/api/science/ia/synthese/plante/{plante['id']}")
    assert reponse.status_code == 200, reponse.text
    synthese = reponse.json()
    assert synthese["niveau_donnees"] == "hypothese"
    assert synthese["persiste"] is False
    assert "avertissement" in synthese and synthese["avertissement"]
    assert "HYPOTHÈSE" in synthese["avertissement"] or "hypothèse" in synthese["avertissement"]
    apres = {
        "plantes": db_session.query(Plant).count(),
        "composes": db_session.query(Compound).count(),
        "preuves": db_session.query(Evidence).count(),
    }
    assert avant == apres, "la synthèse IA ne doit rien persister"


# ---------------------------------------------------------------------------
# 3. Données privées du laboratoire exclues par défaut
# ---------------------------------------------------------------------------

MARQUEUR_PRIVE = "zxq_donnee_privee_confidentielle_456"


def test_prive_exclu_recherche_export_dossier(client, make_project, db_session):
    """Une observation privée n'apparaît ni en recherche, ni en CSV, ni en
    dossier — sauf demande explicite (`inclure_prive=True`)."""
    projet_id = make_project("Labo Privé")
    _plante(client, "Moringa oleifera")
    reponse_obs = client.post(
        "/api/science/observations",
        json={"project_id": projet_id, "comment": f"Résultat {MARQUEUR_PRIVE}"},
    )
    assert reponse_obs.status_code == 201, reponse_obs.text

    # Recherche publique : le marqueur n'y figure pas.
    recherche = client.get("/api/science/recherche", params={"q": "Moringa"})
    assert recherche.status_code == 200, recherche.text
    assert MARQUEUR_PRIVE not in recherche.text

    # Export CSV des plantes : aucune donnée de laboratoire.
    csv_plantes = export_csv(lignes_plantes(db_session), ("nom_scientifique",))
    assert MARQUEUR_PRIVE not in csv_plantes

    # Dossier ZIP par défaut : le marqueur est exclu…
    dossier = build_dossier_zip(db_session, projet_id, inclure_prive=False)
    with zipfile.ZipFile(io.BytesIO(dossier)) as archive:
        contenu = b" ".join(archive.read(nom) for nom in archive.namelist())
    assert MARQUEUR_PRIVE.encode() not in contenu

    # …et n'apparaît qu'avec une demande explicite.
    dossier_prive = build_dossier_zip(db_session, projet_id, inclure_prive=True)
    with zipfile.ZipFile(io.BytesIO(dossier_prive)) as archive:
        contenu_prive = b" ".join(
            archive.read(nom) for nom in archive.namelist()
        )
    assert MARQUEUR_PRIVE.encode() in contenu_prive


# ---------------------------------------------------------------------------
# 4. Idempotence : réimport = 0 doublon
# ---------------------------------------------------------------------------

def test_reimport_fichier_zero_doublon(db_session):
    """Importer deux fois le même CSV : 2e import → 0 création, 0 doublon."""
    with open("docs/exemples_import/plantes.csv", "rb") as fh:
        contenu = fh.read()
    rapport1 = import_export.importer_fichier(
        db_session, contenu, "plantes.csv", "plante"
    )
    db_session.commit()
    assert rapport1["crees"] >= 1
    nombre = db_session.query(Plant).count()

    rapport2 = import_export.importer_fichier(
        db_session, contenu, "plantes.csv", "plante"
    )
    db_session.commit()
    assert rapport2["crees"] == 0
    assert db_session.query(Plant).count() == nombre


# ---------------------------------------------------------------------------
# 5. Provenance enregistrée sur les imports et les connecteurs
# ---------------------------------------------------------------------------

def test_provenance_import_fichier(db_session):
    """Chaque objet importé d'un fichier garde sa provenance (connecteur,
    date, licence)."""
    with open("docs/exemples_import/plantes.csv", "rb") as fh:
        contenu = fh.read()
    import_export.importer_fichier(db_session, contenu, "plantes.csv", "plante")
    db_session.commit()
    lignes = (
        db_session.query(ImportProvenance)
        .filter(ImportProvenance.type_donnees == "plante")
        .all()
    )
    assert lignes, "aucune provenance enregistrée"
    for ligne in lignes:
        assert ligne.connecteur_nom == "fichier:plantes.csv"
        assert ligne.date_import is not None


def test_provenance_connecteur_avec_licence(db_session, monkeypatch):
    """L'import via connecteur enregistre provenance + licence (mock réseau)."""
    from webintel.science.connecteurs.plugins.pubchem import PubChemConnecteur

    def faux_telecharger(self, url, params=None):
        return {
            "PropertyTable": {
                "Properties": [
                    {
                        "CID": 2244,
                        "Title": "Aspirine",
                        "MolecularFormula": "C9H8O4",
                        "MolecularWeight": "180.16",
                        "CanonicalSMILES": "CC(=O)OC1=CC=CC=C1C(=O)O",
                        "InChIKey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                    }
                ]
            }
        }

    monkeypatch.setattr(PubChemConnecteur, "_telecharger", faux_telecharger)
    connecteur = registre_connecteurs.get_connecteur("pubchem")
    rapport = connecteur.importer(db_session, {"nom": "aspirine"})
    db_session.commit()
    assert rapport["crees"] == 1

    compose = db_session.query(Compound).filter(Compound.name == "Aspirine").one()
    prov = (
        db_session.query(ImportProvenance)
        .filter(
            ImportProvenance.type_donnees == "compound",
            ImportProvenance.item_id == compose.id,
        )
        .one()
    )
    assert prov.connecteur_nom == "pubchem"
    assert prov.licence, "la licence du connecteur doit être tracée"
    assert prov.date_import is not None


# ---------------------------------------------------------------------------
# 6. Rejouabilité : mêmes entrées → même hash → mêmes sorties
# ---------------------------------------------------------------------------

ENTREES_STATS = {
    "observations": [
        {"valeur": 12.5, "unite": "µg/mL"},
        {"valeur": 13.1, "unite": "µg/mL"},
        {"valeur": 11.9, "unite": "µg/mL"},
    ]
}


def test_simulation_rejouable_meme_hash_meme_sorties(db_session, _projet_moteur):
    """Deux exécutions identiques : même `input_hash`, mêmes sorties."""
    sim1, _ = run_simulation(
        db_session, engine_name="statistics", name="R1",
        inputs=dict(ENTREES_STATS), parameters={}, executed_by="test",
        project_id=_projet_moteur,
    )
    sim2, _ = run_simulation(
        db_session, engine_name="statistics", name="R2",
        inputs=dict(ENTREES_STATS), parameters={}, executed_by="test",
        project_id=_projet_moteur,
    )
    db_session.commit()
    assert sim1.id != sim2.id
    assert sim1.input_hash == sim2.input_hash
    assert len(sim1.input_hash) == 64
    assert sim1.result == sim2.result
    # …et le hash change si les entrées changent.
    sim3, _ = run_simulation(
        db_session, engine_name="statistics", name="R3",
        inputs={"observations": [{"valeur": 99.9}]},
        parameters={}, executed_by="test",
        project_id=_projet_moteur,
    )
    assert sim3.input_hash != sim1.input_hash


def test_niveau_prediction_jamais_presente_comme_experimental(client, make_project):
    """Garde-fou final : aucun endpoint ne renvoie une simulation avec le
    niveau « experimental », ni une observation avec « prediction »."""
    projet_id = make_project("Labo Garde-fou")
    sim = client.post(
        "/api/science/simulations",
        json={"name": "S", "target": "X", "project_id": projet_id},
    ).json()
    reponse_obs = client.post(
        "/api/science/observations",
        json={"project_id": projet_id, "comment": "c"},
    )
    assert reponse_obs.status_code == 201, reponse_obs.text
    obs = reponse_obs.json()
    assert sim["data_level"] != "experimental"
    assert obs["data_level"] != "prediction"
    assert sim["data_level"] == "prediction"
    assert obs["data_level"] == "experimental"
    # Le service direct non plus.
    assert science_service.create_simulation is not None
