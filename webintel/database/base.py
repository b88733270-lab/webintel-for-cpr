"""Moteur SQLAlchemy, fabrique de sessions, helpers FastAPI / init."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import DateTime, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from webintel.config.settings import get_settings
from webintel.core.logging import get_logger

logger = get_logger(__name__)

# Tables dont la présence est exigée au démarrage en mode PostgreSQL
# (schéma minimal : le reste est créé par `init_db()` juste avant).
_TABLES_MINIMALES_POSTGRES = (
    "projects",
    "sources",
    "entities",
    "observations",
    "source_observations",
)


class Base(DeclarativeBase):
    """Base déclarative commune à tous les modèles."""


# Horodatage UTC portable : TIMESTAMPTZ sous PostgreSQL (les datetimes
# tz-aware y sont natives), DATETIME sous SQLite (valeurs UTC tz-aware
# partout). Défini ici (plutôt que dans `database.models`) pour que le
# package `webintel.science` puisse l'utiliser sans import circulaire ;
# `webintel.database.models` réexporte ce nom pour compatibilité.
TZDateTime = DateTime(timezone=True)


def _make_engine():
    settings = get_settings()
    url = settings.DATABASE_URL
    kwargs: dict = {}
    if url.startswith("sqlite"):
        # Requis pour SQLite utilisé depuis plusieurs threads (scheduler, workers).
        kwargs["connect_args"] = {"check_same_thread": False}
    elif url.startswith("postgresql"):
        # PostgreSQL : vérifie que les connexions du pool sont encore vivantes
        # avant usage (évite les erreurs sur connexions coupées par le
        # serveur, un pare-feu ou un redémarrage du conteneur db).
        kwargs["pool_pre_ping"] = True
    moteur = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # Chantier robustesse : les jobs (workers) et les requêtes HTTP
        # écrivent depuis des threads différents. Sans busy_timeout, une
        # écriture concurrente échoue IMMÉDIATEMENT (« database is locked »)
        # ; avec ce pragma, SQLite patiente jusqu'à 15 s. Strictement plus
        # robuste, aucun changement de sémantique sinon.
        from sqlalchemy import event

        @event.listens_for(moteur, "connect")
        def _pragma_busy_timeout(connexion_dbapi, _enregistrement):
            curseur = connexion_dbapi.cursor()
            curseur.execute("PRAGMA busy_timeout = 15000")
            curseur.close()

    return moteur


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI : fournit une session, la referme ensuite."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crée toutes les tables (importe les modèles pour les enregistrer)."""
    from webintel.database import models  # noqa: F401
    # Enregistre les tables scientifiques (Phase 2) dans les métadonnées,
    # sans créer d'import circulaire (import différé, exécuté à l'init).
    from webintel.science import models as _science_models  # noqa: F401
    # Tables hors-ligne / laboratoire / workflows / versions : importées
    # explicitement pour que Base.metadata soit COMPLET quel que soit
    # l'ordre d'import (les migrations d'index FK de _ensure_fk_indexes
    # dérivent de ces métadonnées).
    from webintel.offline import models as _offline_models  # noqa: F401
    from webintel.science import lab as _lab_models  # noqa: F401
    from webintel.science import versions as _versions_models  # noqa: F401
    from webintel.science import workflows as _workflows_models  # noqa: F401

    Base.metadata.create_all(engine)
    _ensure_simulation_audit_columns()
    _ensure_v21_science_columns()
    _ensure_v22_science_columns()
    _ensure_offline_sync_columns()
    _ensure_v22_performance_indexes()
    _ensure_study_versions_unique()
    _ensure_noms_vernaculaires_unique()
    _ensure_fk_indexes()
    _rattacher_simulations_orphelines()
    _amorcer_connecteurs_sources()
    _amorcer_langues()
    _amorcer_donnees_initiales()


