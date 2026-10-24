"""
MySQL helpers for GMM ISO8583 plugins stored in `gmm_iso8583_plugin`.

Column layout is discovered via SHOW COLUMNS so the UI stays compatible
if the remote schema varies slightly.
"""
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

TABLE = "gmm_iso8583_plugin"
BLOB_PREVIEW_CHARS = 120


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
                "is_blob": any(t in col_type for t in ("blob", "binary", "varbinary")),
            }
        )
    return cols


def _serialize_value(value: Any, *, is_blob: bool = False) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if is_blob:
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                return f"[BLOB {len(raw)} bytes]"
            if len(text) > BLOB_PREVIEW_CHARS:
                return text[:BLOB_PREVIEW_CHARS] + f"… ({len(raw)} bytes)"
            return text
        return raw.decode("utf-8", errors="replace")
    if is_blob and isinstance(value, str) and len(value) > BLOB_PREVIEW_CHARS:
        return value[:BLOB_PREVIEW_CHARS] + f"… ({len(value)} chars)"
    return value


def list_plugins(conn, search: str = "") -> Tuple[List[str], List[Dict[str, Any]]]:
    cols = _table_columns(conn)
    if not cols:
        raise RuntimeError(f"Table `{TABLE}` has no columns")

    col_names = [c["name"] for c in cols]
    blob_names = {c["name"] for c in cols if c["is_blob"]}
    select_list = ", ".join(f"`{n}`" for n in col_names)

    search = (search or "").strip()
    with conn.cursor() as cursor:
        if search:
            like = f"%{search}%"
            where_parts: List[str] = []
            params: List[Any] = []
            for c in cols:
                if c["is_blob"]:
                    continue
                where_parts.append(f"CAST(`{c['name']}` AS CHAR) LIKE %s")
                params.append(like)
            if where_parts:
                cursor.execute(
                    f"SELECT {select_list} FROM `{TABLE}` WHERE "
                    + " OR ".join(where_parts)
                    + f" ORDER BY `{col_names[0]}` ASC",
                    params,
                )
            else:
                cursor.execute(f"SELECT {select_list} FROM `{TABLE}` ORDER BY `{col_names[0]}` ASC")
        else:
            cursor.execute(f"SELECT {select_list} FROM `{TABLE}` ORDER BY `{col_names[0]}` ASC")
        rows = cursor.fetchall()

    plugins: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = {}
        for name in col_names:
            item[name] = _serialize_value(row.get(name), is_blob=name in blob_names)
        plugins.append(item)
    return col_names, plugins


def get_plugin(conn, plugin_id: Any, id_column: Optional[str] = None) -> Optional[Dict[str, Any]]:
    cols = _table_columns(conn)
    if not cols:
        return None
    col_names = [c["name"] for c in cols]
    blob_names = {c["name"] for c in cols if c["is_blob"]}
    pk = id_column or next((c["name"] for c in cols if c["name"].lower() == "id"), col_names[0])
    select_list = ", ".join(f"`{n}`" for n in col_names)

    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT {select_list} FROM `{TABLE}` WHERE `{pk}` = %s",
            (plugin_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None

    item: Dict[str, Any] = {}
    for name in col_names:
        # Full content for get (no truncation for text blobs)
        value = row.get(name)
        if name in blob_names and isinstance(value, (bytes, bytearray, memoryview)):
            item[name] = bytes(value).decode("utf-8", errors="replace")
        else:
            item[name] = _serialize_value(value, is_blob=False)
    return item
