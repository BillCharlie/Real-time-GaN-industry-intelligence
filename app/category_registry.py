from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

from sqlalchemy import asc, select
from sqlalchemy.orm import Session

from .models import CategoryField

DEFAULT_CATEGORIES = {
    "macro": [
        {"key": "industry", "label": "企业产业", "sort_order": 10},
        {"key": "stock", "label": "股市", "sort_order": 20},
        {"key": "academic", "label": "学术", "sort_order": 30},
    ],
    "tech": [
        {"key": "low_power", "label": "低功率", "sort_order": 10},
        {"key": "high_power", "label": "高功率", "sort_order": 20},
        {"key": "high_frequency", "label": "高频", "sort_order": 30},
        {"key": "materials", "label": "材料", "sort_order": 40},
        {"key": "packaging", "label": "封装", "sort_order": 50},
        {"key": "other", "label": "其他", "sort_order": 90},
    ],
}

VALID_GROUPS = {"macro", "tech"}


def ensure_seed_categories(session: Session) -> int:
    inserted = 0
    existing = set(session.execute(select(CategoryField.group_type, CategoryField.key)).all())
    for group_type, rows in DEFAULT_CATEGORIES.items():
        for row in rows:
            pair = (group_type, row["key"])
            if pair in existing:
                continue
            session.add(
                CategoryField(
                    group_type=group_type,
                    key=row["key"],
                    label=row["label"],
                    active=True,
                    built_in=True,
                    created_by_user=False,
                    sort_order=row.get("sort_order", 100),
                )
            )
            inserted += 1
            existing.add(pair)
    if inserted:
        session.commit()
    return inserted


def list_categories(
    session: Session, *, group_type: Optional[str] = None, include_inactive: bool = False
) -> List[CategoryField]:
    stmt = select(CategoryField)
    if group_type:
        stmt = stmt.where(CategoryField.group_type == group_type)
    if not include_inactive:
        stmt = stmt.where(CategoryField.active.is_(True))
    stmt = stmt.order_by(asc(CategoryField.group_type), asc(CategoryField.sort_order), asc(CategoryField.key))
    return list(session.scalars(stmt))


def category_dict(row: CategoryField) -> Dict[str, object]:
    return {
        "id": row.id,
        "group_type": row.group_type,
        "key": row.key,
        "label": row.label,
        "active": row.active,
        "built_in": row.built_in,
        "created_by_user": row.created_by_user,
        "sort_order": row.sort_order,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def categories_payload(session: Session, *, include_inactive: bool = False) -> Dict[str, List[Dict[str, object]]]:
    rows = list_categories(session, include_inactive=include_inactive)
    grouped: Dict[str, List[Dict[str, object]]] = {"macro": [], "tech": []}
    for row in rows:
        grouped.setdefault(row.group_type, []).append(category_dict(row))
    return grouped


def create_category(
    session: Session,
    *,
    group_type: str,
    key: str,
    label: str,
    active: bool = True,
    sort_order: int = 100,
) -> CategoryField:
    clean_group = _normalize_group(group_type)
    clean_key = _normalize_key(key)
    clean_label = label.strip()
    if not clean_label:
        raise ValueError("Label is required.")

    duplicate = session.scalar(
        select(CategoryField).where(CategoryField.group_type == clean_group, CategoryField.key == clean_key)
    )
    if duplicate:
        return duplicate

    row = CategoryField(
        group_type=clean_group,
        key=clean_key,
        label=clean_label[:120],
        active=bool(active),
        built_in=False,
        created_by_user=True,
        sort_order=int(sort_order),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_category(
    session: Session,
    category_id: int,
    *,
    label: Optional[str] = None,
    active: Optional[bool] = None,
    sort_order: Optional[int] = None,
) -> CategoryField:
    row = _get_category_or_raise(session, category_id)
    if label is not None:
        text = label.strip()
        if not text:
            raise ValueError("Label cannot be empty.")
        row.label = text[:120]
    if active is not None:
        row.active = bool(active)
    if sort_order is not None:
        row.sort_order = int(sort_order)
    session.commit()
    session.refresh(row)
    return row


def deactivate_category(session: Session, category_id: int) -> CategoryField:
    row = _get_category_or_raise(session, category_id)
    row.active = False
    session.commit()
    session.refresh(row)
    return row


def get_active_category_keys(session: Session, group_type: str) -> Set[str]:
    group = _normalize_group(group_type)
    keys = session.scalars(
        select(CategoryField.key).where(CategoryField.group_type == group, CategoryField.active.is_(True))
    )
    output = {key for key in keys if key}
    return output


def get_category_labels(session: Session) -> Dict[str, Dict[str, str]]:
    rows = list_categories(session, include_inactive=False)
    grouped: Dict[str, Dict[str, str]] = {"macro": {}, "tech": {}}
    for row in rows:
        grouped.setdefault(row.group_type, {})[row.key] = row.label
    return grouped


def _get_category_or_raise(session: Session, category_id: int) -> CategoryField:
    row = session.get(CategoryField, category_id)
    if not row:
        raise ValueError(f"Category not found: {category_id}")
    return row


def _normalize_group(group_type: str) -> str:
    clean = (group_type or "").strip().lower()
    if clean not in VALID_GROUPS:
        raise ValueError(f"Invalid group_type: {group_type}")
    return clean


def _normalize_key(key: str) -> str:
    raw = (key or "").strip().lower()
    if not raw:
        raise ValueError("Key is required.")
    normalized = re.sub(r"[^a-z0-9_]+", "_", raw)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        raise ValueError("Invalid key.")
    if len(normalized) > 60:
        raise ValueError("Key too long.")
    return normalized

