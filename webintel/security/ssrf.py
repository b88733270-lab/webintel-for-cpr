"""Validation anti-SSRF des URL (contrat §5 d'architecture.md — NON NÉGOCIABLE).

`validate_url(url)` lève `SecurityError` si l'URL est dangereuse, sinon retourne
l'URL normalisée (espaces supprimés). Protection anti DNS-rebinding : on résout
l'hôte AVANT la requête et on valide TOUTES les adresses renvoyées ; le fetcher
doit revalider l'URL finale après chaque redirection.

Exception bornée « hôtes scientifiques » (chantier C)
----------------------------------------------------
Les connecteurs scientifiques interrogent 5 API publiques codées en dur
(`HOTES_SCIENCE_AUTORISES`). Dans certains environnements réseau (proxy en
mode « fake-IP », réseaux de test), le DNS de ces hôtes renvoie des adresses
du réseau de benchmarking `198.18.0.0/15`, classées `is_private` par
`ipaddress` : le rejet strict bloquait alors 100 % des connecteurs, sans
qu'aucun réseau interne soit en jeu.

L'assouplissement est STRICTEMENT borné, et désactivé par défaut :

- il faut le paramètre explicite ``autoriser_hote_science=True`` (défaut
  ``False`` partout : crawler, fetcher, webhooks et toute URL utilisateur
  restent intégralement stricts) ;
- l'hôte doit être EXACTEMENT l'un des 5 noms codés en dur (aucun
  sous-domaine, aucun suffixe toléré) ;
- le schéma doit être ``https`` (``http`` reste bloqué en mode science) ;
- seule l'appartenance à `198.18.0.0/15` est tolérée : loopback,
  link-local (dont l'adresse metadata `169.254.169.254`), multicast,
  non-spécifiée et privées RFC1918 (`10/8`, `172.16/12`, `192.168/16`…)
  restent bloquées MÊME pour un hôte scientifique ;
- une IP littérale n'est jamais assouplie en tant que telle : seul le nom
  whitelisté, résolu puis épinglé par le transport, ouvre l'exception ;
- toutes les adresses résolues sont validées : une seule adresse interdite
  dans la réponse DNS suffit à bloquer.

Pourquoi le risque résiduel reste borné : les 5 hôtes sont fixes et publics,
les requêtes sont en lecture seule (GET/HEAD, imposé par
`pinned_transport.requete_sure`), aucune donnée privée n'est envoyée (des
termes de recherche publics), et le transport épinglé préserve SNI + Host :
le certificat TLS est vérifié contre le NOM d'origine. Un DNS menteur qui
pointerait un hôte scientifique vers `198.18.0.0/15` ne peut donc pas se
faire passer pour l'API légitime sans son certificat, et ne peut pas viser
un service interne RFC1918/loopback, toujours rejeté. Limite résiduelle
honnête : une tentative de connexion vers une adresse `198.18.0.0/15`
arbitraire reste possible si le DNS de l'un des 5 hôtes est compromis ;
elle échoue à la vérification TLS, mais l'existence d'un service écoutant
sur cette plage pourrait être sondée par timing. C'est le prix, accepté et
documenté, de faire fonctionner les connecteurs derrière les proxys
« fake-IP » ; hors de ces environnements, les 5 hôtes se résolvent vers
des IP publiques et l'exception ne sert à rien.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from webintel.core.exceptions import SecurityError

# Hôtes exactement interdits (comparaison insensible à la casse).
BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.google.com",
        "instance-data",
    }
)

# Suffixes interdits (ex. "*.localhost", "*.internal", "*.local"). Liste
# configurable : surchargeable par les lots suivants si besoin.
BLOCKED_SUFFIXES = (".localhost", ".internal", ".local")

_ALLOWED_SCHEMES = ("http", "https")

#: Les SEULS hôtes d'API scientifiques admis à l'exception bornée
#: ci-dessous. Ils correspondent exactement aux ``URL_SOURCE`` codées en
#: dur dans les plugins connecteurs (PubChem, PubMed E-utilities, GBIF,
#: ChEMBL/EBI, OpenAlex). Comparaison exacte après normalisation (minuscules,
#: point final retiré) : aucun sous-domaine n'est admis.
HOTES_SCIENCE_AUTORISES = frozenset(
    {
        "pubchem.ncbi.nlm.nih.gov",
        "eutils.ncbi.nlm.nih.gov",
        "api.gbif.org",
        "www.ebi.ac.uk",
        "api.openalex.org",
    }
)

#: Réseau de benchmarking RFC 2544, utilisé comme plage « fake-IP » par
#: certains proxys : c'est la SEULE plage non publique tolérée, et
#: uniquement pour les hôtes scientifiques en https avec le drapeau
#: explicite. Toute autre adresse non publique reste bloquée partout.
_RESEAU_BENCHMARK = ipaddress.ip_network("198.18.0.0/15")


def _normaliser_hote(host: str | None) -> str:
    return (host or "").strip().lower().rstrip(".")


def _is_blocked_host(host: str) -> bool:
    return host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES)


def _assouplissement_science(
    hote: str, scheme: str | None, autoriser_hote_science: bool
) -> bool:
    """L'exception scientifique s'applique-t-elle à cet hôte ?

    Conditions cumulatives : drapeau explicite, schéma ``https`` et hôte
    exactement whitelisté. Dans tous les autres cas, retourne ``False``
    (fail-closed : le drapeau est simplement ignoré hors périmètre).
    """
    return bool(
        autoriser_hote_science
        and (scheme or "").lower() == "https"
        and _normaliser_hote(hote) in HOTES_SCIENCE_AUTORISES
    )


def valider_url_sans_dns(url: str) -> str:
    """Valide une URL SANS résolution DNS (schéma, userinfo, blocklist d'hôtes).

    Retourne l'URL normalisée (nettoyée). Lève `SecurityError` si : schéma
    non http/https, userinfo présente, hôte vide ou hôte blocklisté.
    Utilisée par `PinnedHTTPTransport` pour revalider CHAQUE requête —
    y compris les redirections suivies automatiquement — sans refaire de
    DNS (la résolution + validation des IP est faite par
    `resoudre_ip_epinglee` au moment de la connexion).
    """
    if not url or not url.strip():
        raise SecurityError("URL bloquée : URL vide.")
    url = url.strip()

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        affiché = parsed.scheme if parsed.scheme else "aucun"
        raise SecurityError(
            f"URL bloquée : schéma '{affiché}' non autorisé (http/https uniquement)."
        )

    # userinfo du type user:pass@host — refusée d'office.
    if "@" in parsed.netloc:
        raise SecurityError("URL bloquée : userinfo interdite dans l'URL (anti-SSRF).")

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise SecurityError("URL bloquée : nom d'hôte vide ou invalide.")

    if _is_blocked_host(host):
        raise SecurityError(
            f"URL bloquée : hôte '{host}' interdit par la blocklist (anti-SSRF)."
        )

    return url


def validate_url(url: str, *, autoriser_hote_science: bool = False) -> str:
    """Valide une URL contre les attaques SSRF.

    Retourne l'URL normalisée (nettoyée). Lève `SecurityError` avec un message
    en français si : schéma non http/https, userinfo présente, hôte vide,
    hôte blocklisté, ou si l'une des adresses IP résolues est privée /
    loopback / link-local / multicast / réservée / non spécifiée.

    ``autoriser_hote_science`` (défaut ``False``) active l'exception bornée
    décrite dans le docstring du module : hôte exactement dans
    ``HOTES_SCIENCE_AUTORISES`` + https + adresses limitées aux IP publiques
    ou au réseau benchmark `198.18.0.0/15`. Hors de ce périmètre exact, le
    drapeau est ignoré et la validation reste intégralement stricte. Ce
    paramètre ne doit être activé que par les connecteurs scientifiques,
    dont les URL sont construites depuis une ``URL_SOURCE`` fixe — jamais
    depuis une saisie utilisateur.
    """
    url = valider_url_sans_dns(url)

    parsed = urlparse(url)
    host = _normaliser_hote(parsed.hostname)
    scheme = parsed.scheme.lower()

    if autoriser_hote_science and _normaliser_hote(host) in HOTES_SCIENCE_AUTORISES:
        # Périmètre science revendiqué : https devient OBLIGATOIRE (le
        # strict classique tolère http vers une IP publique, l'exception
        # non — c'est le certificat vérifié sur le nom qui borne le risque).
        if scheme != "https":
            raise SecurityError(
                "URL bloquée : les hôtes scientifiques exigent le schéma "
                "https (anti-SSRF)."
            )
        _valider_adresses(
            host, _resoudre(host), _relacher_benchmark_science=True
        )
        return url

    # Anti DNS-rebinding : résoudre AVANT la requête et valider TOUTES les IPs.
    _valider_adresses(host, _resoudre(host))

    return url


def _resoudre(host: str) -> list[str]:
    """Résout `host` via `socket.getaddrinfo` (une seule résolution).

    Lève `SecurityError` si la résolution échoue ou ne renvoie rien.
    Utilise `socket.getaddrinfo` directement (pas de résolveur tiers) pour
    que les tests puissent simuler un DNS menteur par monkeypatch.
    """
    try:
        addrinfos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise SecurityError(f"URL bloquée : impossible de résoudre l'hôte '{host}'.")
    except OSError as exc:
        raise SecurityError(
            f"URL bloquée : erreur réseau lors de la résolution de '{host}' ({exc})."
        ) from exc
    if not addrinfos:
        raise SecurityError(f"URL bloquée : aucune adresse résolue pour '{host}'.")
    return [info[4][0] for info in addrinfos]


def _valider_adresses(
    host: str,
    adresses: list[str],
    *,
    _relacher_benchmark_science: bool = False,
) -> None:
    """Valide que CHAQUE adresse résolue est une IP publique routable.

    Lève `SecurityError` dès qu'une adresse est invalide, privée, loopback,
    link-local, multicast, réservée ou non spécifiée.

    Avec ``_relacher_benchmark_science`` (réservé à l'exception scientifique,
    déjà gardée par hôte exact + https + drapeau explicite), les adresses du
    réseau benchmark `198.18.0.0/15` sont tolérées ; TOUTES les autres
    adresses non publiques restent bloquées, y compris pour un hôte
    scientifique (RFC1918, loopback, link-local/metadata…).
    """
    for ip_str in adresses:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SecurityError(
                f"URL bloquée : adresse invalide renvoyée pour l'hôte '{host}'."
            )
        if (
            _relacher_benchmark_science
            and ip.version == 4
            and ip in _RESEAU_BENCHMARK
        ):
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise SecurityError(
                "URL bloquée : l'hôte se résout vers une adresse privée (anti-SSRF)."
            )


def resoudre_ip_epinglee(
    host: str,
    *,
    autoriser_hote_science: bool = False,
    scheme: str | None = None,
    hote_logique: str | None = None,
) -> str:
    """Résout `host` UNE seule fois et retourne l'adresse IP validée à utiliser.

    C'est le cœur de la correction du TOCTOU anti-SSRF : au lieu de laisser
    le client HTTP refaire sa propre résolution DNS au moment de la
    connexion (fenêtre pendant laquelle un DNS menteur peut rebasculer vers
    une IP privée — DNS rebinding), l'appelant se connecte directement à
    l'adresse retournée ici, qui a déjà été validée.

    - IP littérale : validée directement (aucun DNS, aucun TOCTOU possible) ;
    - nom DNS : résolu via `socket.getaddrinfo`, TOUTES les adresses
      validées, la première est retournée (épinglée).

    Exception scientifique (mêmes gardes que `validate_url`) : si
    ``autoriser_hote_science`` est vrai, que ``scheme`` vaut ``https`` et que
    l'hôte — ou ``hote_logique`` quand `host` est l'IP déjà épinglée pour ce
    nom (chaîne de redirection relative du transport) — est exactement l'un
    des ``HOTES_SCIENCE_AUTORISES``, les adresses `198.18.0.0/15` sont
    tolérées. Une IP littérale fournie SANS `hote_logique` whitelisté n'est
    jamais assouplie : taper l'IP benchmark à la main ne passe pas.

    Lève `SecurityError` si la résolution échoue ou si une adresse est
    privée / non routable.
    """
    host = _normaliser_hote(host)
    if not host:
        raise SecurityError("URL bloquée : nom d'hôte vide ou invalide.")
    hote_reference = _normaliser_hote(hote_logique) if hote_logique else host
    relacher = _assouplissement_science(
        hote_reference, scheme, autoriser_hote_science
    )
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        _valider_adresses(host, [host], _relacher_benchmark_science=relacher)
        return host
    adresses = _resoudre(host)
    _valider_adresses(host, adresses, _relacher_benchmark_science=relacher)
    return adresses[0]