def _rattacher_simulations_orphelines() -> None:
    """P0-1 : rattache les simulations orphelines (``project_id`` NULL).

    Bases créées avant le durcissement (colonne NOT NULL) : chaque
    simulation sans projet est rattachée au premier projet existant (ou
    au projet « Défaut » créé si aucun). Idempotent : sans orphelin,
    aucune écriture. Portable SQLite / PostgreSQL. Ne fait jamais
    échouer le démarrage (base verrouillée, table absente...).
    """
    try:
        with engine.begin() as connexion:
            nb = connexion.execute(
                text("SELECT COUNT(*) FROM simulations WHERE project_id IS NULL")
            ).scalar()
            if not nb:
                return
            pid = connexion.execute(
                text("SELECT id FROM projects ORDER BY id LIMIT 1")
            ).scalar()
            if pid is None:
                # created_at est NOT NULL sans défaut SQL : CURRENT_TIMESTAMP
                # (portable SQLite / PostgreSQL).
                connexion.execute(
                    text("INSERT INTO projects (name, created_at)"
                         " VALUES ('Défaut', CURRENT_TIMESTAMP)")
                )
                pid = connexion.execute(
                    text("SELECT id FROM projects ORDER BY id LIMIT 1")
                ).scalar()
            connexion.execute(
                text("UPDATE simulations SET project_id = :pid WHERE project_id IS NULL"),
                {"pid": pid},
            )
        logger.warning(
            "P0-1 : %s simulation(s) orpheline(s) rattachée(s) au projet %s.",
            nb, pid,
        )
    except Exception as exc:  # pragma: no cover - démarrage jamais bloqué
        logger.warning("P0-1 : rattachement des simulations orphelines impossible (%s).", exc)


def _amorcer_connecteurs_sources() -> None:
    """Amorce les lignes ``SourceConnecteur`` manquantes (idempotent).

    Donnée de référence : l'amorçage a lieu au démarrage (``init_db``),
    JAMAIS dans un GET (correctif P2-1 : le GET
    ``/api/science/connecteurs`` est désormais en lecture seule, sans
    effet de bord). Ne fait jamais échouer le démarrage.
    """
    try:
        # Import différé : `registry` dépend de `webintel.science.models`
        # (déjà enregistré ici), sans import circulaire.
        from webintel.science.connecteurs import registry

        session = SessionLocal()
        try:
            crees = registry.initialiser_connecteurs(session)
            if crees:
                session.commit()
                logger.info("Connecteurs de sources amorcés : %s ligne(s) créée(s).", crees)
        finally:
            session.close()
    except Exception as exc:  # pragma: no cover - démarrage jamais bloqué
        logger.warning("Amorçage des connecteurs de sources impossible (%s).", exc)


def _amorcer_langues() -> None:
    """Amorce le registre des langues en base (idempotent).

    Les 5 langues du Burkina Faso du registre ``LANGUES_BURKINA``
    (moore, dioula, fulfulde, gulmancema, bissa) sont créées si
    absentes ; les lignes existantes ne sont jamais modifiées ni
    supprimées (une langue désactivée manuellement ``actif=False``
    le reste). Ne fait jamais échouer le démarrage.
    """
    try:
        from webintel.science.models import (
            LANGUES_BURKINA,
            Langue,
            enregistrer_langue,
        )

        session = SessionLocal()
        try:
            crees = 0
            for code, libelle in LANGUES_BURKINA.items():
                existe = (
                    session.query(Langue)
                    .filter(Langue.code == code)
                    .first()
                )
                if existe is None:
                    session.add(
                        Langue(
                            code=code,
                            libelle=libelle,
                            source="registre initial WebIntel",
                            actif=True,
                        )
                    )
                    crees += 1
                # Le registre en mémoire reste synchronisé.
                enregistrer_langue(code, libelle)
            if crees:
                session.commit()
                logger.info(
                    "Registre des langues amorcé : %s langue(s) créée(s).",
                    crees,
                )
        finally:
            session.close()
    except Exception as exc:  # pragma: no cover - démarrage jamais bloqué
        logger.warning("Amorçage du registre des langues impossible (%s).", exc)


