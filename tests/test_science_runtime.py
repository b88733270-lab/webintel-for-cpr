"""Tests du runtime scientifique optionnel — v2.1 (CORE vs SCIENCE).

Couvre : le contrat de ``webintel/science/chimie.py`` (avec et sans RDKit),
l'enrichissement RDKit de ``molecular_similarity`` et ``property_prediction``,
la détection réelle + l'exécution réelle (mockée de façon contrôlée) de
``docking_adapter``, et le bloc « Outils scientifiques » de la page Paramètres.

Règle dure : aucune valeur inventée — les champs non calculables valent
None / "non_calcule" avec un message explicite.
"""

import logging
import re
import shutil
import subprocess

import pytest

from webintel.science import chimie
from webintel.simulations.plugins import check_plugin
from webintel.simulations.plugins import docking_adapter as dock
from webintel.simulations.plugins import molecular_similarity as sim
from webintel.simulations.plugins import property_prediction as prop

ASPIRINE = "CC(=O)Oc1ccccc1C(=O)O"
ACIDE_SALICYLIQUE = "O=C(O)c1ccccc1O"

RDKit_PRESENT = chimie.rdkit_disponible()
necessite_rdkit = pytest.mark.skipif(
    not RDKit_PRESENT, reason="RDKit absent — niveau SCIENCE non installé"
)

MEEKO_PRESENT = dock.meeko_disponible()
necessite_meeko = pytest.mark.skipif(
    not MEEKO_PRESENT,
    reason="meeko absent — préparation SMILES → PDBQT indisponible "
           "(fournissez un ligand PDBQT déjà préparé)",
)


# ===========================================================================
# 1. Module webintel/science/chimie.py — contrat exact
# ===========================================================================

def test_chimie_infos_biblio_structure():
    infos = chimie.infos_biblio()
    assert infos["bibliotheque"] == "RDKit"
    assert isinstance(infos["disponible"], bool)
    assert infos["disponible"] == RDKit_PRESENT
    if RDKit_PRESENT:
        assert isinstance(infos["version"], str) and infos["version"]
        assert infos["raison"] == ""
    else:
        assert infos["version"] is None
        assert "pip install" in infos["raison"] or "SCIENCE" in infos["raison"]


@necessite_rdkit
def test_chimie_empreinte_morgan_valide():
    fp = chimie.empreinte_morgan(ASPIRINE)
    assert isinstance(fp, list) and len(fp) == 2048
    assert set(fp) <= {0, 1} and sum(fp) > 0
    # Déterminisme.
    assert chimie.empreinte_morgan(ASPIRINE) == fp
    # Paramètres personnalisés.
    fp2 = chimie.empreinte_morgan(ASPIRINE, rayon=3, taille=1024)
    assert len(fp2) == 1024


@necessite_rdkit
def test_chimie_empreinte_morgan_entrees_invalides(caplog):
    with caplog.at_level(logging.WARNING, logger="webintel.science.chimie"):
        assert chimie.empreinte_morgan("") is None
        assert chimie.empreinte_morgan("   ") is None
        assert chimie.empreinte_morgan("XYZ[[[invalide") is None
        assert chimie.empreinte_morgan(123) is None  # type: ignore[arg-type]
        assert chimie.empreinte_morgan(None) is None  # type: ignore[arg-type]
        assert chimie.empreinte_morgan(ASPIRINE, rayon=99) is None
        assert chimie.empreinte_morgan(ASPIRINE, taille=16) is None
    # Raison explicite journalisée pour chaque refus.
    assert "SMILES invalide" in caplog.text or "invalide" in caplog.text


