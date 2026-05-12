from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote_plus, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class SourceDefinition:
    name: str
    source_type: str
    url: str
    params: Dict[str, Any] = field(default_factory=dict)
    macro_hint: str | None = None
    tech_hint: str | None = None
    skip_page_preview: bool = False  # True for paywalled sites (IEEE, ScienceDirect, Nature)


@dataclass
class RawArticle:
    source: str
    source_type: str
    title: str
    url: str
    published_at: datetime | None
    summary: str | None
    content: str | None = None


def _google_news_search_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


def get_default_sources() -> List[SourceDefinition]:
    return [
        SourceDefinition(
            name="Google News - GaN Semiconductor",
            source_type="rss",
            url=_google_news_search_url(
                '"gallium nitride" OR ("GaN" semiconductor power) -generative -adversarial when:7d'
            ),
            macro_hint="industry",
        ),
        SourceDefinition(
            name="Google News - GaN Stock",
            source_type="rss",
            url=_google_news_search_url(
                '"gallium nitride" semiconductor stock earnings -generative -adversarial when:14d'
            ),
            macro_hint="stock",
        ),
        SourceDefinition(
            name="arXiv - GaN Power/High Frequency",
            source_type="arxiv",
            url=(
                "http://export.arxiv.org/api/query?"
                "search_query=all:(gallium+nitride+OR+GaN)+AND+(power+electronics+OR+high+frequency)"
                "&sortBy=submittedDate&sortOrder=descending"
            ),
            params={"max_results": 30},
            macro_hint="academic",
            tech_hint="high_frequency",
        ),
        SourceDefinition(
            name="Crossref - Nature",
            source_type="crossref",
            url="https://api.crossref.org/works",
            params={"journal": "Nature", "query": "gallium nitride power electronics"},
            macro_hint="academic",
        ),
        SourceDefinition(
            name="Crossref - APL",
            source_type="crossref",
            url="https://api.crossref.org/works",
            params={"journal": "Applied Physics Letters", "query": "gallium nitride device"},
            macro_hint="academic",
        ),
        SourceDefinition(
            name="Crossref - IEEE TPEL",
            source_type="crossref",
            url="https://api.crossref.org/works",
            params={"journal": "IEEE Transactions on Power Electronics", "query": "GaN converter"},
            macro_hint="academic",
            tech_hint="high_power",
        ),
        SourceDefinition(
            name="Crossref - IEEE EDL",
            source_type="crossref",
            url="https://api.crossref.org/works",
            params={"journal": "IEEE Electron Device Letters", "query": "GaN HEMT"},
            macro_hint="academic",
            tech_hint="high_frequency",
        ),
        # ── IEEE Xplore RSS ──────────────────────────────────────────────────────
        SourceDefinition(
            name="IEEE TPEL - Transactions on Power Electronics",
            source_type="rss",
            url="https://ieeexplore.ieee.org/rss/TOC63.XML",
            macro_hint="academic",
            tech_hint="high_power",
            skip_page_preview=True,
        ),
        SourceDefinition(
            name="IEEE EDL - Electron Device Letters",
            source_type="rss",
            url="https://ieeexplore.ieee.org/rss/TOC55.XML",
            macro_hint="academic",
            tech_hint="high_frequency",
            skip_page_preview=True,
        ),
        SourceDefinition(
            name="IEEE TED - Transactions on Electron Devices",
            source_type="rss",
            url="https://ieeexplore.ieee.org/rss/TOC68.XML",
            macro_hint="academic",
            tech_hint="high_frequency",
            skip_page_preview=True,
        ),
        SourceDefinition(
            name="IEEE JEDS - Journal of Electron Devices Society",
            source_type="rss",
            url="https://ieeexplore.ieee.org/rss/TOC6882348.XML",
            macro_hint="academic",
            tech_hint="high_frequency",
            skip_page_preview=True,
        ),
        # ── Nature RSS ───────────────────────────────────────────────────────────
        SourceDefinition(
            name="Nature Electronics",
            source_type="rss",
            url="https://www.nature.com/natelectron.rss",
            macro_hint="academic",
            tech_hint="high_frequency",
            skip_page_preview=True,
        ),
        SourceDefinition(
            name="Nature Energy",
            source_type="rss",
            url="https://www.nature.com/nenergy.rss",
            macro_hint="academic",
            tech_hint="high_power",
            skip_page_preview=True,
        ),
        SourceDefinition(
            name="Nature Materials",
            source_type="rss",
            url="https://www.nature.com/nmat.rss",
            macro_hint="academic",
            tech_hint="materials",
            skip_page_preview=True,
        ),
        # ── ScienceDirect RSS (Elsevier) ─────────────────────────────────────────
        SourceDefinition(
            name="ScienceDirect - Solid-State Electronics",
            source_type="rss",
            url="https://rss.sciencedirect.com/publication/science/00381101",
            macro_hint="academic",
            tech_hint="high_frequency",
            skip_page_preview=True,
        ),
        SourceDefinition(
            name="ScienceDirect - Materials Science & Engineering B",
            source_type="rss",
            url="https://rss.sciencedirect.com/publication/science/09215107",
            macro_hint="academic",
            tech_hint="materials",
            skip_page_preview=True,
        ),
        SourceDefinition(
            name="ScienceDirect - Applied Surface Science",
            source_type="rss",
            url="https://rss.sciencedirect.com/publication/science/01694332",
            macro_hint="academic",
            tech_hint="materials",
            skip_page_preview=True,
        ),
        # ── Google News - GaN Fast Charger (Low Power) ───────────────────────────
        SourceDefinition(
            name="Google News - GaN Fast Charger (Low Power)",
            source_type="rss",
            url=_google_news_search_url(
                '"GaN" fast charger USB-C power adapter -generative -adversarial when:14d'
            ),
            macro_hint="industry",
            tech_hint="low_power",
        ),
        SourceDefinition(
            name="Google News - GaN EV Inverter (High Power)",
            source_type="rss",
            url=_google_news_search_url(
                '"GaN" EV inverter traction "power semiconductor" -generative -adversarial when:30d'
            ),
            macro_hint="industry",
            tech_hint="high_power",
        ),
    ]


