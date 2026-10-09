"""Tests de l'interface web scientifique — PHASES 7 & 8.

- ``/workflows`` : liste + création (POST → redirection vers le détail) ;
- ``/workflows/{id}`` : pas-à-pas, avancement et rejeu d'étape ;
- ``/laboratoire`` : tableau de bord « Mon laboratoire » ;
- ``/journal`` : création de note, édition (nouvelle version), historique ;
- ``POST /laboratoire/publier`` : publication explicite avec confirmation.

Le contrat des workflows est porté par ``webintel.science.workflows``
(équipe parallèle) : les tests qui l'exercent sont ignorés
(``importorskip``) tant que ce module n'est pas livré — même motif que
``tests/test_science_ui.py``. Le backend du laboratoire
(``webintel.science.lab``) est livré : ses tests tournent réellement.

Le fixture ``client`` de ``tests/conftest.py`` est réutilisé tel quel :
en mode serveur il passe par ``https://testserver`` (cookie de session
``Secure``) et se connecte via ``/login``.
"""

import re

import pytest

from webintel.science.models import Compound, Plant, PlantCompound


def _backend_workflows():
    """Module workflows (équipe parallèle) — skip si pas encore livré."""
    return pytest.importorskip("webintel.science.workflows")


def _id_depuis_reponse(resp):
    """Extrait l'id d'item depuis l'en-tête Location ou l'URL finale."""
    cible = resp.headers.get("location") or str(resp.url)
    m = re.search(r"/(\d+)(?:[/?#]|$)", cible)
    assert m, f"aucun id trouvé (location={cible!r}, url={resp.url!r})"
    return m.group(1)


def _creer_note(client, titre="Note de test", contenu="Contenu initial",
                auteur="Dr Test"):
    resp = client.post(
        "/journal",
        data={"titre": titre, "contenu": contenu, "auteur": auteur},
    )
    assert resp.status_code in (200, 303), (
        f"création de note impossible : {resp.status_code}"
    )
    return _id_depuis_reponse(resp)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def test_nav_workflows_laboratoire_sans_doublons(client):
    """« Workflows » et « Laboratoire » figurent une seule fois dans la nav,
    sans casser les liens existants."""
    html = client.get("/").text
    assert html.count('href="/workflows"') == 1
    assert html.count('href="/laboratoire"') == 1
    # non-régression : les liens scientifiques existants restent uniques
    assert html.count('href="/plantes"') == 1
    assert html.count('href="/simulations"') == 1


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# PHASE 7 — Workflows (backend `webintel.science.workflows`, équipe parallèle)
# ---------------------------------------------------------------------------
# Contrat réel : creer_workflow(db, *, project_id, type_workflow, nom,
# inputs, cree_par=None) avec inputs = {"plant_id": int, "compound_id":
# int, "engine_name"?: str} ; avancer_workflow(db, run_id) ;
# rejouer_workflow(db, run_id) ; 7 étapes ; rapport final à 6 sections
# (INPUTS, METHOD, PARAMETERS, OUTPUTS, REFERENCES, LIMITATIONS).

@pytest.fixture()
def plante_molecule_db(db_session):
    """Plante + composé lié par une ligne PlantCompound (constituant)."""
    plante = Plant(
        scientific_name="Artemisia annua",
        family="Asteraceae",
        genus="Artemisia",
        species="annua",
    )
    db_session.add(plante)
    db_session.flush()
    compose = Compound(
        name="Artémisinine",
        formula="C15H22O5",
        chemical_class="Lactone sesquiterpénique",
    )
    db_session.add(compose)
    db_session.flush()
    db_session.add(PlantCompound(
        plant_id=plante.id,
        compound_id=compose.id,
        plant_part="feuilles",
        concentration=0.5,
        concentration_unit="%",
        confidence="rapporte",
    ))
    db_session.commit()
    return plante, compose


