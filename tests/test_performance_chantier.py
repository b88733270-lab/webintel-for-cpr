"""Performance — chantier « PERSISTANCE + PERFORMANCE ».

1. Index : toute clé étrangère du modèle porte un index (méta-test sur
   ``Base.metadata`` + vérification en base après ``init_db``).
2. N+1 : la fiche plante et ``chaine_echantillon`` gardent un nombre de
   requêtes SQL constant par volume (comptées via l'event SQLAlchemy).
3. Pagination : les endpoints nouvellement paginés (terrain, evidences,
   observations, simulations, notes labo, workflows) respectent
   ``limit``/``offset`` et bornent ``limit``.
"""

import pytest
from sqlalchemy import event

from webintel.database.base import Base, engine
from webintel.database.models import Project
from webintel.science import models as m
from webintel.science import terrain as t


@pytest.fixture()
def _projet(db_session):
    projet = db_session.query(Project).first()
    if projet is None:
        projet = Project(name="Projet Perf")
        db_session.add(projet)
        db_session.flush()
    return projet


# ---------------------------------------------------------------------------
# 1. Index sur toutes les clés étrangères
# ---------------------------------------------------------------------------

def test_ensure_fk_indexes_idempotent_et_couvrant():
    """``_ensure_fk_indexes`` : idempotente ET couvrant toutes les FK.

    La migration (chantier PERSISTANCE + PERFORMANCE) crée les index au
    runtime — ce test vérifie qu'un double appel ne change rien et que
    chaque contrainte FK (colonnes existantes en base) est couverte par
    un index ``ix_<table>_<colonnes>`` ou un index/contrainte existant.
    """
    from sqlalchemy import inspect as sa_inspect

    from webintel.database.base import _ensure_fk_indexes

    def _noms_index():
        return {
            t: sorted(i["name"] for i in sa_inspect(engine).get_indexes(t))
            for t in Base.metadata.tables
        }

    _ensure_fk_indexes()
    milieu = _noms_index()
    _ensure_fk_indexes()
    assert _noms_index() == milieu, \
        "2e appel a modifié les index (migration non idempotente)"

    manquants = []
    for table in Base.metadata.tables.values():
        indexes = sa_inspect(engine).get_indexes(table.name)
        for fkc in table.foreign_key_constraints:
            colonnes = [c.name for c in fkc.columns]
            if not colonnes:
                continue
            couvert = any(
                set(colonnes) <= set(i.get("column_names") or [])
                for i in indexes)
            if not couvert:
                manquants.append(f"{table.name}.{','.join(colonnes)}")
    assert not manquants, (
        "FK sans index en base : " + ", ".join(sorted(manquants)))


# ---------------------------------------------------------------------------
# 2. N+1 : comptage de requêtes SQL
# ---------------------------------------------------------------------------

class _CompteurRequetes:
    """Compte les SELECT émis sur l'engine (contexte ``with``)."""

    def __init__(self):
        self.selects = 0

    def __enter__(self):
        def _compter(conn, cursor, statement, parameters, context,
                     executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                self.selects += 1
        self._hook = _compter
        event.listen(engine, "before_cursor_execute", _compter)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._hook)
        return False


def _plante_riche(db_session, projet, suffixe: str):
    """Plante + N liens (N=6) pour révéler un N+1."""
    from webintel.science import service as svc
    plante, _ = svc.create_plant(
        db_session, scientific_name=f"Perfus multipla {suffixe}")
    composes = []
    for i in range(6):
        c, _ = svc.create_compound(db_session, f"Perf-Cmp-{suffixe}-{i}")
        composes.append(c)
    db_session.flush()
    for c in composes:
        db_session.add(m.PlantCompound(
            plant_id=plante.id, compound_id=c.id, plant_part="feuille"))
    db_session.flush()
    return plante


def test_fiche_plante_requetes_constantes(db_session, _projet):
    """La fiche plante ne fait pas de N+1 : requêtes constantes à volume ×2."""
    from webintel.science.fiche import build_plant_fiche

    p1 = _plante_riche(db_session, _projet, "A")
    db_session.commit()
    with _CompteurRequetes() as c1:
        build_plant_fiche(db_session, p1.id)
    n1 = c1.selects

    p2 = _plante_riche(db_session, _projet, "B")
    db_session.commit()
    with _CompteurRequetes() as c2:
        build_plant_fiche(db_session, p2.id)
    n2 = c2.selects

    # Volume doublé (6 → 12 liens en base) : la fiche d'UNE plante doit
    # coûter le même nombre de requêtes (pas de N+1 sur les liens).
    assert n1 == n2, f"N+1 suspecté : {n1} vs {n2} SELECT"
    assert n1 <= 12, f"Fiche plante trop bavarde : {n1} SELECT"


