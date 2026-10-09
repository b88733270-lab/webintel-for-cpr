"""Routes UI « Favoris & Collections » (WebIntel v3.0.0).

Permet d'organiser localement les résultats (entités, URLs analysées,
rapports, recherches) en favoris et collections nommées, par projet.

Routes (router enregistré dans ``webintel/main.py``) :

- ``GET  /collections`` : collections du projet + favoris hors collection
  + formulaires de création (collection / favori) ;
- ``POST /collections`` : créer une collection ;
- ``GET  /collections/{id}`` : détail d'une collection et ses éléments ;
- ``POST /collections/{id}/renommer`` : renommer / décrire une collection ;
- ``POST /collections/{id}/supprimer`` : supprimer une collection — ses
  favoris sont CONSERVÉS hors collection (aucune suppression silencieuse) ;
- ``POST /collections/favoris/ajouter`` : ajouter un favori (manuel ou
  depuis une page entité) ;
- ``POST /collections/favoris/{id}/retirer`` : retirer un favori ;
- ``POST /collections/favoris/{id}/deplacer`` : déplacer un favori vers
  une collection (ou hors collection).

Isolation stricte : tout passe par ``_project_for`` (projet de la session
en mode serveur, projet par défaut en mode local) et chaque lecture /
écriture vérifie ``project_id`` — un objet d'un autre projet est traité
comme introuvable. Suppressions uniquement via formulaires POST.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from webintel.api.deps import get_db
from webintel.database.models import (
    TYPES_OBJET_FAVORI,
    ApiKey,
    Collection,
    Favorite,
    Project,
)
from webintel.ui.auth import ui_auth
from webintel.ui.routes import _project_for, _render

router = APIRouter()

LIBELLES_TYPES = {
    "entite": "Entité",
    "url": "URL analysée",
    "rapport": "Rapport",
    "recherche": "Recherche",
}


# ---------------------------------------------------------------------------
# Service (fonctions pures sur session — testables sans HTTP)
# ---------------------------------------------------------------------------


def _nettoyer(valeur) -> str:
    return str(valeur or "").strip()


def collections_du_projet(db: Session, project_id: int) -> list[Collection]:
    return (
        db.query(Collection)
        .filter(Collection.project_id == project_id)
        .order_by(Collection.nom)
        .all()
    )


def favoris_du_projet(db: Session, project_id: int) -> list[Favorite]:
    return (
        db.query(Favorite)
        .filter(Favorite.project_id == project_id)
        .order_by(Favorite.created_at.desc(), Favorite.id.desc())
        .all()
    )


def favoris_hors_collection(db: Session, project_id: int) -> list[Favorite]:
    return [f for f in favoris_du_projet(db, project_id) if f.collection_id is None]


def favoris_de_collection(db: Session, collection_id: int) -> list[Favorite]:
    return (
        db.query(Favorite)
        .filter(Favorite.collection_id == collection_id)
        .order_by(Favorite.created_at.desc(), Favorite.id.desc())
        .all()
    )


def collection_visible(db: Session, project: Project, collection_id: int) -> Collection | None:
    """Collection du projet uniquement (isolation) — None sinon."""
    collection = db.get(Collection, collection_id)
    if collection is None or collection.project_id != project.id:
        return None
    return collection


def favori_visible(db: Session, project: Project, favori_id: int) -> Favorite | None:
    favori = db.get(Favorite, favori_id)
    if favori is None or favori.project_id != project.id:
        return None
    return favori


def creer_collection(
    db: Session, project: Project, nom: str, description: str = "",
    couleur: str = "", icone: str = "",
) -> Collection:
    """Crée une collection du projet. `ValueError` FR si nom invalide/doublon."""
    nom = _nettoyer(nom)
    if not nom:
        raise ValueError("Le nom de la collection est obligatoire.")
    existe = (
        db.query(Collection)
        .filter(Collection.project_id == project.id, Collection.nom == nom)
        .one_or_none()
    )
    if existe is not None:
        raise ValueError(f"Une collection nommée « {nom} » existe déjà dans ce projet.")
    collection = Collection(
        project_id=project.id,
        nom=nom,
        description=_nettoyer(description) or None,
        couleur=_nettoyer(couleur) or None,
        icone=_nettoyer(icone) or None,
    )
    db.add(collection)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError(f"Une collection nommée « {nom} » existe déjà dans ce projet.") from None
    db.refresh(collection)
    return collection


def _doublon(db: Session, project_id: int, collection_id, type_objet: str, reference: str) -> bool:
    requete = db.query(Favorite).filter(
        Favorite.project_id == project_id,
        Favorite.type_objet == type_objet,
        Favorite.reference == reference,
    )
    if collection_id is None:
        requete = requete.filter(Favorite.collection_id.is_(None))
    else:
        requete = requete.filter(Favorite.collection_id == collection_id)
    return requete.one_or_none() is not None


def ajouter_favori(
    db: Session, project: Project, titre: str, type_objet: str,
    reference: str, note: str = "", collection_id: int | None = None,
) -> Favorite:
    """Ajoute un favori. `ValueError` FR si champ invalide, collection
    d'un autre projet, ou doublon (même objet dans la même collection —
    y compris hors collection)."""
    titre = _nettoyer(titre)
    type_objet = _nettoyer(type_objet).lower()
    reference = _nettoyer(reference)
    if not titre:
        raise ValueError("Le titre du favori est obligatoire.")
    if type_objet not in TYPES_OBJET_FAVORI:
        raise ValueError(
            "Type d'objet invalide : choisissez entité, URL, rapport ou recherche."
        )
    if not reference:
        raise ValueError("La référence est obligatoire (identifiant ou URL de l'objet).")
    if collection_id is not None and collection_visible(db, project, collection_id) is None:
        raise ValueError("Collection introuvable dans ce projet.")
    if _doublon(db, project.id, collection_id, type_objet, reference):
        raise ValueError("Cet objet est déjà présent dans cette collection (doublon refusé).")
    favori = Favorite(
        project_id=project.id,
        collection_id=collection_id,
        type_objet=type_objet,
        reference=reference,
        titre=titre,
        note=_nettoyer(note) or None,
    )
    db.add(favori)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError("Cet objet est déjà présent dans cette collection (doublon refusé).") from None
    db.refresh(favori)
    return favori


def deplacer_favori(
    db: Session, project: Project, favori: Favorite, collection_id: int | None,
) -> Favorite:
    """Déplace un favori vers une collection (None = hors collection)."""
    if collection_id is not None and collection_visible(db, project, collection_id) is None:
        raise ValueError("Collection de destination introuvable dans ce projet.")
    if favori.collection_id == collection_id:
        return favori
    if _doublon(db, project.id, collection_id, favori.type_objet, favori.reference):
        raise ValueError("Cet objet est déjà présent dans la collection de destination.")
    favori.collection_id = collection_id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError("Cet objet est déjà présent dans la collection de destination.") from None
    db.refresh(favori)
    return favori


def supprimer_collection(db: Session, project: Project, collection: Collection) -> int:
    """Supprime la collection ; ses favoris sont conservés hors collection.

    Retourne le nombre de favoris conservés. Aucune donnée utilisateur
    n'est supprimée silencieusement.
    """
    conserves = 0
    for favori in favoris_de_collection(db, collection.id):
        favori.collection_id = None
        conserves += 1
    db.delete(collection)
    db.commit()
    return conserves


# ---------------------------------------------------------------------------
# Rendu des pages
# ---------------------------------------------------------------------------


def _lien_favori(favori: Favorite) -> str | None:
    """Lien de consultation d'un favori quand il est déductible."""
    if favori.type_objet == "url" and favori.reference.startswith(("http://", "https://")):
        return favori.reference
    if favori.type_objet == "entite" and favori.reference.isdigit():
        return f"/entites/{favori.reference}"
    return None


