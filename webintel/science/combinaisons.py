"""Études de combinaison de composés — MODE A (screening structural).

Le chercheur sélectionne : plante + partie de plante + plusieurs
composés + cible biologique éventuelle + publications de référence. Le
système produit un **dossier d'analyse informatique en 12 points** :

1. identité des composés · 2. structures · 3. similarité ·
4. propriétés moléculaires · 5. cibles connues/documentées ·
6. cibles communes · 7. littérature existante ·
8. docking lorsque disponible · 9. comparaison des résultats ·
10. limites · 11. hypothèses proposées · 12. conclusion informatique.

RÈGLE DURE — honnêteté scientifique ABSOLUE
------------------------------------------
- La conclusion est TOUJOURS formulée « Résultat de screening
  informatique » / « Hypothèse à vérifier expérimentalement », JAMAIS
  « efficace » / « sûr ».
- Aucune valeur n'est inventée : sans le module chimoinformatique
  (``webintel.science.chimie``, chantier parallèle — accès DÉFENSIF),
  les sections concernées affichent un message explicite et le statut
  de l'étude passe à « partiel ».
- Le « score informatique » est une moyenne pondérée de descripteurs
  normalisés (formule documentée ci-dessous) — il n'est JAMAIS appelé
  « efficacité thérapeutique » ni présenté comme tel.
- ``verifier_honnetete()`` refuse tout texte contenant une formulation
  interdite : ``construire_dossier_12_points()`` lève ``RuntimeError``
  plutôt que de produire un rapport non conforme (testé
  automatiquement).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from webintel.core.utils import sha256_hex, utcnow
from webintel.science.models import (
    CombinationStudy,
    Compound,
    Evidence,
    Plant,
    PlantCompound,
    SynergyAnalysis,
)

# ---------------------------------------------------------------------------
# Garde-fou anti-formulations interdites — centralisé dans
# ``webintel.science.honnetete`` (règle produit : jamais « efficace »,
# « sûr », « traite », « guérit », « soigne » sans source expérimentale).
# Les noms sont réexportés ici pour compatibilité (tests, rapport.py).
# ---------------------------------------------------------------------------
# Racines bannies des rapports générés (même dans une négation, par
# prudence) : voir ``honnetete.FORMULATIONS_INTERDITES``.
# Les mises en garde sont formulées sans ces racines (« ne permet pas
# de conclure à un effet pharmacologique », « sécurité d'emploi »).
from webintel.science.honnetete import (  # noqa: F401
    FORMULATIONS_INTERDITES,
    _exiger_honnete,
    exiger_dossier_honnete,
    texte_genere,
    verifier_honnetete,
)


# ---------------------------------------------------------------------------
# Accès DÉFENSIF au contrat chimoinformatique (chantier parallèle)
# ---------------------------------------------------------------------------
# ``webintel.science.chimie`` exposera : rdkit_disponible(),
# empreinte_morgan(smiles, rayon=2, taille=2048), tanimoto_fp(fp1, fp2),
# descripteurs(smiles), sous_structure(smiles, motif_smarts),
# infos_biblio(). Le module peut être absent ou incomplet : chaque accès
# est protégé (message explicite, jamais de crash).
def _module_chimie():
    try:
        from webintel.science import chimie  # type: ignore

        return chimie
    except ImportError:
        return None


def _fonction_chimie(nom: str):
    module = _module_chimie()
    if module is None:
        return None
    fonction = getattr(module, nom, None)
    return fonction if callable(fonction) else None


def chimie_disponible() -> tuple[bool, str]:
    """Disponibilité réelle du module chimoinformatique (ne lève jamais)."""
    module = _module_chimie()
    if module is None:
        return False, (
            "Module chimoinformatique « webintel.science.chimie » "
            "indisponible (chantier parallèle en cours) : les sections "
            "structures / similarité / propriétés sont limitées aux "
            "données stockées en base."
        )
    manquantes = [
        nom
        for nom in (
            "rdkit_disponible", "empreinte_morgan", "tanimoto_fp",
            "descripteurs", "sous_structure", "infos_biblio",
        )
        if not callable(getattr(module, nom, None))
    ]
    if manquantes:
        return False, (
            "Module chimoinformatique partiellement disponible "
            f"(fonctions manquantes : {', '.join(manquantes)})."
        )
    try:
        rdkit_ok = bool(module.rdkit_disponible())
    except Exception:
        rdkit_ok = False
    if not rdkit_ok:
        return False, (
            "Module chimoinformatique présent mais RDKit indisponible : "
            "calculs de structures impossibles (données stockées en base "
            "uniquement)."
        )
    return True, ""


# ---------------------------------------------------------------------------
# Score informatique — formule documentée (JAMAIS « efficacité »)
# ---------------------------------------------------------------------------
# « Score informatique de comparaison structurale » : moyenne pondérée
# de descripteurs moléculaires normalisés + diversité structurale.
# C'est un indicateur de COMPARAISON (profilage), pas une mesure
# d'activité pharmacologique.
#
# Par composé (chaque composante dans [0, 1], 1 = profil favorable) :
#   s_lipinski = 1 − min(violations_lipinski, 4) / 4
#   s_masse    = 1 − min(|masse − 300| / 300, 1)      (cible ~300 Da)
#   s_logp     = 1 − min(|logP − 2.5| / 2.5, 1)       (cible ~2.5)
#   s_tpsa     = 1 si TPSA ≤ 140, sinon max(0, 1 − (TPSA − 140) / 140)
#   profil     = moyenne des composantes DISPONIBLES (les manquantes
#                sont signalées, pas remplacées par une valeur inventée)
# Pour la combinaison :
#   diversite  = 1 − moyenne des similarités de Tanimoto par paire
#                (1 = structures très différentes)
#   score      = 0.6 × moyenne(profils) + 0.4 × diversite
SCORE_FORMULE = (
    "score = 0.6 × moyenne(profils) + 0.4 × diversite ; "
    "profil = moyenne(s_lipinski, s_masse, s_logp, s_tpsa) ; "
    "diversite = 1 − moyenne(Tanimoto par paire). "
    "Indicateur de comparaison structurale — PAS une mesure d'effet pharmacologique."
)
SCORE_VERSION = "score-comparaison-structurale/1.0"


def _comp_profil(descripteurs: dict | None) -> dict:
    """Calcule les composantes du profil d'un composé (None si non calculable)."""
    if not descripteurs:
        return {"profil": None, "composantes": {}, "manquantes": ["descripteurs"]}
    comp, manquantes = {}, []
    viol = descripteurs.get("lipinski_violations")
    if isinstance(viol, (int, float)):
        comp["s_lipinski"] = round(1.0 - min(float(viol), 4.0) / 4.0, 4)
    else:
        manquantes.append("lipinski_violations")
    masse = descripteurs.get("masse_molaire")
    if isinstance(masse, (int, float)):
        comp["s_masse"] = round(1.0 - min(abs(float(masse) - 300.0) / 300.0, 1.0), 4)
    else:
        manquantes.append("masse_molaire")
    logp = descripteurs.get("logp")
    if isinstance(logp, (int, float)):
        comp["s_logp"] = round(1.0 - min(abs(float(logp) - 2.5) / 2.5, 1.0), 4)
    else:
        manquantes.append("logp")
    tpsa = descripteurs.get("tpsa")
    if isinstance(tpsa, (int, float)):
        tpsa = float(tpsa)
        comp["s_tpsa"] = round(1.0 if tpsa <= 140 else max(0.0, 1.0 - (tpsa - 140) / 140), 4)
    else:
        manquantes.append("tpsa")
    profil = round(sum(comp.values()) / len(comp), 4) if comp else None
    return {"profil": profil, "composantes": comp, "manquantes": manquantes}


