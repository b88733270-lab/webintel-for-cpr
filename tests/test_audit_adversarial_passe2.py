"""Audit adversarial — 2e passe (yeux frais), WebIntel 2.3.0.

Chaque test PROUVE l'exploitation d'une faille réelle trouvée par des
techniques différentes de la 1re passe, puis verrouille le correctif :

- A. CSV avec champ > ``csv.field_size_limit`` → 500 (itération hors try) ;
- D. ``POST /science/terrain/langues`` avec champs mal typés → 500 ;
- E. contrôles bidi Unicode conservés dans les noms affichés (usurpation) ;
- F. ``verifier_integrite_fichier`` suit un symlink substitué après
     rattachement → oracle de SHA-256 d'un fichier arbitraire ;
- G. restore d'une archive aux champs inconnus → ``TypeError`` 500 après
     écritures partielles ;
- W. ``_charger_session`` supprime une session expirée AVANT le contrôle
     projet/owner (mutation avant contrôle d'accès) ;
- P. ``POST /science/provenance`` accepte une provenance pour une entité
     inexistante ou d'un autre projet (falsification).
"""

import hashlib
import json
import os
import secrets
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import webintel.config.settings as _settings_mod
import webintel.main
from webintel.core.utils import sha256_hex
from webintel.database.models import ApiKey, Project, WizardSession
from webintel.science import terrain as terrain_service
from webintel.science.models import AnalyseChimique, Plant


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

@pytest.fixture()
def key_mode(monkeypatch):
    """Active REQUIRE_API_KEY=true pour ce test (isolation inter-projets)."""
    settings = _settings_mod.Settings()
    settings.REQUIRE_API_KEY = True
    _settings_mod._settings = settings
    yield
    _settings_mod._settings = None


def _project_with_key(db_session, name: str):
    project = Project(name=name)
    db_session.add(project)
    db_session.flush()
    plain = secrets.token_urlsafe(32)
    db_session.add(ApiKey(
        project_id=project.id,
        name=f"clé-{name}",
        key_hash=sha256_hex(plain),
        prefix=plain[:8],
        quotas={},
        revoked=False,
    ))
    db_session.commit()
    return project, plain


def _h(plain: str) -> dict:
    return {"X-API-Key": plain}


# ---------------------------------------------------------------------------
# A. CSV avec champ géant → 400, jamais 500
# ---------------------------------------------------------------------------

def test_import_csv_champ_geant_rejete_proprement(client, make_project):
    """Un champ > 128 Ko levait ``_csv.Error`` hors du try → 500."""
    pid = make_project("CSV géant")
    gros = "x" * 200_000
    contenu = f"nom_scientifique,description\nPlanteA,{gros}\n".encode()
    r = client.post(
        "/api/science/import",
        files={"fichier": ("gros.csv", contenu, "text/csv")},
        data={"type_donnees": "plante", "project_id": str(pid)},
    )
    assert r.status_code == 400, f"attendu 400, reçu {r.status_code}"
    assert "CSV" in r.json().get("detail", "")


# ---------------------------------------------------------------------------
# D. Registre des langues : champs mal typés → 422, jamais 500
# ---------------------------------------------------------------------------

def test_langues_code_non_chaine_422(client, make_project):
    pid = make_project("Langues types")
    for corps in (
        {"code": 123, "libelle": "X", "project_id": pid},
        {"code": ["x"], "libelle": "X", "project_id": pid},
        {"code": "okcode", "libelle": "X", "region": {"a": 1},
         "project_id": pid},
        {"code": "okcode2", "libelle": "X", "source": 42,
         "project_id": pid},
    ):
        r = client.post("/api/science/terrain/langues", json=corps)
        assert r.status_code == 422, (
            f"corps={corps!r} : attendu 422, reçu {r.status_code}"
        )


