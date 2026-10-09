"""Tests du durcissement web (chantier robustesse — P1-2/P1-3 de l'audit).

- En-têtes de sécurité présents sur chaque réponse (HTML, JSON, fichiers).
- Content-Security-Policy : `script-src 'self'` STRICT (aucun JS inline
  dans les templates — les comportements UI vivent dans `ui/static/js/`).
- Non-régression XSS : un nom de plante malveillant est échappé dans
  les pages HTML.
- Pas de redirection ouverte : toutes les redirections de l'application
  pointent vers des chemins internes relatifs.
- Les exécutables scientifiques sont lancés SANS shell (argv en liste).
"""

from __future__ import annotations

import re
import subprocess

import pytest

from webintel.database import models
from webintel.science.models import Plant


# ---------------------------------------------------------------------------
# En-têtes de sécurité
# ---------------------------------------------------------------------------

EN_TETES_ATTENDUS = {
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
}


@pytest.mark.parametrize("chemin", ["/", "/api/health", "/plantes"])
def test_en_tetes_securite_sur_reponses_html_et_json(client, chemin):
    r = client.get(chemin)
    assert r.status_code in (200, 303, 307)
    for nom in EN_TETES_ATTENDUS:
        assert nom in r.headers, f"en-tête manquant : {nom}"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_en_tetes_securite_sur_telechargement(client, db_session):
    # StreamingResponse (export CSV) : mêmes en-têtes.
    projet = models.Project(name="Projet Headers")
    db_session.add(projet)
    db_session.commit()
    r = client.get(f"/api/export/csv/entites?project_id={projet.id}")
    assert r.status_code in (200, 404)
    for nom in EN_TETES_ATTENDUS:
        assert nom in r.headers, f"en-tête manquant : {nom}"


def test_csp_script_src_self_strict(client):
    r = client.get("/")
    csp = r.headers["Content-Security-Policy"]
    # script-src STRICTEMENT 'self' : aucun 'unsafe-inline' pour les scripts.
    m = re.search(r"script-src\s+([^;]+)", csp)
    assert m, "directive script-src absente"
    assert "'self'" in m.group(1)
    assert "unsafe-inline" not in m.group(1)
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "form-action 'self'" in csp


def test_hsts_absent_en_mode_local(client, server_bootstrap):
    # Mode local (HTTP) : pas de HSTS (il bloquerait l'accès localhost).
    # En mode serveur ce test n'a pas de sens (HSTS présent, voir
    # test_hsts_present_en_mode_serveur).
    if server_bootstrap is not None:
        pytest.skip("mode local uniquement")
    r = client.get("/")
    assert "Strict-Transport-Security" not in r.headers


def test_pas_de_js_inline_ni_handlers_dans_les_templates():
    import pathlib

    templates = pathlib.Path("webintel/ui/templates")
    assert templates.is_dir()
    for gabarit in templates.glob("*.html"):
        contenu = gabarit.read_text(encoding="utf-8")
        assert "<script>" not in contenu, f"JS inline dans {gabarit.name}"
        assert "<script " not in contenu or 'src="/static/js/' in contenu, (
            f"<script> non local dans {gabarit.name}"
        )
        assert "<style>" not in contenu and "<style " not in contenu, (
            f"<style> inline dans {gabarit.name}"
        )
        for handler in ("onclick=", "onsubmit=", "onchange=", "onload="):
            assert handler not in contenu, f"{handler} inline dans {gabarit.name}"


# ---------------------------------------------------------------------------
# XSS : échappement HTML
# ---------------------------------------------------------------------------


def test_xss_nom_plante_echappe_dans_liste(client, db_session):
    payload = "<script>alert(1)</script>"
    db_session.add(Plant(scientific_name=payload, family="X"))
    db_session.commit()
    r = client.get("/plantes")
    assert r.status_code == 200
    assert payload not in r.text
    assert "&lt;script&gt;" in r.text


def test_xss_recherche_echappee(client):
    payload = "<img src=x onerror=alert(1)>"
    r = client.get(f"/plantes?q={payload}")
    assert r.status_code == 200
    assert payload not in r.text


# ---------------------------------------------------------------------------
# Redirections ouvertes
# ---------------------------------------------------------------------------


