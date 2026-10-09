"""Sauvegarde / restauration complète — WebIntel hors-ligne.

- ``exporter_projet(session, chemin_zip, project_id=None)`` : exporte
  les tables scientifiques + terrain + file de synchronisation dans
  un ZIP versionné (``manifest.json`` : format, version de schéma,
  version WebIntel, date, SHA-256 par fichier et global,
  ``project_id``) + les photos de terrain. ``project_id`` donné :
  seules les lignes du projet sont exportées (les modèles sans
  colonne ``project_id`` sont des données globales de référence,
  exportées en entier) et les photos sont limitées aux collectes du
  projet. Les tables contenant des secrets (clés API…) sont
  EXCLUES.
- ``importer_projet(session, chemin_zip, forcer=False)`` : vérifie
  l'intégrité (SHA-256 de chaque fichier AVANT toute écriture) et la
  compatibilité du schéma, puis restaure. Rapport
  ``{"crees", "mis_a_jour", "ignores", "conflits", "details_conflits",
  "photos_restaurees"}`` — une ligne existante et différente n'est
  JAMAIS écrasée silencieusement : sans ``forcer=True`` elle est
  comptée en ``conflits`` et conservée telle quelle.

Ronde « installation fraîche » : l'export d'une installation puis
l'import sur une base vide restaure l'intégralité des données
(tests : ``tests/test_offline_sauvegarde.py``).
"""

from __future__ import annotations

import binascii
import hashlib
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

SCHEMA_VERSION = "1.0"
FORMAT = "webintel-backup"

_TABLES_PREFIX = "tables/"
_PHOTOS_PREFIX = "photos/"

#: Bornes anti « zip bomb » (audit adversarial 2e passe) : vérifiées sur
#: les métadonnées (``ZipInfo``) AVANT toute décompression, donc sans
#: allouer la taille annoncée. Une sauvegarde légitime = JSON de tables
#: (quelques Mo) + photos ≤ 10 Mo chacune.
_TAILLE_MAX_MEMBRE = 256 * 1024 * 1024      # 256 Mo par membre
_TAILLE_MAX_TOTALE = 1024 * 1024 * 1024     # 1 Go décompressé au total
_RATIO_MAX_COMPRESSION = 100               # file_size / compress_size
_NB_MAX_MEMBRES = 100_000


def _verifier_membres_zip(zf: zipfile.ZipFile) -> None:
    """Refuse une archive ZIP dangereuse AVANT toute décompression.

    Lève ``ValueError`` si : trop de membres, un membre chiffré, une
    taille annoncée excessive, un ratio de compression anormal (bombe),
    ou un membre non vide de taille compressée nulle.
    """
    infos = zf.infolist()
    if len(infos) > _NB_MAX_MEMBRES:
        raise ValueError(
            f"Archive rejetée : trop de membres ({len(infos)}, "
            f"maximum {_NB_MAX_MEMBRES})."
        )
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ValueError(
                f"Archive rejetée : le membre « {info.filename} » est "
                "chiffré (non pris en charge)."
            )
        if info.file_size > _TAILLE_MAX_MEMBRE:
            raise ValueError(
                f"Archive rejetée : le membre « {info.filename} » dépasse "
                f"la taille maximale ({info.file_size / 1_048_576:.0f} Mo "
                f"annoncés, maximum "
                f"{_TAILLE_MAX_MEMBRE // 1_048_576} Mo)."
            )
        total += info.file_size
        if total > _TAILLE_MAX_TOTALE:
            raise ValueError(
                "Archive rejetée : taille décompressée totale annoncée "
                f"> {_TAILLE_MAX_TOTALE // 1_073_741_824} Go."
            )
        if info.file_size > 0 and info.compress_size == 0:
            raise ValueError(
                f"Archive rejetée : membre « {info.filename} » incohérent "
                "(taille compressée nulle)."
            )
        if info.file_size > 0 and \
                info.file_size / info.compress_size > _RATIO_MAX_COMPRESSION:
            raise ValueError(
                f"Archive rejetée : le membre « {info.filename} » présente "
                "un ratio de compression anormal "
                f"({info.file_size // max(info.compress_size, 1)}:1) — "
                "possible bombe de décompression."
            )