def test_langues_creation_legitime_ok(client, make_project):
    from webintel.science.models import LANGUES_BURKINA
    pid = make_project("Langues OK")
    try:
        r = client.post("/api/science/terrain/langues", json={
            "code": "testlangue", "libelle": "Test Langue",
            "region": "Burkina Faso", "source": "test", "project_id": pid,
        })
        assert r.status_code == 201, r.text
        assert r.json()["creee"] is True
    finally:
        # Le registre des langues est volontairement global et persistant
        # (documenté dans models.Langue) : on retire la langue ajoutée
        # pour ne pas polluer les autres tests via _amorcer_langues().
        LANGUES_BURKINA.pop("testlangue", None)


# ---------------------------------------------------------------------------
# E. Contrôles bidi retirés des noms affichés
# ---------------------------------------------------------------------------

def test_bidi_retire_du_nom_vernaculaire(db_session):
    projet = Project(name="Bidi")
    db_session.add(projet)
    db_session.flush()
    plante = Plant(scientific_name="Plante Bidi")
    db_session.add(plante)
    db_session.flush()
    obj, _ = terrain_service.ajouter_nom_vernaculaire(
        db_session, plante.id, projet.id, "Quinine\u202eEVIL")
    db_session.commit()
    assert "\u202e" not in (obj.nom_original or "")
    assert "EVIL" in obj.nom_original  # contenu visible conservé


def test_bidi_seul_rejete_comme_vide(db_session):
    projet = Project(name="Bidi 2")
    db_session.add(projet)
    db_session.flush()
    plante = Plant(scientific_name="Plante Bidi 2")
    db_session.add(plante)
    db_session.flush()
    with pytest.raises(ValueError):
        terrain_service.ajouter_nom_vernaculaire(
            db_session, plante.id, projet.id, "\u202e\u202c")


def test_retirer_controles_bidi_unitaire():
    assert terrain_service.retirer_controles_bidi(None) is None
    assert terrain_service.retirer_controles_bidi("abc") == "abc"
    assert terrain_service.retirer_controles_bidi("a\u200eb\u2069c") == "abc"


# ---------------------------------------------------------------------------
# F. Intégrité : symlink substitué après rattachement → alerte, pas d'oracle
# ---------------------------------------------------------------------------

def test_integrite_symlink_hors_racine_sans_oracle(db_session, tmp_path,
                                                  monkeypatch):
    projet = Project(name="Integrite F")
    db_session.add(projet)
    db_session.flush()
    bruts = tmp_path / "bruts"
    bruts.mkdir()
    import webintel.config.settings as settings_mod
    s = settings_mod.get_settings()
    old = s.FICHIERS_BRUTS_DIR
    s.FICHIERS_BRUTS_DIR = str(bruts)
    try:
        cible = bruts / "legitime.dat"
        cible.write_bytes(b"donnees legitimes")
        analyse = AnalyseChimique(project_id=projet.id, methode="HPLC")
        db_session.add(analyse)
        db_session.flush()
        terrain_service.enregistrer_fichier_brut(
            db_session, analyse.id, str(cible))
        db_session.commit()
        # Attaquant local : remplace le fichier par un symlink hors racine.
        secret = tmp_path / "secret.txt"
        secret.write_bytes(b"contenu-confidentiel-tres-secret")
        cible.unlink()
        cible.symlink_to(secret)
        res = terrain_service.verifier_integrite_fichier(
            db_session, analyse.id)
        assert res["correspond"] is False
        # PAS d'oracle : le hash du fichier cible ne doit pas fuiter.
        assert res["hash_calcule"] is None
        assert "hors du répertoire autorisé" in res["raison"]
    finally:
        s.FICHIERS_BRUTS_DIR = old


def test_lecture_sans_suivi_refuse_symlink_substitue(tmp_path):
    """Scénario de course : le contrôle realpath a validé le chemin, puis
    un symlink a été substitué avant l'ouverture → O_NOFOLLOW refuse."""
    import pytest as _pytest
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"donnees-confidentielles")
    lien = tmp_path / "lien.dat"
    lien.symlink_to(secret)  # même dans la racine : refusé à l'ouverture
    with _pytest.raises(OSError):
        terrain_service._sha256_fichier_sans_suivi(str(lien))
    # Un fichier régulier passe toujours.
    regulier = tmp_path / "ok.dat"
    regulier.write_bytes(b"abc")
    assert terrain_service._sha256_fichier_sans_suivi(
        str(regulier)) == hashlib.sha256(b"abc").hexdigest()


