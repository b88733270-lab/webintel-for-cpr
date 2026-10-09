"""Pipeline canonique d'analyse d'une page WebIntel (contrat §6 d'architecture.md).

C'est le POINT D'ENTRÉE UNIQUE de toute l'intelligence WebIntel. Chaque
source de collecte (API `POST /api/analyze`, UI, crawler, moniteurs, jobs,
planificateur) passe EXACTEMENT par ce pipeline : une page analysée puis
surveillée produit donc les mêmes entités, les mêmes champs et les mêmes
événements.

Étapes (entrées/sorties typées) :

```
1. FETCH      (str url) -> FetchResult
     validation anti-SSRF (`security.ssrf.validate_url`), récupération HTTP
     (`crawler.HybridFetcher`), détection du besoin JS (`needs_js`).
2. PARSE      (FetchResult) -> (BeautifulSoup, structured: dict)
     `extraction.extract_structured` : JSON-LD, Microdata, OpenGraph,
     Twitter Cards, meta tags.
3. CLASSIFY   (url, soup, structured) -> (PageType, score, signaux)
     `extraction.classify_page` : PRODUCT, ARTICLE, COMPANY, ...
4. EXTRACT    (soup, structured, page_type) -> entities_data: dict
     extracteurs sémantiques (`product`, `article`, `company`, `webpage`
     toujours présent) + contacts + prix + liens + images.
5. NORMALIZE  (entities_data, structured)
     prix -> {amount, currency}, dates, téléphones, URLs canoniques
     (intégré aux extracteurs via `normalization/`).
6. ENRICH     -> technologies, SEO, score de santé (preuves incluses).
7. ENTITY     -> `entities.upsert_entity` : clés stables, UNE entité logique
     même si le même produit est vu sur N sources.
8. SNAPSHOT   -> `storage.save_snapshot` (append-only, jamais d'écrasement).
9. DIFF       -> `diff.diff_snapshots` vs snapshot précédent
     (MAJOR / MINOR / COSMETIC / NONE — un changement de classe CSS ne vaut
     pas une baisse de prix).
10. EVENT     -> `events.detect_events` : événements métier typés persistés
     (PRICE_DROP, BACK_IN_STOCK, ...).
11. OBSERVE   -> journal des changements par champ (`observations`) et
     observations par source (`source_observations`, une valeur par
     (entité, source, champ)).
```

Deux points d'entrée :
- `analyze_url(session, project_id, url, ...)` : FETCH + pipeline complet
  (API, UI, scripts) ;
- `analyze_fetch_result(session, project_id, fetch_result, ...)` : pipeline
  depuis un `FetchResult` déjà collecté (monitoring, crawler, jobs) —
  évite un double téléchargement.

Les deux retournent la fiche intelligence (dict JSON-sérialisable).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from webintel.core.enums import PageType
from webintel.core.exceptions import FetchError
from webintel.core.logging import get_logger
from webintel.core.types import FetchResult, SnapshotPayload
from webintel.core.utils import clean_text, sha256_hex, utcnow
from webintel.crawler.fetcher import HybridFetcher
from webintel.crawler.urlutils import extract_links
from webintel.database.models import Event, Observation
from webintel.diff.engine import diff_snapshots
from webintel.entities.registry import upsert_entity
from webintel.events.detector import detect_events
from webintel.extraction import (
    classify_page,
    extract_article,
    extract_company,
    extract_contacts,
    extract_product,
    extract_structured,
)
from webintel.health.scorer import score_page
from webintel.normalization.prices import parse_price
from webintel.normalization.urls import canonical_url
from webintel.security.ssrf import validate_url
from webintel.seo.analyzer import analyze_seo
from webintel.storage.snapshots import get_latest, save_snapshot
from webintel.techdetect.detector import detect_technologies

logger = get_logger(__name__)

# Clés d'entité prioritaires (même ordre que monitoring.service).
_PRIMARY_ENTITY_KIND = {"product": "product", "article": "article",
                        "blog": "article", "news": "article", "company": "company"}


def _webpage_entity(url: str, soup: BeautifulSoup, structured: dict,
                    fetch_result: FetchResult) -> dict:
    """Entité générique WebPage (toujours construite, quel que soit le type)."""
    meta = structured.get("meta", {}) or {}
    headings = []
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = clean_text(tag.get_text(separator=" "))
        if text:
            headings.append({"level": tag.name.lower(), "text": text})
    return {
        "title": clean_text(soup.title.get_text()) if soup.title else None,
        "description": clean_text(str(meta.get("description") or "")) or None,
        "headings": headings[:20],
        "word_count": len((fetch_result.text or "").split()),
    }


def _collect_prices(structured: dict, entities_data: dict) -> list[dict]:
    """Tous les prix normalisés trouvés (entité produit + offres JSON-LD/microdata).

    Chaque entrée : `{amount, currency, source}`. Dédupliquée sur
    `(amount, currency)`.
    """
    found: list[dict] = []
    seen: set[tuple] = set()

    def add(amount, currency, source: str) -> None:
        try:
            amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None
        if amount is None:
            return
        key = (round(amount, 2), (currency or "").upper() or None)
        if key in seen:
            return
        seen.add(key)
        found.append({"amount": amount, "currency": (currency or None), "source": source})

    # Prix de l'entité produit extraite.
    product = entities_data.get("product") or {}
    price = product.get("price") or {}
    if isinstance(price, dict):
        add(price.get("amount"), price.get("currency"), "entite-produit")

    # Offres dans les données structurées (JSON-LD + microdata).
    def _offers_from_ld(obj: dict) -> list[dict]:
        offers = obj.get("offers") or obj.get("Offers")
        if isinstance(offers, dict):
            return [offers]
        if isinstance(offers, list):
            return [o for o in offers if isinstance(o, dict)]
        return []

    for obj in (structured.get("json_ld") or []):
        if not isinstance(obj, dict):
            continue
        obj_type = str(obj.get("@type") or "")
        if "Product" not in obj_type and "Offer" not in obj_type:
            continue
        for offer in _offers_from_ld(obj):
            parsed = parse_price(str(offer.get("price", "")))
            add(parsed.amount if parsed else None,
                offer.get("priceCurrency") or (parsed.currency if parsed else None),
                "json-ld")

    for item in (structured.get("microdata") or []):
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if "Product" not in item_type and "Offer" not in item_type:
            continue
        props = item.get("properties") or {}
        if not isinstance(props, dict):
            continue
        offers = props.get("offers")
        if isinstance(offers, dict):
            offers = [offers]
        for offer in (offers if isinstance(offers, list) else []):
            if not isinstance(offer, dict):
                continue
            oprops = offer.get("properties") or {}
            raw_price = (oprops.get("price") or [None])[0] if isinstance(oprops, dict) else None
            parsed = parse_price(str(raw_price or ""))
            add(parsed.amount if parsed else None,
                parsed.currency if parsed else None,
                "microdata")

    return found


def _extract_images(soup: BeautifulSoup, base_url: str, limit: int = 30) -> list[dict]:
    """Images de la page (URL absolue + texte alternatif), bornées à `limit`."""
    images: list[dict] = []
    seen: set[str] = set()
    for img in soup.find_all("img", src=True):
        src = urljoin(base_url, str(img.get("src") or "").strip())
        if not src or src in seen:
            continue
        seen.add(src)
        images.append({"url": src, "alt": clean_text(str(img.get("alt") or "")) or None})
        if len(images) >= limit:
            break
    return images


def _extract_links(html: str, base_url: str, limit: int = 200) -> list[dict]:
    """Liens de la page (URL + texte d'ancre), bornés à `limit`."""
    links: list[dict] = []
    try:
        for link in extract_links(html or "", base_url):
            url = link.get("url") or ""
            if url:
                links.append({"url": url, "text": clean_text(str(link.get("text") or "")) or None})
            if len(links) >= limit:
                break
    except Exception as exc:
        logger.warning("Extraction des liens impossible sur %s : %s", base_url, exc)
    return links


def _structured_summary(structured: dict) -> dict:
    """Résumé compact des données structurées (pour la fiche intelligence)."""
    json_ld = structured.get("json_ld") or []
    types = sorted({str(o.get("@type")) for o in json_ld
                    if isinstance(o, dict) and o.get("@type")})
    microdata = structured.get("microdata") or []
    og = structured.get("opengraph") or {}
    meta = structured.get("meta") or {}
    return {
        "types_jsonld": types,
        "nb_jsonld": len(json_ld),
        "nb_microdata": len(microdata),
        "opengraph_present": bool(og),
        "meta_cles": sorted(str(k) for k in meta.keys())[:20],
    }


def _quality_report(title: str, description: str | None, headings: list,
                    entities_data: dict, page_type: PageType,
                    images: list, contacts: dict, word_count: int) -> dict:
    """Score de qualité des données : % de champs clés remplis.

    Champs contrôlés (documentés ici) :
    - toujours : titre, meta description, un H1, au moins une image,
      contenu substantiel (>= 300 mots) ;
    - si page produit : prix normalisé ;
    - si page entreprise : au moins un email ou un téléphone ;
    - si page article : auteur ou date de publication.

    Retourne `{completeness_score (0-100), missing_fields: [...]}`.
    """
    checks: list[tuple[str, bool]] = [
        ("titre", bool((title or "").strip())),
        ("meta description", bool((description or "").strip())),
        ("titre H1", any(h.get("level") == "h1" for h in headings or [])),
        ("au moins une image", len(images or []) > 0),
        ("contenu substantiel (>= 300 mots)", (word_count or 0) >= 300),
    ]
    if page_type == PageType.PRODUCT:
        product = entities_data.get("product") or {}
        price = product.get("price") or {}
        checks.append(("prix normalisé", price.get("amount") is not None))
    elif page_type == PageType.COMPANY:
        company = entities_data.get("company") or {}
        emails = company.get("emails") or contacts.get("emails") or []
        phones = company.get("phones") or contacts.get("phones") or []
        checks.append(("contact (email ou téléphone)", bool(emails or phones)))
    elif page_type in (PageType.ARTICLE, PageType.BLOG, PageType.NEWS):
        article = entities_data.get("article") or {}
        checks.append(("auteur ou date de publication",
                       bool(article.get("author") or article.get("published_at"))))

    total = len(checks)
    missing = [name for name, ok in checks if not ok]
    score = round(100 * (total - len(missing)) / total) if total else 0
    return {"completeness_score": score, "missing_fields": missing}


def _primary_entity_kind(page_type: PageType) -> str:
    """Type d'entité logique prioritaire pour une page (pour événements/observations)."""
    return _PRIMARY_ENTITY_KIND.get(page_type.value, "webpage")


def analyze_fetch_result(
    session: Session,
    project_id: int,
    fetch_result: FetchResult,
    keep_html: bool = False,
    progress_cb: Callable[[dict], None] | None = None,
) -> dict:
    """Exécute le pipeline canonique depuis un `FetchResult` déjà collecté.

    Étapes : PARSE -> CLASSIFY -> EXTRACT -> NORMALIZE -> ENRICH -> ENTITY
    -> SNAPSHOT -> DIFF -> EVENT -> OBSERVE. Committe les écritures
    (snapshot, entités, événements, observations).

    Utilisé par le monitoring, le crawler et les jobs pour éviter un double
    téléchargement : le `FetchResult` provient de leur propre collecte.

    Retourne la fiche intelligence (dict JSON-sérialisable).
    """
    started = time.monotonic()
    final_url = fetch_result.final_url or fetch_result.url
    soup = BeautifulSoup(fetch_result.html or "", "lxml")

    # --- Extraction -------------------------------------------------------
    structured = extract_structured(soup, final_url)
    page_type, page_type_score, _signals = classify_page(final_url, soup, structured)
    if progress_cb:
        progress_cb({"phase": "extraction", "page_type": page_type.value})

    entities_data: dict[str, dict] = {
        "webpage": _webpage_entity(final_url, soup, structured, fetch_result),
    }
    if page_type == PageType.PRODUCT:
        entities_data["product"] = extract_product(final_url, soup, structured)
    elif page_type in (PageType.ARTICLE, PageType.BLOG, PageType.NEWS):
        entities_data["article"] = extract_article(final_url, soup, structured)
    elif page_type == PageType.COMPANY:
        entities_data["company"] = extract_company(final_url, soup, structured)

    contacts = extract_contacts(soup)
    prices = _collect_prices(structured, entities_data)
    technologies = detect_technologies(soup, fetch_result.headers or {}, final_url)
    seo = analyze_seo(final_url, soup, structured)
    health = score_page(fetch_result, seo, structured)
    links = _extract_links(fetch_result.html or "", final_url)
    images = _extract_images(soup, final_url)

    # Champs remontés au niveau `structured` pour le diff intelligent.
    webpage = entities_data["webpage"]
    product = entities_data.get("product") or {}
    price = product.get("price") or {}
    meta = structured.get("meta", {}) or {}
    structured["entities"] = entities_data
    structured["headings"] = webpage.get("headings") or []
    structured["meta_description"] = clean_text(str(meta.get("description") or "")) or None
    if price.get("amount") is not None or price.get("currency"):
        structured["price"] = {"amount": price.get("amount"), "currency": price.get("currency")}
    if product.get("availability"):
        structured["availability"] = product.get("availability")

    duration_ms = int((time.monotonic() - started) * 1000)
    title = clean_text(soup.title.get_text()) if soup.title else ""
    payload = SnapshotPayload(
        url=final_url,
        canonical_url=canonical_url(final_url),
        fetched_at=fetch_result.fetched_at,
        status_code=fetch_result.status_code,
        title=title,
        page_type=page_type.value,
        page_type_score=float(page_type_score or 0.0),
        text_hash=sha256_hex(fetch_result.text or ""),
        html_hash=sha256_hex(fetch_result.html or ""),
        word_count=len((fetch_result.text or "").split()),
        structured=structured,
        seo=seo,
        technologies=technologies,
        health=health,
        links=[{"url": l["url"]} for l in links[:50]],
        images=[{"url": i["url"]} for i in images[:20]],
    )

    # --- Snapshot précédent AVANT d'enregistrer le nouveau ----------------
    previous = get_latest(session, project_id, payload.canonical_url)
    snapshot = save_snapshot(session, project_id, fetch_result, payload, keep_html=keep_html)
    if progress_cb:
        progress_cb({"phase": "snapshot enregistré", "snapshot_id": snapshot.id})

    # --- Entités (clés stables, upsert) -----------------------------------
    db_entities: dict[str, object] = {}
    for kind, data in entities_data.items():
        db_entities[kind] = upsert_entity(session, project_id, kind, data, final_url)
    session.flush()

    primary_kind = _primary_entity_kind(page_type)
    primary_entity = db_entities.get(primary_kind) or db_entities["webpage"]
    entity_ref = {
        "id": primary_entity.id,
        "entity_type": primary_entity.entity_type,
        "entity_key": primary_entity.entity_key,
    }

    # --- Observations par source (P0-3, durci v1.1.1) ------------------------
    # Import local : `entities.registry` ne doit pas dépendre du pipeline.
    # La Source est résolue UNE fois ici puis réutilisée : pour les
    # observations métier ET pour la provenance du journal des changements.
    from webintel.entities.registry import record_source_observations
    from webintel.entities.sources import get_or_create_source
    source = get_or_create_source(session, final_url)
    for kind, data in entities_data.items():
        record_source_observations(
            session, db_entities[kind], kind, data, final_url,
            snapshot_id=snapshot.id,
            source=source,
        )

    # --- Diff + événements (re-analyse) -----------------------------------
    if previous is None:
        diff_d: dict = {
            "level": "none", "changes": [], "score": 0.0,
            "summary_fr": "Première capture de cette page.",
            "is_first": True, "page_type": page_type.value,
        }
        previous_id = None
    else:
        old_payload = SnapshotPayload.model_validate(previous.payload or {})
        diff_result = diff_snapshots(
            old_payload, payload,
            old_text=(previous.raw_html or "") if previous.keep_html else None,
            new_text=fetch_result.html if previous.keep_html else None,
        )
        diff_d = diff_result.model_dump(mode="json")
        diff_d["page_type"] = page_type.value
        diff_d["currency"] = (structured.get("price") or {}).get("currency")
        previous_id = previous.id

    events_saved: list[dict] = []
    for event in detect_events(diff_d, entity_ref, final_url,
                               snapshot_ids=[snapshot.id] + ([previous_id] if previous_id else [])):
        row = Event(
            project_id=project_id,
            type=event["type"],
            entity_id=event.get("entity_id"),
            url=event.get("url") or "",
            before=event.get("before"),
            after=event.get("after"),
            importance=event.get("importance") or "low",
            evidence=event.get("evidence") or {},
        )
        session.add(row)
        session.flush()
        event_out = dict(event)
        event_out["id"] = row.id
        events_saved.append(event_out)

    # Une observation par champ changé, liée à l'entité prioritaire.
    # Provenance directe (v1.1.1) : chaque changement est attribué à la
    # source qui l'a produit (source_id + URL + hôte).
    for change in diff_d.get("changes") or []:
        field = change.get("field", "") if isinstance(change, dict) else ""
        session.add(Observation(
            entity_id=primary_entity.id,
            field=str(field),
            old_value={"value": change.get("before")} if isinstance(change, dict) else None,
            new_value={"value": change.get("after")} if isinstance(change, dict) else None,
            snapshot_id=snapshot.id,
            source_id=source.id,
            source_url=final_url,
            source_domain=source.canonical_host,
        ))

    session.commit()

    entities_out = []
    for kind, ent in db_entities.items():
        entities_out.append({
            "id": ent.id, "entity_key": ent.entity_key, "entity_type": ent.entity_type,
            "name": ent.name, "data": ent.data or {},
            "first_seen": ent.first_seen.isoformat() if ent.first_seen else None,
            "last_seen": ent.last_seen.isoformat() if ent.last_seen else None,
            "sources": ent.sources or [],
            "history_url": f"/api/entities/{ent.id}/history",
        })

    quality = _quality_report(
        title, webpage.get("description"), webpage.get("headings") or [],
        entities_data, page_type, images, contacts, payload.word_count,
    )

    if progress_cb:
        progress_cb({"phase": "terminé", "events": len(events_saved)})

    logger.info("Analyse terminée : %s (%s, %d événement(s)).",
                final_url, page_type.value, len(events_saved))
    return {
        "project_id": project_id,
        "url": fetch_result.url,
        "final_url": final_url,
        "status_code": fetch_result.status_code,
        "duration_ms": duration_ms,
        "page_type": page_type.value,
        "page_type_score": float(page_type_score or 0.0),
        "title": title,
        "description": webpage.get("description"),
        "entities": entities_out,
        "prices": prices,
        "contacts": contacts,
        "technologies": technologies,
        "seo": seo,
        "health": health,
        "structured_summary": _structured_summary(structured),
        "links": links,
        "images": images,
        "word_count": payload.word_count,
        "needs_js": bool(fetch_result.needs_js),
        "snapshot_id": snapshot.id,
        "previous_snapshot_id": previous_id,
        "diff": diff_d,
        "events": events_saved,
        "quality": quality,
    }


def analyze_url(
    session: Session,
    project_id: int,
    url: str,
    keep_html: bool = False,
    fetcher: HybridFetcher | None = None,
    progress_cb: Callable[[dict], None] | None = None,
) -> dict:
    """Exécute le pipeline canonique complet sur une URL : FETCH puis `analyze_fetch_result`.

    - `validate_url` lève `SecurityError` (→ 422 côté API) si l'URL est dangereuse ;
    - un `FetchResult` en erreur lève `FetchError` (→ 502 côté API).

    Point d'entrée utilisé par `POST /api/analyze`, l'UI et les scripts.
    """
    normalized = validate_url(url)  # SecurityError -> remonte telle quelle
    http = fetcher or HybridFetcher()
    started = time.monotonic()

    result: FetchResult = http.fetch(normalized)  # SecurityError remonte
    duration_ms = int((time.monotonic() - started) * 1000)
    if result.error:
        raise FetchError(f"Échec de la récupération de {url} : {result.error}")
    if progress_cb:
        progress_cb({"phase": "collecte terminée", "status_code": result.status_code})

    fiche = analyze_fetch_result(
        session, project_id, result, keep_html=keep_html, progress_cb=progress_cb,
    )
    # L'URL normalisée validée prime sur l'URL brute du FetchResult.
    fiche["url"] = normalized
    fiche["duration_ms"] = duration_ms + fiche.get("duration_ms", 0)
    return fiche
