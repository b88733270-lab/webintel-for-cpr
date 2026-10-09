"""Tests « Favoris & Collections » (v3.0.0) : modèles, service, UI.

Isolation par projet vérifiée au niveau service ET via TestClient
(projet par défaut en mode local, second projet créé directement en
base pour simuler un autre périmètre). Seed désactivé par conftest
(``WEBINTEL_SEED_INITIAL=0``).
"""

import pytest

from webintel.database.models import Collection, Entity, Favorite, Project
from webintel.ui import collections_routes as svc


@pytest.fixture(autouse=True)
def _nettoyer_collections(db_session):
    """Les nouvelles tables ne sont pas (encore) dans la purge centrale
    de conftest : nettoyage local, enfants avant parents."""
    db_session.query(Favorite).delete()
    db_session.query(Collection).delete()
    db_session.commit()
    yield
    db_session.query(Favorite).delete()
    db_session.query(Collection).delete()
    db_session.commit()


@pytest.fixture()
def deux_projets(db_session):
    p1 = Project(name="Projet Alpha")
    p2 = Project(name="Projet Beta")
    db_session.add_all([p1, p2])
    db_session.commit()
    return p1, p2


# ---------------------------------------------------------------------------
# Service : collections
# ---------------------------------------------------------------------------


def test_creer_collection(db_session, deux_projets):
    p1, _ = deux_projets
    c = svc.creer_collection(db_session, p1, "Veille", description="Suivi hebdo")
    assert c.id is not None
    assert c.project_id == p1.id
    assert [x.nom for x in svc.collections_du_projet(db_session, p1.id)] == ["Veille"]


def test_creer_collection_nom_vide_et_doublon(db_session, deux_projets):
    p1, _ = deux_projets
    with pytest.raises(ValueError):
        svc.creer_collection(db_session, p1, "  ")
    svc.creer_collection(db_session, p1, "Veille")
    with pytest.raises(ValueError):
        svc.creer_collection(db_session, p1, "Veille")


def test_meme_nom_autorise_dans_un_autre_projet(db_session, deux_projets):
    p1, p2 = deux_projets
    svc.creer_collection(db_session, p1, "Veille")
    svc.creer_collection(db_session, p2, "Veille")  # pas de conflit inter-projet
    assert len(svc.collections_du_projet(db_session, p2.id)) == 1


# ---------------------------------------------------------------------------
# Service : favoris
# ---------------------------------------------------------------------------


def test_ajouter_favori_hors_collection_et_dans_collection(db_session, deux_projets):
    p1, _ = deux_projets
    c = svc.creer_collection(db_session, p1, "Veille")
    f1 = svc.ajouter_favori(db_session, p1, "Exemple", "url", "https://exemple.com/a")
    f2 = svc.ajouter_favori(db_session, p1, "Entité 1", "entite", "1", collection_id=c.id)
    assert f1.collection_id is None
    assert f2.collection_id == c.id
    assert [f.id for f in svc.favoris_hors_collection(db_session, p1.id)] == [f1.id]
    assert [f.id for f in svc.favoris_de_collection(db_session, c.id)] == [f2.id]


def test_ajouter_favori_doublon_refuse(db_session, deux_projets):
    p1, _ = deux_projets
    c = svc.creer_collection(db_session, p1, "Veille")
    svc.ajouter_favori(db_session, p1, "Exemple", "url", "https://exemple.com/a", collection_id=c.id)
    with pytest.raises(ValueError):
        svc.ajouter_favori(db_session, p1, "Autre titre", "url", "https://exemple.com/a", collection_id=c.id)
    # Hors collection : doublon également refusé (vérification applicative).
    svc.ajouter_favori(db_session, p1, "Libre", "rapport", "7")
    with pytest.raises(ValueError):
        svc.ajouter_favori(db_session, p1, "Libre bis", "rapport", "7")
    # Même objet dans une AUTRE collection : autorisé.
    c2 = svc.creer_collection(db_session, p1, "Archive")
    svc.ajouter_favori(db_session, p1, "Exemple", "url", "https://exemple.com/a", collection_id=c2.id)


def test_ajouter_favori_champs_invalides(db_session, deux_projets):
    p1, _ = deux_projets
    with pytest.raises(ValueError):
        svc.ajouter_favori(db_session, p1, "", "url", "https://exemple.com")
    with pytest.raises(ValueError):
        svc.ajouter_favori(db_session, p1, "Titre", "inconnu", "x")
    with pytest.raises(ValueError):
        svc.ajouter_favori(db_session, p1, "Titre", "url", "  ")


def test_deplacer_favori_et_doublon_destination(db_session, deux_projets):
    p1, _ = deux_projets
    c = svc.creer_collection(db_session, p1, "Veille")
    f = svc.ajouter_favori(db_session, p1, "Exemple", "url", "https://exemple.com/a")
    svc.deplacer_favori(db_session, p1, f, c.id)
    assert db_session.get(Favorite, f.id).collection_id == c.id
    svc.deplacer_favori(db_session, p1, db_session.get(Favorite, f.id), None)
    assert db_session.get(Favorite, f.id).collection_id is None


def test_supprimer_collection_conserve_les_favoris(db_session, deux_projets):
    p1, _ = deux_projets
    c = svc.creer_collection(db_session, p1, "Veille")
    svc.ajouter_favori(db_session, p1, "Exemple", "url", "https://exemple.com/a", collection_id=c.id)
    conserves = svc.supprimer_collection(db_session, p1, c)
    assert conserves == 1
    assert db_session.query(Collection).count() == 0
    restants = svc.favoris_hors_collection(db_session, p1.id)
    assert len(restants) == 1 and restants[0].titre == "Exemple"


# ---------------------------------------------------------------------------
# Isolation par projet (service)
# ---------------------------------------------------------------------------


