"""Import de données scientifiques depuis fichiers — Phase 9.

**IMPORT UNIQUEMENT** : CSV, JSON, Excel (.xlsx) et SDF minimal vers les
tables scientifiques (plantes, composés, liens plante-composé, preuves).
L'export est géré par un autre module — ce fichier ne contient AUCUNE
fonction d'export.

Conventions (comme le reste du module scientifique) :

- la session SQLAlchemy est TOUJOURS le premier argument ;
- AUCUNE fonction ne committe : l'appelant (routeur) committe ;
- créations idempotentes via ``webintel.science.service`` (``create_plant``,
  ``create_compound``, ``link_plant_compound``, ``add_evidence``) ;
- validation stricte ligne par ligne : une ligne invalide est rejetée avec
  ``{"ligne_numero", "champ", "message"}`` dans ``erreurs[]``, les lignes
  valides sont importées (jamais d'import partiel silencieux, jamais de
  rollback total à cause d'une ligne) ;
- chaque donnée importée garde sa provenance traçable via
  ``enregistrer_provenance`` (``webintel.science.connecteurs.provenance``).

SDF : il n'y a PAS de RDKit dans l'environnement. Le parseur SDF ci-dessous
est MINIMAL et honnête (pur Python) : les molécules sont séparées par
``$$$$`` et seuls les champs texte ``> <Nom>``, ``> <Formule>``,
``> <SMILES>``, ``> <InChI>``, ``> <InChIKey>`` et ``> <Classe>`` sont
extraits. La table d'atomes/liaisons du molfile n'est PAS interprétée
(aucune validation chimique, aucune stéréochimie). Les composés issus d'un
SDF sont marqués au niveau de données « normalise » avec une note de
limitation stockée dans ``external_ids``.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone

from openpyxl import load_workbook
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from webintel.science import service as _service
from webintel.science import litterature as _litterature
from webintel.science import mesures as _mesures
from webintel.science.models import Compound, Plant

__all__ = [
    "TAILLE_MAX_FICHIER",
    "TYPES_DONNEES",
    "FORMATS_FICHIER",
    "NOTE_LIMITATION_SDF",
    "ErreurImport",
    "SDFInvalide",
    "detecter_format",
    "parse_csv",
    "parse_json",
    "parse_excel",
    "parse_sdf",
    "importer_fichier",
    "importer_lignes",
    "PROVENANCE_STUB",
]

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

#: Taille maximale d'un fichier importé via l'API (10 Mo → 413 au-delà).
TAILLE_MAX_FICHIER = 10 * 1024 * 1024

#: Bornes anti-« ZIP bomb » / bombes de décompression (chantier robustesse).
#:
#: Un ``.xlsx`` est une archive ZIP : un fichier de quelques ko peut se
#: décompresser en centaines de Mo et épuiser la mémoire. Avant tout
#: ``load_workbook``, l'archive est inspectée SANS décompression complète.
XLSX_DECOMPRESSE_MAX_OCTETS = 100 * 1024 * 1024  # 100 Mo décompressés
XLSX_MEMBRES_MAX = 1000  # membres de l'archive
XLSX_RATIO_COMPRESSION_MAX = 200  # taille décompressée / compressée

#: Nombre maximal de lignes/membres acceptés par import (tous formats).
#: Protège la mémoire vive et les temps de réponse ; un import plus gros
#: doit être découpé par le laboratoire.
LIGNES_IMPORT_MAX = 50_000

#: Signature magique d'une archive ZIP (début de fichier).
SIGNATURE_ZIP = b"PK\x03\x04"

#: Types de données importables.
TYPES_DONNEES = ("plante", "compose", "lien", "evidence", "mesure")

#: Formats de fichier supportés, par extension (sans le point).
FORMATS_FICHIER = ("csv", "json", "xlsx", "sdf")

#: Note de limitation stockée avec chaque composé issu d'un SDF.
NOTE_LIMITATION_SDF = (
    "Import SDF minimal (pur Python, sans RDKit) : seuls les champs texte "
    "« > <Nom> », « > <Formule> », « > <SMILES> », « > <InChI> », "
    "« > <InChIKey> » et « > <Classe> » sont extraits. La table "
    "d'atomes/liaisons du molfile n'est PAS interprétée : aucune validation "
    "chimique, aucune stéréochimie, aucun calcul de formule ou de masse."
)

_AVANT_SDF_NORMALISE = (
    "Parseur SDF minimal : seuls les champs texte ont été extraits, la "
    "structure chimique (atomes/liaisons) n'a pas été interprétée "
    "(voir docs/import-export.md)."
)


# ---------------------------------------------------------------------------
# Provenance — branchement contractuel avec fallback temporaire
# ---------------------------------------------------------------------------

try:  # Module créé par l'agent « connecteurs » (travail en parallèle).
    from webintel.science.connecteurs.provenance import (  # type: ignore
        enregistrer_provenance as _enregistrer_provenance_reel,
    )

    PROVENANCE_STUB = False
except ImportError:  # pragma: no cover - remplacé à l'intégration finale.

    PROVENANCE_STUB = True
    _enregistrer_provenance_reel = None


def enregistrer_provenance(
    session: Session,
    *,
    type_donnees: str,
    item_id: int,
    connecteur_nom: str,
    version_donnees: str | None = None,
    licence: str | None = None,
):
    """Enregistre la provenance d'un item importé.

    Délègue au module contractuel ``webintel.science.connecteurs.provenance``
    quand il existe ; sinon utilise un STUB TEMPORAIRE (aucune persistance)
    en attendant l'intégration finale par le coordinateur.
    """
    if _enregistrer_provenance_reel is not None:
        return _enregistrer_provenance_reel(
            session,
            type_donnees=type_donnees,
            item_id=item_id,
            connecteur_nom=connecteur_nom,
            version_donnees=version_donnees,
            licence=licence,
        )
    # --- STUB TEMPORAIRE : ne persiste rien, retourne un récapitulatif. ---
    return {
        "stub_temporaire": True,
        "type_donnees": type_donnees,
        "item_id": item_id,
        "connecteur_nom": connecteur_nom,
        "version_donnees": version_donnees,
        "licence": licence,
    }


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------


class ErreurImport(ValueError):
    """Erreur d'import (fichier ou paramètre invalide) — message en français."""