def _creer_workflow(client, plante_id, compose_id, nom="Workflow test",
                    moteur=""):
    donnees = {"nom": nom, "type": "analyse_plante",
               "plant_id": str(plante_id), "compound_id": str(compose_id)}
    if moteur:
        donnees["engine_name"] = moteur
    resp = client.post("/workflows", data=donnees)
    assert resp.status_code in (200, 303), (
        f"création de workflow impossible : {resp.status_code}"
    )
    return _id_depuis_reponse(resp)


def test_workflows_liste_ok(client):
    _backend_workflows()
    resp = client.get("/workflows")
    assert resp.status_code == 200
    html = resp.text
    assert "Workflows" in html
    # formulaire de création : type, nom, plante, composé, moteur
    for champ in ('name="nom"', 'name="plant_id"', 'name="compound_id"',
                  'name="engine_name"', "analyse_plante"):
        assert champ in html, f"champ manquant : {champ}"


def test_workflow_creation_detail(client, plante_molecule_db):
    _backend_workflows()
    plante, compose = plante_molecule_db
    run_id = _creer_workflow(client, plante.id, compose.id,
                             nom="Analyse Artemisia test")

    detail = client.get(f"/workflows/{run_id}")
    assert detail.status_code == 200
    html = detail.text
    assert "Analyse Artemisia test" in html
    assert "Pas-à-pas (7 étapes)" in html
    assert "Sélection de la plante à analyser" in html
    assert "Simulation in silico" in html
    assert "En attente" in html
    assert "Avancer" in html
    # l'étape de simulation porte l'avertissement, jamais présentée comme preuve
    assert "confirmer expérimentalement" in html


def test_workflow_creation_sans_nom_refusee(client, plante_molecule_db):
    _backend_workflows()
    plante, compose = plante_molecule_db
    resp = client.post("/workflows", data={
        "nom": "", "type": "analyse_plante",
        "plant_id": str(plante.id), "compound_id": str(compose.id)})
    assert resp.status_code == 200  # ré-affichage du formulaire, pas de 500
    assert "Donnez un nom" in resp.text


def test_workflow_creation_ids_invalides(client):
    _backend_workflows()
    resp = client.post("/workflows", data={
        "nom": "X", "type": "analyse_plante",
        "plant_id": "abc", "compound_id": "2"})
    assert resp.status_code == 200
    assert "nombre entier" in resp.text


def test_workflow_avancer_une_etape(client, plante_molecule_db):
    _backend_workflows()
    plante, compose = plante_molecule_db
    run_id = _creer_workflow(client, plante.id, compose.id)

    avance = client.post(f"/workflows/{run_id}/avancer")
    assert avance.status_code in (200, 303)

    detail = client.get(f"/workflows/{run_id}")
    assert detail.status_code == 200
    html = detail.text
    assert "Terminée" in html  # étape 1 (plante) exécutée
    assert "Étape 2 / 7" in html


def test_workflow_complet_rapport_final(client, plante_molecule_db):
    """Bout-en-bout : 7 avancements → run terminé → rapport final à
    6 sections affiché proprement."""
    _backend_workflows()
    plante, compose = plante_molecule_db
    run_id = _creer_workflow(client, plante.id, compose.id,
                             moteur="property_prediction",
                             nom="Workflow complet")

    for _ in range(7):
        resp = client.post(f"/workflows/{run_id}/avancer")
        assert resp.status_code in (200, 303)

    detail = client.get(f"/workflows/{run_id}")
    assert detail.status_code == 200
    html = detail.text
    assert "Terminé" in html
    assert "Rapport final" in html
    for section in ("INPUTS", "METHOD", "PARAMETERS", "OUTPUTS",
                    "REFERENCES", "LIMITATIONS"):
        assert section in html, f"section manquante : {section}"
    # le bouton d'avancement a disparu, le rejeu reste proposé
    assert "Avancer" not in html
    assert "Rejouer" in html