def test_isolation_projet_service(db_session, deux_projets):
    p1, p2 = deux_projets
    c1 = svc.creer_collection(db_session, p1, "Secret Alpha")
    svc.ajouter_favori(db_session, p1, "Fav Alpha", "entite", "11", collection_id=c1.id)
    # Le projet Beta ne voit ni la collection ni ses favoris.
    assert svc.collections_du_projet(db_session, p2.id) == []
    assert svc.favoris_du_projet(db_session, p2.id) == []
    assert svc.collection_visible(db_session, p2, c1.id) is None
    # Ajout dans la collection d'un autre projet : refusé.
    with pytest.raises(ValueError):
        svc.ajouter_favori(db_session, p2, "Intrusion", "entite", "12", collection_id=c1.id)
    # Déplacement vers la collection d'un autre projet : refusé.
    f2 = svc.ajouter_favori(db_session, p2, "Fav Beta", "entite", "13")
    with pytest.raises(ValueError):
        svc.deplacer_favori(db_session, p2, f2, c1.id)


# ---------------------------------------------------------------------------
# Parcours UI (TestClient)
# ---------------------------------------------------------------------------


def test_ui_collections_page_vide(client):
    r = client.get("/collections")
    assert r.status_code == 200, r.text[:500]
    assert "Favoris et collections" in r.text
    assert "Créer une collection" in r.text
    assert "Ajouter un favori" in r.text


def test_ui_parcours_complet(client, db_session):
    r = client.post("/collections", data={"nom": "Ma veille", "description": "Test UI"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    detail_url = r.headers["location"]
    collection_id = int(detail_url.rsplit("/", 1)[1])

    r = client.get("/collections")
    assert r.status_code == 200 and "Ma veille" in r.text

    r = client.get(detail_url)
    assert r.status_code == 200 and "Ma veille" in r.text
    assert "Renommer" in r.text

    # Ajout d'un favori DANS la collection.
    r = client.post("/collections/favoris/ajouter", data={
        "titre": "Page exemple", "type_objet": "url",
        "reference": "https://exemple.com/page", "collection_id": str(collection_id),
        "note": "À relire",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    r = client.get(detail_url)
    assert "Page exemple" in r.text and "À relire" in r.text

    # Doublon via UI : refusé avec message, pas de 500.
    r = client.post("/collections/favoris/ajouter", data={
        "titre": "Copie", "type_objet": "url",
        "reference": "https://exemple.com/page", "collection_id": str(collection_id),
    })
    assert r.status_code == 200 and "doublon" in r.text.lower()
    assert db_session.query(Favorite).count() == 1

    # Renommage.
    r = client.post(f"/collections/{collection_id}/renommer",
                    data={"nom": "Veille renommée"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Veille renommée" in client.get(detail_url).text

    # Déplacement hors collection.
    favori_id = db_session.query(Favorite).one().id
    r = client.post(f"/collections/favoris/{favori_id}/deplacer",
                    data={"collection_id": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/collections"
    assert "Page exemple" in client.get("/collections").text

    # Suppression de la collection : le favori est conservé hors collection.
    r = client.post(f"/collections/{collection_id}/supprimer", follow_redirects=False)
    assert r.status_code == 303
    assert db_session.query(Collection).count() == 0
    assert db_session.query(Favorite).count() == 1

    # Retrait du favori.
    r = client.post(f"/collections/favoris/{favori_id}/retirer", follow_redirects=False)
    assert r.status_code == 303
    assert db_session.query(Favorite).count() == 0


def test_ui_collection_introuvable_message(client):
    r = client.get("/collections/999999")
    assert r.status_code == 200, r.text[:500]
    assert "introuvable" in r.text.lower()


def test_ui_ajout_favori_depuis_fiche_entite(client, db_session, make_project):
    projet_id = make_project("Projet entité favori")
    entite = Entity(project_id=projet_id, entity_key="product:fav",
                    entity_type="product", name="Produit Favori Test",
                    data={}, sources=[])
    db_session.add(entite)
    db_session.commit()
    r = client.get(f"/entites/{entite.id}")
    assert r.status_code == 200, r.text[:500]
    assert "Ajouter aux favoris" in r.text
    assert "Produit Favori Test" in r.text
    r = client.post("/collections/favoris/ajouter", data={
        "titre": "Produit Favori Test", "type_objet": "entite",
        "reference": str(entite.id),
    }, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/collections")
    assert r.status_code == 200 and "Produit Favori Test" in r.text
    assert f'href="/entites/{entite.id}"' in r.text


def test_ui_isolation_autre_projet_invisible(client, db_session, deux_projets):
    """Mode local : l'UI affiche le projet par défaut (le premier) — les
    collections d'un autre projet n'y apparaissent pas."""
    _, p2 = deux_projets
    svc.creer_collection(db_session, p2, "Collection Autre Projet")
    svc.ajouter_favori(db_session, p2, "Favori Autre Projet", "url",
                       "https://autre-projet.exemple/page")
    html = client.get("/collections").text
    assert "Collection Autre Projet" not in html
    assert "Favori Autre Projet" not in html
    r = client.get("/collections", follow_redirects=True)
    assert r.status_code == 200


def test_ui_favori_manuel_types(client):
    for type_objet, reference, titre in [
        ("rapport", "3", "Rapport hebdo"),
        ("recherche", "moringa", "Recherche Moringa"),
    ]:
        r = client.post("/collections/favoris/ajouter", data={
            "titre": titre, "type_objet": type_objet, "reference": reference,
        }, follow_redirects=False)
        assert r.status_code == 303, r.text[:500]
    html = client.get("/collections").text
    assert "Rapport hebdo" in html and "Recherche Moringa" in html
