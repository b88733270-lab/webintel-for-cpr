"""Routes UI « chercheur » : accueil, fiche plante simplifiée, recherche
universelle, « Mes recherches » (v2.2.x).

NOUVEAU module, 100 % additif : aucune route, aucun template, aucun modèle
existant n'est modifié. L'ancien tableau de bord (`/`), la recherche
historique (`/recherche`) et la fiche scientifique complète
(`/plantes/{id}`) restent intacts.

Routes (router NON enregistré ici : le coordinateur l'enregistre dans
`webintel/main.py`) :

- ``GET /accueil`` : page d'accueil du chercheur — « Que souhaitez-vous
  étudier ? » + 5 grandes cartes (plante, molécule, publication,
  nouvelle étude, mes recherches). Aucun détail technique visible.
- ``GET /plante-etude`` : formulaire de saisie (GET) + fiche plante
  structurée en sections claires (identité, botanique, phytochimie,
  pharmacologie, publications, sécurité, simulations). Si la plante est
  absente de la base, propose « Créer la fiche » (formulaire simple,
  pas de JSON) via le service existant ``webintel.science.service``.
- ``POST /plante-etude/creer`` : création d'une fiche plante à partir du
  formulaire simple (nom scientifique + source obligatoires).
- ``GET /recherche-universelle`` : recherche globale (plantes, composés,
  publications, études/simulations) via le contrat existant
  ``webintel.science.search.search_science`` ; chaque résultat affiche
  sa source et son niveau de données.
- ``GET /mes-recherches`` : dossiers de recherche du chercheur, construits
  sur les modèles existants ``Project`` / ``CombinationStudy`` /
  ``Simulation`` / ``SynergyAnalysis`` (aucune nouvelle table).
- ``GET /recherche-en-ligne`` : recherche en ligne (aperçu AVANT import)
  via les connecteurs existants PubChem / GBIF / PubMed — seul le terme
  recherché part vers l'extérieur, lecture seule, aucun JSON demandé.
- ``POST /recherche-en-ligne/importer`` : import d'un aperçu en ligne
  via le connecteur existant (provenance enregistrée par le connecteur),
  puis redirection vers la fiche locale créée.

Règle d'or (honnêteté scientifique) : formulations prudentes partout
(« Résultat informatique », « Association rapportée dans la littérature »,
« Prédiction », « Hypothèse », « Résultat expérimental »), niveaux de
preuve visibles (DONNÉE PUBLIÉE / DONNÉE NORMALISÉE / PRÉDICTION /
SIMULATION / HYPOTHÈSE / RÉSULTAT EXPÉRIMENTAL), jamais de résultat
inventé, jamais « efficace » / « sûr » / « traite » sans source
expérimentale.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Text, cast, func, or_
from sqlalchemy.orm import Session

from webintel.api.deps import get_db
from webintel.core.utils import utcnow
from webintel.database.models import ApiKey, Project
from webintel.science import service as science_service
from webintel.science import fiche as fiche_module
from webintel.science import qualite as qualite_module
from webintel.science.connecteurs import registry as connecteurs_registry
from webintel.science.connecteurs.base import (
    ConnecteurIndisponible,
    ParametresInvalides,
)
from webintel.science.models import (
    CombinationStudy,
    Compound,
    Echantillon,
    Evidence,
    EvidenceType,
    ImportProvenance,
    MesureExperimentale,
    Plant,
    PlantActivity,
    PlantCompound,
    Simulation,
    SourceConnecteur,
    SynergyAnalysis,
)
from webintel.science.search import search_science
from webintel.ui.auth import ui_auth
from webintel.ui.routes import _project_for, _render

router = APIRouter()

# Légende des niveaux de données (mêmes couleurs que la fiche complète).
_NIVEAUX = [
    ("#1a7f37", "Publié", "données publiées et sourcées"),
    ("#0969da", "Normalisé", "données importées et normalisées"),
    ("#9a6700", "Hypothèse", "synthèse à vérifier"),
    ("#8250df", "Prédiction", "simulation — à confirmer expérimentalement"),
    ("#cf222e", "Expérimental", "observations de votre laboratoire (privé)"),
]


def _contexte_commun() -> dict:
    """Contexte partagé par les pages chercheur (légende des niveaux)."""
    return {"niveaux": _NIVEAUX}


def _projets_visibles(db: Session, ui_key: ApiKey | None) -> list[Project]:
    """Dossiers visibles : en mode serveur, seul le projet de la session
    (isolation) ; en mode local, tous les projets."""
    if ui_key is not None:
        projet = db.get(Project, ui_key.project_id)
        return [projet] if projet is not None else []
    return db.query(Project).order_by(Project.id).all()


# ---------------------------------------------------------------------------
# 1. Accueil
# ---------------------------------------------------------------------------


@router.get("/accueil", response_class=HTMLResponse)
def accueil(
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Page d'accueil du chercheur : « Que souhaitez-vous étudier ? »."""
    _project_for(db, ui_key)  # garantit le projet par défaut en mode local
    return _render(
        "accueil.html",
        ui_key,
        active="accueil",
        **_contexte_commun(),
    )


