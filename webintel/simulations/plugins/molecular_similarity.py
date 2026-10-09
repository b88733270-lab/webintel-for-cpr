"""Moteur de similarité moléculaire — heuristique honnête + structure réelle (v2.1).

Deux régimes, explicitement distingués dans chaque sortie :

- **SANS RDKit** (mode CORE) : heuristique 100 % déterministe en Python pur —
  (a) similarité de FORMULE : Tanimoto sur les vecteurs de comptes
  d'éléments de la formule brute (deux isomères sont indiscernables) ;
  (b) similarité InChIKey : 1.0 si le premier bloc (connectivité) est
  identique, 0.0 sinon — HEURISTIQUE, pas une mesure structurelle fine ;
  (c) bonus CLASSE chimique : 1.0 si la classe déclarée est identique
  (information déclarative, pas calculée).
  Chaque composante est étiquetée « heuristique sans RDKit, non structurale ».

- **AVEC RDKit** (niveau SCIENCE, optionnel) : en PLUS de l'heuristique,
  similarité STRUCTURELLE réelle — coefficient de Tanimoto sur empreintes
  de Morgan (rayon 2, 2048 bits) calculées par RDKit à partir des SMILES
  fournis, + comparaison par sous-structure (SMARTS) quand pertinent.

RÈGLE DURE : une ressemblance de FORMULE (``formule_similaire``) n'est
JAMAIS présentée comme une similarité pharmacologique — l'avertissement
est obligatoire dans chaque rapport. Seule ``structure_similaire``
(Tanimoto de Morgan via RDKit) reflète la structure ; même elle ne prouve
aucune activité biologique.
"""

from __future__ import annotations

from ._chimie_utils import (
    analyser_formule,
    premier_bloc_inchikey,
)

try:
    from webintel.science import chimie as _chimie
except Exception:  # noqa: BLE001 — moteur dégradé proprement, jamais d'exception
    _chimie = None

ENGINE_NAME = "molecular_similarity"
ENGINE_VERSION = "1.1.0"
DESCRIPTION = (
    "Similarité entre composés chimiques : heuristique documentée "
    "(Tanimoto sur les comptes d'éléments de la formule brute, égalité du "
    "1er bloc InChIKey, bonus de classe chimique déclarée) + — si RDKit est "
    "installé (niveau SCIENCE) et des SMILES sont fournis — similarité "
    "structurelle réelle (Tanimoto sur empreintes de Morgan) et comparaison "
    "par sous-structure. Chaque composante porte sa méthode ; une "
    "ressemblance de formule n'est jamais présentée comme une similarité "
    "pharmacologique."
)

INPUTS = {
    "composes": (
        "Liste de 2 à 200 composés, chacun un dict avec 'id' ou 'nom' "
        "(identifiant), 'formule' (formule brute, obligatoire, ex. 'C6H12O6'), "
        "et champs optionnels 'smiles' (requis pour la similarité "
        "structurelle via RDKit), 'inchikey', 'classe_chimique'."
    )
}

PARAMETERS = {
    "methode": {
        "type": "choice",
        "default": "structure",
        "min": None,
        "max": None,
        "choices": ["structure", "combinee", "formule", "inchikey", "classe"],
        "description": (
            "Score retourné pour chaque paire : 'structure' (Tanimoto de "
            "Morgan via RDKit ; None si non évaluable) — méthode par défaut ; "
            "'combinee' — HEURISTIQUE NON STRUCTURELLE (moyenne pondérée "
            "des composantes heuristiques : formule 0.5, inchikey 0.3, "
            "classe 0.2 — ne contient AUCUN calcul structurel, inchangée "
            "v1.0.0) ; ou une composante heuristique seule ('formule', "
            "'inchikey', 'classe'). Le détail de toutes les composantes est "
            "toujours fourni, quelle que soit la méthode choisie."
        ),
    }
}

