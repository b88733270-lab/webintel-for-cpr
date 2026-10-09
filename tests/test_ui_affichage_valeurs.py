"""Tests des filtres d'affichage UI : dates lisibles et valeurs observées.

Régression visuelle (audit 2026-10-08) : les écrans Entités / Comparaison /
Recherche affichaient des horodatages ISO bruts (« 2026-10-04T03:43:35.481243 »)
et des repr Python de dicts (« {'amount': 44.99, 'currency': 'EUR'} »).
"""

from __future__ import annotations

from datetime import datetime

from webintel.ui.routes import _afficher_valeur, _date_courte, _jinja


def test_date_courte_datetime():
    assert _date_courte(datetime(2026, 10, 4, 3, 43, 35, 481243)) == "04/10/2026 03:43"


def test_date_courte_chaine_iso_microsecondes():
    assert _date_courte("2026-10-04T03:43:35.481243") == "04/10/2026 03:43"


def test_date_courte_chaine_iso_simple():
    assert _date_courte("2026-10-04 03:43:35") == "04/10/2026 03:43"


def test_date_courte_valeurs_vides():
    assert _date_courte(None) == "—"
    assert _date_courte("") == "—"


def test_date_courte_chaine_non_date_inchangee():
    assert _date_courte("jamais vu") == "jamais vu"


def test_afficher_valeur_prix_dict():
    assert _afficher_valeur({"amount": 44.99, "currency": "EUR"}) == "44,99 EUR"


def test_afficher_valeur_prix_dict_sans_devise():
    assert _afficher_valeur({"amount": 10}) == "10,00"


def test_afficher_valeur_prix_dict_montant_invalide():
    assert _afficher_valeur({"amount": "n/a", "currency": "EUR"}) == "n/a EUR"


def test_afficher_valeur_dict_autre():
    assert _afficher_valeur({"a": 1, "b": "x"}) == "a : 1, b : x"


def test_afficher_valeur_liste():
    assert _afficher_valeur([1, "x"]) == "1, x"


def test_afficher_valeur_datetime():
    assert _afficher_valeur(datetime(2026, 10, 4, 3, 43)) == "04/10/2026 03:43"


def test_afficher_valeur_chaine_iso():
    assert _afficher_valeur("2026-10-04T03:43:35.481243") == "04/10/2026 03:43"


def test_afficher_valeur_chaine_simple():
    assert _afficher_valeur("  Exemple  ") == "Exemple"


def test_afficher_valeur_nombre():
    assert _afficher_valeur(42) == "42"


def test_filtres_enregistres_dans_jinja():
    assert _jinja.filters["date_courte"] is _date_courte
    assert _jinja.filters["afficher_valeur"] is _afficher_valeur


def test_rendu_francais_bout_en_bout(client, db_session):
    """Avec de vraies données : prix « 44,99 EUR », dates « JJ/MM/AAAA HH:MM »,
    aucune repr Python ni horodatage ISO brut dans le HTML rendu."""
    import re

    from webintel.database.models import Project
    from webintel.entities.registry import record_source_observations, upsert_entity

    projet = Project(name="Défaut")
    db_session.add(projet)
    db_session.commit()

    entite = upsert_entity(
        db_session, projet.id, "product",
        {"name": "Casque audio X100 (démonstration)"},
        "https://boutique-exemple.bf/casque-x100",
    )
    record_source_observations(
        db_session, entite, "product",
        {"price": {"amount": 44.99, "currency": "EUR"}},
        "https://boutique-exemple.bf/casque-x100",
    )
    db_session.commit()

    for chemin in ("/entites", "/recherche?q=casque", "/sources"):
        html = client.get(chemin).text
        assert client.get(chemin).status_code == 200, chemin
        assert "{'amount'" not in html, chemin
        assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+", html), chemin
    html = client.get("/entites").text
    assert "44,99 EUR" in html
    assert re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", html)

    html = client.get(f"/comparaison?entite_a={entite.id}&entite_b={entite.id}").text
    assert "44,99 EUR" in html
    assert "{'amount'" not in html


def test_ecrans_touches_repondent():
    from fastapi.testclient import TestClient

    from webintel.main import create_app

    client = TestClient(create_app())
    for chemin in ("/entites", "/comparaison", "/recherche"):
        r = client.get(chemin)
        assert r.status_code == 200, chemin


