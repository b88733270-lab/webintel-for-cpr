"""Templates d'extraction déclaratifs (JSON sérialisables).

`ExtractionTemplate` : un nom, une description et des règles
`{name, kind, expr, attr, transform, multiple}` :
- kind "css"   : sélecteur CSS BeautifulSoup ; `attr` = "text" | "html" | nom d'attribut ;
- kind "xpath" : expression XPath évaluée via lxml sur le HTML brut ;
- kind "regex" : expression régulière sur le HTML brut (`attr` ignoré,
  groupes nommés -> dict si un seul groupe nommé... sinon groupes) ;
- `transform` : "strip" | "lower" | "upper" | None ;
- `multiple` : True -> liste de valeurs, False -> première valeur (ou None).

`save(path)` / `load(path)` : persistance JSON. `apply(soup, html)` accepte une
soup BeautifulSoup et/ou le HTML brut (la soup est re-sérialisée si besoin).

Exemples de templates (docstring, pas de fichiers imposés) :
- "produit" : règles name (css `h1`), price (css `.price` + regex montant),
  availability (regex "en stock|rupture"), images (css `img[src]`, attr src, multiple).
- "entreprise" : name (css `og:site_name` via xpath `//meta[@property='og:site_name']/@content`),
  emails (regex), phones (regex).
- "article" : title (css `h1`), author (css `.author`), date (css `time[datetime]`, attr datetime).
- "immobilier" : surface (regex `(\\d+)\\s*m²`), pièces, prix.
- "emploi" : intitulé (css `h1`), contrat (regex `CDI|CDD|freelance`), lieu.
- "annuaire" : entrées (css `.listing-item`, multiple) avec nom/téléphone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from lxml import html as lxml_html

_VALID_KINDS = ("css", "xpath", "regex")
_VALID_TRANSFORMS = (None, "strip", "lower", "upper")


def _apply_transform(value: str | None, transform: str | None) -> str | None:
    if value is None:
        return None
    if transform == "strip":
        return value.strip()
    if transform == "lower":
        return value.lower()
    if transform == "upper":
        return value.upper()
    return value


class ExtractionTemplate:
    """Template d'extraction déclaratif, sérialisable en JSON."""

    def __init__(self, name: str, description: str = "", rules: list[dict] | None = None) -> None:
        self.name = name
        self.description = description
        self.rules: list[dict] = []
        for rule in rules or []:
            self.add_rule(**rule)

    def add_rule(self, name: str, kind: str, expr: str, attr: str = "text",
                 transform: str | None = "strip", multiple: bool = False) -> None:
        """Ajoute une règle (valide kind/transform ; lève `ValueError` sinon)."""
        if kind not in _VALID_KINDS:
            raise ValueError(f"kind invalide : {kind!r} (attendu : {', '.join(_VALID_KINDS)})")
        if transform not in _VALID_TRANSFORMS:
            raise ValueError(f"transform invalide : {transform!r}")
        if kind == "regex":
            re.compile(expr)  # valide la regex tout de suite
        self.rules.append({
            "name": name, "kind": kind, "expr": expr, "attr": attr,
            "transform": transform, "multiple": bool(multiple),
        })

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "rules": self.rules}

    def save(self, path: str | Path) -> Path:
        """Sérialise le template en JSON."""
        target = Path(path)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "ExtractionTemplate":
        """Charge un template depuis un fichier JSON."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data.get("name", ""), data.get("description", ""), data.get("rules", []))

    def apply(self, soup: BeautifulSoup | None = None, html: str | None = None) -> dict:
        """Applique les règles ; retourne `{nom_règle: valeur | [valeurs]}`."""
        if soup is None and html is None:
            raise ValueError("apply() requiert `soup` ou `html`.")
        if soup is None:
            soup = BeautifulSoup(html, "lxml")
        if html is None:
            html = str(soup)
        results: dict = {}
        for rule in self.rules:
            kind = rule["kind"]
            if kind == "css":
                results[rule["name"]] = self._apply_css(soup, rule)
            elif kind == "xpath":
                results[rule["name"]] = self._apply_xpath(html, rule)
            else:
                results[rule["name"]] = self._apply_regex(html, rule)
        return results

    def _finalize(self, values: list, rule: dict):
        transform = rule.get("transform")
        cleaned = [_apply_transform(v, transform) for v in values]
        cleaned = [v for v in cleaned if v not in (None, "")]
        if rule.get("multiple"):
            return cleaned
        return cleaned[0] if cleaned else None

    def _apply_css(self, soup: BeautifulSoup, rule: dict):
        attr = rule.get("attr", "text")
        values = []
        for el in soup.select(rule["expr"]):
            if attr == "text":
                values.append(el.get_text(separator=" "))
            elif attr == "html":
                values.append(el.decode_contents())
            else:
                val = el.get(attr)
                if val is not None:
                    values.append(str(val))
        return self._finalize(values, rule)

    def _apply_xpath(self, html: str, rule: dict):
        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return [] if rule.get("multiple") else None
        try:
            found = tree.xpath(rule["expr"])
        except Exception:
            return [] if rule.get("multiple") else None
        values = []
        for node in found:
            if isinstance(node, str):
                values.append(node)
            else:
                values.append(node.text_content())
        return self._finalize(values, rule)

    def _apply_regex(self, html: str, rule: dict):
        pattern = re.compile(rule["expr"], re.IGNORECASE | re.DOTALL)
        values = []
        for match in pattern.finditer(html):
            groupdict = match.groupdict()
            if groupdict:
                # Groupes nommés -> dict aplati (premier groupe non vide si un seul).
                named = {k: v for k, v in groupdict.items() if v}
                values.append(named if len(named) > 1 else next(iter(named.values()), ""))
            elif match.groups():
                values.append(match.group(1))
            else:
                values.append(match.group(0))
        return self._finalize(values, rule)