def test_aucune_redirection_ouverte(client):
    # L'application ne doit jamais rediriger vers une URL fournie par
    # l'utilisateur (vecteur d'hameçonnage). Vérification statique des
    # cibles de RedirectResponse dans les routes UI/API.
    import pathlib

    for racine in ("webintel/ui", "webintel/api"):
        for fichier in pathlib.Path(racine).rglob("*.py"):
            contenu = fichier.read_text(encoding="utf-8")
            for m in re.finditer(r"RedirectResponse\s*\(\s*url\s*=\s*([^,)]+)", contenu):
                cible = m.group(1).strip()
                # Chemins internes uniquement : "/", '/...', f"/...", f'/...'
                assert re.match(r"""^f?["']/""", cible), (
                    f"redirection non interne dans {fichier}:{cible}"
                )


# ---------------------------------------------------------------------------
# Sous-processus scientifiques : jamais de shell
# ---------------------------------------------------------------------------


def test_docking_adaptateur_sans_shell(monkeypatch):
    """L'exécutable Vina est lancé via argv (liste), shell=False, même avec
    un chemin de binaire malveillant (injection de commande impossible).

    Non-régression statique (aucun shell=True / os.system dans le module)
    + dynamique (argv vérifié via monkeypatch de subprocess.run).
    """
    import pathlib

    from webintel.simulations.plugins import docking_adapter

    source = pathlib.Path(docking_adapter.__file__).read_text(encoding="utf-8")
    assert not _appels_shell_true(source), "shell=True dans docking_adapter.py"
    assert "os.system(" not in source

    appels = []

    def faux_run(*args, **kwargs):
        appels.append((args, kwargs))

        class _Proc:
            returncode = 0
            stdout = "affinity: -9.9 (kcal/mol)"
            stderr = ""

        return _Proc()

    # Binaire au chemin malveillant : doit rester UN argument argv.
    binaire_malveillant = "/tmp/vina; rm -rf / #.bin"
    monkeypatch.setattr(
        docking_adapter,
        "diagnostic_vina",
        lambda: {"disponible": True, "binaire": binaire_malveillant, "raison": ""},
    )
    monkeypatch.setattr(subprocess, "run", faux_run)
    try:
        docking_adapter.run(
            inputs={
                "recepteur_pdbqt": "REMARK recepteur factice\n",
                "ligand_pdbqt": "REMARK ligand factice\n",
            },
            params={
                "centre_boite": [0.0, 0.0, 0.0],
                "taille_boite": [20.0, 20.0, 20.0],
            },
        )
    except (RuntimeError, ValueError):
        pass  # parsing de sortie factice : seul l'appel compte ici
    assert appels, "subprocess.run n'a pas été appelé"
    for args, kwargs in appels:
        assert kwargs.get("shell", False) is False, "shell=True interdit"
        argv = args[0]
        assert isinstance(argv, (list, tuple)), "argv doit être une liste"
        assert argv[0] == binaire_malveillant, (
            "le binaire malveillant doit rester un seul argument argv"
        )


def _appels_shell_true(source: str) -> list[int]:
    """Retourne les numéros de ligne des appels avec `shell=True` (AST).

    Insensible aux docstrings/commentaires qui mentionnent « shell=True »
    pour l'interdire : seul un VRAI argument `shell=True` est signalé.
    """
    import ast

    lignes = []
    try:
        arbre = ast.parse(source)
    except SyntaxError:
        return [-1]
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Call):
            for mot_cle in noeud.keywords:
                if mot_cle.arg == "shell" and isinstance(mot_cle.value, ast.Constant):
                    if mot_cle.value.value is True:
                        lignes.append(noeud.lineno)
    return lignes


def test_aucun_shell_true_dans_le_code():
    """Balayage statique (AST) : aucun appel shell dans tout le backend."""
    import pathlib

    fautifs = []
    for fichier in pathlib.Path("webintel").rglob("*.py"):
        if "__pycache__" in str(fichier):
            continue
        lignes = _appels_shell_true(fichier.read_text(encoding="utf-8"))
        if lignes:
            fautifs.append(f"{fichier}:{lignes}")
        if "os.system(" in fichier.read_text(encoding="utf-8"):
            fautifs.append(f"{fichier}:os.system")
    assert not fautifs, f"appels shell détectés : {fautifs}"


def test_hsts_present_en_mode_serveur(client, server_bootstrap):
    # Mode serveur (REQUIRE_API_KEY=true, déploiement TLS documenté) :
    # HSTS envoyé. En mode local ce test est ignoré (voir
    # test_hsts_absent_en_mode_local).
    if server_bootstrap is None:
        pytest.skip("mode serveur uniquement")
    r = client.get("/")
    hsts = r.headers.get("Strict-Transport-Security", "")
    assert hsts.startswith("max-age=31536000"), hsts
