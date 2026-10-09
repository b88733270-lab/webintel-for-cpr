"""Services de l'extension v2.2 : terrain, ethnobotanique, phytochimie.

Conventions (comme ``webintel.science.service``) :

- la session SQLAlchemy est TOUJOURS le premier argument ;
- AUCUNE fonction ne committe : l'appelant (routeur) committe ;
- écritures idempotentes via ``get_or_create()`` quand un identifiant
  naturel existe ; les journaux (usages, pics) sont toujours créés ;
- RÈGLE ABSOLUE : jamais de données inventées — colonnes NULLABLES,
  langue jamais devinée, GPS jamais inventé, suppressions = soft-delete.
"""

from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from webintel.core.utils import utcnow
from webintel.database.helpers import get_or_create
from webintel.config.settings import get_settings
from webintel.science.models import (
    AnalyseChimique,
    Compound,
    DataLevel,
    Echantillon,
    Extraction,
    Extrait,
    Fraction,
    FractionCompound,
    LANGUES_BURKINA,
    Langue,
    NiveauConfidentialite,
    NomVernaculaire,
    Pharmacognosie,
    PicChromatographique,
    Plant,
    StatutEpistemique,
    StatutIdentification,
    TechniqueAnalyse,
    UsageTraditionnel,
    Voucher,
    enregistrer_langue,
    langue_connue,
)

__all__ = [
    "normaliser_texte",
    "normaliser_nom_vernaculaire",
    "retirer_controles_bidi",
    "ajouter_nom_vernaculaire",
    "lister_noms_vernaculaires",
    "ajouter_usage_traditionnel",
    "lister_usages_traditionnels",
    "creer_echantillon",
    "lister_echantillons",
    "creer_voucher",
    "lister_vouchers",
    "lier_echantillon_voucher",
    "creer_extraction",
    "creer_extrait",
    "creer_fraction",
    "lier_fraction_compose",
    "chaine_echantillon",
    "creer_analyse_chimique",
    "lister_analyses_chimiques",
    "ajouter_pic",
    "lister_pics",
    "definir_pharmacognosie",
    "lire_pharmacognosie",
    "definir_statut_epistemique",
    "supprimer_doucement",
    "rechercher_plantes",
    "export_public_plante",
    "sanitiser_echantillon_public",
    "sanitiser_usage_public",
    "valider_gps",
    "enregistrer_langue_db",
    "lister_langues",
    "langue_connue_db",
    "enregistrer_fichier_brut",
    "verifier_integrite_fichier",
    "MOTIFS_RECHERCHE",
    "BANDEAU_USAGE_TRADITIONNEL",
]

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normaliser_texte(texte: str | None) -> str:
    """Normalise un texte pour la comparaison : minuscules, sans accents,
    espaces réduits. ``None`` / vide -> ``""``."""
    if not texte:
        return ""
    decompose = unicodedata.normalize("NFKD", str(texte))
    sans_accents = "".join(
        c for c in decompose if not unicodedata.combining(c)
    )
    return " ".join(sans_accents.lower().split())


#: Contrôles de directionnalité Unicode (U+061C, U+200E/F, U+202A-E,
#: U+2066-9) : invisibles mais capables de réordonner l'affichage d'un
#: nom (usurpation visuelle, ex. un nom vernaculaire affiché comme un
#: autre). Retirés des noms affichés — jamais de 500, jamais de perte
#: de contenu visible.
_CONTROLES_BIDI = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u2066\u2067\u2068\u2069"
)


def retirer_controles_bidi(texte: str | None) -> str | None:
    """Retire les contrôles de directionnalité Unicode d'un texte affiché.

    Audit adversarial (2e passe) : ``html.escape`` neutralise le HTML
    mais pas ces caractères invisibles — un nom vernaculaire comme
    ``"Quinine\\u202eEVIL"`` pouvait s'afficher dans un ordre trompeur.
    ``None`` reste ``None``.
    """
    if texte is None:
        return None
    return "".join(c for c in str(texte) if c not in _CONTROLES_BIDI)


def normaliser_nom_vernaculaire(nom: str) -> str:
    """Forme normalisée d'un nom vernaculaire (recherche).

    Lève ``ValueError`` si le nom est vide : un nom vernaculaire vide
    n'est jamais enregistré.
    """
    normalise = normaliser_texte(nom)
    if not normalise:
        raise ValueError(
            "Le nom vernaculaire ne peut pas être vide."
        )
    return normalise


# ---------------------------------------------------------------------------
# GPS : validation de plage (jamais de coordonnées impossibles)
# ---------------------------------------------------------------------------

#: Plages géographiques valides (degrés décimaux WGS84).
LATITUDE_MIN, LATITUDE_MAX = -90.0, 90.0
LONGITUDE_MIN, LONGITUDE_MAX = -180.0, 180.0


def valider_gps(
    latitude: float | None, longitude: float | None
) -> None:
    """Valide des coordonnées GPS (plages WGS84).

    ``None`` est accepté (GPS non renseigné — jamais inventé). Toute
    valeur hors plage (ex. latitude = 999, faute de frappe) lève
    ``ValueError`` avec un message explicite : des coordonnées
    impossibles ne sont jamais enregistrées silencieusement.
    """
    if latitude is not None:
        try:
            lat = float(latitude)
        except (TypeError, ValueError):
            raise ValueError(
                f"Latitude invalide : {latitude!r} (nombre attendu)."
            ) from None
        if not (LATITUDE_MIN <= lat <= LATITUDE_MAX):
            raise ValueError(
                f"Latitude hors plage : {latitude!r} "
                f"(attendu entre {LATITUDE_MIN} et {LATITUDE_MAX} degrés)."
            )
    if longitude is not None:
        try:
            lon = float(longitude)
        except (TypeError, ValueError):
            raise ValueError(
                f"Longitude invalide : {longitude!r} (nombre attendu)."
            ) from None
        if not (LONGITUDE_MIN <= lon <= LONGITUDE_MAX):
            raise ValueError(
                f"Longitude hors plage : {longitude!r} "
                f"(attendu entre {LONGITUDE_MIN} et {LONGITUDE_MAX} degrés)."
            )


# ---------------------------------------------------------------------------
# Registre des langues (persistance — la table ``Langue``)
# ---------------------------------------------------------------------------


def langue_connue_db(session: Session, code: str | None) -> bool:
    """La langue ``code`` est-elle enregistrée (base OU registre mémoire) ?

    ``None`` (ou chaîne vide) = « non renseignée » → toujours accepté.
    La langue n'est JAMAIS devinée : cette fonction ne fait que vérifier
    une déclaration explicite.
    """
    if code is None:
        return True
    norme = (code or "").strip().lower()
    if not norme:
        return True
    if session is not None:
        existe = (
            session.query(Langue)
            .filter(Langue.code == norme, Langue.actif.is_(True))
            .first()
        )
        if existe is not None:
            return True
    return langue_connue(code)


