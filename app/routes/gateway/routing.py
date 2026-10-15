from flask import Blueprint, request, jsonify
import os
import sqlite3
from app.routes.auth import verify_token


routing_api_bp = Blueprint('routing_api', __name__)
# --- Auth helpers ---
@routing_api_bp.before_request
def _require_auth_and_rbac():
    # Allow preflight
    if request.method == 'OPTIONS':
        return None
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'success': False, 'error': 'Authorization header required'}), 401
    token = auth_header.split(' ')[1]
    payload = verify_token(token)
    if not payload:
        return jsonify({'success': False, 'error': 'Invalid or expired token'}), 401
    # Enforce can_crud for mutating methods
    if request.method in ['POST', 'PUT', 'DELETE']:
        if not payload.get('can_crud', False):
            return jsonify({'success': False, 'error': 'Insufficient permissions'}), 403
    # Attach payload for handlers if needed
    request.user = payload
    return None


# Constants
SQLITE_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'instance', 'myapp_db.db')


# --- DB helpers ---
def get_connection_details(connection_name):
    """Load connection details from SQLite `connection` table."""
    conn = None
    try:
        conn = sqlite3.connect(SQLITE_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM connection WHERE name = ?", (connection_name,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"Error getting connection details: {e}")
        return None
    finally:
        if conn:
            conn.close()


def execute_query(conn_details, query, params=None):
    """Execute SQL on target DB. Uses MySQL if connection_type == 'DB', else falls back to SQLite."""
    try:
        if conn_details.get('connection_type') == 'DB':
            import pymysql
            conn = pymysql.connect(
                host=conn_details.get('host'),
                port=int(conn_details.get('port') or 3306),
                user=conn_details.get('user'),
                password=conn_details.get('password'),
                database=conn_details.get('database'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
            cursor = conn.cursor()
            cursor.execute(query, params or [])
            if query.strip().upper().startswith('SELECT'):
                result = cursor.fetchall()
            else:
                conn.commit()
                result = {"affected_rows": cursor.rowcount}
            cursor.close()
            conn.close()
            return result
        else:
            conn = sqlite3.connect(SQLITE_DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if params:
                # Convert %s placeholders to ? for SQLite if present
                query_sqlite = query.replace('%s', '?')
                cursor.execute(query_sqlite, params)
            else:
                cursor.execute(query)
            if query.strip().upper().startswith('SELECT'):
                rows = cursor.fetchall()
                result = [dict(r) for r in rows]
            else:
                conn.commit()
                result = {"affected_rows": cursor.rowcount}
            cursor.close()
            conn.close()
            return result
    except Exception as e:
        print(f"DB error: {e}")
        raise


def format_routes_for_frontend(routes):
    formatted_routes = []
    for route in routes:
        path = []
        if route.get('Acq') and route.get('Acq Name'):
            path.append({"id": route.get('Acq Name'), "type": "acquirer"})
        if route.get('gateway_id') and route.get('Gateway Name'):
            path.append({"id": route.get('Gateway Name'), "type": "switch"})
        if route.get('Iss') and route.get('Issuer Name'):
            path.append({"id": route.get('Issuer Name'), "type": "issuer"})
        bin_range = ""
        if route.get('bin_lower') and route.get('bin_upper'):
            bin_range = f"{route.get('bin_lower')} - {route.get('bin_upper')}"
        formatted_routes.append({
            "id": route.get('id'),
            "messageType": (route.get('rule') or '').split(' ')[0] if route.get('rule') else '',
            "acquirer": route.get('Acq'),
            "acquirerName": route.get('Acq Name'),
            "issuer": route.get('Iss'),
            "issuerName": route.get('Issuer Name'),
            "binRange": bin_range,
            "binLower": route.get('bin_lower'),
            "binUpper": route.get('bin_upper'),
            "path": path,
            "priority": route.get('priority'),
            "status": route.get('status', 'active'),
            "description": route.get('description')
        })
    return formatted_routes


def has_column(conn_details, table_name, column_name):
    """Check if a column exists in a table for the active connection."""
    try:
        if conn_details.get('connection_type') == 'DB':
            import pymysql
            conn = pymysql.connect(
                host=conn_details.get('host'),
                port=int(conn_details.get('port') or 3306),
                user=conn_details.get('user'),
                password=conn_details.get('password'),
                database=conn_details.get('database'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s AND column_name = %s
                """,
                [conn_details.get('database'), table_name, column_name]
            )
            exists = (cursor.fetchone() or {}).get('cnt', 0) > 0
            cursor.close()
            conn.close()
            return exists
        else:
            conn = sqlite3.connect(SQLITE_DB_PATH)
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({table_name})")
            cols = [row[1] for row in cursor.fetchall()]
            cursor.close()
            conn.close()
            return column_name in cols
    except Exception as e:
        print(f"has_column error: {e}")
        return False


# --- Routing: routes listing/export/import ---
@routing_api_bp.route('/routes', methods=['GET'])
def list_routes():
    connection_name = request.args.get('connection')
    if not connection_name:
        return jsonify({"success": False, "error": "Connection name is required"}), 400

    acquirer_filter = request.args.get('acquirer', '')
    issuer_filter = request.args.get('issuer', '')
    msg_type_filter = request.args.get('messageType', '')
    status_filter = request.args.get('status', '')
    search_term = request.args.get('search', '')

    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

    bp_has_status = has_column(conn_details, 'bin_participant', 'status')

    select_cols = [
        "bp.id",
        "bp.participant_acq_id AS `Acq`",
        "ap.name AS `Acq Name`",
        "bd.bin_lower",
        "bd.bin_upper",
        "bp.gateway_id",
        "bg.name AS `Gateway Name`",
        "bp.priority",
        "bp.participant_iss_id AS `Iss`",
        "ip.name AS `Issuer Name`",
        "ip.container_plugin_name",
        "bp.rule",
        "bp.description"
    ]
    if bp_has_status:
        select_cols.append("bp.status")

    query = f"""
        SELECT 
            {', '.join(select_cols)}
        FROM bin_participant bp
            RIGHT JOIN bin_details bd ON bp.gateway_id = bd.gateway_id
            LEFT JOIN bin_gateway bg ON bp.gateway_id = bg.id
            LEFT JOIN participant ap ON bp.participant_acq_id = ap.id
            LEFT JOIN participant ip ON bp.participant_iss_id = ip.id
    """
    where_clauses = []
    params = []
    if acquirer_filter:
        where_clauses.append("bp.participant_acq_id = %s")
        params.append(acquirer_filter)
    if issuer_filter:
        where_clauses.append("bp.participant_iss_id = %s")
        params.append(issuer_filter)
    if msg_type_filter:
        where_clauses.append("bp.rule LIKE %s")
        params.append(f"%{msg_type_filter}%")
    if status_filter and bp_has_status:
        where_clauses.append("bp.status = %s")
        params.append(status_filter)
    if search_term:
        where_clauses.append("(ap.name LIKE %s OR ip.name LIKE %s OR bp.description LIKE %s)")
        params.extend([f"%{search_term}%", f"%{search_term}%", f"%{search_term}%"])
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    query += " ORDER BY bp.priority DESC, bd.bin_lower ASC"

    try:
        routes = execute_query(conn_details, query, params)
        return jsonify({"success": True, "routes": format_routes_for_frontend(routes)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@routing_api_bp.route('/routes', methods=['POST'])
def create_route():
    data = request.get_json() or {}
    required_fields = ['messageTypes', 'acquirer', 'issuer', 'path', 'priority', 'connection']
    for field in required_fields:
        if field not in data:
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

    connection_name = data.get('connection')
    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

    message_types = data.get('messageTypes') or []
    acquirer = data.get('acquirer')
    issuer = data.get('issuer')
    priority = data.get('priority')
    bin_lower = data.get('binLower')
    bin_upper = data.get('binUpper')
    bin_type = data.get('bin_type', 1)
    country = data.get('country', 360)
    currency = data.get('currency', 360)
    region = data.get('region', 'B')
    description = data.get('description', '')
    status = data.get('status', 'active')
    path = data.get('path') or []

    if not message_types:
        return jsonify({"success": False, "error": "At least one message type is required"}), 400

    gateway_id = None
    for node in path:
        if node.get('type') == 'switch':
            gateway_id = node.get('id') if isinstance(node.get('id'), int) else None
            break
    if gateway_id is None:
        gateway_id = 1

    if len(message_types) == 1:
        rule = message_types[0]
    else:
        rule = ' OR '.join([f"rule LIKE '%{t}%'" for t in message_types])

    bp_has_status = has_column(conn_details, 'bin_participant', 'status')
    if bp_has_status:
        insert_sql = """
            INSERT INTO bin_participant (
                participant_acq_id, participant_iss_id, gateway_id,
                priority, rule, description, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        params = [acquirer, issuer, gateway_id, priority, rule, description, status]
    else:
        insert_sql = """
            INSERT INTO bin_participant (
                participant_acq_id, participant_iss_id, gateway_id,
                priority, rule, description
            ) VALUES (%s, %s, %s, %s, %s, %s)
        """
        params = [acquirer, issuer, gateway_id, priority, rule, description]

    try:
        execute_query(conn_details, insert_sql, params)
        # Optionally insert BIN range
        if bin_lower and bin_upper:
            bin_sql = """
                INSERT INTO bin_details (gateway_id, bin_lower, bin_upper, bin_type, country, currency, region)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            execute_query(conn_details, bin_sql, [gateway_id, bin_lower, bin_upper, bin_type, country, currency, region])
        return jsonify({"success": True, "message": "Route created successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@routing_api_bp.route('/routes/<int:route_id>', methods=['PUT'])
def update_route(route_id):
    data = request.get_json() or {}
    required_fields = ['messageTypes', 'acquirer', 'issuer', 'path', 'priority', 'connection']
    for field in required_fields:
        if field not in data:
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

    connection_name = data.get('connection')
    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

    message_types = data.get('messageTypes') or []
    acquirer = data.get('acquirer')
    issuer = data.get('issuer')
    priority = data.get('priority')
    bin_lower = data.get('binLower')
    bin_upper = data.get('binUpper')
    description = data.get('description', '')
    status = data.get('status', 'active')
    path = data.get('path') or []

    if not message_types:
        return jsonify({"success": False, "error": "At least one message type is required"}), 400

    gateway_id = None
    for node in path:
        if node.get('type') == 'switch':
            gateway_id = node.get('id') if isinstance(node.get('id'), int) else None
            break
    if gateway_id is None:
        gateway_id = 1

    if len(message_types) == 1:
        rule = message_types[0]
    else:
        rule = ' OR '.join([f"rule LIKE '%{t}%'" for t in message_types])

    bp_has_status = has_column(conn_details, 'bin_participant', 'status')
    if bp_has_status:
        update_sql = """
            UPDATE bin_participant 
            SET participant_acq_id = %s,
                participant_iss_id = %s,
                gateway_id = %s,
                priority = %s,
                rule = %s,
                description = %s,
                status = %s
            WHERE id = %s
        """
        params = [acquirer, issuer, gateway_id, priority, rule, description, status, route_id]
    else:
        update_sql = """
            UPDATE bin_participant 
            SET participant_acq_id = %s,
                participant_iss_id = %s,
                gateway_id = %s,
                priority = %s,
                rule = %s,
                description = %s
            WHERE id = %s
        """
        params = [acquirer, issuer, gateway_id, priority, rule, description, route_id]

    try:
        result = execute_query(conn_details, update_sql, params)
        if result and isinstance(result, dict) and result.get('affected_rows', 0) == 0:
            return jsonify({"success": False, "error": f"Route {route_id} not found"}), 404

        if bin_lower and bin_upper:
            # Try update existing, else insert
            select_sql = "SELECT id FROM bin_details WHERE gateway_id = %s"
            rows = execute_query(conn_details, select_sql, [gateway_id])
            if rows:
                bin_sql = """
                    UPDATE bin_details
                    SET bin_lower = %s, bin_upper = %s
                    WHERE gateway_id = %s
                """
                execute_query(conn_details, bin_sql, [bin_lower, bin_upper, gateway_id])
            else:
                bin_sql = """
                    INSERT INTO bin_details (gateway_id, bin_lower, bin_upper)
                    VALUES (%s, %s, %s)
                """
                execute_query(conn_details, bin_sql, [gateway_id, bin_lower, bin_upper])

        return jsonify({"success": True, "message": f"Route {route_id} updated successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@routing_api_bp.route('/routes/<int:route_id>', methods=['DELETE'])
def delete_route(route_id):
    data = request.get_json() or {}
    connection_name = data.get('connection')
    if not connection_name:
        return jsonify({"success": False, "error": "Connection name is required"}), 400
    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
    try:
        result = execute_query(conn_details, "DELETE FROM bin_participant WHERE id = %s", [route_id])
        if result and isinstance(result, dict) and result.get('affected_rows', 0) == 0:
            return jsonify({"success": False, "error": f"Route {route_id} not found"}), 404
        return jsonify({"success": True, "message": f"Route {route_id} deleted successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@routing_api_bp.route('/routes/<int:route_id>/gateway', methods=['PUT'])
def update_route_gateway(route_id):
    data = request.get_json() or {}
    connection_name = data.get('connection')
    gateway_id = data.get('gateway_id')
    if not connection_name or gateway_id is None:
        return jsonify({"success": False, "error": "connection and gateway_id are required"}), 400
    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
    try:
        result = execute_query(conn_details, "UPDATE bin_participant SET gateway_id = %s WHERE id = %s", [gateway_id, route_id])
        if result and isinstance(result, dict) and result.get('affected_rows', 0) == 0:
            return jsonify({"success": False, "error": f"Route {route_id} not found"}), 404
        return jsonify({"success": True, "message": f"Gateway for route {route_id} updated successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@routing_api_bp.route('/routes/<int:route_id>/issuer', methods=['PUT'])
def update_route_issuer(route_id):
    data = request.get_json() or {}
    connection_name = data.get('connection')
    issuer_id = data.get('issuer_id')
    if not connection_name or issuer_id is None:
        return jsonify({"success": False, "error": "connection and issuer_id are required"}), 400
    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
    try:
        result = execute_query(conn_details, "UPDATE bin_participant SET participant_iss_id = %s WHERE id = %s", [issuer_id, route_id])
        if result and isinstance(result, dict) and result.get('affected_rows', 0) == 0:
            return jsonify({"success": False, "error": f"Route {route_id} not found"}), 404
        return jsonify({"success": True, "message": f"Issuer for route {route_id} updated successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@routing_api_bp.route('/export', methods=['GET'])
def export_routes():
    connection_name = request.args.get('connection')
    if not connection_name:
        return jsonify({"success": False, "error": "Connection name is required"}), 400
    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
    bp_has_status = has_column(conn_details, 'bin_participant', 'status')
    select_cols = [
        "bp.id",
        "bp.participant_acq_id AS `Acq`",
        "ap.name AS `Acq Name`",
        "bd.bin_lower",
        "bd.bin_upper",
        "bp.gateway_id",
        "bg.name AS `Gateway Name`",
        "bp.priority",
        "bp.participant_iss_id AS `Iss`",
        "ip.name AS `Issuer Name`",
        "ip.container_plugin_name",
        "bp.rule",
        "bp.description"
    ]
    if bp_has_status:
        select_cols.append("bp.status")
    query = f"""
        SELECT 
            {', '.join(select_cols)}
        FROM bin_participant bp
            RIGHT JOIN bin_details bd ON bp.gateway_id = bd.gateway_id
            LEFT JOIN bin_gateway bg ON bp.gateway_id = bg.id
            LEFT JOIN participant ap ON bp.participant_acq_id = ap.id
            LEFT JOIN participant ip ON bp.participant_iss_id = ip.id
        ORDER BY bp.priority DESC, bd.bin_lower ASC
    """
    try:
        routes = execute_query(conn_details, query)
        return jsonify(format_routes_for_frontend(routes))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@routing_api_bp.route('/import', methods=['POST'])
def import_routes():
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "No file part"}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({"success": False, "error": "No selected file"}), 400
        if not (file.filename.endswith('.json') or file.filename.endswith('.csv')):
            return jsonify({"success": False, "error": "Unsupported file format"}), 400
        content = file.read().decode('utf-8')
        if file.filename.endswith('.json'):
            import json as _json
            routes = _json.loads(content)
        else:
            lines = content.strip().split('\n')
            headers = lines[0].split(',')
            routes = []
            for line in lines[1:]:
                values = line.split(',')
                obj = {}
                for i, h in enumerate(headers):
                    if i < len(values):
                        obj[h] = values[i]
                routes.append(obj)
        return jsonify({"success": True, "message": f"Successfully imported {len(routes)} routes"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# --- Routing Simulation ---
@routing_api_bp.route('/simulate', methods=['POST'])
def simulate_routing():
    """Simulate routing decision for a transaction-like input.

    Request JSON:
    {
      connection: string,
      messageType: string,   # e.g. "0200"
      bin: string,           # PAN/BIN prefix (6+ recommended)
      acquirer_id?: number,
      issuer_id?: number,
      gateway_id?: number
    }
    """
    try:
        data = request.get_json() or {}
        connection_name = data.get('connection')
        message_type = data.get('messageType', '').strip()
        pan_or_bin = (data.get('bin') or '').strip()
        provided_gateway_id = data.get('gateway_id')
        provided_acquirer_id = data.get('acquirer_id')
        provided_issuer_id = data.get('issuer_id')

        if not connection_name:
            return jsonify({"success": False, "error": "Connection name is required"}), 400
        if not message_type:
            return jsonify({"success": False, "error": "messageType is required"}), 400
        if not pan_or_bin:
            return jsonify({"success": False, "error": "bin is required"}), 400

        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404

        # Build candidate selection query: match BIN within range and rule contains messageType
        params = []
        if conn_details.get('connection_type') == 'DB':
            # MySQL/MariaDB range check
            bin_query = """
                SELECT 
                    bp.id,
                    bp.participant_acq_id AS `Acq`,
                    ap.name AS `Acq Name`,
                    bd.bin_lower,
                    bd.bin_upper,
                    bp.gateway_id,
                    bg.name AS `Gateway Name`,
                    bp.priority,
                    bp.participant_iss_id AS `Iss`,
                    ip.name AS `Issuer Name`,
                    ip.container_plugin_name,
                    bp.rule,
                    bp.description
                FROM bin_details bd
                JOIN bin_gateway bg ON bd.gateway_id = bg.id
                LEFT JOIN bin_participant bp ON bp.gateway_id = bd.gateway_id
                LEFT JOIN participant ap ON bp.participant_acq_id = ap.id
                LEFT JOIN participant ip ON bp.participant_iss_id = ip.id
                WHERE (bd.bin_lower <= CONVERT(CONCAT(%s,'0000000000000') USING UCS2)
                       AND bd.bin_upper >= CONVERT(CONCAT(%s,'9999999999999') USING UCS2))
                  AND (bp.rule LIKE %s)
                ORDER BY bp.priority DESC, bd.bin_lower ASC
            """
            params = [pan_or_bin, pan_or_bin, f"%{message_type}%"]
            rows = execute_query(conn_details, bin_query, params)
        else:
            # SQLite fallback: use LIKE prefix on bin_lower
            bin_query = """
                SELECT 
                    bp.id,
                    bp.participant_acq_id AS `Acq`,
                    ap.name AS `Acq Name`,
                    bd.bin_lower,
                    bd.bin_upper,
                    bp.gateway_id,
                    bg.name AS `Gateway Name`,
                    bp.priority,
                    bp.participant_iss_id AS `Iss`,
                    ip.name AS `Issuer Name`,
                    ip.container_plugin_name,
                    bp.rule,
                    bp.description
                FROM bin_details bd
                LEFT JOIN bin_gateway bg ON bd.gateway_id = bg.id
                LEFT JOIN bin_participant bp ON bp.gateway_id = bd.gateway_id
                LEFT JOIN participant ap ON bp.participant_acq_id = ap.id
                LEFT JOIN participant ip ON bp.participant_iss_id = ip.id
                WHERE bd.bin_lower LIKE ? AND bp.rule LIKE ?
                ORDER BY bp.priority DESC, bd.bin_lower ASC
            """
            params = [f"{pan_or_bin}%", f"%{message_type}%"]
            rows = execute_query(conn_details, bin_query, params)

        # enrich candidates with raw gateway_id for comparisons
        candidates = format_routes_for_frontend(rows)
        for idx, r in enumerate(rows):
            if idx < len(candidates):
                candidates[idx]['gateway_id'] = r.get('gateway_id')

        # Score candidates by matches: rule/bin already matched, add acquirer/issuer/gateway matches
        def score_candidate(c):
            score = 1  # base for BIN+rule match
            if provided_acquirer_id is not None and c.get('acquirer') is not None:
                try:
                    if int(c['acquirer']) == int(provided_acquirer_id):
                        score += 1
                except Exception:
                    pass
            if provided_issuer_id is not None and c.get('issuer') is not None:
                try:
                    if int(c['issuer']) == int(provided_issuer_id):
                        score += 1
                except Exception:
                    pass
            if provided_gateway_id is not None and c.get('gateway_id') is not None:
                try:
                    if int(c['gateway_id']) == int(provided_gateway_id):
                        score += 1
                except Exception:
                    pass
            return score

        candidates_sorted = sorted(candidates, key=score_candidate, reverse=True)
        best = candidates_sorted[0] if candidates_sorted else None

        # Determine match flags
        acq_match = iss_match = gw_match = None
        if best:
            if provided_acquirer_id is not None and best.get('acquirer') is not None:
                try:
                    acq_match = (int(best['acquirer']) == int(provided_acquirer_id))
                except Exception:
                    acq_match = False
            if provided_issuer_id is not None and best.get('issuer') is not None:
                try:
                    iss_match = (int(best['issuer']) == int(provided_issuer_id))
                except Exception:
                    iss_match = False
            if provided_gateway_id is not None and best.get('gateway_id') is not None:
                try:
                    gw_match = (int(best['gateway_id']) == int(provided_gateway_id))
                except Exception:
                    gw_match = False

        # Build status and reasons
        if not best:
            status_code = 'NO_MATCH'
            reasons = ['No route matched by BIN and messageType']
        else:
            reasons = []
            status_code = 'MATCH'  # base
            if acq_match is False:
                reasons.append('Acquirer differs from IN.ACQ_ID')
            if iss_match is False:
                reasons.append('Issuer differs from IN.ISS_ID')
            if gw_match is False:
                reasons.append('Gateway differs from IN.GATEWAY_ID')
            if reasons:
                status_code = 'PARTIAL_MATCH'

        return jsonify({
            "success": True,
            "summary": {
                "status": status_code,
                "reasons": reasons,
                "bestRouteId": best.get('id') if best else None,
                "provided": {
                    "acquirer_id": provided_acquirer_id,
                    "issuer_id": provided_issuer_id,
                    "gateway_id": provided_gateway_id
                },
                "matches": {
                    "acquirer": acq_match,
                    "issuer": iss_match,
                    "gateway": gw_match
                }
            },
            "candidates": candidates_sorted
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- BIN utilities ---
@routing_api_bp.route('/bins/search', methods=['GET'])
def search_bins():
    connection_name = request.args.get('connection')
    bin_number = request.args.get('bin')
    range_search = request.args.get('range_search', 'false').lower() == 'true'
    if not connection_name:
        return jsonify({"success": False, "error": "Connection name is required"}), 400
    if not bin_number:
        return jsonify({"success": False, "error": "BIN number is required"}), 400
    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
    if range_search:
        query = """
            SELECT bd.id, bd.bin_lower, bd.bin_upper, bd.gateway_id,
                   bd.bin_type, bd.country, bd.currency, bd.region,
                   bg.name as gateway_name
            FROM bin_details bd
            LEFT JOIN bin_gateway bg ON bd.gateway_id = bg.id
            WHERE bd.bin_lower LIKE %s
            ORDER BY bd.bin_lower
        """
        params = [f"{bin_number}%"]
    else:
        query = """
            SELECT bd.id, bd.bin_lower, bd.bin_upper, bd.gateway_id,
                   bd.bin_type, bd.country, bd.currency, bd.region,
                   bg.name as gateway_name
            FROM bin_details bd
            JOIN bin_gateway bg ON bd.gateway_id = bg.id
            WHERE (bd.bin_lower <= CONVERT(CONCAT(%s,'0000000000000') USING UCS2) 
                   AND bd.bin_upper >= CONVERT(CONCAT(%s,'9999999999999') USING UCS2))
            ORDER BY bd.bin_lower
        """
        params = [bin_number, bin_number]
    try:
        results = execute_query(conn_details, query, params)
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@routing_api_bp.route('/bins/by-gateway', methods=['GET'])
def get_bin_details_by_gateway():
    connection_name = request.args.get('connection')
    gateway_id = request.args.get('gateway_id')
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 50))
    search_term = request.args.get('search', '')
    if not connection_name:
        return jsonify({"success": False, "error": "Connection name is required"}), 400
    if not gateway_id:
        return jsonify({"success": False, "error": "Gateway ID is required"}), 400
    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
    offset = (page - 1) * page_size
    base = "FROM bin_details bd WHERE bd.gateway_id = %s"
    params = [gateway_id]
    if search_term:
        base += " AND (bd.bin_lower LIKE %s OR bd.bin_upper LIKE %s OR bd.bin_type LIKE %s OR bd.country LIKE %s OR bd.currency LIKE %s OR bd.region LIKE %s)"
        s = f"%{search_term}%"
        params.extend([s, s, s, s, s, s])
    try:
        total = execute_query(conn_details, f"SELECT COUNT(*) as total {base}", params)
        total_count = total[0]['total'] if total else 0
        data = execute_query(conn_details, f"""
            SELECT bd.id, bd.bin_lower, bd.bin_upper, bd.gateway_id,
                   bd.bin_type, bd.country, bd.currency, bd.region
            {base}
            ORDER BY bd.bin_lower ASC
            LIMIT %s OFFSET %s
        """ , params + [page_size, offset])
        for d in data:
            d['issuer_name'] = '-'
        total_pages = (total_count + page_size - 1) // page_size
        return jsonify({
            "success": True,
            "bin_details": data,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@routing_api_bp.route('/bins/holder-names', methods=['POST'])
def get_bin_holder_names_batch():
    try:
        data = request.get_json() or {}
        bin_numbers = data.get('bin_numbers', [])
        names = {}
        if bin_numbers:
            sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
            cursor = sqlite_conn.cursor()
            uniq = list(set(bin_numbers))
            placeholders = ','.join(['?'] * len(uniq))
            cursor.execute(f"SELECT BIN, Issuer FROM bin_list WHERE BIN IN ({placeholders})", uniq)
            for row in cursor.fetchall():
                names[row[0]] = row[1]
            cursor.close()
            sqlite_conn.close()
        return jsonify({"success": True, "bin_holder_names": names})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@routing_api_bp.route('/bins/<int:bin_id>', methods=['PUT'])
def update_bin_details(bin_id):
    data = request.get_json() or {}
    connection_name = data.get('connection')
    if not connection_name:
        return jsonify({"success": False, "error": "Connection name is required"}), 400
    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
    bin_lower = data.get('bin_lower')
    bin_upper = data.get('bin_upper')
    bin_type = data.get('bin_type')
    country = data.get('country')
    currency = data.get('currency')
    region = data.get('region')
    sql = """
        UPDATE bin_details
        SET bin_lower = %s, bin_upper = %s, bin_type = %s, country = %s, currency = %s, region = %s
        WHERE id = %s
    """
    params = [bin_lower, bin_upper, bin_type, country, currency, region, bin_id]
    try:
        execute_query(conn_details, sql, params)
        return jsonify({"success": True, "message": f"BIN detail {bin_id} updated successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@routing_api_bp.route('/bins/<int:bin_id>', methods=['DELETE'])
def delete_bin_details(bin_id):
    data = request.get_json() or {}
    connection_name = data.get('connection')
    if not connection_name:
        return jsonify({"success": False, "error": "Connection name is required"}), 400
    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
    try:
        execute_query(conn_details, "DELETE FROM bin_details WHERE id = %s", [bin_id])
        return jsonify({"success": True, "message": f"BIN detail {bin_id} deleted successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# --- Gateways (lightweight duplication for convenience) ---
@routing_api_bp.route('/gateways', methods=['GET'])
def get_gateways():
    connection_name = request.args.get('connection')
    search_term = request.args.get('search', '')
    if not connection_name:
        return jsonify({"success": False, "error": "Connection name is required"}), 400
    conn_details = get_connection_details(connection_name)
    if not conn_details:
        return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
    if search_term:
        query = "SELECT id, name FROM bin_gateway WHERE name LIKE %s ORDER BY name"
        params = [f"%{search_term}%"]
    else:
        query = "SELECT id, name FROM bin_gateway ORDER BY name"
        params = []
    try:
        gateways = execute_query(conn_details, query, params)
        return jsonify({"success": True, "gateways": gateways})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


