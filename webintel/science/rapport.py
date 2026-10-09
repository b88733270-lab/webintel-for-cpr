"""Rapport de recherche exportable (v2.1).

Sections : FICHE DE LA PLANTE, COMPOSÉS, PUBLICATIONS, DONNÉES,
HYPOTHÈSES, SIMULATIONS, COMBINAISONS ANALYSÉES, RÉSULTATS, LIMITES,
RÉFÉRENCES.

Formats : PDF (reportlab, déjà en dépendances), HTML, Markdown, et ZIP
de recherche contenant les données nécessaires à la reproductibilité
(entrées, hash SHA-256, versions, paramètres).

RÈGLE DURE : le rapport agrège des résultats informatiques et des
données de laboratoire SANS jamais prétendre une efficacité
pharmacologique ou une sécurité d'emploi. Chaque section calculée
porte son avertissement ; les formulations interdites sont bannies
(auto-contrôle via ``combinaisons.verifier_honnetete``).
"""

from __future__ import annotations

import html as html_lib
import io
import json
import zipfile
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from webintel import __version__ as WEBINTEL_VERSION
from webintel.core.utils import utcnow
from webintel.database.models import Project
from webintel.science import combinaisons as comb_module
from webintel.science import export_dossier as dossier_module
from webintel.science.models import (
    CombinationStudy,
    Compound,
    DockingJob,
    Evidence,
    PlantCompound,
    ResearchObservation,
    Simulation,
    SynergyAnalysis,
)

AVERTISSEMENT_RAPPORT = (
    "Rapport de recherche WebIntel : agrégation de données rapportées, "
    "de résultats informatiques (prédictions) et — si inclus — de données "
    "de laboratoire. Résultat de screening informatique — hypothèse à "
    "vérifier expérimentalement. Ne constitue pas une preuve d'effet "
    "pharmacologique et ne se prononce pas sur la sécurité d'emploi."
)

LIMITES_RAPPORT = [
    "Ce rapport agrège des niveaux de données strictement séparés "
    "(publié, normalisé, hypothèse, prédiction, expérimental) : leur "
    "juxtaposition ne les rend pas équivalents.",
    "Les résultats informatiques (scores, similarités, docking, "
    "synergie) sont des prédictions/analyses — à confirmer "
    "expérimentalement.",
    "Aucune donnée de ce rapport ne permet de conclure à un effet "
    "pharmacologique ni de se prononcer sur la sécurité d'emploi.",
    "La reproductibilité repose sur les hash SHA-256 des entrées et les "
    "versions documentées dans le ZIP (dossier « donnees/ »).",
]


def _projet_ou_404(db: Session, project_id: int) -> Project:
    projet = db.get(Project, project_id)
    if projet is None:
        raise KeyError(f"Projet introuvable : id={project_id}.")
    return projet


