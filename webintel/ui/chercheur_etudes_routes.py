"""Assistant de création d'étude — wizard en 6 étapes (UI chercheur, v2.2).

Page d'entrée : ``GET /etudes/nouvelle``. L'état du wizard est conservé
côté serveur en base de données (table ``wizard_sessions``, modèle
``webintel.database.models.WizardSession``), clé = UUID passé en
query param ``session`` — aucun JavaScript requis, formulaires HTML
classiques. Chaque étape sauvegarde en base : une étude en cours
survit au rechargement de page, à la fermeture du navigateur et au
redémarrage du serveur. TTL 30 jours (fenêtre glissante, repoussée à
chaque écriture) ; les sessions expirées sont purgées
(``purger_sessions_expirees``, appelée à la création d'une session et
par le planificateur).

IMPORTANT : ce routeur n'est PAS enregistré ici — le coordinateur
l'enregistre dans ``webintel/main.py`` via
``app.include_router(chercheur_etudes_routes.router)``.

Isolation : chaque session est rattachée à un ``project_id`` (NOT NULL)
et à une identité ``owner``. L'UUID seul ne suffit plus : chaque accès
vérifie que la session appartient au projet de la requête (accès
croisé → 403, session inconnue/expirée → 404).

Les moteurs scientifiques sont CONSOMMÉS (jamais modifiés) :
similarité / propriétés / interactions (``webintel.simulations.plugins``),
cibles documentées (``webintel.science.models.CompoundTarget``),
combinaisons (``webintel.science.combinaisons``),
synergie (``webintel.science.synergie``),
docking (``webintel.simulations.plugins.docking_adapter``, diagnostic seul).

Discipline d'affichage : formulations prudentes obligatoires, niveaux
de preuve visibles, aucune valeur inventée, une simulation n'est
jamais présentée comme une preuve.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from webintel.api.deps import get_db
from webintel.database.models import (
    ApiKey,
    Project,
    WizardSession,
    DUREE_WIZARD_SESSION_JOURS,
)
from webintel.science.models import Compound, CompoundTarget, Plant, PlantCompound
from webintel.ui.auth import ui_auth
from webintel.ui.explications import explication_pour
from webintel.ui.routes import _project_for, _render
from webintel.ui.science_combinaisons_routes import _identite_ui

router = APIRouter()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Persistance des sessions du wizard (table ``wizard_sessions``)
# ---------------------------------------------------------------------------

def _expiration_session() -> datetime:
    """Échéance TTL : 30 jours glissants à partir de maintenant (UTC)."""
    return datetime.now(timezone.utc) + timedelta(
        days=DUREE_WIZARD_SESSION_JOURS)


def _utc_conscient(valeur: datetime | None) -> datetime | None:
    """Normalise un horodatage lu en base (SQLite stocke naïf)."""
    if valeur is None:
        return None
    if valeur.tzinfo is None:
        return valeur.replace(tzinfo=timezone.utc)
    return valeur


def purger_sessions_expirees(db: Session) -> int:
    """Supprime les BROUILLONS wizard dont le TTL est dépassé.

    Comparaison faite en Python (portable SQLite/PostgreSQL : les
    horodatages tz-aware ne se comparent pas de façon homogène en SQL
    selon le dialecte). Retourne le nombre de sessions purgées.

    GARANTIE P0 (persistance) : seules les sessions ``status="en_cours"``
    sont purgées. Une session ``"terminee"`` est une étude finalisée —
    ses résultats vivent dans le brouillon (page ``/etudes/{id}/resultats``)
    — elle n'est JAMAIS supprimée par la purge TTL, même expirée.
    Les études finalisées vivent déjà en base (``combination_studies``,
    ``simulations``…) : la purge ne supprime que le brouillon du wizard.
    """
    maintenant = datetime.now(timezone.utc)
    purgees = 0
    for ligne in db.query(WizardSession).filter(
        WizardSession.status == "en_cours"
    ):
        expiration = _utc_conscient(ligne.expires_at)
        if expiration is not None and expiration <= maintenant:
            db.delete(ligne)
            purgees += 1
    if purgees:
        db.commit()
        logger.info("Wizard : %d session(s) expirée(s) purgée(s).", purgees)
    return purgees


def _etat_depuis(ligne: WizardSession) -> dict:
    """Reconstitue le dict d'état attendu par les étapes/templates.

    Valeurs par défaut garanties (le code historique indexe
    ``etat["molecules"]`` & co directement) ; les clés inconnues du
    payload sont conservées telles quelles (compatibilité ascendante).
    """
    charge = dict(ligne.payload or {})
    etat = {
        "id": ligne.session_id,
        "type_objet": charge.get("type_objet"),
        "plante_id": charge.get("plante_id"),
        "molecules": charge.get("molecules") or [],
        "analyses": charge.get("analyses") or [],
        "mode": charge.get("mode") or "simple",
        "params": charge.get("params") or {},
        "donnees_exp": charge.get("donnees_exp"),
        "resultats": charge.get("resultats"),
        "cree_le": charge.get("cree_le"),
    }
    for cle, valeur in charge.items():
        etat.setdefault(cle, valeur)
    return etat


def _sauvegarder(db: Session, ligne: WizardSession, etat: dict) -> None:
    """Persiste l'état après une étape ; repousse le TTL (fenêtre glissante)."""
    ligne.payload = {cle: valeur for cle, valeur in etat.items()
                     if cle != "id"}
    ligne.study_type = etat.get("type_objet")
    ligne.expires_at = _expiration_session()
    db.add(ligne)
    db.commit()


