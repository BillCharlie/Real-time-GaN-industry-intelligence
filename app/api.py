from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .category_registry import (
    categories_payload,
    category_dict,
    create_category,
    delete_category,
    ensure_seed_categories,
    get_active_category_keys,
    update_category,
)
from .config import get_settings
from .db import SessionLocal, engine, get_db_session
from .deepseek_client import DeepSeekClient
from .models import Article, Base
from .pipeline import (
    backfill_stock_snapshots,
    enrich_existing_article_content,
    query_articles,
    query_category_stats,
    query_latest_stocks,
    query_stock_series,
    refresh_stock_snapshots,
    run_ingestion,
)
from .reporter import build_weekly_report, send_weekly_email
from .scheduler import RuntimeScheduler
from .source_registry import (
    active_company_domains,
    available_modules,
    approve_source,
    company_to_dict,
    create_source,
    create_company_whitelist,
    delete_company_whitelist,
    delete_source,
    ensure_seed_sources,
    ensure_seed_company_whitelist,
    list_sources,
    list_company_whitelist,
    source_to_dict,
    sync_company_whitelist_sources,
    update_company_whitelist,
    update_source,
    verify_source,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

settings = get_settings()
deepseek_client = DeepSeekClient(settings)

templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

app = FastAPI(title=settings.project_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=8, max_length=1500)
    source_type: str = Field(default="rss")
    module_type: str = Field(default="macro")
    module_key: str = Field(default="industry")
    params: dict = Field(default_factory=dict)


class SourceApproveRequest(BaseModel):
    note: Optional[str] = None


class SourceUpdateRequest(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    source_type: Optional[str] = None
    module_type: Optional[str] = None
    module_key: Optional[str] = None
    params: Optional[dict] = None
    active: Optional[bool] = None
    reverify: bool = False


class CompanyWhitelistCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=3, max_length=300)
    active: bool = True
    note: Optional[str] = None


class CompanyWhitelistUpdateRequest(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    active: Optional[bool] = None
    note: Optional[str] = None


class ArticleClassificationUpdateRequest(BaseModel):
    macro_category: Optional[str] = None
    tech_category: Optional[str] = None


class CategoryCreateRequest(BaseModel):
    group_type: str
    key: str = ""
    label: str
    parent_id: Optional[int] = None
    active: bool = True
    sort_order: int = 100


class CategoryUpdateRequest(BaseModel):
    label: Optional[str] = None
    active: Optional[bool] = None
    sort_order: Optional[int] = None


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        categories_seeded = ensure_seed_categories(session)
        if categories_seeded:
            logger.info("Seeded categories: %s", categories_seeded)
        whitelist_seeded = ensure_seed_company_whitelist(session)
        if whitelist_seeded:
            logger.info("Seeded company whitelist rows: %s", whitelist_seeded)
        synced = sync_company_whitelist_sources(session)
        if synced["created"] or synced["activated"] or synced["deactivated"]:
            logger.info("Synced whitelist sources: %s", synced)
        seeded = ensure_seed_sources(session)
        if seeded:
            logger.info("Seeded default source sites: %s", seeded)
    app.state.runtime_scheduler = RuntimeScheduler(settings, deepseek_client)
    app.state.runtime_scheduler.start()
    logger.info("Application started.")


@app.on_event("shutdown")
def on_shutdown():
    runtime_scheduler: RuntimeScheduler | None = getattr(app.state, "runtime_scheduler", None)
    if runtime_scheduler:
        runtime_scheduler.shutdown()
    logger.info("Application stopped.")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "project_name": settings.project_name,
            "deepseek_enabled": deepseek_client.enabled,
            "email_enabled": settings.email_enabled,
            "timezone": settings.timezone,
            "stock_tickers": settings.stock_tickers,
        },
    )


@app.get("/api/config")
def api_config():
    return {
        "project_name": settings.project_name,
        "deepseek_enabled": deepseek_client.enabled,
        "email_enabled": settings.email_enabled,
        "timezone": settings.timezone,
        "stock_tickers": settings.stock_tickers,
    }


