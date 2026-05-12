from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import Settings
from .db import SessionLocal
from .deepseek_client import DeepSeekClient
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

        # ── 每 N 小时自动抓取 ──────────────────────────────────────────────
        s.add_job(self._ingest_job, trigger="interval",
                  hours=self.settings.ingest_interval_hours,
                  id="ingest_job", max_instances=1, coalesce=True)

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
            "Scheduler started. ingest=%dh | daily=%02d:%02d | "
            "weekly=Sun %02d:%02d | monthly=last-day %02d:%02d (%s)",
            self.settings.ingest_interval_hours,
            self.settings.daily_report_hour, self.settings.daily_report_minute,
            self.settings.daily_report_hour, self.settings.daily_report_minute + 5,
            self.settings.daily_report_hour, self.settings.daily_report_minute + 10,
            self.settings.timezone,
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped.")

    # ── jobs ──────────────────────────────────────────────────────────────────

    def _ingest_job(self) -> None:
        with SessionLocal() as session:
            result = run_ingestion(session, self.settings, self.deepseek)
            logger.info("Ingestion: %s", result.as_dict())

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