def _chaine_ou_aucune(valeur: Any, nom_champ: str) -> str | None:
    """Valide un champ texte du registre des langues (audit adversarial).

    ``None`` accepté ; toute autre valeur non-chaîne lève ``ValueError``
    (→ 422) au lieu de provoquer ``AttributeError`` → 500 plus bas.
    """
    if valeur is None:
        return None
    if not isinstance(valeur, str):
        raise ValueError(
            f"Le champ « {nom_champ} » doit être une chaîne de caractères "
            f"(reçu : {type(valeur).__name__})."
        )
    return valeur


def enregistrer_langue_db(
    session: Session,
    code: str,
    libelle: str,
    region: str | None = None,
    source: str | None = None,
) -> tuple[Langue, bool]:
    """Enregistre une langue au registre (base + mémoire), idempotent.

    Clé d'idempotence : ``code`` normalisé. Une langue existante mais
    désactivée (``actif=False``) est réactivée — jamais supprimée.
    Lève ``ValueError`` si le code est vide ou si un champ n'est pas
    une chaîne (robustesse : jamais de 500 sur entrée mal typée).
    Ne committe jamais.
    """
    code_texte = _chaine_ou_aucune(code, "code")
    code_norm = (code_texte or "").strip().lower()
    if not code_norm:
        raise ValueError("Le code de langue ne peut pas être vide.")
    libelle_texte = _chaine_ou_aucune(libelle, "libelle")
    libelle_propre = (libelle_texte or "").strip() or code_norm
    region_propre = _chaine_ou_aucune(region, "region")
    source_propre = _chaine_ou_aucune(source, "source")
    langue, creee = get_or_create(
        session,
        Langue,
        defaults={
            "libelle": libelle_propre,
            "region": region_propre,
            "source": source_propre or "déclaration chercheur",
            "actif": True,
        },
        code=code_norm,
    )
    if not creee and not langue.actif:
        langue.actif = True
        session.flush()
    # Le registre en mémoire reste synchronisé (compatibilité des
    # vérifications sans session).
    enregistrer_langue(code_norm, libelle_propre)
    return langue, creee


def lister_langues(
    session: Session, actives_seulement: bool = True
) -> list[Langue]:
    """Langues du registre (ordre alphabétique du libellé)."""
    requete = session.query(Langue)
    if actives_seulement:
        requete = requete.filter(Langue.actif.is_(True))
    return requete.order_by(Langue.libelle).all()


# ---------------------------------------------------------------------------
# Noms vernaculaires
# ---------------------------------------------------------------------------


def ajouter_nom_vernaculaire(
    session: Session,
    plant_id: int,
    project_id: int,
    nom_original: str,
    langue: str | None = None,
    **fields: Any,
) -> tuple[NomVernaculaire, bool]:
    """Enregistre un nom vernaculaire (idempotent).

    Clé d'idempotence : ``(plant_id, nom_normalise, langue)`` — le même
    nom rapporté deux fois ne crée pas de doublon. La ``langue`` n'est
    JAMAIS devinée : ``None`` reste ``None`` (affiché « non renseignée ») ;
    une langue inconnue (ni en base ni au registre ``LANGUES_BURKINA``)
    lève ``ValueError`` — utilisez ``enregistrer_langue_db`` pour
    l'ajouter. Ne committe jamais.
    """
    if not langue_connue_db(session, langue):
        raise ValueError(
            f"Langue inconnue : {langue!r}. Langues enregistrées : "
            f"{', '.join(sorted(LANGUES_BURKINA))} — utilisez "
            "`enregistrer_langue_db` pour en ajouter une."
        )
    # P1 affichage (audit adversarial 2e passe) : les contrôles de
    # directionnalité Unicode sont retirés AVANT normalisation — un nom
    # composé uniquement de ces caractères invisibles est rejeté comme
    # vide, et l'usurpation visuelle devient impossible.
    nom_assaini = retirer_controles_bidi(nom_original.strip())
    nom_normalise = normaliser_nom_vernaculaire(nom_assaini or "")
    langue_norm = (langue or "").strip().lower() or None
    return get_or_create(
        session,
        NomVernaculaire,
        defaults={
            "project_id": project_id,
            "nom_original": nom_assaini,
            **fields,
        },
        plant_id=plant_id,
        nom_normalise=nom_normalise,
        langue=langue_norm,
    )


def _paginer(requete, limit: int | None, offset: int = 0):
    """Applique la pagination à une requête (chantier performance).

    ``limit=None`` (défaut) conserve le comportement historique (tout) ;
    ``limit``/``offset`` activent la pagination. ``offset`` négatif levé
    en ``ValueError`` (le routeur répond 422 via ``Query(ge=0)``).
    """
    if offset < 0:
        raise ValueError("Le décalage de pagination (offset) ne peut pas être négatif.")
    requete = requete.offset(offset)
    if limit is not None:
        requete = requete.limit(max(1, limit))
    return requete


def lister_noms_vernaculaires(
    session: Session, plant_id: int, project_id: int | None = None,
    limit: int | None = None, offset: int = 0,
) -> list[NomVernaculaire]:
    """Noms vernaculaires actifs d'une plante (hors soft-delete).

    ``limit``/``offset`` : pagination (``None`` = tout, historique).
    """
    requete = session.query(NomVernaculaire).filter(
        NomVernaculaire.plant_id == plant_id,
        NomVernaculaire.supprime_le.is_(None),
    )
    if project_id is not None:
        requete = requete.filter(
            NomVernaculaire.project_id == project_id
        )
    requete = requete.order_by(NomVernaculaire.nom_original)
    return _paginer(requete, limit, offset).all()


# ---------------------------------------------------------------------------
# Usages traditionnels (ethnobotanique)
# ---------------------------------------------------------------------------

BANDEAU_USAGE_TRADITIONNEL = "USAGE TRADITIONNEL RAPPORTÉ"


def ajouter_usage_traditionnel(
    session: Session,
    plant_id: int,
    project_id: int,
    usage: str,
    **fields: Any,
) -> UsageTraditionnel:
    """Enregistre un usage traditionnel rapporté (toujours créé).

    Journal ethnobotanique : chaque appel crée une nouvelle ligne (deux
    enquêtes ne sont jamais « la même »). ``data_level`` = ``experimental``
    par défaut (enquête de terrain). Le texte ``usage`` est conservé
    verbatim — jamais reformulé en allégation. Ne committe jamais.
    """
    if not (usage or "").strip():
        raise ValueError(
            "La description de l'usage traditionnel ne peut pas être vide."
        )
    fields.setdefault("data_level", DataLevel.EXPERIMENTAL.value)
    objet = UsageTraditionnel(
        plant_id=plant_id,
        project_id=project_id,
        usage=usage.strip(),
        **fields,
    )
    session.add(objet)
    session.flush()
    return objet


