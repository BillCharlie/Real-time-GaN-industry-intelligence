from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import Settings
from .db import SessionLocal
from .deepseek_client import DeepSeekClient
from .models import IngestLog
from .pipeline import refresh_stock_snapshots, run_ingestion
from .reporter import (
    build_daily_report,
    build_monthly_report,
    build_weekly_report,
    send_report_email,
)

logger = logging.getLogger(__name__)


class RuntimeScheduler:
    def __init__(self, settings: Settings, deepseek: DeepSeekClient):
        self.settings = settings
        self.deepseek = deepseek
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def start(self) -> None:
        s = self.scheduler
        interval_minutes = self.settings.effective_ingest_interval_minutes
        now = datetime.now(ZoneInfo(self.settings.timezone))

        # ── 每 N 小时自动抓取 ──────────────────────────────────────────────
        s.add_job(self._ingest_job, trigger="interval",
                  minutes=interval_minutes,
                  next_run_time=now,
                  id="ingest_job", max_instances=1, coalesce=True,
                  misfire_grace_time=30 * 60, replace_existing=True)

        # ── 每 30 分钟刷新股价 ────────────────────────────────────────────
        s.add_job(self._stock_job, trigger="interval",
                  minutes=self.settings.stock_interval_minutes,
                  id="stock_job", max_instances=1, coalesce=True)

        # ── 每天 08:00 发日报 ─────────────────────────────────────────────
        s.add_job(self._daily_report_job, trigger="cron",
                  hour=self.settings.daily_report_hour,
                  minute=self.settings.daily_report_minute,
                  id="daily_report_job", max_instances=1, coalesce=True)

        # ── 每周日 08:05 发周报 ───────────────────────────────────────────
        s.add_job(self._weekly_report_job, trigger="cron",
                  day_of_week="sun",
                  hour=self.settings.daily_report_hour,
                  minute=self.settings.daily_report_minute + 5,
                  id="weekly_report_job", max_instances=1, coalesce=True)

        # ── 每月最后一天 08:10 发月报 ─────────────────────────────────────
        s.add_job(self._monthly_report_job, trigger="cron",
                  day="last",
                  hour=self.settings.daily_report_hour,
                  minute=self.settings.daily_report_minute + 10,
                  id="monthly_report_job", max_instances=1, coalesce=True)

        s.start()
        logger.info(
            "Scheduler started. ingest=%dm (runs immediately, then interval) | daily=%02d:%02d | "
            "weekly=Sun %02d:%02d | monthly=last-day %02d:%02d (%s)",
            interval_minutes,
            self.settings.daily_report_hour, self.settings.daily_report_minute,
            self.settings.daily_report_hour, self.settings.daily_report_minute + 5,
            self.settings.daily_report_hour, self.settings.daily_report_minute + 10,
            self.settings.timezone,
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped.")

    def status(self) -> dict:
        ingest_job = self.scheduler.get_job("ingest_job")
        next_run = ingest_job.next_run_time if ingest_job else None
        interval_minutes = self.settings.effective_ingest_interval_minutes
        return {
            "running": self.scheduler.running,
            "ingest_interval_minutes": interval_minutes,
            "ingest_interval_hours": round(interval_minutes / 60, 2),
            "next_ingest_run": next_run.isoformat() if next_run else None,
        }

    # ── jobs ──────────────────────────────────────────────────────────────────

    def _ingest_job(self) -> None:
        run_started_at = datetime.now(timezone.utc)
        try:
            with SessionLocal() as session:
                result = run_ingestion(session, self.settings, self.deepseek)
                logger.info("Ingestion: %s", result.as_dict())
        except Exception as exc:
            logger.exception("Scheduled ingestion job failed.")
            try:
                with SessionLocal() as session:
                    session.add(
                        IngestLog(
                            started_at=run_started_at,
                            finished_at=datetime.now(ZoneInfo(self.settings.timezone)),
                            fetched=0,
                            inserted=0,
                            skipped=0,
                            errors=1,
                            status="error",
                            error_message=str(exc)[:2000],
                        )
                    )
                    session.commit()
            except Exception:
                logger.exception("Failed to record scheduled ingestion failure.")

    def _stock_job(self) -> None:
        with SessionLocal() as session:
            result = refresh_stock_snapshots(session, self.settings)
            logger.info("Stock refresh: %s", result)

    def _daily_report_job(self) -> None:
        if not self.settings.email_enabled:
            logger.warning("Daily report skipped: email not configured.")
            return
        with SessionLocal() as session:
            subject, text_body, html_body, stats = build_daily_report(
                session, self.settings, self.deepseek)
            send_report_email(session, self.settings, "daily", subject, text_body, html_body)
            logger.info("Daily report sent: %s", stats)

    def _weekly_report_job(self) -> None:
        if not self.settings.email_enabled:
            logger.warning("Weekly report skipped: email not configured.")
            return
        with SessionLocal() as session:
            subject, text_body, html_body, stats = build_weekly_report(
                session, self.settings, self.deepseek)
            send_report_email(session, self.settings, "weekly", subject, text_body, html_body)
            logger.info("Weekly report sent: %s", stats)

    def _monthly_report_job(self) -> None:
        if not self.settings.email_enabled:
            logger.warning("Monthly report skipped: email not configured.")
            return
        with SessionLocal() as session:
            subject, text_body, html_body, stats = build_monthly_report(
                session, self.settings, self.deepseek)
            send_report_email(session, self.settings, "monthly", subject, text_body, html_body)
            logger.info("Monthly report sent: %s", stats)
