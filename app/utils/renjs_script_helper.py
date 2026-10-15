"""
MySQL helpers for RenJS scripts stored in existing `scripts` table (script LONGBLOB).
"""
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pymysql

SCRIPT_TABLE = "scripts"
HISTORY_TABLE = "scripts_history"


def checksum_content(content: str) -> str:
    return hashlib.md5((content or "").encode("utf-8")).hexdigest()


def _decode_script_blob(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _encode_script_blob(content: str) -> bytes:
    return (content or "").encode("utf-8")


def _table_columns(conn, table: str) -> Dict[str, str]:
    with conn.cursor() as cursor:
        cursor.execute(f"SHOW COLUMNS FROM `{table}`")
        rows = cursor.fetchall()
    cols: Dict[str, str] = {}
    for row in rows:
        if isinstance(row, dict):
            cols[row["Field"].lower()] = row["Field"]
        else:
            cols[row[0].lower()] = row[0]
    return cols


def _pick_column(cols: Dict[str, str], candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c.lower() in cols:
            return cols[c.lower()]
    return None


class ScriptsSchema:
    def __init__(self, cols: Dict[str, str]):
        self.id = _pick_column(cols, ["id"])
        self.group = _pick_column(cols, ["group", "group_name", "script_group", "grp"])
        self.name = _pick_column(cols, ["name", "script_name"])
        self.script = _pick_column(cols, ["script"])
        self.checksum = _pick_column(cols, ["checksum", "md5", "checksum_md5"])
        self.comment = _pick_column(cols, ["comment", "comments", "description"])
        self.last_update = _pick_column(
            cols, ["last_update", "updated_at", "last_modified", "modified_at", "update_time"]
        )

    def validate(self) -> Optional[str]:
        if not self.id or not self.script or not self.name:
            return "Table scripts must contain columns: id, name (or script_name), script (LONGBLOB)"
        return None


def _get_schema(conn) -> Tuple[Optional[ScriptsSchema], Optional[str]]:
    try:
        cols = _table_columns(conn, SCRIPT_TABLE)
    except Exception as e:
        return None, f"Table `{SCRIPT_TABLE}` not found or inaccessible: {e}"
    schema = ScriptsSchema(cols)
    err = schema.validate()
    if err:
        return None, err
    return schema, None


def _normalize_row(row: Dict[str, Any], schema: ScriptsSchema) -> Dict[str, Any]:
    out = {
        "id": row.get(schema.id),
        "group_name": row.get(schema.group) if schema.group else "",
        "name": row.get(schema.name) if schema.name else "",
        "checksum": row.get(schema.checksum) if schema.checksum else None,
        "comment": row.get(schema.comment) if schema.comment else None,
        "last_update": row.get(schema.last_update) if schema.last_update else None,
    }
    if out["last_update"] and isinstance(out["last_update"], datetime):
        out["last_update"] = out["last_update"].strftime("%Y-%m-%d %H:%M:%S")
    if not out["checksum"] and schema.script and schema.script in row:
        out["checksum"] = checksum_content(_decode_script_blob(row.get(schema.script)))
    return out


def _ensure_history_table(conn) -> bool:
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS `{HISTORY_TABLE}` (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    script_id INT NOT NULL,
                    version INT NOT NULL DEFAULT 1,
                    script LONGBLOB,
                    checksum VARCHAR(64),
                    comment VARCHAR(500) NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_scripts_history_script_id (script_id),
                    UNIQUE KEY uq_scripts_history_script_version (script_id, version)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            # Migrate older tables that were created without version
            cursor.execute(f"SHOW COLUMNS FROM `{HISTORY_TABLE}` LIKE 'version'")
            if not cursor.fetchone():
                cursor.execute(
                    f"ALTER TABLE `{HISTORY_TABLE}` ADD COLUMN version INT NOT NULL DEFAULT 0 AFTER script_id"
                )
                # Backfill version per script_id (MySQL 5.7+ compatible)
                cursor.execute("SET @rn := 0, @sid := NULL")
                cursor.execute(
                    f"""
                    UPDATE `{HISTORY_TABLE}` h
                    JOIN (
                        SELECT id,
                               (@rn := IF(@sid = script_id, @rn + 1, 1)) AS rn,
                               (@sid := script_id) AS _
                        FROM `{HISTORY_TABLE}`
                        ORDER BY script_id ASC, id ASC
                    ) x ON h.id = x.id
                    SET h.version = x.rn
                    """
                )
                try:
                    cursor.execute(
                        f"ALTER TABLE `{HISTORY_TABLE}` ADD UNIQUE KEY uq_scripts_history_script_version (script_id, version)"
                    )
                except Exception:
                    pass
        conn.commit()
        return True
    except Exception:
        return False


def _next_history_version(cursor, script_id: int) -> int:
    cursor.execute(
        f"SELECT COALESCE(MAX(version), 0) + 1 AS next_ver FROM `{HISTORY_TABLE}` WHERE script_id = %s",
        (script_id,),
    )
    row = cursor.fetchone()
    if not row:
        return 1
    if isinstance(row, dict):
        return int(row.get("next_ver") or 1)
    return int(row[0] or 1)


def list_scripts(conn, search: str = "") -> List[Dict[str, Any]]:
    schema, err = _get_schema(conn)
    if err:
        raise RuntimeError(err)

    select_cols = [f"`{schema.id}` AS `{schema.id}`"]
    if schema.group:
        select_cols.append(f"`{schema.group}` AS `{schema.group}`")
    if schema.name:
        select_cols.append(f"`{schema.name}` AS `{schema.name}`")
    if schema.checksum:
        select_cols.append(f"`{schema.checksum}` AS `{schema.checksum}`")
    if schema.comment:
        select_cols.append(f"`{schema.comment}` AS `{schema.comment}`")
    if schema.last_update:
        select_cols.append(f"`{schema.last_update}` AS `{schema.last_update}`")

    search = (search or "").strip()
    with conn.cursor() as cursor:
        if search:
            like = f"%{search}%"
            where_parts = [f"CAST(`{schema.id}` AS CHAR) LIKE %s"]
            params: List[Any] = [like]
            if schema.group:
                where_parts.append(f"`{schema.group}` LIKE %s")
                params.append(like)
            if schema.name:
                where_parts.append(f"`{schema.name}` LIKE %s")
                params.append(like)
            if schema.checksum:
                where_parts.append(f"`{schema.checksum}` LIKE %s")
                params.append(like)
            if schema.comment:
                where_parts.append(f"`{schema.comment}` LIKE %s")
                params.append(like)
            order = f"`{schema.last_update}` DESC" if schema.last_update else f"`{schema.id}` DESC"
            cursor.execute(
                f"SELECT {', '.join(select_cols)} FROM `{SCRIPT_TABLE}` WHERE "
                + " OR ".join(where_parts)
                + f" ORDER BY {order}",
                params,
            )
        else:
            order = f"`{schema.last_update}` DESC" if schema.last_update else f"`{schema.id}` DESC"
            cursor.execute(
                f"SELECT {', '.join(select_cols)} FROM `{SCRIPT_TABLE}` ORDER BY {order}"
            )
        rows = cursor.fetchall()

    return [_normalize_row(row, schema) for row in rows]


def get_script(conn, script_id: int) -> Optional[Dict[str, Any]]:
    schema, err = _get_schema(conn)
    if err:
        raise RuntimeError(err)

    select_cols = [f"`{schema.id}` AS `{schema.id}`", f"`{schema.script}` AS `{schema.script}`"]
    if schema.group:
        select_cols.append(f"`{schema.group}` AS `{schema.group}`")
    if schema.name:
        select_cols.append(f"`{schema.name}` AS `{schema.name}`")
    if schema.checksum:
        select_cols.append(f"`{schema.checksum}` AS `{schema.checksum}`")
    if schema.comment:
        select_cols.append(f"`{schema.comment}` AS `{schema.comment}`")
    if schema.last_update:
        select_cols.append(f"`{schema.last_update}` AS `{schema.last_update}`")

    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(select_cols)} FROM `{SCRIPT_TABLE}` WHERE `{schema.id}` = %s",
            (script_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None

    normalized = _normalize_row(row, schema)
    normalized["content"] = _decode_script_blob(row.get(schema.script))
    return normalized


def list_history(conn, script_id: int) -> List[Dict[str, Any]]:
    if not _ensure_history_table(conn):
        return []
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, script_id, version, checksum, comment, created_at,
                   OCTET_LENGTH(IFNULL(script, '')) AS content_length
            FROM `{HISTORY_TABLE}`
            WHERE script_id = %s
            ORDER BY version DESC, id DESC
            LIMIT 100
            """,
            (script_id,),
        )
        rows = cursor.fetchall()
    for row in rows:
        if isinstance(row.get("created_at"), datetime):
            row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        if row.get("version") is None:
            row["version"] = row.get("id")
    return rows


def get_history_entry(conn, history_id: int) -> Optional[Dict[str, Any]]:
    if not _ensure_history_table(conn):
        return None
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, script_id, version, script, checksum, comment, created_at
            FROM `{HISTORY_TABLE}`
            WHERE id = %s
            """,
            (history_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None
    row["content"] = _decode_script_blob(row.pop("script", None))
    if isinstance(row.get("created_at"), datetime):
        row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    if row.get("version") is None:
        row["version"] = row.get("id")
    return row


def delete_history_entry(conn, history_id: int) -> Tuple[bool, Dict[str, Any]]:
    if not _ensure_history_table(conn):
        return False, {"error": "History table unavailable"}
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `{HISTORY_TABLE}` WHERE id = %s",
            (history_id,),
        )
        deleted = cursor.rowcount
    conn.commit()
    if deleted == 0:
        return False, {"error": "History entry not found"}
    return True, {"id": history_id, "deleted": deleted}


def clear_history(conn, script_id: int) -> Tuple[bool, Dict[str, Any]]:
    if not _ensure_history_table(conn):
        return False, {"error": "History table unavailable"}
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `{HISTORY_TABLE}` WHERE script_id = %s",
            (script_id,),
        )
        deleted = cursor.rowcount
    conn.commit()
    return True, {"script_id": script_id, "deleted": deleted}


def _insert_history(cursor, script_id: int, content: str, checksum: str, comment: Optional[str]):
    version = _next_history_version(cursor, script_id)
    cursor.execute(
        f"""
        INSERT INTO `{HISTORY_TABLE}` (script_id, version, script, checksum, comment)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (script_id, version, _encode_script_blob(content), checksum, comment),
    )
    return version


def save_script(
    conn,
    *,
    mode: str,
    content: str,
    comment: Optional[str] = None,
    script_id: Optional[int] = None,
    group_name: Optional[str] = None,
    name: Optional[str] = None,
) -> Tuple[bool, Dict[str, Any]]:
    schema, err = _get_schema(conn)
    if err:
        return False, {"error": err}

    content = content if content is not None else ""
    comment = (comment or "").strip() or None
    checksum = checksum_content(content)
    mode = (mode or "").strip().lower()
    blob = _encode_script_blob(content)

    if mode == "overwrite":
        if not script_id:
            return False, {"error": "script id is required for overwrite mode"}
        existing = get_script(conn, int(script_id))
        if not existing:
            return False, {"error": f"Script id {script_id} not found"}

        with conn.cursor() as cursor:
            if _ensure_history_table(conn):
                _insert_history(
                    cursor,
                    int(script_id),
                    existing.get("content") or "",
                    existing.get("checksum") or checksum_content(existing.get("content") or ""),
                    existing.get("comment"),
                )

            set_parts = [f"`{schema.script}` = %s"]
            params: List[Any] = [blob]
            if schema.checksum:
                set_parts.append(f"`{schema.checksum}` = %s")
                params.append(checksum)
            if schema.comment:
                set_parts.append(f"`{schema.comment}` = %s")
                params.append(comment)
            if schema.last_update:
                set_parts.append(f"`{schema.last_update}` = NOW()")
            params.append(int(script_id))
            cursor.execute(
                f"UPDATE `{SCRIPT_TABLE}` SET {', '.join(set_parts)} WHERE `{schema.id}` = %s",
                params,
            )
        conn.commit()
        saved = get_script(conn, int(script_id))
        return True, {"script": saved, "mode": "overwrite"}

    if mode == "new":
        name = (name or "").strip() or "main"
        group_name = (group_name or "").strip() or None

        cols = [f"`{schema.name}`", f"`{schema.script}`"]
        val_tokens = ["%s", "%s"]
        vals: List[Any] = [name, blob]
        if schema.group and group_name:
            cols.insert(0, f"`{schema.group}`")
            val_tokens.insert(0, "%s")
            vals.insert(0, group_name)
        if schema.checksum:
            cols.append(f"`{schema.checksum}`")
            val_tokens.append("%s")
            vals.append(checksum)
        if schema.comment:
            cols.append(f"`{schema.comment}`")
            val_tokens.append("%s")
            vals.append(comment)
        if schema.last_update:
            cols.append(f"`{schema.last_update}`")
            val_tokens.append("NOW()")

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO `{SCRIPT_TABLE}` ({', '.join(cols)}) VALUES ({', '.join(val_tokens)})",
                    vals,
                )
                new_id = cursor.lastrowid
            conn.commit()
            saved = get_script(conn, new_id)
            return True, {"script": saved, "mode": "new"}
        except pymysql.err.IntegrityError as e:
            return False, {"error": f"Failed to insert script (duplicate or constraint): {e}"}

    return False, {"error": "mode must be 'overwrite' or 'new'"}
