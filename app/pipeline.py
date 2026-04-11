from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import yfinance as yf
from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .category_registry import get_active_category_keys
from .config import Settings
from .deepseek_client import DeepSeekClient
from .models import Article, StockSnapshot
from .source_registry import sources_for_ingestion
from .sources import RawArticle, fetch_article_page_preview, fetch_from_source, get_default_sources
from .taxonomy import classify_text

logger = logging.getLogger(__name__)

DEFAULT_TECH_BASE_KEYS = ("low_power", "high_power", "high_frequency", "materials", "packaging", "other")


@dataclass
class IngestResult:
    fetched: int = 0
    inserted: int = 0
    skipped: int = 0
    errors: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "fetched": self.fetched,
            "inserted": self.inserted,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def run_ingestion(session: Session, settings: Settings, deepseek: DeepSeekClient) -> IngestResult:
    result = IngestResult()
    macro_keys = get_active_category_keys(session, "macro") or {"industry", "stock", "academic"}
    tech_keys = get_active_category_keys(session, "tech") or {
        "industry_low_power",
        "industry_high_power",
        "industry_high_frequency",
        "industry_materials",
        "industry_packaging",
        "industry_other",
        "academic_low_power",
        "academic_high_power",
        "academic_high_frequency",
        "academic_materials",
        "academic_packaging",
        "academic_other",
    }
    sources = sources_for_ingestion(session)
    if not sources:
        sources = get_default_sources()

    seen_urls: set[str] = set()
    pending_articles: Dict[str, Article] = {}

    for source in sources:
        try:
            rows = fetch_from_source(source, max_items=settings.max_articles_per_source)
        except Exception:
            logger.exception("Failed to fetch source: %s", source.name)
            result.errors += 1
            continue

        result.fetched += len(rows)
        for row in rows:
            if not row.url:
                result.skipped += 1
                continue
            article_url = row.url.strip()[:1500]
            if not article_url:
                result.skipped += 1
                continue

            summary = row.summary or _derive_summary_from_content(row.content)
            if not _is_gan_relevant_article(row.title, summary, row.content):
                result.skipped += 1
                continue

            pending = pending_articles.get(article_url)
            if pending:
                changed = False
                if summary and not pending.summary:
                    pending.summary = summary
                    changed = True
                if row.content and not pending.content:
                    pending.content = row.content
                    changed = True
                if changed:
                    pending.updated_at = datetime.now(timezone.utc)
                result.skipped += 1
                continue

            if article_url in seen_urls:
                result.skipped += 1
                continue

            existing = _get_article_by_url(session, article_url)
            if existing:
                updated = False
                if summary and not existing.summary:
                    existing.summary = summary
                    updated = True
                if row.content and not existing.content:
                    existing.content = row.content
                    updated = True
                if updated:
                    existing.updated_at = datetime.now(timezone.utc)
                seen_urls.add(article_url)
                result.skipped += 1
                continue

            # Keep a deterministic fallback from title text; DeepSeek is the primary classifier.
            macro, tech, tags = classify_text(row.title, None)
            if source.macro_hint:
                macro = source.macro_hint
            if source.tech_hint and tech == "other":
                tech = source.tech_hint

            macro = _resolve_macro_choice(macro, macro_keys, fallback="industry")
            tech = _resolve_tech_choice(
                macro=macro,
                tech=tech,
                valid_tech_keys=tech_keys,
                fallback="industry_other",
            )

            ds_analysis = None
            ds_text = None
            sentiment = None
            impact = None
            if deepseek.enabled:
                try:
                    ds_analysis = deepseek.analyze_article(
                        title=row.title, summary=None, macro_hint=macro, tech_hint=tech
                    )
                except Exception:
                    logger.exception("DeepSeek analysis failed for: %s", row.url)

            if ds_analysis:
                macro = _resolve_macro_choice(ds_analysis.macro_category, macro_keys, fallback=macro)
                tech = _resolve_tech_choice(
                    macro=macro,
                    tech=ds_analysis.tech_category,
                    valid_tech_keys=tech_keys,
                    fallback=tech,
                )
                sentiment = ds_analysis.sentiment_score
                impact = ds_analysis.impact_score
                ds_text = ds_analysis.analysis_text

            article = Article(
                source=row.source[:120],
                source_type=row.source_type[:40],
                title=row.title[:600],
                url=article_url,
                published_at=row.published_at,
                summary=summary,
                content=row.content,
                macro_category=macro,
                tech_category=tech,
                tags_csv=",".join(tags) if tags else None,
                sentiment_score=sentiment,
                impact_score=impact,
                deepseek_analysis=ds_text,
            )
            try:
                with session.begin_nested():
                    session.add(article)
                    session.flush()
                pending_articles[article_url] = article
                seen_urls.add(article_url)
                result.inserted += 1
            except IntegrityError:
                logger.warning("Skipped duplicate URL during ingestion: %s", article_url)
                result.skipped += 1

    session.commit()
    return result


