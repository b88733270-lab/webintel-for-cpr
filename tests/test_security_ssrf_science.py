"""Tests de l'exception bornée « hôtes scientifiques » anti-SSRF (chantier C).

Contexte : les 5 API scientifiques codées en dur dans les connecteurs se
résolvent, dans certains environnements (proxy « fake-IP »), vers le réseau
benchmark `198.18.0.0/15` — classé privé — ce qui bloquait tous les
connecteurs. L'exception testée ici est STRICTE :

- drapeau explicite `autoriser_hote_science=True` (défaut False partout) ;
- hôte exactement whitelisté + https obligatoire + GET/HEAD uniquement ;
- seule la plage `198.18.0.0/15` est tolérée en plus des IP publiques ;
- RFC1918 / loopback / link-local / IP littérale : TOUJOURS bloqués ;
- hôte arbitraire : TOUJOURS bloqué, drapeau ou non.

Zéro réseau : `socket.getaddrinfo` est simulé par test (comme
`tests/test_ssrf_pinning.py`) et l'envoi réel est espionné sur
`httpx.HTTPTransport`.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from webintel.core.exceptions import SecurityError
from webintel.security.pinned_transport import (
    client_http_sure,
    requete_sure,
)
from webintel.security.ssrf import (
    HOTES_SCIENCE_AUTORISES,
    resoudre_ip_epinglee,
    validate_url,
)

_IP_BENCHMARK = "198.18.56.167"
_IP_PUBLIQUE = "93.184.216.34"
_IP_RFC1918 = "192.168.1.10"
_IP_METADATA = "169.254.169.254"

_URLS_SCIENCE = [
    "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/quercetin/JSON",
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed",
    "https://api.gbif.org/v1/species/match?name=Moringa%20oleifera",
    "https://www.ebi.ac.uk/chembl/api/data/molecule.json",
    "https://api.openalex.org/works?search=moringa",
]


def _dns_vers(*ips):
    """Faux DNS : renvoie toujours les mêmes adresses, dans l'ordre.

    Une IP littérale est renvoyée telle quelle, comme le ferait le vrai
    `getaddrinfo` (sinon les tests sur IP littérales seraient faussés).
    """
    import ipaddress

    def _faux_getaddrinfo(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(str(host))
        except ValueError:
            adresses = ips
        else:
            adresses = (str(host),)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))
            for ip in adresses
        ]

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


def test_whitelist_exactement_cinq_hotes():
    """La whitelist est limitée aux 5 hôtes d'API codés en dur."""
    assert HOTES_SCIENCE_AUTORISES == frozenset(
        {
            "pubchem.ncbi.nlm.nih.gov",
            "eutils.ncbi.nlm.nih.gov",
            "api.gbif.org",
            "www.ebi.ac.uk",
            "api.openalex.org",
        }
    )


@pytest.mark.parametrize("url", _URLS_SCIENCE)
def test_hote_science_benchmark_autorise_avec_drapeau(monkeypatch, url):
    """(a) Hôte scientifique résolvant vers 198.18.x + drapeau → autorisé."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_BENCHMARK))
    assert validate_url(url, autoriser_hote_science=True) == url


@pytest.mark.parametrize("url", _URLS_SCIENCE)
def test_hote_science_benchmark_bloque_sans_drapeau(monkeypatch, url):
    """Sans le drapeau explicite, le comportement strict est inchangé."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_BENCHMARK))
    with pytest.raises(SecurityError):
        validate_url(url)


@pytest.mark.parametrize("ip", [_IP_BENCHMARK, _IP_RFC1918])
def test_hote_arbitraire_toujours_bloque_meme_avec_drapeau(monkeypatch, ip):
    """(b) Un hôte hors whitelist résolvant vers 198.18.x ou 192.168.x est
    TOUJOURS bloqué : le drapeau ne vaut que pour les 5 hôtes codés en dur."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(ip))
    with pytest.raises(SecurityError):
        validate_url(
            "https://exemple-utilisateur.com/page",
            autoriser_hote_science=True,
        )


@pytest.mark.parametrize(
    "url",
    [
        # Sous-domaine non codé en dur.
        "https://api.pubchem.ncbi.nlm.nih.gov/rest/pug",
        # Imposteur : le nom whitelisté n'est qu'un préfixe.
        "https://pubchem.ncbi.nlm.nih.gov.evil.example/rest/pug",
        # metadata / blocklist, drapeau ou non.
        "https://metadata.google.internal/",
    ],
)
def test_imposteurs_et_sous_domaines_bloques(monkeypatch, url):
    """Aucun sous-domaine ni hôte « ressemblant » ne bénéficie de l'exception."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_BENCHMARK))
    with pytest.raises(SecurityError):
        validate_url(url, autoriser_hote_science=True)


def test_hote_science_http_bloque_avec_drapeau(monkeypatch):
    """(c) Hôte whitelisté mais schéma http → bloqué même avec le drapeau,
    même si l'IP résolue est publique (https est obligatoire en science)."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_PUBLIQUE))
    with pytest.raises(SecurityError):
        validate_url(
            "http://pubchem.ncbi.nlm.nih.gov/rest/pug",
            autoriser_hote_science=True,
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://192.168.1.10/admin",
        "https://10.0.0.5/",
        "https://127.0.0.1/",
        # L'IP benchmark ELLE-MÊME, tapée littéralement : jamais assouplie.
        "https://198.18.56.167/rest/pug",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
def test_ip_litterale_privee_toujours_bloquee(monkeypatch, url):
    """(d) Une IP littérale privée/réservée est bloquée, drapeau ou non :
    l'exception ne vaut que pour un NOM whitelisté résolu et épinglé."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_PUBLIQUE))
    with pytest.raises(SecurityError):
        validate_url(url, autoriser_hote_science=True)


