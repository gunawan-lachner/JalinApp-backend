"""
MySQL helpers for GMM XSLT mappings stored in `gmm_xslt_mapping`.

Schema:
  id BIGINT(18) UNSIGNED PK
  name VARCHAR(255) NOT NULL
  usercomment VARCHAR(255) NOT NULL
  mappingdata BLOB NOT NULL
  hash VARCHAR(32) NOT NULL
  last_mod TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP

History (auto-created):
  gmm_xslt_mapping_history
"""
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pymysql

TABLE = "gmm_xslt_mapping"
HISTORY_TABLE = "gmm_xslt_mapping_history"


def checksum_content(content: str) -> str:
    return hashlib.md5((content or "").encode("utf-8")).hexdigest()


def _decode_blob(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _encode_blob(content: str) -> bytes:
    return (content or "").encode("utf-8")


def _normalize_row(row: Dict[str, Any], include_content: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": row.get("id"),
        "name": row.get("name") or "",
        "usercomment": row.get("usercomment") or "",
        "hash": row.get("hash") or None,
        "last_mod": row.get("last_mod"),
    }
    if out["last_mod"] and isinstance(out["last_mod"], datetime):
        out["last_mod"] = out["last_mod"].strftime("%Y-%m-%d %H:%M:%S")
    if include_content:
        out["content"] = _decode_blob(row.get("mappingdata"))
        out["mappingdata"] = out["content"]
        if not out["hash"]:
            out["hash"] = checksum_content(out["content"])
    return out


def _ensure_history_table(conn) -> bool:
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS `{HISTORY_TABLE}` (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    mapping_id BIGINT UNSIGNED NOT NULL,
                    version INT NOT NULL DEFAULT 1,
                    mappingdata LONGBLOB,
                    hash VARCHAR(64),
                    usercomment VARCHAR(500) NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_gmm_xslt_history_mapping_id (mapping_id),
                    UNIQUE KEY uq_gmm_xslt_history_mapping_version (mapping_id, version)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        conn.commit()
        return True
    except Exception:
        return False


def _next_history_version(cursor, mapping_id: int) -> int:
    cursor.execute(
        f"SELECT COALESCE(MAX(version), 0) + 1 AS next_ver FROM `{HISTORY_TABLE}` WHERE mapping_id = %s",
        (mapping_id,),
    )
    row = cursor.fetchone()
    if not row:
        return 1
    if isinstance(row, dict):
        return int(row.get("next_ver") or 1)
    return int(row[0] or 1)


def _insert_history(cursor, mapping_id: int, content: str, checksum: str, usercomment: Optional[str]):
    version = _next_history_version(cursor, mapping_id)
    cursor.execute(
        f"""
        INSERT INTO `{HISTORY_TABLE}` (mapping_id, version, mappingdata, hash, usercomment)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (mapping_id, version, _encode_blob(content), checksum, usercomment),
    )
    return version


def list_mappings(conn, search: str = "") -> List[Dict[str, Any]]:
    search = (search or "").strip()
    with conn.cursor() as cursor:
        if search:
            like = f"%{search}%"
            cursor.execute(
                f"""
                SELECT id, name, usercomment, hash, last_mod
                FROM `{TABLE}`
                WHERE CAST(id AS CHAR) LIKE %s
                   OR name LIKE %s
                   OR usercomment LIKE %s
                   OR hash LIKE %s
                ORDER BY last_mod DESC, id DESC
                """,
                (like, like, like, like),
            )
        else:
            cursor.execute(
                f"""
                SELECT id, name, usercomment, hash, last_mod
                FROM `{TABLE}`
                ORDER BY last_mod DESC, id DESC
                """
            )
        rows = cursor.fetchall()
    return [_normalize_row(row) for row in rows]


def get_mapping(conn, mapping_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, name, usercomment, mappingdata, hash, last_mod
            FROM `{TABLE}`
            WHERE id = %s
            """,
            (int(mapping_id),),
        )
        row = cursor.fetchone()
    if not row:
        return None
    return _normalize_row(row, include_content=True)


def list_history(conn, mapping_id: int) -> List[Dict[str, Any]]:
    if not _ensure_history_table(conn):
        return []
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, mapping_id, version, hash, usercomment, created_at,
                   OCTET_LENGTH(IFNULL(mappingdata, '')) AS content_length
            FROM `{HISTORY_TABLE}`
            WHERE mapping_id = %s
            ORDER BY version DESC, id DESC
            LIMIT 100
            """,
            (int(mapping_id),),
        )
        rows = cursor.fetchall()
    for row in rows:
        if isinstance(row.get("created_at"), datetime):
            row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        if row.get("version") is None:
            row["version"] = row.get("id")
        # Alias for UI parity with RENJS
        row["comment"] = row.get("usercomment")
        row["checksum"] = row.get("hash")
        row["script_id"] = row.get("mapping_id")
    return rows


def get_history_entry(conn, history_id: int) -> Optional[Dict[str, Any]]:
    if not _ensure_history_table(conn):
        return None
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, mapping_id, version, mappingdata, hash, usercomment, created_at
            FROM `{HISTORY_TABLE}`
            WHERE id = %s
            """,
            (int(history_id),),
        )
        row = cursor.fetchone()
    if not row:
        return None
    row["content"] = _decode_blob(row.pop("mappingdata", None))
    if isinstance(row.get("created_at"), datetime):
        row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    if row.get("version") is None:
        row["version"] = row.get("id")
    row["comment"] = row.get("usercomment")
    row["checksum"] = row.get("hash")
    row["script_id"] = row.get("mapping_id")
    return row


def delete_history_entry(conn, history_id: int) -> Tuple[bool, Dict[str, Any]]:
    if not _ensure_history_table(conn):
        return False, {"error": "History table unavailable"}
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `{HISTORY_TABLE}` WHERE id = %s",
            (int(history_id),),
        )
        deleted = cursor.rowcount
    conn.commit()
    if deleted == 0:
        return False, {"error": "History entry not found"}
    return True, {"id": history_id, "deleted": deleted}


