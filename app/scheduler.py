from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import Settings
from .db import SessionLocal
from .deepseek_client import DeepSeekClient
from .models import IngestLog
from .pipeline import run_ingestion
from .reporter import (
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
        tz = ZoneInfo(self.settings.timezone)
        now = datetime.now(tz)
        h = self.settings.daily_report_hour
        m = self.settings.daily_report_minute

        # Next 08:00 occurrence (today if not yet passed, else tomorrow)
        first_run = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if first_run <= now:
            first_run += timedelta(days=1)

        # ── 启动时立即抓取一次（仅数据，不发报）────────────────────────────
        s.add_job(self._ingest_only_job, trigger="date",
                  run_date=now,
                  id="startup_ingest_job", max_instances=1, coalesce=True,
                  replace_existing=True)

        # ── 每天 08:00：只抓取，不发报 ──────────────────────────────────
        s.add_job(self._ingest_job, trigger="interval",
                  days=1,
                  start_date=first_run,
                  id="ingest_job", max_instances=1, coalesce=True,
                  misfire_grace_time=60 * 60, replace_existing=True)

        # ── 每周日 08:05 发周报 ───────────────────────────────────────────
        s.add_job(self._weekly_report_job, trigger="cron",
                  day_of_week="sun",
                  hour=h,
                  minute=m + 5,
                  id="weekly_report_job", max_instances=1, coalesce=True)

        # ── 每月最后一天 08:10 发月报 ─────────────────────────────────────
        s.add_job(self._monthly_report_job, trigger="cron",
                  day="last",
                  hour=h,
                  minute=m + 10,
                  id="monthly_report_job", max_instances=1, coalesce=True)

        s.start()
        logger.info(
            "Scheduler started. startup_ingest=now | ingest=%s (daily at %02d:%02d) | "
            "weekly report=Sun %02d:%02d | monthly report=last-day %02d:%02d (%s)",
            first_run.strftime("%Y-%m-%d %H:%M"),
            h, m, h, m + 5, h, m + 10,
            self.settings.timezone,
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped.")

    def status(self) -> dict:
        ingest_job = self.scheduler.get_job("ingest_job")
        next_run = ingest_job.next_run_time if ingest_job else None
        return {
            "running": self.scheduler.running,
            "next_ingest_run": next_run.isoformat() if next_run else None,
        }

    # ── jobs ──────────────────────────────────────────────────────────────────

    def _ingest_only_job(self) -> None:
        """Runs once on startup: fetch latest articles without sending any email."""
        run_started_at = datetime.now(timezone.utc)
        try:
            with SessionLocal() as session:
                result = run_ingestion(session, self.settings, self.deepseek)
                logger.info("Startup ingestion: %s", result.as_dict())
        except Exception as exc:
            logger.exception("Startup ingestion failed.")
            try:
                with SessionLocal() as session:
                    session.add(
                        IngestLog(
                            started_at=run_started_at,
                            finished_at=datetime.now(ZoneInfo(self.settings.timezone)),
                            fetched=0, inserted=0, skipped=0, errors=1,
                            status="error", error_message=str(exc)[:2000],
                        )
                    )
                    session.commit()
            except Exception:
                logger.exception("Failed to record startup ingestion failure.")

    def _ingest_job(self) -> None:
        """Daily at 08:00: ingest fresh articles. Reports go out weekly/monthly."""
        run_started_at = datetime.now(timezone.utc)
        try:
            with SessionLocal() as session:
                result = run_ingestion(session, self.settings, self.deepseek)
                logger.info("Scheduled ingestion: %s", result.as_dict())
        except Exception as exc:
            logger.exception("Scheduled ingestion failed.")
            try:
                with SessionLocal() as session:
                    session.add(
                        IngestLog(
                            started_at=run_started_at,
                            finished_at=datetime.now(ZoneInfo(self.settings.timezone)),
                            fetched=0, inserted=0, skipped=0, errors=1,
                            status="error", error_message=str(exc)[:2000],
                        )
                    )
                    session.commit()
            except Exception:
                logger.exception("Failed to record scheduled ingestion failure.")

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
