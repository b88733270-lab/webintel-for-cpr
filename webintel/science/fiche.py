"""Fiche scientifique complète d'une plante médicinale (Phase 3 — backend).

``build_plant_fiche`` assemble, pour une plante donnée, toutes les données
enregistrées dans WebIntel, sectionnées par niveau de preuve (``DataLevel``),
jamais mélangés :

- ``publie`` : identité, botanique, phytochimie rapportée, pharmacologie,
  activités pharmacologiques rapportées, cibles documentées, littérature ;
- ``experimental`` : observations du laboratoire (privé, par projet) ;
- ``prediction`` : simulations (privé, par projet).

Discipline scientifique (règles strictes, documentées) :

1. **Aucun lien plante-composé inventé** : la section ``phytochimie``
   reflète UNIQUEMENT les lignes ``PlantCompound`` enregistrées en base
   pour cette plante. Une plante sans lien donne une section vide.
2. **Pharmacologie — règle de liaison explicite** : une ``Evidence`` est
   rattachée à la plante si et seulement si ``Evidence.doi`` est non nul
   et égale le ``doi`` d'au moins une ligne ``PlantCompound`` de cette
   plante. C'est la seule règle de liaison ; une preuve dont le DOI ne
   correspond à aucun lien plante-composé n'apparaît pas dans la fiche.
3. **Sécurité — heuristique, pas un avis médical** : la section
   ``securite`` est un sous-ensemble de ``pharmacologie`` — les preuves
   dont le titre ou l'extrait contient (insensible à la casse) l'un des
   mots-clés de prudence suivants :
   ``"toxic"``, ``"toxique"``, ``"contre-indication"``,
   ``"effet indésirable"``, ``"précaution"``, ``"danger"``,
   ``"surdosage"``.
   Cette détection est une simple heuristique textuelle : elle ne
   constitue en aucun cas un avis médical, toxicologique ou
   réglementaire, et chaque entrée rappelle de consulter la source
   originale.
4. **Séparation public / privé** : les observations de laboratoire et les
   simulations ne sont incluses que si ``project_id`` est fourni, et
   uniquement celles rattachées à ce projet. Sans ``project_id``, ces
   sections sont vides et aucune donnée de laboratoire ne fuite.
5. **Aucune donnée « validée »** : les libellés et avertissements
   rappellent ce qu'est chaque donnée (rapportée, simulée, observée) —
   jamais une preuve scientifique du seul fait qu'elle est stockée ici.
6. **Preuves pointées (v2.1)** : les relations importantes
   (``PlantCompound.evidence_id``, ``CompoundTarget.evidence_id``,
   ``PlantActivity.evidence_id``) pointent vers la ``Evidence`` qui les
   établit — la chaîne donnée → publication → DOI est traçable dans la
   fiche (champ ``preuve``) et dans l'API.
7. **Vocabulaire interdit** : la fiche ne qualifie jamais une plante,
   un composé ou une activité d'« efficace », « sûr » ou « traitant »
   quoi que ce soit sans source expérimentale explicite ; les sections
   rapportent des faits publiés (activité étudiée, modèle, résultat
   publié) avec leurs sources.
8. **Champs manquants** : une section sans donnée est rendue vide et
   l'UI affiche « non renseignée » — jamais de valeur inventée.

Toutes les valeurs retournées sont JSON-sérialisables (str / int / float /
bool / None / list / dict ; les dates en ISO 8601). Portable SQLite /
PostgreSQL : aucune requête spécifique à un moteur.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from webintel.science.models import (
    Compound,
    CompoundTarget,
    Evidence,
    ImportProvenance,
    Plant,
    PlantActivity,
    PlantCompound,
    Provenance,
    ResearchObservation,
    Simulation,
    Study,
)

# ---------------------------------------------------------------------------
# Badges et mots-clés
# ---------------------------------------------------------------------------

_BADGES: dict[str, dict[str, str]] = {
    "publie": {"label": "Publié", "couleur": "#1a7f37"},
    "normalise": {"label": "Normalisé", "couleur": "#1d4ed8"},
    "hypothese": {"label": "Hypothèse", "couleur": "#b45309"},
    "experimental": {"label": "Expérimental", "couleur": "#cf222e"},
    "prediction": {"label": "Prédiction", "couleur": "#8250df"},
}

#: Mots-clés de prudence (français) pour la section « sécurité ».
#: HEURISTIQUE textuelle insensible à la casse — jamais un avis médical.
MOTS_CLES_PRUDENCE: tuple[str, ...] = (
    "toxic",
    "toxique",
    "contre-indication",
    "effet indésirable",
    "précaution",
    "danger",
    "surdosage",
)

AVERTISSEMENT_SECURITE = (
    "Information de sécurité rapportée — consulter la source originale."
)
AVERTISSEMENT_SEPARATION_LABO = (
    "Données du laboratoire — jamais mélangées aux données publiques."
)
AVERTISSEMENT_SIMULATION = (
    "Simulation — à confirmer expérimentalement. N'est pas une preuve."
)
NOTE_PHARMACOGNOSIE = "données rapportées, non validées par WebIntel"


def _badge(niveau: str) -> dict[str, str]:
    """Badge UI {label, couleur} pour un niveau de preuve."""
    return dict(_BADGES.get(niveau, {"label": niveau, "couleur": "#6e7781"}))


def niveau_preuve_plante(db: Session, plante: Plant) -> dict[str, str]:
    """Niveau de preuve RÉEL des données d'identité/botanique d'une plante.

    Règle d'honnêteté : jamais « Donnée publiée » par défaut.
    - saisie manuelle (UI, connecteur ``ui-manuel``) → ``normalise`` ;
    - import de fichier structuré → niveau de l'import (``publie``,
      sauf SDF → ``normalise``) ;
    - provenance documentaire (DOI, connecteur PubMed/PubChem) → ``publie`` ;
    - aucune provenance connue → ``normalise`` (on ne prétend pas « publiée »).

    Retourne ``{"niveau": ..., "label": ...}``.
    """
    prov = (
        db.query(Provenance)
        .filter(Provenance.entity_type == "plant",
                Provenance.entity_id == plante.id)
        .order_by(Provenance.id.desc())
        .first()
    )
    if prov is not None:
        if (prov.connector or "") == "ui-manuel":
            return {"niveau": "normalise",
                    "label": "Saisie manuelle — donnée normalisée"}
        src = (prov.source_name or "").lower()
        connecteur = (prov.connector or "").lower()
        if ("doi" in src or "10." in src or connecteur in {
                "pubmed", "pubchem", "connecteur-pubmed", "connecteur-pubchem"}):
            return {"niveau": "publie", "label": "Donnée publiée"}
        return {"niveau": "normalise", "label": "Donnée normalisée"}
    imp = (
        db.query(ImportProvenance)
        .filter(ImportProvenance.type_donnees == "plante",
                ImportProvenance.item_id == plante.id)
        .order_by(ImportProvenance.id.desc())
        .first()
    )
    if imp is not None:
        nom = (imp.connecteur_nom or "").lower()
        if nom.endswith(".sdf"):
            return {"niveau": "normalise",
                    "label": "Donnée normalisée (import SDF)"}
        return {"niveau": "publie", "label": "Donnée publiée"}
    return {"niveau": "normalise", "label": "Donnée normalisée"}


def _iso(date) -> str | None:
    """Date/heure en ISO 8601 (ou None)."""
    return date.isoformat() if date is not None else None


# ---------------------------------------------------------------------------
# Sections publiques
# ---------------------------------------------------------------------------

def _section_plante(plante: Plant) -> dict:
    return {
        "id": plante.id,
        "nom_scientifique": plante.scientific_name,
        "synonymes": list(plante.synonyms or []),
        "noms_vernaculaires": dict(plante.vernacular_names or {}),
        "famille": plante.family,
        "genre": plante.genus,
        "espece": plante.species,
        "taxonomie": dict(plante.taxonomy or {}),
        "identifiants_externes": dict(plante.external_ids or {}),
        "data_level": "publie",
        "badge": _badge("publie"),
    }


def _section_botanique(plante: Plant) -> dict:
    return {
        "description": plante.description,
        "distribution": plante.distribution,
        "parties_utilisees": list(plante.used_parts or []),
        "images": list(plante.images or []),
        # v2.1 — morphologie détaillée (None = non renseigné, jamais inventé)
        "feuilles": plante.feuilles,
        "fleurs": plante.fleurs,
        "fruits": plante.fruits,
        "graines": plante.graines,
        "racines": plante.racines,
        "ecorce": plante.ecorce,
        "habitat": plante.habitat,
        "references_botaniques": list(plante.references_botaniques or []),
        "data_level": "publie",
        "badge": _badge("publie"),
    }


def _preuve_dict(preuve: Evidence | None) -> dict | None:
    """Forme allégée d'une preuve pointée (ou None si absente)."""
    if preuve is None:
        return None
    return {
        "evidence_id": preuve.id,
        "reference": preuve.reference,
        "doi": preuve.doi,
        "pmid": preuve.pmid,
        "url": preuve.url,
        "titre": preuve.title,
        "auteurs": list(preuve.authors or []),
        "annee": preuve.year,
        "citation": _citer_evidence(preuve),
    }


