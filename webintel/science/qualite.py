"""Contrôle qualité automatique des données scientifiques.

Neuf détecteurs en lecture seule — **aucune correction automatique** :
chaque anomalie est une fiche ``{"categorie", "severite", "entite_type",
"entite_id", "titre", "detail", "action_proposee"}`` que le chercheur
traite à la main (page « Contrôle qualité des données »).

Sévérités : ``"info"`` (lacune documentaire), ``"avertissement"``
(incohérence à vérifier), ``"critique"`` (intégrité menacée).

``executer_controle(session)`` exécute les 9 détecteurs et retourne le
rapport complet. Lecture seule : aucune écriture en base.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from webintel.core.utils import utcnow
from webintel.science import chimie as _chimie
from webintel.science.litterature import normaliser_titre
from webintel.science.mesures import dimension_unite, unite_connue
from webintel.science.models import (
    Compound,
    Echantillon,
    Evidence,
    MesureExperimentale,
    Plant,
    PlantCompound,
    Specimen,
)

__all__ = [
    "SEVERITES",
    "executer_controle",
    "detecter_smiles_invalides",
    "detecter_doublons_composes",
    "detecter_doublons_publications",
    "detecter_taxons_ambigus",
    "detecter_synonymes_contradictoires",
    "detecter_relations_sans_source",
    "detecter_echantillons_sans_voucher",
    "detecter_donnees_incompletes",
    "detecter_unites_incoherentes",
]

SEVERITES = ("info", "avertissement", "critique")

_BINOME = re.compile(r"^[A-Z][a-zéèêëàâîïôöùûüç-]+\s+[a-zéèêëàâîïôöùûüç-]+")

_STATUTS_TAXON_AMBIGUS = {"SYNONYM", "DOUBTFUL", "MISAPPLIED"}


def _anomalie(
    categorie: str,
    severite: str,
    entite_type: str,
    entite_id: int | None,
    titre: str,
    detail: str,
    action_proposee: str,
) -> dict[str, Any]:
    return {
        "categorie": categorie,
        "severite": severite,
        "entite_type": entite_type,
        "entite_id": entite_id,
        "titre": titre,
        "detail": detail,
        "action_proposee": action_proposee,
    }


def _normaliser_nom(nom: str | None) -> str:
    return normaliser_titre(nom)


# ---------------------------------------------------------------------------
# 1. SMILES invalides
# ---------------------------------------------------------------------------


def detecter_smiles_invalides(session: Session) -> list[dict[str, Any]]:
    """Composés dont le SMILES ne parse pas (RDKit requis).

    Sans RDKit, la vérification est impossible : le détecteur retourne
    une liste vide (le rapport global le signale dans
    ``verifications_ignorees``).
    """
    anomalies: list[dict[str, Any]] = []
    if not _chimie.rdkit_disponible():
        return anomalies
    for compose in session.query(Compound).all():
        if not (compose.smiles or "").strip():
            continue
        diagnostic = _chimie.diagnostic_smiles(compose.smiles)
        if not diagnostic["valide"]:
            anomalies.append(
                _anomalie(
                    "smiles_invalides",
                    "avertissement",
                    "compound",
                    compose.id,
                    f"SMILES invalide : {compose.name}",
                    f"Le SMILES « {compose.smiles} » ne parse pas "
                    f"({diagnostic['raison'] or 'raison inconnue'}).",
                    "Corriger le SMILES depuis la source d'origine "
                    "(connecteur, publication) ou vider le champ "
                    "plutôt que de garder une structure fausse.",
                )
            )
    return anomalies


# ---------------------------------------------------------------------------
# 2. Doublons — composés
# ---------------------------------------------------------------------------


def detecter_doublons_composes(session: Session) -> list[dict[str, Any]]:
    """Doublons de composés : même InChIKey (critique) ou même nom (avertissement)."""
    anomalies: list[dict[str, Any]] = []
    par_inchikey: dict[str, list[Compound]] = defaultdict(list)
    par_nom: dict[str, list[Compound]] = defaultdict(list)
    for compose in session.query(Compound).all():
        if (compose.inchikey or "").strip():
            par_inchikey[compose.inchikey.strip()].append(compose)
        if (compose.name or "").strip():
            par_nom[_normaliser_nom(compose.name)].append(compose)
    for inchikey, groupe in sorted(par_inchikey.items()):
        if len(groupe) > 1:
            noms = ", ".join(f"#{c.id} « {c.name} »" for c in groupe)
            anomalies.append(
                _anomalie(
                    "doublons_composes",
                    "critique",
                    "compound",
                    groupe[0].id,
                    f"InChIKey partagée : {inchikey}",
                    f"L'InChIKey « {inchikey} » (identifiant chimique "
                    f"unique) est portée par {len(groupe)} composés : {noms}.",
                    "Fusionner les fiches en gardant la plus complète, "
                    "ou corriger l'InChIKey erronée depuis la source.",
                )
            )
    for nom_norm, groupe in sorted(par_nom.items()):
        if len(groupe) > 1 and len({c.id for c in groupe}) > 1:
            noms = ", ".join(f"#{c.id} « {c.name} »" for c in groupe)
            anomalies.append(
                _anomalie(
                    "doublons_composes",
                    "avertissement",
                    "compound",
                    groupe[0].id,
                    f"Nom en doublon : « {groupe[0].name} »",
                    f"{len(groupe)} composés partagent le même nom "
                    f"normalisé : {noms}.",
                    "Vérifier s'il s'agit du même composé (fusionner) ou "
                    "de noms ambigus (préciser : sel, stéréoisomère...).",
                )
            )
    return anomalies


# ---------------------------------------------------------------------------
# 3. Doublons — publications
# ---------------------------------------------------------------------------


def detecter_doublons_publications(session: Session) -> list[dict[str, Any]]:
    """Doublons de preuves : même DOI, même PMID ou même titre normalisé (+ année)."""
    anomalies: list[dict[str, Any]] = []
    par_doi: dict[str, list[Evidence]] = defaultdict(list)
    par_pmid: dict[str, list[Evidence]] = defaultdict(list)
    par_titre: dict[tuple[str, int | None], list[Evidence]] = defaultdict(list)
    for preuve in session.query(Evidence).all():
        if (preuve.doi or "").strip():
            par_doi[preuve.doi.strip()].append(preuve)
        if (preuve.pmid or "").strip():
            par_pmid[preuve.pmid.strip()].append(preuve)
        cle_titre = normaliser_titre(preuve.title)
        if cle_titre:
            par_titre[(cle_titre, preuve.year)].append(preuve)
    for doi, groupe in sorted(par_doi.items()):
        if len(groupe) > 1:
            ids = ", ".join(f"#{p.id}" for p in groupe)
            anomalies.append(
                _anomalie(
                    "doublons_publications",
                    "critique",
                    "evidence",
                    groupe[0].id,
                    f"DOI en doublon : {doi}",
                    f"Le DOI « {doi} » apparaît sur {len(groupe)} preuves "
                    f"({ids}) — l'unicité DOI est violée.",
                    "Fusionner les preuves en gardant la plus complète.",
                )
            )
    for pmid, groupe in sorted(par_pmid.items()):
        if len(groupe) > 1:
            ids = ", ".join(f"#{p.id}" for p in groupe)
            anomalies.append(
                _anomalie(
                    "doublons_publications",
                    "avertissement",
                    "evidence",
                    groupe[0].id,
                    f"PMID en doublon : {pmid}",
                    f"Le PMID « {pmid} » apparaît sur {len(groupe)} preuves "
                    f"({ids}).",
                    "Fusionner les preuves en gardant la plus complète.",
                )
            )
    for (titre_norm, annee), groupe in sorted(par_titre.items()):
        if len(groupe) <= 1:
            continue
        # Un vrai doublon a déjà été signalé via DOI/PMID : on ne
        # signale ici que les groupes sans identifiant externe commun.
        dois = {p.doi for p in groupe if p.doi}
        pmids = {p.pmid for p in groupe if p.pmid}
        if len(dois) <= 1 and len(pmids) <= 1 and not (dois or pmids):
            ids = ", ".join(f"#{p.id}" for p in groupe)
            annee_txt = f" ({annee})" if annee else ""
            anomalies.append(
                _anomalie(
                    "doublons_publications",
                    "avertissement",
                    "evidence",
                    groupe[0].id,
                    f"Titre en doublon : « {groupe[0].title[:60]} »{annee_txt}",
                    f"{len(groupe)} preuves sans DOI/PMID partagent le même "
                    f"titre normalisé ({ids}).",
                    "Vérifier qu'il s'agit du même document (fusionner) ou "
                    "de documents distincts au titre proche (préciser "
                    "l'institution / le type de document).",
                )
            )
    return anomalies


# ---------------------------------------------------------------------------
# 4. Taxons ambigus
# ---------------------------------------------------------------------------


def detecter_taxons_ambigus(session: Session) -> list[dict[str, Any]]:
    """Noms scientifiques suspects : non-binomiaux, synonymes GBIF, conflits."""
    anomalies: list[dict[str, Any]] = []
    plantes = session.query(Plant).all()
    noms_acceptes = {_normaliser_nom(p.scientific_name): p for p in plantes}
    for plante in plantes:
        nom = (plante.scientific_name or "").strip()
        if not _BINOME.match(nom):
            anomalies.append(
                _anomalie(
                    "taxons_ambigus",
                    "avertissement",
                    "plant",
                    plante.id,
                    f"Nom scientifique non binomial : « {nom} »",
                    "Le nom ne suit pas le format « Genre espece » — "
                    "possible coquille, nom vernaculaire ou rang "
                    "supra-spécifique.",
                    "Vérifier le nom contre une flore de référence "
                    "(ex. via le connecteur GBIF) et corriger.",
                )
            )
        ids_externes = (
            plante.external_ids if isinstance(plante.external_ids, dict) else {}
        )
        info_gbif = ids_externes.get("gbif")
        if isinstance(info_gbif, dict):
            # Le connecteur GBIF enregistre un dict (clé « statut ») ;
            # les données historiques peuvent contenir la clé taxon brute
            # (chaîne) ou un dict au format brut de l'API (clé « status »).
            statut_gbif = info_gbif.get("statut") or info_gbif.get("status")
        else:
            statut_gbif = None
        if statut_gbif in _STATUTS_TAXON_AMBIGUS:
            anomalies.append(
                _anomalie(
                    "taxons_ambigus",
                    "avertissement",
                    "plant",
                    plante.id,
                    f"Statut GBIF ambigu : « {nom} » → {statut_gbif}",
                    f"GBIF considère ce nom comme « {statut_gbif} » "
                    "(synonyme ou application douteuse).",
                    "Adopter le nom accepté GBIF (clé taxon enregistrée "
                    "dans les identifiants externes) ou justifier le "
                    "maintien du nom.",
                )
            )
        for synonyme in plante.synonyms or []:
            cle = _normaliser_nom(str(synonyme))
            autre = noms_acceptes.get(cle)
            if autre is not None and autre.id != plante.id:
                anomalies.append(
                    _anomalie(
                        "taxons_ambigus",
                        "avertissement",
                        "plant",
                        plante.id,
                        f"Nom accepté ailleurs comme synonyme : « {synonyme} »",
                        f"« {synonyme} » est synonyme de « {nom} » "
                        f"(#{plante.id}) mais nom accepté de "
                        f"« {autre.scientific_name} » (#{autre.id}).",
                        "Lever l'ambiguïté : un nom ne peut être à la fois "
                        "accepté et synonyme — corriger l'un des deux.",
                    )
                )
    return anomalies


# ---------------------------------------------------------------------------
# 5. Synonymes contradictoires
# ---------------------------------------------------------------------------


def detecter_synonymes_contradictoires(session: Session) -> list[dict[str, Any]]:
    """Un même synonyme rattaché à plusieurs entités, ou synonyme = nom accepté."""
    anomalies: list[dict[str, Any]] = []
    for modele, label, champ_nom in (
        (Plant, "plant", "scientific_name"),
        (Compound, "compound", "name"),
    ):
        par_synonyme: dict[str, list[Any]] = defaultdict(list)
        noms_acceptes: dict[str, Any] = {}
        for entite in session.query(modele).all():
            nom_accepte = _normaliser_nom(getattr(entite, champ_nom))
            if nom_accepte:
                noms_acceptes[nom_accepte] = entite
            for synonyme in entite.synonyms or []:
                cle = _normaliser_nom(str(synonyme))
                if cle:
                    par_synonyme[cle].append(entite)
        for cle, groupe in sorted(par_synonyme.items()):
            ids = {e.id for e in groupe}
            if len(ids) > 1:
                noms = ", ".join(
                    f"#{e.id} « {getattr(e, champ_nom)} »" for e in groupe
                )
                anomalies.append(
                    _anomalie(
                        "synonymes_contradictoires",
                        "avertissement",
                        label,
                        groupe[0].id,
                        f"Synonyme partagé : « {groupe[0].synonyms} »",
                        f"Le synonyme « {cle} » est rattaché à {len(ids)} "
                        f"{label}s : {noms}.",
                        "Déterminer la bonne entité et retirer le synonyme "
                        "des autres.",
                    )
                )
            acceptee = noms_acceptes.get(cle)
            if acceptee is not None and acceptee.id not in ids:
                anomalies.append(
                    _anomalie(
                        "synonymes_contradictoires",
                        "avertissement",
                        label,
                        groupe[0].id,
                        f"Synonyme = nom accepté : « {cle} »",
                        f"« {cle} » est synonyme de "
                        f"« {getattr(groupe[0], champ_nom)} » (#{groupe[0].id}) "
                        f"et nom accepté de "
                        f"« {getattr(acceptee, champ_nom)} » (#{acceptee.id}).",
                        "Un nom ne peut être synonyme et accepté à la fois "
                        "— corriger l'un des deux.",
                    )
                )
    return anomalies


# ---------------------------------------------------------------------------
# 6. Relations sans source
# ---------------------------------------------------------------------------


def detecter_relations_sans_source(session: Session) -> list[dict[str, Any]]:
    """Associations plante-composé sans aucune source traçable."""
    anomalies: list[dict[str, Any]] = []
    for lien in session.query(PlantCompound).all():
        sans_evidence = lien.evidence_id is None
        sans_doi = not (lien.doi or "").strip()
        sans_publication = not (lien.publication or "").strip()
        if sans_evidence and sans_doi and sans_publication:
            anomalies.append(
                _anomalie(
                    "relations_sans_source",
                    "avertissement",
                    "plant_compound",
                    lien.id,
                    f"Association sans source : plante #{lien.plant_id} ↔ "
                    f"composé #{lien.compound_id}",
                    "Ni preuve (evidence_id), ni DOI, ni référence de "
                    "publication : l'origine de cette association est "
                    "intraçable.",
                    "Rattacher une preuve (publication, thèse, rapport) ou "
                    "supprimer l'association si elle est infondée.",
                )
            )
    return anomalies


# ---------------------------------------------------------------------------
# 7. Échantillons sans voucher
# ---------------------------------------------------------------------------


def detecter_echantillons_sans_voucher(session: Session) -> list[dict[str, Any]]:
    """Échantillons sans voucher : ``Specimen`` (ancien modèle) et
    ``Echantillon`` (chaîne terrain v2.2, ``voucher_id`` NULL).

    Un échantillon sans spécimen d'herbier déposé n'est pas retraçable
    physiquement : l'anomalie est un avertissement à traiter à la main
    (aucune correction automatique).
    """
    anomalies: list[dict[str, Any]] = []
    for specimen in session.query(Specimen).all():
        if not (specimen.code_voucher or "").strip():
            anomalies.append(
                _anomalie(
                    "echantillons_sans_voucher",
                    "avertissement",
                    "specimen",
                    specimen.id,
                    f"Spécimen #{specimen.id} sans code voucher",
                    "Aucun code de dépôt en herbier : l'échantillon "
                    "physique n'est pas retraçable.",
                    "Saisir le code voucher (ex. OUA-2026-0147) après "
                    "dépôt en herbier.",
                )
            )
        if not (specimen.herbier_depot or "").strip():
            anomalies.append(
                _anomalie(
                    "echantillons_sans_voucher",
                    "info",
                    "specimen",
                    specimen.id,
                    f"Spécimen #{specimen.id} sans herbier de dépôt",
                    "L'herbier de dépôt n'est pas renseigné.",
                    "Préciser l'herbier (ex. Herbier National du "
                    "Burkina Faso — OUA).",
                )
            )
    # v2.2 : chaîne terrain — échantillons sans voucher rattaché.
    for echantillon in (
        session.query(Echantillon)
        .filter(Echantillon.supprime_le.is_(None))
        .all()
    ):
        if echantillon.voucher_id is None:
            anomalies.append(
                _anomalie(
                    "echantillons_sans_voucher",
                    "avertissement",
                    "echantillon",
                    echantillon.id,
                    "Échantillon de terrain sans voucher : "
                    f"« {echantillon.code_echantillon} »",
                    "Aucun spécimen d'herbier rattaché : l'échantillon "
                    "physique n'est pas retraçable (vérification "
                    "taxonomique impossible a posteriori).",
                    "Déposer un spécimen en herbier puis le rattacher "
                    "(voucher), ou documenter pourquoi aucun dépôt "
                    "n'est possible.",
                )
            )
    return anomalies


# ---------------------------------------------------------------------------
# 8. Données incomplètes
# ---------------------------------------------------------------------------


def detecter_donnees_incompletes(session: Session) -> list[dict[str, Any]]:
    """Champs manquants qui limitent l'usage scientifique."""
    anomalies: list[dict[str, Any]] = []
    for compose in session.query(Compound).all():
        if not (compose.formula or "").strip() and not (compose.smiles or "").strip():
            anomalies.append(
                _anomalie(
                    "donnees_incompletes",
                    "info",
                    "compound",
                    compose.id,
                    f"Composé sans formule ni SMILES : « {compose.name} »",
                    "Aucune information structurale : impossible de "
                    "calculer descripteurs ou similarité.",
                    "Compléter depuis une source chimique (connecteur "
                    "PubChem/ChEMBL) ou la publication d'origine.",
                )
            )
    for preuve in session.query(Evidence).all():
        if not (preuve.title or "").strip():
            anomalies.append(
                _anomalie(
                    "donnees_incompletes",
                    "avertissement",
                    "evidence",
                    preuve.id,
                    f"Preuve #{preuve.id} sans titre",
                    "Une preuve sans titre est inexploitable en "
                    "bibliographie.",
                    "Saisir le titre exact du document.",
                )
            )
        elif preuve.year is None and not (preuve.authors or []):
            anomalies.append(
                _anomalie(
                    "donnees_incompletes",
                    "info",
                    "evidence",
                    preuve.id,
                    f"Preuve sans année ni auteurs : « {preuve.title[:60]} »",
                    "Année et auteurs manquants : citation incomplète.",
                    "Compléter l'année et les auteurs depuis le document.",
                )
            )
    for plante in session.query(Plant).all():
        if not (plante.family or "").strip():
            anomalies.append(
                _anomalie(
                    "donnees_incompletes",
                    "info",
                    "plant",
                    plante.id,
                    f"Plante sans famille : « {plante.scientific_name} »",
                    "Famille botanique non renseignée.",
                    "Compléter la famille (flore de référence ou "
                    "connecteur GBIF).",
                )
            )
    for mesure in session.query(MesureExperimentale).all():
        valeurs = [mesure.dose, mesure.concentration, mesure.response]
        if all(v is None for v in valeurs):
            anomalies.append(
                _anomalie(
                    "donnees_incompletes",
                    "avertissement",
                    "mesure_experimentale",
                    mesure.id,
                    f"Mesure #{mesure.id} sans aucune valeur",
                    "Ni dose, ni concentration, ni réponse : la mesure "
                    "n'apporte aucune donnée.",
                    "Compléter ou supprimer la ligne.",
                )
            )
    return anomalies


