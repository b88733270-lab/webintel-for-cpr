"""Construction et rendu des rapports de simulation WebIntel.

Un rapport de simulation comporte EXACTEMENT les 6 sections suivantes :
INPUTS, METHOD, PARAMETERS, OUTPUTS, REFERENCES, LIMITATIONS, complétées
des métadonnées : ``engine_name``, ``engine_version``, ``input_hash``
(SHA-256 du JSON canonique trié des entrées), ``avertissement``
(étiquetage honnêteté scientifique) et ``genere_le`` (UTC ISO).

Le rendu HTML (``render_html``) produit un document simple et propre avec
un tableau par section et échappe systématiquement le HTML injecté.
"""

from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone
from typing import Any

try:  # Réutilise les helpers canoniques du projet si disponibles.
    from webintel.database.helpers import canonical_json, sha256_hex
except Exception:  # noqa: BLE001 — repli local équivalent

    def canonical_json(value: Any) -> str:
        """Forme canonique stable : clés triées, séparateurs compacts."""
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )

    def sha256_hex(s: str) -> str:
        """Empreinte SHA-256 hexadécimale d'une chaîne (UTF-8)."""
        return hashlib.sha256(s.encode("utf-8")).hexdigest()


#: Étiquetage obligatoire de tout rapport de simulation (honnêteté scientifique).
AVERTISSEMENT = (
    "Simulation — à confirmer expérimentalement. "
    "Ce résultat est une prédiction informatique, pas une preuve expérimentale."
)

#: Les 6 sections obligatoires d'un rapport, dans l'ordre d'affichage.
SECTIONS = ("INPUTS", "METHOD", "PARAMETERS", "OUTPUTS", "REFERENCES", "LIMITATIONS")


def build_report(
    engine: Any,
    inputs: dict,
    params: dict,
    outputs: dict,
    *,
    references: list[str] | None = None,
    method: str | None = None,
) -> dict:
    """Construit un rapport de simulation normalisé.

    - ``engine`` : module moteur (doit définir ``ENGINE_NAME``,
      ``ENGINE_VERSION``, ``LIMITATIONS`` et idéalement ``DESCRIPTION``).
    - ``inputs`` / ``params`` : entrées et paramètres déjà validés.
    - ``outputs`` : sorties du moteur (JSON-sérialisables).
    - ``references`` : références bibliographiques éventuelles.
    - ``method`` : description de la méthode ; par défaut la description
      du moteur.
    """
    nom_moteur = getattr(engine, "ENGINE_NAME", "moteur_inconnu")
    version_moteur = getattr(engine, "ENGINE_VERSION", "0.0.0")
    methode = method or getattr(engine, "DESCRIPTION", "") or "Méthode non documentée."
    limitations = list(getattr(engine, "LIMITATIONS", []) or [])

    return {
        "engine_name": nom_moteur,
        "engine_version": version_moteur,
        "input_hash": sha256_hex(canonical_json({"inputs": inputs, "params": params})),
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "avertissement": AVERTISSEMENT,
        "INPUTS": inputs,
        "METHOD": {"description": methode, "engine_name": nom_moteur,
                   "engine_version": version_moteur},
        "PARAMETERS": params,
        "OUTPUTS": outputs,
        "REFERENCES": list(references or []),
        "LIMITATIONS": limitations,
    }


def _valeur_en_html(valeur: Any) -> str:
    """Convertit une valeur en fragment HTML échappé."""
    if isinstance(valeur, dict):
        lignes = "".join(
            f"<tr><th>{html.escape(str(cle))}</th>"
            f"<td>{_valeur_en_html(val)}</td></tr>"
            for cle, val in valeur.items()
        )
        return f"<table class='kv'>{lignes}</table>"
    if isinstance(valeur, (list, tuple)):
        elements = "".join(f"<li>{_valeur_en_html(elt)}</li>" for elt in valeur)
        return f"<ul>{elements}</ul>" if elements else "<em>(vide)</em>"
    if valeur is None:
        return "<em>(non renseigné)</em>"
    return html.escape(str(valeur))