@pytest.mark.parametrize("ip", [_IP_RFC1918, "10.1.2.3", "127.0.0.1", _IP_METADATA])
def test_hote_science_vers_reseau_interne_toujours_bloque(monkeypatch, ip):
    """Même whitelisté + https + drapeau : une résolution vers RFC1918,
    loopback ou link-local/metadata reste bloquée. Seul 198.18/15 est toléré."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(ip))
    with pytest.raises(SecurityError):
        validate_url(
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug",
            autoriser_hote_science=True,
        )


def test_hote_science_dns_mixte_bloque(monkeypatch):
    """TOUTES les adresses sont validées : une RFC1918 parmi les réponses
    DNS d'un hôte scientifique suffit à bloquer (anti-rebinding)."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _dns_vers(_IP_BENCHMARK, _IP_RFC1918)
    )
    with pytest.raises(SecurityError):
        validate_url(
            "https://api.gbif.org/v1/species/match",
            autoriser_hote_science=True,
        )


def test_hote_science_userinfo_bloquee(monkeypatch):
    """Le drapeau ne lève ni l'interdiction userinfo ni la blocklist."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_BENCHMARK))
    with pytest.raises(SecurityError):
        validate_url(
            "https://user:pass@pubchem.ncbi.nlm.nih.gov/rest/pug",
            autoriser_hote_science=True,
        )


def test_resoudre_ip_epinglee_science_exige_https_explicite(monkeypatch):
    """`resoudre_ip_epinglee` n'assouplit que si le schéma https est fourni :
    sans lui, même un hôte whitelisté + drapeau reste strict."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_BENCHMARK))
    assert (
        resoudre_ip_epinglee(
            "pubchem.ncbi.nlm.nih.gov",
            autoriser_hote_science=True,
            scheme="https",
        )
        == _IP_BENCHMARK
    )
    with pytest.raises(SecurityError):
        resoudre_ip_epinglee(
            "pubchem.ncbi.nlm.nih.gov", autoriser_hote_science=True
        )
    with pytest.raises(SecurityError):
        resoudre_ip_epinglee(
            "pubchem.ncbi.nlm.nih.gov",
            autoriser_hote_science=True,
            scheme="http",
        )


def test_requete_sure_science_benchmark_aboutit(monkeypatch, espion_transport):
    """De bout en bout : `requete_sure` en mode science se connecte à l'IP
    benchmark épinglée, Host et SNI restés ceux du nom d'origine."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_BENCHMARK))
    reponse = requete_sure(
        "GET",
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
        "quercetin/property/InChIKey/JSON",
        timeout=5,
        autoriser_hote_science=True,
    )
    assert reponse.status_code == 200
    assert espion_transport["hote"] == _IP_BENCHMARK
    assert espion_transport["host_header"] == "pubchem.ncbi.nlm.nih.gov"
    assert espion_transport["sni"] == "pubchem.ncbi.nlm.nih.gov"
    # L'appelant voit l'URL d'origine, jamais l'IP épinglée.
    assert str(reponse.request.url).startswith(
        "https://pubchem.ncbi.nlm.nih.gov/"
    )


def test_requete_sure_sans_drapeau_bloquee_sur_benchmark(
    monkeypatch, espion_transport
):
    """Le même appel SANS drapeau est bloqué et n'émet aucune requête :
    c'est le comportement que gardent crawler/fetcher/webhooks."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_BENCHMARK))
    with pytest.raises(SecurityError):
        requete_sure(
            "GET", "https://pubchem.ncbi.nlm.nih.gov/rest/pug", timeout=5
        )
    assert "url" not in espion_transport


def test_requete_sure_science_ecriture_bloquee(monkeypatch, espion_transport):
    """Mode science = lecture seule : POST est refusé avant tout réseau."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_BENCHMARK))
    with pytest.raises(SecurityError):
        requete_sure(
            "POST",
            "https://api.openalex.org/works",
            timeout=5,
            autoriser_hote_science=True,
        )
    assert "url" not in espion_transport


def test_client_science_redirection_http_bloquee(monkeypatch):
    """Une redirection qui dégrade https → http est refusée par le
    transport science, même vers le même hôte whitelisté."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_BENCHMARK))

    def _espion_redirection(self, request):
        if str(request.url).startswith("https://"):
            return httpx.Response(
                302,
                headers={
                    "location": "http://pubchem.ncbi.nlm.nih.gov/rest/pug"
                },
                request=request,
            )
        raise AssertionError("la cible http n'aurait jamais dû être appelée")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _espion_redirection)
    with client_http_sure(autoriser_hote_science=True) as client:
        with pytest.raises(SecurityError):
            client.get(
                "https://pubchem.ncbi.nlm.nih.gov/depart", follow_redirects=True
            )


def test_client_science_redirection_relative_benchmark_ok(monkeypatch):
    """Redirection relative en mode science : l'IP benchmark épinglée pour
    l'hôte logique reste admise au second saut (Host/SNI d'origine)."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_vers(_IP_BENCHMARK))
    appels = []

    def _espion(self, request):
        appels.append(request.url.host)
        if len(appels) == 1:
            return httpx.Response(
                302, headers={"location": "/etape2"}, request=request
            )
        return httpx.Response(200, request=request, content=b"ok")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _espion)
    with client_http_sure(autoriser_hote_science=True) as client:
        reponse = client.get(
            "https://api.gbif.org/v1/depart", follow_redirects=True
        )
    assert reponse.status_code == 200
    assert appels == [_IP_BENCHMARK, _IP_BENCHMARK]
    assert str(reponse.url) == "https://api.gbif.org/etape2"