def test_integrite_fichier_legitime_ok(db_session, tmp_path, monkeypatch):
    projet = Project(name="Integrite OK")
    db_session.add(projet)
    db_session.flush()
    bruts = tmp_path / "bruts2"
    bruts.mkdir()
    import webintel.config.settings as settings_mod
    s = settings_mod.get_settings()
    old = s.FICHIERS_BRUTS_DIR
    s.FICHIERS_BRUTS_DIR = str(bruts)
    try:
        cible = bruts / "ok.dat"
        cible.write_bytes(b"donnees intactes")
        analyse = AnalyseChimique(project_id=projet.id, methode="HPLC")
        db_session.add(analyse)
        db_session.flush()
        terrain_service.enregistrer_fichier_brut(
            db_session, analyse.id, str(cible))
        db_session.commit()
        res = terrain_service.verifier_integrite_fichier(
            db_session, analyse.id)
        assert res["correspond"] is True
        assert res["present"] is True
    finally:
        s.FICHIERS_BRUTS_DIR = old


# ---------------------------------------------------------------------------
# G. Restore : archive aux champs inconnus → 400 avant toute écriture
# ---------------------------------------------------------------------------

def _archive_malveillante(chemin):
    lignes = [{"id": 99999, "name": "Evil",
               "cle_inconnue": "boom", "code": "x"}]
    brut = json.dumps(lignes).encode()
    manifest = {
        "format": "webintel-backup", "schema_version": "1.0",
        "webintel_version": "x", "created_at": "2026-01-01",
        "project_id": None, "photos": 0,
        "tables": {"langues": {
            "lignes": 1, "sha256": hashlib.sha256(brut).hexdigest()}},
    }
    with zipfile.ZipFile(chemin, "w") as zf:
        zf.writestr("tables/langues.json", brut)
        zf.writestr("manifest.json", json.dumps(manifest))


def test_restore_champs_inconnus_refuse_avant_ecriture(db_session, tmp_path):
    from webintel.offline import sauvegarde as sauvegarde_mod
    from webintel.science.models import Langue
    zpath = str(tmp_path / "malin.zip")
    _archive_malveillante(zpath)
    avant = db_session.query(Langue).count()
    with pytest.raises(ValueError, match="champs inconnus"):
        sauvegarde_mod.importer_projet(db_session, zpath)
    db_session.rollback()
    # Aucune écriture partielle.
    assert db_session.query(Langue).count() == avant
    assert db_session.query(Langue).filter(
        Langue.code == "x").one_or_none() is None


# ---------------------------------------------------------------------------
# W. Session wizard expirée d'un autre projet : 403, JAMAIS supprimée
# ---------------------------------------------------------------------------

def test_session_expiree_autre_projet_non_supprimee(db_session):
    import webintel.ui.chercheur_etudes_routes as routes_mod
    from fastapi import HTTPException
    from types import SimpleNamespace
    pa, _ka = _project_with_key(db_session, "Wiz A")
    pb, _kb = _project_with_key(db_session, "Wiz B")
    sid = secrets.token_hex(16)
    db_session.add(WizardSession(
        session_id=sid, project_id=pa.id, owner="ui:KEYA",
        status="en_cours",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        payload={},
    ))
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        routes_mod._charger_session(
            db_session, SimpleNamespace(id=pb.id), sid, "ui:KEYB")
    assert exc.value.status_code == 403
    db_session.rollback()
    # La session du projet A existe toujours (pas de mutation inter-projets).
    assert db_session.get(WizardSession, sid) is not None


def test_session_expiree_propre_projet_purgee_404(db_session):
    """Non-régression : sa propre session expirée reste purgée (404)."""
    import webintel.ui.chercheur_etudes_routes as routes_mod
    from fastapi import HTTPException
    from types import SimpleNamespace
    pa, _ka = _project_with_key(db_session, "Wiz C")
    sid = secrets.token_hex(16)
    db_session.add(WizardSession(
        session_id=sid, project_id=pa.id, owner="ui:KEYA",
        status="en_cours",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        payload={},
    ))
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        routes_mod._charger_session(
            db_session, SimpleNamespace(id=pa.id), sid, "ui:KEYA")
    assert exc.value.status_code == 404
    assert db_session.get(WizardSession, sid) is None