def lister_usages_traditionnels(
    session: Session, plant_id: int, project_id: int | None = None,
    limit: int | None = None, offset: int = 0,
) -> list[UsageTraditionnel]:
    """Usages traditionnels actifs d'une plante (hors soft-delete).

    ``limit``/``offset`` : pagination (``None`` = tout, historique).
    """
    requete = session.query(UsageTraditionnel).filter(
        UsageTraditionnel.plant_id == plant_id,
        UsageTraditionnel.supprime_le.is_(None),
    )
    if project_id is not None:
        requete = requete.filter(
            UsageTraditionnel.project_id == project_id
        )
    requete = requete.order_by(UsageTraditionnel.id.desc())
    return _paginer(requete, limit, offset).all()


# ---------------------------------------------------------------------------
# Échantillons, vouchers, chaîne extraction → extrait → fraction → composé
# ---------------------------------------------------------------------------


def creer_echantillon(
    session: Session,
    project_id: int,
    plant_id: int,
    code_echantillon: str,
    **fields: Any,
) -> tuple[Echantillon, bool]:
    """Crée ou récupère un échantillon (idempotent).

    Clé d'idempotence : ``(project_id, code_echantillon)``. Le GPS n'est
    jamais inventé : ``latitude`` / ``longitude`` restent ``None`` si non
    fournis. Des coordonnées hors plage WGS84 lèvent ``ValueError``
    (``valider_gps``) — jamais enregistrées silencieusement.
    Ne committe jamais.
    """
    code = (code_echantillon or "").strip()
    if not code:
        raise ValueError(
            "Le code d'échantillon ne peut pas être vide."
        )
    valider_gps(fields.get("latitude"), fields.get("longitude"))
    voucher_id = fields.get("voucher_id")
    if voucher_id is not None and session.get(Voucher, voucher_id) is None:
        raise KeyError(f"Voucher introuvable : id={voucher_id}")
    fields.setdefault("data_level", DataLevel.EXPERIMENTAL.value)
    return get_or_create(
        session,
        Echantillon,
        defaults={"plant_id": plant_id, **fields},
        project_id=project_id,
        code_echantillon=code,
    )


def lister_echantillons(
    session: Session, plant_id: int, project_id: int | None = None,
    limit: int | None = None, offset: int = 0,
) -> list[Echantillon]:
    """Échantillons actifs d'une plante (hors soft-delete).

    ``limit``/``offset`` : pagination (``None`` = tout, historique).
    """
    requete = session.query(Echantillon).filter(
        Echantillon.plant_id == plant_id,
        Echantillon.supprime_le.is_(None),
    )
    if project_id is not None:
        requete = requete.filter(Echantillon.project_id == project_id)
    requete = requete.order_by(Echantillon.id.desc())
    return _paginer(requete, limit, offset).all()


def creer_voucher(
    session: Session,
    project_id: int,
    code_voucher: str,
    **fields: Any,
) -> tuple[Voucher, bool]:
    """Crée ou récupère un voucher d'herbier (idempotent sur le code).

    Ne committe jamais.
    """
    code = (code_voucher or "").strip()
    if not code:
        raise ValueError("Le code voucher ne peut pas être vide.")
    return get_or_create(
        session,
        Voucher,
        defaults={"project_id": project_id, **fields},
        code_voucher=code,
    )


def lister_vouchers(
    session: Session, project_id: int | None = None,
    limit: int | None = None, offset: int = 0,
) -> list[Voucher]:
    """Vouchers actifs (hors soft-delete).

    ``limit``/``offset`` : pagination (``None`` = tout, historique).
    """
    requete = session.query(Voucher).filter(
        Voucher.supprime_le.is_(None)
    )
    if project_id is not None:
        requete = requete.filter(Voucher.project_id == project_id)
    requete = requete.order_by(Voucher.code_voucher)
    return _paginer(requete, limit, offset).all()


def lier_echantillon_voucher(
    session: Session, echantillon_id: int, voucher_id: int
) -> Echantillon:
    """Rattache un échantillon à un voucher d'herbier.

    Lève ``KeyError`` si l'échantillon ou le voucher est absent.
    Ne committe jamais.
    """
    echantillon = session.get(Echantillon, echantillon_id)
    if echantillon is None:
        raise KeyError(f"Échantillon introuvable : id={echantillon_id}")
    voucher = session.get(Voucher, voucher_id)
    if voucher is None:
        raise KeyError(f"Voucher introuvable : id={voucher_id}")
    echantillon.voucher_id = voucher_id
    session.flush()
    return echantillon


def creer_extraction(
    session: Session,
    project_id: int,
    echantillon_id: int,
    **fields: Any,
) -> Extraction:
    """Enregistre une extraction sur un échantillon (toujours créée).

    Lève ``KeyError`` si l'échantillon est absent. Ne committe jamais.
    """
    if session.get(Echantillon, echantillon_id) is None:
        raise KeyError(f"Échantillon introuvable : id={echantillon_id}")
    fields.setdefault("data_level", DataLevel.EXPERIMENTAL.value)
    objet = Extraction(
        project_id=project_id, echantillon_id=echantillon_id, **fields
    )
    session.add(objet)
    session.flush()
    return objet


def creer_extrait(
    session: Session,
    project_id: int,
    extraction_id: int,
    **fields: Any,
) -> Extrait:
    """Enregistre un extrait issu d'une extraction (toujours créé).

    Lève ``KeyError`` si l'extraction est absente. Ne committe jamais.
    """
    if session.get(Extraction, extraction_id) is None:
        raise KeyError(f"Extraction introuvable : id={extraction_id}")
    fields.setdefault("data_level", DataLevel.EXPERIMENTAL.value)
    objet = Extrait(
        project_id=project_id, extraction_id=extraction_id, **fields
    )
    session.add(objet)
    session.flush()
    return objet


def creer_fraction(
    session: Session,
    project_id: int,
    extrait_id: int,
    **fields: Any,
) -> Fraction:
    """Enregistre une fraction issue d'un extrait (toujours créée).

    Lève ``KeyError`` si l'extrait est absent. Ne committe jamais.
    """
    if session.get(Extrait, extrait_id) is None:
        raise KeyError(f"Extrait introuvable : id={extrait_id}")
    fields.setdefault("data_level", DataLevel.EXPERIMENTAL.value)
    objet = Fraction(
        project_id=project_id, extrait_id=extrait_id, **fields
    )
    session.add(objet)
    session.flush()
    return objet


def lier_fraction_compose(
    session: Session,
    project_id: int,
    fraction_id: int,
    compound_id: int,
    identification_statut: str = StatutIdentification.INCONNU.value,
    **fields: Any,
) -> tuple[FractionCompound, bool]:
    """Lie une fraction à un composé (idempotent).

    Clé d'idempotence : ``(fraction_id, compound_id)``. Le statut
    d'identification vaut ``inconnu`` par défaut — jamais d'identification
    inventée. Lève ``KeyError`` si la fraction ou le composé est absent.
    Ne committe jamais.
    """
    if session.get(Fraction, fraction_id) is None:
        raise KeyError(f"Fraction introuvable : id={fraction_id}")
    if session.get(Compound, compound_id) is None:
        raise KeyError(f"Composé introuvable : id={compound_id}")
    valeurs = {s.value for s in StatutIdentification}
    if identification_statut not in valeurs:
        raise ValueError(
            f"Statut d'identification invalide : {identification_statut!r} "
            f"(attendu : {', '.join(sorted(valeurs))})."
        )
    return get_or_create(
        session,
        FractionCompound,
        defaults={
            "project_id": project_id,
            "identification_statut": identification_statut,
            **fields,
        },
        fraction_id=fraction_id,
        compound_id=compound_id,
    )


