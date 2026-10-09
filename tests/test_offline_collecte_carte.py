"""Tests offline-first : collecte de terrain, photos, carte, GPS.

- saisie offline (formulaire) → enregistrée localement + mise en file ;
- photos : stockage local, miniatures compressées, lazy loading ;
- badges « donnée locale » vs « donnée synchronisée » ;
- carte SVG 100 % locale (aucune tuile / aucun appel réseau) ;
- protection GPS : une donnée confidentielle n'est JAMAIS exposée en
  clair à un non-auteur (liste, détail, carte, SVG) ; restreint =
  flouté ;
- cloisonnement projet (P0) : 404 hors projet.
"""

from __future__ import annotations

import io
import os

import pytest

from webintel.database import models as core_models
from webintel.offline import cartographie as carto
from webintel.offline import photos as photos_mod
from webintel.offline import reseau
from webintel.offline.models import (
    CollecteTerrain,
    NiveauConfidentialite,
    OperationSync,
)
from webintel.offline.synchronisation import statut_entite
from webintel.science.models import Echantillon, Plant


@pytest.fixture(autouse=True)
def _mode_reseau_propre():
    yield
    reseau.definir_mode_hors_ligne(None)


def _projet(db_session):
    projet = db_session.query(core_models.Project).order_by(
        core_models.Project.id).first()
    if projet is None:
        projet = core_models.Project(name="Défaut")
        db_session.add(projet)
        db_session.commit()  # visible de la session du client de test
    return projet