def _section_pharmacognosie(plante: Plant, liens: list[PlantCompound]) -> dict:
    methodes: list[str] = []
    for lien in liens:
        methode = (lien.extraction_method or "").strip()
        if methode and methode not in methodes:
            methodes.append(methode)
    # Détail par extraction rapportée : partie → méthode → rendement →
    # méthode d'analyse → publication → DOI (chaîne de preuve complète).
    extractions: list[dict] = []
    for lien in liens:
        if not any(
            [lien.plant_part, lien.extraction_method, lien.rendement,
             lien.analysis_method, lien.publication, lien.doi]
        ):
            continue
        extractions.append(
            {
                "partie": lien.plant_part,
                "methode_extraction": lien.extraction_method,
                "rendement": lien.rendement,
                "rendement_unite": lien.rendement_unite,
                "methode_analyse": lien.analysis_method,
                "publication": lien.publication,
                "doi": lien.doi,
                "annee": lien.year,
            }
        )
    return {
        "parties_utilisees": list(plante.used_parts or []),
        "matiere_premiere": plante.matiere_premiere,
        "preparation": plante.preparation,
        "methodes_extraction_rapporteess": methodes,
        "extractions": extractions,
        "note": NOTE_PHARMACOGNOSIE,
        "data_level": "publie",
        "badge": _badge("publie"),
    }


