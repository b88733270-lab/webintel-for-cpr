"""Aide pour le test de persistance après redémarrage.

Exécuté en SOUS-PROCESSUS (``python aide_redemarrage.py ecrire|relire <db>``)
pour simuler un VRAI redémarrage du serveur : nouveau processus = nouveau
moteur SQLAlchemy, nouvelle ``create_app()`` + lifespan — seule la base
SQLite sur disque est partagée entre les deux phases.

Convention : la dernière ligne ``RESULTAT_JSON:<json>`` sur stdout porte
le résultat structuré ; tout le reste (logs) est ignoré par le test.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

CHEMIN_DB = sys.argv[2] if len(sys.argv) > 2 else "/tmp/restart-test.db"
os.environ["DATABASE_URL"] = "sqlite:///" + CHEMIN_DB
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["REQUIRE_API_KEY"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _resultat(donnees: dict) -> None:
    print("RESULTAT_JSON:" + json.dumps(donnees, ensure_ascii=False))


def phase_ecrire() -> None:
    """Phase 1 : crée l'application et persiste un jeu de données complet."""
    from webintel.main import create_app, lifespan
    from webintel.database.base import SessionLocal
    from webintel.database.models import Project, WizardSession
    from webintel.science import combinaisons as comb
    from webintel.science import models as m
    from webintel.science import terrain as t
    from webintel.science.versions import enregistrer_version

    app = create_app()

    async def _travail():
        async with lifespan(app):
            db = SessionLocal()
            try:
                projet = Project(name="Restart P0")
                db.add(projet)
                db.flush()

                plante = m.Plant(
                    scientific_name="Restartia persistens",
                    family="Fabaceae", genus="Restartia",
                )
                db.add(plante)
                db.flush()

                composes = []
                for i, (nom, formule) in enumerate(
                    [("Restartine A", "C20H24N2O2"),
                     ("Restartine B", "C21H26N2O2")]
                ):
                    c = m.Compound(name=nom, formula=formule,
                                   smiles="C" * (i + 1))
                    db.add(c)
                    db.flush()
                    composes.append(c)

                preuve = m.Evidence(
                    reference="Kaboré et al. 2024",
                    title="Phytochimie de Restartia persistens",
                    doi="10.restart/2024.persist",
                )
                db.add(preuve)
                db.flush()

                db.add(m.PlantCompound(
                    plant_id=plante.id, compound_id=composes[0].id,
                    doi=preuve.doi, evidence_id=preuve.id,
                    plant_part="feuille", extraction_method="macération",
                ))
                db.add(m.CompoundTarget(
                    compound_id=composes[0].id, target_name="COX-2",
                    evidence_id=preuve.id,
                ))
                db.add(m.PlantActivity(
                    plant_id=plante.id, activity="anti-inflammatoire",
                    evidence_id=preuve.id,
                ))
                # Échantillon + chaîne complète + analyse.
                ech, _ = t.creer_echantillon(
                    db, project_id=projet.id, plant_id=plante.id,
                    code_echantillon="RST-001", latitude=12.35,
                    longitude=-1.51, pays="Burkina Faso",
                )
                extraction = t.creer_extraction(
                    db, project_id=projet.id,
                    echantillon_id=ech.id, methode="macération",
                )
                extrait = t.creer_extrait(
                    db, project_id=projet.id,
                    extraction_id=extraction.id,
                )
                fraction = t.creer_fraction(
                    db, project_id=projet.id, extrait_id=extrait.id,
                    code_fraction="RST-001-F1",
                )
                t.lier_fraction_compose(
                    db, project_id=projet.id,
                    fraction_id=fraction.id,
                    compound_id=composes[0].id,
                )
                analyse = t.creer_analyse_chimique(
                    db, project_id=projet.id,
                    echantillon_id=ech.id, methode="HPLC-UV",
                )
                t.ajouter_pic(
                    db, analyse.id, temps_retention=12.5,
                    compound_id=composes[0].id,
                )
                db.flush()

                # Étude de combinaison complète (dossier 12 points) + v1.
                etude = comb.creer_etude(
                    db,
                    project_id=projet.id,
                    nom="Étude persistance P0",
                    compound_ids=[c.id for c in composes],
                    plant_id=plante.id,
                    plant_part="feuille",
                    target="COX-2",
                    evidence_ids=[preuve.id],
                    parametres={"seuil_similarite": 0.7,
                                "methode": "structure"},
                    description="Étude créée avant redémarrage.",
                    cree_par="test-persistance",
                )
                db.flush()
                version = enregistrer_version(
                    db, etude, cree_par="test-persistance",
                    resume="Version initiale",
                )
                db.flush()

                # Brouillon wizard en cours + session finalisée.
                maintenant = datetime.now(timezone.utc)
                brouillon = WizardSession(
                    session_id="b" * 32, project_id=projet.id,
                    owner="test", study_type="combinaison",
                    payload={"type_objet": "combinaison",
                             "molecules": [{"nom": "X"}],
                             "plante_id": plante.id},
                    status="en_cours",
                    expires_at=maintenant + timedelta(days=30),
                )
                finalisee = WizardSession(
                    session_id="f" * 32, project_id=projet.id,
                    owner="test", study_type="combinaison",
                    payload={"type_objet": "combinaison",
                             "resultats": {"blocs": [{"titre": "OK"}]}},
                    status="terminee",
                    expires_at=maintenant + timedelta(days=30),
                )
                db.add_all([brouillon, finalisee])
                db.commit()
                _resultat({
                    "ok": True,
                    "project_id": projet.id,
                    "plant_id": plante.id,
                    "compound_ids": [c.id for c in composes],
                    "evidence_id": preuve.id,
                    "echantillon_id": ech.id,
                    "analyse_id": analyse.id,
                    "etude_id": etude.id,
                    "version_id": version.id,
                    "inputs_hash": etude.inputs_hash,
                    "session_brouillon": brouillon.session_id,
                    "session_terminee": finalisee.session_id,
                })
            finally:
                db.close()

    asyncio.run(_travail())