LIMITATIONS = [
    "Sans RDKit : heuristique « sans RDKit, non structurale » — la "
    "similarité de formule ignore totalement la structure (liaisons, "
    "cycles ; deux isomères obtiennent 1.0).",
    "Même avec RDKit, structure_similaire (Tanimoto de Morgan) mesure une "
    "ressemblance de fragments, pas une similarité pharmacologique : elle "
    "ne prédit ni l'activité biologique, ni la sélectivité, ni la toxicité.",
    "La composante 'sous_structure' indique une inclusion de motif, pas une "
    "équivalence d'effet.",
    "Le bonus de classe chimique repose sur une étiquette déclarative, "
    "pas sur un calcul.",
    "Les scores de ce moteur sont des outils de tri exploratoire, pas des "
    "preuves de similarité chimique de référence.",
]

# Pondérations du score combiné (documentées dans PARAMETERS ; inchangées v1.0.0).
_POIDS = {"formule": 0.5, "inchikey": 0.3, "classe": 0.2}

_METHODE_FORMULE = (
    "Tanimoto sur vecteurs de comptes d'éléments (formule brute) : "
    "dot(v1,v2) / (|v1|² + |v2|² − dot(v1,v2)). Ignore la structure ; "
    "deux isomères obtiennent 1.0. Heuristique sans RDKit, non structurale."
)
_METHODE_INCHIKEY = (
    "Heuristique : 1.0 si le premier bloc (14 caractères, couche de "
    "connectivité) de l'InChIKey est identique, 0.0 sinon. Ne reflète pas "
    "une similarité structurelle fine."
)
_METHODE_CLASSE = (
    "Bonus déclaratif : 1.0 si la classe chimique déclarée est identique, "
    "0.0 sinon. Information fournie en entrée, pas calculée."
)
_METHODE_STRUCTURE = (
    "Coefficient de Tanimoto sur empreintes de Morgan (rayon 2, 2048 bits) "
    "calculées par RDKit à partir des SMILES : |A∩B| / |A∪B| sur les bits. "
    "Mesure structurelle réelle (fragments), pas une similarité "
    "pharmacologique."
)
_METHODE_SOUS_STRUCTURE = (
    "Recherche de sous-structure (SMARTS) via RDKit : indique si l'une des "
    "deux molécules contient l'autre comme sous-structure. Inclusion de "
    "motif, pas équivalence d'effet."
)

AVERTISSEMENT = (
    "DISTINCTION OBLIGATOIRE : 'formule_similaire' (ressemblance de formule "
    "brute) et 'classe_chimique_similaire' (étiquette déclarative) ne "
    "constituent PAS une similarité pharmacologique et ne prédisent AUCUNE "
    "activité biologique. 'structure_similaire' (Tanimoto de Morgan, RDKit) "
    "reflète la structure mais ne permet de conclure ni à un effet, ni à "
    "une sélectivité, ni à la sécurité d'emploi. Scores exploratoires — "
    "à confirmer expérimentalement."
)

#: Bornes de normalisation des champs texte.
_MAX_ID = 200
_MAX_FORMULE = 500
_MAX_TEXTE = 1000
_MAX_SMILES = 2000


def is_available() -> tuple[bool, str]:
    """Disponibilité : pur Python + RDKit optionnel (jamais bloquant)."""
    if _chimie is not None and _chimie.rdkit_disponible():
        infos = _chimie.infos_biblio()
        return True, (
            f"Moteur disponible (Python pur + RDKit {infos['version']} : "
            "similarité structurelle réelle active)."
        )
    return True, (
        "Moteur disponible (Python pur, heuristique sans RDKit, non "
        "structurale — installez le niveau SCIENCE pour la similarité "
        "structurelle réelle)."
    )


def _rdkit_actif() -> bool:
    """Vrai si le calcul structurel réel est possible ici."""
    return _chimie is not None and _chimie.rdkit_disponible()


