"""
MySQL helpers for GMM data definitions stored in `gmm_data_definition`.

Column layout is discovered via SHOW COLUMNS.
"""
import hashlib
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

TABLE = "gmm_data_definition"

_AUTO_TIMESTAMP_NAMES = {
    "last_mod",
    "last_update",
    "updated_at",
    "modified_at",
    "update_time",
}

# Prefer these fields (in order) as the source content for hash recalculation.
_HASH_SOURCE_FIELDS = (
    "encodingdata",
    "encoding_data",
    "definition",
    "data",
    "content",
    "payload",
)


def checksum_content(content: str) -> str:
    return hashlib.md5((content or "").encode("utf-8")).hexdigest()


def _table_columns(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(f"SHOW COLUMNS FROM `{TABLE}`")
        rows = cursor.fetchall()
    cols: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            field = row.get("Field") or row.get("field")
            col_type = (row.get("Type") or row.get("type") or "").lower()
        else:
            field = row[0]
            col_type = (row[1] or "").lower()
        cols.append(
            {
                "name": field,
                "type": col_type,
                "is_blob": any(
                    t in col_type
                    for t in ("blob", "binary", "varbinary", "text", "longtext", "mediumtext")
                ),
                "is_timestamp": any(t in col_type for t in ("timestamp", "datetime")),
            }
        )
    return cols


def _pk_name(cols: List[Dict[str, Any]]) -> str:
    return next((c["name"] for c in cols if c["name"].lower() == "id"), cols[0]["name"])


def _serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def _encode_for_column(value: Any, col: Dict[str, Any]) -> Any:
    if value is None:
        return None
    if col.get("is_blob") and isinstance(value, str):
        # Prefer text as-is for TEXT; encode only for binary/blob types
        col_type = col.get("type") or ""
        if any(t in col_type for t in ("blob", "binary", "varbinary")):
            return value.encode("utf-8")
        return value
    return value


def get_definition(conn, definition_id: int) -> Optional[Dict[str, Any]]:
    cols = _table_columns(conn)
    if not cols:
        raise RuntimeError(f"Table `{TABLE}` has no columns")

    col_names = [c["name"] for c in cols]
    pk = _pk_name(cols)
    select_list = ", ".join(f"`{n}`" for n in col_names)

    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT {select_list} FROM `{TABLE}` WHERE `{pk}` = %s",
            (int(definition_id),),
        )
        row = cursor.fetchone()
    if not row:
        return None

    return {name: _serialize_value(row.get(name)) for name in col_names}


def _resolve_hash_source(fields: Dict[str, Any], existing: Dict[str, Any]) -> Optional[str]:
    """Pick content used to compute MD5 hash (prefer incoming encodingdata)."""
    lower_fields = {str(k).lower(): v for k, v in (fields or {}).items()}
    lower_existing = {str(k).lower(): v for k, v in (existing or {}).items()}
    for name in _HASH_SOURCE_FIELDS:
        if name in lower_fields and lower_fields[name] is not None:
            return "" if lower_fields[name] is None else str(lower_fields[name])
        if name in lower_existing and lower_existing[name] is not None:
            return "" if lower_existing[name] is None else str(lower_existing[name])
    return None


def update_definition(
    conn,
    definition_id: int,
    fields: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    cols = _table_columns(conn)
    if not cols:
        return False, {"error": f"Table `{TABLE}` has no columns"}

    pk = _pk_name(cols)
    existing = get_definition(conn, int(definition_id))
    if not existing:
        return False, {"error": f"Data definition id {definition_id} not found"}

    by_name = {c["name"]: c for c in cols}
    by_lower = {c["name"].lower(): c for c in cols}
    fields = fields or {}

    set_parts: List[str] = []
    params: List[Any] = []
    touched_lower = set()

    for key, value in fields.items():
        col = by_name.get(key) or by_lower.get(str(key).lower())
        if not col:
            continue
        lower_name = col["name"].lower()
        if lower_name == pk.lower():
            continue
        if lower_name in _AUTO_TIMESTAMP_NAMES:
            continue
        # hash always recomputed server-side — ignore client value
        if lower_name == "hash":
            continue
        set_parts.append(f"`{col['name']}` = %s")
        params.append(_encode_for_column(value, col))
        touched_lower.add(lower_name)

    # Recalculate hash from encodingdata (or fallback content fields)
    hash_col = by_lower.get("hash")
    if hash_col:
        source = _resolve_hash_source(fields, existing)
        if source is not None:
            set_parts.append(f"`{hash_col['name']}` = %s")
            params.append(checksum_content(source))
            touched_lower.add("hash")

    # Touch last_mod / updated_at when present
    for c in cols:
        if c["name"].lower() in _AUTO_TIMESTAMP_NAMES and c["name"].lower() not in touched_lower:
            set_parts.append(f"`{c['name']}` = CURRENT_TIMESTAMP")

    if not set_parts:
        return False, {"error": "No updatable fields provided"}

    params.append(int(definition_id))
    with conn.cursor() as cursor:
        cursor.execute(
            f"UPDATE `{TABLE}` SET {', '.join(set_parts)} WHERE `{pk}` = %s",
            params,
        )
    conn.commit()
    saved = get_definition(conn, int(definition_id))
    return True, {"definition": saved}