def _amorcer_donnees_initiales() -> None:
    """Charge le jeu de données initial sourcé au premier démarrage.

    v2.4.0 : une installation fraîche ne démarre plus sur une base vide.
    ``charger_donnees_initiales`` est idempotent (clés naturelles) : aux
    démarrages suivants, aucun doublon n'est créé. Chaque donnée porte
    sa provenance (« jeu de démonstration intégré WebIntel »). Ne fait
    jamais échouer le démarrage.

    Désactivable par ``WEBINTEL_SEED_INITIAL=0`` (utilisé par la suite
    de tests, qui construit ses propres fixtures sur base vide).
    """
    import os

    if os.environ.get("WEBINTEL_SEED_INITIAL", "1").strip().lower() in (
        "0", "false", "non", "no",
    ):
        return
    try:
        from webintel.science.seed_initial import charger_donnees_initiales

        session = SessionLocal()
        try:
            compteurs = charger_donnees_initiales(session)
            if any(compteurs.values()):
                session.commit()
                logger.info(
                    "Données initiales chargées : %s.",
                    ", ".join(f"{k}={v}" for k, v in compteurs.items() if v),
                )
        finally:
            session.close()
    except Exception as exc:  # pragma: no cover - démarrage jamais bloqué
        logger.warning("Chargement des données initiales impossible (%s).", exc)


def _url_masquee(url: str) -> str:
    """URL de base avec mot de passe masqué (pour logs / messages d'erreur)."""
    from urllib.parse import urlparse, urlunparse

    try:
        parsed = urlparse(url)
        if parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc += f":{parsed.port}"
            netloc = f"{parsed.username}:***@{netloc}"
            parsed = parsed._replace(netloc=netloc)
        return urlunparse(parsed)
    except Exception:
        return "<URL illisible>"


def verifier_connexion_postgres(timeout_s: int = 5) -> None:
    """Fail-fast : vérifie que PostgreSQL est joignable AVANT `init_db()`.

    Appelée au démarrage quand `DATABASE_URL` pointe vers PostgreSQL.
    Lève `RuntimeError` avec un message explicite (FR) si le serveur est
    injoignable — jamais de démarrage silencieux dégradé qui planterait
    plus tard en pleine requête.
    """
    url = get_settings().DATABASE_URL
    moteur = create_engine(url, connect_args={"connect_timeout": timeout_s})
    try:
        with moteur.connect() as connexion:
            connexion.execute(text("SELECT 1"))
    except Exception as exc:
        raise RuntimeError(
            "WebIntel ne peut pas démarrer : PostgreSQL est injoignable "
            f"(DATABASE_URL={_url_masquee(url)}). Cause : {exc}. "
            "Vérifiez que le serveur est lancé, que l'hôte/le port sont "
            "corrects et que pg_hba.conf autorise la connexion."
        ) from exc
    finally:
        moteur.dispose()


def verifier_schema_minimal_postgres() -> None:
    """Fail-fast : vérifie le schéma minimal APRÈS `init_db()` (mode PG).

    Lève `RuntimeError` explicite si des tables minimales manquent —
    signe d'une base partiellement créée ou d'un problème de droits.
    """
    try:
        existantes = set(inspect(engine).get_table_names())
    except Exception as exc:
        raise RuntimeError(
            "WebIntel ne peut pas démarrer : impossible d'inspecter le "
            f"schéma PostgreSQL ({exc})."
        ) from exc
    manquantes = [t for t in _TABLES_MINIMALES_POSTGRES if t not in existantes]
    if manquantes:
        raise RuntimeError(
            "WebIntel ne peut pas démarrer : schéma PostgreSQL incomplet, "
            f"tables manquantes : {', '.join(manquantes)}. "
            "La base est peut-être partiellement créée ou l'utilisateur "
            "n'a pas les droits nécessaires."
        )