def test_workflow_echec_etape_message_francais(client):
    """Plante inexistante : l'étape échoue avec un message français
    (pas de 500), l'état d'échec est persisté et affiché."""
    _backend_workflows()
    run_id = _creer_workflow(client, 999999, 999999, nom="Workflow en échec")

    avance = client.post(f"/workflows/{run_id}/avancer")
    # ré-affichage du détail avec l'erreur, pas d'explosion
    assert avance.status_code == 200
    html = avance.text
    assert "Échouée" in html
    assert "Plante introuvable" in html
    # le rejeu / la correction restent proposés
    assert "Réessayer" in html
    assert "Rejouer" in html


def test_workflow_rejouer(client, plante_molecule_db):
    _backend_workflows()
    plante, compose = plante_molecule_db
    run_id = _creer_workflow(client, plante.id, compose.id)

    assert client.post(f"/workflows/{run_id}/avancer").status_code in (200, 303)
    assert "Terminée" in client.get(f"/workflows/{run_id}").text

    rejoue = client.post(f"/workflows/{run_id}/rejouer")
    assert rejoue.status_code in (200, 303)

    detail = client.get(f"/workflows/{run_id}")
    assert detail.status_code == 200
    html = detail.text
    assert "Terminée" not in html  # étapes réinitialisées
    assert "En attente" in html
    assert "En cours" in html  # statut du run


def test_workflow_detail_404(client):
    _backend_workflows()
    resp = client.get("/workflows/999999")
    assert resp.status_code == 404
    assert "introuvable" in resp.text.lower()
# PHASE 8 — Laboratoire : tableau de bord
# ---------------------------------------------------------------------------

def test_laboratoire_ok(client):
    note_id = _creer_note(client)
    resp = client.get("/laboratoire")
    assert resp.status_code == 200
    html = resp.text
    assert "Mon laboratoire" in html
    for kpi in ("Observations", "Simulations", "Notes de laboratoire",
                "Plantes étudiées", "Composés étudiés"):
        assert kpi in html, f"KPI manquant : {kpi}"
    # la note créée apparaît dans les derniers ajouts, avec son badge
    assert f"/journal/{note_id}" in html
    assert "badge-experimental" in html
    # liens vers le journal et les workflows
    assert 'href="/journal"' in html
    assert 'href="/workflows"' in html


def test_laboratoire_compte_workflows_reel(client, plante_molecule_db):
    """Le KPI « Workflows » du laboratoire reflète les runs réels.

    Le backend lab date d'avant la livraison du module workflows par
    l'équipe parallèle (``comptes["workflows"]`` vaut ``None``) : la route
    UI complète défensivement via ``list_workflows(db, project_id)`` au lieu
    d'afficher « — ».
    """
    plante, compose = plante_molecule_db
    _creer_workflow(client, plante.id, compose.id)
    resp = client.get("/laboratoire")
    assert resp.status_code == 200
    html = resp.text
    assert '<div class="label">Workflows</div><div class="value">1</div>' in html


# ---------------------------------------------------------------------------
# PHASE 8 — Journal de laboratoire
# ---------------------------------------------------------------------------

def test_journal_liste_ok(client):
    _creer_note(client, titre="Note du journal")
    resp = client.get("/journal")
    assert resp.status_code == 200
    html = resp.text
    assert "Journal de laboratoire" in html
    assert "Note du journal" in html
    assert "badge-experimental" in html  # niveau expérimental, jamais publié


def test_journal_creation_sans_contenu_refusee(client):
    resp = client.post("/journal", data={"titre": "", "contenu": "", "auteur": ""})
    assert resp.status_code == 200  # pas de 500
    assert "au moins un titre ou un contenu" in resp.text


def test_note_detail_edition_nouvelle_version(client):
    note_id = _creer_note(client, contenu="Première version")
    url = f"/journal/{note_id}"

    detail = client.get(url)
    assert detail.status_code == 200
    assert "Première version" in detail.text
    assert "L'édition crée une nouvelle version" in detail.text

    edit = client.post(url, data={"titre": "Note de test",
                                  "contenu": "Deuxième version"})
    assert edit.status_code in (200, 303)

    detail2 = client.get(url)
    assert detail2.status_code == 200
    html2 = detail2.text
    assert "Deuxième version" in html2
    assert re.search(r"Version\s*<strong>2</strong>", html2), (
        "la version courante doit être la version 2"
    )

    versions = client.get(f"{url}/versions")
    assert versions.status_code == 200
    html_v = versions.text
    assert "Première version" in html_v  # l'historique est conservé
    assert "v1" in html_v