def _section_phytochimie(
    liens: list[PlantCompound],
    composes: dict[int, Compound],
    preuves: dict[int, Evidence] | None = None,
) -> list[dict]:
    items: list[dict] = []
    preuves = preuves or {}
    for lien in liens:
        compose = composes.get(lien.compound_id)
        items.append(
            {
                "compose_id": compose.id if compose else lien.compound_id,
                "nom": compose.name if compose else None,
                "formule": compose.formula if compose else None,
                "classe_chimique": compose.chemical_class if compose else None,
                "partie": lien.plant_part,
                "concentration": lien.concentration,
                "unite": lien.concentration_unit,
                "methode_extraction": lien.extraction_method,
                # v2.1 — méthode d'analyse et rendement rapportés
                "methode_analyse": lien.analysis_method,
                "rendement": lien.rendement,
                "rendement_unite": lien.rendement_unite,
                "publication": lien.publication,
                "doi": lien.doi,
                "annee": lien.year,
                "confiance": lien.confidence,
                # v2.1 — preuve pointée (None si le lien n'en cite aucune)
                "preuve": _preuve_dict(preuves.get(lien.evidence_id))
                if lien.evidence_id
                else None,
                "data_level": "publie",
                "badge": _badge("publie"),
            }
        )
    return items


def _item_evidence(preuve: Evidence) -> dict:
    """Forme commune d'une preuve pour les sections pharmacologie / sécurité."""
    return {
        "evidence_id": preuve.id,
        "type_source": preuve.source_type,
        "reference": preuve.reference,
        "doi": preuve.doi,
        "pmid": preuve.pmid,
        "url": preuve.url,
        "titre": preuve.title,
        "auteurs": list(preuve.authors or []),
        "annee": preuve.year,
        "extrait": preuve.excerpt,
        "data_level": "publie",
        "badge": _badge("publie"),
    }


def _contient_mot_cle_prudence(preuve: Evidence) -> bool:
    """Heuristique : le titre ou l'extrait mentionne-t-il un mot-clé de
    prudence ? (insensible à la casse — voir la liste documentée dans le
    docstring du module ; jamais un avis médical)."""
    texte = f"{preuve.title or ''} {preuve.excerpt or ''}".lower()
    return any(mot in texte for mot in MOTS_CLES_PRUDENCE)


