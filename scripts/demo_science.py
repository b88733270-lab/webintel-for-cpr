#!/usr/bin/env python3
"""Scénario de démonstration scientifique WebIntel — 100 % hors-ligne.

Exécution : `./scripts/demo.sh` (ou `python3 scripts/demo_science.py`)
depuis la racine du projet. Idempotent : le répertoire de travail
`/tmp/webintel-demo` est reconstruit à chaque lancement.

Étapes :
  (1) import des 4 fichiers démo (`demo/`) : plantes, composés, liens,
      preuves ;
  (2) consultation de la fiche scientifique de *Moringa oleifera* ;
  (3) exécution d'une simulation (moteur `statistics`, déterministe) ;
  (4) export du dossier de recherche (ZIP, données privées exclues) ;
  (5) génération de la fiche plante en vrai PDF (reportlab) ;
  (6) export du rapport de simulation en Markdown.

Sortie : récapitulatif avec ✅ par étape et « DÉMO RÉUSSIE » à la fin,
plus la liste des fichiers produits dans `/tmp/webintel-demo`.
En cas d'échec : message explicite + exit 1.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import sys
import traceback

# --- Environnement : AVANT tout import webintel ---------------------------
_DEMO_DIR = "/tmp/webintel-demo"
if os.path.isdir(_DEMO_DIR):
    shutil.rmtree(_DEMO_DIR, ignore_errors=True)
os.makedirs(_DEMO_DIR, exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_DEMO_DIR}/demo.db"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["REQUIRE_API_KEY"] = "false"
os.environ["EXPORT_DIR"] = os.path.join(_DEMO_DIR, "exports")
os.environ["LOG_LEVEL"] = "WARNING"

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
_DEMO_DATA = os.path.join(_PROJECT_ROOT, "demo")


def _fake_getaddrinfo(host, port, *args, **kwargs):
    """DNS fictif : noms d'hôtes -> IP publique fictive, IP littérales inchangées."""
    try:
        ipaddress.ip_address(str(host))
        family = socket.AF_INET6 if ":" in str(host) else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (str(host), port or 0))]
    except ValueError:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0))]


socket.getaddrinfo = _fake_getaddrinfo  # neutralise le réseau pour toute la démo


def _lire(nom: str) -> bytes:
    with open(os.path.join(_DEMO_DATA, nom), "rb") as fh:
        return fh.read()


