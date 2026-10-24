from flask import Blueprint, request, jsonify
import pymysql
from app.routes.transaction.rna_transactions import load_profiles
import datetime, os

participant_bp = Blueprint('participant', __name__)

# Base endpoint: with url_prefix '/api/participant', support both with and without trailing slash
@participant_bp.route('', methods=['POST'], strict_slashes=False)
@participant_bp.route('/', methods=['POST'], strict_slashes=False)
def get_participants():
    """
    Get participant data with pagination from the participant table.
    Endpoint: /api/participant
    Request JSON body:
      - profileId: connection profile name (required)
      - page: page number (default 1)
      - page_size: page size (default 20)
    """
    # Required fields for response
    fields = [
        'id', 'ext_id', 'participant_type', 'name', 'container_plugin_name',
        'network_type', 'active', 'display_on_ui', 'comments'
    ]
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json()
    profile_id = data.get('profileId')
    page = int(data.get('page', 1))
    page_size = int(data.get('page_size', 20))
    offset = (page - 1) * page_size
    if not profile_id:
        return jsonify({'error': 'Missing profileId parameter'}), 400
    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'error': f'Profile {profile_id} not found'}), 404
    sort = data.get('sort', 'asc').lower()
    if sort not in ['asc', 'desc']:
        sort = 'asc'
    search = (data.get('search') or '').strip()
    column_filters = data.get('column_filters') or {}
    if not isinstance(column_filters, dict):
        column_filters = {}
    filters = data.get('filters') or data.get('dynamic_filters') or []
    if not isinstance(filters, list):
        filters = []

    # Build WHERE: optional global search (OR across fields) AND optional per-column / dynamic filters (AND)
    where_parts = []
    params = []
    if search:
        like = f"%{search}%"
        where_parts.append('(' + ' OR '.join([f"CAST(`{field}` AS CHAR) LIKE %s" for field in fields]) + ')')
        params.extend([like] * len(fields))
    for key, val in column_filters.items():
        if key in fields and val is not None and str(val).strip():
            where_parts.append(f"CAST(`{key}` AS CHAR) LIKE %s")
            params.append(f"%{str(val).strip()}%")
    for f in filters:
        if not isinstance(f, dict):
            continue
        field = (f.get('field') or '').strip()
        if field not in fields:
            continue
        expr, expr_params = _build_cp_filter_clause('p', field, f.get('operator'), f.get('value'))
        if expr:
            where_parts.append(expr)
            params.extend(expr_params)
    where_clause = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''
    try:
        import time
        start_time = time.time()
        
        def format_sql(sql, params):
            # Simple formatting for %s placeholders
            if not params:
                return sql
            for p in params:
                if isinstance(p, str):
                    p = p.replace("'", "''")
                    sql = sql.replace('%s', f"'{p}'", 1)
                elif p is None:
                    sql = sql.replace('%s', 'NULL', 1)
                else:
                    sql = sql.replace('%s', str(p), 1)
            return sql
        
        conn = pymysql.connect(
            host=profile['host'],
            port=int(profile['port']) if profile.get('port') else 3306,
            user=profile['user'],
            password=profile['password'],
            database=profile['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        with conn.cursor() as cursor:
            # Optimized query with pagination, search
            sql = f"""
                SELECT {', '.join(fields)}
                FROM participant
                {where_clause}
                ORDER BY id ASC
                LIMIT %s OFFSET %s
            """
            cursor.execute(sql, params + [page_size, offset])
            results = cursor.fetchall()
            # Get total count with same filter
            count_sql = f"SELECT COUNT(*) as total FROM participant {where_clause}"
            cursor.execute(count_sql, params)
            total_rows = cursor.fetchone()['total']
        conn.close()
        execution_time = round(time.time() - start_time, 6)
        executed_sql = format_sql(sql, params + [page_size, offset])
        return jsonify({
            'results': results,
            'total_rows': total_rows,
            'page': page,
            'page_size': page_size,
            'execution_time': execution_time,
            'executed_sql': executed_sql,
            'error': None
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_participants: {e}")
        print(f"Error details: {error_details}")
        return jsonify({'error': str(e), 'details': error_details, 'execution_time': None, 'executed_sql': None}), 500

# Allowed operators for dynamic container_plugin / attr filters
_CP_ALLOWED_OPS = {
    '=', '!=', '>', '<', '>=', '<=', 'LIKE', 'NOT LIKE',
    'STARTS_WITH', 'ENDS_WITH', 'IS_EMPTY', 'IS_NOT_EMPTY',
}

_CP_PLUGIN_COLUMNS = {
    'id', 'name', 'plugin_id', 'node_type', 'container_id',
    'in_next_plugin', 'out_next_plugin', 'connectto_ipport',
    'port_listen', 'queue_threshold', 'response_timeout',
    'startup_order', 'state_startup', 'dynamic_creation_mode',
    'max_dynamic_instances', 'flapping_ignore_time', 'cnt_instances',
    'comments',
}

_CP_ATTR_COLUMNS = {'id', 'container_plugin_id', 'field', 'content'}

_CP_FILTER_FIELDS = [
    {'label': 'Plugin: ID', 'value': 'cp.id', 'table': 'cp'},
    {'label': 'Plugin: Name', 'value': 'cp.name', 'table': 'cp'},
    {'label': 'Plugin: Container ID', 'value': 'cp.container_id', 'table': 'cp'},
    {'label': 'Plugin: Plugin ID', 'value': 'cp.plugin_id', 'table': 'cp'},
    {'label': 'Plugin: State Startup', 'value': 'cp.state_startup', 'table': 'cp'},
    {'label': 'Plugin: Port Listen', 'value': 'cp.port_listen', 'table': 'cp'},
    {'label': 'Plugin: Instances', 'value': 'cp.cnt_instances', 'table': 'cp'},
    {'label': 'Plugin: In Next', 'value': 'cp.in_next_plugin', 'table': 'cp'},
    {'label': 'Plugin: Out Next', 'value': 'cp.out_next_plugin', 'table': 'cp'},
    {'label': 'Plugin: Connect To', 'value': 'cp.connectto_ipport', 'table': 'cp'},
    {'label': 'Plugin: Queue Threshold', 'value': 'cp.queue_threshold', 'table': 'cp'},
    {'label': 'Plugin: Node Type', 'value': 'cp.node_type', 'table': 'cp'},
    {'label': 'Plugin: Startup Order', 'value': 'cp.startup_order', 'table': 'cp'},
    {'label': 'Plugin: Response Timeout', 'value': 'cp.response_timeout', 'table': 'cp'},
    {'label': 'Plugin: Comments', 'value': 'cp.comments', 'table': 'cp'},
    {'label': 'Attr: ID', 'value': 'attr.id', 'table': 'attr'},
    {'label': 'Attr: Plugin ID', 'value': 'attr.container_plugin_id', 'table': 'attr'},
    {'label': 'Attr: Field', 'value': 'attr.field', 'table': 'attr'},
    {'label': 'Attr: Content', 'value': 'attr.content', 'table': 'attr'},
]

_CP_FILTER_OPERATORS = [
    {'label': '=', 'value': '='},
    {'label': '!=', 'value': '!='},
    {'label': '>', 'value': '>'},
    {'label': '<', 'value': '<'},
    {'label': '>=', 'value': '>='},
    {'label': '<=', 'value': '<='},
    {'label': 'LIKE', 'value': 'LIKE'},
    {'label': 'NOT LIKE', 'value': 'NOT LIKE'},
    {'label': 'starts with', 'value': 'STARTS_WITH'},
    {'label': 'ends with', 'value': 'ENDS_WITH'},
    {'label': 'is empty', 'value': 'IS_EMPTY'},
    {'label': 'is not empty', 'value': 'IS_NOT_EMPTY'},
]


def _parse_cp_filter_field(field_key):
    """Return (table, column) or None if invalid."""
    if not field_key or not isinstance(field_key, str):
        return None
    if '.' in field_key:
        table, column = field_key.split('.', 1)
    else:
        table, column = 'cp', field_key
    table = table.strip().lower()
    column = column.strip().lower()
    if table == 'cp' and column in _CP_PLUGIN_COLUMNS:
        return 'cp', column
    if table in ('attr', 'cpa') and column in _CP_ATTR_COLUMNS:
        return 'attr', column
    return None


def _build_cp_filter_clause(table, column, operator, value):
    """
    Build a SQL predicate fragment and params for one filter.
    For attr table, column is used inside EXISTS subquery separately.
    Returns (sql_expr_without_alias, params) where sql_expr uses `{col}` placeholder.
    """
    op = (operator or '=').strip().upper()
    if op not in {o.upper() for o in _CP_ALLOWED_OPS}:
        return None, []

    col_ref = f'`{column}`'
    if op == 'IS_EMPTY':
        return f"({col_ref} IS NULL OR CAST({col_ref} AS CHAR) = '')", []
    if op == 'IS_NOT_EMPTY':
        return f"({col_ref} IS NOT NULL AND CAST({col_ref} AS CHAR) <> '')", []

    raw = '' if value is None else str(value)
    if raw.strip() == '':
        # Inactive filter (no value)
        return None, []

    if op == 'STARTS_WITH':
        return f"CAST({col_ref} AS CHAR) LIKE %s", [f'{raw}%']
    if op == 'ENDS_WITH':
        return f"CAST({col_ref} AS CHAR) LIKE %s", [f'%{raw}']
    if op == 'LIKE':
        return f"CAST({col_ref} AS CHAR) LIKE %s", [f'%{raw}%']
    if op == 'NOT LIKE':
        return f"CAST({col_ref} AS CHAR) NOT LIKE %s", [f'%{raw}%']
    if op in ('=', '!=', '>', '<', '>=', '<='):
        sql_op = '<>' if op == '!=' else op
        return f"{col_ref} {sql_op} %s", [raw]
    return None, []


def _connect_profile_db(profile):
    return pymysql.connect(
        host=profile['host'],
        port=int(profile['port']) if profile.get('port') else 3306,
        user=profile['user'],
        password=profile['password'],
        database=profile['database'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )


@participant_bp.route('/containerPlugin', methods=['POST'])
def get_container_plugin():
    """
    Get container plugin(s) by name from the container_plugin table.
    Endpoint: /api/participant/containerPlugin
    Request JSON body:
      - name: container plugin name (required)
    """
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json()
    name = data.get('name')
    profile_id = data.get('profileId')
    if not name:
        return jsonify({'error': 'Missing name parameter'}), 400
    if not profile_id:
        return jsonify({'error': 'Missing profileId parameter'}), 400
    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'error': f'Profile {profile_id} not found'}), 404
    try:
        conn = _connect_profile_db(profile)
        with conn.cursor() as cursor:
            sql = """
                SELECT * FROM container_plugin WHERE name = %s
            """
            cursor.execute(sql, (name,))
            results = cursor.fetchall()
        conn.close()
        return jsonify(results)
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_container_plugin: {e}")
        print(f"Error details: {error_details}")
        return jsonify({'error': str(e), 'details': error_details}), 500


@participant_bp.route('/containerPlugin/meta', methods=['GET', 'POST', 'OPTIONS'], strict_slashes=False)
def container_plugin_filter_meta():
    """Return available filter fields and operators for Container Plugin search."""
    if request.method == 'OPTIONS':
        return '', 204
    return jsonify({
        'filterFields': _CP_FILTER_FIELDS,
        'filterOperators': _CP_FILTER_OPERATORS,
    })


@participant_bp.route('/containerPlugin/names', methods=['POST', 'OPTIONS'], strict_slashes=False)
def list_container_plugin_names():
    """
    List container_plugin.name values for dropdowns (in_next / out_next).
    Body: { profileId }
    """
    if request.method == 'OPTIONS':
        return '', 204
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile_id = data.get('profileId')
    if not profile_id:
        return jsonify({'success': False, 'error': 'Missing profileId parameter'}), 400

    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'success': False, 'error': f'Profile {profile_id} not found'}), 404

    try:
        conn = _connect_profile_db(profile)
        with conn.cursor() as cursor:
            cursor.execute(
                '''
                SELECT DISTINCT name
                FROM container_plugin
                WHERE name IS NOT NULL AND TRIM(name) <> ''
                ORDER BY name ASC
                '''
            )
            rows = cursor.fetchall() or []
        conn.close()
        names = [r['name'] for r in rows if r.get('name')]
        return jsonify({
            'success': True,
            'data': names,
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'data': [],
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@participant_bp.route('/by-container-plugin', methods=['POST', 'OPTIONS'], strict_slashes=False)
def get_participant_by_container_plugin():
    """
    Find participant(s) by exact container_plugin_name.
    Body: { profileId, container_plugin_name }
    """
    if request.method == 'OPTIONS':
        return '', 204
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile_id = data.get('profileId')
    cp_name = (data.get('container_plugin_name') or data.get('name') or '').strip()
    if not profile_id:
        return jsonify({'success': False, 'error': 'Missing profileId parameter'}), 400
    if not cp_name:
        return jsonify({'success': False, 'error': 'Missing container_plugin_name parameter'}), 400

    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'success': False, 'error': f'Profile {profile_id} not found'}), 404

    fields = [
        'id', 'ext_id', 'participant_type', 'name', 'container_plugin_name',
        'network_type', 'active', 'display_on_ui', 'comments',
    ]
    try:
        conn = _connect_profile_db(profile)
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT {', '.join(fields)} FROM participant "
                f"WHERE container_plugin_name = %s ORDER BY id ASC",
                (cp_name,),
            )
            rows = cursor.fetchall() or []
        conn.close()
        if not rows:
            return jsonify({
                'success': False,
                'data': [],
                'error': (
                    f'Participant dengan container_plugin_name "{cp_name}" '
                    f'tidak ditemukan'
                ),
            }), 404
        return jsonify({
            'success': True,
            'data': rows,
            'total_rows': len(rows),
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'data': [],
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@participant_bp.route('/plugin/detail', methods=['POST', 'OPTIONS'], strict_slashes=False)
def plugin_table_detail():
    """
    Get one row from the `plugin` table by id.
    Used when clicking plugin_id on Container Plugin menu.
    Body: { profileId, id }
    """
    if request.method == 'OPTIONS':
        return '', 204
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile_id = data.get('profileId')
    plugin_id = data.get('id')
    if not profile_id:
        return jsonify({'success': False, 'error': 'Missing profileId parameter'}), 400
    if plugin_id is None or str(plugin_id).strip() == '':
        return jsonify({'success': False, 'error': 'Missing id parameter'}), 400

    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'success': False, 'error': f'Profile {profile_id} not found'}), 404

    try:
        conn = _connect_profile_db(profile)
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM plugin WHERE id = %s', (plugin_id,))
            row = cursor.fetchone()
        conn.close()
        if not row:
            return jsonify({
                'success': False,
                'error': f'Plugin id {plugin_id} tidak ditemukan di tabel plugin',
            }), 404
        return jsonify({
            'success': True,
            'data': row,
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@participant_bp.route('/plugin/<int:plugin_id>', methods=['PUT', 'OPTIONS'], strict_slashes=False)
def update_plugin_table_row(plugin_id):
    """
    Update a row in the `plugin` table by id.
    Body: { profileId, ...column values to update }
    Only columns that already exist on the row (except id) are updated.
    """
    if request.method == 'OPTIONS':
        return '', 204
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile_id = data.get('profileId')
    if not profile_id:
        return jsonify({'success': False, 'error': 'Missing profileId parameter'}), 400

    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'success': False, 'error': f'Profile {profile_id} not found'}), 404

    try:
        conn = _connect_profile_db(profile)
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM plugin WHERE id = %s', (plugin_id,))
            current = cursor.fetchone()
            if not current:
                conn.close()
                return jsonify({
                    'success': False,
                    'error': f'Plugin id {plugin_id} tidak ditemukan di tabel plugin',
                }), 404

            allowed = {k for k in current.keys() if k != 'id'}
            updates = {}
            for key, value in data.items():
                if key in ('profileId', 'id'):
                    continue
                if key not in allowed:
                    continue
                updates[key] = value

            if not updates:
                conn.close()
                return jsonify({
                    'success': False,
                    'error': 'No fields to update',
                }), 400

            if 'name' in updates:
                name_val = ('' if updates['name'] is None else str(updates['name'])).strip()
                if not name_val:
                    conn.close()
                    return jsonify({
                        'success': False,
                        'error': 'name tidak boleh kosong',
                    }), 400
                updates['name'] = name_val
                current_name = ('' if current.get('name') is None else str(current.get('name'))).strip()
                if name_val != current_name:
                    cursor.execute(
                        'SELECT id FROM plugin WHERE name = %s AND id <> %s',
                        (name_val, plugin_id),
                    )
                    conflict = cursor.fetchone()
                    if conflict:
                        conn.close()
                        return jsonify({
                            'success': False,
                            'error': (
                                f'Nama plugin "{name_val}" sudah digunakan '
                                f'oleh ID {conflict.get("id")}'
                            ),
                        }), 400

            set_clause = ', '.join([f'`{k}` = %s' for k in updates.keys()])
            cursor.execute(
                f'UPDATE plugin SET {set_clause} WHERE id = %s',
                list(updates.values()) + [plugin_id],
            )
            conn.commit()
            cursor.execute('SELECT * FROM plugin WHERE id = %s', (plugin_id,))
            updated = cursor.fetchone()
        conn.close()
        return jsonify({
            'success': True,
            'message': 'Plugin updated successfully',
            'data': updated,
        }), 200
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': f'Failed to update plugin: {str(e)}',
            'details': traceback.format_exc(),
        }), 500


