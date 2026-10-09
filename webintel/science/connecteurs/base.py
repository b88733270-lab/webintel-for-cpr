"""Contrat des connecteurs de sources externes — Phase 10.

Un connecteur est une source de données externe en LECTURE SEULE :
il interroge une API publique, importe sélectivement (jamais de copie
aveugle), et enregistre la provenance de chaque objet importé.

Contrat d'un connecteur (attributs de classe) :
- ``NOM`` : identifiant unique (ex. ``"pubchem"``)
- ``DESCRIPTION`` : description courte (FR)
- ``URL_SOURCE`` : URL de base de l'API distante
- ``LICENCE`` : licence des données (OBLIGATOIRE)
- ``LICENCE_URL`` : URL du texte de la licence
- ``CONDITIONS_UTILISATION`` : CGU / limites de débit à respecter
- ``VERSION`` : version du connecteur (ex. ``"1.0"``)
- ``STRATEGIE_MAJ`` : stratégie de mise à jour (FR)

Discipline HTTP commune : timeout 10 s, retries bornés (2 au maximum),
``User-Agent: WebIntel-recherche``, journalisation, cache 24 h.
Réseau indisponible → ``ConnecteurIndisponible`` (jamais de faux résultat).
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from webintel.core.utils import sha256_hex, utcnow
from webintel.science.models import ConnecteurCache

logger = logging.getLogger(__name__)

USER_AGENT = "WebIntel-recherche"
TIMEOUT_S = 10.0
MAX_TENTATIVES = 3  # 1 tentative initiale + 2 retries
TTL_CACHE_HEURES = 24
#: Plafond d'attente sur HTTP 429 (Retry-After) : au-delà, on abandonne
#: proprement au lieu de bloquer l'usage local.
PLAFOND_ATTENTE_RATE_LIMIT_S = 30.0


class ConnecteurIndisponible(Exception):
    """La source distante est injoignable (réseau / timeout / DNS).

    Levée APRES épuisement des retries : le connecteur refuse de
    produire un faux résultat — l'appelant doit afficher une erreur
    explicite (HTTP 503 côté API).
    """


def _delai_retry_after(reponse: httpx.Response) -> float:
    """Extrait le délai ``Retry-After`` d'une réponse 429 (secondes).

    Retourne 0 si l'en-tête est absent ou illisible (réessai
    immédiat, dans la limite des tentatives).
    """
    try:
        valeur = (reponse.headers.get("retry-after") or "").strip()
        return max(0.0, float(valeur))
    except (TypeError, ValueError):
        return 0.0


class ParametresInvalides(ValueError):
    """Les paramètres d'import fournis sont invalides (→ HTTP 422)."""


