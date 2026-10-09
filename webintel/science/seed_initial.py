"""Seed de démarrage — jeu de démonstration intégré WebIntel.

Chantier A : ce module charge, de façon **idempotente**, un petit jeu de
données scientifiques de démarrage (plantes médicinales d'Afrique de
l'Ouest, quelques composés aux structures vérifiées, liens
plante→composé sourcés, preuves documentaires) afin qu'une installation
fraîche ne démarre JAMAIS sur une base vide.

SOURCE DE VÉRITÉ (choix documenté)
----------------------------------
Les données vivent en **constantes Python** dans ce module
(``PLANTES_SEED``, ``COMPOSES_SEED``, ``LIENS_SEED``,
``EVIDENCES_SEED``). Le contenu des fichiers ``demo/``
(``demo_plantes.csv``, ``demo_composes.csv``, ``demo_liens.csv``,
``demo_evidences.json``) y est repris pour les 3 plantes, 4 composés,
3 liens et 2 preuves déjà présents, puis étendu prudemment.
Les fichiers ``demo/`` ne sont PAS modifiés : ils restent le corpus du
script de démonstration ``scripts/demo_science.py``, qui y compte
exactement 3 + 4 + 3 + 2 objets créés. Les constantes évitent en outre
toute dépendance à un chemin de fichier au moment de ``init_db()``
(répertoire de travail arbitraire).

IDEMPOTENCE
-----------
Chaque enregistrement est créé via les fonctions idempotentes de
``webintel.science.service`` (``create_plant``, ``create_compound``,
``link_plant_compound``, ``add_evidence``, ``record_provenance``),
qui s'appuient sur les clés naturelles uniques du modèle :

- ``Plant.scientific_name`` (unique) ;
- ``Compound.name`` / ``Compound.inchikey`` (uniques) ;
- contrainte unique ``PlantCompound`` (plante, composé, partie,
  publication) doublée d'une recherche préalable dans le service ;
- ``Evidence.doi`` (unique).

Un second appel à :func:`charger_donnees_initiales` ne crée donc AUCUN
doublon et retourne des compteurs à zéro. Conformément à la convention
du module scientifique, cette fonction ne committe JAMAIS : elle
``flush`` et laisse l'appelant (le coordinateur, à chaque ``init_db``)
committer.

HONNÊTETÉ SCIENTIFIQUE (règles appliquées aux données ci-dessous)
------------------------------------------------------------------
- Aucune concentration, posologie ou allégation d'effet n'est semée :
  les concentrations du corpus ``demo/`` ne sont PAS reprises (non
  revérifiées ici — un nombre non vérifié ne doit pas être présenté
  comme une donnée publiée).
- Les usages traditionnels, quand ils sont mentionnés, portent la
  mention « USAGE TRADITIONNEL RAPPORTÉ » et ne sont jamais formulés
  comme une efficacité, une innocuité ou un traitement.
- Un composé n'est ajouté que si son SMILES exact est connu et vérifié
  (les 4 composés du corpus démo, plus le 6-gingérol dont la structure
  a été revérifiée par RDKit : formule C17H26O4, masse 294,39 g/mol ;
  SMILES volontairement sans stéréochimie assignée). Sinon, la plante
  est créée sans composé plutôt que d'inventer une structure (ex.
  *Azadirachta indica* : aucun lien vers l'azadirachtine, faute de
  SMILES certain).
- Les noms vernaculaires ne sont renseignés que lorsqu'ils sont
  certains (ex. « Bissap » pour *Hibiscus sabdariffa*) ; la langue
  n'est jamais devinée : format ``{"fr": ..., "en": ...}`` comme le
  corpus démo, ou dictionnaire vide.
- Les synonymes douteux du corpus démo ne sont pas repris :
  « Hibiscus acetosella » (espèce distincte) et « Artemisia
  chamomilla » (synonymie non confirmée) sont écartés plutôt que
  propagés.

PROVENANCE
----------
Chaque enregistrement semé est traçable : lignes ``Provenance``
(``source_name="jeu de démonstration intégré WebIntel"``,
``connector="seed_initial"``), mention « Jeu de démonstration intégré
WebIntel » dans la description de chaque plante, et
``Evidence.provenance`` renseigné pour les preuves.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from webintel.science import service as _service
from webintel.science.models import Evidence, Plant, PlantCompound

__all__ = [
    "charger_donnees_initiales",
    "PLANTES_SEED",
    "COMPOSES_SEED",
    "LIENS_SEED",
    "EVIDENCES_SEED",
    "SOURCE_SEED",
    "VERSION_SEED",
]

#: Source déclarée pour toute ligne de provenance du seed.
SOURCE_SEED = "jeu de démonstration intégré WebIntel"
#: Version du jeu de démarrage (traçabilité des imports).
VERSION_SEED = "seed-1.0"
#: Mention portée par la description de chaque plante semée.
_MENTION_SEED = "Jeu de démonstration intégré WebIntel (donnée de démarrage)."


# ---------------------------------------------------------------------------
# Plantes — identités botaniques de base uniquement (nom, famille,
# répartition large, usages alimentaires / traditionnels rapportés).
# Aucune concentration, posologie ou allégation d'effet.
# ---------------------------------------------------------------------------

PLANTES_SEED: list[dict] = [
    {
        "scientific_name": "Moringa oleifera",
        "family": "Moringaceae",
        "genus": "Moringa",
        "species": "oleifera",
        "vernacular_names": {"fr": "Moringa", "en": "Drumstick tree"},
        "synonyms": ["Moringa pterygosperma"],
        "description": (
            "Arbre tropical à croissance rapide ; feuilles consommées "
            "comme aliment en Afrique de l'Ouest. Des usages "
            "traditionnels sont rapportés dans la littérature "
            "ethnobotanique (USAGE TRADITIONNEL RAPPORTÉ, sans "
            f"allégation d'effet). {_MENTION_SEED}"
        ),
        "distribution": "Afrique subsaharienne, Inde",
        "used_parts": ["feuilles", "graines"],
        "external_ids": {"gbif": "3190532"},
    },
    {
        "scientific_name": "Artemisia annua",
        "family": "Asteraceae",
        "genus": "Artemisia",
        "species": "annua",
        "vernacular_names": {"fr": "Armoise annuelle", "en": "Sweet wormwood"},
        "synonyms": [],
        "description": (
            "Plante herbacée annuelle dont les feuilles contiennent "
            "de l'artémisinine (association plante→composé sourcée "
            f"par publication). {_MENTION_SEED}"
        ),
        "distribution": "Asie tempérée, cultivée en Afrique",
        "used_parts": ["feuilles"],
        "external_ids": {"gbif": "3120649"},
    },
    {
        "scientific_name": "Hibiscus sabdariffa",
        "family": "Malvaceae",
        "genus": "Hibiscus",
        "species": "sabdariffa",
        "vernacular_names": {"fr": "Bissap", "en": "Roselle"},
        "synonyms": [],
        "description": (
            "Plante cultivée pour ses calices rouges, préparés en "
            "infusion : la boisson « bissap », répandue en Afrique "
            f"de l'Ouest. {_MENTION_SEED}"
        ),
        "distribution": "Afrique de l'Ouest, zones tropicales",
        "used_parts": ["calices"],
        "external_ids": {"gbif": "3152703"},
    },
    {
        "scientific_name": "Khaya senegalensis",
        "family": "Meliaceae",
        "genus": "Khaya",
        "species": "senegalensis",
        "vernacular_names": {"fr": "Caïlcédrat"},
        "synonyms": [],
        "description": (
            "Grand arbre des forêts et savanes d'Afrique de l'Ouest, "
            f"connu comme acajou du Sénégal pour son bois. {_MENTION_SEED}"
        ),
        "distribution": "Afrique de l'Ouest tropicale",
        "used_parts": [],
        "external_ids": {},
    },
    {
        "scientific_name": "Parkia biglobosa",
        "family": "Fabaceae",
        "genus": "Parkia",
        "species": "biglobosa",
        "vernacular_names": {"fr": "Néré", "en": "African locust bean"},
        "synonyms": [],
        "description": (
            "Arbre des savanes d'Afrique de l'Ouest ; les graines "
            "fermentées de ses gousses donnent un condiment "
            f"alimentaire (soumbala). {_MENTION_SEED}"
        ),
        "distribution": "Savanes d'Afrique de l'Ouest",
        "used_parts": ["graines"],
        "external_ids": {},
    },
    {
        "scientific_name": "Tamarindus indica",
        "family": "Fabaceae",
        "genus": "Tamarindus",
        "species": "indica",
        "vernacular_names": {"fr": "Tamarinier", "en": "Tamarind"},
        "synonyms": [],
        "description": (
            "Arbre tropical planté pour son ombre et ses fruits ; la "
            "pulpe acidulée du fruit est utilisée en cuisine et en "
            f"boissons. {_MENTION_SEED}"
        ),
        "distribution": "Afrique tropicale, largement planté sous les tropiques",
        "used_parts": ["fruits"],
        "external_ids": {},
    },
    {
        "scientific_name": "Adansonia digitata",
        "family": "Malvaceae",
        "genus": "Adansonia",
        "species": "digitata",
        "vernacular_names": {"fr": "Baobab"},
        "synonyms": [],
        "description": (
            "Arbre emblématique des savanes africaines ; son fruit "
            "(« pain de singe ») et ses feuilles sont consommés "
            f"comme aliments. {_MENTION_SEED}"
        ),
        "distribution": "Savanes d'Afrique tropicale",
        "used_parts": ["fruits", "feuilles"],
        "external_ids": {},
    },
    {
        "scientific_name": "Balanites aegyptiaca",
        "family": "Zygophyllaceae",
        "genus": "Balanites",
        "species": "aegyptiaca",
        "vernacular_names": {"fr": "Dattier du désert", "en": "Desert date"},
        "synonyms": [],
        "description": (
            "Arbre épineux des zones sahéliennes et arides ; ses "
            f"fruits sont consommés localement. {_MENTION_SEED}"
        ),
        "distribution": "Zones sahéliennes et arides d'Afrique",
        "used_parts": ["fruits"],
        "external_ids": {},
    },
    {
        "scientific_name": "Combretum micranthum",
        "family": "Combretaceae",
        "genus": "Combretum",
        "species": "micranthum",
        "vernacular_names": {"fr": "Kinkéliba"},
        "synonyms": [],
        "description": (
            "Arbuste des savanes d'Afrique de l'Ouest. USAGE "
            "TRADITIONNEL RAPPORTÉ : feuilles préparées en tisane, "
            "selon la littérature ethnobotanique (donnée rapportée, "
            f"sans allégation d'effet). {_MENTION_SEED}"
        ),
        "distribution": "Afrique de l'Ouest (savanes et zone sahélienne)",
        "used_parts": ["feuilles"],
        "external_ids": {},
    },
    {
        "scientific_name": "Vernonia amygdalina",
        "family": "Asteraceae",
        "genus": "Vernonia",
        "species": "amygdalina",
        "vernacular_names": {"en": "Bitter leaf"},
        "synonyms": [],
        "description": (
            "Arbuste d'Afrique tropicale ; ses feuilles amères sont "
            "consommées comme légume dans plusieurs cuisines "
            f"d'Afrique de l'Ouest et centrale. {_MENTION_SEED}"
        ),
        "distribution": "Afrique tropicale",
        "used_parts": ["feuilles"],
        "external_ids": {},
    },
    {
        "scientific_name": "Azadirachta indica",
        "family": "Meliaceae",
        "genus": "Azadirachta",
        "species": "indica",
        "vernacular_names": {"fr": "Margousier", "en": "Neem"},
        "synonyms": [],
        "description": (
            "Arbre originaire d'Asie du Sud, largement planté en "
            f"Afrique de l'Ouest pour l'ombrage et l'alignement. {_MENTION_SEED}"
        ),
        "distribution": "Originaire d'Asie du Sud, planté en Afrique de l'Ouest",
        "used_parts": [],
        "external_ids": {},
    },
    {
        "scientific_name": "Carica papaya",
        "family": "Caricaceae",
        "genus": "Carica",
        "species": "papaya",
        "vernacular_names": {"fr": "Papayer", "en": "Papaya"},
        "synonyms": [],
        "description": (
            "Plante fruitière originaire d'Amérique tropicale, "
            "cultivée en Afrique de l'Ouest ; le fruit est consommé "
            f"frais. {_MENTION_SEED}"
        ),
        "distribution": "Originaire d'Amérique tropicale, cultivée en Afrique de l'Ouest",
        "used_parts": ["fruits"],
        "external_ids": {},
    },
    {
        "scientific_name": "Zingiber officinale",
        "family": "Zingiberaceae",
        "genus": "Zingiber",
        "species": "officinale",
        "vernacular_names": {"fr": "Gingembre", "en": "Ginger"},
        "synonyms": [],
        "description": (
            "Plante herbacée cultivée pour son rhizome, utilisé "
            f"comme épice en cuisine. {_MENTION_SEED}"
        ),
        "distribution": "Cultivée sous les tropiques, dont l'Afrique de l'Ouest",
        "used_parts": ["rhizome"],
        "external_ids": {},
    },
]


# ---------------------------------------------------------------------------
# Composés — UNIQUEMENT des structures exactes connues et vérifiées.
# Les 4 premiers reprennent le corpus ``demo/demo_composes.csv`` ;
# le 6-gingérol a été revérifié par RDKit (formule C17H26O4 recalculée
# depuis le SMILES, masse 294,39 g/mol, InChI/InChIKey régénérés).
# SMILES du gingérol volontairement SANS stéréochimie assignée.
# ---------------------------------------------------------------------------

COMPOSES_SEED: list[dict] = [
    {
        "name": "Quercétine",
        "formula": "C15H10O7",
        "molecular_weight": 302.24,
        "smiles": "C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O)O",
        "inchi": "InChI=1S/C15H10O7/c16-8-4-11(19)15-12(20)14(22-13(15)5-8)7-1-2-9(17)10(18)3-7/h1-5,16-19",
        "inchikey": "REFJWTPEDVJJIY-UHFFFAOYSA-N",
        "chemical_class": "Flavonoïde",
        "synonyms": ["Quercetin"],
        "external_ids": {"pubchem": "5280343"},
    },
    {
        "name": "Artémisinine",
        "formula": "C15H22O5",
        "molecular_weight": 282.33,
        "smiles": "C[C@@H]1CC[C@H]2[C@@H](C)C(=O)O[C@@H]3O[C@@]4(C)CC[C@@H]1[C@]32OO4",
        "inchi": "InChI=1S/C15H22O5/c1-8-4-5-11-9(2)12(16)17-13-15(11)10(8)6-7-14(3,18-13)19-20-15/h8-11,13H,4-7H2,1-3H3/t8-,9-,10+,11+,13-,14-,15-/m1/s1",
        "inchikey": "BLUAFEHZUWYNDE-NNWCWBAJSA-N",
        "chemical_class": "Lactone sesquiterpénique",
        "synonyms": ["Artemisinin"],
        "external_ids": {"pubchem": "68827"},
    },
    {
        "name": "Kaempférol",
        "formula": "C15H10O6",
        "molecular_weight": 286.24,
        "smiles": "OC1=CC=C(C=C1)C1=C(O)C(=O)C2=C(O)C=C(O)C=C2O1",
        "inchi": "InChI=1S/C15H10O6/c16-8-3-1-7(2-4-8)15-14(20)13(19)12-10(18)5-9(17)6-11(12)21-15/h1-6,16-18,20H",
        "inchikey": "IYRMWMYZSQPJKC-UHFFFAOYSA-N",
        "chemical_class": "Flavonoïde",
        "synonyms": ["Kaempferol"],
        "external_ids": {"pubchem": "5280863"},
    },
    {
        "name": "Acide chlorogénique",
        "formula": "C16H18O9",
        "molecular_weight": 354.31,
        "smiles": "C1[C@H]([C@H]([C@@H](C[C@@]1(C(=O)O)O)OC(=O)/C=C/C2=CC(=C(C=C2)O)O)O)O",
        "inchi": "InChI=1S/C16H18O9/c17-9-3-1-8(5-10(9)18)2-4-13(20)25-12-7-16(24,15(22)23)6-11(19)14(12)21/h1-5,11-12,14,17-19,21,24H,6-7H2,(H,22,23)/b4-2+/t11-,12-,14-,16+/m1/s1",
        "inchikey": "CWVRJTMFETXNAD-JUHZACGLSA-N",
        "chemical_class": "Acide phénolique",
        "synonyms": ["Chlorogenic acid"],
        "external_ids": {"pubchem": "1794427"},
    },
    {
        "name": "Gingérol",
        "formula": "C17H26O4",
        "molecular_weight": 294.39,
        # 6-gingérol : structure revérifiée par RDKit (voir docstring
        # du module). Aucun identifiant externe deviné : ni PubChem
        # ni autre identifiant n'est renseigné sans certitude.
        "smiles": "CCCCCC(O)CC(=O)CCc1ccc(O)c(OC)c1",
        "inchi": "InChI=1S/C17H26O4/c1-3-4-5-6-14(18)12-15(19)9-7-13-8-10-16(20)17(11-13)21-2/h8,10-11,14,18,20H,3-7,9,12H2,1-2H3",
        "inchikey": "NLDDIKRKFXEWBK-UHFFFAOYSA-N",
        "chemical_class": "Composé phénolique",
        "synonyms": ["6-Gingerol"],
        "external_ids": {},
    },
]


# ---------------------------------------------------------------------------
# Preuves documentaires — reprises du corpus ``demo/demo_evidences.json``
# (DOI uniques : clé naturelle d'idempotence).
# ---------------------------------------------------------------------------

EVIDENCES_SEED: list[dict] = [
    {
        "doi": "10.3390/molecules14062167",
        "source_type": "publication",
        "reference": (
            "Sultana B., Anwar F., Ashraf M. (2009). Effect of extraction "
            "solvent/technique on the antioxidant activity of selected "
            "medicinal plant extracts. Molecules, 14(6), 2167–2180."
        ),
        "title": (
            "Effect of extraction solvent/technique on the antioxidant "
            "activity of selected medicinal plant extracts"
        ),
        "authors": ["Sultana B.", "Anwar F.", "Ashraf M."],
        "year": 2009,
        "pmid": "19553890",
        "excerpt": (
            "Les extraits méthanoliques de Moringa oleifera montrent une "
            "activité antioxydante élevée, corrélée à leur teneur en "
            "flavonoïdes (quercétine)."
        ),
    },
    {
        "doi": "10.1016/j.phytochem.2019.03.001",
        "source_type": "publication",
        "reference": (
            "Zhang Y. et al. (2019). Artemisinin content variation in "
            "Artemisia annua. Phytochemistry, 160, 1–8."
        ),
        "title": "Artemisinin content variation in Artemisia annua",
        "authors": ["Zhang Y.", "Li X."],
        "year": 2019,
        "pmid": None,
        "excerpt": (
            "La teneur en artémisinine des feuilles d'Artemisia annua "
            "varie selon le stade de récolte ; les valeurs rapportées "
            "restent des mesures publiées, à confirmer par des essais "
            "indépendants."
        ),
    },
]


# ---------------------------------------------------------------------------
# Liens plante → composé — associations très connues et publiées
# uniquement. AUCUNE concentration (non revérifiée : jamais semée).
# « evidence_doi » pointe vers la preuve de EVIDENCES_SEED quand elle
# existe ; le lien Hibiscus reprend le corpus démo (confiance
# « rapporte », sans ligne Evidence dédiée dans le corpus) et le lien
# Zingiber est une association rapportée sans publication semée
# (aucune référence inventée).
# ---------------------------------------------------------------------------

LIENS_SEED: list[dict] = [
    {
        "plant": "Moringa oleifera",
        "compound": "Quercétine",
        "plant_part": "feuilles",
        "association_type": "présence",
        "publication": "Sultana et al., Molecules",
        "doi": "10.3390/molecules14062167",
        "year": 2009,
        "confidence": "publie",
        "evidence_doi": "10.3390/molecules14062167",
    },
    {
        "plant": "Artemisia annua",
        "compound": "Artémisinine",
        "plant_part": "feuilles",
        "association_type": "présence",
        "publication": "Zhang et al., Phytochemistry",
        "doi": "10.1016/j.phytochem.2019.03.001",
        "year": 2019,
        "confidence": "publie",
        "evidence_doi": "10.1016/j.phytochem.2019.03.001",
    },
    {
        "plant": "Hibiscus sabdariffa",
        "compound": "Kaempférol",
        "plant_part": "calices",
        "association_type": "présence",
        "publication": "Da-Costa et al., J. Food Sci.",
        "doi": "10.1111/j.1750-3841.2010.01599.x",
        "year": 2010,
        "confidence": "rapporte",
        "evidence_doi": None,
    },
    {
        "plant": "Zingiber officinale",
        "compound": "Gingérol",
        "plant_part": "rhizome",
        "association_type": "présence",
        "publication": None,
        "doi": None,
        "year": None,
        "confidence": "rapporte",
        "evidence_doi": None,
    },
]


# ---------------------------------------------------------------------------
# Chargement idempotent
# ---------------------------------------------------------------------------


def _provenance(session: Session, entity_type: str, entity_id: int) -> bool:
    """Enregistre la provenance « seed » d'une entité. Retourne True si créée."""
    _, creee = _service.record_provenance(
        session,
        entity_type,
        entity_id,
        source_name=SOURCE_SEED,
        connector="seed_initial",
        data_version=VERSION_SEED,
        niveau_preuve="donnée de démonstration (à vérifier par le chercheur)",
    )
    return creee


