"""
RENJS API: local file explorer + container_plugin_attr lookup.
"""
import os
import glob
import time
from datetime import datetime

from flask import Blueprint, request, jsonify, current_app
import pymysql

from app.routes.transaction.rna_transactions import load_profiles
from app.utils.renjs_workspace_helper import (
    list_workspaces,
    get_workspace,
    save_workspace,
    delete_workspace,
)
from app.utils.renjs_script_helper import (
    list_scripts as db_list_scripts,
    get_script as db_get_script,
    list_history as db_list_history,
    get_history_entry as db_get_history_entry,
    delete_history_entry as db_delete_history_entry,
    clear_history as db_clear_history,
    save_script as db_save_script,
)

renjs_bp = Blueprint('renjs', __name__)

EDITABLE_EXTENSIONS = {
    '.txt', '.renjs', '.md', '.markdown', '.json',
    '.yml', '.yaml', '.xml', '.xsl', '.xslt', '.conf', '.cfg', '.ini', '.log',
    '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx',
    '.html', '.htm', '.css', '.scss', '.less',
    '.sql', '.py', '.sh', '.bash',
}


def _is_path_allowed(path: str) -> bool:
    """Optional allow-list enforcement for local file APIs."""
    try:
        if not path:
            return False
        roots = current_app.config.get('ALLOWED_LOCAL_ROOTS')
        if not roots:
            return True
        real = os.path.realpath(os.path.expanduser(path))
        for r in roots:
            rr = os.path.realpath(os.path.expanduser(r))
            if real.startswith(rr + os.sep) or real == rr:
                return True
        return False
    except Exception:
        return False