# ---------------------------------------------------------------------------
# 9. Unités incohérentes
# ---------------------------------------------------------------------------

_CHAMPS_UNITES_MESURE = (
    "dose_unite",
    "concentration_unite",
    "response_unite",
    "time_unite",
    "unite",
)


def detecter_unites_incoherentes(session: Session) -> list[dict[str, Any]]:
    """Unités inconnues ou mélangées pour un même test/composé."""
    anomalies: list[dict[str, Any]] = []
    mesures = session.query(MesureExperimentale).all()
    for mesure in mesures:
        for champ in _CHAMPS_UNITES_MESURE:
            valeur = getattr(mesure, champ)
            if valeur and str(valeur).strip() and not unite_connue(valeur):
                anomalies.append(
                    _anomalie(
                        "unites_incoherentes",
                        "avertissement",
                        "mesure_experimentale",
                        mesure.id,
                        f"Unité inconnue sur la mesure #{mesure.id} : "
                        f"« {valeur} » ({champ})",
                        "Unité hors vocabulaire : risque d'erreur "
                        "d'interprétation (facteur 1000...).",
                        "Vérifier et uniformiser l'unité depuis le "
                        "protocole — sans convertir silencieusement "
                        "les valeurs existantes.",
                    )
                )
    # Unités mélangées pour un même (test, composé).
    par_groupe: dict[tuple[str | None, str | None], dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for mesure in mesures:
        cle = (mesure.assay, mesure.compound_name or str(mesure.compound_id))
        for champ in ("concentration_unite", "dose_unite", "response_unite"):
            valeur = getattr(mesure, champ)
            if valeur and str(valeur).strip():
                par_groupe[cle][champ].add(str(valeur).strip().lower())
    for (test, compose), champs in sorted(
        par_groupe.items(), key=lambda kv: str(kv[0])
    ):
        for champ, unites in champs.items():
            if len(unites) > 1:
                anomalies.append(
                    _anomalie(
                        "unites_incoherentes",
                        "avertissement",
                        "mesure_experimentale",
                        None,
                        f"Unités mélangées ({champ}) pour « {test} » / "
                        f"« {compose} »",
                        f"Unités différentes sur le même test : "
                        f"{', '.join(sorted(unites))} — comparaisons "
                        "directes invalides.",
                        "Uniformiser explicitement (nouvelle version "
                        "« normalise » de chaque mesure, avec note de "
                        "conversion) — jamais de conversion silencieuse.",
                    )
                )
    # Incohérence dimensionnelle : même champ, dimensions différentes.
    for mesure in mesures:
        for champ in ("dose_unite", "concentration_unite", "response_unite", "time_unite"):
            valeur = getattr(mesure, champ)
            dimension_attendue = {
                "dose_unite": "dose",
                "concentration_unite": "concentration",
                "response_unite": "reponse",
                "time_unite": "temps",
            }[champ]
            if valeur and str(valeur).strip() and unite_connue(valeur):
                dimension_reelle = dimension_unite(valeur)
                if dimension_reelle != dimension_attendue and not (
                    dimension_attendue == "reponse"
                    and dimension_reelle == "concentration"
                ):
                    anomalies.append(
                        _anomalie(
                            "unites_incoherentes",
                            "avertissement",
                            "mesure_experimentale",
                            mesure.id,
                            f"Dimension incohérente sur la mesure #{mesure.id} "
                            f"({champ} = « {valeur} »)",
                            f"« {valeur} » est une unité de "
                            f"« {dimension_reelle} », pas de "
                            f"« {dimension_attendue} ».",
                            "Corriger l'unité (probable erreur de saisie) "
                            "depuis le protocole.",
                        )
                    )
    return anomalies


# ---------------------------------------------------------------------------
# Rapport global
# ---------------------------------------------------------------------------

_DETECTEURS = (
    ("smiles_invalides", detecter_smiles_invalides),
    ("doublons_composes", detecter_doublons_composes),
    ("doublons_publications", detecter_doublons_publications),
    ("taxons_ambigus", detecter_taxons_ambigus),
    ("synonymes_contradictoires", detecter_synonymes_contradictoires),
    ("relations_sans_source", detecter_relations_sans_source),
    ("echantillons_sans_voucher", detecter_echantillons_sans_voucher),
    ("donnees_incompletes", detecter_donnees_incompletes),
    ("unites_incoherentes", detecter_unites_incoherentes),
)


def executer_controle(
    session: Session, detecteurs: list[str] | None = None
) -> dict[str, Any]:
    """Exécute les 9 détecteurs — lecture seule, aucune correction.

    ``detecteurs`` restreint le contrôle à une sélection (noms de
    ``_DETECTEURS``) ; ``None`` = les 9 détecteurs. Un nom inconnu lève
    ``ValueError`` (message explicite).

    Retourne ``{"date_controle", "anomalies", "resume",
    "verifications_ignorees"}``. ``resume`` compte par sévérité et par
    catégorie.
    """
    selection = list(_DETECTEURS)
    if detecteurs is not None:
        connus = {nom for nom, _ in _DETECTEURS}
        inconnus = [nom for nom in detecteurs if nom not in connus]
        if inconnus:
            raise ValueError(
                "Détecteur(s) inconnu(s) : "
                + ", ".join(inconnus)
                + ". Détecteurs : "
                + ", ".join(sorted(connus))
                + "."
            )
        selection = [(nom, fn) for nom, fn in _DETECTEURS if nom in detecteurs]
    anomalies: list[dict[str, Any]] = []
    ignorees: list[str] = []
    if not _chimie.rdkit_disponible():
        ignorees.append(
            "smiles_invalides : RDKit indisponible — les SMILES n'ont pas "
            "pu être validés chimiquement."
        )
    for _nom, detecteur in selection:
        anomalies.extend(detecteur(session))
    par_severite: dict[str, int] = {s: 0 for s in SEVERITES}
    par_categorie: dict[str, int] = {}
    for anomalie in anomalies:
        par_severite[anomalie["severite"]] = (
            par_severite.get(anomalie["severite"], 0) + 1
        )
        par_categorie[anomalie["categorie"]] = (
            par_categorie.get(anomalie["categorie"], 0) + 1
        )
    return {
        "date_controle": utcnow().isoformat(),
        "anomalies": anomalies,
        "resume": {
            "total": len(anomalies),
            "par_severite": par_severite,
            "par_categorie": par_categorie,
        },
        "verifications_ignorees": ignorees,
    }
