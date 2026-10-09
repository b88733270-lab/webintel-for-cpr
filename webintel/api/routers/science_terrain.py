"""Routeur API v2.2 : terrain, ethnobotanique, phytochimie, pharmacognosie.

Tous les endpoints exigent une clé API (``api_key_auth``) et sont
cloisonnés au projet via ``scoped_project_id`` /
``require_project_access_strict`` (même pattern que
``routers/science.py`` pour les données de laboratoire).

Règles de confidentialité :

- les lectures API retournent les enregistrements non supprimés du
  projet autorisé (principe de confiance du laboratoire) ;
- les niveaux de confidentialité (``NiveauConfidentialite``) s'appliquent
  aux EXPORTS : ``GET /science/terrain/export-public/...`` n'inclut QUE
  les enregistrements ``public`` et ne divulgue JAMAIS les coordonnées
  GPS précises ni les identités (voir
  ``webintel.science.terrain.export_public_plante``) ;
- les suppressions sont LOGIQUES (``supprime_le`` renseigné) : la ligne
  reste en base, tracée (qui, quand), et disparaît des listes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    require_project_access_strict,
    scoped_project_id,
)
from webintel.database.models import ApiKey, Project
from webintel.science import terrain as terrain_service
from webintel.science.models import (
    AnalyseChimique,
    CompoundTarget,
    Echantillon,
    Extraction,
    Extrait,
    Fraction,
    NiveauConfidentialite,
    NomVernaculaire,
    Pharmacognosie,
    PicChromatographique,
    PlantActivity,
    StatutEpistemique,
    UsageTraditionnel,
    Voucher,
)
from webintel.science.schemas import (
    AnalyseChimiqueCreate,
    AnalyseChimiqueOut,
    EchantillonCreate,
    EchantillonOut,
    ExtractionCreate,
    ExtractionOut,
    ExtraitCreate,
    ExtraitOut,
    FractionComposeCreate,
    FractionComposeOut,
    FractionCreate,
    FractionOut,
    NomVernaculaireCreate,
    NomVernaculaireOut,
    PharmacognosieCreate,
    PharmacognosieOut,
    PicChromatographiqueCreate,
    PicChromatographiqueOut,
    RecherchePlanteOut,
    StatutEpistemiquePatch,
    UsageTraditionnelCreate,
    UsageTraditionnelOut,
    VoucherCreate,
    VoucherOut,
)

router = APIRouter()

_NIVEAUX_CONFIDENTIALITE = {n.value for n in NiveauConfidentialite}
_STATUTS_EPISTEMIQUES = {s.value for s in StatutEpistemique}


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------


def _valider_confidentialite(valeur: str | None) -> str:
    """Valide un niveau de confidentialité (défaut : "projet")."""
    niveau = (valeur or "projet").strip().lower()
    if niveau not in _NIVEAUX_CONFIDENTIALITE:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Niveau de confidentialité invalide : {valeur!r} "
                f"(attendu : {', '.join(sorted(_NIVEAUX_CONFIDENTIALITE))})."
            ),
        )
    return niveau


def _projet_requis(api_key: ApiKey | None, project_id: int | None) -> int:
    """Résout le projet effectif (cloisonnement par défaut sur la clé)."""
    effectif = scoped_project_id(api_key, project_id)
    if effectif is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Le champ project_id est requis : les données de terrain "
                "sont toujours rattachées à un projet."
            ),
        )
    require_project_access_strict(api_key, effectif)
    return effectif


def _objet_ou_404(db: Session, modele, objet_id: int, nom: str):
    """Charge un objet actif ou lève une 404 (message FR)."""
    objet = db.get(modele, objet_id)
    if objet is None or getattr(objet, "supprime_le", None) is not None:
        raise HTTPException(
            status_code=404,
            detail=f"{nom} introuvable : id={objet_id}.",
        )
    return objet


def _verifier_projet(
    api_key: ApiKey | None, objet, nom: str = "Cet enregistrement"
) -> None:
    """Contrôle strict : l'objet appartient au projet autorisé."""
    require_project_access_strict(api_key, objet.project_id)