def _citer_evidence(preuve: Evidence) -> str:
    """Citation « Auteurs (année). Titre. Référence. DOI »."""
    auteurs = ", ".join(preuve.authors) if preuve.authors else "Auteurs inconnus"
    annee = str(preuve.year) if preuve.year else "s.d."
    morceaux = [f"{auteurs} ({annee})"]
    if (preuve.title or "").strip():
        morceaux.append(preuve.title.strip())
    if (preuve.reference or "").strip():
        morceaux.append(preuve.reference.strip())
    if (preuve.doi or "").strip():
        morceaux.append(f"DOI : {preuve.doi.strip()}")
    return ". ".join(morceaux) + "."


def _citer_publication_lien(lien: PlantCompound) -> str | None:
    """Citation de repli pour un lien plante-composé sans DOI."""
    publication = (lien.publication or "").strip()
    if not publication:
        return None
    if lien.year and str(lien.year) not in publication:
        return f"{publication} ({lien.year})."
    return publication if publication.endswith(".") else f"{publication}."


# ---------------------------------------------------------------------------
# Sections v2.1 — activités, cibles documentées, littérature structurée
# ---------------------------------------------------------------------------
# Chaque entrée pointe vers sa preuve (``evidence_id`` → ``Evidence``) :
# la chaîne donnée → publication → DOI est traçable. Aucune entrée
# n'invente de donnée : une section sans entrée est rendue vide et
# l'UI affiche « non renseignée ».


def _section_activites(
    db: Session, plant_id: int, preuves: dict[int, Evidence]
) -> list[dict]:
    """Activités pharmacologiques rapportées pour la plante (v2.1).

    Données PUBLIÉES : l'activité est décrite telle que rapportée (modèle
    et type d'étude), jamais comme « efficace » ni « traitant » quoi que
    ce soit — ce vocabulaire est interdit sans source expérimentale.
    """
    activites = (
        db.query(PlantActivity)
        .filter(PlantActivity.plant_id == plant_id)
        .order_by(PlantActivity.id)
        .all()
    )
    return [
        {
            "id": activite.id,
            "activite": activite.activity,
            "type_etude": activite.study_type,
            "modele_etude": activite.study_model,
            "resultat_publie": activite.published_result,
            "reference": activite.reference,
            "doi": activite.doi,
            "preuve": _preuve_dict(preuves.get(activite.evidence_id))
            if activite.evidence_id
            else None,
            "data_level": activite.data_level or "publie",
            "badge": _badge(activite.data_level or "publie"),
        }
        for activite in activites
    ]


def _section_cibles(
    db: Session,
    ids_composes: set[int],
    composes: dict[int, Compound],
    preuves: dict[int, Evidence],
) -> list[dict]:
    """Cibles biologiques documentées des composés de la plante (v2.1).

    Une cible est DOCUMENTÉE (publication citée, preuve pointée), jamais
    « validée » par WebIntel.
    """
    if not ids_composes:
        return []
    cibles = (
        db.query(CompoundTarget)
        .filter(CompoundTarget.compound_id.in_(ids_composes))
        .order_by(CompoundTarget.id)
        .all()
    )
    items: list[dict] = []
    for cible in cibles:
        compose = composes.get(cible.compound_id)
        items.append(
            {
                "id": cible.id,
                "compose_id": cible.compound_id,
                "compose_nom": compose.name if compose else None,
                "cible": cible.target_name,
                "type_cible": cible.target_type,
                "type_interaction": cible.interaction_type,
                "affinite": cible.affinity_value,
                "affinite_unite": cible.affinity_unit,
                "source": cible.source,
                "publication": cible.publication,
                "doi": cible.doi,
                "preuve": _preuve_dict(preuves.get(cible.evidence_id))
                if cible.evidence_id
                else None,
                "data_level": cible.data_level or "publie",
                "badge": _badge(cible.data_level or "publie"),
            }
        )
    return items