def render_html(report: dict) -> str:
    """Rend un rapport de simulation en page HTML simple et propre.

    Chaque section est un tableau ; tout le contenu dynamique est
    échappé (protection contre l'injection HTML).
    """
    titre = html.escape(str(report.get("engine_name", "Rapport de simulation")))
    version = html.escape(str(report.get("engine_version", "")))
    avertissement = html.escape(str(report.get("avertissement", "")))
    genere_le = html.escape(str(report.get("genere_le", "")))
    input_hash = html.escape(str(report.get("input_hash", "")))

    sections_html = []
    for section in SECTIONS:
        contenu = _valeur_en_html(report.get(section))
        sections_html.append(
            f"<h2>{html.escape(section)}</h2>\n{contenu}"
        )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{titre} — rapport de simulation</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto;
       padding: 0 1rem; color: #222; }}
.badge {{ display: inline-block; background: #b45309; color: #fff;
          padding: .25rem .6rem; border-radius: .4rem; font-weight: 600; }}
table.kv {{ border-collapse: collapse; width: 100%; margin: .5rem 0; }}
table.kv th, table.kv td {{ border: 1px solid #ddd; padding: .35rem .6rem;
                            text-align: left; vertical-align: top; }}
table.kv th {{ background: #f5f5f5; width: 30%; }}
.meta {{ color: #555; font-size: .9rem; }}
h2 {{ border-bottom: 2px solid #eee; padding-bottom: .25rem; }}
</style>
</head>
<body>
<h1>{titre} <small>v{version}</small></h1>
<p><span class="badge">⚠ {avertissement}</span></p>
<p class="meta">Généré le {genere_le} · empreinte des entrées : <code>{input_hash}</code></p>
{''.join(sections_html)}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Phase 13 — rapport reproductible depuis une simulation persistée
# ---------------------------------------------------------------------------


def build_report_from_simulation(simulation: Any) -> dict:
    """Reconstruit le rapport à 6 sections d'une simulation persistée.

    ``simulation`` : objet ``Simulation`` (SQLAlchemy) ou tout objet
    exposant les mêmes attributs. Les sections sont dérivées des champs
    persistés :

    - INPUTS : ``simulation.inputs`` (entrées normalisées, Phase 13) ;
    - METHOD : ``simulation.method`` + nom/version du moteur ;
    - PARAMETERS : ``simulation.parameters`` ;
    - OUTPUTS : ``simulation.result`` ;
    - REFERENCES : ``simulation.references`` ;
    - LIMITATIONS : ``simulation.hypotheses`` (les limitations du moteur
      y sont consignées à l'exécution).

    Les métadonnées d'audit sont incluses : ``input_hash`` (SHA-256 des
    entrées), ``executed_by`` (qui), ``run_at`` (quand), ``engine_name`` /
    ``engine_version``, ``simulation_id``. L'avertissement
    « Simulation — à confirmer expérimentalement » est toujours présent.

    Rejouabilité : avec ``engine_name``, ``inputs`` et ``parameters``,
    ``POST /science/simulations/run`` (ou le bouton « Rejouer »)
    recalcule exactement les mêmes entrées → même ``input_hash`` (pour
    un moteur déterministe, mêmes sorties).
    """
    entrees = dict(getattr(simulation, "inputs", None) or {})
    parametres = dict(getattr(simulation, "parameters", None) or {})
    nom_moteur = getattr(simulation, "engine_name", None) or "moteur_inconnu"
    version_moteur = getattr(simulation, "engine_version", None) or ""
    methode = getattr(simulation, "method", None) or (
        f"{nom_moteur} {version_moteur}".strip() or "Méthode non documentée."
    )
    hypotheses = getattr(simulation, "hypotheses", None) or ""
    limitations = [
        ligne[2:].strip()
        for ligne in hypotheses.splitlines()
        if ligne.strip().startswith("- ")
    ]
    run_at = getattr(simulation, "run_at", None)
    limitations_export = (
        limitations if limitations else ([hypotheses] if hypotheses else [])
    )
    return {
        "simulation_id": getattr(simulation, "id", None),
        "simulation_name": getattr(simulation, "name", None),
        "engine_name": nom_moteur,
        "engine_version": version_moteur,
        "input_hash": getattr(simulation, "input_hash", None),
        "executed_by": getattr(simulation, "executed_by", None),
        "run_at": run_at.isoformat() if run_at is not None else None,
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "avertissement": AVERTISSEMENT,
        "INPUTS": entrees,
        "METHOD": {
            "description": methode,
            "engine_name": nom_moteur,
            "engine_version": version_moteur,
        },
        "PARAMETERS": parametres,
        "OUTPUTS": getattr(simulation, "result", None) or {},
        "REFERENCES": list(getattr(simulation, "references", None) or []),
        "LIMITATIONS": limitations_export,
    }


def _valeur_en_markdown(valeur: Any, niveau: int = 0) -> list[str]:
    """Convertit une valeur en lignes Markdown (échappement minimal)."""
    prefixe = "  " * niveau
    if valeur is None:
        return [f"{prefixe}*(non renseigné)*"]
    if isinstance(valeur, dict):
        lignes: list[str] = []
        for cle, val in valeur.items():
            sous = _valeur_en_markdown(val, niveau + 1)
            if len(sous) == 1 and not isinstance(val, (dict, list)):
                lignes.append(f"{prefixe}- **{cle}** : {sous[0].strip()}")
            else:
                lignes.append(f"{prefixe}- **{cle}** :")
                lignes.extend(sous)
        return lignes or [f"{prefixe}*(vide)*"]
    if isinstance(valeur, (list, tuple)):
        lignes = []
        for elt in valeur:
            sous = _valeur_en_markdown(elt, niveau + 1)
            lignes.append(f"{prefixe}- {sous[0].strip()}")
            lignes.extend(sous[1:])
        return lignes or [f"{prefixe}*(vide)*"]
    texte = str(valeur).replace("\n", " ")
    return [f"{prefixe}{texte}"]


def render_markdown(report: dict) -> str:
    """Rend un rapport de simulation en document Markdown exportable.

    Le document reprend les 6 sections dans l'ordre
    INPUTS → METHOD → PARAMETERS → OUTPUTS → REFERENCES → LIMITATIONS,
    précédées des métadonnées d'audit (empreinte SHA-256 des entrées,
    moteur + version, exécuté par, date) et de l'avertissement
    scientifique obligatoire.
    """
    lignes = [
        f"# Rapport de simulation — {report.get('simulation_name') or report.get('engine_name', '')}",
        "",
        f"> ⚠ **{report.get('avertissement', AVERTISSEMENT)}**",
        "",
        "## Audit",
        "",
        f"- **Simulation id** : {report.get('simulation_id')}",
        f"- **Moteur** : {report.get('engine_name')} v{report.get('engine_version')}",
        f"- **Empreinte des entrées (SHA-256)** : `{report.get('input_hash')}`",
        f"- **Exécutée par** : {report.get('executed_by') or '—'}",
        f"- **Exécutée le** : {report.get('run_at') or '—'}",
        f"- **Rapport généré le** : {report.get('genere_le')}",
        "",
        "## Rejouabilité",
        "",
        "Pour rejouer : `POST /science/simulations/run` avec le même "
        f"`engine_name` (`{report.get('engine_name')}`), les mêmes `inputs` "
        "(section INPUTS) et les mêmes `parameters` (section PARAMETERS). "
        "Des entrées identiques produisent le même `input_hash` ; pour un "
        "moteur déterministe, les sorties sont identiques.",
        "",
    ]
    for section in SECTIONS:
        lignes.append(f"## {section}")
        lignes.append("")
        lignes.extend(_valeur_en_markdown(report.get(section)))
        lignes.append("")
    return "\n".join(lignes).rstrip() + "\n"
