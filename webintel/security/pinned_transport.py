"""Transport HTTP(S) anti-SSRF avec épinglage DNS (correction du TOCTOU).

Contexte : `validate_url()` vérifie que le DNS résout vers une IP publique,
mais le client httpx effectuait ensuite SA PROPRE résolution au moment de
la connexion. Entre les deux, un DNS menteur pouvait rebasculer vers une IP
privée (DNS rebinding / TOCTOU) — cf. `docs/securite.md` §7.4.

`PinnedHTTPTransport` supprime la seconde résolution : le nom d'hôte est
résolu UNE seule fois (via `ssrf.resoudre_ip_epinglee`, qui valide TOUTES
les adresses renvoyées), puis la connexion TCP/TLS se fait directement vers
l'adresse épinglée, en préservant :

- l'en-tête `Host` d'origine (routage par hôte virtuel côté serveur) ;
- le SNI TLS d'origine (extension httpcore `sni_hostname`) ;
- la vérification du certificat contre le nom d'hôte d'origine (et non
  contre l'IP épinglée).

Utilisation : passer `transport=PinnedHTTPTransport()` (ou utiliser
`requete_sure()` / `client_http_sure()`) pour toute requête sortante vers
une URL préalablement validée par `validate_url` : fetcher, robots.txt,
connecteurs, webhooks.

Exception bornée « hôtes scientifiques » (chantier C)
-----------------------------------------------------
Le paramètre ``autoriser_hote_science`` (défaut ``False`` partout) relaie
l'exception documentée dans `webintel.security.ssrf` : hôtes d'API
scientifiques codés en dur uniquement, https obligatoire à CHAQUE saut
(redirections comprises), méthodes limitées à GET/HEAD (lecture seule), et
seule la plage benchmark `198.18.0.0/15` tolérée en plus des IP publiques.
Les adresses RFC1918, loopback, link-local et metadata restent bloquées
même en mode science, et le certificat reste vérifié contre le nom
d'origine (SNI + Host préservés par l'épinglage). Le crawler/fetcher et
les webhooks n'activent jamais ce drapeau : les URL utilisateur restent
intégralement strictes.

Limites résiduelles (honnêtes) :
- si un proxy HTTP est configuré (`trust_env=True` + `*_PROXY`), c'est le
  proxy qui résout le nom : l'épinglage ne s'applique qu'aux connexions
  directes ;
- la validation porte sur la résolution au moment de la requête ; un DNS
  légitime dont l'IP change entre deux requêtes successives reste accepté
  (chaque requête re-épingle) ;
- les IP littérales ne passent par aucun DNS : elles sont revalidées
  (publiques uniquement) mais il n'y a rien à épingler ;
- en mode science, un DNS compromis pour l'un des 5 hôtes pourrait sonder
  l'existence d'un service sur `198.18.0.0/15` (voir le détail dans
  `webintel.security.ssrf`) ; la vérification TLS sur le nom empêche en
  revanche toute usurpation du contenu de l'API.
"""

from __future__ import annotations

import ipaddress

import httpx

from webintel.core.exceptions import SecurityError
from webintel.security.ssrf import (
    resoudre_ip_epinglee,
    valider_url_sans_dns,
    validate_url,
)

# Clé d'extension httpx : (hôte logique, IP épinglée) d'une chaîne épinglée.
# Quand le transport réécrit une URL vers l'IP épinglée, il mémorise ici le
# nom d'hôte d'origine ET l'IP choisie. Si une redirection RELATIVE est
# ensuite suivie, httpx construit la requête suivante à partir de l'URL
# réécrite (donc avec l'IP littérale) : si cette IP est bien celle qu'on a
# épinglée, le transport restaure l'hôte logique (Host, SNI, URL exposée).
# Pour une redirection ABSOLUE (nouveau nom d'hôte, ou IP différente),
# l'URL porte la nouvelle cible et le marqueur est ignoré/écrasé
# (revalidation + ré-épinglage complets).
_EXT_HOTE_ORIGINE = "webintel_hote_origine"

#: Méthodes autorisées en mode science : lecture seule, aucune écriture
#: sortante vers les API publiques (et donc aucune exfiltration possible
#: via l'assouplissement d'IP).
_METHODES_SCIENCE = frozenset({"GET", "HEAD"})