def clear_history(conn, mapping_id: int) -> Tuple[bool, Dict[str, Any]]:
    if not _ensure_history_table(conn):
        return False, {"error": "History table unavailable"}
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `{HISTORY_TABLE}` WHERE mapping_id = %s",
            (int(mapping_id),),
        )
        deleted = cursor.rowcount
    conn.commit()
    return True, {"mapping_id": mapping_id, "deleted": deleted}


COLUMNS_META: List[Dict[str, Any]] = [
    {
        "name": "id",
        "type": "bigint(18) unsigned",
        "nullable": False,
        "is_pk": True,
        "is_auto": False,
        "is_blob": False,
        "is_numeric": True,
        "is_server_only": False,
    },
    {
        "name": "name",
        "type": "varchar(255)",
        "nullable": False,
        "is_pk": False,
        "is_auto": False,
        "is_blob": False,
        "is_numeric": False,
        "is_server_only": False,
    },
    {
        "name": "usercomment",
        "type": "varchar(255)",
        "nullable": False,
        "is_pk": False,
        "is_auto": False,
        "is_blob": False,
        "is_numeric": False,
        "is_server_only": False,
    },
    {
        "name": "mappingdata",
        "type": "blob",
        "nullable": False,
        "is_pk": False,
        "is_auto": False,
        "is_blob": True,
        "is_numeric": False,
        "is_server_only": False,
    },
    {
        "name": "hash",
        "type": "varchar(32)",
        "nullable": False,
        "is_pk": False,
        "is_auto": False,
        "is_blob": False,
        "is_numeric": False,
        "is_server_only": True,
    },
    {
        "name": "last_mod",
        "type": "timestamp",
        "nullable": False,
        "is_pk": False,
        "is_auto": False,
        "is_blob": False,
        "is_numeric": False,
        "is_server_only": True,
    },
]

COLUMN_NAMES = [c["name"] for c in COLUMNS_META]


def columns_meta() -> List[Dict[str, Any]]:
    return list(COLUMNS_META)


