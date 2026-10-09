"""Routes UI v2.2 — espace chercheur (100 % français).

NOUVEAU routeur (non enregistré ici — le coordinateur l'ajoute dans
``webintel/main.py`` via ``app.include_router(chercheur_donnees_routes.router)``) :

- ``GET/POST /donnees/importer`` : import de résultats du laboratoire
  (CSV / JSON / Excel .xlsx via le pipeline existant) + association de
  l'import à une étude / plante / molécule / analyse de synergie /
  expérience (listes déroulantes, jamais de JSON à saisir) ;
- ``GET/POST /synergie/nouvelle`` : analyse de synergie simplifiée en
  langage chercheur (tableaux dose/réponse, cartes de méthodes) ;
- ``GET/POST /etudes/{id}/rapport`` (+ ``/telecharger``) : rapport
  scientifique de l'étude via le code d'export existant ;
- ``GET/POST /etudes/{id}/historique`` : historique versionné
  (append-only — la restauration crée une nouvelle version).

RÈGLES :
- aucun fichier existant n'est modifié — tout passe par les services
  existants (``import_export``, ``combinaisons``, ``synergie``,
  ``rapport``, ``versions``) ;
- données du laboratoire : badge « DONNÉES PRIVÉES — non partagées »,
  ``is_private=True`` systématique, JAMAIS de fusion automatique avec
  les données publiques ;
- formulations prudentes : « Résultat informatique », niveaux de preuve
  visibles, jamais de valeur inventée (données insuffisantes → page
  « Analyse impossible avec les données fournies. »).
"""

from __future__ import annotations

import hashlib
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from webintel import __version__ as WEBINTEL_VERSION
from webintel.api.deps import get_db
from webintel.core.utils import utcnow
from webintel.database.models import ApiKey
from webintel.science import combinaisons as comb_module
from webintel.science import docking as dock_module
from webintel.science import fiche as fiche_module
from webintel.science import import_export as import_service
from webintel.science import rapport as rapport_module
from webintel.science import versions as versions_module
from webintel.science.models import (
    CombinationStudy,
    Compound,
    Dataset,
    Plant,
    Provenance,
    ResearchObservation,
    Simulation,
    Study,
    SynergyAnalysis,
)
from webintel.science.synergie import DonneesInsuffisantes, methodes_disponibles
from webintel.ui.auth import ui_auth
from webintel.ui.routes import _project_for, _render

router = APIRouter()

TAILLE_MAX_IMPORT = import_service.TAILLE_MAX_FICHIER  # 10 Mo, comme l'API.

# Phrases d'explication des méthodes (fidèles aux docstrings des modules).
METHODES_SIMPLE = {
    "bliss": "Compare, pour chaque point de mélange, l'effet mesuré à "
             "l'effet attendu si les deux molécules agissaient indépendamment "
             "(Bliss, 1939).",
    "loewe": "Compare chaque point de mélange à l'attente d'additivité "
             "déduite des courbes dose-réponse des deux molécules seules "
             "(indice de combinaison CI ; Loewe, 1953 ; Chou-Talalay, 1984).",
    "hsa": "Compare l'effet du mélange à l'effet le plus fort des deux "
           "molécules seules, point par point — modèle simple et "
           "conservateur (Borisy et al., 2003).",
    "zip": "Compare l'effet observé à une attente calculée depuis les "
           "courbes dose-réponse ajustées des deux molécules seules "
           "(Yadav et al., 2015).",
}

_BADGE_PRIVE = "DONNÉES PRIVÉES — non partagées"


def _identite_ui(ui_key: ApiKey | None) -> str:
    if ui_key is None:
        return "ui-local"
    return f"ui:{getattr(ui_key, 'prefix', None) or '?'}"