def verifier_demarrage_postgres() -> None:
    """Validation complète au démarrage en mode PostgreSQL.

    1. `verifier_connexion_postgres()` AVANT `init_db()` ;
    2. `init_db()` (création du schéma) ;
    3. `verifier_schema_minimal_postgres()` APRÈS.

    À appeler depuis le lifespan de l'application quand `DATABASE_URL`
    pointe vers PostgreSQL.
    """
    verifier_connexion_postgres()
    init_db()
    verifier_schema_minimal_postgres()


def _ensure_simulation_audit_columns() -> None:
    """Ajoute idempotemment les colonnes d'audit de ``simulations``.

    ``create_all`` ne modifie jamais une table existante : cette fonction
    ajoute les colonnes introduites en Phase 13 (``executed_by``,
    ``inputs``) aux bases créées avant, via ``ALTER TABLE`` portable
    SQLite / PostgreSQL. Ignorée silencieusement si la table n'existe
    pas encore ou si la base est inaccessible (l'appelant gère).
    """
    from sqlalchemy import inspect, text

    try:
        colonnes = {c["name"] for c in inspect(engine).get_columns("simulations")}
    except Exception:
        return
    ajouts = {
        "executed_by": "VARCHAR(255)",
        "inputs": "JSON" if engine.dialect.name != "sqlite" else "TEXT",
    }
    manquantes = [
        (nom, type_sql)
        for nom, type_sql in ajouts.items()
        if nom not in colonnes
    ]
    if not manquantes:
        return
    with engine.begin() as connexion:
        for nom, type_sql in manquantes:
            connexion.execute(
                text(f'ALTER TABLE simulations ADD COLUMN "{nom}" {type_sql}')
            )
            if nom == "inputs":
                # Valeur par défaut pour les lignes pré-existantes.
                connexion.execute(
                    text("UPDATE simulations SET inputs = '{}' WHERE inputs IS NULL")
                )


# Colonnes v2.2 (publications locales + provenance locale) : table ->
# {colonne: type SQL}. Même motif idempotent que v2.1 (``ALTER TABLE``
# portable SQLite / PostgreSQL, colonnes toutes NULLABLE). Les nouvelles
# TABLES v2.2 (``specimens``, ``mesures_experimentales``) sont créées par
# ``create_all`` — seules les colonnes ajoutées à des tables EXISTANTES
# figurent ici.
_V22_COLONNES: dict[str, dict[str, str]] = {
    "evidences": {
        "document_type": "VARCHAR(64)",
        "institution": "VARCHAR(255)",
        "fichier_local": "TEXT",
        "identifiant_interne": "VARCHAR(32)",
        "langue": "VARCHAR(16)",
        "pays_edition": "VARCHAR(64)",
    },
    "provenances": {
        "acteur": "VARCHAR(255)",
        "lieu": "VARCHAR(255)",
        "commentaire": "TEXT",
        "outil": "VARCHAR(255)",
        "parametres": "JSON",
        "niveau_preuve": "VARCHAR(64)",
    },
    # v2.2 (extension modèle scientifique : statuts épistémiques) —
    # colonnes dédiées sur les relations composé→cible et plante→activité.
    # DataLevel reste FIGÉ (test `test_cinq_niveaux_distincts_et_stables`).
    "compound_targets": {
        "statut_epistemique": "VARCHAR(16)",
        "methode_statut": "TEXT",
    },
    "plant_activities": {
        "statut_epistemique": "VARCHAR(16)",
        "methode_statut": "TEXT",
    },
}