def _enrichir(favoris: list[Favorite]) -> list[dict]:
    return [
        {"favori": f, "libelle_type": LIBELLES_TYPES.get(f.type_objet, f.type_objet),
         "lien": _lien_favori(f)}
        for f in favoris
    ]


def _page_collections(db, ui_key, project, message=None, erreur=None) -> HTMLResponse:
    collections = collections_du_projet(db, project.id)
    compteurs = {
        c.id: db.query(Favorite).filter(Favorite.collection_id == c.id).count()
        for c in collections
    }
    return _render(
        "collections.html", ui_key, active="collections",
        project=project, collections=collections, compteurs=compteurs,
        favoris=_enrichir(favoris_hors_collection(db, project.id)),
        types_favori=LIBELLES_TYPES, message=message, erreur=erreur,
    )


def _page_detail(db, ui_key, project, collection, message=None, erreur=None) -> HTMLResponse:
    return _render(
        "collection_detail.html", ui_key, active="collections",
        project=project, collection=collection,
        favoris=_enrichir(favoris_de_collection(db, collection.id)),
        autres_collections=[c for c in collections_du_projet(db, project.id) if c.id != collection.id],
        types_favori=LIBELLES_TYPES, message=message, erreur=erreur,
    )


def _id_optionnel(valeur) -> int | None:
    valeur = _nettoyer(valeur)
    if not valeur:
        return None
    try:
        return int(valeur)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Pages & actions
# ---------------------------------------------------------------------------


@router.get("/collections", response_class=HTMLResponse)
def collections_liste(db: Session = Depends(get_db), ui_key: ApiKey | None = Depends(ui_auth)):
    """Collections du projet + favoris hors collection + formulaires."""
    project = _project_for(db, ui_key)
    return _page_collections(db, ui_key, project)