def enrich_existing_article_content(session: Session, *, days: int = 45, limit: int = 200) -> Dict[str, int]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = list(
        session.scalars(
            select(Article)
            .where(
                or_(Article.published_at.is_(None), Article.published_at >= since),
                or_(Article.summary.is_(None), Article.content.is_(None)),
            )
            .order_by(desc(Article.published_at), desc(Article.created_at))
            .limit(limit)
        )
    )
    scanned = 0
    updated = 0
    errors = 0
    for article in rows:
        scanned += 1
        try:
            page_summary, page_content = fetch_article_page_preview(article.url)
            changed = False
            if page_summary and not article.summary:
                article.summary = page_summary
                changed = True
            if page_content and not article.content:
                article.content = page_content
                changed = True
            if changed:
                updated += 1
        except Exception:
            errors += 1
            logger.exception("Enrich content failed: %s", article.url)
    session.commit()
    return {"scanned": scanned, "updated": updated, "errors": errors}


def refresh_stock_snapshots(session: Session, settings: Settings) -> Dict[str, int]:
    inserted = 0
    errors = 0
    for ticker in settings.stock_tickers:
        try:
            snapshot = _fetch_one_ticker(ticker)
            if not snapshot:
                continue
            session.add(
                StockSnapshot(
                    ticker=ticker,
                    price=snapshot.get("price"),
                    change_pct=snapshot.get("change_pct"),
                    volume=snapshot.get("volume"),
                )
            )
            inserted += 1
        except Exception:
            logger.exception("Stock refresh failed for %s", ticker)
            errors += 1
    session.commit()
    return {"inserted": inserted, "errors": errors}


def backfill_stock_snapshots(
    session: Session,
    settings: Settings,
    *,
    period: str = "5d",
    interval: str = "15m",
    max_rows_per_ticker: int = 500,
) -> Dict[str, int]:
    inserted = 0
    errors = 0
    for ticker in settings.stock_tickers:
        try:
            latest_dt = session.scalar(select(func.max(StockSnapshot.captured_at)).where(StockSnapshot.ticker == ticker))
            history = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
            if history is None or history.empty:
                continue
            history = history.tail(max_rows_per_ticker)
            prev_close = None
            for index, row in history.iterrows():
                price = _safe_float(row.get("Close"))
                if price is None:
                    continue
                ts = _normalize_timestamp(index)
                if latest_dt and ts <= latest_dt:
                    prev_close = price
                    continue
                volume = _safe_float(row.get("Volume"))
                change_pct = ((price - prev_close) / prev_close * 100.0) if prev_close else None
                session.add(
                    StockSnapshot(
                        ticker=ticker,
                        price=price,
                        change_pct=change_pct,
                        volume=volume,
                        captured_at=ts,
                    )
                )
                inserted += 1
                prev_close = price
        except Exception:
            logger.exception("Stock backfill failed for %s", ticker)
            errors += 1
    session.commit()
    return {"inserted": inserted, "errors": errors}


