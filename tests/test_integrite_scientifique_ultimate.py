"""Tests d'intégrité scientifique — correctifs « WEBINTEL ULTIMATE ».

Règle absolue : jamais simulation→preuve, jamais de données / scores /
publications inventés ; les formulations « efficace », « sûr », « traite »,
« guérit » sont interdites dans tout texte généré sans source expérimentale.

Couverture :
 1. les 3 molécules Moringa de la démo sont réelles et valides RDKit
    (SMILES parse, formule et InChIKey recalculés = déclarés, CID réels) ;
 2. la démo Moringa lie effectivement 3 molécules structurées ;
 3. badge de saisie manuelle = « normalise », jamais « publie » par défaut ;
 4. section vide = « aucune donnée », sans badge ;
 5. score de synergie = « Résultat de calcul (synergie) », niveau
    prédiction, nature « calculé », limites visibles ;
 6. le garde-fou bloque les formulations interdites dans le texte généré ;
 7. le garde-fou NE bloque PAS ces mots dans une citation sourcée ;
 8. la synthèse IA conserve une citation sourcée sans l'affirmer ;
 9. fiche / rapport / export ne contiennent pas les formulations bannies ;
10. Vina absent → aucun formulaire de calcul (+ message + bouton outils) ;
11. Vina présent → formulaire affiché, paramètres alignés sur le moteur ;
12. défaut de similarité = « structure » (plugin, workflow, UI) ;
13. « combinee » étiquetée « heuristique non structurelle ».
"""

from __future__ import annotations

import csv
import io
import os

import pytest

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEMO = os.path.join(_RACINE, "demo")

# ---------------------------------------------------------------------------
# 1. Les 3 molécules Moringa : réelles, valides RDKit
# ---------------------------------------------------------------------------

TRIO_MORINGA_ATTENDU = {
    # nom: (formule, inchikey, pubchem_cid)
    "Quercétine": ("C15H10O7", "REFJWTPEDVJJIY-UHFFFAOYSA-N", "5280343"),
    "Kaempférol": ("C15H10O6", "IYRMWMYZSQPJKC-UHFFFAOYSA-N", "5280863"),
    "Acide chlorogénique": (
        "C16H18O9", "CWVRJTMFETXNAD-JUHZACGLSA-N", "1794427",
    ),
}


def _lire_csv_composes() -> dict[str, dict]:
    chemin = os.path.join(_DEMO, "demo_composes.csv")
    with open(chemin, encoding="utf-8") as fh:
        lignes = list(csv.DictReader(fh, delimiter=";"))
    return {l["nom"]: l for l in lignes}


def test_trio_moringa_valide_rdkit():
    """Chaque molécule : SMILES parse, formule/InChIKey recalculés = déclarés."""
    pytest.importorskip("rdkit")
    from rdkit import Chem
    from rdkit.Chem import rdMolDescriptors

    composes = _lire_csv_composes()
    for nom, (formule, inchikey, cid) in TRIO_MORINGA_ATTENDU.items():
        ligne = composes.get(nom)
        assert ligne is not None, f"{nom} absent de demo_composes.csv"
        mol = Chem.MolFromSmiles(ligne["smiles"])
        assert mol is not None, f"{nom} : SMILES invalide"
        assert rdMolDescriptors.CalcMolFormula(mol) == ligne["formule"] == formule
        assert Chem.MolToInchiKey(mol) == ligne["inchikey"] == inchikey
        # CID non fictif : identifiant numérique PubChem attendu.
        assert cid in (ligne["ids_externes"] or ""), (
            f"{nom} : CID PubChem {cid} absent"
        )


# ---------------------------------------------------------------------------
# 2. La démo Moringa : 3 molécules structurées réellement liées
# ---------------------------------------------------------------------------