class SDFInvalide(ErreurImport):
    """Le contenu fourni n'est pas du SDF valide."""


# ---------------------------------------------------------------------------
# Utilitaires de parsing des champs
# ---------------------------------------------------------------------------


def _nettoyer(valeur) -> str | None:
    """Normalise une valeur brute : chaîne dépouillée, vide → None."""
    if valeur is None:
        return None
    texte = str(valeur).strip()
    return texte if texte else None


def _parse_liste(valeur) -> list[str] | None:
    """« a ; b ; c » → ["a", "b", "c"]. None si vide.

    Passe tel quel une liste déjà parsée (entrée JSON native).
    """
    if isinstance(valeur, (list, tuple)):
        items = [str(item).strip() for item in valeur if _nettoyer(item) is not None]
        return items or None
    texte = _nettoyer(valeur)
    if texte is None:
        return None
    items = [morceau.strip() for morceau in texte.split(";")]
    items = [item for item in items if item]
    return items or None


def _parse_dict(valeur) -> dict | None:
    """JSON objet ou « k=v ; k2=v2 » → dict. None si vide.

    Passe tel quel un dict déjà parsé (entrée JSON native, conversion SDF).
    """
    if isinstance(valeur, dict):
        return dict(valeur) or None
    texte = _nettoyer(valeur)
    if texte is None:
        return None
    if texte.startswith("{"):
        try:
            obj = json.loads(texte)
        except json.JSONDecodeError as exc:
            raise ErreurImport(f"Dictionnaire JSON invalide : {texte!r}.") from exc
        if not isinstance(obj, dict):
            raise ErreurImport(f"Dictionnaire JSON invalide : {texte!r}.")
        return obj
    resultat: dict[str, str] = {}
    for morceau in texte.split(";"):
        morceau = morceau.strip()
        if not morceau or "=" not in morceau:
            continue
        cle, _, val = morceau.partition("=")
        resultat[cle.strip()] = val.strip()
    return resultat or None


def _parse_float(valeur, champ: str) -> float | None:
    texte = _nettoyer(valeur)
    if texte is None:
        return None
    try:
        return float(texte.replace(",", "."))
    except ValueError:
        raise ErreurImport(f"Champ « {champ} » : {texte!r} n'est pas un nombre.")


def _parse_int(valeur, champ: str) -> int | None:
    texte = _nettoyer(valeur)
    if texte is None:
        return None
    try:
        return int(float(texte.replace(",", ".")))
    except ValueError:
        raise ErreurImport(f"Champ « {champ} » : {texte!r} n'est pas un entier.")


# ---------------------------------------------------------------------------
# Spécifications des colonnes par type de données
# ---------------------------------------------------------------------------
#
# Chaque entrée : nom_champ -> (requis, fonction_parse).
# Les noms sont les en-têtes CSV / clés JSON / en-têtes Excel.

_SPEC_PLANTE = {
    "nom_scientifique": (True, _nettoyer),
    "famille": (False, _nettoyer),
    "genre": (False, _nettoyer),
    "espece": (False, _nettoyer),
    "noms_vernaculaires": (False, _parse_dict),
    "synonymes": (False, _parse_liste),
    "description": (False, _nettoyer),
    "distribution": (False, _nettoyer),
    "parties_utilisees": (False, _parse_liste),
    "ids_externes": (False, _parse_dict),
}

_SPEC_COMPOSE = {
    "nom": (True, _nettoyer),
    "formule": (False, _nettoyer),
    "poids_moleculaire": (False, lambda v: _parse_float(v, "poids_moleculaire")),
    "smiles": (False, _nettoyer),
    "inchi": (False, _nettoyer),
    "inchikey": (False, _nettoyer),
    "classe_chimique": (False, _nettoyer),
    "synonymes": (False, _parse_liste),
    "ids_externes": (False, _parse_dict),
}

_SPEC_LIEN = {
    "plante": (True, _nettoyer),
    "compose": (True, _nettoyer),
    "partie_plante": (False, _nettoyer),
    "type_association": (False, _nettoyer),
    "concentration": (False, lambda v: _parse_float(v, "concentration")),
    "unite_concentration": (False, _nettoyer),
    "methode_extraction": (False, _nettoyer),
    "publication": (False, _nettoyer),
    "doi": (False, _nettoyer),
    "annee": (False, lambda v: _parse_int(v, "annee")),
    "confiance": (False, _nettoyer),
}

_SPEC_EVIDENCE = {
    "type_source": (False, _nettoyer),
    # --- v2.2 : publications locales (thèses, mémoires, rapports...) ---
    "type_document": (False, _nettoyer),
    "institution": (False, _nettoyer),
    "fichier_local": (False, _nettoyer),
    "langue": (False, _nettoyer),
    "pays_edition": (False, _nettoyer),
    "reference": (False, _nettoyer),
    "titre": (False, _nettoyer),
    "doi": (False, _nettoyer),
    "pmid": (False, _nettoyer),
    "url": (False, _nettoyer),
    "auteurs": (False, _parse_liste),
    "annee": (False, lambda v: _parse_int(v, "annee")),
    "extrait": (False, _nettoyer),
}

_SPEC_MESURE = {
    # --- v2.2 : mesures expérimentales (colonnes du contrat) ------------
    "sample": (False, _nettoyer),
    "compound": (False, _nettoyer),
    "dose": (False, lambda v: _parse_float(v, "dose")),
    "dose_unite": (False, _nettoyer),
    "concentration": (False, lambda v: _parse_float(v, "concentration")),
    "concentration_unite": (False, _nettoyer),
    "response": (False, lambda v: _parse_float(v, "response")),
    "response_unite": (False, _nettoyer),
    "replicate": (False, lambda v: _parse_int(v, "replicate")),
    "assay": (False, _nettoyer),
    "organism": (False, _nettoyer),
    "cell_line": (False, _nettoyer),
    "time": (False, lambda v: _parse_float(v, "time")),
    "time_unite": (False, _nettoyer),
    "unit": (False, _nettoyer),
    "control": (False, _nettoyer),
}