def test_note_detail_404(client):
    resp = client.get("/journal/999999")
    assert resp.status_code == 404
    assert "introuvable" in resp.text.lower()


def test_note_edition_publiee_refusee(client):
    """Une note publiée est en lecture seule : l'édition est refusée."""
    note_id = _creer_note(client)
    pub = client.post(
        "/laboratoire/publier",
        data={"item_type": "note", "item_id": note_id, "confirmation": "oui"},
    )
    assert pub.status_code in (200, 303)

    edit = client.post(f"/journal/{note_id}",
                       data={"titre": "Note de test", "contenu": "Tentative"})
    assert edit.status_code == 200  # pas de 500
    assert "lecture seule" in edit.text


# ---------------------------------------------------------------------------
# PHASE 8 — Publication explicite
# ---------------------------------------------------------------------------

def test_publication_avec_confirmation(client):
    note_id = _creer_note(client)
    resp = client.post(
        "/laboratoire/publier",
        data={"item_type": "note", "item_id": note_id,
              "confirmation": "oui", "publie_par": "Dr Test"},
    )
    assert resp.status_code in (200, 303)

    detail = client.get(f"/journal/{note_id}")
    assert detail.status_code == 200
    html = detail.text
    assert "Publiée" in html
    assert "publie_le" not in html  # pas de fuite de nom de champ brut
    assert "publication" in html.lower()  # mention de traçabilité
    # le formulaire de publication a disparu (lecture seule)
    assert "Je confirme la publication" not in html


def test_publication_sans_confirmation_refusee(client):
    """Sans la case « Je confirme », rien n'est publié (pas de 500)."""
    note_id = _creer_note(client)
    resp = client.post(
        "/laboratoire/publier",
        data={"item_type": "note", "item_id": note_id},
    )
    assert resp.status_code == 200
    assert "Cochez la case" in resp.text

    detail = client.get(f"/journal/{note_id}")
    assert '<span class="badge ok">Publiée</span>' not in detail.text
    assert "Je confirme la publication" in detail.text  # tjrs proposée


def test_publication_item_inexistant_404(client):
    resp = client.post(
        "/laboratoire/publier",
        data={"item_type": "note", "item_id": "999999", "confirmation": "oui"},
    )
    assert resp.status_code == 404


def test_publication_type_invalide_422(client):
    resp = client.post(
        "/laboratoire/publier",
        data={"item_type": "truc", "item_id": "1", "confirmation": "oui"},
    )
    assert resp.status_code == 422


def test_publication_deja_publiee_409(client):
    note_id = _creer_note(client)
    donnees = {"item_type": "note", "item_id": note_id, "confirmation": "oui"}
    assert client.post("/laboratoire/publier", data=donnees).status_code in (200, 303)
    assert client.post("/laboratoire/publier", data=donnees).status_code == 409


# ---------------------------------------------------------------------------
# Authentification (motif du mode serveur : 303 → /login, cf. ui_auth)
# ---------------------------------------------------------------------------

def test_pages_protegees_en_mode_serveur(server_bootstrap):
    """Sans session : 303 vers /login en mode serveur, 200 en mode local."""
    from fastapi.testclient import TestClient

    import webintel.main

    base_url = "https://testserver" if server_bootstrap is not None else "http://testserver"
    with TestClient(webintel.main.app, base_url=base_url) as anon:
        for chemin in ("/workflows", "/laboratoire", "/journal"):
            resp = anon.get(chemin, follow_redirects=False)
            if server_bootstrap is not None:
                assert resp.status_code == 303, f"{chemin} : {resp.status_code}"
                assert "/login" in resp.headers.get("location", "")
            else:
                assert resp.status_code == 200, f"{chemin} : {resp.status_code}"
