"""
MySQL helpers for GMM data definitions stored in `gmm_data_definition`.

Column layout is discovered via SHOW COLUMNS.

Typical schema (non-auto PK):
  id BIGINT UNSIGNED PK
  name, usercomment VARCHAR(255)
  encodingdata BLOB
  hash VARCHAR(32)  — MD5 of encodingdata, server-computed
  last_mod TIMESTAMP
"""
import hashlib
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

TABLE = "gmm_data_definition"
BLOB_PREVIEW_CHARS = 120

_AUTO_TIMESTAMP_NAMES = {
    "last_mod",
    "last_update",
    "updated_at",
    "modified_at",
    "update_time",
    "created_at",
}

_HASH_SOURCE_FIELDS = (
    "encodingdata",
    "encoding_data",
    "definition",
    "data",
    "content",
    "payload",
)

_SERVER_ONLY_FIELDS = {"hash"} | _AUTO_TIMESTAMP_NAMES


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
        lower = str(field).lower()
        cols.append(
            {
                "name": field,
                "type": col_type,
                "nullable": nullable,
                "key": key,
                "default": default,
                "extra": extra,
                "is_pk": key == "PRI" or lower == "id",
                "is_auto": "auto_increment" in extra,
                "is_blob": any(
                    t in col_type
                    for t in ("blob", "binary", "varbinary", "text", "longtext", "mediumtext")
                ),
                "is_numeric": any(
                    t in col_type
                    for t in ("int", "decimal", "numeric", "float", "double", "bigint", "smallint", "tinyint")
                ),
                "is_server_only": lower in _SERVER_ONLY_FIELDS,
            }
        )
    return cols


def _pk_name(cols: List[Dict[str, Any]]) -> str:
    return next((c["name"] for c in cols if c.get("is_pk")), cols[0]["name"])


def _serialize_value(value: Any, *, preview: bool = False) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        text = bytes(value).decode("utf-8", errors="replace")
        if preview and len(text) > BLOB_PREVIEW_CHARS:
            return text[:BLOB_PREVIEW_CHARS] + f"… ({len(bytes(value))} bytes)"
        return text
    if preview and isinstance(value, str) and len(value) > BLOB_PREVIEW_CHARS:
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
            "is_server_only": c.get("is_server_only", False),
        }
        for c in _table_columns(conn)
    ]


def list_definitions(conn, search: str = "") -> Tuple[List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
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

    definitions: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = {}
        for name in col_names:
            item[name] = _serialize_value(row.get(name), preview=name in blob_names)
        definitions.append(item)
    return col_names, definitions, columns_meta(conn)


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

    return {name: _serialize_value(row.get(name), preview=False) for name in col_names}


def _resolve_hash_source(fields: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Optional[str]:
    lower_fields = {str(k).lower(): v for k, v in (fields or {}).items()}
    lower_existing = {str(k).lower(): v for k, v in (existing or {}).items()}
    for name in _HASH_SOURCE_FIELDS:
        if name in lower_fields and lower_fields[name] is not None:
            return str(lower_fields[name])
        if name in lower_existing and lower_existing[name] is not None:
            return str(lower_existing[name])
    return None


def create_definition(conn, fields: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    cols = _table_columns(conn)
    if not cols:
        return False, {"error": f"Table `{TABLE}` has no columns"}

    pk = _pk_name(cols)
    by_name = {c["name"]: c for c in cols}
    by_lower = {c["name"].lower(): c for c in cols}
    fields = fields or {}

    resolved: Dict[str, Any] = {}
    for key, value in fields.items():
        col = by_name.get(key) or by_lower.get(str(key).lower())
        if not col:
            continue
        lower = col["name"].lower()
        if lower in _SERVER_ONLY_FIELDS:
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
        if get_definition(conn, resolved[pk]):
            return False, {"error": f"Data definition id {resolved[pk]} already exists"}

    for col in cols:
        name = col["name"]
        lower = name.lower()
        if name in resolved or col.get("is_auto") or lower in _AUTO_TIMESTAMP_NAMES:
            continue
        if lower == "hash":
            continue
        if not col.get("nullable") and col.get("default") is None:
            resolved[name] = _empty_default(col)

    hash_col = by_lower.get("hash")
    if hash_col:
        source = _resolve_hash_source(resolved, None) or ""
        resolved[hash_col["name"]] = checksum_content(source)

    insert_cols = list(resolved.keys())
    if not insert_cols:
        return False, {"error": "No fields provided"}

    # Touch last_mod via DEFAULT / CURRENT_TIMESTAMP if present and omitted
    for c in cols:
        if c["name"].lower() in _AUTO_TIMESTAMP_NAMES and c["name"] not in resolved:
            # let MySQL default handle it if possible; otherwise set explicitly
            if c.get("default") is None:
                insert_cols.append(c["name"])
                resolved[c["name"]] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    placeholders = ", ".join(["%s"] * len(insert_cols))
    col_sql = ", ".join(f"`{n}`" for n in insert_cols)
    params = [_encode_for_column(resolved[n], by_name[n]) for n in insert_cols]

    with conn.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO `{TABLE}` ({col_sql}) VALUES ({placeholders})",
            params,
        )
        new_id = cursor.lastrowid if pk_col.get("is_auto") else resolved[pk]
    conn.commit()
    saved = get_definition(conn, int(new_id))
    return True, {"definition": saved}


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
        if lower_name == "hash":
            continue
        set_parts.append(f"`{col['name']}` = %s")
        if value is None and not col.get("nullable") and not col.get("is_numeric"):
            value = ""
        params.append(_encode_for_column(value, col))
        touched_lower.add(lower_name)

    hash_col = by_lower.get("hash")
    if hash_col:
        source = _resolve_hash_source(fields, existing)
        if source is not None:
            set_parts.append(f"`{hash_col['name']}` = %s")
            params.append(checksum_content(source))
            touched_lower.add("hash")

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


def delete_definition(conn, definition_id: int) -> Tuple[bool, Dict[str, Any]]:
    cols = _table_columns(conn)
    if not cols:
        return False, {"error": f"Table `{TABLE}` has no columns"}
    pk = _pk_name(cols)
    existing = get_definition(conn, int(definition_id))
    if not existing:
        return False, {"error": f"Data definition id {definition_id} not found"}

    with conn.cursor() as cursor:
        cursor.execute(f"DELETE FROM `{TABLE}` WHERE `{pk}` = %s", (int(definition_id),))
    conn.commit()
    return True, {"deleted": True, "id": int(definition_id), "definition": existing}