def charger_donnees_initiales(session: Session) -> dict[str, int]:
    """Charge le jeu de démarrage WebIntel (idempotent, sans commit).

    Crée — uniquement s'ils n'existent pas déjà — les plantes,
    composés, preuves, liens plante→composé et lignes de provenance
    des constantes ``*_SEED`` de ce module, via les fonctions
    idempotentes de ``webintel.science.service`` (clés naturelles :
    ``Plant.scientific_name``, ``Compound.name``/``inchikey``,
    contrainte unique ``PlantCompound``, ``Evidence.doi``).

    Un second appel ne crée AUCUN doublon et retourne des compteurs à
    zéro. Ne committe jamais (convention du module scientifique) :
    l'appelant committe.

    Retourne les compteurs d'objets CRÉÉS par ce appel, par table :
    ``{"plants": n, "compounds": n, "evidences": n,
    "plant_compounds": n, "provenances": n}``.
    """
    compteurs = {
        "plants": 0,
        "compounds": 0,
        "evidences": 0,
        "plant_compounds": 0,
        "provenances": 0,
    }

    # --- Preuves d'abord : les liens pointent vers elles par DOI. --------
    preuves_par_doi: dict[str, Evidence] = {}
    for donnees in EVIDENCES_SEED:
        preuve, creee = _service.add_evidence(
            session,
            doi=donnees["doi"],
            source_type=donnees["source_type"],
            reference=donnees["reference"],
            title=donnees["title"],
            authors=list(donnees["authors"]),
            year=donnees["year"],
            pmid=donnees["pmid"],
            excerpt=donnees["excerpt"],
            provenance={
                "origine": SOURCE_SEED,
                "version": VERSION_SEED,
                "data_level": "publie",
            },
        )
        preuves_par_doi[donnees["doi"]] = preuve
        if creee:
            compteurs["evidences"] += 1
        if _provenance(session, "evidence", preuve.id):
            compteurs["provenances"] += 1

    # --- Plantes ----------------------------------------------------------
    plantes_par_nom: dict[str, Plant] = {}
    for donnees in PLANTES_SEED:
        plante, creee = _service.create_plant(
            session,
            donnees["scientific_name"],
            family=donnees["family"],
            genus=donnees["genus"],
            species=donnees["species"],
            synonyms=list(donnees["synonyms"]),
            vernacular_names=dict(donnees["vernacular_names"]),
            description=donnees["description"],
            distribution=donnees["distribution"],
            used_parts=list(donnees["used_parts"]),
            external_ids=dict(donnees["external_ids"]),
        )
        plantes_par_nom[donnees["scientific_name"]] = plante
        if creee:
            compteurs["plants"] += 1
        if _provenance(session, "plant", plante.id):
            compteurs["provenances"] += 1

    # --- Composés ---------------------------------------------------------
    composes_par_nom = {}
    for donnees in COMPOSES_SEED:
        compose, cree = _service.create_compound(
            session,
            donnees["name"],
            formula=donnees["formula"],
            molecular_weight=donnees["molecular_weight"],
            smiles=donnees["smiles"],
            inchi=donnees["inchi"],
            inchikey=donnees["inchikey"],
            chemical_class=donnees["chemical_class"],
            synonyms=list(donnees["synonyms"]),
            external_ids=dict(donnees["external_ids"]),
        )
        composes_par_nom[donnees["name"]] = compose
        if cree:
            compteurs["compounds"] += 1
        if _provenance(session, "compound", compose.id):
            compteurs["provenances"] += 1

    # --- Liens plante → composé ------------------------------------------
    for donnees in LIENS_SEED:
        plante = plantes_par_nom[donnees["plant"]]
        compose = composes_par_nom[donnees["compound"]]
        preuve = preuves_par_doi.get(donnees.get("evidence_doi") or "")
        lien, cree = _service.link_plant_compound(
            session,
            plante.id,
            compose.id,
            plant_part=donnees["plant_part"],
            publication=donnees["publication"],
            association_type=donnees["association_type"],
            doi=donnees["doi"],
            year=donnees["year"],
            confidence=donnees["confidence"],
            evidence_id=preuve.id if preuve is not None else None,
        )
        if not cree and preuve is not None and lien.evidence_id is None:
            # Lien préexistant sans preuve pointée : on complète la
            # traçabilité sans rien écraser (jamais de doublon).
            lien.evidence_id = preuve.id
        if cree:
            compteurs["plant_compounds"] += 1
        if _provenance(session, "plant_compound", lien.id):
            compteurs["provenances"] += 1

    session.flush()
    return compteurs
