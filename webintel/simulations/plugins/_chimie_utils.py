"""Utilitaires chimiques partagés par les plugins de simulation.

Contenu :
  - table des symboles d'éléments chimiques (118 éléments, validation) ;
  - table des masses atomiques standard (IUPAC, g/mol) ;
  - analyseur déterministe de formule brute (ex. "C6H12O6", "Ca(OH)2") ;
  - extraction du premier bloc d'une InChIKey (couche de connectivité).

Aucune dépendance optionnelle : pur Python standard. Pas de RDKit ici,
et ce module ne prétend jamais en utiliser.
"""

from __future__ import annotations

import re

# --- Table des éléments (symboles, ordre du tableau périodique) --------------
ELEMENTS: tuple[str, ...] = (
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni",
    "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd",
    "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd",
    "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W",
    "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po",
    "At", "Rn", "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu",
    "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf",
    "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn", "Nh", "Fl",
    "Mc", "Lv", "Ts", "Og",
)
_ELEMENTS_SET = frozenset(ELEMENTS)

# --- Masses atomiques standard (IUPAC, g/mol) --------------------------------
# Valeurs conventionnelles pour les éléments à intervalle (ex. C, H, O).
MASSES_ATOMIQUES: dict[str, float] = {
    "H": 1.008, "He": 4.0026, "Li": 6.94, "Be": 9.0122, "B": 10.81,
    "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998, "Ne": 20.180,
    "Na": 22.990, "Mg": 24.305, "Al": 26.982, "Si": 28.085, "P": 30.974,
    "S": 32.06, "Cl": 35.45, "Ar": 39.948, "K": 39.098, "Ca": 40.078,
    "Sc": 44.956, "Ti": 47.867, "V": 50.942, "Cr": 51.996, "Mn": 54.938,
    "Fe": 55.845, "Co": 58.933, "Ni": 58.693, "Cu": 63.546, "Zn": 65.38,
    "Ga": 69.723, "Ge": 72.630, "As": 74.922, "Se": 78.971, "Br": 79.904,
    "Kr": 83.798, "Rb": 85.468, "Sr": 87.62, "Y": 88.906, "Zr": 91.224,
    "Nb": 92.906, "Mo": 95.95, "Tc": 98.0, "Ru": 101.07, "Rh": 102.91,
    "Pd": 106.42, "Ag": 107.87, "Cd": 112.41, "In": 114.82, "Sn": 118.71,
    "Sb": 121.76, "Te": 127.60, "I": 126.90, "Xe": 131.29, "Cs": 132.91,
    "Ba": 137.33, "La": 138.91, "Ce": 140.12, "Pr": 140.91, "Nd": 144.24,
    "Pm": 145.0, "Sm": 150.36, "Eu": 151.96, "Gd": 157.25, "Tb": 158.93,
    "Dy": 162.50, "Ho": 164.93, "Er": 167.26, "Tm": 168.93, "Yb": 173.05,
    "Lu": 174.97, "Hf": 178.49, "Ta": 180.95, "W": 183.84, "Re": 186.21,
    "Os": 190.23, "Ir": 192.22, "Pt": 195.08, "Au": 196.97, "Hg": 200.59,
    "Tl": 204.38, "Pb": 207.2, "Bi": 208.98, "Po": 209.0, "At": 210.0,
    "Rn": 222.0, "Fr": 223.0, "Ra": 226.0, "Ac": 227.0, "Th": 232.04,
    "Pa": 231.04, "U": 238.03, "Np": 237.0, "Pu": 244.0, "Am": 243.0,
    "Cm": 247.0, "Bk": 247.0, "Cf": 251.0, "Es": 252.0, "Fm": 257.0,
    "Md": 258.0, "No": 259.0, "Lr": 266.0, "Rf": 267.0, "Db": 268.0,
    "Sg": 269.0, "Bh": 270.0, "Hs": 277.0, "Mt": 278.0, "Ds": 281.0,
    "Rg": 282.0, "Cn": 285.0, "Nh": 286.0, "Fl": 289.0, "Mc": 290.0,
    "Lv": 293.0, "Ts": 294.0, "Og": 294.0,
}

# --- Analyseur de formule brute ----------------------------------------------
_JETON = re.compile(r"[A-Z][a-z]?|\d+|\(|\)")


def analyser_formule(formule: str) -> dict[str, int]:
    """Analyse une formule brute et retourne {symbole: nombre d'atomes}.

    Gère les éléments (ex. "C", "Cl"), les multiplicités (ex. "H2") et les
    parenthèses imbriquées (ex. "Ca(OH)2"). Lève ``ValueError`` avec un
    message explicite si la formule est invalide (élément inconnu,
    caractère non reconnu, parenthèses mal équilibrées).
    """
    if not isinstance(formule, str) or not formule.strip():
        raise ValueError("Formule invalide : chaîne vide ou absente.")
    texte = formule.strip()
    jetons = _JETON.findall(texte)
    if "".join(jetons) != texte:
        raise ValueError(
            f"Formule invalide {formule!r} : caractères non reconnus."
        )
    pile: list[dict[str, int]] = [{}]
    i = 0
    while i < len(jetons):
        jeton = jetons[i]
        if jeton == "(":
            pile.append({})
            i += 1
        elif jeton == ")":
            if len(pile) < 2:
                raise ValueError(
                    f"Formule invalide {formule!r} : parenthèse fermante "
                    "sans parenthèse ouvrante."
                )
            groupe = pile.pop()
            i += 1
            multiplicateur = 1
            if i < len(jetons) and jetons[i].isdigit():
                multiplicateur = int(jetons[i])
                i += 1
            for element, compte in groupe.items():
                pile[-1][element] = pile[-1].get(element, 0) + compte * multiplicateur
        elif jeton[0].isupper():
            if jeton not in _ELEMENTS_SET:
                raise ValueError(
                    f"Formule invalide {formule!r} : élément inconnu {jeton!r}."
                )
            i += 1
            nombre = 1
            if i < len(jetons) and jetons[i].isdigit():
                nombre = int(jetons[i])
                i += 1
            pile[-1][jeton] = pile[-1].get(jeton, 0) + nombre
        else:
            raise ValueError(
                f"Formule invalide {formule!r} : nombre {jeton!r} inattendu "
                "(un nombre doit suivre un élément ou une parenthèse fermante)."
            )
    if len(pile) != 1:
        raise ValueError(
            f"Formule invalide {formule!r} : parenthèse ouvrante non fermée."
        )
    if not pile[0]:
        raise ValueError(f"Formule invalide {formule!r} : aucun atome trouvé.")
    return dict(pile[0])


def masse_molaire(comptes: dict[str, int]) -> float:
    """Masse molaire (g/mol) depuis des comptes d'atomes.

    Méthode : somme des masses atomiques standard (IUPAC) pondérées par
    les comptes. Déterministe, sans approximation cachée.
    """
    return sum(MASSES_ATOMIQUES[element] * n for element, n in comptes.items())


def premier_bloc_inchikey(inchikey: str | None) -> str | None:
    """Extrait le premier bloc d'une InChIKey (14 car., couche connectivité).

    Retourne ``None`` si l'InChIKey est absente ou mal formée. Ce bloc
    encode la connectivité atomique (sans stéréochimie fine) : l'utiliser
    comme mesure de similarité reste une HEURISTIQUE, documentée comme
    telle dans les moteurs.
    """
    if not isinstance(inchikey, str) or not inchikey.strip():
        return None
    bloc = inchikey.strip().upper().split("-")[0]
    if len(bloc) != 14 or not bloc.isalnum():
        return None
    return bloc