# ---------------------------------------------------------------------------
# 2. Plante : saisie + fiche structurée
# ---------------------------------------------------------------------------


def _chercher_plantes(db: Session, q: str) -> list[Plant]:
    """Plantes dont le nom (scientifique, synonymes, noms locaux, famille,
    genre, espèce) contient la requête — liens réels uniquement, aucune
    donnée inventée."""
    motif = f"%{q.lower()}%"
    # Noms locaux et synonymes : colonnes JSON sérialisées en texte avant
    # comparaison (même approximation assumée que webintel.science.search) —
    # sans cela, « Bissap » ne retrouvait pas Hibiscus sabdariffa.
    motif_json = f"%{q.lower()}%"
    return (
        db.query(Plant)
        .filter(
            or_(
                Plant.scientific_name.ilike(motif),
                Plant.family.ilike(motif),
                Plant.genus.ilike(motif),
                Plant.species.ilike(motif),
                func.lower(cast(Plant.vernacular_names, Text)).like(motif_json),
                func.lower(cast(Plant.synonyms, Text)).like(motif_json),
            )
        )
        .order_by(Plant.scientific_name)
        .limit(20)
        .all()
    )


@router.get("/plante-etude", response_class=HTMLResponse)
def plante_etude(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Saisie d'un nom de plante (GET) puis fiche structurée en sections.

    - sans ``q`` : simple formulaire de saisie ;
    - une seule correspondance : fiche en sections claires ;
    - plusieurs : liste de désambiguïsation ;
    - aucune : proposition « Créer la fiche » (formulaire simple, pas de
      JSON).
    """
    _project_for(db, ui_key)
    q = (request.query_params.get("q") or "").strip()
    contexte = {"q": q, "active": "plante-etude", **_contexte_commun()}

    if not q:
        return _render("plante_etude.html", ui_key, etat="saisie", **contexte)

    correspondances = _chercher_plantes(db, q)
    if len(correspondances) > 1:
        return _render(
            "plante_etude.html", ui_key, etat="choix",
            plantes=correspondances, **contexte,
        )
    if not correspondances:
        return _render(
            "plante_etude.html", ui_key, etat="introuvable", **contexte,
        )
    plante = correspondances[0]
    return _render(
        "plante_etude.html", ui_key, etat="fiche",
        plante=plante,
        fiche=_fiche_plante_resume(db, plante),
        **contexte,
    )


def _fiche_plante_resume(db: Session, plante: Plant) -> dict:
    """Sections simplifiées de la fiche, lues sur les modèles existants."""
    liens = (
        db.query(PlantCompound, Compound)
        .join(Compound, Compound.id == PlantCompound.compound_id)
        .filter(PlantCompound.plant_id == plante.id)
        .order_by(Compound.name)
        .all()
    )
    activites = (
        db.query(PlantActivity)
        .filter(PlantActivity.plant_id == plante.id)
        .order_by(PlantActivity.activity)
        .all()
    )
    # Publications distinctes citées par les liens plante→composé.
    publications: dict[str, dict] = {}
    for lien, _compose in liens:
        cle = lien.doi or lien.publication or ""
        if cle and cle not in publications:
            publications[cle] = {
                "titre": lien.publication or "Référence sans titre",
                "doi": lien.doi,
                "annee": lien.year,
            }
    etudes = (
        db.query(CombinationStudy)
        .filter(CombinationStudy.plant_id == plante.id)
        .order_by(CombinationStudy.created_at.desc())
        .limit(10)
        .all()
    )
    simulations = (
        db.query(Simulation)
        .filter(Simulation.project_id.isnot(None))
        .order_by(Simulation.run_at.desc())
        .limit(50)
        .all()
    )
    simulations_plante = [
        s for s in simulations
        if isinstance(s.inputs, dict) and s.inputs.get("plant_id") == plante.id
    ][:10]
    # Badges de preuve DYNAMIQUES (jamais codés en dur) : chaque section
    # affiche son vrai niveau — None = section vide → aucun badge.
    badge_base = fiche_module.niveau_preuve_plante(db, plante)
    phyto_sourcee = any((lien.doi or lien.publication) for lien, _ in liens)
    pharma_sourcee = any((a.doi or a.reference) for a in activites)
    badges = {
        "identite": badge_base,
        "botanique": badge_base,
        "phytochimie": (
            {"niveau": "publie", "label": "Donnée publiée"}
            if phyto_sourcee else
            {"niveau": "normalise", "label": "Donnée normalisée"}
            if liens else None
        ),
        "pharmacologie": (
            {"niveau": "publie", "label": "Donnée publiée"}
            if pharma_sourcee else
            {"niveau": "normalise", "label": "Donnée normalisée"}
            if activites else None
        ),
        "publications": (
            {"niveau": "publie", "label": "Donnée publiée"}
            if publications else None
        ),
        # Vue simplifiée : aucune donnée de sécurité propre → aucun badge.
        "securite": None,
        "simulations": (
            {"niveau": "prediction", "label": "Prédiction"}
            if (etudes or simulations_plante) else None
        ),
    }
    return {
        "nb_composes": len(liens),
        "composes": [
            {
                "nom": compose.name,
                "partie": lien.plant_part,
                "publication": lien.publication,
                "doi": lien.doi,
            }
            for lien, compose in liens[:8]
        ],
        "activites": activites,
        "publications": list(publications.values()),
        "etudes": etudes,
        "simulations": simulations_plante,
        "badges": badges,
    }


@router.post("/plante-etude/creer", response_class=HTMLResponse)
async def plante_etude_creer(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Création d'une fiche plante depuis le formulaire simple (pas de JSON).

    Nom scientifique + source obligatoires ; la source est enregistrée en
    provenance (donnée saisie manuellement, niveau « Donnée publiée » si la
    source est une publication, sinon « Donnée normalisée »).
    """
    _project_for(db, ui_key)
    form = await request.form()
    nom = (form.get("nom_scientifique") or "").strip()
    source = (form.get("source") or "").strip()
    q = nom
    if not nom:
        return _render(
            "plante_etude.html", ui_key, etat="introuvable", q=q,
            erreur="Le nom scientifique est obligatoire.",
            active="plante-etude", **_contexte_commun(),
        )
    if not source:
        return _render(
            "plante_etude.html", ui_key, etat="introuvable", q=q,
            erreur="La source est obligatoire : précisez d'où vient cette "
                   "information (publication, DOI, ouvrage…).",
            active="plante-etude", **_contexte_commun(),
        )
    synonymes = [s.strip() for s in (form.get("synonymes") or "").split(";") if s.strip()]
    langue = (form.get("langue") or "").strip() or "non précisée"
    noms_locaux = [
        n.strip() for n in (form.get("noms_locaux") or "").split(",") if n.strip()
    ]
    try:
        plante, _creee = science_service.create_plant(
            db,
            nom,
            family=(form.get("famille") or "").strip() or None,
            genus=(form.get("genre") or "").strip() or None,
            species=(form.get("espece") or "").strip() or None,
            synonyms=synonymes,
            vernacular_names={langue: noms_locaux} if noms_locaux else {},
            description=(form.get("description") or "").strip() or None,
        )
        science_service.record_provenance(
            db, "plant", plante.id, source_name=source,
            connector="ui-manuel", data_version="saisie-accueil",
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _render(
            "plante_etude.html", ui_key, etat="introuvable", q=q,
            erreur=str(exc), active="plante-etude", **_contexte_commun(),
        )
    return RedirectResponse(url=f"/plante-etude?q={nom}", status_code=303)


# ---------------------------------------------------------------------------
# 3. Recherche universelle
# ---------------------------------------------------------------------------

_TYPES_RECHERCHE = {
    "tout": None,
    "plante": ["plante"],
    "compose": ["compose"],
    "publication": ["publication"],
    "simulation": ["simulation"],
}


@router.get("/recherche-universelle", response_class=HTMLResponse)
def recherche_universelle(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Recherche globale : plantes, molécules, publications, études.

    Utilise le contrat existant ``search_science`` ; chaque résultat affiche
    son niveau de données et sa source (DOI / URL / référence).
    """
    project = _project_for(db, ui_key)
    params = request.query_params
    q = (params.get("q") or "").strip()
    type_demande = (params.get("type") or "tout").strip()
    types = _TYPES_RECHERCHE.get(type_demande, None)
    resultats = None
    erreur = None
    if q:
        try:
            resultats = search_science(
                db, q, types=types, project_id=project.id, limit=20
            )
        except ValueError as exc:
            erreur = str(exc)
    return _render(
        "recherche_universelle.html",
        ui_key,
        active="recherche-universelle",
        q=q,
        type_demande=type_demande if type_demande in _TYPES_RECHERCHE else "tout",
        resultats=resultats,
        erreur=erreur,
        aucun_resultat_local=bool(
            q and erreur is None and resultats is not None
            and resultats.get("total", 0) == 0
        ),
        **_contexte_commun(),
    )


# ---------------------------------------------------------------------------
# 3 bis. Recherche en ligne (PubChem / GBIF / PubMed) — aperçu puis import
# ---------------------------------------------------------------------------
# Règles :
# - seul le terme recherché (``q``) part vers l'extérieur (GET lecture
#   seule via les connecteurs existants) : aucune donnée de laboratoire,
#   de projet ou d'échantillon n'est envoyée ;
# - l'aperçu utilise les méthodes internes des connecteurs
#   (``requete_json`` / ``_url_proprietes`` / ``_apparier`` /
#   ``_recherche_pmids`` / ``_resume``) — aucun appel HTTP brut n'est
#   réinventé ici ;
# - l'import réutilise ``connecteur.importer(session, parametres)`` :
#   la provenance (SourceConnecteur / ImportProvenance, licence, date)
#   est enregistrée par le connecteur lui-même ;
# - source injoignable (``ConnecteurIndisponible``) : message français
#   explicite, jamais de faux résultat, jamais de traceback affiché.

_SOURCES_EN_LIGNE = ("pubchem", "gbif", "pubmed")

#: Type de recherche universelle -> connecteur en ligne correspondant.
_TYPE_VERS_SOURCE = {
    "plante": "gbif",
    "compose": "pubchem",
    "publication": "pubmed",
}

_MESSAGE_INJOIGNABLE = (
    "source injoignable, vos données locales restent disponibles"
)


def _sources_depuis_demande(
    source: str | None, type_demande: str | None
) -> list[str]:
    """Connecteurs à interroger selon la demande du chercheur."""
    source = (source or "").strip().lower()
    if source in _SOURCES_EN_LIGNE:
        return [source]
    if source == "tout":
        return list(_SOURCES_EN_LIGNE)
    type_demande = (type_demande or "").strip().lower()
    if type_demande in _TYPE_VERS_SOURCE:
        return [_TYPE_VERS_SOURCE[type_demande]]
    return list(_SOURCES_EN_LIGNE)


def _fiche_source(connecteur) -> dict:
    """Métadonnées affichées d'un connecteur (nom, licence, description)."""
    return {
        "nom": connecteur.NOM,
        "nom_affichage": {
            "pubchem": "PubChem",
            "gbif": "GBIF",
            "pubmed": "PubMed",
        }.get(connecteur.NOM, connecteur.NOM),
        "licence": connecteur.LICENCE,
        "licence_url": connecteur.LICENCE_URL,
        "description": connecteur.DESCRIPTION,
        "url_source": connecteur.URL_SOURCE,
    }


def _apercu_pubchem(db: Session, q: str) -> list[dict]:
    """Aperçu PubChem (sans import) : propriétés du composé nommé ``q``."""
    connecteur = connecteurs_registry.get_connecteur("pubchem")
    donnees = connecteur.requete_json(db, connecteur._url_proprietes(q))
    proprietes = (donnees.get("PropertyTable") or {}).get("Properties", [])
    if not proprietes:
        return []
    props = proprietes[0]
    morceaux = []
    if props.get("MolecularFormula"):
        morceaux.append(f"Formule : {props['MolecularFormula']}")
    if props.get("MolecularWeight"):
        morceaux.append(f"Masse moléculaire : {props['MolecularWeight']}")
    if props.get("InChIKey"):
        morceaux.append(f"InChIKey : {props['InChIKey']}")
    return [{
        "titre": props.get("Title") or q,
        "detail": " · ".join(morceaux),
        "identifiant": f"CID {props['CID']}" if props.get("CID") else "",
    }]


def _apercu_gbif(db: Session, q: str) -> list[dict]:
    """Aperçu GBIF (sans import) : appariement taxonomique de ``q``."""
    connecteur = connecteurs_registry.get_connecteur("gbif")
    appariement = connecteur._apparier(db, q)
    if appariement is None:
        return []
    morceaux = []
    if appariement.get("status"):
        morceaux.append(f"Statut taxonomique : {appariement['status']}")
    if appariement.get("rank"):
        morceaux.append(f"Rang : {appariement['rank']}")
    if appariement.get("family"):
        morceaux.append(f"Famille : {appariement['family']}")
    if appariement.get("confidence") is not None:
        morceaux.append(f"Confiance d'appariement : {appariement['confidence']} %")
    # Comptes d'occurrences (informatif, jamais bloquant).
    try:
        par_pays, total = connecteur._compter_occurrences(
            db, appariement.get("usageKey"), None
        )
        if total is not None:
            morceaux.append(f"Occurrences connues : {total}")
    except Exception:  # noqa: BLE001 — l'aperçu reste affichable sans comptes
        pass
    return [{
        "titre": appariement.get("canonicalName") or q,
        "detail": " · ".join(morceaux),
        "identifiant": (
            f"Taxon GBIF n°{appariement['usageKey']}"
            if appariement.get("usageKey") else ""
        ),
    }]


def _apercu_pubmed(db: Session, q: str) -> list[dict]:
    """Aperçu PubMed (sans import) : publications pour les mots-clés ``q``."""
    connecteur = connecteurs_registry.get_connecteur("pubmed")
    pmids = connecteur._recherche_pmids(db, q, 5)
    resultats = []
    for pmid in pmids:
        resume = connecteur._resume(db, pmid)
        auteurs = [
            a.get("name") for a in resume.get("authors", []) if a.get("name")
        ]
        morceaux = []
        if auteurs:
            morceaux.append(", ".join(auteurs[:3]))
        annee = connecteur._extraire_annee(resume)
        if annee:
            morceaux.append(str(annee))
        morceaux.append(f"PMID {pmid}")
        doi = connecteur._extraire_doi(resume)
        if doi:
            morceaux.append(f"DOI {doi}")
        resultats.append({
            "titre": resume.get("title") or f"Publication PMID {pmid}",
            "detail": " · ".join(morceaux),
            "identifiant": f"PMID {pmid}",
        })
    return resultats


_APERCUS = {
    "pubchem": _apercu_pubchem,
    "gbif": _apercu_gbif,
    "pubmed": _apercu_pubmed,
}


def _construire_apercus(db: Session, q: str, sources: list[str]) -> list[dict]:
    """Interroge chaque connecteur demandé et construit les aperçus.

    Une source en échec produit une entrée avec ``erreur`` (message
    français explicite) : les autres sources et les données locales
    restent affichables — jamais de faux résultat.
    """
    aperçus: list[dict] = []
    for nom in sources:
        try:
            connecteur = connecteurs_registry.get_connecteur(nom)
        except ValueError as exc:
            aperçus.append({
                "nom": nom, "nom_affichage": nom, "licence": "",
                "licence_url": "", "description": "", "url_source": "",
                "resultats": [], "erreur": str(exc),
            })
            continue
        fiche = _fiche_source(connecteur)
        try:
            resultats = _APERCUS[nom](db, q)
            aperçus.append({**fiche, "resultats": resultats, "erreur": None})
        except ConnecteurIndisponible as exc:
            # Échec réseau explicite : retour à l'état d'avant l'appel
            # pour ne laisser aucune écriture de cache partielle.
            db.rollback()
            aperçus.append({
                **fiche, "resultats": [],
                "erreur": f"Source {_MESSAGE_INJOIGNABLE}. Détail : {exc}",
            })
        except httpx.HTTPStatusError as exc:
            db.rollback()
            if exc.response is not None and exc.response.status_code == 404:
                aperçus.append({**fiche, "resultats": [], "erreur": None})
            else:
                aperçus.append({
                    **fiche, "resultats": [],
                    "erreur": (
                        f"Source {_MESSAGE_INJOIGNABLE}. "
                        f"Détail : la source répond en erreur."
                    ),
                })
        except Exception:  # noqa: BLE001 — jamais de traceback affiché
            db.rollback()
            aperçus.append({
                **fiche, "resultats": [],
                "erreur": (
                    f"Recherche en ligne impossible pour cette source : "
                    f"{_MESSAGE_INJOIGNABLE}."
                ),
            })
    return aperçus


def _render_recherche_en_ligne(ui_key, **contexte):
    return _render(
        "recherche_en_ligne.html",
        ui_key,
        active="recherche-universelle",
        **_contexte_commun(),
        **contexte,
    )


@router.get("/recherche-en-ligne", response_class=HTMLResponse)
def recherche_en_ligne(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Recherche en ligne : aperçu PubChem / GBIF / PubMed AVANT import.

    Seul le terme ``q`` est transmis aux sources externes (GET lecture
    seule via les connecteurs existants). Aucun import n'a lieu ici.
    """
    _project_for(db, ui_key)
    params = request.query_params
    q = (params.get("q") or "").strip()
    source = (params.get("source") or "").strip().lower()
    type_demande = (params.get("type") or "").strip().lower()
    sources = _sources_depuis_demande(source or None, type_demande or None)
    aperçus = None
    erreur_globale = None
    if q and len(q) < 2:
        erreur_globale = "La recherche nécessite au moins 2 caractères."
    elif q:
        aperçus = _construire_apercus(db, q, sources)
    return _render_recherche_en_ligne(
        ui_key,
        q=q,
        source_demandee=source if source in (*_SOURCES_EN_LIGNE, "tout") else "",
        aperçus=aperçus,
        erreur_globale=erreur_globale,
        message=None,
    )


def _parametres_import(nom_connecteur: str, q: str) -> dict:
    """Paramètres d'import attendus par chaque plugin (voir leurs docstrings)."""
    if nom_connecteur == "pubchem":
        return {"nom": q}
    if nom_connecteur == "gbif":
        return {"nom_scientifique": q}
    return {"requete": q, "max": 5}


@router.post("/recherche-en-ligne/importer", response_class=HTMLResponse)
async def recherche_en_ligne_importer(
    request: Request,
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Importe un résultat en ligne via le connecteur existant.

    La provenance est enregistrée par le connecteur (``importer`` +
    ``enregistrer_provenance``). Après import, redirection vers la
    fiche locale créée ; en cas d'échec réseau, message français
    explicite et aucun objet créé.
    """
    _project_for(db, ui_key)
    form = await request.form()
    nom_connecteur = (form.get("connecteur") or "").strip().lower()
    q = (form.get("q") or "").strip()
    if nom_connecteur not in _SOURCES_EN_LIGNE or not q:
        return _render_recherche_en_ligne(
            ui_key, q=q, source_demandee="", aperçus=None,
            erreur_globale=(
                "Import impossible : choisissez une source "
                "(PubChem, GBIF ou PubMed) et un terme de recherche."
            ),
            message=None,
        )
    try:
        connecteur = connecteurs_registry.get_connecteur(nom_connecteur)
    except ValueError as exc:
        return _render_recherche_en_ligne(
            ui_key, q=q, source_demandee="", aperçus=None,
            erreur_globale=str(exc), message=None,
        )
    fiche = _fiche_source(connecteur)
    # Repère pour retrouver la provenance créée par CET import.
    avant = (
        db.query(func.max(ImportProvenance.id)).scalar() or 0
    )
    try:
        rapport = connecteur.importer(db, _parametres_import(nom_connecteur, q))
    except ConnecteurIndisponible as exc:
        db.rollback()
        return _render_recherche_en_ligne(
            ui_key, q=q, source_demandee=nom_connecteur, aperçus=None,
            erreur_globale=(
                f"Source {fiche['nom_affichage']} {_MESSAGE_INJOIGNABLE}. "
                f"Détail : {exc}"
            ),
            message=None,
        )
    except ParametresInvalides as exc:
        db.rollback()
        return _render_recherche_en_ligne(
            ui_key, q=q, source_demandee=nom_connecteur, aperçus=None,
            erreur_globale=str(exc), message=None,
        )
    except Exception:  # noqa: BLE001 — jamais de traceback affiché
        db.rollback()
        return _render_recherche_en_ligne(
            ui_key, q=q, source_demandee=nom_connecteur, aperçus=None,
            erreur_globale=(
                f"Import impossible depuis {fiche['nom_affichage']} : "
                f"{_MESSAGE_INJOIGNABLE}."
            ),
            message=None,
        )

    # Provenances fraîches éventuelles (créées par le connecteur ci-dessus).
    nouvelles_provenances = (
        db.query(ImportProvenance)
        .filter(
            ImportProvenance.id > avant,
            ImportProvenance.connecteur_nom == nom_connecteur,
        )
        .order_by(ImportProvenance.id.asc())
        .all()
    )
    nouvelle_provenance = nouvelles_provenances[-1] if nouvelles_provenances else None
    erreurs_rapport = [str(e) for e in (rapport.get("erreurs") or [])]
    if (
        nouvelle_provenance is None
        and not rapport.get("crees")
        and not rapport.get("mis_a_jour")
        and erreurs_rapport
    ):
        # Rien n'a été créé (ex. introuvable chez la source) : message
        # explicite du connecteur, aucun faux résultat affiché.
        db.rollback()
        return _render_recherche_en_ligne(
            ui_key, q=q, source_demandee=nom_connecteur, aperçus=None,
            erreur_globale=" ".join(erreurs_rapport),
            message=None,
        )

    # Même discipline que l'API connecteurs : dernier_import tracé.
    ligne_source = (
        db.query(SourceConnecteur)
        .filter(SourceConnecteur.nom == nom_connecteur)
        .one_or_none()
    )
    if ligne_source is not None:
        ligne_source.dernier_import = utcnow()

    # PubMed : import groupé — on affiche les fiches locales créées
    # (la recherche locale par le terme d'origine ne retrouverait pas
    # toujours ces titres, qui ne le contiennent pas forcément).
    if nom_connecteur == "pubmed" and nouvelles_provenances:
        importes = []
        for prov in nouvelles_provenances:
            preuve = db.get(Evidence, prov.item_id)
            if preuve is None:
                continue
            morceaux = []
            if preuve.authors:
                morceaux.append(", ".join(str(a) for a in preuve.authors[:3]))
            if preuve.year:
                morceaux.append(str(preuve.year))
            if preuve.pmid:
                morceaux.append(f"PMID {preuve.pmid}")
            if preuve.doi:
                morceaux.append(f"DOI {preuve.doi}")
            importes.append({
                "titre": preuve.title or "(sans titre)",
                "detail": " · ".join(morceaux),
            })
        db.commit()
        return _render_recherche_en_ligne(
            ui_key, q=q, source_demandee=nom_connecteur, aperçus=None,
            erreur_globale=None,
            message=(
                f"{len(importes)} publication(s) importée(s) depuis "
                f"PubMed, avec provenance enregistrée (source, licence, "
                f"date). Données normalisées — pas des preuves."
            ),
            importes=importes,
        )
    db.commit()

    # Redirection vers la fiche locale créée (ou la recherche locale).
    if nom_connecteur == "gbif" and nouvelle_provenance is not None:
        plante = db.get(Plant, nouvelle_provenance.item_id)
        if plante is not None:
            return RedirectResponse(
                url=f"/plante-etude?q={quote(plante.scientific_name)}",
                status_code=303,
            )
    if nom_connecteur == "pubchem" and nouvelle_provenance is not None:
        compose = db.get(Compound, nouvelle_provenance.item_id)
        if compose is not None:
            return RedirectResponse(
                url=f"/recherche-universelle?q={quote(compose.name)}",
                status_code=303,
            )
    return RedirectResponse(
        url=f"/recherche-universelle?q={quote(q)}", status_code=303
    )



# ---------------------------------------------------------------------------
# 4. Mes recherches
# ---------------------------------------------------------------------------


def _dossier_recherche(db: Session, projet: Project, chercheur: str) -> dict:
    """Synthèse d'un dossier de recherche à partir des modèles existants."""
    combinaisons = (
        db.query(CombinationStudy)
        .filter(CombinationStudy.project_id == projet.id)
        .order_by(CombinationStudy.created_at.desc())
        .all()
    )
    simulations = (
        db.query(Simulation)
        .filter(Simulation.project_id == projet.id)
        .order_by(Simulation.run_at.desc())
        .all()
    )
    synergies = (
        db.query(SynergyAnalysis)
        .filter(SynergyAnalysis.project_id == projet.id)
        .order_by(SynergyAnalysis.created_at.desc())
        .all()
    )
    plantes_ids: set[int] = set()
    composes_ids: set[int] = set()
    for etude in combinaisons:
        if etude.plant_id:
            plantes_ids.add(etude.plant_id)
        composes_ids.update(etude.compound_ids or [])
    for sim in simulations:
        composes_ids.update(sim.compound_ids or [])
        if isinstance(sim.inputs, dict) and sim.inputs.get("plant_id"):
            plantes_ids.add(sim.inputs["plant_id"])
    noms_plantes = [
        p.scientific_name
        for p in db.query(Plant).filter(Plant.id.in_(plantes_ids)).all()
    ] if plantes_ids else []
    noms_composes = [
        c.name
        for c in db.query(Compound).filter(Compound.id.in_(composes_ids)).all()
    ] if composes_ids else []

    # Dernière analyse (tout type confondu) : nom + date, jamais inventée.
    evenements = []
    for etude in combinaisons:
        evenements.append((etude.created_at, f"Étude de combinaison « {etude.nom} »"))
    for sim in simulations:
        evenements.append((sim.run_at, f"Simulation « {sim.name} » (résultat informatique)"))
    for syn in synergies:
        evenements.append((syn.created_at, f"Analyse de synergie « {syn.nom} »"))
    evenements = [e for e in evenements if e[0] is not None]
    evenements.sort(key=lambda e: e[0], reverse=True)
    derniere = evenements[0] if evenements else None

    nb_analyses = len(combinaisons) + len(simulations) + len(synergies)
    return {
        "id": projet.id,
        "nom": projet.name,
        "date_creation": projet.created_at,
        "chercheur": chercheur,
        "plantes": sorted(noms_plantes),
        "composes": sorted(noms_composes),
        "nb_analyses": nb_analyses,
        "nb_combinaisons": len(combinaisons),
        "nb_simulations": len(simulations),
        "nb_synergies": len(synergies),
        "derniere_analyse": (
            {"nom": derniere[1], "date": derniere[0]} if derniere else None
        ),
        "statut": "Actif" if nb_analyses else "Nouveau",
    }


@router.get("/mes-recherches", response_class=HTMLResponse)
def mes_recherches(
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Dossiers de recherche du chercheur (un dossier = un projet).

    En mode serveur, seul le projet de la session est visible (isolation) ;
    en mode local, tous les projets sont listés.
    """
    projets = _projets_visibles(db, ui_key)
    chercheur = ui_key.name if ui_key is not None else "Chercheur (mode local)"
    dossiers = [_dossier_recherche(db, p, chercheur) for p in projets]
    return _render(
        "mes_recherches.html",
        ui_key,
        active="mes-recherches",
        dossiers=dossiers,
        **_contexte_commun(),
    )


# ---------------------------------------------------------------------------
# 8. Tableau de bord chercheur (v2.3) — agrégat réel, aucune stat artificielle
# ---------------------------------------------------------------------------


def _compter_projets(db: Session, ids_projets: list[int], modele) -> int:
    """Compte les lignes d'un modèle isolé par projet (``project_id``)."""
    if not ids_projets:
        return 0
    return (
        db.query(func.count(modele.id))
        .filter(modele.project_id.in_(ids_projets))
        .scalar()
        or 0
    )


@router.get("/tableau-de-bord", response_class=HTMLResponse)
def tableau_de_bord(
    db: Session = Depends(get_db),
    ui_key: ApiKey | None = Depends(ui_auth),
):
    """Tableau de bord du chercheur : projets, plantes, échantillons,
    études, expériences, simulations, publications, rapports, données à
    valider, outils — chaque chiffre est calculé en temps réel à partir de
    la base de données. Aucune statistique artificielle : les compteurs
    partagent la même isolation projet que le reste de l'UI
    (``_projets_visibles``) ; les entités globales (plantes, publications)
    sont comptées sur l'ensemble de la base, indiqué explicitement.
    """
    projets = _projets_visibles(db, ui_key)
    ids_projets = [p.id for p in projets]
    controle = qualite_module.executer_controle(db)
    return _render(
        "tableau_de_bord.html",
        ui_key,
        active="tableau-de-bord",
        indicateurs=[
            ("Projets de recherche", _compter_projets_trivial(projets),
             "/mes-recherches", "Dossiers de recherche du chercheur."),
            ("Plantes étudiées", db.query(func.count(Plant.id)).scalar() or 0,
             "/plantes", "Fiches botaniques (toutes les plantes de la base)."),
            ("Échantillons de terrain",
             _compter_projets(db, ids_projets, Echantillon),
             "/recherche-terrain", "Échantillons collectés avec coordonnées GPS."),
            ("Études", _compter_projets(db, ids_projets, CombinationStudy),
             "/combinaisons", "Études de combinaison (résultat informatique)."),
            ("Expériences de laboratoire",
             _compter_projets(db, ids_projets, MesureExperimentale),
             "/laboratoire", "Mesures expérimentales importées (données privées)."),
            ("Simulations",
             _compter_projets(db, ids_projets, Simulation),
             "/simulations", "Simulations (prédictions — jamais des faits)."),
            ("Publications",
             db.query(func.count(Evidence.id))
             .filter(Evidence.source_type == EvidenceType.PUBLICATION.value)
             .scalar() or 0,
             "/recherche-science", "Publications enregistrées, avec DOI/PMID."),
            ("Données à valider", len(controle["anomalies"]),
             "/qualite", "Anomalies détectées par le contrôle qualité (lecture seule)."),
        ],
        rapports=(
            "Les rapports (d'étude, de simulation, dossier complet) sont "
            "générés à la demande et n'existent que sur action du chercheur : "
            "ouvrez une étude, une simulation ou /export pour produire le vôtre."
        ),
        **_contexte_commun(),
    )


def _compter_projets_trivial(projets: list) -> int:
    """Compte les projets déjà chargés (pas de requête supplémentaire)."""
    return len(projets)
