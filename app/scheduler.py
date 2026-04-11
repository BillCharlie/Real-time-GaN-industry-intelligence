from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import Settings
from .db import SessionLocal
from .deepseek_client import DeepSeekClient
from .pipeline import refresh_stock_snapshots, run_ingestion
from .reporter import build_weekly_report, send_weekly_email

logger = logging.getLogger(__name__)


class RuntimeScheduler:
    def __init__(self, settings: Settings, deepseek: DeepSeekClient):
        self.settings = settings
        self.deepseek = deepseek
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def start(self) -> None:
        self.scheduler.add_job(
            self._ingest_job,
            trigger="cron",
            hour=self.settings.ingest_cron_hour,
            minute=self.settings.ingest_cron_minute,
            id="ingest_job",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._stock_job,
            trigger="interval",
            minutes=self.settings.stock_interval_minutes,
            id="stock_job",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._weekly_report_job,
            trigger="cron",
            day_of_week=self.settings.weekly_cron_day_of_week,
            hour=self.settings.weekly_cron_hour,
            minute=self.settings.weekly_cron_minute,
            id="weekly_report_job",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        logger.info(
            "Scheduler started. Ingest cron=%02d:%02d (%s).",
            self.settings.ingest_cron_hour,
            self.settings.ingest_cron_minute,
            self.settings.timezone,
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped.")

    def _ingest_job(self) -> None:
        with SessionLocal() as session:
            result = run_ingestion(session, self.settings, self.deepseek)
            logger.info("Ingestion completed: %s", result.as_dict())

    def _stock_job(self) -> None:
        with SessionLocal() as session:
            result = refresh_stock_snapshots(session, self.settings)
            logger.info("Stock refresh completed: %s", result)

    def _weekly_report_job(self) -> None:
        if not self.settings.email_enabled:
            logger.warning("Weekly report skipped: email not configured.")
            return
        with SessionLocal() as session:
            subject, text_body, html_body, stats = build_weekly_report(session, self.settings, self.deepseek)
            send_weekly_email(session, self.settings, subject, text_body, html_body)
            logger.info("Weekly report sent: %s", stats)
