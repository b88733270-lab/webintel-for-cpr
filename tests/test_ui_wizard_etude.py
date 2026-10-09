"""Tests v2.2 — assistant de création d'étude (wizard 6 étapes).

Couvre : parcours complet 1→6 via TestClient, cases à cocher étape 2,
lancement d'analyses de similarité/propriétés sur des molécules de test,
page de résultats (mentions prudentes, badges de preuve, explications),
mode simple sans JSON, mode avancé avec paramètres.

Le routeur ``chercheur_etudes_routes`` est enregistré dans
``webintel/main.py`` (``create_app``) ; le montage ci-dessous n'est qu'un
filet de sécurité (garde anti-doublon + suivi d'un éventuel
``importlib.reload(webintel.main)`` par un autre module de la suite).
"""

from __future__ import annotations

import re

import pytest

import webintel.main
from webintel.ui import chercheur_etudes_routes
from webintel.ui.explications import EXPLICATIONS, explication_pour

def _router_deja_monte(app, module: str) -> bool:
    """Détecte un routeur déjà inclus, y compris via `_IncludedRouter`
    (FastAPI récent n'aplatit plus les routes incluses : le test sur
    `path` ne voit jamais les routes d'un routeur inclus)."""
    for entree in app.router.routes:
        if getattr(getattr(entree, "endpoint", None), "__module__", None) == module:
            return True
        inner = getattr(entree, "original_router", None)
        for route in getattr(inner, "routes", None) or ():
            if getattr(getattr(route, "endpoint", None), "__module__", None) == module:
                return True
    return False


def _assurer_montage_wizard() -> None:
    """Monte le routeur sur l'app COURANTE (import local : `webintel.main.app`
    est résolu à chaque appel, donc un `importlib.reload(webintel.main)`
    effectué par un autre module de la suite — qui remplace l'objet `app` —
    n'enlève plus les routes : `create_app()` les ré-enregistre et, si
    l'app a été remplacée, le montage est refait ici)."""
    import webintel.main  # noqa: PLC0415

    app = webintel.main.app
    if not _router_deja_monte(app, chercheur_etudes_routes.__name__):
        app.include_router(chercheur_etudes_routes.router)


_assurer_montage_wizard()


@pytest.fixture(autouse=True)
def _montage_wizard_etude():
    _assurer_montage_wizard()
    yield


CAFEINE = "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"
THEOPHYLLINE = "CN1C=NC2=C1C(=O)NC(=O)N2C"


def _compose_api(client, nom: str, smiles: str | None = None) -> int:
    payload = {"name": nom}
    if smiles:
        payload["smiles"] = smiles
    r = client.post("/api/science/compounds", json=payload)
    assert r.status_code in (200, 201), r.text  # idempotent sur le nom
    return r.json()["id"]


def _session_id(location: str) -> str:
    m = re.search(r"session=([0-9a-f]{32})", location or "")
    assert m, f"pas de session dans la redirection : {location!r}"
    return m.group(1)


