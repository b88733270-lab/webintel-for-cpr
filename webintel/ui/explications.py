"""Explications « Qu'est-ce que cela signifie ? » — UI chercheur v2.2.

Dictionnaire français d'explications prudentes pour chaque métrique
technique affichée dans l'assistant d'étude. Chaque entrée expose :

- ``titre`` : libellé lisible de la métrique ;
- ``explication`` : texte prudent, jamais de promesse d'efficacité ;
- ``echelle`` (optionnel) : repères d'interprétation honnêtes.

Règle d'or : une explication décrit ce que la valeur MESURE et ses
limites — jamais ce qu'elle prouverait.
"""

from __future__ import annotations

EXPLICATIONS: dict[str, dict[str, str]] = {
    "tanimoto": {
        "titre": "Similarité de Tanimoto",
        "explication": (
            "Le coefficient de Tanimoto mesure le recouvrement entre deux "
            "empreintes moléculaires (0 = aucun fragment commun, 1 = "
            "empreintes identiques). Une valeur élevée indique que les deux "
            "molécules partagent beaucoup de fragments structuraux, selon "
            "l'empreinte utilisée. Cela ne signifie pas qu'elles ont "
            "nécessairement la même activité biologique : deux molécules "
            "très semblables peuvent agir différemment, et deux molécules "
            "dissemblables peuvent viser la même cible."
        ),
        "echelle": (
            "Repères usuels (indicatifs, pas des seuils de vérité) : "
            "≥ 0,85 très semblables · 0,70–0,85 semblables · 0,50–0,70 "
            "partiellement semblables · < 0,50 peu semblables."
        ),
    },
    "logp": {
        "titre": "logP (coefficient de partage octanol/eau)",
        "explication": (
            "Le logP estime l'affinité d'une molécule pour les graisses "
            "plutôt que pour l'eau. Une valeur positive indique une molécule "
            "plutôt lipophile, une valeur négative une molécule plutôt "
            "hydrophile. C'est une valeur calculée (Wildman-Crippen via "
            "RDKit), pas une mesure expérimentale : elle ne permet pas de "
            "conclure à un effet pharmacologique ni à la sécurité d'emploi "
            "d'un composé."
        ),
        "echelle": (
            "Repère de Lipinski : un logP calculé ≤ 5 est l'un des cinq "
            "critères usuels de « drug-likeness » — un critère parmi "
            "d'autres, pas une garantie."
        ),
    },
    "tpsa": {
        "titre": "TPSA (surface polaire topologique)",
        "explication": (
            "La TPSA additionne la surface des atomes polaires d'une "
            "molécule (en Å²). Elle donne une indication théorique sur la "
            "capacité de la molécule à traverser les membranes. C'est une "
            "estimation calculée, pas une mesure de perméabilité réelle, et "
            "elle ne prédit ni l'absorption ni l'activité."
        ),
        "echelle": (
            "Repères usuels : < 140 Å² souvent cité pour une absorption "
            "orale plausible, < 90 Å² pour un passage potentiel de la "
            "barrière hémato-encéphalique — des indications, pas des preuves."
        ),
    },
    "hbd": {
        "titre": "Donneurs de liaisons hydrogène (HBD)",
        "explication": (
            "Nombre de groupes de la molécule capables de donner une "
            "liaison hydrogène (ex. –OH, –NH). C'est un descripteur compté "
            "sur la structure, utile pour caractériser la molécule. Il ne "
            "mesure ni l'activité biologique ni la sécurité."
        ),
        "echelle": "Repère de Lipinski : ≤ 5 donneurs.",
    },
    "hba": {
        "titre": "Accepteurs de liaisons hydrogène (HBA)",
        "explication": (
            "Nombre d'atomes de la molécule capables d'accepter une liaison "
            "hydrogène (ex. oxygènes, azotes). Descripteur structural compté "
            "par calcul — il décrit la molécule, il ne prédit pas son effet."
        ),
        "echelle": "Repère de Lipinski : ≤ 10 accepteurs.",
    },
    "masse_molaire": {
        "titre": "Masse molaire",
        "explication": (
            "Masse d'une mole de molécules, en g/mol, calculée depuis la "
            "formule brute ou la structure. C'est une donnée de "
            "caractérisation chimique, sans lien direct avec une activité "
            "biologique."
        ),
        "echelle": "Repère de Lipinski : < 500 g/mol.",
    },
    "lipinski": {
        "titre": "Règles de Lipinski (drug-likeness)",
        "explication": (
            "Les règles de Lipinski comptent combien de critères usuels "
            "(masse < 500, logP ≤ 5, ≤ 5 donneurs, ≤ 10 accepteurs) une "
            "molécule respecte. Zéro violation suggère une molécule "
            "« médicamenteuse » sur le papier. C'est un filtre de "
            "criblage, pas une validation : beaucoup de médicaments "
            "violent ces règles, et les respecter ne garantit ni un "
            "effet thérapeutique ni la sécurité d'emploi."
        ),
    },
    "docking_score": {
        "titre": "Score de docking (affinité prédite)",
        "explication": (
            "Le score de docking (en kcal/mol, plus négatif = interaction "
            "théoriquement plus favorable) estime l'affinité entre un "
            "ligand et un récepteur par calcul. C'est une PRÉDICTION "
            "informatique : elle dépend du récepteur préparé, de la boîte "
            "de recherche et du paramétrage, et ne constitue pas une "
            "preuve d'activité biologique. Un bon score ne signifie pas "
            "que la molécule aurait un effet thérapeutique."
        ),
    },
    "bliss": {
        "titre": "Score de Bliss (synergie)",
        "explication": (
            "Le modèle de Bliss compare l'effet observé d'un mélange à "
            "l'effet attendu si les deux composés agissaient indépendamment "
            "(E_bliss = E_A + E_B − E_A·E_B). Un écart positif suggère une "
            "synergie, un écart négatif un antagonisme — sur les données "
            "fournies uniquement. Le résultat dépend entièrement de la "
            "qualité des mesures expérimentales importées et ne constitue "
            "pas une preuve d'effet clinique."
        ),
    },
    "loewe": {
        "titre": "Score de Loewe (synergie)",
        "explication": (
            "Le modèle de Loewe compare le mélange à ce qu'on attendrait "
            "si les deux composés étaient la même substance à doses "
            "additionnées. Comme Bliss, c'est un calcul appliqué à des "
            "données expérimentales fournies par le chercheur : le score "
            "décrit ces données, il ne constitue pas une preuve d'effet "
            "thérapeutique."
        ),
    },
    "hsa": {
        "titre": "Score HSA — Highest Single Agent (synergie)",
        "explication": (
            "Le modèle HSA compare l'effet du mélange à celui du meilleur "
            "des deux composés seul. C'est le critère le plus simple : il "
            "décrit les données expérimentales importées et ne constitue "
            "pas une preuve d'effet clinique."
        ),
    },
    "zip": {
        "titre": "Score ZIP (synergie)",
        "explication": (
            "Le modèle ZIP combine les logiques de Bliss et de Loewe pour "
            "évaluer l'écart entre l'effet observé du mélange et l'effet "
            "attendu. Calculé sur les données expérimentales fournies, il "
            "reste une description de ces données — pas une démonstration "
            "d'effet clinique."
        ),
    },
    "fraction_csp3": {
        "titre": "Fraction Csp3",
        "explication": (
            "Proportion d'atomes de carbone hybridés sp3 (tétraédriques, "
            "« 3D ») parmi les carbones de la molécule. Une fraction "
            "élevée indique une molécule moins plane, parfois associée à "
            "une meilleure solubilité. Descripteur calculé : il caractérise "
            "la forme, pas l'activité."
        ),
    },
    "liaisons_rotatives": {
        "titre": "Liaisons rotatives",
        "explication": (
            "Nombre de liaisons autour desquelles la molécule peut "
            "pivoter — une mesure calculée de sa flexibilité. Une "
            "flexibilité élevée est parfois associée à une moins bonne "
            "biodisponibilité orale, mais c'est une indication théorique, "
            "pas une mesure d'effet."
        ),
    },
}