def _normaliser_compose(entree: object, index: int) -> dict:
    """Valide et normalise un composé ; lève ValueError si invalide."""
    if not isinstance(entree, dict):
        raise ValueError(
            f"Composé #{index} invalide : un dict est requis, "
            f"reçu {type(entree).__name__}."
        )
    identifiant = entree.get("id", entree.get("nom", entree.get("identifiant")))
    # `identifiant` : la forme déjà normalisée (sortie de validate_inputs)
    # est acceptée telle quelle — la validation est idempotente, ce qui
    # permet à `run()` de re-valider les entrées normalisées que lui
    # transmet `simulations.service.run_simulation`.
    if not isinstance(identifiant, str) or not identifiant.strip():
        raise ValueError(
            f"Composé #{index} invalide : 'id' ou 'nom' (chaîne non vide) "
            "est obligatoire."
        )
    formule = entree.get("formule")
    if not isinstance(formule, str) or not formule.strip():
        raise ValueError(
            f"Composé {identifiant!r} invalide : 'formule' (formule brute) "
            "est obligatoire."
        )
    # Lève ValueError avec un message explicite si la formule est invalide.
    comptes = analyser_formule(formule.strip()[:_MAX_FORMULE])

    def _texte(cle: str) -> str | None:
        valeur = entree.get(cle)
        if valeur is None:
            return None
        if not isinstance(valeur, str) or not valeur.strip():
            return None
        return valeur.strip()[:_MAX_TEXTE]

    smiles_brut = entree.get("smiles")
    smiles: str | None = None
    if isinstance(smiles_brut, str) and smiles_brut.strip():
        smiles = smiles_brut.strip()[:_MAX_SMILES]

    return {
        "identifiant": identifiant.strip()[:_MAX_ID],
        "formule": formule.strip()[:_MAX_FORMULE],
        "comptes": comptes,
        "smiles": smiles,
        "inchikey": _texte("inchikey"),
        "classe_chimique": _texte("classe_chimique"),
    }


def validate_inputs(inputs: dict, params: dict) -> dict:
    """Valide et normalise entrées + paramètres (contrat plugin)."""
    if not isinstance(inputs, dict):
        raise ValueError("Les entrées doivent être un dict avec la clé 'composes'.")
    composes = inputs.get("composes")
    if not isinstance(composes, list):
        raise ValueError("'composes' doit être une liste de dicts.")
    if len(composes) < 2:
        raise ValueError(
            f"Au moins 2 composés sont requis pour une similarité, "
            f"reçu {len(composes)}."
        )
    if len(composes) > 200:
        raise ValueError(
            f"Maximum 200 composés par appel, reçu {len(composes)}."
        )
    normalises = [_normaliser_compose(c, i) for i, c in enumerate(composes)]

    if not isinstance(params, dict):
        raise ValueError("Les paramètres doivent être un dict.")
    methode = params.get("methode", "structure")
    if methode not in ("structure", "combinee", "formule", "inchikey", "classe"):
        raise ValueError(
            f"Paramètre 'methode' invalide : {methode!r}. Valeurs autorisées : "
            "'combinee', 'formule', 'inchikey', 'classe', 'structure'."
        )
    return {"inputs": {"composes": normalises}, "params": {"methode": methode}}


def _tanimoto_formule(c1: dict[str, int], c2: dict[str, int]) -> float:
    """Tanimoto sur les vecteurs de comptes d'éléments (Python pur).

    Identique au calcul numpy d'origine :
    dot(v1,v2) / (dot(v1,v1) + dot(v2,v2) − dot(v1,v2)),
    calculé sur l'union des clés (les zéros ne contribuent pas).
    """
    cles = set(c1) | set(c2)
    produit = sum(c1.get(k, 0) * c2.get(k, 0) for k in cles)
    norme = (
        sum(v * v for v in c1.values())
        + sum(v * v for v in c2.values())
        - produit
    )
    if norme == 0:  # impossible après validation, garde-fou
        return 0.0
    return produit / norme


