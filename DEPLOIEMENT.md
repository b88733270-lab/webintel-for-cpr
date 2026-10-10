# Déployer WebIntel gratuitement (Render + Supabase)

Objectif : WebIntel en ligne, gratuitement, en ~15 minutes.
Limites de l'offre gratuite : l'app s'endort après ~15 min sans visite
(~30 s de réveil), pas de garantie de disponibilité. Parfait pour une démo.

## 1. Base de données (Supabase, gratuit)

1. Crée un compte sur supabase.com (gratuit, sans carte bancaire).
2. **New project** → nomme-le `webintel`, choisis un mot de passe base de données
   (note-le), région la plus proche (Europe de l'Ouest).
3. Attends la fin de la création (~2 min).
4. Va dans **Project Settings → Database** → rubrique **Connection string**.
   Dans le sélecteur de type, choisis **Session pooler** (surtout pas
   « Direct connection » ni « Transaction ») → copie l'URI affichée.
   Elle ressemble à :
   `postgresql://postgres.[REF-PROJET]:[TON-MOT-DE-PASSE]@aws-0-xxxxx.pooler.supabase.com:5432/postgres`
   Remplace `[TON-MOT-DE-PASSE]` par ton mot de passe (simple, sans `@`,
   `#` ni `/`).
   Pourquoi le pooler et pas l'URI directe ? L'hôte direct
   (`db.xxxxx.supabase.co`) ne répond qu'en IPv6 et Render (offre gratuite)
   ne parle qu'IPv4 : la connexion est impossible. Le pooler est en IPv4,
   c'est lui qu'il faut pour Render. Note aussi que sur le pooler le nom
   d'utilisateur est `postgres.[REF-PROJET]` (avec la référence du projet),
   pas `postgres` seul.

## 2. Application (Render, gratuit)

1. Crée un compte sur render.com (**Sign up with GitHub**).
2. **New → Web Service** → **Connect** ton dépôt `webintel-for-cpr`.
3. Render détecte automatiquement le fichier `render.yaml` du dépôt.
4. Dans **Environment**, renseigne la variable `DATABASE_URL` avec l'URI
   Supabase copiée à l'étape 1 (les autres variables sont déjà réglées).
5. **Create Web Service** → le déploiement démarre (~5-10 min la première fois :
   installation de RDKit et des dépendances scientifiques).

## 3. Vérifier

- Ton app est en ligne sur `https://webintel.onrender.com` (ou le nom choisi).
- Ouvre `/docs` pour vérifier que l'API répond.
- Dans **Paramètres → Services & clés API**, vérifie le statut des providers.

## 4. Après le déploiement

- **Ne perds pas** ton mot de passe Supabase (il est dans l'URI `DATABASE_URL`).
- Pour réveiller l'app endormie plus vite, un service gratuit comme UptimeRobot
  peut la « pinger » toutes les 5 minutes.
- Quand le projet grandit : Render et Supabase ont des offres payantes
  (~7 $/mois) qui enlèvent la mise en veille et les limites.

## Dépannage

- **Le build échoue sur RDKit** : c'est le paquet le plus lourd. Relance le
  déploiement (bouton **Manual Deploy**) — c'est souvent transitoire.
- **L'app démarre mais n'affiche rien** : vérifie que `DATABASE_URL` est bien
  renseignée et que le mot de passe ne contient pas de caractère spécial
  non encodé (`@`, `#`, `/` → à remplacer par leur code `%40`, `%23`, `%2F`).
- **« WebIntel ne peut pas démarrer : PostgreSQL est injoignable »** : dans
  9 cas sur 10, `DATABASE_URL` pointe vers le mauvais hôte. Elle doit utiliser
  le **pooler** (`....pooler.supabase.com`, utilisateur
  `postgres.[ref-projet]`, étape 1.4 ci-dessus), jamais l'hôte direct
  `db.xxxx.supabase.co` (IPv6 uniquement, injoignable depuis Render).
  Si la cause mentionne « Tenant or user not found » : projet Supabase en
  pause (1 clic pour réveiller) OU utilisateur incorrect sur le pooler
  (doit être `postgres.[ref-projet]`, pas `postgres` seul).
- **Erreur base de données** : dans Supabase, vérifie que le projet est bien
  « Active » (un projet en pause se réactive en 1 clic).
