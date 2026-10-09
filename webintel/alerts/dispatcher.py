"""Distribution des alertes (contrat §9 d'architecture.md).

Canaux :
- "log" : ligne en base (`status="sent"`) + `logger.info` (message FR) ;
- "webhook" : POST JSON (timeout 10 s) via le transport épinglé anti-TOCTOU.
  L'URL (prise dans `rule["webhook_url"]` ou `payload["webhook_url"]`) est
  VALIDÉE par `security.ssrf.validate_url` avant tout envoi (anti-SSRF) et
  la connexion utilise l'IP validée (anti DNS-rebinding) ; statut
  `sent` / `failed` tracé en base avec l'erreur éventuelle.

Canal inconnu -> `ValueError`.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from webintel.core.enums import AlertChannel
from webintel.core.exceptions import SecurityError
from webintel.core.logging import get_logger
from webintel.database.models import Alert
from webintel.security.pinned_transport import client_http_sure
from webintel.security.ssrf import validate_url

logger = get_logger(__name__)

_WEBHOOK_TIMEOUT_S = 10


def _summarize(payload: dict) -> str:
    event = (payload or {}).get("event") or {}
    url = (payload or {}).get("url") or ""
    return f"événement {event.get('type', '?')} sur {url or 'URL inconnue'}"


def dispatch(
    session: Session,
    project_id: int,
    channel: str,
    rule: dict,
    payload: dict,
    monitor_id: int | None = None,
) -> Alert:
    """Crée et envoie une alerte sur `channel` ("log" | "webhook").

    Retourne l'objet `Alert` persisté (statut sent/failed/pending).
    Lève `ValueError` si le canal est inconnu.
    """
    channel = (channel or "").lower()
    if channel not in (AlertChannel.log.value, AlertChannel.webhook.value):
        raise ValueError(
            f"Canal d'alerte inconnu : {channel!r} (attendus : 'log', 'webhook')."
        )
    rule = dict(rule or {})
    payload = dict(payload or {})

    alert = Alert(
        project_id=project_id,
        monitor_id=monitor_id,
        rule=rule,
        channel=channel,
        payload=payload,
        status="pending",
    )

    if channel == AlertChannel.log.value:
        alert.status = "sent"
        session.add(alert)
        session.flush()
        logger.info(
            "Alerte WebIntel [projet %s, moniteur %s] : %s",
            project_id, monitor_id if monitor_id is not None else "—", _summarize(payload),
        )
        return alert

    # --- webhook : validation anti-SSRF AVANT tout envoi réseau ---
    target = rule.get("webhook_url") or payload.get("webhook_url")
    try:
        safe_url = validate_url(target or "")
    except SecurityError as exc:
        alert.status = "failed"
        alert.error = f"URL webhook bloquée (sécurité) : {exc}"
        session.add(alert)
        session.flush()
        logger.warning("Alerte webhook non envoyée (SSRF) : %s", exc)
        return alert

    try:
        # Transport épinglé anti-TOCTOU : la connexion utilise l'IP validée
        # par `validate_url` (résolution DNS unique, Host/SNI préservés).
        with client_http_sure(timeout=_WEBHOOK_TIMEOUT_S) as client:
            response = client.post(safe_url, json=payload)
        if response.status_code >= 400:
            alert.status = "failed"
            alert.error = f"Le webhook a répondu avec le statut HTTP {response.status_code}."
        else:
            alert.status = "sent"
    except Exception as exc:
        alert.status = "failed"
        alert.error = f"Échec d'envoi du webhook : {exc}."
    session.add(alert)
    session.flush()
    if alert.status == "failed":
        logger.warning("Alerte webhook échouée : %s", alert.error)
    else:
        logger.info("Alerte webhook envoyée vers %s.", safe_url)
    return alert
