from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

import httpx
from sqlalchemy import asc, desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .category_registry import get_active_category_keys, get_category_labels
from .models import CompanyWhitelist, SourceSite
from .sources import SourceDefinition, get_default_sources

VALID_SOURCE_TYPES = {"rss", "arxiv", "crossref"}
VALID_MODULE_TYPES = {"macro", "tech"}

STATIC_TRUSTED_DOMAIN_SUFFIXES = {
    "news.google.com",
    "arxiv.org",
    "export.arxiv.org",
    "api.crossref.org",
    "doi.org",
    "ieee.org",
    "nature.com",
    "scitation.org",
    "infineon.com",
    "onsemi.com",
    "st.com",
    "ti.com",
    "navitassemi.com",
    "wolfspeed.com",
    "renesas.com",
    "vis.com.tw",
    "microchip.com",
    "analog.com",
    "mouser.com",
    "digikey.com",
    "finance.yahoo.com",
    "yahoo.com",
    "reuters.com",
    "bloomberg.com",
}

DEFAULT_COMPANY_WHITELIST = [
    {
        "name": "VIS",
        "domain": "vis.com.tw",
        "note": "Official site (press): https://www.vis.com.tw/tc/press_latest",
    },
    {"name": "Infineon", "domain": "infineon.com", "note": "Official site"},
    {"name": "onsemi", "domain": "onsemi.com", "note": "Official site"},
    {"name": "ST", "domain": "st.com", "note": "Official site"},
    {"name": "Navitas", "domain": "navitassemi.com", "note": "Official site"},
    {"name": "Wolfspeed", "domain": "wolfspeed.com", "note": "Official site"},
    {"name": "TI", "domain": "ti.com", "note": "Official site"},
    {"name": "Renesas", "domain": "renesas.com", "note": "Official site"},
]


def available_modules(session: Session) -> Dict[str, List[Dict[str, str]]]:
    labels = get_category_labels(session)
    macro = [
        {"key": key, "label": label}
        for key, label in (labels.get("macro") or {}).items()
    ]
    tech = [
        {"key": key, "label": label}
        for key, label in (labels.get("tech") or {}).items()
    ]
    return {"macro": macro, "tech": tech}


def ensure_seed_company_whitelist(session: Session) -> int:
    _migrate_legacy_vlsi_to_vis(session)
    inserted = 0
    existing_domains = set(session.scalars(select(CompanyWhitelist.domain)))
    for row in DEFAULT_COMPANY_WHITELIST:
        domain = _normalize_domain(row["domain"])
        if domain in existing_domains:
            continue
        session.add(
            CompanyWhitelist(
                name=row["name"][:120],
                domain=domain[:300],
                active=True,
                created_by_user=False,
                note=row.get("note"),
            )
        )
        inserted += 1
        existing_domains.add(domain)
    if inserted:
        session.commit()
    return inserted


def list_company_whitelist(session: Session, include_inactive: bool = True) -> List[CompanyWhitelist]:
    stmt = select(CompanyWhitelist)
    if not include_inactive:
        stmt = stmt.where(CompanyWhitelist.active.is_(True))
    stmt = stmt.order_by(asc(CompanyWhitelist.name))
    return list(session.scalars(stmt))