def test_session_expiree_meme_projet_autre_utilisateur_purgee_404(db_session):
    """Contrat historique : même projet, autre utilisateur, expirée →
    404 + purge (le projet est le périmètre de confiance ; la session
    expirée est de toute façon inaccessible)."""
    import webintel.ui.chercheur_etudes_routes as routes_mod
    from fastapi import HTTPException
    from types import SimpleNamespace
    pa, _ka = _project_with_key(db_session, "Wiz D")
    sid = secrets.token_hex(16)
    db_session.add(WizardSession(
        session_id=sid, project_id=pa.id, owner="ui:AUTRE",
        status="en_cours",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        payload={},
    ))
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        routes_mod._charger_session(
            db_session, SimpleNamespace(id=pa.id), sid, "ui:KEYA")
    assert exc.value.status_code == 404
    assert db_session.get(WizardSession, sid) is None


# ---------------------------------------------------------------------------
# GPS via l'UI : bornes exactes acceptées, hors-bornes rejeté (rien stocké)
# ---------------------------------------------------------------------------

def test_gps_ui_hors_bornes_rejete(client, db_session):
    """Le formulaire /collectes/nouvelle valide comme l'API (valider_gps)."""
    from webintel.offline.models import CollecteTerrain
    projet = Project(name="GPS UI")
    db_session.add(projet)
    db_session.commit()
    avant = db_session.query(CollecteTerrain).count()
    r = client.post(
        "/collectes/nouvelle",
        data={"localite": "Ouagadougou",
              "latitude": "91.0", "longitude": "0",
              "niveau_confidentialite": "public"},
        files=[],
    )
    assert r.status_code == 200
    assert "hors plage" in r.text
    db_session.expire_all()
    assert db_session.query(CollecteTerrain).count() == avant


def test_gps_ui_bornes_exactes_acceptees(client, db_session):
    from webintel.offline.models import CollecteTerrain
    projet = Project(name="GPS UI 2")
    db_session.add(projet)
    db_session.commit()
    r = client.post(
        "/collectes/nouvelle",
        data={"localite": "Pôle Nord",
              "latitude": "90.0", "longitude": "-180.0",
              "niveau_confidentialite": "public"},
        files=[],
    )
    assert r.status_code == 200
    db_session.expire_all()
    collecte = db_session.query(CollecteTerrain).filter(
        CollecteTerrain.localite == "Pôle Nord").one()
    assert collecte.latitude == 90.0
    assert collecte.longitude == -180.0


# ---------------------------------------------------------------------------
# Z. Zip bomb / zip dangereux → ValueError AVANT toute décompression
# ---------------------------------------------------------------------------

def _zip_malveillant(chemin, nom_membre, contenu, sha_manifeste=None):
    brut = contenu
    sha = sha_manifeste or hashlib.sha256(brut).hexdigest()
    manifest = {
        "format": "webintel-backup", "schema_version": "1.0",
        "webintel_version": "x", "created_at": "2026-01-01",
        "project_id": None, "photos": 0,
        "tables": {"x": {"lignes": 0, "sha256": sha}},
    }
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr(nom_membre, brut)


def test_zip_bomb_ratio_refuse_sans_oom(db_session, tmp_path):
    """200 Mo de zéros dans ~200 Ko : refusé sur les métadonnées,
    jamais décompressé en mémoire."""
    from webintel.offline import sauvegarde as sauvegarde_mod
    zpath = str(tmp_path / "bombe.zip")
    _zip_malveillant(zpath, "tables/x.json", b"0" * 200_000_000)
    with pytest.raises(ValueError, match="ratio de compression anormal"):
        sauvegarde_mod.importer_projet(db_session, zpath)


