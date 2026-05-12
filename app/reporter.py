from __future__ import annotations

import html
import logging
import os
import smtplib
from collections import Counter, defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session

from .config import Settings
from .deepseek_client import DeepSeekClient
from .models import Article, ReportLog
from .pipeline import weekly_articles

logger = logging.getLogger(__name__)


def build_weekly_report(
    session: Session, settings: Settings, deepseek: DeepSeekClient
) -> Tuple[str, str, str, Dict[str, int]]:
    articles = weekly_articles(session, days=7)
    macro_counter = Counter(article.macro_category for article in articles)
    tech_counter = Counter(article.tech_category for article in articles)

    grouped: Dict[str, List[Article]] = defaultdict(list)
    for article in articles:
        grouped[article.macro_category].append(article)

    top_by_macro = {macro: rows[:8] for macro, rows in grouped.items()}
    payload = {
        "weekly_total": len(articles),
        "macro_count": dict(macro_counter),
        "tech_count": dict(tech_counter),
        "highlights": [
            {"title": row.title, "source": row.source, "macro": row.macro_category, "tech": row.tech_category}
            for row in articles[:30]
        ],
    }

    ai_brief = None
    if deepseek.enabled:
        try:
            ai_brief = deepseek.summarize_weekly(payload)
        except Exception:
            ai_brief = None

    date_label = datetime.now().strftime("%Y-%m-%d")
    subject = f"[GaN Morning Intelligence] {date_label} 动态晨报"
    text_body = _build_text_body(macro_counter, tech_counter, top_by_macro, ai_brief)
    html_body = _build_html_body(macro_counter, tech_counter, top_by_macro, ai_brief)
    stats = {"total_articles": len(articles), "macro_categories": len(macro_counter), "tech_categories": len(tech_counter)}
    return subject, text_body, html_body, stats


def send_weekly_email(session: Session, settings: Settings, subject: str, text_body: str, html_body: str) -> bool:
    if not settings.email_enabled:
        raise RuntimeError("Email config missing: please set GMAIL_USER/GMAIL_APP_PASSWORD/GMAIL_TO in .env")

    sender = settings.gmail_user
    recipient = settings.gmail_to
    recipients = [r.strip() for r in recipient.split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.gmail_from_display_name} <{sender}>"
    msg["To"] = recipient
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        # ── prefer Resend HTTP API (works on Railway / cloud) ──────────────
        resend_key = os.getenv("RESEND_API_KEY", "").strip()
        if resend_key:
            ok = _send_via_resend(resend_key, recipients, subject, html_body,
                                  settings.gmail_from_display_name)
        else:
            # ── fallback: Gmail SMTP (local dev only) ──────────────────────
            ok = _smtp_send(sender, settings.gmail_app_password, recipients, msg)

        status = "sent" if ok else "failed"
        session.add(ReportLog(report_type="weekly", recipient=recipient, subject=subject, status=status))
        session.commit()
        if not ok:
            raise RuntimeError("Email sending failed — check logs for details")
        return True
    except Exception as exc:
        session.add(
            ReportLog(
                report_type="weekly",
                recipient=recipient or "",
                subject=subject,
                status="failed",
                error_message=str(exc),
            )
        )
        session.commit()
        raise


def _send_via_resend(api_key: str, recipients: list[str], subject: str,
                     html_body: str, from_name: str = "GaN情报助手") -> bool:
    """Send via Resend HTTP API (port 443 — works on Railway/cloud).
    Free tier: 3000 emails/month, 100/day.
    Sends FROM onboarding@resend.dev unless a custom verified domain is configured.
    """
    import requests as _req

    payload: dict = {
        "from": f"{from_name} <onboarding@resend.dev>",
        "to": recipients,
        "subject": subject,
        "html": html_body,
    }

    try:
        resp = _req.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            logger.info("Resend API: email sent → %s", recipients)
            return True
        logger.error("Resend API %d: %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("Resend API request failed: %s", e)
        return False


def _smtp_send(sender: str, password: str, recipients: list[str], msg) -> bool:
    """Try Gmail SMTP — port 465 (SSL) first, then 587 (STARTTLS).
    NOTE: Most cloud hosts (including Railway) block SMTP ports.
    This is kept as a fallback for local development only.
    """
    raw = msg.as_bytes()

    for port, use_ssl in [(465, True), (587, False)]:
        try:
            if use_ssl:
                with smtplib.SMTP_SSL("smtp.gmail.com", port, timeout=30) as s:
                    s.login(sender, password)
                    s.sendmail(sender, recipients, raw)
            else:
                with smtplib.SMTP("smtp.gmail.com", port, timeout=30) as s:
                    s.ehlo(); s.starttls(); s.ehlo()
                    s.login(sender, password)
                    s.sendmail(sender, recipients, raw)
            logger.info("Email sent via SMTP port %d to %s", port, recipients)
            return True
        except smtplib.SMTPAuthenticationError as e:
            logger.error("Gmail auth failed (port %d): %s", port, e)
            return False
        except Exception as e:
            logger.warning("SMTP port %d failed: %s", port, e)

    logger.error("All SMTP ports blocked — set RESEND_API_KEY on cloud hosts")
    return False


def _build_text_body(
    macro_counter: Counter, tech_counter: Counter, top_by_macro: Dict[str, List[Article]], ai_brief: str | None
) -> str:
    lines: List[str] = []
    lines.append("GaN 行业动态晨报")
    lines.append("")
    if ai_brief:
        lines.append("【DeepSeek 摘要】")
        lines.append(ai_brief)
        lines.append("")

    lines.append("【一级分类统计】")
    for key, value in macro_counter.most_common():
        lines.append(f"- {key}: {value}")
    lines.append("")

    lines.append("【技术分类统计】")
    for key, value in tech_counter.most_common():
        lines.append(f"- {key}: {value}")
    lines.append("")

    for macro, rows in top_by_macro.items():
        lines.append(f"【{macro} Top】")
        for row in rows[:5]:
            lines.append(f"- {row.title} | {row.source} | {row.url}")
        lines.append("")

    return "\n".join(lines).strip()


def _build_html_body(
    macro_counter: Counter, tech_counter: Counter, top_by_macro: Dict[str, List[Article]], ai_brief: str | None
) -> str:
    macro_items = "".join(f"<li><b>{html.escape(k)}</b>: {v}</li>" for k, v in macro_counter.most_common())
    tech_items = "".join(f"<li><b>{html.escape(k)}</b>: {v}</li>" for k, v in tech_counter.most_common())

    sections = []
    for macro, rows in top_by_macro.items():
        item_html = "".join(
            f"<li><a href='{html.escape(row.url)}'>{html.escape(row.title)}</a> "
            f"<small>({html.escape(row.source)})</small></li>"
            for row in rows[:8]
        )
        sections.append(f"<h3>{html.escape(macro)}</h3><ul>{item_html}</ul>")

    brief_block = f"<p>{html.escape(ai_brief)}</p>" if ai_brief else "<p>未启用 DeepSeek 摘要。</p>"
    return f"""
    <html>
      <body style="font-family:Arial,sans-serif;line-height:1.5;">
        <h2>GaN 行业动态晨报</h2>
        <h3>DeepSeek 摘要</h3>
        {brief_block}
        <h3>一级分类统计</h3>
        <ul>{macro_items}</ul>
        <h3>技术分类统计</h3>
        <ul>{tech_items}</ul>
        {''.join(sections)}
      </body>
    </html>
    """