def test_chaine_echantillon_requetes_bornees(db_session, _projet):
    """chaine_echantillon : requêtes bornées, pas de N+1 par niveau."""
    plante = db_session.query(m.Plant).first()
    if plante is None:
        from webintel.science import service as svc
        plante, _ = svc.create_plant(db_session,
                                     scientific_name="Perfus chaine")
        db_session.flush()
    c, _ = t.creer_echantillon(
        db_session, project_id=_projet.id, plant_id=plante.id,
        code_echantillon="PERF-CH")
    cmp1 = m.Compound(name="Perf-Mol-1")
    cmp2 = m.Compound(name="Perf-Mol-2")
    db_session.add_all([cmp1, cmp2])
    db_session.flush()
    for i in range(3):  # 3 extractions × 2 extraits × 2 fractions
        ext = t.creer_extraction(
            db_session, project_id=_projet.id, echantillon_id=c.id,
            methode="macération")
        for j in range(2):
            ex = t.creer_extrait(
                db_session, project_id=_projet.id,
                extraction_id=ext.id)
            for k in range(2):
                fr = t.creer_fraction(
                    db_session, project_id=_projet.id,
                    extrait_id=ex.id,
                    code_fraction=f"PERF-CH-{i}{j}{k}")
                t.lier_fraction_compose(
                    db_session, project_id=_projet.id,
                    fraction_id=fr.id, compound_id=cmp1.id)
    db_session.commit()

    with _CompteurRequetes() as compteur:
        chaine = t.chaine_echantillon(db_session, c.id)
    assert len(chaine["extractions"]) == 3
    total_fractions = sum(
        len(ex["fractions"])
        for ext in chaine["extractions"] for ex in ext["extraits"])
    assert total_fractions == 12
    # 3+6+12 objets imbriqués : ≤ 8 SELECT (chargements groupés par niveau).
    assert compteur.selects <= 8, (
        f"N+1 dans chaine_echantillon : {compteur.selects} SELECT")


# ---------------------------------------------------------------------------
# 3. Pagination des endpoints nouvellement paginés
# ---------------------------------------------------------------------------

def _echantillons(db_session, projet, n: int):
    plante = db_session.query(m.Plant).first()
    if plante is None:
        from webintel.science import service as svc
        plante, _ = svc.create_plant(db_session, scientific_name="Perfus pag")
        db_session.flush()
    for i in range(n):
        t.creer_echantillon(
            db_session, project_id=projet.id, plant_id=plante.id,
            code_echantillon=f"PERF-PAG-{i:03d}")
    db_session.commit()
    return plante


def test_pagination_echantillons_service(db_session, _projet):
    """Service terrain : limit/offset découpent la liste."""
    plante = _echantillons(db_session, _projet, 7)
    page1 = t.lister_echantillons(db_session, plante.id,
                                  project_id=_projet.id,
                                  limit=3, offset=0)
    page2 = t.lister_echantillons(db_session, plante.id,
                                  project_id=_projet.id,
                                  limit=3, offset=3)
    page3 = t.lister_echantillons(db_session, plante.id,
                                  project_id=_projet.id,
                                  limit=3, offset=6)
    assert len(page1) == 3 and len(page2) == 3 and len(page3) == 1
    ids1 = {e.id for e in page1}
    ids2 = {e.id for e in page2}
    ids3 = {e.id for e in page3}
    assert not (ids1 & ids2) and not (ids1 & ids3) and not (ids2 & ids3)
    # Compatibilité : limit=None conserve l'ancien comportement.
    tous = t.lister_echantillons(db_session, plante.id,
                                 project_id=_projet.id)
    assert len(tous) == 7


def test_pagination_echantillons_api(client, db_session):
    """API terrain : /plantes/{id}/echantillons honore limit/offset."""
    from webintel.database.models import Project
    projet = db_session.query(Project).first()
    if projet is None:
        projet = Project(name="Projet Perf API")
        db_session.add(projet)
        db_session.flush()
    plante = _echantillons(db_session, projet, 5)
    db_session.commit()  # libère le verrou avant l'appel HTTP
    base = (f"/api/science/terrain/plantes/{plante.id}/echantillons"
            f"?project_id={projet.id}")
    r = client.get(base + "&limit=2&offset=1")
    assert r.status_code == 200, r.text
    donnees = r.json()
    assert len(donnees) == 2
    # limit bornée : 501 → 422 (défense anti-abus).
    r = client.get(base + "&limit=501")
    assert r.status_code == 422


def test_pagination_etudes_api(client):
    """API études : /api/science/combinaisons pagine (limit/offset)."""
    r = client.get("/api/science/combinaisons?limit=1&offset=0")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
    assert len(r.json()) <= 1


def test_pagination_evidences_api(client):
    """API /api/science/evidences : offset ajouté."""
    r = client.get("/api/science/evidences?limit=2&offset=0")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
