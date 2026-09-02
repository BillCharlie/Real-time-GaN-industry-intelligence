from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional, Set

from sqlalchemy import asc, select, text
from sqlalchemy.orm import Session

from .models import Article, CategoryField, SourceSite

VALID_GROUPS = {"macro", "tech"}

DEFAULT_MACRO_ROOTS = [
    {"key": "academic", "label": "学术", "sort_order": 10},
    {"key": "industry", "label": "产业", "sort_order": 20},
    {"key": "stock", "label": "股市", "sort_order": 30},
]

DEFAULT_TECH_TEMPLATES = [
    {"base_key": "low_power", "label": "Power-MHZ", "sort_order": 10},
    {"base_key": "high_frequency", "label": "RF-GHZ", "sort_order": 30},
    {"base_key": "materials", "label": "材料", "sort_order": 40},
    {"base_key": "packaging", "label": "封装", "sort_order": 50},
    {"base_key": "other", "label": "其他", "sort_order": 90},
]


def ensure_seed_categories(session: Session) -> int:
    # Keep schema compatible for existing SQLite databases.
    _ensure_category_schema(session)
    _normalize_existing_category_flags(session)
    return _ensure_default_category_tree(session)


def list_categories(
    session: Session, *, group_type: Optional[str] = None, include_inactive: bool = False
) -> List[CategoryField]:
    stmt = select(CategoryField)
    if group_type:
        stmt = stmt.where(CategoryField.group_type == group_type)
    if not include_inactive:
        stmt = stmt.where(CategoryField.active.is_(True))
    stmt = stmt.order_by(
        asc(CategoryField.group_type),
        asc(CategoryField.parent_id),
        asc(CategoryField.sort_order),
        asc(CategoryField.key),
    )
    return list(session.scalars(stmt))