class BaseConnecteur:
    """Classe de base de tout connecteur de source externe.

    Les plugins héritent de cette classe et renseignent les attributs
    contractuels. Toute la discipline HTTP (timeout, retry, cache,
    journalisation) est centralisée ici.
    """

    # --- Contrat (à renseigner par chaque plugin) ----------------------
    NOM: str = ""
    DESCRIPTION: str = ""
    URL_SOURCE: str = ""
    LICENCE: str = ""
    LICENCE_URL: str = ""
    CONDITIONS_UTILISATION: str = ""
    VERSION: str = "1.0"
    STRATEGIE_MAJ: str = ""

    # --- Discipline HTTP (modifiable par plugin si la source l'exige) --
    ttl_cache_heures: int = TTL_CACHE_HEURES
    timeout_s: float = TIMEOUT_S
    max_tentatives: int = MAX_TENTATIVES
    #: Délai minimal entre deux requêtes HTTP (secondes) — respect des
    #: CGU de débit (ex. NCBI : ≤ 3/s → 0.34). 0 = aucun délai imposé.
    delai_minimum_s: float = 0.0
    _dernier_appel_monotone: float = 0.0

    def _respecter_delai(self) -> None:
        """Espace les requêtes selon ``delai_minimum_s`` (CGU de débit)."""
        if self.delai_minimum_s <= 0:
            return
        attente = (
            self._dernier_appel_monotone + self.delai_minimum_s - time.monotonic()
        )
        if attente > 0:
            time.sleep(attente)
        self._dernier_appel_monotone = time.monotonic()

    def construire_provenance_import(
        self, *, identifiant_externe: str | None = None
    ) -> dict[str, Any]:
        """Provenance standard d'un import : source, date, version, URL,
        licence, identifiant externe et conditions d'utilisation.

        Chaque connecteur l'inclut dans son rapport (``provenance``) et
        dans ``Evidence.provenance`` / ``Compound.external_ids`` des
        objets créés : la traçabilité est uniforme quel que soit le
        connecteur.
        """
        return {
            "connecteur": self.NOM,
            "version_connecteur": self.VERSION,
            "date_import": utcnow().isoformat(),
            "url_source": self.URL_SOURCE,
            "licence": self.LICENCE,
            "licence_url": self.LICENCE_URL,
            "conditions_utilisation": self.CONDITIONS_UTILISATION,
            "respect_conditions": (
                f"User-Agent: {USER_AGENT} ; "
                + (
                    f"requêtes espacées d'au moins {self.delai_minimum_s:g} s ; "
                    if self.delai_minimum_s > 0
                    else ""
                )
                + "import sélectif (pas de moissonnage) ; "
                "niveau_donnees=normalise."
            ),
            "identifiant_externe": identifiant_externe,
            "niveau_donnees": "normalise",
        }

    # ------------------------------------------------------------------
    # HTTP : cache + retries + timeout + User-Agent
    # ------------------------------------------------------------------
    def _cle_cache(self, url: str, params: dict | None) -> str:
        """Clé de cache déterministe (nom connecteur + URL + params triés)."""
        canonique = url + "?" + "&".join(
            f"{k}={params[k]}" for k in sorted(params or {})
        )
        return sha256_hex(f"{self.NOM}|{canonique}")[:64]

    def _lire_cache(
        self, session: Session, cle: str
    ) -> dict | None:
        ligne = (
            session.query(ConnecteurCache)
            .filter(
                ConnecteurCache.connecteur_nom == self.NOM,
                ConnecteurCache.cle_requete == cle,
            )
            .one_or_none()
        )
        if ligne is None:
            return None
        # SQLite renvoie des datetimes naïfs, PostgreSQL des conscients :
        # on compare en naïf (UTC partout) pour rester portable.
        expire_le = ligne.expire_le
        if expire_le.tzinfo is not None:
            expire_le = expire_le.replace(tzinfo=None)
        if expire_le <= utcnow().replace(tzinfo=None):
            session.delete(ligne)
            session.flush()
            return None
        logger.debug("Connecteur %s : cache utilisé (%s).", self.NOM, cle)
        return ligne.reponse_json

    def _ecrire_cache(
        self, session: Session, cle: str, reponse: dict
    ) -> None:
        ligne = (
            session.query(ConnecteurCache)
            .filter(
                ConnecteurCache.connecteur_nom == self.NOM,
                ConnecteurCache.cle_requete == cle,
            )
            .one_or_none()
        )
        expire_le = utcnow() + timedelta(hours=self.ttl_cache_heures)
        if ligne is None:
            ligne = ConnecteurCache(
                connecteur_nom=self.NOM,
                cle_requete=cle,
                reponse_json=reponse,
                expire_le=expire_le,
            )
            session.add(ligne)
        else:
            ligne.reponse_json = reponse
            ligne.expire_le = expire_le
        session.flush()

    def _telecharger(self, url: str, params: dict | None) -> dict:
        """Un appel HTTP brut (sans cache). Séparé pour les tests (mock).

        Défense en profondeur (Phase 13) : l'URL construite est revalidée
        par ``validate_url`` avant l'appel — même si les plugins ne
        construisent leurs URL que depuis ``URL_SOURCE`` (hôte fixe), un
        ``URL_SOURCE`` mal configuré ne pourra jamais viser le réseau
        interne. La connexion utilise le transport épinglé anti-TOCTOU
        (résolution DNS unique validée, Host/SNI préservés). Timeout
        borné, User-Agent fixe.

        Repli proxy (v2.4.0) : si la connexion directe épinglée échoue
        (``ConnectError``) ET qu'un proxy HTTP(S) est configuré dans
        l'environnement, un second essai passe par ce proxy. Ce repli
        est strictement borné : hôte devant appartenir à la liste
        blanche scientifique (``HOTES_SCIENCE_AUTORISES``), schéma
        https, GET seul. Il sert les postes derrière proxy d'entreprise
        ou VPN (qui résolvent les hôtes d'API vers la plage 198.18/15) ;
        le proxy résout alors le nom, mais le certificat reste vérifié
        et l'hôte cible ne peut pas être choisi par l'utilisateur (URL
        construite depuis ``URL_SOURCE`` fixe du plugin). Sans proxy
        configuré, aucun repli : l'erreur d'origine est propagée.
        """
        from webintel.core.exceptions import SecurityError
        from webintel.security.pinned_transport import requete_sure

        try:
            reponse = requete_sure(
                "GET",
                url,
                params=params,
                timeout=self.timeout_s,
                autoriser_hote_science=True,
                headers={"User-Agent": USER_AGENT},
            )
        except SecurityError as exc:
            raise ConnecteurIndisponible(
                f"URL de la source rejetée par la protection anti-SSRF : {exc}. "
                "Aucune donnée n'a été récupérée."
            ) from exc
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            reponse = self._repli_proxy(url, params, exc)
        reponse.raise_for_status()
        return reponse.json()

    def _repli_proxy(self, url: str, params: dict | None, erreur_directe: Exception):
        """Second essai via le proxy d'environnement (hôtes science seuls).

        Voir la docstring de ``_telecharger`` pour le raisonnement
        sécurité. Lève l'erreur d'origine si le repli ne s'applique pas.
        """
        import os
        from urllib.parse import urlparse

        from webintel.security.ssrf import HOTES_SCIENCE_AUTORISES

        parsed = urlparse(url)
        hote = (parsed.hostname or "").lower().rstrip(".")
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        if parsed.scheme != "https" or hote not in HOTES_SCIENCE_AUTORISES or not proxy:
            raise erreur_directe
        logger.warning(
            "Connecteur %s : connexion directe impossible (%s) — "
            "second essai via le proxy configuré pour l'hôte scientifique %s.",
            self.NOM, erreur_directe, hote,
        )
        # NB : proxy explicite + trust_env=False : httpx plante sur
        # certaines variables d'environnement (no_proxy IPv6) quand il
        # les parse lui-même ; on ne lui donne que l'URL du proxy.
        with httpx.Client(
            proxy=proxy,
            timeout=self.timeout_s,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
        ) as client:
            return client.get(url, params=params)

    def requete_json(
        self, session: Session, url: str, params: dict | None = None
    ) -> dict:
        """GET JSON avec cache (TTL), timeout 10 s et 2 retries maximum.

        - cache : la réponse est réutilisée jusqu'à expiration du TTL ;
        - retries : après échec réseau, réessaie jusqu'à épuisement ;
        - réseau indisponible : lève ``ConnecteurIndisponible`` explicite.

        Ne committe jamais.
        """
        from webintel.offline.reseau import lever_si_hors_ligne

        # Dégradation gracieuse : en mode hors-ligne, message français
        # explicite SANS tentative réseau (l'usage local n'est jamais
        # bloqué — l'appelant affiche l'erreur, ex. HTTP 503 côté API).
        lever_si_hors_ligne(f"connecteur « {self.NOM} »")
        cle = self._cle_cache(url, params)
        en_cache = self._lire_cache(session, cle)
        if en_cache is not None:
            return en_cache

        derniere_erreur: Exception | None = None
        for tentative in range(1, self.max_tentatives + 1):
            try:
                logger.info(
                    "Connecteur %s : GET %s (tentative %d/%d).",
                    self.NOM, url, tentative, self.max_tentatives,
                )
                self._respecter_delai()
                donnees = self._telecharger(url, params)
                self._ecrire_cache(session, cle, donnees)
                return donnees
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 429:
                    # Rate-limit de la source : on honore Retry-After
                    # (borné), puis on réessaie UNE fois dans la limite
                    # des tentatives restantes. Au-delà, dégradation
                    # gracieuse (ConnecteurIndisponible → HTTP 503
                    # explicite côté API, jamais de faux résultat).
                    attente = _delai_retry_after(exc.response)
                    logger.warning(
                        "Connecteur %s : rate-limit 429 (Retry-After: %ss), "
                        "tentative %d/%d.",
                        self.NOM, attente, tentative, self.max_tentatives,
                    )
                    if attente <= PLAFOND_ATTENTE_RATE_LIMIT_S:
                        time.sleep(attente)
                        continue
                    derniere_erreur = exc
                    break
                # Autre erreur HTTP de la source (4xx/5xx) : pas un
                # problème réseau. 404 = « introuvable », laissé au plugin
                # (il la traite). Toute autre erreur HTTP se dégrade en
                # message français explicite (jamais de traceback brut).
                if exc.response is not None and exc.response.status_code == 404:
                    raise
                code = exc.response.status_code if exc.response is not None else "?"
                raise ConnecteurIndisponible(
                    f"Source « {self.NOM} » répond en erreur HTTP {code} "
                    f"après {tentative} tentative(s). "
                    "Aucune donnée n'a été importée — réessayez plus tard, "
                    "ou travaillez en mode local : vos données existantes "
                    "restent disponibles."
                ) from exc
            except httpx.RequestError as exc:
                derniere_erreur = exc
                logger.warning(
                    "Connecteur %s : échec réseau tentative %d/%d (%s).",
                    self.NOM, tentative, self.max_tentatives, exc,
                )
        raise ConnecteurIndisponible(
            f"Source « {self.NOM} » injoignable après "
            f"{self.max_tentatives} tentatives "
            f"(timeout {self.timeout_s:g} s) : {derniere_erreur}. "
            "Vérifiez la connexion réseau ou réessayez plus tard — "
            "aucune donnée n'a été importée."
        )

    # ------------------------------------------------------------------
    # À implémenter par les plugins
    # ------------------------------------------------------------------
    def tester_statut(self) -> tuple[bool, str]:
        """Teste la disponibilité de la source → ``(ok, message)``."""
        raise NotImplementedError

    def importer(
        self, session: Session, parametres: dict
    ) -> dict[str, Any]:
        """Importe sélectivement depuis la source.

        Retourne un rapport ``{crees, mis_a_jour, ignores, erreurs[],
        provenance{connecteur, date_import, version_donnees}}``.
        Ne committe jamais (l'appelant décide).
        """
        raise NotImplementedError
