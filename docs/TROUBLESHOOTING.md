# Dépannage — WebIntel v2.3.0

> **Ce qui est VRAI** : chaque problème ci-dessous correspond à un cas
> **réellement prévu par le code** (message d'erreur explicite, refus
> propre ou page de diagnostic). Les solutions sont celles que le code
> applique.

## 1. « Vina absent » — le docking refuse

**Symptôme** : page « Outils scientifiques » → Vina : indisponible ;
`POST /api/science/docking/...` répond un refus avec la raison explicite ;
l'aperçu du workflow affiche `scores: null`.

**C'est normal** : le binaire AutoDock Vina **n'est pas fourni** avec
WebIntel. Ce n'est pas un bug — c'est le garde-fou (jamais de faux score).

**Solution** : installez AutoDock Vina depuis son site officiel, vérifiez
`vina --version` dans le terminal, relancez l'application, contrôlez la
page « Outils scientifiques ».

## 2. « RDKit absent » — calculs indisponibles

**Symptôme** : « Outils scientifiques » → RDKit : indisponible ; les
fonctions de similarité/descripteurs répondent explicitement « non
calculable » ; la construction d'un dossier de combinaison **lève une
erreur explicite** au lieu de produire un dossier dégradé.

**Solution** :
```bash
./scripts/install_science.sh
```
Vérifiez : `.venv/bin/python -c "import rdkit; print(rdkit.__version__)"`.
Sans RDKit, le mode CORE reste utilisable (données, terrain, rapports) —
**aucune valeur n'est inventée**.

## 3. « PostgreSQL injoignable »

**Symptôme** : au démarrage avec `DATABASE_URL=<redacted>`,
l'application **échoue explicitement** (fail-fast) au lieu de basculer
silencieusement sur SQLite.

**Solutions** :
- Vérifiez que le serveur PG tourne et que l'URL est correcte
  (hôte, port, utilisateur, mot de passe, base existante).
- Testez la connexion : `.venv/bin/python scripts/test_postgres.py`
  (12 contrôles).
- En attendant, **retirez `DATABASE_URL`** pour redémarrer en SQLite local
  (données locales uniquement — pas de magie : ce sont deux bases
  distinctes).

## 4. « Port 8000 occupé »

**Symptôme** : `./start.sh` échoue avec « address already in use ».

**Solution** : `PORT=8080 ./start.sh` (ou tout port libre) →
http://127.0.0.1:8080.

## 5. « Clé API refusée » / login impossible

**Symptôme** : 401/403 sur l'API ou la page de login boucle.

**Solutions** :
- En mode serveur, vérifiez `REQUIRE_API_KEY=1` **et** la clé envoyée
  (en-tête `X-API-Key` ou selon votre configuration).
- Après **5 tentatives en 300 s**, le login est temporairement bloqué
  (rate limit) : attendez ou redémarrez avec `RATE_LIMIT_ENABLED=false`
  (dépannage uniquement).
- En mode local (`REQUIRE_API_KEY=0`), aucune clé n'est demandée : si
  l'UI en réclame une, c'est que le mode serveur est actif.

## 6. « Session wizard expirée »

**Symptôme** : reprise d'un brouillon d'étude impossible après ~30 jours.

**C'est le fonctionnement prévu** : les brouillons `en_cours` sont purgés
après **30 jours** d'inactivité (TTL). Les études **terminées** sont
conservées et restent accessibles. Pour les brouillons longs : finalisez
l'étude (elle sera conservée) ou exportez le projet (`/sauvegarde`).

## 7. Import qui échoue

**Causes réelles gérées par le code** :
- Fichier > 10 Mo (limite d'import) → réduisez ou découpez.
- Format déclaré ≠ contenu réel (ex. ZIP renommé `.csv`) → refus explicite.
- Lignes invalides : le rapport d'import indique **ligne par ligne** ce qui
  a été rejeté et pourquoi (jamais de correction silencieuse).

## 8. Conflit de synchronisation

**Symptôme** : une ligne de la file passe au statut `CONFLIT`.

**Solution** : page `/synchronisation` — comparez **version locale vs
version distante** et choisissez explicitement. Tant que le conflit n'est
pas résolu, la ligne n'est pas rejouée (pas de fusion automatique).

## 9. Lenteurs

- Les listes sont paginées (`limit`/`offset`) : si l'UI rame, réduisez
  `limit` (défaut 50, max 500).
- Les imports se font par paquets de 500 lignes : un gros fichier prend
  du temps, c'est normal (garde-fou mémoire).
- Vérifiez l'espace disque (`webintel_data/`, `webintel.db`, fichiers
  bruts jusqu'à 200 Mo/pièce par défaut).

## 10. Obtenir de l'aide utile

Avant de signaler un problème, notez :
1. La page « Outils scientifiques » (état RDKit/Meeko/Vina/PostgreSQL).
2. Le message d'erreur **exact** (les messages WebIntel sont explicites).
3. `LOG_LEVEL=DEBUG ./start.sh` pour des logs détaillés.
4. Version : v2.3.0 — `CHANGELOG.md` pour l'historique.

## Limites

- Ce guide couvre les cas prévus par le code. Un cas non listé n'est pas
  forcément un bug : consultez les logs avant tout.
- Aucun support officiel n'est organisé à ce jour : le dépannage repose
  sur cette documentation et les messages d'erreur explicites.