class PinnedHTTPTransport(httpx.HTTPTransport):
    """Transport httpx qui se connecte à l'IP validée, pas au nom d'hôte.

    Pour chaque requête dont l'hôte est un nom DNS : validation sans DNS
    (schéma, userinfo, blocklist — aussi appliquée aux redirections suivies
    automatiquement), résolution unique + validation via
    `resoudre_ip_epinglee`, réécriture de l'URL vers l'IP épinglée, `Host`
    (nom + port d'origine) et SNI préservés. Les IP littérales sont
    revalidées sans réécriture (aucun DNS en jeu). La réponse exposée à
    l'appelant référence la requête d'ORIGINE (URL avec le nom d'hôte),
    jamais l'URL réécrite vers l'IP.

    ``autoriser_hote_science`` (défaut ``False``) relaie l'exception bornée
    de `ssrf` : en mode science, chaque requête (redirections comprises)
    doit être en https et en GET/HEAD, sous peine de `SecurityError`.
    """

    def __init__(
        self, *args, autoriser_hote_science: bool = False, **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self._autoriser_hote_science = autoriser_hote_science

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        # Validation SANS DNS à chaque requête : bloque les cibles de
        # redirection automatique que `validate_url()` n'a jamais vues
        # (ex. hôte blocklisté résolvant vers une IP publique).
        valider_url_sans_dns(str(url))
        if self._autoriser_hote_science:
            # Mode science : https et lecture seule à CHAQUE saut. Une
            # redirection qui dégrade vers http (ou change de méthode)
            # est refusée ici, même si l'URL d'origine était légitime.
            if url.scheme != "https":
                raise SecurityError(
                    "URL bloquée : les hôtes scientifiques exigent le "
                    "schéma https (anti-SSRF)."
                )
            if request.method.upper() not in _METHODES_SCIENCE:
                raise SecurityError(
                    "URL bloquée : mode scientifique en lecture seule "
                    "(GET/HEAD uniquement, anti-SSRF)."
                )
        host = url.host or ""
        try:
            est_litterale = ipaddress.ip_address(host) is not None
        except ValueError:  # pragma: no cover - déjà validé ci-dessus
            est_litterale = False

        hote_logique = request.extensions.get(_EXT_HOTE_ORIGINE)
        if (
            est_litterale
            and isinstance(hote_logique, tuple)
            and host == hote_logique[1]
        ):
            # Suite d'une chaîne épinglée après redirection relative : l'IP
            # de l'URL est exactement celle que ce transport a épinglée et
            # validée pour l'hôte logique. On la revalide (publique
            # uniquement) et on restaure l'hôte logique pour la réponse
            # exposée (Host/SNI sont déjà ceux d'origine : httpx les a
            # propagés avec la redirection). En mode science, la validation
            # est rattachée à l'hôte LOGIQUE whitelisté (jamais à l'IP
            # littérale elle-même) : une IP benchmark épinglée pour
            # pubchem reste admise, une IP RFC1918 resterait bloquée.
            resoudre_ip_epinglee(
                host,
                autoriser_hote_science=self._autoriser_hote_science,
                scheme=url.scheme,
                hote_logique=hote_logique[0],
            )
            reponse = super().handle_request(request)
            reponse.request = httpx.Request(
                request.method,
                url.copy_with(host=hote_logique[0]),
                headers=request.headers,
                extensions=request.extensions,
            )
            return reponse

        ip = resoudre_ip_epinglee(
            host,
            autoriser_hote_science=self._autoriser_hote_science,
            scheme=url.scheme,
        )
        if est_litterale or ip == host:
            # IP littérale déjà validée : rien à épingler.
            return super().handle_request(request)

        # En-tête Host d'origine COMPLET (nom + port non standard éventuel),
        # pour le routage par hôte virtuel côté serveur.
        hote_origine = request.headers.get("host") or url.netloc.decode("ascii")

        nouvelle_url = url.copy_with(host=_hote_pour_url(ip))
        en_tetes = [
            (cle, valeur)
            for cle, valeur in request.headers.raw
            if cle.lower() != b"host"
        ]
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = host
        extensions[_EXT_HOTE_ORIGINE] = (host, ip)
        nouvelle_requete = httpx.Request(
            request.method,
            nouvelle_url,
            headers=en_tetes,
            stream=request.stream,
            extensions=extensions,
        )
        # Host d'origine : le serveur virtuel / l'application voient le
        # nom d'hôte attendu, pas l'IP épinglée.
        nouvelle_requete.headers["host"] = hote_origine
        reponse = super().handle_request(nouvelle_requete)
        # Restaurer la requête d'origine sur la réponse : l'appelant (URL,
        # historique de redirections, cookies) voit le nom d'hôte d'origine,
        # jamais l'IP épinglée interne.
        reponse.request = request
        return reponse


def _hote_pour_url(ip: str) -> str:
    """Formate une IP pour `URL.copy_with(host=...)` (crochets IPv6)."""
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(parsed, ipaddress.IPv6Address):
        return f"[{ip}]"
    return ip


def client_http_sure(
    *,
    timeout: float | None = None,
    headers: dict | None = None,
    autoriser_hote_science: bool = False,
) -> httpx.Client:
    """Client httpx synchrone pré-configuré avec le transport épinglé.

    Pas de proxy d'environnement (`trust_env=False`) : en présence d'un
    proxy explicite, la résolution DNS incombe au proxy et l'épinglage
    serait contourné — on l'interdit donc ici par défaut.

    ``autoriser_hote_science`` (défaut ``False``) relaie l'exception bornée
    de `ssrf` au transport ; réservé aux connecteurs scientifiques.
    """
    return httpx.Client(
        transport=PinnedHTTPTransport(
            verify=True,
            trust_env=False,
            autoriser_hote_science=autoriser_hote_science,
        ),
        timeout=timeout,
        headers=headers,
    )


def requete_sure(
    methode: str,
    url: str,
    *,
    timeout: float | None = None,
    headers: dict | None = None,
    autoriser_hote_science: bool = False,
    **kwargs,
) -> httpx.Response:
    """Une requête HTTP(S) unique, anti-SSRF de bout en bout.

    `validate_url` est appliquée AVANT (schéma, userinfo, blocklist), puis
    la connexion utilise l'IP épinglée et validée (anti-TOCTOU).
    Lève `SecurityError` si l'URL est dangereuse.

    ``autoriser_hote_science=True`` (jamais par défaut) active l'exception
    bornée de `ssrf` pour les 5 hôtes d'API scientifiques codés en dur, et
    impose alors https + GET/HEAD. À réserver aux connecteurs, dont les
    URL sont construites depuis une ``URL_SOURCE`` fixe — jamais à une
    URL fournie par un utilisateur.
    """
    if autoriser_hote_science and methode.upper() not in _METHODES_SCIENCE:
        raise SecurityError(
            "URL bloquée : mode scientifique en lecture seule "
            "(GET/HEAD uniquement, anti-SSRF)."
        )
    url_sure = validate_url(url, autoriser_hote_science=autoriser_hote_science)
    with client_http_sure(
        timeout=timeout,
        headers=headers,
        autoriser_hote_science=autoriser_hote_science,
    ) as client:
        return client.request(methode, url_sure, **kwargs)