def fetch_from_source(source: SourceDefinition, max_items: int = 20) -> List[RawArticle]:
    if source.source_type in {"rss", "arxiv"}:
        return _fetch_rss_like(source, max_items=max_items)
    if source.source_type == "crossref":
        return _fetch_crossref(source, max_items=max_items)
    return []


def _fetch_rss_like(source: SourceDefinition, max_items: int) -> List[RawArticle]:
    feed_url = source.url
    if source.source_type == "arxiv":
        max_results = source.params.get("max_results", max_items)
        feed_url = f"{source.url}&max_results={int(max_results)}"
    parsed = feedparser.parse(feed_url)
    items: List[RawArticle] = []
    for entry in parsed.entries[:max_items]:
        title = _clean_text(entry.get("title", "")).strip()
        link = entry.get("link", "")
        summary = _clean_text(entry.get("summary", "")).strip() or None
        content = _extract_entry_content(entry)
        # RSS sources often miss useful snippets; try pulling page metadata/body preview.
        # Skip for paywalled academic sites (IEEE, ScienceDirect, Nature) — fetching would fail.
        if source.source_type == "rss" and not source.skip_page_preview:
            needs_enrich = (not summary or len(summary) < 80) or (not content or len(content) < 180)
            if needs_enrich:
                page_summary, page_content = fetch_article_page_preview(link)
                if (not summary or len(summary) < 80) and page_summary:
                    summary = page_summary
                if page_content:
                    content = page_content
        published_at = _parse_feed_date(entry)
        if not title or not link:
            continue
        items.append(
            RawArticle(
                source=source.name,
                source_type=source.source_type,
                title=title,
                url=link,
                published_at=published_at,
                summary=_truncate_text(summary, 600) if summary else None,
                content=_truncate_text(content, 6000) if content else None,
            )
        )
    return items