def chaine_echantillon(
    session: Session, echantillon_id: int
) -> dict[str, Any]:
    """Reconstitue la chaîne complète d'un échantillon.

    Retourne ``{"echantillon", "voucher", "extractions": [...]}`` où chaque
    extraction porte ses extraits, chaque extrait ses fractions, chaque
    fraction ses composés liés (avec statut d'identification). Les lignes
    supprimées (soft-delete) sont exclues. Lève ``KeyError`` si
    l'échantillon est absent.

    Performance (chantier PERSISTANCE + PERFORMANCE) : requêtes GROUPÉES
    (``IN`` + regroupement Python) au lieu de requêtes imbriquées —
    6 requêtes constantes quelle que soit la taille de la chaîne
    (contre 2 + E + E·X + E·X·F + E·X·F·L avant).
    """
    echantillon = session.get(Echantillon, echantillon_id)
    if echantillon is None:
        raise KeyError(f"Échantillon introuvable : id={echantillon_id}")

    voucher = (
        session.get(Voucher, echantillon.voucher_id)
        if echantillon.voucher_id
        else None
    )
    extractions = (
        session.query(Extraction)
        .filter(
            Extraction.echantillon_id == echantillon.id,
            Extraction.supprime_le.is_(None),
        )
        .order_by(Extraction.id)
        .all()
    )
    ids_extractions = [e.id for e in extractions]
    extraits = (
        session.query(Extrait)
        .filter(
            Extrait.extraction_id.in_(ids_extractions),
            Extrait.supprime_le.is_(None),
        )
        .order_by(Extrait.id)
        .all()
        if ids_extractions
        else []
    )
    par_extraction: dict[int, list] = {}
    for extrait in extraits:
        par_extraction.setdefault(extrait.extraction_id, []).append(extrait)
    ids_extraits = [x.id for x in extraits]
    fractions = (
        session.query(Fraction)
        .filter(
            Fraction.extrait_id.in_(ids_extraits),
            Fraction.supprime_le.is_(None),
        )
        .order_by(Fraction.id)
        .all()
        if ids_extraits
        else []
    )
    par_extrait: dict[int, list] = {}
    for fraction in fractions:
        par_extrait.setdefault(fraction.extrait_id, []).append(fraction)
    ids_fractions = [f.id for f in fractions]
    liens = (
        session.query(FractionCompound)
        .filter(
            FractionCompound.fraction_id.in_(ids_fractions),
            FractionCompound.supprime_le.is_(None),
        )
        .order_by(FractionCompound.id)
        .all()
        if ids_fractions
        else []
    )
    par_fraction: dict[int, list] = {}
    for lien in liens:
        par_fraction.setdefault(lien.fraction_id, []).append(lien)
    ids_composes = {lien.compound_id for lien in liens if lien.compound_id}
    composes = (
        {
            c.id: c
            for c in session.query(Compound)
            .filter(Compound.id.in_(ids_composes))
            .all()
        }
        if ids_composes
        else {}
    )

    resultat = []
    for extraction in extractions:
        noeuds_extraits = []
        for extrait in par_extraction.get(extraction.id, []):
            noeuds_fractions = []
            for fraction in par_extrait.get(extrait.id, []):
                composes_fraction = []
                for lien in par_fraction.get(fraction.id, []):
                    compose = composes.get(lien.compound_id)
                    composes_fraction.append(
                        {
                            "lien_id": lien.id,
                            "compound_id": lien.compound_id,
                            "nom_compose": (
                                compose.name if compose is not None else None
                            ),
                            "identification_statut": (
                                lien.identification_statut
                            ),
                            "commentaire": lien.commentaire,
                        }
                    )
                noeuds_fractions.append(
                    {"fraction": fraction, "composes": composes_fraction}
                )
            noeuds_extraits.append(
                {"extrait": extrait, "fractions": noeuds_fractions}
            )
        resultat.append(
            {"extraction": extraction, "extraits": noeuds_extraits}
        )
    return {
        "echantillon": echantillon,
        "voucher": voucher,
        "extractions": resultat,
    }


# ---------------------------------------------------------------------------
# Analyses phytochimiques
# ---------------------------------------------------------------------------


def creer_analyse_chimique(
    session: Session,
    project_id: int,
    methode: str,
    echantillon_id: int | None = None,
    extrait_id: int | None = None,
    fraction_id: int | None = None,
    **fields: Any,
) -> AnalyseChimique:
    """Enregistre une analyse phytochimique (toujours créée).

    L'analyse doit être rattachée à AU MOINS un maillon de la chaîne
    (échantillon, extrait ou fraction) — ``ValueError`` sinon. La méthode
    doit appartenir au référentiel ``TechniqueAnalyse``. ``data_level`` =
    ``experimental`` par défaut. Ne committe jamais.
    """
    if not any(
        [echantillon_id is not None, extrait_id is not None,
         fraction_id is not None]
    ):
        raise ValueError(
            "Une analyse chimique doit être rattachée à au moins un "
            "échantillon, un extrait ou une fraction."
        )
    techniques = {t.value for t in TechniqueAnalyse}
    if methode not in techniques:
        raise ValueError(
            f"Technique d'analyse invalide : {methode!r} "
            f"(attendu : {', '.join(sorted(techniques))})."
        )
    for nom, modele, identifiant in (
        ("echantillon", Echantillon, echantillon_id),
        ("extrait", Extrait, extrait_id),
        ("fraction", Fraction, fraction_id),
    ):
        if identifiant is not None and session.get(modele, identifiant) is None:
            raise KeyError(
                f"{nom.capitalize()} introuvable : id={identifiant}"
            )
    fields.setdefault("data_level", DataLevel.EXPERIMENTAL.value)
    objet = AnalyseChimique(
        project_id=project_id,
        methode=methode,
        echantillon_id=echantillon_id,
        extrait_id=extrait_id,
        fraction_id=fraction_id,
        **fields,
    )
    session.add(objet)
    session.flush()
    return objet