def build_rapport_recherche(
    db: Session, project_id: int, *, inclure_prive: bool = False
) -> dict:
    """Construit le rapport de recherche structuré d'un projet.

    ``inclure_prive=False`` (défaut) : les observations et analyses
    privées du laboratoire sont EXCLUES (séparation labo / public).
    """
    projet = _projet_ou_404(db, project_id)
    plantes = dossier_module.plantes_du_projet(db, project_id)

    # COMPOSÉS : composés liés aux plantes du projet.
    ids_plantes = [p.id for p in plantes]
    composes: list[dict] = []
    if ids_plantes:
        liens = (
            db.query(PlantCompound)
            .filter(PlantCompound.plant_id.in_(ids_plantes))
            .all()
        )
        ids_composes = sorted({l.compound_id for l in liens})
        noms_plantes = {p.id: p.scientific_name for p in plantes}
        for cid in ids_composes:
            comp = db.get(Compound, cid)
            if comp is None:
                continue
            composes.append({
                "id": comp.id, "nom": comp.name, "formule": comp.formula,
                "smiles": comp.smiles, "inchikey": comp.inchikey,
                "classe_chimique": comp.chemical_class,
                "plantes": sorted({
                    noms_plantes[l.plant_id]
                    for l in liens if l.compound_id == cid
                }),
            })

    # PUBLICATIONS : preuves documentaires (données publiées, partagées).
    publications = [
        {"id": e.id, "titre": e.title, "reference": e.reference,
         "doi": e.doi, "annee": e.year,
         "auteurs": list(e.authors or []),
         "data_level": "publie"}
        for e in db.query(Evidence).order_by(Evidence.id).all()
    ]

    # DONNÉES : observations de laboratoire (privées sauf inclusion explicite).
    observations = []
    if inclure_prive:
        observations = [
            {"id": o.id, "protocole": o.protocol_ref, "auteur": o.author,
             "laboratoire": o.laboratory, "statut": o.status,
             "data_level": "experimental"}
            for o in db.query(ResearchObservation).filter(
                ResearchObservation.project_id == project_id
            ).order_by(ResearchObservation.id).all()
        ]

    # HYPOTHÈSES : hypothèses des simulations + études de combinaison.
    hypotheses = []
    for sim in db.query(Simulation).filter(
        Simulation.project_id == project_id
    ).order_by(Simulation.id).all():
        if sim.hypotheses:
            hypotheses.append({
                "source": f"simulation #{sim.id} « {sim.name} »",
                "texte": sim.hypotheses, "data_level": "hypothese",
            })
    for etude in comb_module.list_etudes(db, project_id):
        for section in (etude.resultats or {}).get("sections", []):
            if section.get("titre", "").startswith("11."):
                for hyp in section.get("hypotheses", []):
                    hypotheses.append({
                        "source": f"étude de combinaison #{etude.id} « {etude.nom} »",
                        "texte": hyp, "data_level": "hypothese",
                    })

    # SIMULATIONS (prédictions).
    simulations = [
        {"id": s.id, "nom": s.name, "methode": s.method,
         "moteur": s.engine_name, "input_hash": s.input_hash,
         "data_level": "prediction",
         "avertissement": "Simulation — à confirmer expérimentalement."}
        for s in db.query(Simulation).filter(
            Simulation.project_id == project_id
        ).order_by(Simulation.id).all()
    ]

    # COMBINAISONS ANALYSÉES : études + analyses de synergie + docking.
    combinaisons = []
    for etude in comb_module.list_etudes(db, project_id):
        score = None
        for section in (etude.resultats or {}).get("sections", []):
            if section.get("titre", "").startswith("9."):
                score = (section.get("score_informatique") or {}).get("score")
        combinaisons.append({
            "type": "etude_combinaison", "id": etude.id, "nom": etude.nom,
            "statut": etude.statut, "inputs_hash": etude.inputs_hash,
            "score_informatique": score, "data_level": "prediction",
        })
    for analyse in comb_module.list_analyses_synergie(
        db, project_id, inclure_prive=inclure_prive
    ):
        calcul = (analyse.resultats or {}).get("calcul", {})
        combinaisons.append({
            "type": "analyse_synergie", "id": analyse.id, "nom": analyse.nom,
            "methode": analyse.methode, "statut": analyse.statut,
            "inputs_hash": analyse.inputs_hash,
            "delta_moyen": calcul.get("delta_moyen"),
            "ci_moyen": calcul.get("ci_moyen"),
            "is_private": analyse.is_private,
            "data_level": "prediction",
        })
    for job in (
        db.query(DockingJob)
        .filter(DockingJob.project_id == project_id)
        .order_by(DockingJob.id).all()
    ):
        combinaisons.append({
            "type": "docking", "id": job.id, "nom": job.nom,
            "statut": job.statut, "inputs_hash": job.inputs_hash,
            "moteur": job.moteur, "data_level": "prediction",
        })

    # RÉFÉRENCES : bibliographie agrégée.
    references = []
    vus = set()
    for pub in publications:
        cle = pub.get("doi") or pub.get("reference") or pub.get("titre")
        if cle and cle not in vus:
            vus.add(cle)
            references.append(cle)

    rapport = {
        "projet": {"id": projet.id, "nom": projet.name},
        "genere_le": utcnow().isoformat(),
        "webintel_version": WEBINTEL_VERSION,
        "inclure_prive": bool(inclure_prive),
        "avertissement": AVERTISSEMENT_RAPPORT,
        "fiche_plantes": [
            {"id": p.id, "nom_scientifique": p.scientific_name,
             "famille": p.family, "parties_utilisees": list(p.used_parts or []),
             "data_level": "publie"}
            for p in plantes
        ],
        "composes": composes,
        "publications": publications,
        "donnees": {
            "observations_laboratoire": observations,
            "note": (
                "Données expérimentales du laboratoire (privées)."
                if inclure_prive else
                "Données privées du laboratoire exclues de ce rapport "
                "(séparation labo / public)."
            ),
        },
        "hypotheses": hypotheses,
        "simulations": simulations,
        "combinaisons_analysees": combinaisons,
        "resultats": {
            "nb_plantes": len(plantes),
            "nb_composes": len(composes),
            "nb_publications": len(publications),
            "nb_observations": len(observations),
            "nb_hypotheses": len(hypotheses),
            "nb_simulations": len(simulations),
            "nb_combinaisons": len(combinaisons),
            "note": "Synthèse quantitative — les résultats détaillés sont "
                    "dans les sections ci-dessus et les exports associés.",
        },
        "limites": LIMITES_RAPPORT,
        "references": references,
    }
    comb_module.exiger_dossier_honnete(  # noqa: SLF001 — auto-contrôle interne
        rapport, "rapport de recherche"
    )
    return rapport


