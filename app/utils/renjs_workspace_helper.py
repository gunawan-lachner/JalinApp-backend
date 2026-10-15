"""
Persistence helpers for RENJS named workspaces.
"""
import os
import sqlite3

DATABASE_FILE = os.path.join('instance', 'myapp_db.db')


def _ensure_db_dir():
    db_dir = os.path.dirname(DATABASE_FILE)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)


def init_renjs_workspace_db():
    """Create renjs_workspaces table if it does not exist."""
    conn = None
    try:
        _ensure_db_dir()
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS renjs_workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                path TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    except Exception as e:
        print(f"Error initializing RENJS workspace table: {e}")
    finally:
        if conn:
            conn.close()


def list_workspaces():
    init_renjs_workspace_db()
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, path, description, created_at, updated_at
            FROM renjs_workspaces
            ORDER BY name COLLATE NOCASE
        ''')
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error listing RENJS workspaces: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_workspace(workspace_id: int):
    init_renjs_workspace_db()
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, path, description, created_at, updated_at
            FROM renjs_workspaces
            WHERE id = ?
        ''', (workspace_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"Error getting RENJS workspace {workspace_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()


def save_workspace(data: dict):
    """Create or update a workspace. data: {id?, name, path, description?}"""
    init_renjs_workspace_db()
    name = (data.get('name') or '').strip()
    path = (data.get('path') or '').strip()
    description = (data.get('description') or '').strip() or None
    workspace_id = data.get('id')

    if not name:
        return {'success': False, 'error': 'Name is required'}
    if not path:
        return {'success': False, 'error': 'Path is required'}

    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        if workspace_id:
            cursor.execute('''
                UPDATE renjs_workspaces
                SET name = ?, path = ?, description = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (name, path, description, workspace_id))
            if cursor.rowcount == 0:
                return {'success': False, 'error': f'Workspace {workspace_id} not found'}
            conn.commit()
            return {'success': True, 'id': workspace_id}

        cursor.execute('''
            INSERT INTO renjs_workspaces (name, path, description)
            VALUES (?, ?, ?)
        ''', (name, path, description))
        new_id = cursor.lastrowid
        conn.commit()
        return {'success': True, 'id': new_id}
    except sqlite3.IntegrityError:
        return {'success': False, 'error': f'Workspace name "{name}" already exists'}
    except Exception as e:
        print(f"Error saving RENJS workspace: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        if conn:
            conn.close()


def delete_workspace(workspace_id: int):
    init_renjs_workspace_db()
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM renjs_workspaces WHERE id = ?', (workspace_id,))
        if cursor.rowcount == 0:
            return {'success': False, 'error': f'Workspace {workspace_id} not found'}
        conn.commit()
        return {'success': True}
    except Exception as e:
        print(f"Error deleting RENJS workspace {workspace_id}: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        if conn:
            conn.close()
