from __future__ import annotations

import html
import logging
import os
import smtplib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session

from .config import Settings
from .deepseek_client import DeepSeekClient
from .models import Article, ReportLog
from .pipeline import articles_in_window, weekly_articles

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Public builders
# ─────────────────────────────────────────────────────────────────────────────

def build_daily_report(
    session: Session, settings: Settings, deepseek: DeepSeekClient
) -> Tuple[str, str, str, Dict]:
    """Yesterday's articles → daily briefing."""
    articles = articles_in_window(session, days_ago_start=1, days_ago_end=0)
    macro_counter = Counter(a.macro_category for a in articles)
    tech_counter  = Counter(a.tech_category  for a in articles)
    grouped       = _group_by_macro(articles)

    payload = {
        "report_type": "daily",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total": len(articles),
        "macro_count": dict(macro_counter),
        "tech_count": dict(tech_counter),
        "highlights": [
            {"title": a.title, "source": a.source, "url": a.url,
             "macro": a.macro_category, "tech": a.tech_category}
            for a in articles[:20]
        ],
    }

    ai_brief = _safe_ai(deepseek.summarize_daily, payload)
    date_label = datetime.now().strftime("%Y-%m-%d")
    subject    = f"[GaN 晨报] {date_label} 昨日动态"
    html_body  = _render_html(
        title=subject, period_label="昨日（过去24小时）",
        ai_brief=ai_brief, macro_counter=macro_counter,
        tech_counter=tech_counter, grouped=grouped,
        accent="#2563eb",
    )
    text_body = _render_text("日报", ai_brief, macro_counter, tech_counter, grouped)
    stats = {"total_articles": len(articles), "report_type": "daily"}
    return subject, text_body, html_body, stats


def build_weekly_report(
    session: Session, settings: Settings, deepseek: DeepSeekClient
) -> Tuple[str, str, str, Dict]:
    """Past 7 days → weekly summary."""
    articles = weekly_articles(session, days=7)
    macro_counter = Counter(a.macro_category for a in articles)
    tech_counter  = Counter(a.tech_category  for a in articles)
    grouped       = _group_by_macro(articles)

    now = datetime.now()
    year, week, _ = now.isocalendar()
    payload = {
        "report_type": "weekly",
        "week": f"{year}-W{week:02d}",
        "total": len(articles),
        "macro_count": dict(macro_counter),
        "tech_count": dict(tech_counter),
        "highlights": [
            {"title": a.title, "source": a.source, "url": a.url,
             "macro": a.macro_category, "tech": a.tech_category}
            for a in articles[:30]
        ],
    }

    ai_brief   = _safe_ai(deepseek.summarize_weekly, payload)
    subject    = f"[GaN 周报] {year}-W{week:02d} 本周汇总"
    html_body  = _render_html(
        title=subject, period_label=f"本周（{year}-W{week:02d}，过去7天）",
        ai_brief=ai_brief, macro_counter=macro_counter,
        tech_counter=tech_counter, grouped=grouped,
        accent="#7c3aed",
    )
    text_body = _render_text("周报", ai_brief, macro_counter, tech_counter, grouped)
    stats = {"total_articles": len(articles), "report_type": "weekly"}
    return subject, text_body, html_body, stats