def score_informatique(
    descripteurs_par_compose: list[dict | None],
    similarites_par_paire: list[float],
) -> dict:
    """Calcule le score informatique de comparaison structurale.

    Formule : voir ``SCORE_FORMULE``. Retourne ``{"score", "formule",
    "version", "details", "note"}`` ; ``score`` vaut ``None`` si les
    données sont insuffisantes (jamais de valeur inventée).
    """
    profils = [_comp_profil(d) for d in descripteurs_par_compose]
    valeurs = [p["profil"] for p in profils if p["profil"] is not None]
    if not valeurs:
        return {
            "score": None,
            "formule": SCORE_FORMULE,
            "version": SCORE_VERSION,
            "details": {"profils": profils, "diversite": None},
            "note": "Score non calculable : descripteurs moléculaires "
                    "indisponibles pour tous les composés (aucune valeur "
                    "inventée).",
        }
    moyenne_profils = sum(valeurs) / len(valeurs)
    sims = [s for s in similarites_par_paire if isinstance(s, (int, float))]
    diversite = round(1.0 - sum(sims) / len(sims), 4) if sims else None
    if diversite is None:
        score = round(moyenne_profils, 4)
        note = (
            "Diversité structurale non calculable (similarités "
            "indisponibles) : score = moyenne des profils uniquement."
        )
    else:
        score = round(0.6 * moyenne_profils + 0.4 * diversite, 4)
        note = (
            "Moyenne pondérée de descripteurs normalisés : indicateur de "
            "comparaison structurale, PAS une mesure d'effet pharmacologique."
        )
    return {
        "score": score,
        "formule": SCORE_FORMULE,
        "version": SCORE_VERSION,
        "details": {
            "profils": profils,
            "moyenne_profils": round(moyenne_profils, 4),
            "diversite": diversite,
            "nb_similarites": len(sims),
        },
        "note": note,
    }


# ---------------------------------------------------------------------------
# Hachage des entrées (reproductibilité)
# ---------------------------------------------------------------------------

