"""Tests offline-first : sauvegarde / restauration.

- export ZIP versionné (manifeste, SHA-256 par fichier) + photos ;
- import sur installation fraîche simulée → tout est « créé » ;
- round-trip : export → wipe → import → export → empreintes identiques ;
- réimport de la même archive → « ignorés » (idempotent) ;
- ligne existante différente → « conflit » conservé (jamais d'écrasement
  silencieux) ; ``forcer=True`` → « mis à jour » explicite ;
- intégrité : archive corrompue / manifeste altéré / schéma incompatible
  → ValueError AVANT toute écriture ;
- UI : export télécharge un ZIP, import affiche le rapport.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile

import pytest

from webintel.offline import photos as photos_mod
from webintel.offline import reseau, sauvegarde
from webintel.offline.models import CollecteTerrain
from webintel.science.models import Plant


@pytest.fixture(autouse=True)
def _mode_reseau_propre():
    yield
    reseau.definir_mode_hors_ligne(None)


def _png_mini():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 120, 60)).save(buf, "PNG")
    return buf.getvalue()


def _jeu_donnees(db_session):
    plante = Plant(scientific_name="Moringa oleifera", family="Moringaceae",
                   used_parts=["feuilles"])
    db_session.add(plante)
    db_session.flush()
    c = CollecteTerrain(localite="Ouaga", latitude=12.37, longitude=-1.52,
                        niveau_confidentialite="public", auteur="adama",
                        plante_id=plante.id)
    db_session.add(c)
    db_session.commit()
    return plante, c


def _vider_tout(db_session):
    """Simule une installation fraîche (tables exportées vidées)."""
    for _, modele in reversed(sauvegarde._modeles_exportes()):
        db_session.query(modele).delete()
    db_session.commit()


def _lire_manifeste(chemin):
    with zipfile.ZipFile(chemin) as zf:
        return json.loads(zf.read("manifest.json").decode("utf-8"))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_export_zip_versionne_avec_hashes(db_session, tmp_path):
    _jeu_donnees(db_session)
    chemin = str(tmp_path / "sauve.zip")
    manifeste = sauvegarde.exporter_projet(db_session, chemin)
    assert os.path.isfile(chemin)
    assert manifeste["format"] == "webintel-backup"
    assert manifeste["schema_version"] == sauvegarde.SCHEMA_VERSION
    assert "webintel_version" in manifeste
    assert manifeste["tables"]["plants"]["lignes"] == 1
    assert manifeste["tables"]["collectes_terrain"]["lignes"] == 1
    # Chaque fichier du ZIP correspond à son empreinte du manifeste.
    with zipfile.ZipFile(chemin) as zf:
        for nom, info in manifeste["tables"].items():
            brut = zf.read(f"tables/{nom}.json")
            assert hashlib.sha256(brut).hexdigest() == info["sha256"], nom


def test_export_inclut_photos(db_session, tmp_path):
    _, c = _jeu_donnees(db_session)
    meta = photos_mod.enregistrer_photo(c.id, _png_mini(), "t.png")
    c.photos = [meta]
    db_session.commit()
    chemin = str(tmp_path / "sauve.zip")
    manifeste = sauvegarde.exporter_projet(db_session, chemin)
    assert manifeste["photos"] >= 2  # original + miniature (dir partagé)
    with zipfile.ZipFile(chemin) as zf:
        noms = zf.namelist()
    assert any(n.startswith("photos/") for n in noms)


def test_export_exclut_les_secrets(db_session, tmp_path):
    from webintel.database import models as core_models
    from webintel.core.utils import sha256_hex

    projet = core_models.Project(name="P")
    db_session.add(projet)
    db_session.flush()
    db_session.add(core_models.ApiKey(
        project_id=projet.id, name="k", key_hash=sha256_hex("secret"),
        prefix="abcd", quotas={}, revoked=False))
    db_session.commit()
    chemin = str(tmp_path / "sauve.zip")
    manifeste = sauvegarde.exporter_projet(db_session, chemin)
    assert "api_keys" not in manifeste["tables"]
    with zipfile.ZipFile(chemin) as zf:
        contenu = zf.read("tables/projects.json").decode("utf-8")
    assert "secret" not in contenu


# ---------------------------------------------------------------------------
# Import : installation fraîche
# ---------------------------------------------------------------------------

def test_import_installation_fraiche_tout_cree(db_session, tmp_path):
    _jeu_donnees(db_session)
    chemin = str(tmp_path / "sauve.zip")
    sauvegarde.exporter_projet(db_session, chemin)
    _vider_tout(db_session)
    assert db_session.query(Plant).count() == 0
    rapport = sauvegarde.importer_projet(db_session, chemin)
    db_session.commit()
    assert rapport["conflits"] == 0
    assert rapport["crees"] > 0
    plante = db_session.query(Plant).filter(
        Plant.scientific_name == "Moringa oleifera").one()
    assert plante.family == "Moringaceae"
    assert plante.used_parts == ["feuilles"]
    collecte = db_session.query(CollecteTerrain).one()
    assert collecte.localite == "Ouaga"
    assert collecte.plante_id == plante.id  # FK préservée


def test_round_trip_empreintes_identiques(db_session, tmp_path):
    _jeu_donnees(db_session)
    z1 = str(tmp_path / "a.zip")
    z2 = str(tmp_path / "b.zip")
    m1 = sauvegarde.exporter_projet(db_session, z1)
    _vider_tout(db_session)
    sauvegarde.importer_projet(db_session, z1)
    db_session.commit()
    m2 = sauvegarde.exporter_projet(db_session, z2)
    for nom, info in m1["tables"].items():
        assert m2["tables"][nom]["sha256"] == info["sha256"], nom
        assert m2["tables"][nom]["lignes"] == info["lignes"], nom


def test_reimport_idempotent_ignores(db_session, tmp_path):
    _jeu_donnees(db_session)
    chemin = str(tmp_path / "sauve.zip")
    sauvegarde.exporter_projet(db_session, chemin)
    rapport = sauvegarde.importer_projet(db_session, chemin)
    db_session.commit()
    assert rapport["crees"] == 0
    assert rapport["conflits"] == 0
    assert rapport["ignores"] > 0


def test_ligne_differente_conflit_jamais_ecrase(db_session, tmp_path):
    _jeu_donnees(db_session)
    chemin = str(tmp_path / "sauve.zip")
    sauvegarde.exporter_projet(db_session, chemin)
    # Quelqu'un modifie la plante APRES la sauvegarde.
    plante = db_session.query(Plant).one()
    plante.family = "Famille MODIFIÉE après sauvegarde"
    db_session.commit()
    rapport = sauvegarde.importer_projet(db_session, chemin)
    db_session.commit()
    assert rapport["conflits"] >= 1
    assert rapport["mis_a_jour"] == 0
    db_session.refresh(plante)
    # La valeur locale est CONSERVÉE (pas d'écrasement silencieux).
    assert plante.family == "Famille MODIFIÉE après sauvegarde"
    assert any(d["table"] == "plants"
               for d in rapport["details_conflits"])


def test_forcer_ecrase_explicitement(db_session, tmp_path):
    _jeu_donnees(db_session)
    chemin = str(tmp_path / "sauve.zip")
    sauvegarde.exporter_projet(db_session, chemin)
    plante = db_session.query(Plant).one()
    plante.family = "MODIFIÉE"
    db_session.commit()
    rapport = sauvegarde.importer_projet(db_session, chemin, forcer=True)
    db_session.commit()
    assert rapport["mis_a_jour"] >= 1
    assert rapport["conflits"] == 0
    db_session.refresh(plante)
    assert plante.family == "Moringaceae"


def test_import_restaure_photos(db_session, tmp_path):
    _, c = _jeu_donnees(db_session)
    meta = photos_mod.enregistrer_photo(c.id, _png_mini(), "t.png")
    c.photos = [meta]
    db_session.commit()
    chemin = str(tmp_path / "sauve.zip")
    sauvegarde.exporter_projet(db_session, chemin)
    photos_mod.supprimer_photos_collecte(c.id)
    _vider_tout(db_session)
    rapport = sauvegarde.importer_projet(db_session, chemin)
    db_session.commit()
    # Le répertoire photos est partagé entre tests : on vérifie au moins
    # les 2 fichiers de CE test, et que sa miniature est restaurée.
    assert rapport["photos_restaurees"] >= 2
    assert os.path.isfile(os.path.join(
        photos_mod.repertoire_photos(), str(c.id), meta["miniature"]))


# ---------------------------------------------------------------------------
# Intégrité : refus AVANT toute écriture
# ---------------------------------------------------------------------------

def _altérer_zip(chemin_src, chemin_dst, altération):
    with zipfile.ZipFile(chemin_src) as zin:
        fichiers = {n: zin.read(n) for n in zin.namelist()}
    altération(fichiers)
    with zipfile.ZipFile(chemin_dst, "w",
                         compression=zipfile.ZIP_DEFLATED) as zout:
        for nom, contenu in fichiers.items():
            zout.writestr(nom, contenu)


def test_archive_corrompue_refusee_sans_ecriture(db_session, tmp_path):
    _jeu_donnees(db_session)
    src = str(tmp_path / "sauve.zip")
    sauvegarde.exporter_projet(db_session, src)

    def altérer(fichiers):
        # On modifie une donnée SANS mettre à jour le manifeste.
        fichiers["tables/plants.json"] = b'[{"id": 999, "scientific_name": "PIRATE"}]'

    dst = str(tmp_path / "pirate.zip")
    _altérer_zip(src, dst, altérer)
    _vider_tout(db_session)
    with pytest.raises(ValueError, match="[Ii]ntégrité|empreinte|SHA"):
        sauvegarde.importer_projet(db_session, dst)
    db_session.rollback()
    assert db_session.query(Plant).count() == 0  # rien n'a été écrit


def test_schema_incompatible_refuse(db_session, tmp_path):
    _jeu_donnees(db_session)
    src = str(tmp_path / "sauve.zip")
    sauvegarde.exporter_projet(db_session, src)

    def altérer(fichiers):
        manif = json.loads(fichiers["manifest.json"])
        manif["schema_version"] = "99.0"
        fichiers["manifest.json"] = json.dumps(manif).encode()

    dst = str(tmp_path / "vieux.zip")
    _altérer_zip(src, dst, altérer)
    with pytest.raises(ValueError, match="[Ii]ncompatible"):
        sauvegarde.importer_projet(db_session, dst)


def test_pas_un_zip_refuse(db_session, tmp_path):
    faux = tmp_path / "faux.zip"
    faux.write_text("ceci n'est pas un zip")
    with pytest.raises(ValueError, match="[Ii]nvalide"):
        sauvegarde.importer_projet(db_session, str(faux))


# ---------------------------------------------------------------------------
# UI : export télécharge, import affiche le rapport
# ---------------------------------------------------------------------------

def test_ui_export_telecharge_zip(client, db_session):
    _jeu_donnees(db_session)
    r = client.post("/sauvegarde/exporter")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.content[:2] == b"PK"


def test_ui_import_affiche_rapport(client, db_session, tmp_path):
    _jeu_donnees(db_session)
    chemin = str(tmp_path / "sauve.zip")
    sauvegarde.exporter_projet(db_session, chemin)
    _vider_tout(db_session)
    with open(chemin, "rb") as fh:
        contenu = fh.read()
    r = client.post(
        "/sauvegarde/importer",
        files=[("fichier", ("sauve.zip", contenu, "application/zip"))],
        data={"forcer": ""},
    )
    assert r.status_code == 200
    assert "Rapport de restauration" in r.text
    assert "Créés" in r.text
    assert db_session.query(Plant).count() == 1


def test_ui_import_corrompu_affiche_erreur(client, db_session, tmp_path):
    r = client.post(
        "/sauvegarde/importer",
        files=[("fichier", ("x.zip", b"pas un zip", "application/zip"))],
        data={"forcer": ""},
    )
    assert r.status_code == 200
    assert "Restauration refusée" in r.text
    assert "Aucune donnée n'a été modifiée" in r.text


# ---------------------------------------------------------------------------
# Export cloisonné par projet
# ---------------------------------------------------------------------------

def _projet_sauve(db_session, nom):
    from webintel.database import models as core_models

    projet = db_session.query(core_models.Project).filter_by(
        name=nom).first()
    if projet is None:
        projet = core_models.Project(name=nom)
        db_session.add(projet)
        db_session.commit()
    return projet


def test_export_par_projet_sans_fuite(db_session, tmp_path):
    """L'export avec ``project_id`` ne contient que les lignes du
    projet (pas celles des autres projets) ; les données globales
    (plantes) restent exportées en entier ; le manifeste porte le
    ``project_id``."""
    from webintel.database import models as core_models

    p1 = _projet_sauve(db_session, "Projet un (sauve)")
    p2 = _projet_sauve(db_session, "Projet deux (sauve)")
    db_session.add(CollecteTerrain(
        localite="Ouaga", niveau_confidentialite="public", auteur="u",
        project_id=p1.id))
    db_session.add(CollecteTerrain(
        localite="Bobo", niveau_confidentialite="public", auteur="u",
        project_id=p2.id))
    db_session.add(Plant(scientific_name="Moringa oleifera"))
    db_session.commit()

    chemin = str(tmp_path / "sauve-p1.zip")
    manifeste = sauvegarde.exporter_projet(
        db_session, chemin, project_id=p1.id)
    assert manifeste["project_id"] == p1.id

    with zipfile.ZipFile(chemin) as zf:
        collectes = json.loads(
            zf.read("tables/collectes_terrain.json").decode("utf-8"))
        projets = json.loads(
            zf.read("tables/projects.json").decode("utf-8"))
        plantes = json.loads(
            zf.read("tables/plants.json").decode("utf-8"))

    # Aucune fuite : seule la collecte de p1.
    assert {c["localite"] for c in collectes} == {"Ouaga"}
    # La table projects ne contient que p1.
    assert {p["name"] for p in projets} == {"Projet un (sauve)"}
    # Les plantes (données globales) sont exportées en entier.
    assert any(p["scientific_name"] == "Moringa oleifera"
               for p in plantes)


def test_export_par_projet_photos_filtrees(db_session, tmp_path,
                                           monkeypatch):
    """Les photos exportées sont limitées aux collectes du projet."""
    import tempfile

    p1 = _projet_sauve(db_session, "Projet un (photos)")
    p2 = _projet_sauve(db_session, "Projet deux (photos)")
    c1 = CollecteTerrain(
        localite="Ouaga", niveau_confidentialite="public", auteur="u",
        project_id=p1.id)
    c2 = CollecteTerrain(
        localite="Bobo", niveau_confidentialite="public", auteur="u",
        project_id=p2.id)
    db_session.add_all([c1, c2])
    db_session.commit()

    rep = tempfile.mkdtemp(prefix="photos-projet-")
    monkeypatch.setenv("WEBINTEL_PHOTOS_DIR", rep)
    photos_mod.enregistrer_photo(c1.id, _png_mini(), "feuille.png")
    photos_mod.enregistrer_photo(c2.id, _png_mini(), "racine.png")

    chemin = str(tmp_path / "sauve-p1-photos.zip")
    manifeste = sauvegarde.exporter_projet(
        db_session, chemin, project_id=p1.id)

    with zipfile.ZipFile(chemin) as zf:
        noms = [n for n in zf.namelist() if n.startswith("photos/")]
    # Uniquement les fichiers du dossier de la collecte de p1.
    assert noms, "aucune photo exportée"
    assert all(n.split("/")[1] == str(c1.id) for n in noms), noms
    assert manifeste["photos"] == len(noms)


def test_export_sans_project_id_reste_complet(db_session, tmp_path):
    """Sans ``project_id`` : comportement historique (tout est
    exporté, manifeste sans filtre projet)."""
    p1 = _projet_sauve(db_session, "Projet un (complet)")
    db_session.add(CollecteTerrain(
        localite="Ouaga", niveau_confidentialite="public", auteur="u",
        project_id=p1.id))
    db_session.commit()

    chemin = str(tmp_path / "sauve-complet.zip")
    manifeste = sauvegarde.exporter_projet(db_session, chemin)
    assert manifeste["project_id"] is None
    with zipfile.ZipFile(chemin) as zf:
        collectes = json.loads(
            zf.read("tables/collectes_terrain.json").decode("utf-8"))
    assert len(collectes) >= 1


def test_ui_export_cloisonne_par_projet(client, db_session, tmp_path):
    """L'export UI ne contient que les données du projet courant."""
    from webintel.ui.routes import _default_project

    mien = _default_project(db_session)
    autre = _projet_sauve(db_session, "Autre (ui export)")
    db_session.add(CollecteTerrain(
        localite="Chez moi", niveau_confidentialite="public", auteur="u",
        project_id=mien.id))
    db_session.add(CollecteTerrain(
        localite="Ailleurs", niveau_confidentialite="public", auteur="u",
        project_id=autre.id))
    db_session.commit()

    r = client.post("/sauvegarde/exporter")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    chemin = str(tmp_path / "ui.zip")
    with open(chemin, "wb") as fh:
        fh.write(r.content)
    with zipfile.ZipFile(chemin) as zf:
        collectes = json.loads(
            zf.read("tables/collectes_terrain.json").decode("utf-8"))
    assert {c["localite"] for c in collectes} == {"Chez moi"}