def _empreinte(compose: dict) -> list[int] | None:
    """Empreinte de Morgan d'un composé (None si non calculable, jamais d'exception)."""
    if not _rdkit_actif() or not compose.get("smiles"):
        return None
    try:
        return _chimie.empreinte_morgan(compose["smiles"])
    except Exception:  # noqa: BLE001 — chimie.py ne lève déjà pas ; garde-fou
        return None


def _comparer_paire(a: dict, b: dict, methode: str) -> dict:
    """Calcule les composantes de similarité pour une paire de composés."""
    # --- (a) formule : heuristique, toujours évaluable ---
    score_formule = _tanimoto_formule(a["comptes"], b["comptes"])
    bloc_formule = {
        "score": round(score_formule, 6),
        "statut": "évalué",
        "methode": _METHODE_FORMULE,
        "nature": "formule_similaire — heuristique sans RDKit, non structurale",
    }

    # --- (b) InChIKey : heuristique, inchangée ---
    bloc_a = premier_bloc_inchikey(a["inchikey"])
    bloc_b = premier_bloc_inchikey(b["inchikey"])
    if bloc_a is None or bloc_b is None:
        score_inchikey: float | None = None
        statut_inchikey = (
            "non évaluable : InChIKey absente ou mal formée pour au moins "
            "un des deux composés"
        )
    else:
        score_inchikey = 1.0 if bloc_a == bloc_b else 0.0
        statut_inchikey = "évalué"
    bloc_inchikey = {
        "score": score_inchikey,
        "statut": statut_inchikey,
        "methode": _METHODE_INCHIKEY,
        "nature": "heuristique sans RDKit, non structurale",
    }

    # --- (c) classe chimique : bonus déclaratif, inchangé ---
    classe_a = (a["classe_chimique"] or "").strip().lower() or None
    classe_b = (b["classe_chimique"] or "").strip().lower() or None
    if classe_a is None or classe_b is None:
        score_classe: float | None = None
        statut_classe = (
            "non évaluable : classe chimique non déclarée pour au moins "
            "un des deux composés"
        )
    else:
        score_classe = 1.0 if classe_a == classe_b else 0.0
        statut_classe = "évalué"
    bloc_classe = {
        "score": score_classe,
        "statut": statut_classe,
        "methode": _METHODE_CLASSE,
        "nature": "classe_chimique_similaire — étiquette déclarative, pas un calcul",
    }

    # --- (d) structure : Tanimoto de Morgan via RDKit (réel) ---
    if not _rdkit_actif():
        score_structure: float | None = None
        statut_structure = (
            "non évaluable : RDKit absent (niveau SCIENCE non installé) — "
            "voir docs/runtime_scientifique.md"
        )
    elif not a.get("smiles") or not b.get("smiles"):
        score_structure = None
        statut_structure = (
            "non évaluable : SMILES manquant pour au moins un des deux "
            "composés (la similarité structurelle exige un SMILES + RDKit)"
        )
    else:
        fp_a, fp_b = _empreinte(a), _empreinte(b)
        if fp_a is None or fp_b is None:
            score_structure = None
            statut_structure = (
                "non évaluable : au moins un SMILES est invalide "
                "(rejeté par RDKit) — aucun score inventé"
            )
        else:
            score_structure = _chimie.tanimoto_fp(fp_a, fp_b)
            statut_structure = (
                "évalué (RDKit {})".format(_chimie.infos_biblio()["version"])
                if score_structure is not None
                else "non évaluable : empreintes vides"
            )
    bloc_structure = {
        "score": round(score_structure, 6) if score_structure is not None else None,
        "statut": statut_structure,
        "methode": _METHODE_STRUCTURE,
        "nature": "structure_similaire — Tanimoto de Morgan (RDKit), structurelle réelle",
    }

    # --- (e) sous-structure : inclusion de motif, quand pertinent ---
    if _rdkit_actif() and a.get("smiles") and b.get("smiles"):
        try:
            a_dans_b = _chimie.sous_structure(b["smiles"], a["smiles"])
            b_dans_a = _chimie.sous_structure(a["smiles"], b["smiles"])
        except Exception:  # noqa: BLE001 — garde-fou
            a_dans_b = b_dans_a = None
        if a_dans_b is None or b_dans_a is None:
            relation = None
            statut_sous = "non évaluable : SMILES invalide pour la recherche de motif"
        elif a_dans_b and b_dans_a:
            relation = "motifs_mutuellement_inclus"
            statut_sous = "évalué"
        elif a_dans_b:
            relation = "a_sous_structure_de_b"
            statut_sous = "évalué"
        elif b_dans_a:
            relation = "b_sous_structure_de_a"
            statut_sous = "évalué"
        else:
            relation = "aucune_inclusion"
            statut_sous = "évalué"
    else:
        relation = None
        if not _rdkit_actif():
            statut_sous = "non évaluable : RDKit absent"
        else:
            statut_sous = "non évaluable : SMILES manquant pour au moins un composé"
    bloc_sous_structure = {
        "relation": relation,
        "statut": statut_sous,
        "methode": _METHODE_SOUS_STRUCTURE,
    }

    # Nomenclature explicite exigée (v2.1) + alias rétrocompatibles v1.0.0.
    composantes = {
        "formule_similaire": bloc_formule,
        "structure_similaire": bloc_structure,
        "classe_chimique_similaire": bloc_classe,
        "inchikey": bloc_inchikey,
        "sous_structure": bloc_sous_structure,
        # Alias v1.0.0 (rétrocompatibilité) :
        "formule": bloc_formule,
        "classe": bloc_classe,
    }

    if methode == "combinee":
        total_poids = 0.0
        total = 0.0
        for nom, poids in _POIDS.items():
            s = composantes[nom]["score"]
            if s is not None:
                total += poids * s
                total_poids += poids
        score = round(total / total_poids, 6) if total_poids > 0 else None
    elif methode == "structure":
        s = bloc_structure["score"]
        score = round(s, 6) if s is not None else None
    else:
        s = composantes[methode]["score"]
        score = round(s, 6) if s is not None else None

    return {
        "a": a["identifiant"],
        "b": b["identifiant"],
        "score": score,
        "details": composantes,
    }