def _demo_v21(session, moringa, projet) -> list[str]:
    """Étape (2b) de la démo — v2.1 : modèle enrichi + fiche détaillée.

    Moringa oleifera → 3 molécules réelles et valides (Quercétine,
    Kaempférol, Acide chlorogénique — structures vérifiées par RDKit depuis
    le CSV démo, jamais inventées) → similarité → propriétés → cibles →
    combinaison → simulation → rapport.

    Règle d'honnêteté de la démo : AUCUNE publication, DOI, valeur ou
    résultat inventé. Les liens créés par le script ne portent ni
    publication ni DOI fictifs (données d'illustration explicitement
    étiquetées) ; l'activité rapportée reprend le verbatim sourcé de la
    preuve importée du corpus démo (Sultana et al. 2009) ; les sections sans
    donnée (cibles) restent vides au lieu d'être remplies de faux.

    Étapes absentes : proprement ignorées avec un message explicite —
    jamais de faux résultat inventé.
    """
    from webintel.science import fiche as fiche_module
    from webintel.science import service as science_service
    from webintel.science.export_dossier import exporter_fiche
    from webintel.science.models import Compound, Evidence

    import importlib

    etapes: list[str] = []
    etape_ignoree = lambda msg: etapes.append(f"étape ignorée (non disponible) : {msg}")

    # --- Botanique détaillée (source : flore de référence) ----------------
    science_service.set_plant_fields(
        session,
        moringa.id,
        feuilles="feuilles composées pennées, à folioles ovales",
        fleurs="fleurs blanches en panicules, parfumées",
        fruits="gousses longues, pendantes",
        graines="graines ailées",
        habitat="zones arides et semi-arides d'Afrique et d'Asie",
        references_botaniques=["Flora of Tropical East Africa"],
    )
    session.commit()
    etapes.append("botanique détaillée (feuilles/fleurs/fruits/graines/habitat)")

    # --- Moringa → 3 molécules RÉELLES (structures du CSV, validées RDKit) --
    # Le lien Moringa→Quercétine vient déjà du CSV (publication citée) ; les
    # deux autres sont créés ici SANS publication ni DOI inventés : données
    # d'illustration explicitement étiquetées, jamais présentées comme
    # publiées.
    trio_moringa = ["Quercétine", "Kaempférol", "Acide chlorogénique"]
    for nom in trio_moringa:
        compose = session.query(Compound).filter_by(name=nom).one()
        assert compose.smiles, f"{nom} : SMILES manquant (CSV démo)"
        assert compose.formula, f"{nom} : formule manquante (CSV démo)"
        assert compose.inchikey, f"{nom} : InChIKey manquant (CSV démo)"
    for nom in ["Kaempférol", "Acide chlorogénique"]:
        compose = session.query(Compound).filter_by(name=nom).one()
        science_service.link_plant_compound(
            session, moringa.id, compose.id,
            plant_part="feuilles",
            confidence="normalise",
        )
        science_service.record_provenance(
            session, "plant_compound", compose.id,
            source_name=(
                "Script de démonstration WebIntel "
                "(données d'illustration, non publiées)"
            ),
            connector="demo-script", data_version="v2.1",
        )
    session.commit()
    etapes.append(
        "3 molécules réelles liées à Moringa oleifera "
        "(Quercétine, Kaempférol, Acide chlorogénique — structures "
        "vérifiées par RDKit, aucune molécule inventée)"
    )

    # --- Cibles : aucune donnée d'illustration — section laissée vide -------
    # (on n'invente ni cible, ni interaction, ni publication pour la remplir).
    etape_ignoree(
        "cibles biologiques : aucune donnée dans la démo — "
        "section vide, rien d'inventé"
    )

    # --- Activité rapportée : verbatim SOURCÉ, aucun chiffre inventé --------
    # Reprend l'extrait de la preuve importée du corpus démo
    # (Sultana et al. 2009, DOI 10.3390/molecules14062167) — aucune valeur
    # (IC50 ou autre) n'est ajoutée.
    preuve_antiox = (
        session.query(Evidence)
        .filter_by(doi="10.3390/molecules14062167")
        .one()
    )
    science_service.add_plant_activity(
        session, moringa.id, "antioxydant",
        study_type="in_vitro",
        published_result=preuve_antiox.excerpt,
        reference=preuve_antiox.reference,
        doi=preuve_antiox.doi, evidence_id=preuve_antiox.id,
    )
    session.commit()
    etapes.append(
        "activité rapportée : antioxydant (in vitro) — verbatim sourcé "
        "(Sultana et al. 2009), aucun résultat chiffré inventé"
    )

    # --- Analyse structurale réelle (RDKit) sur le trio Moringa ------------
    descripteurs_ok = {}
    try:
        chimie = importlib.import_module("webintel.science.chimie")
        for nom in trio_moringa:
            compose = session.query(Compound).filter_by(name=nom).one_or_none()
            if compose is not None and compose.smiles:
                desc = chimie.descripteurs(compose.smiles)
                if desc is not None:
                    descripteurs_ok[nom] = desc
                    # Cohérence chimique stricte : la formule et l'InChIKey
                    # déclarés dans le CSV doivent correspondre à ce que
                    # RDKit recalcule depuis le SMILES (jamais de fixture
                    # incohérente dans la démo officielle).
                    assert desc.get("formule") == compose.formula, (
                        f"{nom} : formule RDKit {desc.get('formule')} != "
                        f"formule déclarée {compose.formula}"
                    )
                    assert desc.get("inchi_key") == compose.inchikey, (
                        f"{nom} : InChIKey RDKit {desc.get('inchi_key')} != "
                        f"InChIKey déclaré {compose.inchikey}"
                    )
        assert len(descripteurs_ok) == 3, (
            "les 3 molécules Moringa doivent être validées par RDKit : "
            f"{sorted(descripteurs_ok)}"
        )
        masses = ", ".join(
            f"{n} : {d.get('masse_molaire')} g/mol"
            for n, d in descripteurs_ok.items()
        )
        etapes.append(
            "analyse structurale réelle (RDKit) : "
            f"{len(descripteurs_ok)}/3 descripteurs calculés ({masses})"
        )
    except ImportError:
        etape_ignoree("analyse structurale : module webintel.science.chimie absent")

    # --- Comparaison structurale type « combinaison » (si le chantier existe) --
    try:
        combinaisons = importlib.import_module("webintel.science.combinaisons")
        dispo, raison = combinaisons.chimie_disponible()
        if dispo and descripteurs_ok and len(descripteurs_ok) >= 2:
            noms = list(descripteurs_ok)
            fps = [chimie.empreinte_morgan(
                session.query(Compound).filter_by(name=n).one().smiles
            ) for n in noms]
            sims = [
                chimie.tanimoto_fp(fps[i], fps[j])
                for i in range(len(fps)) for j in range(i + 1, len(fps))
            ]
            sims = [s for s in sims if s is not None]
            resultat = combinaisons.score_informatique(
                [descripteurs_ok[n] for n in noms], sims
            )
            etapes.append(
                "comparaison structurale de démonstration "
                f"(« {', '.join(noms)} ») : score informatique = "
                f"{resultat.get('score')} — indicateur de comparaison, "
                "PAS une mesure d'effet pharmacologique"
            )
        else:
            etape_ignoree(
                "étude de combinaison : "
                + (raison if not dispo else "données insuffisantes (RDKit/SMILES)")
            )
    except ImportError:
        etape_ignoree("moteur de combinaisons « webintel.science.combinaisons » absent")

    # --- Docking moléculaire (moteur externe, si installé) -------------------
    try:
        docking = importlib.import_module("webintel.science.docking")
        dispo, raison = docking.statut_moteur()
        if dispo:
            etapes.append(
                "docking : moteur présent — calcul non rejoué en démo "
                "(opération lourde, moteur protégé)"
            )
        else:
            etape_ignoree(f"docking : {raison}")
    except ImportError:
        etape_ignoree("docking : module webintel.science.docking absent")

    # --- Fiche enrichie vérifiée + export HTML --------------------------------
    fiche = fiche_module.build_plant_fiche(session, moringa.id, project_id=projet.id)
    assert fiche["botanique"]["feuilles"], "feuilles attendues dans la fiche"
    assert len(fiche["phytochimie"]) >= 3, "3 composés attendus"
    assert len(fiche["activites_pharmacologiques"]) >= 1, "activités attendues"
    assert len(fiche["litterature"]) >= 1, "littérature attendue"
    # Anti-régression honnêteté : aucune publication/DOI de démonstration
    # inventé ne doit subsister dans la fiche.
    import json as _json
    fiche_texte = _json.dumps(fiche, ensure_ascii=False, default=str).lower()
    for marqueur in ("demo.moringa", "demo et al", "journal de démonstration"):
        assert marqueur not in fiche_texte, (
            f"marqueur de publication inventée détecté : {marqueur}"
        )
    contenu_html, mime, nom_html = exporter_fiche(
        session, moringa.id, format="html", project_id=projet.id
    )
    assert mime.startswith("text/html"), mime
    for section in (
        "Botanique", "Pharmacognosie", "Phytochimie",
        "Activités pharmacologiques",
        "Littérature", "n'est pas un avis médical",
    ):
        assert section in contenu_html.decode("utf-8"), section
    for marqueur in ("demo.moringa", "Demo et al", "Journal de démonstration"):
        assert marqueur not in contenu_html.decode("utf-8"), marqueur
    chemin_html = os.path.join(_DEMO_DIR, nom_html)
    with open(chemin_html, "wb") as fh:
        fh.write(contenu_html)
    etapes.append(
        "fiche enrichie v2.1 vérifiée (botanique, 3 composés réels, "
        "activité sourcée, littérature — aucune donnée inventée) "
        f"→ export HTML {chemin_html}"
    )
    return etapes


