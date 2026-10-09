# API — WebIntel v2.3.0

> **Ce qui est VRAI** : cette liste est **générée depuis l'OpenAPI
> du code** (`create_app().openapi()`), le 2026-09-23 : **215 chemins**,
> 261 opérations (GET 145 / POST 106 / PUT 2 / DELETE 4). Aucun
> endpoint n'est inventé. Authentification : clé API en mode serveur
> (`REQUIRE_API_KEY=1`) ; isolation par projet (accès croisé → 403) ;
> pagination `limit` (1...500, défaut 50) / `offset` sur les listes.
>
> Spécification interactive : `/docs` (Swagger) et `/redoc` sur
> l'instance en cours d'exécution.

## API /alerts (1 opérations)

- `GET   ` `/api/alerts`

## API /analyze (1 opérations)

- `POST  ` `/api/analyze`

## API /crawls (6 opérations)

- `GET   ` `/api/crawls`
- `POST  ` `/api/crawls`
- `GET   ` `/api/crawls/{job_id}`
- `POST  ` `/api/crawls/{job_id}/cancel`
- `POST  ` `/api/crawls/{job_id}/pause`
- `POST  ` `/api/crawls/{job_id}/resume`

## API /entities (3 opérations)

- `GET   ` `/api/entities`
- `GET   ` `/api/entities/{entity_id}`
- `GET   ` `/api/entities/{entity_id}/history`

## API /events (1 opérations)

- `GET   ` `/api/events`

## API /exports (2 opérations)

- `POST  ` `/api/exports`
- `GET   ` `/api/exports/{export_id}/download`

## API /health (1 opérations)

- `GET   ` `/api/health`

## API /monitors (5 opérations)

- `GET   ` `/api/monitors`
- `POST  ` `/api/monitors`
- `DELETE` `/api/monitors/{monitor_id}`
- `GET   ` `/api/monitors/{monitor_id}`
- `POST  ` `/api/monitors/{monitor_id}/run`

## API /projects (5 opérations)

- `GET   ` `/api/projects`
- `POST  ` `/api/projects`
- `GET   ` `/api/projects/{project_id}`
- `GET   ` `/api/projects/{project_id}/api-keys`
- `POST  ` `/api/projects/{project_id}/api-keys`

## API /science/combinaisons (7 opérations)

- `GET   ` `/api/science/combinaisons`
- `POST  ` `/api/science/combinaisons`
- `GET   ` `/api/science/combinaisons/versions/{version_a}/comparer/{version_b}`
- `GET   ` `/api/science/combinaisons/{etude_id}`
- `GET   ` `/api/science/combinaisons/{etude_id}/rapport`
- `GET   ` `/api/science/combinaisons/{etude_id}/versions`
- `POST  ` `/api/science/combinaisons/{etude_id}/versions`

## API /science/compounds (6 opérations)

- `GET   ` `/api/science/compounds`
- `POST  ` `/api/science/compounds`
- `GET   ` `/api/science/compounds/{compound_id}`
- `POST  ` `/api/science/compounds/{compound_id}/normaliser`
- `GET   ` `/api/science/compounds/{compound_id}/targets`
- `POST  ` `/api/science/compounds/{compound_id}/targets`

## API /science/connecteurs (2 opérations)

- `GET   ` `/api/science/connecteurs`
- `POST  ` `/api/science/connecteurs/{nom}/importer`

## API /science/data-levels (1 opérations)

- `GET   ` `/api/science/data-levels`

## API /science/datasets (2 opérations)

- `GET   ` `/api/science/datasets`
- `POST  ` `/api/science/datasets`

## API /science/docking (5 opérations)

- `GET   ` `/api/science/docking`
- `POST  ` `/api/science/docking/apercu`
- `POST  ` `/api/science/docking/executer`
- `GET   ` `/api/science/docking/statut`
- `GET   ` `/api/science/docking/{job_id}`

## API /science/evidences (2 opérations)

- `GET   ` `/api/science/evidences`
- `POST  ` `/api/science/evidences`

## API /science/export (5 opérations)

- `GET   ` `/api/science/export/composes`
- `POST  ` `/api/science/export/dossier`
- `GET   ` `/api/science/export/evidences`
- `GET   ` `/api/science/export/fiche/{plant_id}`
- `GET   ` `/api/science/export/plantes`

## API /science/ia (2 opérations)

- `GET   ` `/api/science/ia/statut`
- `GET   ` `/api/science/ia/synthese/plante/{plant_id}`

## API /science/import (1 opérations)

- `POST  ` `/api/science/import/`

## API /science/jobs (4 opérations)

