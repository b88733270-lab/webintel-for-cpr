"""Routes UI v2.1 — combinaisons, synergie, docking, rapport (100 % français).

Pages (toutes sous le routeur UI ``/``) :

- ``/combinaisons`` : études + analyses de synergie + jobs docking, avec les
  4 actions chercheur (« Nouvelle étude », « Nouvelle simulation »,
  « Importer mes résultats », « Exporter mon dossier ») ;
- ``/combinaisons/nouvelle`` : formulaire de création d'étude ;
- ``/combinaisons/{id}`` : dossier en 12 points ;
- ``/synergie/importer`` : import de résultats expérimentaux (JSON) ;
- ``/synergie/{id}`` : résultat d'analyse (formule, graphe SVG) ;
- ``/docking`` : workflow docking (aperçu / exécution) ;
- ``/docking/{id}`` : détail d'un job ;
- ``/rapport`` : page d'export du dossier de recherche ;
- ``/rapport/telecharger`` : téléchargement (json/markdown/html/pdf/zip).

Discipline : aucun texte de l'UI ne qualifie un résultat d'« efficace »,
« sûr » ou « prouvé » — les garde-fous backend lèveraient une erreur.
"""

from __future__ import annotations

import io
import json
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from webintel.api.deps import get_db
from webintel.database.models import ApiKey
from webintel.science import combinaisons as comb_module
from webintel.science import docking as dock_module
from webintel.science import rapport as rapport_module
from webintel.science.models import Compound, Plant
from webintel.science.synergie import METHODES, DonneesInsuffisantes
from webintel.ui.auth import ui_auth
from webintel.ui.routes import _project_for, _render

router = APIRouter()


def _identite_ui(ui_key: ApiKey | None) -> str:
    if ui_key is None:
        return "ui-local"
    return f"ui:{getattr(ui_key, 'prefix', None) or '?'}"


def _contexte_combinaisons(ui_key, project, db, erreur=None, message=None):
    etudes = comb_module.list_etudes(db, project.id)
    analyses = comb_module.list_analyses_synergie(db, project.id,
                                                  inclure_prive=True)
    jobs = dock_module.list_docking_jobs(db, project.id)
    return dict(
        ui_key=ui_key,
        active="combinaisons",
        project=project,
        etudes=etudes,
        analyses=analyses,
        jobs=jobs,
        erreur=erreur,
        message=message,
    )