def lister_analyses_chimiques(
    session: Session,
    project_id: int | None = None,
    echantillon_id: int | None = None,
    extrait_id: int | None = None,
    fraction_id: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[AnalyseChimique]:
    """Analyses chimiques actives (hors soft-delete), filtrables par maillon.

    ``limit``/``offset`` : pagination (``None`` = tout, historique).
    """
    requete = session.query(AnalyseChimique).filter(
        AnalyseChimique.supprime_le.is_(None)
    )
    if project_id is not None:
        requete = requete.filter(
            AnalyseChimique.project_id == project_id
        )
    if echantillon_id is not None:
        requete = requete.filter(
            AnalyseChimique.echantillon_id == echantillon_id
        )
    if extrait_id is not None:
        requete = requete.filter(
            AnalyseChimique.extrait_id == extrait_id
        )
    if fraction_id is not None:
        requete = requete.filter(
            AnalyseChimique.fraction_id == fraction_id
        )
    requete = requete.order_by(AnalyseChimique.id.desc())
    return _paginer(requete, limit, offset).all()


def ajouter_pic(
    session: Session,
    analyse_id: int,
    identification_statut: str = StatutIdentification.INCONNU.value,
    **fields: Any,
) -> PicChromatographique:
    """Ajoute un pic à une analyse chimique (toujours créé).

    Le statut d'identification vaut ``inconnu`` par défaut — jamais
    d'identification inventée. Lève ``KeyError`` si l'analyse est absente.
    Ne committe jamais.
    """
    if session.get(AnalyseChimique, analyse_id) is None:
        raise KeyError(f"Analyse chimique introuvable : id={analyse_id}")
    valeurs = {s.value for s in StatutIdentification}
    if identification_statut not in valeurs:
        raise ValueError(
            f"Statut d'identification invalide : {identification_statut!r} "
            f"(attendu : {', '.join(sorted(valeurs))})."
        )
    pic = PicChromatographique(
        analyse_id=analyse_id,
        identification_statut=identification_statut,
        **fields,
    )
    session.add(pic)
    session.flush()
    return pic


def lister_pics(
    session: Session, analyse_id: int,
    limit: int | None = None, offset: int = 0,
) -> list[PicChromatographique]:
    """Pics d'une analyse chimique (ordre de temps de rétention).

    ``limit``/``offset`` : pagination (``None`` = tout, historique).
    """
    requete = (
        session.query(PicChromatographique)
        .filter(PicChromatographique.analyse_id == analyse_id)
        .order_by(
            PicChromatographique.temps_retention.is_(None),
            PicChromatographique.temps_retention,
        )
    )
    return _paginer(requete, limit, offset).all()


# ---------------------------------------------------------------------------
# Intégrité des fichiers bruts (jamais de fichier corrompu silencieux)
# ---------------------------------------------------------------------------


def _sha256_fichier(chemin: str) -> str:
    """SHA-256 d'un fichier (lecture par blocs — gros fichiers OK)."""
    condensation = hashlib.sha256()
    with open(chemin, "rb") as fh:
        for bloc in iter(lambda: fh.read(1024 * 1024), b""):
            condensation.update(bloc)
    return condensation.hexdigest()


def _sha256_fichier_sans_suivi(chemin: str) -> str:
    """SHA-256 sans suivre le dernier composant s'il est un lien.

    Sur POSIX, ``O_NOFOLLOW`` fait échouer l'ouverture (``OSError``) si
    le chemin final est un lien symbolique — cela referme la course
    entre la validation ``realpath`` et la lecture : un symlink
    substitué dans l'intervalle est refusé au lieu d'être suivi.
    Sur les plateformes sans ``O_NOFOLLOW`` (Windows), repli sur la
    lecture classique (le contrôle ``realpath`` préalable reste actif).
    """
    drapeau_sans_suivi = getattr(os, "O_NOFOLLOW", 0)
    if drapeau_sans_suivi:
        descripteur = os.open(chemin, os.O_RDONLY | drapeau_sans_suivi)
        ferme_manuellement = True
        try:
            if not stat.S_ISREG(os.fstat(descripteur).st_mode):
                raise OSError(
                    f"Refus de lecture : « {chemin} » n'est pas un "
                    "fichier régulier."
                )
            condensation = hashlib.sha256()
            with os.fdopen(descripteur, "rb", closefd=True) as fh:
                ferme_manuellement = False  # le bloc with ferme
                for bloc in iter(lambda: fh.read(1024 * 1024), b""):
                    condensation.update(bloc)
            return condensation.hexdigest()
        finally:
            if ferme_manuellement:
                os.close(descripteur)
    return _sha256_fichier(chemin)


def _racine_fichiers_bruts() -> str:
    """Racine autorisée pour les fichiers bruts (créée si absente)."""
    racine = os.path.realpath(get_settings().FICHIERS_BRUTS_DIR)
    os.makedirs(racine, exist_ok=True)
    return racine


def _chemin_brut_autorise(chemin: str) -> str:
    """Valide qu'un chemin de fichier brut reste dans la racine autorisée.

    Résolution canonique (``realpath`` : ``..`` et liens symboliques
    résolus) puis vérification d'appartenance stricte. Lève
    ``PermissionError`` si le chemin sort de la racine,
    ``FileNotFoundError`` s'il n'existe pas, ``ValueError`` s'il dépasse
    la taille maximale. Les messages ne répercutent jamais le chemin
    fourni (pas d'oracle).
    """
    if not chemin or not isinstance(chemin, str):
        raise FileNotFoundError("Fichier brut introuvable.")
    racine = _racine_fichiers_bruts()
    canonique = os.path.realpath(chemin)
    if canonique != racine and not canonique.startswith(racine + os.sep):
        raise PermissionError(
            "Chemin refusé : les fichiers bruts doivent être déposés "
            "dans le répertoire autorisé (paramètre "
            "WEBINTEL_FICHIERS_BRUTS_DIR)."
        )
    if not os.path.isfile(canonique):
        raise FileNotFoundError("Fichier brut introuvable.")
    taille_max = get_settings().FICHIER_BRUT_MAX_OCTETS
    if os.path.getsize(canonique) > taille_max:
        raise ValueError(
            f"Fichier brut trop volumineux (limite : {taille_max} octets)."
        )
    return canonique


def _chemin_brut_a_verifier(chemin: str | None) -> str | None:
    """Résout le chemin stocké d'un fichier brut AVANT lecture.

    Audit adversarial (2e passe) : ``verifier_integrite_fichier``
    lisait le chemin stocké tel quel — un lien symbolique substitué
    APRÈS le rattachement (dans ``FICHIERS_BRUTS_DIR``) faisait hasher
    un fichier arbitraire hors racine (oracle de SHA-256). Ici, le
    chemin est re-résolu (``realpath``) et le confinement re-vérifié :
    retourne le chemin canonique, ``None`` si le fichier est absent,
    lève ``PermissionError`` s'il sort de la racine autorisée.
    """
    if not chemin or not isinstance(chemin, str):
        return None
    racine = _racine_fichiers_bruts()
    canonique = os.path.realpath(chemin)
    if canonique != racine and not canonique.startswith(racine + os.sep):
        raise PermissionError(
            "Chemin refusé : le fichier rattaché pointe hors du "
            "répertoire autorisé (paramètre WEBINTEL_FICHIERS_BRUTS_DIR)."
        )
    if not os.path.isfile(canonique):
        return None
    return canonique


def enregistrer_fichier_brut(
    session: Session, analyse_id: int, chemin: str
) -> AnalyseChimique:
    """Rattache un fichier brut à une analyse et enregistre son SHA-256.

    Le fichier n'est jamais copié en base : seuls son chemin canonique
    et son empreinte SHA-256 sont conservés (traçabilité).

    P0 sécurité : le chemin est CONFINÉ au répertoire
    ``FICHIERS_BRUTS_DIR`` (résolution canonique, liens symboliques et
    ``..`` neutralisés) — l'endpoint n'est plus un oracle de fichiers
    locaux. Lève ``KeyError`` si l'analyse est absente,
    ``PermissionError`` si le chemin sort de la racine,
    ``FileNotFoundError`` si le fichier n'existe pas, ``ValueError``
    s'il dépasse la taille maximale. Ne committe jamais.
    """
    analyse = session.get(AnalyseChimique, analyse_id)
    if analyse is None:
        raise KeyError(f"Analyse chimique introuvable : id={analyse_id}")
    canonique = _chemin_brut_autorise(chemin)
    analyse.fichier_brut_path = canonique
    analyse.fichier_brut_sha256 = _sha256_fichier(canonique)
    session.flush()
    return analyse


def verifier_integrite_fichier(
    session: Session, analyse_id: int
) -> dict[str, Any]:
    """Vérifie l'intégrité du fichier brut d'une analyse.

    Retourne ``{"analyse_id", "chemin", "present", "hash_enregistre",
    "hash_calcule", "correspond", "raison"}`` :

    - fichier absent du disque → ``present=False`` (pas une erreur en
      soi : le chemin peut pointer vers un autre poste) ;
    - aucun hash enregistré → ``correspond=None`` (invérifiable) ;
    - hash différent → ``correspond=False`` : le fichier a été modifié
      ou corrompu après enregistrement — SIGNALÉ, jamais masqué.

    Lecture seule (aucune écriture). Lève ``KeyError`` si l'analyse est
    absente.
    """
    analyse = session.get(AnalyseChimique, analyse_id)
    if analyse is None:
        raise KeyError(f"Analyse chimique introuvable : id={analyse_id}")
    chemin = analyse.fichier_brut_path
    hash_enregistre = analyse.fichier_brut_sha256
    resultat: dict[str, Any] = {
        "analyse_id": analyse_id,
        "chemin": chemin,
        "present": False,
        "hash_enregistre": hash_enregistre,
        "hash_calcule": None,
        "correspond": None,
        "raison": "",
    }
    if not chemin:
        resultat["raison"] = "Aucun fichier brut rattaché à cette analyse."
        return resultat
    try:
        canonique = _chemin_brut_a_verifier(chemin)
    except PermissionError:
        # P0 sécurité (audit adversarial 2e passe) : le fichier rattaché
        # a été remplacé par un lien (ou un chemin) sortant de la racine
        # autorisée — on ne le lit JAMAIS (pas d'oracle de contenu).
        resultat["correspond"] = False
        resultat["raison"] = (
            "ALERTE INTÉGRITÉ : le fichier rattaché pointe désormais hors "
            "du répertoire autorisé (lien symbolique ou chemin modifié "
            "après rattachement). Fichier non lu — rattachez un fichier "
            "du répertoire autorisé."
        )
        return resultat
    if canonique is None:
        resultat["raison"] = (
            f"Fichier introuvable sur ce poste : {chemin!r} "
            "(vérifiez le chemin ou le support de stockage)."
        )
        return resultat
    resultat["present"] = True
    try:
        # P0 sécurité (audit adversarial 2e passe) : ouvrir sans suivre
        # les liens (O_NOFOLLOW, POSIX) referme la course validation →
        # ouverture : un symlink substitué entre le contrôle realpath et
        # la lecture est refusé au lieu d'être suivi. Limite honnête : le
        # remplacement d'un répertoire parent par un lien reste une
        # opération privilégiée du système de fichiers, hors modèle de
        # menace de l'application (accès local requis dans les deux cas).
        hash_calcule = _sha256_fichier_sans_suivi(canonique)
    except OSError as exc:
        resultat["raison"] = f"Fichier illisible : {exc}."
        return resultat
    resultat["hash_calcule"] = hash_calcule
    if not hash_enregistre:
        resultat["raison"] = (
            "Aucune empreinte SHA-256 enregistrée : l'intégrité ne peut "
            "pas être vérifiée (utilisez `enregistrer_fichier_brut`)."
        )
        return resultat
    if hash_calcule == hash_enregistre:
        resultat["correspond"] = True
        resultat["raison"] = "Empreinte SHA-256 conforme : fichier intact."
    else:
        resultat["correspond"] = False
        resultat["raison"] = (
            "ALERTE INTÉGRITÉ : l'empreinte SHA-256 du fichier ne "
            "correspond plus à celle enregistrée — le fichier a été "
            "modifié ou corrompu après son rattachement. Ne pas utiliser "
            "ces données sans vérification."
        )
    return resultat


# ---------------------------------------------------------------------------
# Pharmacognosie
# ---------------------------------------------------------------------------

_CHAMPS_PHARMACOGNOSIE = (
    "identification_macro",
    "identification_micro",
    "organoleptique",
    "partie_utilisee",
    "authentification",
    "substitution_adulteration",
    "sechage",
    "stockage",
    "preparation",
)


def definir_pharmacognosie(
    session: Session,
    plant_id: int,
    project_id: int,
    **champs: Any,
) -> tuple[Pharmacognosie, bool]:
    """Crée ou met à jour la fiche pharmacognosie d'une plante.

    Une seule ligne par plante (``get_or_create`` sur ``plant_id``).
    Seuls les champs de ``_CHAMPS_PHARMACOGNOSIE`` (+ ``source``,
    ``cree_par``, ``evidence_id``, ``niveau_confidentialite``) sont
    acceptés — tout autre nom lève ``ValueError``. Les sections non
    renseignées restent ``None`` (affichées « non renseignée »).
    Lève ``KeyError`` si la plante est absente. Lève ``PermissionError``
    si une fiche existe déjà pour un AUTRE projet : un projet ne peut
    pas écraser silencieusement la fiche d'un autre projet (intégrité +
    confidentialité — test adversarial ``test_pharmacognosie``).
    Ne committe jamais.
    """
    if session.get(Plant, plant_id) is None:
        raise KeyError(f"Plante introuvable : id={plant_id}")
    autorises = set(_CHAMPS_PHARMACOGNOSIE) | {
        "source", "cree_par", "evidence_id", "niveau_confidentialite",
        "data_level",
    }
    for nom in champs:
        if nom not in autorises:
            raise ValueError(
                f"Champ pharmacognosie inconnu : {nom!r}."
            )
    fiche, cree = get_or_create(
        session,
        Pharmacognosie,
        defaults={"project_id": project_id},
        plant_id=plant_id,
    )
    if not cree and fiche.project_id != project_id:
        # P0 sécurité : la fiche appartient à un autre projet. Ne pas
        # l'écraser (et ne pas révéler son contenu) — refus explicite.
        raise PermissionError(
            "Cette plante possède déjà une fiche pharmacognosie créée "
            "par un autre projet : seul le projet propriétaire peut la "
            "modifier."
        )
    for nom, valeur in champs.items():
        setattr(fiche, nom, valeur)
    session.flush()
    return fiche, cree


def lire_pharmacognosie(
    session: Session, plant_id: int, project_id: int | None = None
) -> Pharmacognosie | None:
    """Fiche pharmacognosie active d'une plante (``None`` si absente).

    P0 sécurité : une fiche non ``public`` n'est lisible que par son
    projet propriétaire — un autre projet reçoit ``None`` (→ 404
    honnête côté routeur), conformément au niveau de confidentialité
    du modèle (défaut ``projet``). ``project_id=None`` conserve
    l'ancien comportement (export public, qui filtre lui-même sur
    ``public``).
    """
    fiche = (
        session.query(Pharmacognosie)
        .filter(
            Pharmacognosie.plant_id == plant_id,
            Pharmacognosie.supprime_le.is_(None),
        )
        .one_or_none()
    )
    if (
        fiche is not None
        and project_id is not None
        and fiche.project_id != project_id
        and fiche.niveau_confidentialite
        != NiveauConfidentialite.PUBLIC.value
    ):
        return None
    return fiche


# ---------------------------------------------------------------------------
# Statut épistémique (colonnes v2.2 — DataLevel reste figé)
# ---------------------------------------------------------------------------


def definir_statut_epistemique(
    session: Session,
    objet: Any,
    statut: str,
    methode_statut: str | None = None,
) -> Any:
    """Définit le statut épistémique d'une cible ou d'une activité.

    ``statut`` : "documente" | "infere" | "predit" | "calcule".
    Lève ``ValueError`` pour un statut inconnu, ``TypeError`` si l'objet
    ne porte pas la colonne. Ne committe jamais.
    """
    if not hasattr(objet, "statut_epistemique"):
        raise TypeError(
            "Cet objet ne porte pas de colonne `statut_epistemique`."
        )
    valeurs = {s.value for s in StatutEpistemique}
    if statut not in valeurs:
        raise ValueError(
            f"Statut épistémique invalide : {statut!r} "
            f"(attendu : {', '.join(sorted(valeurs))})."
        )
    objet.statut_epistemique = statut
    if methode_statut is not None:
        objet.methode_statut = methode_statut
    session.flush()
    return objet


# ---------------------------------------------------------------------------
# Soft-delete (jamais de suppression silencieuse)
# ---------------------------------------------------------------------------


def supprimer_doucement(
    session: Session,
    objet: Any,
    supprime_par: str | None = None,
) -> Any:
    """Suppression logique : renseigne ``supprime_le`` / ``supprime_par``.

    La ligne reste en base (audit, traçabilité) mais disparaît des listes.
    Lève ``TypeError`` si l'objet ne supporte pas le soft-delete.
    Ne committe jamais.
    """
    if not hasattr(objet, "supprime_le"):
        raise TypeError(
            "Cet objet ne supporte pas la suppression logique."
        )
    objet.supprime_le = utcnow()
    if supprime_par is not None and hasattr(objet, "supprime_par"):
        objet.supprime_par = supprime_par
    session.flush()
    return objet


# ---------------------------------------------------------------------------
# Recherche multi-motifs (EXACT / NORMALISÉ / SYNONYME / VERNACULAIRE / PARTIEL)
# ---------------------------------------------------------------------------

MOTIFS_RECHERCHE = (
    "exact",
    "normalise",
    "synonyme",
    "vernaculaire",
    "partiel",
)


def rechercher_plantes(
    session: Session, q: str, project_id: int | None = None
) -> list[dict[str, Any]]:
    """Recherche de plantes avec motif de correspondance affiché.

    Ordre de priorité (une plante n'apparaît qu'une fois, avec son
    meilleur motif) :

    - ``exact`` : nom scientifique égal à la requête (insensible à la casse) ;
    - ``normalise`` : nom scientifique égal après normalisation
      (minuscules, sans accents, espaces réduits) ;
    - ``synonyme`` : la requête correspond à un synonyme enregistré ;
    - ``vernaculaire`` : la requête correspond à un nom vernaculaire
      (table ``noms_vernaculaires`` active, ou dictionnaire historique
      ``Plant.vernacular_names``) — c'est ce motif qui fait trouver
      « neem » ou « karité » ;
    - ``partiel`` : la requête est une sous-chaîne du nom scientifique,
      d'un synonyme ou d'un nom vernaculaire.

    Retourne ``[{"plant_id", "scientific_name", "motif", "detail"}]`` où
    ``detail`` précise la forme appariée (ex. le nom vernaculaire trouvé
    et sa langue, jamais devinée : « non renseignée » si absente).
    """
    requete = (q or "").strip()
    if not requete:
        return []
    norme = normaliser_texte(requete)

    plantes = session.query(Plant).order_by(Plant.scientific_name).all()
    noms_vern: dict[int, list[NomVernaculaire]] = {}
    tous_noms = (
        session.query(NomVernaculaire)
        .filter(NomVernaculaire.supprime_le.is_(None))
        .all()
    )
    for nv in tous_noms:
        if project_id is not None and nv.project_id != project_id:
            continue
        noms_vern.setdefault(nv.plant_id, []).append(nv)

    trouves: dict[int, dict[str, Any]] = {}

    def _poser(plante: Plant, motif: str, detail: str) -> None:
        if plante.id not in trouves:
            trouves[plante.id] = {
                "plant_id": plante.id,
                "scientific_name": plante.scientific_name,
                "motif": motif,
                "detail": detail,
            }

    def _valeurs_vernaculaires(
        plante: Plant,
    ) -> list[tuple[str, str | None, str]]:
        """(nom_normalise, langue, nom_original) depuis la table + le dict historique."""
        valeurs: list[tuple[str, str | None, str]] = []
        for nv in noms_vern.get(plante.id, []):
            valeurs.append(
                (nv.nom_normalise, nv.langue, nv.nom_original)
            )
            for variante in nv.orthographes_variantes or []:
                valeurs.append(
                    (normaliser_texte(variante), nv.langue, variante)
                )
        historique = plante.vernacular_names or {}
        if isinstance(historique, dict):
            for langue, noms in historique.items():
                if langue == "sources":
                    continue
                if isinstance(noms, str):
                    noms = [noms]
                for nom in noms or []:
                    valeurs.append(
                        (normaliser_texte(nom), str(langue), str(nom))
                    )
        elif isinstance(historique, list):
            for nom in historique:
                valeurs.append((normaliser_texte(nom), None, str(nom)))
        return valeurs

    # 1. EXACT — 2. NORMALISÉ
    for plante in plantes:
        if plante.scientific_name.strip().lower() == requete.lower():
            _poser(plante, "exact", plante.scientific_name)
        elif normaliser_texte(plante.scientific_name) == norme:
            _poser(plante, "normalise", plante.scientific_name)

    # 3. SYNONYME
    for plante in plantes:
        if plante.id in trouves:
            continue
        for synonyme in plante.synonyms or []:
            if normaliser_texte(synonyme) == norme:
                _poser(plante, "synonyme", f"synonyme : {synonyme}")
                break

    # 4. VERNACULAIRE
    for plante in plantes:
        if plante.id in trouves:
            continue
        for nom_norm, langue, original in _valeurs_vernaculaires(plante):
            if nom_norm and nom_norm == norme:
                libelle_langue = (
                    LANGUES_BURKINA.get(langue, langue)
                    if langue
                    else "non renseignée"
                )
                _poser(
                    plante,
                    "vernaculaire",
                    f"nom vernaculaire : « {original} » "
                    f"(langue : {libelle_langue})",
                )
                break

    # 5. PARTIEL
    for plante in plantes:
        if plante.id in trouves:
            continue
        candidats = [plante.scientific_name] + list(plante.synonyms or [])
        candidats += [
            original
            for _, _, original in _valeurs_vernaculaires(plante)
        ]
        for candidat in candidats:
            if norme and norme in normaliser_texte(candidat):
                _poser(
                    plante,
                    "partiel",
                    f"correspondance partielle : « {candidat} »",
                )
                break

    ordre = {motif: i for i, motif in enumerate(MOTIFS_RECHERCHE)}
    resultats = list(trouves.values())
    resultats.sort(
        key=lambda r: (ordre[r["motif"]], r["scientific_name"].lower())
    )
    return resultats


# ---------------------------------------------------------------------------
# Export public (confidentialité : jamais de fuite)
# ---------------------------------------------------------------------------


def sanitiser_usage_public(usage: UsageTraditionnel) -> dict[str, Any]:
    """Vue publique d'un usage traditionnel.

    Exclut toute information identifiante (code informateur) ; le bandeau
    « USAGE TRADITIONNEL RAPPORTÉ » est systématique — jamais une
    allégation d'efficacité.
    """
    return {
        "id": usage.id,
        "bandeau": BANDEAU_USAGE_TRADITIONNEL,
        "usage": usage.usage,
        "partie_utilisee": usage.partie_utilisee,
        "preparation": usage.preparation,
        "forme": usage.forme,
        "voie_administration": usage.voie_administration,
        "contexte": usage.contexte,
        "localite": usage.localite,
        "groupe_population": usage.groupe_population,
        "publication": usage.publication,
        "date_enquete": (
            usage.date_enquete.isoformat() if usage.date_enquete else None
        ),
        "source": usage.source,
        "niveau_preuve": usage.data_level,
    }


def sanitiser_echantillon_public(
    echantillon: Echantillon,
) -> dict[str, Any]:
    """Vue publique d'un échantillon : SANS coordonnées GPS précises.

    Seuls le contexte géographique textuel (pays → localité / site) est
    conservé — ``latitude`` / ``longitude`` / ``precision_gps`` ne sont
    JAMAIS divulgués dans un export public (pas d'autorisation explicite
    gérée ici : l'export public est par construction non autorisé à les
    diffuser).
    """
    return {
        "id": echantillon.id,
        "code_echantillon": echantillon.code_echantillon,
        "pays": echantillon.pays,
        "region": echantillon.region,
        "province": echantillon.province,
        "commune": echantillon.commune,
        "localite": echantillon.localite,
        "site": echantillon.site,
        "altitude_m": echantillon.altitude_m,
        "habitat": echantillon.habitat,
        "partie": echantillon.partie,
        "institution": echantillon.institution,
        "source": echantillon.source,
        "niveau_preuve": echantillon.data_level,
    }


def export_public_plante(
    session: Session, plant_id: int
) -> dict[str, Any]:
    """Export public d'une plante : UNIQUEMENT les données ``public``.

    Règles de confidentialité (testées) :

    - seuls les enregistrements ``niveau_confidentialite == "public"``
      et non supprimés sont inclus — ``projet`` / ``chercheur`` /
      ``restreint`` / ``confidentiel`` sont EXCLUS ;
    - les coordonnées GPS précises ne sont JAMAIS divulguées
      (``sanitiser_echantillon_public``) ;
    - aucune identité (informateur, collecteur) n'est divulguée ;
    - les usages traditionnels portent le bandeau
      « USAGE TRADITIONNEL RAPPORTÉ ».

    Lève ``KeyError`` si la plante est absente.
    """
    plante = session.get(Plant, plant_id)
    if plante is None:
        raise KeyError(f"Plante introuvable : id={plant_id}")

    def _publics(requete, modele):
        return (
            requete.filter(
                modele.niveau_confidentialite
                == NiveauConfidentialite.PUBLIC.value,
                modele.supprime_le.is_(None),
            )
            .order_by(modele.id)
            .all()
        )

    noms = _publics(
        session.query(NomVernaculaire).filter(
            NomVernaculaire.plant_id == plant_id
        ),
        NomVernaculaire,
    )
    usages = _publics(
        session.query(UsageTraditionnel).filter(
            UsageTraditionnel.plant_id == plant_id
        ),
        UsageTraditionnel,
    )
    echantillons = _publics(
        session.query(Echantillon).filter(Echantillon.plant_id == plant_id),
        Echantillon,
    )
    pharmaco = lire_pharmacognosie(session, plant_id)
    pharmaco_public = (
        pharmaco
        if pharmaco is not None
        and pharmaco.niveau_confidentialite
        == NiveauConfidentialite.PUBLIC.value
        else None
    )

    return {
        "plante": {
            "id": plante.id,
            "scientific_name": plante.scientific_name,
            "family": plante.family,
        },
        "avertissement": (
            "Export public — seules les données marquées « public » sont "
            "incluses. Les coordonnées GPS précises et les identités ne "
            "sont jamais divulguées dans un export public."
        ),
        "noms_vernaculaires": [
            {
                "nom": n.nom_original,
                "langue": (
                    LANGUES_BURKINA.get(n.langue, n.langue)
                    if n.langue
                    else "non renseignée"
                ),
                "region": n.region,
                "source": n.source,
            }
            for n in noms
        ],
        "usages_traditionnels": [
            sanitiser_usage_public(u) for u in usages
        ],
        "echantillons": [
            sanitiser_echantillon_public(e) for e in echantillons
        ],
        "pharmacognosie": (
            {
                "identification_macro": pharmaco_public.identification_macro,
                "identification_micro": pharmaco_public.identification_micro,
                "organoleptique": pharmaco_public.organoleptique,
                "partie_utilisee": pharmaco_public.partie_utilisee,
                "authentification": pharmaco_public.authentification,
                "substitution_adulteration": (
                    pharmaco_public.substitution_adulteration
                ),
                "sechage": pharmaco_public.sechage,
                "stockage": pharmaco_public.stockage,
                "preparation": pharmaco_public.preparation,
                "source": pharmaco_public.source,
            }
            if pharmaco_public is not None
            else None
        ),
    }