# Index de performance v2.2 (chantier robustesse) : {table: [colonnes]}.
# Créés idempotemment au démarrage (``CREATE INDEX IF NOT EXISTS``,
# portable SQLite / PostgreSQL). Les modèles déclarent aussi
# ``index=True`` pour les bases créées neuves via ``create_all`` ; cette
# migration couvre les bases EXISTANTES (créées avant l'ajout).
_V22_INDEX: dict[str, list[str]] = {
    "plants": ["family", "genus"],
    "compounds": ["formula", "chemical_class"],
    "plant_compounds": ["plant_id", "compound_id"],
    "combination_studies": ["project_id"],
    "docking_jobs": ["project_id"],
    "studies": ["year"],
    # NOTE (chantier PERSISTANCE + PERFORMANCE) : ``"evidences":
    # ["project_id"]`` a été RETIRÉ — la colonne n'existe pas dans le
    # modèle ; SQLite avait créé un index d'expression trompeur
    # ``ix_evidences_project_id`` (avertissement SQLAlchemy à la
    # réflexion). La migration ci-dessous vérifie désormais l'existence
    # des colonnes et supprime cet index invalide s'il existe.
}


# Index invalides connus, à supprimer s'ils existent (chantier
# PERSISTANCE + PERFORMANCE).
_INDEX_INVALIDES = (
    "ix_evidences_project_id",  # colonne project_id inexistante
)


def _ensure_fk_indexes() -> None:
    """Crée idempotemment un index sur chaque colonne de clé étrangère.

    Chantier performance (PERSISTANCE + PERFORMANCE) : une jointure ou
    une suppression en cascade sur une FK non indexée = parcours
    complet de la table enfant. La liste est dérivée dynamiquement des
    métadonnées SQLAlchemy — elle couvre les 45 FK historiquement sans
    index (``api_keys.project_id``, ``observations.source_id``, les FK
    ``evidence_id``/``project_id`` des tables terrain, ``langues``… non,
    les tables offline ``collectes_terrain``/``resolutions_conflit``…)
    ET toute future FK ajoutée, sans modifier les déclarations de
    modèles (additif, zéro régression).

    ``CREATE INDEX IF NOT EXISTS`` — portable SQLite / PostgreSQL,
    idempotent (bases existantes ET neuves : ``init_db()`` appelle
    ``create_all`` juste avant). Les colonnes déjà indexées (``index=True``,
    contrainte UNIQUE, index de la migration v2.2) sont ignorées.
    Ne fait jamais échouer le démarrage.
    """
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        tables_existantes = set(insp.get_table_names())
    except Exception as exc:
        logger.warning("index FK : inspection impossible (%s)", exc)
        return
    for table in Base.metadata.tables.values():
        if table.name not in tables_existantes:
            continue
        try:
            colonnes_existantes = {
                c["name"] for c in insp.get_columns(table.name)
            }
            indexes_couverts = {
                tuple(sorted(idx["column_names"]))
                for idx in insp.get_indexes(table.name)
            }
            uniques_couverts = {
                tuple(sorted(u["column_names"]))
                for u in insp.get_unique_constraints(table.name)
            }
        except Exception:
            continue
        for fkc in table.foreign_key_constraints:
            colonnes = [c.name for c in fkc.columns]
            if not colonnes:
                continue
            if any(c not in colonnes_existantes for c in colonnes):
                continue  # colonne ajoutée au modèle, absente de cette base
            cle = tuple(sorted(colonnes))
            if cle in indexes_couverts or cle in uniques_couverts:
                continue
            nom_index = f"ix_{table.name}_{'_'.join(colonnes)}"
            try:
                with engine.begin() as connexion:
                    liste_cols = ", ".join(f'"{c}"' for c in colonnes)
                    connexion.execute(
                        text(
                            f'CREATE INDEX IF NOT EXISTS "{nom_index}" '
                            f'ON "{table.name}" ({liste_cols})'
                        )
                    )
            except Exception as exc:
                logger.warning(
                    "index FK : création impossible de %s (%s)",
                    nom_index, exc,
                )