def _png_rouge():
    from PIL import Image

    img = Image.new("RGB", (800, 600), (200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Règle de confidentialité GPS (unitaire)
# ---------------------------------------------------------------------------

def test_gps_public_exact_pour_tous():
    pt = carto.coordonnees_affichees(12.3714, -1.5197, "public", "x", "y")
    assert (pt.lat, pt.lon, pt.statut, pt.masque) == \
        (12.3714, -1.5197, "exact", False)


def test_gps_auteur_voit_toujours_exact():
    for niveau in ("public", "restreint", "confidentiel", "projet",
                   "chercheur"):
        pt = carto.coordonnees_affichees(12.3714, -1.5197, niveau,
                                         "adama", "adama")
        assert pt.statut == "exact", niveau
        assert pt.masque is False


def test_gps_restreint_floute_pour_non_auteur():
    pt = carto.coordonnees_affichees(12.3714, -1.5197, "restreint",
                                     "visiteur", "adama")
    assert pt.statut == "floute" and pt.masque is True
    assert pt.lat != 12.3714 and pt.lon != -1.5197
    # Floutage ~55 km : loin du point exact, mais dans le pays.
    assert carto.distance_km(12.3714, -1.5197, pt.lat, pt.lon) > 1.0
    assert 9.0 < pt.lat < 15.5 and -6.0 < pt.lon < 3.0


def test_gps_confidentiel_jamais_expose_non_auteur():
    for niveau in ("confidentiel", "chercheur"):
        pt = carto.coordonnees_affichees(12.3714, -1.5197, niveau,
                                         "visiteur", "adama")
        assert pt.lat is None and pt.lon is None
        assert pt.statut == "masque" and pt.masque is True


def test_gps_absent_sans_coordonnees():
    pt = carto.coordonnees_affichees(None, None, "public", "a", "a")
    assert pt.statut == "absent"


def test_svg_sans_aucune_dependance_reseau():
    svg = carto.generer_carte_svg([
        {"lat": 12.3, "lon": -1.7, "statut": "exact", "titre": "P1"},
        {"lat": None, "lon": None, "statut": "masque", "titre": "P2",
         "masque": True},
    ])
    assert "<svg" in svg and "Burkina Faso" in svg
    assert "schématique" in svg
    # Aucune ressource externe : pas de tuile, pas d'image distante.
    for interdit in ("https://", "<image", "xlink:href"):
        assert interdit not in svg


# ---------------------------------------------------------------------------
# Saisie offline via le formulaire
# ---------------------------------------------------------------------------

def test_creation_collecte_hors_ligne_mise_en_file(client, db_session):
    reseau.definir_mode_hors_ligne(True)
    projet = _projet(db_session)
    r = client.post(
        "/collectes/nouvelle",
        data={
            "localite": "Kaya",
            "nom_plante_saisi": "karité",
            "latitude": "13.0907",
            "longitude": "-1.0835",
            "echantillon_ref": "EK-001",
            "voucher": "OUA-2026-0001",
            "partie_utilisee": "feuilles",
            "niveau_confidentialite": "restreint",
            "notes": "abondant en bordure de champ",
        },
        files=[],
    )
    assert r.status_code == 200
    collecte = db_session.query(CollecteTerrain).filter(
        CollecteTerrain.project_id == projet.id).one()
    assert collecte.localite == "Kaya"
    assert collecte.latitude == pytest.approx(13.0907)
    assert collecte.echantillon_ref == "EK-001"
    assert collecte.voucher == "OUA-2026-0001"
    # Hors connexion → mise en file, badge « locale ».
    assert statut_entite(db_session, "collecte", collecte.id) == "locale"
    assert "Donnée locale" in client.get("/collectes").text


def test_creation_collecte_en_ligne_pas_de_file(client, db_session):
    reseau.definir_mode_hors_ligne(False)
    projet = _projet(db_session)
    client.post("/collectes/nouvelle",
                data={"localite": "Manga",
                      "niveau_confidentialite": "public"}, files=[])
    collecte = db_session.query(CollecteTerrain).filter(
        CollecteTerrain.project_id == projet.id).one()
    assert statut_entite(db_session, "collecte", collecte.id) == \
        "synchronisee"
    assert "Donnée synchronisée" in client.get("/collectes").text


def test_suppression_collecte_hors_ligne_en_file(client, db_session):
    reseau.definir_mode_hors_ligne(True)
    projet = _projet(db_session)
    c = CollecteTerrain(localite="X", auteur="u", project_id=projet.id)
    db_session.add(c)
    db_session.commit()
    cid = c.id
    r = client.post(f"/collectes/{cid}/supprimer")
    assert r.status_code == 200
    db_session.expire_all()  # la suppression a eu lieu dans une autre session
    assert db_session.get(CollecteTerrain, cid) is None


# ---------------------------------------------------------------------------
# Photos : stockage local + miniatures + lazy loading
# ---------------------------------------------------------------------------

def test_upload_photo_miniature_et_lazy_loading(client, db_session):
    reseau.definir_mode_hors_ligne(True)
    _projet(db_session)
    contenu = _png_rouge()
    r = client.post(
        "/collectes/nouvelle",
        data={"localite": "Tenkodogo",
              "niveau_confidentialite": "public"},
        files=[("photos", ("feuille.png", contenu, "image/png"))],
    )
    assert r.status_code == 200
    collecte = db_session.query(CollecteTerrain).order_by(
        CollecteTerrain.id.desc()).first()
    assert len(collecte.photos) == 1
    meta = collecte.photos[0]
    dossier = os.path.join(photos_mod.repertoire_photos(), str(collecte.id))
    assert os.path.isfile(os.path.join(dossier, meta["fichier"]))
    assert os.path.isfile(os.path.join(dossier, meta["miniature"]))
    from PIL import Image

    with Image.open(os.path.join(dossier, meta["miniature"])) as mini:
        assert max(mini.size) <= 256  # miniature compressée
    # Détail : miniature en lazy loading, jamais l'original d'emblée.
    html = client.get(f"/collectes/{collecte.id}").text
    assert 'loading="lazy"' in html
    assert meta["miniature"] in html


def test_photo_format_refuse():
    with pytest.raises(ValueError):
        photos_mod.enregistrer_photo(999, b"GIF89a...", "virus.exe")


def test_photo_trop_lourde_refusee():
    with pytest.raises(ValueError):
        photos_mod.enregistrer_photo(999, b"x" * (11 * 1024 * 1024),
                                     "grosse.jpg")


def test_chemin_photo_anti_traversal():
    assert photos_mod.chemin_photo(1, "../../etc/passwd") is None
    assert photos_mod.chemin_photo(1, "mini_abc.jpg") is None  # inexistant


def test_servir_photo_interdit_hors_liste(client, db_session):
    _projet(db_session)
    c = CollecteTerrain(localite="Y", auteur="u",
                       project_id=_projet(db_session).id)
    db_session.add(c)
    db_session.commit()
    r = client.get(f"/collectes/{c.id}/photos/mini_pirate.jpg")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Carte : points, protection GPS, échantillons
# ---------------------------------------------------------------------------

def _jeu_carte(db_session):
    projet = _projet(db_session)
    c1 = CollecteTerrain(
        localite="Ouaga", latitude=12.3714, longitude=-1.5197,
        niveau_confidentialite="public", auteur="utilisateur local",
        project_id=projet.id)
    c2 = CollecteTerrain(
        localite="Dori", latitude=13.9911, longitude=-0.0333,
        niveau_confidentialite="confidentiel", auteur="adama",
        project_id=projet.id)
    db_session.add_all([c1, c2])
    db_session.flush()
    plante = Plant(scientific_name="Vitellaria paradoxa")
    db_session.add(plante)
    db_session.flush()
    ech = Echantillon(
        plant_id=plante.id, code_echantillon="ECH-42",
        project_id=projet.id, localite="Fada", latitude=12.0600,
        longitude=0.3490, niveau_confidentialite="restreint",
        cree_par="utilisateur local")
    db_session.add(ech)
    db_session.commit()
    return c1, c2, ech


def test_carte_points_et_legende(client, db_session):
    _jeu_carte(db_session)
    r = client.get("/carte")
    assert r.status_code == 200
    assert "<svg" in r.text
    assert "schématique" in r.text
    assert "Échantillon" in r.text
    assert "ECH-42" in r.text


def test_carte_gps_confidentiel_jamais_en_clair(client, db_session):
    _jeu_carte(db_session)
    # Vue visiteur : le point confidentiel (Dori 13.9911, -0.0333)
    # ne doit apparaître nulle part en clair.
    html = client.get("/carte?vue=visiteur").text
    assert "13.9911" not in html
    assert "-0.0333" not in html
    assert "masqu" in html  # badge « masqué »
    # … mais le point public reste visible en exact.
    assert "12.3714" in html


def test_liste_collectes_masque_gps_confidentiel(client, db_session):
    _jeu_carte(db_session)
    html = client.get("/collectes").text
    assert "13.9911" not in html  # auteur = adama ≠ utilisateur local
    assert "GPS masqué" in html


def test_detail_collecte_confidentiel_non_auteur(client, db_session):
    _, c2, _ = _jeu_carte(db_session)
    html = client.get(f"/collectes/{c2.id}").text
    assert html  # 200
    assert "13.9911" not in html
    assert "masqué" in html


def test_echantillon_restreint_floute_visiteur(client, db_session):
    _jeu_carte(db_session)
    html = client.get("/carte?vue=visiteur").text
    # Échantillon restreint (12.06, 0.349) : flouté, pas exact.
    assert "12.0600" not in html
    assert "flouté" in html


def test_carte_sans_coordonnees_absent_du_svg(client, db_session):
    projet = _projet(db_session)
    db_session.add(CollecteTerrain(localite="Sans GPS", auteur="u",
                                  project_id=projet.id))
    db_session.commit()
    r = client.get("/carte")
    assert r.status_code == 200


def test_collecte_hors_projet_404(client, db_session):
    _projet(db_session)  # « Défaut » : projet de la session UI (premier)
    autre = core_models.Project(name="Autre projet")
    db_session.add(autre)
    db_session.commit()
    c = CollecteTerrain(localite="secret", auteur="u", project_id=autre.id)
    db_session.add(c)
    db_session.commit()
    # La session UI voit le projet « Défaut » (premier) : 404.
    assert client.get(f"/collectes/{c.id}").status_code == 404
    assert client.get(f"/collectes/{c.id}/photos/mini_x.jpg").status_code \
        == 404
