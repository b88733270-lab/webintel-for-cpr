"""Moteur de prédiction de propriétés physico-chimiques (v2.1).

Deux régimes, explicitement distingués :

- **Depuis la formule brute** (toujours, Python pur) : masse molaire exacte
  (somme des masses atomiques standard IUPAC) et comptes d'atomes —
  calculs déterministes et documentés.
- **Via RDKit** (niveau SCIENCE, optionnel) : quand un ``smiles`` est fourni
  ET que RDKit est installé — masse molaire, logP (Wildman-Crippen),
  donneurs/accepteurs de liaisons H, TPSA, liaisons rotatives, fraction
  Csp3, violations de Lipinski — RÉELLEMENT calculés par RDKit, jamais
  inventés.

RÈGLE DURE : sans RDKit (ou sans SMILES), logP, donneurs/accepteurs H,
TPSA et les autres descripteurs structurels portent
``"statut": "non_calcule"`` avec la raison explicite. Ce moteur n'invente
JAMAIS ces valeurs.

Chaque résultat enregistre sa provenance : méthode, bibliothèque, version,
entrée, limites (voir ``provenance_calcul`` par composé et ``calcul`` au
niveau du run).
"""

from __future__ import annotations

from ._chimie_utils import analyser_formule, masse_molaire

try:
    from webintel.science import chimie as _chimie
except Exception:  # noqa: BLE001 — moteur dégradé proprement, jamais d'exception
    _chimie = None

ENGINE_NAME = "property_prediction"
ENGINE_VERSION = "1.1.0"
DESCRIPTION = (
    "Propriétés physico-chimiques : masse molaire et comptes d'atomes "
    "calculés exactement depuis la formule brute (toujours) + — si RDKit "
    "est installé (niveau SCIENCE) et un SMILES est fourni — logP, "
    "donneurs/accepteurs H, TPSA, liaisons rotatives, fraction Csp3 et "
    "violations de Lipinski réellement calculés par RDKit. Les propriétés "
    "non calculables sont marquées 'non_calcule' avec leur raison : ce "
    "moteur n'invente jamais de valeurs."
)

INPUTS = {
    "composes": (
        "Liste de 1 à 200 composés, chacun un dict avec 'id' ou 'nom' "
        "(identifiant), 'formule' (formule brute, obligatoire, ex. 'H2O'), "
        "et 'smiles' optionnel (requis pour les descripteurs RDKit : logP, "
        "HBD/HBA, TPSA, liaisons rotatives, fraction Csp3, Lipinski)."
    )
}

PARAMETERS: dict = {}

LIMITATIONS = [
    "Sans RDKit (ou sans SMILES fourni) : logP, donneurs/accepteurs de "
    "liaisons H, TPSA, liaisons rotatives, fraction Csp3 et violations de "
    "Lipinski ne sont PAS calculés — statut 'non_calcule', jamais inventés.",
    "La règle de Lipinski n'est complète qu'avec RDKit + SMILES ; sinon le "
    "verdict reste 'partiel' (critère de masse uniquement) et ne constitue "
    "pas une évaluation d'adéquation médicamenteuse.",
    "Les descripteurs RDKit (ex. logP de Wildman-Crippen) sont des "
    "ESTIMATIONS théoriques : ce ne sont pas des mesures expérimentales.",
    "La masse molaire depuis la formule brute utilise les masses atomiques "
    "standard (isotopes naturels) : ni masse monoisotopique, ni composition "
    "isotopique particulière.",
    "Aucune propriété 3D, conformationnelle ou d'activité biologique "
    "n'est estimée par ce moteur.",
]

_RAISON_SANS_RDKIT = (
    "non calculable : propriété structurelle — nécessite RDKit "
    "(niveau SCIENCE : `pip install -e \".[science]\"`) ET un SMILES en entrée"
)

_METHODE_MASSE = (
    "calculée depuis la formule brute : somme des masses atomiques "
    "standard (IUPAC) pondérées par les comptes d'atomes"
)
_METHODE_ATOMES = "calculée depuis la formule brute (analyse déterministe)"

_MAX_ID = 200
_MAX_FORMULE = 500
_MAX_SMILES = 2000