def _ensure_study_versions_unique() -> None:
    """Garantit l'unicité ``(study_id, numero)`` des versions d'étude.

    Chantier PERSISTANCE + PERFORMANCE : sans contrainte unique, deux
    exécutions concurrentes produisaient deux versions « n° N » (course
    constatée : numéros ``[1, 1, 2, 3, 4, 5, 5, 6]``).

    - Bases neuves : ``create_all`` a déjà créé la contrainte
      (``UniqueConstraint`` déclaré sur le modèle) — rien à faire.
    - Bases existantes : les doublons éventuels sont renumérotés
      (par ``id`` croissant : 1, 2, 3… — l'ordre de création est
      préservé, aucun snapshot n'est modifié), puis l'index unique est
      créé. Portable SQLite / PostgreSQL. Ne fait jamais échouer le
      démarrage.
    """
    from sqlalchemy import text

    try:
        with engine.begin() as connexion:
            tables = {
                r[0] for r in connexion.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                    if engine.dialect.name == "sqlite"
                    else text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public'"
                    )
                )
            }
            if "study_versions" not in tables:
                return
            # Doublons existants ?
            doublons = connexion.execute(
                text(
                    "SELECT study_id FROM study_versions "
                    "GROUP BY study_id, numero HAVING COUNT(*) > 1"
                )
            ).all()
            for (study_id,) in doublons:
                ids = [
                    r[0] for r in connexion.execute(
                        text(
                            "SELECT id FROM study_versions "
                            "WHERE study_id = :sid ORDER BY id"
                        ),
                        {"sid": study_id},
                    )
                ]
                for nouveau_numero, vid in enumerate(ids, start=1):
                    connexion.execute(
                        text(
                            "UPDATE study_versions SET numero = :num "
                            "WHERE id = :vid"
                        ),
                        {"num": nouveau_numero, "vid": vid},
                    )
                logger.warning(
                    "versions : %d doublon(s) de numéro renuméroté(s) "
                    "pour l'étude %s (unicité (study_id, numero)).",
                    len(ids), study_id,
                )
            connexion.execute(
                text(
                    'CREATE UNIQUE INDEX IF NOT EXISTS '
                    '"uq_study_versions_study_numero" '
                    'ON "study_versions" ("study_id", "numero")'
                )
            )
    except Exception as exc:
        logger.warning(
            "versions : unicité (study_id, numero) non garantie (%s)", exc
        )


def _ensure_noms_vernaculaires_unique() -> None:
    """Garantit l'unicité ``(plant_id, nom_normalise, langue)``.

    Chantier PERSISTANCE + PERFORMANCE : sans contrainte, deux
    insertions concurrentes du même nom vernaculaire créaient des
    doublons (``MultipleResultsFound`` constaté en test de concurrence).
    Les bases existantes sont dédupliquées (on garde la ligne la plus
    ancienne par clé) avant création de l'index unique. Portable
    SQLite / PostgreSQL. Ne fait jamais échouer le démarrage.
    """
    from sqlalchemy import text

    try:
        with engine.begin() as connexion:
            existe = connexion.execute(
                text(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name='noms_vernaculaires'"
                    if engine.dialect.name == "sqlite"
                    else "SELECT COUNT(*) FROM pg_tables "
                         "WHERE schemaname='public' "
                         "AND tablename='noms_vernaculaires'"
                )
            ).scalar()
            if not existe:
                return
            doublons = connexion.execute(
                text(
                    "SELECT plant_id, nom_normalise, langue "
                    "FROM noms_vernaculaires "
                    "GROUP BY plant_id, nom_normalise, langue "
                    "HAVING COUNT(*) > 1"
                )
            ).all()
            for plant_id, nom_normalise, langue in doublons:
                ids = [
                    r[0] for r in connexion.execute(
                        text(
                            "SELECT id FROM noms_vernaculaires "
                            "WHERE plant_id = :pid "
                            "AND nom_normalise = :nom "
                            "AND COALESCE(langue, '') = COALESCE(:lg, '') "
                            "ORDER BY id"
                        ),
                        {"pid": plant_id, "nom": nom_normalise,
                         "lg": langue},
                    )
                ]
                for vid in ids[1:]:
                    connexion.execute(
                        text("DELETE FROM noms_vernaculaires WHERE id=:vid"),
                        {"vid": vid},
                    )
                logger.warning(
                    "noms vernaculaires : %d doublon(s) supprimé(s) "
                    "(plante %s, nom %r).",
                    len(ids) - 1, plant_id, nom_normalise,
                )
            connexion.execute(
                text(
                    'CREATE UNIQUE INDEX IF NOT EXISTS '
                    '"uq_noms_vernaculaires_unicite" '
                    'ON "noms_vernaculaires" '
                    '("plant_id", "nom_normalise", "langue")'
                )
            )
    except Exception as exc:
        logger.warning(
            "noms vernaculaires : unicité non garantie (%s)", exc
        )