def test_zip_membre_geant_refuse(db_session, tmp_path, monkeypatch):
    """Un membre annonçant > 256 Mo est refusé sans être lu."""
    from webintel.offline import sauvegarde as sauvegarde_mod
    monkeypatch.setattr(sauvegarde_mod, "_TAILLE_MAX_MEMBRE", 1024)
    zpath = str(tmp_path / "geant.zip")
    # Contenu incompressible : le ratio ne se déclenche pas, seule la
    # borne de taille parle.
    _zip_malveillant(zpath, "tables/x.json", secrets.token_bytes(10_000))
    with pytest.raises(ValueError, match="[Tt]aille maximale") as exc:
        sauvegarde_mod.importer_projet(db_session, zpath)
    assert "tables/x.json" in str(exc.value)


def test_zip_membre_chiffre_refuse(db_session, tmp_path):
    """Un membre chiffré est refusé (non pris en charge)."""
    from webintel.offline import sauvegarde as sauvegarde_mod
    import io as _io
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("tables/x.json", b"{}")
    # Positionne le bit « chiffré » sur le membre tables/x.json : on
    # localise l'en-tête locale (PK\x03\x04, nom à +30, drapeau à +6)
    # et le répertoire central (PK\x01\x02, nom à +46, drapeau à +8).
    data = bytearray(buf.getvalue())
    cible = b"tables/x.json"
    debut, trouves = 0, 0
    while True:
        pos = bytes(data).find(cible, debut)
        if pos == -1:
            break
        if bytes(data)[pos - 30:pos - 26] == b"PK\x03\x04":
            data[pos - 30 + 6] |= 0x1
            trouves += 1
        elif bytes(data)[pos - 46:pos - 42] == b"PK\x01\x02":
            data[pos - 46 + 8] |= 0x1
            trouves += 1
        debut = pos + 1
    assert trouves == 2, f"en-têtes trouvées : {trouves}"
    zpath = str(tmp_path / "chiffre.zip")
    with open(zpath, "wb") as fh:
        fh.write(bytes(data))
    with pytest.raises(ValueError, match="chiffré"):
        sauvegarde_mod.importer_projet(db_session, zpath)


# ---------------------------------------------------------------------------
# Q. Recherche : requête démesurée → 422 (pas de LIKE géant).
# ---------------------------------------------------------------------------

def test_recherche_q_demesuree_422(client):
    r = client.get("/api/science/recherche", params={"q": "a" * 201})
    assert r.status_code == 422, f"attendu 422, reçu {r.status_code}"


def test_recherche_q_normale_ok(client):
    r = client.get("/api/science/recherche", params={"q": "karité"})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# K. InChIKey déclarée fausse (mais au bon format) : divergence signalée,
#    JAMAIS présentée comme calculée ni écrasée.
# ---------------------------------------------------------------------------

def test_inchikey_fausse_signalee_jamais_ecrasee(db_session):
    """Une InChIKey déclarée, syntaxiquement valide mais fausse, est
    signalée comme divergente (valeur stockée vs valeur calculée) et
    n'est jamais écrasée ni présentée comme calculée."""
    from webintel.science import service as science_service
    from webintel.science import normalisation_moleculaire as normalisation
    projet = Project(name="InChIKey")
    db_session.add(projet)
    db_session.flush()
    compose, _ = science_service.create_compound(
        db_session, "Faux Key", smiles="CCO",
        inchikey="AAAAAAAAAAAAAA-BBBBBBBBBB-C")
    db_session.commit()
    rapport = normalisation.normaliser_compose(
        db_session, compose.id, ecrire=True)
    divergences = [d for d in rapport["divergences"]
                   if d["champ"] == "inchikey"]
    assert divergences, (
        "la fausse InChIKey aurait dû être signalée comme divergente, "
        f"rapport={rapport!r}")
    assert divergences[0]["valeur_stockee"] == "AAAAAAAAAAAAAA-BBBBBBBBBB-C"
    assert divergences[0]["valeur_calculee"] != "AAAAAAAAAAAAAA-BBBBBBBBBB-C"
    # La valeur déclarée n'est PAS écrasée par le calcul.
    db_session.refresh(compose)
    assert compose.inchikey == "AAAAAAAAAAAAAA-BBBBBBBBBB-C"
    # ... ni présentée comme « complétée par le calcul ».
    assert "inchikey" not in rapport["champs_completes"]


