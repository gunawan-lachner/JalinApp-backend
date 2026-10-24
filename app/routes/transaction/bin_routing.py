from flask import Blueprint, render_template, request, jsonify
import os
import sqlite3

# Use the same SQLite DB location as other route modules
DATABASE_FILE = os.path.join('instance', 'myapp_db.db')

# Blueprint
bin_routing_bp = Blueprint('bin_routing', __name__)

# --- Helpers ---

def load_profiles():
    profiles_list = []
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM connection WHERE connection_type = 'DB' ORDER BY name")
        rows = cursor.fetchall()
        profiles_list = [dict(row) for row in rows]
        return profiles_list
    except Exception as e:
        print(f"Error loading profiles from database: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_connection_details(connection_name):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM connection WHERE name = ? AND connection_type = 'DB'", (connection_name,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"Error getting connection details: {e}")
        return None


def execute_query(conn_details, query, params=None, fetch=True):
    try:
        if conn_details and conn_details.get('connection_type') == 'DB':
            import pymysql
            conn = pymysql.connect(
                host=conn_details.get('host'),
                port=int(conn_details.get('port', 3306)),
                user=conn_details.get('user'),
                password=conn_details.get('password'),
                database=conn_details.get('database'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
        else:
            conn = sqlite3.connect(DATABASE_FILE)
            conn.row_factory = sqlite3.Row

        cursor = conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        if fetch:
            results = cursor.fetchall()
            conn.close()
            return results
        else:
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected
    except Exception as e:
        print(f"Error executing query: {e}")
        raise e


def create_readable_query(query, params):
    try:
        if not params:
            return query
        readable_query = query
        for p in params:
            if isinstance(p, str):
                readable_query = readable_query.replace('%s', f"'{p}'", 1)
                readable_query = readable_query.replace('?', f"'{p}'", 1)
            else:
                readable_query = readable_query.replace('%s', str(p), 1)
                readable_query = readable_query.replace('?', str(p), 1)
        return readable_query
    except Exception as e:
        return f"Error creating readable query: {str(e)}"


def create_bin_filter_condition(bin_value):
    return f"(bd.bin_lower <= CONVERT(CONCAT('{bin_value}','0000000000000') USING UCS2) AND bd.bin_upper >= CONVERT(CONCAT('{bin_value}','9999999999999') USING UCS2))"

# Helper to fetch BIN holder name (issuer) from local SQLite bin_list table
def get_bin_holder_name(bin_number):
    try:
        if not bin_number:
            return ''
        # Ensure we query using string form (usually 6-digit BIN prefix)
        with sqlite3.connect(DATABASE_FILE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT Issuer FROM bin_list WHERE BIN = ?", (str(bin_number),))
            row = cur.fetchone()
            return row[0] if row and len(row) > 0 else ''
    except Exception as e:
        print(f"Error getting BIN holder name from SQLite: {e}")
        return ''

def update_bin_holder_name(bin_number, holder_name):
    """Upsert holder name into local SQLite bin_list table"""
    try:
        if not bin_number or holder_name is None:
            return False
        with sqlite3.connect(DATABASE_FILE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM bin_list WHERE BIN = ?", (str(bin_number),))
            existing = cur.fetchone()
            if existing:
                cur.execute("UPDATE bin_list SET Issuer = ? WHERE BIN = ?", (holder_name, str(bin_number)))
            else:
                cur.execute("INSERT INTO bin_list (BIN, Issuer) VALUES (?, ?)", (str(bin_number), holder_name))
            conn.commit()
        return True
    except Exception as e:
        print(f"Error updating BIN holder name in SQLite: {e}")
        return False

def get_participant_name(conn_details, participant_id):
    """Fetch participant name by ID from the primary DB (e.g., MySQL)"""
    try:
        if not participant_id:
            return None
        rows = execute_query(conn_details, "SELECT name FROM participant WHERE id = %s LIMIT 1", [int(participant_id)])
        if rows:
            if isinstance(rows[0], dict):
                return rows[0].get('name')
            return rows[0][0]
        return None
    except Exception:
        return None

# --- Page route (optional UI) ---
@bin_routing_bp.route('/bin-routing')
def index():
    saved_profiles = load_profiles()
    selected_connection = request.args.get('rna_connection')
    return render_template(
        'rna/transaction_routing_bin.html',
        saved_profiles=saved_profiles,
        selected_connection=selected_connection
    )

# --- API routes (prefix applied during blueprint registration) ---

@bin_routing_bp.route('/routes', methods=['GET'])
def get_bin_routes():
    try:
        connection_name = request.args.get('connection')
        if not connection_name:
            return jsonify({"success": False, "error": "Connection name is required"}), 400

        acquirer_id = request.args.get('acquirer_id', '')
        issuer_id = request.args.get('issuer_id', '')
        bin_number = request.args.get('bin', '')
        gateway_id = request.args.get('gateway_id', '')
        search_term = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('pageSize', 20))

        query = """
        SELECT 
            bp.id,
            bp.participant_acq_id,
            ap.name AS `acq_name`,
            bd.bin_lower,
            bd.bin_upper,
            bp.gateway_id,
            bg.name AS `gateway_name`,
            bp.priority,
            bp.participant_iss_id,
            ip.name AS `iss_name`,
            ip.container_plugin_name,
            bp.rule,
            bp.description,
            bd.id AS `bin_details_id`
        FROM bin_participant bp
            LEFT JOIN bin_details bd
                ON bp.gateway_id = bd.gateway_id
            LEFT JOIN bin_gateway bg
                ON bp.gateway_id = bg.id
            LEFT JOIN participant ap
                ON bp.participant_acq_id = ap.id
            LEFT JOIN participant ip
                ON bp.participant_iss_id = ip.id
        """

        where_clauses = []
        params = []

        if acquirer_id and acquirer_id.strip():
            where_clauses.append("bp.participant_acq_id = %s")
            params.append(int(acquirer_id.strip()))
        if issuer_id and issuer_id.strip():
            where_clauses.append("bp.participant_iss_id = %s")
            params.append(int(issuer_id.strip()))
        if bin_number and bin_number.strip():
            bin_value = bin_number.strip()
            where_clauses.append(create_bin_filter_condition(bin_value))
        if gateway_id and gateway_id.strip():
            where_clauses.append("bp.gateway_id = %s")
            params.append(int(gateway_id.strip()))
        if search_term and search_term.strip():
            where_clauses.append("(ap.name LIKE %s OR bg.name LIKE %s OR ip.name LIKE %s OR ip.container_plugin_name LIKE %s OR bp.description LIKE %s OR bp.rule LIKE %s)")
            params.extend([f"%{search_term.strip()}%"] * 6)

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        query += " ORDER BY bp.priority DESC, bd.bin_lower ASC"
        offset = (page - 1) * page_size
        query += f" LIMIT {page_size} OFFSET {offset}"

        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

        routes = execute_query(conn_details, query, params)

        count_query = """
        SELECT COUNT(*) as total
        FROM bin_participant bp
            LEFT JOIN bin_details bd
                ON bp.gateway_id = bd.gateway_id
            LEFT JOIN bin_gateway bg
                ON bp.gateway_id = bg.id
            LEFT JOIN participant ap
                ON bp.participant_acq_id = ap.id
            LEFT JOIN participant ip
                ON bp.participant_iss_id = ip.id
        """
        if where_clauses:
            count_query += " WHERE " + " AND ".join(where_clauses)
        total_result = execute_query(conn_details, count_query, params)
        if total_result:
            if isinstance(total_result[0], dict):
                total_count = total_result[0].get('total', 0)
            else:
                total_count = total_result[0][0] if total_result else 0
        else:
            total_count = 0

        formatted_routes = []
        for route in routes:
            if isinstance(route, dict):
                formatted_routes.append({
                    "id": route.get('id'),
                    "participant_acq_id": route.get('participant_acq_id'),
                    "acq_name": route.get('acq_name'),
                    "bin_lower": route.get('bin_lower'),
                    "bin_upper": route.get('bin_upper'),
                    "gateway_id": route.get('gateway_id'),
                    "gateway_name": route.get('gateway_name'),
                    "priority": route.get('priority'),
                    "participant_iss_id": route.get('participant_iss_id'),
                    "iss_name": route.get('iss_name'),
                    "container_plugin_name": route.get('container_plugin_name'),
                    "rule": route.get('rule'),
                    "description": route.get('description'),
                    "bin_details_id": route.get('bin_details_id')
                })
            else:
                formatted_routes.append({
                    "id": route[0],
                    "participant_acq_id": route[1],
                    "acq_name": route[2],
                    "bin_lower": route[3],
                    "bin_upper": route[4],
                    "gateway_id": route[5],
                    "gateway_name": route[6],
                    "priority": route[7],
                    "participant_iss_id": route[8],
                    "iss_name": route[9],
                    "container_plugin_name": route[10],
                    "rule": route[11],
                    "description": route[12],
                    "bin_details_id": route[13]
                })

        total_pages = (total_count + page_size - 1) // page_size
        readable_query = create_readable_query(query, params)

        return jsonify({
            "success": True,
            "routes": formatted_routes,
            "total": total_count,
            "pagination": {"current": page, "total": total_pages, "pageSize": page_size},
            "query": readable_query
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- Pattern-aligned POST list endpoint (similar to /api/rna/participant) ---
@bin_routing_bp.route('/routes/list', methods=['POST'])
def list_bin_routes_post():
    """List BIN routes using POST with JSON body (profileId, pagination, filters, search)"""
    try:
        if not request.is_json:
            return jsonify({"error": "Request body must be JSON"}), 400
        data = request.get_json()
        profile_id = data.get('profileId')
        page = int(data.get('page', 1))
        page_size = int(data.get('page_size', 20))
        offset = (page - 1) * page_size
        sort = str(data.get('sort', 'desc')).lower()
        if sort not in ['asc', 'desc']:
            sort = 'desc'
        # Filters
        acquirer_id = str(data.get('acquirer_id', '') or '').strip()
        issuer_id = str(data.get('issuer_id', '') or '').strip()
        bin_number = str(data.get('bin', '') or '').strip()
        gateway_id = str(data.get('gateway_id', '') or '').strip()
        search = str(data.get('search', '') or '').strip()

        if not profile_id:
            return jsonify({"error": "Missing profileId parameter"}), 400

        conn_details = get_connection_details(profile_id)
        if not conn_details:
            return jsonify({"error": f"Profile {profile_id} not found"}), 404

        import time
        start_time = time.time()

        def format_sql(sql, params):
            if not params:
                return sql
            for p in params:
                if isinstance(p, str):
                    escaped = p.replace("'", "''")
                    sql = sql.replace('%s', f"'{escaped}'", 1)
                elif p is None:
                    sql = sql.replace('%s', 'NULL', 1)
                else:
                    sql = sql.replace('%s', str(p), 1)
            return sql

        base_sql = """
            SELECT 
                bp.id,
                bp.participant_acq_id,
                ap.name AS acq_name,
                bd.bin_lower,
                bd.bin_upper,
                bp.gateway_id,
                bg.name AS gateway_name,
                bp.priority,
                bp.participant_iss_id,
                ip.name AS iss_name,
                ip.container_plugin_name,
                bp.rule,
                bp.description,
                bd.id AS bin_details_id
            FROM bin_participant bp
                LEFT JOIN bin_details bd ON bp.gateway_id = bd.gateway_id
                LEFT JOIN bin_gateway bg ON bp.gateway_id = bg.id
                LEFT JOIN participant ap ON bp.participant_acq_id = ap.id
                LEFT JOIN participant ip ON bp.participant_iss_id = ip.id
        """

        where_clauses = []
        params = []
        if acquirer_id:
            where_clauses.append("bp.participant_acq_id = %s")
            params.append(int(acquirer_id))
        if issuer_id:
            where_clauses.append("bp.participant_iss_id = %s")
            params.append(int(issuer_id))
        if bin_number:
            where_clauses.append(create_bin_filter_condition(bin_number))
        if gateway_id:
            where_clauses.append("bp.gateway_id = %s")
            params.append(int(gateway_id))
        if search:
            where_clauses.append("(ap.name LIKE %s OR bg.name LIKE %s OR ip.name LIKE %s OR ip.container_plugin_name LIKE %s OR bp.description LIKE %s OR bp.rule LIKE %s)")
            like = f"%{search}%"
            params.extend([like, like, like, like, like, like])

        sql = base_sql
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        sql += f" ORDER BY bp.priority {sort.upper()}, bd.bin_lower ASC"
        sql += " LIMIT %s OFFSET %s"
        params_with_pagination = params + [page_size, offset]

        rows = execute_query(conn_details, sql, params_with_pagination)

        # Enrich rows with bin_holder_name from SQLite
        enriched_rows = []
        for r in rows:
            if isinstance(r, dict):
                bin_prefix = (r.get('bin_lower') or '')[:6]
                r['bin_holder_name'] = get_bin_holder_name(bin_prefix)
                enriched_rows.append(r)
            else:
                # If tuple-like, convert to dict minimally to append new field
                try:
                    r_dict = {
                        'id': r[0], 'participant_acq_id': r[1], 'acq_name': r[2],
                        'bin_lower': r[3], 'bin_upper': r[4], 'gateway_id': r[5],
                        'gateway_name': r[6], 'priority': r[7], 'participant_iss_id': r[8],
                        'iss_name': r[9], 'container_plugin_name': r[10], 'rule': r[11],
                        'description': r[12]
                    }
                    r_dict['bin_holder_name'] = get_bin_holder_name(str(r_dict.get('bin_lower') or '')[:6])
                    enriched_rows.append(r_dict)
                except Exception:
                    enriched_rows.append(r)

        count_sql = """
            SELECT COUNT(*) as total
            FROM bin_participant bp
                LEFT JOIN bin_details bd ON bp.gateway_id = bd.gateway_id
                LEFT JOIN bin_gateway bg ON bp.gateway_id = bg.id
                LEFT JOIN participant ap ON bp.participant_acq_id = ap.id
                LEFT JOIN participant ip ON bp.participant_iss_id = ip.id
        """
        if where_clauses:
            count_sql += " WHERE " + " AND ".join(where_clauses)
        total_rows_res = execute_query(conn_details, count_sql, params)
        if total_rows_res and isinstance(total_rows_res[0], dict):
            total_rows = total_rows_res[0].get('total', 0)
        else:
            total_rows = total_rows_res[0][0] if total_rows_res else 0

        execution_time = round(time.time() - start_time, 6)
        executed_sql = format_sql(sql, params_with_pagination)

        return jsonify({
            'results': enriched_rows,
            'total_rows': total_rows,
            'page': page,
            'page_size': page_size,
            'execution_time': execution_time,
            'executed_sql': executed_sql,
            'error': None
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'details': traceback.format_exc(), 'execution_time': None, 'executed_sql': None}), 500


@bin_routing_bp.route('/routes/simulation', methods=['GET'])
def get_bin_routes_simulation():
    try:
        connection_name = request.args.get('connection')
        if not connection_name:
            return jsonify({"success": False, "error": "Connection name is required"}), 400

        acquirer_id = request.args.get('acquirer_id', '')
        issuer_id = request.args.get('issuer_id', '')
        bin_number = request.args.get('bin', '')
        gateway_id = request.args.get('gateway_id', '')
        search_term = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('pageSize', 20))

        query = """
        SELECT 
            bp.id,
            bp.participant_acq_id,
            ap.name AS `acq_name`,
            bd.bin_lower,
            bd.bin_upper,
            bp.gateway_id,
            bg.name AS `gateway_name`,
            bp.priority,
            bp.participant_iss_id,
            ip.name AS `iss_name`,
            ip.container_plugin_name,
            bp.rule,
            bp.description
        FROM bin_participant bp
            /* Use an aggregated view of bin_details to avoid duplicate rows per gateway */
            LEFT JOIN (
                SELECT 
                    gateway_id,
                    MIN(bin_lower) AS bin_lower,
                    MAX(bin_upper) AS bin_upper
                FROM bin_details
                GROUP BY gateway_id
            ) bd
                ON bp.gateway_id = bd.gateway_id
            LEFT JOIN bin_gateway bg
                ON bp.gateway_id = bg.id
            LEFT JOIN participant ap
                ON bp.participant_acq_id = ap.id
            LEFT JOIN participant ip
                ON bp.participant_iss_id = ip.id
        """

        where_clauses = []
        params = []

        if acquirer_id and acquirer_id.strip():
            where_clauses.append("bp.participant_acq_id = %s")
            params.append(int(acquirer_id.strip()))
        if issuer_id and issuer_id.strip():
            where_clauses.append("bp.participant_iss_id = %s")
            params.append(int(issuer_id.strip()))
        if bin_number and bin_number.strip():
            bin_value = bin_number.strip()
            where_clauses.append(create_bin_filter_condition(bin_value))
        if gateway_id and gateway_id.strip():
            where_clauses.append("bp.gateway_id = %s")
            params.append(int(gateway_id.strip()))
        if search_term and search_term.strip():
            where_clauses.append("(ap.name LIKE %s OR bg.name LIKE %s OR ip.name LIKE %s OR ip.container_plugin_name LIKE %s OR bp.description LIKE %s OR bp.rule LIKE %s)")
            params.extend([f"%{search_term.strip()}%"] * 6)

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY bp.priority DESC"

        offset = (page - 1) * page_size
        query += f" LIMIT {page_size} OFFSET {offset}"

        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

        routes = execute_query(conn_details, query, params)

        count_query = """
        SELECT COUNT(*) as total
        FROM bin_participant bp
            LEFT JOIN (
                SELECT 
                    gateway_id,
                    MIN(bin_lower) AS bin_lower,
                    MAX(bin_upper) AS bin_upper
                FROM bin_details
                GROUP BY gateway_id
            ) bd
                ON bp.gateway_id = bd.gateway_id
            LEFT JOIN bin_gateway bg
                ON bp.gateway_id = bg.id
            LEFT JOIN participant ap
                ON bp.participant_acq_id = ap.id
            LEFT JOIN participant ip
                ON bp.participant_iss_id = ip.id
        """
        if where_clauses:
            count_query += " WHERE " + " AND ".join(where_clauses)
        total_result = execute_query(conn_details, count_query, params)
        if total_result:
            if isinstance(total_result[0], dict):
                total_count = total_result[0].get('total', 0)
            else:
                total_count = total_result[0][0] if total_result else 0
        else:
            total_count = 0

        formatted_routes = []
        for route in routes:
            if isinstance(route, dict):
                formatted_routes.append({
                    "id": route.get('id'),
                    "participant_acq_id": route.get('participant_acq_id'),
                    "acq_name": route.get('acq_name'),
                    "bin_lower": route.get('bin_lower'),
                    "bin_upper": route.get('bin_upper'),
                    "gateway_id": route.get('gateway_id'),
                    "gateway_name": route.get('gateway_name'),
                    "priority": route.get('priority'),
                    "participant_iss_id": route.get('participant_iss_id'),
                    "iss_name": route.get('iss_name'),
                    "container_plugin_name": route.get('container_plugin_name'),
                    "rule": route.get('rule'),
                    "description": route.get('description'),
                    "bin_holder_name": get_bin_holder_name((route.get('bin_lower') or '')[:6])
                })
            else:
                formatted_routes.append({
                    "id": route[0],
                    "participant_acq_id": route[1],
                    "acq_name": route[2],
                    "bin_lower": route[3],
                    "bin_upper": route[4],
                    "gateway_id": route[5],
                    "gateway_name": route[6],
                    "priority": route[7],
                    "participant_iss_id": route[8],
                    "iss_name": route[9],
                    "container_plugin_name": route[10],
                    "rule": route[11],
                    "description": route[12],
                    "bin_holder_name": get_bin_holder_name(str(route[3])[:6] if route[3] else '')
                })

        total_pages = (total_count + page_size - 1) // page_size
        readable_query = create_readable_query(query, params)

        return jsonify({
            "success": True,
            "routes": formatted_routes,
            "total": total_count,
            "pagination": {"current": page, "total": total_pages, "pageSize": page_size},
            "query": readable_query
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/acquirers', methods=['GET'])
def get_acquirers():
    try:
        connection_name = request.args.get('connection')
        if not connection_name:
            return jsonify({"success": False, "error": "Connection name is required"}), 400

        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

        query = "SELECT id, name FROM participant WHERE participant_type IN ('ACQ','acquirer') ORDER BY name"
        acquirers = execute_query(conn_details, query)

        formatted = []
        for a in acquirers:
            if isinstance(a, dict):
                formatted.append({"id": a['id'], "name": a['name']})
            else:
                formatted.append({"id": a[0], "name": a[1]})
        return jsonify({"success": True, "acquirers": formatted})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# Pattern-aligned POST: list acquirers
@bin_routing_bp.route('/acquirers/list', methods=['POST'])
def list_acquirers_post():
    try:
        if not request.is_json:
            return jsonify({'error': 'Request body must be JSON'}), 400
        data = request.get_json()
        profile_id = data.get('profileId')
        if not profile_id:
            return jsonify({'error': 'Missing profileId parameter'}), 400
        conn_details = get_connection_details(profile_id)
        if not conn_details:
            return jsonify({'error': f'Profile {profile_id} not found'}), 404
        sql = "SELECT id, name FROM participant WHERE participant_type IN ('ACQ','acquirer') ORDER BY name"
        rows = execute_query(conn_details, sql)
        return jsonify({'results': rows, 'total_rows': len(rows), 'page': 1, 'page_size': len(rows), 'execution_time': 0, 'executed_sql': sql, 'error': None})
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'details': traceback.format_exc()}), 500


@bin_routing_bp.route('/gateways', methods=['GET'])
def get_gateways():
    try:
        connection_name = request.args.get('connection')
        if not connection_name:
            return jsonify({"success": False, "error": "Connection name is required"}), 400

        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

        query = "SELECT id, name FROM bin_gateway ORDER BY name"
        gateways = execute_query(conn_details, query)

        formatted = []
        for g in gateways:
            if isinstance(g, dict):
                formatted.append({"id": g['id'], "name": g['name']})
            else:
                formatted.append({"id": g[0], "name": g[1]})
        return jsonify({"success": True, "gateways": formatted})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# Pattern-aligned POST: list gateways
@bin_routing_bp.route('/gateways/list', methods=['POST'])
def list_gateways_post():
    try:
        if not request.is_json:
            return jsonify({'error': 'Request body must be JSON'}), 400
        data = request.get_json()
        profile_id = data.get('profileId')
        if not profile_id:
            return jsonify({'error': 'Missing profileId parameter'}), 400
        conn_details = get_connection_details(profile_id)
        if not conn_details:
            return jsonify({'error': f'Profile {profile_id} not found'}), 404
        sql = "SELECT id, name FROM bin_gateway ORDER BY name"
        rows = execute_query(conn_details, sql)
        return jsonify({'results': rows, 'total_rows': len(rows), 'page': 1, 'page_size': len(rows), 'execution_time': 0, 'executed_sql': sql, 'error': None})
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'details': traceback.format_exc()}), 500

# CORS preflight handlers (avoid 404 for OPTIONS)
@bin_routing_bp.route('', methods=['OPTIONS'])
def options_root():
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200

@bin_routing_bp.route('/<path:path>', methods=['OPTIONS'])
def options_catch_all(path):
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200


@bin_routing_bp.route('/routes', methods=['POST'])
def create_bin_route():
    try:
        data = request.get_json()
        # Require core fields; bin can be provided as single 'bin' OR as 'bin_lower'+'bin_upper'
        required_fields = ['connection', 'participant_acq_id', 'gateway_id', 'priority', 'participant_iss_id', 'rule']
        for f in required_fields:
            if f not in data or not data[f]:
                return jsonify({"success": False, "error": f"Missing required field: {f}"}), 400

        connection_name = data.get('connection')
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

        participant_acq_id = data.get('participant_acq_id')
        # Accept either 'bin' or ('bin_lower' and 'bin_upper')
        bin_number = data.get('bin')
        bin_lower_input = data.get('bin_lower')
        bin_upper_input = data.get('bin_upper')
        gateway_id = data.get('gateway_id')
        priority = data.get('priority')
        participant_iss_id = data.get('participant_iss_id')
        rule = data.get('rule')
        description = data.get('description', '')
        # Optional BIN detail attributes (use defaults if not provided)
        bin_type = int(data.get('bin_type', 1))
        country = int(data.get('country', 360))
        currency = int(data.get('currency', 360))
        region = str(data.get('region', 'B'))
        # Optional bin holder name from client
        input_bin_holder_name = str(data.get('bin_holder_name', '') or '').strip()

        # Validate and compute final bin_lower/bin_upper values
        if bin_number:
            bin_lower_val = str(bin_number)
            bin_upper_val = str(bin_number)
        elif bin_lower_input and bin_upper_input:
            bin_lower_val = str(bin_lower_input)
            bin_upper_val = str(bin_upper_input)
        else:
            return jsonify({"success": False, "error": "Missing required field: bin (or bin_lower and bin_upper)"}), 400

        participant_query = """
        INSERT INTO bin_participant (
            participant_acq_id, participant_iss_id, gateway_id,
            priority, rule, description
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """
        participant_params = [participant_acq_id, participant_iss_id, gateway_id, priority, rule, description]

        import time
        start_time = time.time()

        try:
            # insert participant
            execute_query(conn_details, participant_query, participant_params, fetch=False)
            # get last insert id - not reliable across dbs here; skip returning exact id
            # insert bin details
            bin_query = """
            INSERT INTO bin_details (gateway_id, bin_lower, bin_upper, bin_type, country, currency, region)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            bin_params = [gateway_id, bin_lower_val, bin_upper_val, bin_type, country, currency, region]
            execute_query(conn_details, bin_query, bin_params, fetch=False)

            # Upsert BIN holder into local SQLite bin_list
            # Use bin_lower as source for BIN prefix
            bin_prefix = str(bin_lower_val)[:6] if bin_lower_val else ''
            holder_name = input_bin_holder_name or get_participant_name(conn_details, participant_iss_id)
            if bin_prefix and holder_name:
                update_bin_holder_name(bin_prefix, holder_name)

            execution_time = time.time() - start_time
            readable_participant_query = create_readable_query(participant_query, participant_params)
            readable_bin_query = create_readable_query(bin_query, bin_params)
            full_query = f"{readable_participant_query}\n\n{readable_bin_query}"

            return jsonify({
                "success": True,
                "message": "BIN route created successfully",
                "query": full_query,
                "execution_time": round(execution_time, 3),
                "bin_holder_name": holder_name if bin_prefix else None
            })
        except Exception as e:
            print(f"Error executing insert query: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/routes/<int:route_id>', methods=['PUT'])
def update_route_unified(route_id):
    try:
        data = request.get_json() or {}
        new_priority = data.get('priority')
        new_gateway_id = data.get('gateway_id')
        new_participant_iss_id = data.get('participant_iss_id')
        new_participant_acq_id = data.get('participant_acq_id')
        # Allow explicit null-clear? No — empty string clears description/rule to "".
        has_rule = 'rule' in data
        has_description = 'description' in data
        new_rule = data.get('rule') if has_rule else None
        new_description = data.get('description') if has_description else None

        if all(
            v is None
            for v in [
                new_priority,
                new_gateway_id,
                new_participant_iss_id,
                new_participant_acq_id,
            ]
        ) and not has_rule and not has_description:
            return jsonify({
                "success": False,
                "error": (
                    "At least one field (priority, gateway_id, participant_iss_id, "
                    "participant_acq_id, rule, description) must be provided"
                ),
            }), 400

        # validations
        def to_int_or_none(v):
            if v is None:
                return None
            iv = int(v)
            if iv <= 0:
                raise ValueError('must be positive integer')
            return iv
        try:
            if new_priority is not None:
                new_priority = int(new_priority)
            if new_gateway_id is not None:
                new_gateway_id = to_int_or_none(new_gateway_id)
            if new_participant_iss_id is not None:
                new_participant_iss_id = to_int_or_none(new_participant_iss_id)
            if new_participant_acq_id is not None:
                new_participant_acq_id = to_int_or_none(new_participant_acq_id)
            if has_rule:
                new_rule = "" if new_rule is None else str(new_rule)
            if has_description:
                new_description = "" if new_description is None else str(new_description)
        except Exception as ve:
            return jsonify({"success": False, "error": f"Validation error: {ve}"}), 400

        connection_name = request.args.get('connection') or data.get('connection')
        if not connection_name:
            return jsonify({"success": False, "error": "Missing required field: connection"}), 400
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": "Database connection not found"}), 500

        update_fields = []
        params = []
        if new_priority is not None:
            update_fields.append("priority = %s")
            params.append(new_priority)
        if new_gateway_id is not None:
            update_fields.append("gateway_id = %s")
            params.append(new_gateway_id)
        if new_participant_iss_id is not None:
            update_fields.append("participant_iss_id = %s")
            params.append(new_participant_iss_id)
        if new_participant_acq_id is not None:
            update_fields.append("participant_acq_id = %s")
            params.append(new_participant_acq_id)
        if has_rule:
            update_fields.append("rule = %s")
            params.append(new_rule)
        if has_description:
            update_fields.append("description = %s")
            params.append(new_description)
        params.append(route_id)

        query = f"UPDATE bin_participant SET {', '.join(update_fields)} WHERE id = %s"
        result = execute_query(conn_details, query, params, fetch=False)
        if result:
            return jsonify({
                "success": True,
                "message": f"Route {route_id} updated successfully",
                "updated_fields": {
                    "priority": new_priority,
                    "gateway_id": new_gateway_id,
                    "participant_iss_id": new_participant_iss_id,
                    "participant_acq_id": new_participant_acq_id,
                    "rule": new_rule if has_rule else None,
                    "description": new_description if has_description else None,
                }
            })
        else:
            return jsonify({"success": False, "error": "Failed to update route"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/routes/<int:route_id>', methods=['DELETE'])
def delete_bin_route(route_id):
    try:
        data = request.get_json() or {}
        connection_name = data.get('connection')
        if not connection_name:
            return jsonify({"success": False, "error": "Connection name is required"}), 400
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

        query = "DELETE FROM bin_participant WHERE id = %s"
        params = [route_id]
        result = execute_query(conn_details, query, params, fetch=False)
        if result and result > 0:
            return jsonify({"success": True, "message": f"BIN route {route_id} deleted successfully"})
        else:
            return jsonify({"success": False, "error": f"BIN route {route_id} not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/export', methods=['GET'])
def export_bin_routes():
    try:
        connection_name = request.args.get('connection')
        if not connection_name:
            return jsonify({"success": False, "error": "Connection name is required"}), 400

        acquirer_id = request.args.get('acquirer_id', '')
        issuer_id = request.args.get('issuer_id', '')
        bin_number = request.args.get('bin', '')
        gateway_id = request.args.get('gateway_id', '')
        search_term = request.args.get('search', '')

        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

        query = """
        SELECT 
            bp.id,
            bp.participant_acq_id,
            ap.name AS `acq_name`,
            bd.bin_lower,
            bd.bin_upper,
            bp.gateway_id,
            bg.name AS `gateway_name`,
            bp.priority,
            bp.participant_iss_id,
            ip.name AS `iss_name`,
            ip.container_plugin_name,
            bp.rule,
            bp.description,
            bd.id AS `bin_details_id`
        FROM bin_participant bp
            RIGHT JOIN bin_details bd
                ON bp.gateway_id = bd.gateway_id
            LEFT JOIN bin_gateway bg
                ON bp.gateway_id = bg.id
            LEFT JOIN participant ap
                ON bp.participant_acq_id = ap.id
            LEFT JOIN participant ip
                ON bp.participant_iss_id = ip.id
        """

        where_clauses = []
        params = []
        if acquirer_id and acquirer_id.strip():
            where_clauses.append("bp.participant_acq_id = %s")
            params.append(int(acquirer_id.strip()))
        if issuer_id and issuer_id.strip():
            where_clauses.append("bp.participant_iss_id = %s")
            params.append(int(issuer_id.strip()))
        if bin_number and bin_number.strip():
            where_clauses.append(create_bin_filter_condition(bin_number.strip()))
        if gateway_id and gateway_id.strip():
            where_clauses.append("bp.gateway_id = %s")
            params.append(int(gateway_id.strip()))
        if search_term and search_term.strip():
            where_clauses.append("(ap.name LIKE %s OR ip.name LIKE %s OR bp.description LIKE %s)")
            params.extend([f"%{search_term.strip()}%", f"%{search_term.strip()}%", f"%{search_term.strip()}%"]) 

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY bp.priority DESC, bd.bin_lower ASC"

        routes = execute_query(conn_details, query, params)
        formatted = []
        for route in routes:
            if isinstance(route, dict):
                formatted.append(route)
            else:
                formatted.append({
                    "id": route[0],
                    "participant_acq_id": route[1],
                    "acq_name": route[2],
                    "bin_lower": route[3],
                    "bin_upper": route[4],
                    "gateway_id": route[5],
                    "gateway_name": route[6],
                    "priority": route[7],
                    "participant_iss_id": route[8],
                    "iss_name": route[9],
                    "container_plugin_name": route[10],
                    "rule": route[11],
                    "description": route[12],
                    "bin_details_id": route[13]
                })
        return jsonify(formatted)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/routes/gateway-priority/<int:route_id>', methods=['PUT'])
def update_route_gateway_priority(route_id):
    try:
        data = request.get_json() or {}
        new_priority = data.get('priority')
        new_gateway_id = data.get('gateway_id')
        if new_priority is None and new_gateway_id is None:
            return jsonify({"success": False, "error": "At least one field (priority or gateway_id) must be provided"}), 400
        try:
            if new_priority is not None:
                new_priority = int(new_priority)
            if new_gateway_id is not None:
                new_gateway_id = int(new_gateway_id)
        except Exception:
            return jsonify({"success": False, "error": "Invalid numeric values"}), 400

        connection_name = request.args.get('connection', 'default')
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": "Database connection not found"}), 500

        update_fields, params = [], []
        if new_priority is not None:
            update_fields.append("priority = %s")
            params.append(new_priority)
        if new_gateway_id is not None:
            update_fields.append("gateway_id = %s")
            params.append(new_gateway_id)
        params.append(route_id)

        query = f"UPDATE bin_participant SET {', '.join(update_fields)} WHERE id = %s"
        result = execute_query(conn_details, query, params, fetch=False)
        if result:
            return jsonify({"success": True, "message": f"Route {route_id} updated successfully", "updated_fields": {"priority": new_priority, "gateway_id": new_gateway_id}})
        else:
            return jsonify({"success": False, "error": "Failed to update route"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/routes/issuer-priority/<int:route_id>', methods=['PUT'])
def update_route_issuer_priority(route_id):
    try:
        data = request.get_json() or {}
        new_priority = data.get('priority')
        new_participant_iss_id = data.get('participant_iss_id')
        if new_priority is None and new_participant_iss_id is None:
            return jsonify({"success": False, "error": "At least one field (priority or participant_iss_id) must be provided"}), 400
        try:
            if new_priority is not None:
                new_priority = int(new_priority)
            if new_participant_iss_id is not None:
                new_participant_iss_id = int(new_participant_iss_id)
        except Exception:
            return jsonify({"success": False, "error": "Invalid numeric values"}), 400

        connection_name = request.args.get('connection', 'default')
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": "Database connection not found"}), 500

        update_fields, params = [], []
        if new_priority is not None:
            update_fields.append("priority = %s")
            params.append(new_priority)
        if new_participant_iss_id is not None:
            update_fields.append("participant_iss_id = %s")
            params.append(new_participant_iss_id)
        params.append(route_id)

        query = f"UPDATE bin_participant SET {', '.join(update_fields)} WHERE id = %s"
        result = execute_query(conn_details, query, params, fetch=False)
        if result:
            return jsonify({"success": True, "message": f"Route {route_id} updated successfully", "updated_fields": {"priority": new_priority, "participant_iss_id": new_participant_iss_id}})
        else:
            return jsonify({"success": False, "error": "Failed to update route"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/routes/gateway-update', methods=['PUT'])
def update_bin_details_gateway():
    try:
        data = request.get_json() or {}
        gateway_id = data.get('gateway_id')
        bin_details_id = data.get('bin_details_id')
        if gateway_id is None or bin_details_id is None:
            return jsonify({"success": False, "error": "gateway_id and bin_details_id are required"}), 400
        try:
            gateway_id = int(gateway_id)
            bin_details_id = int(bin_details_id)
        except Exception:
            return jsonify({"success": False, "error": "Invalid numeric values"}), 400

        connection_name = request.args.get('connection', 'default')
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": "Database connection not found"}), 500

        query = "UPDATE bin_details SET gateway_id = %s WHERE id = %s"
        params = [gateway_id, bin_details_id]
        result = execute_query(conn_details, query, params, fetch=False)
        if result:
            return jsonify({"success": True, "message": f"BIN Details {bin_details_id} gateway updated successfully", "updated_fields": {"gateway_id": gateway_id, "bin_details_id": bin_details_id}})
        else:
            return jsonify({"success": False, "error": "Failed to update BIN Details gateway"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/bin-details', methods=['POST'])
def create_bin_details():
    try:
        data = request.get_json() or {}
        bin_lower = data.get('bin_lower')
        bin_upper = data.get('bin_upper')
        gateway_id = data.get('gateway_id')
        bin_type = data.get('bin_type', 1)
        country = data.get('country', 360)
        currency = data.get('currency', 360)
        region = data.get('region', 'B')

        if not bin_lower or not bin_upper or not gateway_id:
            return jsonify({"success": False, "error": "bin_lower, bin_upper, and gateway_id are required"}), 400
        try:
            gateway_id = int(gateway_id)
            bin_type = int(bin_type)
            country = int(country)
            currency = int(currency)
        except Exception:
            return jsonify({"success": False, "error": "Invalid numeric values provided"}), 400

        connection_name = data.get('connection', 'default')
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": "Database connection not found"}), 500

        query = """
            INSERT INTO bin_details 
            (bin_lower, bin_upper, gateway_id, bin_type, country, currency, region) 
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        params = [bin_lower, bin_upper, gateway_id, bin_type, country, currency, region]
        result = execute_query(conn_details, query, params, fetch=False)
        if result is not None:
            return jsonify({"success": True, "message": "BIN details created successfully", "query": create_readable_query(query, params)})
        else:
            return jsonify({"success": False, "error": "Failed to create BIN details"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/bin-details/<int:bin_id>', methods=['PUT'])
def update_bin_details(bin_id):
    try:
        data = request.get_json() or {}
        bin_lower = data.get('bin_lower')
        bin_upper = data.get('bin_upper')
        gateway_id = data.get('gateway_id')
        bin_type = data.get('bin_type', 1)
        country = data.get('country', 360)
        currency = data.get('currency', 360)
        region = data.get('region', 'B')
        if not bin_lower or not bin_upper or not gateway_id:
            return jsonify({"success": False, "error": "Missing required fields: bin_lower, bin_upper, gateway_id"}), 400
        try:
            gateway_id = int(gateway_id)
            bin_type = int(bin_type)
            country = int(country)
            currency = int(currency)
        except Exception as e:
            return jsonify({"success": False, "error": f"Invalid numeric values provided: {str(e)}"}), 400
        if not str(bin_lower).isdigit() or not str(bin_upper).isdigit():
            return jsonify({"success": False, "error": "BIN values must be numeric"}), 400
        if len(str(bin_lower)) > 19 or len(str(bin_upper)) > 19:
            return jsonify({"success": False, "error": "BIN values cannot exceed 19 digits"}), 400

        connection_name = data.get('connection', 'default')
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": "Database connection not found"}), 500

        check_query = "SELECT COUNT(*) as count FROM bin_details WHERE id = %s"
        check_result = execute_query(conn_details, check_query, [bin_id])
        if not check_result or (isinstance(check_result[0], dict) and check_result[0].get('count', 0) == 0):
            return jsonify({"success": False, "error": f"BIN details record with ID {bin_id} not found"}), 404

        query = """
            UPDATE bin_details 
            SET bin_lower = %s, bin_upper = %s, gateway_id = %s, bin_type = %s, country = %s, currency = %s, region = %s
            WHERE id = %s
        """
        params = [bin_lower, bin_upper, gateway_id, bin_type, country, currency, region, bin_id]
        result = execute_query(conn_details, query, params, fetch=False)
        if result is not None and result >= 0:
            return jsonify({"success": True, "message": f"BIN details updated successfully. {result} row(s) affected.", "query": create_readable_query(query, params), "rows_affected": result})
        else:
            return jsonify({"success": False, "error": "Failed to update BIN details - no rows affected"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/bin-details/<int:bin_id>', methods=['DELETE'])
def delete_bin_details(bin_id):
    try:
        data = request.get_json() or {}
        connection_name = data.get('connection')
        if not connection_name:
            return jsonify({"success": False, "error": "Connection name is required"}), 400
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

        check_query = "SELECT COUNT(*) as count FROM bin_details WHERE id = %s"
        check_result = execute_query(conn_details, check_query, [bin_id])
        if not check_result or (isinstance(check_result[0], dict) and check_result[0].get('count', 0) == 0):
            return jsonify({"success": False, "error": f"BIN details record with ID {bin_id} not found"}), 404

        query = "DELETE FROM bin_details WHERE id = %s"
        params = [bin_id]
        result = execute_query(conn_details, query, params, fetch=False)
        if result and result > 0:
            return jsonify({"success": True, "message": f"BIN details record {bin_id} deleted successfully.", "rows_affected": result})
        else:
            return jsonify({"success": False, "error": "Failed to delete BIN details - no rows affected"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/bin-details/test-connection', methods=['GET'])
def test_bin_details_connection():
    try:
        connection_name = request.args.get('connection', 'default')
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": "Database connection not found"}), 500
        test_query = "SELECT COUNT(*) as count FROM bin_details LIMIT 1"
        result = execute_query(conn_details, test_query)
        count_val = 0
        if result:
            if isinstance(result[0], dict):
                count_val = result[0].get('count', 0)
            else:
                count_val = result[0][0]
        return jsonify({"success": True, "message": "Database connection successful", "table_count": count_val})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bin_routing_bp.route('/bin-list/debug/<bin_number>', methods=['GET'])
def debug_bin_list_entry(bin_number):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT id, BIN, Issuer FROM bin_list WHERE BIN = ?", (bin_number,))
        row = cursor.fetchone()
        if row:
            result = {"success": True, "data": {"id": row[0], "bin": row[1], "issuer": row[2]}}
        else:
            result = {"success": False, "error": f"BIN {bin_number} not found in bin_list table"}
        conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