@router.post("/collections", response_class=HTMLResponse)
async def collections_creer(request: Request, db: Session = Depends(get_db),
                            ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    form = await request.form()
    try:
        collection = creer_collection(
            db, project, str(form.get("nom") or ""),
            str(form.get("description") or ""),
            str(form.get("couleur") or ""), str(form.get("icone") or ""),
        )
    except ValueError as exc:
        return _page_collections(db, ui_key, project, erreur=str(exc))
    return RedirectResponse(url=f"/collections/{collection.id}", status_code=303)


@router.get("/collections/{collection_id}", response_class=HTMLResponse)
def collection_detail(collection_id: int, db: Session = Depends(get_db),
                      ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    collection = collection_visible(db, project, collection_id)
    if collection is None:
        return _page_collections(db, ui_key, project,
                                 erreur=f"Collection introuvable : id={collection_id}.")
    return _page_detail(db, ui_key, project, collection)


@router.post("/collections/{collection_id}/renommer", response_class=HTMLResponse)
async def collection_renommer(collection_id: int, request: Request,
                              db: Session = Depends(get_db),
                              ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    collection = collection_visible(db, project, collection_id)
    if collection is None:
        return _page_collections(db, ui_key, project, erreur="Collection introuvable.")
    form = await request.form()
    nom = _nettoyer(form.get("nom"))
    if not nom:
        return _page_detail(db, ui_key, project, collection,
                            erreur="Le nom de la collection est obligatoire.")
    doublon = (
        db.query(Collection)
        .filter(Collection.project_id == project.id, Collection.nom == nom,
                Collection.id != collection.id)
        .one_or_none()
    )
    if doublon is not None:
        return _page_detail(db, ui_key, project, collection,
                            erreur=f"Une collection nommée « {nom} » existe déjà dans ce projet.")
    collection.nom = nom
    collection.description = _nettoyer(form.get("description")) or None
    collection.couleur = _nettoyer(form.get("couleur")) or None
    collection.icone = _nettoyer(form.get("icone")) or None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _page_detail(db, ui_key, project, collection,
                            erreur=f"Une collection nommée « {nom} » existe déjà dans ce projet.")
    return RedirectResponse(url=f"/collections/{collection.id}", status_code=303)


@router.post("/collections/{collection_id}/supprimer")
async def collection_supprimer(collection_id: int, db: Session = Depends(get_db),
                               ui_key: ApiKey | None = Depends(ui_auth)):
    """Suppression via formulaire POST : les favoris sont conservés hors
    collection (jamais de suppression silencieuse de données)."""
    project = _project_for(db, ui_key)
    collection = collection_visible(db, project, collection_id)
    if collection is not None:
        supprimer_collection(db, project, collection)
    return RedirectResponse(url="/collections", status_code=303)


@router.post("/collections/favoris/ajouter", response_class=HTMLResponse)
async def favori_ajouter(request: Request, db: Session = Depends(get_db),
                         ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    form = await request.form()
    collection_id = _id_optionnel(form.get("collection_id"))
    try:
        ajouter_favori(
            db, project,
            str(form.get("titre") or ""), str(form.get("type_objet") or ""),
            str(form.get("reference") or ""), str(form.get("note") or ""),
            collection_id=collection_id,
        )
    except ValueError as exc:
        if collection_id is not None:
            collection = collection_visible(db, project, collection_id)
            if collection is not None:
                return _page_detail(db, ui_key, project, collection, erreur=str(exc))
        return _page_collections(db, ui_key, project, erreur=str(exc))
    if collection_id is not None:
        return RedirectResponse(url=f"/collections/{collection_id}", status_code=303)
    return RedirectResponse(url="/collections", status_code=303)


@router.post("/collections/favoris/{favori_id}/retirer")
async def favori_retirer(favori_id: int, db: Session = Depends(get_db),
                         ui_key: ApiKey | None = Depends(ui_auth)):
    """Retrait d'un favori via formulaire POST (projet vérifié)."""
    project = _project_for(db, ui_key)
    favori = favori_visible(db, project, favori_id)
    if favori is None:
        return RedirectResponse(url="/collections", status_code=303)
    collection_id = favori.collection_id
    db.delete(favori)
    db.commit()
    if collection_id is not None:
        return RedirectResponse(url=f"/collections/{collection_id}", status_code=303)
    return RedirectResponse(url="/collections", status_code=303)


@router.post("/collections/favoris/{favori_id}/deplacer", response_class=HTMLResponse)
async def favori_deplacer(favori_id: int, request: Request,
                          db: Session = Depends(get_db),
                          ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    favori = favori_visible(db, project, favori_id)
    if favori is None:
        return _page_collections(db, ui_key, project, erreur="Favori introuvable.")
    form = await request.form()
    destination = _id_optionnel(form.get("collection_id"))
    try:
        deplacer_favori(db, project, favori, destination)
    except ValueError as exc:
        if favori.collection_id is not None:
            collection = collection_visible(db, project, favori.collection_id)
            if collection is not None:
                return _page_detail(db, ui_key, project, collection, erreur=str(exc))
        return _page_collections(db, ui_key, project, erreur=str(exc))
    if destination is not None:
        return RedirectResponse(url=f"/collections/{destination}", status_code=303)
    return RedirectResponse(url="/collections", status_code=303)