def test_demo_moringa_trois_molecules_structurees():
    """Import CSV + liens démo → 3 composés Moringa tous structurés (RDKit)."""
    pytest.importorskip("rdkit")
    from rdkit import Chem
    from rdkit.Chem import rdMolDescriptors

    from webintel.database.base import SessionLocal
    from webintel.science import import_export, service
    from webintel.science.models import Compound, Plant, PlantCompound

    def _lire(nom):
        with open(os.path.join(_DEMO, nom), "rb") as fh:
            return fh.read()

    session = SessionLocal()
    try:
        for nom_fichier, type_donnees in [
            ("demo_plantes.csv", "plante"),
            ("demo_composes.csv", "compose"),
            ("demo_liens.csv", "lien"),
        ]:
            rapport = import_export.importer_fichier(
                session, _lire(nom_fichier), nom_fichier, type_donnees
            )
            assert not rapport["erreurs"], rapport["erreurs"]
        moringa = (
            session.query(Plant)
            .filter(Plant.scientific_name == "Moringa oleifera")
            .one()
        )
        # Comme le script démo : les 2 liens d'illustration (sans DOI inventé).
        for nom in ["Kaempférol", "Acide chlorogénique"]:
            compose = session.query(Compound).filter_by(name=nom).one()
            service.link_plant_compound(
                session, moringa.id, compose.id,
                plant_part="feuilles", confidence="normalise",
            )
        session.commit()

        lies = (
            session.query(Compound)
            .join(PlantCompound, PlantCompound.compound_id == Compound.id)
            .filter(PlantCompound.plant_id == moringa.id)
            .all()
        )
        noms = sorted(c.name for c in lies)
        assert noms == sorted(TRIO_MORINGA_ATTENDU), noms
        for compose in lies:
            assert compose.smiles, f"{compose.name} : sans SMILES"
            mol = Chem.MolFromSmiles(compose.smiles)
            assert mol is not None, f"{compose.name} : SMILES invalide"
            assert rdMolDescriptors.CalcMolFormula(mol) == compose.formula
            assert Chem.MolToInchiKey(mol) == compose.inchikey
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 3-4. Badges de preuve dynamiques
# ---------------------------------------------------------------------------

def _plante_vide(session, nom="Planta fictiva"):
    from webintel.science.models import Plant
    plante = Plant(scientific_name=nom)
    session.add(plante)
    session.commit()
    return plante


def test_badge_saisie_manuelle_normalise_jamais_publie():
    """Saisie manuelle → « normalise » ; sans provenance → « normalise »."""
    from webintel.database.base import SessionLocal
    from webintel.science.fiche import niveau_preuve_plante
    from webintel.science.models import Provenance

    session = SessionLocal()
    try:
        # Aucune provenance connue : jamais « publiée » par défaut.
        plante = _plante_vide(session, "Planta sine nota")
        badge = niveau_preuve_plante(session, plante)
        assert badge["niveau"] == "normalise", badge
        assert "publie" not in badge["label"].lower()

        # Saisie manuelle UI : « normalise », jamais « publiée ».
        session.add(Provenance(
            entity_type="plant", entity_id=plante.id,
            connector="ui-manuel", source_name="saisie manuelle",
        ))
        session.commit()
        badge = niveau_preuve_plante(session, plante)
        assert badge["niveau"] == "normalise", badge
        assert "manuelle" in badge["label"].lower()
    finally:
        session.close()


def test_section_vide_aucun_badge():
    """Section vide → aucun badge (None), jamais un niveau prétendu."""
    from webintel.database.base import SessionLocal
    from webintel.ui.chercheur_accueil_routes import _fiche_plante_resume

    session = SessionLocal()
    try:
        plante = _plante_vide(session, "Planta vacua")
        fiche = _fiche_plante_resume(session, plante)
        badges = fiche["badges"]
        # Identité/botanique : niveau réel minimal (normalisé), pas « publié ».
        assert badges["identite"]["niveau"] == "normalise"
        assert badges["botanique"]["niveau"] == "normalise"
        # Sections vides : aucun badge.
        assert badges["phytochimie"] is None
        assert badges["pharmacologie"] is None
        assert badges["publications"] is None
        assert badges["securite"] is None
        assert badges["simulations"] is None
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 5. Synergie = résultat de calcul, pas mesure expérimentale
# ---------------------------------------------------------------------------

def _etat_synergie():
    return {
        "donnees_exp": {
            "methode": "bliss",
            "points": [
                {"dose_a": 10, "dose_b": 20, "reponse_a": 0.5,
                 "reponse_b": 0.5, "reponse_ab": 0.9},
            ],
        }
    }


def test_synergie_resultat_de_calcul():
    """Le score calculé n'est jamais étiqueté « Résultat expérimental »."""
    from webintel.ui.chercheur_etudes_routes import _lancer_experimentales

    bloc = _lancer_experimentales(_etat_synergie())
    assert bloc["statut"] == "ok"
    assert bloc["niveau"] == "prediction", bloc["niveau"]
    assert "Résultat de calcul (synergie)" in bloc["resume"]
    assert "Résultat expérimental" not in bloc["resume"]
    assert bloc.get("nature") == "calculé"
    assert bloc.get("limites"), "les limites du calcul doivent être visibles"