def create_company_whitelist(
    session: Session, *, name: str, domain: str, active: bool = True, note: str | None = None
) -> CompanyWhitelist:
    clean_domain = _normalize_domain(domain)
    if not clean_domain:
        raise ValueError("Invalid domain.")
    duplicate = session.scalar(
        select(CompanyWhitelist).where(
            or_(CompanyWhitelist.name == name[:120], CompanyWhitelist.domain == clean_domain[:300])
        )
    )
    if duplicate:
        return duplicate
    row = CompanyWhitelist(
        name=name[:120],
        domain=clean_domain[:300],
        active=bool(active),
        created_by_user=True,
        note=(note or "")[:1000] or None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_company_whitelist(
    session: Session,
    company_id: int,
    *,
    name: Optional[str] = None,
    domain: Optional[str] = None,
    active: Optional[bool] = None,
    note: Optional[str] = None,
) -> CompanyWhitelist:
    row = _get_company_or_raise(session, company_id)
    if name is not None:
        row.name = name[:120]
    if domain is not None:
        clean_domain = _normalize_domain(domain)
        if not clean_domain:
            raise ValueError("Invalid domain.")
        row.domain = clean_domain[:300]
    if active is not None:
        row.active = bool(active)
    if note is not None:
        row.note = note[:1000]
    session.commit()
    session.refresh(row)
    return row


def delete_company_whitelist(session: Session, company_id: int) -> Dict[str, Any]:
    row = _get_company_or_raise(session, company_id)
    if not row.created_by_user:
        raise ValueError("Built-in company cannot be deleted. Disable it instead.")

    deleted = company_to_dict(row)
    session.delete(row)
    session.commit()
    synced = sync_company_whitelist_sources(session)
    return {"item": deleted, "synced": synced}


def sync_company_whitelist_sources(session: Session) -> Dict[str, int]:
    created = 0
    activated = 0
    deactivated = 0

    companies = list_company_whitelist(session, include_inactive=True)
    company_by_name = {company.name: company for company in companies}

    # Retire orphan or stale generated sources first (company deleted / renamed / domain changed).
    generated_rows = list(
        session.scalars(
            select(SourceSite).where(
                SourceSite.created_by_user.is_(False),
                SourceSite.source_type == "rss",
                SourceSite.module_type == "macro",
                SourceSite.module_key == "industry",
                SourceSite.name.like("Whitelist Company - %"),
            )
        )
    )
    for row in generated_rows:
        company_name = _extract_company_name_from_generated_source(row.name)
        company = company_by_name.get(company_name)
        expected_url = build_company_google_news_rss(company.domain) if company else ""
        should_active = bool(company and company.active and row.url == expected_url)
        if row.active and not should_active:
            row.active = False
            deactivated += 1

    for company in companies:
        source_name = f"Whitelist Company - {company.name}"
        source_url = build_company_google_news_rss(company.domain)
        rows = list(
            session.scalars(
                select(SourceSite).where(
                    SourceSite.name == source_name,
                    SourceSite.source_type == "rss",
                    SourceSite.module_type == "macro",
                    SourceSite.module_key == "industry",
                )
            )
        )
        source = next((x for x in rows if x.url == source_url), None)

        # If company domain changes, retire old generated sources with same company name.
        for old_row in rows:
            if old_row.url != source_url and old_row.active:
                old_row.active = False
                deactivated += 1

        if company.active:
            if source is None:
                verification = verify_source_url(source_url, extra_trusted_domains=[company.domain])
                session.add(
                    SourceSite(
                        name=source_name[:160],
                        url=source_url[:1500],
                        source_type="rss",
                        module_type="macro",
                        module_key="industry",
                        params_json=json.dumps({"company": company.name, "domain": company.domain}, ensure_ascii=False),
                        active=True,
                        created_by_user=False,
                        verification_status=verification["verification_status"],
                        verification_method=verification["verification_method"],
                        verification_message=verification["verification_message"],
                        trusted_domain=verification["trusted_domain"],
                        reachable=verification["reachable"],
                        last_checked_at=verification["last_checked_at"],
                    )
                )
                created += 1
            elif not source.active:
                source.active = True
                activated += 1
        else:
            for old_row in rows:
                if old_row.active:
                    old_row.active = False
                    deactivated += 1

    if created or activated or deactivated:
        session.commit()
    return {"created": created, "activated": activated, "deactivated": deactivated}


def ensure_seed_sources(session: Session) -> int:
    inserted = 0
    updated = 0
    defaults = get_default_sources()
    existing_pairs = set(session.execute(select(SourceSite.module_type, SourceSite.module_key, SourceSite.url)).all())
    extra_domains = active_company_domains(session)

    for source in defaults:
        module_type, module_key = _infer_module_from_hints(source.macro_hint, source.tech_hint)
        existing_by_name = session.scalar(
            select(SourceSite)
            .where(
                SourceSite.created_by_user.is_(False),
                SourceSite.name == source.name,
                SourceSite.source_type == source.source_type[:40],
                SourceSite.module_type == module_type,
                SourceSite.module_key == module_key,
            )
            .order_by(desc(SourceSite.created_at))
            .limit(1)
        )
        expected_params = json.dumps(source.params, ensure_ascii=False) if source.params else None
        if existing_by_name:
            needs_update = (
                existing_by_name.url != source.url
                or (existing_by_name.params_json or None) != (expected_params or None)
            )
            if needs_update:
                verification = verify_source_url(source.url, extra_trusted_domains=extra_domains)
                existing_by_name.url = source.url[:1500]
                existing_by_name.params_json = expected_params
                existing_by_name.verification_status = verification["verification_status"]
                existing_by_name.verification_method = verification["verification_method"]
                existing_by_name.verification_message = verification["verification_message"]
                existing_by_name.trusted_domain = verification["trusted_domain"]
                existing_by_name.reachable = verification["reachable"]
                existing_by_name.last_checked_at = verification["last_checked_at"]
                updated += 1
            continue

        if (module_type, module_key, source.url) in existing_pairs:
            continue
        verification = verify_source_url(source.url, extra_trusted_domains=extra_domains)
        row = SourceSite(
            name=source.name[:160],
            url=source.url[:1500],
            source_type=source.source_type[:40],
            module_type=module_type,
            module_key=module_key,
            params_json=json.dumps(source.params, ensure_ascii=False) if source.params else None,
            active=True,
            created_by_user=False,
            verification_status=verification["verification_status"],
            verification_method=verification["verification_method"],
            verification_message=verification["verification_message"],
            trusted_domain=verification["trusted_domain"],
            reachable=verification["reachable"],
            last_checked_at=verification["last_checked_at"],
        )
        session.add(row)
        inserted += 1
        existing_pairs.add((module_type, module_key, source.url))
    if inserted or updated:
        session.commit()
    return inserted + updated


def list_sources(
    session: Session,
    *,
    module_type: Optional[str] = None,
    module_key: Optional[str] = None,
    include_inactive: bool = False,
) -> List[SourceSite]:
    stmt = select(SourceSite)
    if module_type:
        stmt = stmt.where(SourceSite.module_type == module_type)
    if module_key:
        stmt = stmt.where(SourceSite.module_key == module_key)
    if not include_inactive:
        stmt = stmt.where(SourceSite.active.is_(True))
    stmt = stmt.order_by(asc(SourceSite.module_type), asc(SourceSite.module_key), desc(SourceSite.created_at))
    return list(session.scalars(stmt))


def create_source(
    session: Session,
    *,
    name: str,
    url: str,
    source_type: str,
    module_type: str,
    module_key: str,
    params: Optional[Dict[str, Any]] = None,
) -> SourceSite:
    _validate_source_inputs(
        session, source_type=source_type, module_type=module_type, module_key=module_key, url=url
    )

    existing = session.scalar(
        select(SourceSite).where(
            SourceSite.url == url[:1500],
            SourceSite.module_type == module_type,
            SourceSite.module_key == module_key,
        )
    )
    if existing:
        return existing

    verification = verify_source_url(url, extra_trusted_domains=active_company_domains(session))
    row = SourceSite(
        name=name[:160],
        url=url[:1500],
        source_type=source_type[:40],
        module_type=module_type,
        module_key=module_key,
        params_json=json.dumps(params or {}, ensure_ascii=False) if params else None,
        active=True,
        created_by_user=True,
        verification_status=verification["verification_status"],
        verification_method=verification["verification_method"],
        verification_message=verification["verification_message"],
        trusted_domain=verification["trusted_domain"],
        reachable=verification["reachable"],
        last_checked_at=verification["last_checked_at"],
    )
    session.add(row)
    try:
        session.commit()
        session.refresh(row)
        return row
    except IntegrityError:
        session.rollback()
        by_url = session.scalar(select(SourceSite).where(SourceSite.url == url[:1500]))
        if by_url:
            return by_url
        raise ValueError("Source insert conflict.")


def update_source(
    session: Session,
    source_id: int,
    *,
    name: Optional[str] = None,
    url: Optional[str] = None,
    source_type: Optional[str] = None,
    module_type: Optional[str] = None,
    module_key: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    active: Optional[bool] = None,
    reverify: bool = False,
) -> SourceSite:
    row = _get_source_or_raise(session, source_id)
    next_source_type = source_type or row.source_type
    next_module_type = module_type or row.module_type
    next_module_key = module_key or row.module_key
    next_url = (url or row.url).strip()
    _validate_source_inputs(
        session,
        source_type=next_source_type,
        module_type=next_module_type,
        module_key=next_module_key,
        url=next_url,
    )

    duplicate = session.scalar(
        select(SourceSite).where(
            SourceSite.id != row.id,
            SourceSite.url == next_url[:1500],
            SourceSite.module_type == next_module_type,
            SourceSite.module_key == next_module_key,
        )
    )
    if duplicate:
        raise ValueError("Duplicate source in same module.")

    if name is not None:
        row.name = name[:160]
    if url is not None:
        row.url = next_url[:1500]
    if source_type is not None:
        row.source_type = source_type[:40]
    if module_type is not None:
        row.module_type = module_type
    if module_key is not None:
        row.module_key = module_key
    if params is not None:
        row.params_json = json.dumps(params, ensure_ascii=False)
    if active is not None:
        row.active = bool(active)

    if reverify or url is not None:
        verification = verify_source_url(row.url, extra_trusted_domains=active_company_domains(session))
        row.verification_status = verification["verification_status"]
        row.verification_method = verification["verification_method"]
        row.verification_message = verification["verification_message"]
        row.trusted_domain = verification["trusted_domain"]
        row.reachable = verification["reachable"]
        row.last_checked_at = verification["last_checked_at"]

    session.commit()
    session.refresh(row)
    return row


def delete_source(session: Session, source_id: int) -> Dict[str, Any]:
    row = _get_source_or_raise(session, source_id)
    if not row.created_by_user:
        raise ValueError("System source cannot be deleted. Disable it instead.")
    deleted = source_to_dict(row)
    session.delete(row)
    session.commit()
    return deleted


def verify_source(session: Session, source_id: int) -> SourceSite:
    row = _get_source_or_raise(session, source_id)
    verification = verify_source_url(row.url, extra_trusted_domains=active_company_domains(session))
    row.verification_status = verification["verification_status"]
    row.verification_method = verification["verification_method"]
    row.verification_message = verification["verification_message"]
    row.trusted_domain = verification["trusted_domain"]
    row.reachable = verification["reachable"]
    row.last_checked_at = verification["last_checked_at"]
    if row.manual_approved and row.reachable:
        row.verification_status = "verified"
        row.verification_method = "manual_approved+reachable"
    session.commit()
    session.refresh(row)
    return row


def approve_source(session: Session, source_id: int, note: str | None = None) -> SourceSite:
    row = _get_source_or_raise(session, source_id)
    row.manual_approved = True
    row.verification_status = "verified" if row.reachable else "unverified"
    row.verification_method = "manual_approved"
    row.verification_message = (note or "Manually approved by user.")[:1000]
    row.last_checked_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(row)
    return row


def sources_for_ingestion(session: Session) -> List[SourceDefinition]:
    rows = list(
        session.scalars(
            select(SourceSite).where(
                SourceSite.active.is_(True),
                or_(
                    SourceSite.verification_status == "verified",
                    SourceSite.manual_approved.is_(True),
                ),
            )
        )
    )
    macro_keys = get_active_category_keys(session, "macro")
    tech_keys = get_active_category_keys(session, "tech")
    definitions: List[SourceDefinition] = []
    for row in rows:
        params = {}
        if row.params_json:
            try:
                params = json.loads(row.params_json)
            except json.JSONDecodeError:
                params = {}
        macro_hint = row.module_key if row.module_type == "macro" and row.module_key in macro_keys else None
        tech_hint = row.module_key if row.module_type == "tech" and row.module_key in tech_keys else None
        definitions.append(
            SourceDefinition(
                name=row.name,
                source_type=row.source_type,
                url=row.url,
                params=params,
                macro_hint=macro_hint,
                tech_hint=tech_hint,
            )
        )
    return definitions


def company_to_dict(row: CompanyWhitelist) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "domain": row.domain,
        "active": row.active,
        "created_by_user": row.created_by_user,
        "note": row.note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def source_to_dict(row: SourceSite) -> Dict[str, Any]:
    payload = {
        "id": row.id,
        "name": row.name,
        "url": row.url,
        "source_type": row.source_type,
        "module_type": row.module_type,
        "module_key": row.module_key,
        "module_label": row.module_key,
        "active": row.active,
        "created_by_user": row.created_by_user,
        "verification_status": row.verification_status,
        "verification_method": row.verification_method,
        "verification_message": row.verification_message,
        "trusted_domain": row.trusted_domain,
        "reachable": row.reachable,
        "manual_approved": row.manual_approved,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if row.params_json:
        try:
            payload["params"] = json.loads(row.params_json)
        except json.JSONDecodeError:
            payload["params"] = {}
    else:
        payload["params"] = {}
    return payload


def active_company_domains(session: Session) -> List[str]:
    rows = session.scalars(select(CompanyWhitelist.domain).where(CompanyWhitelist.active.is_(True)))
    return [_normalize_domain(x) for x in rows if _normalize_domain(x)]


def build_company_google_news_rss(domain: str) -> str:
    clean_domain = _normalize_domain(domain)
    q = quote_plus(
        f'site:{clean_domain} ("gallium nitride" OR ("GaN" semiconductor)) '
        "-generative -adversarial when:14d"
    )
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def verify_source_url(url: str, extra_trusted_domains: Optional[List[str]] = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {
            "verification_status": "failed",
            "verification_method": "url_format_check",
            "verification_message": "Invalid URL format.",
            "trusted_domain": False,
            "reachable": False,
            "last_checked_at": now,
        }

    allowlist = set(STATIC_TRUSTED_DOMAIN_SUFFIXES)
    for domain in extra_trusted_domains or []:
        clean = _normalize_domain(domain)
        if clean:
            allowlist.add(clean)

    trusted = _is_trusted_domain(parsed.hostname or "", allowlist)
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(
                url,
                headers={
                    "User-Agent": "GaN-Intel-Bot/1.0 (+source-verification)",
                    "Accept": "text/html,application/xml,text/xml,application/json,*/*",
                },
            )
            status_code = int(resp.status_code)
            reachable = status_code < 400
            final_host = (resp.url.host or parsed.hostname or "").lower()
            trusted = trusted or _is_trusted_domain(final_host, allowlist)
    except Exception as exc:
        return {
            "verification_status": "failed",
            "verification_method": "http_reachability",
            "verification_message": f"Request failed: {str(exc)[:180]}",
            "trusted_domain": trusted,
            "reachable": False,
            "last_checked_at": now,
        }

    if reachable and trusted:
        return {
            "verification_status": "verified",
            "verification_method": "reachable+trusted_domain",
            "verification_message": "Reachable and matched trusted domain allow-list.",
            "trusted_domain": True,
            "reachable": True,
            "last_checked_at": now,
        }
    if reachable:
        return {
            "verification_status": "unverified",
            "verification_method": "reachable_only",
            "verification_message": "Reachable, but domain not in trusted allow-list. Manual approval required.",
            "trusted_domain": False,
            "reachable": True,
            "last_checked_at": now,
        }
    return {
        "verification_status": "failed",
        "verification_method": "http_reachability",
        "verification_message": "URL is not reachable.",
        "trusted_domain": trusted,
        "reachable": False,
        "last_checked_at": now,
    }


def _validate_source_inputs(
    session: Session, *, source_type: str, module_type: str, module_key: str, url: str
) -> None:
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(f"Unsupported source_type: {source_type}")
    if module_type not in VALID_MODULE_TYPES:
        raise ValueError(f"Unsupported module_type: {module_type}")
    valid_keys = get_active_category_keys(session, module_type)
    if module_key not in valid_keys:
        raise ValueError(f"Unsupported module_key: {module_key}")
    if len(url.strip()) > 1500:
        raise ValueError("URL too long.")


def _infer_module_from_hints(macro_hint: str | None, tech_hint: str | None) -> tuple[str, str]:
    if macro_hint in {"industry", "stock", "academic"}:
        return "macro", macro_hint
    if tech_hint in {"low_power", "high_power", "high_frequency", "materials", "packaging", "other"}:
        return "tech", f"industry_{tech_hint}"
    if tech_hint and tech_hint.startswith(("industry_", "academic_")):
        return "tech", tech_hint
    return "macro", "industry"


def _is_trusted_domain(host: str, allowlist: set[str]) -> bool:
    host = (host or "").lower()
    if not host:
        return False
    for suffix in allowlist:
        if host == suffix or host.endswith(f".{suffix}"):
            return True
    return False


def _normalize_domain(domain: str) -> str:
    value = (domain or "").strip().lower()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        value = parsed.netloc.lower()
    if value.startswith("www."):
        value = value[4:]
    return value.strip("/")


def _migrate_legacy_vlsi_to_vis(session: Session) -> None:
    legacy = session.scalar(
        select(CompanyWhitelist).where(
            or_(
                CompanyWhitelist.name == "VLSI",
                CompanyWhitelist.domain == "vlsitimes.com",
            )
        )
    )
    if not legacy:
        return

    target = session.scalar(
        select(CompanyWhitelist).where(
            or_(
                CompanyWhitelist.name == "VIS",
                CompanyWhitelist.domain == "vis.com.tw",
            )
        )
    )
    if target and target.id != legacy.id:
        if legacy.active and not target.active:
            target.active = True
        if not target.note:
            target.note = "Official site (press): https://www.vis.com.tw/tc/press_latest"
        session.delete(legacy)
        session.commit()
        return

    legacy.name = "VIS"
    legacy.domain = "vis.com.tw"
    legacy.note = "Official site (press): https://www.vis.com.tw/tc/press_latest"
    legacy.created_by_user = False
    session.commit()


def _get_source_or_raise(session: Session, source_id: int) -> SourceSite:
    row = session.get(SourceSite, source_id)
    if not row:
        raise ValueError(f"Source not found: {source_id}")
    return row


def _get_company_or_raise(session: Session, company_id: int) -> CompanyWhitelist:
    row = session.get(CompanyWhitelist, company_id)
    if not row:
        raise ValueError(f"Company not found: {company_id}")
    return row


def _module_label(module_type: str, module_key: str) -> str:
    mapping = {
        "macro:industry": "企业产业",
        "macro:stock": "股市",
        "macro:academic": "学术",
        "tech:industry_low_power": "产业 / 低功率",
        "tech:industry_high_power": "产业 / 高功率",
        "tech:industry_high_frequency": "产业 / 高频",
        "tech:industry_materials": "产业 / 材料",
        "tech:industry_packaging": "产业 / 封装",
        "tech:industry_other": "产业 / 其他",
        "tech:academic_low_power": "学术 / 低功率",
        "tech:academic_high_power": "学术 / 高功率",
        "tech:academic_high_frequency": "学术 / 高频",
        "tech:academic_materials": "学术 / 材料",
        "tech:academic_packaging": "学术 / 封装",
        "tech:academic_other": "学术 / 其他",
    }
    return mapping.get(f"{module_type}:{module_key}", module_key)


def _extract_company_name_from_generated_source(source_name: str) -> str:
    prefix = "Whitelist Company - "
    if not source_name.startswith(prefix):
        return ""
    return source_name[len(prefix) :].strip()
