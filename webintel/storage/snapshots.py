"""Persistance des snapshots de pages — APPEND-ONLY.

On ne met JAMAIS à jour un snapshot existant : chaque capture crée une
nouvelle ligne `page_snapshots`. Le HTML brut n'est conservé que si
`keep_html=True` (donnée secondaire, contrat §1).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from webintel.core.types import FetchResult, SnapshotPayload
from webintel.core.utils import sha256_hex
from webintel.database.models import PageSnapshot


def save_snapshot(
    session: Session,
    project_id: int,
    fetch_result: FetchResult,
    payload: SnapshotPayload | dict,
    keep_html: bool = False,
) -> PageSnapshot:
    """Enregistre un nouveau snapshot (insertion seule, jamais d'écrasement).

    - `payload` accepté en `SnapshotPayload` ou dict sérialisé ;
    - `html_hash` / `text_hash` calculés (sha256 du HTML / du texte du
      `FetchResult`) s'ils sont absents du payload ;
    - `raw_html` stocké UNIQUEMENT si `keep_html=True`, sinon `None`.
    """
    data = (
        payload
        if isinstance(payload, SnapshotPayload)
        else SnapshotPayload.model_validate(payload or {})
    )
    if not data.html_hash:
        data.html_hash = sha256_hex(fetch_result.html or "")
    if not data.text_hash:
        data.text_hash = sha256_hex(fetch_result.text or "")

    snapshot = PageSnapshot(
        project_id=project_id,
        url=data.url,
        canonical_url=data.canonical_url,
        fetched_at=data.fetched_at,
        status_code=data.status_code,
        title=data.title or "",
        page_type=data.page_type,
        page_type_score=data.page_type_score,
        html_hash=data.html_hash,
        text_hash=data.text_hash,
        word_count=data.word_count,
        payload=data.model_dump(mode="json"),
        keep_html=bool(keep_html),
        raw_html=fetch_result.html if keep_html else None,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def get_history(
    session: Session, project_id: int, canonical_url: str, limit: int = 50
) -> list[PageSnapshot]:
    """Historique des snapshots d'une URL canonique, ordre antichronologique."""
    return (
        session.query(PageSnapshot)
        .filter(
            PageSnapshot.project_id == project_id,
            PageSnapshot.canonical_url == canonical_url,
        )
        .order_by(PageSnapshot.fetched_at.desc(), PageSnapshot.id.desc())
        .limit(max(1, limit))
        .all()
    )


def get_latest(
    session: Session, project_id: int, canonical_url: str
) -> PageSnapshot | None:
    """Snapshot le plus récent d'une URL canonique, ou `None`."""
    history = get_history(session, project_id, canonical_url, limit=1)
    return history[0] if history else None
