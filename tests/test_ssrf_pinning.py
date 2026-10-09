"""Tests de régression anti-TOCTOU : `security.pinned_transport`.

Scénario attaqué : `validate_url()` résout le DNS vers une IP publique, puis
le client HTTP refait SA PROPRE résolution au moment de la connexion — un
DNS menteur rebasculant vers une IP privée entre les deux obtenait une
connexion vers le réseau interne (DNS rebinding / TOCTOU).

Correction testée ici : `PinnedHTTPTransport` ne résout qu'UNE fois (via
`resoudre_ip_epinglee`, qui valide TOUTES les adresses) et se connecte à
l'IP épinglée en préservant Host et SNI TLS.

Zéro réseau : `socket.getaddrinfo` est simulé localement par test, et
l'envoi réel est remplacé par un espion sur `httpx.HTTPTransport`.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from webintel.core.exceptions import SecurityError
from webintel.security.pinned_transport import (
    PinnedHTTPTransport,
    requete_sure,
)
from webintel.security.ssrf import resoudre_ip_epinglee, validate_url

_IP_PUBLIQUE = "93.184.216.34"
_IP_PRIVEE = "10.9.8.7"


def _fabrique_dns(*ips):
    """Faux DNS : renvoie les IP dans l'ordre des appels successifs."""
    appels = {"n": 0}

    def _faux_getaddrinfo(host, port, *args, **kwargs):
        ip = ips[min(appels["n"], len(ips) - 1)]
        appels["n"] += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]

    _faux_getaddrinfo.appels = appels
    return _faux_getaddrinfo


@pytest.fixture()
def espion_transport(monkeypatch):
    """Remplace l'envoi réel par un espion qui capture la requête sortante."""
    capture = {}

    def _espion(self, request):
        capture["url"] = str(request.url)
        capture["hote"] = request.url.host
        capture["host_header"] = request.headers.get("host")
        capture["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, request=request, content=b"ok")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _espion)
    return capture


def test_pinned_transport_reecrit_vers_ip_epinglee(monkeypatch, espion_transport):
    """La connexion effective utilise l'IP validée, Host et SNI préservés."""
    monkeypatch.setattr(socket, "getaddrinfo", _fabrique_dns(_IP_PUBLIQUE))
    transport = PinnedHTTPTransport()
    reponse = transport.handle_request(
        httpx.Request("GET", "https://example.org:8443/chemin?q=1")
    )
    assert reponse.status_code == 200
    assert espion_transport["hote"] == _IP_PUBLIQUE
    assert espion_transport["url"] == f"https://{_IP_PUBLIQUE}:8443/chemin?q=1"
    # Host d'origine COMPLET : nom + port non standard (routage virtuel).
    assert espion_transport["host_header"] == "example.org:8443"
    assert espion_transport["sni"] == "example.org"
    # La réponse exposée référence la requête d'ORIGINE, pas l'IP épinglée.
    assert str(reponse.request.url) == "https://example.org:8443/chemin?q=1"


def test_pinned_transport_ip_litterale_pas_de_reecriture(monkeypatch, espion_transport):
    """IP littérale publique : aucune résolution, aucune réécriture."""
    faux_dns = _fabrique_dns(_IP_PUBLIQUE)
    monkeypatch.setattr(socket, "getaddrinfo", faux_dns)
    PinnedHTTPTransport().handle_request(httpx.Request("GET", "http://93.184.216.34/x"))
    assert espion_transport["hote"] == "93.184.216.34"
    assert espion_transport["sni"] is None
    assert faux_dns.appels["n"] == 0  # aucun DNS sollicité


def test_pinned_transport_hote_standard_sans_port(monkeypatch, espion_transport):
    """Port standard : l'en-tête Host reste le nom seul."""
    monkeypatch.setattr(socket, "getaddrinfo", _fabrique_dns(_IP_PUBLIQUE))
    PinnedHTTPTransport().handle_request(
        httpx.Request("GET", "https://example.org/chemin")
    )
    assert espion_transport["host_header"] == "example.org"


def test_pinned_transport_redirection_blocklistee_refusee(monkeypatch):
    """Une redirection automatique vers un hôte blocklisté est refusée par
    le transport, même si `validate_url()` n'a vu que l'URL d'origine."""
    monkeypatch.setattr(socket, "getaddrinfo", _fabrique_dns(_IP_PUBLIQUE))

    def _espion_redirection(self, request):
        if request.url.host == _IP_PUBLIQUE:
            return httpx.Response(
                302,
                headers={"location": "http://service.internal/secret"},
                request=request,
            )
        raise AssertionError("la cible blocklistée n'aurait jamais dû être appelée")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _espion_redirection)
    client = httpx.Client(transport=PinnedHTTPTransport(), follow_redirects=True)
    with pytest.raises(SecurityError):
        client.get("https://example.org/depart")