- `POST  ` `/api/science/jobs/docking`
- `POST  ` `/api/science/jobs/simulations`
- `GET   ` `/api/science/jobs/{job_id}`
- `POST  ` `/api/science/jobs/{job_id}/annuler`

## API /science/lab (9 opérations)

- `GET   ` `/api/science/lab/notes`
- `POST  ` `/api/science/lab/notes`
- `GET   ` `/api/science/lab/notes/{note_id}`
- `PUT   ` `/api/science/lab/notes/{note_id}`
- `GET   ` `/api/science/lab/notes/{note_id}/versions`
- `PATCH ` `/api/science/lab/observations/{observation_id}`
- `POST  ` `/api/science/lab/publier`
- `PATCH ` `/api/science/lab/simulations/{simulation_id}`
- `GET   ` `/api/science/lab/tableau-de-bord`

## API /science/observations (2 opérations)

- `GET   ` `/api/science/observations`
- `POST  ` `/api/science/observations`

## API /science/plant-compounds (2 opérations)

- `GET   ` `/api/science/plant-compounds/{link_id}/preuve`
- `POST  ` `/api/science/plant-compounds/{link_id}/preuve`

## API /science/plants (8 opérations)

- `GET   ` `/api/science/plants`
- `POST  ` `/api/science/plants`
- `GET   ` `/api/science/plants/{plant_id}`
- `GET   ` `/api/science/plants/{plant_id}/activities`
- `POST  ` `/api/science/plants/{plant_id}/activities`
- `GET   ` `/api/science/plants/{plant_id}/compounds`
- `POST  ` `/api/science/plants/{plant_id}/compounds`
- `GET   ` `/api/science/plants/{plant_id}/fiche`

## API /science/provenance (2 opérations)

- `GET   ` `/api/science/provenance`
- `POST  ` `/api/science/provenance`

## API /science/qualite (1 opérations)

- `GET   ` `/api/science/qualite`

## API /science/rapport (2 opérations)

- `GET   ` `/api/science/rapport`
- `GET   ` `/api/science/rapport/zip`

## API /science/recherche (1 opérations)

- `GET   ` `/api/science/recherche`

## API /science/simulations (7 opérations)

- `GET   ` `/api/science/simulations`
- `POST  ` `/api/science/simulations`
- `GET   ` `/api/science/simulations/moteurs`
- `POST  ` `/api/science/simulations/run`
- `GET   ` `/api/science/simulations/{simulation_id}`
- `GET   ` `/api/science/simulations/{simulation_id}/rapport`
- `POST  ` `/api/science/simulations/{simulation_id}/rejouer`

## API /science/studies (3 opérations)

- `GET   ` `/api/science/studies`
- `POST  ` `/api/science/studies`
- `GET   ` `/api/science/studies/{study_id}`

## API /science/synergie (4 opérations)

- `GET   ` `/api/science/synergie`
- `POST  ` `/api/science/synergie/analyser`
- `GET   ` `/api/science/synergie/methodes`
- `GET   ` `/api/science/synergie/{analyse_id}`

## API /science/terrain (32 opérations)

- `PATCH ` `/api/science/terrain/activites/{activite_id}/statut`
- `GET   ` `/api/science/terrain/analyses`
- `POST  ` `/api/science/terrain/analyses`
- `POST  ` `/api/science/terrain/analyses/{analyse_id}/fichier`
- `GET   ` `/api/science/terrain/analyses/{analyse_id}/integrite`
- `GET   ` `/api/science/terrain/analyses/{analyse_id}/pics`
- `POST  ` `/api/science/terrain/analyses/{analyse_id}/pics`
- `PATCH ` `/api/science/terrain/cibles/{cible_id}/statut`
- `POST  ` `/api/science/terrain/echantillons`
- `DELETE` `/api/science/terrain/echantillons/{echantillon_id}`
- `GET   ` `/api/science/terrain/echantillons/{echantillon_id}`
- `GET   ` `/api/science/terrain/echantillons/{echantillon_id}/chaine`
- `POST  ` `/api/science/terrain/echantillons/{echantillon_id}/voucher`
- `GET   ` `/api/science/terrain/export-public/plantes/{plant_id}`
- `POST  ` `/api/science/terrain/extractions`
- `POST  ` `/api/science/terrain/extraits`
- `POST  ` `/api/science/terrain/fractions`
- `POST  ` `/api/science/terrain/fractions/{fraction_id}/composes`
- `GET   ` `/api/science/terrain/langues`
- `POST  ` `/api/science/terrain/langues`
- `POST  ` `/api/science/terrain/noms-vernaculaires`
- `DELETE` `/api/science/terrain/noms-vernaculaires/{nom_id}`
- `GET   ` `/api/science/terrain/plantes/{plant_id}/echantillons`
- `GET   ` `/api/science/terrain/plantes/{plant_id}/noms-vernaculaires`
- `GET   ` `/api/science/terrain/plantes/{plant_id}/pharmacognosie`
- `PUT   ` `/api/science/terrain/plantes/{plant_id}/pharmacognosie`
- `GET   ` `/api/science/terrain/plantes/{plant_id}/usages`
- `GET   ` `/api/science/terrain/recherche`
- `POST  ` `/api/science/terrain/usages`
- `DELETE` `/api/science/terrain/usages/{usage_id}`
- `GET   ` `/api/science/terrain/vouchers`
- `POST  ` `/api/science/terrain/vouchers`