def main() -> int:
    from webintel.database import models as _modeles  # noqa: F401
    from webintel.database.base import SessionLocal, init_db
    from webintel.science import fiche as fiche_module
    from webintel.science import import_export
    from webintel.science.export_dossier import build_dossier_zip, exporter_fiche
    from webintel.science.models import Plant
    from webintel.simulations.reports import (
        build_report_from_simulation,
        render_markdown,
    )
    from webintel.simulations.service import run_simulation

    init_db()
    session = SessionLocal()
    etapes: list[str] = []
    try:
        # --- (0) Projet démo ------------------------------------------------
        projet = _modeles.Project(name="Démo WebIntel — plantes médicinales")
        session.add(projet)
        session.commit()
        etapes.append("projet « Démo WebIntel — plantes médicinales » créé")

        # --- (1) Imports ----------------------------------------------------
        total_crees = 0
        for nom_fichier, type_donnees in [
            ("demo_plantes.csv", "plante"),
            ("demo_composes.csv", "compose"),
            ("demo_liens.csv", "lien"),
            ("demo_evidences.json", "evidence"),
        ]:
            rapport = import_export.importer_fichier(
                session, _lire(nom_fichier), nom_fichier, type_donnees
            )
            session.commit()
            assert not rapport["erreurs"], f"erreurs d'import {nom_fichier}"
            total_crees += rapport["crees"]
            print(
                f"  import {nom_fichier} : {rapport['crees']} créés, "
                f"{rapport['ignores']} ignorés (niveau « {rapport['niveau_donnees']} »)"
            )
        assert total_crees == 3 + 4 + 3 + 2, f"total inattendu : {total_crees}"
        etapes.append(f"import démo : {total_crees} objets créés (0 erreur)")

        # --- (2) Fiche plante ------------------------------------------------
        moringa = (
            session.query(Plant)
            .filter(Plant.scientific_name == "Moringa oleifera")
            .one()
        )
        fiche = fiche_module.build_plant_fiche(
            session, moringa.id, project_id=projet.id
        )
        assert fiche["plante"]["nom_scientifique"] == "Moringa oleifera"
        assert len(fiche["phytochimie"]) >= 1
        assert fiche["avertissement_separation"], "séparation des niveaux requise"
        etapes.append(
            "fiche *Moringa oleifera* : "
            f"{len(fiche['phytochimie'])} composé(s), "
            f"{len(fiche['bibliographie'])} référence(s), niveaux séparés ✅"
        )

        # --- (2b) Enrichissement v2.1 : modèle scientifique enrichi -------------
        etapes.extend(_demo_v21(session, moringa, projet))

        # --- (3) Simulation --------------------------------------------------
        # Valeurs SYNTHÉTIQUES de démonstration (unités arbitraires) : elles
        # servent uniquement à exercer le moteur « statistics » — elles ne
        # sont attribuées à aucune molécule ni à aucune mesure réelle.
        entrees = {
            "observations": [
                {"valeur": 12.5, "unite": "u.a.", "libelle": "Mesure de démonstration 1 (valeur synthétique)"},
                {"valeur": 13.1, "unite": "u.a.", "libelle": "Mesure de démonstration 2 (valeur synthétique)"},
                {"valeur": 11.9, "unite": "u.a.", "libelle": "Mesure de démonstration 3 (valeur synthétique)"},
            ]
        }
        simulation, rapport = run_simulation(
            session,
            engine_name="statistics",
            name="Démo — statistiques sur valeurs synthétiques",
            inputs=entrees,
            parameters={},
            project_id=projet.id,
            description="Démonstration reproductible (Phase 14) — entrées synthétiques.",
            executed_by="demo",
        )
        session.commit()
        moyenne = simulation.result["moyenne"]["valeur"]
        assert abs(moyenne - 12.5) < 1e-9, moyenne
        # Rejouabilité : mêmes entrées → même hash.
        assert simulation.input_hash == (
            __import__("hashlib").sha256(
                __import__("json").dumps(
                    entrees, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
        )
        etapes.append(
            f"simulation « statistics » sur valeurs synthétiques : moyenne = "
            f"{moyenne:.2f} u.a., hash {simulation.input_hash[:12]}… "
            "(rejeu → même hash)"
        )

        # --- (4) Dossier de recherche ZIP ------------------------------------
        dossier = build_dossier_zip(session, projet.id, inclure_prive=False)
        chemin_zip = os.path.join(_DEMO_DIR, "dossier_recherche_demo.zip")
        with open(chemin_zip, "wb") as fh:
            fh.write(dossier)
        etapes.append(f"dossier de recherche : {chemin_zip} ({len(dossier)} octets)")

        # --- (5) Fiche PDF ----------------------------------------------------
        contenu_pdf, mime, nom_pdf = exporter_fiche(
            session, moringa.id, format="pdf"
        )
        assert mime == "application/pdf", mime
        assert contenu_pdf.startswith(b"%PDF"), "pas un vrai PDF"
        chemin_pdf = os.path.join(_DEMO_DIR, nom_pdf)
        with open(chemin_pdf, "wb") as fh:
            fh.write(contenu_pdf)
        etapes.append(f"fiche PDF : {chemin_pdf} ({len(contenu_pdf)} octets)")

        # --- (6) Rapport de simulation Markdown -------------------------------
        rapport_complet = build_report_from_simulation(simulation)
        chemin_md = os.path.join(_DEMO_DIR, "rapport_simulation_demo.md")
        with open(chemin_md, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(rapport_complet))
        etapes.append(f"rapport Markdown : {chemin_md}")
    except Exception:
        print("❌ ÉCHEC DE LA DÉMO :", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        session.close()

    print("\n=== Démo scientifique WebIntel ===")
    for etape in etapes:
        print(f"✅ {etape}")
    print(f"\nFichiers produits dans {_DEMO_DIR} :")
    for nom in sorted(os.listdir(_DEMO_DIR)):
        print(f"  - {nom}")
    print("\nDÉMO RÉUSSIE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