def _lire_membre_borne(zf: zipfile.ZipFile, nom: str) -> bytes:
    """Lit un membre en flux borné, avec vérification du CRC.

    Remplace ``zf.read()`` : ne charge jamais plus que
    ``_TAILLE_MAX_MEMBRE`` et échoue si le CRC ne correspond pas
    (intégrité ZIP), au lieu de ``testzip()`` qui décompresse tout.
    """
    info = zf.getinfo(nom)
    morceaux: list[bytes] = []
    taille = 0
    with zf.open(nom, "r") as fh:
        while True:
            bloc = fh.read(1_048_576)
            if not bloc:
                break
            taille += len(bloc)
            if taille > _TAILLE_MAX_MEMBRE:
                raise ValueError(
                    f"Archive rejetée : le membre « {nom} » dépasse "
                    "la taille maximale pendant la lecture."
                )
            morceaux.append(bloc)
    contenu = b"".join(morceaux)
    if binascii.crc32(contenu) & 0xFFFFFFFF != info.CRC:
        raise ValueError(
            f"Archive corrompue : CRC invalide pour « {nom} »."
        )
    return contenu


def _json_safe(valeur: Any) -> Any:
    if isinstance(valeur, datetime):
        # Normalisation UTC naïve : stable entre SQLite et PostgreSQL
        # (qui renvoie des datetimes conscients), donc un export →
        # import → export produit des empreintes identiques.
        if valeur.tzinfo is not None:
            valeur = valeur.astimezone(timezone.utc).replace(tzinfo=None)
        return valeur.isoformat()
    if isinstance(valeur, dict):
        return {k: _json_safe(v) for k, v in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [_json_safe(v) for v in valeur]
    return valeur


def _modeles_exportes() -> list[tuple[str, Any]]:
    """Tables exportées, parents AVANT enfants (restauration FK-safe).

    Secrets exclus : ``ApiKey`` (empreintes de clés) et tout ce qui
    touche aux identifiants ne transite jamais par une sauvegarde.

    L'ordre est garanti sûr pour les clés étrangères par tri
    topologique (parents avant enfants), quel que soit l'ordre
    d'ajout ci-dessous.
    """
    from webintel.database.models import Project, Source, WizardSession
    from webintel.offline import models as off
    from webintel.science import lab as lab
    from webintel.science import models as sc
    from webintel.science.versions import StudyVersion
    from webintel.science.workflows import WorkflowRun

    candidats = [
        ("projects", Project),
        ("plants", sc.Plant),
        ("compounds", sc.Compound),
        ("evidences", sc.Evidence),
        ("studies", sc.Study),
        ("datasets", sc.Dataset),
        ("sources_connecteur", sc.SourceConnecteur),
        ("provenances", sc.Provenance),
        # Registre des langues (chantier PERSISTANCE + PERFORMANCE) :
        # données de référence — une langue ajoutée par le chercheur
        # doit survivre à la sauvegarde/restauration.
        ("langues", sc.Langue),
        ("lab_notes", lab.LabNote),
        ("lab_note_versions", lab.LabNoteVersion),
        ("publication_records", lab.PublicationRecord),
        ("plant_compounds", sc.PlantCompound),
        ("compound_targets", sc.CompoundTarget),
        ("plant_activities", sc.PlantActivity),
        ("research_observations", sc.ResearchObservation),
        ("simulations", sc.Simulation),
        ("combination_studies", sc.CombinationStudy),
        ("synergy_analyses", sc.SynergyAnalysis),
        ("docking_jobs", sc.DockingJob),
        ("import_provenances", sc.ImportProvenance),
        ("connecteur_cache", sc.ConnecteurCache),
        ("collectes_terrain", off.CollecteTerrain),
        ("file_synchronisation", off.FileSynchronisation),
        ("resolutions_conflit", off.ResolutionConflit),
        # Brouillons wizard (chantier PERSISTANCE + PERFORMANCE) : un
        # brouillon en cours ou une étude finalisée doit survivre à la
        # restauration — la purge TTL ne les supprime pas, la sauvegarde
        # non plus ne doit pas les perdre.
        ("wizard_sessions", WizardSession),
        # Terrain / pharmacognosie (ajout offline-first)
        ("sources", Source),
        ("specimens", sc.Specimen),
        ("mesures_experimentales", sc.MesureExperimentale),
        ("study_versions", StudyVersion),
        ("workflow_runs", WorkflowRun),
        ("noms_vernaculaires", sc.NomVernaculaire),
        ("usages_traditionnels", sc.UsageTraditionnel),
        ("vouchers", sc.Voucher),
        ("echantillons", sc.Echantillon),
        ("extractions", sc.Extraction),
        ("extraits", sc.Extrait),
        ("fractions", sc.Fraction),
        ("fraction_compounds", sc.FractionCompound),
        ("analyses_chimiques", sc.AnalyseChimique),
        ("pics_chromatographiques", sc.PicChromatographique),
        ("pharmacognosie", sc.Pharmacognosie),
    ]
    return _tri_topologique(candidats)


def _tri_topologique(candidats: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """Trie les modèles pour que les tables référencées soient exportées
    (et donc importées) AVANT celles qui les référencent.

    Déterministe (ordre alphabétique à dépendances égales). En cas de
    cycle résiduel, l'ordre alphabétique est utilisé pour le reste.
    """
    noms = {nom for nom, _ in candidats}
    par_nom = dict(candidats)
    dependances: dict[str, set[str]] = {}
    for nom, modele in candidats:
        fks = {fk.column.table.name for fk in modele.__table__.foreign_keys}
        dependances[nom] = {d for d in fks if d in noms and d != nom}
    tries: list[tuple[str, Any]] = []
    while dependances:
        prets = sorted(n for n, deps in dependances.items() if not deps)
        if not prets:
            prets = sorted(dependances)
        for nom in prets:
            tries.append((nom, par_nom[nom]))
            del dependances[nom]
        for deps in dependances.values():
            deps.difference_update(prets)
    return tries


def _valider_contenus(contenus: dict) -> None:
    """Valide les lignes d'une archive AVANT toute écriture.

    Audit adversarial (2e passe) : une archive malveillante contenant
    des champs inconnus (ex. ``{"cle_inconnue": ...}``) faisait échouer
    ``modele(**valeurs)`` avec ``TypeError`` → 500, possiblement après
    des écritures partielles. Ici, chaque ligne est confrontée aux
    colonnes du modèle : tout champ inconnu lève ``ValueError`` (→ 400)
    avant la moindre écriture.
    """
    for nom, modele in _modeles_exportes():
        colonnes = {c.key for c in sa_inspect(modele).columns}
        for index, ligne in enumerate(contenus.get(nom, []), start=1):
            if not isinstance(ligne, dict):
                raise ValueError(
                    f"Archive invalide : table « {nom} », ligne n°{index} : "
                    "chaque ligne doit être un objet JSON — restauration "
                    "refusée, aucune donnée modifiée."
                )
            inconnus = sorted(set(ligne) - colonnes)
            if inconnus:
                raise ValueError(
                    f"Archive invalide : table « {nom} », ligne n°{index} : "
                    f"champs inconnus {inconnus} — restauration refusée, "
                    "aucune donnée modifiée."
                )


def _restaurer_tables(session, contenus: dict, rapport: dict,
                      forcer: bool = False) -> None:
    """Restaure les tables (parents d'abord — ordre topologique).

    - ligne absente → créée ;
    - ligne identique → ignorée (idempotent) ;
    - ligne différente + ``forcer`` → mise à jour explicite ;
    - ligne différente sans ``forcer`` → conflit, valeur locale
      conservée (jamais d'écrasement silencieux).
    """
    for nom, modele in _modeles_exportes():
        lignes = contenus.get(nom, [])
        info = {"crees": 0, "mis_a_jour": 0, "ignores": 0, "conflits": 0}
        # Clé primaire générique (certains modèles, ex. ``WizardSession``,
        # n'ont pas de colonne ``id``) — sinon la réimport créerait des
        # doublons au lieu de détecter l'existant.
        cles_pk = [c.key for c in sa_inspect(modele).mapper.primary_key]
        for donnees in lignes:
            valeurs = _deserialiser_ligne(modele, donnees)
            if len(cles_pk) == 1:
                ident = valeurs.get(cles_pk[0])
            else:
                ident = tuple(valeurs.get(k) for k in cles_pk)
            existant = session.get(modele, ident) if ident else None
            if existant is None:
                session.add(modele(**valeurs))
                info["crees"] += 1
            elif _serialiser_ligne(existant) == _json_safe(valeurs):
                info["ignores"] += 1
            elif forcer:
                for cle, val in valeurs.items():
                    if cle != "id":
                        setattr(existant, cle, val)
                info["mis_a_jour"] += 1
            else:
                info["conflits"] += 1
                rapport["details_conflits"].append({
                    "table": nom, "id": ident,
                    "raison": "ligne existante et différente — "
                              "conservée telle quelle (forcer=True "
                              "pour écraser explicitement)",
                })
            session.flush()
        rapport["tables"][nom] = info
        rapport["crees"] += info["crees"]
        rapport["mis_a_jour"] += info["mis_a_jour"]
        rapport["ignores"] += info["ignores"]
        rapport["conflits"] += info["conflits"]


def _serialiser_ligne(obj: Any) -> dict:
    donnees: dict[str, Any] = {}
    for attr in sa_inspect(obj).mapper.column_attrs:
        donnees[attr.key] = _json_safe(getattr(obj, attr.key))
    return donnees


def _deserialiser_ligne(modele: Any, donnees: dict) -> dict:
    """Reconvertit les ISO → datetime pour les colonnes temporelles."""
    from sqlalchemy import DateTime

    colonnes = {c.key: c for c in sa_inspect(modele).columns}
    sortie: dict[str, Any] = {}
    for cle, valeur in donnees.items():
        col = colonnes.get(cle)
        # TZDateTime est une INSTANCE de DateTime(timezone=True) :
        # on teste contre la classe DateTime.
        if col is not None and isinstance(valeur, str) and isinstance(
            col.type, DateTime
        ):
            try:
                # Conservé naïf : cohérent avec la normalisation de
                # l'export (idempotence : réimporter la même archive
                # donne « ignorés », pas « conflits »).
                sortie[cle] = datetime.fromisoformat(valeur).replace(
                    tzinfo=None
                )
            except ValueError:
                sortie[cle] = valeur
        else:
            sortie[cle] = valeur
    return sortie


def _sha256_fichier(chemin: str) -> str:
    h = hashlib.sha256()
    with open(chemin, "rb") as fh:
        for bloc in iter(lambda: fh.read(65536), b""):
            h.update(bloc)
    return h.hexdigest()


def exporter_projet(session: Session, chemin_zip: str,
                    project_id: int | None = None) -> dict:
    """Exporte le projet dans ``chemin_zip`` (flush, sans commit).

    Retourne le manifeste (aussi stocké dans le ZIP).

    - ``project_id=None`` : export complet de l'installation (toutes
      les tables, toutes les photos) — comportement historique.
    - ``project_id=<id>`` : seules les lignes de ce projet sont
      exportées. Les modèles sans colonne ``project_id`` (plantes,
      composés, sources : données globales de référence) sont
      exportés en entier ; la table ``projects`` ne contient que ce
      projet ; les photos sont limitées aux collectes du projet.
    """
    from webintel import __version__ as version_app
    from webintel.offline import photos as photos_mod

    tmpdir = tempfile.mkdtemp(prefix="webintel-export-")
    try:
        tables_info: dict[str, dict] = {}
        collectes_exportees: set[int] = set()
        for nom, modele in _modeles_exportes():
            # Tri déterministe sur la clé primaire (certains modèles,
            # ex. ``WizardSession``, n'ont pas de colonne ``id``).
            cles_primaires = list(modele.__table__.primary_key.columns)
            requete = session.query(modele).order_by(*cles_primaires)
            if project_id is not None:
                if nom == "projects":
                    requete = requete.filter(modele.id == project_id)
                elif "project_id" in modele.__table__.columns:
                    requete = requete.filter(
                        modele.project_id == project_id)
            objets = requete.all()
            if nom == "collectes_terrain":
                collectes_exportees = {o.id for o in objets}
            lignes = [_serialiser_ligne(o) for o in objets]
            chemin = os.path.join(tmpdir, f"{nom}.json")
            with open(chemin, "w", encoding="utf-8") as fh:
                json.dump(lignes, fh, ensure_ascii=False, indent=1)
            tables_info[nom] = {
                "lignes": len(lignes),
                "sha256": _sha256_fichier(chemin),
            }
        # Photos de terrain (binaires, jamais inventées : que l'existant).
        photos_dir = photos_mod.repertoire_photos()
        fichiers_photos: list[str] = []
        if os.path.isdir(photos_dir):
            for racine, _, fichiers in os.walk(photos_dir):
                for f in sorted(fichiers):
                    abs_f = os.path.join(racine, f)
                    rel = os.path.relpath(abs_f, photos_dir)
                    if project_id is not None:
                        # Les photos vivent dans
                        # <photos_dir>/<collecte_id>/... : on ne garde
                        # que les dossiers des collectes du projet.
                        try:
                            cid = int(rel.split(os.sep, 1)[0])
                        except (ValueError, IndexError):
                            continue
                        if cid not in collectes_exportees:
                            continue
                    fichiers_photos.append((abs_f, rel))

        manifeste = {
            "format": FORMAT,
            "schema_version": SCHEMA_VERSION,
            "webintel_version": version_app,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "tables": tables_info,
            "photos": len(fichiers_photos),
        }
        with zipfile.ZipFile(chemin_zip, "w",
                             compression=zipfile.ZIP_DEFLATED) as zf:
            for nom in tables_info:
                zf.write(os.path.join(tmpdir, f"{nom}.json"),
                         f"{_TABLES_PREFIX}{nom}.json")
            for abs_f, rel in fichiers_photos:
                zf.write(abs_f, f"{_PHOTOS_PREFIX}{rel}")
            zf.writestr("manifest.json",
                        json.dumps(manifeste, ensure_ascii=False, indent=2))
        manifeste["chemin"] = chemin_zip
        manifeste["sha256_zip"] = _sha256_fichier(chemin_zip)
        return manifeste
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def _verifier_majeur(version: str) -> str:
    return str(version).split(".")[0]


def importer_projet(session: Session, chemin_zip: str,
                    forcer: bool = False) -> dict:
    """Restaure une sauvegarde (flush, sans commit) — voir module docstring.

    Étapes : intégrité du ZIP → format + compatibilité du schéma →
    SHA-256 de chaque fichier AVANT toute écriture → restauration
    table par table → photos. Toute anomalie lève ``ValueError`` avec
    un message français explicite, sans écriture partielle masquée.
    """
    from webintel.offline import photos as photos_mod

    rapport: dict[str, Any] = {
        "crees": 0, "mis_a_jour": 0, "ignores": 0, "conflits": 0,
        "details_conflits": [], "photos_restaurees": 0,
        "tables": {},
    }
    if not zipfile.is_zipfile(chemin_zip):
        raise ValueError(
            "Fichier de sauvegarde invalide : ce n'est pas une archive ZIP."
        )
    with zipfile.ZipFile(chemin_zip, "r") as zf:
        # Anti « zip bomb » (audit adversarial 2e passe) : bornes sur les
        # métadonnées AVANT toute décompression, en remplacement de
        # testzip() qui décompressait tout en mémoire.
        _verifier_membres_zip(zf)
        try:
            manifeste = json.loads(
                _lire_membre_borne(zf, "manifest.json").decode("utf-8"))
        except KeyError:
            raise ValueError(
                "Archive invalide : manifeste « manifest.json » absent."
            )
        if manifeste.get("format") != FORMAT:
            raise ValueError(
                f"Format de sauvegarde inconnu : {manifeste.get('format')!r} "
                f"(attendu : {FORMAT!r})."
            )
        if _verifier_majeur(manifeste.get("schema_version", "")) != \
                _verifier_majeur(SCHEMA_VERSION):
            raise ValueError(
                "Sauvegarde incompatible : schéma "
                f"{manifeste.get('schema_version')!r} (logiciel : "
                f"schéma {SCHEMA_VERSION}). Mettez à jour WebIntel avant "
                "de restaurer."
            )
        # --- Intégrité : chaque fichier est vérifié AVANT toute écriture.
        tables_info = manifeste.get("tables", {})
        contenus: dict[str, list] = {}
        for nom in tables_info:
            arcname = f"{_TABLES_PREFIX}{nom}.json"
            try:
                brut = _lire_membre_borne(zf, arcname)
            except KeyError:
                raise ValueError(
                    f"Archive incomplète : fichier « {arcname} » absent."
                )
            if hashlib.sha256(brut).hexdigest() != \
                    tables_info[nom].get("sha256"):
                raise ValueError(
                    f"Intégrité compromise : l'empreinte SHA-256 de "
                    f"« {arcname} » ne correspond pas au manifeste. "
                    "Restauration refusée — aucune donnée n'a été modifiée."
                )
            contenus[nom] = json.loads(brut.decode("utf-8"))

        # --- Validation structurelle AVANT toute écriture (audit
        # adversarial 2e passe) : champs inconnus → ValueError (400),
        # jamais de TypeError 500 après écritures partielles.
        _valider_contenus(contenus)

        # --- Restauration table par table (parents d'abord).
        # La restauration n'est PAS une « modification locale » : la mise
        # en file automatique est suspendue (sinon chaque ligne restaurée
        # serait proposée à la synchronisation).
        from webintel.offline.auto_file import file_auto_suspendue

        with file_auto_suspendue():
            _restaurer_tables(session, contenus, rapport, forcer=forcer)

        # --- Photos.
        dest_photos = photos_mod.repertoire_photos()
        for membre in zf.namelist():
            if not membre.startswith(_PHOTOS_PREFIX) or membre.endswith("/"):
                continue
            rel = membre[len(_PHOTOS_PREFIX):]
            # Assainissement : rester sous le dossier photos.
            cible = os.path.abspath(os.path.join(dest_photos, rel))
            if not cible.startswith(os.path.abspath(dest_photos) + os.sep):
                continue
            os.makedirs(os.path.dirname(cible), exist_ok=True)
            with open(cible, "wb") as fh:
                fh.write(_lire_membre_borne(zf, membre))
            rapport["photos_restaurees"] += 1
    return rapport