# ---------------------------------------------------------------------------
# Rendus
# ---------------------------------------------------------------------------

def render_markdown(rapport: dict) -> str:
    """Rapport en Markdown."""
    projet = rapport["projet"]
    lignes = [
        f"# Rapport de recherche — {projet['nom']}",
        "",
        f"> {rapport['avertissement']}",
        "",
        f"Généré le {rapport['genere_le']} par WebIntel "
        f"{rapport['webintel_version']} (données privées : "
        f"{'incluses' if rapport['inclure_prive'] else 'exclues'}).",
        "",
        "## FICHE DE LA PLANTE",
        "",
    ]
    for p in rapport["fiche_plantes"]:
        lignes.append(
            f"- **{p['nom_scientifique']}** (id={p['id']}, "
            f"famille={p.get('famille') or '—'}) [Publié]"
        )
    lignes += ["", "## COMPOSÉS", ""]
    for c in rapport["composes"]:
        lignes.append(
            f"- **{c['nom']}** (formule={c.get('formule') or '—'}, "
            f"plantes={', '.join(c['plantes']) or '—'})"
        )
    lignes += ["", "## PUBLICATIONS", ""]
    for pub in rapport["publications"]:
        lignes.append(
            f"- {pub.get('titre') or pub.get('reference') or '—'}"
            + (f" (DOI : {pub['doi']})" if pub.get("doi") else "")
            + " [Publié]"
        )
    lignes += ["", "## DONNÉES", "", rapport["donnees"]["note"], ""]
    for obs in rapport["donnees"]["observations_laboratoire"]:
        lignes.append(
            f"- Observation #{obs['id']} — protocole={obs.get('protocole') or '—'} "
            f"[Expérimental]"
        )
    lignes += ["", "## HYPOTHÈSES", ""]
    for hyp in rapport["hypotheses"]:
        lignes.append(f"- ({hyp['source']}) [Hypothèse] {hyp['texte']}")
    lignes += ["", "## SIMULATIONS", ""]
    for sim in rapport["simulations"]:
        lignes.append(
            f"- #{sim['id']} « {sim['nom']} » — {sim.get('methode') or '—'} "
            f"[Prédiction] — {sim['avertissement']}"
        )
    lignes += ["", "## COMBINAISONS ANALYSÉES", ""]
    for combo in rapport["combinaisons_analysees"]:
        lignes.append(
            f"- [{combo['type']}] #{combo['id']} « {combo['nom']} » — "
            f"statut={combo['statut']}, hash=`{combo['inputs_hash']}` [Prédiction]"
        )
    lignes += ["", "## RÉSULTATS", ""]
    res = rapport["resultats"]
    lignes.append(
        f"{res['nb_plantes']} plante(s), {res['nb_composes']} composé(s), "
        f"{res['nb_publications']} publication(s), {res['nb_simulations']} "
        f"simulation(s), {res['nb_combinaisons']} combinaison(s) analysée(s)."
    )
    lignes += ["", "## LIMITES", ""]
    lignes += [f"- {lim}" for lim in rapport["limites"]]
    lignes += ["", "## RÉFÉRENCES", ""]
    lignes += [f"- {ref}" for ref in rapport["references"]]
    return "\n".join(lignes)