def category_dict(row: CategoryField) -> Dict[str, object]:
    return {
        "id": row.id,
        "group_type": row.group_type,
        "key": row.key,
        "label": row.label,
        "parent_id": row.parent_id,
        "active": row.active,
        "built_in": row.built_in,
        "created_by_user": row.created_by_user,
        "sort_order": row.sort_order,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def categories_payload(session: Session, *, include_inactive: bool = False) -> Dict[str, object]:
    rows = list_categories(session, include_inactive=include_inactive)
    grouped_flat: Dict[str, List[Dict[str, object]]] = {"macro": [], "tech": []}
    grouped_tree: Dict[str, List[Dict[str, object]]] = {"macro": [], "tech": []}
    by_id: Dict[int, Dict[str, object]] = {}

    for row in rows:
        data = category_dict(row)
        data["children"] = []
        grouped_flat.setdefault(row.group_type, []).append(data)
        if row.id is not None:
            by_id[int(row.id)] = data

    for group in ("macro", "tech"):
        for node in grouped_flat.get(group, []):
            parent_id = node.get("parent_id")
            if isinstance(parent_id, int) and parent_id in by_id:
                by_id[parent_id]["children"].append(node)
            else:
                grouped_tree[group].append(node)

    return {"macro": grouped_flat["macro"], "tech": grouped_flat["tech"], "tree": grouped_tree}


def create_category(
    session: Session,
    *,
    group_type: str,
    key: str,
    label: str,
    parent_id: int | None = None,
    active: bool = True,
    sort_order: int = 100,
) -> CategoryField:
    clean_group = _normalize_group(group_type)
    clean_label = (label or "").strip()
    if not clean_label:
        raise ValueError("Label is required.")

    clean_key = _build_category_key(session, group_type=clean_group, preferred_key=key, label=clean_label)
    parent = _validate_parent(session, parent_id=parent_id, group_type=clean_group)

    row = CategoryField(
        group_type=clean_group,
        key=clean_key,
        label=clean_label[:120],
        parent_id=parent.id if parent else None,
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
    parent_id: Optional[int] = None,
    move_to_root: bool = False,
) -> CategoryField:
    row = _get_category_or_raise(session, category_id)
    if label is not None:
        text_value = label.strip()
        if not text_value:
            raise ValueError("Label cannot be empty.")
        row.label = text_value[:120]
    if active is not None:
        row.active = bool(active)
    if sort_order is not None:
        row.sort_order = int(sort_order)
    if move_to_root:
        row.parent_id = None
    elif parent_id is not None:
        parent = session.get(CategoryField, parent_id)
        if parent is None:
            raise ValueError(f"Parent category #{parent_id} not found.")
        row.parent_id = parent_id
    session.commit()
    session.refresh(row)
    return row


def deactivate_category(session: Session, category_id: int) -> CategoryField:
    row = _get_category_or_raise(session, category_id)
    row.active = False
    session.commit()
    session.refresh(row)
    return row


def delete_category(session: Session, category_id: int) -> Dict[str, object]:
    root = _get_category_or_raise(session, category_id)
    all_rows = list(session.scalars(select(CategoryField)))
    subtree = _collect_subtree(root.id, all_rows)
    subtree_ids = {row.id for row in subtree}
    deleted_keys_by_group: Dict[str, Set[str]] = {"macro": set(), "tech": set()}
    for row in subtree:
        deleted_keys_by_group.setdefault(row.group_type, set()).add(row.key)

    for group_type, deleted_keys in deleted_keys_by_group.items():
        if not deleted_keys:
            continue
        group_rows = [row for row in all_rows if row.group_type == group_type]
        fallback_key = _pick_fallback_key_for_delete(
            session,
            group_type=group_type,
            all_rows=group_rows,
            subtree_ids=subtree_ids,
            subtree_keys=deleted_keys,
        )
        _reassign_articles_for_deleted_keys(
            session, group_type=group_type, deleted_keys=deleted_keys, fallback_key=fallback_key
        )
        _reassign_sources_for_deleted_keys(
            session, group_type=group_type, deleted_keys=deleted_keys, fallback_key=fallback_key
        )

    deleted = category_dict(root)
    depth_cache: Dict[int, int] = {}
    subtree_by_id = {row.id: row for row in subtree}

    def depth(node: CategoryField) -> int:
        if node.id in depth_cache:
            return depth_cache[node.id]
        level = 0
        parent_id = node.parent_id
        while isinstance(parent_id, int) and parent_id in subtree_ids:
            level += 1
            parent = subtree_by_id.get(parent_id)
            parent_id = parent.parent_id if parent else None
        depth_cache[node.id] = level
        return level

    for row in sorted(subtree, key=depth, reverse=True):
        session.delete(row)
    session.commit()
    return deleted


def get_active_category_keys(session: Session, group_type: str) -> Set[str]:
    group = _normalize_group(group_type)
    keys = session.scalars(
        select(CategoryField.key).where(CategoryField.group_type == group, CategoryField.active.is_(True))
    )
    return {key for key in keys if key}


def get_category_labels(session: Session) -> Dict[str, Dict[str, str]]:
    rows = list_categories(session, include_inactive=False)
    grouped: Dict[str, Dict[str, str]] = {"macro": {}, "tech": {}}
    for row in rows:
        grouped.setdefault(row.group_type, {})[row.key] = row.label
    return grouped


def _collect_subtree(root_id: int, rows: List[CategoryField]) -> List[CategoryField]:
    children_map: Dict[int | None, List[CategoryField]] = {}
    for row in rows:
        children_map.setdefault(row.parent_id, []).append(row)

    stack = [root_id]
    seen: Set[int] = set()
    output: List[CategoryField] = []
    by_id = {row.id: row for row in rows}
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        row = by_id.get(node_id)
        if row is None:
            continue
        output.append(row)
        for child in children_map.get(node_id, []):
            stack.append(child.id)
    return output


def _validate_parent(session: Session, *, parent_id: int | None, group_type: str) -> CategoryField | None:
    if parent_id is None:
        return None
    parent = _get_category_or_raise(session, parent_id)
    if group_type == "macro":
        if parent.group_type != "macro":
            raise ValueError("Macro category parent must be macro.")
        return parent
    if group_type == "tech":
        if parent.group_type not in {"macro", "tech"}:
            raise ValueError("Tech category parent must be macro or tech.")
        return parent
    raise ValueError(f"Invalid group_type: {group_type}")


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


def _normalize_key(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        raise ValueError("Key is required.")
    normalized = _slugify_ascii(raw)
    if not normalized:
        raise ValueError("Invalid key.")
    if len(normalized) > 60:
        raise ValueError("Key too long.")
    return normalized


def _slugify_ascii(raw: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", raw)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def _ensure_category_schema(session: Session) -> None:
    bind = session.get_bind()
    if not bind or bind.dialect.name != "sqlite":
        return
    columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(category_fields)")).mappings().all()
    }
    if "parent_id" not in columns:
        session.execute(text("ALTER TABLE category_fields ADD COLUMN parent_id INTEGER"))
        session.commit()


def _normalize_existing_category_flags(session: Session) -> None:
    rows = list(session.scalars(select(CategoryField).where(CategoryField.built_in.is_(True))))
    changed = False
    for row in rows:
        if row.built_in:
            row.built_in = False
            changed = True
        if not row.created_by_user:
            row.created_by_user = True
            changed = True
    if changed:
        session.commit()


def _ensure_default_category_tree(session: Session) -> int:
    changed = 0
    rows = list(session.scalars(select(CategoryField)))
    by_group_key: Dict[tuple[str, str], CategoryField] = {(row.group_type, row.key): row for row in rows}
    macro_roots: Dict[str, CategoryField] = {}

    for item in DEFAULT_MACRO_ROOTS:
        key = str(item["key"])
        label = str(item["label"])
        sort_order = int(item["sort_order"])
        row = by_group_key.get(("macro", key))
        if row is None:
            row = CategoryField(
                group_type="macro",
                key=key,
                label=label,
                parent_id=None,
                active=True,
                built_in=False,
                created_by_user=True,
                sort_order=sort_order,
            )
            session.add(row)
            session.flush()
            changed += 1
        else:
            row_changed = False
            # Preserve user-renamed labels across restarts; only backfill empty labels.
            if not (row.label or "").strip():
                row.label = label
                row_changed = True
            if row.parent_id is not None:
                row.parent_id = None
                row_changed = True
            if int(row.sort_order or 0) != sort_order:
                row.sort_order = sort_order
                row_changed = True
            if not row.active:
                row.active = True
                row_changed = True
            if row.built_in:
                row.built_in = False
                row_changed = True
            if not row.created_by_user:
                row.created_by_user = True
                row_changed = True
            if row_changed:
                changed += 1
        by_group_key[("macro", key)] = row
        macro_roots[key] = row

    max_default_sort = max(int(item["sort_order"]) for item in DEFAULT_MACRO_ROOTS)
    other_macro_roots = sorted(
        [
            row
            for row in session.scalars(select(CategoryField).where(CategoryField.group_type == "macro"))
            if row.parent_id is None and row.key not in macro_roots
        ],
        key=lambda row: (int(row.sort_order or 0), str(row.key)),
    )
    next_sort = max_default_sort + 10
    for row in other_macro_roots:
        current_sort = int(row.sort_order or 0)
        if current_sort <= max_default_sort:
            row.sort_order = next_sort
            next_sort += 10
            changed += 1
        else:
            next_sort = max(next_sort, current_sort + 10)

    if "industry" in macro_roots:
        changed += _migrate_legacy_tech_templates(
            session,
            industry_parent_id=int(macro_roots["industry"].id),
        )

    rows = list(session.scalars(select(CategoryField)))
    by_group_key = {(row.group_type, row.key): row for row in rows}
    for macro_key in ("academic", "industry"):
        parent = macro_roots.get(macro_key)
        if parent is None:
            continue
        for item in DEFAULT_TECH_TEMPLATES:
            child_key = f"{macro_key}_{item['base_key']}"
            child_label = str(item["label"])
            child_sort = int(item["sort_order"])
            row = by_group_key.get(("tech", child_key))
            if row is None:
                row = CategoryField(
                    group_type="tech",
                    key=child_key,
                    label=child_label,
                    parent_id=int(parent.id),
                    active=True,
                    built_in=False,
                    created_by_user=True,
                    sort_order=child_sort,
                )
                session.add(row)
                session.flush()
                by_group_key[("tech", child_key)] = row
                changed += 1
            else:
                row_changed = False
                # Preserve user-renamed labels across restarts; only backfill empty labels.
                if not (row.label or "").strip():
                    row.label = child_label
                    row_changed = True
                if row.parent_id != int(parent.id):
                    row.parent_id = int(parent.id)
                    row_changed = True
                if int(row.sort_order or 0) != child_sort:
                    row.sort_order = child_sort
                    row_changed = True
                if not row.active:
                    row.active = True
                    row_changed = True
                if row.built_in:
                    row.built_in = False
                    row_changed = True
                if not row.created_by_user:
                    row.created_by_user = True
                    row_changed = True
                if row_changed:
                    changed += 1

    changed += _relocate_flat_high_power_categories(session, macro_roots=macro_roots)
    changed += _rename_power_labels_to_voltage(session)
    changed += _normalize_article_tech_categories(session)
    changed += _normalize_source_tech_module_keys(session)

    if changed:
        session.commit()
    return changed


def _migrate_legacy_tech_templates(session: Session, *, industry_parent_id: int) -> int:
    changed = 0
    legacy_by_key = {
        row.key: row
        for row in session.scalars(
            select(CategoryField).where(CategoryField.group_type == "tech", CategoryField.key.in_([x["base_key"] for x in DEFAULT_TECH_TEMPLATES]))
        )
    }
    by_group_key = {
        (row.group_type, row.key): row
        for row in session.scalars(select(CategoryField).where(CategoryField.group_type == "tech"))
    }

    for item in DEFAULT_TECH_TEMPLATES:
        base_key = str(item["base_key"])
        target_key = f"industry_{base_key}"
        target = by_group_key.get(("tech", target_key))
        legacy = legacy_by_key.get(base_key)
        if legacy is None:
            continue

        if target is None:
            old_key = legacy.key
            legacy.key = target_key
            legacy.label = str(item["label"])
            legacy.parent_id = industry_parent_id
            legacy.sort_order = int(item["sort_order"])
            legacy.active = True
            legacy.built_in = False
            legacy.created_by_user = True
            _reassign_source_module_key(session, old_key=old_key, new_key=target_key)
            changed += 1
            by_group_key.pop(("tech", old_key), None)
            by_group_key[("tech", target_key)] = legacy
            continue

        if legacy.id == target.id:
            continue

        _move_children_to_new_parent(session, old_parent_id=int(legacy.id), new_parent_id=int(target.id))
        _reassign_articles_tech_exact(session, old_key=legacy.key, new_key=target.key)
        _reassign_source_module_key(session, old_key=legacy.key, new_key=target.key)
        session.delete(legacy)
        changed += 1

    return changed


# Labels the user asked to retire, mapped to what replaces them. Seeding only
# backfills empty labels (renames are preserved), so existing rows need this
# one-off pass or they keep the old wording forever.
#
# Matching is by label, not by key: the keys drifted from what they display —
# `*_low_power` renders as 低频, while 低功率 lives on user-created `cat_*` rows.
_LABEL_RENAMES = {
    "低频": "Power-MHZ",
    "MHZ": "Power-MHZ",  # already shipped as MHZ; carry those rows forward
    "高频": "RF-GHZ",
    "高功率": "高压",
    "低功率": "低压",
}


def _rename_power_labels_to_voltage(session: Session) -> int:
    changed = 0
    for row in session.scalars(select(CategoryField).where(CategoryField.group_type == "tech")):
        replacement = _LABEL_RENAMES.get((row.label or "").strip())
        if replacement:
            row.label = replacement
            changed += 1
    return changed


def _relocate_flat_high_power_categories(
    session: Session,
    *,
    macro_roots: Dict[str, CategoryField],
) -> int:
    changed = 0
    rows = list(session.scalars(select(CategoryField).where(CategoryField.group_type == "tech")))
    by_key = {row.key: row for row in rows}
    by_parent: Dict[int, List[CategoryField]] = {}
    for row in rows:
        if isinstance(row.parent_id, int):
            by_parent.setdefault(int(row.parent_id), []).append(row)

    for macro_key in ("industry", "academic"):
        macro_root = macro_roots.get(macro_key)
        low_power = by_key.get(f"{macro_key}_low_power")
        high_power = by_key.get(f"{macro_key}_high_power")
        if macro_root is None or low_power is None or high_power is None:
            continue

        # Merge duplicated "高功率" child under 低频 into the canonical <macro>_high_power key.
        dup = next(
            (
                row
                for row in by_parent.get(int(low_power.id), [])
                if row.id != high_power.id and (row.label or "").strip() == (high_power.label or "").strip()
            ),
            None,
        )
        if dup is not None:
            _move_children_to_new_parent(session, old_parent_id=int(dup.id), new_parent_id=int(high_power.id))
            _reassign_articles_tech_exact(session, old_key=dup.key, new_key=high_power.key)
            _reassign_source_module_key(session, old_key=dup.key, new_key=high_power.key)
            session.delete(dup)
            changed += 1

        # Ensure "高功率" is nested under "低频" instead of being a flat sibling.
        if high_power.parent_id != int(low_power.id):
            high_power.parent_id = int(low_power.id)
            changed += 1
        if int(high_power.sort_order or 0) < 100:
            high_power.sort_order = 100
            changed += 1
        if not high_power.active:
            high_power.active = True
            changed += 1
    return changed


def _normalize_article_tech_categories(session: Session) -> int:
    active_tech_keys = set(
        session.scalars(select(CategoryField.key).where(CategoryField.group_type == "tech", CategoryField.active.is_(True)))
    )
    if not active_tech_keys:
        return 0
    base_keys = {str(item["base_key"]) for item in DEFAULT_TECH_TEMPLATES} | {"high_power"}
    fallback_stock = "industry_other" if "industry_other" in active_tech_keys else sorted(active_tech_keys)[0]
    changed = 0
    rows = list(session.scalars(select(Article)))
    for row in rows:
        macro = (row.macro_category or "").strip().lower()
        tech = (row.tech_category or "").strip().lower()
        next_key = _resolve_tech_key_for_macro(
            macro=macro,
            tech=tech,
            active_tech_keys=active_tech_keys,
            base_keys=base_keys,
            fallback_stock=fallback_stock,
        )
        if next_key and row.tech_category != next_key:
            row.tech_category = next_key
            changed += 1
    return changed


def _normalize_source_tech_module_keys(session: Session) -> int:
    active_tech_keys = set(
        session.scalars(select(CategoryField.key).where(CategoryField.group_type == "tech", CategoryField.active.is_(True)))
    )
    if not active_tech_keys:
        return 0
    base_keys = {str(item["base_key"]) for item in DEFAULT_TECH_TEMPLATES} | {"high_power"}
    changed = 0
    rows = list(session.scalars(select(SourceSite).where(SourceSite.module_type == "tech")))
    for row in rows:
        raw_key = (row.module_key or "").strip().lower()
        if raw_key in active_tech_keys:
            continue
        candidate = ""
        if raw_key in base_keys:
            candidate = f"industry_{raw_key}"
        elif raw_key.startswith("industry_") or raw_key.startswith("academic_"):
            _, _, suffix = raw_key.partition("_")
            if suffix in base_keys:
                candidate = f"industry_{suffix}"
        if candidate and candidate in active_tech_keys:
            row.module_key = candidate
            changed += 1
            continue
        if "industry_other" in active_tech_keys:
            row.module_key = "industry_other"
            changed += 1
            continue
        fallback = sorted(active_tech_keys)[0]
        row.module_key = fallback
        changed += 1
    return changed


def _resolve_tech_key_for_macro(
    *,
    macro: str,
    tech: str,
    active_tech_keys: Set[str],
    base_keys: Set[str],
    fallback_stock: str,
) -> str:
    if tech in active_tech_keys:
        if macro in {"academic", "industry"}:
            if tech.startswith("academic_") or tech.startswith("industry_"):
                _, _, suffix = tech.partition("_")
                if suffix in base_keys:
                    candidate = f"{macro}_{suffix}"
                    if candidate in active_tech_keys:
                        return candidate
            if tech in base_keys:
                candidate = f"{macro}_{tech}"
                if candidate in active_tech_keys:
                    return candidate
        return tech

    if macro in {"academic", "industry"}:
        normalized = tech
        if tech.startswith("academic_") or tech.startswith("industry_"):
            _, _, normalized = tech.partition("_")
        if normalized in base_keys:
            candidate = f"{macro}_{normalized}"
            if candidate in active_tech_keys:
                return candidate
        fallback = f"{macro}_other"
        if fallback in active_tech_keys:
            return fallback

    if macro == "stock":
        if fallback_stock in active_tech_keys:
            return fallback_stock
        return sorted(active_tech_keys)[0]

    if "industry_other" in active_tech_keys:
        return "industry_other"
    if "academic_other" in active_tech_keys:
        return "academic_other"
    return sorted(active_tech_keys)[0]


def _move_children_to_new_parent(session: Session, *, old_parent_id: int, new_parent_id: int) -> None:
    rows = list(session.scalars(select(CategoryField).where(CategoryField.parent_id == old_parent_id)))
    for row in rows:
        row.parent_id = new_parent_id


def _reassign_articles_tech_exact(session: Session, *, old_key: str, new_key: str) -> None:
    if not old_key or old_key == new_key:
        return
    rows = list(session.scalars(select(Article).where(Article.tech_category == old_key)))
    for row in rows:
        row.tech_category = new_key


def _reassign_source_module_key(session: Session, *, old_key: str, new_key: str) -> None:
    if not old_key or old_key == new_key:
        return
    rows = list(
        session.scalars(
            select(SourceSite).where(SourceSite.module_type == "tech", SourceSite.module_key == old_key)
        )
    )
    for row in rows:
        row.module_key = new_key


def _build_category_key(session: Session, *, group_type: str, preferred_key: str, label: str) -> str:
    direct = _slugify_ascii((preferred_key or "").strip().lower())
    if direct:
        base = direct[:60]
    else:
        from_label = _slugify_ascii((label or "").strip().lower())
        if from_label:
            base = from_label[:60]
        else:
            digest = hashlib.md5((label or "").encode("utf-8")).hexdigest()[:10]
            base = f"cat_{digest}"
    return _make_unique_key(session, group_type=group_type, base_key=base)


def _make_unique_key(session: Session, *, group_type: str, base_key: str) -> str:
    base = (base_key or "cat").strip("_")[:60] or "cat"
    candidate = base
    index = 2
    while session.scalar(
        select(CategoryField.id).where(CategoryField.group_type == group_type, CategoryField.key == candidate).limit(1)
    ):
        suffix = f"_{index}"
        max_prefix = max(1, 60 - len(suffix))
        candidate = f"{base[:max_prefix]}{suffix}"
        index += 1
    return candidate


def _pick_fallback_key_for_delete(
    session: Session,
    *,
    group_type: str,
    all_rows: List[CategoryField],
    subtree_ids: Set[int],
    subtree_keys: Set[str],
) -> str:
    candidates = [row for row in all_rows if row.id not in subtree_ids and row.key not in subtree_keys]
    if candidates:
        candidates.sort(key=lambda x: (0 if x.active else 1, int(x.sort_order or 0), str(x.key)))
        return candidates[0].key

    fallback_label = "未分类" if group_type == "macro" else "其他"
    fallback_base = "uncategorized" if group_type == "macro" else "other"
    fallback_key = _make_unique_key(session, group_type=group_type, base_key=fallback_base)
    session.add(
        CategoryField(
            group_type=group_type,
            key=fallback_key,
            label=fallback_label,
            parent_id=None,
            active=True,
            built_in=False,
            created_by_user=True,
            sort_order=9999,
        )
    )
    session.flush()
    return fallback_key


def _reassign_articles_for_deleted_keys(
    session: Session,
    *,
    group_type: str,
    deleted_keys: Set[str],
    fallback_key: str,
) -> None:
    if not deleted_keys:
        return
    if group_type == "macro":
        rows = list(session.scalars(select(Article).where(Article.macro_category.in_(deleted_keys))))
        for row in rows:
            row.macro_category = fallback_key
    else:
        rows = list(session.scalars(select(Article).where(Article.tech_category.in_(deleted_keys))))
        for row in rows:
            row.tech_category = fallback_key


def _reassign_sources_for_deleted_keys(
    session: Session,
    *,
    group_type: str,
    deleted_keys: Set[str],
    fallback_key: str,
) -> None:
    if not deleted_keys:
        return
    rows = list(
        session.scalars(
            select(SourceSite).where(
                SourceSite.module_type == group_type,
                SourceSite.module_key.in_(deleted_keys),
            )
        )
    )
    for row in rows:
        row.module_key = fallback_key