@participant_bp.route('/containerPlugin/search', methods=['POST', 'OPTIONS'], strict_slashes=False)
def search_container_plugins():
    """
    Search container_plugin with optional filters on container_plugin and container_plugin_attr.
    Body:
      - profileId (required)
      - page, page_size
      - search (optional quick search on name/id/comments)
      - filters: [{ field, operator, value }]
        field examples: cp.name, attr.field, attr.content
    """
    if request.method == 'OPTIONS':
        return '', 204
    import time
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile_id = data.get('profileId')
    if not profile_id:
        return jsonify({'error': 'Missing profileId parameter'}), 400

    page = max(int(data.get('page', 1) or 1), 1)
    page_size = min(max(int(data.get('page_size', 50) or 50), 1), 500)
    offset = (page - 1) * page_size
    search = (data.get('search') or '').strip()
    filters = data.get('filters') or data.get('dynamic_filters') or []
    if not isinstance(filters, list):
        filters = []

    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'error': f'Profile {profile_id} not found'}), 404

    where_parts = []
    params = []

    if search:
        like = f'%{search}%'
        where_parts.append(
            '('
            'CAST(cp.id AS CHAR) LIKE %s OR '
            'CAST(cp.name AS CHAR) LIKE %s OR '
            'CAST(IFNULL(cp.comments, "") AS CHAR) LIKE %s OR '
            'CAST(IFNULL(cp.in_next_plugin, "") AS CHAR) LIKE %s OR '
            'CAST(IFNULL(cp.out_next_plugin, "") AS CHAR) LIKE %s'
            ')'
        )
        params.extend([like, like, like, like, like])

    for f in filters:
        if not isinstance(f, dict):
            continue
        parsed = _parse_cp_filter_field(f.get('field'))
        if not parsed:
            continue
        table, column = parsed
        expr, expr_params = _build_cp_filter_clause(table, column, f.get('operator'), f.get('value'))
        if not expr:
            continue
        if table == 'cp':
            # Qualify column with cp alias
            qualified = expr.replace(f'`{column}`', f'`cp`.`{column}`')
            where_parts.append(qualified)
            params.extend(expr_params)
        else:
            # Attr filter via EXISTS so we return matching plugins
            attr_expr = expr.replace(f'`{column}`', f'`cpa`.`{column}`')
            where_parts.append(
                f'EXISTS (SELECT 1 FROM container_plugin_attr cpa '
                f'WHERE cpa.container_plugin_id = cp.id AND {attr_expr})'
            )
            params.extend(expr_params)

    where_sql = (' WHERE ' + ' AND '.join(where_parts)) if where_parts else ''
    list_sql = f'''
        SELECT cp.*
        FROM container_plugin cp
        {where_sql}
        ORDER BY cp.id ASC
        LIMIT %s OFFSET %s
    '''
    count_sql = f'''
        SELECT COUNT(*) AS total
        FROM container_plugin cp
        {where_sql}
    '''

    t0 = time.time()
    try:
        conn = _connect_profile_db(profile)
        with conn.cursor() as cursor:
            cursor.execute(count_sql, params)
            total_rows = int((cursor.fetchone() or {}).get('total') or 0)
            cursor.execute(list_sql, params + [page_size, offset])
            rows = cursor.fetchall() or []
        conn.close()
        return jsonify({
            'success': True,
            'data': rows,
            'total_rows': total_rows,
            'page': page,
            'page_size': page_size,
            'execution_time': round(time.time() - t0, 6),
            'filterFields': _CP_FILTER_FIELDS,
            'filterOperators': _CP_FILTER_OPERATORS,
            'error': None,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'data': [],
            'total_rows': 0,
            'page': page,
            'page_size': page_size,
            'error': str(e),
            'details': traceback.format_exc(),
            'execution_time': round(time.time() - t0, 6),
        }), 500


