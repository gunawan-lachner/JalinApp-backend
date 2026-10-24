"""Seed IDE parent menu and nest RENJS under it."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "instance" / "myapp_db.db"


def main() -> None:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(page)").fetchall()]
    if "parent_id" not in cols:
        cur.execute("ALTER TABLE page ADD COLUMN parent_id INTEGER")
        print("added parent_id column")

    cur.execute("SELECT id_page FROM page WHERE path = ? OR lower(name) = ?", ("/ide", "ide"))
    row = cur.fetchone()
    if row:
        ide_id = row[0]
        print(f"IDE already exists: id={ide_id}")
    else:
        cur.execute(
            "INSERT INTO page (name, path, page_order, icon, parent_id) VALUES (?, ?, ?, ?, NULL)",
            ("IDE", "/ide", 0, "FiCode"),
        )
        ide_id = cur.lastrowid
        print(f"created IDE: id={ide_id}")

        # Copy roles from RENJS if present, otherwise assign admin
        renjs = cur.execute(
            "SELECT id_page FROM page WHERE path = ? OR lower(name) = ?",
            ("/renjs", "renjs"),
        ).fetchone()
        if renjs:
            for (role_id,) in cur.execute(
                "SELECT id_role FROM page_role WHERE id_page = ?",
                (renjs[0],),
            ).fetchall():
                cur.execute(
                    "INSERT OR IGNORE INTO page_role (id_page, id_role) VALUES (?, ?)",
                    (ide_id, role_id),
                )
        cur.execute(
            "INSERT OR IGNORE INTO page_role (id_page, id_role) VALUES (?, 1)",
            (ide_id,),
        )

    renjs = cur.execute(
        "SELECT id_page FROM page WHERE path = ? OR lower(name) = ?",
        ("/renjs", "renjs"),
    ).fetchone()
    if not renjs:
        print("RENJS page not found; skipping parent link")
    else:
        cur.execute(
            "UPDATE page SET parent_id = ? WHERE id_page = ?",
            (ide_id, renjs[0]),
        )
        print(f"RENJS (id={renjs[0]}) parent_id set to IDE (id={ide_id})")

    conn.commit()
    print("pages:")
    for r in cur.execute(
        "SELECT id_page, name, path, page_order, icon, parent_id FROM page ORDER BY page_order, id_page"
    ).fetchall():
        print(r)
    conn.close()


if __name__ == "__main__":
    main()