def render_html(rapport: dict) -> str:
    """Rapport en page HTML autonome (imprimable)."""
    def ech(texte):
        return html_lib.escape(str(texte if texte is not None else "—"))

    projet = rapport["projet"]
    parties = [
        "<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>",
        f"<title>Rapport de recherche — {ech(projet['nom'])}</title>",
        "<style>body{font-family:sans-serif;max-width:960px;margin:2em auto;"
        "padding:0 1em;color:#222}h1{color:#1a5276}h2{color:#1a5276;"
        "border-bottom:1px solid #ccc;padding-bottom:.3em}"
        ".avert{background:#fef9e7;border-left:4px solid #d4ac0d;"
        "padding:1em;margin:1em 0}.badge{display:inline-block;font-size:.8em;"
        "padding:.1em .5em;border-radius:8px;margin-left:.5em}"
        ".publie{background:#e6f4ea;color:#1a7f37}.prediction{background:#f3e8fd;"
        "color:#8250df}.hypothese{background:#fef3e2;color:#b45309}"
        ".experimental{background:#fde8e8;color:#cf222e}"
        "li{margin:.3em 0}</style></head><body>",
        f"<h1>Rapport de recherche — {ech(projet['nom'])}</h1>",
        f"<div class='avert'><strong>Avertissement :</strong> "
        f"{ech(rapport['avertissement'])}</div>",
    ]

    def badge(niveau, libelle):
        return f"<span class='badge {ech(niveau)}'>{ech(libelle)}</span>"

    sections = [
        ("FICHE DE LA PLANTE",
         "".join(f"<li><i>{ech(p['nom_scientifique'])}</i> "
                  f"(famille : {ech(p.get('famille'))})"
                  f"{badge('publie', 'Publié')}</li>"
                  for p in rapport["fiche_plantes"]) or "<li>—</li>"),
        ("COMPOSÉS",
         "".join(f"<li><b>{ech(c['nom'])}</b> — formule : {ech(c.get('formule'))}"
                  f"{badge('publie', 'Publié')}</li>"
                  for c in rapport["composes"]) or "<li>—</li>"),
        ("PUBLICATIONS",
         "".join(f"<li>{ech(p.get('titre') or p.get('reference'))}"
                  f"{badge('publie', 'Publié')}</li>"
                  for p in rapport["publications"]) or "<li>—</li>"),
        ("DONNÉES",
         f"<p>{ech(rapport['donnees']['note'])}</p>" + "".join(
             f"<li>Observation #{o['id']} — {ech(o.get('protocole'))}"
             f"{badge('experimental', 'Expérimental')}</li>"
             for o in rapport["donnees"]["observations_laboratoire"])),
        ("HYPOTHÈSES",
         "".join(f"<li>({ech(h['source'])}) {ech(h['texte'])}"
                  f"{badge('hypothese', 'Hypothèse')}</li>"
                  for h in rapport["hypotheses"]) or "<li>—</li>"),
        ("SIMULATIONS",
         "".join(f"<li>#{s['id']} « {ech(s['nom'])} » — {ech(s.get('methode'))}"
                  f"{badge('prediction', 'Prédiction')}</li>"
                  for s in rapport["simulations"]) or "<li>—</li>"),
        ("COMBINAISONS ANALYSÉES",
         "".join(f"<li>[{ech(c['type'])}] #{c['id']} « {ech(c['nom'])} » — "
                  f"statut : {ech(c['statut'])}"
                  f"{badge('prediction', 'Prédiction')}</li>"
                  for c in rapport["combinaisons_analysees"]) or "<li>—</li>"),
        ("RÉSULTATS",
         f"<p>{ech(rapport['resultats']['nb_plantes'])} plante(s), "
         f"{ech(rapport['resultats']['nb_composes'])} composé(s), "
         f"{ech(rapport['resultats']['nb_publications'])} publication(s), "
         f"{ech(rapport['resultats']['nb_simulations'])} simulation(s), "
         f"{ech(rapport['resultats']['nb_combinaisons'])} combinaison(s) "
         "analysée(s).</p>"),
        ("LIMITES",
         "<ul>" + "".join(f"<li>{ech(l)}</li>" for l in rapport["limites"])
         + "</ul>"),
        ("RÉFÉRENCES",
         "<ul>" + "".join(f"<li>{ech(r)}</li>" for r in rapport["references"])
         + "</ul>" if rapport["references"] else "<p>—</p>"),
    ]
    for titre, corps in sections:
        parties.append(f"<h2>{ech(titre)}</h2>")
        if corps.startswith("<p>") or corps.startswith("<ul>"):
            parties.append(corps)
        else:
            parties.append(f"<ul>{corps}</ul>")
    parties.append(
        f"<div class='avert'><strong>Avertissement :</strong> "
        f"{ech(rapport['avertissement'])}</div></body></html>"
    )
    page = "\n".join(parties)
    comb_module.exiger_dossier_honnete(rapport, "rendu HTML rapport")  # noqa: SLF001
    return page


