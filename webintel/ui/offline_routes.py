"""Routes UI « hors-ligne » — WebIntel (100 % français).

NOUVEAU routeur (enregistré dans ``webintel/main.py`` via
``app.include_router(offline_routes.router)``) :

- ``GET /synchronisation`` : file de synchronisation (filtre par
  statut), actions (synchroniser / relancer / vider), section
  « Réseau » (bascule mode hors-ligne), section « Sauvegarde »
  (export ZIP / import + rapport) ;
- ``GET /synchronisation/conflits/{file_id}`` + POST résolution :
  affichage LOCAL vs DISTANT côte à côte, choix explicite
  (local / distant / fusion manuelle), chaque résolution journalisée ;
- ``GET /collectes`` (+ ``/nouvelle``, ``/{id}``) : collecte de
  terrain offline (photo, GPS, localité, notes, échantillon, voucher,
  partie utilisée) avec badges « locale » / « synchronisée » et
  protection du GPS selon confidentialité ;
- ``GET /carte`` : carte SVG 100 % locale (aucune tuile externe, fond
  schématique) des sites de collecte, GPS floutés/masqués selon les
  permissions.

RÈGLES :
- 100 % additif : aucun fichier existant modifié sauf l'enregistrement
  du routeur dans ``main.py`` et les liens de navigation ;
- toute modification faite en mode hors-ligne est mise en file
  (``mettre_en_file``) — jamais d'écrasement silencieux ;
- les données de terrain sont étiquetées « observation de terrain »
  (donnée d'observation locale, jamais confondue avec du publié).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from webintel.api.deps import get_db
from webintel.config.settings import get_settings
from webintel.database.models import ApiKey
from webintel.offline import cartographie as carto
from webintel.offline import photos as photos_mod
from webintel.offline import reseau
from webintel.offline import sauvegarde as sauvegarde_mod
from webintel.offline import synchronisation as sync
from webintel.offline.models import (
    CollecteTerrain,
    FileSynchronisation,
    NiveauConfidentialite,
    OperationSync,
    StatutSync,
)
from webintel.offline.synchronisation import (
    DepotDistantDossier,
    _filtre_projet,
    comparer_versions,
    executer_synchronisation,
    lister_file,
    mettre_en_file,
    relancer_file,
    resoudre_conflit,
    statut_entite,
    vider_file,
)
from webintel.science import terrain as terrain_service
from webintel.science.models import Echantillon, Plant
from webintel.ui.auth import ui_auth
from webintel.ui.routes import _project_for, _render

router = APIRouter()

LIBELLES_STATUT = {
    StatutSync.EN_ATTENTE.value: ("En attente", "warn"),
    StatutSync.ENVOYE.value: ("Envoyé", "ok"),
    StatutSync.ECHOUE.value: ("Échoué", "bad"),
    StatutSync.CONFLIT.value: ("Conflit", "bad"),
}
LIBELLES_OPERATION = {
    OperationSync.CREATE.value: "création",
    OperationSync.UPDATE.value: "modification",
    OperationSync.DELETE.value: "suppression",
}
LIBELLES_ENTITE = {
    "collecte": "collecte terrain",
    "plante": "plante",
    "echantillon": "échantillon",
    "etude": "étude",
    "observation": "observation",
    "simulation": "simulation",
    "note": "note de laboratoire",
}
LIBELLES_CONFIDENTIALITE = {
    NiveauConfidentialite.PUBLIC.value: "Public",
    NiveauConfidentialite.RESTREINT.value: "Restreint",
    NiveauConfidentialite.CONFIDENTIEL.value: "Confidentiel",
}
COULEURS_CARTE = {
    NiveauConfidentialite.PUBLIC.value: "#1f7a3a",
    NiveauConfidentialite.RESTREINT.value: "#c77414",
    NiveauConfidentialite.CONFIDENTIEL.value: "#b3261e",
}


def _auteur(ui_key: ApiKey | None) -> str:
    """Nom d'auteur : clé API en mode serveur, « utilisateur local » sinon."""
    if ui_key is not None and getattr(ui_key, "name", None):
        return str(ui_key.name)
    return "utilisateur local"