def test_chimie_tanimoto_fp_pur_python():
    """Tanimoto arithmétique : fonctionne même sans RDKit (pas de chimie)."""
    fp_a = [1, 0, 1, 1, 0]
    assert chimie.tanimoto_fp(fp_a, fp_a) == pytest.approx(1.0)
    assert chimie.tanimoto_fp([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)
    assert chimie.tanimoto_fp([1, 1, 0], [1, 0, 0]) == pytest.approx(0.5)


def test_chimie_tanimoto_fp_invalide():
    assert chimie.tanimoto_fp([], []) is None
    assert chimie.tanimoto_fp([1, 0], [1, 0, 1]) is None
    assert chimie.tanimoto_fp([1, 2], [1, 0]) is None
    assert chimie.tanimoto_fp("101", "101") is None  # type: ignore[arg-type]
    assert chimie.tanimoto_fp([0, 0], [0, 0]) is None  # union vide : non défini


def test_logging_warnings_capturables_via_root_apres_import_app():
    """Régression fiabilisation v2.1.x : les avertissements scientifiques
    doivent atteindre un gestionnaire attaché au logger RACINE même quand
    l'application a déjà été importée (l'ancien `propagate = False` sur
    le logger "webintel" coupait la capture `caplog` selon l'ordre des
    imports — les warnings étaient émis mais jamais récupérés)."""
    import webintel.main  # noqa: F401 — garantit l'import « tardif »

    recus = []

    class Capteur(logging.Handler):
        def emit(self, record):
            recus.append(record.getMessage())

    capteur = Capteur()
    capteur.setLevel(logging.WARNING)
    racine = logging.getLogger()
    niveau_avant = racine.level
    racine.addHandler(capteur)
    try:
        racine.setLevel(logging.WARNING)
        # Paramètre invalide (indépendant de RDKit) : le warning doit être
        # émis et atteindre le logger racine.
        assert chimie.empreinte_morgan("CCO", rayon=99) is None
    finally:
        racine.removeHandler(capteur)
        racine.setLevel(niveau_avant)
    assert recus, "aucun avertissement scientifique n'a atteint le logger racine"
    assert any("invalide" in message for message in recus)
    assert any("invalide" in message for message in recus)


@necessite_rdkit
def test_chimie_tanimoto_fp_reel_rdkit():
    fp_a = chimie.empreinte_morgan(ASPIRINE)
    fp_s = chimie.empreinte_morgan(ACIDE_SALICYLIQUE)
    fp_b = chimie.empreinte_morgan("c1ccccc1")
    t_as = chimie.tanimoto_fp(fp_a, fp_s)
    t_ab = chimie.tanimoto_fp(fp_a, fp_b)
    assert 0.0 < t_as < 1.0 and 0.0 < t_ab < 1.0
    assert t_as > t_ab  # l'acide salicylique ressemble plus à l'aspirine


@necessite_rdkit
def test_chimie_descripteurs_aspirine():
    d = chimie.descripteurs(ASPIRINE)
    assert d is not None
    assert set(d) == {
        "formule", "masse_molaire", "logp", "hbd", "hba", "tpsa",
        "rotatable_bonds", "fraction_csp3", "lipinski_violations",
        "inchi_key", "smiles_canonique",
    }
    assert d["formule"] == "C9H8O4"
    assert d["masse_molaire"] == pytest.approx(180.159, abs=0.01)
    assert d["logp"] == pytest.approx(1.31, abs=0.05)
    assert d["hbd"] == 1
    assert isinstance(d["hba"], int) and d["hba"] >= 1
    assert d["tpsa"] == pytest.approx(63.6, abs=1.0)
    assert isinstance(d["rotatable_bonds"], int)
    assert 0.0 <= d["fraction_csp3"] <= 1.0
    assert d["lipinski_violations"] == 0
    assert re.fullmatch(r"[A-Z0-9]{14}-[A-Z0-9]{10}-[A-Z]", d["inchi_key"])
    assert isinstance(d["smiles_canonique"], str) and d["smiles_canonique"]


@necessite_rdkit
def test_chimie_descripteurs_invalide():
    assert chimie.descripteurs("") is None
    assert chimie.descripteurs("pas un smiles [[") is None
    assert chimie.descripteurs(42) is None  # type: ignore[arg-type]


@necessite_rdkit
def test_chimie_sous_structure():
    assert chimie.sous_structure(ASPIRINE, "c1ccccc1") is True
    assert chimie.sous_structure(ASPIRINE, "N") is False
    assert chimie.sous_structure("", "c1ccccc1") is None
    assert chimie.sous_structure(ASPIRINE, "") is None
    assert chimie.sous_structure(ASPIRINE, "[[[") is None
    assert chimie.sous_structure("XYZ[[", "c1ccccc1") is None


@necessite_rdkit
def test_chimie_diagnostic_smiles():
    ok = chimie.diagnostic_smiles(ASPIRINE)
    assert ok["valide"] is True and ok["raison"] is None
    assert ok["rdkit_disponible"] is True
    ko = chimie.diagnostic_smiles("[[[")
    assert ko["valide"] is False and isinstance(ko["raison"], str)


# --- Sans RDKit : None + raison explicite, jamais d'exception -----------

@pytest.fixture
def sans_rdkit(monkeypatch):
    monkeypatch.setattr(chimie, "_RDKIT_DISPONIBLE", False)
    monkeypatch.setattr(chimie, "_RDKIT_VERSION", None)
    return chimie


def test_chimie_sans_rdkit_aucune_valeur_inventee(sans_rdkit, caplog):
    assert sans_rdkit.rdkit_disponible() is False
    infos = sans_rdkit.infos_biblio()
    assert infos["disponible"] is False and infos["version"] is None
    assert infos["raison"]  # raison explicite
    with caplog.at_level(logging.WARNING, logger="webintel.science.chimie"):
        assert sans_rdkit.empreinte_morgan(ASPIRINE) is None
        assert sans_rdkit.descripteurs(ASPIRINE) is None
        assert sans_rdkit.sous_structure(ASPIRINE, "c1ccccc1") is None
    assert "RDKit" in caplog.text


def test_chimie_tanimoto_fonctionne_sans_rdkit(sans_rdkit):
    """Le Tanimoto arithmétique ne dépend pas de RDKit : résultat réel."""
    assert sans_rdkit.tanimoto_fp([1, 1, 0], [1, 1, 0]) == pytest.approx(1.0)


# ===========================================================================
# 2. molecular_similarity — similarité structurelle réelle
# ===========================================================================

def _entrees_structurelles():
    return {
        "composes": [
            {"id": "aspirine", "formule": "C9H8O4", "smiles": ASPIRINE},
            {"id": "acide_salicylique", "formule": "C7H6O3", "smiles": ACIDE_SALICYLIQUE},
            {"id": "cyclohexane", "formule": "C6H12", "smiles": "C1CCCCC1"},
        ]
    }


def _paire(resultat, a, b):
    for p in resultat["paires"]:
        if {p["a"], p["b"]} == {a, b}:
            return p
    raise AssertionError(f"Paire {(a, b)} introuvable")


@necessite_rdkit
def test_similarite_structurelle_reelle_morgan():
    resultat = sim.run(_entrees_structurelles(), {"methode": "structure"})
    paire = _paire(resultat, "aspirine", "acide_salicylique")
    bloc = paire["details"]["structure_similaire"]
    assert bloc["statut"].startswith("évalué")
    assert "Morgan" in bloc["methode"] and "RDKit" in bloc["methode"]
    assert 0.0 < bloc["score"] < 1.0
    assert paire["score"] == pytest.approx(bloc["score"])
    # Distinction explicite des trois natures.
    details = paire["details"]
    assert "formule_similaire" in details
    assert "structure_similaire" in details
    assert "classe_chimique_similaire" in details
    # Une molécule est identique à elle-même structurellement.
    paire2 = _paire(
        sim.run({"composes": [
            {"id": "x", "formule": "C9H8O4", "smiles": ASPIRINE},
            {"id": "y", "formule": "C9H8O4", "smiles": ASPIRINE},
        ]}, {"methode": "structure"}), "x", "y")
    assert paire2["details"]["structure_similaire"]["score"] == pytest.approx(1.0)


@necessite_rdkit
def test_similarite_distinction_pharmacologique_obligatoire():
    resultat = sim.run(_entrees_structurelles(), {})
    assert "pharmacologique" in resultat["avertissement"]
    assert "formule_similaire" in resultat["avertissement"]
    d = resultat["distinction_sorties"]
    assert "N'est PAS une similarité pharmacologique" in d["formule_similaire"]
    assert "N'est PAS une similarité pharmacologique" in d["classe_chimique_similaire"]
    assert "pas l'activité biologique" in d["structure_similaire"]


@necessite_rdkit
def test_similarite_sous_structure_quand_pertinent():
    resultat = sim.run(_entrees_structurelles(), {})
    paire = _paire(resultat, "aspirine", "cyclohexane")
    sous = paire["details"]["sous_structure"]
    assert sous["statut"] == "évalué"
    assert sous["relation"] in {
        "a_sous_structure_de_b", "b_sous_structure_de_a",
        "motifs_mutuellement_inclus", "aucune_inclusion",
    }


@necessite_rdkit
def test_similarite_smiles_invalide_jamais_invente():
    entrees = {"composes": [
        {"id": "a", "formule": "C9H8O4", "smiles": "[[["},
        {"id": "b", "formule": "C9H8O4", "smiles": ASPIRINE},
    ]}
    paire = _paire(sim.run(entrees, {"methode": "structure"}), "a", "b")
    bloc = paire["details"]["structure_similaire"]
    assert bloc["score"] is None  # pas de score inventé
    assert "invalide" in bloc["statut"]
    assert paire["score"] is None


@necessite_rdkit
def test_similarite_sans_smiles_structure_non_evaluable():
    entrees = {"composes": [
        {"id": "a", "formule": "H2O"},
        {"id": "b", "formule": "H2O2"},
    ]}
    paire = _paire(sim.run(entrees, {"methode": "structure"}), "a", "b")
    bloc = paire["details"]["structure_similaire"]
    assert bloc["score"] is None
    assert "SMILES" in bloc["statut"]


def test_similarite_sans_rdkit_heuristique_etiquetee(monkeypatch):
    monkeypatch.setattr(chimie, "_RDKIT_DISPONIBLE", False)
    paire = _paire(sim.run(_entrees_structurelles(), {}), "aspirine", "cyclohexane")
    bloc = paire["details"]["structure_similaire"]
    assert bloc["score"] is None
    assert "RDKit" in bloc["statut"]
    assert "Heuristique sans RDKit" in paire["details"]["formule_similaire"]["methode"]


# ===========================================================================
# 3. property_prediction — descripteurs RDKit réels + provenance
# ===========================================================================

@necessite_rdkit
def test_proprietes_rdkit_reelles_avec_provenance():
    resultat = prop.run(
        {"composes": [{"id": "aspirine", "formule": "C9H8O4", "smiles": ASPIRINE}]}, {}
    )
    fiche = resultat["composes"][0]
    assert resultat["calcul"]["bibliotheque"] == "RDKit"
    assert resultat["calcul"]["version_bibliotheque"] == chimie.infos_biblio()["version"]

    logp = fiche["logp"]
    assert logp["statut"] == "calcule"
    assert logp["valeur"] == pytest.approx(1.31, abs=0.05)
    assert logp["bibliotheque"] == "RDKit"
    assert logp["version_bibliotheque"]
    assert "RDKit" in logp["methode"]

    assert fiche["donneurs_hydrogene"]["valeur"] == 1
    assert isinstance(fiche["accepteurs_hydrogene"]["valeur"], int)
    assert fiche["tpsa"]["valeur"] == pytest.approx(63.6, abs=1.0)
    assert fiche["tpsa"]["unite"] == "Å²"
    assert isinstance(fiche["liaisons_rotatives"]["valeur"], int)
    assert 0.0 <= fiche["fraction_csp3"]["valeur"] <= 1.0

    lipinski = fiche["lipinski"]
    assert lipinski["verdict"] == "conforme"
    assert lipinski["violations"] == 0

    prov = fiche["provenance_calcul"]
    for cle in ("methode", "bibliotheque", "version_bibliotheque", "entree", "limites"):
        assert cle in prov, f"provenance {cle!r} manquante"
    assert prov["bibliotheque"] == "RDKit"
    assert prov["entree"] == {"formule": "C9H8O4", "smiles": ASPIRINE}
    assert prov["limites"]  # limites honnêtes enregistrées

    # Contrôle de cohérence formule déclarée / formule RDKit.
    assert fiche["coherence_formule"]["concorde"] is True
    assert fiche["smiles_canonique"]


@necessite_rdkit
def test_proprietes_smiles_incoherent_signale():
    fiche = prop.run(
        {"composes": [{"id": "x", "formule": "H2O", "smiles": ASPIRINE}]}, {}
    )["composes"][0]
    assert fiche["coherence_formule"]["concorde"] is False
    # ... mais les descripteurs RDKit restent calculés depuis le SMILES réel.
    assert fiche["logp"]["statut"] == "calcule"


@necessite_rdkit
def test_proprietes_smiles_invalide_jamais_invente():
    fiche = prop.run(
        {"composes": [{"id": "x", "formule": "C9H8O4", "smiles": "[[["}]}, {}
    )["composes"][0]
    for cle in ("logp", "donneurs_hydrogene", "accepteurs_hydrogene", "tpsa"):
        assert fiche[cle]["statut"] == "non_calcule"
        assert fiche[cle].get("valeur") is None
    assert fiche["lipinski"]["verdict"] == "partiel"


def test_proprietes_sans_rdkit_non_calculees(monkeypatch):
    monkeypatch.setattr(chimie, "_RDKIT_DISPONIBLE", False)
    fiche = prop.run(
        {"composes": [{"id": "x", "formule": "C9H8O4", "smiles": ASPIRINE}]}, {}
    )["composes"][0]
    for cle in ("logp", "donneurs_hydrogene", "accepteurs_hydrogene", "tpsa",
                "liaisons_rotatives", "fraction_csp3"):
        bloc = fiche[cle]
        assert bloc["statut"] == "non_calcule"
        assert bloc.get("valeur") is None
        assert "RDKit" in bloc["raison"]
    assert fiche["lipinski"]["verdict"] == "partiel"
    assert fiche["provenance_calcul"]["bibliotheque"] == "Python pur (stdlib)"


# ===========================================================================
# 4. docking_adapter — détection réelle, vrai docking, refus honnête
# ===========================================================================

RECEPTEUR_PDBQT = """REMARK  Test recepteur
ATOM      1  N   ALA A   1      11.104  13.207   2.100  1.00  0.00
ATOM      2  CA  ALA A   1      12.104  13.207   2.100  1.00  0.00
"""
LIGAND_PDBQT = """REMARK  Test ligand
ATOM      1  C   LIG A   1       0.000   0.000   0.000  1.00  0.00
"""

FAUX_PDBQT_SORTIE = """MODEL 1
REMARK VINA RESULT:      -7.200      0.000      0.000
REMARK  Name = ligand
ATOM      1  C   LIG A   1       0.000   0.000   0.000  1.00  0.00
ENDMDL
MODEL 2
REMARK VINA RESULT:      -6.100      1.234      2.345
ATOM      1  C   LIG A   1       0.100   0.000   0.000  1.00  0.00
ENDMDL
"""


def _entrees_docking(ligand="pdbqt"):
    entrees = {"recepteur_pdbqt": RECEPTEUR_PDBQT}
    if ligand == "pdbqt":
        entrees["ligand_pdbqt"] = LIGAND_PDBQT
    else:
        entrees["ligand_smiles"] = ASPIRINE
    return entrees


@pytest.fixture
def vina_absent(monkeypatch):
    """Force l'absence du binaire vina (hermétique à l'environnement)."""
    monkeypatch.setattr(dock, "_localiser_vina", lambda: None)
    return dock


@pytest.fixture
def vina_simule(monkeypatch):
    """Simule un binaire vina présent + un sous-processus contrôlé."""
    appels = []

    def faux_run(argv, **kwargs):
        appels.append({"argv": list(argv), "kwargs": dict(kwargs)})
        args = list(argv)
        idx_out = args.index("--out")
        with open(args[idx_out + 1], "w", encoding="utf-8") as fh:
            fh.write(FAUX_PDBQT_SORTIE)
        # Vina 1.2.x n'a plus d'option --log : le journal passe par stdout,
        # que l'adaptateur conserve dans le fichier journal.
        return subprocess.CompletedProcess(
            args, 0, stdout="faux journal vina\n", stderr=""
        )

    monkeypatch.setattr(dock, "_localiser_vina", lambda: "/faux/vina")
    monkeypatch.setattr(
        dock, "_version_vina", lambda binaire: "AutoDock Vina 1.2.7 (faux)"
    )
    monkeypatch.setattr(subprocess, "run", faux_run)
    return appels


def test_docking_contrat_plugin():
    assert check_plugin(dock) == []


def test_docking_indisponible_sans_binaire(vina_absent):
    disponible, raison = dock.is_available()
    assert disponible is False
    assert "Vina" in raison
    assert "docs/simulations.md" in raison
    diag = dock.diagnostic_vina()
    assert diag == {
        "binaire": None, "version": None,
        "disponible": False, "raison": raison,
    }


def test_docking_run_refuse_sans_binaire_jamais_de_faux_score(vina_absent):
    with pytest.raises(RuntimeError) as exc_info:
        dock.run(_entrees_docking(), {})
    message = str(exc_info.value)
    assert "indisponible" in message and "Vina" in message
    assert "kcal" not in message  # aucun faux score dans l'erreur


def test_docking_validation_stricte(vina_absent):
    # Ancien champ PDB brut → message de migration explicite.
    with pytest.raises(ValueError, match="recepteur_pdbqt"):
        dock.validate_inputs(
            {"recepteur_pdb": "ATOM ...", "ligand_smiles": ASPIRINE}, {})
    # Boîte non bornée.
    with pytest.raises(ValueError):
        dock.validate_inputs(
            _entrees_docking(),
            {"taille_boite": [100.0, 20.0, 20.0]})
    # Deux ligands.
    with pytest.raises(ValueError, match="Un seul ligand"):
        dock.validate_inputs(
            {"recepteur_pdbqt": RECEPTEUR_PDBQT,
             "ligand_pdbqt": LIGAND_PDBQT, "ligand_smiles": ASPIRINE}, {})
    # Aucun ligand.
    with pytest.raises(ValueError, match="Ligand manquant"):
        dock.validate_inputs({"recepteur_pdbqt": RECEPTEUR_PDBQT}, {})
    # Paramètres hors bornes.
    with pytest.raises(ValueError):
        dock.validate_inputs(_entrees_docking(), {"exhaustiveness": 99})
    with pytest.raises(ValueError):
        dock.validate_inputs(_entrees_docking(), {"seed": 0})
    # Contenu PDBQT invalide.
    with pytest.raises(ValueError, match="PDBQT invalide"):
        dock.validate_inputs(
            {"recepteur_pdbqt": "ceci n'est pas un pdbqt",
             "ligand_pdbqt": LIGAND_PDBQT}, {})


def test_docking_disponible_avec_binaire(vina_simule):
    disponible, raison = dock.is_available()
    assert disponible is True and raison == ""


def test_docking_argv_compatible_vina_1_2_x():
    """L'argv construit est compatible avec Vina 1.2.x : l'option ``--log``
    (Vina 1.1.x, refusée par Vina 1.2.x avec « unrecognised option ») ne doit
    jamais apparaître ; le journal est récupéré via stdout."""
    argv = dock._construire_argv(
        "/usr/bin/vina", "/tmp/r.pdbqt", "/tmp/l.pdbqt", "/tmp/o.pdbqt",
        [1.0, 2.0, 3.0], [20.0, 20.0, 20.0], 8, 5, 42,
    )
    assert "--log" not in argv
    for opt in ("--receptor", "--ligand", "--center_x", "--center_y",
                "--center_z", "--size_x", "--size_y", "--size_z",
                "--exhaustiveness", "--num_modes", "--seed", "--out"):
        assert opt in argv
    assert argv[0] == "/usr/bin/vina"
    assert argv[argv.index("--seed") + 1] == "42"


def test_docking_vrai_run_via_sous_processus_controle(vina_simule):
    appels = vina_simule
    resultat = dock.run(_entrees_docking(), {"seed": 123})

    # Le sous-processus a été appelé UNE fois, argv fixe, sans shell.
    assert len(appels) == 1
    appel = appels[0]
    assert isinstance(appel["argv"], list)
    assert appel["argv"][0] == "/faux/vina"
    assert appel["kwargs"]["shell"] is False
    assert "--seed" in appel["argv"]
    assert appel["argv"][appel["argv"].index("--seed") + 1] == "123"

    # Sorties réelles parsées (pas inventées : issues du faux binaire contrôlé).
    assert resultat["affinites_kcal_mol"] == [-7.2, -6.1]
    assert resultat["rmsd_bornes"] == [[0.0, 0.0], [1.234, 2.345]]
    assert resultat["nb_poses"] == 2
    assert len(resultat["poses_pdbqt"]) == 2
    assert resultat["unites"] == "kcal/mol"
    assert resultat["commande_vina"].startswith("/faux/vina --receptor")
    assert resultat["vina"] == {
        "binaire": "/faux/vina", "version": "AutoDock Vina 1.2.7 (faux)"}
    assert resultat["seed"] == 123
    # Hash SHA-256 des entrées réellement dockées.
    for cle in ("recepteur_pdbqt_sha256", "ligand_pdbqt_sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", resultat["entrees_hash"][cle])
    assert resultat["entrees_hash"]["ligand_source"] == "contenu fourni"
    # Avertissement « résultat informatique » obligatoire.
    assert "INFORMATIQUE" in resultat["avertissement"]
    assert "pas une mesure" in resultat["avertissement"].lower()


@necessite_rdkit
@necessite_meeko
def test_docking_ligand_smiles_prepare_via_rdkit_meeko(vina_simule):
    resultat = dock.run(_entrees_docking(ligand="smiles"), {})
    assert resultat["nb_poses"] == 2
    assert "smiles" in resultat["entrees_hash"]["ligand_source"]
    assert re.fullmatch(
        r"[0-9a-f]{64}", resultat["entrees_hash"]["ligand_pdbqt_sha256"])


def test_docking_smiles_sans_meeko_demande_pdbqt_prepare(monkeypatch):
    """Sans meeko, pas de conversion silencieuse : le moteur exige un
    ligand PDBQT déjà préparé (le logiciel reste utilisable)."""
    monkeypatch.setattr(dock, "meeko_disponible", lambda: False)
    import builtins
    vrai_import = builtins.__import__

    def faux_import(nom, *args, **kwargs):
        if nom == "meeko" or nom.startswith("meeko."):
            raise ImportError("meeko simulé absent")
        return vrai_import(nom, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", faux_import)
    with pytest.raises(ValueError, match="[Pp][Dd][Bb][Qq][Tt]"):
        dock.validate_inputs(_entrees_docking(ligand="smiles"), {})


def test_docking_echec_vina_jamais_de_faux_resultat(monkeypatch):
    monkeypatch.setattr(dock, "_localiser_vina", lambda: "/faux/vina")
    monkeypatch.setattr(dock, "_version_vina", lambda b: "1.2.7")

    def faux_echec(argv, **kwargs):
        return subprocess.CompletedProcess(
            list(argv), 1, stdout="", stderr="erreur fictive")
    monkeypatch.setattr(subprocess, "run", faux_echec)
    with pytest.raises(RuntimeError) as exc_info:
        dock.run(_entrees_docking(), {})
    assert "échoué" in str(exc_info.value)


def test_docking_determinisme_entrees_hash(vina_simule):
    r1 = dock.run(_entrees_docking(), {"seed": 42})
    r2 = dock.run(_entrees_docking(), {"seed": 42})
    assert r1["entrees_hash"] == r2["entrees_hash"]
    assert r1["affinites_kcal_mol"] == r2["affinites_kcal_mol"]


# ===========================================================================
# 4b. Docking RÉEL — exécuté uniquement si le vrai binaire Vina est présent
# (ignoré sinon, JAMAIS simulé : ce test ne fabrique aucun score).
# ===========================================================================

VINA_REEL = shutil.which("vina")
necessite_vina_reel = pytest.mark.skipif(
    VINA_REEL is None,
    reason="binaire `vina` réel absent du PATH — docking réel non exécuté",
)

MINI_RECEPTEUR_PDBQT = """REMARK  Mini-recepteur synthetique (test docking reel uniquement)
ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00     0.000 N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00     0.000 C
ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00     0.000 C
ATOM      4  O   ALA A   1       1.300   2.335   0.000  1.00  0.00     0.000 OA
ATOM      5  CB  ALA A   1       1.988  -0.700  -1.210  1.00  0.00     0.000 C
END
"""


@necessite_vina_reel
@necessite_rdkit
@necessite_meeko
def test_docking_reel_binaire_vina_si_present():
    """Exécute le VRAI binaire Vina sur un mini-système synthétique.

    Ignoré si `vina` est absent. Vérifie : exécution réelle (pas de mock),
    affinités parsées, version enregistrée, reproductibilité à seed fixe,
    avertissement « résultat informatique ».
    """
    resultat = dock.run(
        {"recepteur_pdbqt": MINI_RECEPTEUR_PDBQT, "ligand_smiles": "CCO"},
        {
            "centre_boite": [1.0, 0.5, 0.0],
            "taille_boite": [15.0, 15.0, 15.0],
            "exhaustiveness": 4,
            "seed": 7,
            "nb_poses": 3,
        },
    )
    assert resultat["nb_poses"] == 3
    affinites = resultat["affinites_kcal_mol"]
    assert len(affinites) == 3
    assert all(isinstance(a, float) for a in affinites)
    assert "INFORMATIQUE" in resultat["avertissement"]
    assert resultat["vina"]["binaire"] == VINA_REEL

    # Reproductibilité : même seed -> mêmes affinités.
    resultat2 = dock.run(
        {"recepteur_pdbqt": MINI_RECEPTEUR_PDBQT, "ligand_smiles": "CCO"},
        {
            "centre_boite": [1.0, 0.5, 0.0],
            "taille_boite": [15.0, 15.0, 15.0],
            "exhaustiveness": 4,
            "seed": 7,
            "nb_poses": 3,
        },
    )
    assert resultat2["affinites_kcal_mol"] == affinites


# ===========================================================================
# 5. Page Paramètres — bloc « Outils scientifiques »
# ===========================================================================

def test_parametres_bloc_outils_scientifiques(client):
    reponse = client.get("/parametres")
    assert reponse.status_code == 200, reponse.text
    html = reponse.text
    assert "Outils scientifiques" in html
    assert "RDKit" in html
    assert "AutoDock Vina" in html
    if RDKit_PRESENT:
        assert "Présent" in html
        assert chimie.infos_biblio()["version"] in html
    else:
        assert "install_science.sh" in html