# ---------------------------------------------------------------------------
# 6-7. Garde-fou : bloque le généré, épargne le sourcé
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("formulation", [
    "ce mélange est efficace",
    "l'efficacité du traitement",
    "ce produit est sûr",
    "cette plante traite le paludisme",
    "ce remède guérit",
    "cette décoction soigne",
])
def test_garde_fou_bloque_texte_genere(formulation):
    """Toute formulation interdite dans un texte généré lève une erreur."""
    from webintel.science.honnetete import _exiger_honnete

    with pytest.raises(RuntimeError):
        _exiger_honnete(
            f"Conclusion de l'étude : {formulation}.", "test"
        )


def test_garde_fou_ne_bloque_pas_citation_sourcee():
    """Une publication sourcée contenant ces mots reste affichable."""
    from webintel.science.honnetete import exiger_dossier_honnete

    dossier = {
        "titre": "Étude sur Artemisia",
        "reference": "Dupont et al. (2021). J. Ethnopharmacol.",
        "extrait": (
            "L'extrait traite de l'activité observée ; les auteurs jugent "
            "l'effet efficace dans leur modèle — citation verbatim."
        ),
        "synthese_generee": (
            "Résultat informatique : similarité calculée entre deux "
            "molécules ; hypothèse à vérifier expérimentalement."
        ),
    }
    # Ne lève pas : les verbatims sourcés ne sont jamais bloqués.
    exiger_dossier_honnete(dossier, "test")


def test_garde_fou_bloque_meme_dans_dossier_mixte():
    """Le même mot dans un champ GÉNÉRÉ du dossier reste bloqué."""
    from webintel.science.honnetete import exiger_dossier_honnete

    dossier = {
        "reference": "Dupont et al. (2021)",
        "conclusion_generee": "cette association est efficace",
    }
    with pytest.raises(RuntimeError):
        exiger_dossier_honnete(dossier, "test")


# ---------------------------------------------------------------------------
# 8. IA : citation sourcée conservée, jamais affirmée par WebIntel
# ---------------------------------------------------------------------------

def test_ia_conserve_citation_sourcee_sans_l_affirmer():
    """Un extrait sourcé contenant un mot banni ne bloque pas la synthèse."""
    from webintel.database.base import SessionLocal
    from webintel.science import ia as ia_module
    from webintel.science import service

    session = SessionLocal()
    try:
        plante, _ = service.create_plant(session, "Artemisia testia")
        compose, _ = service.create_compound(session, "Testine")
        doi = "10.1234/test.integration"
        service.add_evidence(
            session, doi=doi, title="Étude de test",
            reference="Test et al. (2020)",
            excerpt=(
                "Les auteurs estiment l'extrait efficace dans leur modèle ; "
                "l'article traite aussi de la toxicité."
            ),
        )
        service.link_plant_compound(
            session, plante.id, compose.id,
            publication="Test et al. (2020)", doi=doi,
        )
        session.commit()
        synthese = ia_module.synthese_plante(session, plante.id)
        # La synthèse est produite (pas de blocage) et étiquetée hypothèse.
        assert synthese["niveau_donnees"] == "hypothese"
        # La citation est conservée dans les sources, avec son DOI.
        assert any(s.get("doi") == doi for s in synthese["sources"])
        # Le texte généré autour ne présente pas cela comme un fait WebIntel.
        assert "HYPOTHÈSE" in synthese["avertissement"]
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 9. Fiche / rapport / exports : aucun texte généré banni
# ---------------------------------------------------------------------------

def test_fiche_et_exports_sans_formulation_bannie():
    """La fiche complète et ses exports passent le garde-fou."""
    from webintel.database.base import SessionLocal
    from webintel.science import fiche as fiche_module
    from webintel.science import service
    from webintel.science.export_dossier import (
        build_fiche_pdf, render_fiche_html,
    )
    from webintel.science.honnetete import exiger_dossier_honnete

    session = SessionLocal()
    try:
        plante, _ = service.create_plant(session, "Moringa testia")
        compose, _ = service.create_compound(
            session, "Quercétine",
            formula="C15H10O7", smiles="C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O",
            inchikey="REFJWTPEDVJJIY-UHFFFAOYSA-N",
        )
        doi = "10.1234/test.fiche"
        service.add_evidence(
            session, doi=doi, title="Étude source",
            reference="Source et al. (2022)",
            # Verbatim sourcé avec mots bannis : ne doit PAS bloquer.
            excerpt="Les auteurs décrivent un effet qu'ils jugent efficace.",
        )
        service.link_plant_compound(
            session, plante.id, compose.id,
            publication="Source et al. (2022)", doi=doi,
        )
        session.commit()

        fiche = fiche_module.build_plant_fiche(session, plante.id)
        exiger_dossier_honnete(fiche, "fiche")  # ne lève pas
        # Les exports HTML/PDF appliquent le même auto-contrôle (ne lèvent pas).
        html = render_fiche_html(session, plante.id)
        assert "Moringa testia" in html
        pdf = build_fiche_pdf(session, plante.id)
        if pdf is not None:
            assert pdf.startswith(b"%PDF")
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 10-11. Docking : formulaire aligné sur la présence réelle de Vina
# ---------------------------------------------------------------------------