def _depot() -> DepotDistantDossier:
    """Dépôt distant « dossier » (échangeable via clé USB sur le terrain).

    Configurable via ``WEBINTEL_DEPOT_SYNC`` ; défaut : dossier
    ``depot_distant`` à côté des exports (clairement étiqueté « simulé »
    dans l'UI).
    """
    base = os.environ.get("WEBINTEL_DEPOT_SYNC", "").strip()
    if not base:
        export_dir = get_settings().EXPORT_DIR or "./webintel_data/exports"
        base = os.path.join(
            os.path.dirname(os.path.abspath(export_dir)), "depot_distant"
        )
    return DepotDistantDossier(base)


def _bandeau_hors_ligne() -> str | None:
    if reseau.est_hors_ligne():
        return (
            "Mode hors-ligne actif : les connecteurs externes sont "
            "désactivés (message explicite, aucune tentative réseau). "
            "Vos modifications sont enregistrées dans la file de "
            "synchronisation."
        )
    return None


# ---------------------------------------------------------------------------
# /synchronisation — file, réseau, sauvegarde
# ---------------------------------------------------------------------------

def _projet_courant_id(db: Session, ui_key) -> int:
    """Id du projet de la session UI (cloisonnement P0 de la file)."""
    return _project_for(db, ui_key).id


def _file_visible(db: Session, ui_key, ligne: FileSynchronisation) -> bool:
    """Une entrée de file est visible si elle appartient au projet
    courant ou si elle est globale (``project_id`` NULL : plantes,
    entrées antérieures au cloisonnement)."""
    pid = _projet_courant_id(db, ui_key)
    return ligne.project_id is None or ligne.project_id == pid


