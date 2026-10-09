# WebIntel — Vision produit Phase 1 (documentation)

> **Statut de ce document** : vision produit. Elle décrit la direction que
> prend WebIntel, pas des fonctionnalités livrées. Tout ce qui relève de la
> vision est rédigé au conditionnel ou au futur ; ce qui existe déjà est
> décrit au présent et signalé comme tel.

## 1. Qui est le chercheur ?

Le chercheur type de WebIntel n'est **pas développeur**. C'est, par exemple :

- un pharmacien ou un phytochimiste qui étudie les plantes médicinales ;
- un doctorant en pharmacognosie qui constitue l'état de l'art de sa thèse ;
- un responsable R&D « produits naturels » qui surveille la littérature et le
  marché.

Il sait utiliser un navigateur, pas un terminal. Il installe le logiciel sur
sa machine (SQLite, zéro configuration), ouvre l'interface en français, et
travaille. Ses exigences :

1. **Trouver vite** : une recherche par nom de plante doit mener à une fiche
   structurée, pas à une pile de liens.
2. **Savoir d'où vient chaque information** : source, date, contexte —
   toujours visibles.
3. **Ne jamais être induit en erreur** : ce qui est publié, ce qui est une
   hypothèse et ce qui est une simulation doivent être visuellement
   impossibles à confondre.
4. **Garder ses propres observations** : résultats de laboratoire, notes de
   terrain, clairement séparés des données externes.

## 2. Parcours type (vision)

> Scénario cible — « recherche *Moringa oleifera* → fiche structurée ».

1. **Recherche** : le chercheur tape « Moringa oleifera » dans la barre de
   recherche. Le logiciel proposerait l'entité plante correspondante
   (nom latin, synonymes, famille botanique).
2. **Fiche structurée** : la fiche de la plante regrouperait, par niveau de
   preuve (voir [docs/donnees-scientifiques.md](donnees-scientifiques.md)) :
   - les **faits publiés** : usages traditionnels documentés, composés
     identifiés dans la littérature, avec référence de chaque source ;
   - les **données normalisées** : noms unifiés, unités harmonisées,
     doublons fusionnés ;
   - les **hypothèses** en cours (interprétations non vérifiées), badgées
     comme telles ;
   - les **prédictions** (résultats de simulations), portant la mention
     « à confirmer expérimentalement » ;
   - les **observations expérimentales** du chercheur lui-même (saisies ou
     importées, datées, signées).
3. **Traçabilité** : chaque ligne de la fiche afficherait sa source, sa date
   et son niveau. Un clic mènerait à la source d'origine.
4. **Surveillance** : le chercheur pourrait suivre la plante — toute
   nouvelle donnée collectée (nouvelle publication indexée, changement
   détecté sur une page suivie) générerait un événement et, selon ses
   règles, une alerte. *(La surveillance de pages web et les alertes
   existent déjà aujourd'hui : moniteurs, règles, canaux `log`/`webhook`.)*

## 3. Ce que le logiciel fait (aujourd'hui)

Fonctionnalités existantes, factuelles :

- **Crawl web borné** : collecte de pages avec limites (pages, profondeur,
  délai, concurrence), respect de `robots.txt`, protection anti-SSRF.
- **Analyse d'URL** : extraction de données structurées
  (JSON-LD/Microdata/OpenGraph), classification du type de page,
  extraction sémantique (produit, article, entreprise, contacts),
  normalisation (prix, dates, téléphones, URL), détection de technologies,
  audit SEO, score de santé.
- **Entités et observations** : entités à clés stables (identité uniquement
  dans `Entity.data`) et **observations sourcées par source**, horodatées,
  avec provenance (`source_id`, `source_url`, `source_domain`) et
  insertion idempotente.
- **Historique et diff** : snapshots append-only, diff intelligent
  (MAJOR/MINOR/COSMETIC/NONE), événements.
- **Surveillance et alertes** : moniteurs planifiés, règles JSON, alertes
  via `log` ou `webhook`.
- **Recherche et exports** : recherche d'entités avec filtres, exports
  JSON/CSV/XLSX.
- **Interface web en français** + API REST documentée (`/docs`).

## 4. Ce que le logiciel NE fait PAS (règles produit)

Ces règles sont non négociables, aujourd'hui comme dans la vision :

1. **Ne jamais présenter une hypothèse ou une prédiction comme un fait.**
   Un résultat de simulation reste une PREDICTION même si son statut
   interne indique « validé » ; seule une nouvelle observation
   expérimentale (nouvelle source) peut l'étayer.
2. **Ne jamais écrire qu'une donnée est « validée scientifiquement »**
   parce qu'elle est stockée dans le logiciel. Le stockage n'est pas une
   preuve ; le niveau de preuve est une étiquette explicite, jamais
   implicite.
3. **Ne jamais mélanger les niveaux** : données publiées, données
   normalisées, hypothèses, prédictions et observations expérimentales
   restent strictement séparées à l'affichage comme au stockage.
4. **Ne jamais promouvoir un niveau sans nouvelle source** : aucun
   changement de statut interne, aucun score, aucune « validation »
   automatique ne fait passer une donnée d'un niveau à un autre.
5. **Ne jamais inventer de source** : pas de donnée sans provenance ;
   une donnée orpheline est une donnée suspecte.
6. **Ne pas devenir une usine à gaz** : installation locale simple pour
   un non-développeur, interface en français, pas de jargon inutile.

Toute évolution future (fiches plantes, import de données de laboratoire,
connecteurs vers des bases scientifiques) sera jugée à l'aune de ces
règles avant d'être acceptée.