# ---------------------------------------------------------------------------
# X. Export public : données restreint/confidentiel/projet JAMAIS incluses.
# ---------------------------------------------------------------------------

def test_export_public_ne_fuit_pas_le_restreint(db_session):
    """Noms, usages, échantillons et pharmacognosie en
    restreint/confidentiel/projet sont exclus de l'export public ;
    le GPS précis et les identités ne fuient jamais."""
    from webintel.science.models import NiveauConfidentialite
    projet = Project(name="Export X")
    db_session.add(projet)
    db_session.flush()
    plante = Plant(scientific_name="Plante Secrète")
    db_session.add(plante)
    db_session.flush()
    # Nom vernaculaire PUBLIC (doit apparaître) + RESTREINT (exclu).
    terrain_service.ajouter_nom_vernaculaire(
        db_session, plante.id, projet.id, "Nom Public",
        niveau_confidentialite=NiveauConfidentialite.PUBLIC.value)
    terrain_service.ajouter_nom_vernaculaire(
        db_session, plante.id, projet.id, "Nom Secret Clan",
        niveau_confidentialite=NiveauConfidentialite.RESTREINT.value)
    # Échantillon restreint avec GPS + collecteur (ne doit pas fuir).
    terrain_service.creer_echantillon(
        db_session, projet.id, plante.id, "ECH-SECRET",
        latitude=12.3714, longitude=-1.5197,
        niveau_confidentialite=NiveauConfidentialite.CONFIDENTIEL.value)
    # Pharmacognosie restreinte (exclue).
    terrain_service.definir_pharmacognosie(
        db_session, plante.id, projet.id,
        identification_macro="Détail réservé au laboratoire",
        niveau_confidentialite=NiveauConfidentialite.RESTREINT.value)
    db_session.commit()
    export = terrain_service.export_public_plante(db_session, plante.id)
    noms = [n["nom"] for n in export["noms_vernaculaires"]]
    assert "Nom Public" in noms
    assert "Nom Secret Clan" not in noms
    assert export["echantillons"] == []
    assert export.get("pharmacognosie") in (None, {})
    # Aucune coordonnée ni identité dans tout l'export sérialisé.
    import json as _json
    texte = _json.dumps(export, ensure_ascii=False)
    assert "12.3714" not in texte and "-1.5197" not in texte
    assert "Nom Secret Clan" not in texte
    assert "Détail réservé au laboratoire" not in texte


# ---------------------------------------------------------------------------
# P. Provenance : entité inexistante → 404 ; autre projet → 403
# ---------------------------------------------------------------------------

def test_provenance_entite_inexistante_404(key_mode, db_session):
    pa, ka = _project_with_key(db_session, "Prov A")
    with TestClient(webintel.main.app,
                    base_url="https://testserver") as api:
        r = api.post("/api/science/provenance",
                     headers=_h(ka),
                     json={"entity_type": "plant", "entity_id": 424242,
                           "source_name": "Source inventée"})
        assert r.status_code == 404, f"attendu 404, reçu {r.status_code}"


def test_provenance_type_inconnu_422(key_mode, db_session):
    pa, ka = _project_with_key(db_session, "Prov B")
    with TestClient(webintel.main.app,
                    base_url="https://testserver") as api:
        r = api.post("/api/science/provenance",
                     headers=_h(ka),
                     json={"entity_type": "vaisseau_spatial",
                           "entity_id": 1, "source_name": "X"})
        assert r.status_code == 422, f"attendu 422, reçu {r.status_code}"


def test_provenance_legitime_ok(key_mode, db_session):
    from webintel.science import service as science_service
    pa, ka = _project_with_key(db_session, "Prov C")
    plante, _ = science_service.create_plant(db_session, "Provenance OK")
    db_session.commit()
    with TestClient(webintel.main.app,
                    base_url="https://testserver") as api:
        r = api.post("/api/science/provenance",
                     headers=_h(ka),
                     json={"entity_type": "plant",
                           "entity_id": plante.id,
                           "source_name": "Herbier de test"})
        assert r.status_code == 201, r.text
