from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(600))
    url: Mapped[str] = mapped_column(String(1500), unique=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)

    macro_category: Mapped[str] = mapped_column(String(40), index=True, default="industry")
    tech_category: Mapped[str] = mapped_column(String(60), index=True, default="other")
    tags_csv: Mapped[str | None] = mapped_column(String(500))

    sentiment_score: Mapped[float | None] = mapped_column(Float)
    impact_score: Mapped[float | None] = mapped_column(Float)
    deepseek_analysis: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


Index("idx_articles_macro_tech_pub", Article.macro_category, Article.tech_category, Article.published_at)


class StockSnapshot(Base):
    __tablename__ = "stock_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    price: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class IngestLog(Base):
    __tablename__ = "ingest_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ok", index=True)  # ok / error
    error_message: Mapped[str | None] = mapped_column(Text)


class ReportLog(Base):
    __tablename__ = "report_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_type: Mapped[str] = mapped_column(String(40), index=True, default="weekly")
    recipient: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(40), index=True, default="sent")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class SourceSite(Base):
    __tablename__ = "source_sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    url: Mapped[str] = mapped_column(String(1500), index=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True)

    module_type: Mapped[str] = mapped_column(String(20), index=True)  # macro / tech
    module_key: Mapped[str] = mapped_column(String(60), index=True)   # industry / high_frequency / ...

    params_json: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by_user: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    verification_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    verification_method: Mapped[str | None] = mapped_column(String(120))
    verification_message: Mapped[str | None] = mapped_column(Text)
    trusted_domain: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reachable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    manual_approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


Index("idx_sources_module", SourceSite.module_type, SourceSite.module_key, SourceSite.active)
Index("uq_sources_module_url", SourceSite.module_type, SourceSite.module_key, SourceSite.url, unique=True)


class CompanyWhitelist(Base):
    __tablename__ = "company_whitelist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    domain: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by_user: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CategoryField(Base):
    __tablename__ = "category_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_type: Mapped[str] = mapped_column(String(20), index=True)  # macro / tech
    key: Mapped[str] = mapped_column(String(60), index=True)
    label: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[int | None] = mapped_column(Integer, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    built_in: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_by_user: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


Index("uq_category_group_key", CategoryField.group_type, CategoryField.key, unique=True)