def build_monthly_report(
    session: Session, settings: Settings, deepseek: DeepSeekClient
) -> Tuple[str, str, str, Dict]:
    """Past 30 days vs previous 30 days → monthly review."""
    this_month = articles_in_window(session, days_ago_start=30, days_ago_end=0)
    prev_month = articles_in_window(session, days_ago_start=60, days_ago_end=30)

    this_macro = Counter(a.macro_category for a in this_month)
    this_tech  = Counter(a.tech_category  for a in this_month)
    prev_macro = Counter(a.macro_category for a in prev_month)
    prev_tech  = Counter(a.tech_category  for a in prev_month)
    grouped    = _group_by_macro(this_month)

    # build trend dict  { category: (this_count, prev_count, delta) }
    all_macro_keys = set(this_macro) | set(prev_macro)
    macro_trend = {
        k: (this_macro.get(k, 0), prev_macro.get(k, 0),
            this_macro.get(k, 0) - prev_macro.get(k, 0))
        for k in all_macro_keys
    }

    now = datetime.now()
    payload = {
        "report_type": "monthly",
        "month": now.strftime("%Y年%m月"),
        "this_month_total": len(this_month),
        "prev_month_total": len(prev_month),
        "this_macro_count": dict(this_macro),
        "prev_macro_count": dict(prev_macro),
        "this_tech_count":  dict(this_tech),
        "prev_tech_count":  dict(prev_tech),
        "highlights": [
            {"title": a.title, "source": a.source, "url": a.url,
             "macro": a.macro_category, "tech": a.tech_category}
            for a in this_month[:40]
        ],
    }

    ai_brief  = _safe_ai(deepseek.summarize_monthly, payload)
    month_str = now.strftime("%Y年%m月")
    subject   = f"[GaN 月报] {month_str} 月度回顾"
    html_body = _render_monthly_html(
        title=subject, month_str=month_str,
        ai_brief=ai_brief,
        this_macro=this_macro, prev_macro=prev_macro,
        this_tech=this_tech,  prev_tech=prev_tech,
        macro_trend=macro_trend,
        this_total=len(this_month), prev_total=len(prev_month),
        grouped=grouped,
    )
    text_body = _render_text("月报", ai_brief, this_macro, this_tech, grouped)
    stats = {
        "total_articles": len(this_month),
        "prev_total": len(prev_month),
        "report_type": "monthly",
    }
    return subject, text_body, html_body, stats


# ─────────────────────────────────────────────────────────────────────────────
# Unified email sender
# ─────────────────────────────────────────────────────────────────────────────