@participant_bp.route('/containerPlugin/detail', methods=['POST', 'OPTIONS'], strict_slashes=False)
def container_plugin_detail():
    """
    Get one container_plugin by id (or name) plus its container_plugin_attr rows.
    Optionally re-apply attr filters so matching attrs are highlighted / filtered in dialog.
    Body:
      - profileId (required)
      - id OR name
      - filters (optional attr filters to apply on returned attrs)
    """
    if request.method == 'OPTIONS':
        return '', 204
    import time
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile_id = data.get('profileId')
    plugin_id = data.get('id')
    name = (data.get('name') or '').strip()
    filters = data.get('filters') or data.get('dynamic_filters') or []
    if not isinstance(filters, list):
        filters = []

    if not profile_id:
        return jsonify({'error': 'Missing profileId parameter'}), 400
    if plugin_id is None and not name:
        return jsonify({'error': 'Missing id or name parameter'}), 400

    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'error': f'Profile {profile_id} not found'}), 404

    t0 = time.time()
    try:
        conn = _connect_profile_db(profile)
        with conn.cursor() as cursor:
            if plugin_id is not None and str(plugin_id).strip() != '':
                cursor.execute('SELECT * FROM container_plugin WHERE id = %s', (plugin_id,))
            else:
                cursor.execute('SELECT * FROM container_plugin WHERE name = %s', (name,))
            plugin = cursor.fetchone()
            if not plugin:
                conn.close()
                return jsonify({
                    'success': False,
                    'error': 'Container plugin tidak ditemukan',
                }), 404

            attr_where = ['container_plugin_id = %s']
            attr_params = [plugin['id']]
            for f in filters:
                if not isinstance(f, dict):
                    continue
                parsed = _parse_cp_filter_field(f.get('field'))
                if not parsed:
                    continue
                table, column = parsed
                if table != 'attr':
                    continue
                expr, expr_params = _build_cp_filter_clause(
                    table, column, f.get('operator'), f.get('value')
                )
                if not expr:
                    continue
                attr_where.append(expr)
                attr_params.extend(expr_params)

            # Always also fetch all attrs for the dialog; filtered subset separately
            cursor.execute(
                '''
                SELECT id, container_plugin_id, field, content
                FROM container_plugin_attr
                WHERE container_plugin_id = %s
                ORDER BY id ASC
                ''',
                (plugin['id'],),
            )
            all_attrs = cursor.fetchall() or []

            filtered_attrs = all_attrs
            if len(attr_where) > 1:
                cursor.execute(
                    f'''
                    SELECT id, container_plugin_id, field, content
                    FROM container_plugin_attr
                    WHERE {' AND '.join(attr_where)}
                    ORDER BY id ASC
                    ''',
                    attr_params,
                )
                filtered_attrs = cursor.fetchall() or []

        conn.close()
        return jsonify({
            'success': True,
            'data': {
                'plugin': plugin,
                'attrs': all_attrs,
                'filtered_attrs': filtered_attrs,
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
            'execution_time': round(time.time() - t0, 6),
        }), 500


_CP_UPDATABLE_COLUMNS = [
    'container_id', 'plugin_id', 'name', 'state_startup', 'port_listen',
    'cnt_instances', 'in_next_plugin', 'out_next_plugin', 'connectto_ipport',
    'comments', 'queue_threshold', 'node_type', 'startup_order',
    'flapping_ignore_time', 'max_dynamic_instances', 'dynamic_creation_mode',
    'response_timeout',
]

_CP_ATTR_UPDATABLE_COLUMNS = ['field', 'content']


@participant_bp.route('/containerPlugin/<int:plugin_id>', methods=['PUT', 'OPTIONS'], strict_slashes=False)
def update_container_plugin(plugin_id):
    """
    Update container_plugin by id.
    Body: { profileId, ...updatable fields, attrs?: [{id?, field, content, _delete?}] }
    If attrs is provided:
      - existing id -> update field/content
      - id with _delete true -> delete
      - no id -> insert new attr for this plugin
    """
    if request.method == 'OPTIONS':
        return '', 204
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile_id = data.get('profileId')
    if not profile_id:
        return jsonify({'success': False, 'error': 'Missing profileId parameter'}), 400

    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'success': False, 'error': f'Profile {profile_id} not found'}), 404

    updates = {k: data.get(k) for k in _CP_UPDATABLE_COLUMNS if k in data}
    attrs_payload = data.get('attrs')
    if attrs_payload is not None and not isinstance(attrs_payload, list):
        return jsonify({'success': False, 'error': 'attrs must be an array'}), 400

    if not updates and attrs_payload is None:
        return jsonify({'success': False, 'error': 'No fields to update'}), 400

    if 'name' in updates:
        name_val = (updates.get('name') or '').strip() if updates.get('name') is not None else ''
        if not name_val:
            return jsonify({'success': False, 'error': 'name tidak boleh kosong'}), 400
        updates['name'] = name_val

    try:
        conn = _connect_profile_db(profile)
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM container_plugin WHERE id = %s', (plugin_id,))
            current = cursor.fetchone()
            if not current:
                conn.close()
                return jsonify({
                    'success': False,
                    'error': f'Container plugin id {plugin_id} tidak ditemukan',
                }), 404

            # Skip name uniqueness when nama tidak benar-benar berubah
            if 'name' in updates:
                current_name = (current.get('name') or '').strip()
                if updates['name'] == current_name:
                    # Tidak rename — jangan update kolom name
                    updates.pop('name', None)
                else:
                    cursor.execute(
                        'SELECT id, name FROM container_plugin WHERE name = %s AND id <> %s',
                        (updates['name'], plugin_id),
                    )
                    conflict = cursor.fetchone()
                    if conflict:
                        conn.close()
                        return jsonify({
                            'success': False,
                            'error': (
                                f'Nama container_plugin "{updates["name"]}" sudah digunakan '
                                f'oleh ID {conflict.get("id")}'
                            ),
                        }), 400

            if updates:
                set_clause = ', '.join([f'`{k}` = %s' for k in updates.keys()])
                cursor.execute(
                    f'UPDATE container_plugin SET {set_clause} WHERE id = %s',
                    list(updates.values()) + [plugin_id],
                )

            if attrs_payload is not None:
                for attr in attrs_payload:
                    if not isinstance(attr, dict):
                        continue
                    attr_id = attr.get('id')
                    do_delete = bool(attr.get('_delete'))
                    if do_delete and attr_id is not None:
                        cursor.execute(
                            'DELETE FROM container_plugin_attr '
                            'WHERE id = %s AND container_plugin_id = %s',
                            (attr_id, plugin_id),
                        )
                        continue

                    field_val = attr.get('field')
                    content_val = attr.get('content')
                    if attr_id is not None and str(attr_id).strip() != '':
                        attr_updates = {}
                        if 'field' in attr:
                            attr_updates['field'] = field_val
                        if 'content' in attr:
                            attr_updates['content'] = content_val
                        if not attr_updates:
                            continue
                        set_attr = ', '.join([f'`{k}` = %s' for k in attr_updates.keys()])
                        cursor.execute(
                            f'UPDATE container_plugin_attr SET {set_attr} '
                            f'WHERE id = %s AND container_plugin_id = %s',
                            list(attr_updates.values()) + [attr_id, plugin_id],
                        )
                    else:
                        # insert new
                        if field_val is None or str(field_val).strip() == '':
                            continue
                        cursor.execute(
                            'INSERT INTO container_plugin_attr '
                            '(container_plugin_id, field, content) VALUES (%s, %s, %s)',
                            (plugin_id, field_val, content_val if content_val is not None else ''),
                        )

            conn.commit()

            cursor.execute('SELECT * FROM container_plugin WHERE id = %s', (plugin_id,))
            plugin = cursor.fetchone()
            cursor.execute(
                '''
                SELECT id, container_plugin_id, field, content
                FROM container_plugin_attr
                WHERE container_plugin_id = %s
                ORDER BY id ASC
                ''',
                (plugin_id,),
            )
            attrs = cursor.fetchall() or []
        conn.close()
        return jsonify({
            'success': True,
            'message': 'Container plugin updated successfully',
            'data': {
                'plugin': plugin,
                'attrs': attrs,
            },
        }), 200
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': f'Failed to update container plugin: {str(e)}',
            'details': traceback.format_exc(),
        }), 500