_SPECS = {
    "plante": _SPEC_PLANTE,
    "compose": _SPEC_COMPOSE,
    "lien": _SPEC_LIEN,
    "evidence": _SPEC_EVIDENCE,
    "mesure": _SPEC_MESURE,
}

_CONFANCES_LIEN = ("publie", "rapporte", "incertain")
_TYPES_SOURCE_EVIDENCE = (
    "publication",
    "base_de_donnees",
    "site_expert",
    "these",
    # --- v2.2 : documents locaux ---------------------------------------
    "memoire",
    "rapport",
    "rapport_local",
    "communication",
    "document_local",
    "travaux_universitaires",
    "autre",
)


def valider_ligne(
    type_donnees: str, ligne: dict, ligne_numero: int
) -> tuple[dict, list[dict]]:
    """Valide une ligne brute contre la spec de ``type_donnees``.

    Retourne ``(champs_valides, erreurs)`` où chaque erreur vaut
    ``{"ligne_numero", "champ", "message"}``. Les champs inconnus sont
    ignorés silencieusement (tolérance aux colonnes supplémentaires).
    """
    spec = _SPECS[type_donnees]
    champs: dict = {}
    erreurs: list[dict] = []

    def _erreur(champ: str, message: str) -> None:
        erreurs.append(
            {"ligne_numero": ligne_numero, "champ": champ, "message": message}
        )

    for nom_champ, (requis, parseur) in spec.items():
        brut = ligne.get(nom_champ)
        try:
            valeur = parseur(brut)
        except ErreurImport as exc:
            _erreur(nom_champ, str(exc))
            continue
        if requis and valeur is None:
            _erreur(nom_champ, f"Champ requis manquant : « {nom_champ} ».")
            continue
        if valeur is not None:
            champs[nom_champ] = valeur

    if type_donnees == "lien" and not erreurs:
        confiance = champs.get("confiance")
        if confiance is not None and confiance not in _CONFANCES_LIEN:
            _erreur(
                "confiance",
                f"Valeur invalide : {confiance!r}. "
                f"Valeurs acceptées : {', '.join(_CONFANCES_LIEN)}.",
            )
    if type_donnees == "evidence" and not erreurs:
        if not champs.get("reference") and not champs.get("titre"):
            _erreur(
                "reference",
                "Une preuve doit avoir au moins une « reference » ou un « titre ».",
            )
        type_source = champs.get("type_source") or "publication"
        if type_source not in _TYPES_SOURCE_EVIDENCE:
            _erreur(
                "type_source",
                f"Valeur invalide : {type_source!r}. Valeurs acceptées : "
                f"{', '.join(_TYPES_SOURCE_EVIDENCE)}.",
            )
        type_document = champs.get("type_document")
        if type_document is not None and type_document not in _TYPES_DOCUMENT_LOCAUX:
            _erreur(
                "type_document",
                f"Valeur invalide : {type_document!r}. Valeurs acceptées : "
                f"{', '.join(_TYPES_DOCUMENT_LOCAUX)}.",
            )

    return champs, erreurs


# ---------------------------------------------------------------------------
# Détection du format
# ---------------------------------------------------------------------------


def detecter_format(nom_fichier: str) -> str:
    """Détecte le format depuis l'extension du nom de fichier.

    Lève ``ErreurImport`` (→ 400 côté API) si l'extension est inconnue.
    """
    extension = nom_fichier.rsplit(".", 1)[-1].lower() if "." in nom_fichier else ""
    if extension in FORMATS_FICHIER:
        return extension
    raise ErreurImport(
        f"Format de fichier non supporté : {extension or '(sans extension)'!r}. "
        f"Formats acceptés : {', '.join('.' + f for f in FORMATS_FICHIER)}."
    )


# Types MIME manifestement incompatibles avec un fichier de données
# (beaucoup de clients envoient `application/octet-stream` ou omettent le
# type : on ne rejette que les contradictions FRANCHES, jamais les
# déclarations vagues — la vraie sécurité reste `verifier_contenu`).
_MIME_INCOMPATIBLES = ("image/", "video/", "audio/", "font/", "model/")


def verifier_coherence_mime(
    type_mime: str | None, format_fichier: str
) -> None:
    """Rejette un type MIME déclaré franchement contradictoire avec l'extension.

    Chantier robustesse — ex. : un « .csv » déclaré « image/png ». Les
    déclarations vagues (`application/octet-stream`, vide, `text/*`) sont
    acceptées : trop de clients HTTP légitimes les envoient.
    """
    if not type_mime:
        return
    mime = type_mime.split(";")[0].strip().lower()
    if mime.startswith(_MIME_INCOMPATIBLES):
        raise ErreurImport(
            f"Type MIME déclaré ({mime!r}) incompatible avec un fichier "
            f"« .{format_fichier} » : import refusé."
        )


def verifier_contenu(contenu: bytes, format_fichier: str) -> None:
    """Vérifie la cohérence signature/contenu AVANT tout parsing.

    Chantier robustesse — rejette proprement (``ErreurImport`` → 400) :
    - une archive ZIP déguisée en ``.csv`` / ``.json`` / ``.sdf`` ;
    - un ``.xlsx`` qui n'est pas une archive ZIP (XLSX = ZIP) ;
    - un JSON qui ne commence ni par ``{`` ni par ``[``.
    """
    if not contenu:
        raise ErreurImport("Le fichier est vide.")
    if format_fichier in ("csv", "sdf"):
        if contenu[:4] == SIGNATURE_ZIP:
            raise ErreurImport(
                f"Le fichier « .{format_fichier} » est en réalité une archive "
                "ZIP : renommez-le en .zip ou fournissez un vrai fichier "
                f".{format_fichier}."
            )
    elif format_fichier == "xlsx":
        if contenu[:4] != SIGNATURE_ZIP:
            raise ErreurImport(
                "Le fichier « .xlsx » n'est pas un classeur Excel valide "
                "(un .xlsx est une archive ZIP)."
            )
    elif format_fichier == "json":
        premier = contenu.lstrip()[:1]
        if premier not in (b"{", b"["):
            raise ErreurImport(
                "Le fichier JSON doit commencer par « { » ou « [ »."
            )