def test_pinned_transport_redirection_legitime_reepinglée(monkeypatch):
    """Redirection automatique légitime : chaque cible est revalidée ET
    re-épinglée (nouvelle résolution validée)."""
    monkeypatch.setattr(socket, "getaddrinfo", _fabrique_dns(_IP_PUBLIQUE))
    appels = []

    def _espion(self, request):
        appels.append(request.url.host)
        if len(appels) == 1:
            return httpx.Response(
                302, headers={"location": "/etape2"}, request=request
            )
        return httpx.Response(200, request=request, content=b"ok")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _espion)
    client = httpx.Client(transport=PinnedHTTPTransport(), follow_redirects=True)
    reponse = client.get("https://example.org/depart")
    assert reponse.status_code == 200
    # Les deux requêtes sont parties vers l'IP épinglée (pas le nom DNS).
    assert appels == [_IP_PUBLIQUE, _IP_PUBLIQUE]
    # … mais l'appelant voit les URL d'origine.
    assert str(reponse.url) == "https://example.org/etape2"
    assert [str(r.request.url) for r in reponse.history] == [
        "https://example.org/depart"
    ]


def test_pinned_transport_ip_privee_refusee(monkeypatch):
    """Un hôte qui se résout vers une IP privée est refusé par le transport."""
    monkeypatch.setattr(socket, "getaddrinfo", _fabrique_dns(_IP_PRIVEE))
    with pytest.raises(SecurityError):
        PinnedHTTPTransport().handle_request(
            httpx.Request("GET", "https://interne.example/")
        )


def test_toctou_dns_rebinding_bloque(monkeypatch, espion_transport):
    """RÉGRESSION : DNS qui bascule public → privé entre validation et
    connexion. Avant la correction, la connexion partait vers l'IP privée ;
    désormais le transport re-valide l'adresse épinglée et refuse."""
    # 1er appel (validate_url) : IP publique -> URL acceptée.
    # 2e appel (transport, au moment de la connexion) : IP privée.
    monkeypatch.setattr(
        socket, "getaddrinfo", _fabrique_dns(_IP_PUBLIQUE, _IP_PRIVEE)
    )
    url = validate_url("https://piege.example/")
    assert url == "https://piege.example/"
    with pytest.raises(SecurityError):
        requete_sure("GET", url, timeout=5)
    # Aucune requête n'est jamais partie vers l'IP privée.
    assert "url" not in espion_transport


def test_toctou_dns_stable_connexion_vers_ip_validee(monkeypatch, espion_transport):
    """DNS stable et public : la requête aboutit vers l'IP validée."""
    monkeypatch.setattr(socket, "getaddrinfo", _fabrique_dns(_IP_PUBLIQUE))
    reponse = requete_sure("GET", "https://stable.example/api", timeout=5)
    assert reponse.status_code == 200
    assert espion_transport["hote"] == _IP_PUBLIQUE
    assert espion_transport["host_header"] == "stable.example"


def test_resoudre_ip_epinglee_rejette_toutes_les_adresses_privees(monkeypatch):
    """Toutes les adresses renvoyées sont validées, pas seulement la 1re."""
    def _faux_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (_IP_PUBLIQUE, port or 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (_IP_PRIVEE, port or 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _faux_getaddrinfo)
    with pytest.raises(SecurityError):
        resoudre_ip_epinglee("mixte.example")
