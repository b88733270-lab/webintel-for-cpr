# IA — synthèse automatique et désactivation complète (Phase 11)

## Ce que fait l'IA dans WebIntel

WebIntel ne contient **aucun modèle génératif externe** (pas d'appel à un
fournisseur de LLM, pas de clé API tierce, pas de réseau). La seule fonction
d'IA est la **synthèse automatique d'une fiche plante** :

- **API** : `GET /api/science/ia/synthese/plante/{plant_id}`
- **Interface** : section « Synthèse IA » de la fiche plante, page
  `/plantes/{plant_id}/synthese-ia`

La synthèse est un **assemblage déterministe** de phrases construites à
partir des données **déjà enregistrées** :

- les preuves documentaires (`Evidence`) liées à la plante (même règle de
  liaison que la fiche : DOI commun avec un lien `PlantCompound`) ;
- les liens plante-composé enregistrés (`PlantCompound`).

**Aucune information nouvelle n'est inventée.** Chaque affirmation cite sa
source (référence, DOI, URL). Les observations **privées** du laboratoire
(`ResearchObservation`) ne sont **jamais** utilisées.

## Garanties d'honnêteté scientifique

- Le résultat porte **toujours** le niveau de donnée `hypothese`
  (badge « hypothèse — généré par IA, à vérifier »), jamais `publie`.
- Il est **toujours** accompagné de son avertissement :
  « Texte généré automatiquement par assemblage des sources citées...
  HYPOTHÈSE de travail, pas une preuve scientifique. »
- Il est **éphémère** : jamais persisté en base (`"persiste": false`).
  Une synthèse générée ne peut donc jamais devenir un fait.
- Sans source liée, la synthèse le dit explicitement au lieu d'inventer.

## Désactivation complète

Un garde-fou global coupe **toutes** les fonctions d'IA :

| Mécanisme | Effet | Portée |
|---|---|---|
| Variable d'environnement `WEBINTEL_AI_ENABLED=false` | IA coupée au démarrage | Lue **au démarrage** (comme tous les réglages) : un redémarrage est nécessaire |
| Page **Paramètres** (case à cocher) | IA coupée **immédiatement**, sans redémarrage | Écrit un override dans `webintel_data/runtime_config.json`, qui prend le pas sur la variable d'environnement ; le bouton « Réinitialiser » rend la main à la variable |

Quand l'IA est désactivée :

- **l'interface ne montre plus aucune fonction d'IA** (la section
  « Synthèse IA » disparaît de la fiche plante — pas de bouton fantôme) ;
- **l'API répond 503** `{"detail": "Fonctionnalité d'IA désactivée : ..."}` ;
- **aucun calcul ni appel n'est effectué** : `synthese_plante()` lève
  `IaDesactiveeError` *avant* tout accès aux données.

État du garde-fou (et origine de la décision : `interface`,
`variable_environnement` ou `defaut`) : `GET /api/science/ia/statut`
et page « Paramètres ».

## Limites honnêtes

- La synthèse est un assemblage de phrases pré-écrites, pas une analyse
  sémantique : elle ne « comprend » pas les publications, elle les cite.
- Aucune validation chimique ou médicale n'est effectuée ; la synthèse ne
  constitue ni un avis scientifique définitif, ni un avis médical.
- La désactivation via la variable d'environnement exige un redémarrage
  (cohérent avec tous les autres réglages de WebIntel).