def controler_archive_xlsx(contenu: bytes) -> None:
    """Inspecte l'archive ZIP d'un .xlsx SANS décompression complète.

    Lève ``ErreurImport`` (→ 400) si :
    - la taille décompressée totale dépasse ``XLSX_DECOMPRESSE_MAX_OCTETS`` ;
    - le nombre de membres dépasse ``XLSX_MEMBRES_MAX`` ;
    - le ratio de compression dépasse ``XLSX_RATIO_COMPRESSION_MAX``
      (signature typique d'une « ZIP bomb ») ;
    - un membre a un chemin absolu ou contient ``..`` (traversal).
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile as exc:
        raise ErreurImport(f"Archive .xlsx illisible : {exc}.") from exc
    with archive:
        membres = archive.infolist()
        if len(membres) > XLSX_MEMBRES_MAX:
            raise ErreurImport(
                f"Archive .xlsx suspecte : {len(membres)} membres "
                f"(maximum {XLSX_MEMBRES_MAX})."
            )
        total_decompresse = 0
        total_compresse = 0
        for membre in membres:
            nom = membre.filename
            if nom.startswith(("/", "\\")) or ".." in nom.split("/"):
                raise ErreurImport(
                    f"Archive .xlsx suspecte : membre au chemin dangereux "
                    f"({nom!r})."
                )
            total_decompresse += membre.file_size
            total_compresse += membre.compress_size
            if total_decompresse > XLSX_DECOMPRESSE_MAX_OCTETS:
                raise ErreurImport(
                    "Archive .xlsx suspecte : taille décompressée "
                    f"({total_decompresse // (1024 * 1024)} Mo) supérieure à "
                    f"{XLSX_DECOMPRESSE_MAX_OCTETS // (1024 * 1024)} Mo "
                    "(possible « ZIP bomb »)."
                )
        if total_compresse > 0:
            ratio = total_decompresse / total_compresse
            if ratio > XLSX_RATIO_COMPRESSION_MAX:
                raise ErreurImport(
                    f"Archive .xlsx suspecte : ratio de compression "
                    f"({ratio:.0f}) supérieur à {XLSX_RATIO_COMPRESSION_MAX} "
                    "(possible « ZIP bomb »)."
                )


# ---------------------------------------------------------------------------
# Parseurs de fichiers → liste de (ligne_numero, dict_brut)
# ---------------------------------------------------------------------------


def _lignes_non_vides(paires: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
    """Écarte les lignes entièrement vides (ni erreur, ni import)."""
    return [
        (numero, ligne)
        for numero, ligne in paires
        if any(_nettoyer(v) is not None for v in ligne.values())
    ]


def parse_csv(contenu: bytes) -> list[tuple[int, dict]]:
    """Parse un CSV (UTF-8, virgule ou point-virgule) → [(n°, dict)]."""
    try:
        texte = contenu.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurImport(
            "Le fichier CSV doit être encodé en UTF-8."
        ) from exc
    if not texte.strip():
        raise ErreurImport("Le fichier CSV est vide.")
    premiere_ligne = texte.splitlines()[0]
    delimiteur = (
        ";" if ";" in premiere_ligne and "," not in premiere_ligne else ","
    )
    try:
        lecteur = csv.DictReader(io.StringIO(texte), delimiter=delimiteur)
    except csv.Error as exc:
        raise ErreurImport(f"CSV illisible : {exc}.") from exc
    if not lecteur.fieldnames:
        raise ErreurImport("CSV sans en-tête : la première ligne doit nommer les colonnes.")
    paires = []
    try:
        for numero, ligne in enumerate(lecteur, start=2):
            if len(paires) >= LIGNES_IMPORT_MAX:
                raise ErreurImport(
                    f"CSV trop volumineux : plus de {LIGNES_IMPORT_MAX} lignes. "
                    "Découpez le fichier en plusieurs imports."
                )
            paires.append((numero, {k: v for k, v in ligne.items() if k is not None}))
    except csv.Error as exc:
        # P1 robustesse (audit adversarial 2e passe) : l'itération du
        # lecteur était hors du try — un champ dépassant
        # ``csv.field_size_limit`` (128 Ko) levait ``_csv.Error`` → 500.
        # Désormais rejeté proprement (→ 400).
        raise ErreurImport(
            f"CSV illisible : {exc}."
        ) from exc
    return _lignes_non_vides(paires)


def parse_json(contenu: bytes) -> list[tuple[int, dict]]:
    """Parse un JSON (liste d'objets) → [(n°, dict)]."""
    try:
        texte = contenu.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurImport("Le fichier JSON doit être encodé en UTF-8.") from exc
    try:
        donnees = json.loads(texte)
    except json.JSONDecodeError as exc:
        raise ErreurImport(f"JSON invalide : {exc}.") from exc
    if isinstance(donnees, dict) and isinstance(donnees.get("items"), list):
        donnees = donnees["items"]
    if not isinstance(donnees, list):
        raise ErreurImport(
            "Le JSON doit être une liste d'objets "
            "(ex. [{\"nom_scientifique\": \"...\"}, ...])."
        )
    if len(donnees) > LIGNES_IMPORT_MAX:
        raise ErreurImport(
            f"JSON trop volumineux : plus de {LIGNES_IMPORT_MAX} éléments. "
            "Découpez le fichier en plusieurs imports."
        )
    paires = []
    for index, element in enumerate(donnees, start=1):
        if not isinstance(element, dict):
            raise ErreurImport(
                f"Élément n°{index} : chaque élément doit être un objet JSON."
            )
        paires.append((index, element))
    return _lignes_non_vides(paires)


def parse_excel(contenu: bytes) -> list[tuple[int, dict]]:
    """Parse un .xlsx (première feuille, ligne 1 = en-têtes) → [(n°, dict)]."""
    # Chantier robustesse : contrôle anti-« ZIP bomb » AVANT décompression.
    controler_archive_xlsx(contenu)
    try:
        classeur = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    except Exception as exc:
        raise ErreurImport(f"Fichier Excel illisible : {exc}.") from exc
    feuille = classeur.active
    paires = []
    entetes = None
    for decalage, valeurs in enumerate(feuille.iter_rows(values_only=True), start=1):
        if decalage - 1 > LIGNES_IMPORT_MAX:
            raise ErreurImport(
                f"Excel trop volumineux : plus de {LIGNES_IMPORT_MAX} lignes. "
                "Découpez le fichier en plusieurs imports."
            )
        if decalage == 1:
            entetes = [_nettoyer(v) for v in valeurs]
            if not any(entetes):
                raise ErreurImport(
                    "Excel sans en-tête : la première ligne doit nommer les colonnes."
                )
            continue
        ligne = {
            entete: valeur
            for entete, valeur in zip(entetes, valeurs)
            if entete is not None
        }
        paires.append((decalage, ligne))
    if entetes is None:
        raise ErreurImport("La feuille Excel est vide.")
    return _lignes_non_vides(paires)


# ---------------------------------------------------------------------------
# Parseur SDF minimal (pur Python, sans RDKit)
# ---------------------------------------------------------------------------

_RE_PROPRIETE_SDF = re.compile(r"^>\s*<([^>]+)>\s*$")
_RE_LIGNE_COMPTES = re.compile(r"^\s*\d{1,4}\s+\d{1,4}\s")


def _bloc_ressemble_molfile(bloc: str) -> bool:
    """Heuristique honnête : un bloc molfile a ≥ 4 lignes et une ligne de
    comptes (nb atomes / nb liaisons) en 4e position."""
    lignes = [l for l in bloc.splitlines() if l.strip()]
    if len(lignes) < 4:
        return False
    return bool(_RE_LIGNE_COMPTES.match(lignes[3]))


def _extraire_proprietes_sdf(bloc: str) -> dict[str, str]:
    """Extrait les paires « > <Nom> » / valeur d'un bloc SDF."""
    proprietes: dict[str, str] = {}
    nom_courant: str | None = None
    accumulateur: list[str] = []

    def _vider():
        nonlocal nom_courant
        if nom_courant is not None:
            valeur = "\n".join(accumulateur).strip()
            if valeur:
                proprietes[nom_courant] = valeur
        nom_courant = None
        accumulateur.clear()

    for ligne in bloc.splitlines():
        correspondance = _RE_PROPRIETE_SDF.match(ligne.strip())
        if correspondance:
            _vider()
            nom_courant = correspondance.group(1).strip()
        elif nom_courant is not None:
            accumulateur.append(ligne)
    _vider()
    return proprietes


def parse_sdf(contenu: bytes) -> list[tuple[int, dict]]:
    """Parse un SDF minimal → [(n° molécule, dict)].

    Règles honnêtes :
    - molécules séparées par ``$$$$`` ;
    - chaque bloc doit avoir un nom (``> <Nom>`` ou 1re ligne d'en-tête) ;
    - champs texte extraits : Nom, Formule, SMILES, InChI, InChIKey, Classe ;
    - la table d'atomes/liaisons n'est JAMAIS interprétée.

    Lève ``SDFInvalide`` si le contenu n'est pas du SDF.
    """
    try:
        texte = contenu.decode("utf-8")
    except UnicodeDecodeError:
        texte = contenu.decode("latin-1")
    if "$$$$" not in texte:
        raise SDFInvalide(
            "Ce fichier n'est pas du SDF valide : séparateur « $$$$ » "
            "introuvable entre les molécules."
        )
    blocs = [b.strip("\n") for b in texte.split("$$$$")]
    blocs = [b for b in blocs if b.strip()]
    if not blocs:
        raise SDFInvalide("Fichier SDF vide : aucune molécule trouvée.")
    if len(blocs) > LIGNES_IMPORT_MAX:
        raise SDFInvalide(
            f"SDF trop volumineux : plus de {LIGNES_IMPORT_MAX} molécules. "
            "Découpez le fichier en plusieurs imports."
        )

    molecules: list[tuple[int, dict]] = []
    for numero, bloc in enumerate(blocs, start=1):
        proprietes = _extraire_proprietes_sdf(bloc)
        lignes = [l for l in bloc.splitlines() if l.strip()]
        nom = (
            proprietes.get("Nom")
            or proprietes.get("Name")
            or (lignes[0].strip() if lignes else None)
        )
        if not nom:
            raise SDFInvalide(
                f"Molécule n°{numero} : aucun nom trouvé "
                "(ni champ « > <Nom> », ni ligne d'en-tête)."
            )
        if not proprietes and not _bloc_ressemble_molfile(bloc):
            raise SDFInvalide(
                f"Molécule n°{numero} ({nom!r}) : bloc sans table molfile "
                "ni propriété « > <...> » — ce n'est pas du SDF valide."
            )
        molecule = {
            "nom": nom,
            "formule": proprietes.get("Formule"),
            "smiles": proprietes.get("SMILES"),
            "inchi": proprietes.get("InChI"),
            "inchikey": proprietes.get("InChIKey"),
            "classe_chimique": proprietes.get("Classe"),
            "poids_moleculaire": proprietes.get("Poids") or proprietes.get("Masse"),
            "_sdf_bloc": bloc,
        }
        molecules.append((numero, molecule))
    return molecules


# ---------------------------------------------------------------------------
# Import ligne par ligne (idempotent)
# ---------------------------------------------------------------------------


def _appliquer_mises_a_jour(objet, champs: dict, ignorables: set[str]) -> bool:
    """Applique ``champs`` sur un objet existant. Retourne True si au moins
    un champ a changé (→ « mis_a_jour »), False sinon (→ « ignore »)."""
    modifie = False
    for cle, valeur in champs.items():
        if cle in ignorables:
            continue
        actuel = getattr(objet, cle, None)
        if actuel != valeur:
            setattr(objet, cle, valeur)
            modifie = True
    return modifie


def _seuls_fournis(**candidats) -> dict:
    """Ne garde que les champs effectivement fournis (non None).

    Garantit qu'un réimport partiel n'écrase jamais une valeur existante
    par None — condition de l'idempotence stricte (2e import → « ignores »).
    """
    return {cle: valeur for cle, valeur in candidats.items() if valeur is not None}


def _importer_plante(session: Session, champs: dict) -> tuple[Plant, bool, bool]:
    """→ (plante, créée ?, mise_à_jour ?)."""
    ids_externes = champs.get("ids_externes")
    plante, creee = _service.create_plant(
        session,
        champs["nom_scientifique"],
        family=champs.get("famille"),
        genus=champs.get("genre"),
        species=champs.get("espece"),
        synonyms=champs.get("synonymes") or [],
        vernacular_names=champs.get("noms_vernaculaires") or {},
        description=champs.get("description"),
        distribution=champs.get("distribution"),
        used_parts=champs.get("parties_utilisees") or [],
        external_ids=dict(ids_externes) if ids_externes else {},
    )
    if creee:
        return plante, True, False
    a_jour = _seuls_fournis(
        family=champs.get("famille"),
        genus=champs.get("genre"),
        species=champs.get("espece"),
        synonyms=champs.get("synonymes"),
        vernacular_names=champs.get("noms_vernaculaires"),
        description=champs.get("description"),
        distribution=champs.get("distribution"),
        used_parts=champs.get("parties_utilisees"),
    )
    if ids_externes:
        a_jour["external_ids"] = {**(plante.external_ids or {}), **ids_externes}
    maj = _appliquer_mises_a_jour(plante, a_jour, ignorables=set())
    return plante, False, maj


def _importer_compose(session: Session, champs: dict) -> tuple[Compound, bool, bool]:
    """→ (composé, créé ?, mis_à_jour ?). Lève ValueError sur conflit InChIKey."""
    ids_externes = champs.get("ids_externes")
    compose, cree = _service.create_compound(
        session,
        champs["nom"],
        formula=champs.get("formule"),
        molecular_weight=champs.get("poids_moleculaire"),
        smiles=champs.get("smiles"),
        inchi=champs.get("inchi"),
        inchikey=champs.get("inchikey"),
        chemical_class=champs.get("classe_chimique"),
        synonyms=champs.get("synonymes") or [],
        external_ids=dict(ids_externes) if ids_externes else {},
    )
    if cree:
        return compose, True, False
    a_jour = _seuls_fournis(
        formula=champs.get("formule"),
        molecular_weight=champs.get("poids_moleculaire"),
        smiles=champs.get("smiles"),
        inchi=champs.get("inchi"),
        chemical_class=champs.get("classe_chimique"),
        synonyms=champs.get("synonymes"),
    )
    if ids_externes:
        a_jour["external_ids"] = {**(compose.external_ids or {}), **ids_externes}
    maj = _appliquer_mises_a_jour(
        compose, a_jour, ignorables=set()
    )
    # Note : l'inchikey n'est jamais modifiée ici (identifiant unique).
    return compose, False, maj


def _trouver_plante_par_nom(session: Session, nom: str) -> Plant | None:
    return (
        session.query(Plant).filter(Plant.scientific_name == nom).one_or_none()
    )


def _trouver_compose_par_nom(session: Session, nom: str) -> Compound | None:
    return session.query(Compound).filter(Compound.name == nom).one_or_none()


def _importer_lien(session: Session, champs: dict):
    """→ (lien, créé ?, mis_à_jour ?). Lève ErreurImport si plante/composé
    introuvable (le nom doit correspondre EXACTEMENT à un objet existant)."""
    plante = _trouver_plante_par_nom(session, champs["plante"])
    if plante is None:
        raise ErreurImport(
            f"Plante introuvable : {champs['plante']!r} "
            "(le nom doit correspondre exactement à une plante existante)."
        )
    compose = _trouver_compose_par_nom(session, champs["compose"])
    if compose is None:
        raise ErreurImport(
            f"Composé introuvable : {champs['compose']!r} "
            "(le nom doit correspondre exactement à un composé existant)."
        )
    lien, cree = _service.link_plant_compound(
        session,
        plant_id=plante.id,
        compound_id=compose.id,
        plant_part=champs.get("partie_plante"),
        publication=champs.get("publication"),
        association_type=champs.get("type_association"),
        concentration=champs.get("concentration"),
        concentration_unit=champs.get("unite_concentration"),
        extraction_method=champs.get("methode_extraction"),
        doi=champs.get("doi"),
        year=champs.get("annee"),
        confidence=champs.get("confiance") or "rapporte",
    )
    if cree:
        return lien, True, False
    a_jour = _seuls_fournis(
        association_type=champs.get("type_association"),
        concentration=champs.get("concentration"),
        concentration_unit=champs.get("unite_concentration"),
        extraction_method=champs.get("methode_extraction"),
        doi=champs.get("doi"),
        year=champs.get("annee"),
        confidence=champs.get("confiance"),
    )
    maj = _appliquer_mises_a_jour(lien, a_jour, ignorables=set())
    return lien, False, maj


def _importer_evidence(session: Session, champs: dict):
    """→ (preuve, créée ?, mise_à_jour ?). Idempotent avec OU sans DOI.

    Déduplication : DOI → PMID → titre normalisé (+ année), via
    ``litterature.trouver_evidence_existante``. Les preuves sans DOI
    ni PMID reçoivent un identifiant interne (``WBF-AAAA-NNNN``,
    marqué « identifiant interne WebIntel »).
    """
    type_document = champs.get("type_document") or champs.get("type_source") or "publication"
    if type_document == "publication":
        type_document = "article"
    if type_document not in _TYPES_DOCUMENT_LOCAUX:
        type_document = "document_local"
    existante = _litterature.trouver_evidence_existante(
        session,
        doi=champs.get("doi"),
        pmid=champs.get("pmid"),
        titre=champs.get("titre"),
        annee=champs.get("annee"),
        reference=champs.get("reference"),
    )
    if existante is not None:
        maj = _appliquer_mises_a_jour(
            existante,
            _seuls_fournis(
                excerpt=champs.get("extrait"),
                institution=champs.get("institution"),
                document_type=type_document,
            ),
            ignorables=set(),
        )
        return existante, False, maj
    preuve, creee = _service.add_evidence(
        session,
        doi=champs.get("doi"),
        source_type=champs.get("type_source") or "publication",
        document_type=type_document,
        reference=champs.get("reference") or "",
        title=champs.get("titre") or "",
        pmid=champs.get("pmid"),
        url=champs.get("url"),
        authors=champs.get("auteurs") or [],
        year=champs.get("annee"),
        excerpt=champs.get("extrait"),
        institution=champs.get("institution"),
        fichier_local=champs.get("fichier_local"),
        langue=champs.get("langue"),
        pays_edition=champs.get("pays_edition"),
    )
    if creee and not preuve.doi and not preuve.pmid:
        preuve.identifiant_interne = _litterature.generer_identifiant_interne(
            session, preuve.year
        )
        session.flush()
    if creee:
        return preuve, True, False
    a_jour = _seuls_fournis(
        source_type=champs.get("type_source"),
        document_type=type_document,
        reference=champs.get("reference"),
        title=champs.get("titre"),
        pmid=champs.get("pmid"),
        url=champs.get("url"),
        authors=champs.get("auteurs"),
        year=champs.get("annee"),
        excerpt=champs.get("extrait"),
        institution=champs.get("institution"),
        fichier_local=champs.get("fichier_local"),
        langue=champs.get("langue"),
        pays_edition=champs.get("pays_edition"),
    )
    maj = _appliquer_mises_a_jour(preuve, a_jour, ignorables=set())
    return preuve, False, maj


def _importer_mesure(session: Session, champs: dict):
    """→ (mesure, créée ?, mise_à_jour ?). Import de mesures expérimentales.

    Chaque ligne crée une mesure brute (``niveau_chaine="brut"``,
    privée par défaut). Les unités sont validées : tout écart est
    enregistré dans ``mesure.avertissements`` (jamais corrigé).
    Une ligne sans ``sample`` ni composé est rejetée (erreur explicite).
    """
    if not champs.get("sample") and not champs.get("compound"):
        raise ErreurImport(
            "Une mesure exige au moins « sample » ou « compound »."
        )
    compound_id = None
    nom_compose = (champs.get("compound") or "").strip() or None
    if nom_compose:
        compose = session.query(Compound).filter(
            Compound.name == nom_compose
        ).one_or_none()
        if compose is not None:
            compound_id = compose.id
    mesure = _mesures.creer_mesure(
        session,
        sample=(champs.get("sample") or "").strip() or None,
        compound_id=compound_id,
        compound_name=nom_compose,
        dose=champs.get("dose"),
        dose_unite=champs.get("dose_unite"),
        concentration=champs.get("concentration"),
        concentration_unite=champs.get("concentration_unite"),
        response=champs.get("response"),
        response_unite=champs.get("response_unite"),
        replicate=champs.get("replicate"),
        assay=(champs.get("assay") or "").strip() or None,
        organism=(champs.get("organism") or "").strip() or None,
        cell_line=(champs.get("cell_line") or "").strip() or None,
        time_value=champs.get("time"),
        time_unite=champs.get("time_unite"),
        unite=champs.get("unit"),
        control=(champs.get("control") or "").strip() or None,
        provenance={"canal": "import_fichier"},
    )
    return mesure, True, False


#: Types de documents acceptés (publications locales) — voir litterature.
_TYPES_DOCUMENT_LOCAUX = _litterature.TYPES_DOCUMENT

_IMPORTEURS = {
    "plante": _importer_plante,
    "compose": _importer_compose,
    "lien": _importer_lien,
    "evidence": _importer_evidence,
    "mesure": _importer_mesure,
}


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------


def _nouveau_rapport(type_donnees: str, nom_fichier: str) -> dict:
    return {
        "type_donnees": type_donnees,
        "fichier": nom_fichier,
        "crees": 0,
        "mis_a_jour": 0,
        "ignores": 0,
        "erreurs": [],
        "avertissements": [],
    }


def importer_lignes(
    session: Session,
    type_donnees: str,
    lignes: list[tuple[int, dict]],
    nom_fichier: str,
) -> dict:
    """Importe des lignes déjà parsées/validées (ne committe jamais).

    Retourne le rapport ``{crees, mis_a_jour, ignores, erreurs, ...}``.
    Chaque item créé ou mis à jour est tracé via ``enregistrer_provenance``
    (connecteur ``"fichier:<nom_fichier>"``).
    """
    if type_donnees not in _IMPORTEURS:
        raise ErreurImport(
            f"Type de données non supporté : {type_donnees!r}. "
            f"Types acceptés : {', '.join(TYPES_DONNEES)}."
        )
    rapport = _nouveau_rapport(type_donnees, nom_fichier)
    importeur = _IMPORTEURS[type_donnees]
    connecteur_nom = f"fichier:{nom_fichier}"

    # Garde-fou mémoire (chantier PERSISTANCE + PERFORMANCE) : sur un gros
    # fichier, la session SQLAlchemy accumulerait tous les objets importés
    # (identity map) jusqu'au commit final du routeur. Tous les
    # ``_TAILLE_PAQUET`` lignes : flush (écrit en transaction, SANS
    # committer — le routeur reste seul maître du commit) puis détachement
    # des objets déjà traités. Sûr : l'objet courant est entièrement
    # exploité (provenance, avertissements) AVANT le détachement de fin
    # de paquet, et chaque importeur relit en base ce dont il a besoin.
    _TAILLE_PAQUET = 500
    for numero_paquet, (ligne_numero, ligne_brute) in enumerate(lignes, start=1):
        champs, erreurs_validation = valider_ligne(type_donnees, ligne_brute, ligne_numero)
        if erreurs_validation:
            rapport["erreurs"].extend(erreurs_validation)
            continue
        try:
            objet, cree, mis_a_jour = importeur(session, champs)
        except ErreurImport as exc:
            rapport["erreurs"].append(
                {
                    "ligne_numero": ligne_numero,
                    "champ": "",
                    "message": str(exc),
                }
            )
            continue
        except ValueError as exc:
            # Ex. conflit InChIKey remonté par le service : erreur de ligne,
            # pas d'import partiel silencieux, pas de rollback total.
            rapport["erreurs"].append(
                {
                    "ligne_numero": ligne_numero,
                    "champ": "",
                    "message": str(exc),
                }
            )
            continue
        except SQLAlchemyError as exc:
            # Panne base de données : la transaction est compromise, on
            # annule tout et on lève une erreur explicite.
            session.rollback()
            raise ErreurImport(
                f"Import interrompu à la ligne {ligne_numero} : "
                f"erreur de base de données ({exc}). Aucune donnée de ce "
                "fichier n'a été conservée."
            ) from exc

        if cree:
            rapport["crees"] += 1
        elif mis_a_jour:
            rapport["mis_a_jour"] += 1
        else:
            rapport["ignores"] += 1

        if cree or mis_a_jour:
            enregistrer_provenance(
                session,
                type_donnees=type_donnees,
                item_id=objet.id,
                connecteur_nom=connecteur_nom,
                version_donnees=None,
                licence=None,
            )
            # Avertissements non bloquants portés par l'objet (ex. unités
            # incohérentes d'une mesure) : remontés dans le rapport avec
            # leur numéro de ligne — signalés, jamais corrigés.
            for avertissement in getattr(objet, "avertissements", None) or []:
                rapport["avertissements"].append(
                    f"Ligne {ligne_numero} : {avertissement}"
                )

        if numero_paquet % _TAILLE_PAQUET == 0:
            # Garde-fou mémoire : écrit le paquet en transaction (sans
            # committer) puis détache les objets déjà traités de la
            # session — la mémoire reste bornée sur les gros fichiers.
            session.flush()
            session.expunge_all()

    return rapport


def importer_fichier(
    session: Session,
    contenu: bytes,
    nom_fichier: str,
    type_donnees: str,
    *,
    project_id: int | None = None,
    type_mime: str | None = None,
) -> dict:
    """Importe un fichier complet (détection du format par extension).

    Le SDF n'est accepté que pour ``type_donnees="compose"``. Les composés
    issus d'un SDF sont marqués au niveau de données « normalise » avec un
    avertissement explicite ; les autres imports sont au niveau « publie ».
    Ne committe jamais.

    P1-4 : ``project_id`` (projet de rattachement de l'import, déjà
    validé par l'appelant) est enregistré dans
    ``rapport["contexte"]["projet_id"]`` — l'import s'écrit dans le
    projet déclaré et contrôlé, jamais ailleurs.

    Chantier robustesse : ``type_mime`` (déclaré par le client HTTP)
    est contrôlé pour cohérence franche avec l'extension.
    """
    if type_donnees not in TYPES_DONNEES:
        raise ErreurImport(
            f"Type de données non supporté : {type_donnees!r}. "
            f"Types acceptés : {', '.join(TYPES_DONNEES)}."
        )
    format_fichier = detecter_format(nom_fichier)
    # Chantier robustesse : cohérence MIME/extension PUIS signature/contenu
    # AVANT tout parsing (rejette les ZIP déguisés, les faux .xlsx, les
    # faux JSON).
    verifier_coherence_mime(type_mime, format_fichier)
    verifier_contenu(contenu, format_fichier)
    if format_fichier == "sdf" and type_donnees != "compose":
        raise ErreurImport(
            "Le format SDF n'est supporté que pour le type de données "
            f"« compose » (reçu : {type_donnees!r})."
        )

    if format_fichier == "csv":
        lignes = parse_csv(contenu)
    elif format_fichier == "json":
        lignes = parse_json(contenu)
    elif format_fichier == "xlsx":
        lignes = parse_excel(contenu)
    else:  # sdf
        lignes = _lignes_sdf_vers_composes(parse_sdf(contenu))

    rapport = importer_lignes(session, type_donnees, lignes, nom_fichier)
    if project_id is not None:
        # P1-4 : rattachement de l'import au projet déclaré et contrôlé.
        rapport["contexte"] = {"projet_id": project_id}
    if format_fichier == "sdf":
        rapport["niveau_donnees"] = "normalise"
        rapport["avertissements"].append(_AVANT_SDF_NORMALISE)
    else:
        rapport["niveau_donnees"] = "publie"
    return rapport


def _lignes_sdf_vers_composes(
    molecules: list[tuple[int, dict]],
) -> list[tuple[int, dict]]:
    """Convertit les molécules SDF parsées en lignes « compose ».

    Ajoute la note de limitation dans ``ids_externes`` (traçabilité).
    """
    lignes = []
    for numero, mol in molecules:
        ids_externes = {
            "format_source": "sdf",
            "note_import_sdf": NOTE_LIMITATION_SDF,
        }
        lignes.append(
            (
                numero,
                {
                    "nom": mol.get("nom"),
                    "formule": mol.get("formule"),
                    "poids_moleculaire": mol.get("poids_moleculaire"),
                    "smiles": mol.get("smiles"),
                    "inchi": mol.get("inchi"),
                    "inchikey": mol.get("inchikey"),
                    "classe_chimique": mol.get("classe_chimique"),
                    "ids_externes": ids_externes,
                },
            )
        )
    return lignes


def horodatage_utc() -> str:
    """Horodatage ISO-8601 UTC (pour la réponse API)."""
    return datetime.now(timezone.utc).isoformat()