def _parcours_jusqu_etape3(client, analyses, mode="simple",
                           donnees_exp: str | None = None):
    """Joue les étapes 1→3 ; retourne (session_id, (id_compose_a, id_compose_b))."""
    a = _compose_api(client, "Caféine", CAFEINE)
    b = _compose_api(client, "Théophylline", THEOPHYLLINE)

    r = client.get("/etudes/nouvelle")
    assert r.status_code == 200
    assert "Que voulez-vous étudier ?" in r.text

    r = client.post("/etudes/etape1", data={"type_objet": "molecules"},
                    follow_redirects=False)
    assert r.status_code == 303
    sid = _session_id(r.headers["location"])

    r = client.get(f"/etudes/etape2?session={sid}")
    assert r.status_code == 200
    assert "Choisissez vos éléments" in r.text
    assert 'type="checkbox"' in r.text
    assert "Caféine" in r.text and "Théophylline" in r.text

    r = client.post(f"/etudes/etape2?session={sid}",
                    data={"action": "continuer",
                          "compound_ids": [str(a), str(b)]},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    assert f"/etudes/etape3?session={sid}" in r.headers["location"]

    r = client.get(f"/etudes/etape3?session={sid}")
    assert r.status_code == 200
    assert "Quel type d'analyse ?" in r.text

    donnees = {"analyses": analyses, "mode": mode}
    if donnees_exp is not None:
        donnees["donnees_exp"] = donnees_exp
    r = client.post(f"/etudes/etape3?session={sid}", data=donnees,
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    assert f"/etudes/etape4?session={sid}" in r.headers["location"]
    return sid, (a, b)


def _lancer_et_resultats(client, sid: str):
    r = client.get(f"/etudes/etape4?session={sid}")
    assert r.status_code == 200
    assert "Vérification" in r.text

    r = client.post(f"/etudes/lancer?session={sid}", follow_redirects=False)
    assert r.status_code == 200
    assert "Analyses en cours" in r.text

    r = client.get(f"/etudes/etape6?session={sid}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/etudes/{sid}/resultats"

    r = client.get(f"/etudes/{sid}/resultats")
    assert r.status_code == 200
    return r


# ---------------------------------------------------------------------------
# Parcours complet — mode simple
# ---------------------------------------------------------------------------

def test_wizard_complet_mode_simple(client):
    sid, _ids = _parcours_jusqu_etape3(
        client, ["structures", "proprietes", "cibles", "docking"])
    r = _lancer_et_resultats(client, sid)

    # Titre, formulations prudentes, niveaux de preuve, incertitude.
    assert "Analyse de combinaison" in r.text
    assert "Résultat informatique" in r.text
    assert "PRÉDICTION" in r.text
    assert ("Cette analyse est informatique et ne constitue pas une preuve "
            "d'effet clinique.") in r.text  # « efficace » banni (règle absolue)
    assert "Incertitude" in r.text
    assert "Limites" in r.text
    # Barre de progression 1─2─3─4─5─6.
    for numero in ("1", "2", "3", "4", "5", "6"):
        assert numero in r.text
    # Explications « Qu'est-ce que cela signifie ? » + provenance.
    assert "Qu'est-ce que cela signifie ?" in r.text
    assert "Voir la provenance" in r.text
    assert "Niveau de preuve" in r.text
    # Résultats réels des moteurs (pas de valeurs inventées).
    assert "Tanimoto" in r.text
    assert "Caféine" in r.text and "Théophylline" in r.text
    # Cibles : aucune en base → bloc honnête « non lancé ».
    assert "Aucune cible documentée en base" in r.text
    # Docking : jamais exécuté dans l'assistant (apostrophe échappée par Jinja).
    assert "exécuté dans l" in r.text


def test_resultats_mentions_prudentes_et_badges(client, db_session):
    from webintel.science.models import CompoundTarget

    sid, (a, _b) = _parcours_jusqu_etape3(client, ["proprietes"])
    db_session.add(CompoundTarget(
        compound_id=a, target_name="Adénosine A1",
        target_type="récepteur", interaction_type="antagonisme",
        source="ChEMBL (test)", data_level="publie"))
    db_session.commit()

    # Relance un parcours avec cibles + interactions.
    sid2, _ids2 = _parcours_jusqu_etape3(client, ["cibles", "interactions"])
    r = _lancer_et_resultats(client, sid2)

    assert "DONNÉE PUBLIÉE" in r.text
    assert "Adénosine A1" in r.text
    assert "Association rapportée dans la littérature" in r.text


def test_mode_simple_sans_json(client):
    # En mode simple : aucun champ structuré (textarea/JSON) — la molécule
    # s'ajoute par de simples champs texte, les paramètres sont par défaut.
    sid, _ids = _parcours_jusqu_etape3(client, ["proprietes"])
    for url in (f"/etudes/etape2?session={sid}",
                f"/etudes/etape3?session={sid}",
                f"/etudes/etape4?session={sid}"):
        r = client.get(url)
        assert r.status_code == 200
        assert "<textarea" not in r.text, url
    r = _lancer_et_resultats(client, sid)
    assert "Mode simple" in r.text


# ---------------------------------------------------------------------------
# Mode avancé
# ---------------------------------------------------------------------------

def test_wizard_mode_avance_parametres(client):
    a = _compose_api(client, "Caféine", CAFEINE)
    b = _compose_api(client, "Théophylline", THEOPHYLLINE)

    r = client.get("/etudes/nouvelle")
    assert r.status_code == 200
    r = client.post("/etudes/etape1", data={"type_objet": "molecules"},
                    follow_redirects=False)
    sid = _session_id(r.headers["location"])
    r = client.post(f"/etudes/etape2?session={sid}",
                    data={"action": "continuer",
                          "compound_ids": [str(a), str(b)]},
                    follow_redirects=False)
    assert r.status_code == 303

    r = client.get(f"/etudes/etape3?session={sid}&mode=avance")
    assert r.status_code == 200
    assert "Paramètres avancés" in r.text
    assert "Rayon de Morgan" in r.text
    assert "Seed Vina" in r.text

    r = client.post(
        f"/etudes/etape3?session={sid}",
        data={"analyses": ["structures"], "mode": "avance",
              "methode_similarite": "structure",
              "rayon_morgan": "3", "taille_empreinte": "1024",
              "seuil_tanimoto": "0,5",
              "seed_vina": "123", "exhaustiveness": "16"},
        follow_redirects=False)
    assert r.status_code == 303

    r = client.get(f"/etudes/etape4?session={sid}")
    assert r.status_code == 200
    assert "Paramètres avancés" in r.text
    assert "rayon" in r.text.lower()

    r = _lancer_et_resultats(client, sid)
    # Variante paramétrée calculée avec rayon=3 / taille=1024.
    assert "Variante avancée" in r.text
    assert "rayon=3" in r.text


def test_donnees_experimentales_sans_donnees_honnete(client):
    sid, _ids = _parcours_jusqu_etape3(client, ["experimentales"], mode="avance",
                                     donnees_exp="")
    r = _lancer_et_resultats(client, sid)
    assert "RÉSULTAT EXPÉRIMENTAL" in r.text
    assert "ne calcule jamais un score de synergie sans mesures réelles" in r.text


def test_donnees_experimentales_bliss_calcule(client):
    donnees = (
        "10,10,0.30,0.25,0.55\n"
        "20,10,0.45,0.25,0.62\n"
        "10,20,0.30,0.40,0.60\n"
    )
    sid, _ids = _parcours_jusqu_etape3(client, ["experimentales"], mode="avance",
                                     donnees_exp=donnees)
    r = _lancer_et_resultats(client, sid)
    assert "bliss" in r.text.lower()
    assert "3 point(s) de mesure" in r.text


# ---------------------------------------------------------------------------
# Robustesse / garde-fous
# ---------------------------------------------------------------------------

def test_etape1_sans_choix_erreur(client):
    r = client.post("/etudes/etape1", data={}, follow_redirects=False)
    assert r.status_code == 200
    assert "Choisissez ce que vous voulez étudier" in r.text


def test_etape2_sans_molecule_erreur(client):
    r = client.get("/etudes/nouvelle")
    assert r.status_code == 200
    r = client.post("/etudes/etape1", data={"type_objet": "molecule"},
                    follow_redirects=False)
    sid = _session_id(r.headers["location"])
    r = client.post(f"/etudes/etape2?session={sid}",
                    data={"action": "continuer"}, follow_redirects=False)
    assert r.status_code == 200
    assert "au moins une molécule" in r.text


def test_session_inconnue_404(client):
    r = client.get("/etudes/etape2?session=abcdef0123456789abcdef0123456789")
    assert r.status_code == 404
    r = client.get("/etudes/xyz/resultats")
    assert r.status_code == 404


def test_ajout_molecule_saisie_manuelle(client):
    r = client.get("/etudes/nouvelle")
    r = client.post("/etudes/etape1", data={"type_objet": "molecules"},
                    follow_redirects=False)
    sid = _session_id(r.headers["location"])
    r = client.post(f"/etudes/etape2?session={sid}",
                    data={"action": "ajouter", "nom_mol": "Quercétine",
                          "smiles_mol": "C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O)O)O"},
                    follow_redirects=False)
    assert r.status_code == 303
    r = client.get(f"/etudes/etape2?session={sid}")
    assert "Quercétine" in r.text
    # Continuer sans cocher de case : la molécule saisie suffit.
    r = client.post(f"/etudes/etape2?session={sid}",
                    data={"action": "continuer"}, follow_redirects=False)
    assert r.status_code == 303
    assert "/etudes/etape3" in r.headers["location"]


# ---------------------------------------------------------------------------
# Dictionnaire d'explications
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cle", [
    "tanimoto", "logp", "tpsa", "hbd", "hba", "masse_molaire", "lipinski",
    "docking_score", "bliss", "loewe", "hsa", "zip",
    "fraction_csp3", "liaisons_rotatives",
])
def test_explications_prudentes(cle):
    exp = explication_pour(cle)
    assert exp is not None
    assert exp["titre"] and exp["explication"]
    texte = (exp["titre"] + exp["explication"] + (exp.get("echelle") or "")).lower()
    # Aucune promesse : ni « efficace », ni « sûr », ni « traite ».
    assert "efficac" not in texte, cle
    assert "innocu" not in texte, cle
    assert "traite " not in texte and "traitement" not in texte, cle


def test_explication_tanimoto_exemple():
    exp = explication_pour("tanimoto", valeur="0,82")
    assert exp is not None
    assert "0,82" in (exp["exemple"] or "")
    assert "ne signifie pas" in (exp["exemple"] or "").lower()


def test_explication_inconnue_none():
    assert explication_pour("metrique_inexistante") is None
    assert set(EXPLICATIONS) >= {
        "tanimoto", "logp", "tpsa", "hbd", "hba", "masse_molaire",
        "lipinski", "docking_score", "bliss", "loewe", "hsa", "zip",
        "fraction_csp3", "liaisons_rotatives",
    }