@router.get("/synchronisation", response_class=HTMLResponse)
def page_synchronisation(
    request: Request,
    statut: str | None = None,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    pid = _projet_courant_id(db, ui_key)
    lignes = lister_file(
        db, statut if statut in LIBELLES_STATUT else None, project_id=pid
    )
    compteurs = {
        s: _filtre_projet(
            db.query(FileSynchronisation).filter(
                FileSynchronisation.statut == s),
            pid,
        ).count()
        for s in LIBELLES_STATUT
    }
    return _render(
        "synchronisation.html",
        ui_key=ui_key,
        active="synchronisation",
        lignes=lignes,
        libelles_statut=LIBELLES_STATUT,
        libelles_operation=LIBELLES_OPERATION,
        libelles_entite=LIBELLES_ENTITE,
        filtre=statut,
        compteurs=compteurs,
        hors_ligne=reseau.est_hors_ligne(),
        bandeau=_bandeau_hors_ligne(),
        depot_base=_depot().base,
        message=request.query_params.get("message"),
        erreur=request.query_params.get("erreur"),
    )


@router.post("/synchronisation/action")
def action_synchronisation(
    action: str = Form(...),
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    auteur = _auteur(ui_key)
    try:
        if action == "synchroniser":
            stats = executer_synchronisation(
                db, _depot(), auteur=auteur,
                project_id=_projet_courant_id(db, ui_key),
            )
            db.commit()
            message = (
                f"Synchronisation terminée : {stats['traitees']} entrée(s) "
                f"traitée(s), {stats['envoyees']} envoyée(s), "
                f"{stats['conflits']} conflit(s), {stats['echoues']} "
                "échec(s)."
            )
            if stats["conflits"]:
                message += (
                    " Les conflits attendent votre choix explicite "
                    "(aucun écrasement automatique)."
                )
        elif action == "relancer":
            n = relancer_file(db, project_id=_projet_courant_id(db, ui_key))
            db.commit()
            message = f"{n} entrée(s) en échec remise(s) en file."
        elif action == "vider":
            n = vider_file(db, project_id=_projet_courant_id(db, ui_key))
            db.commit()
            message = f"File allégée : {n} entrée(s) déjà envoyée(s) supprimée(s)."
        elif action == "hors_ligne_on":
            reseau.definir_mode_hors_ligne(True)
            message = "Mode hors-ligne ACTIVÉ (simulation terrain)."
        elif action == "hors_ligne_off":
            reseau.definir_mode_hors_ligne(False)
            message = "Mode hors-ligne DÉSACTIVÉ."
        elif action == "hors_ligne_auto":
            reseau.definir_mode_hors_ligne(None)
            message = "Mode réseau : suivi de WEBINTEL_HORS_LIGNE."
        else:
            raise ValueError(f"Action inconnue : {action!r}.")
    except Exception as exc:
        db.rollback()
        return RedirectResponse(
            f"/synchronisation?erreur={_q(str(exc))}", status_code=303
        )
    return RedirectResponse(f"/synchronisation?message={_q(message)}",
                            status_code=303)


def _q(texte: str) -> str:
    from urllib.parse import quote

    return quote(texte, safe="")


# ---------------------------------------------------------------------------
# Conflits : LOCAL vs DISTANT + résolution explicite journalisée
# ---------------------------------------------------------------------------

@router.get("/synchronisation/conflits/{file_id}", response_class=HTMLResponse)
def page_conflit(
    file_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    ligne = db.get(FileSynchronisation, file_id)
    if (
        ligne is None
        or ligne.statut != StatutSync.CONFLIT.value
        or not _file_visible(db, ui_key, ligne)
    ):
        raise HTTPException(
            status_code=404,
            detail="Conflit introuvable (résolu, inexistant ou "
                   "d'un autre projet).",
        )
    comparaison = comparer_versions(ligne.payload or {},
                                    ligne.remote_payload or {})
    distant_supprime = bool((ligne.remote_payload or {}).get("__supprime__"))
    return _render(
        "synchronisation_conflit.html",
        ui_key=ui_key,
        active="synchronisation",
        ligne=ligne,
        comparaison=comparaison,
        distant_supprime=distant_supprime,
        libelles_operation=LIBELLES_OPERATION,
        libelles_entite=LIBELLES_ENTITE,
        fusion_json=json.dumps(ligne.payload or {}, ensure_ascii=False,
                               indent=2),
        hors_ligne=reseau.est_hors_ligne(),
        erreur=None,
    )


@router.post("/synchronisation/conflits/{file_id}/resoudre")
def resoudre(
    file_id: int,
    choix: str = Form(...),
    fusion_json: str = Form(""),
    commentaire: str = Form(""),
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    ligne = db.get(FileSynchronisation, file_id)
    try:
        if ligne is not None and not _file_visible(db, ui_key, ligne):
            raise ValueError(
                "Entrée de file d'un autre projet : résolution refusée."
            )
        fusion = None
        if choix == "fusion":
            try:
                fusion = json.loads(fusion_json or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"JSON de fusion invalide : {exc}."
                ) from exc
            if not isinstance(fusion, dict):
                raise ValueError("La fusion doit être un objet JSON.")
        resoudre_conflit(
            db, _depot(), file_id, choix,
            auteur=_auteur(ui_key), fusion=fusion,
            commentaire=commentaire or None,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        return _render(
            "synchronisation_conflit.html",
            ui_key=ui_key,
            active="synchronisation",
            ligne=ligne,
            comparaison=comparer_versions(
                (ligne.payload if ligne else {}) or {},
                (ligne.remote_payload if ligne else {}) or {}),
            distant_supprime=False,
            libelles_operation=LIBELLES_OPERATION,
        libelles_entite=LIBELLES_ENTITE,
            fusion_json=fusion_json,
            hors_ligne=reseau.est_hors_ligne(),
            erreur=str(exc),
        )
    return RedirectResponse(
        "/synchronisation?message="
        + _q(f"Conflit #{file_id} résolu (choix : {choix}) et journalisé."),
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Sauvegarde / restauration
# ---------------------------------------------------------------------------

@router.post("/sauvegarde/exporter")
def exporter(
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    tmp = tempfile.mktemp(prefix="webintel-sauvegarde-", suffix=".zip")
    horodatage = datetime.now().strftime("%Y%m%d-%H%M%S")
    sauvegarde_mod.exporter_projet(
        db, tmp, project_id=_projet_courant_id(db, ui_key))
    return FileResponse(
        tmp,
        media_type="application/zip",
        filename=f"webintel-sauvegarde-{horodatage}.zip",
        background=BackgroundTask(os.remove, tmp),
    )


@router.post("/sauvegarde/importer", response_class=HTMLResponse)
def importer(
    request: Request,
    fichier: UploadFile = File(...),
    forcer: str = Form(""),
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    tmp = tempfile.mktemp(prefix="webintel-restauration-", suffix=".zip")
    try:
        contenu = fichier.file.read()
        with open(tmp, "wb") as fh:
            fh.write(contenu)
        rapport = sauvegarde_mod.importer_projet(
            db, tmp, forcer=forcer == "oui")
        db.commit()
        erreur = None
    except Exception as exc:
        db.rollback()
        rapport, erreur = None, str(exc)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return _render(
        "sauvegarde_rapport.html",
        ui_key=ui_key,
        active="synchronisation",
        rapport=rapport,
        erreur=erreur,
        forcer=forcer == "oui",
        hors_ligne=reseau.est_hors_ligne(),
    )


# ---------------------------------------------------------------------------
# Collectes de terrain
# ---------------------------------------------------------------------------

def _badge_collecte(db: Session, collecte: CollecteTerrain) -> tuple[str, str]:
    statut = statut_entite(db, "collecte", collecte.id)
    if statut == "locale":
        return ("Donnée locale — en attente de synchronisation", "warn")
    return ("Donnée synchronisée", "ok")


@router.get("/collectes", response_class=HTMLResponse)
def page_collectes(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    collectes = (
        db.query(CollecteTerrain)
        .filter(CollecteTerrain.project_id == _project_for(db, ui_key).id)
        .order_by(CollecteTerrain.id.desc())
        .all()
    )
    demandeur = _auteur(ui_key)
    cartes = []
    for c in collectes:
        badge, classe = _badge_collecte(db, c)
        pt = carto.coordonnees_affichees(
            c.latitude, c.longitude, c.niveau_confidentialite, demandeur,
            c.auteur)
        cartes.append({
            "collecte": c, "badge": badge, "classe": classe,
            "gps": pt,
            "nom_plante": _nom_plante(db, c),
        })
    return _render(
        "collectes.html",
        ui_key=ui_key,
        active="collectes",
        cartes=cartes,
        hors_ligne=reseau.est_hors_ligne(),
        bandeau=_bandeau_hors_ligne(),
        libelles_confidentialite=LIBELLES_CONFIDENTIALITE,
        message=request.query_params.get("message"),
        erreur=request.query_params.get("erreur"),
    )


def _nom_plante(db: Session, c: CollecteTerrain) -> str:
    if c.plante_id:
        plante = db.get(Plant, c.plante_id)
        if plante is not None:
            return plante.scientific_name
    return c.nom_plante_saisi or "—"


@router.get("/collectes/nouvelle", response_class=HTMLResponse)
def page_collecte_nouvelle(
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    plantes = db.query(Plant).order_by(Plant.scientific_name).limit(500).all()
    return _render(
        "collecte_nouvelle.html",
        ui_key=ui_key,
        active="collectes",
        plantes=plantes,
        niveaux=LIBELLES_CONFIDENTIALITE,
        hors_ligne=reseau.est_hors_ligne(),
        erreur=None,
    )


def _flottant(valeur: str | None) -> float | None:
    if valeur is None or not str(valeur).strip():
        return None
    try:
        return float(str(valeur).replace(",", "."))
    except ValueError:
        raise ValueError(f"Coordonnée invalide : {valeur!r}.")


@router.post("/collectes/nouvelle")
def creer_collecte(
    localite: str = Form(""),
    plante_id: str = Form(""),
    nom_plante_saisi: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    precision_gps_m: str = Form(""),
    echantillon_ref: str = Form(""),
    voucher: str = Form(""),
    partie_utilisee: str = Form(""),
    niveau_confidentialite: str = Form("restreint"),
    notes: str = Form(""),
    photos: list[UploadFile] = File([]),
    legendes: str = Form(""),
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    auteur = _auteur(ui_key)
    try:
        if niveau_confidentialite not in LIBELLES_CONFIDENTIALITE:
            raise ValueError("Niveau de confidentialité invalide.")
        latitude = _flottant(latitude)
        longitude = _flottant(longitude)
        # Des coordonnées hors plage WGS84 (faute de frappe) ne sont
        # jamais enregistrées silencieusement.
        terrain_service.valider_gps(latitude, longitude)
        collecte = CollecteTerrain(
            project_id=_project_for(db, ui_key).id,
            plante_id=int(plante_id) if plante_id.strip() else None,
            nom_plante_saisi=nom_plante_saisi.strip() or None,
            localite=localite.strip(),
            latitude=latitude,
            longitude=longitude,
            precision_gps_m=_flottant(precision_gps_m),
            notes=notes.strip() or None,
            echantillon_ref=echantillon_ref.strip() or None,
            voucher=voucher.strip() or None,
            partie_utilisee=partie_utilisee.strip() or None,
            niveau_confidentialite=niveau_confidentialite,
            auteur=auteur,
        )
        db.add(collecte)
        db.flush()
        metas = []
        for up in photos:
            if not up.filename:
                continue
            contenu = up.file.read()
            if not contenu:
                continue
            metas.append(photos_mod.enregistrer_photo(
                collecte.id, contenu, up.filename))
        collecte.photos = metas
        db.flush()
        # Hors connexion : la création part dans la file (jamais perdue).
        if reseau.est_hors_ligne():
            mettre_en_file(
                db, "collecte", collecte.id, OperationSync.CREATE.value,
                sync._payload_depuis_objet(collecte), auteur=auteur,
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        plantes = db.query(Plant).order_by(Plant.scientific_name).limit(500).all()
        return _render(
            "collecte_nouvelle.html",
            ui_key=ui_key,
            active="collectes",
            plantes=plantes,
            niveaux=LIBELLES_CONFIDENTIALITE,
            hors_ligne=reseau.est_hors_ligne(),
            erreur=str(exc),
        )
    return RedirectResponse(
        f"/collectes/{collecte.id}?message="
        + _q("Collecte enregistrée localement (fonctionne sans réseau)."),
        status_code=303,
    )


def _collecte_ou_404(db: Session, ui_key: ApiKey | None,
                     collecte_id: int) -> CollecteTerrain:
    """Collecte du projet de la session, sinon 404 (cloisonnement P0)."""
    collecte = (
        db.query(CollecteTerrain)
        .filter(CollecteTerrain.id == collecte_id,
                CollecteTerrain.project_id == _project_for(db, ui_key).id)
        .one_or_none()
    )
    if collecte is None:
        raise HTTPException(status_code=404, detail="Collecte introuvable.")
    return collecte


@router.get("/collectes/{collecte_id}", response_class=HTMLResponse)
def page_collecte_detail(
    collecte_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    collecte = _collecte_ou_404(db, ui_key, collecte_id)
    demandeur = _auteur(ui_key)
    badge, classe = _badge_collecte(db, collecte)
    gps = carto.coordonnees_affichees(
        collecte.latitude, collecte.longitude,
        collecte.niveau_confidentialite, demandeur, collecte.auteur)
    return _render(
        "collecte_detail.html",
        ui_key=ui_key,
        active="collectes",
        collecte=collecte,
        nom_plante=_nom_plante(db, collecte),
        badge=badge,
        classe=classe,
        gps=gps,
        demandeur=demandeur,
        libelles_confidentialite=LIBELLES_CONFIDENTIALITE,
        hors_ligne=reseau.est_hors_ligne(),
        message=request.query_params.get("message"),
    )


@router.get("/collectes/{collecte_id}/photos/{nom}")
def servir_photo(
    collecte_id: int,
    nom: str,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    collecte = _collecte_ou_404(db, ui_key, collecte_id)
    autorises = {p.get("fichier") for p in (collecte.photos or [])} | \
                {p.get("miniature") for p in (collecte.photos or [])}
    if nom not in autorises:
        raise HTTPException(status_code=404, detail="Photo introuvable.")
    chemin = photos_mod.chemin_photo(collecte_id, nom)
    if chemin is None:
        raise HTTPException(status_code=404, detail="Photo introuvable.")
    media = "image/jpeg" if nom.endswith(".jpg") else "image/png"
    return FileResponse(chemin, media_type=media)


@router.post("/collectes/{collecte_id}/supprimer")
def supprimer_collecte(
    collecte_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    collecte = _collecte_ou_404(db, ui_key, collecte_id)
    auteur = _auteur(ui_key)
    if reseau.est_hors_ligne():
        mettre_en_file(
            db, "collecte", collecte.id, OperationSync.DELETE.value,
            {"id": collecte.id}, auteur=auteur,
            base_version=collecte.updated_at.isoformat()
            if collecte.updated_at else None,
        )
    photos_mod.supprimer_photos_collecte(collecte.id)
    db.delete(collecte)
    db.commit()
    return RedirectResponse(
        "/collectes?message=" + _q("Collecte supprimée."),
        status_code=303,
    )


# ---------------------------------------------------------------------------
# /carte — cartographie 100 % locale (SVG, aucune tuile externe)
# ---------------------------------------------------------------------------

@router.get("/carte", response_class=HTMLResponse)
def page_carte(
    vue: str = "auteur",
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    auteur_session = _auteur(ui_key)
    # « vue=visiteur » : démontre la protection (GPS floutés/masqués).
    demandeur = auteur_session if vue != "visiteur" else "visiteur (démonstration)"
    projet = _project_for(db, ui_key)
    collectes = (
        db.query(CollecteTerrain)
        .filter(CollecteTerrain.project_id == projet.id)
        .order_by(CollecteTerrain.id)
        .all()
    )
    # Échantillons du projet (soft-delete exclus — P0) : leurs GPS
    # suivent la même règle de confidentialité.
    echantillons = (
        db.query(Echantillon)
        .filter(Echantillon.project_id == projet.id,
                Echantillon.supprime_le.is_(None))
        .order_by(Echantillon.id)
        .all()
    )
    points = []
    lignes_table = []
    for c in collectes:
        pt = carto.coordonnees_affichees(
            c.latitude, c.longitude, c.niveau_confidentialite, demandeur,
            c.auteur)
        if pt.statut != "absent":
            points.append({
                "lat": pt.lat, "lon": pt.lon, "statut": pt.statut,
                "masque": pt.masque,
                "couleur": COULEURS_CARTE.get(c.niveau_confidentialite,
                                              "#1f7a3a"),
                "titre": f"Collecte #{c.id} — {_nom_plante(db, c)} "
                         f"({c.localite or 'sans localité'})",
            })
        lignes_table.append({"type": "Collecte", "id": c.id,
                             "nom": _nom_plante(db, c),
                             "lieu": c.localite or "—", "gps": pt,
                             "conf": LIBELLES_CONFIDENTIALITE.get(
                                 c.niveau_confidentialite,
                                 c.niveau_confidentialite)})
    for e in echantillons:
        pt = carto.coordonnees_affichees(
            e.latitude, e.longitude, e.niveau_confidentialite, demandeur,
            e.cree_par or "")
        if pt.statut != "absent":
            points.append({
                "lat": pt.lat, "lon": pt.lon, "statut": pt.statut,
                "masque": pt.masque, "couleur": "#1d4ed8",
                "titre": f"Échantillon {e.code_echantillon} "
                         f"({e.localite or e.commune or 'sans localité'})",
            })
        lignes_table.append({"type": "Échantillon", "id": e.id,
                             "nom": e.code_echantillon,
                             "lieu": e.localite or e.commune or "—",
                             "gps": pt,
                             "conf": e.niveau_confidentialite})
    svg = carto.generer_carte_svg(points)
    return _render(
        "carte.html",
        ui_key=ui_key,
        active="carte",
        svg=svg,
        lignes=lignes_table,
        nb_points=len(points),
        vue=vue,
        demandeur=demandeur,
        libelles_confidentialite=LIBELLES_CONFIDENTIALITE,
        hors_ligne=reseau.est_hors_ligne(),
    )