def _nouvelle_session(
    db: Session, project: Project, ui_key: ApiKey | None
) -> tuple[WizardSession, dict]:
    """Crée une session wizard persistée (étape 1) et son état initial."""
    try:
        purger_sessions_expirees(db)  # purge opportuniste, silencieuse
    except Exception:  # noqa: BLE001 — la purge ne bloque jamais le wizard
        logger.exception("Wizard : purge opportuniste des sessions impossible.")
    sid = uuid.uuid4().hex
    ligne = WizardSession(
        session_id=sid,
        project_id=project.id,
        owner=_identite_ui(ui_key),
        study_type=None,
        payload={
            "type_objet": None,
            "plante_id": None,
            "molecules": [],
            "analyses": [],
            "mode": "simple",
            "params": {},
            "donnees_exp": None,
            "resultats": None,
            "cree_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        status="en_cours",
        expires_at=_expiration_session(),
    )
    db.add(ligne)
    db.commit()
    db.refresh(ligne)
    return ligne, _etat_depuis(ligne)


def _charger_session(
    db: Session, project: Project, sid: str | None,
    attendu_owner: str | None = None,
) -> tuple[WizardSession, dict]:
    """Charge une session : 404 si inconnue/expirée, 403 si autre projet.

    L'UUID seul ne suffit jamais : la session doit appartenir au projet
    de la requête (isolation inter-projets). P0 sécurité : si
    ``attendu_owner`` est fourni, la session doit aussi appartenir à
    l'utilisateur courant — un brouillon d'assistant est personnel, un
    autre membre du projet muni du seul ``session_id`` (présent dans
    l'URL) ne peut ni le lire ni le modifier (403). Un brouillon expiré
    (``en_cours``) est supprimé puis répond 404 avec le même message
    qu'une session inconnue (pas d'oracle). Une session ``terminee``
    (étude finalisée) n'est jamais supprimée : elle reste lisible même
    expirée (P0 persistance).

    Audit adversarial (2e passe) : le contrôle PROJET a lieu AVANT la
    suppression d'une session expirée — une session expirée d'un autre
    projet répond 403 SANS être supprimée (aucune mutation avant
    contrôle d'accès, même classe de faille que P0 science_lab). Le
    contrôle propriétaire garde son ordre historique (après la purge
    TTL) : le contrat 403 inter-projets / inter-utilisateurs existant
    est conservé.
    """
    ligne = db.get(WizardSession, sid or "")
    if ligne is None:
        raise HTTPException(
            status_code=404,
            detail="Session d'étude introuvable ou expirée — recommencez "
                   "l'assistant.",
        )
    if ligne.project_id != project.id:
        raise HTTPException(
            status_code=403,
            detail="Cette session d'étude appartient à un autre projet.",
        )
    expiration = _utc_conscient(ligne.expires_at)
    if expiration is not None and expiration <= datetime.now(timezone.utc):
        if ligne.status == "terminee":
            # P0 persistance : une étude finalisée n'est JAMAIS supprimée
            # par expiration — ni ici, ni par purger_sessions_expirees.
            # Ses résultats restent consultables (page /etudes/{id}/resultats)
            # même après l'échéance du TTL.
            pass
        else:
            db.delete(ligne)
            db.commit()
            raise HTTPException(
                status_code=404,
                detail="Session d'étude introuvable ou expirée — "
                       "recommencez l'assistant.",
            )
    # Le contrôle propriétaire a lieu APRES la purge TTL (ordre historique
    # conservé) : une session expirée du même projet — même d'un autre
    # utilisateur — est purgée en 404, jamais lue. Seul le contrôle PROJET
    # (ci-dessus) précède toute mutation (audit adversarial 2e passe) :
    # une session expirée d'un AUTRE projet répond 403 sans être supprimée.
    if attendu_owner is not None and ligne.owner != attendu_owner:
        raise HTTPException(
            status_code=403,
            detail="Cette session d'étude appartient à un autre "
                   "utilisateur.",
        )
    return ligne, _etat_depuis(ligne)

TYPES_OBJET = {
    "plante": "Une plante",
    "molecule": "Une molécule",
    "molecules": "Plusieurs molécules",
    "combinaison": "Une combinaison",
}

ANALYSES = {
    "structures": "Analyse des structures",
    "proprietes": "Propriétés moléculaires",
    "cibles": "Recherche de cibles",
    "interactions": "Interactions connues",
    "combinaison": "Étude de combinaison",
    "docking": "Docking moléculaire",
    "experimentales": "Analyse de données expérimentales",
}

METHODES_SIMILARITE = ["structure", "combinee", "formule", "inchikey", "classe"]
METHODES_SYNERGIE = ["bliss", "loewe", "hsa", "zip"]

AVERTISSEMENT_GLOBAL = (
    "Cette analyse est informatique et ne constitue pas une preuve "
    "d'effet clinique."
)

LIMITES_GLOBALES = [
    "Les similarités et propriétés sont calculées à partir des structures "
    "fournies (SMILES) : une erreur de saisie fausse tous les résultats.",
    "Une similarité structurale élevée n'implique pas une même activité "
    "biologique.",
    "Les propriétés (logP, TPSA…) sont des estimations théoriques, pas "
    "des mesures expérimentales.",
    "Les cibles et interactions affichées sont des données rapportées "
    "dans la littérature, jamais validées par WebIntel.",
    "Un score de synergie ne peut être calculé que sur des mesures "
    "expérimentales réelles fournies par le chercheur.",
    "Le docking exige un récepteur préparé et le binaire Vina : il n'est "
    "jamais exécuté automatiquement par l'assistant.",
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vers(request: Request, etape: int, etat: dict) -> RedirectResponse:
    return RedirectResponse(
        url=f"/etudes/etape{etape}?session={etat['id']}", status_code=303
    )


def _contexte(ui_key, project, etat: dict, **extra) -> dict:
    ctx = dict(
        ui_key=ui_key,
        active="combinaisons",
        project=project,
        etat=etat,
        session_id=etat["id"],
        etape_courante=extra.pop("etape_courante", None),
        types_objet=TYPES_OBJET,
        analyses=ANALYSES,
    )
    ctx.update(extra)
    return ctx


# ---------------------------------------------------------------------------
# Étape 1 — Que voulez-vous étudier ?
# ---------------------------------------------------------------------------

@router.get("/etudes/nouvelle", response_class=HTMLResponse)
def etape1_form(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    project = _project_for(db, ui_key)
    _ligne, etat = _nouvelle_session(db, project, ui_key)
    return _render("etude_etape1.html", **_contexte(
        ui_key, project, etat, etape_courante=1))


@router.post("/etudes/etape1", response_class=HTMLResponse)
async def etape1_post(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    project = _project_for(db, ui_key)
    form = await request.form()
    ligne, etat = _nouvelle_session(db, project, ui_key)
    type_objet = (form.get("type_objet") or "").strip()
    if type_objet not in TYPES_OBJET:
        return _render("etude_etape1.html", **_contexte(
            ui_key, project, etat, etape_courante=1,
            erreur="Choisissez ce que vous voulez étudier."))
    etat["type_objet"] = type_objet
    _sauvegarder(db, ligne, etat)
    return _vers(request, 2, etat)


# ---------------------------------------------------------------------------
# Étape 2 — Choisissez vos éléments
# ---------------------------------------------------------------------------

def _composes_base(db: Session, q: str | None = None) -> list[Compound]:
    requete = db.query(Compound).order_by(Compound.name)
    if q:
        requete = requete.filter(Compound.name.ilike(f"%{q}%"))
    return requete.limit(200).all()


def _composes_plante(db: Session, plant_id: int) -> list[dict]:
    lignes = (
        db.query(PlantCompound, Compound)
        .join(Compound, PlantCompound.compound_id == Compound.id)
        .filter(PlantCompound.plant_id == plant_id)
        .order_by(Compound.name)
        .all()
    )
    vus, resultat = set(), []
    for pc, compose in lignes:
        if compose.id in vus:
            continue
        vus.add(compose.id)
        resultat.append({
            "compose": compose,
            "partie": pc.plant_part,
            "publication": pc.publication,
            "annee": pc.year,
        })
    return resultat


@router.get("/etudes/etape2", response_class=HTMLResponse)
def etape2_form(
    request: Request,
    session: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    project = _project_for(db, ui_key)
    _ligne, etat = _charger_session(db, project, session, _identite_ui(ui_key))
    if not etat["type_objet"]:
        return RedirectResponse(url="/etudes/nouvelle", status_code=303)
    plantes = db.query(Plant).order_by(Plant.scientific_name).all()
    plante = db.get(Plant, etat["plante_id"]) if etat["plante_id"] else None
    composes_plante = (
        _composes_plante(db, plante.id) if plante is not None else []
    )
    return _render("etude_etape2.html", **_contexte(
        ui_key, project, etat, etape_courante=2,
        plantes=plantes, plante=plante, composes_plante=composes_plante,
        composes=_composes_base(db, q), q=q or "",
        erreur=None, message=None))


@router.post("/etudes/etape2", response_class=HTMLResponse)
async def etape2_post(
    request: Request,
    session: str | None = None,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    project = _project_for(db, ui_key)
    ligne, etat = _charger_session(db, project, session, _identite_ui(ui_key))
    form = await request.form()
    action = (form.get("action") or "continuer").strip()

    if action == "choisir_plante":
        try:
            etat["plante_id"] = int(form.get("plante_id") or 0) or None
        except (TypeError, ValueError):
            etat["plante_id"] = None
        _sauvegarder(db, ligne, etat)
        return RedirectResponse(
            url=f"/etudes/etape2?session={etat['id']}", status_code=303)

    if action == "ajouter":
        nom = (form.get("nom_mol") or "").strip()
        smiles = (form.get("smiles_mol") or "").strip() or None
        if not nom:
            return _render("etude_etape2.html", **_contexte(
                ui_key, project, etat, etape_courante=2,
                plantes=db.query(Plant).order_by(Plant.scientific_name).all(),
                plante=db.get(Plant, etat["plante_id"]) if etat["plante_id"] else None,
                composes_plante=_composes_plante(db, etat["plante_id"]) if etat["plante_id"] else [],
                composes=_composes_base(db), q="",
                erreur="Indiquez au moins le nom de la molécule à ajouter."))
        if not any(m["nom"].lower() == nom.lower()
                   for m in etat["molecules"]):
            etat["molecules"].append({
                "nom": nom, "smiles": smiles, "db_id": None,
                "source": "saisie",
            })
        _sauvegarder(db, ligne, etat)
        return RedirectResponse(
            url=f"/etudes/etape2?session={etat['id']}", status_code=303)

    # action == "continuer" : mémorise les cases cochées
    ids = [int(v) for v in form.getlist("compound_ids") if str(v).isdigit()]
    selection = []
    for cid in ids:
        compose = db.get(Compound, cid)
        if compose is None:
            continue
        selection.append({
            "nom": compose.name, "smiles": compose.smiles,
            "db_id": compose.id, "source": "base",
        })
    # Conserve les molécules saisies manuellement (non cochables).
    saisies = [m for m in etat["molecules"] if m["source"] == "saisie"]
    vus = {m["nom"].lower() for m in saisies}
    for mol in selection:
        if mol["nom"].lower() not in vus:
            saisies.append(mol)
            vus.add(mol["nom"].lower())
    etat["molecules"] = saisies
    if not etat["molecules"]:
        return _render("etude_etape2.html", **_contexte(
            ui_key, project, etat, etape_courante=2,
            plantes=db.query(Plant).order_by(Plant.scientific_name).all(),
            plante=db.get(Plant, etat["plante_id"]) if etat["plante_id"] else None,
            composes_plante=_composes_plante(db, etat["plante_id"]) if etat["plante_id"] else [],
            composes=_composes_base(db), q="",
            erreur="Sélectionnez au moins une molécule (case à cocher) "
                   "ou ajoutez-en une par son nom."))
    _sauvegarder(db, ligne, etat)
    return _vers(request, 3, etat)


# ---------------------------------------------------------------------------
# Étape 3 — Quel type d'analyse ? (mode simple / avancé)
# ---------------------------------------------------------------------------

@router.get("/etudes/etape3", response_class=HTMLResponse)
def etape3_form(
    request: Request,
    session: str | None = None,
    mode: str | None = None,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    project = _project_for(db, ui_key)
    ligne, etat = _charger_session(db, project, session, _identite_ui(ui_key))
    if not etat["molecules"]:
        return RedirectResponse(
            url=f"/etudes/etape2?session={etat['id']}", status_code=303)
    if mode in ("simple", "avance"):
        etat["mode"] = mode
        _sauvegarder(db, ligne, etat)
    return _render("etude_etape3.html", **_contexte(
        ui_key, project, etat, etape_courante=3,
        methodes_similarite=METHODES_SIMILARITE,
        methodes_synergie=METHODES_SYNERGIE, erreur=None))


def _entier(form, cle: str, defaut: int, mini: int, maxi: int) -> int:
    try:
        valeur = int((form.get(cle) or "").strip())
    except (TypeError, ValueError, AttributeError):
        return defaut
    return max(mini, min(maxi, valeur))


def _flottant(form, cle: str, defaut: float, mini: float, maxi: float) -> float:
    try:
        valeur = float(str(form.get(cle) or "").strip().replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return defaut
    if not math.isfinite(valeur):
        return defaut
    return max(mini, min(maxi, valeur))


def _parser_donnees_exp(texte: str) -> dict:
    """Parse le tableau de données expérimentales (une mesure par ligne).

    Format accepté (sans JSON) : ``dose_a,dose_b,reponse_a,reponse_b,
    reponse_ab`` — séparateur virgule ou point-virgule, lignes vides et
    lignes commençant par ``#`` ignorées.
    """
    points, erreurs = [], []
    for numero, ligne in enumerate((texte or "").splitlines(), start=1):
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#"):
            continue
        separateur = ";" if ";" in ligne else ","
        morceaux = [m.strip().replace(",", ".") for m in ligne.split(separateur)]
        if len(morceaux) != 5:
            erreurs.append(
                f"Ligne {numero} : 5 valeurs attendues "
                f"(dose_a, dose_b, reponse_a, reponse_b, reponse_ab), "
                f"reçu {len(morceaux)}.")
            continue
        try:
            valeurs = [float(m) for m in morceaux]
        except ValueError:
            erreurs.append(f"Ligne {numero} : valeurs numériques attendues.")
            continue
        points.append({
            "dose_a": valeurs[0], "dose_b": valeurs[1],
            "reponse_a": valeurs[2], "reponse_b": valeurs[3],
            "reponse_ab": valeurs[4],
        })
    return {"points": points, "erreurs": erreurs}


@router.post("/etudes/etape3", response_class=HTMLResponse)
async def etape3_post(
    request: Request,
    session: str | None = None,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    project = _project_for(db, ui_key)
    ligne, etat = _charger_session(db, project, session, _identite_ui(ui_key))
    form = await request.form()
    analyses = [a for a in form.getlist("analyses") if a in ANALYSES]
    mode = "avance" if (form.get("mode") or "simple") == "avance" else "simple"
    etat["mode"] = mode
    if not analyses:
        return _render("etude_etape3.html", **_contexte(
            ui_key, project, etat, etape_courante=3,
            methodes_similarite=METHODES_SIMILARITE,
            methodes_synergie=METHODES_SYNERGIE,
            erreur="Choisissez au moins un type d'analyse."))

    params: dict = {
        "methode_similarite": (
            form.get("methode_similarite") or "structure"
            if (form.get("methode_similarite") or "structure") in METHODES_SIMILARITE
            else "structure"
        ),
    }
    if mode == "avance":
        params.update({
            "rayon_morgan": _entier(form, "rayon_morgan", 2, 0, 4),
            "taille_empreinte": _entier(form, "taille_empreinte", 2048, 64, 8192),
            "seuil_tanimoto": _flottant(form, "seuil_tanimoto", 0.7, 0.0, 1.0),
            "seed_vina": _entier(form, "seed_vina", 42, 0, 2 ** 31 - 1),
            "exhaustiveness": _entier(form, "exhaustiveness", 8, 1, 32),
            "methode_synergie": (
                (form.get("methode_synergie") or "bliss").strip().lower()
                if (form.get("methode_synergie") or "bliss").strip().lower() in METHODES_SYNERGIE
                else "bliss"
            ),
        })
        if "experimentales" in analyses:
            parse = _parser_donnees_exp(form.get("donnees_exp") or "")
            etat["donnees_exp"] = {
                "methode": params["methode_synergie"],
                "points": parse["points"],
                "erreurs": parse["erreurs"],
            }
    else:
        if "experimentales" in analyses:
            etat["donnees_exp"] = {"methode": "bliss", "points": [],
                                   "erreurs": []}
    etat["analyses"] = analyses
    etat["params"] = params
    _sauvegarder(db, ligne, etat)
    return _vers(request, 4, etat)


# ---------------------------------------------------------------------------
# Étape 4 — Vérification
# ---------------------------------------------------------------------------

@router.get("/etudes/etape4", response_class=HTMLResponse)
def etape4_form(
    request: Request,
    session: str | None = None,
    mode: str | None = None,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    project = _project_for(db, ui_key)
    ligne, etat = _charger_session(db, project, session, _identite_ui(ui_key))
    if not etat["analyses"]:
        return RedirectResponse(
            url=f"/etudes/etape3?session={etat['id']}", status_code=303)
    if mode in ("simple", "avance"):
        etat["mode"] = mode
        _sauvegarder(db, ligne, etat)
    return _render("etude_etape4.html", **_contexte(
        ui_key, project, etat, etape_courante=4))


# ---------------------------------------------------------------------------
# Moteurs — exécution (étape 5)
# ---------------------------------------------------------------------------

def _bloc(titre: str, niveau: str, statut: str, **champs) -> dict:
    bloc = {
        "titre": titre,
        "niveau": niveau,          # publie|normalise|prediction|simulation|
                                   # hypothese|experimental
        "statut": statut,          # ok|non_lance|echec
        "resume": "",
        "lignes": [],
        "explications": [],
        "provenance": {},
        "note": "",
    }
    bloc.update(champs)
    return bloc


def _molecules_avec_smiles(etat: dict) -> list[dict]:
    return [m for m in etat["molecules"] if (m.get("smiles") or "").strip()]


def _formule_depuis_smiles(smiles: str | None) -> str | None:
    """Formule brute dérivée d'un SMILES via RDKit (None si impossible)."""
    if not (smiles or "").strip():
        return None
    try:
        from webintel.science import chimie

        if not chimie.rdkit_disponible():
            return None
        desc = chimie.descripteurs(smiles)
        formule = (desc or {}).get("formule")
        return formule.strip() if isinstance(formule, str) and formule.strip() else None
    except Exception:  # noqa: BLE001 — jamais de crash, jamais d'invention
        return None


def _entrees_moteur(molecules: list[dict], db: Session) -> tuple[list[dict], list[str]]:
    """Construit les entrées normalisées des moteurs (formule brute requise).

    La formule vient de la base (composé connu) ou est dérivée du SMILES
    via RDKit. Les molécules sans formule déterminable sont exclues
    (noms retournés) — jamais de formule inventée.
    """
    entrees, exclues = [], []
    for mol in molecules:
        formule = None
        if mol.get("db_id"):
            compose = db.get(Compound, mol["db_id"])
            if compose is not None and (compose.formula or "").strip():
                formule = compose.formula.strip()
        if not formule:
            formule = _formule_depuis_smiles(mol.get("smiles"))
        if not formule:
            exclues.append(mol["nom"])
            continue
        entrees.append({
            "nom": mol["nom"],
            "formule": formule,
            "smiles": (mol.get("smiles") or "").strip() or None,
        })
    return entrees, exclues


def _lancer_structures(etat: dict, db: Session, project=None) -> dict:
    from webintel.simulations.plugins import molecular_similarity

    entrees, exclues = _entrees_moteur(_molecules_avec_smiles(etat), db)
    if len(entrees) < 2:
        return _bloc(
            "Analyse des structures", "prediction", "non_lance",
            note="Au moins 2 molécules avec un SMILES valide (formule "
                 "brute déterminable) sont nécessaires pour comparer des "
                 "structures (aucune valeur inventée).")
    params = etat.get("params", {})
    methode = params.get("methode_similarite", "structure")
    try:
        resultat = molecular_similarity.run(
            {"composes": entrees}, {"methode": methode})
    except Exception as exc:  # noqa: BLE001 — moteur défensif, jamais de crash
        return _bloc("Analyse des structures", "prediction", "echec",
                     note=f"Le moteur de similarité a refusé le calcul : {exc}")
    seuil = params.get("seuil_tanimoto", 0.7)
    paires = []
    for paire in resultat.get("paires", []):
        score = paire.get("score")
        paires.append({
            "a": paire.get("a"), "b": paire.get("b"),
            "score": (round(score, 4) if score is not None else None),
            "au_dessus_du_seuil": (
                score is not None and score >= seuil),
        })
    bloc = _bloc(
        "Analyse des structures", "prediction", "ok",
        resume=(
            "Résultat informatique : similarité calculée entre "
            f"{resultat.get('nombre_paires', 0)} paire(s) de molécules "
            f"(méthode « {methode} »). Une similarité élevée ne signifie "
            "pas une même activité biologique."
        ),
        lignes=paires,
        explications=["tanimoto"],
        provenance={
            "moteur": resultat.get("moteur"),
            "version": resultat.get("version"),
            "parametres": {"methode": methode, "seuil_affichage": seuil},
            "rdkit_actif": resultat.get("rdkit_actif"),
            "calcule_le": _utcnow_iso(),
        },
    )
    # Mode avancé : variante paramétrée (même pile RDKit, paramètres
    # explicites rayon/taille — le moteur v2.1 utilise ses réglages
    # internes, cette variante les expose honnêtement).
    if (etat.get("mode") == "avance" and resultat.get("rdkit_actif")
            and (params.get("rayon_morgan", 2), params.get("taille_empreinte", 2048)) != (2, 2048)):
        bloc["variante_avancee"] = _similarite_parametree(
            [e for e in entrees if e.get("smiles")],
            params.get("rayon_morgan", 2),
            params.get("taille_empreinte", 2048))
    if exclues:
        bloc["note"] = ("Molécules exclues du calcul (formule brute "
                        "indéterminable) : " + ", ".join(exclues) + ".")
    return bloc


def _similarite_parametree(entrees: list[dict], rayon: int,
                           taille: int) -> dict:
    """Tanimoto honnête sur empreintes de Morgan paramétrées (mode avancé)."""
    from webintel.science import chimie

    empreintes = {}
    indisponibles = []
    for entree in entrees:
        fp = chimie.empreinte_morgan(entree["smiles"], rayon=rayon,
                                     taille=taille)
        if fp is None:
            indisponibles.append(entree["nom"])
        else:
            empreintes[entree["nom"]] = fp
    noms = list(empreintes)
    paires = []
    for i in range(len(noms)):
        for j in range(i + 1, len(noms)):
            score = chimie.tanimoto_fp(empreintes[noms[i]],
                                       empreintes[noms[j]])
            paires.append({
                "a": noms[i], "b": noms[j],
                "score": (round(score, 4) if score is not None else None),
            })
    paires.sort(key=lambda p: (p["score"] is None,
                               -(p["score"] or 0.0), p["a"], p["b"]))
    return {
        "titre": (f"Variante avancée — empreinte de Morgan "
                  f"(rayon={rayon}, taille={taille})"),
        "paires": paires,
        "indisponibles": indisponibles,
        "note": ("Similarité structurelle calculée avec vos paramètres. "
                 "Le moteur standard de WebIntel utilise rayon=2, taille=2048."),
    }


def _lancer_proprietes(etat: dict, db: Session, project=None) -> dict:
    from webintel.simulations.plugins import property_prediction

    if not etat["molecules"]:
        return _bloc("Propriétés moléculaires", "prediction", "non_lance",
                     note="Aucune molécule sélectionnée.")
    entrees, exclues = _entrees_moteur(etat["molecules"], db)
    if not entrees:
        return _bloc(
            "Propriétés moléculaires", "prediction", "non_lance",
            note="Aucune molécule avec une formule brute déterminable "
                 "(aucune valeur inventée).")
    try:
        resultat = property_prediction.run({"composes": entrees}, {})
    except Exception as exc:  # noqa: BLE001
        return _bloc("Propriétés moléculaires", "prediction", "echec",
                     note=f"Le moteur de propriétés a refusé le calcul : {exc}")
    lignes = []
    for fiche in resultat.get("composes", []):
        def _valeur(bloc_dict, cle="valeur"):
            if isinstance(bloc_dict, dict):
                return bloc_dict.get(cle)
            return None

        lip = fiche.get("lipinski") or {}
        lignes.append({
            "nom": fiche.get("nom"),
            "masse_molaire": _valeur(fiche.get("masse_molaire")),
            "logp": _valeur(fiche.get("logp")),
            "tpsa": _valeur(fiche.get("tpsa")),
            "donneurs_h": _valeur(fiche.get("donneurs_hydrogene")),
            "accepteurs_h": _valeur(fiche.get("accepteurs_hydrogene")),
            "liaisons_rotatives": _valeur(fiche.get("liaisons_rotatives")),
            "fraction_csp3": _valeur(fiche.get("fraction_csp3")),
            "lipinski_violations": (
                lip.get("violations") if isinstance(lip, dict) else None),
            "calcule": bool((fiche.get("provenance_calcul") or {})
                            .get("bibliotheque")),
        })
    calcul = resultat.get("calcul", {})
    return _bloc(
        "Propriétés moléculaires", "prediction", "ok",
        resume=(
            "Résultat informatique : propriétés estimées par calcul "
            f"({calcul.get('bibliotheque', 'moteur')}) — ce sont des "
            "estimations théoriques, pas des mesures expérimentales."
        ),
        lignes=lignes,
        explications=["masse_molaire", "logp", "tpsa", "hbd", "hba",
                      "liaisons_rotatives", "fraction_csp3", "lipinski"],
        provenance={
            "moteur": resultat.get("moteur"),
            "version": resultat.get("version"),
            "bibliotheque": calcul.get("bibliotheque"),
            "version_bibliotheque": calcul.get("version_bibliotheque"),
            "limites": calcul.get("limites", []),
            "calcule_le": _utcnow_iso(),
        },
    )


def _lancer_cibles(etat: dict, db: Session, project=None) -> dict:
    lignes = []
    for mol in etat["molecules"]:
        if not mol.get("db_id"):
            continue
        cibles = (
            db.query(CompoundTarget)
            .filter(CompoundTarget.compound_id == mol["db_id"])
            .order_by(CompoundTarget.id)
            .all()
        )
        for cible in cibles:
            lignes.append({
                "compose": mol["nom"],
                "cible": cible.target_name,
                "type_cible": cible.target_type,
                "interaction": cible.interaction_type,
                "affinite": cible.affinity_value,
                "unite": cible.affinity_unit,
                "source": cible.source,
                "publication": cible.publication,
                "doi": cible.doi,
                "niveau": cible.data_level or "publie",
            })
    if not lignes:
        return _bloc(
            "Recherche de cibles", "publie", "non_lance",
            note="Aucune cible documentée en base pour les molécules "
                 "sélectionnées (aucune valeur inventée).")
    return _bloc(
        "Recherche de cibles", "publie", "ok",
        resume=(
            "Association rapportée dans la littérature : "
            f"{len(lignes)} lien(s) composé → cible documentés en base, "
            "avec source. Une cible documentée n'est pas une cible validée "
            "par WebIntel."
        ),
        lignes=lignes,
        provenance={
            "source": "table compound_targets (base WebIntel)",
            "calcule_le": _utcnow_iso(),
        },
    )


def _lancer_interactions(etat: dict, db: Session, project=None) -> dict:
    from webintel.simulations.plugins import interaction_analysis

    liens = []
    for mol in etat["molecules"]:
        if not mol.get("db_id"):
            continue
        cibles = (
            db.query(CompoundTarget)
            .filter(CompoundTarget.compound_id == mol["db_id"])
            .order_by(CompoundTarget.id)
            .all()
        )
        for cible in cibles:
            liens.append({
                "compose_nom": mol["nom"],
                "cible": cible.target_name or "cible non nommée",
            })
    if not liens:
        return _bloc(
            "Interactions connues", "prediction", "non_lance",
            note="Aucun lien composé → cible enregistré en base pour les "
                 "molécules sélectionnées : rien à décrire (aucune valeur "
                 "inventée).")
    try:
        resultat = interaction_analysis.run({"liens": liens}, {})
    except Exception as exc:  # noqa: BLE001
        return _bloc("Interactions connues", "prediction", "echec",
                     note=f"Le moteur d'interactions a refusé le calcul : {exc}")
    return _bloc(
        "Interactions connues", "prediction", "ok",
        resume=(
            "Résultat informatique : description du réseau composé ↔ cible "
            f"à partir de {len(liens)} lien(s) enregistré(s) — analyse "
            "descriptive, pas une découverte de nouvelles interactions."
        ),
        lignes=liens,
        provenance={
            "moteur": resultat.get("moteur"),
            "version": resultat.get("version"),
            "calcule_le": _utcnow_iso(),
        },
    )


def _lancer_combinaison(etat: dict, db: Session, project) -> dict:
    from webintel.science import combinaisons as comb_module
    from webintel.science import service as science_service

    # Les molécules saisies manuellement deviennent des composés en base
    # (service existant, idempotent) pour alimenter l'étude.
    ids = []
    for mol in etat["molecules"]:
        if mol.get("db_id"):
            ids.append(mol["db_id"])
            continue
        compose, _ = science_service.create_compound(
            db, mol["nom"], smiles=mol.get("smiles"))
        db.flush()
        mol["db_id"] = compose.id
        ids.append(compose.id)
    if len(ids) < 2:
        return _bloc(
            "Étude de combinaison", "prediction", "non_lance",
            note="Une étude de combinaison nécessite au moins 2 composés "
                 "(aucune étude partielle créée).")
    nom = "Étude (assistant) — " + " + ".join(
        m["nom"] for m in etat["molecules"][:4])
    try:
        etude = comb_module.creer_etude(
            db, project_id=project.id, nom=nom, compound_ids=ids,
            plant_id=etat.get("plante_id"),
            description="Étude créée par l'assistant en 6 étapes.",
            cree_par="assistant-etude",
        )
        # Versionnage (chantier PERSISTANCE + PERFORMANCE) : chaque
        # exécution de l'assistant crée une NOUVELLE étude ET sa version
        # initiale — jamais d'écrasement silencieux d'une étude existante.
        from webintel.science import versions as versions_module
        versions_module.enregistrer_version(
            db, etude, cree_par="assistant-etude",
            resume="Création par l'assistant en 6 étapes",
        )
        db.commit()
    except (ValueError, RuntimeError) as exc:
        db.rollback()
        return _bloc("Étude de combinaison", "prediction", "echec",
                     note=f"Création de l'étude impossible : {exc}")
    return _bloc(
        "Étude de combinaison", "prediction", "ok",
        resume=(
            "Résultat informatique : dossier de screening structural en "
            "12 points généré (hypothèses de travail, jamais une preuve "
            "d'effet)."
        ),
        lignes=[{"etude_id": etude.id, "nom": etude.nom,
                 "statut": etude.statut}],
        provenance={
            "moteur": "webintel.science.combinaisons",
            "etude_id": etude.id,
            "lien_dossier": f"/combinaisons/{etude.id}",
            "calcule_le": _utcnow_iso(),
        },
    )


def _lancer_docking(etat: dict, db: Session | None = None, project=None) -> dict:
    from webintel.simulations.plugins import docking_adapter

    try:
        vina = docking_adapter.diagnostic_vina()
    except Exception:  # noqa: BLE001
        vina = {"disponible": False, "raison": "Diagnostic impossible."}
    params = etat.get("params", {})
    disponible = bool(vina.get("disponible"))
    return _bloc(
        "Docking moléculaire", "simulation", "non_lance",
        note=(
            "Le docking n'est pas exécuté dans l'assistant : il exige un "
            "récepteur préparé au format PDBQT et le binaire Vina. "
            + ("Vina est disponible sur ce serveur : lancez le workflow "
               "« Docking moléculaire » avec vos paramètres."
               if disponible else
               "Vina n'est pas disponible ici "
               f"({vina.get('raison') or 'binaire introuvable'}) : aucun "
               "score n'est calculé ni simulé.")
        ),
        lignes=[{
            "parametres_proposes": {
                "seed": params.get("seed_vina", 42),
                "exhaustiveness": params.get("exhaustiveness", 8),
            },
            "lien_workflow": "/docking",
        }],
        explications=["docking_score"],
        provenance={
            "moteur": "webintel.simulations.plugins.docking_adapter",
            "vina_disponible": disponible,
            "diagnostic": vina,
            "calcule_le": _utcnow_iso(),
        },
    )


def _lancer_experimentales(etat: dict, db: Session | None = None, project=None) -> dict:
    from webintel.science.synergie import (
        DonneesInsuffisantes, analyser_synergie)

    donnees = etat.get("donnees_exp") or {}
    methode = (donnees.get("methode") or "bliss").strip().lower()
    points = donnees.get("points") or []
    erreurs = donnees.get("erreurs") or []
    if erreurs:
        return _bloc(
            "Analyse de données expérimentales", "experimental", "echec",
            note="Données illisibles : " + " ".join(erreurs[:5]))
    if not points:
        return _bloc(
            "Analyse de données expérimentales", "experimental", "non_lance",
            note=(
                "Aucune donnée expérimentale fournie : l'assistant ne "
                "calcule jamais un score de synergie sans mesures réelles "
                "(mode avancé : renseignez le tableau dose/réponse)."))
    try:
        resultat = analyser_synergie(methode, {"points": points}, {})
    except DonneesInsuffisantes as exc:
        return _bloc(
            "Analyse de données expérimentales", "experimental", "non_lance",
            note=f"Données insuffisantes pour la méthode {methode} : {exc}")
    except (ValueError, ArithmeticError) as exc:
        return _bloc(
            "Analyse de données expérimentales", "experimental", "echec",
            note=f"Calcul impossible : {exc}")
    return _bloc(
        "Analyse de données expérimentales", "prediction", "ok",
        # Honnêteté : le score est un CALCUL sur les mesures fournies —
        # pas une mesure expérimentale, pas une preuve d'effet clinique.
        nature="calculé",
        resume=(
            "Résultat de calcul (synergie) : score calculé à partir de "
            f"{len(points)} point(s) de mesure fournis par le chercheur "
            f"(méthode {methode}). Ce score décrit vos données ; il ne "
            "constitue pas une preuve d'effet clinique."
        ),
        lignes=[{
            "methode": methode,
            "points_utilises": len(points),
            "score_global": resultat.get("score_global"),
            "interpretation": resultat.get("interpretation"),
            "nature": "calculé (prédiction)",
        }],
        limites=resultat.get("limites") or [],
        explications=[methode] if methode in METHODES_SYNERGIE else [],
        provenance={
            "moteur": f"webintel.science.synergie.{methode}",
            "donnees": "fournies par le chercheur (tableau de mesures)",
            "nature": "calcul à partir de mesures — pas une mesure",
            "calcule_le": _utcnow_iso(),
        },
    )


_LANCEURS = {
    "structures": _lancer_structures,
    "proprietes": _lancer_proprietes,
    "cibles": _lancer_cibles,
    "interactions": _lancer_interactions,
    "combinaison": _lancer_combinaison,
    "docking": _lancer_docking,
    "experimentales": _lancer_experimentales,
}


@router.post("/etudes/lancer", response_class=HTMLResponse)
async def etape5_lancer(
    request: Request,
    session: str | None = None,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    project = _project_for(db, ui_key)
    ligne, etat = _charger_session(db, project, session, _identite_ui(ui_key))
    if not etat["analyses"]:
        return RedirectResponse(
            url=f"/etudes/etape3?session={etat['id']}", status_code=303)
    blocs = []
    for cle in etat["analyses"]:
        lanceur = _LANCEURS.get(cle)
        if lanceur is None:
            continue
        try:
            blocs.append(lanceur(etat, db, project))
        except Exception as exc:  # noqa: BLE001 — un bloc ne bloque jamais
            blocs.append(_bloc(
                ANALYSES.get(cle, cle), "prediction", "echec",
                note=f"Erreur inattendue pendant le calcul : {exc}"))
    etat["resultats"] = {
        "blocs": blocs,
        "lance_le": _utcnow_iso(),
        "avertissement": AVERTISSEMENT_GLOBAL,
        "limites": LIMITES_GLOBALES,
    }
    # Décorations d'affichage : explications + badges (calculés une fois).
    for bloc in blocs:
        bloc["explications_riches"] = [
            explication_pour(cle) for cle in bloc.get("explications", [])
        ]
        bloc["explications_riches"] = [
            e for e in bloc["explications_riches"] if e is not None
        ]
    ligne.status = "terminee"  # le brouillon a abouti ; l'étude vit en base
    _sauvegarder(db, ligne, etat)
    return _render("etude_etape5.html", **_contexte(
        ui_key, project, etat, etape_courante=5))


@router.get("/etudes/etape6", response_class=HTMLResponse)
def etape6_redirige(
    request: Request,
    session: str | None = None,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    project = _project_for(db, ui_key)
    _ligne, etat = _charger_session(db, project, session, _identite_ui(ui_key))
    if not etat.get("resultats"):
        return RedirectResponse(
            url=f"/etudes/etape4?session={etat['id']}", status_code=303)
    return RedirectResponse(
        url=f"/etudes/{etat['id']}/resultats", status_code=303)


# ---------------------------------------------------------------------------
# Page de résultats
# ---------------------------------------------------------------------------

@router.get("/etudes/{etude_id}/resultats", response_class=HTMLResponse)
def resultats(
    etude_id: str,
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    project = _project_for(db, ui_key)
    _ligne, etat = _charger_session(db, project, etude_id, _identite_ui(ui_key))
    if not etat.get("resultats"):
        return RedirectResponse(
            url=f"/etudes/etape4?session={etat['id']}", status_code=303)
    return _render("etude_resultats.html", **_contexte(
        ui_key, project, etat, etape_courante=6,
        resultats=etat["resultats"]))
