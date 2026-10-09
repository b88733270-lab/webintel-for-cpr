"""Exports scientifiques — Phase 9 (EXPORT uniquement, pas d'import).

Ce module fournit les fonctions pures d'export des données scientifiques
de WebIntel :

- ``lignes_plantes`` / ``lignes_composes`` / ``lignes_evidences`` :
  lignes structurées (dicts JSON-sérialisables) pour les exports CSV/JSON.
  Chaque ligne porte ``data_level`` (niveau de preuve, jamais mélangé) et
  ``source_principale`` (DOI ou URL de la source de référence). Convention
  documentée : les données botaniques et chimiques de référence sont
  étiquetées ``"publie"`` (données de référence issues de sources
  publiées), comme dans les sections de ``build_plant_fiche``.
- ``export_csv`` : aplatit des lignes structurées en CSV (``;`` séparateur,
  convention tableur français), avec en-têtes en français.
- ``build_fiche_pdf`` : fiche plante en VRAI PDF via ``reportlab``
  (badges en texte, sources, avertissements). **Limitation documentée** :
  si ``reportlab`` n'est pas importable, la fonction retourne ``None`` et
  le routeur sert alors le HTML imprimable (``render_fiche_html``) — on ne
  prétend JAMAIS servir un PDF quand c'est du HTML.
- ``render_fiche_html`` : fiche plante en HTML imprimable (CSS print),
  réutilisé dans le dossier ZIP.
- ``build_dossier_zip`` : dossier de recherche complet d'un projet au
  format ZIP (fiches JSON+HTML par plante du projet, bibliographie texte
  + BibTeX, rapports de simulation, journal du projet, MANIFEST.json,
  README.txt).

Règles de confidentialité (strictes) :

- Les données de laboratoire (``ResearchObservation``, ``LabNote``,
  ``Simulation``) sont PRIVÉES par projet. Avec ``inclure_prive=False``
  (défaut), seules celles EXPLICITEMENT publiées (``est_publie``) sont
  incluses ; les autres sont exclues. Aucune donnée privée ne sort vers
  le public sans action explicite (paramètre ``inclure_prive=True`` +
  contrôle d'accès projet côté routeur).
- « Plantes du projet » : convention reprise de
  ``webintel.science.lab.tableau_de_bord`` — les plantes liées aux
  composés étudiés dans les simulations du projet (via
  ``plant_compounds``). Les fiches incluses dans le dossier sont
  publiques (``project_id=None``) sauf si ``inclure_prive=True``.
- Toute simulation exportée porte l'avertissement
  « Simulation — à confirmer expérimentalement » (via
  ``webintel.simulations.reports``).

Portable SQLite / PostgreSQL : aucune requête spécifique à un moteur.
"""

from __future__ import annotations

import csv
import html as html_lib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.orm import Session

from webintel import __version__ as WEBINTEL_VERSION
from webintel.database.models import Project
from webintel.science import fiche as fiche_module
from webintel.science.honnetete import exiger_dossier_honnete
from webintel.science.lab import LabNote, est_publie, list_notes
from webintel.science.models import (
    Compound,
    Evidence,
    Plant,
    PlantCompound,
    ResearchObservation,
    Simulation,
)
from webintel.simulations.reports import (
    AVERTISSEMENT as AVERTISSEMENT_SIMULATION,
    build_report,
    render_html,
)

__all__ = [
    "AVERTISSEMENT_EXPORT",
    "lignes_plantes",
    "lignes_composes",
    "lignes_evidences",
    "export_csv",
    "render_fiche_html",
    "build_fiche_pdf",
    "exporter_fiche",
    "fiche_pdf_disponible",
    "build_dossier_zip",
    "plantes_du_projet",
]

#: Avertissement obligatoire sur tout export contenant des simulations.
AVERTISSEMENT_EXPORT = (
    "Simulation — à confirmer expérimentalement. "
    "Ce résultat est une prédiction informatique, pas une preuve expérimentale."
)

#: Niveaux de données possibles dans un export (5 niveaux, jamais mélangés).
NIVEAUX_EXPORT = ("publie", "normalise", "hypothese", "prediction", "experimental")


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _iso(valeur) -> str | None:
    """Date/heure en ISO 8601 (ou None)."""
    return valeur.isoformat() if valeur is not None else None


def _slug(texte: str) -> str:
    """Nom de fichier sûr à partir d'un libellé (minuscules, tirets)."""
    texte = (texte or "sans-nom").strip().lower()
    texte = re.sub(r"[^a-z0-9]+", "-", texte).strip("-")
    return texte or "sans-nom"


def _source_principale_lien(
    db: Session, plant_id: int | None = None, compound_id: int | None = None
) -> str:
    """Source principale (DOI ou publication) du premier lien plante-composé.

    Chaîne vide si aucun lien : on n'invente jamais une source.
    """
    requete = db.query(PlantCompound)
    if plant_id is not None:
        requete = requete.filter(PlantCompound.plant_id == plant_id)
    if compound_id is not None:
        requete = requete.filter(PlantCompound.compound_id == compound_id)
    lien = requete.order_by(PlantCompound.id).first()
    if lien is None:
        return ""
    return lien.doi or lien.publication or ""


# ---------------------------------------------------------------------------
# 1. Lignes d'export CSV/JSON (données publiques de référence)
# ---------------------------------------------------------------------------

_COLONNES_PLANTES = (
    "id",
    "nom_scientifique",
    "famille",
    "genre",
    "espece",
    "synonymes",
    "noms_vernaculaires",
    "description",
    "distribution",
    "parties_utilisees",
    "identifiants_externes",
    "data_level",
    "source_principale",
)

_COLONNES_COMPOSES = (
    "id",
    "nom",
    "formule",
    "masse_moleculaire",
    "smiles",
    "inchikey",
    "classe_chimique",
    "synonymes",
    "identifiants_externes",
    "data_level",
    "source_principale",
)

_COLONNES_EVIDENCES = (
    "id",
    "type_source",
    "reference",
    "titre",
    "auteurs",
    "annee",
    "doi",
    "pmid",
    "url",
    "extrait",
    "data_level",
    "source_principale",
)


def lignes_plantes(db: Session) -> list[dict]:
    """Lignes d'export des plantes (données publiques, triées par nom).

    ``data_level`` vaut ``"publie"`` (données botaniques de référence) et
    ``source_principale`` est le DOI ou la publication du premier lien
    plante-composé enregistré (vide si aucun).
    """
    lignes = []
    plantes = db.query(Plant).order_by(Plant.scientific_name, Plant.id).all()
    for plante in plantes:
        lignes.append({
            "id": plante.id,
            "nom_scientifique": plante.scientific_name,
            "famille": plante.family,
            "genre": plante.genus,
            "espece": plante.species,
            "synonymes": list(plante.synonyms or []),
            "noms_vernaculaires": dict(plante.vernacular_names or {}),
            "description": plante.description,
            "distribution": plante.distribution,
            "parties_utilisees": list(plante.used_parts or []),
            "identifiants_externes": dict(plante.external_ids or {}),
            "data_level": "publie",
            "source_principale": _source_principale_lien(db, plant_id=plante.id),
        })
    return lignes