def is_available() -> tuple[bool, str]:
    """Disponibilité : pur Python + RDKit optionnel (jamais bloquant)."""
    if _chimie is not None and _chimie.rdkit_disponible():
        infos = _chimie.infos_biblio()
        return True, (
            f"Moteur disponible (Python pur + RDKit {infos['version']} : "
            "descripteurs structurels réels actifs)."
        )
    return True, "Moteur disponible (aucune dépendance optionnelle)."


def _rdkit_actif() -> bool:
    """Vrai si les descripteurs RDKit sont calculables ici."""
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

    smiles_brut = entree.get("smiles")
    smiles: str | None = None
    if isinstance(smiles_brut, str) and smiles_brut.strip():
        smiles = smiles_brut.strip()[:_MAX_SMILES]

    return {
        "identifiant": identifiant.strip()[:_MAX_ID],
        "formule": formule.strip()[:_MAX_FORMULE],
        "comptes": comptes,
        "smiles": smiles,
    }


def validate_inputs(inputs: dict, params: dict) -> dict:
    """Valide et normalise entrées + paramètres (contrat plugin)."""
    if not isinstance(inputs, dict):
        raise ValueError("Les entrées doivent être un dict avec la clé 'composes'.")
    composes = inputs.get("composes")
    if not isinstance(composes, list):
        raise ValueError("'composes' doit être une liste de dicts.")
    if len(composes) < 1:
        raise ValueError("Au moins 1 composé est requis.")
    if len(composes) > 200:
        raise ValueError(
            f"Maximum 200 composés par appel, reçu {len(composes)}."
        )
    normalises = [_normaliser_compose(c, i) for i, c in enumerate(composes)]
    if not isinstance(params, dict):
        raise ValueError("Les paramètres doivent être un dict.")
    return {"inputs": {"composes": normalises}, "params": {}}


def _propriete_non_calculee(sans_smiles: bool, echec_calcul: bool = False) -> dict:
    """Bloc standard pour une propriété impossible sans RDKit/SMILES."""
    if echec_calcul:
        raison = (
            "non calculable : le calcul RDKit a échoué pour ce descripteur "
            "(molécule problématique) — aucune valeur inventée"
        )
    elif sans_smiles and _rdkit_actif():
        raison = (
            "non calculable : aucun SMILES fourni pour ce composé — "
            "ajoutez le champ 'smiles' (RDKit est installé)"
        )
    else:
        raison = _RAISON_SANS_RDKIT
    return {"statut": "non_calcule", "raison": raison}


def _bloc_rdkit(nom: str, valeur, unite: str | None, methode: str) -> dict:
    """Bloc standard pour une propriété réellement calculée par RDKit."""
    infos = _chimie.infos_biblio()
    bloc: dict = {
        "statut": "calcule",
        "valeur": valeur,
        "methode": methode,
        "bibliotheque": "RDKit",
        "version_bibliotheque": infos["version"],
    }
    if unite is not None:
        bloc["unite"] = unite
    return bloc


_METHODE_RDKIT_BASE = "calculé par RDKit {version} via {fonction} (niveau SCIENCE)"


def _descripteurs_rdkit(compose: dict) -> dict | None:
    """Descripteurs RDKit d'un composé (None si non calculables, jamais d'exception)."""
    if not _rdkit_actif() or not compose.get("smiles"):
        return None
    try:
        desc = _chimie.descripteurs(compose["smiles"])
    except Exception:  # noqa: BLE001 — chimie.py ne lève déjà pas ; garde-fou
        desc = None
    return desc


