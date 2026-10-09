"""Score de santé d'une page : `score_page(fetch_result, seo, structured)`.

Retourne `{overall: 0..100, axes: {technical, seo, content, structure, data_quality},
factors: [{name, score, weight, evidence, recommendation}]}`.

- technical : statut HTTP, temps de réponse, taille de la réponse ;
- seo : reprend le score de `seo.analyzer` ;
- content : volume de texte, ratio texte/HTML (bruit de gabarit) ;
- structure : hiérarchie des titres, landmarks HTML5, attributs alt ;
- data_quality : présence et richesse des données structurées.

Chaque facteur est expliqué (`evidence`) avec une recommandation en français.
`overall` = moyenne pondérée des facteurs.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from webintel.core.types import FetchResult
from webintel.core.utils import clean_text


def _factor(name: str, score: float, weight: float, evidence: str, recommendation: str) -> dict:
    return {
        "name": name,
        "score": round(max(0.0, min(100.0, score)), 1),
        "weight": weight,
        "evidence": evidence,
        "recommendation": recommendation,
    }


def score_page(fetch_result: FetchResult, seo: dict, structured: dict) -> dict:
    """Calcule le score de santé d'une page (0..100) avec facteurs expliqués."""
    factors: list[dict] = []
    fr = fetch_result
    soup = BeautifulSoup(fr.html or "", "lxml")

    # ---------- Axe technical ----------
    status = fr.status_code
    if fr.error:
        factors.append(_factor("Statut HTTP", 0, 2,
                               f"Échec de récupération : {fr.error}",
                               "Corrigez l'erreur de collecte avant toute analyse."))
    elif status == 200:
        factors.append(_factor("Statut HTTP", 100, 2, "HTTP 200 : page accessible.",
                               "Aucune action requise."))
    elif 300 <= status < 400:
        factors.append(_factor("Statut HTTP", 60, 2, f"HTTP {status} : redirection suivie vers {fr.final_url}.",
                               "Limitez les chaînes de redirections (une seule idéalement)."))
    else:
        factors.append(_factor("Statut HTTP", 0, 2, f"HTTP {status} : page en erreur.",
                               "Corrigez le statut HTTP (visez 200)."))

    duration_s = fr.duration_ms / 1000
    if duration_s <= 1:
        t_score = 100.0
    elif duration_s >= 10:
        t_score = 0.0
    else:
        t_score = 100.0 * (10 - duration_s) / 9
    factors.append(_factor(
        "Temps de réponse", t_score, 1,
        f"{duration_s:.2f} s pour récupérer la page.",
        "Visez moins d'1 s : cache, CDN, optimisation serveur." if t_score < 100
        else "Temps de réponse excellent."))

    size_kb = len((fr.html or "").encode("utf-8")) / 1024
    if size_kb <= 500:
        s_score = 100.0
    elif size_kb >= 5000:
        s_score = 0.0
    else:
        s_score = 100.0 * (5000 - size_kb) / 4500
    factors.append(_factor(
        "Taille de la réponse", s_score, 1,
        f"{size_kb:.0f} Ko de HTML transféré.",
        "Allégez la page : minifiez, paginez, limitez le HTML inutile." if s_score < 100
        else "Taille de page raisonnable."))

    # ---------- Axe seo ----------
    seo_score = float((seo or {}).get("score", 0))
    n_errors = sum(1 for i in (seo or {}).get("issues", []) if i.get("severity") == "error")
    factors.append(_factor(
        "Score SEO", seo_score, 2,
        f"Score SEO {seo_score:.0f}/100 ({n_errors} erreur(s) bloquante(s)).",
        "Traitez les issues SEO par sévérité (erreurs d'abord)." if seo_score < 100
        else "SEO on-page sain."))

    # ---------- Axe content ----------
    text = clean_text(soup.get_text(separator=" "))
    words = len(text.split())
    if words >= 600:
        w_score = 100.0
    elif words == 0:
        w_score = 0.0
    else:
        w_score = 100.0 * words / 600
    factors.append(_factor(
        "Volume de contenu", w_score, 2,
        f"{words} mots de texte visible.",
        "Enrichissez le contenu (visez 600+ mots utiles)." if w_score < 100
        else "Volume de contenu satisfaisant."))
    html_len = max(1, len(fr.html or ""))
    ratio = len(text) / html_len
    r_score = 100.0 if ratio >= 0.15 else max(0.0, 100.0 * ratio / 0.15)
    factors.append(_factor(
        "Ratio texte / HTML", r_score, 1,
        f"{ratio:.1%} du HTML est du texte visible.",
        "Réduisez le bruit de gabarit (scripts, menus) ou enrichissez le texte." if r_score < 100
        else "Bon équilibre contenu / code."))

    # ---------- Axe structure ----------
    headings = [(int(h.name[1]), clean_text(h.get_text())[:60]) for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])]
    h1_count = sum(1 for level, _ in headings if level == 1)
    skipped = any(b - a > 1 for (a, _), (b, _) in zip(headings, headings[1:]))
    if h1_count == 1 and not skipped and headings:
        h_score, h_ev = 100.0, f"{len(headings)} titres, un seul <h1>, hiérarchie respectée."
        h_rec = "Aucune action requise."
    elif not headings:
        h_score, h_ev, h_rec = 0.0, "Aucun titre (<h1>..<h6>) détecté.", "Structurez le contenu avec des titres hiérarchisés."
    else:
        h_score = 50.0
        h_ev = f"{len(headings)} titres, {h1_count} <h1>" + (" avec sauts de niveau." if skipped else ".")
        h_rec = "Un seul <h1> par page et pas de saut de niveau (<h1> → <h3>)."
    factors.append(_factor("Hiérarchie des titres", h_score, 1.5, h_ev, h_rec))

    landmarks = [tag for tag in ("header", "nav", "main", "footer") if soup.find(tag)]
    l_score = 100.0 * len(landmarks) / 4
    factors.append(_factor(
        "Landmarks HTML5", l_score, 1,
        f"{len(landmarks)}/4 landmarks présents ({', '.join(landmarks) or 'aucun'}).",
        "Ajoutez <header>, <nav>, <main>, <footer> (SEO + accessibilité)." if l_score < 100
        else "Structure sémantique complète."))

    images = soup.find_all("img")
    with_alt = sum(1 for img in images if (img.get("alt") or "").strip())
    alt_score = 100.0 if not images else 100.0 * with_alt / len(images)
    factors.append(_factor(
        "Attributs alt des images", alt_score, 0.5,
        f"{with_alt}/{len(images)} images avec un alt renseigné.",
        "Renseignez l'attribut alt de chaque image." if alt_score < 100
        else "Toutes les images sont décrites."))

    # ---------- Axe data_quality ----------
    present = [k for k in ("json_ld", "microdata", "opengraph", "twitter")
               if (structured or {}).get(k)]
    dq_score = 100.0 * len(present) / 4
    factors.append(_factor(
        "Données structurées", dq_score, 2,
        f"Blocs présents : {', '.join(present) or 'aucun'} (JSON-LD, microdonnées, OpenGraph, Twitter).",
        "Ajoutez du JSON-LD (schema.org) et les balises OpenGraph/Twitter." if dq_score < 100
        else "Données structurées complètes."))
    json_ld_count = len((structured or {}).get("json_ld", []) or [])
    richness = min(100.0, 50.0 + 25.0 * json_ld_count) if json_ld_count else (50.0 if present else 0.0)
    factors.append(_factor(
        "Richesse des données structurées", richness, 1,
        f"{json_ld_count} objet(s) JSON-LD détecté(s).",
        "Décrivez l'entité principale en JSON-LD (Product, Article, Organization...)." if richness < 100
        else "Entités bien décrites."))

    # ---------- Agrégation ----------
    total_weight = sum(f["weight"] for f in factors)
    overall = (sum(f["score"] * f["weight"] for f in factors) / total_weight) if total_weight else 0.0

    axes: dict[str, float] = {}
    axis_of = {
        "Statut HTTP": "technical", "Temps de réponse": "technical", "Taille de la réponse": "technical",
        "Score SEO": "seo",
        "Volume de contenu": "content", "Ratio texte / HTML": "content",
        "Hiérarchie des titres": "structure", "Landmarks HTML5": "structure", "Attributs alt des images": "structure",
        "Données structurées": "data_quality", "Richesse des données structurées": "data_quality",
    }
    for axis in ("technical", "seo", "content", "structure", "data_quality"):
        axis_factors = [f for f in factors if axis_of[f["name"]] == axis]
        w = sum(f["weight"] for f in axis_factors)
        axes[axis] = round(sum(f["score"] * f["weight"] for f in axis_factors) / w, 1) if w else 0.0

    return {"overall": round(overall, 1), "axes": axes, "factors": factors}