def lignes_composes(db: Session) -> list[dict]:
    """Lignes d'export des composés (données publiques, triées par nom)."""
    lignes = []
    composes = db.query(Compound).order_by(Compound.name, Compound.id).all()
    for compose in composes:
        lignes.append({
            "id": compose.id,
            "nom": compose.name,
            "formule": compose.formula,
            "masse_moleculaire": compose.molecular_weight,
            "smiles": compose.smiles,
            "inchikey": compose.inchikey,
            "classe_chimique": compose.chemical_class,
            "synonymes": list(compose.synonyms or []),
            "identifiants_externes": dict(compose.external_ids or {}),
            "data_level": "publie",
            "source_principale": _source_principale_lien(db, compound_id=compose.id),
        })
    return lignes


def lignes_evidences(db: Session) -> list[dict]:
    """Lignes d'export des preuves documentaires (données publiques)."""
    lignes = []
    preuves = db.query(Evidence).order_by(Evidence.id).all()
    for preuve in preuves:
        lignes.append({
            "id": preuve.id,
            "type_source": preuve.source_type,
            "reference": preuve.reference,
            "titre": preuve.title,
            "auteurs": list(preuve.authors or []),
            "annee": preuve.year,
            "doi": preuve.doi,
            "pmid": preuve.pmid,
            "url": preuve.url,
            "extrait": preuve.excerpt,
            "data_level": "publie",
            "source_principale": preuve.doi or preuve.url or "",
        })
    return lignes


def _aplatir(valeur) -> str:
    """Aplatit une valeur structurée pour une cellule CSV."""
    if valeur is None:
        return ""
    if isinstance(valeur, dict):
        return "; ".join(f"{cle}={val}" for cle, val in valeur.items())
    if isinstance(valeur, (list, tuple)):
        return "; ".join(str(elt) for elt in valeur)
    return str(valeur)


def export_csv(lignes: list[dict], colonnes: tuple[str, ...]) -> str:
    """Convertit des lignes structurées en CSV (``;`` séparateur, UTF-8).

    Les listes/dicts sont aplatis en texte lisible ; les valeurs ``None``
    deviennent des cellules vides.
    """
    tampon = io.StringIO()
    redacteur = csv.writer(tampon, delimiter=";", lineterminator="\n")
    redacteur.writerow(colonnes)
    for ligne in lignes:
        redacteur.writerow([_aplatir(ligne.get(col)) for col in colonnes])
    return tampon.getvalue()


# ---------------------------------------------------------------------------
# 2. Fiche plante : HTML imprimable + PDF réel (reportlab)
# ---------------------------------------------------------------------------

def fiche_pdf_disponible() -> bool:
    """``True`` si ``reportlab`` est importable (vrai PDF possible)."""
    try:
        import reportlab  # noqa: F401
        return True
    except ImportError:
        return False


_BADGES_TEXTE = {
    "publie": "[Publié]",
    "normalise": "[Normalisé]",
    "hypothese": "[Hypothèse]",
    "prediction": "[Prédiction]",
    "experimental": "[Expérimental]",
}


def _badge_texte(niveau: str | None) -> str:
    """Badge de niveau de donnée en texte brut (pour le PDF)."""
    return _BADGES_TEXTE.get(niveau or "", f"[{niveau or '—'}]")


def _echapper(texte) -> str:
    """Échappe le HTML pour les paragraphes reportlab."""
    return html_lib.escape("" if texte is None else str(texte))


