from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote_plus, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

# Crossref and OpenAlex both run a "polite pool": callers who identify themselves
# get a dedicated, far more reliable slice of capacity. Anonymous traffic is the
# first to be throttled, which is exactly how these sources end up returning
# nothing on a busy host.
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip() or os.getenv("GMAIL_USER", "").strip()
_POLITE_HEADERS = {
    "User-Agent": (
        "GaNIndustryMonitor/1.0 (+https://realtimegan.up.railway.app;"
        f" mailto:{CONTACT_EMAIL or 'unknown'})"
    ),
    "Accept": "application/json",
}


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
    """Every source here is abstract-level only: RSS summaries, Crossref/OpenAlex
    abstract fields, arXiv summaries. Nothing fetches a PDF or a full text body.

    Journal-wide table-of-contents feeds were dropped. GaN is a sliver of what
    Nature Electronics or Applied Surface Science publish, so the relevance filter
    discarded ~100% of what they returned while they still cost a fetch every run.
    Query-shaped sources (arXiv / Crossref / OpenAlex) are GaN-specific at the
    source, so what they return is already on topic.
    """
    return [
        # ── Industry news (Google News queries) ──────────────────────────────
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
        SourceDefinition(
            name="Google News - GaN Foundry & Capacity",
            source_type="rss",
            url=_google_news_search_url(
                '"GaN" (foundry OR fab OR "200mm" OR "8-inch" OR capacity) semiconductor when:30d'
            ),
            macro_hint="industry",
            tech_hint="materials",
        ),
        SourceDefinition(
            name="Google News - GaN Data Center Power",
            source_type="rss",
            url=_google_news_search_url(
                '"GaN" ("data center" OR "AI server" OR PSU) power supply when:30d'
            ),
            macro_hint="industry",
            tech_hint="high_power",
        ),

        # ── arXiv (full abstracts, free, GaN-specific queries) ───────────────
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
            name="arXiv - GaN HEMT Devices",
            source_type="arxiv",
            url=(
                "http://export.arxiv.org/api/query?"
                "search_query=all:(GaN+OR+gallium+nitride)+AND+(HEMT+OR+transistor)"
                "&sortBy=submittedDate&sortOrder=descending"
            ),
            params={"max_results": 30},
            macro_hint="academic",
            tech_hint="high_frequency",
        ),
        SourceDefinition(
            name="arXiv - GaN Epitaxy & Materials",
            source_type="arxiv",
            url=(
                "http://export.arxiv.org/api/query?"
                "search_query=all:(gallium+nitride+OR+GaN)+AND+(epitaxy+OR+substrate+OR+MOCVD)"
                "&sortBy=submittedDate&sortOrder=descending"
            ),
            params={"max_results": 30},
            macro_hint="academic",
            tech_hint="materials",
        ),

        # ── OpenAlex: abstracts for publishers Crossref has none for (IEEE) ──
        SourceDefinition(
            name="OpenAlex - GaN Power Devices",
            source_type="openalex",
            url="https://api.openalex.org/works?_src=gan_power",
            params={"query": "gallium nitride power device", "lookback_days": 120},
            macro_hint="academic",
            tech_hint="high_power",
        ),
        SourceDefinition(
            name="OpenAlex - GaN HEMT",
            source_type="openalex",
            url="https://api.openalex.org/works?_src=gan_hemt",
            params={"query": "GaN HEMT transistor", "lookback_days": 120},
            macro_hint="academic",
            tech_hint="high_frequency",
        ),
        SourceDefinition(
            name="OpenAlex - GaN Epitaxy & Substrate",
            source_type="openalex",
            url="https://api.openalex.org/works?_src=gan_epi",
            params={"query": "gallium nitride epitaxy substrate", "lookback_days": 150},
            macro_hint="academic",
            tech_hint="materials",
        ),
        SourceDefinition(
            name="OpenAlex - GaN Reliability & Packaging",
            source_type="openalex",
            url="https://api.openalex.org/works?_src=gan_rel",
            params={"query": "GaN reliability degradation packaging", "lookback_days": 150},
            macro_hint="academic",
            tech_hint="packaging",
        ),
        SourceDefinition(
            name="OpenAlex - IEEE Electron Device Letters (GaN)",
            source_type="openalex",
            url="https://api.openalex.org/works?_src=ieee_edl",
            params={
                "query": "gallium nitride",
                "venue_issn": "0741-3106",
                "lookback_days": 240,
            },
            macro_hint="academic",
            tech_hint="high_frequency",
        ),
        SourceDefinition(
            name="OpenAlex - IEEE Trans. Power Electronics (GaN)",
            source_type="openalex",
            url="https://api.openalex.org/works?_src=ieee_tpel",
            params={
                "query": "GaN converter",
                "venue_issn": "0885-8993",
                "lookback_days": 240,
            },
            macro_hint="academic",
            tech_hint="high_power",
        ),

        # ── Crossref, kept where the publisher actually deposits abstracts ───
        SourceDefinition(
            name="Crossref - GaN Power Electronics",
            source_type="crossref",
            url="https://api.crossref.org/works?_src=gan_power",
            params={"query": "gallium nitride power electronics", "lookback_days": 120},
            macro_hint="academic",
            tech_hint="high_power",
        ),
        SourceDefinition(
            name="Crossref - Nature",
            source_type="crossref",
            url="https://api.crossref.org/works?_src=nature",
            params={
                "journal": "Nature",
                "query": "gallium nitride power electronics",
                "lookback_days": 240,
            },
            macro_hint="academic",
        ),
        SourceDefinition(
            name="Crossref - Applied Physics Letters",
            source_type="crossref",
            url="https://api.crossref.org/works?_src=apl2",
            params={
                "journal": "Applied Physics Letters",
                "query": "gallium nitride device",
                "lookback_days": 240,
            },
            macro_hint="academic",
            tech_hint="materials",
        ),
        SourceDefinition(
            name="Crossref - Journal of Crystal Growth",
            source_type="crossref",
            url="https://api.crossref.org/works?_src=jcg",
            params={
                "journal": "Journal of Crystal Growth",
                "query": "GaN gallium nitride epitaxy",
                "lookback_days": 240,
            },
            macro_hint="academic",
            tech_hint="materials",
        ),

        # ── Publisher RSS that empirically returns GaN hits ──────────────────
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
            # The old TOC6882348 feed returns zero entries; TOC6245494 is the live one.
            name="IEEE JEDS - Journal of the Electron Devices Society",
            source_type="rss",
            url="https://ieeexplore.ieee.org/rss/TOC6245494.XML",
            macro_hint="academic",
            tech_hint="high_frequency",
            skip_page_preview=True,
        ),
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
    ]


