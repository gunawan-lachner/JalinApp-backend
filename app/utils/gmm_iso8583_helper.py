"""
MySQL helpers for GMM ISO8583 plugins stored in `gmm_iso8583_plugin`.

Column layout is discovered via SHOW COLUMNS so the UI stays compatible
if the remote schema varies slightly.

Typical schema (non-auto PK):
  id BIGINT UNSIGNED PK
  container_plugin_name, msg_direction, iso_mti, message_answered
  xslt_mapping_id, data_definition_id
  pre_trans_functor, post_trans_functor, library_name, extern_xslt_functions
  iso_proccode (nullable)
"""
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

TABLE = "gmm_iso8583_plugin"
BLOB_PREVIEW_CHARS = 120
_AUTO_TIMESTAMP_NAMES = {
    "last_mod",
    "last_update",
    "updated_at",
    "modified_at",
    "update_time",
    "created_at",
}


def _table_columns(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(f"SHOW COLUMNS FROM `{TABLE}`")
        rows = cursor.fetchall()
    cols: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            field = row.get("Field") or row.get("field")
            col_type = (row.get("Type") or row.get("type") or "").lower()
            nullable = str(row.get("Null") or row.get("null") or "").upper() == "YES"
            key = (row.get("Key") or row.get("key") or "").upper()
            default = row.get("Default") if "Default" in row else row.get("default")
            extra = (row.get("Extra") or row.get("extra") or "").lower()
        else:
            field = row[0]
            col_type = (row[1] or "").lower()
            nullable = str(row[2] or "").upper() == "YES"
            key = str(row[3] or "").upper()
            default = row[4] if len(row) > 4 else None
            extra = str(row[5] or "").lower() if len(row) > 5 else ""
        cols.append(
            {
                "name": field,
                "type": col_type,
                "nullable": nullable,
                "key": key,
                "default": default,
                "extra": extra,
                "is_pk": key == "PRI" or str(field).lower() == "id",
                "is_auto": "auto_increment" in extra,
                "is_blob": any(t in col_type for t in ("blob", "binary", "varbinary")),
                "is_numeric": any(
                    t in col_type
                    for t in ("int", "decimal", "numeric", "float", "double", "bigint", "smallint", "tinyint")
                ),
            }
        )
    return cols


def _pk_name(cols: List[Dict[str, Any]]) -> str:
    return next((c["name"] for c in cols if c.get("is_pk")), cols[0]["name"])


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


def _encode_for_column(value: Any, col: Dict[str, Any]) -> Any:
    if value is None:
        return None
    if col.get("is_blob") and isinstance(value, str):
        col_type = col.get("type") or ""
        if any(t in col_type for t in ("blob", "binary", "varbinary")):
            return value.encode("utf-8")
        return value
    if col.get("is_numeric") and value != "" and not isinstance(value, (int, float)):
        text = str(value).strip()
        if text == "":
            return None if col.get("nullable") else 0
        try:
            if any(t in (col.get("type") or "") for t in ("decimal", "float", "double", "numeric")):
                return float(text)
            return int(text)
        except ValueError:
            return value
    return value


def _empty_default(col: Dict[str, Any]) -> Any:
    if col.get("nullable"):
        return None
    if col.get("is_numeric"):
        return 0
    return ""


def columns_meta(conn) -> List[Dict[str, Any]]:
    return [
        {
            "name": c["name"],
            "type": c["type"],
            "nullable": c["nullable"],
            "is_pk": c["is_pk"],
            "is_auto": c["is_auto"],
            "is_blob": c["is_blob"],
            "is_numeric": c["is_numeric"],
        }
        for c in _table_columns(conn)
    ]


def list_plugins(conn, search: str = "") -> Tuple[List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
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
    return col_names, plugins, columns_meta(conn)


def get_plugin(conn, plugin_id: Any, id_column: Optional[str] = None) -> Optional[Dict[str, Any]]:
    cols = _table_columns(conn)
    if not cols:
        return None
    col_names = [c["name"] for c in cols]
    blob_names = {c["name"] for c in cols if c["is_blob"]}
    pk = id_column or _pk_name(cols)
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
        value = row.get(name)
        if name in blob_names and isinstance(value, (bytes, bytearray, memoryview)):
            item[name] = bytes(value).decode("utf-8", errors="replace")
        else:
            item[name] = _serialize_value(value, is_blob=False)
    return item


def create_plugin(conn, fields: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    cols = _table_columns(conn)
    if not cols:
        return False, {"error": f"Table `{TABLE}` has no columns"}

    pk = _pk_name(cols)
    by_name = {c["name"]: c for c in cols}
    by_lower = {c["name"].lower(): c for c in cols}
    fields = fields or {}

    # Resolve incoming values (case-insensitive keys)
    resolved: Dict[str, Any] = {}
    for key, value in fields.items():
        col = by_name.get(key) or by_lower.get(str(key).lower())
        if not col:
            continue
        if col["name"].lower() in _AUTO_TIMESTAMP_NAMES and col.get("is_auto") is False:
            # still allow explicit timestamp if provided; otherwise skip for insert default
            if value in (None, ""):
                continue
        resolved[col["name"]] = value

    pk_col = by_name[pk]
    if not pk_col.get("is_auto"):
        if pk not in resolved or resolved[pk] in (None, ""):
            return False, {"error": f"Missing required field: {pk}"}
        try:
            resolved[pk] = int(str(resolved[pk]).strip())
        except ValueError:
            return False, {"error": f"Invalid {pk}: must be numeric"}

        existing = get_plugin(conn, resolved[pk], pk)
        if existing:
            return False, {"error": f"Plugin id {resolved[pk]} already exists"}

    # Fill NOT NULL columns that were omitted
    for col in cols:
        name = col["name"]
        if name in resolved:
            continue
        if col.get("is_auto"):
            continue
        if name.lower() in _AUTO_TIMESTAMP_NAMES:
            continue
        if not col.get("nullable") and col.get("default") is None:
            resolved[name] = _empty_default(col)

    insert_cols = list(resolved.keys())
    if not insert_cols:
        return False, {"error": "No fields provided"}

    placeholders = ", ".join(["%s"] * len(insert_cols))
    col_sql = ", ".join(f"`{n}`" for n in insert_cols)
    params = [_encode_for_column(resolved[n], by_name[n]) for n in insert_cols]

    with conn.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO `{TABLE}` ({col_sql}) VALUES ({placeholders})",
            params,
        )
        if pk_col.get("is_auto"):
            new_id = cursor.lastrowid
        else:
            new_id = resolved[pk]
    conn.commit()
    saved = get_plugin(conn, new_id, pk)
    return True, {"plugin": saved}


def update_plugin(conn, plugin_id: Any, fields: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    cols = _table_columns(conn)
    if not cols:
        return False, {"error": f"Table `{TABLE}` has no columns"}

    pk = _pk_name(cols)
    existing = get_plugin(conn, plugin_id, pk)
    if not existing:
        return False, {"error": f"Plugin id {plugin_id} not found"}

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
        set_parts.append(f"`{col['name']}` = %s")
        # NOT NULL string columns: coerce null to empty string
        if value is None and not col.get("nullable") and not col.get("is_numeric"):
            value = ""
        params.append(_encode_for_column(value, col))
        touched_lower.add(lower_name)

    for c in cols:
        if c["name"].lower() in _AUTO_TIMESTAMP_NAMES and c["name"].lower() not in touched_lower:
            set_parts.append(f"`{c['name']}` = CURRENT_TIMESTAMP")

    if not set_parts:
        return False, {"error": "No updatable fields provided"}

    params.append(plugin_id)
    with conn.cursor() as cursor:
        cursor.execute(
            f"UPDATE `{TABLE}` SET {', '.join(set_parts)} WHERE `{pk}` = %s",
            params,
        )
    conn.commit()
    saved = get_plugin(conn, plugin_id, pk)
    return True, {"plugin": saved}


def delete_plugin(conn, plugin_id: Any) -> Tuple[bool, Dict[str, Any]]:
    cols = _table_columns(conn)
    if not cols:
        return False, {"error": f"Table `{TABLE}` has no columns"}
    pk = _pk_name(cols)
    existing = get_plugin(conn, plugin_id, pk)
    if not existing:
        return False, {"error": f"Plugin id {plugin_id} not found"}

    with conn.cursor() as cursor:
        cursor.execute(f"DELETE FROM `{TABLE}` WHERE `{pk}` = %s", (plugin_id,))
    conn.commit()
    return True, {"deleted": True, "id": plugin_id, "plugin": existing}
