"""Routes UI v2.2 : données terrain par plante (formulaires HTML simples).

- ``/plantes/{plant_id}/terrain`` : tableau de bord « Données terrain »
  d'une plante — noms vernaculaires, usages traditionnels, échantillons /
  vouchers, chaîne extraction→extrait→fraction→composé, analyses
  phytochimiques, pharmacognosie. Formulaires HTML simples (pas de JSON).
- ``/recherche-terrain`` : recherche par nom (scientifique, synonyme,
  vernaculaire) avec motif de correspondance affiché.

Projet : celui de la session UI (``_project_for``) — cloisonnement
identique au reste de l'UI. Les usages traditionnels affichent
OBLIGATOIREMENT le bandeau « USAGE TRADITIONNEL RAPPORTÉ ».
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from webintel.api.deps import get_db
from webintel.database.models import ApiKey
from webintel.science import terrain as terrain_service
from webintel.science.models import (
    LANGUES_BURKINA,
    Compound,
    NiveauConfidentialite,
    Plant,
    StatutEpistemique,
    StatutIdentification,
    TechniqueAnalyse,
)
from webintel.ui.auth import ui_auth
from webintel.ui.routes import _project_for, _render

router = APIRouter()

_NIVEAUX = [(n.value, n.value) for n in NiveauConfidentialite]
_TECHNIQUES = [t.value for t in TechniqueAnalyse]
_STATUTS_ID = [s.value for s in StatutIdentification]
_STATUTS_EPI = [s.value for s in StatutEpistemique]
_LANGUES = sorted(LANGUES_BURKINA.items())


def _plante_ou_404(db: Session, plant_id: int) -> Plant:
    plante = db.get(Plant, plant_id)
    if plante is None:
        raise HTTPException(
            status_code=404, detail=f"Plante introuvable : id={plant_id}."
        )
    return plante


def _contexte_plante(db: Session, ui_key: ApiKey | None, plant_id: int) -> dict:
    """Contexte complet du tableau de bord terrain d'une plante."""
    from webintel.science.models import (
        AnalyseChimique,
        Echantillon,
        NomVernaculaire,
        Pharmacognosie,
        UsageTraditionnel,
        Voucher,
    )

    project = _project_for(db, ui_key)
    plante = _plante_ou_404(db, plant_id)
    pid = project.id
    noms = terrain_service.lister_noms_vernaculaires(db, plant_id, pid)
    usages = terrain_service.lister_usages_traditionnels(db, plant_id, pid)
    echantillons = terrain_service.lister_echantillons(db, plant_id, pid)
    vouchers = terrain_service.lister_vouchers(db, pid)
    ids_ech = [e.id for e in echantillons]
    analyses = (
        terrain_service.lister_analyses_chimiques(db, project_id=pid)
        if ids_ech
        else []
    )
    analyses = [
        a
        for a in analyses
        if a.echantillon_id in ids_ech
    ]
    pics_par_analyse = {
        a.id: terrain_service.lister_pics(db, a.id) for a in analyses
    }
    chaines = {}
    for echantillon in echantillons:
        try:
            chaines[echantillon.id] = terrain_service.chaine_echantillon(
                db, echantillon.id
            )
        except KeyError:
            continue
    pharmacognosie = terrain_service.lire_pharmacognosie(db, plant_id, pid)
    composes = db.query(Compound).order_by(Compound.name).limit(500).all()
    return {
        "project": project,
        "plante": plante,
        "noms": noms,
        "usages": usages,
        "echantillons": echantillons,
        "vouchers": vouchers,
        "analyses": analyses,
        "pics_par_analyse": pics_par_analyse,
        "chaines": chaines,
        "pharmacognosie": pharmacognosie,
        "composes": composes,
        "langues": _LANGUES,
        "niveaux": _NIVEAUX,
        "techniques": _TECHNIQUES,
        "statuts_id": _STATUTS_ID,
        "statuts_epi": _STATUTS_EPI,
        "bandeau_usage": terrain_service.BANDEAU_USAGE_TRADITIONNEL,
    }


def _redirige_terrain(plant_id: int) -> RedirectResponse:
    return RedirectResponse(
        url=f"/plantes/{plant_id}/terrain", status_code=303
    )