def query_articles(
    session: Session,
    *,
    macro: Optional[str] = None,
    tech: Optional[str] = None,
    module_group: Optional[str] = None,
    q: Optional[str] = None,
    days: int = 30,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = 200,
) -> List[Article]:
    stmt = select(Article)
    if date_from or date_to:
        stamp = func.coalesce(Article.published_at, Article.created_at)
        if date_from:
            from_dt = datetime.combine(date_from, time.min).replace(tzinfo=timezone.utc)
            stmt = stmt.where(stamp >= from_dt)
        if date_to:
            to_dt = datetime.combine(date_to, time.max).replace(tzinfo=timezone.utc)
            stmt = stmt.where(stamp <= to_dt)
    else:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = stmt.where(or_(Article.published_at.is_(None), Article.published_at >= since))

    macro_keys = get_active_category_keys(session, "macro")
    tech_keys = get_active_category_keys(session, "tech")

    group = (module_group or "").strip().lower()
    if group == "power":
        stmt = stmt.where(
            Article.tech_category.in_(
                [
                    "low_power",
                    "high_power",
                    "industry_low_power",
                    "industry_high_power",
                    "academic_low_power",
                    "academic_high_power",
                ]
            )
        )
    elif group in macro_keys:
        stmt = stmt.where(Article.macro_category == group)
    elif group in tech_keys:
        stmt = stmt.where(Article.tech_category == group)

    if macro:
        stmt = stmt.where(Article.macro_category == macro)
    if tech:
        stmt = stmt.where(Article.tech_category == tech)
    if q:
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(or_(Article.title.ilike(keyword), Article.summary.ilike(keyword)))
    stmt = stmt.order_by(desc(Article.published_at), desc(Article.created_at)).limit(limit)
    return list(session.scalars(stmt))


def query_category_stats(session: Session, *, days: int = 7) -> Dict[str, Dict[str, int]]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = session.execute(
        select(Article.macro_category, Article.tech_category)
        .where(or_(Article.published_at.is_(None), Article.published_at >= since))
        .order_by(desc(Article.created_at))
    ).all()
    macro_counter = Counter(row[0] for row in rows)
    tech_counter = Counter(row[1] for row in rows)
    return {"macro": dict(macro_counter), "tech": dict(tech_counter)}


def query_latest_stocks(session: Session) -> List[Dict[str, float | str | None]]:
    # SQLite-friendly approach: read latest rows first, then keep newest per ticker.
    rows = list(
        session.scalars(
            select(StockSnapshot).order_by(desc(StockSnapshot.captured_at), desc(StockSnapshot.id)).limit(500)
        )
    )
    latest: Dict[str, StockSnapshot] = {}
    for row in rows:
        if row.ticker not in latest:
            latest[row.ticker] = row
        if len(latest) >= 60:
            break
    output = []
    for ticker in sorted(latest):
        item = latest[ticker]
        output.append(
            {
                "ticker": ticker,
                "price": item.price,
                "change_pct": item.change_pct,
                "volume": item.volume,
                "captured_at": item.captured_at.isoformat() if item.captured_at else None,
            }
        )
    return output


def query_stock_series(session: Session, ticker: str, *, points: int = 240) -> List[Dict[str, float | str | None]]:
    rows = list(
        session.scalars(
            select(StockSnapshot)
            .where(StockSnapshot.ticker == ticker.upper())
            .order_by(desc(StockSnapshot.captured_at), desc(StockSnapshot.id))
            .limit(points)
        )
    )
    rows.reverse()
    return [
        {
            "ticker": row.ticker,
            "captured_at": row.captured_at.isoformat() if row.captured_at else None,
            "price": row.price,
            "change_pct": row.change_pct,
            "volume": row.volume,
        }
        for row in rows
    ]


def weekly_articles(session: Session, *, days: int = 7) -> List[Article]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return list(
        session.scalars(
            select(Article)
            .where(or_(Article.published_at.is_(None), Article.published_at >= since))
            .order_by(desc(Article.impact_score), desc(Article.published_at), desc(Article.created_at))
            .limit(500)
        )
    )