@router.get("/combinaisons", response_class=HTMLResponse)
def combinaisons_page(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Études de combinaison, analyses de synergie, jobs docking + actions."""
    project = _project_for(db, ui_key)
    return _render("combinaisons.html",
                   **_contexte_combinaisons(ui_key, project, db))


@router.get("/combinaisons/nouvelle", response_class=HTMLResponse)
def combinaison_nouvelle_form(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Formulaire « Nouvelle étude » (MODE A : screening structural)."""
    project = _project_for(db, ui_key)
    composes = db.query(Compound).order_by(Compound.name).all()
    plantes = db.query(Plant).order_by(Plant.scientific_name).all()
    return _render(
        "combinaison_nouvelle.html",
        ui_key=ui_key, active="combinaisons", project=project,
        composes=composes, plantes=plantes, erreur=None, form={},
    )


@router.post("/combinaisons/creer", response_class=HTMLResponse)
async def combinaison_creer(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Crée une étude de combinaison puis redirige vers son dossier."""
    project = _project_for(db, ui_key)
    form = await request.form()
    nom = (form.get("nom") or "").strip() or "Étude sans nom"
    ids = [int(v) for v in form.getlist("compound_ids") if str(v).isdigit()]
    try:
        plant_id = int(form.get("plant_id") or 0) or None
    except (TypeError, ValueError):
        plant_id = None
    try:
        etude = comb_module.creer_etude(
            db, project_id=project.id, nom=nom, compound_ids=ids,
            plant_id=plant_id,
            plant_part=(form.get("plant_part") or "").strip() or None,
            target=(form.get("target") or "").strip() or None,
            description=(form.get("description") or "").strip() or None,
            cree_par=_identite_ui(ui_key),
        )
    except (ValueError, RuntimeError) as exc:
        db.rollback()
        composes = db.query(Compound).order_by(Compound.name).all()
        plantes = db.query(Plant).order_by(Plant.scientific_name).all()
        return _render(
            "combinaison_nouvelle.html",
            ui_key=ui_key, active="combinaisons", project=project,
            composes=composes, plantes=plantes,
            erreur=str(exc), form=dict(form),
        )
    db.commit()
    return RedirectResponse(url=f"/combinaisons/{etude.id}", status_code=303)


@router.get("/combinaisons/{etude_id}", response_class=HTMLResponse)
def combinaison_detail(
    request: Request,
    etude_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Dossier d'étude : les 12 points + provenance."""
    project = _project_for(db, ui_key)
    try:
        etude = comb_module.get_etude(db, etude_id)
    except KeyError:
        raise HTTPException(status_code=404,
                            detail="Étude de combinaison introuvable.")
    if etude.project_id != project.id:
        raise HTTPException(status_code=404,
                            detail="Étude de combinaison introuvable.")
    return _render(
        "combinaison_detail.html",
        ui_key=ui_key, active="combinaisons", project=project,
        etude=etude, sections=(etude.resultats or {}).get("sections", []),
    )


# ---------------------------------------------------------------------------
# Synergie — import de résultats expérimentaux (MODE B)
# ---------------------------------------------------------------------------

@router.get("/synergie/importer", response_class=HTMLResponse)
def synergie_importer_form(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Formulaire « Importer mes résultats » (données expérimentales)."""
    project = _project_for(db, ui_key)
    return _render(
        "synergie_importer.html",
        ui_key=ui_key, active="combinaisons", project=project,
        methodes=sorted(METHODES), erreur=None, form={},
    )


@router.post("/synergie/importer", response_class=HTMLResponse)
async def synergie_importer(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Analyse une série expérimentale fournie par le laboratoire."""
    project = _project_for(db, ui_key)
    form = await request.form()
    nom = (form.get("nom") or "").strip() or "Analyse sans nom"
    methode = (form.get("methode") or "").strip().lower()
    brut = (form.get("donnees") or "").strip()
    is_private = bool(form.get("is_private"))
    erreur = None
    try:
        donnees = json.loads(brut) if brut else {}
    except json.JSONDecodeError as exc:
        donnees, erreur = None, f"JSON invalide : {exc}."
    analyse = None
    if erreur is None:
        try:
            analyse = comb_module.creer_analyse_synergie(
                db, project_id=project.id, nom=nom, methode=methode,
                donnees=donnees, is_private=is_private,
                cree_par=_identite_ui(ui_key),
            )
        except (ValueError, DonneesInsuffisantes, RuntimeError) as exc:
            db.rollback()
            erreur = str(exc)
    if erreur is not None:
        return _render(
            "synergie_importer.html",
            ui_key=ui_key, active="combinaisons", project=project,
            methodes=sorted(METHODES), erreur=erreur, form=dict(form),
        )
    db.commit()
    return RedirectResponse(url=f"/synergie/{analyse.id}", status_code=303)


@router.get("/synergie/{analyse_id}", response_class=HTMLResponse)
def synergie_detail(
    request: Request,
    analyse_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Résultat d'analyse : formule, paramètres, graphe SVG, limites."""
    project = _project_for(db, ui_key)
    try:
        analyse = comb_module.get_analyse_synergie(db, analyse_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Analyse introuvable.")
    if analyse.project_id != project.id:
        raise HTTPException(status_code=404, detail="Analyse introuvable.")
    return _render(
        "synergie_detail.html",
        ui_key=ui_key, active="combinaisons", project=project,
        analyse=analyse, resultat=analyse.resultats or {},
    )


# ---------------------------------------------------------------------------
# Docking — workflow en 9 étapes
# ---------------------------------------------------------------------------

@router.get("/docking", response_class=HTMLResponse)
def docking_page(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Workflow docking : formulaire + jobs du projet."""
    project = _project_for(db, ui_key)
    disponible, raison = dock_module.statut_moteur()
    jobs = dock_module.list_docking_jobs(db, project.id)
    return _render(
        "docking.html",
        ui_key=ui_key, active="combinaisons", project=project,
        moteur_disponible=disponible, moteur_raison=raison,
        jobs=jobs, apercu=None, erreur=None, form={},
    )


@router.post("/docking", response_class=HTMLResponse)
async def docking_lancer(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Aperçu (sans calcul) ou exécution réelle du workflow docking."""
    project = _project_for(db, ui_key)
    form = await request.form()
    action = (form.get("action") or "apercu").strip()
    entrees = {
        "recepteur_pdb": (form.get("recepteur_pdb") or ""),
        "ligand_smiles": (form.get("ligand_smiles") or "").strip(),
        "recepteur_nom": (form.get("recepteur_nom") or "").strip() or None,
        "ligand_nom": (form.get("ligand_nom") or "").strip() or None,
    }
    try:
        params = {
            "centre_boite": [float(form.get(f"cx") or 0),
                             float(form.get(f"cy") or 0),
                             float(form.get(f"cz") or 0)],
            "taille_boite": [float(form.get("taille") or 20)] * 3,
            "exhaustiveness": int(form.get("exhaustiveness") or 8),
            "nb_poses": int(form.get("nb_poses") or 9),
        }
    except (TypeError, ValueError):
        params = {}

    def _reafficher(erreur=None, apercu=None):
        disponible, raison = dock_module.statut_moteur()
        jobs = dock_module.list_docking_jobs(db, project.id)
        return _render(
            "docking.html",
            ui_key=ui_key, active="combinaisons", project=project,
            moteur_disponible=disponible, moteur_raison=raison,
            jobs=jobs, apercu=apercu, erreur=erreur, form=dict(form),
        )

    if action == "apercu":
        try:
            apercu = dock_module.apercu_workflow(
                recepteur_pdb=entrees["recepteur_pdb"],
                ligand_smiles=entrees["ligand_smiles"],
                parametres=params,
                recepteur_nom=entrees["recepteur_nom"],
                ligand_nom=entrees["ligand_nom"],
            )
        except ValueError as exc:
            return _reafficher(erreur=str(exc))
        return _reafficher(apercu=apercu)

    # Exécution réelle (ou refus propre si le moteur est indisponible).
    nom = (form.get("nom") or "").strip() or "Docking sans nom"
    try:
        job = dock_module.executer_docking(
            db, project_id=project.id, nom=nom,
            recepteur_pdb=entrees["recepteur_pdb"],
            ligand_smiles=entrees["ligand_smiles"],
            parametres=params,
            recepteur_nom=entrees["recepteur_nom"],
            ligand_nom=entrees["ligand_nom"],
            execute_par=_identite_ui(ui_key),
        )
    except dock_module.MoteurIndisponible as exc:
        db.commit()  # persiste le job « refuse » pour l'audit
        return _reafficher(erreur=str(exc))
    except ValueError as exc:
        db.rollback()
        return _reafficher(erreur=str(exc))
    db.commit()
    return RedirectResponse(url=f"/docking/{job.id}", status_code=303)


@router.get("/docking/{job_id}", response_class=HTMLResponse)
def docking_detail(
    request: Request,
    job_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Détail d'un job de docking : paramètres, méthode, résultats."""
    project = _project_for(db, ui_key)
    try:
        job = dock_module.get_docking_job(db, job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job introuvable.")
    if job.project_id != project.id:
        raise HTTPException(status_code=404, detail="Job introuvable.")
    return _render(
        "docking_detail.html",
        ui_key=ui_key, active="combinaisons", project=project, job=job,
    )


# ---------------------------------------------------------------------------
# Rapport — « Exporter mon dossier »
# ---------------------------------------------------------------------------

@router.get("/rapport", response_class=HTMLResponse)
def rapport_page(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Page d'export du dossier de recherche du projet."""
    project = _project_for(db, ui_key)
    rapport = rapport_module.build_rapport_recherche(db, project.id)
    return _render(
        "rapport.html",
        ui_key=ui_key, active="combinaisons", project=project,
        rapport=rapport, erreur=None,
    )


@router.get("/rapport/telecharger")
def rapport_telecharger(
    request: Request,
    format: str = "markdown",
    inclure_prive: str = "",
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Télécharge le dossier de recherche (json/markdown/html/pdf/zip)."""
    project = _project_for(db, ui_key)
    prive = inclure_prive.lower() in ("1", "true", "oui", "on")
    fmt = (format or "markdown").lower()
    if fmt == "zip":
        donnees = rapport_module.build_zip_recherche(
            db, project.id, inclure_prive=prive)
        return StreamingResponse(
            io.BytesIO(donnees), media_type="application/zip",
            headers={"Content-Disposition":
                     f"attachment; filename=dossier-recherche-p{project.id}.zip"})
    rapport = rapport_module.build_rapport_recherche(
        db, project.id, inclure_prive=prive)
    if fmt == "json":
        corps = json.dumps(rapport, ensure_ascii=False, indent=2).encode()
        media, ext = "application/json", "json"
    elif fmt == "html":
        corps = rapport_module.render_html(rapport).encode()
        media, ext = "text/html", "html"
    elif fmt == "pdf":
        corps = rapport_module.build_pdf(rapport) or b""
        media, ext = "application/pdf", "pdf"
    else:
        corps = rapport_module.render_markdown(rapport).encode()
        media, ext = "text/markdown", "md"
    return StreamingResponse(
        io.BytesIO(corps), media_type=media,
        headers={"Content-Disposition":
                 f"attachment; filename=rapport-recherche-p{project.id}.{ext}"})