def _section_litterature(
    preuves_liees: list[Evidence], db: Session, dois_lies: set[str]
) -> list[dict]:
    """Littérature structurée de la plante (v2.1).

    Combine les preuves documentaires liées (par la règle DOI explicite)
    et les études structurées (``Study``) dont le DOI correspond — forme
    normalisée : titre, auteurs, année, DOI, PMID, journal, type, source.
    """
    entrees: list[dict] = []
    for preuve in preuves_liees:
        entrees.append(
            {
                "type_entree": "preuve",
                "id": preuve.id,
                "titre": preuve.title,
                "auteurs": list(preuve.authors or []),
                "annee": preuve.year,
                "doi": preuve.doi,
                "pmid": preuve.pmid,
                "journal": None,
                "type": preuve.source_type,
                "reference": preuve.reference,
                "url": preuve.url,
                "data_level": "publie",
                "badge": _badge("publie"),
            }
        )
    if dois_lies:
        etudes = (
            db.query(Study)
            .filter(Study.doi.in_(dois_lies))
            .order_by(Study.id)
            .all()
        )
        for etude in etudes:
            entrees.append(
                {
                    "type_entree": "etude",
                    "id": etude.id,
                    "titre": etude.title,
                    "auteurs": list(etude.authors or []),
                    "annee": etude.year,
                    "doi": etude.doi,
                    "pmid": etude.pmid,
                    "journal": etude.journal,
                    "type": etude.study_type,
                    "reference": None,
                    "url": etude.url,
                    "data_level": "publie",
                    "badge": _badge("publie"),
                }
            )
    return entrees


# ---------------------------------------------------------------------------
# Sections privées (laboratoire)
# ---------------------------------------------------------------------------

def _section_laboratoire_prive(
    db: Session, project_id: int
) -> dict:
    observations = (
        db.query(ResearchObservation)
        .filter(ResearchObservation.project_id == project_id)
        .order_by(ResearchObservation.id)
        .all()
    )
    return {
        "observations": [
            {
                "id": obs.id,
                "protocol_ref": obs.protocol_ref,
                "observed_date": _iso(obs.observed_date),
                "author": obs.author,
                "laboratory": obs.laboratory,
                "status": obs.status,
                "comment": obs.comment,
                "data_level": "experimental",
                "badge": _badge("experimental"),
            }
            for obs in observations
        ],
        "avertissement_separation": AVERTISSEMENT_SEPARATION_LABO,
    }