@participant_bp.route('/duplicate-info', methods=['POST'])
def get_duplicate_info():
    """
    Fetch participant, paired container_plugins (main + COM), their attrs, and online configs
    based on participant_id or container_plugin_name (selected_container). Accepts POST with JSON body.
    """
    import pymysql
    from app.routes.transaction.rna_transactions import load_profiles
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json()
    profile_id = data.get('profileId')
    participant_id = data.get('participant_id')
    selected_container = data.get('container_plugin_name')
    if not profile_id or (not participant_id and not selected_container):
        return jsonify({'error': 'profileId and participant_id or container_plugin_name are required'}), 400
    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'error': f'Profile {profile_id} not found'}), 404
    try:
        conn = pymysql.connect(
            host=profile['host'],
            port=int(profile['port']) if profile.get('port') else 3306,
            user=profile['user'],
            password=profile['password'],
            database=profile['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        with conn.cursor() as cursor:
            # 1. Fetch participant
            if participant_id:
                cursor.execute("SELECT * FROM participant WHERE id = %s", (participant_id,))
                participant = cursor.fetchone()
                if not participant:
                    return jsonify({'error': f'Participant with id {participant_id} not found'}), 404
                selected_container = participant['container_plugin_name']
            else:
                cursor.execute("SELECT * FROM participant WHERE container_plugin_name = %s", (selected_container,))
                participant = cursor.fetchone()
                if not participant:
                    return jsonify({'error': f'Participant with container_plugin_name {selected_container} not found'}), 404
            # 2. Fetch main container_plugin (no COM prefix)
            cursor.execute("SELECT * FROM container_plugin WHERE name = %s", (selected_container,))
            main_plugin = cursor.fetchone()
            if not main_plugin:
                return jsonify({'error': f'Main container_plugin with name {selected_container} not found'}), 404
            # 3. Find the paired COM plugin (out_next_plugin of main_plugin)
            com_plugin = None
            if main_plugin['out_next_plugin']:
                cursor.execute("SELECT * FROM container_plugin WHERE name = %s", (main_plugin['out_next_plugin'],))
                maybe_com = cursor.fetchone()
                if maybe_com and maybe_com['name'].startswith('COM'):
                    com_plugin = maybe_com
            # If not paired, return error
            if not com_plugin:
                return jsonify({'error': 'The container plugin not paired, there is just one or none container plugin.'}), 400
            # 4. Fetch attrs for both plugins
            plugin_ids = [main_plugin['id']]
            if com_plugin:
                plugin_ids.append(com_plugin['id'])
            format_strings = ','.join(['%s'] * len(plugin_ids))
            cursor.execute(f"SELECT * FROM container_plugin_attr WHERE container_plugin_id IN ({format_strings})", tuple(plugin_ids))
            plugin_attrs = cursor.fetchall()
            # 5. Fetch online configs for selected container_plugin_name, acq_participant_id, or iss_participant_id
            online_configs = []
            # By container_plugin_name
            cursor.execute("SELECT * FROM online_config WHERE container_plugin_name = %s", (selected_container,))
            online_configs.extend(cursor.fetchall())
            # By acq_participant_id
            cursor.execute("SELECT * FROM online_config WHERE acq_participant_id = %s", (participant_id,))
            acq_configs = cursor.fetchall()
            # By iss_participant_id
            cursor.execute("SELECT * FROM online_config WHERE iss_participant_id = %s", (participant_id,))
            iss_configs = cursor.fetchall()
            # Merge all unique configs (by id if present, else by all fields)
            seen = set()
            def config_key(cfg):
                return cfg.get('id') or tuple(sorted(cfg.items()))
            for cfg in acq_configs + iss_configs:
                k = config_key(cfg)
                if k not in seen:
                    online_configs.append(cfg)
                    seen.add(k)
        conn.close()
        return jsonify({
            'participant': participant,
            'container_plugins': [main_plugin, com_plugin] if com_plugin else [main_plugin],
            'container_plugin_attrs': plugin_attrs,
            'online_configs': online_configs
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        return jsonify({'error': str(e), 'details': error_details}), 500

@participant_bp.route('/duplicate', methods=['POST'])
def duplicate_participant():
    """
    Insert new participant, paired container_plugins (main + COM), their attrs, and online configs,
    with custom values as provided. All duplication logic matches the Vue.js flow and pairing logic.
    """
    import pymysql
    from app.routes.transaction.rna_transactions import load_profiles
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json()
    profile_id = data.get('profileId')
    participant_data = data.get('participant')
    container_plugins = data.get('container_plugins', [])
    container_plugin_attrs = data.get('container_plugin_attrs', [])
    online_configs = data.get('online_configs', [])
    is_query_downloaded = data.get('is_query_downloaded', False)
    if not profile_id or not participant_data or not container_plugins:
        return jsonify({'error': 'profileId, participant, and container_plugins are required'}), 400
    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'error': f'Profile {profile_id} not found'}), 404
    try:
        executed_sqls = []
        def format_sql(sql, params):
            # Simple formatting for %s placeholders
            if not params:
                return sql
            for p in params:
                if isinstance(p, str):
                    p = p.replace("'", "''")
                    sql = sql.replace('%s', f"'{p}'", 1)
                elif p is None:
                    sql = sql.replace('%s', 'NULL', 1)
                else:
                    sql = sql.replace('%s', str(p), 1)
            return sql
        conn = pymysql.connect(
            host=profile['host'],
            port=int(profile['port']) if profile.get('port') else 3306,
            user=profile['user'],
            password=profile['password'],
            database=profile['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        with conn.cursor() as cursor:
            # 1. Insert participant
            participant_columns = [
                'id', 'ext_id', 'participant_type', 'name', 'container_plugin_name',
                'network_type', 'active', 'display_on_ui', 'comments'
            ]
            participant_insert = {k: v for k, v in participant_data.items() if k in participant_columns}
            participant_id = participant_insert.get('id', None)
            if participant_id not in (None, ''):
                cursor.execute('SELECT COUNT(*) as cnt FROM participant WHERE id = %s', (participant_id,))
                if cursor.fetchone()['cnt'] > 0:
                    return jsonify({'error': f'Participant ID {participant_id} already exists'}), 400
            else:
                if 'id' in participant_insert:
                    del participant_insert['id']
            participant_fields = list(participant_insert.keys())
            participant_values = [participant_insert[f] for f in participant_fields]
            sql_participant = f"INSERT INTO participant ({', '.join(participant_fields)}) VALUES ({', '.join(['%s'] * len(participant_fields))})"
            executed_sqls.append(format_sql(sql_participant, participant_values))
            cursor.execute(sql_participant, participant_values)
            if participant_id in (None, ''):
                cursor.execute("SELECT LAST_INSERT_ID() as new_id")
                participant_id = cursor.fetchone()['new_id']
            # 2. Insert container_plugins (main + COM)
            container_plugin_columns = [
                'id', 'container_id', 'plugin_id', 'name', 'state_startup', 'port_listen',
                'cnt_instances', 'in_next_plugin', 'out_next_plugin', 'connectto_ipport',
                'comments', 'queue_threshold', 'node_type', 'startup_order',
                'flapping_ignore_time', 'max_dynamic_instances', 'dynamic_creation_mode', 'response_timeout'
            ]
            old_to_new_cp_id = {}
            for cp in container_plugins:
                cp_insert = {k: v for k, v in cp.items() if k in container_plugin_columns}
                cp_id = cp_insert.get('id', None)
                if cp_id not in (None, ''):
                    cursor.execute('SELECT COUNT(*) as cnt FROM container_plugin WHERE id = %s', (cp_id,))
                    if cursor.fetchone()['cnt'] > 0:
                        return jsonify({'error': f'Container Plugin ID {cp_id} already exists'}), 400
                else:
                    if 'id' in cp_insert:
                        del cp_insert['id']
                cp_fields = list(cp_insert.keys())
                cp_values = [cp_insert[f] for f in cp_fields]
                sql_cp = f"INSERT INTO container_plugin ({', '.join(cp_fields)}) VALUES ({', '.join(['%s'] * len(cp_fields))})"
                executed_sqls.append(format_sql(sql_cp, cp_values))
                cursor.execute(sql_cp, cp_values)
                if cp_id in (None, ''):
                    cursor.execute("SELECT LAST_INSERT_ID() as new_cp_id")
                    new_cp_id = cursor.fetchone()['new_cp_id']
                else:
                    new_cp_id = cp_id
                old_to_new_cp_id[cp.get('id', None)] = new_cp_id
            conn.commit()
            # 3. Insert container_plugin_attrs
            container_plugin_attr_columns = [
                'id', 'container_plugin_id', 'field', 'content'
            ]
            for attr in container_plugin_attrs:
                attr_insert = attr.copy()
                if 'id' in attr_insert:
                    del attr_insert['id']
                # Update container_plugin_id reference if needed
                old_cp_id = attr_insert.get('container_plugin_id', None)
                if old_cp_id in old_to_new_cp_id:
                    attr_insert['container_plugin_id'] = old_to_new_cp_id[old_cp_id]
                attr_insert = {k: v for k, v in attr_insert.items() if k in container_plugin_attr_columns}
                attr_fields = list(attr_insert.keys())
                attr_values = [attr_insert[f] for f in attr_fields]
                sql_attr = f"INSERT INTO container_plugin_attr ({', '.join(attr_fields)}) VALUES ({', '.join(['%s'] * len(attr_fields))})"
                executed_sqls.append(format_sql(sql_attr, attr_values))
                cursor.execute(sql_attr, attr_values)
            conn.commit()
            # 4. Insert online_configs
            online_config_columns = [
                'id', 'acq_participant_id', 'iss_participant_id', 'plugin_id', 'container_plugin_name',
                'key_type', 'config_key', 'config_value'
            ]
            for config in online_configs:
                config_insert = config.copy()
                if 'id' in config_insert:
                    del config_insert['id']
                config_insert['acq_participant_id'] = participant_id
                config_insert = {k: v for k, v in config_insert.items() if k in online_config_columns}
                config_fields = list(config_insert.keys())
                config_values = [config_insert[f] for f in config_fields]
                sql_config = f"INSERT INTO online_config ({', '.join(config_fields)}) VALUES ({', '.join(['%s'] * len(config_fields))})"
                executed_sqls.append(format_sql(sql_config, config_values))
                cursor.execute(sql_config, config_values)
            conn.commit()
        conn.close()
        if is_query_downloaded:
            return jsonify({'success': True, 'message': 'Duplication completed', 'participant_id': participant_id, 'executed_sqls': executed_sqls})
        return jsonify({'success': True, 'message': 'Duplication completed', 'participant_id': participant_id})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        return jsonify({'error': str(e), 'details': error_details}), 500 

PARTICIPANT_COLUMNS = [
    'id', 'ext_id', 'participant_type', 'name', 'container_plugin_name',
    'network_type', 'active', 'display_on_ui', 'comments'
]


def _get_profile_or_error(profile_id):
    if not profile_id:
        return None, (jsonify({'success': False, 'error': 'Missing profileId parameter'}), 400)
    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return None, (jsonify({'success': False, 'error': f'Profile {profile_id} not found'}), 404)
    return profile, None


def _connect(profile):
    return pymysql.connect(
        host=profile['host'],
        port=int(profile['port']) if profile.get('port') else 3306,
        user=profile['user'],
        password=profile['password'],
        database=profile['database'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


def _normalize_participant_payload(data, require_name=True):
    """Extract and normalize participant fields from request body."""
    payload = {}
    for col in PARTICIPANT_COLUMNS:
        if col in data and data[col] is not None:
            payload[col] = data[col]

    if 'name' in payload and isinstance(payload['name'], str):
        payload['name'] = payload['name'].strip()
    if 'ext_id' in payload and isinstance(payload['ext_id'], str):
        payload['ext_id'] = payload['ext_id'].strip()
    if 'participant_type' in payload and isinstance(payload['participant_type'], str):
        payload['participant_type'] = payload['participant_type'].strip()
    if 'container_plugin_name' in payload and isinstance(payload['container_plugin_name'], str):
        payload['container_plugin_name'] = payload['container_plugin_name'].strip()
    if 'network_type' in payload and isinstance(payload['network_type'], str):
        payload['network_type'] = payload['network_type'].strip()
    if 'comments' in payload and isinstance(payload['comments'], str):
        payload['comments'] = payload['comments'].strip()

    if 'active' in payload:
        try:
            payload['active'] = 1 if int(payload['active']) == 1 else 0
        except (TypeError, ValueError):
            payload['active'] = 0

    if 'display_on_ui' in payload:
        display = str(payload['display_on_ui']).strip().upper()
        payload['display_on_ui'] = 'Y' if display in ('Y', '1', 'TRUE', 'YES') else 'N'

    if require_name and not payload.get('name'):
        return None, 'Participant name is required'

    return payload, None


@participant_bp.route('/new', methods=['POST'])
def create_participant():
    """Create a new participant row."""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile, err = _get_profile_or_error(data.get('profileId'))
    if err:
        return err

    payload, validation_error = _normalize_participant_payload(data, require_name=True)
    if validation_error:
        return jsonify({'success': False, 'error': validation_error}), 400

    try:
        conn = _connect(profile)
        with conn.cursor() as cursor:
            participant_id = payload.get('id')
            if participant_id not in (None, ''):
                cursor.execute('SELECT id FROM participant WHERE id = %s', (participant_id,))
                if cursor.fetchone():
                    return jsonify({
                        'success': False,
                        'error': f'Participant with ID {participant_id} already exists'
                    }), 400
            else:
                payload.pop('id', None)

            fields = list(payload.keys())
            values = [payload[f] for f in fields]
            sql = f"INSERT INTO participant ({', '.join(fields)}) VALUES ({', '.join(['%s'] * len(fields))})"
            cursor.execute(sql, values)
            conn.commit()

            inserted_id = participant_id if participant_id not in (None, '') else cursor.lastrowid
            cursor.execute(
                f"SELECT {', '.join(PARTICIPANT_COLUMNS)} FROM participant WHERE id = %s",
                (inserted_id,)
            )
            new_row = cursor.fetchone()
        conn.close()
        return jsonify({
            'success': True,
            'message': 'Participant created successfully',
            'data': new_row
        }), 201
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': f'Failed to create participant: {str(e)}',
            'details': traceback.format_exc()
        }), 500


@participant_bp.route('/<int:participant_id>', methods=['GET'])
def get_participant(participant_id):
    """Get a single participant by ID."""
    profile_id = request.args.get('profileId')
    if not profile_id and request.is_json:
        profile_id = (request.get_json(silent=True) or {}).get('profileId')
    profile, err = _get_profile_or_error(profile_id)
    if err:
        return err

    try:
        conn = _connect(profile)
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT {', '.join(PARTICIPANT_COLUMNS)} FROM participant WHERE id = %s",
                (participant_id,)
            )
            row = cursor.fetchone()
        conn.close()
        if not row:
            return jsonify({
                'success': False,
                'error': f'Participant with ID {participant_id} not found'
            }), 404
        return jsonify({'success': True, 'data': row}), 200
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': f'Failed to get participant: {str(e)}',
            'details': traceback.format_exc()
        }), 500


@participant_bp.route('/<int:participant_id>', methods=['PUT'])
def update_participant(participant_id):
    """Update a participant by ID."""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    profile, err = _get_profile_or_error(data.get('profileId'))
    if err:
        return err

    payload, validation_error = _normalize_participant_payload(data, require_name=False)
    if validation_error:
        return jsonify({'success': False, 'error': validation_error}), 400

    # Never allow changing primary key via body
    payload.pop('id', None)
    if not payload:
        return jsonify({'success': False, 'error': 'No fields to update'}), 400

    try:
        conn = _connect(profile)
        with conn.cursor() as cursor:
            cursor.execute('SELECT id FROM participant WHERE id = %s', (participant_id,))
            if not cursor.fetchone():
                return jsonify({
                    'success': False,
                    'error': f'Participant with ID {participant_id} not found'
                }), 404

            set_clause = ', '.join([f'`{k}` = %s' for k in payload.keys()])
            values = list(payload.values()) + [participant_id]
            cursor.execute(f"UPDATE participant SET {set_clause} WHERE id = %s", values)
            conn.commit()

            cursor.execute(
                f"SELECT {', '.join(PARTICIPANT_COLUMNS)} FROM participant WHERE id = %s",
                (participant_id,)
            )
            updated = cursor.fetchone()
        conn.close()
        return jsonify({
            'success': True,
            'message': 'Participant updated successfully',
            'data': updated
        }), 200
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': f'Failed to update participant: {str(e)}',
            'details': traceback.format_exc()
        }), 500


