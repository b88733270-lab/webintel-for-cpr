# Mode hors-ligne — WebIntel v2.3.0

> **Ce qui est VRAI** : WebIntel est **offline-first**. Le mode local
> (SQLite) fonctionne sans internet ; une file de synchronisation enregistre
> les opérations à répercuter ; les conflits sont détectés (LOCAL / DISTANT /
> CONFLIT) et résolus **explicitement** par le chercheur — jamais
> automatiquement.

## 1. Ce qui fonctionne sans internet

- **Tout le mode local** : base SQLite `./webintel.db`, interface complète,
  wizard d'étude, recherches, rapports, chimoinformatique RDKit (locale),
  docking Vina (binaire local).
- **Ce qui ne fonctionne pas** : les connecteurs externes (PubChem, PubMed,
  OpenAlex, ChEMBL, GBIF), le crawl web, et toute fonction réseau — avec
  **dégradation propre** (message explicite, pas de plantage silencieux ;
  tests `test_offline_degradation.py`).

## 2. La file de synchronisation

**Ce qui est VRAI** (`webintel/offline/synchronisation.py`) : les opérations
effectuées hors-ligne sont enregistrées dans une **file de synchronisation**
persistée. À la reconnexion, elles sont rejouées vers la cible (serveur /
PostgreSQL) dans l'ordre, avec suivi de statut.

Chaque ligne de la file porte un **statut de synchronisation**
(`StatutSync`, `webintel/offline/models.py`) :

| Statut | Signification |
|---|---|
| `LOCAL` | Existe localement, pas encore synchronisé |
| `DISTANT` | Synchronisé avec la cible |
| `CONFLIT` | Les deux côtés ont modifié — **résolution manuelle requise** |

## 3. Conflits : LOCAL vs DISTANT, décision humaine

**Ce qui est VRAI** : quand la même donnée a été modifiée des deux côtés,
le statut passe à `CONFLIT` et la file conserve **les deux versions**
(version locale + version distante reçue) pour affichage comparé.

La résolution est **toujours explicite** : le chercheur choisit quelle
version prévaut (voir `synchronisation.py` — une ligne en conflit ne peut
pas être rejouée tant qu'elle n'est pas résolue). **Aucune fusion
automatique** : c'est la même règle que pour les taxons et les molécules
(voir [`DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md)).

Interface : page `/synchronisation` (4 routes UI) — file d'attente,
statuts, résolution des conflits.

## 4. Sauvegarde locale

**Ce qui est VRAI** (`webintel/offline/sauvegarde.py`) : export d'un projet
complet en ZIP (`exporter_projet`) et réimport (`importer_projet`), avec
**round-trip vérifié par test** sur base fraîche (12 tables relues champ à
champ, SQLite et PostgreSQL). C'est le mécanisme recommandé avant toute
mission de terrain : exportez, partez, travaillez hors-ligne, réimportez.

Page UI : `/sauvegarde` (export / import).

## 5. Cartographie hors-ligne

La cartographie (`webintel/offline/cartographie.py`) fonctionne sur les
données locales, avec le **même masquage GPS** qu'en ligne (voir
[`SAMPLE_MANAGEMENT.md`](SAMPLE_MANAGEMENT.md) §4). Tests :
`test_offline_collecte_carte.py`.

## Limites

- La synchronisation suppose une **cible joignable** au moment du rejeu ;
  il n'y a pas de protocole temps réel multi-utilisateurs (pas de
  collaboration simultanée façon Google Docs).
- En cas de conflit, **c'est au chercheur de trancher** : le logiciel
  présente les deux versions, il ne devine pas.
- Les photos de terrain (`webintel/offline/photos.py`) sont stockées
  localement : prévoyez l'espace disque en mission longue.
- Le mode avion n'empêche pas les erreurs humaines : sauvegardez
  (`/sauvegarde`) avant de partir.