@router.get("/plantes/{plant_id}/terrain", response_class=HTMLResponse)
def terrain_plante(
    plant_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Tableau de bord « Données terrain » d'une plante."""
    ctx = _contexte_plante(db, ui_key, plant_id)
    return _render(
        "terrain_plante.html", ui_key=ui_key, active="plantes",
        erreur=None, **ctx,
    )


@router.post("/plantes/{plant_id}/terrain/noms")
async def terrain_ajouter_nom(
    plant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Ajoute un nom vernaculaire (formulaire HTML)."""
    project = _project_for(db, ui_key)
    _plante_ou_404(db, plant_id)
    form = await request.form()
    nom = (form.get("nom_original") or "").strip()
    langue = (form.get("langue") or "").strip() or None
    try:
        terrain_service.ajouter_nom_vernaculaire(
            db,
            plant_id=plant_id,
            project_id=project.id,
            nom_original=nom,
            langue=langue,
            region=(form.get("region") or "").strip() or None,
            localite=(form.get("localite") or "").strip() or None,
            source=(form.get("source") or "").strip() or None,
            cree_par=(form.get("cree_par") or "").strip() or None,
            niveau_confidentialite=(
                (form.get("niveau_confidentialite") or "projet").strip()
            ),
        )
    except ValueError as exc:
        ctx = _contexte_plante(db, ui_key, plant_id)
        return _render(
            "terrain_plante.html", ui_key=ui_key, active="plantes",
            erreur=str(exc), **ctx,
        )
    db.commit()
    return _redirige_terrain(plant_id)


@router.post("/plantes/{plant_id}/terrain/usages")
async def terrain_ajouter_usage(
    plant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Ajoute un usage traditionnel rapporté (formulaire HTML)."""
    project = _project_for(db, ui_key)
    _plante_ou_404(db, plant_id)
    form = await request.form()
    try:
        terrain_service.ajouter_usage_traditionnel(
            db,
            plant_id=plant_id,
            project_id=project.id,
            usage=(form.get("usage") or "").strip(),
            partie_utilisee=(form.get("partie_utilisee") or "").strip() or None,
            preparation=(form.get("preparation") or "").strip() or None,
            forme=(form.get("forme") or "").strip() or None,
            voie_administration=(
                (form.get("voie_administration") or "").strip() or None
            ),
            contexte=(form.get("contexte") or "").strip() or None,
            localite=(form.get("localite") or "").strip() or None,
            groupe_population=(
                (form.get("groupe_population") or "").strip() or None
            ),
            publication=(form.get("publication") or "").strip() or None,
            source=(form.get("source") or "").strip() or None,
            cree_par=(form.get("cree_par") or "").strip() or None,
            niveau_confidentialite=(
                (form.get("niveau_confidentialite") or "restreint").strip()
            ),
        )
    except ValueError as exc:
        ctx = _contexte_plante(db, ui_key, plant_id)
        return _render(
            "terrain_plante.html", ui_key=ui_key, active="plantes",
            erreur=str(exc), **ctx,
        )
    db.commit()
    return _redirige_terrain(plant_id)


@router.post("/plantes/{plant_id}/terrain/echantillons")
async def terrain_ajouter_echantillon(
    plant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Ajoute un échantillon de terrain (formulaire HTML)."""
    project = _project_for(db, ui_key)
    _plante_ou_404(db, plant_id)
    form = await request.form()

    def _flottant(cle: str):
        brut = (form.get(cle) or "").strip()
        if not brut:
            return None
        try:
            return float(brut.replace(",", "."))
        except ValueError:
            return None

    try:
        terrain_service.creer_echantillon(
            db,
            project_id=project.id,
            plant_id=plant_id,
            code_echantillon=(form.get("code_echantillon") or "").strip(),
            voucher_id=(
                int(form.get("voucher_id"))
                if (form.get("voucher_id") or "").strip() else None
            ),
            collecteur=(form.get("collecteur") or "").strip() or None,
            pays=(form.get("pays") or "").strip() or None,
            region=(form.get("region") or "").strip() or None,
            province=(form.get("province") or "").strip() or None,
            commune=(form.get("commune") or "").strip() or None,
            localite=(form.get("localite") or "").strip() or None,
            site=(form.get("site") or "").strip() or None,
            latitude=_flottant("latitude"),
            longitude=_flottant("longitude"),
            precision_gps=(
                (form.get("precision_gps") or "").strip() or None
            ),
            habitat=(form.get("habitat") or "").strip() or None,
            partie=(form.get("partie") or "").strip() or None,
            institution=(form.get("institution") or "").strip() or None,
            source=(form.get("source") or "").strip() or None,
            cree_par=(form.get("cree_par") or "").strip() or None,
            niveau_confidentialite=(
                (form.get("niveau_confidentialite") or "projet").strip()
            ),
        )
    except (ValueError, KeyError) as exc:
        ctx = _contexte_plante(db, ui_key, plant_id)
        return _render(
            "terrain_plante.html", ui_key=ui_key, active="plantes",
            erreur=str(exc), **ctx,
        )
    db.commit()
    return _redirige_terrain(plant_id)


@router.post("/plantes/{plant_id}/terrain/vouchers")
async def terrain_ajouter_voucher(
    plant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Ajoute un voucher d'herbier (formulaire HTML)."""
    project = _project_for(db, ui_key)
    _plante_ou_404(db, plant_id)
    form = await request.form()
    try:
        terrain_service.creer_voucher(
            db,
            project_id=project.id,
            code_voucher=(form.get("code_voucher") or "").strip(),
            institution=(form.get("institution") or "").strip() or None,
            code_herbier=(form.get("code_herbier") or "").strip() or None,
            source=(form.get("source") or "").strip() or None,
            cree_par=(form.get("cree_par") or "").strip() or None,
        )
    except ValueError as exc:
        ctx = _contexte_plante(db, ui_key, plant_id)
        return _render(
            "terrain_plante.html", ui_key=ui_key, active="plantes",
            erreur=str(exc), **ctx,
        )
    db.commit()
    return _redirige_terrain(plant_id)


@router.post("/plantes/{plant_id}/terrain/extractions")
async def terrain_ajouter_extraction(
    plant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Ajoute une extraction sur un échantillon (formulaire HTML)."""
    project = _project_for(db, ui_key)
    _plante_ou_404(db, plant_id)
    form = await request.form()
    try:
        echantillon_id = int(form.get("echantillon_id"))
    except (TypeError, ValueError):
        ctx = _contexte_plante(db, ui_key, plant_id)
        return _render(
            "terrain_plante.html", ui_key=ui_key, active="plantes",
            erreur="Choisissez un échantillon pour l'extraction.", **ctx,
        )
    try:
        terrain_service.creer_extraction(
            db,
            project_id=project.id,
            echantillon_id=echantillon_id,
            nom=(form.get("nom") or "").strip() or None,
            methode=(form.get("methode") or "").strip() or None,
            solvant=(form.get("solvant") or "").strip() or None,
            cree_par=(form.get("cree_par") or "").strip() or None,
        )
    except (ValueError, KeyError) as exc:
        ctx = _contexte_plante(db, ui_key, plant_id)
        return _render(
            "terrain_plante.html", ui_key=ui_key, active="plantes",
            erreur=str(exc), **ctx,
        )
    db.commit()
    return _redirige_terrain(plant_id)


@router.post("/plantes/{plant_id}/terrain/analyses")
async def terrain_ajouter_analyse(
    plant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Ajoute une analyse phytochimique (formulaire HTML)."""
    project = _project_for(db, ui_key)
    _plante_ou_404(db, plant_id)
    form = await request.form()

    def _entier(cle: str):
        brut = (form.get(cle) or "").strip()
        return int(brut) if brut else None

    try:
        terrain_service.creer_analyse_chimique(
            db,
            project_id=project.id,
            methode=(form.get("methode") or "").strip(),
            echantillon_id=_entier("echantillon_id"),
            extrait_id=_entier("extrait_id"),
            fraction_id=_entier("fraction_id"),
            instrument=(form.get("instrument") or "").strip() or None,
            laboratoire=(form.get("laboratoire") or "").strip() or None,
            operateur=(form.get("operateur") or "").strip() or None,
            cree_par=(form.get("cree_par") or "").strip() or None,
            niveau_confidentialite=(
                (form.get("niveau_confidentialite") or "projet").strip()
            ),
        )
    except (ValueError, KeyError) as exc:
        ctx = _contexte_plante(db, ui_key, plant_id)
        return _render(
            "terrain_plante.html", ui_key=ui_key, active="plantes",
            erreur=str(exc), **ctx,
        )
    db.commit()
    return _redirige_terrain(plant_id)


@router.post("/plantes/{plant_id}/terrain/pharmacognosie")
async def terrain_pharmacognosie(
    plant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Crée / met à jour la fiche pharmacognosie (formulaire HTML)."""
    project = _project_for(db, ui_key)
    _plante_ou_404(db, plant_id)
    form = await request.form()
    champs = {}
    for cle in (
        "identification_macro", "identification_micro", "organoleptique",
        "partie_utilisee", "authentification", "substitution_adulteration",
        "sechage", "stockage", "preparation",
    ):
        champs[cle] = (form.get(cle) or "").strip() or None
    champs["source"] = (form.get("source") or "").strip() or None
    champs["cree_par"] = (form.get("cree_par") or "").strip() or None
    try:
        terrain_service.definir_pharmacognosie(
            db, plant_id, project.id, **champs
        )
    except PermissionError as exc:
        # P0 sécurité : la fiche appartient à un autre projet — erreur
        # affichée en page, jamais d'écrasement silencieux.
        ctx = _contexte_plante(db, ui_key, plant_id)
        return _render(
            "terrain_plante.html", ui_key=ui_key, active="plantes",
            erreur=str(exc), **ctx,
        )
    except (ValueError, KeyError) as exc:
        ctx = _contexte_plante(db, ui_key, plant_id)
        return _render(
            "terrain_plante.html", ui_key=ui_key, active="plantes",
            erreur=str(exc), **ctx,
        )
    db.commit()
    return _redirige_terrain(plant_id)


@router.get("/recherche-terrain", response_class=HTMLResponse)
def recherche_terrain(
    q: str | None = None,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Recherche par nom avec motif de correspondance affiché.

    « neem », « karité »... sont trouvés via les noms vernaculaires
    enregistrés (motif ``vernaculaire``).
    """
    project = _project_for(db, ui_key)
    resultats = []
    if (q or "").strip():
        resultats = terrain_service.rechercher_plantes(
            db, q.strip(), project_id=project.id
        )
    libelles_motif = {
        "exact": "Nom exact",
        "normalise": "Nom normalisé",
        "synonyme": "Synonyme",
        "vernaculaire": "Nom vernaculaire",
        "partiel": "Correspondance partielle",
    }
    return _render(
        "terrain_recherche.html",
        ui_key=ui_key,
        active="plantes",
        project=project,
        q=q or "",
        resultats=resultats,
        libelles_motif=libelles_motif,
    )
