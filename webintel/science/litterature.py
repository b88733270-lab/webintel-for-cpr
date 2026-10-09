"""Publications locales — thèses, mémoires, rapports, documents sans DOI/PMID.

Les chercheurs burkinabè produisent thèses, mémoires, rapports locaux
et travaux universitaires souvent sans DOI ni PMID : ces documents
sont documentables ici **au même niveau** que la littérature
internationale, sans jamais inventer un identifiant externe.

Règles dures :
- **jamais de publication / DOI / PMID inventé** ;
- l'identifiant généré ``WBF-AAAA-NNNN`` est un **identifiant interne
  WebIntel** : il ne doit jamais être présenté, cité ou exporté comme
  un DOI, un PMID ou un autre identifiant externe ;
- déduplication : DOI → PMID → titre normalisé (+ année).

``ajouter_publication_locale`` est le point d'entrée unique (service,
import de fichiers, connecteurs).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy.orm import Session

from webintel.core.utils import utcnow
from webintel.science import service as _service
from webintel.science.models import Evidence

__all__ = [
    "PREFIXE_IDENTIFIANT_INTERNE",
    "MARQUE_IDENTIFIANT_INTERNE",
    "TYPES_DOCUMENT",
    "normaliser_titre",
    "generer_identifiant_interne",
    "trouver_evidence_existante",
    "ajouter_publication_locale",
    "reference_citee",
    "est_identifiant_interne",
]

#: Préfixe des identifiants internes (WBF = WebIntel Burkina Faso).
PREFIXE_IDENTIFIANT_INTERNE = "WBF"

#: Mention obligatoire accompagnant tout identifiant interne affiché.
MARQUE_IDENTIFIANT_INTERNE = "identifiant interne WebIntel"

#: Types de documents acceptés pour les publications locales.
TYPES_DOCUMENT = (
    "article",
    "these",
    "memoire",
    "rapport",
    "rapport_local",
    "communication",
    "document_local",
    "travaux_universitaires",
    "base_de_donnees",
    "site_expert",
    "autre",
)

_MOTIF_IDENTIFIANT_INTERNE = re.compile(
    r"^WBF-\d{4}-\d{4}$"
)


def normaliser_titre(titre: str | None) -> str:
    """Normalise un titre pour la déduplication.

    Minuscules, accents supprimés, ponctuation et espaces multiples
    réduits : « Étude  du Moringa ! » et « etude du moringa » donnent
    la même clé. Fonction pure et déterministe.
    """
    if not titre:
        return ""
    texte = unicodedata.normalize("NFKD", titre)
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    texte = texte.lower()
    texte = re.sub(r"[^a-z0-9]+", " ", texte)
    return re.sub(r"\s+", " ", texte).strip()


def est_identifiant_interne(valeur: str | None) -> bool:
    """Dit si ``valeur`` ressemble à un identifiant interne WebIntel."""
    return bool(valeur) and bool(_MOTIF_IDENTIFIANT_INTERNE.match(str(valeur).strip()))


def generer_identifiant_interne(session: Session, annee: int | None = None) -> str:
    """Génère le prochain identifiant interne ``WBF-AAAA-NNNN``.

    Séquence annuelle : le compteur repart à 0001 chaque année civile.
    L'identifiant est **interne** — voir ``MARQUE_IDENTIFIANT_INTERNE``.
    Ne committe jamais.
    """
    if annee is None:
        annee = utcnow().year
    prefixe = f"{PREFIXE_IDENTIFIANT_INTERNE}-{annee}-"
    lignes = (
        session.query(Evidence.identifiant_interne)
        .filter(Evidence.identifiant_interne.like(f"{prefixe}%"))
        .all()
    )
    maximum = 0
    for (valeur,) in lignes:
        try:
            numero = int(str(valeur).rsplit("-", 1)[1])
            maximum = max(maximum, numero)
        except (ValueError, IndexError):
            continue
    return f"{prefixe}{maximum + 1:04d}"


def trouver_evidence_existante(
    session: Session,
    *,
    doi: str | None = None,
    pmid: str | None = None,
    titre: str | None = None,
    annee: int | None = None,
    reference: str | None = None,
) -> Evidence | None:
    """Déduplication d'une preuve : DOI → PMID → titre normalisé.

    1. ``doi`` renseigné → recherche sur ``Evidence.doi`` (unique) ;
    2. sinon ``pmid`` renseigné → recherche sur ``Evidence.pmid`` ;
    3. sinon titre normalisé (+ année si fournie) : deux preuves sans
       DOI/PMID partageant le même titre normalisé et la même année
       sont considérées comme le même document ;
    4. repli historique : (référence exacte, titre exact, année) —
       compatibilité avec l'import existant.

    Retourne la preuve existante ou ``None``. Lecture seule.
    """
    if doi:
        trouvee = (
            session.query(Evidence).filter(Evidence.doi == doi).one_or_none()
        )
        if trouvee is not None:
            return trouvee
    if pmid:
        trouvee = (
            session.query(Evidence)
            .filter(Evidence.pmid == pmid)
            .order_by(Evidence.id)
            .first()
        )
        if trouvee is not None:
            return trouvee
    titre_norm = normaliser_titre(titre)
    if titre_norm:
        requete = session.query(Evidence).filter(Evidence.doi.is_(None))
        if annee is not None:
            requete = requete.filter(Evidence.year == annee)
        for candidate in requete.all():
            if normaliser_titre(candidate.title) == titre_norm:
                return candidate
    if reference or titre:
        requete = session.query(Evidence).filter(Evidence.doi.is_(None))
        if reference:
            requete = requete.filter(Evidence.reference == reference)
        else:
            requete = requete.filter(Evidence.reference.in_(["", None]))
        if titre:
            requete = requete.filter(Evidence.title == titre)
        else:
            requete = requete.filter(Evidence.title.in_(["", None]))
        if annee is not None:
            requete = requete.filter(Evidence.year == annee)
        trouvee = requete.one_or_none()
        if trouvee is not None:
            return trouvee
    return None


def ajouter_publication_locale(
    session: Session,
    *,
    titre: str,
    auteurs: list[str] | None = None,
    annee: int | None = None,
    journal_ou_institution: str | None = None,
    type_document: str = "document_local",
    fichier_local: str | None = None,
    doi: str | None = None,
    pmid: str | None = None,
    url: str | None = None,
    reference: str | None = None,
    extrait: str | None = None,
    langue: str | None = None,
    pays_edition: str | None = None,
    provenance: dict | None = None,
) -> tuple[Evidence, bool]:
    """Enregistre une publication locale (avec ou sans DOI/PMID).

    - Déduplication : DOI → PMID → titre normalisé (+ année).
    - Sans DOI ni PMID : génère un ``identifiant_interne``
      (``WBF-AAAA-NNNN``) marqué « identifiant interne WebIntel ».
    - ``type_document`` validé contre ``TYPES_DOCUMENT`` (repli
      ``"document_local"`` si inconnu — jamais rejeté, jamais inventé).

    Retourne ``(preuve, créée ?)``. Ne committe jamais.
    """
    if not (titre or "").strip():
        raise ValueError("Une publication locale exige au moins un titre.")
    if type_document not in TYPES_DOCUMENT:
        type_document = "document_local"

    existante = trouver_evidence_existante(
        session, doi=doi, pmid=pmid, titre=titre, annee=annee,
        reference=reference,
    )
    if existante is not None:
        return existante, False

    champs: dict[str, Any] = {
        "source_type": type_document,
        "document_type": type_document,
        "reference": reference or "",
        "title": titre.strip(),
        "authors": list(auteurs or []),
        "year": annee,
        "pmid": pmid,
        "url": url,
        "excerpt": extrait,
        "institution": journal_ou_institution,
        "fichier_local": fichier_local,
        "langue": langue,
        "pays_edition": pays_edition,
        "retrieved_at": utcnow(),
        "provenance": {
            **(provenance or {}),
            "canal": "publication_locale",
        },
    }
    if not doi and not pmid:
        champs["identifiant_interne"] = generer_identifiant_interne(
            session, annee or utcnow().year
        )
        champs["provenance"]["identifiant_interne_note"] = (
            f"{champs['identifiant_interne']} : {MARQUE_IDENTIFIANT_INTERNE} "
            "(ne pas citer comme DOI/PMID)."
        )
    preuve, _ = _service.add_evidence(session, doi=doi, **champs)
    return preuve, True


def reference_citee(preuve: Evidence) -> str:
    """Forme citable d'une preuve, honnête sur la nature de l'identifiant.

    - Avec DOI → citation DOI classique ;
    - avec PMID seul → citation PMID ;
    - sinon → identifiant interne **explicitement marqué** comme
      « identifiant interne WebIntel » (jamais présenté comme un
      identifiant externe).
    """
    auteurs = ", ".join(preuve.authors or [])
    base = f"{auteurs} ({preuve.year})" if auteurs else f"({preuve.year})"
    base = f"{base} {preuve.title}".strip()
    if preuve.doi:
        return f"{base}. DOI: {preuve.doi}"
    if preuve.pmid:
        return f"{base}. PMID: {preuve.pmid}"
    identifiant = preuve.identifiant_interne or "sans identifiant"
    return (
        f"{base}. [{identifiant} — {MARQUE_IDENTIFIANT_INTERNE} : "
        "ne pas citer comme DOI/PMID]"
    )
