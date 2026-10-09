"""Tests anti-SSRF : `security.ssrf.validate_url` (contrat §5, NON NÉGOCIABLE).

Zéro réseau : la fixture autouse de `conftest.py` neutralise déjà le DNS
(IP publique fictive pour les noms d'hôtes, IP littérales renvoyées telles
quelles). Le test de DNS rebinding surcharge localement `getaddrinfo`.
"""

import socket

import pytest

from webintel.core.exceptions import SecurityError
from webintel.security.ssrf import validate_url


@pytest.mark.parametrize("url", [
    "http://localhost/",
    "http://localhost:8000/admin",
    "http://127.0.0.1/",
    "http://127.0.0.1:9000/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://10.0.0.5/",
    "http://192.168.1.10/",
    "http://intranet.local/page",
    "ftp://example.com/fichier",
    "http://user:pass@example.com/",
])
def test_validate_url_rejette_dangereux(url):
    with pytest.raises(SecurityError):
        validate_url(url)


def test_validate_url_accepte_url_publique():
    # Le DNS est neutralisé (fixture autouse) : example.com se résout vers
    # une IP publique fictive, donc l'URL est acceptée sans réseau réel.
    assert validate_url("https://example.com/produit") == "https://example.com/produit"


def test_validate_url_dns_rebinding_simule(monkeypatch):
    """Un hôte qui se résout vers une IP privée (rebinding) doit être rejeté."""

    def _rebound_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.9.8.7", port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _rebound_getaddrinfo)
    with pytest.raises(SecurityError):
        validate_url("https://faux-domaine-rebinding.example/")