def _etude_du_projet_ou_404(db: Session, project, etude_id: int) -> CombinationStudy:
    try:
        etude = comb_module.get_etude(db, etude_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if etude.project_id != project.id:
        raise HTTPException(status_code=404, detail="Étude introuvable.")
    return etude


def _sha256_hex_octets(contenu: bytes) -> str:
    """Empreinte SHA-256 hexadécimale d'un contenu binaire."""
    return hashlib.sha256(contenu).hexdigest()


def _sauvegarder_fichier_prive(contenu: bytes, nom_fichier: str) -> str | None:
    """Archive best-effort le fichier brut importé (traçabilité, jamais bloquant)."""
    base = os.environ.get("EXPORT_DIR")
    if not base:
        try:
            from webintel.config.settings import get_settings

            base = getattr(get_settings(), "EXPORT_DIR", None)
        except Exception:  # pragma: no cover - garde-fou
            base = None
    if not base:
        return None
    try:
        dossier = os.path.join(base, "donnees_labo_privees")
        os.makedirs(dossier, exist_ok=True)
        nom = (
            f"{utcnow().strftime('%Y%m%d%H%M%S')}_{_sha256_hex_octets(contenu)[:12]}_"
            f"{os.path.basename(nom_fichier or 'fichier')}"
        )
        chemin = os.path.join(dossier, nom)
        with open(chemin, "wb") as fh:
            fh.write(contenu)
        return chemin
    except OSError:  # pragma: no cover - garde-fou
        return None


# ---------------------------------------------------------------------------
# 1. Import de résultats du laboratoire
# ---------------------------------------------------------------------------


def _contexte_import(ui_key, project, **kw):
    ctx = dict(
        ui_key=ui_key,
        active="laboratoire",
        project=project,
        etape="televersement",
        types=import_service.TYPES_DONNEES,
        formats=import_service.FORMATS_FICHIER,
        badge_prive=_BADGE_PRIVE,
        erreur=None,
    )
    ctx.update(kw)
    return ctx


@router.get("/donnees/importer", response_class=HTMLResponse)
def importer_donnees_form(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Formulaire d'import : fichier CSV / JSON / Excel + type de données."""
    project = _project_for(db, ui_key)
    return _render("importer_donnees.html", **_contexte_import(ui_key, project))


@router.post("/donnees/importer", response_class=HTMLResponse)
async def importer_donnees_submit(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Traite l'upload via le pipeline d'import existant, crée le jeu de
    données PRIVÉ, puis affiche le formulaire d'association."""
    project = _project_for(db, ui_key)
    form = await request.form()
    type_donnees = (form.get("type_donnees") or "").strip()
    nom_jeu = (form.get("nom_jeu") or "").strip()
    description = (form.get("description") or "").strip()
    fichier = form.get("fichier")

    def _erreur(message: str):
        return _render(
            "importer_donnees.html",
            **_contexte_import(ui_key, project, erreur=message),
        )

    if type_donnees not in import_service.TYPES_DONNEES:
        return _erreur(
            f"Type de données non supporté : {type_donnees!r}. "
            f"Types acceptés : {', '.join(import_service.TYPES_DONNEES)}."
        )
    nom_fichier = getattr(fichier, "filename", None) or ""
    if not nom_fichier:
        return _erreur("Aucun fichier sélectionné.")
    contenu = await fichier.read(TAILLE_MAX_IMPORT + 1)
    if not contenu:
        return _erreur("Le fichier est vide.")
    if len(contenu) > TAILLE_MAX_IMPORT:
        return _erreur(
            f"Fichier trop volumineux ({len(contenu) / 1048576:.1f} Mo — "
            f"maximum {TAILLE_MAX_IMPORT // 1048576} Mo)."
        )
    try:
        rapport = import_service.importer_fichier(
            db,
            contenu=contenu,
            nom_fichier=nom_fichier,
            type_donnees=type_donnees,
            # P1-4 : l'import s'écrit dans le projet déclaré et contrôlé
            # (projet de la session UI), jamais ailleurs.
            project_id=project.id,
            # Chantier robustesse : cohérence MIME/extension.
            type_mime=getattr(fichier, "content_type", None),
        )
    except import_service.ErreurImport as exc:
        db.rollback()
        return _erreur(str(exc))

    # Jeu de données PRIVÉ du laboratoire (jamais fusionné automatiquement
    # aux données publiques) + archive best-effort du fichier brut.
    jeu = Dataset(
        project_id=project.id,
        name=nom_jeu or nom_fichier,
        description=description or None,
        is_private=True,
        sha256=_sha256_hex_octets(contenu),
        file_path=_sauvegarder_fichier_prive(contenu, nom_fichier),
    )
    db.add(jeu)
    db.flush()
    db.add(
        Provenance(
            entity_type="dataset",
            entity_id=jeu.id,
            source_name=f"Import laboratoire — {nom_fichier}",
            connector="donnees-labo-ui",
            data_version=(jeu.sha256 or "")[:16] or None,
            license="privé — non partagé",
        )
    )
    db.commit()
    db.refresh(jeu)

    return _render(
        "importer_donnees.html",
        **_contexte_import(
            ui_key,
            project,
            etape="associer",
            dataset=jeu,
            rapport=rapport,
            type_donnees=type_donnees,
            etudes=comb_module.list_etudes(db, project.id),
            plantes=db.query(Plant).order_by(Plant.scientific_name).all(),
            composes=db.query(Compound).order_by(Compound.name).all(),
            analyses=comb_module.list_analyses_synergie(
                db, project.id, inclure_prive=True
            ),
            experiences=db.query(ResearchObservation)
            .filter(ResearchObservation.project_id == project.id)
            .order_by(ResearchObservation.id.desc())
            .all(),
        ),
    )


# Cibles d'association : (champ du formulaire, type d'entité, modèle, libellé).
_CIBLES_ASSOCIATION = (
    ("assoc_etude", "etude", CombinationStudy, "Étude de combinaison"),
    ("assoc_plante", "plante", Plant, "Plante"),
    ("assoc_compose", "compose", Compound, "Molécule"),
    ("assoc_synergie", "analyse_synergie", SynergyAnalysis, "Analyse de synergie"),
    ("assoc_experience", "experience", ResearchObservation, "Expérience"),
)


@router.post("/donnees/associer", response_class=HTMLResponse)
async def associer_donnees_submit(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Associe le jeu de données importé à des entités (provenance, pas de
    fusion de données)."""
    project = _project_for(db, ui_key)
    form = await request.form()
    try:
        dataset_id = int(form.get("dataset_id") or 0)
    except ValueError:
        dataset_id = 0
    jeu = db.get(Dataset, dataset_id)
    if jeu is None or jeu.project_id != project.id:
        raise HTTPException(status_code=404, detail="Jeu de données introuvable.")

    associations: list[str] = []
    for champ, type_entite, modele, libelle in _CIBLES_ASSOCIATION:
        brut = (form.get(champ) or "").strip()
        if not brut:
            continue
        try:
            cible_id = int(brut)
        except ValueError:
            continue
        cible = db.get(modele, cible_id)
        if cible is None:
            continue
        # Isolation projet pour les entités projetées.
        if type_entite in ("etude", "analyse_synergie", "experience") and getattr(
            cible, "project_id", None
        ) != project.id:
            continue
        nom_cible = (
            getattr(cible, "nom", None)
            or getattr(cible, "name", None)
            or getattr(cible, "scientific_name", None)
            or getattr(cible, "protocol_ref", None)
            or f"#{cible_id}"
        )
        db.add(
            Provenance(
                entity_type=type_entite,
                entity_id=cible_id,
                source_name=(
                    f"Jeu de données « {jeu.name} » (import laboratoire, privé)"
                ),
                connector="donnees-labo-ui",
                data_version=(jeu.sha256 or "")[:16] or None,
                license="privé — non partagé",
            )
        )
        associations.append(f"{libelle} « {nom_cible} »")
    db.commit()

    return _render(
        "importer_donnees.html",
        **_contexte_import(
            ui_key,
            project,
            etape="termine",
            dataset=jeu,
            associations=associations,
        ),
    )


# ---------------------------------------------------------------------------
# 2. Analyse de synergie simplifiée
# ---------------------------------------------------------------------------


def _contexte_synergie(ui_key, project, **kw):
    fiches = methodes_disponibles()
    ctx = dict(
        ui_key=ui_key,
        active="combinaisons",
        project=project,
        fiches=fiches,
        descriptions=METHODES_SIMPLE,
        badge_prive=_BADGE_PRIVE,
        erreur=None,
        detail=None,
        form={},
    )
    ctx.update(kw)
    return ctx


@router.get("/synergie/nouvelle", response_class=HTMLResponse)
def synergie_nouvelle_form(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Formulaire chercheur : molécules A / B / combinaison + cartes de méthodes."""
    project = _project_for(db, ui_key)
    return _render("synergie_simple.html", **_contexte_synergie(ui_key, project))


def _lire_serie(form, cle_dose: str, cle_reponse: str) -> list[dict]:
    """Lit un tableau dose/réponse (lignes dynamiques du formulaire)."""
    serie = []
    for dose_brute, rep_brute in zip(
        form.getlist(cle_dose), form.getlist(cle_reponse)
    ):
        dose_brute = (dose_brute or "").strip()
        rep_brute = (rep_brute or "").strip()
        if not dose_brute and not rep_brute:
            continue
        try:
            dose = float(dose_brute.replace(",", "."))
            reponse = float(rep_brute.replace(",", "."))
        except ValueError:
            raise ValueError(
                f"Valeur non numérique ignorée : dose={dose_brute!r}, "
                f"réponse={rep_brute!r} — corrigez ou videz la ligne."
            )
        serie.append({"dose": dose, "reponse": reponse})
    return serie


def _lire_melanges(form) -> list[dict]:
    """Lit le tableau des combinaisons (dose A, dose B, réponse)."""
    melanges = []
    for dose_a, dose_b, reponse in zip(
        form.getlist("c_dose_a"), form.getlist("c_dose_b"), form.getlist("c_reponse")
    ):
        dose_a, dose_b, reponse = (
            (dose_a or "").strip(),
            (dose_b or "").strip(),
            (reponse or "").strip(),
        )
        if not dose_a and not dose_b and not reponse:
            continue
        try:
            melanges.append(
                {
                    "dose_a": float(dose_a.replace(",", ".")),
                    "dose_b": float(dose_b.replace(",", ".")),
                    "reponse": float(reponse.replace(",", ".")),
                }
            )
        except ValueError:
            raise ValueError(
                f"Ligne de combinaison non numérique : dose_a={dose_a!r}, "
                f"dose_b={dose_b!r}, réponse={reponse!r}."
            )
    return melanges


def _donnees_synergie(
    methode: str,
    serie_a: list[dict],
    serie_b: list[dict],
    melanges: list[dict],
) -> dict:
    """Construit le ``donnees`` attendu par les méthodes de synergie.

    Bliss / HSA : chaque ligne de combinaison doit reprendre EXACTEMENT une
    dose mesurée pour A seul et pour B seul (appariement par dose) ; sinon
    ``DonneesInsuffisantes`` — jamais de valeur inventée.
    Loewe / ZIP : courbes individuelles + points de mélange (le module
    vérifie lui-même le minimum requis et ajuste les courbes de Hill).
    """
    if methode in ("bliss", "hsa"):
        rep_a = {p["dose"]: p["reponse"] for p in serie_a}
        rep_b = {p["dose"]: p["reponse"] for p in serie_b}
        points = []
        for i, mel in enumerate(melanges, start=1):
            if mel["dose_a"] not in rep_a:
                raise DonneesInsuffisantes(
                    f"données insuffisantes pour la méthode {methode} : "
                    f"point n°{i} — aucune réponse mesurée pour la molécule A "
                    f"seule à la dose {mel['dose_a']} (tableau « Molécule A »)."
                )
            if mel["dose_b"] not in rep_b:
                raise DonneesInsuffisantes(
                    f"données insuffisantes pour la méthode {methode} : "
                    f"point n°{i} — aucune réponse mesurée pour la molécule B "
                    f"seule à la dose {mel['dose_b']} (tableau « Molécule B »)."
                )
            points.append(
                {
                    "dose_a": mel["dose_a"],
                    "dose_b": mel["dose_b"],
                    "reponse_a": rep_a[mel["dose_a"]],
                    "reponse_b": rep_b[mel["dose_b"]],
                    "reponse_ab": mel["reponse"],
                }
            )
        if not points:
            raise DonneesInsuffisantes(
                f"données insuffisantes pour la méthode {methode} : aucune "
                "ligne de combinaison complète (dose A, dose B, réponse)."
            )
        return {"points": points}
    # Loewe / ZIP.
    return {"courbe_a": serie_a, "courbe_b": serie_b, "melanges": melanges}


@router.post("/synergie/nouvelle", response_class=HTMLResponse)
async def synergie_nouvelle_submit(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Lance l'analyse : vérifie D'ABORD la suffisance des données via le
    code existant (``DonneesInsuffisantes`` → page « Analyse impossible
    avec les données fournies. »), sinon affiche le résultat."""
    project = _project_for(db, ui_key)
    form = await request.form()
    nom = (form.get("nom") or "").strip()
    methode = (form.get("methode") or "").strip().lower()
    nom_a = (form.get("nom_a") or "Molécule A").strip() or "Molécule A"
    nom_b = (form.get("nom_b") or "Molécule B").strip() or "Molécule B"
    form_ctx = {"nom": nom, "nom_a": nom_a, "nom_b": nom_b, "methode": methode}

    def _impossible(detail: str):
        return _render(
            "synergie_simple.html",
            **_contexte_synergie(
                ui_key,
                project,
                erreur="Analyse impossible avec les données fournies.",
                detail=detail,
                form=form_ctx,
            ),
        )

    if methode not in METHODES_SIMPLE:
        return _impossible(
            f"Méthode inconnue : {methode!r} — choisissez Bliss, Loewe, HSA ou ZIP."
        )
    try:
        serie_a = _lire_serie(form, "a_dose", "a_reponse")
        serie_b = _lire_serie(form, "b_dose", "b_reponse")
        melanges = _lire_melanges(form)
    except ValueError as exc:
        return _impossible(str(exc))
    try:
        donnees = _donnees_synergie(methode, serie_a, serie_b, melanges)
    except DonneesInsuffisantes as exc:
        return _impossible(str(exc))
    if not nom:
        nom = f"Synergie {nom_a} + {nom_b} ({methode.upper()})"
    try:
        analyse = comb_module.creer_analyse_synergie(
            db,
            project_id=project.id,
            nom=nom,
            methode=methode,
            donnees=donnees,
            is_private=True,
            cree_par=_identite_ui(ui_key),
        )
    except DonneesInsuffisantes as exc:
        db.rollback()
        return _impossible(str(exc))
    db.commit()
    db.refresh(analyse)
    resultat = analyse.resultats or {}
    return _render(
        "synergie_resultat.html",
        ui_key=ui_key,
        active="combinaisons",
        project=project,
        analyse=analyse,
        resultat=resultat,
        nom_a=nom_a,
        nom_b=nom_b,
        badge_prive=_BADGE_PRIVE,
    )


# ---------------------------------------------------------------------------
# 3. Rapport scientifique d'une étude
# ---------------------------------------------------------------------------


def _construire_rapport_etude(db: Session, project, etude: CombinationStudy) -> dict:
    """Assemble le rapport d'étude depuis les données existantes (aucune
    valeur inventée : chaque section manquante est signalée)."""
    provenance = etude.provenance or {}
    sections = (etude.resultats or {}).get("sections", [])

    def _section(prefixe: str) -> dict:
        return next(
            (s for s in sections if str(s.get("titre", "")).startswith(prefixe)), {}
        )

    plante = db.get(Plant, etude.plant_id) if etude.plant_id else None
    composes = [
        c
        for cid in (etude.compound_ids or [])
        if (c := db.get(Compound, cid)) is not None
    ]
    publications = [
        e
        for eid in (etude.evidence_ids or [])
        if (e := db.get(Study, eid)) is not None
    ]
    jeux_donnees = [
        {
            "source": p.source_name,
            "empreinte": p.data_version,
            "date": p.imported_at,
        }
        for p in db.query(Provenance)
        .filter(
            Provenance.entity_type == "etude",
            Provenance.entity_id == etude.id,
            Provenance.connector == "donnees-labo-ui",
        )
        .order_by(Provenance.id)
        .all()
    ]
    score = _section("9.").get("score_informatique") or {}
    return {
        "titre": etude.nom,
        "chercheur": provenance.get("cree_par") or "—",
        "date": etude.created_at,
        "statut": etude.statut,
        "plante": (
            {
                "nom": plante.scientific_name,
                "famille": plante.family,
                "partie": etude.plant_part,
            }
            if plante
            else None
        ),
        "molecules": [
            {
                "nom": c.name,
                "formule": c.formula,
                "smiles": c.smiles,
                "classe": c.chemical_class,
            }
            for c in composes
        ],
        "cible": etude.target,
        "publications": [
            {
                "titre": e.title,
                "auteurs": list(e.authors or []),
                "annee": e.year,
                "doi": e.doi,
                "journal": e.journal,
            }
            for e in publications
        ],
        "parametres": etude.parametres or {},
        "sections": sections,
        "score": score,
        "conclusion": _section("12.").get("conclusion", ""),
        "limites": _section("10.").get("limites", []),
        "hypotheses": _section("11.").get("hypotheses", []),
        "references": _section("7.").get("references", []),
        "jeux_donnees": jeux_donnees,
        # Niveau projet, étiqueté comme tel (honnêteté : pas de lien
        # étude↔simulation inventé).
        "simulations_projet": db.query(Simulation)
        .filter(Simulation.project_id == project.id)
        .order_by(Simulation.id)
        .all(),
        "docking_projet": dock_module.list_docking_jobs(db, project.id),
        "inputs_hash": etude.inputs_hash,
        "data_level": etude.data_level,
        "webintel_version": WEBINTEL_VERSION,
        "moteurs": {
            "chimie_infos_biblio": provenance.get("chimie_infos_biblio"),
            "chimie_disponible": provenance.get("chimie_disponible"),
            "score_version": provenance.get("score_version"),
        },
        "avertissement": comb_module.AVERTISSEMENT_ETUDE,
    }


def _rapport_pdf_forme_projet(
    db: Session, project, etude: CombinationStudy, rapport: dict
) -> dict:
    """Met en forme le rapport d'étude selon le schéma attendu par le
    générateur PDF existant (``rapport.build_pdf``) — sans inventer de
    données : chaque section reprend le contenu de l'étude."""
    nom_plante = (rapport["plante"] or {}).get("nom")
    plante = db.get(Plant, etude.plant_id) if etude.plant_id else None
    # Niveau de preuve réel de la plante (jamais « publié » par défaut).
    niveau_plante = (
        fiche_module.niveau_preuve_plante(db, plante)["niveau"]
        if plante is not None else "normalise"
    )
    return {
        "projet": {
            "id": project.id,
            "nom": f"{project.name} — étude « {etude.nom} »",
        },
        "genere_le": utcnow().isoformat(),
        "webintel_version": WEBINTEL_VERSION,
        "inclure_prive": False,
        "avertissement": rapport_module.AVERTISSEMENT_RAPPORT,
        "fiche_plantes": (
            [
                {
                    "id": etude.plant_id,
                    "nom_scientifique": nom_plante,
                    "famille": (rapport["plante"] or {}).get("famille"),
                    "parties_utilisees": (
                        [etude.plant_part] if etude.plant_part else []
                    ),
                    "data_level": niveau_plante,
                }
            ]
            if nom_plante
            else []
        ),
        "composes": [
            {
                "nom": m["nom"],
                "formule": m["formule"],
                "plantes": [nom_plante] if nom_plante else [],
            }
            for m in rapport["molecules"]
        ],
        "publications": [
            {
                "titre": p["titre"],
                "reference": p["doi"] or p["journal"],
                "doi": p["doi"],
            }
            for p in rapport["publications"]
        ],
        "donnees": {
            "observations_laboratoire": [],
            "note": (
                "Données privées du laboratoire exclues de ce rapport "
                "(séparation labo / public)."
            ),
        },
        "hypotheses": [
            {"source": f"étude « {etude.nom} »", "texte": h}
            for h in rapport["hypotheses"]
        ],
        "simulations": [],
        "combinaisons_analysees": [
            {
                "type": "etude_combinaison",
                "id": etude.id,
                "nom": etude.nom,
                "statut": etude.statut,
                "inputs_hash": etude.inputs_hash,
            }
        ],
        "resultats": {
            "nb_plantes": 1 if nom_plante else 0,
            "nb_composes": len(rapport["molecules"]),
            "nb_publications": len(rapport["publications"]),
            "nb_simulations": 0,
            "nb_combinaisons": 1,
        },
        "limites": list(rapport_module.LIMITES_RAPPORT) + list(rapport["limites"]),
        "references": [
            r.get("titre") or r.get("publication") or ""
            for r in rapport["references"]
            if isinstance(r, dict)
        ],
    }


@router.get("/etudes/{etude_id}/rapport", response_class=HTMLResponse)
def rapport_etude_page(
    request: Request,
    etude_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Page du rapport d'étude : résumé + bouton [Créer le rapport]."""
    project = _project_for(db, ui_key)
    etude = _etude_du_projet_ou_404(db, project, etude_id)
    return _render(
        "rapport_etude.html",
        ui_key=ui_key,
        active="combinaisons",
        project=project,
        etude=etude,
        genere=False,
        rapport=None,
        avertissement=comb_module.AVERTISSEMENT_ETUDE,
        badge_prive=_BADGE_PRIVE,
    )


@router.post("/etudes/{etude_id}/rapport", response_class=HTMLResponse)
def rapport_etude_generer(
    request: Request,
    etude_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Génère le rapport complet de l'étude (code d'export existant)."""
    project = _project_for(db, ui_key)
    etude = _etude_du_projet_ou_404(db, project, etude_id)
    rapport = _construire_rapport_etude(db, project, etude)
    return _render(
        "rapport_etude.html",
        ui_key=ui_key,
        active="combinaisons",
        project=project,
        etude=etude,
        genere=True,
        rapport=rapport,
        badge_prive=_BADGE_PRIVE,
    )


@router.get("/etudes/{etude_id}/rapport/telecharger")
def rapport_etude_telecharger(
    etude_id: int,
    format: str = "html",
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Téléchargement du rapport : HTML / Markdown (rendus existants) / PDF
    (générateur existant ; HTML servi avec avertissement si reportlab
    indisponible — jamais de faux PDF)."""
    project = _project_for(db, ui_key)
    etude = _etude_du_projet_ou_404(db, project, etude_id)
    base = f"rapport-etude-{etude.id}"
    if format == "markdown":
        corps = comb_module.rendre_markdown_etude(etude).encode("utf-8")
        return Response(
            content=corps,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{base}.md"'},
        )
    if format == "pdf":
        rapport = _construire_rapport_etude(db, project, etude)
        pdf = rapport_module.build_pdf(
            _rapport_pdf_forme_projet(db, project, etude, rapport)
        )
        if pdf is None:
            return Response(
                content=comb_module.rendre_html_etude(etude).encode("utf-8"),
                media_type="text/html",
                headers={
                    "X-Avertissement": (
                        "reportlab indisponible : HTML servi à la place du PDF."
                    )
                },
            )
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{base}.pdf"'},
        )
    if format not in ("html",):
        raise HTTPException(
            status_code=400,
            detail=f"Format non supporté : {format!r} (html, markdown, pdf).",
        )
    corps = comb_module.rendre_html_etude(etude).encode("utf-8")
    return Response(
        content=corps,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{base}.html"'},
    )


# ---------------------------------------------------------------------------
# 4. Historique versionné des études
# ---------------------------------------------------------------------------


@router.get("/etudes/{etude_id}/historique", response_class=HTMLResponse)
def etude_historique_page(
    request: Request,
    etude_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Historique versionné (append-only) + restauration (= nouvelle version)."""
    project = _project_for(db, ui_key)
    etude = _etude_du_projet_ou_404(db, project, etude_id)
    versions = versions_module.list_versions(db, etude.id)
    return _render(
        "etude_historique.html",
        ui_key=ui_key,
        active="combinaisons",
        project=project,
        etude=etude,
        versions=versions,
        message=request.query_params.get("message"),
        erreur=None,
    )


@router.post("/etudes/{etude_id}/historique/instantane")
async def etude_instantane(
    request: Request,
    etude_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Enregistre un instantané de l'état actuel (nouvelle version)."""
    project = _project_for(db, ui_key)
    etude = _etude_du_projet_ou_404(db, project, etude_id)
    version = versions_module.enregistrer_version(
        db, etude, cree_par=_identite_ui(ui_key)
    )
    db.commit()
    return RedirectResponse(
        url=f"/etudes/{etude.id}/historique"
        f"?message=Version+{version.numero}+enregistr%C3%A9e.",
        status_code=303,
    )


@router.post("/etudes/{etude_id}/historique/restaurer")
async def etude_restaurer(
    request: Request,
    etude_id: int,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Restaure une version : crée une NOUVELLE étude + une NOUVELLE
    version (jamais d'écrasement)."""
    project = _project_for(db, ui_key)
    etude = _etude_du_projet_ou_404(db, project, etude_id)
    form = await request.form()
    try:
        version_id = int(form.get("version_id") or 0)
    except ValueError:
        version_id = 0
    try:
        version = versions_module.get_version(db, version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if version.study_id != etude.id:
        raise HTTPException(
            status_code=404, detail="Cette version n'appartient pas à l'étude."
        )
    nouvelle = versions_module.restaurer_version(
        db, version.id, cree_par=_identite_ui(ui_key)
    )
    db.commit()
    return RedirectResponse(
        url=f"/etudes/{etude.id}/historique"
        f"?message=Restauration+de+la+version+{version.numero}+%3A+nouvelle+%C3%A9tude+%23{nouvelle.id}+cr%C3%A9%C3%A9e+%28aucune+version+%C3%A9cras%C3%A9e%29.",
        status_code=303,
    )