def phase_relire() -> None:
    """Phase 2 (NOUVEAU processus) : relit tout après « redémarrage »."""
    from webintel.main import create_app, lifespan
    from webintel.database.base import SessionLocal
    from webintel.database.models import Project, WizardSession
    from webintel.science import models as m
    from webintel.science import terrain as t
    from webintel.science.versions import get_version, list_versions
    from webintel.ui.chercheur_etudes_routes import (
        purger_sessions_expirees,
    )

    app = create_app()
    details: dict = {"verifications": []}

    def check(nom: str, condition: bool, extra: str = "") -> None:
        details["verifications"].append(
            {"nom": nom, "ok": bool(condition), "detail": extra})
        if not condition:
            raise AssertionError(f"ÉCHEC persistance : {nom} {extra}")

    async def _travail():
        async with lifespan(app):
            db = SessionLocal()
            try:
                projet = db.query(Project).filter(
                    Project.name == "Restart P0").one()
                check("projet", projet.id > 0)

                plante = db.query(m.Plant).filter(
                    m.Plant.scientific_name
                    == "Restartia persistens").one()
                check("plante", plante.family == "Fabaceae")

                composes = db.query(m.Compound).filter(
                    m.Compound.name.like("Restartine%")).order_by(
                    m.Compound.id).all()
                check("moleculs(2)", len(composes) == 2,
                      str([c.name for c in composes]))

                preuve = db.get(m.Evidence, details.get("evidence_id") or 0)
                if preuve is None:
                    preuve = db.query(m.Evidence).filter(
                        m.Evidence.doi == "10.restart/2024.persist").one()
                details["evidence_id"] = preuve.id
                check("preuve", preuve.title.startswith("Phytochimie"))

                lien = db.query(m.PlantCompound).filter(
                    m.PlantCompound.plant_id == plante.id).one()
                check("lien plante-molecule",
                      lien.compound_id == composes[0].id
                      and lien.evidence_id == preuve.id)

                cible = db.query(m.CompoundTarget).filter(
                    m.CompoundTarget.compound_id == composes[0].id).one()
                check("cible", cible.target_name == "COX-2")

                activite = db.query(m.PlantActivity).filter(
                    m.PlantActivity.plant_id == plante.id).one()
                check("activite", True)

                ech = db.query(m.Echantillon).filter(
                    m.Echantillon.code_echantillon == "RST-001").one()
                check("echantillon", ech.latitude == 12.35
                      and ech.pays == "Burkina Faso")
                chaine = t.chaine_echantillon(db, ech.id)
                check("chaine echantillon",
                      len(chaine["extractions"]) == 1
                      and len(chaine["extractions"][0]["extraits"]) == 1)

                analyse = db.query(m.AnalyseChimique).filter(
                    m.AnalyseChimique.echantillon_id == ech.id).one()
                pics = t.lister_pics(db, analyse.id)
                check("analyse+pic",
                      analyse.methode == "HPLC-UV" and len(pics) == 1
                      and pics[0].temps_retention == 12.5)

                # --- Étude complète : tous les champs exigés. ---
                etude = db.query(m.CombinationStudy).filter(
                    m.CombinationStudy.nom
                    == "Étude persistance P0").one()
                for champ in ("nom", "description", "plant_id",
                              "plant_part", "compound_ids", "target",
                              "evidence_ids", "parametres", "resultats",
                              "inputs_hash", "statut", "data_level",
                              "provenance", "created_at", "updated_at"):
                    check(f"etude.{champ}",
                          getattr(etude, champ) not in (None, "", [], {}),
                          repr(getattr(etude, champ))[:80])
                check("etude.compound_ids",
                      set(etude.compound_ids) == {c.id for c in composes})
                check("etude.parametres",
                      etude.parametres.get("seuil_similarite") == 0.7)
                check("etude.inputs_hash",
                      len(etude.inputs_hash) == 64)
                check("etude.statut", etude.statut in (
                    "termine", "partiel", "echoue"))
                # Dossier en 12 points : 12 sections titrées.
                dossier = etude.resultats or {}
                sections_dossier = dossier.get("sections") or []
                check("etude.resultats(dossier)",
                      isinstance(sections_dossier, list)
                      and len(sections_dossier) == 12
                      and all(str(s.get("titre", "")).split(".")[0].strip()
                              .isdigit() for s in sections_dossier),
                      f"{len(sections_dossier)} sections")
                # Provenance : logiciels / versions.
                prov = etude.provenance or {}
                check("etude.provenance(logiciels)",
                      "chimie_infos_biblio" in prov
                      and "score_version" in prov,
                      str(sorted(prov)[:8]))
                # Limitations : le dossier porte les limites/avertissements.
                texte_dossier = json.dumps(dossier, ensure_ascii=False)
                check("etude.limitations",
                      "limite" in texte_dossier.lower()
                      or "avertissement" in texte_dossier.lower())

                # --- Versionnage ---
                versions = list_versions(db, etude.id)
                check("version v1",
                      len(versions) >= 1 and versions[0].numero == 1)
                snap = versions[0].snapshot or {}
                check("snapshot complet",
                      snap.get("inputs_hash") == etude.inputs_hash
                      and snap.get("nom") == etude.nom
                      and "resultats" in snap and "provenance" in snap)

                # --- Sessions wizard : relues après redémarrage ---
                brouillon = db.get(WizardSession, "b" * 32)
                finalisee = db.get(WizardSession, "f" * 32)
                check("brouillon wizard",
                      brouillon is not None
                      and brouillon.status == "en_cours"
                      and brouillon.payload["plante_id"] == plante.id)
                check("session finalisee",
                      finalisee is not None
                      and finalisee.status == "terminee"
                      and finalisee.payload["resultats"]["blocs"][0][
                          "titre"] == "OK")

                # --- Purge TTL : ne supprime JAMAIS la finalisée ---
                passe = datetime.now(timezone.utc) - timedelta(seconds=1)
                brouillon.expires_at = passe
                finalisee.expires_at = passe
                db.commit()
                purgees = purger_sessions_expirees(db)
                check("purge(brouillon expire purge)",
                      db.get(WizardSession, "b" * 32) is None
                      and purgees == 1, f"purgees={purgees}")
                check("purge(finalisee JAMAIS supprimee)",
                      db.get(WizardSession, "f" * 32) is not None)

                details["ok"] = True
                details["etude_id"] = etude.id
            finally:
                db.close()

    try:
        asyncio.run(_travail())
    except AssertionError as exc:
        details["ok"] = False
        details["erreur"] = str(exc)
    _resultat(details)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "relire":
        phase_relire()
    else:
        phase_ecrire()