def _ensure_offline_sync_columns() -> None:
    """Ajoute idempotemment ``project_id`` à la file de synchronisation.

    Cloisonnement projet (P0) des tables offline créées avant cette
    colonne. Portable SQLite / PostgreSQL. Ne fait jamais échouer le
    démarrage (base verrouillée, table absente...).
    """
    from sqlalchemy import inspect, text

    try:
        from webintel.offline.models import COLONNES_MIGRATION
    except Exception:
        return
    for table, colonnes in COLONNES_MIGRATION.items():
        try:
            existantes = {c["name"] for c in inspect(engine).get_columns(table)}
        except Exception:
            continue
        manquantes = [nom for nom in colonnes if nom not in existantes]
        if not manquantes:
            continue
        try:
            with engine.begin() as connexion:
                for nom in manquantes:
                    connexion.execute(
                        text(f'ALTER TABLE {table} ADD COLUMN "{nom}" '
                             f"{colonnes[nom]}")
                    )
        except Exception:
            continue


def _ensure_v22_performance_indexes() -> None:
    """Crée idempotemment les index de performance v2.2.

    Ne fait jamais échouer le démarrage (base verrouillée, table
    absente...) : toute erreur est journalisée et ignorée.
    """
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        tables_existantes = set(insp.get_table_names())
    except Exception as exc:
        logger.warning("index v2.2 : inspection impossible (%s)", exc)
        return
    # Suppression des index invalides connus (ex. créé sur une colonne
    # inexistante) — idempotent, portable SQLite / PostgreSQL.
    for nom_index in _INDEX_INVALIDES:
        try:
            with engine.begin() as connexion:
                connexion.execute(
                    text(f'DROP INDEX IF EXISTS "{nom_index}"')
                )
        except Exception as exc:
            logger.warning(
                "index v2.2 : suppression impossible de %s (%s)",
                nom_index, exc,
            )
    for table, colonnes in _V22_INDEX.items():
        if table not in tables_existantes:
            continue
        try:
            colonnes_existantes = {
                c["name"] for c in insp.get_columns(table)
            }
        except Exception:
            continue
        for colonne in colonnes:
            if colonne not in colonnes_existantes:
                logger.warning(
                    "index v2.2 : colonne %s.%s absente — index ignoré",
                    table, colonne,
                )
                continue
            nom_index = f"ix_{table}_{colonne}"
            try:
                with engine.begin() as connexion:
                    connexion.execute(
                        text(
                            f'CREATE INDEX IF NOT EXISTS "{nom_index}" '
                            f'ON "{table}" ("{colonne}")'
                        )
                    )
            except Exception as exc:
                logger.warning(
                    "index v2.2 : création impossible de %s (%s)", nom_index, exc
                )


