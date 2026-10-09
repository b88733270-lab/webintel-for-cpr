"""Stockage local des photos de collecte — WebIntel hors-ligne.

- Répertoire local : ``WEBINTEL_PHOTOS_DIR`` ou
  ``<dossier_parent_de_EXPORT_DIR>/photos`` (cohérent avec la
  convention ``webintel_data/`` ; en test : dossier temporaire).
- Arborescence : ``photos/<collecte_id>/orig_<uuid>.<ext>`` (original)
  et ``photos/<collecte_id>/mini_<uuid>.jpg`` (miniature compressée,
  256 px max, JPEG qualité 70).
- L'UI affiche les miniatures avec ``loading="lazy"`` (lazy loading
  natif) ; l'original n'est chargé qu'au clic.
- Sécurité : extensions autorisées (images uniquement), taille max
  10 Mo, nom de fichier assaini (uuid, pas de chemin utilisateur),
  lecture via une route qui vérifie que le fichier appartient bien à
  la collecte (anti path-traversal).

Aucune donnée n'est inventée : seules les photos téléversées par le
chercheur sont stockées.
"""

from __future__ import annotations

import os
import uuid

TAILLE_MAX_PHOTO = 10 * 1024 * 1024  # 10 Mo
EXTENSIONS_AUTORISEES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
TAILLE_MINIATURE_PX = 256


def repertoire_photos() -> str:
    """Répertoire racine des photos de terrain (créé si besoin)."""
    base = os.environ.get("WEBINTEL_PHOTOS_DIR", "").strip()
    if not base:
        from webintel.config.settings import get_settings

        export_dir = get_settings().EXPORT_DIR or "./webintel_data/exports"
        base = os.path.join(os.path.dirname(os.path.abspath(export_dir)),
                            "photos")
    os.makedirs(base, exist_ok=True)
    return base


def _extension_sure(nom: str) -> str:
    ext = os.path.splitext(nom or "")[1].lower()
    if ext not in EXTENSIONS_AUTORISEES:
        raise ValueError(
            f"Format de photo non pris en charge : {ext or '(aucun)'} "
            f"(autorisés : {', '.join(sorted(EXTENSIONS_AUTORISEES))})."
        )
    return ext


def enregistrer_photo(collecte_id: int, contenu: bytes, nom_origine: str,
                      legende: str = "") -> dict:
    """Stocke une photo + sa miniature compressée.

    Retourne les métadonnées à ranger dans ``CollecteTerrain.photos`` :
    ``{"fichier", "miniature", "legende", "taille_octets"}``.
    Lève ``ValueError`` si le format ou la taille est invalide.
    """
    if len(contenu) > TAILLE_MAX_PHOTO:
        raise ValueError(
            f"Photo trop lourde ({len(contenu) / 1_048_576:.1f} Mo, "
            "maximum 10 Mo)."
        )
    _extension_sure(nom_origine)
    dossier = os.path.join(repertoire_photos(), str(int(collecte_id)))
    os.makedirs(dossier, exist_ok=True)
    ident = uuid.uuid4().hex
    nom_orig = f"orig_{ident}.jpg"
    nom_mini = f"mini_{ident}.jpg"
    chemin_orig = os.path.join(dossier, nom_orig)
    chemin_mini = os.path.join(dossier, nom_mini)
    with open(chemin_orig, "wb") as fh:
        fh.write(contenu)
    try:
        _fabriquer_miniature(chemin_orig, chemin_mini)
    except Exception as exc:
        # Fichier illisible comme image : on nettoie et on refuse
        # proprement (message français), sans laisser de fichier
        # orphelin.
        for chemin in (chemin_orig, chemin_mini):
            if os.path.exists(chemin):
                os.remove(chemin)
        raise ValueError(
            "Le fichier n'est pas une image lisible : "
            f"{nom_origine!r} ({exc})."
        ) from exc
    return {
        "fichier": nom_orig,
        "miniature": nom_mini,
        "legende": legende,
        "taille_octets": len(contenu),
    }


def _fabriquer_miniature(chemin_orig: str, chemin_mini: str) -> None:
    """Miniature JPEG 256 px max, qualité 70.

    Si Pillow est indisponible (installation minimale), la miniature
    est une copie de l'original : l'affichage reste fonctionnel, le
    lazy loading HTML s'applique dans tous les cas.
    """
    try:
        from PIL import Image
    except ImportError:
        with open(chemin_orig, "rb") as src, open(chemin_mini, "wb") as dst:
            dst.write(src.read())
        return
    with Image.open(chemin_orig) as img:
        img = img.convert("RGB")
        img.thumbnail((TAILLE_MINIATURE_PX, TAILLE_MINIATURE_PX))
        img.save(chemin_mini, "JPEG", quality=70, optimize=True)


def chemin_photo(collecte_id: int, nom: str) -> str | None:
    """Chemin absolu d'une photo/miniature si elle appartient à la collecte.

    Retourne ``None`` si le nom est invalide ou hors du dossier de la
    collecte (protection path-traversal : seuls les noms ``orig_*`` /
    ``mini_*`` générés par ``enregistrer_photo`` sont acceptés).
    """
    if not nom or "/" in nom or "\\" in nom or ".." in nom:
        return None
    if not (nom.startswith("orig_") or nom.startswith("mini_")):
        return None
    dossier = os.path.join(repertoire_photos(), str(int(collecte_id)))
    chemin = os.path.abspath(os.path.join(dossier, nom))
    if not chemin.startswith(os.path.abspath(dossier) + os.sep):
        return None
    return chemin if os.path.isfile(chemin) else None


def supprimer_photos_collecte(collecte_id: int) -> None:
    """Supprime le dossier photos d'une collecte (ménage)."""
    import shutil

    dossier = os.path.join(repertoire_photos(), str(int(collecte_id)))
    if os.path.isdir(dossier):
        shutil.rmtree(dossier, ignore_errors=True)