## API /science/workflows (5 opérations)

- `GET   ` `/api/science/workflows`
- `POST  ` `/api/science/workflows`
- `GET   ` `/api/science/workflows/{run_id}`
- `POST  ` `/api/science/workflows/{run_id}/avancer`
- `POST  ` `/api/science/workflows/{run_id}/rejouer`

## API /search (1 opérations)

- `GET   ` `/api/search`

## API /sources (2 opérations)

- `GET   ` `/api/sources`
- `GET   ` `/api/sources/{source_id}`

## API /usage (1 opérations)

- `GET   ` `/api/usage`

## UI / (racine) (1 opérations)

- `GET   ` `/`

## UI /accueil (1 opérations)

- `GET   ` `/accueil`

## UI /alertes (1 opérations)

- `GET   ` `/alertes`

## UI /analyser (2 opérations)

- `GET   ` `/analyser`
- `POST  ` `/analyser`

## UI /carte (1 opérations)

- `GET   ` `/carte`

## UI /collectes (6 opérations)

- `GET   ` `/collectes`
- `GET   ` `/collectes/nouvelle`
- `POST  ` `/collectes/nouvelle`
- `GET   ` `/collectes/{collecte_id}`
- `GET   ` `/collectes/{collecte_id}/photos/{nom}`
- `POST  ` `/collectes/{collecte_id}/supprimer`

## UI /combinaisons (4 opérations)

- `GET   ` `/combinaisons`
- `POST  ` `/combinaisons/creer`
- `GET   ` `/combinaisons/nouvelle`
- `GET   ` `/combinaisons/{etude_id}`

## UI /connecteurs (2 opérations)

- `GET   ` `/connecteurs`
- `POST  ` `/connecteurs/{nom}/importer`

## UI /crawls (3 opérations)

- `GET   ` `/crawls`
- `POST  ` `/crawls`
- `GET   ` `/crawls/{job_id}`

## UI /docking (3 opérations)

- `GET   ` `/docking`
- `POST  ` `/docking`
- `GET   ` `/docking/{job_id}`

## UI /donnees (3 opérations)

- `POST  ` `/donnees/associer`
- `GET   ` `/donnees/importer`
- `POST  ` `/donnees/importer`

## UI /entites (2 opérations)

- `GET   ` `/entites`
- `GET   ` `/entites/{entity_id}`

## UI /etudes (16 opérations)

- `POST  ` `/etudes/etape1`
- `GET   ` `/etudes/etape2`
- `POST  ` `/etudes/etape2`
- `GET   ` `/etudes/etape3`
- `POST  ` `/etudes/etape3`
- `GET   ` `/etudes/etape4`
- `GET   ` `/etudes/etape6`
- `POST  ` `/etudes/lancer`
- `GET   ` `/etudes/nouvelle`
- `GET   ` `/etudes/{etude_id}/historique`
- `POST  ` `/etudes/{etude_id}/historique/instantane`
- `POST  ` `/etudes/{etude_id}/historique/restaurer`
- `GET   ` `/etudes/{etude_id}/rapport`
- `POST  ` `/etudes/{etude_id}/rapport`
- `GET   ` `/etudes/{etude_id}/rapport/telecharger`
- `GET   ` `/etudes/{etude_id}/resultats`

## UI /evenements (1 opérations)

- `GET   ` `/evenements`

## UI /export (4 opérations)

- `GET   ` `/export`
- `POST  ` `/export/dossier`
- `POST  ` `/export/fiche`
- `POST  ` `/export/lot`

## UI /exports (2 opérations)

- `GET   ` `/exports`
- `POST  ` `/exports`

## UI /import (2 opérations)

- `GET   ` `/import`
- `POST  ` `/import`

