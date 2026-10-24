"""Seed Container Plugin menu under IDE."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "instance" / "myapp_db.db"


def main() -> None:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(page)").fetchall()]
    if "parent_id" not in cols:
        cur.execute("ALTER TABLE page ADD COLUMN parent_id INTEGER")

    cur.execute(
        "SELECT id_page FROM page WHERE path = ? OR lower(name) = ?",
        ("/ide", "ide"),
    )
    ide = cur.fetchone()
    if not ide:
        print("IDE menu not found. Run seed_ide_parent.py first.")
        conn.close()
        return
    ide_id = ide[0]

    cur.execute(
        "SELECT id_page, parent_id FROM page WHERE path = ? OR lower(name) = ?",
        ("/container-plugin", "container plugin"),
    )
    row = cur.fetchone()
    if row:
        cp_id = row[0]
        if row[1] != ide_id:
            cur.execute(
                "UPDATE page SET parent_id = ?, icon = COALESCE(icon, ?) WHERE id_page = ?",
                (ide_id, "FiCpu", cp_id),
            )
            print(f"Updated Container Plugin parent -> IDE (id={cp_id})")
        else:
            print(f"Container Plugin already under IDE (id={cp_id})")
    else:
        cur.execute(
            "INSERT INTO page (name, path, page_order, icon, parent_id) VALUES (?, ?, ?, ?, ?)",
            ("Container Plugin", "/container-plugin", 3, "FiCpu", ide_id),
        )
        cp_id = cur.lastrowid
        print(f"Created Container Plugin id={cp_id}")

        cur.execute("SELECT id_role FROM page_role WHERE id_page = ?", (ide_id,))
        for (role_id,) in cur.fetchall():
            cur.execute(
                "INSERT OR IGNORE INTO page_role (id_page, id_role) VALUES (?, ?)",
                (cp_id, role_id),
            )
        cur.execute(
            "INSERT OR IGNORE INTO page_role (id_page, id_role) VALUES (?, 1)",
            (cp_id,),
        )

    conn.commit()
    print("pages under IDE:")
    for r in cur.execute(
        """
        SELECT id_page, name, path, page_order, parent_id
        FROM page
        WHERE id_page = ? OR parent_id = ?
        ORDER BY page_order, id_page
        """,
        (ide_id, ide_id),
    ).fetchall():
        print(r)
    conn.close()


if __name__ == "__main__":
    main()
