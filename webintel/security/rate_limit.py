"""Rate-limiting HTTP — fenêtre glissante en mémoire, par (bucket, client).

Deux usages :

- ``bucket="login"`` (anti-brute-force) : seuls les ÉCHECS d'authentification
  comptent. Après ``LOGIN_MAX_TENTATIVES`` échecs dans ``LOGIN_FENETRE_S``
  secondes, le client est verrouillé temporairement (429 + message français).
  Un succès réinitialise le compteur du client.
- buckets génériques (ex. ``"api_couteux"``) : toutes les requêtes comptent ;
  protège les endpoints coûteux (simulations, docking, imports, exports
  PDF/ZIP) contre l'abus.

Conception volontairement simple (pas de dépendance externe) :

- fenêtre glissante exacte (pas de compteur à fenêtre fixe) ;
- état en mémoire, protégé par un verrou (thread-safe) ;
- clés = ``f"{bucket}:{ip_client}"`` (IP vue par l'application) ;
- configurable par variables d'environnement (voir ``config/settings.py``) ;
- désactivable : ``RATE_LIMIT_ENABLED=false`` (utilisé par la suite de tests).

En production multi-processus, chaque processus a son propre compteur
(limite indicative) : le verrouillage anti-brute-force reste efficace car
un attaquant frappe le même processus via le répartiteur de charge, et la
limite est basse (5 échecs). Documenté dans ``docs/securite.md``.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class DecisionLimite:
    """Résultat d'une vérification : autorisé ou non + délai avant réessai."""

    autorise: bool
    reessayer_dans_s: float = 0.0


class LimiteurFenetreGlissante:
    """Compteur à fenêtre glissante, en mémoire, thread-safe.

    Pour chaque clé, on conserve les horodatages (``time.monotonic``) des
    événements dans la fenêtre ; un événement trop ancien est éjecté.
    Complexité O(1) amorti par vérification.
    """

    def __init__(self) -> None:
        self._verrou = threading.Lock()
        self._evenements: dict[str, deque[float]] = {}

    def verifier(
        self, cle: str, limite: int, fenetre_s: float, maintenant: float | None = None
    ) -> DecisionLimite:
        """Vérifie sans enregistrer (dry-run)."""
        now = time.monotonic() if maintenant is None else maintenant
        with self._verrou:
            file = self._evenements.get(cle)
            if file is None:
                return DecisionLimite(True, 0.0)
            limite_basse = now - fenetre_s
            while file and file[0] <= limite_basse:
                file.popleft()
            if len(file) >= limite:
                return DecisionLimite(False, max(0.0, file[0] + fenetre_s - now))
            return DecisionLimite(True, 0.0)

    def enregistrer(
        self, cle: str, limite: int, fenetre_s: float, maintenant: float | None = None
    ) -> DecisionLimite:
        """Enregistre un événement puis vérifie (check-and-hit atomique)."""
        now = time.monotonic() if maintenant is None else maintenant
        with self._verrou:
            file = self._evenements.setdefault(cle, deque())
            limite_basse = now - fenetre_s
            while file and file[0] <= limite_basse:
                file.popleft()
            file.append(now)
            if len(file) > limite:
                # L'événement qu'on vient d'ajouter dépasse la limite :
                # on le retire (il n'a pas eu lieu) et on refuse.
                file.pop()
                return DecisionLimite(False, max(0.0, file[0] + fenetre_s - now))
            return DecisionLimite(True, 0.0)

    def reinitialiser(self, cle: str | None = None) -> None:
        """Réinitialise le compteur d'une clé (ou tout, si ``cle`` est None).

        Utilisé après un login réussi (anti-brute-force) et par les tests
        pour garantir l'isolation entre les cas.
        """
        with self._verrou:
            if cle is None:
                self._evenements.clear()
            else:
                self._evenements.pop(cle, None)

    def nombre_evenements(self, cle: str, fenetre_s: float) -> int:
        """Nombre d'événements dans la fenêtre (introspection, tests)."""
        now = time.monotonic()
        with self._verrou:
            file = self._evenements.get(cle)
            if file is None:
                return 0
            limite_basse = now - fenetre_s
            return sum(1 for t in file if t > limite_basse)


# Singleton du module (état partagé par toutes les requêtes du processus).
_limiteur = LimiteurFenetreGlissante()


def get_limiteur() -> LimiteurFenetreGlissante:
    """Retourne le limiteur partagé du processus."""
    return _limiteur


def cle_client(bucket: str, ip_client: str | None) -> str:
    """Clé de comptage : bucket + IP (jamais de secret dans la clé)."""
    return f"{bucket}:{ip_client or 'inconnu'}"


def ip_client_requete(request) -> str:
    """IP du client : ``request.client.host`` (TestClient → « testclient »).

    Derrière un reverse-proxy, déployer avec l'en-tête ``X-Forwarded-For``
    géré par le proxy (non lu ici par prudence : falsifiable par le client).
    """
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return str(host) if host else "inconnu"