@app.get("/api/articles")
def api_articles(
    macro: Optional[str] = Query(default=None),
    tech: Optional[str] = Query(default=None),
    module_group: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1, le=180),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    limit: int = Query(default=120, ge=1, le=500),
    session: Session = Depends(get_db_session),
):
    rows = query_articles(
        session,
        macro=macro,
        tech=tech,
        module_group=module_group,
        q=q,
        days=days,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return {
        "count": len(rows),
        "items": [
            {
                "id": row.id,
                "source": row.source,
                "source_type": row.source_type,
                "title": row.title,
                "url": row.url,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "summary": row.summary,
                "content": row.content,
                "macro_category": row.macro_category,
                "tech_category": row.tech_category,
                "tags": row.tags_csv.split(",") if row.tags_csv else [],
                "sentiment_score": row.sentiment_score,
                "impact_score": row.impact_score,
                "deepseek_analysis": row.deepseek_analysis,
            }
            for row in rows
        ],
    }


@app.put("/api/articles/{article_id}/classification")
def api_update_article_classification(
    article_id: int,
    payload: ArticleClassificationUpdateRequest,
    session: Session = Depends(get_db_session),
):
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail=f"Article not found: {article_id}")

    if payload.macro_category is not None:
        macro_keys = get_active_category_keys(session, "macro")
        if payload.macro_category not in macro_keys:
            raise HTTPException(status_code=400, detail=f"Invalid macro_category: {payload.macro_category}")
        article.macro_category = payload.macro_category

    if payload.tech_category is not None:
        tech_keys = get_active_category_keys(session, "tech")
        if payload.tech_category not in tech_keys:
            raise HTTPException(status_code=400, detail=f"Invalid tech_category: {payload.tech_category}")
        article.tech_category = payload.tech_category

    session.commit()
    session.refresh(article)
    return {
        "ok": True,
        "item": {
            "id": article.id,
            "macro_category": article.macro_category,
            "tech_category": article.tech_category,
            "updated_at": article.updated_at.isoformat() if article.updated_at else None,
        },
    }


@app.get("/api/stats")
def api_stats(days: int = Query(default=7, ge=1, le=90), session: Session = Depends(get_db_session)):
    return {"days": days, "updated_at": datetime.utcnow().isoformat() + "Z", **query_category_stats(session, days=days)}


@app.get("/api/stocks")
def api_stocks(session: Session = Depends(get_db_session)):
    return {"items": query_latest_stocks(session)}


@app.get("/api/stocks/{ticker}/series")
def api_stock_series(
    ticker: str,
    points: int = Query(default=240, ge=20, le=2000),
    session: Session = Depends(get_db_session),
):
    rows = query_stock_series(session, ticker=ticker, points=points)
    return {"ticker": ticker.upper(), "count": len(rows), "items": rows}


@app.get("/api/source-sites/modules")
def api_source_modules(session: Session = Depends(get_db_session)):
    return available_modules(session)


@app.get("/api/categories")
def api_categories(
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_db_session),
):
    return categories_payload(session, include_inactive=include_inactive)