def hacher_entrees(entrees: dict) -> str:
    """Empreinte SHA-256 des entrées normalisées (JSON canonique trié)."""
    canonique = json.dumps(entrees, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return sha256_hex(canonique)


# ---------------------------------------------------------------------------
# Construction du dossier en 12 points
# ---------------------------------------------------------------------------

AVERTISSEMENT_ETUDE = (
    "Résultat de screening informatique — hypothèse à vérifier "
    "expérimentalement. Ne constitue pas une preuve d'effet "
    "pharmacologique et ne se prononce pas sur la sécurité d'emploi."
)

LIMITES_ETUDE = [
    "Analyse strictement informatique : aucune mesure expérimentale "
    "n'est produite ici.",
    "La similarité structurale n'implique pas une activité comparable "
    "(« activity cliffs » : des structures proches peuvent avoir des "
    "effets très différents).",
    "Le score informatique est un indicateur de comparaison, pas une "
    "mesure d'effet pharmacologique.",
    "Les cibles et la littérature citées sont des associations "
    "observées/rapportées, jamais des validations.",
    "Tout docking éventuel reste une prédiction (énergie estimée), à "
    "confirmer expérimentalement.",
]


def _infos_compose(db: Session, compound_id: int) -> dict:
    compose = db.get(Compound, compound_id)
    if compose is None:
        raise ValueError(f"Composé introuvable : id={compound_id}.")
    return {
        "id": compose.id,
        "nom": compose.name,
        "synonymes": list(compose.synonyms or []),
        "formule": compose.formula,
        "masse_molaire_base": compose.molecular_weight,
        "smiles": compose.smiles,
        "inchi": compose.inchi,
        "inchikey": compose.inchikey,
        "classe_chimique": compose.chemical_class,
        "ids_externes": dict(compose.external_ids or {}),
    }


def _section_structures(infos: list[dict]) -> dict:
    """Section 2 — structures (via chimie.py défensif, sinon base)."""
    ok, raison = chimie_disponible()
    descripteurs_fn = _fonction_chimie("descripteurs")
    lignes = []
    for info in infos:
        ligne = {
            "compose_id": info["id"],
            "nom": info["nom"],
            "smiles_base": info["smiles"],
            "formule_base": info["formule"],
            "inchikey_base": info["inchikey"],
        }
        if ok and descripteurs_fn and info["smiles"]:
            try:
                desc = descripteurs_fn(info["smiles"])
            except Exception as exc:  # calcul défensif : pas de crash
                desc = None
                ligne["erreur_calcul"] = str(exc)
            ligne["descripteurs_calcules"] = desc
            ligne["source"] = "calcul chimoinformatique (RDKit)"
        else:
            ligne["descripteurs_calcules"] = None
            ligne["source"] = "base WebIntel (calcul indisponible)"
            ligne["note"] = raison
        lignes.append(ligne)
    return {
        "titre": "2. Structures",
        "chimie_disponible": ok,
        "raison_indisponibilite": None if ok else raison,
        "composes": lignes,
    }


def _section_similarite(infos: list[dict]) -> dict:
    """Section 3 — similarité de Tanimoto par paire (Morgan, défensif)."""
    ok, raison = chimie_disponible()
    emp_fn = _fonction_chimie("empreinte_morgan")
    tan_fn = _fonction_chimie("tanimoto_fp")
    paires = []
    if ok and emp_fn and tan_fn:
        empreintes = {}
        for info in infos:
            if info["smiles"]:
                try:
                    empreintes[info["id"]] = emp_fn(info["smiles"])
                except Exception:
                    empreintes[info["id"]] = None
        ids = [info["id"] for info in infos]
        noms = {info["id"]: info["nom"] for info in infos}
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                sim = None
                if empreintes.get(a) is not None and empreintes.get(b) is not None:
                    try:
                        sim = round(float(tan_fn(empreintes[a], empreintes[b])), 4)
                    except Exception:
                        sim = None
                paires.append({
                    "compose_a": noms[a], "compose_b": noms[b],
                    "tanimoto": sim,
                    "note": None if sim is not None else (
                        "Similarité non calculable pour cette paire "
                        "(empreinte indisponible)."
                    ),
                })
    return {
        "titre": "3. Similarité structurale",
        "methode": "Indice de Tanimoto sur empreintes de Morgan (rayon 2, 2048 bits)",
        "chimie_disponible": ok,
        "raison_indisponibilite": None if ok else raison,
        "paires": paires,
        "note": (
            "Une similarité élevée n'implique pas des effets comparables."
            if paires else
            "Aucune similarité calculée : module chimoinformatique indisponible."
        ),
    }


def _section_proprietes(section_structures: dict) -> dict:
    """Section 4 — propriétés moléculaires (descripteurs calculés ou base)."""
    lignes = []
    for comp in section_structures["composes"]:
        desc = comp.get("descripteurs_calcules") or {}
        lignes.append({
            "compose_id": comp["compose_id"],
            "nom": comp["nom"],
            "formule": desc.get("formule") or comp.get("formule_base"),
            "masse_molaire": desc.get("masse_molaire"),
            "logp": desc.get("logp"),
            "hbd": desc.get("hbd"),
            "hba": desc.get("hba"),
            "tpsa": desc.get("tpsa"),
            "liaisons_rotatables": desc.get("rotatable_bonds"),
            "fraction_csp3": desc.get("fraction_csp3"),
            "violations_lipinski": desc.get("lipinski_violations"),
            "smiles_canonique": desc.get("smiles_canonique"),
            "source": comp.get("source"),
        })
    return {
        "titre": "4. Propriétés moléculaires",
        "composes": lignes,
        "note": "Descripteurs calculés informatiquement (ou issus de la base) : "
                "ils décrivent la structure, pas un effet pharmacologique.",
    }


def _section_cibles(
    db: Session, infos: list[dict], cible_etude: str | None
) -> dict:
    """Sections 5-6 — cibles documentées et cibles communes.

    Utilise la table ``compound_targets`` (v2.1, chantier parallèle —
    accès défensif) : chaque cible est DOCUMENTÉE (source/publication
    citées), jamais « validée ». Sans cible en base : message explicite,
    aucune valeur inventée.
    """
    try:
        from webintel.science.models import CompoundTarget
    except ImportError:
        CompoundTarget = None

    par_compose: dict[int, list[dict]] = {}
    for info in infos:
        cibles = []
        if CompoundTarget is not None:
            try:
                lignes = (
                    db.query(CompoundTarget)
                    .filter(CompoundTarget.compound_id == info["id"])
                    .order_by(CompoundTarget.id)
                    .all()
                )
            except Exception:
                lignes = []
            for ligne in lignes:
                cibles.append({
                    "cible": ligne.target_name,
                    "type_cible": ligne.target_type,
                    "interaction": ligne.interaction_type,
                    "affinite": ligne.affinity_value,
                    "unite_affinite": ligne.affinity_unit,
                    "source": ligne.source,
                    "publication": ligne.publication,
                    "doi": ligne.doi,
                    "niveau": ligne.data_level,
                    "note": "Cible documentée (rapportée avec source), "
                            "jamais validée par WebIntel.",
                })
        par_compose[info["id"]] = cibles

    s5 = {
        "titre": "5. Cibles connues / documentées",
        "cible_visee_par_etude": cible_etude,
        "composes": [
            {
                "compose_id": info["id"],
                "nom": info["nom"],
                "cibles_documentees": par_compose[info["id"]],
                "note": (
                    "Aucune cible documentée en base pour ce composé "
                    "(aucune valeur inventée)."
                    if not par_compose[info["id"]] else None
                ),
            }
            for info in infos
        ],
    }
    # Cibles communes : intersection des noms normalisés.
    ensembles = [
        {(c["cible"] or "").strip().lower()
         for c in par_compose[info["id"]] if c["cible"]}
        for info in infos
    ]
    communes = sorted(set.intersection(*ensembles)) if ensembles else []
    s6 = {
        "titre": "6. Cibles communes",
        "cibles_communes": [
            {
                "cible": nom,
                "detail_par_compose": [
                    {"compose": info["nom"],
                     "interaction": next(
                         (c["interaction"] for c in par_compose[info["id"]]
                          if (c["cible"] or "").strip().lower() == nom),
                         None)}
                    for info in infos
                ],
                "note": "Cible documentée pour chaque composé (sources en "
                        "section 5) : piste à croiser avec la littérature, "
                        "pas une validation.",
            }
            for nom in communes
        ],
        "note": (
            "Aucune cible commune documentée pour ces composés."
            if not communes else
            f"{len(communes)} cible(s) documentée(s) pour tous les composés."
        ),
    }
    return s5, s6


def _section_litterature(
    db: Session, infos: list[dict], plant_id: int | None,
    plant_part: str | None, evidence_ids: list[int],
) -> dict:
    """Section 7 — littérature existante (associations observées)."""
    references = []
    # 7a. Preuves explicitement sélectionnées par le chercheur.
    for eid in evidence_ids:
        ev = db.get(Evidence, eid)
        if ev is None:
            continue
        references.append({
            "type": "reference_chercheur",
            "titre": ev.title,
            "reference": ev.reference,
            "doi": ev.doi,
            "annee": ev.year,
            "extrait": ev.excerpt,
        })
    # 7b. Associations plante-composé rapportées (table plant_compounds).
    if plant_id is not None:
        ids = [info["id"] for info in infos]
        liens = (
            db.query(PlantCompound)
            .filter(PlantCompound.plant_id == plant_id,
                    PlantCompound.compound_id.in_(ids))
            .all()
        )
        noms = {info["id"]: info["nom"] for info in infos}
        for lien in liens:
            if plant_part and lien.plant_part != plant_part:
                continue
            references.append({
                "type": "association_rapportee",
                "compose": noms.get(lien.compound_id),
                "partie_plante": lien.plant_part,
                "publication": lien.publication,
                "doi": lien.doi,
                "annee": lien.year,
                "confiance": lien.confidence,
                "note": "Association observée dans la littérature "
                        "(rapportée, non validée).",
            })
    # 7c. Mentions textuelles dans les preuves (recherche simple).
    if not references:
        for info in infos:
            nom = (info["nom"] or "").strip().lower()
            if not nom:
                continue
            for ev in db.query(Evidence).limit(200).all():
                texte = " ".join([
                    ev.title or "", ev.reference or "", ev.excerpt or ""
                ]).lower()
                if nom in texte:
                    references.append({
                        "type": "mention_textuelle",
                        "compose": info["nom"],
                        "titre": ev.title,
                        "doi": ev.doi,
                        "annee": ev.year,
                        "note": "Mention textuelle détectée automatiquement : "
                                "association observée dans la littérature, "
                                "à vérifier manuellement.",
                    })
                    break
    return {
        "titre": "7. Littérature existante",
        "references": references,
        "note": (
            "Références = associations observées dans la littérature. "
            "Aucune ne constitue une validation d'effet."
            if references else
            "Aucune référence trouvée dans la base pour ces composés : "
            "littérature à constituer par le chercheur."
        ),
    }


def _section_docking(infos: list[dict]) -> dict:
    """Section 8 — docking lorsque disponible (jamais de faux score)."""
    try:
        from webintel.science import docking as docking_module

        disponible, raison = docking_module.statut_moteur()
    except ImportError:
        disponible, raison = False, "Module de workflow docking indisponible."
    return {
        "titre": "8. Docking moléculaire",
        "moteur_disponible": disponible,
        "raison": None if disponible else raison,
        "note": (
            "Moteur de docking disponible : un calcul peut être lancé via "
            "le workflow docking (résultat = prédiction informatique)."
            if disponible else
            "Moteur de docking indisponible : aucun score de docking "
            "n'est produit (aucune valeur inventée)."
        ),
    }


def construire_dossier_12_points(
    db: Session,
    *,
    compound_ids: list[int],
    plant_id: int | None = None,
    plant_part: str | None = None,
    target: str | None = None,
    evidence_ids: list[int] | None = None,
) -> tuple[dict, str]:
    """Construit le dossier d'analyse en 12 points (+ statut).

    Retourne ``(dossier, statut)`` où ``statut`` vaut "termine" ou
    "partiel" (calculs indisponibles). Lève ``ValueError`` si les
    entrées sont invalides, ``RuntimeError`` si le dossier généré
    contient une formulation interdite (auto-contrôle).
    """
    evidence_ids = evidence_ids or []
    if len(compound_ids) < 2:
        raise ValueError(
            "Une étude de combinaison requiert au moins 2 composés "
            f"(reçu : {len(compound_ids)})."
        )
    # Déduplique en préservant l'ordre (les doublons fausseraient les paires).
    vus, uniques = set(), []
    for cid in compound_ids:
        if cid not in vus:
            vus.add(cid)
            uniques.append(cid)
    if len(uniques) < 2:
        raise ValueError(
            "Une étude de combinaison requiert au moins 2 composés distincts."
        )
    infos = [_infos_compose(db, cid) for cid in uniques]

    plante = None
    if plant_id is not None:
        plante = db.get(Plant, plant_id)
        if plante is None:
            raise ValueError(f"Plante introuvable : id={plant_id}.")
    for eid in evidence_ids:
        if db.get(Evidence, eid) is None:
            raise ValueError(f"Preuve introuvable : id={eid}.")

    s1 = {
        "titre": "1. Identité des composés",
        "composes": [
            {
                "id": i["id"], "nom": i["nom"], "synonymes": i["synonymes"],
                "formule": i["formule"], "classe_chimique": i["classe_chimique"],
                "inchikey": i["inchikey"],
            }
            for i in infos
        ],
    }
    s2 = _section_structures(infos)
    s3 = _section_similarite(infos)
    s4 = _section_proprietes(s2)
    s5, s6 = _section_cibles(db, infos, target)
    s7 = _section_litterature(db, infos, plant_id, plant_part, evidence_ids)
    s8 = _section_docking(infos)

    descripteurs = [c.get("descripteurs_calcules") for c in s2["composes"]]
    similarites = [
        p["tanimoto"] for p in s3["paires"] if p["tanimoto"] is not None
    ]
    score = score_informatique(descripteurs, similarites)
    s9 = {
        "titre": "9. Comparaison des résultats",
        "score_informatique": score,
        "tableau_proprietes": s4["composes"],
        "tableau_similarites": s3["paires"],
        "note": "Comparaison strictement structurale et informatique.",
    }
    s10 = {"titre": "10. Limites", "limites": LIMITES_ETUDE}
    s11 = {
        "titre": "11. Hypothèses proposées",
        "hypotheses": [
            "Hypothèse à vérifier expérimentalement : les similarités et "
            "profils ci-dessus suggèrent des comparaisons à tester en "
            "laboratoire (aucune conclusion d'effet ne peut en être tirée).",
            "Hypothèse à vérifier expérimentalement : croiser ces résultats "
            "informatiques avec la littérature (section 7) avant toute "
            "expérimentation.",
        ],
        "note": "Chaque hypothèse est une piste de travail, pas un résultat.",
    }
    conclusion = (
        "Résultat de screening informatique : cette étude compare "
        f"{len(infos)} composés sur des critères structuraux calculés "
        "(similarité, descripteurs, score de comparaison). "
        + (
            f"Score informatique de comparaison structurale : {score['score']} "
            f"(formule : {score['formule']}). "
            if score["score"] is not None else
            "Score informatique non calculable (données insuffisantes, "
            "aucune valeur inventée). "
        )
        + "Hypothèse à vérifier expérimentalement : toute décision "
        "d'expérimentation doit reposer sur des données de laboratoire, "
        "pas sur ce dossier. " + AVERTISSEMENT_ETUDE
    )
    s12 = {
        "titre": "12. Conclusion informatique",
        "conclusion": conclusion,
        "avertissement": AVERTISSEMENT_ETUDE,
    }

    dossier = {
        "plante": (
            {"id": plante.id, "nom_scientifique": plante.scientific_name,
             "partie": plant_part}
            if plante else None
        ),
        "cible_visee": target,
        "sections": [s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11, s12],
        "avertissement": AVERTISSEMENT_ETUDE,
        "data_level": "prediction",
    }
    # Auto-contrôle : aucune formulation interdite dans le texte GÉNÉRÉ.
    # Les verbatims sourcés (extraits, titres, références cités avec leur
    # source) sont exclus du scan : ce ne sont pas des affirmations du
    # logiciel (voir webintel.science.honnetete).
    exiger_dossier_honnete(dossier, "dossier 12 points")

    partiel = (
        not s2["chimie_disponible"]
        or score["score"] is None
        or not s8["moteur_disponible"]
    )
    return dossier, ("partiel" if partiel else "termine")


def _entrees_normalisees(
    *,
    compound_ids: list[int],
    plant_id: int | None,
    plant_part: str | None,
    target: str | None,
    evidence_ids: list[int],
    parametres: dict,
) -> dict:
    return {
        "compound_ids": sorted(set(compound_ids)),
        "plant_id": plant_id,
        "plant_part": plant_part,
        "target": (target or "").strip() or None,
        "evidence_ids": sorted(set(evidence_ids)),
        "parametres": parametres or {},
    }


def creer_etude(
    db: Session,
    *,
    project_id: int,
    nom: str,
    compound_ids: list[int],
    plant_id: int | None = None,
    plant_part: str | None = None,
    target: str | None = None,
    evidence_ids: list[int] | None = None,
    parametres: dict | None = None,
    description: str | None = None,
    cree_par: str | None = None,
) -> CombinationStudy:
    """Crée et persiste une étude de combinaison (MODE A).

    Ne committe jamais (convention : c'est au routeur). Enregistre les
    entrées normalisées, leur hash SHA-256, le dossier en 12 points, le
    statut et la provenance (versions des bibliothèques).
    """
    evidence_ids = evidence_ids or []
    parametres = parametres or {}
    entrees = _entrees_normalisees(
        compound_ids=compound_ids, plant_id=plant_id, plant_part=plant_part,
        target=target, evidence_ids=evidence_ids, parametres=parametres,
    )
    dossier, statut = construire_dossier_12_points(
        db,
        compound_ids=entrees["compound_ids"],
        plant_id=plant_id, plant_part=plant_part,
        target=entrees["target"], evidence_ids=entrees["evidence_ids"],
    )
    chimie_ok, chimie_raison = chimie_disponible()
    try:
        infos_biblio = (_fonction_chimie("infos_biblio") or (lambda: {}))()
    except Exception:
        infos_biblio = {}
    etude = CombinationStudy(
        project_id=project_id,
        nom=nom,
        description=description,
        plant_id=plant_id,
        plant_part=plant_part,
        compound_ids=entrees["compound_ids"],
        target=entrees["target"],
        evidence_ids=entrees["evidence_ids"],
        parametres=entrees["parametres"],
        resultats=dossier,
        inputs_hash=hacher_entrees(entrees),
        statut=statut,
        data_level="prediction",
        provenance={
            "cree_par": cree_par,
            "cree_le": utcnow().isoformat(),
            "chimie_disponible": chimie_ok,
            "chimie_raison": chimie_raison,
            "chimie_infos_biblio": infos_biblio,
            "score_version": SCORE_VERSION,
        },
    )
    db.add(etude)
    db.flush()
    # Traçabilité (v2.3) : chaque étude créée alimente le journal de
    # provenance (table ``provenances``) — la fiche
    # ``/resultats/etude/{id}/provenance`` affiche alors la vraie
    # traçabilité au lieu d'un 404 honnête. Idempotent : recréer la même
    # étude ne duplique pas la ligne (clé entité + source + connecteur +
    # version). Import différé : `service` n'est pas importé ici en tête
    # pour éviter tout risque de cycle d'import.
    from webintel.science import service as _service  # noqa: PLC0415

    _service.record_provenance(
        db,
        "etude",
        etude.id,
        source_name=f"Étude de combinaison « {nom} »",
        connector="webintel.science.combinaisons",
        connector_version=SCORE_VERSION,
        data_version="dossier-12-points",
        acteur=cree_par,
        niveau_preuve="prediction",
        commentaire=(
            "Résultat informatique : dossier de screening structural en "
            "12 points — hypothèses de travail, jamais une preuve d'effet."
        ),
        parametres={
            "compound_ids": entrees["compound_ids"],
            "plant_id": plant_id,
        },
    )
    return etude


def get_etude(db: Session, etude_id: int) -> CombinationStudy:
    """Charge une étude ou lève ``KeyError`` (404)."""
    etude = db.get(CombinationStudy, etude_id)
    if etude is None:
        raise KeyError(f"Étude de combinaison introuvable : id={etude_id}.")
    return etude


def list_etudes(
    db: Session,
    project_id: int,
    limit: int | None = None,
    offset: int = 0,
) -> list[CombinationStudy]:
    """Études d'un projet (isolation projet stricte).

    `limit=None` (défaut) conserve le comportement historique (tout) ;
    `limit`/`offset` activent la pagination.
    """
    requete = (
        db.query(CombinationStudy)
        .filter(CombinationStudy.project_id == project_id)
        .order_by(CombinationStudy.id.desc())
        .offset(max(0, offset))
    )
    if limit is not None:
        requete = requete.limit(max(1, limit))
    return requete.all()


# ---------------------------------------------------------------------------
# MODE B — persistance des analyses de synergie
# ---------------------------------------------------------------------------

def creer_analyse_synergie(
    db: Session,
    *,
    project_id: int,
    nom: str,
    methode: str,
    donnees: dict,
    parametres: dict | None = None,
    is_private: bool = True,
    cree_par: str | None = None,
) -> SynergyAnalysis:
    """Crée et persiste une analyse de synergie (MODE B).

    Exécute la méthode UNIQUEMENT sur les données expérimentales
    fournies ; ``DonneesInsuffisantes`` (message « données insuffisantes
    pour la méthode X ») est propagée SANS persistance d'un faux
    résultat. Ne committe jamais.
    """
    from webintel.science.synergie import analyser_synergie

    resultat = analyser_synergie(methode, donnees or {}, parametres or {})
    nom_methode = resultat["methode"]
    entrees = {
        "methode": nom_methode,
        "donnees": donnees or {},
        "parametres": parametres or {},
    }
    analyse = SynergyAnalysis(
        project_id=project_id,
        nom=nom,
        methode=nom_methode,
        donnees=donnees or {},
        parametres=parametres or {},
        resultats=resultat,
        inputs_hash=hacher_entrees(entrees),
        statut="termine",
        data_level="prediction",
        is_private=is_private,
        provenance={
            "cree_par": cree_par,
            "cree_le": utcnow().isoformat(),
            "formule": resultat.get("formule"),
            "reference": resultat.get("reference"),
        },
    )
    db.add(analyse)
    db.flush()
    return analyse


def get_analyse_synergie(db: Session, analyse_id: int) -> SynergyAnalysis:
    analyse = db.get(SynergyAnalysis, analyse_id)
    if analyse is None:
        raise KeyError(f"Analyse de synergie introuvable : id={analyse_id}.")
    return analyse


def list_analyses_synergie(
    db: Session, project_id: int, inclure_prive: bool = False
) -> list[SynergyAnalysis]:
    """Analyses d'un projet ; privées exclues sauf ``inclure_prive``."""
    requete = db.query(SynergyAnalysis).filter(
        SynergyAnalysis.project_id == project_id
    )
    if not inclure_prive:
        requete = requete.filter(SynergyAnalysis.is_private.is_(False))
    return requete.order_by(SynergyAnalysis.id.desc()).all()


# ---------------------------------------------------------------------------
# Intégration au dossier d'export existant (export_dossier.py)
# ---------------------------------------------------------------------------

def ajouter_au_dossier(
    archive, contenu_manifest: list, niveaux: set,
    db: Session, project_id: int, inclure_prive: bool = False,
) -> None:
    """Ajoute études de combinaison + analyses de synergie au ZIP dossier.

    Appelé par ``export_dossier.build_dossier_zip`` (import paresseux,
    erreur encapsulée par l'appelant) : n'écrit que des JSON
    reproductibles (entrées, hash, résultats, versions).
    """
    for etude in list_etudes(db, project_id):
        chemin = f"combinaisons/etude_{etude.id}.json"
        archive.writestr(
            chemin,
            json.dumps({
                "id": etude.id, "nom": etude.nom,
                "description": etude.description,
                "inputs_hash": etude.inputs_hash,
                "statut": etude.statut,
                "data_level": etude.data_level,
                "compound_ids": etude.compound_ids,
                "plant_id": etude.plant_id,
                "plant_part": etude.plant_part,
                "target": etude.target,
                "evidence_ids": etude.evidence_ids,
                "parametres": etude.parametres,
                "resultats": etude.resultats,
                "provenance": etude.provenance,
                "avertissement": AVERTISSEMENT_ETUDE,
            }, ensure_ascii=False, indent=2),
        )
        contenu_manifest.append({
            "fichier": chemin, "type": "etude_combinaison",
            "etude_id": etude.id, "data_level": "prediction",
            "avertissement": AVERTISSEMENT_ETUDE,
        })
        niveaux.add("prediction")
    for analyse in list_analyses_synergie(db, project_id, inclure_prive):
        chemin = f"synergie/analyse_{analyse.id}_{analyse.methode}.json"
        archive.writestr(
            chemin,
            json.dumps({
                "id": analyse.id, "nom": analyse.nom,
                "methode": analyse.methode,
                "inputs_hash": analyse.inputs_hash,
                "statut": analyse.statut,
                "data_level": analyse.data_level,
                "is_private": analyse.is_private,
                "donnees": analyse.donnees,
                "parametres": analyse.parametres,
                "resultats": analyse.resultats,
                "provenance": analyse.provenance,
            }, ensure_ascii=False, indent=2),
        )
        contenu_manifest.append({
            "fichier": chemin, "type": "analyse_synergie",
            "analyse_id": analyse.id, "methode": analyse.methode,
            "data_level": "prediction",
        })
        niveaux.add("prediction")
        if analyse.is_private:
            niveaux.add("experimental")


# ---------------------------------------------------------------------------
# Rendus exportables d'une étude
# ---------------------------------------------------------------------------

def rendre_markdown_etude(etude: CombinationStudy) -> str:
    """Rend le dossier 12 points en Markdown (texte contrôlé)."""
    lignes = [
        f"# Étude de combinaison — {etude.nom}",
        "",
        f"> {AVERTISSEMENT_ETUDE}",
        "",
        f"Hash des entrées : `{etude.inputs_hash}` · Statut : {etude.statut} · "
        f"Niveau : {etude.data_level}",
        "",
    ]
    for section in (etude.resultats or {}).get("sections", []):
        lignes.append(f"## {section.get('titre', '')}")
        lignes.append("")
        lignes.append(_section_vers_markdown(section))
        lignes.append("")
    # Auto-contrôle du texte généré (verbatims sourcés exclus du scan).
    exiger_dossier_honnete(etude.resultats or {}, "rendu Markdown étude")
    return "\n".join(lignes)


def _section_vers_markdown(section: dict) -> str:
    titre = section.get("titre", "")
    if titre.startswith("12."):
        return section.get("conclusion", "")
    if titre.startswith("10."):
        return "\n".join(f"- {limite}" for limite in section.get("limites", []))
    if titre.startswith("11."):
        lignes = [f"- {h}" for h in section.get("hypotheses", [])]
        if section.get("note"):
            lignes.append(f"\n*{section['note']}*")
        return "\n".join(lignes)
    if titre.startswith("9."):
        score = section.get("score_informatique") or {}
        return (
            f"Score informatique : **{score.get('score')}**\n\n"
            f"Formule : {score.get('formule')}\n\n{score.get('note', '')}"
        )
    if titre.startswith("8."):
        return section.get("note", "")
    if titre.startswith("7."):
        refs = section.get("references", [])
        if not refs:
            return section.get("note", "")
        return "\n".join(
            f"- [{r.get('type')}] {r.get('titre') or r.get('publication') or r.get('compose', '')}"
            + (f" (DOI : {r['doi']})" if r.get("doi") else "")
            for r in refs
        )
    if titre.startswith("5.") or titre.startswith("6."):
        return section.get("note", "")
    if titre.startswith("3."):
        paires = section.get("paires", [])
        if not paires:
            return section.get("note", "")
        return "\n".join(
            f"- {p['compose_a']} × {p['compose_b']} : Tanimoto = {p['tanimoto']}"
            for p in paires
        )
    if titre.startswith("4."):
        lignes = []
        for c in section.get("composes", []):
            lignes.append(
                f"- **{c['nom']}** : formule={c.get('formule')}, "
                f"masse={c.get('masse_molaire')}, logP={c.get('logp')}, "
                f"TPSA={c.get('tpsa')}, violations Lipinski={c.get('violations_lipinski')}"
            )
        lignes.append(f"\n*{section.get('note', '')}*")
        return "\n".join(lignes)
    if titre.startswith("2."):
        lignes = []
        for c in section.get("composes", []):
            lignes.append(f"- **{c['nom']}** : SMILES `{c.get('smiles_base')}`")
        if section.get("raison_indisponibilite"):
            lignes.append(f"\n*{section['raison_indisponibilite']}*")
        return "\n".join(lignes)
    if titre.startswith("1."):
        return "\n".join(
            f"- **{c['nom']}** (id={c['id']}, formule={c.get('formule')})"
            for c in section.get("composes", [])
        )
    return section.get("note", "") or ""


def rendre_html_etude(etude: CombinationStudy) -> str:
    """Rend le dossier 12 points en page HTML autonome (imprimable)."""
    import html as _html

    def ech(texte):
        return _html.escape(str(texte if texte is not None else "—"))

    parties = [
        "<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>",
        f"<title>Étude de combinaison — {ech(etude.nom)}</title>",
        "<style>body{font-family:sans-serif;max-width:900px;margin:2em auto;"
        "padding:0 1em;color:#222}h1{color:#1a5276}.avert{background:#fef9e7;"
        "border-left:4px solid #d4ac0d;padding:1em;margin:1em 0}"
        "h2{color:#1a5276;border-bottom:1px solid #ccc;padding-bottom:.3em}"
        "table{border-collapse:collapse;width:100%;margin:.5em 0}"
        "th,td{border:1px solid #ccc;padding:.4em;text-align:left}"
        "th{background:#eaf2f8}.meta{color:#555;font-size:.9em}</style>",
        "</head><body>",
        f"<h1>Étude de combinaison — {ech(etude.nom)}</h1>",
        f"<p class='meta'>Hash des entrées : <code>{ech(etude.inputs_hash)}</code>"
        f" · Statut : {ech(etude.statut)} · Niveau de données : "
        f"{ech(etude.data_level)} (prédiction informatique)</p>",
        f"<div class='avert'><strong>Avertissement :</strong> "
        f"{ech(AVERTISSEMENT_ETUDE)}</div>",
    ]
    for section in (etude.resultats or {}).get("sections", []):
        parties.append(f"<h2>{ech(section.get('titre', ''))}</h2>")
        md = _section_vers_markdown(section)
        # Markdown minimal → HTML (listes et paragraphes uniquement).
        for bloc in md.split("\n\n"):
            bloc = bloc.strip()
            if not bloc:
                continue
            if bloc.startswith("- "):
                items = "".join(
                    f"<li>{ech(l[2:])}</li>" for l in bloc.split("\n")
                    if l.startswith("- ")
                )
                parties.append(f"<ul>{items}</ul>")
            else:
                parties.append(f"<p>{ech(bloc)}</p>")
    parties.append(
        f"<div class='avert'><strong>Avertissement :</strong> "
        f"{ech(AVERTISSEMENT_ETUDE)}</div></body></html>"
    )
    page = "\n".join(parties)
    # Auto-contrôle du texte généré uniquement : la page rend aussi des
    # verbatims sourcés (titres, extraits cités avec leur source) et le nom
    # de l'étude choisi par le chercheur — ni les uns ni l'autre ne sont des
    # affirmations du logiciel.
    exiger_dossier_honnete(etude.resultats or {}, "rendu HTML étude")
    return page