def test_gabarits_utilisent_les_filtres():
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent / "webintel" / "ui" / "templates"
    comparaison = (base / "comparaison.html").read_text(encoding="utf-8")
    assert "{{ valeur|afficher_valeur }}" in comparaison
    assert "{{ o.valeur|afficher_valeur }}" in comparaison
    assert "{{ carte.premiere_vue|date_courte }}" in comparaison
    assert "{{ carte.derniere_vue|date_courte }}" in comparaison
    assert "{{ o.observe_le|date_courte }}" in comparaison
    for nom in ("entites.html", "recherche.html"):
        texte = (base / nom).read_text(encoding="utf-8")
        assert "{{ e.last_seen|date_courte }}" in texte, nom
    detail = (base / "entite_detail.html").read_text(encoding="utf-8")
    assert "{{ entity.last_seen|date_courte }}" in detail
    for nom, motif in (
        ("sources.html", "{{ s.derniere_observation|date_courte }}"),
        ("evenements.html", "{{ ev.created_at|date_courte }}"),
        ("alertes.html", "{{ a.created_at|date_courte }}"),
        ("crawls.html", "{{ j.created_at|date_courte }}"),
        ("moniteurs.html", '{{ m.last_run_at|date_courte if m.last_run_at else "jamais" }}'),
    ):
        assert motif in (base / nom).read_text(encoding="utf-8"), nom
    # Le libellé anglais résiduel est francisé.
    assert "<h2>Exportation</h2>" in (base / "fiche.html").read_text(encoding="utf-8")
    # La page des connecteurs (oubliée par l'audit v3.0.4) formate aussi
    # son « Dernier import » (chaîne ISO venue de l'API interne).
    assert "{{ c.dernier_import|date_courte }}" in (
        base / "connecteurs.html"
    ).read_text(encoding="utf-8")
    # Les prix s'affichent à la française (« 44,99 EUR »), sans repr Python.
    for nom in ("entites.html", "recherche.html"):
        texte = (base / nom).read_text(encoding="utf-8")
        assert "{{ e.prices[0]|afficher_valeur }}" in texte, nom
    fiche = (base / "fiche.html").read_text(encoding="utf-8")
    assert '{{ {"amount": p.amount}|afficher_valeur }}' in fiche
    # Balayage : plus aucun horodatage brut dans les écrans couverts.
    for nom, motifs in (
        ("collection_detail.html", ("{{ collection.created_at|date_courte }}",
                                    "{{ collection.updated_at|date_courte }}",
                                    "{{ f.created_at|date_courte }}")),
        ("collections.html", ("Créée le {{ c.created_at|date_courte }}",
                               "{{ f.created_at|date_courte }}")),
        ("entite_detail.html", ("{{ entity.first_seen|date_courte }}",
                                "{{ o.observed_at|date_courte }}")),
        ("etude_historique.html", ("{{ v.cree_le|date_courte }}",)),
        ("simulations.html", ("{{ rapport.genere_le|date_courte }}",)),
        ("synthese_ia.html", ("{{ synthese.genere_le|date_courte }}",)),
    ):
        texte = (base / nom).read_text(encoding="utf-8")
        for motif in motifs:
            assert motif in texte, (nom, motif)


def test_pas_de_libelle_export_anglais_residuel():
    """Balayage de tous les gabarits : le mot anglais résiduel « Export »
    (francisé en « Exportation » en v3.0.4) n'apparaît plus nulle part —
    régression du libellé oublié dans synchronisation.html (v3.0.6)."""
    import re
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent / "webintel" / "ui" / "templates"
    oublis = []
    for gabarit in sorted(base.glob("*.html")):
        texte = gabarit.read_text(encoding="utf-8")
        if re.search(r"\bExport\b", texte):
            oublis.append(gabarit.name)
    assert oublis == [], oublis
    # Le libellé corrigé est bien en place sur l'écran concerné.
    assert "Exportation complète du projet" in (
        base / "synchronisation.html"
    ).read_text(encoding="utf-8")


def test_rendu_connecteurs_dernier_import_lisible():
    """Rendu direct du gabarit : l'ISO brut de l'API interne devient
    « JJ/MM/AAAA HH:MM » sur la page des connecteurs."""
    import re
    from types import SimpleNamespace

    connecteurs = [
        {
            "nom": "pubmed",
            "statut": "actif",
            "version_connecteur": "1.0",
            "description": "Connecteur PubMed",
            "url": "https://pubmed.ncbi.nlm.nih.gov",
            "licence": "NCBI",
            "licence_url": None,
            "conditions_utilisation": None,
            "dernier_import": "2026-10-08T14:32:11.123456",
            "strategie_maj": "manuelle",
        }
    ]
    html = _jinja.get_template("connecteurs.html").render(
        active="connecteurs",
        project=SimpleNamespace(name="Défaut"),
        connecteurs=connecteurs,
        erreur=None,
        message=None,
    )
    assert "Dernier import" in html
    assert "08/10/2026 14:32" in html
    assert "2026-10-08T14:32:11" not in html
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", html)


def test_pas_de_strftime_inline_dans_les_gabarits():
    """Balayage de tous les gabarits : les 6 appels inline `.strftime(...)`
    restants (plante_etude, mes_recherches, synchronisation, collecte_detail)
    ont été convertis vers le filtre `date_courte` du Design System
    (v3.0.7) — le filtre accepte datetime OU chaîne ISO, le `.strftime`
    inline plantait sur une chaîne."""
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent / "webintel" / "ui" / "templates"
    oublis = [
        gabarit.name
        for gabarit in sorted(base.glob("*.html"))
        if ".strftime(" in gabarit.read_text(encoding="utf-8")
    ]
    assert oublis == [], oublis
    # Les 4 écrans convertis utilisent bien le filtre, avec leurs libellés.
    assert '{{ e.created_at|date_courte if e.created_at else "date inconnue" }}' in (
        base / "plante_etude.html"
    ).read_text(encoding="utf-8")
    assert '{{ s.run_at|date_courte if s.run_at else "date inconnue" }}' in (
        base / "plante_etude.html"
    ).read_text(encoding="utf-8")
    assert "{{ d.date_creation|date_courte }}" in (
        base / "mes_recherches.html"
    ).read_text(encoding="utf-8")
    assert '{{ d.derniere_analyse.date|date_courte if d.derniere_analyse.date else "" }}' in (
        base / "mes_recherches.html"
    ).read_text(encoding="utf-8")
    assert "{{ l.created_at|date_courte }}" in (
        base / "synchronisation.html"
    ).read_text(encoding="utf-8")
    assert "{{ collecte.created_at|date_courte }}" in (
        base / "collecte_detail.html"
    ).read_text(encoding="utf-8")