def build_pdf(rapport: dict) -> bytes | None:
    """Rapport en VRAI PDF (reportlab). ``None`` si reportlab indisponible.

    Ne prétend jamais être un PDF quand reportlab manque (le routeur
    sert alors le HTML imprimable).
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )
    except ImportError:
        return None

    def ech(texte):
        return html_lib.escape("" if texte is None else str(texte))

    tampon = io.BytesIO()
    doc = SimpleDocTemplate(
        tampon, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Rapport de recherche — {rapport['projet']['nom']}",
        author="WebIntel",
    )
    styles = getSampleStyleSheet()
    s_titre = ParagraphStyle("T", parent=styles["Title"], fontSize=20)
    s_h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=14,
                          spaceBefore=12)
    s_normal = styles["Normal"]
    s_petit = ParagraphStyle("P", parent=styles["Normal"], fontSize=9,
                             textColor=colors.HexColor("#555555"))
    s_alerte = ParagraphStyle(
        "A", parent=styles["Normal"], fontSize=10,
        textColor=colors.HexColor("#7d6608"), borderPadding=6,
        backColor=colors.HexColor("#fef9e7"),
    )
    recit: list = [
        Paragraph(f"Rapport de recherche — {ech(rapport['projet']['nom'])}", s_titre),
        Paragraph(
            f"Généré le {ech(rapport['genere_le'])} par WebIntel "
            f"{ech(rapport['webintel_version'])}.", s_petit),
        Spacer(1, 4 * mm),
        Paragraph(f"<b>Avertissement.</b> {ech(rapport['avertissement'])}", s_alerte),
        Spacer(1, 4 * mm),
    ]

    def tableau(donnees):
        lignes = [[Paragraph(c, s_normal) for c in ligne] for ligne in donnees]
        tab = Table(lignes, repeatRows=1)
        tab.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f4f4")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return tab

    def section(titre, lignes_table=None, paragraphes=None):
        recit.append(Paragraph(ech(titre), s_h2))
        if lignes_table:
            recit.append(tableau(lignes_table))
        for para in paragraphes or []:
            recit.append(Paragraph(ech(para), s_normal))
        recit.append(Spacer(1, 3 * mm))

    section("FICHE DE LA PLANTE",
            [["Plante", "Famille", "Niveau"]] + [
                [p["nom_scientifique"], p.get("famille") or "—", "[Publié]"]
                for p in rapport["fiche_plantes"]])
    section("COMPOSÉS",
            [["Composé", "Formule", "Plantes"]] + [
                [c["nom"], c.get("formule") or "—",
                 ", ".join(c["plantes"]) or "—"]
                for c in rapport["composes"]])
    section("PUBLICATIONS",
            paragraphes=[p.get("titre") or p.get("reference") or "—"
                         for p in rapport["publications"]] or ["—"])
    section("DONNÉES", paragraphes=[rapport["donnees"]["note"]] + [
        f"Observation #{o['id']} — {o.get('protocole') or '—'} [Expérimental]"
        for o in rapport["donnees"]["observations_laboratoire"]])
    section("HYPOTHÈSES",
            paragraphes=[f"({h['source']}) [Hypothèse] {h['texte']}"
                         for h in rapport["hypotheses"]] or ["—"])
    section("SIMULATIONS",
            paragraphes=[
                f"#{s['id']} « {s['nom']} » [Prédiction] — "
                "Simulation — à confirmer expérimentalement."
                for s in rapport["simulations"]] or ["—"])
    section("COMBINAISONS ANALYSÉES",
            paragraphes=[
                f"[{c['type']}] #{c['id']} « {c['nom']} » — {c['statut']} "
                f"[Prédiction] (hash {c['inputs_hash'][:12]}…)"
                for c in rapport["combinaisons_analysees"]] or ["—"])
    res = rapport["resultats"]
    section("RÉSULTATS", paragraphes=[
        f"{res['nb_plantes']} plante(s), {res['nb_composes']} composé(s), "
        f"{res['nb_publications']} publication(s), {res['nb_simulations']} "
        f"simulation(s), {res['nb_combinaisons']} combinaison(s) analysée(s)."
    ])
    section("LIMITES", paragraphes=[f"• {lim}" for lim in rapport["limites"]])
    section("RÉFÉRENCES",
            paragraphes=rapport["references"] or ["—"])
    recit.append(Paragraph(
        f"<b>Avertissement.</b> {ech(rapport['avertissement'])}", s_alerte))
    doc.build(recit)
    return tampon.getvalue()


# ---------------------------------------------------------------------------
# ZIP de recherche (reproductibilité)
# ---------------------------------------------------------------------------

def _methodes_synergie() -> dict:
    """Modules des 4 méthodes de synergie (import paresseux)."""
    from webintel.science.synergie import METHODES

    return METHODES


def build_zip_recherche(
    db: Session, project_id: int, *, inclure_prive: bool = False
) -> bytes:
    """ZIP de recherche : rapport + données nécessaires à la reproductibilité.

    Contenu : ``rapport.md``, ``rapport.html``, ``donnees/entrees.json``
    (entrées normalisées, hash SHA-256, paramètres, versions des
    bibliothèques/moteurs), ``MANIFEST.json`` (inventaire, niveaux de
    données, avertissements). Lève ``KeyError`` si le projet est absent.
    """
    rapport = build_rapport_recherche(db, project_id, inclure_prive=inclure_prive)
    projet = rapport["projet"]

    entrees: dict = {"projet_id": project_id, "inclure_prive": inclure_prive}
    # Hash des entrées de chaque combinaison analysée (rejouabilité).
    entrees["combinaisons"] = [
        {"type": c["type"], "id": c["id"], "inputs_hash": c["inputs_hash"]}
        for c in rapport["combinaisons_analysees"]
    ]
    versions = {
        "webintel": WEBINTEL_VERSION,
        "score_comparaison_structurale": comb_module.SCORE_VERSION,
        "synergie": {
            nom: {"formule": module.FORMULE}
            for nom, module in _methodes_synergie().items()
        },
    }
    try:
        from webintel.science import docking as _dock

        moteur_dispo, moteur_raison = _dock.statut_moteur()
    except ImportError:
        moteur_dispo, moteur_raison = False, "module docking indisponible"
    versions["docking"] = {
        "moteur_disponible": moteur_dispo, "raison": moteur_raison,
    }
    donnees_json = {
        "entrees": entrees,
        "hash_entrees_global": comb_module.hacher_entrees(entrees),
        "versions": versions,
        "niveaux_donnees": ["publie", "normalise", "hypothese", "prediction"]
        + (["experimental"] if inclure_prive else []),
        "genere_le": rapport["genere_le"],
    }

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("rapport.md", render_markdown(rapport))
        archive.writestr("rapport.html", render_html(rapport))
        archive.writestr(
            "donnees/entrees.json",
            json.dumps(donnees_json, ensure_ascii=False, indent=2),
        )
        manifeste = {
            "dossier": "Dossier de recherche WebIntel — rapport",
            "projet_id": projet["id"],
            "projet_nom": projet["nom"],
            "genere_le": datetime.now(timezone.utc).isoformat(),
            "genere_par": "webintel",
            "webintel_version": WEBINTEL_VERSION,
            "inclure_prive": bool(inclure_prive),
            "avertissement": AVERTISSEMENT_RAPPORT,
            "contenu": [
                {"fichier": "rapport.md", "type": "rapport_markdown"},
                {"fichier": "rapport.html", "type": "rapport_html"},
                {"fichier": "donnees/entrees.json",
                 "type": "donnees_reproductibilite",
                 "hash": donnees_json["hash_entrees_global"]},
            ],
        }
        archive.writestr(
            "MANIFEST.json", json.dumps(manifeste, ensure_ascii=False, indent=2)
        )
    return tampon.getvalue()