def _evaluer_compose(compose: dict) -> dict:
    """Calcule les propriétés d'un composé normalisé."""
    comptes = compose["comptes"]
    masse = round(masse_molaire(comptes), 3)
    total_atomes = sum(comptes.values())
    lourds = sum(n for el, n in comptes.items() if el != "H")
    sans_smiles = not compose.get("smiles")
    desc = _descripteurs_rdkit(compose)
    rdkit_ok = desc is not None
    version_rdkit = _chimie.infos_biblio()["version"] if rdkit_ok else None

    def _methode(fonction: str) -> str:
        return _METHODE_RDKIT_BASE.format(version=version_rdkit, fonction=fonction)

    # --- Propriétés structurelles : RDKit réel ou "non_calcule" honnête ---
    def _bloc_ou_echec(cle: str, valeur, unite: str | None, methode: str) -> dict:
        """Bloc RDKit si la valeur existe, sinon 'non_calcule' (échec individuel)."""
        if valeur is not None:
            return _bloc_rdkit(cle, valeur, unite, methode)
        return _propriete_non_calculee(sans_smiles, echec_calcul=True)

    if rdkit_ok:
        logp = _bloc_ou_echec(
            "logp", desc["logp"], None,
            _methode("Descriptors.MolLogP (Wildman-Crippen)"),
        )
        donneurs = _bloc_ou_echec(
            "donneurs_hydrogene", desc["hbd"], None,
            _methode("Lipinski.NumHDonors"),
        )
        accepteurs = _bloc_ou_echec(
            "accepteurs_hydrogene", desc["hba"], None,
            _methode("Lipinski.NumHAcceptors"),
        )
        tpsa = _bloc_ou_echec(
            "tpsa", desc["tpsa"], "Å²",
            _methode("rdMolDescriptors.CalcTPSA"),
        )
        liaisons_rotatives = _bloc_ou_echec(
            "liaisons_rotatives", desc["rotatable_bonds"], None,
            _methode("Lipinski.NumRotatableBonds"),
        )
        fraction_csp3 = _bloc_ou_echec(
            "fraction_csp3", desc["fraction_csp3"], None,
            _methode("rdMolDescriptors.CalcFractionCSP3"),
        )
        masse_rdkit = _bloc_ou_echec(
            "masse_molaire_rdkit", desc["masse_molaire"], "g/mol",
            _methode("Descriptors.MolWt"),
        )
    else:
        logp = _propriete_non_calculee(sans_smiles)
        donneurs = _propriete_non_calculee(sans_smiles)
        accepteurs = _propriete_non_calculee(sans_smiles)
        tpsa = _propriete_non_calculee(sans_smiles)
        liaisons_rotatives = _propriete_non_calculee(sans_smiles)
        fraction_csp3 = _propriete_non_calculee(sans_smiles)
        masse_rdkit = _propriete_non_calculee(sans_smiles)

    # --- Règle de Lipinski : complète avec RDKit, partielle sinon ---
    lipinski_masse = {
        "critere": "masse molaire < 500 g/mol",
        "statut": "evalue",
        "respecte": masse < 500,
        "valeur_masse": masse,
        "methode": _METHODE_MASSE,
    }
    lipinski_non_evaluable = {
        "statut": "non_evaluable_sans_rdkit",
        "raison": _RAISON_SANS_RDKIT,
    }
    if rdkit_ok and desc["lipinski_violations"] is not None:
        violations = int(desc["lipinski_violations"])
        details_lipinski = {
            "masse_molaire": lipinski_masse,
            "logp": {
                "critere": "logP < 5",
                "statut": "evalue",
                "respecte": desc["logp"] is not None and desc["logp"] < 5,
                "valeur": desc["logp"],
                "methode": _methode("Descriptors.MolLogP"),
            },
            "donneurs_hydrogene": {
                "critere": "donneurs H ≤ 5",
                "statut": "evalue",
                "respecte": desc["hbd"] is not None and desc["hbd"] <= 5,
                "valeur": desc["hbd"],
                "methode": _methode("Lipinski.NumHDonors"),
            },
            "accepteurs_hydrogene": {
                "critere": "accepteurs H ≤ 10",
                "statut": "evalue",
                "respecte": desc["hba"] is not None and desc["hba"] <= 10,
                "valeur": desc["hba"],
                "methode": _methode("Lipinski.NumHAcceptors"),
            },
        }
        lipinski = {
            "verdict": "conforme" if violations == 0 else "non_conforme",
            "violations": violations,
            "note": (
                "Règle de Lipinski évaluée complètement par RDKit "
                f"({version_rdkit}) à partir du SMILES. Rappel : estimations "
                "théoriques, pas une évaluation d'adéquation médicamenteuse."
            ),
            "details": details_lipinski,
        }
    else:
        lipinski = {
            "verdict": "partiel",
            "note": (
                "Seul le critère de masse est évaluable sans RDKit ; les "
                "autres critères sont non évaluables. Ce verdict partiel "
                "ne constitue pas une évaluation d'adéquation "
                "médicamenteuse."
            ),
            "details": {
                "masse_molaire": lipinski_masse,
                "logp": {
                    "critere": "logP < 5",
                    **lipinski_non_evaluable,
                },
                "donneurs_hydrogene": {
                    "critere": "donneurs H ≤ 5",
                    **lipinski_non_evaluable,
                },
                "accepteurs_hydrogene": {
                    "critere": "accepteurs H ≤ 10",
                    **lipinski_non_evaluable,
                },
            },
        }

    # --- Provenance du calcul (méthode, bibliothèque, version, entrée, limites) ---
    if rdkit_ok:
        provenance = {
            "methode": (
                f"descripteurs calculés par RDKit {version_rdkit} à partir du "
                "SMILES (logP Wildman-Crippen, HBD/HBA Lipinski, TPSA, "
                "liaisons rotatives, fraction Csp3) ; masse et comptes "
                "d'atomes calculés depuis la formule brute (Python pur)"
            ),
            "bibliotheque": "RDKit",
            "version_bibliotheque": version_rdkit,
            "entree": {"formule": compose["formule"], "smiles": compose["smiles"]},
            "limites": [
                "Estimations théoriques, pas des mesures expérimentales.",
                "La qualité des descripteurs dépend du SMILES fourni "
                "(tautomère, état de protonation, stéréochimie).",
            ],
        }
    else:
        provenance = {
            "methode": (
                "masse molaire et comptes d'atomes calculés depuis la "
                "formule brute (Python pur) ; descripteurs structurels "
                "non calculés (RDKit absent ou SMILES manquant)"
            ),
            "bibliotheque": "Python pur (stdlib)",
            "version_bibliotheque": None,
            "entree": {"formule": compose["formule"], "smiles": compose["smiles"]},
            "limites": [
                "logP, HBD/HBA, TPSA, liaisons rotatives, fraction Csp3 : "
                "non calculables sans RDKit + SMILES (jamais inventés).",
            ],
        }

    fiche: dict = {
        "nom": compose["identifiant"],
        "formule": compose["formule"],
        "masse_molaire": {
            "valeur": masse,
            "unite": "g/mol",
            "methode": _METHODE_MASSE,
        },
        "atomes": {
            "total": total_atomes,
            "lourds": lourds,
            "par_element": dict(sorted(comptes.items())),
            "methode": _METHODE_ATOMES,
        },
        "logp": logp,
        "donneurs_hydrogene": donneurs,
        "accepteurs_hydrogene": accepteurs,
        "tpsa": tpsa,
        "liaisons_rotatives": liaisons_rotatives,
        "fraction_csp3": fraction_csp3,
        "lipinski": lipinski,
        "provenance_calcul": provenance,
    }
    if rdkit_ok:
        fiche["masse_molaire_rdkit"] = masse_rdkit
        fiche["smiles_canonique"] = desc["smiles_canonique"]
        fiche["inchi_key"] = desc["inchi_key"]
        # Contrôle honnête : la formule RDKit (depuis le SMILES) concorde-t-elle
        # avec la formule brute déclarée ?
        fiche["coherence_formule"] = {
            "formule_declaree": compose["formule"],
            "formule_rdkit": desc["formule"],
            "concorde": (
                desc["formule"] is not None
                and "".join(sorted(desc["formule"])) == "".join(sorted(compose["formule"]))
            ) if desc["formule"] else None,
            "note": (
                "Comparaison indicative (ordre des éléments normalisé) : une "
                "divergence signale un SMILES incohérent avec la formule "
                "déclarée — vérifiez l'entrée."
            ),
        }
    return fiche


def run(inputs: dict, params: dict) -> dict:
    """Exécute la prédiction de propriétés.

    Déterministe, JSON-sérialisable, sans code arbitraire ni shell.
    """
    valide = validate_inputs(inputs, params)
    composes = valide["inputs"]["composes"]
    calcul = {
        "bibliotheque": "RDKit" if _rdkit_actif() else "Python pur (formule brute)",
        "version_bibliotheque": (
            _chimie.infos_biblio()["version"] if _rdkit_actif() else None
        ),
        "rdkit_actif": _rdkit_actif(),
        "limites": list(LIMITATIONS),
    }
    return {
        "moteur": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "calcul": calcul,
        "nombre_composes": len(composes),
        "composes": [_evaluer_compose(c) for c in composes],
        "avertissement": (
            "Valeurs calculées depuis la formule brute et — si RDKit + SMILES "
            "— par RDKit (estimations théoriques, pas des mesures "
            "expérimentales). Les propriétés non calculables sont marquées "
            "'non_calcule' et ne sont jamais inventées."
        ),
    }