def _fetch_one_ticker(ticker: str) -> Optional[Dict[str, float | None]]:
    hist = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        return None
    latest_close = float(hist["Close"].iloc[-1]) if "Close" in hist else None
    if latest_close is None:
        return None
    previous_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else latest_close
    volume = float(hist["Volume"].iloc[-1]) if "Volume" in hist else None
    change_pct = ((latest_close - previous_close) / previous_close * 100.0) if previous_close else 0.0
    return {"price": latest_close, "change_pct": change_pct, "volume": volume}


def _article_exists(session: Session, url: str) -> bool:
    stmt = select(func.count()).select_from(Article).where(Article.url == url[:1500])
    return bool(session.scalar(stmt))


def _get_article_by_url(session: Session, url: str) -> Article | None:
    return session.scalar(select(Article).where(Article.url == url[:1500]).limit(1))


def _safe_choice(value: str, valid_choices: Iterable[str], fallback: str) -> str:
    value = (value or "").strip()
    return value if value in set(valid_choices) else fallback


def _resolve_macro_choice(value: str | None, valid_keys: Iterable[str], fallback: str = "industry") -> str:
    choices = {str(x) for x in valid_keys if x}
    normalized = (value or "").strip().lower()
    if normalized in choices:
        return normalized
    if fallback in choices:
        return fallback
    for candidate in ("industry", "academic", "stock"):
        if candidate in choices:
            return candidate
    return sorted(choices)[0] if choices else fallback


def _resolve_tech_choice(
    *,
    macro: str,
    tech: str | None,
    valid_tech_keys: Iterable[str],
    fallback: str,
) -> str:
    choices = {str(x) for x in valid_tech_keys if x}
    normalized = (tech or "").strip().lower()
    if normalized in choices:
        return normalized

    if macro in {"academic", "industry"}:
        suffix = normalized
        if suffix.startswith("academic_") or suffix.startswith("industry_"):
            _, _, suffix = suffix.partition("_")
        if suffix in DEFAULT_TECH_BASE_KEYS:
            candidate = f"{macro}_{suffix}"
            if candidate in choices:
                return candidate
        fallback_macro = f"{macro}_other"
        if fallback_macro in choices:
            return fallback_macro

    if normalized in DEFAULT_TECH_BASE_KEYS:
        for candidate in (f"industry_{normalized}", f"academic_{normalized}"):
            if candidate in choices:
                return candidate

    for candidate in ("industry_other", "academic_other", fallback):
        if candidate in choices:
            return candidate
    return sorted(choices)[0] if choices else (normalized or fallback)


def _derive_summary_from_content(content: str | None) -> str | None:
    if not content:
        return None
    text = content.strip()
    if not text:
        return None
    if len(text) <= 320:
        return text
    return text[:320].rstrip() + "..."


GAN_EXCLUSION_HINTS = (
    "generative adversarial",
    "diffusion",
    "large language model",
    "machine learning",
    "image generation",
)

GAN_SEMICONDUCTOR_HINTS = (
    "gallium nitride",
    "semiconductor",
    "power electronics",
    "power device",
    "power supply",
    "hemt",
    "gan fet",
    "transistor",
    "charger",
    "inverter",
    "mmwave",
    "wafer",
    "epitaxy",
    "氮化镓",
)


def _is_gan_relevant_article(title: str, summary: str | None, content: str | None) -> bool:
    text = " ".join(x for x in [title or "", summary or "", content or ""] if x).strip()
    if not text:
        return False
    normalized = " ".join(text.lower().split())

    if "gallium nitride" in normalized or "氮化镓" in normalized:
        return True

    has_gan_token = bool(re.search(r"\bGaN\b", text) or re.search(r"\bgan\b", text))
    if not has_gan_token:
        return False

    has_semiconductor_hint = any(term in normalized for term in GAN_SEMICONDUCTOR_HINTS)
    has_ai_hint = any(term in normalized for term in GAN_EXCLUSION_HINTS)
    return has_semiconductor_hint and not has_ai_hint


def _normalize_timestamp(value) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime.now(timezone.utc)


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