def fetch_from_source(source: SourceDefinition, max_items: int = 20) -> List[RawArticle]:
    if source.source_type in {"rss", "arxiv"}:
        return _fetch_rss_like(source, max_items=max_items)
    if source.source_type == "crossref":
        return _fetch_crossref(source, max_items=max_items)
    if source.source_type == "openalex":
        return _fetch_openalex(source, max_items=max_items)
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
    # query.bibliographic matches title/abstract far more tightly than the generic
    # `query`, which used to drag in papers that only mention GaN in a reference.
    params = {
        "query.bibliographic": source.params.get("query", "gallium nitride"),
        "rows": max_items,
        "sort": "published",
        "order": "desc",
        # Crossref's polite pool. Anonymous callers share a throttled pool and are
        # the first to be shed under load, which is what starves these sources.
        "mailto": CONTACT_EMAIL,
    }
    journal = source.params.get("journal")
    if journal:
        params["query.container-title"] = journal
    lookback_days = int(source.params.get("lookback_days", 180))
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    params["filter"] = "from-pub-date:" + since.strftime("%Y-%m-%d")

    with httpx.Client(timeout=30, headers=_POLITE_HEADERS, follow_redirects=True) as client:
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


def _fetch_openalex(source: SourceDefinition, max_items: int) -> List[RawArticle]:
    """OpenAlex indexes abstracts for far more publishers than Crossref does —
    notably IEEE, which deposits titles to Crossref but no abstract text. Only the
    abstract is pulled here; no full text or PDF is ever requested."""
    filters = ["type:article"]
    lookback_days = int(source.params.get("lookback_days", 120))
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    filters.append("from_publication_date:" + since.strftime("%Y-%m-%d"))
    venue = source.params.get("venue_issn")
    if venue:
        filters.append("primary_location.source.issn:" + venue)

    params = {
        "search": source.params.get("query", "gallium nitride"),
        "filter": ",".join(filters),
        "sort": "publication_date:desc",
        "per-page": min(int(max_items), 50),
        "mailto": CONTACT_EMAIL,
    }
    with httpx.Client(timeout=30, headers=_POLITE_HEADERS, follow_redirects=True) as client:
        response = client.get(source.url, params=params)
        response.raise_for_status()
        payload = response.json()

    items: List[RawArticle] = []
    for row in payload.get("results", []):
        title = _clean_text(row.get("display_name") or "")
        doi = row.get("doi") or ""
        link = doi or (row.get("id") or "")
        if not title or not link:
            continue
        summary = _openalex_abstract(row.get("abstract_inverted_index"))
        items.append(
            RawArticle(
                source=source.name,
                source_type=source.source_type,
                title=title,
                url=link,
                published_at=_parse_openalex_date(row.get("publication_date")),
                summary=_truncate_text(summary, 600) if summary else None,
            )
        )
    return items


def _openalex_abstract(inverted_index: dict | None) -> str | None:
    """OpenAlex ships abstracts as {word: [positions]} for licensing reasons;
    rebuild the running text from it."""
    if not inverted_index:
        return None
    positions: List[Tuple[int, str]] = []
    for word, slots in inverted_index.items():
        for slot in slots or []:
            positions.append((int(slot), word))
    if not positions:
        return None
    positions.sort(key=lambda pair: pair[0])
    return _clean_text(" ".join(word for _, word in positions)) or None


def _parse_openalex_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


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
