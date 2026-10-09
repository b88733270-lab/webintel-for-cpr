"""Tests v2.2 — mesures expérimentales : chaîne RAW → NORMALISÉ →
ANALYSE → DÉRIVÉ (append-only), validation non destructive des unités,
provenance locale complète.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from webintel.science import mesures
from webintel.science.connecteurs.provenance import (
    CHAMPS_PROVENANCE_LOCALE,
    enregistrer_provenance_detaillee,
    normaliser_provenance_locale,
)
from webintel.science.models import MesureExperimentale


def _mesure_brute(db_session: Session, **kwargs) -> MesureExperimentale:
    kwargs.setdefault("sample", "Extrait aqueux Moringa")
    kwargs.setdefault("concentration", 100.0)
    kwargs.setdefault("concentration_unite", "µg/mL")
    kwargs.setdefault("response", 42.0)
    kwargs.setdefault("response_unite", "%")
    return mesures.creer_mesure(db_session, **kwargs)


def test_creation_brute_privee_par_defaut(db_session: Session):
    mesure = _mesure_brute(db_session)
    assert mesure.niveau_chaine == "brut"
    assert mesure.id is not None


def test_promotion_append_only(db_session: Session):
    brute = _mesure_brute(db_session)
    normalisee = mesures.promouvoir_mesure(
        db_session,
        brute.id,
        "normalise",
        modifications={"concentration_unite": "µg/mL"},
        note="Unité confirmée.",
    )
    assert normalisee.id != brute.id
    assert normalisee.niveau_chaine == "normalise"
    assert normalisee.normalise_depuis_id == brute.id
    # La ligne brute est intouchée.
    db_session.refresh(brute)
    assert brute.niveau_chaine == "brut"

    analyse = mesures.promouvoir_mesure(db_session, normalisee.id, "analyse")
    derivee = mesures.promouvoir_mesure(db_session, analyse.id, "derive")
    assert derivee.niveau_chaine == "derive"
    assert derivee.data_level == "prediction"  # jamais « prouvé »

    chaine = mesures.chaine_mesure(db_session, derivee.id)
    assert [m.niveau_chaine for m in chaine] == [
        "brut", "normalise", "analyse", "derive",
    ]


def test_promotion_saut_interdit(db_session: Session):
    brute = _mesure_brute(db_session)
    with pytest.raises(ValueError, match="immédiatement supérieur"):
        mesures.promouvoir_mesure(db_session, brute.id, "derive")


def test_promotion_niveau_invalide(db_session: Session):
    brute = _mesure_brute(db_session)
    with pytest.raises(ValueError, match="Niveau cible invalide"):
        mesures.promouvoir_mesure(db_session, brute.id, "publie")


def test_promotion_mesure_introuvable(db_session: Session):
    with pytest.raises(ValueError, match="introuvable"):
        mesures.promouvoir_mesure(db_session, 999999, "normalise")


def test_promotion_dernier_niveau_bloquee(db_session: Session):
    brute = _mesure_brute(db_session)
    m = brute
    for niveau in ("normalise", "analyse", "derive"):
        m = mesures.promouvoir_mesure(db_session, m.id, niveau)
    with pytest.raises(ValueError, match="dernier niveau"):
        mesures.promouvoir_mesure(db_session, m.id, "derive")


def test_promotion_champ_inconnu_rejete(db_session: Session):
    brute = _mesure_brute(db_session)
    with pytest.raises(ValueError, match="Champ inconnu"):
        mesures.promouvoir_mesure(
            db_session, brute.id, "normalise",
            modifications={"efficacite": 99.0},
        )


def test_unite_inconnue_avertissement_pas_correction(db_session: Session):
    mesure = _mesure_brute(db_session, concentration_unite="pincées")
    assert mesure.concentration_unite == "pincées"  # conservée telle quelle
    assert any("pincées" in a for a in mesure.avertissements)


def test_unite_dimension_incorrecte_signalee(db_session: Session):
    mesure = _mesure_brute(db_session, dose=5.0, dose_unite="heures")
    assert mesure.dose_unite == "heures"
    assert any("dose_unite" in a and "temps" in a for a in mesure.avertissements)


def test_unites_propres_sans_avertissement(db_session: Session):
    mesure = _mesure_brute(db_session)
    assert mesure.avertissements == []


def test_valider_unites_pur():
    assert mesures.valider_unites({"concentration_unite": "mg/L"}) == []
    avertissements = mesures.valider_unites({"dose_unite": "xyz"})
    assert len(avertissements) == 1 and "xyz" in avertissements[0]


def test_provenance_locale_10_champs(db_session: Session):
    mesure = _mesure_brute(db_session)
    ligne = enregistrer_provenance_detaillee(
        db_session,
        entity_type="mesure_experimentale",
        entity_id=mesure.id,
        qui="A. Ouédraogo",
        quand="2026-09-20T10:00:00",
        ou="Laboratoire de pharmacognosie, Ouagadougou",
        source="Cahier de laboratoire n° 12",
        commentaire="Triplicat, lecture à 24 h.",
        outil="Lecteur de microplaques X",
        version="protocole v3",
        parametres={"temperature_c": 37, "duree_h": 24},
        niveau_preuve="observation",
    )
    assert ligne.acteur == "A. Ouédraogo"
    assert ligne.lieu == "Laboratoire de pharmacognosie, Ouagadougou"
    assert ligne.niveau_preuve == "observation"
    assert ligne.parametres == {"temperature_c": 37, "duree_h": 24}
    assert ligne.entity_type == "mesure_experimentale"
    assert ligne.entity_id == mesure.id


def test_normaliser_provenance_champs_canoniques():
    normalisee = normaliser_provenance_locale(
        {"qui": "X", "outil": "Y", "champ_libre": "conservé"}
    )
    for champ in CHAMPS_PROVENANCE_LOCALE:
        assert champ in normalisee
    assert normalisee["qui"] == "X"
    assert normalisee["quoi"] is None
    assert normalisee["champ_libre"] == "conservé"  # jamais de perte
    assert normaliser_provenance_locale(None)["qui"] is None