def _preparer_creation(payload, api_key: ApiKey | None) -> tuple[dict, int]:
    """Extrait project_id (requis) + champs, valide la confidentialité.

    Le niveau de confidentialité par défaut est celui du schéma
    (``restreint`` pour les usages traditionnels — données d'enquête
    sensibles —, ``projet`` sinon) : ``exclude_unset`` ne doit pas le
    faire retomber sur le défaut SQL ``projet``.
    """
    data = payload.model_dump(exclude_unset=True)
    project_id = _projet_requis(api_key, data.pop("project_id", None))
    if "niveau_confidentialite" in data:
        data["niveau_confidentialite"] = _valider_confidentialite(
            data["niveau_confidentialite"]
        )
    else:
        defaut = type(payload).model_fields["niveau_confidentialite"].default
        data["niveau_confidentialite"] = _valider_confidentialite(defaut)
    return data, project_id


# ---------------------------------------------------------------------------
# Noms vernaculaires
# ---------------------------------------------------------------------------


@router.post(
    "/science/terrain/noms-vernaculaires",
    response_model=NomVernaculaireOut,
    status_code=201,
)
def creer_nom_vernaculaire(
    payload: NomVernaculaireCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> NomVernaculaireOut:
    """Enregistre un nom vernaculaire (idempotent : 201 créé, 200 existant).

    La langue n'est JAMAIS devinée : absente -> "non renseignée".
    """
    data, project_id = _preparer_creation(payload, api_key)
    try:
        objet, cree = terrain_service.ajouter_nom_vernaculaire(
            db, project_id=project_id, **data
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(objet)
    return NomVernaculaireOut.model_validate(objet)


@router.get(
    "/science/terrain/plantes/{plant_id}/noms-vernaculaires",
    response_model=list[NomVernaculaireOut],
)
def lister_noms_vernaculaires(
    plant_id: int,
    project_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> list[NomVernaculaireOut]:
    """Noms vernaculaires actifs d'une plante (projet autorisé, paginé)."""
    effectif = _projet_requis(api_key, project_id)
    objets = terrain_service.lister_noms_vernaculaires(
        db, plant_id, project_id=effectif, limit=limit, offset=offset
    )
    return [NomVernaculaireOut.model_validate(o) for o in objets]


@router.delete("/science/terrain/noms-vernaculaires/{nom_id}")
def supprimer_nom_vernaculaire(
    nom_id: int,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Suppression LOGIQUE d'un nom vernaculaire (tracée, jamais silencieuse)."""
    objet = _objet_ou_404(db, NomVernaculaire, nom_id, "Nom vernaculaire")
    _verifier_projet(api_key, objet)
    terrain_service.supprimer_doucement(db, objet)
    db.commit()
    return {"detail": "Nom vernaculaire supprimé (suppression logique tracée)."}


# ---------------------------------------------------------------------------
# Usages traditionnels (ethnobotanique)
# ---------------------------------------------------------------------------


@router.post(
    "/science/terrain/usages",
    response_model=UsageTraditionnelOut,
    status_code=201,
)
def creer_usage_traditionnel(
    payload: UsageTraditionnelCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> UsageTraditionnelOut:
    """Enregistre un usage traditionnel RAPPORTÉ (toujours créé).

    Le texte est conservé verbatim — jamais reformulé en allégation
    d'efficacité. Confidentialité par défaut : "restreint".
    """
    data, project_id = _preparer_creation(payload, api_key)
    try:
        objet = terrain_service.ajouter_usage_traditionnel(
            db, project_id=project_id, **data
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(objet)
    return UsageTraditionnelOut.model_validate(objet)


@router.get(
    "/science/terrain/plantes/{plant_id}/usages",
    response_model=list[UsageTraditionnelOut],
)
def lister_usages(
    plant_id: int,
    project_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> list[UsageTraditionnelOut]:
    """Usages traditionnels actifs d'une plante (projet autorisé, paginé)."""
    effectif = _projet_requis(api_key, project_id)
    objets = terrain_service.lister_usages_traditionnels(
        db, plant_id, project_id=effectif, limit=limit, offset=offset
    )
    return [UsageTraditionnelOut.model_validate(o) for o in objets]


@router.delete("/science/terrain/usages/{usage_id}")
def supprimer_usage(
    usage_id: int,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Suppression LOGIQUE d'un usage traditionnel (tracée)."""
    objet = _objet_ou_404(db, UsageTraditionnel, usage_id, "Usage traditionnel")
    _verifier_projet(api_key, objet)
    terrain_service.supprimer_doucement(db, objet)
    db.commit()
    return {"detail": "Usage traditionnel supprimé (suppression logique tracée)."}


# ---------------------------------------------------------------------------
# Échantillons & vouchers
# ---------------------------------------------------------------------------


@router.post(
    "/science/terrain/echantillons",
    response_model=EchantillonOut,
    status_code=201,
)
def creer_echantillon(
    payload: EchantillonCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> EchantillonOut:
    """Crée un échantillon de terrain (idempotent sur le code, par projet).

    Le GPS n'est jamais inventé : coordonnées absentes -> nulles.
    """
    data, project_id = _preparer_creation(payload, api_key)
    if data.get("voucher_id") is not None:
        voucher = _objet_ou_404(db, Voucher, data["voucher_id"], "Voucher")
        _verifier_projet(api_key, voucher, "Ce voucher")
    try:
        objet, _cree = terrain_service.creer_echantillon(
            db, project_id=project_id, **data
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(objet)
    return EchantillonOut.model_validate(objet)


@router.get(
    "/science/terrain/plantes/{plant_id}/echantillons",
    response_model=list[EchantillonOut],
)
def lister_echantillons(
    plant_id: int,
    project_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> list[EchantillonOut]:
    """Échantillons actifs d'une plante (projet autorisé, paginé)."""
    effectif = _projet_requis(api_key, project_id)
    objets = terrain_service.lister_echantillons(
        db, plant_id, project_id=effectif, limit=limit, offset=offset
    )
    return [EchantillonOut.model_validate(o) for o in objets]


@router.get(
    "/science/terrain/echantillons/{echantillon_id}",
    response_model=EchantillonOut,
)
def lire_echantillon(
    echantillon_id: int,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> EchantillonOut:
    """Détail d'un échantillon (projet autorisé)."""
    objet = _objet_ou_404(db, Echantillon, echantillon_id, "Échantillon")
    _verifier_projet(api_key, objet)
    return EchantillonOut.model_validate(objet)


@router.get("/science/terrain/echantillons/{echantillon_id}/chaine")
def lire_chaine_echantillon(
    echantillon_id: int,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Chaîne complète : échantillon → voucher → extractions → extraits →
    fractions → composés (projet autorisé)."""
    objet = _objet_ou_404(db, Echantillon, echantillon_id, "Échantillon")
    _verifier_projet(api_key, objet)
    try:
        chaine = terrain_service.chaine_echantillon(db, echantillon_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "echantillon": EchantillonOut.model_validate(
            chaine["echantillon"]
        ).model_dump(mode="json"),
        "voucher": (
            VoucherOut.model_validate(chaine["voucher"]).model_dump(mode="json")
            if chaine["voucher"] is not None
            else None
        ),
        "extractions": [
            {
                "extraction": ExtractionOut.model_validate(
                    e["extraction"]
                ).model_dump(mode="json"),
                "extraits": [
                    {
                        "extrait": ExtraitOut.model_validate(
                            x["extrait"]
                        ).model_dump(mode="json"),
                        "fractions": [
                            {
                                "fraction": FractionOut.model_validate(
                                    f["fraction"]
                                ).model_dump(mode="json"),
                                "composes": f["composes"],
                            }
                            for f in x["fractions"]
                        ],
                    }
                    for x in e["extraits"]
                ],
            }
            for e in chaine["extractions"]
        ],
    }


@router.delete("/science/terrain/echantillons/{echantillon_id}")
def supprimer_echantillon(
    echantillon_id: int,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Suppression LOGIQUE d'un échantillon (tracée)."""
    objet = _objet_ou_404(db, Echantillon, echantillon_id, "Échantillon")
    _verifier_projet(api_key, objet)
    terrain_service.supprimer_doucement(db, objet)
    db.commit()
    return {"detail": "Échantillon supprimé (suppression logique tracée)."}


@router.post(
    "/science/terrain/vouchers",
    response_model=VoucherOut,
    status_code=201,
)
def creer_voucher(
    payload: VoucherCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> VoucherOut:
    """Crée un voucher d'herbier (idempotent sur le code)."""
    data, project_id = _preparer_creation(payload, api_key)
    try:
        objet, _cree = terrain_service.creer_voucher(
            db, project_id=project_id, **data
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(objet)
    return VoucherOut.model_validate(objet)


@router.get(
    "/science/terrain/vouchers", response_model=list[VoucherOut]
)
def lister_vouchers(
    project_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> list[VoucherOut]:
    """Vouchers actifs du projet autorisé (paginé)."""
    effectif = _projet_requis(api_key, project_id)
    objets = terrain_service.lister_vouchers(
        db, project_id=effectif, limit=limit, offset=offset)
    return [VoucherOut.model_validate(o) for o in objets]


@router.post("/science/terrain/echantillons/{echantillon_id}/voucher")
def lier_voucher(
    echantillon_id: int,
    voucher_id: int = Query(),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Rattache un échantillon à un voucher d'herbier (même projet)."""
    objet = _objet_ou_404(db, Echantillon, echantillon_id, "Échantillon")
    _verifier_projet(api_key, objet)
    voucher = _objet_ou_404(db, Voucher, voucher_id, "Voucher")
    _verifier_projet(api_key, voucher, "Ce voucher")
    try:
        terrain_service.lier_echantillon_voucher(db, echantillon_id, voucher_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    db.commit()
    return {"detail": "Échantillon rattaché au voucher."}


# ---------------------------------------------------------------------------
# Chaîne extraction → extrait → fraction → composé
# ---------------------------------------------------------------------------


@router.post(
    "/science/terrain/extractions",
    response_model=ExtractionOut,
    status_code=201,
)
def creer_extraction(
    payload: ExtractionCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> ExtractionOut:
    """Enregistre une extraction sur un échantillon du projet."""
    data, project_id = _preparer_creation(payload, api_key)
    echantillon = _objet_ou_404(
        db, Echantillon, data["echantillon_id"], "Échantillon"
    )
    _verifier_projet(api_key, echantillon)
    try:
        objet = terrain_service.creer_extraction(
            db, project_id=project_id, **data
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(objet)
    return ExtractionOut.model_validate(objet)


@router.post(
    "/science/terrain/extraits",
    response_model=ExtraitOut,
    status_code=201,
)
def creer_extrait(
    payload: ExtraitCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> ExtraitOut:
    """Enregistre un extrait issu d'une extraction du projet."""
    data, project_id = _preparer_creation(payload, api_key)
    extraction = _objet_ou_404(
        db, Extraction, data["extraction_id"], "Extraction"
    )
    _verifier_projet(api_key, extraction)
    objet = terrain_service.creer_extrait(db, project_id=project_id, **data)
    db.commit()
    db.refresh(objet)
    return ExtraitOut.model_validate(objet)


@router.post(
    "/science/terrain/fractions",
    response_model=FractionOut,
    status_code=201,
)
def creer_fraction(
    payload: FractionCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> FractionOut:
    """Enregistre une fraction issue d'un extrait du projet."""
    data, project_id = _preparer_creation(payload, api_key)
    extrait = _objet_ou_404(db, Extrait, data["extrait_id"], "Extrait")
    _verifier_projet(api_key, extrait)
    objet = terrain_service.creer_fraction(db, project_id=project_id, **data)
    db.commit()
    db.refresh(objet)
    return FractionOut.model_validate(objet)


@router.post(
    "/science/terrain/fractions/{fraction_id}/composes",
    response_model=FractionComposeOut,
    status_code=201,
)
def lier_fraction_compose(
    fraction_id: int,
    payload: FractionComposeCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> FractionComposeOut:
    """Lie une fraction à un composé (statut d'identification, jamais deviné)."""
    data, project_id = _preparer_creation(payload, api_key)
    fraction = _objet_ou_404(db, Fraction, fraction_id, "Fraction")
    _verifier_projet(api_key, fraction)
    try:
        objet, _cree = terrain_service.lier_fraction_compose(
            db,
            project_id=project_id,
            fraction_id=fraction_id,
            compound_id=data["compound_id"],
            identification_statut=data.get(
                "identification_statut", "inconnu"
            ),
            commentaire=data.get("commentaire"),
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(objet)
    return FractionComposeOut.model_validate(objet)


# ---------------------------------------------------------------------------
# Analyses phytochimiques
# ---------------------------------------------------------------------------


@router.post(
    "/science/terrain/analyses",
    response_model=AnalyseChimiqueOut,
    status_code=201,
)
def creer_analyse(
    payload: AnalyseChimiqueCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> AnalyseChimiqueOut:
    """Enregistre une analyse phytochimique (au moins un maillon rattaché)."""
    data, project_id = _preparer_creation(payload, api_key)
    for cle, modele, nom in (
        ("echantillon_id", Echantillon, "Échantillon"),
        ("extrait_id", Extrait, "Extrait"),
        ("fraction_id", Fraction, "Fraction"),
    ):
        if data.get(cle) is not None:
            maillon = _objet_ou_404(db, modele, data[cle], nom)
            _verifier_projet(api_key, maillon, f"Ce {nom.lower()}")
    try:
        objet = terrain_service.creer_analyse_chimique(
            db, project_id=project_id, **data
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(objet)
    return AnalyseChimiqueOut.model_validate(objet)


@router.get(
    "/science/terrain/analyses",
    response_model=list[AnalyseChimiqueOut],
)
def lister_analyses(
    project_id: int | None = Query(default=None),
    echantillon_id: int | None = Query(default=None),
    extrait_id: int | None = Query(default=None),
    fraction_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> list[AnalyseChimiqueOut]:
    """Analyses chimiques du projet (filtrables par maillon, paginé)."""
    effectif = _projet_requis(api_key, project_id)
    objets = terrain_service.lister_analyses_chimiques(
        db,
        project_id=effectif,
        echantillon_id=echantillon_id,
        extrait_id=extrait_id,
        fraction_id=fraction_id,
        limit=limit,
        offset=offset,
    )
    return [AnalyseChimiqueOut.model_validate(o) for o in objets]


@router.post(
    "/science/terrain/analyses/{analyse_id}/pics",
    response_model=PicChromatographiqueOut,
    status_code=201,
)
def ajouter_pic(
    analyse_id: int,
    payload: PicChromatographiqueCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> PicChromatographiqueOut:
    """Ajoute un pic à une analyse (identification jamais inventée)."""
    analyse = _objet_ou_404(
        db, AnalyseChimique, analyse_id, "Analyse chimique"
    )
    _verifier_projet(api_key, analyse)
    data = payload.model_dump(exclude_unset=True)
    data.pop("analyse_id", None)
    try:
        pic = terrain_service.ajouter_pic(db, analyse_id, **data)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(pic)
    return PicChromatographiqueOut.model_validate(pic)


@router.get(
    "/science/terrain/analyses/{analyse_id}/pics",
    response_model=list[PicChromatographiqueOut],
)
def lister_pics(
    analyse_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> list[PicChromatographiqueOut]:
    """Pics d'une analyse chimique (projet autorisé, paginé)."""
    analyse = _objet_ou_404(
        db, AnalyseChimique, analyse_id, "Analyse chimique"
    )
    _verifier_projet(api_key, analyse)
    pics = terrain_service.lister_pics(
        db, analyse_id, limit=limit, offset=offset)
    return [PicChromatographiqueOut.model_validate(p) for p in pics]


@router.post("/science/terrain/analyses/{analyse_id}/fichier")
def rattacher_fichier_brut(
    analyse_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Rattache un fichier brut à une analyse (SHA-256 enregistré).

    Corps : ``{"chemin": "/chemin/local/du/fichier.brut"}`` — le fichier
    n'est jamais copié en base, seuls son chemin et son empreinte
    SHA-256 sont conservés (traçabilité). 404 si le fichier est
    introuvable sur le poste.
    """
    analyse = _objet_ou_404(
        db, AnalyseChimique, analyse_id, "Analyse chimique"
    )
    _verifier_projet(api_key, analyse)
    chemin = (payload or {}).get("chemin")
    try:
        objet = terrain_service.enregistrer_fichier_brut(
            db, analyse_id, chemin
        )
    except PermissionError as exc:
        # P0 sécurité : chemin hors du répertoire autorisé.
        raise HTTPException(status_code=403, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    db.commit()
    return {
        "detail": "Fichier brut rattaché (empreinte SHA-256 enregistrée).",
        "chemin": objet.fichier_brut_path,
        "sha256": objet.fichier_brut_sha256,
    }


@router.get("/science/terrain/analyses/{analyse_id}/integrite")
def verifier_integrite(
    analyse_id: int,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Vérifie l'intégrité du fichier brut d'une analyse (SHA-256).

    Un fichier modifié ou corrompu après son rattachement est SIGNALÉ
    (``correspond: false``) — jamais masqué.
    """
    analyse = _objet_ou_404(
        db, AnalyseChimique, analyse_id, "Analyse chimique"
    )
    _verifier_projet(api_key, analyse)
    return terrain_service.verifier_integrite_fichier(db, analyse_id)


# ---------------------------------------------------------------------------
# Registre des langues (préparation multilingue)
# ---------------------------------------------------------------------------


@router.get("/science/terrain/langues")
def lister_langues(
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> list[dict]:
    """Langues du registre (Mooré, Dioula, Fulfuldé, Gulmancéma, Bissa
    et langues ajoutées par les chercheurs — jamais auto-attribuées)."""
    return [
        {
            "code": langue.code,
            "libelle": langue.libelle,
            "region": langue.region,
            "source": langue.source,
            "actif": langue.actif,
        }
        for langue in terrain_service.lister_langues(db)
    ]


@router.post("/science/terrain/langues", status_code=201)
def creer_langue(
    payload: dict,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Ajoute une langue au registre (idempotent sur le code).

    Corps : ``{"code": "gourmantche", "libelle": "Gourmantché",
    "region": "...", "source": "..."}``. La langue reste déclarative :
    elle n'est jamais attribuée automatiquement à un nom vernaculaire.
    """
    _projet_requis(api_key, (payload or {}).get("project_id"))
    try:
        langue, creee = terrain_service.enregistrer_langue_db(
            db,
            (payload or {}).get("code") or "",
            (payload or {}).get("libelle") or "",
            region=(payload or {}).get("region"),
            source=(payload or {}).get("source"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return {
        "code": langue.code,
        "libelle": langue.libelle,
        "creee": creee,
    }


# ---------------------------------------------------------------------------
# Pharmacognosie
# ---------------------------------------------------------------------------


@router.put(
    "/science/terrain/plantes/{plant_id}/pharmacognosie",
    response_model=PharmacognosieOut,
)
def definir_pharmacognosie(
    plant_id: int,
    payload: PharmacognosieCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> PharmacognosieOut:
    """Crée ou met à jour la fiche pharmacognosie d'une plante.

    Sections absentes = non renseignées (jamais inventées).
    """
    data, project_id = _preparer_creation(payload, api_key)
    data.pop("plant_id", None)
    projet = db.get(Project, project_id)
    if projet is None:
        raise HTTPException(
            status_code=404, detail=f"Projet introuvable : id={project_id}."
        )
    try:
        fiche, _cree = terrain_service.definir_pharmacognosie(
            db, plant_id, project_id, **data
        )
    except PermissionError as exc:
        # P0 sécurité : la fiche appartient à un autre projet — 403,
        # jamais d'écrasement silencieux inter-projets.
        raise HTTPException(status_code=403, detail=str(exc))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(fiche)
    return PharmacognosieOut.model_validate(fiche)


@router.get(
    "/science/terrain/plantes/{plant_id}/pharmacognosie",
    response_model=PharmacognosieOut | None,
)
def lire_pharmacognosie(
    plant_id: int,
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> PharmacognosieOut | None:
    """Fiche pharmacognosie d'une plante (null si non renseignée).

    P0 sécurité : une fiche non ``public`` appartenant à un autre projet
    n'est pas divulguée (null honnête, pas de 403 révélateur).
    """
    effectif = _projet_requis(api_key, project_id)
    fiche = terrain_service.lire_pharmacognosie(db, plant_id, effectif)
    if fiche is None:
        return None
    return PharmacognosieOut.model_validate(fiche)


# ---------------------------------------------------------------------------
# Statuts épistémiques (cibles / activités existantes — DataLevel figé)
# ---------------------------------------------------------------------------


@router.patch("/science/terrain/cibles/{cible_id}/statut")
def definir_statut_cible(
    cible_id: int,
    payload: StatutEpistemiquePatch,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Définit le statut épistémique d'une cible (documente/infere/predit/calcule)."""
    cible = db.get(CompoundTarget, cible_id)
    if cible is None:
        raise HTTPException(
            status_code=404, detail=f"Cible introuvable : id={cible_id}."
        )
    if payload.statut_epistemique not in _STATUTS_EPISTEMIQUES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Statut épistémique invalide : {payload.statut_epistemique!r} "
                f"(attendu : {', '.join(sorted(_STATUTS_EPISTEMIQUES))})."
            ),
        )
    terrain_service.definir_statut_epistemique(
        db, cible, payload.statut_epistemique, payload.methode_statut
    )
    db.commit()
    return {
        "id": cible.id,
        "statut_epistemique": cible.statut_epistemique,
        "methode_statut": cible.methode_statut,
    }


@router.patch("/science/terrain/activites/{activite_id}/statut")
def definir_statut_activite(
    activite_id: int,
    payload: StatutEpistemiquePatch,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Définit le statut épistémique d'une activité rapportée."""
    activite = db.get(PlantActivity, activite_id)
    if activite is None:
        raise HTTPException(
            status_code=404,
            detail=f"Activité introuvable : id={activite_id}.",
        )
    if payload.statut_epistemique not in _STATUTS_EPISTEMIQUES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Statut épistémique invalide : {payload.statut_epistemique!r} "
                f"(attendu : {', '.join(sorted(_STATUTS_EPISTEMIQUES))})."
            ),
        )
    terrain_service.definir_statut_epistemique(
        db, activite, payload.statut_epistemique, payload.methode_statut
    )
    db.commit()
    return {
        "id": activite.id,
        "statut_epistemique": activite.statut_epistemique,
        "methode_statut": activite.methode_statut,
    }


# ---------------------------------------------------------------------------
# Recherche multi-motifs + export public
# ---------------------------------------------------------------------------


@router.get(
    "/science/terrain/recherche",
    response_model=list[RecherchePlanteOut],
)
def rechercher(
    q: str = Query(min_length=1, max_length=255),
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> list[RecherchePlanteOut]:
    """Recherche de plantes avec motif affiché.

    Motifs : ``exact`` > ``normalise`` > ``synonyme`` > ``vernaculaire``
    > ``partiel``. « neem » et « karité » sont trouvés via les noms
    vernaculaires enregistrés (motif ``vernaculaire``).
    """
    effectif = scoped_project_id(api_key, project_id)
    if effectif is not None:
        require_project_access_strict(api_key, effectif)
    resultats = terrain_service.rechercher_plantes(
        db, q, project_id=effectif
    )
    return [RecherchePlanteOut.model_validate(r) for r in resultats]


@router.get("/science/terrain/export-public/plantes/{plant_id}")
def export_public(
    plant_id: int,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Export PUBLIC d'une plante (confidentialité garantie).

    N'inclut QUE les enregistrements ``niveau_confidentialite="public"``.
    Les coordonnées GPS précises et les identités ne sont JAMAIS
    divulguées. Les usages traditionnels portent le bandeau
    « USAGE TRADITIONNEL RAPPORTÉ ».
    """
    try:
        return terrain_service.export_public_plante(db, plant_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