def _is_editable_file(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in EDITABLE_EXTENSIONS


def _get_db_profile(profile_id: str):
    profiles = load_profiles()
    return next((p for p in profiles if p.get('name') == profile_id), None)


def _connect_profile(profile: dict):
    return pymysql.connect(
        host=profile['host'],
        port=int(profile['port']) if profile.get('port') else 3306,
        user=profile['user'],
        password=profile['password'],
        database=profile['database'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )


@renjs_bp.route('/files/default-path', methods=['GET'])
def get_default_path():
    """Return configured default path for RENJS explorer."""
    default_path = os.environ.get('RENJS_DEFAULT_PATH', '')
    return jsonify({
        'success': True,
        'data': {'path': default_path},
        'error': None,
    })


@renjs_bp.route('/files/roots', methods=['GET'])
def list_roots():
    """List browseable root directories on the backend host."""
    try:
        t0 = time.time()
        roots = []
        seen = set()

        def add_root(path, label=None):
            try:
                if not path:
                    return
                expanded = os.path.expanduser(path)
                if not os.path.isdir(expanded):
                    return
                if not _is_path_allowed(expanded):
                    return
                real = os.path.realpath(expanded)
                if real in seen:
                    return
                seen.add(real)
                roots.append({
                    'name': label or os.path.basename(real.rstrip('\\/')) or real,
                    'path': real,
                    'is_dir': True,
                })
            except Exception:
                return

        # Configured allow-list roots take priority when present
        allowed = current_app.config.get('ALLOWED_LOCAL_ROOTS') or []
        for r in allowed:
            add_root(r)

        default_path = os.environ.get('RENJS_DEFAULT_PATH', '')
        if default_path:
            add_root(default_path, label='RENJS Default')

        add_root(os.path.expanduser('~'), label='Home')
        add_root(os.getcwd(), label='App Working Dir')

        if os.name == 'nt':
            import string
            for letter in string.ascii_uppercase:
                drive = f'{letter}:\\'
                if os.path.isdir(drive):
                    add_root(drive, label=f'Drive {letter}:')
        else:
            add_root('/', label='Root /')
            add_root('/home', label='Home Dir')
            add_root('/tmp', label='Temp')
            add_root('/app', label='App')

        return jsonify({
            'success': True,
            'data': {'roots': roots},
            'execution_time': round(time.time() - t0, 6),
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/files/list', methods=['POST'])
def list_files():
    """List local files under a directory. Body: { path, pattern?, dirs_only? }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    target = data.get('path')
    pattern = data.get('pattern', '*')
    dirs_only = bool(data.get('dirs_only', False))
    try:
        t0 = time.time()
        if not target:
            return jsonify({'success': False, 'error': 'Missing required field: path'}), 400
        target = os.path.expanduser(target)
        if not _is_path_allowed(target):
            return jsonify({'success': False, 'error': 'Path not allowed'}), 403
        if not os.path.isdir(target):
            return jsonify({'success': False, 'error': 'Not a directory'}), 400

        entries = []
        for p in glob.glob(os.path.join(target, pattern)):
            try:
                st = os.stat(p)
                is_dir = os.path.isdir(p)
                name = os.path.basename(p)
                if dirs_only and not is_dir:
                    continue
                if not is_dir and not _is_editable_file(p):
                    continue
                entries.append({
                    'name': name,
                    'path': os.path.realpath(p),
                    'is_dir': is_dir,
                    'size': st.st_size,
                    'modified_at': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'editable': (not is_dir) and _is_editable_file(p),
                })
            except Exception:
                continue

        entries.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        return jsonify({
            'success': True,
            'data': {
                'path': os.path.realpath(target),
                'entries': entries,
            },
            'execution_time': round(time.time() - t0, 6),
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/files/read', methods=['POST'])
def read_file():
    """Read local text file. Body: { path }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    path = data.get('path')
    try:
        t0 = time.time()
        if not path:
            return jsonify({'success': False, 'error': 'Missing required field: path'}), 400
        path = os.path.expanduser(path)
        if not _is_path_allowed(path):
            return jsonify({'success': False, 'error': 'Path not allowed'}), 403
        if not os.path.isfile(path):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        if not _is_editable_file(path):
            return jsonify({'success': False, 'error': 'File type not editable'}), 400

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        st = os.stat(path)
        return jsonify({
            'success': True,
            'data': {
                'path': os.path.realpath(path),
                'name': os.path.basename(path),
                'content': content,
                'size': st.st_size,
                'mtime': st.st_mtime,
                'modified_at': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            },
            'execution_time': round(time.time() - t0, 6),
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/files/write', methods=['POST'])
def write_file():
    """Write local text file. Body: { path, content }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    path = data.get('path')
    content = data.get('content')
    try:
        t0 = time.time()
        if not path:
            return jsonify({'success': False, 'error': 'Missing required field: path'}), 400
        if content is None:
            return jsonify({'success': False, 'error': 'Missing required field: content'}), 400
        path = os.path.expanduser(path)
        if not _is_path_allowed(path):
            return jsonify({'success': False, 'error': 'Path not allowed'}), 403
        if not _is_editable_file(path):
            return jsonify({'success': False, 'error': 'File type not editable'}), 400

        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            return jsonify({'success': False, 'error': 'Parent directory does not exist'}), 400

        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(content if isinstance(content, str) else str(content))

        st = os.stat(path)
        return jsonify({
            'success': True,
            'data': {
                'path': os.path.realpath(path),
                'name': os.path.basename(path),
                'size': st.st_size,
                'mtime': st.st_mtime,
                'modified_at': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            },
            'execution_time': round(time.time() - t0, 6),
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/files/stat', methods=['POST'])
def stat_file():
    """Return mtime/size without reading content. Body: { path }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    path = data.get('path')
    try:
        if not path:
            return jsonify({'success': False, 'error': 'Missing required field: path'}), 400
        path = os.path.expanduser(path)
        if not _is_path_allowed(path):
            return jsonify({'success': False, 'error': 'Path not allowed'}), 403
        if not os.path.isfile(path):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        st = os.stat(path)
        return jsonify({
            'success': True,
            'data': {
                'path': os.path.realpath(path),
                'size': st.st_size,
                'mtime': st.st_mtime,
                'modified_at': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            },
            'error': None,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@renjs_bp.route('/files/delete', methods=['POST'])
def delete_file():
    """Delete a local file (not directory). Body: { path }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    path = data.get('path')
    try:
        t0 = time.time()
        if not path:
            return jsonify({'success': False, 'error': 'Missing required field: path'}), 400
        path = os.path.expanduser(path)
        if not _is_path_allowed(path):
            return jsonify({'success': False, 'error': 'Path not allowed'}), 403
        if not os.path.exists(path):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        if os.path.isdir(path):
            return jsonify({'success': False, 'error': 'Cannot delete directory from this action'}), 400
        if not _is_editable_file(path):
            return jsonify({'success': False, 'error': 'File type not deletable via Explorer'}), 400

        name = os.path.basename(path)
        real = os.path.realpath(path)
        os.remove(path)

        return jsonify({
            'success': True,
            'data': {
                'path': real,
                'name': name,
            },
            'execution_time': round(time.time() - t0, 6),
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/workspaces', methods=['GET'])
def api_list_workspaces():
    """List all named RENJS workspaces."""
    try:
        workspaces = list_workspaces()
        return jsonify({
            'success': True,
            'data': {'workspaces': workspaces},
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/workspaces/<int:workspace_id>', methods=['GET'])
def api_get_workspace(workspace_id: int):
    """Get a single workspace by id."""
    try:
        workspace = get_workspace(workspace_id)
        if not workspace:
            return jsonify({'success': False, 'error': 'Workspace not found'}), 404
        return jsonify({
            'success': True,
            'data': {'workspace': workspace},
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/workspaces', methods=['POST'])
def api_save_workspace():
    """Create or update a workspace. Body: { id?, name, path, description? }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    path = (data.get('path') or '').strip()
    if path:
        expanded = os.path.expanduser(path)
        if not _is_path_allowed(expanded):
            return jsonify({'success': False, 'error': 'Path not allowed'}), 403
        data = {**data, 'path': os.path.realpath(expanded) if os.path.isdir(expanded) else expanded}

    result = save_workspace(data)
    if not result.get('success'):
        status = 404 if 'not found' in (result.get('error') or '').lower() else 400
        return jsonify({'success': False, 'error': result.get('error')}), status

    workspace = get_workspace(result['id'])
    return jsonify({
        'success': True,
        'data': {'workspace': workspace, 'id': result['id']},
        'error': None,
    })


@renjs_bp.route('/workspaces/<int:workspace_id>', methods=['DELETE'])
def api_delete_workspace(workspace_id: int):
    """Delete a workspace by id."""
    result = delete_workspace(workspace_id)
    if not result.get('success'):
        status = 404 if 'not found' in (result.get('error') or '').lower() else 400
        return jsonify({'success': False, 'error': result.get('error')}), status
    return jsonify({'success': True, 'data': {'id': workspace_id}, 'error': None})


def _resolve_profile_or_error(profile_id: str):
    if not profile_id:
        return None, (jsonify({'success': False, 'error': 'Missing required field: profileId'}), 400)
    profile = _get_db_profile(profile_id)
    if not profile:
        return None, (jsonify({'success': False, 'error': f'Profile {profile_id} not found'}), 404)
    return profile, None


@renjs_bp.route('/scripts/list', methods=['POST'])
def api_list_scripts():
    """List scripts from DB. Body: { profileId, search? }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get('profileId'))
    if err:
        return err
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        scripts = db_list_scripts(conn, data.get('search') or '')
        conn.close()
        return jsonify({
            'success': True,
            'data': {'scripts': scripts},
            'execution_time': round(time.time() - t0, 6),
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/scripts/get', methods=['POST'])
def api_get_script():
    """Get one script including content. Body: { profileId, id }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get('profileId'))
    if err:
        return err
    script_id = data.get('id')
    if script_id is None:
        return jsonify({'success': False, 'error': 'Missing required field: id'}), 400
    try:
        conn = _connect_profile(profile)
        script = db_get_script(conn, int(script_id))
        conn.close()
        if not script:
            return jsonify({'success': False, 'error': 'Script not found'}), 404
        return jsonify({'success': True, 'data': {'script': script}, 'error': None})
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/scripts/save', methods=['POST'])
def api_save_script():
    """
    Save editor content to DB script.
    Body: {
      profileId, mode: 'overwrite'|'new', content,
      id?, name?, group_name?, comment?
    }
    """
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get('profileId'))
    if err:
        return err
    if data.get('content') is None:
        return jsonify({'success': False, 'error': 'Missing required field: content'}), 400
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        ok, payload = db_save_script(
            conn,
            mode=data.get('mode') or '',
            content=data.get('content'),
            comment=data.get('comment'),
            script_id=data.get('id'),
            group_name=data.get('group_name'),
            name=data.get('name'),
        )
        conn.close()
        if not ok:
            return jsonify({'success': False, 'error': payload.get('error')}), 400
        return jsonify({
            'success': True,
            'data': payload,
            'execution_time': round(time.time() - t0, 6),
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/scripts/history', methods=['POST'])
def api_script_history():
    """List history for a script. Body: { profileId, id }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get('profileId'))
    if err:
        return err
    script_id = data.get('id')
    if script_id is None:
        return jsonify({'success': False, 'error': 'Missing required field: id'}), 400
    try:
        conn = _connect_profile(profile)
        history = db_list_history(conn, int(script_id))
        conn.close()
        return jsonify({'success': True, 'data': {'history': history}, 'error': None})
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/scripts/history/get', methods=['POST'])
def api_script_history_get():
    """Get one history entry content. Body: { profileId, history_id }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get('profileId'))
    if err:
        return err
    history_id = data.get('history_id')
    if history_id is None:
        return jsonify({'success': False, 'error': 'Missing required field: history_id'}), 400
    try:
        conn = _connect_profile(profile)
        entry = db_get_history_entry(conn, int(history_id))
        conn.close()
        if not entry:
            return jsonify({'success': False, 'error': 'History entry not found'}), 404
        return jsonify({'success': True, 'data': {'history': entry}, 'error': None})
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/scripts/history/delete', methods=['POST'])
def api_script_history_delete():
    """Delete one history entry. Body: { profileId, history_id }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get('profileId'))
    if err:
        return err
    history_id = data.get('history_id')
    if history_id is None:
        return jsonify({'success': False, 'error': 'Missing required field: history_id'}), 400
    try:
        conn = _connect_profile(profile)
        ok, payload = db_delete_history_entry(conn, int(history_id))
        conn.close()
        if not ok:
            return jsonify({'success': False, 'error': payload.get('error')}), 404
        return jsonify({'success': True, 'data': payload, 'error': None})
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/scripts/history/clear', methods=['POST'])
def api_script_history_clear():
    """Clear all history for a script. Body: { profileId, id }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get('profileId'))
    if err:
        return err
    script_id = data.get('id')
    if script_id is None:
        return jsonify({'success': False, 'error': 'Missing required field: id'}), 400
    try:
        conn = _connect_profile(profile)
        ok, payload = db_clear_history(conn, int(script_id))
        conn.close()
        if not ok:
            return jsonify({'success': False, 'error': payload.get('error')}), 400
        return jsonify({'success': True, 'data': payload, 'error': None})
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/attrs', methods=['POST'])
def get_attrs():
    """
    Fetch container_plugin_attr by container plugin name.
    Body: { profileId, container_plugin_name }
    """
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile_id = data.get('profileId')
    plugin_name = (data.get('container_plugin_name') or '').strip()
    if not profile_id:
        return jsonify({'success': False, 'error': 'Missing required field: profileId'}), 400
    if not plugin_name:
        return jsonify({'success': False, 'error': 'Missing required field: container_plugin_name'}), 400

    profile = _get_db_profile(profile_id)
    if not profile:
        return jsonify({'success': False, 'error': f'Profile {profile_id} not found'}), 404

    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, name, plugin_id, container_id, comments FROM container_plugin WHERE name = %s",
                (plugin_name,),
            )
            plugin = cursor.fetchone()
            if not plugin:
                conn.close()
                return jsonify({
                    'success': False,
                    'error': f'Container plugin "{plugin_name}" not found',
                }), 404

            cursor.execute(
                """
                SELECT id, container_plugin_id, field, content
                FROM container_plugin_attr
                WHERE container_plugin_id = %s
                ORDER BY id ASC
                """,
                (plugin['id'],),
            )
            attrs = cursor.fetchall()
        conn.close()

        return jsonify({
            'success': True,
            'data': {
                'plugin': plugin,
                'attrs': attrs,
            },
            'execution_time': round(time.time() - t0, 6),
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@renjs_bp.route('/<path:path>', methods=['OPTIONS'])
@renjs_bp.route('', methods=['OPTIONS'], strict_slashes=False)
def options_handler(path=None):
    return '', 204