def send_report_email(
    session: Session, settings: Settings,
    report_type: str, subject: str, text_body: str, html_body: str
) -> bool:
    if not settings.email_enabled:
        raise RuntimeError("Email config missing: please set GMAIL_USER/GMAIL_APP_PASSWORD/GMAIL_TO in .env")

    sender     = settings.gmail_user
    recipient  = settings.gmail_to
    recipients = [r.strip() for r in recipient.split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{settings.gmail_from_display_name} <{sender}>"
    msg["To"]      = recipient
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html",  "utf-8"))

    try:
        resend_key = os.getenv("RESEND_API_KEY", "").strip()
        if resend_key:
            ok = _send_via_resend(resend_key, recipients, subject, html_body,
                                  settings.gmail_from_display_name)
        else:
            ok = _smtp_send(sender, settings.gmail_app_password, recipients, msg)

        status = "sent" if ok else "failed"
        session.add(ReportLog(report_type=report_type, recipient=recipient,
                              subject=subject, status=status))
        session.commit()
        if not ok:
            raise RuntimeError("Email sending failed — check logs")
        return True
    except Exception as exc:
        session.add(ReportLog(
            report_type=report_type, recipient=recipient or "",
            subject=subject, status="failed", error_message=str(exc),
        ))
        session.commit()
        raise


# keep old name as alias for backward compat
def send_weekly_email(session, settings, subject, text_body, html_body):
    return send_report_email(session, settings, "weekly", subject, text_body, html_body)


# ─────────────────────────────────────────────────────────────────────────────
# HTML / text renderers
# ─────────────────────────────────────────────────────────────────────────────

_BASE_STYLE = """
<style>
  body{font-family:'Helvetica Neue',Arial,sans-serif;background:#f8fafc;margin:0;padding:0;color:#1e293b;}
  .wrap{max-width:680px;margin:32px auto;background:#fff;border-radius:12px;
        box-shadow:0 2px 12px rgba(0,0,0,.08);overflow:hidden;}
  .header{padding:28px 32px;color:#fff;}
  .header h1{margin:0 0 6px;font-size:1.35em;font-weight:700;}
  .header p{margin:0;font-size:.88em;opacity:.85;}
  .body{padding:28px 32px;}
  .ai-block{background:#f0f9ff;border-left:4px solid #0ea5e9;border-radius:6px;
            padding:16px 18px;margin-bottom:24px;font-size:.95em;line-height:1.7;white-space:pre-wrap;}
  .section-title{font-size:1em;font-weight:700;color:#475569;
                 text-transform:uppercase;letter-spacing:.06em;margin:24px 0 10px;}
  .stat-row{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;}
  .stat-chip{background:#f1f5f9;border-radius:20px;padding:4px 12px;
             font-size:.82em;color:#334155;}
  .stat-chip b{color:#0f172a;}
  table.trend{width:100%;border-collapse:collapse;font-size:.87em;margin-bottom:16px;}
  table.trend th{background:#f1f5f9;padding:7px 10px;text-align:left;font-weight:600;color:#475569;}
  table.trend td{padding:7px 10px;border-bottom:1px solid #f1f5f9;}
  .up{color:#16a34a;font-weight:600;}
  .dn{color:#dc2626;font-weight:600;}
  .neu{color:#94a3b8;}
  ul.articles{list-style:none;padding:0;margin:0;}
  ul.articles li{padding:9px 0;border-bottom:1px solid #f1f5f9;font-size:.9em;}
  ul.articles li a{color:#2563eb;text-decoration:none;font-weight:500;}
  ul.articles li a:hover{text-decoration:underline;}
  ul.articles li small{color:#64748b;margin-left:6px;}
  .footer{background:#f8fafc;padding:16px 32px;font-size:.78em;color:#94a3b8;text-align:center;}
  h2{font-size:1.05em;color:#1e293b;margin:20px 0 8px;}
</style>
"""


def _render_html(
    *, title: str, period_label: str, ai_brief: str | None,
    macro_counter: Counter, tech_counter: Counter,
    grouped: Dict[str, List[Article]], accent: str = "#2563eb",
) -> str:
    brief_block = (
        f'<div class="ai-block">🤖 <b>DeepSeek 摘要</b><br><br>{html.escape(ai_brief)}</div>'
        if ai_brief else
        '<div class="ai-block" style="color:#94a3b8;">DeepSeek 摘要未启用。</div>'
    )
    macro_chips = "".join(
        f'<span class="stat-chip"><b>{html.escape(k)}</b>: {v}</span>'
        for k, v in macro_counter.most_common()
    )
    tech_chips = "".join(
        f'<span class="stat-chip"><b>{html.escape(k)}</b>: {v}</span>'
        for k, v in tech_counter.most_common()
    )
    sections = _render_article_sections(grouped)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">{_BASE_STYLE}</head>
<body><div class="wrap">
  <div class="header" style="background:{accent};">
    <h1>📡 {html.escape(title)}</h1>
    <p>统计周期：{html.escape(period_label)}</p>
  </div>
  <div class="body">
    {brief_block}
    <div class="section-title">一级分类分布</div>
    <div class="stat-row">{macro_chips}</div>
    <div class="section-title">技术分类分布</div>
    <div class="stat-row">{tech_chips}</div>
    {sections}
  </div>
  <div class="footer">GaN Intelligence Bot · 自动生成，请勿回复</div>
</div></body></html>"""


def _render_monthly_html(
    *, title: str, month_str: str, ai_brief: str | None,
    this_macro: Counter, prev_macro: Counter,
    this_tech: Counter,  prev_tech: Counter,
    macro_trend: Dict, this_total: int, prev_total: int,
    grouped: Dict[str, List[Article]],
) -> str:
    brief_block = (
        f'<div class="ai-block">🤖 <b>DeepSeek 月度分析</b><br><br>{html.escape(ai_brief)}</div>'
        if ai_brief else
        '<div class="ai-block" style="color:#94a3b8;">DeepSeek 摘要未启用。</div>'
    )
    delta_total = this_total - prev_total
    delta_cls   = "up" if delta_total >= 0 else "dn"
    delta_str   = f"+{delta_total}" if delta_total >= 0 else str(delta_total)

    trend_rows = ""
    for k, (cur, prv, delta) in sorted(macro_trend.items(), key=lambda x: -x[1][0]):
        d_str = f"+{delta}" if delta > 0 else str(delta)
        d_cls = "up" if delta > 0 else ("dn" if delta < 0 else "neu")
        trend_rows += (
            f"<tr><td>{html.escape(k)}</td><td>{cur}</td>"
            f"<td>{prv}</td><td class='{d_cls}'>{d_str}</td></tr>"
        )

    this_tech_chips = "".join(
        f'<span class="stat-chip"><b>{html.escape(k)}</b>: {v}</span>'
        for k, v in this_tech.most_common()
    )
    sections = _render_article_sections(grouped, max_per_macro=5)

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">{_BASE_STYLE}</head>
<body><div class="wrap">
  <div class="header" style="background:#0f766e;">
    <h1>📊 {html.escape(title)}</h1>
    <p>统计周期：{html.escape(month_str)}（过去30天 vs 上月30天）</p>
  </div>
  <div class="body">
    {brief_block}
    <div class="section-title">本月总览</div>
    <div class="stat-row">
      <span class="stat-chip">本月文章 <b>{this_total}</b></span>
      <span class="stat-chip">上月文章 <b>{prev_total}</b></span>
      <span class="stat-chip {delta_cls}">月比变化 <b>{delta_str}</b></span>
    </div>
    <div class="section-title">一级分类月比趋势</div>
    <table class="trend">
      <tr><th>分类</th><th>本月</th><th>上月</th><th>变化</th></tr>
      {trend_rows}
    </table>
    <div class="section-title">本月技术分类分布</div>
    <div class="stat-row">{this_tech_chips}</div>
    {sections}
  </div>
  <div class="footer">GaN Intelligence Bot · 月度报告 · 自动生成，请勿回复</div>
</div></body></html>"""


def _render_article_sections(grouped: Dict[str, List[Article]], max_per_macro: int = 8) -> str:
    out = []
    for macro, rows in grouped.items():
        items_html = "".join(
            f"<li><a href='{html.escape(r.url)}' target='_blank'>{html.escape(r.title)}</a>"
            f"<small>({html.escape(r.source)})</small></li>"
            for r in rows[:max_per_macro]
        )
        out.append(f"<h2>📁 {html.escape(macro)}</h2><ul class='articles'>{items_html}</ul>")
    return "\n".join(out)


def _render_text(
    label: str, ai_brief: str | None,
    macro_counter: Counter, tech_counter: Counter,
    grouped: Dict[str, List[Article]],
) -> str:
    lines = [f"GaN 行业{label}", ""]
    if ai_brief:
        lines += ["【DeepSeek 摘要】", ai_brief, ""]
    lines.append("【一级分类统计】")
    for k, v in macro_counter.most_common():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("【技术分类统计】")
    for k, v in tech_counter.most_common():
        lines.append(f"  {k}: {v}")
    lines.append("")
    for macro, rows in grouped.items():
        lines.append(f"【{macro}】")
        for r in rows[:5]:
            lines.append(f"  - {r.title} | {r.source}")
            lines.append(f"    {r.url}")
        lines.append("")
    return "\n".join(lines).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _group_by_macro(articles: List[Article]) -> Dict[str, List[Article]]:
    grouped: Dict[str, List[Article]] = defaultdict(list)
    for a in articles:
        grouped[a.macro_category].append(a)
    return {macro: rows[:10] for macro, rows in grouped.items()}


def _safe_ai(fn, payload) -> str | None:
    try:
        return fn(payload)
    except Exception as exc:
        logger.warning("DeepSeek call failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Transport helpers
# ─────────────────────────────────────────────────────────────────────────────

def _send_via_resend(api_key: str, recipients: list[str], subject: str,
                     html_body: str, from_name: str = "GaN情报助手") -> bool:
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
            json=payload, timeout=30,
        )
        if resp.status_code in (200, 201):
            logger.info("Resend: sent → %s", recipients)
            return True
        logger.error("Resend %d: %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("Resend failed: %s", e)
        return False


def _smtp_send(sender: str, password: str, recipients: list[str], msg) -> bool:
    raw = msg.as_bytes()
    for port, use_ssl in [(465, True), (587, False)]:
        try:
            if use_ssl:
                with smtplib.SMTP_SSL("smtp.gmail.com", port, timeout=30) as s:
                    s.login(sender, password); s.sendmail(sender, recipients, raw)
            else:
                with smtplib.SMTP("smtp.gmail.com", port, timeout=30) as s:
                    s.ehlo(); s.starttls(); s.ehlo()
                    s.login(sender, password); s.sendmail(sender, recipients, raw)
            logger.info("SMTP port %d → %s", port, recipients)
            return True
        except smtplib.SMTPAuthenticationError as e:
            logger.error("Gmail auth failed (port %d): %s", port, e)
            return False
        except Exception as e:
            logger.warning("SMTP port %d failed: %s", port, e)
    logger.error("All SMTP ports blocked — set RESEND_API_KEY")
    return False