def _section_simulations(db: Session, project_id: int) -> list[dict]:
    simulations = (
        db.query(Simulation)
        .filter(Simulation.project_id == project_id)
        .order_by(Simulation.id)
        .all()
    )
    return [
        {
            "id": sim.id,
            "nom": sim.name,
            "cible": sim.target,
            "methode": sim.method,
            "moteur": sim.engine_name,
            "version_moteur": sim.engine_version,
            "resultat": dict(sim.result or {}),
            "unites": sim.units,
            "date": _iso(sim.run_at),
            "confiance": sim.confidence,
            "references": list(sim.references or []),
            "statut": sim.status,
            "data_level": "prediction",
            "badge": _badge("prediction"),
            "avertissement": AVERTISSEMENT_SIMULATION,
        }
        for sim in simulations
    ]


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def build_plant_fiche(
    db: Session, plant_id: int, project_id: int | None = None
) -> dict:
    """Construit la fiche scientifique complète d'une plante médicinale.

    Règles de liaison (documentées dans le docstring du module) :

    - ``phytochimie`` = exactement les lignes ``PlantCompound`` de la
      plante (aucun lien inventé) ; chaque item expose sa méthode
      d'analyse, son rendement rapporté et sa preuve pointée
      (``preuve``, None si absente) ;
    - ``pharmacologie`` = les ``Evidence`` dont le ``doi`` (non nul)
      égale celui d'un lien ``PlantCompound`` de la plante ;
    - ``activites_pharmacologiques`` = les ``PlantActivity`` de la
      plante (activité, modèle, type d'étude, résultat publié, preuve
      pointée) — données rapportées, jamais « efficaces » ;
    - ``cibles`` = les ``CompoundTarget`` des composés de la plante
      (cible documentée, type d'interaction, preuve pointée) ;
    - ``securite`` = sous-ensemble heuristique de ``pharmacologie``
      (mots-clés de prudence dans le titre ou l'extrait) — heuristique,
      jamais un avis médical ;
    - ``bibliographie`` = citations des preuves liées + publications des
      liens sans DOI ;
    - ``litterature`` = entrées structurées (preuves liées + études
      ``Study`` au DOI correspondant : titre, auteurs, année, DOI, PMID,
      journal, type, source) ;
    - ``laboratoire_prive`` = liste des observations du ``project_id``
      fourni (``[]`` si ``project_id`` est None) ; ``avertissement_separation``
      est alors posé au niveau de la fiche (jamais mélangé aux données
      publiques) ; ``simulations`` = liste, ``[]`` si ``project_id`` est None.

    Lève ``KeyError(f"Plante introuvable : id={plant_id}")`` si la plante
    est absente. Ne retourne que des types JSON-sérialisables.
    """
    plante = db.get(Plant, plant_id)
    if plante is None:
        raise KeyError(f"Plante introuvable : id={plant_id}")

    # Liens plante-composé : la seule source de vérité phytochimique.
    liens = (
        db.query(PlantCompound)
        .filter(PlantCompound.plant_id == plant_id)
        .order_by(PlantCompound.id)
        .all()
    )
    ids_composes = {lien.compound_id for lien in liens}
    composes = (
        {c.id: c for c in db.query(Compound).filter(Compound.id.in_(ids_composes)).all()}
        if ids_composes
        else {}
    )

    # Pharmacologie : preuves liées par la règle DOI explicite.
    dois_lies = {lien.doi for lien in liens if lien.doi}
    preuves = (
        db.query(Evidence)
        .filter(Evidence.doi.in_(dois_lies))
        .order_by(Evidence.id)
        .all()
        if dois_lies
        else []
    )
    preuves_par_id: dict[int, Evidence] = {p.id: p for p in preuves}
    # Preuves pointées directement par les relations (evidence_id) :
    # la chaîne donnée → publication → DOI reste traçable même quand le
    # DOI du lien est absent. On collecte TOUS les evidence_id pointés :
    # liens plante-composé, activités de la plante et cibles de ses
    # composés — sinon une activité/cible dont la preuve n'est liée à
    # aucun PlantCompound afficherait « preuve=None ».
    ids_composes = {lien.compound_id for lien in liens}
    ids_activites = [
        eid
        for (eid,) in db.query(PlantActivity.evidence_id)
        .filter(PlantActivity.plant_id == plant_id)
        .all()
    ]
    ids_cibles = (
        [
            eid
            for (eid,) in db.query(CompoundTarget.evidence_id)
            .filter(CompoundTarget.compound_id.in_(ids_composes))
            .all()
        ]
        if ids_composes
        else []
    )
    ids_preuves_pointees = {
        identifiant
        for identifiant in (
            [lien.evidence_id for lien in liens] + ids_activites + ids_cibles
        )
        if identifiant
    }
    if ids_preuves_pointees - set(preuves_par_id):
        for preuve in (
            db.query(Evidence)
            .filter(Evidence.id.in_(ids_preuves_pointees - set(preuves_par_id)))
            .all()
        ):
            preuves_par_id[preuve.id] = preuve
    pharmacologie = [_item_evidence(p) for p in preuves]
    securite = [
        {**item, "avertissement": AVERTISSEMENT_SECURITE}
        for item, preuve in zip(pharmacologie, preuves)
        if _contient_mot_cle_prudence(preuve)
    ]

    # Bibliographie : preuves liées + publications des liens sans DOI.
    bibliographie = [_citer_evidence(p) for p in preuves]
    for lien in liens:
        if not lien.doi:
            citation = _citer_publication_lien(lien)
            if citation and citation not in bibliographie:
                bibliographie.append(citation)

    fiche: dict = {
        "plante": _section_plante(plante),
        "botanique": _section_botanique(plante),
        "pharmacognosie": _section_pharmacognosie(plante, liens),
        "phytochimie": _section_phytochimie(liens, composes, preuves_par_id),
        "pharmacologie": pharmacologie,
        "activites_pharmacologiques": _section_activites(db, plant_id, preuves_par_id),
        "cibles": _section_cibles(db, ids_composes, composes, preuves_par_id),
        "securite": securite,
        "bibliographie": bibliographie,
        "litterature": _section_litterature(preuves, db, dois_lies),
    }

    if project_id is None:
        fiche["laboratoire_prive"] = []
        fiche["simulations"] = []
    else:
        # Forme du contrat : laboratoire_prive = liste d'observations, et
        # avertissement_separation au niveau de la fiche (jamais mélangé).
        section_labo = _section_laboratoire_prive(db, project_id)
        fiche["laboratoire_prive"] = section_labo["observations"]
        fiche["avertissement_separation"] = section_labo["avertissement_separation"]
        fiche["simulations"] = _section_simulations(db, project_id)

    return fiche