# Valeurs numériques éventuelles interpolées dans « exemple » (jamais
# inventées : fournies par l'appelant depuis un vrai résultat).
_EXEMPLES: dict[str, str] = {
    "tanimoto": (
        "Exemple : Tanimoto {valeur} — « Les deux molécules présentent "
        "une forte similarité selon l'empreinte moléculaire utilisée. "
        "Cela ne signifie pas qu'elles ont nécessairement la même "
        "activité biologique. »"
    ),
}


def explication_pour(cle: str, valeur: str | float | None = None) -> dict | None:
    """Retourne l'explication prudente d'une métrique (None si inconnue).

    ``valeur`` (optionnelle) est interpolée dans l'exemple illustratif
    lorsqu'un gabarit existe — la valeur doit provenir d'un vrai calcul.
    """
    entree = EXPLICATIONS.get((cle or "").strip().lower())
    if entree is None:
        return None
    resultat = {
        "cle": (cle or "").strip().lower(),
        "titre": entree["titre"],
        "explication": entree["explication"],
        "echelle": entree.get("echelle"),
        "exemple": None,
    }
    gabarit = _EXEMPLES.get(resultat["cle"])
    if gabarit is not None and valeur is not None:
        try:
            resultat["exemple"] = gabarit.format(valeur=valeur)
        except Exception:  # noqa: BLE001 — gabarit fautif : pas d'exemple
            resultat["exemple"] = None
    return resultat


def cles_disponibles() -> list[str]:
    """Clés de métriques documentées (triées)."""
    return sorted(EXPLICATIONS)
