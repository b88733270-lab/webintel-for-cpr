"""Routeur d'import de données scientifiques — Phase 9.

``POST /api/science/import`` (multipart) : importe un fichier (CSV, JSON,
Excel .xlsx, SDF minimal) vers les tables scientifiques — plantes, composés,
liens plante-composé, preuves.

Authentification : même pattern que ``routers/science.py`` — ``api_key_auth``
+ ``get_db`` (en mode serveur, clé API requise ; en mode local, ouvert).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from webintel.api.deps import (
    api_key_auth,
    get_db,
    limite_debit_couteux,
    projet_effectif_ou_404,
)
from webintel.science import import_export as import_service

router = APIRouter(prefix="/api/science/import", tags=["science"])


def _erreur_400(exc: import_service.ErreurImport) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.post("", include_in_schema=False)
@router.post("/")
async def importer_fichier(
    fichier: UploadFile = File(
        ..., description="Fichier à importer (.csv, .json, .xlsx, .sdf ; max 10 Mo)."
    ),
    type_donnees: str = Form(
        ..., description="Type de données : plante, compose, lien ou evidence."
    ),
    projet_id: int | None = Form(
        default=None,
        description=(
            "Projet de rattachement de l'import (validé : 403 vers un "
            "projet étranger, 404 si inexistant ; projet de la clé par "
            "défaut en mode serveur)."
        ),
    ),
    db=Depends(get_db),
    api_key=Depends(api_key_auth),
    _limite: None = Depends(limite_debit_couteux),
) -> dict:
    """Importe un fichier de données scientifiques (multipart).

    P1-4 : ``projet_id`` est VALIDÉ — résolu via ``projet_effectif_ou_404``
    (projet de la clé, 403 vers un projet étranger, en mode serveur ;
    projet « Défaut » en mode local). L'import est rattaché au projet
    déclaré et contrôlé (``rapport["contexte"]`` + bloc ``provenance``),
    jamais à un autre.

    Retourne ``200 {"rapport", "provenance", "avertissement?"}``.
    ``413`` si le fichier dépasse 10 Mo, ``400`` si le type de données ou
    le format n'est pas supporté.
    """
    nom_fichier = fichier.filename or "sans_nom"

    # P1-1/P1-4 : le projet déclaré est validé AVANT tout traitement —
    # 403 vers un projet étranger, 404 si inexistant.
    projet_effectif = projet_effectif_ou_404(db, api_key, projet_id)

    if type_donnees not in import_service.TYPES_DONNEES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Type de données non supporté : {type_donnees!r}. "
                f"Types acceptés : {', '.join(import_service.TYPES_DONNEES)}."
            ),
        )

    # Lecture bornée : on ne charge jamais plus de 10 Mo + 1 octet.
    contenu = await fichier.read(import_service.TAILLE_MAX_FICHIER + 1)
    taille_mo = len(contenu) / (1024 * 1024)
    if len(contenu) > import_service.TAILLE_MAX_FICHIER:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Fichier trop volumineux : {taille_mo:.1f} Mo "
                f"(maximum {import_service.TAILLE_MAX_FICHIER // (1024 * 1024)} Mo)."
            ),
        )
    if not contenu:
        raise HTTPException(status_code=400, detail="Le fichier est vide.")

    try:
        rapport = import_service.importer_fichier(
            db,
            contenu=contenu,
            nom_fichier=nom_fichier,
            type_donnees=type_donnees,
            project_id=projet_effectif,
            # Chantier robustesse : cohérence MIME/extension (les
            # contradictions franches sont rejetées en 400).
            type_mime=fichier.content_type,
        )
    except import_service.ErreurImport as exc:
        db.rollback()
        raise _erreur_400(exc)

    db.commit()

    reponse: dict = {
        "rapport": rapport,
        "provenance": {
            "source": nom_fichier,
            "date_import": import_service.horodatage_utc(),
            "connecteur": f"fichier:{nom_fichier}",
            "projet_id": projet_effectif,
            "provenance_stub_temporaire": import_service.PROVENANCE_STUB,
        },
    }
    if rapport.get("avertissements"):
        reponse["avertissement"] = " ; ".join(rapport["avertissements"])
    return reponse