## UI /journal (5 opérations)

- `GET   ` `/journal`
- `POST  ` `/journal`
- `GET   ` `/journal/{note_id}`
- `POST  ` `/journal/{note_id}`
- `GET   ` `/journal/{note_id}/versions`

## UI /laboratoire (2 opérations)

- `GET   ` `/laboratoire`
- `POST  ` `/laboratoire/publier`

## UI /login (2 opérations)

- `GET   ` `/login`
- `POST  ` `/login`

## UI /logout (1 opérations)

- `GET   ` `/logout`

## UI /mes-recherches (1 opérations)

- `GET   ` `/mes-recherches`

## UI /moniteurs (4 opérations)

- `GET   ` `/moniteurs`
- `POST  ` `/moniteurs`
- `POST  ` `/moniteurs/{monitor_id}/delete`
- `POST  ` `/moniteurs/{monitor_id}/run`

## UI /outils-scientifiques (1 opérations)

- `GET   ` `/outils-scientifiques`

## UI /parametres (2 opérations)

- `GET   ` `/parametres`
- `POST  ` `/parametres`

## UI /plante-etude (2 opérations)

- `GET   ` `/plante-etude`
- `POST  ` `/plante-etude/creer`

## UI /plantes (16 opérations)

- `GET   ` `/plantes`
- `GET   ` `/plantes/{plant_id}`
- `POST  ` `/plantes/{plant_id}/activites/ajouter`
- `POST  ` `/plantes/{plant_id}/botanique`
- `POST  ` `/plantes/{plant_id}/cibles/ajouter`
- `POST  ` `/plantes/{plant_id}/composes/ajouter`
- `POST  ` `/plantes/{plant_id}/etudes/ajouter`
- `GET   ` `/plantes/{plant_id}/synthese-ia`
- `GET   ` `/plantes/{plant_id}/terrain`
- `POST  ` `/plantes/{plant_id}/terrain/analyses`
- `POST  ` `/plantes/{plant_id}/terrain/echantillons`
- `POST  ` `/plantes/{plant_id}/terrain/extractions`
- `POST  ` `/plantes/{plant_id}/terrain/noms`
- `POST  ` `/plantes/{plant_id}/terrain/pharmacognosie`
- `POST  ` `/plantes/{plant_id}/terrain/usages`
- `POST  ` `/plantes/{plant_id}/terrain/vouchers`

## UI /qualite (1 opérations)

- `GET   ` `/qualite`

## UI /rapport (2 opérations)

- `GET   ` `/rapport`
- `GET   ` `/rapport/telecharger`

## UI /recherche (1 opérations)

- `GET   ` `/recherche`

## UI /recherche-science (1 opérations)

- `GET   ` `/recherche-science`

## UI /recherche-terrain (1 opérations)

- `GET   ` `/recherche-terrain`

## UI /recherche-universelle (1 opérations)

- `GET   ` `/recherche-universelle`

## UI /resultats (1 opérations)

- `GET   ` `/resultats/{type_resultat}/{resultat_id}/provenance`

## UI /sauvegarde (2 opérations)

- `POST  ` `/sauvegarde/exporter`
- `POST  ` `/sauvegarde/importer`

## UI /simulations (3 opérations)

- `GET   ` `/simulations`
- `POST  ` `/simulations/run`
- `GET   ` `/simulations/{simulation_id}/rapport`

## UI /synchronisation (4 opérations)

- `GET   ` `/synchronisation`
- `POST  ` `/synchronisation/action`
- `GET   ` `/synchronisation/conflits/{file_id}`
- `POST  ` `/synchronisation/conflits/{file_id}/resoudre`

## UI /synergie (5 opérations)

- `GET   ` `/synergie/importer`
- `POST  ` `/synergie/importer`
- `GET   ` `/synergie/nouvelle`
- `POST  ` `/synergie/nouvelle`
- `GET   ` `/synergie/{analyse_id}`

## UI /workflows (5 opérations)

- `GET   ` `/workflows`
- `POST  ` `/workflows`
- `GET   ` `/workflows/{workflow_id}`
- `POST  ` `/workflows/{workflow_id}/avancer`
- `POST  ` `/workflows/{workflow_id}/rejouer`

## Limites

- La liste reflète le code au jour de la génération ; après toute
  modification du code, régénérez via l'endpoint `/openapi.json`.
- Les endpoints `/api/science/ia/*` exigent `WEBINTEL_AI_ENABLED=true`.
- Le docking (`/api/science/docking/*`) refuse proprement sans binaire Vina.
- Les données `experimental`/`prediction` exigent un `project_id`.