@participant_bp.route('/<int:participant_id>', methods=['DELETE'])
def delete_participant(participant_id):
    """Delete a participant by ID. Refuses if referenced by online_config."""
    profile_id = request.args.get('profileId')
    if not profile_id and request.is_json:
        profile_id = (request.get_json(silent=True) or {}).get('profileId')
    if not profile_id:
        # Some clients send JSON body on DELETE
        try:
            body = request.get_json(force=True, silent=True) or {}
            profile_id = body.get('profileId')
        except Exception:
            profile_id = None

    profile, err = _get_profile_or_error(profile_id)
    if err:
        return err

    try:
        conn = _connect(profile)
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT {', '.join(PARTICIPANT_COLUMNS)} FROM participant WHERE id = %s",
                (participant_id,)
            )
            row = cursor.fetchone()
            if not row:
                return jsonify({
                    'success': False,
                    'error': f'Participant with ID {participant_id} not found'
                }), 404

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt FROM online_config
                WHERE acq_participant_id = %s OR iss_participant_id = %s
                """,
                (participant_id, participant_id)
            )
            ref_count = cursor.fetchone()['cnt']
            if ref_count > 0:
                return jsonify({
                    'success': False,
                    'error': (
                        f'Cannot delete participant: {ref_count} online_config row(s) still reference this participant. '
                        'Remove related online_config first.'
                    )
                }), 400

            cursor.execute('DELETE FROM participant WHERE id = %s', (participant_id,))
            conn.commit()
        conn.close()
        return jsonify({
            'success': True,
            'message': f'Participant "{row.get("name")}" deleted successfully'
        }), 200
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': f'Failed to delete participant: {str(e)}',
            'details': traceback.format_exc()
        }), 500


@participant_bp.route('', methods=['OPTIONS'], strict_slashes=False)
@participant_bp.route('/', methods=['OPTIONS'], strict_slashes=False)
@participant_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path=None):
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200