def save_mapping(
    conn,
    *,
    mode: str,
    content: str,
    usercomment: Optional[str] = None,
    mapping_id: Optional[int] = None,
    name: Optional[str] = None,
) -> Tuple[bool, Dict[str, Any]]:
    content = content if content is not None else ""
    comment = (usercomment if usercomment is not None else "").strip()
    # NOT NULL columns — keep empty string rather than NULL
    checksum = checksum_content(content)
    blob = _encode_blob(content)
    mode = (mode or "").strip().lower()

    if mode == "overwrite":
        if mapping_id is None:
            return False, {"error": "id is required for overwrite mode"}
        existing = get_mapping(conn, int(mapping_id))
        if not existing:
            return False, {"error": f"Mapping id {mapping_id} not found"}

        next_name = (name if name is not None else existing.get("name") or "").strip()
        if not next_name:
            next_name = existing.get("name") or ""

        with conn.cursor() as cursor:
            if _ensure_history_table(conn):
                _insert_history(
                    cursor,
                    int(mapping_id),
                    existing.get("content") or "",
                    existing.get("hash") or checksum_content(existing.get("content") or ""),
                    existing.get("usercomment"),
                )

            cursor.execute(
                f"""
                UPDATE `{TABLE}`
                SET name = %s,
                    mappingdata = %s,
                    hash = %s,
                    usercomment = %s,
                    last_mod = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (next_name, blob, checksum, comment, int(mapping_id)),
            )
        conn.commit()
        saved = get_mapping(conn, int(mapping_id))
        return True, {"mapping": saved, "mode": "overwrite"}

    if mode == "new":
        name = (name or "").strip()
        if not name:
            return False, {"error": "name is required for new mode"}
        if mapping_id is None:
            return False, {"error": "id is required for new mode (no auto-increment)"}

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO `{TABLE}`
                        (id, name, usercomment, mappingdata, hash, last_mod)
                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    """,
                    (int(mapping_id), name, comment, blob, checksum),
                )
            conn.commit()
            saved = get_mapping(conn, int(mapping_id))
            return True, {"mapping": saved, "mode": "new"}
        except pymysql.err.IntegrityError as e:
            return False, {"error": f"Failed to insert mapping (duplicate id or constraint): {e}"}

    return False, {"error": "mode must be 'overwrite' or 'new'"}


def update_mapping(
    conn,
    mapping_id: int,
    fields: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    """Update name / usercomment / mappingdata (content). Hash recomputed server-side."""
    existing = get_mapping(conn, int(mapping_id))
    if not existing:
        return False, {"error": f"Mapping id {mapping_id} not found"}

    fields = fields or {}
    lower = {str(k).lower(): v for k, v in fields.items()}

    next_name = existing.get("name") or ""
    if "name" in lower and lower["name"] is not None:
        next_name = str(lower["name"]).strip() or next_name

    next_comment = existing.get("usercomment") or ""
    if "usercomment" in lower and lower["usercomment"] is not None:
        next_comment = str(lower["usercomment"])

    content_changed = False
    next_content = existing.get("content") or ""
    for key in ("mappingdata", "content", "mapping_data"):
        if key in lower and lower[key] is not None:
            next_content = str(lower[key])
            content_changed = True
            break

    checksum = checksum_content(next_content)
    blob = _encode_blob(next_content)

    with conn.cursor() as cursor:
        if content_changed and _ensure_history_table(conn):
            _insert_history(
                cursor,
                int(mapping_id),
                existing.get("content") or "",
                existing.get("hash") or checksum_content(existing.get("content") or ""),
                existing.get("usercomment"),
            )
        cursor.execute(
            f"""
            UPDATE `{TABLE}`
            SET name = %s,
                usercomment = %s,
                mappingdata = %s,
                hash = %s,
                last_mod = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (next_name, next_comment, blob, checksum, int(mapping_id)),
        )
    conn.commit()
    saved = get_mapping(conn, int(mapping_id))
    return True, {"mapping": saved}


def delete_mapping(conn, mapping_id: int) -> Tuple[bool, Dict[str, Any]]:
    existing = get_mapping(conn, int(mapping_id))
    if not existing:
        return False, {"error": f"Mapping id {mapping_id} not found"}

    with conn.cursor() as cursor:
        if _ensure_history_table(conn):
            cursor.execute(
                f"DELETE FROM `{HISTORY_TABLE}` WHERE mapping_id = %s",
                (int(mapping_id),),
            )
        cursor.execute(f"DELETE FROM `{TABLE}` WHERE id = %s", (int(mapping_id),))
    conn.commit()
    return True, {"deleted": True, "id": int(mapping_id), "mapping": existing}