@app.post("/api/categories")
def api_create_category(payload: CategoryCreateRequest, session: Session = Depends(get_db_session)):
    try:
        row = create_category(
            session,
            group_type=payload.group_type,
            key=payload.key,
            label=payload.label,
            parent_id=payload.parent_id,
            active=payload.active,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": category_dict(row)}


@app.put("/api/categories/{category_id}")
def api_update_category(category_id: int, payload: CategoryUpdateRequest, session: Session = Depends(get_db_session)):
    try:
        row = update_category(
            session,
            category_id,
            label=payload.label,
            active=payload.active,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": category_dict(row)}


@app.delete("/api/categories/{category_id}")
def api_delete_category(category_id: int, session: Session = Depends(get_db_session)):
    try:
        deleted = delete_category(session, category_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": deleted}


@app.get("/api/source-sites")
def api_source_sites(
    module_type: Optional[str] = Query(default=None),
    module_key: Optional[str] = Query(default=None),
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_db_session),
):
    rows = list_sources(session, module_type=module_type, module_key=module_key, include_inactive=include_inactive)
    return {"count": len(rows), "items": [source_to_dict(row) for row in rows]}


@app.post("/api/source-sites")
def api_create_source(payload: SourceCreateRequest, session: Session = Depends(get_db_session)):
    try:
        row = create_source(
            session,
            name=payload.name,
            url=payload.url,
            source_type=payload.source_type,
            module_type=payload.module_type,
            module_key=payload.module_key,
            params=payload.params,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": source_to_dict(row)}


@app.put("/api/source-sites/{source_id}")
def api_update_source(source_id: int, payload: SourceUpdateRequest, session: Session = Depends(get_db_session)):
    try:
        row = update_source(
            session,
            source_id,
            name=payload.name,
            url=payload.url,
            source_type=payload.source_type,
            module_type=payload.module_type,
            module_key=payload.module_key,
            params=payload.params,
            active=payload.active,
            reverify=payload.reverify,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": source_to_dict(row)}


@app.delete("/api/source-sites/{source_id}")
def api_delete_source(source_id: int, session: Session = Depends(get_db_session)):
    try:
        deleted = delete_source(session, source_id=source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": deleted}


@app.post("/api/source-sites/{source_id}/verify")
def api_verify_source(source_id: int, session: Session = Depends(get_db_session)):
    try:
        row = verify_source(session, source_id=source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "item": source_to_dict(row)}


@app.post("/api/source-sites/{source_id}/approve")
def api_approve_source(source_id: int, payload: SourceApproveRequest, session: Session = Depends(get_db_session)):
    try:
        row = approve_source(session, source_id=source_id, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "item": source_to_dict(row)}


@app.get("/api/company-whitelist")
def api_company_whitelist(include_inactive: bool = Query(default=True), session: Session = Depends(get_db_session)):
    rows = list_company_whitelist(session, include_inactive=include_inactive)
    return {
        "count": len(rows),
        "trusted_domains": active_company_domains(session),
        "items": [company_to_dict(row) for row in rows],
    }


@app.post("/api/company-whitelist")
def api_create_company_whitelist(payload: CompanyWhitelistCreateRequest, session: Session = Depends(get_db_session)):
    try:
        row = create_company_whitelist(
            session,
            name=payload.name,
            domain=payload.domain,
            active=payload.active,
            note=payload.note,
        )
        synced = sync_company_whitelist_sources(session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": company_to_dict(row), "synced": synced}


@app.put("/api/company-whitelist/{company_id}")
def api_update_company_whitelist(
    company_id: int, payload: CompanyWhitelistUpdateRequest, session: Session = Depends(get_db_session)
):
    try:
        row = update_company_whitelist(
            session,
            company_id,
            name=payload.name,
            domain=payload.domain,
            active=payload.active,
            note=payload.note,
        )
        synced = sync_company_whitelist_sources(session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": company_to_dict(row), "synced": synced}


@app.delete("/api/company-whitelist/{company_id}")
def api_delete_company_whitelist(company_id: int, session: Session = Depends(get_db_session)):
    try:
        deleted = delete_company_whitelist(session, company_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **deleted}


@app.post("/api/company-whitelist/sync")
def api_sync_company_sources(session: Session = Depends(get_db_session)):
    return {"ok": True, "result": sync_company_whitelist_sources(session)}


@app.post("/api/tasks/ingest")
def api_run_ingest():
    with SessionLocal() as session:
        result = run_ingestion(session, settings, deepseek_client)
        return {"ok": True, "result": result.as_dict()}


@app.post("/api/tasks/enrich-content")
def api_enrich_content(
    days: int = Query(default=45, ge=1, le=365),
    limit: int = Query(default=200, ge=20, le=3000),
):
    with SessionLocal() as session:
        result = enrich_existing_article_content(session, days=days, limit=limit)
        return {"ok": True, "result": result}


@app.post("/api/tasks/stocks")
def api_run_stock_refresh():
    with SessionLocal() as session:
        result = refresh_stock_snapshots(session, settings)
        return {"ok": True, "result": result}


@app.post("/api/tasks/stocks/backfill")
def api_stock_backfill(
    period: str = Query(default="5d"),
    interval: str = Query(default="15m"),
    max_rows_per_ticker: int = Query(default=500, ge=50, le=4000),
):
    with SessionLocal() as session:
        result = backfill_stock_snapshots(
            session,
            settings,
            period=period,
            interval=interval,
            max_rows_per_ticker=max_rows_per_ticker,
        )
        return {"ok": True, "result": result, "period": period, "interval": interval}


@app.post("/api/admin/upload-db")
async def api_upload_db(token: str = Query(...), file: UploadFile = File(...)):
    """Temporary endpoint to upload a SQLite DB file. Protected by ADMIN_TOKEN env var."""
    import os, shutil
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token")
    from .db import engine
    db_path = settings.db_url.replace("sqlite:///", "").replace("sqlite://", "")
    engine.dispose()
    tmp = db_path + ".tmp"
    with open(tmp, "wb") as f:
        shutil.copyfileobj(file.file, f)
    os.replace(tmp, db_path)
    return {"ok": True, "size": os.path.getsize(db_path)}


@app.post("/api/tasks/send-weekly")
def api_send_weekly():
    if not settings.email_enabled:
        raise HTTPException(status_code=400, detail="Email is not configured.")
    with SessionLocal() as session:
        subject, text_body, html_body, stats = build_weekly_report(session, settings, deepseek_client)
        send_weekly_email(session, settings, subject, text_body, html_body)
    return {"ok": True, "subject": subject, "stats": stats}