def run(inputs: dict, params: dict) -> dict:
    """Exécute la similarité : matrice des paires triée par score décroissant.

    Déterministe, JSON-sérialisable, sans code arbitraire ni shell.
    """
    valide = validate_inputs(inputs, params)
    composes = valide["inputs"]["composes"]
    methode = valide["params"]["methode"]

    paires = [
        _comparer_paire(composes[i], composes[j], methode)
        for i in range(len(composes))
        for j in range(i + 1, len(composes))
    ]
    # Tri décroissant sur le score ; les paires non évaluables (score None)
    # en fin de liste ; ordre déterministe en cas d'égalité (a, b).
    paires.sort(
        key=lambda p: (
            p["score"] is None,
            -(p["score"] if p["score"] is not None else 0.0),
            p["a"],
            p["b"],
        )
    )
    return {
        "moteur": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "methode": methode,
        "rdkit_actif": _rdkit_actif(),
        "nombre_composes": len(composes),
        "nombre_paires": len(paires),
        "paires": paires,
        "avertissement": AVERTISSEMENT,
        "distinction_sorties": {
            "formule_similaire": (
                "Ressemblance de FORMULE BRUTE (Tanimoto sur comptes "
                "d'éléments) — heuristique sans RDKit, non structurale. "
                "N'est PAS une similarité pharmacologique."
            ),
            "structure_similaire": (
                "Similarité STRUCTURELLE réelle (Tanimoto sur empreintes de "
                "Morgan, RDKit) — reflète les fragments, pas l'activité "
                "biologique."
            ),
            "classe_chimique_similaire": (
                "Même CLASSE CHIMIQUE DÉCLARÉE (étiquette d'entrée) — "
                "information déclarative. N'est PAS une similarité "
                "pharmacologique."
            ),
        },
    }