def _ensure_v22_science_columns() -> None:
    """Ajoute idempotemment les colonnes v2.2 aux tables existantes.

    Portable SQLite / PostgreSQL (``ALTER TABLE ... ADD COLUMN``).
    Les colonnes JSON sous SQLite sont déclarées en TEXT (le type JSON
    SQLAlchemy sérialise de toute façon). Unicité de
    ``evidences.identifiant_interne`` : garantie applicative
    (génération séquentielle) — aucun index unique ajouté ici pour
    rester portable sur les bases existantes.
    """
    from sqlalchemy import inspect, text

    for table, colonnes in _V22_COLONNES.items():
        try:
            existantes = {c["name"] for c in inspect(engine).get_columns(table)}
        except Exception:
            continue
        manquantes = [
            (nom, type_sql)
            for nom, type_sql in colonnes.items()
            if nom not in existantes
        ]
        if not manquantes:
            continue
        with engine.begin() as connexion:
            for nom, type_sql in manquantes:
                type_effectif = (
                    "TEXT"
                    if type_sql == "JSON" and engine.dialect.name == "sqlite"
                    else type_sql
                )
                connexion.execute(
                    text(f'ALTER TABLE {table} ADD COLUMN "{nom}" {type_effectif}')
                )
            # Statuts épistémiques (v2.2) : les lignes antérieures à la
            # migration sont des liens documentés — jamais NULL (le modèle
            # déclare la colonne NOT NULL avec défaut "documente").
            if "statut_epistemique" in dict(manquantes):
                connexion.execute(
                    text(
                        f"UPDATE {table} SET statut_epistemique = 'documente' "
                        "WHERE statut_epistemique IS NULL"
                    )
                )
# Colonnes v2.1 (modèle scientifique enrichi) : table -> {colonne: type SQL}.
# ``create_all`` ne touche jamais une table existante : ces colonnes sont
# ajoutées idempotemment aux bases créées avant la v2.1 (même motif que
# ``_ensure_simulation_audit_columns``). Toutes sont NULLABLE : une base
# existante reste valide sans migration de données.
_V21_COLONNES: dict[str, dict[str, str]] = {
    "plants": {
        "feuilles": "TEXT",
        "fleurs": "TEXT",
        "fruits": "TEXT",
        "graines": "TEXT",
        "racines": "TEXT",
        "ecorce": "TEXT",
        "habitat": "TEXT",
        "references_botaniques": "JSON",
        "matiere_premiere": "TEXT",
        "preparation": "TEXT",
    },
    "plant_compounds": {
        "analysis_method": "TEXT",
        "rendement": "FLOAT",
        "rendement_unite": "VARCHAR(32)",
        "evidence_id": "INTEGER",
    },
}


def _ensure_v21_science_columns() -> None:
    """Ajoute idempotemment les colonnes v2.1 aux tables existantes.

    Portable SQLite / PostgreSQL (``ALTER TABLE ... ADD COLUMN``).
    Les colonnes JSON sous SQLite sont déclarées en TEXT (le type JSON
    SQLAlchemy sérialise de toute façon) ; ``references_botaniques``
    reçoit ``'[]'`` pour les lignes pré-existantes afin de préserver le
    contrat « liste, jamais None ». Ignorée silencieusement si une table
    n'existe pas encore ou si la base est inaccessible.
    """
    from sqlalchemy import inspect, text

    for table, colonnes in _V21_COLONNES.items():
        try:
            existantes = {c["name"] for c in inspect(engine).get_columns(table)}
        except Exception:
            continue
        manquantes = [
            (nom, type_sql)
            for nom, type_sql in colonnes.items()
            if nom not in existantes
        ]
        if not manquantes:
            continue
        with engine.begin() as connexion:
            for nom, type_sql in manquantes:
                type_effectif = (
                    "TEXT"
                    if type_sql == "JSON" and engine.dialect.name == "sqlite"
                    else type_sql
                )
                connexion.execute(
                    text(f'ALTER TABLE {table} ADD COLUMN "{nom}" {type_effectif}')
                )
            if table == "plants" and "references_botaniques" in dict(manquantes):
                connexion.execute(
                    text(
                        "UPDATE plants SET references_botaniques = '[]' "
                        "WHERE references_botaniques IS NULL"
                    )
                )