def test_docking_sans_vina_aucun_formulaire(client):
    """Sans moteur : aucun formulaire de calcul, message + bouton outils."""
    reponse = client.get("/docking")
    assert reponse.status_code == 200
    texte = reponse.text
    assert 'name="recepteur_pdb"' not in texte
    assert 'name="ligand_smiles"' not in texte
    assert "outils-scientifiques" in texte


def test_docking_avec_vina_formulaire_coherent(client, monkeypatch):
    """Moteur présent (mocké) : formulaire affiché, paramètres réels."""
    from webintel.science import docking as dock_module

    monkeypatch.setattr(dock_module, "statut_moteur", lambda: (True, "mock"))
    reponse = client.get("/docking")
    assert reponse.status_code == 200
    texte = reponse.text
    # Champs strictement alignés sur _valider_parametres (docking.py) :
    # centre (cx/cy/cz), taille 5–60, exhaustiveness 1–32, poses 1–20.
    for champ in ("recepteur_pdb", "ligand_smiles", "cx", "cy", "cz",
                  "taille", "exhaustiveness", "nb_poses"):
        assert f'name="{champ}"' in texte, champ
    # Le récepteur PDBQT préparé est expliqué honnêtement.
    assert "PDBQT" in texte


def test_docking_post_execution_sans_vina_refuse(client):
    """POST direct d'exécution sans moteur : refus propre, aucun score."""
    pdb = ("HEADER\nATOM      1  N   ALA A   1      11.104  13.207   2.100 "
           " 1.00 20.00           N\nEND")
    reponse = client.post("/docking", data={
        "action": "executer", "nom": "D-refuse",
        "recepteur_pdb": pdb, "ligand_smiles": "CCO"})
    assert reponse.status_code == 200
    assert "aucun score" in reponse.text.lower()


# ---------------------------------------------------------------------------
# 12-13. Similarité : défaut « structure », « combinee » = heuristique
# ---------------------------------------------------------------------------

def test_defaut_similarite_structure_plugin():
    """Le plugin molecular_similarity a « structure » par défaut."""
    from webintel.simulations.plugins import molecular_similarity

    assert molecular_similarity.PARAMETERS["methode"]["default"] == "structure"
    entrees = {"composes": [
        {"nom": "A", "formule": "C6H12O6", "smiles": "C(C1C(C(C(C(O1)O)O)O)O)O"},
        {"nom": "B", "formule": "C6H12O6", "smiles": "C1=CC=CC=C1"},
    ]}
    resultat = molecular_similarity.run(entrees, {})
    assert resultat["methode"] == "structure"


def test_defaut_similarite_structure_workflow():
    """Le workflow analyse_plante compose le moteur en méthode « structure »."""
    from webintel.science.workflows import _compose_moteur

    _, params = _compose_moteur(
        "molecular_similarity",
        {"nom": "A", "formule": "C6H12O6"},
        [{"nom": "B", "formule": "H2O"}],
    )
    assert params == {"methode": "structure"}


def test_defaut_similarite_structure_ui():
    """L'assistant d'étude utilise « structure » quand aucun choix n'est fait."""
    from webintel.database.base import SessionLocal
    from webintel.ui.chercheur_etudes_routes import (
        _lancer_structures, METHODES_SIMILARITE,
    )

    assert METHODES_SIMILARITE[0] == "structure"
    session = SessionLocal()
    try:
        etat = {
            "molecules": [
                {"nom": "A", "smiles": "CCO"},
                {"nom": "B", "smiles": "c1ccccc1"},
            ],
            "params": {},
        }
        bloc = _lancer_structures(etat, session)
        assert bloc["statut"] == "ok", bloc.get("note")
        assert bloc["provenance"]["parametres"]["methode"] == "structure"
    finally:
        session.close()


def test_combinee_etiquettee_heuristique_non_structurelle():
    """« combinee » reste disponible mais explicitement non structurelle."""
    from webintel.simulations.plugins import molecular_similarity

    description = molecular_similarity.PARAMETERS["methode"]["description"]
    assert "combinee" in molecular_similarity.PARAMETERS["methode"]["choices"]
    assert "heuristique non structurelle" in description.lower()