def _fetch_crossref(source: SourceDefinition, max_items: int) -> List[RawArticle]:
    params = {
        "query": source.params.get("query", "gallium nitride"),
        "query.container-title": source.params.get("journal", ""),
        "rows": max_items,
        "sort": "published",
        "order": "desc",
    }
    with httpx.Client(timeout=20) as client:
        response = client.get(source.url, params=params)
        response.raise_for_status()
        payload = response.json()
    rows = payload.get("message", {}).get("items", [])

    items: List[RawArticle] = []
    for row in rows:
        title_list = row.get("title") or []
        title = _clean_text(title_list[0]) if title_list else ""
        doi = row.get("DOI")
        link = row.get("URL") or (f"https://doi.org/{doi}" if doi else "")
        summary = _clean_text(row.get("abstract", "")) or None
        published_at = _parse_crossref_date(row)
        if not title or not link:
            continue
        items.append(
            RawArticle(
                source=source.name,
                source_type=source.source_type,
                title=title,
                url=link,
                published_at=published_at,
                summary=_truncate_text(summary, 600) if summary else None,
            )
        )
    return items


def _clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())


def _extract_entry_content(entry: dict) -> str | None:
    chunks: List[str] = []
    content_entries = entry.get("content") or []
    if isinstance(content_entries, list):
        for content_obj in content_entries:
            if isinstance(content_obj, dict):
                value = _clean_text(content_obj.get("value", ""))
                if value:
                    chunks.append(value)
    for key in ("description", "summary"):
        value = _clean_text(entry.get(key, ""))
        if value:
            chunks.append(value)
    if not chunks:
        return None
    merged = " ".join(chunks)
    merged = _truncate_text(merged, 6000)
    return merged or None


def fetch_article_page_preview(url: str, timeout_seconds: int = 6) -> Tuple[str | None, str | None]:
    if not url:
        return None, None
    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            resp = client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            if resp.status_code >= 400:
                return None, None
            html = resp.text
    except Exception:
        return None, None

    soup = BeautifulSoup(html, "html.parser")
    summary = _extract_meta_description(soup)
    content = _extract_main_content(soup)

    # Google News indirection pages often have little readable text.
    host = (urlparse(str(resp.url)).hostname or "").lower()
    if host.endswith("news.google.com") and (not content or len(content) < 120):
        return summary, None

    if not summary and content:
        summary = _truncate_text(content, 300)
    return (
        _truncate_text(summary, 600) if summary else None,
        _truncate_text(content, 6000) if content else None,
    )


def _extract_meta_description(soup: BeautifulSoup) -> str | None:
    for attr_name, attr_value in (
        ("property", "og:description"),
        ("name", "description"),
        ("name", "twitter:description"),
    ):
        tag = soup.find("meta", attrs={attr_name: attr_value})
        if not tag:
            continue
        content = _clean_text(tag.get("content", ""))
        if content:
            return content
    return None


def _extract_main_content(soup: BeautifulSoup) -> str | None:
    candidates = [soup.find("article"), soup.find("main"), soup.body, soup]
    seen = set()
    fragments: List[str] = []
    for node in candidates:
        if node is None:
            continue
        for tag in node.find_all(["p", "h2", "h3", "li"], limit=250):
            text = _clean_text(tag.get_text(" ", strip=True))
            if len(text) < 35:
                continue
            if text in seen:
                continue
            seen.add(text)
            fragments.append(text)
            if len(" ".join(fragments)) > 9000:
                merged = _truncate_text(" ".join(fragments), 6000)
                return merged or None
        if fragments:
            break
    if not fragments:
        return None
    merged = _truncate_text(" ".join(fragments), 6000)
    return merged or None


def _truncate_text(value: str | None, limit: int) -> str | None:
    if not value:
        return value
    raw = value.strip()
    if len(raw) <= limit:
        return raw
    return raw[:limit].rstrip() + "..."


def _parse_feed_date(entry: dict) -> datetime | None:
    published = entry.get("published") or entry.get("updated")
    if not published:
        return None
    try:
        parsed = parsedate_to_datetime(published)
    except Exception:
        try:
            return datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        except Exception:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_crossref_date(item: dict) -> datetime | None:
    candidates = [
        item.get("published-online", {}).get("date-parts"),
        item.get("published-print", {}).get("date-parts"),
        item.get("created", {}).get("date-parts"),
    ]
    for date_parts in candidates:
        if not date_parts:
            continue
        values = date_parts[0]
        if not values:
            continue
        try:
            year = int(values[0])
            month = int(values[1]) if len(values) > 1 else 1
            day = int(values[2]) if len(values) > 2 else 1
            return datetime(year, month, day, tzinfo=timezone.utc)
        except Exception:
            continue
    return None
