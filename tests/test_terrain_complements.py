"""Tests v2.2 — compléments du chantier MODÈLE SCIENTIFIQUE & TERRAIN.

Couverture des ajouts du chantier (code additif, zéro réécriture) :

- validation GPS (plages WGS84) : service, API, UI collecte offline ;
- registre des langues persistant (table ``Langue``) : amorçage des 5
  langues du Burkina Faso, enregistrement idempotent, langue jamais
  devinée ;
- normalisation moléculaire (SMILES → InChI / InChIKey via RDKit) :
  SMILES valide, invalide (signalé, jamais corrigé), divergences
  (jamais écrasées), lot ;
- intégrité des fichiers bruts d'analyse (SHA-256) : rattachement,
  fichier intact, fichier corrompu (signalé), fichier absent,
  sans empreinte ;
- qualité : le détecteur « échantillons sans voucher » couvre aussi le
  modèle ``Echantillon`` (chaîne terrain v2.2), pas seulement
  ``Specimen``.
"""

from __future__ import annotations

import pytest

from webintel.database import models as core_models
from webintel.science import normalisation_moleculaire as norm_mol
from webintel.science import qualite as q
from webintel.science import terrain as t
from webintel.science.models import (
    AnalyseChimique,
    Compound,
    Echantillon,
    Langue,
    NiveauConfidentialite,
    Plant,
    Voucher,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def projet(db_session):
    p = core_models.Project(name="Projet Compléments")
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
def cafeine(db_session):
    c = Compound(
        name="Caféine",
        smiles="CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
    )
    db_session.add(c)
    db_session.commit()
    return c


# ---------------------------------------------------------------------------
# Validation GPS
# ---------------------------------------------------------------------------


class TestValidationGps:
    def test_bornes_acceptees(self):
        for lat, lon in [
            (-90.0, -180.0), (90.0, 180.0), (0.0, 0.0), (12.3714, -1.5197),
        ]:
            t.valider_gps(lat, lon)  # ne lève pas

    def test_none_accepte(self):
        t.valider_gps(None, None)
        t.valider_gps(None, 2.0)
        t.valider_gps(12.0, None)

    def test_latitude_hors_plage_refusee(self):
        for lat in (90.0001, 91, -91, 999, -1000):
            with pytest.raises(ValueError, match="[Ll]atitude"):
                t.valider_gps(lat, 0.0)

    def test_longitude_hors_plage_refusee(self):
        for lon in (180.0001, 181, -181, 500):
            with pytest.raises(ValueError, match="[Ll]ongitude"):
                t.valider_gps(0.0, lon)

    def test_non_numerique_refuse(self):
        with pytest.raises(ValueError):
            t.valider_gps("douze", 0.0)

    def test_creer_echantillon_gps_invalide(self, db_session, neem, projet):
        with pytest.raises(ValueError, match="[Ll]atitude"):
            t.creer_echantillon(
                db_session, projet, neem.id, "ECH-GPS-001",
                latitude=999.0, longitude=0.0,
            )
        # Rien n'a été persisté (le flush n'a pas eu lieu).
        assert (
            db_session.query(Echantillon)
            .filter(Echantillon.code_echantillon == "ECH-GPS-001")
            .count()
            == 0
        )

    def test_creer_echantillon_gps_valide(self, db_session, neem, projet):
        ech, cree = t.creer_echantillon(
            db_session, projet, neem.id, "ECH-GPS-002",
            latitude=12.3714, longitude=-1.5197,
        )
        assert cree and ech.latitude == pytest.approx(12.3714)

    def test_api_echantillon_gps_invalide_422(self, client, neem, projet):
        # Le schéma Pydantic borne déjà le GPS (défense en profondeur
        # avec le service `valider_gps`) : 422 dans tous les cas.
        reponse = client.post(
            "/api/science/terrain/echantillons",
            json={
                "project_id": projet,
                "plant_id": neem.id,
                "code_echantillon": "ECH-GPS-API",
                "latitude": 95.0,
            },
        )
        assert reponse.status_code == 422

    def test_ui_collecte_gps_invalide_affiche_erreur(self, client, projet):
        reponse = client.post(
            "/collectes/nouvelle",
            data={
                "localite": "Ouagadougou",
                "latitude": "999",
                "longitude": "-1.5",
                "niveau_confidentialite": "restreint",
            },
        )
        assert reponse.status_code == 200
        assert "hors plage" in reponse.text

    def test_ui_terrain_echantillon_gps_invalide_affiche_erreur(
        self, client, neem, projet
    ):
        reponse = client.post(
            f"/plantes/{neem.id}/terrain/echantillons",
            data={
                "code_echantillon": "ECH-UI-GPS",
                "latitude": "-91",
                "longitude": "2.0",
            },
        )
        assert reponse.status_code == 200
        assert "hors plage" in reponse.text


# ---------------------------------------------------------------------------
# Registre des langues (table persistante)
# ---------------------------------------------------------------------------


class TestRegistreLangues:
    def test_cinq_langues_amorcees_en_base(self, db_session):
        langues = {l.code: l.libelle for l in t.lister_langues(db_session)}
        assert langues == {
            "moore": "Mooré",
            "dioula": "Dioula",
            "fulfulde": "Fulfuldé",
            "gulmancema": "Gulmancéma",
            "bissa": "Bissa",
        }

    def test_enregistrer_langue_db_idempotent(self, db_session):
        langue, creee = t.enregistrer_langue_db(
            db_session, "gourmantche", "Gourmantché",
            source="test",
        )
        assert creee and langue.code == "gourmantche"
        _langue2, creee2 = t.enregistrer_langue_db(
            db_session, " Gourmantche ", "Gourmantché"
        )
        assert not creee2
        assert (
            db_session.query(Langue)
            .filter(Langue.code == "gourmantche")
            .count()
            == 1
        )

    def test_enregistrer_langue_db_code_vide(self, db_session):
        with pytest.raises(ValueError):
            t.enregistrer_langue_db(db_session, "  ", "Vide")

    def test_langue_connue_db(self, db_session):
        assert t.langue_connue_db(db_session, None)
        assert t.langue_connue_db(db_session, "moore")
        assert not t.langue_connue_db(db_session, "klingon")

    def test_nom_vernaculaire_accepte_nouvelle_langue_db(
        self, db_session, neem, projet
    ):
        t.enregistrer_langue_db(db_session, "samo", "Samo")
        # La langue enregistrée EN BASE est acceptée (jamais devinée :
        # elle a été déclarée explicitement juste avant).
        objet, cree = t.ajouter_nom_vernaculaire(
            db_session, neem.id, projet, "sana", langue="samo"
        )
        assert cree and objet.langue == "samo"

    def test_langue_inconnue_toujours_refusee(self, db_session, neem, projet):
        with pytest.raises(ValueError, match="Langue inconnue"):
            t.ajouter_nom_vernaculaire(
                db_session, neem.id, projet, "xyz", langue="klingon"
            )

    def test_api_langues(self, client, projet):
        reponse = client.get("/api/science/terrain/langues")
        assert reponse.status_code == 200
        codes = {l["code"] for l in reponse.json()}
        assert {"moore", "dioula", "fulfulde", "gulmancema", "bissa"} <= codes

        creation = client.post(
            "/api/science/terrain/langues",
            json={
                "project_id": projet,
                "code": "dagara",
                "libelle": "Dagara",
            },
        )
        assert creation.status_code == 201
        assert creation.json()["code"] == "dagara"

        invalide = client.post(
            "/api/science/terrain/langues",
            json={"project_id": projet, "code": "  ", "libelle": "Vide"},
        )
        assert invalide.status_code == 422


# ---------------------------------------------------------------------------
# Normalisation moléculaire
# ---------------------------------------------------------------------------


class TestNormalisationMoleculaire:
    def test_smiles_valide_complete_inchi(self, db_session, cafeine):
        rapport = norm_mol.normaliser_compose(
            db_session, cafeine.id, ecrire=True
        )
        assert rapport["statut"] == "normalise"
        assert set(rapport["champs_completes"]) == {"inchi", "inchikey"}
        db_session.refresh(cafeine)
        assert cafeine.inchikey == "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
        assert cafeine.inchi.startswith("InChI=1S/C8H10N4O2/")
        # Le SMILES d'origine n'est jamais réécrit.
        assert cafeine.smiles == "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"

    def test_second_passage_deja_normalise(self, db_session, cafeine):
        norm_mol.normaliser_compose(db_session, cafeine.id, ecrire=True)
        rapport = norm_mol.normaliser_compose(
            db_session, cafeine.id, ecrire=True
        )
        assert rapport["statut"] == "deja_normalise"
        assert rapport["champs_completes"] == []

    def test_smiles_invalide_signale_jamais_corrige(
        self, db_session
    ):
        compose = Compound(name="Fausse molécule", smiles="XYZ-INVALIDE-[[")
        db_session.add(compose)
        db_session.commit()
        rapport = norm_mol.normaliser_compose(
            db_session, compose.id, ecrire=True
        )
        assert rapport["statut"] == "invalide"
        assert "invalide" in rapport["raison"].lower()
        db_session.refresh(compose)
        # Rien n'a été écrit ni « corrigé » : le SMILES fautif est intact.
        assert compose.smiles == "XYZ-INVALIDE-[["
        assert compose.inchi is None and compose.inchikey is None

    def test_sans_smiles(self, db_session):
        compose = Compound(name="Sans structure")
        db_session.add(compose)
        db_session.commit()
        rapport = norm_mol.normaliser_compose(db_session, compose.id)
        assert rapport["statut"] == "sans_smiles"

    def test_divergence_jamais_ecrasee(self, db_session, cafeine):
        # D'abord une normalisation propre (remplit inchi/inchikey),
        # puis on simule une valeur stockée divergente.
        norm_mol.normaliser_compose(db_session, cafeine.id, ecrire=True)
        cafeine.inchikey = "FAUSSE-CLE-ENREGISTREE"
        db_session.commit()
        rapport = norm_mol.normaliser_compose(
            db_session, cafeine.id, ecrire=True
        )
        assert rapport["statut"] == "deja_normalise"
        divergences_inchikey = [
            d for d in rapport["divergences"] if d["champ"] == "inchikey"
        ]
        assert len(divergences_inchikey) == 1
        divergence = divergences_inchikey[0]
        assert divergence["champ"] == "inchikey"
        assert divergence["valeur_stockee"] == "FAUSSE-CLE-ENREGISTREE"
        assert divergence["valeur_calculee"] == "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
        # La valeur stockée est conservée, jamais écrasée.
        db_session.refresh(cafeine)
        assert cafeine.inchikey == "FAUSSE-CLE-ENREGISTREE"

    def test_compose_introuvable(self, db_session):
        with pytest.raises(KeyError):
            norm_mol.normaliser_compose(db_session, 999999)

    def test_lot(self, db_session, cafeine):
        mauvais = Compound(name="Mauvais", smiles="pas-un-smiles")
        db_session.add(mauvais)
        db_session.commit()
        resultat = norm_mol.normaliser_composes(
            db_session, ids=[cafeine.id, mauvais.id]
        )
        assert resultat["traites"] == 2
        assert resultat["resume"]["normalise"] == 1
        assert resultat["resume"]["invalide"] == 1

    def test_api_normaliser(self, client, cafeine):
        reponse = client.post(
            f"/api/science/compounds/{cafeine.id}/normaliser"
        )
        assert reponse.status_code == 200
        rapport = reponse.json()
        assert rapport["statut"] == "normalise"
        assert "inchikey" in rapport["champs_completes"]

    def test_api_normaliser_404(self, client):
        reponse = client.post("/api/science/compounds/999999/normaliser")
        assert reponse.status_code == 404


# ---------------------------------------------------------------------------
# Intégrité des fichiers bruts d'analyse
# ---------------------------------------------------------------------------


class TestIntegriteFichierBrut:
    @pytest.fixture()
    def racine_bruts(self, tmp_path, monkeypatch):
        """Pointe la racine autorisée vers tmp_path (confinement P0)."""
        from webintel.config.settings import get_settings
        monkeypatch.setattr(
            get_settings(), "FICHIERS_BRUTS_DIR", str(tmp_path)
        )
        return tmp_path

    @pytest.fixture()
    def analyse(self, db_session, neem, projet):
        ech, _ = t.creer_echantillon(
            db_session, projet, neem.id, "ECH-FICHIER-001"
        )
        objet = t.creer_analyse_chimique(
            db_session, projet, "HPLC", echantillon_id=ech.id
        )
        db_session.commit()
        return objet.id

    def test_rattacher_et_verifier_ok(
        self, db_session, analyse, racine_bruts, tmp_path
    ):
        fichier = tmp_path / "chromato.brut"
        fichier.write_bytes(b"donnees-chromatographiques-brutes")
        objet = t.enregistrer_fichier_brut(
            db_session, analyse, str(fichier)
        )
        assert objet.fichier_brut_sha256 is not None
        resultat = t.verifier_integrite_fichier(db_session, analyse)
        assert resultat["present"] is True
        assert resultat["correspond"] is True

    def test_fichier_corrompu_signale(self, db_session, analyse, racine_bruts, tmp_path):
        fichier = tmp_path / "chromato.brut"
        fichier.write_bytes(b"donnees-originales")
        t.enregistrer_fichier_brut(db_session, analyse, str(fichier))
        # Le fichier est modifié APRÈS le rattachement.
        fichier.write_bytes(b"donnees-modifiees-apres-coup")
        resultat = t.verifier_integrite_fichier(db_session, analyse)
        assert resultat["present"] is True
        assert resultat["correspond"] is False
        assert "corrompu" in resultat["raison"] or "modifié" in resultat["raison"]

    def test_fichier_absent_disque(self, db_session, analyse, racine_bruts, tmp_path):
        fichier = tmp_path / "disparu.brut"
        fichier.write_bytes(b"donnees")
        t.enregistrer_fichier_brut(db_session, analyse, str(fichier))
        fichier.unlink()
        resultat = t.verifier_integrite_fichier(db_session, analyse)
        assert resultat["present"] is False
        assert resultat["correspond"] is None

    def test_sans_empreinte_enregistree(self, db_session, analyse, racine_bruts, tmp_path):
        fichier = tmp_path / "sans-hash.brut"
        fichier.write_bytes(b"donnees")
        objet = db_session.get(AnalyseChimique, analyse)
        objet.fichier_brut_path = str(fichier)  # chemin sans hash
        db_session.commit()
        resultat = t.verifier_integrite_fichier(db_session, analyse)
        assert resultat["present"] is True
        assert resultat["correspond"] is None
        assert "empreinte" in resultat["raison"].lower()

    def test_aucun_fichier_rattache(self, db_session, analyse):
        resultat = t.verifier_integrite_fichier(db_session, analyse)
        assert resultat["present"] is False
        assert "Aucun fichier brut" in resultat["raison"]

    def test_fichier_inexistant_leve(self, db_session, analyse, racine_bruts, tmp_path):
        with pytest.raises(FileNotFoundError):
            t.enregistrer_fichier_brut(
                db_session, analyse, str(tmp_path / "absent.brut")
            )

    def test_analyse_introuvable(self, db_session):
        with pytest.raises(KeyError):
            t.verifier_integrite_fichier(db_session, 999999)

    def test_api_integrite(self, client, db_session, analyse, tmp_path, racine_bruts,
                           projet):
        fichier = tmp_path / "api.brut"
        fichier.write_bytes(b"donnees-api")
        rattachement = client.post(
            f"/api/science/terrain/analyses/{analyse}/fichier",
            json={"chemin": str(fichier), "project_id": projet},
        )
        # project_id n'est pas requis sur cette route (projet du token)
        assert rattachement.status_code in (200, 400)
        if rattachement.status_code == 200:
            assert rattachement.json()["sha256"]
        verification = client.get(
            f"/api/science/terrain/analyses/{analyse}/integrite"
        )
        assert verification.status_code == 200
        assert verification.json()["present"] is True

    def test_api_fichier_hors_racine_403(self, client, analyse):
        # P0 sécurité : un chemin hors du répertoire autorisé est refusé
        # (403), même s'il n'existe pas — plus d'oracle de fichiers.
        reponse = client.post(
            f"/api/science/terrain/analyses/{analyse}/fichier",
            json={"chemin": "/chemin/qui/n/existe/pas.brut"},
        )
        assert reponse.status_code == 403

    def test_api_fichier_introuvable_dans_racine_404(
        self, client, analyse, racine_bruts, tmp_path
    ):
        # Dans la racine autorisée, un fichier absent reste un 404.
        reponse = client.post(
            f"/api/science/terrain/analyses/{analyse}/fichier",
            json={"chemin": str(tmp_path / "absent-api.brut")},
        )
        assert reponse.status_code == 404


# ---------------------------------------------------------------------------
# Qualité : échantillons de terrain sans voucher
# ---------------------------------------------------------------------------


class TestQualiteEchantillonSansVoucher:
    def test_echantillon_sans_voucher_detecte(
        self, db_session, neem, projet
    ):
        ech, _ = t.creer_echantillon(
            db_session, projet, neem.id, "ECH-Q-001"
        )
        db_session.commit()
        anomalies = q.detecter_echantillons_sans_voucher(db_session)
        visees = [
            a for a in anomalies
            if a["entite_type"] == "echantillon"
            and a["entite_id"] == ech.id
        ]
        assert len(visees) == 1
        assert visees[0]["severite"] == "avertissement"
        assert "voucher" in visees[0]["titre"].lower()

    def test_echantillon_avec_voucher_silencieux(
        self, db_session, neem, projet
    ):
        voucher, _ = t.creer_voucher(
            db_session, projet, "OUA-2026-0999",
            institution="Herbier National du Burkina Faso",
        )
        ech, _ = t.creer_echantillon(
            db_session, projet, neem.id, "ECH-Q-002",
            voucher_id=voucher.id,
        )
        db_session.commit()
        anomalies = q.detecter_echantillons_sans_voucher(db_session)
        visees = [
            a for a in anomalies
            if a["entite_type"] == "echantillon"
            and a["entite_id"] == ech.id
        ]
        assert visees == []

    def test_executer_controle_couvre_les_echantillons(
        self, db_session, neem, projet
    ):
        t.creer_echantillon(db_session, projet, neem.id, "ECH-Q-003")
        db_session.commit()
        rapport = q.executer_controle(
            db_session, detecteurs=["echantillons_sans_voucher"]
        )
        assert rapport["resume"]["par_categorie"][
            "echantillons_sans_voucher"
        ] >= 1
        assert any(
            a["entite_type"] == "echantillon"
            for a in rapport["anomalies"]
        )


# ---------------------------------------------------------------------------
# Confidentialité : non-régression des protections existantes
# ---------------------------------------------------------------------------


class TestConfidentialiteNonRegression:
    def test_gps_valide_reste_protege_en_export_public(
        self, db_session, neem, projet
    ):
        # Un échantillon PUBLIC avec GPS : l'export public ne doit
        # toujours pas divulguer les coordonnées précises.
        ech, _ = t.creer_echantillon(
            db_session, projet, neem.id, "ECH-CONF-001",
            latitude=12.3714, longitude=-1.5197,
            niveau_confidentialite=(
                NiveauConfidentialite.PUBLIC.value
            ),
        )
        db_session.commit()
        export = t.export_public_plante(db_session, neem.id)
        echantillons = export["echantillons"]
        assert len(echantillons) == 1
        assert "latitude" not in echantillons[0]
        assert "longitude" not in echantillons[0]
        assert echantillons[0]["code_echantillon"] == "ECH-CONF-001"