def render_fiche_html(db: Session, plant_id: int, project_id: int | None = None) -> str:
    """Fiche plante en HTML imprimable (CSS ``@media print``).

    Mêmes sections et avertissements que la fiche web ; les badges de
    niveau de donnée reprennent les couleurs du contrat. Lève
    ``KeyError`` si la plante est absente.
    """
    fiche = fiche_module.build_plant_fiche(db, plant_id, project_id=project_id)
    plante = fiche["plante"]
    nom = html_lib.escape(plante.get("nom_scientifique") or f"Plante #{plant_id}")

    def badge(niveau: str | None, label: str | None = None) -> str:
        couleurs = {
            "publie": "#1a7f37", "normalise": "#1d4ed8", "hypothese": "#b45309",
            "prediction": "#8250df", "experimental": "#cf222e",
        }
        libelles = {
            "publie": "Publié", "normalise": "Normalisé", "hypothese": "Hypothèse",
            "prediction": "Prédiction", "experimental": "Expérimental",
        }
        couleur = couleurs.get(niveau or "", "#6e7781")
        texte = html_lib.escape(label or libelles.get(niveau or "", niveau or "—"))
        return (
            f'<span style="display:inline-block;background:{couleur};color:#fff;'
            f'padding:2px 8px;border-radius:4px;font-size:0.8em;">{texte}</span>'
        )

    morceaux: list[str] = []
    morceaux.append(f"<h1><em>{nom}</em></h1>")
    morceaux.append(
        f"<p>Fiche scientifique WebIntel {badge(plante.get('data_level'))}</p>"
    )
    if fiche.get("avertissement_separation"):
        morceaux.append(
            "<p><strong>Lecture de la fiche.</strong> "
            f"{html_lib.escape(fiche['avertissement_separation'])}</p>"
        )

    # Identité
    lignes_identite = [
        ("Nom scientifique", f"<em>{nom}</em>"),
        ("Famille", html_lib.escape(plante.get("famille") or "—")),
        ("Genre", f"<em>{html_lib.escape(plante.get('genre') or '—')}</em>"),
        ("Espèce", f"<em>{html_lib.escape(plante.get('espece') or '—')}</em>"),
    ]
    if plante.get("synonymes"):
        lignes_identite.append(
            ("Synonymes", html_lib.escape(", ".join(plante["synonymes"])))
        )
    if plante.get("noms_vernaculaires"):
        vern = "; ".join(
            f"{langue} : {', '.join(noms)}"
            for langue, noms in plante["noms_vernaculaires"].items()
        )
        lignes_identite.append(("Noms vernaculaires", html_lib.escape(vern)))
    morceaux.append("<h2>Identité</h2><table>")
    for cle, valeur in lignes_identite:
        morceaux.append(f"<tr><th>{cle}</th><td>{valeur}</td></tr>")
    morceaux.append("</table>")

    botanique = fiche["botanique"]
    morceaux.append(f"<h2>Botanique {badge(botanique.get('data_level'))}</h2>")
    if botanique.get("description"):
        morceaux.append(f"<p>{html_lib.escape(botanique['description'])}</p>")
    # v2.1 — morphologie détaillée (champs manquants = non affichés, jamais inventés)
    for cle, libelle in (
        ("feuilles", "Feuilles"), ("fleurs", "Fleurs"), ("fruits", "Fruits"),
        ("graines", "Graines"), ("racines", "Racines"), ("ecorce", "Écorce"),
        ("habitat", "Habitat"),
    ):
        if botanique.get(cle):
            morceaux.append(
                f"<p><strong>{libelle} :</strong> "
                f"{html_lib.escape(botanique[cle])}</p>"
            )
    if botanique.get("distribution"):
        morceaux.append(
            f"<p><strong>Distribution :</strong> "
            f"{html_lib.escape(botanique['distribution'])}</p>"
        )
    if botanique.get("parties_utilisees"):
        morceaux.append(
            "<p><strong>Parties utilisées :</strong> "
            f"{html_lib.escape(', '.join(botanique['parties_utilisees']))}</p>"
        )
    if botanique.get("references_botaniques"):
        morceaux.append(
            "<p><strong>Références botaniques :</strong> "
            f"{html_lib.escape('; '.join(botanique['references_botaniques']))}</p>"
        )

    pharmacognosie = fiche["pharmacognosie"]
    morceaux.append(
        f"<h2>Pharmacognosie {badge(pharmacognosie.get('data_level'))}</h2>"
    )
    if pharmacognosie.get("parties_utilisees"):
        morceaux.append(
            "<p><strong>Parties utilisées :</strong> "
            f"{html_lib.escape(', '.join(pharmacognosie['parties_utilisees']))}</p>"
        )
    if pharmacognosie.get("matiere_premiere"):
        morceaux.append(
            f"<p><strong>Matière première :</strong> "
            f"{html_lib.escape(pharmacognosie['matiere_premiere'])}</p>"
        )
    if pharmacognosie.get("preparation"):
        morceaux.append(
            f"<p><strong>Préparation :</strong> "
            f"{html_lib.escape(pharmacognosie['preparation'])}</p>"
        )
    if pharmacognosie.get("methodes_extraction_rapporteess"):
        morceaux.append(
            "<p><strong>Méthodes d'extraction rapportées :</strong> "
            f"{html_lib.escape('; '.join(pharmacognosie['methodes_extraction_rapporteess']))}</p>"
        )
    if pharmacognosie.get("note"):
        morceaux.append(f"<p>{html_lib.escape(pharmacognosie['note'])}</p>")

    morceaux.append("<h2>Phytochimie</h2>")
    if fiche["phytochimie"]:
        morceaux.append(
            "<table><tr><th>Composé</th><th>Formule</th><th>Partie</th>"
            "<th>Concentration</th><th>Analyse</th><th>Rendement</th>"
            "<th>Source</th><th>Niveau</th></tr>"
        )
        for item in fiche["phytochimie"]:
            conc = (
                f"{item['concentration']} {item.get('unite') or ''}".strip()
                if item.get("concentration") is not None else "—"
            )
            rendement = (
                f"{item['rendement']} {item.get('rendement_unite') or ''}".strip()
                if item.get("rendement") is not None else "—"
            )
            preuve = item.get("preuve") or {}
            src = (
                item.get("doi") or item.get("publication") or "—"
            )
            if preuve.get("doi") and preuve["doi"] != item.get("doi"):
                src += f" (preuve : {preuve['doi']})"
            morceaux.append(
                "<tr><td><strong>"
                f"{html_lib.escape(item.get('nom') or '—')}</strong></td>"
                f"<td>{html_lib.escape(item.get('formule') or '—')}</td>"
                f"<td>{html_lib.escape(item.get('partie') or '—')}</td>"
                f"<td>{html_lib.escape(conc)}</td>"
                f"<td>{html_lib.escape(item.get('methode_analyse') or '—')}</td>"
                f"<td>{html_lib.escape(rendement)}</td>"
                f"<td>{html_lib.escape(src)}</td>"
                f"<td>{badge(item.get('data_level'))}</td></tr>"
            )
        morceaux.append("</table>")
    else:
        morceaux.append("<p>Aucun composé rapporté pour cette plante.</p>")

    morceaux.append("<h2>Pharmacologie</h2>")
    if fiche["pharmacologie"]:
        for etude in fiche["pharmacologie"]:
            auteurs = ", ".join(etude.get("auteurs") or [])
            morceaux.append(
                f"<p><strong>{html_lib.escape(etude.get('titre') or 'Étude')}</strong> "
                f"{badge(etude.get('data_level'))}<br>"
                f"{html_lib.escape(auteurs)}"
                + (f" ({etude['annee']})" if etude.get("annee") else "")
                + (f" — {html_lib.escape(etude['extrait'])}" if etude.get("extrait") else "")
                + (f"<br>DOI : {html_lib.escape(etude['doi'])}" if etude.get("doi") else "")
                + (f"<br>URL : {html_lib.escape(etude['url'])}" if etude.get("url") else "")
                + "</p>"
            )
    else:
        morceaux.append("<p>Aucune étude pharmacologique référencée.</p>")

    # v2.1 — activités pharmacologiques rapportées
    if fiche.get("activites_pharmacologiques"):
        morceaux.append("<h3>Activités pharmacologiques rapportées</h3>")
        morceaux.append(
            "<p><em>Données rapportées telles que publiées — jamais une "
            "allégation d'effet thérapeutique.</em></p>"
        )
        morceaux.append(
            "<table><tr><th>Activité</th><th>Type d'étude</th><th>Modèle</th>"
            "<th>Résultat publié</th><th>Preuve</th></tr>"
        )
        for act in fiche["activites_pharmacologiques"]:
            preuve = act.get("preuve") or {}
            morceaux.append(
                "<tr><td><strong>"
                f"{html_lib.escape(act.get('activite') or '—')}</strong> "
                f"{badge(act.get('data_level'))}</td>"
                f"<td>{html_lib.escape(act.get('type_etude') or '—')}</td>"
                f"<td>{html_lib.escape(act.get('modele_etude') or '—')}</td>"
                f"<td>{html_lib.escape(act.get('resultat_publie') or '—')}</td>"
                f"<td>{html_lib.escape(preuve.get('doi') or preuve.get('reference') or '—')}</td></tr>"
            )
        morceaux.append("</table>")

    # v2.1 — cibles biologiques documentées
    if fiche.get("cibles"):
        morceaux.append("<h3>Cibles biologiques documentées</h3>")
        morceaux.append(
            "<table><tr><th>Composé</th><th>Cible</th><th>Interaction</th>"
            "<th>Preuve</th></tr>"
        )
        for cible in fiche["cibles"]:
            preuve = cible.get("preuve") or {}
            morceaux.append(
                "<tr><td><strong>"
                f"{html_lib.escape(cible.get('compose_nom') or '—')}</strong></td>"
                f"<td>{html_lib.escape(cible.get('cible') or '—')}</td>"
                f"<td>{html_lib.escape(cible.get('type_interaction') or '—')}</td>"
                f"<td>{html_lib.escape(preuve.get('doi') or preuve.get('reference') or '—')}</td></tr>"
            )
        morceaux.append("</table>")

    # Sécurité : le bandeau de prudence est TOUJOURS affiché (visible même vide).
    morceaux.append("<h2>⚠️ Sécurité</h2>")
    morceaux.append(
        "<p><strong>Bandeau de prudence.</strong> Données rapportées "
        "(heuristique textuelle sur les sources citées) — <strong>ceci "
        "n'est pas un avis médical, toxicologique ou réglementaire</strong>. "
        "Consultez toujours la source originale et un professionnel de "
        "santé qualifié. L'absence d'alerte ne signifie pas l'absence "
        "de risque.</p>"
    )
    if fiche["securite"]:
        for item in fiche["securite"]:
            morceaux.append(
                f"<p><strong>{html_lib.escape(item.get('avertissement') or '')}</strong><br>"
                f"{html_lib.escape(item.get('titre') or '')}</p>"
            )
    else:
        morceaux.append(
            "<p><em>Aucune alerte détectée dans les sources citées — "
            "cela ne permet pas de se prononcer sur la sécurité d'emploi.</em></p>"
        )

    # v2.1 — littérature structurée
    if fiche.get("litterature"):
        morceaux.append("<h2>Littérature</h2>")
        morceaux.append("<ol>")
        for entree in fiche["litterature"]:
            auteurs = ", ".join(entree.get("auteurs") or [])
            morceaux.append(
                "<li>"
                f"<strong>{html_lib.escape(entree.get('titre') or 'Sans titre')}</strong> "
                f"{badge(entree.get('data_level'))}<br>"
                f"{html_lib.escape(auteurs)}"
                + (f" ({entree['annee']})" if entree.get("annee") else "")
                + (f" — <em>{html_lib.escape(entree['journal'])}</em>" if entree.get("journal") else "")
                + (f"<br>DOI : {html_lib.escape(entree['doi'])}" if entree.get("doi") else "")
                + (f"<br>PMID : {html_lib.escape(entree['pmid'])}" if entree.get("pmid") else "")
                + "</li>"
            )
        morceaux.append("</ol>")

    morceaux.append("<h2>Bibliographie</h2>")
    if fiche["bibliographie"]:
        morceaux.append("<ol>")
        for ref in fiche["bibliographie"]:
            morceaux.append(f"<li>{html_lib.escape(ref)}</li>")
        morceaux.append("</ol>")
    else:
        morceaux.append("<p>Aucune référence bibliographique.</p>")

    if fiche.get("laboratoire_prive"):
        morceaux.append("<h2>Données de laboratoire (privé)</h2>")
        morceaux.append(
            f"<p><strong>{html_lib.escape(fiche.get('avertissement_separation') or '')}</strong></p>"
        )
        for obs in fiche["laboratoire_prive"]:
            morceaux.append(
                f"<p><strong>{html_lib.escape(obs.get('protocol_ref') or 'Observation')}</strong> "
                f"{badge('experimental')}<br>"
                f"{html_lib.escape(obs.get('comment') or '')}</p>"
            )

    if fiche.get("simulations"):
        morceaux.append("<h2>Simulations</h2>")
        morceaux.append(
            f"<p><strong>{html_lib.escape(fiche_module.AVERTISSEMENT_SIMULATION)}</strong></p>"
        )
        for sim in fiche["simulations"]:
            morceaux.append(
                f"<p><strong>{html_lib.escape(sim.get('nom') or 'Simulation')}</strong> "
                f"{badge('prediction')}<br>"
                f"Cible : {html_lib.escape(sim.get('cible') or '—')} · "
                f"Méthode : {html_lib.escape(sim.get('methode') or '—')}</p>"
            )

    corps = "\n".join(morceaux)
    # Auto-contrôle : seul le texte généré par WebIntel est scanné ; les
    # verbatims sourcés (titres, références, extraits, résultats publiés
    # cités avec leur source) n'entraînent jamais de blocage.
    exiger_dossier_honnete(fiche, "fiche HTML exportée")
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Fiche {nom} — WebIntel</title>
<style>
body {{ font-family: Georgia, serif; max-width: 800px; margin: 2rem auto;
       padding: 0 1rem; color: #1a1a1a; }}
table {{ border-collapse: collapse; width: 100%; margin: .6rem 0; }}
th, td {{ border: 1px solid #ccc; padding: .4rem .6rem; text-align: left;
          vertical-align: top; }}
th {{ background: #f4f4f4; }}
h2 {{ border-bottom: 2px solid #ddd; padding-bottom: .2rem; margin-top: 1.6rem; }}
@media print {{
  body {{ margin: 0; max-width: none; }}
  h2 {{ page-break-after: avoid; }}
  table, p {{ page-break-inside: avoid; }}
}}
</style>
</head>
<body>
{corps}
<hr>
<p><small>Fiche générée par WebIntel v{html_lib.escape(WEBINTEL_VERSION)} —
données rapportées, non validées par WebIntel.</small></p>
</body>
</html>
"""


def build_fiche_pdf(
    db: Session, plant_id: int, project_id: int | None = None
) -> bytes | None:
    """Fiche plante complète en VRAI PDF (``reportlab``, moteur platypus).

    Sections : identité, botanique, pharmacognosie, phytochimie (tableau),
    pharmacologie, sécurité (avertissement), bibliographie, puis — si
    ``project_id`` est fourni — données de laboratoire et simulations
    (avec l'avertissement « Simulation — à confirmer
    expérimentalement »). Les badges de niveau de donnée sont rendus en
    texte (``[Publié]``, ``[Prédiction]``...).

    **Limitation documentée** : si ``reportlab`` n'est pas importable, la
    fonction retourne ``None`` — le routeur sert alors le HTML imprimable
    (``render_fiche_html``) et ne prétend JAMAIS que c'est un PDF.

    Lève ``KeyError`` si la plante est absente.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        return None

    fiche = fiche_module.build_plant_fiche(db, plant_id, project_id=project_id)
    plante = fiche["plante"]
    nom = plante.get("nom_scientifique") or f"Plante #{plant_id}"
    # Auto-contrôle : seul le texte généré par WebIntel est scanné (les
    # verbatims sourcés — titres, références, extraits, résultats publiés
    # cités avec leur source — n'entraînent jamais de blocage).
    exiger_dossier_honnete(fiche, "fiche PDF exportée")

    tampon = io.BytesIO()
    doc = SimpleDocTemplate(
        tampon, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Fiche {nom} — WebIntel",
        author="WebIntel",
    )
    styles = getSampleStyleSheet()
    style_titre = ParagraphStyle("TitreFiche", parent=styles["Title"], fontSize=20)
    style_h2 = ParagraphStyle("H2Fiche", parent=styles["Heading2"], fontSize=14,
                              spaceBefore=14, textColor=colors.HexColor("#1a1a1a"))
    style_normal = styles["Normal"]
    style_petit = ParagraphStyle("Petit", parent=styles["Normal"], fontSize=9,
                                 textColor=colors.HexColor("#555555"))
    style_alerte = ParagraphStyle(
        "Alerte", parent=styles["Normal"], fontSize=10,
        textColor=colors.HexColor("#9a1b1b"), borderPadding=6,
        backColor=colors.HexColor("#fef2f2"),
    )

    recit: list = []
    recit.append(Paragraph(f"Fiche scientifique — <i>{_echapper(nom)}</i>", style_titre))
    recit.append(Paragraph(
        f"Générée par WebIntel v{_echapper(WEBINTEL_VERSION)} — "
        "données rapportées, non validées par WebIntel.", style_petit))
    recit.append(Spacer(1, 6 * mm))
    if fiche.get("avertissement_separation"):
        recit.append(Paragraph(
            f"<b>Lecture de la fiche.</b> {_echapper(fiche['avertissement_separation'])}",
            style_normal))
        recit.append(Spacer(1, 4 * mm))

    def _tableau(donnees: list[list[str]], largeurs=None) -> Table:
        lignes = [
            [Paragraph(cellule, style_normal) for cellule in ligne]
            for ligne in donnees
        ]
        tableau = Table(lignes, colWidths=largeurs, repeatRows=1)
        tableau.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f4f4")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        return tableau

    # Identité
    recit.append(Paragraph("Identité", style_h2))
    identite = [
        ["Champ", "Valeur"],
        ["Nom scientifique", f"<i>{_echapper(nom)}</i>"],
        ["Famille", _echapper(plante.get("famille") or "—")],
        ["Genre", f"<i>{_echapper(plante.get('genre') or '—')}</i>"],
        ["Espèce", f"<i>{_echapper(plante.get('espece') or '—')}</i>"],
    ]
    if plante.get("synonymes"):
        identite.append(["Synonymes", _echapper(", ".join(plante["synonymes"]))])
    if plante.get("noms_vernaculaires"):
        vern = "; ".join(
            f"{langue} : {', '.join(noms)}"
            for langue, noms in plante["noms_vernaculaires"].items()
        )
        identite.append(["Noms vernaculaires", _echapper(vern)])
    identite.append(["Niveau de donnée", _badge_texte(plante.get("data_level"))])
    recit.append(_tableau(identite, largeurs=[55 * mm, 115 * mm]))

    # Botanique
    botanique = fiche["botanique"]
    recit.append(Paragraph(
        f"Botanique {_badge_texte(botanique.get('data_level'))}", style_h2))
    for cle in ("description", "distribution"):
        if botanique.get(cle):
            recit.append(Paragraph(_echapper(botanique[cle]), style_normal))
    # v2.1 — morphologie détaillée
    for cle, libelle in (
        ("feuilles", "Feuilles"), ("fleurs", "Fleurs"), ("fruits", "Fruits"),
        ("graines", "Graines"), ("racines", "Racines"), ("ecorce", "Écorce"),
        ("habitat", "Habitat"),
    ):
        if botanique.get(cle):
            recit.append(Paragraph(
                f"<b>{libelle} :</b> {_echapper(botanique[cle])}", style_normal))
    if botanique.get("parties_utilisees"):
        recit.append(Paragraph(
            "<b>Parties utilisées :</b> "
            f"{_echapper(', '.join(botanique['parties_utilisees']))}", style_normal))
    if botanique.get("references_botaniques"):
        recit.append(Paragraph(
            "<b>Références botaniques :</b> "
            f"{_echapper('; '.join(botanique['references_botaniques']))}", style_normal))

    # Pharmacognosie
    pharmacognosie = fiche["pharmacognosie"]
    recit.append(Paragraph(
        f"Pharmacognosie {_badge_texte(pharmacognosie.get('data_level'))}", style_h2))
    if pharmacognosie.get("parties_utilisees"):
        recit.append(Paragraph(
            "<b>Parties utilisées :</b> "
            f"{_echapper(', '.join(pharmacognosie['parties_utilisees']))}", style_normal))
    if pharmacognosie.get("matiere_premiere"):
        recit.append(Paragraph(
            "<b>Matière première :</b> "
            f"{_echapper(pharmacognosie['matiere_premiere'])}", style_normal))
    if pharmacognosie.get("preparation"):
        recit.append(Paragraph(
            "<b>Préparation :</b> "
            f"{_echapper(pharmacognosie['preparation'])}", style_normal))
    if pharmacognosie.get("methodes_extraction_rapporteess"):
        recit.append(Paragraph(
            "<b>Méthodes d'extraction rapportées :</b> "
            f"{_echapper('; '.join(pharmacognosie['methodes_extraction_rapporteess']))}",
            style_normal))
    if pharmacognosie.get("note"):
        recit.append(Paragraph(_echapper(pharmacognosie["note"]), style_normal))

    # Phytochimie
    recit.append(Paragraph("Phytochimie", style_h2))
    if fiche["phytochimie"]:
        entetes = ["Composé", "Partie", "Concentration", "Analyse", "Source", "Niveau"]
        lignes = [entetes]
        for item in fiche["phytochimie"]:
            conc = (
                f"{item['concentration']} {item.get('unite') or ''}".strip()
                if item.get("concentration") is not None else "—"
            )
            preuve = item.get("preuve") or {}
            lignes.append([
                _echapper(item.get("nom") or "—"),
                _echapper(item.get("partie") or "—"),
                _echapper(conc),
                _echapper(item.get("methode_analyse") or "—"),
                _echapper(item.get("doi") or item.get("publication") or "—")
                + (f" [preuve : {preuve['doi']}]" if preuve.get("doi") and preuve["doi"] != item.get("doi") else ""),
                _badge_texte(item.get("data_level")),
            ])
        recit.append(_tableau(lignes))
    else:
        recit.append(Paragraph("Aucun composé rapporté pour cette plante.", style_normal))

    # Pharmacologie
    recit.append(Paragraph("Pharmacologie", style_h2))
    if fiche["pharmacologie"]:
        for etude in fiche["pharmacologie"]:
            auteurs = ", ".join(etude.get("auteurs") or [])
            texte = (
                f"<b>{_echapper(etude.get('titre') or 'Étude')}</b> "
                f"{_badge_texte(etude.get('data_level'))}<br/>"
                f"{_echapper(auteurs)}"
                + (f" ({etude['annee']})" if etude.get("annee") else "")
            )
            if etude.get("extrait"):
                texte += f"<br/>{_echapper(etude['extrait'])}"
            if etude.get("doi"):
                texte += f"<br/>DOI : {_echapper(etude['doi'])}"
            elif etude.get("url"):
                texte += f"<br/>URL : {_echapper(etude['url'])}"
            recit.append(Paragraph(texte, style_normal))
            recit.append(Spacer(1, 3 * mm))
    else:
        recit.append(Paragraph("Aucune étude pharmacologique référencée.", style_normal))

    # v2.1 — activités pharmacologiques rapportées
    if fiche.get("activites_pharmacologiques"):
        recit.append(Paragraph("Activités pharmacologiques rapportées", style_h2))
        recit.append(Paragraph(
            "<i>Données rapportées telles que publiées — jamais une "
            "allégation d'effet thérapeutique.</i>", style_normal))
        for act in fiche["activites_pharmacologiques"]:
            preuve = act.get("preuve") or {}
            texte = (
                f"<b>{_echapper(act.get('activite') or 'Activité')}</b> "
                f"{_badge_texte(act.get('data_level'))}"
            )
            if act.get("type_etude"):
                texte += f"<br/>Type d'étude : {_echapper(act['type_etude'])}"
            if act.get("modele_etude"):
                texte += f"<br/>Modèle : {_echapper(act['modele_etude'])}"
            if act.get("resultat_publie"):
                texte += f"<br/>Résultat publié : {_echapper(act['resultat_publie'])}"
            ref_preuve = preuve.get("doi") or preuve.get("reference")
            if ref_preuve:
                texte += f"<br/>Preuve : {_echapper(ref_preuve)}"
            recit.append(Paragraph(texte, style_normal))
            recit.append(Spacer(1, 3 * mm))

    # v2.1 — cibles biologiques documentées
    if fiche.get("cibles"):
        recit.append(Paragraph("Cibles biologiques documentées", style_h2))
        for cible in fiche["cibles"]:
            preuve = cible.get("preuve") or {}
            texte = (
                f"<b>{_echapper(cible.get('compose_nom') or '—')}</b> → "
                f"{_echapper(cible.get('cible') or '—')}"
            )
            if cible.get("type_interaction"):
                texte += f" ({_echapper(cible['type_interaction'])})"
            ref_preuve = preuve.get("doi") or preuve.get("reference")
            if ref_preuve:
                texte += f"<br/>Preuve : {_echapper(ref_preuve)}"
            recit.append(Paragraph(texte, style_normal))
            recit.append(Spacer(1, 3 * mm))

    # Sécurité : le bandeau de prudence est TOUJOURS affiché (visible même vide).
    recit.append(Paragraph("Sécurité", style_h2))
    recit.append(Paragraph(
        "<b>Bandeau de prudence.</b> Données rapportées (heuristique "
        "textuelle sur les sources citées) — <b>ceci n'est pas un avis "
        "médical, toxicologique ou réglementaire</b>. L'absence d'alerte "
        "ne signifie pas l'absence de risque.", style_alerte))
    recit.append(Spacer(1, 3 * mm))
    if fiche["securite"]:
        for item in fiche["securite"]:
            recit.append(Paragraph(
                f"<b>{_echapper(item.get('avertissement') or '')}</b><br/>"
                f"{_echapper(item.get('titre') or '')}", style_alerte))
            recit.append(Spacer(1, 3 * mm))

    # v2.1 — littérature structurée
    if fiche.get("litterature"):
        recit.append(Paragraph("Littérature", style_h2))
        for numero, entree in enumerate(fiche["litterature"], start=1):
            auteurs = ", ".join(entree.get("auteurs") or [])
            texte = (
                f"[{numero}] <b>{_echapper(entree.get('titre') or 'Sans titre')}</b> "
                f"{_badge_texte(entree.get('data_level'))}<br/>{_echapper(auteurs)}"
                + (f" ({entree['annee']})" if entree.get("annee") else "")
            )
            if entree.get("journal"):
                texte += f" — <i>{_echapper(entree['journal'])}</i>"
            if entree.get("doi"):
                texte += f"<br/>DOI : {_echapper(entree['doi'])}"
            if entree.get("pmid"):
                texte += f"<br/>PMID : {_echapper(entree['pmid'])}"
            recit.append(Paragraph(texte, style_normal))
            recit.append(Spacer(1, 2 * mm))

    # Bibliographie
    recit.append(Paragraph("Bibliographie", style_h2))
    if fiche["bibliographie"]:
        for numero, ref in enumerate(fiche["bibliographie"], start=1):
            recit.append(Paragraph(f"[{numero}] {_echapper(ref)}", style_normal))
            recit.append(Spacer(1, 2 * mm))
    else:
        recit.append(Paragraph("Aucune référence bibliographique.", style_normal))

    # Données de laboratoire (privé, si project_id fourni)
    if fiche.get("laboratoire_prive"):
        recit.append(Paragraph("Données de laboratoire (privé)", style_h2))
        recit.append(Paragraph(
            f"<b>{_echapper(fiche.get('avertissement_separation') or '')}</b>",
            style_normal))
        for obs in fiche["laboratoire_prive"]:
            recit.append(Paragraph(
                f"<b>{_echapper(obs.get('protocol_ref') or 'Observation')}</b> "
                f"{_badge_texte('experimental')}<br/>"
                f"{_echapper(obs.get('comment') or '')}", style_normal))
            recit.append(Spacer(1, 3 * mm))

    # Simulations
    if fiche.get("simulations"):
        recit.append(Paragraph("Simulations", style_h2))
        recit.append(Paragraph(
            f"<b>{_echapper(fiche_module.AVERTISSEMENT_SIMULATION)}</b>", style_alerte))
        recit.append(Spacer(1, 3 * mm))
        for sim in fiche["simulations"]:
            recit.append(Paragraph(
                f"<b>{_echapper(sim.get('nom') or 'Simulation')}</b> "
                f"{_badge_texte('prediction')}<br/>"
                f"Cible : {_echapper(sim.get('cible') or '—')} — "
                f"Méthode : {_echapper(sim.get('methode') or '—')}", style_normal))
            recit.append(Spacer(1, 3 * mm))

    doc.build(recit)
    return tampon.getvalue()


def exporter_fiche(
    db: Session, plant_id: int, format: str = "pdf",
    project_id: int | None = None,
) -> tuple[bytes, str, str]:
    """Exporte une fiche plante : ``(contenu, type_mime, nom_fichier)``.

    - ``format="pdf"`` : vrai PDF (``reportlab``) ;
    - ``format="html"`` : HTML imprimable (CSS print).

    **Limitation documentée** : si ``reportlab`` est indisponible,
    ``format="pdf"`` retourne le HTML imprimable avec un bandeau « PDF
    indisponible » (``text/html``) — on ne prétend JAMAIS servir un PDF
    quand c'est du HTML.

    Lève ``ValueError`` si le format est invalide, ``KeyError`` si la
    plante est absente.
    """
    fmt = (format or "").strip().lower()
    if fmt not in ("pdf", "html"):
        raise ValueError(
            f"Format invalide : « {format} ». Formats acceptés : pdf, html."
        )
    horodatage = datetime.now(timezone.utc).strftime("%Y%m%d")
    if fmt == "pdf":
        pdf = build_fiche_pdf(db, plant_id, project_id=project_id)
        if pdf is not None:
            return pdf, "application/pdf", f"fiche_plante_{plant_id}_{horodatage}.pdf"
        # Repli documenté : HTML imprimable, jamais un faux PDF.
        page = render_fiche_html(db, plant_id, project_id=project_id)
        bandeau = (
            "<p style='border:2px solid #b45309;background:#fffbeb;"
            "padding:8px;'><strong>PDF indisponible</strong> : la "
            "bibliothèque reportlab n'est pas installée sur ce serveur. "
            "Version HTML imprimable (utilisez « Imprimer → PDF » de "
            "votre navigateur).</p>"
        )
        page = page.replace("<body>", f"<body>\n{bandeau}", 1)
        return (
            page.encode("utf-8"),
            "text/html; charset=utf-8",
            f"fiche_plante_{plant_id}_{horodatage}.html",
        )
    page = render_fiche_html(db, plant_id, project_id=project_id)
    return (
        page.encode("utf-8"),
        "text/html; charset=utf-8",
        f"fiche_plante_{plant_id}_{horodatage}.html",
    )


# ---------------------------------------------------------------------------
# 3. Dossier de recherche (ZIP)
# ---------------------------------------------------------------------------

def plantes_du_projet(db: Session, project_id: int) -> list[Plant]:
    """Plantes étudiées par un projet (convention ``tableau_de_bord``).

    Les plantes liées aux composés des simulations du projet (via
    ``plant_compounds``), triées par nom scientifique. Les plantes sont
    des données publiques : cette fonction ne fuit aucune donnée privée.
    """
    ids_composes: set[int] = set()
    lignes = (
        db.query(Simulation.compound_ids)
        .filter(Simulation.project_id == project_id)
        .all()
    )
    for (liste,) in lignes:
        for identifiant in liste or []:
            try:
                ids_composes.add(int(identifiant))
            except (TypeError, ValueError):
                continue
    if not ids_composes:
        return []
    ids_plantes = {
        identifiant
        for (identifiant,) in db.query(PlantCompound.plant_id)
        .filter(PlantCompound.compound_id.in_(ids_composes))
        .distinct()
        .all()
    }
    if not ids_plantes:
        return []
    return (
        db.query(Plant)
        .filter(Plant.id.in_(ids_plantes))
        .order_by(Plant.scientific_name, Plant.id)
        .all()
    )


def _observations_visibles(
    db: Session, project_id: int, inclure_prive: bool
) -> list[ResearchObservation]:
    """Observations du projet visibles selon la règle de confidentialité."""
    observations = (
        db.query(ResearchObservation)
        .filter(ResearchObservation.project_id == project_id)
        .order_by(ResearchObservation.id)
        .all()
    )
    if inclure_prive:
        return observations
    return [o for o in observations if est_publie(db, "observation", o.id)]


def _notes_visibles(
    db: Session, project_id: int, inclure_prive: bool
) -> list[LabNote]:
    """Notes de laboratoire visibles selon la règle de confidentialité."""
    notes = list_notes(db, project_id) or []
    if inclure_prive:
        return list(notes)
    return [n for n in notes if est_publie(db, "note", n.id)]


def _simulations_visibles(
    db: Session, project_id: int, inclure_prive: bool, inclure_simulations: bool
) -> list[Simulation]:
    """Simulations du projet visibles (flag + règle de confidentialité)."""
    if not inclure_simulations:
        return []
    simulations = (
        db.query(Simulation)
        .filter(Simulation.project_id == project_id)
        .order_by(Simulation.id)
        .all()
    )
    if inclure_prive:
        return simulations
    return [s for s in simulations if est_publie(db, "simulation", s.id)]


def _rapport_simulation(simulation: Simulation) -> dict:
    """Construit un rapport normalisé depuis une ``Simulation`` stockée.

    Réutilise ``webintel.simulations.reports.build_report`` : le rapport
    porte l'avertissement « Simulation — à confirmer
    expérimentalement ».
    """
    moteur = SimpleNamespace(
        ENGINE_NAME=simulation.engine_name or "moteur_inconnu",
        ENGINE_VERSION=simulation.engine_version or "0.0.0",
        DESCRIPTION=simulation.method or "Méthode non documentée.",
        LIMITATIONS=[
            "Résultat de calcul : à confirmer expérimentalement.",
        ],
    )
    entrees = {
        "cible": simulation.target,
        "hypotheses": simulation.hypotheses,
        "composes_etudies": list(simulation.compound_ids or []),
    }
    return build_report(
        moteur,
        entrees,
        dict(simulation.parameters or {}),
        dict(simulation.result or {}),
        references=list(simulation.references or []),
        method=simulation.method,
    )


def _entree_bibtex(cle: str, etude: dict) -> str:
    """Entrée BibTeX ``@misc`` depuis un item de pharmacologie."""
    auteurs = " and ".join(etude.get("auteurs") or ["Auteurs inconnus"])
    champs = [
        f"  author = {{{auteurs}}}",
        f"  title = {{{etude.get('titre') or 'Sans titre'}}}",
    ]
    if etude.get("annee"):
        champs.append(f"  year = {{{etude['annee']}}}")
    if etude.get("reference"):
        champs.append(f"  howpublished = {{{etude['reference']}}}")
    if etude.get("doi"):
        champs.append(f"  doi = {{{etude['doi']}}}")
    if etude.get("url"):
        champs.append(f"  url = {{{etude['url']}}}")
    champs.append("  note = {Donnée rapportée, non validée par WebIntel}")
    return f"@misc{{{cle},\n" + ",\n".join(champs) + "\n}\n"


def _rendre_journal(
    observations: list[ResearchObservation], notes: list[LabNote]
) -> str:
    """Journal du projet en texte brut (notes + observations visibles)."""
    lignes = ["JOURNAL DU PROJET", "=================", ""]
    lignes.append("Notes de laboratoire")
    lignes.append("--------------------")
    if notes:
        for note in notes:
            lignes.append(f"- [{note.id}] {note.titre or 'Sans titre'} "
                          f"(v{note.version_courante}, {note.auteur or 'auteur inconnu'})")
            if note.contenu:
                lignes.append(f"  {note.contenu}")
            lignes.append("")
    else:
        lignes.append("(aucune note)")
        lignes.append("")
    lignes.append("Observations de recherche")
    lignes.append("-------------------------")
    if observations:
        for obs in observations:
            date = obs.observed_date.isoformat() if obs.observed_date else "date inconnue"
            lignes.append(f"- [{obs.id}] {obs.protocol_ref or 'Observation'} "
                          f"— {date} — {obs.author or 'auteur inconnu'} "
                          f"(statut : {obs.status})")
            if obs.comment:
                lignes.append(f"  {obs.comment}")
            lignes.append("")
    else:
        lignes.append("(aucune observation)")
        lignes.append("")
    return "\n".join(lignes)


def _rendre_readme(
    projet_nom: str, inclure_prive: bool, inclure_simulations: bool
) -> str:
    """README.txt du dossier : contenu, niveaux de données, avertissements."""
    prive = (
        "OUI — le dossier contient des données privées du laboratoire "
        "(observations, notes, simulations du projet)."
        if inclure_prive else
        "NON — seules les données publiques et les items explicitement "
        "publiés sont inclus."
    )
    return f"""DOSSIER DE RECHERCHE — {projet_nom}
{"=" * (24 + len(projet_nom))}

Généré par WebIntel v{WEBINTEL_VERSION}.

Contenu :
- fiches/ : une fiche par plante étudiée du projet, en JSON (données
  structurées) et en HTML imprimable.
- bibliographie.txt : références citées dans les fiches.
- bibliographie.bib : les mêmes références au format BibTeX.
- simulations/ : un rapport HTML par simulation incluse.
- journal.txt : journal du projet (notes + observations incluses).
- MANIFEST.json : inventaire du dossier (contenu, dates, niveaux de
  données, paramètres d'export).

Données privées incluses : {prive}

Niveaux de données (strictement séparés, jamais mélangés) :
- publie : données de référence issues de sources publiées ;
- experimental : observations et notes de laboratoire ;
- prediction : résultats de simulation.

AVERTISSEMENT — simulations :
« {AVERTISSEMENT_EXPORT} »
Un résultat de simulation n'est JAMAIS une preuve expérimentale.

Les données rapportées ne sont pas validées par WebIntel : consultez
toujours les sources originales citées.
"""


def build_dossier_zip(
    db: Session,
    project_id: int,
    *,
    inclure_prive: bool = False,
    inclure_simulations: bool = True,
) -> bytes:
    """Construit le dossier de recherche d'un projet (ZIP en mémoire).

    Contenu :

    - ``fiches/fiche_<id>_<slug>.json`` et ``.html`` : fiche complète par
      plante du projet (publique si ``inclure_prive=False``, avec les
      données du laboratoire si ``inclure_prive=True``) ;
    - ``bibliographie.txt`` + ``bibliographie.bib`` : références citées ;
    - ``simulations/simulation_<id>.html`` : rapports (avec l'avertissement
      « Simulation — à confirmer expérimentalement ») ;
    - ``journal.txt`` : notes + observations incluses ;
    - ``MANIFEST.json`` : inventaire (contenu, dates, niveaux de données,
      paramètres, ``genere_par``, version WebIntel) ;
    - ``README.txt`` : lecture du dossier et avertissements.

    Confidentialité : avec ``inclure_prive=False`` (défaut), les
    observations, notes et simulations privées (non publiées
    explicitement) sont EXCLUES. Lève ``KeyError`` si le projet est
    absent.
    """
    projet = db.get(Project, project_id)
    if projet is None:
        raise KeyError(f"Projet introuvable : id={project_id}")

    plantes = plantes_du_projet(db, project_id)
    observations = _observations_visibles(db, project_id, inclure_prive)
    notes = _notes_visibles(db, project_id, inclure_prive)
    simulations = _simulations_visibles(
        db, project_id, inclure_prive, inclure_simulations
    )

    tampon = io.BytesIO()
    contenu_manifest: list[dict] = []
    niveaux: set[str] = set()

    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        # Fiches par plante.
        for plante in plantes:
            fiche = fiche_module.build_plant_fiche(
                db, plante.id, project_id=project_id if inclure_prive else None
            )
            base = f"fiches/fiche_{plante.id}_{_slug(plante.scientific_name)}"
            donnees_json = json.dumps(fiche, ensure_ascii=False, indent=2)
            archive.writestr(f"{base}.json", donnees_json)
            contenu_manifest.append({
                "fichier": f"{base}.json",
                "type": "fiche_plante_json",
                "plante_id": plante.id,
                "data_level": "publie",
            })
            niveaux.add("publie")
            page_html = render_fiche_html(
                db, plante.id, project_id=project_id if inclure_prive else None
            )
            archive.writestr(f"{base}.html", page_html)
            contenu_manifest.append({
                "fichier": f"{base}.html",
                "type": "fiche_plante_html",
                "plante_id": plante.id,
                "data_level": "publie",
            })
            if fiche.get("laboratoire_prive"):
                niveaux.add("experimental")
            if fiche.get("simulations"):
                niveaux.add("prediction")

        # Bibliographie agrégée (preuves liées aux fiches).
        citations: list[str] = []
        entrees_bibtex: list[str] = []
        vus: set[str] = set()
        for plante in plantes:
            fiche = fiche_module.build_plant_fiche(db, plante.id)
            for ref in fiche.get("bibliographie", []):
                if ref not in vus:
                    vus.add(ref)
                    citations.append(ref)
            for numero, etude in enumerate(fiche.get("pharmacologie", []), start=1):
                cle = f"webintel_{plante.id}_{numero}"
                entrees_bibtex.append(_entree_bibtex(cle, etude))
        archive.writestr(
            "bibliographie.txt",
            "BIBLIOGRAPHIE\n==============\n\n"
            + ("\n\n".join(f"[{i}] {c}" for i, c in enumerate(citations, 1))
               if citations else "(aucune référence)"),
        )
        contenu_manifest.append({
            "fichier": "bibliographie.txt", "type": "bibliographie",
            "data_level": "publie",
        })
        archive.writestr(
            "bibliographie.bib",
            "% Bibliographie WebIntel — données rapportées, non validées.\n\n"
            + ("\n".join(entrees_bibtex) if entrees_bibtex else "% (aucune entrée)"),
        )
        contenu_manifest.append({
            "fichier": "bibliographie.bib", "type": "bibliographie_bibtex",
            "data_level": "publie",
        })

        # Rapports de simulation (avec avertissement).
        for simulation in simulations:
            rapport = _rapport_simulation(simulation)
            page = render_html(rapport)
            chemin = f"simulations/simulation_{simulation.id}_{_slug(simulation.name)}.html"
            archive.writestr(chemin, page)
            contenu_manifest.append({
                "fichier": chemin,
                "type": "rapport_simulation",
                "simulation_id": simulation.id,
                "data_level": "prediction",
                "avertissement": AVERTISSEMENT_EXPORT,
            })
            niveaux.add("prediction")

        # Études de combinaison + analyses de synergie (v2.1, optionnel).
        # Import paresseux + garde-fou : une erreur ici ne doit jamais
        # casser l'export du dossier existant.
        try:
            from webintel.science import combinaisons as _comb

            _comb.ajouter_au_dossier(
                archive, contenu_manifest, niveaux, db, project_id,
                inclure_prive=inclure_prive,
            )
        except Exception as exc:  # noqa: BLE001 — export dégradé, pas d'échec
            contenu_manifest.append({
                "fichier": None,
                "type": "combinaisons_indisponibles",
                "note": f"Module combinaisons indisponible : {exc}",
            })

        # Journal du projet.
        journal = _rendre_journal(observations, notes)
        archive.writestr("journal.txt", journal)
        contenu_manifest.append({
            "fichier": "journal.txt", "type": "journal_projet",
            "data_level": "experimental",
        })
        if observations or notes:
            niveaux.add("experimental")

        # README + MANIFEST.
        readme = _rendre_readme(projet.name, inclure_prive, inclure_simulations)
        archive.writestr("README.txt", readme)
        contenu_manifest.append({"fichier": "README.txt", "type": "readme"})
        manifeste = {
            "dossier": "Dossier de recherche WebIntel",
            "projet_id": project_id,
            "projet_nom": projet.name,
            "genere_le": datetime.now(timezone.utc).isoformat(),
            "genere_par": "webintel",
            "webintel_version": WEBINTEL_VERSION,
            "inclure_prive": bool(inclure_prive),
            "inclure_simulations": bool(inclure_simulations),
            "niveaux_donnees": sorted(niveaux),
            "nb_plantes": len(plantes),
            "nb_observations": len(observations),
            "nb_notes": len(notes),
            "nb_simulations": len(simulations),
            "avertissement_simulations": AVERTISSEMENT_EXPORT,
            "contenu": contenu_manifest,
        }
        archive.writestr("MANIFEST.json", json.dumps(manifeste, ensure_ascii=False, indent=2))

    return tampon.getvalue()
