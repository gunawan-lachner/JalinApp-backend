from flask import Blueprint, request, jsonify, current_app
import sqlite3
import os
import json
import pymysql
from datetime import datetime
import pymysql.cursors
import re
import time

gateway_bp = Blueprint('gateway', __name__)

# Cache for BIN list data
_bin_list_cache = None
_bin_list_cache_time = 0
_cache_duration = 300  # 5 minutes

def load_bin_list_data():
    """Load BIN list data from JSON file with caching"""
    global _bin_list_cache, _bin_list_cache_time
    
    current_time = time.time()
    
    # Return cached data if still valid
    if _bin_list_cache is not None and (current_time - _bin_list_cache_time) < _cache_duration:
        return _bin_list_cache
    
    # JSON source no longer used; return empty list
    _bin_list_cache = []
    _bin_list_cache_time = current_time
    return _bin_list_cache

def get_bin_holder_name(bin_number):
    """Get holder name from bin_list table in SQLite database"""
    try:
        # Get SQLite connection using Flask instance_path (absolute)
        instance_dir = current_app.instance_path if current_app else os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'instance')
        os.makedirs(instance_dir, exist_ok=True)
        db_path = os.path.join(instance_dir, 'myapp_db.db')
        sqlite_conn = sqlite3.connect(db_path)
        sqlite_cursor = sqlite_conn.cursor()
        
        # Query the bin_list table for the BIN number
        sqlite_cursor.execute("SELECT Issuer FROM bin_list WHERE BIN = ?", (str(bin_number),))
        result = sqlite_cursor.fetchone()
        
        sqlite_cursor.close()
        sqlite_conn.close()
        
        return result[0] if result else ''
    except Exception as e:
        print(f"Error getting BIN holder name from SQLite: {e}")
        return ''

def update_bin_holder_name(bin_number, holder_name):
    """Update holder name in bin_list table in SQLite database"""
    try:
        # Get SQLite connection
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'instance', 'myapp_db.db')
        sqlite_conn = sqlite3.connect(db_path)
        sqlite_cursor = sqlite_conn.cursor()
        
        # Check if BIN exists in bin_list
        sqlite_cursor.execute("SELECT id FROM bin_list WHERE BIN = ?", (str(bin_number),))
        existing_record = sqlite_cursor.fetchone()
        
        if existing_record:
            # Update existing record
            sqlite_cursor.execute("UPDATE bin_list SET Issuer = ? WHERE BIN = ?", (holder_name, str(bin_number)))
        else:
            # Insert new record with minimal required fields
            sqlite_cursor.execute("""
                INSERT INTO bin_list (BIN, Issuer) 
                VALUES (?, ?)
            """, (str(bin_number), holder_name))
        
        sqlite_conn.commit()
        sqlite_cursor.close()
        sqlite_conn.close()
        
        return True
    except Exception as e:
        print(f"Error updating BIN holder name in SQLite at path {db_path}: {e}")
        return False

def get_db_connection():
    """Get SQLite database connection using Flask instance_path"""
    instance_dir = current_app.instance_path if current_app else os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'instance')
    os.makedirs(instance_dir, exist_ok=True)
    db_path = os.path.join(instance_dir, 'myapp_db.db')
    try:
        return sqlite3.connect(db_path)
    except Exception as e:
        print(f"SQLite connect failed at path {db_path}: {e}")
        raise

def load_profiles():
    """Load database profiles from SQLite"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, host, port, user, password, database FROM connection")
    profiles = []
    for row in cursor.fetchall():
        profiles.append({
            'name': row[0],
            'host': row[1],
            'port': row[2],
            'user': row[3],
            'password': row[4],
            'database': row[5]
        })
    conn.close()
    return profiles

def get_mysql_connection(profile_id='LOKAL'):
    """Get MySQL database connection using profile"""
    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    
    if not profile:
        raise Exception(f'Profile {profile_id} not found')
    
    return pymysql.connect(
        host=profile['host'],
        port=int(profile['port']) if profile.get('port') else 3306,
        user=profile['user'],
        password=profile['password'],
        database=profile['database'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def format_sql(sql, params):
    """Format SQL query for logging"""
    if not params:
        return sql
    try:
        return sql % tuple(params)
    except:
        return f"{sql} with params: {params}"

# Gateway Management Endpoints

@gateway_bp.route('/', methods=['GET', 'POST'])
def get_gateways():
    """Get all gateways with optional search and pagination"""
    try:
        # Get parameters from POST body or GET query params
        if request.method == 'POST':
            if not request.is_json:
                return jsonify({'error': 'Request body must be JSON'}), 400
            data = request.get_json()
            connection = data.get('connection', 'LOKAL')
            search = data.get('search', '')
            page = int(data.get('page', 1))
            page_size = min(int(data.get('page_size', 20)), 100)  # Limit to 100 max
        else:
            # GET method - query parameters
            connection = request.args.get('connection', 'LOKAL')
            search = request.args.get('search', '')
            page = int(request.args.get('page', 1))
            page_size = min(int(request.args.get('page_size', 20)), 100)  # Limit to 100 max
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Build SQL query
        sql = """
            SELECT g.id, g.name, 
                   COUNT(bd.id) as bin_count
            FROM bin_gateway g
            LEFT JOIN bin_details bd ON g.id = bd.gateway_id
        """
        
        where_clause = ""
        params = []
        
        if search:
            where_clause = " WHERE g.name LIKE %s OR g.id LIKE %s"
            params = [f'%{search}%', f'%{search}%']
        
        sql += where_clause
        sql += " GROUP BY g.id, g.name ORDER BY g.name ASC LIMIT %s OFFSET %s"
        params.extend([page_size, offset])
        
        # Get total count for pagination
        count_sql = """
            SELECT COUNT(DISTINCT g.id) as total
            FROM bin_gateway g
        """
        if search:
            count_sql += " WHERE g.name LIKE %s OR g.id LIKE %s"
            count_params = [f'%{search}%', f'%{search}%']
        else:
            count_params = []
        
        # Execute queries
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Get total count for pagination info
        mysql_cursor.execute(count_sql, count_params)
        total_result = mysql_cursor.fetchone()
        total_count = total_result['total'] if total_result else 0
        
        # Get gateways
        mysql_cursor.execute(sql, params)
        gateways = mysql_cursor.fetchall()
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'data': gateways,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_count': total_count,
                'has_more': len(gateways) == page_size
            },
            'connection': connection
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to get gateways: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify table structure', 'Check query syntax']
            }
        }), 500

@gateway_bp.route('/new', methods=['POST'])
def create_gateway():
    """Create a new gateway"""
    try:
        data = request.get_json()
        connection = data.get('connection', 'LOKAL')
        gateway_id = data.get('id', '')
        name = data.get('name', '').strip()
        
        # Validation
        if not name:
            return jsonify({
                'success': False,
                'error': 'Gateway name is required'
            }), 400
        
        # Check if gateway name already exists
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Check for duplicate name
        mysql_cursor.execute("SELECT id FROM bin_gateway WHERE name = %s", (name,))
        if mysql_cursor.fetchone():
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': f'Gateway with name "{name}" already exists'
            }), 400
        
        # If ID is provided, check if it exists
        if gateway_id:
            mysql_cursor.execute("SELECT id FROM bin_gateway WHERE id = %s", (gateway_id,))
            if mysql_cursor.fetchone():
                mysql_cursor.close()
                mysql_conn.close()
                return jsonify({
                    'success': False,
                    'error': f'Gateway with ID "{gateway_id}" already exists'
                }), 400
        
        # Insert new gateway
        if gateway_id:
            sql = "INSERT INTO bin_gateway (id, name) VALUES (%s, %s)"
            params = [gateway_id, name]
        else:
            # Auto-generate ID
            sql = "INSERT INTO bin_gateway (name) VALUES (%s)"
            params = [name]
        
        mysql_cursor.execute(sql, params)
        mysql_conn.commit()
        
        # Get the inserted gateway
        inserted_id = gateway_id if gateway_id else mysql_cursor.lastrowid
        mysql_cursor.execute("SELECT id, name FROM bin_gateway WHERE id = %s", (inserted_id,))
        new_gateway = mysql_cursor.fetchone()
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Gateway created successfully',
            'data': new_gateway
        }), 201
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to create gateway: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify table structure', 'Check for duplicate entries']
            }
        }), 500

@gateway_bp.route('/<gateway_id>', methods=['GET'])
def get_gateway(gateway_id):
    """Get specific gateway by ID"""
    try:
        connection = request.args.get('connection', 'LOKAL')
        
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Get gateway details
        mysql_cursor.execute("""
            SELECT g.id, g.name, COUNT(bd.id) as bin_count
            FROM bin_gateway g
            LEFT JOIN bin_details bd ON g.id = bd.gateway_id
            WHERE g.id = %s
            GROUP BY g.id, g.name
        """, (gateway_id,))
        
        gateway = mysql_cursor.fetchone()
        
        if not gateway:
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': f'Gateway with ID "{gateway_id}" not found'
            }), 404
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'data': gateway
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to get gateway: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify gateway ID', 'Check table structure']
            }
        }), 500

@gateway_bp.route('/<gateway_id>', methods=['PUT'])
def update_gateway(gateway_id):
    """Update gateway by ID"""
    try:
        data = request.get_json()
        connection = data.get('connection', 'LOKAL')
        name = data.get('name', '').strip()
        
        # Validation
        if not name:
            return jsonify({
                'success': False,
                'error': 'Gateway name is required'
            }), 400
        
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Check if gateway exists
        mysql_cursor.execute("SELECT id FROM bin_gateway WHERE id = %s", (gateway_id,))
        if not mysql_cursor.fetchone():
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': f'Gateway with ID "{gateway_id}" not found'
            }), 404
        
        # Check if new name already exists (excluding current gateway)
        mysql_cursor.execute("SELECT id FROM bin_gateway WHERE name = %s AND id != %s", (name, gateway_id))
        if mysql_cursor.fetchone():
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': f'Gateway with name "{name}" already exists'
            }), 400
        
        # Update gateway
        mysql_cursor.execute("UPDATE bin_gateway SET name = %s WHERE id = %s", (name, gateway_id))
        mysql_conn.commit()
        
        # Get updated gateway
        mysql_cursor.execute("""
            SELECT g.id, g.name, COUNT(bd.id) as bin_count
            FROM bin_gateway g
            LEFT JOIN bin_details bd ON g.id = bd.gateway_id
            WHERE g.id = %s
            GROUP BY g.id, g.name
        """, (gateway_id,))
        
        updated_gateway = mysql_cursor.fetchone()
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Gateway updated successfully',
            'data': updated_gateway
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to update gateway: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify gateway ID', 'Check for duplicate names']
            }
        }), 500

@gateway_bp.route('/<gateway_id>', methods=['DELETE'])
def delete_gateway(gateway_id):
    """Delete gateway by ID (only if no BINs are associated)"""
    try:
        connection = request.args.get('connection', 'LOKAL')
        
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Check if gateway exists
        mysql_cursor.execute("SELECT id, name FROM bin_gateway WHERE id = %s", (gateway_id,))
        gateway = mysql_cursor.fetchone()
        
        if not gateway:
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': f'Gateway with ID "{gateway_id}" not found'
            }), 404
        
        # Check if gateway has associated BINs
        mysql_cursor.execute("SELECT COUNT(*) as bin_count FROM bin_details WHERE gateway_id = %s", (gateway_id,))
        bin_count_result = mysql_cursor.fetchone()
        bin_count = bin_count_result['bin_count'] if bin_count_result else 0
        
        if bin_count > 0:
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': f'Cannot delete gateway: {bin_count} BIN(s) are associated with this gateway. Please remove all BINs first.'
            }), 400
        
        # Delete gateway
        mysql_cursor.execute("DELETE FROM bin_gateway WHERE id = %s", (gateway_id,))
        mysql_conn.commit()
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Gateway "{gateway["name"]}" deleted successfully'
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to delete gateway: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify gateway ID', 'Check for foreign key constraints']
            }
        }), 500

# BIN Management Endpoints

@gateway_bp.route('/<gateway_id>/bins', methods=['GET', 'POST'])
def get_gateway_bins(gateway_id):
    """Get BINs for a specific gateway with pagination"""
    try:
        # Get parameters from POST body or GET query params
        if request.method == 'POST':
            if not request.is_json:
                return jsonify({'error': 'Request body must be JSON'}), 400
            data = request.get_json()
            connection = data.get('connection', 'LOKAL')
            page = int(data.get('page', 1))
            page_size = min(int(data.get('page_size', 50)), 100)  # Limit to 100 max
            search = data.get('search', '')
        else:
            # GET method - query parameters
            connection = request.args.get('connection', 'LOKAL')
            page = int(request.args.get('page', 1))
            page_size = min(int(request.args.get('page_size', 50)), 100)  # Limit to 100 max
            search = request.args.get('search', '')
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Build SQL query
        sql = """
            SELECT id, gateway_id, bin_lower, bin_upper, 
                   bin_type, country, currency, region
            FROM bin_details
            WHERE gateway_id = %s
        """
        
        params = [gateway_id]
        
        if search:
            sql += " AND (bin_lower LIKE %s OR bin_upper LIKE %s)"
            search_param = f'%{search}%'
            params.extend([search_param, search_param])
        
        # Get total count
        count_sql = "SELECT COUNT(*) as total FROM bin_details WHERE gateway_id = %s"
        count_params = [gateway_id]
        
        if search:
            count_sql += " AND (bin_lower LIKE %s OR bin_upper LIKE %s)"
            search_param = f'%{search}%'
            count_params.extend([search_param, search_param])
        
        # Add pagination
        sql += " ORDER BY bin_lower ASC LIMIT %s OFFSET %s"
        params.extend([page_size, offset])
        
        # Execute queries
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Get total count for pagination info
        mysql_cursor.execute(count_sql, count_params)
        total_result = mysql_cursor.fetchone()
        total_count = total_result['total'] if total_result else 0
        
        # Get BINs
        mysql_cursor.execute(sql, params)
        bins = mysql_cursor.fetchall()
        
        # Add holder names from JSON data (only if needed)
        if len(bins) <= 50:  # Only enrich if reasonable amount
            for bin_item in bins:
                # Extract first 6 digits from bin_lower as BIN number
                bin_number = bin_item['bin_lower'][:6] if len(bin_item['bin_lower']) >= 6 else bin_item['bin_lower']
                bin_item['bin_holder_name'] = get_bin_holder_name(bin_number)
        else:
            # Skip holder name enrichment for large datasets
            for bin_item in bins:
                bin_item['bin_holder_name'] = ''
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'data': bins,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_count': total_count,
                'has_more': len(bins) == page_size
            },
            'gateway_id': gateway_id,
            'connection': connection
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to get BINs: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify gateway ID', 'Check table structure']
            }
        }), 500

@gateway_bp.route('/<gateway_id>/bins/new', methods=['POST'])
def add_bin_to_gateway(gateway_id):
    """Add a new BIN to a gateway"""
    try:
        data = request.get_json()
        connection = data.get('connection', 'LOKAL')
        
        # Extract BIN data
        bin_lower = data.get('bin_lower', '').strip()
        bin_upper = data.get('bin_upper', '').strip()
        bin_type = data.get('bin_type', 1)
        country = data.get('country', 360)
        currency = data.get('currency', 360)
        region = data.get('region', 'B')
        
        # Validation
        if not bin_lower or not bin_upper:
            return jsonify({
                'success': False,
                'error': 'BIN lower and upper are required'
            }), 400
        
        # Validate BIN format (6-19 digits)
        if not re.match(r'^\d{6,19}$', bin_lower) or not re.match(r'^\d{6,19}$', bin_upper):
            return jsonify({
                'success': False,
                'error': 'BIN must be 6-19 digits'
            }), 400
        
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Check if gateway exists
        mysql_cursor.execute("SELECT id FROM bin_gateway WHERE id = %s", (gateway_id,))
        if not mysql_cursor.fetchone():
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': f'Gateway with ID "{gateway_id}" not found'
            }), 404
        
        # BIN range overlap validation removed - allowing multiple BINs with same range
        
        # Insert new BIN
        sql = """
            INSERT INTO bin_details (gateway_id, bin_lower, bin_upper, 
                                   bin_type, country, currency, region)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        params = [gateway_id, bin_lower, bin_upper, 
                 bin_type, country, currency, region]
        
        mysql_cursor.execute(sql, params)
        mysql_conn.commit()
        
        # Get the inserted BIN
        bin_id = mysql_cursor.lastrowid
        mysql_cursor.execute("""
            SELECT id, gateway_id, bin_lower, bin_upper, 
                   bin_type, country, currency, region
            FROM bin_details WHERE id = %s
        """, (bin_id,))
        
        new_bin = mysql_cursor.fetchone()
        
        # Add holder name from JSON data
        bin_number = new_bin['bin_lower'][:6] if len(new_bin['bin_lower']) >= 6 else new_bin['bin_lower']
        new_bin['bin_holder_name'] = get_bin_holder_name(bin_number)
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'message': 'BIN added successfully',
            'data': new_bin
        }), 201
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to add BIN: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify gateway ID', 'Check for duplicate BINs']
            }
        }), 500

@gateway_bp.route('/bins/<int:bin_id>', methods=['GET'])
def get_bin(bin_id):
    """Get specific BIN by ID"""
    try:
        connection = request.args.get('connection', 'LOKAL')
        
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        mysql_cursor.execute("""
            SELECT bd.id, bd.gateway_id, bd.bin_lower, bd.bin_upper,
                   bd.bin_type, bd.country, bd.currency, bd.region,
                   bg.name as gateway_name
            FROM bin_details bd
            LEFT JOIN bin_gateway bg ON bd.gateway_id = bg.id
            WHERE bd.id = %s
        """, (bin_id,))
        
        bin_data = mysql_cursor.fetchone()
        
        if not bin_data:
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': f'BIN with ID {bin_id} not found'
            }), 404
        
        # Add holder name from JSON data
        bin_number = bin_data['bin_lower'][:6] if len(bin_data['bin_lower']) >= 6 else bin_data['bin_lower']
        bin_data['bin_holder_name'] = get_bin_holder_name(bin_number)
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'data': bin_data
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to get BIN: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify BIN ID', 'Check table structure']
            }
        }), 500

@gateway_bp.route('/bins/<int:bin_id>', methods=['PUT'])
def update_bin(bin_id):
    """Update BIN by ID"""
    try:
        data = request.get_json()
        connection = data.get('connection', 'LOKAL')
        
        # Extract BIN data
        bin_lower = data.get('bin_lower', '').strip()
        bin_upper = data.get('bin_upper', '').strip()
        bin_type = data.get('bin_type')
        country = data.get('country')
        currency = data.get('currency')
        region = data.get('region')
        gateway_id = data.get('gateway_id')
        bin_holder_name = data.get('bin_holder_name', '').strip()
        
        # Validation - only validate if provided
        if bin_lower and bin_upper:
            # Validate BIN format
            if not re.match(r'^\d{6,19}$', bin_lower) or not re.match(r'^\d{6,19}$', bin_upper):
                return jsonify({
                    'success': False,
                    'error': 'BIN must be 6-19 digits'
                }), 400
        elif bin_lower or bin_upper:
            return jsonify({
                'success': False,
                'error': 'Both BIN lower and upper must be provided together'
            }), 400
        
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Check if BIN exists
        mysql_cursor.execute("SELECT id, gateway_id FROM bin_details WHERE id = %s", (bin_id,))
        existing_bin = mysql_cursor.fetchone()
        
        if not existing_bin:
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': f'BIN with ID {bin_id} not found'
            }), 404
        
        # If gateway_id is being changed, verify it exists
        if gateway_id and gateway_id != existing_bin['gateway_id']:
            mysql_cursor.execute("SELECT id FROM bin_gateway WHERE id = %s", (gateway_id,))
            if not mysql_cursor.fetchone():
                mysql_cursor.close()
                mysql_conn.close()
                return jsonify({
                    'success': False,
                    'error': f'Gateway with ID "{gateway_id}" not found'
                }), 404
        
        # Check for duplicate BIN range (excluding current BIN) - only if both bin_lower and bin_upper are provided
        if bin_lower and bin_upper:
            mysql_cursor.execute("""
                SELECT id FROM bin_details 
                WHERE id != %s AND (
                    (bin_lower <= %s AND bin_upper >= %s) 
                    OR (bin_lower <= %s AND bin_upper >= %s)
                    OR (bin_lower >= %s AND bin_upper <= %s)
                )
            """, (bin_id, bin_lower, bin_lower, bin_upper, bin_upper, bin_lower, bin_upper))
            
            if mysql_cursor.fetchone():
                mysql_cursor.close()
                mysql_conn.close()
                return jsonify({
                    'success': False,
                    'error': 'BIN range overlaps with existing BIN'
                }), 400
        
        # Build update query
        update_fields = []
        params = []
        
        if bin_lower:
            update_fields.append("bin_lower = %s")
            params.append(bin_lower)
        if bin_upper:
            update_fields.append("bin_upper = %s")
            params.append(bin_upper)

        if bin_type is not None:
            update_fields.append("bin_type = %s")
            params.append(bin_type)
        if country is not None:
            update_fields.append("country = %s")
            params.append(country)
        if currency is not None:
            update_fields.append("currency = %s")
            params.append(currency)
        if region:
            update_fields.append("region = %s")
            params.append(region)
        if gateway_id:
            update_fields.append("gateway_id = %s")
            params.append(gateway_id)
        
        # If only bin_holder_name is provided and no other fields, we still need to proceed
        # because bin_holder_name updates the SQLite bin_list table, not MySQL bin_details
        if not update_fields and not bin_holder_name:
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': 'No fields to update'
            }), 400
        
        # Update MySQL bin_details table only if there are fields to update
        if update_fields:
            # Add BIN ID to params
            params.append(bin_id)
            
            # Update BIN
            sql = f"UPDATE bin_details SET {', '.join(update_fields)} WHERE id = %s"
            mysql_cursor.execute(sql, params)
            mysql_conn.commit()
        
        # Get updated BIN
        mysql_cursor.execute("""
            SELECT bd.id, bd.gateway_id, bd.bin_lower, bd.bin_upper,
                   bd.bin_type, bd.country, bd.currency, bd.region,
                   bg.name as gateway_name
            FROM bin_details bd
            LEFT JOIN bin_gateway bg ON bd.gateway_id = bg.id
            WHERE bd.id = %s
        """, (bin_id,))
        
        updated_bin = mysql_cursor.fetchone()
        
        # Update bin_list table if bin_holder_name is provided
        if bin_holder_name:
            bin_number = updated_bin['bin_lower'][:6] if len(updated_bin['bin_lower']) >= 6 else updated_bin['bin_lower']
            update_success = update_bin_holder_name(bin_number, bin_holder_name)
            if not update_success:
                print(f"Warning: Failed to update bin_holder_name in bin_list table for BIN {bin_number}")
        
        # Add holder name from JSON data or get from database
        bin_number = updated_bin['bin_lower'][:6] if len(updated_bin['bin_lower']) >= 6 else updated_bin['bin_lower']
        updated_bin['bin_holder_name'] = get_bin_holder_name(bin_number)
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'message': 'BIN updated successfully',
            'data': updated_bin
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to update BIN: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify BIN ID', 'Check for duplicate BINs']
            }
        }), 500

@gateway_bp.route('/bins/<int:bin_id>', methods=['DELETE'])
def delete_bin(bin_id):
    """Delete BIN by ID

    Supports optional removal of holder name (Issuer) from SQLite bin_list via:
    - JSON body boolean: delete_bin_holder_name
    - Query param: delete_bin_holder_name=true|false
    """
    try:
        data = request.get_json() or {}
        connection = data.get('connection', 'LOKAL')

        # Determine whether to delete BIN holder name (Issuer) in SQLite
        def _parse_bool(val):
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.strip().lower() in ['1', 'true', 'yes', 'y']
            if isinstance(val, (int, float)):
                return bool(val)
            return None

        delete_flag = _parse_bool(data.get('delete_bin_holder_name'))
        if delete_flag is None:
            delete_flag = _parse_bool(request.args.get('delete_bin_holder_name'))
        delete_bin_holder_name = delete_flag if delete_flag is not None else False
        
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Get BIN details before deletion
        mysql_cursor.execute("""
            SELECT id, gateway_id, bin_lower, bin_upper
            FROM bin_details WHERE id = %s
        """, (bin_id,))
        
        bin_data = mysql_cursor.fetchone()
        
        if not bin_data:
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': f'BIN with ID {bin_id} not found'
            }), 404
        
        # Delete from bin_details
        mysql_cursor.execute("DELETE FROM bin_details WHERE id = %s", (bin_id,))
        mysql_conn.commit()
        
        # Handle SQLite bin_list deletion if requested
        if delete_bin_holder_name and bin_data['bin_lower']:
            bin_6digits = bin_data['bin_lower'][:6]
            try:
                sqlite_conn = get_db_connection()
                sqlite_cursor = sqlite_conn.cursor()
                # Use correct column name BIN (SQLite identifiers are case-insensitive but keep consistent)
                sqlite_cursor.execute("DELETE FROM bin_list WHERE BIN = ?", (bin_6digits,))
                sqlite_conn.commit()
                sqlite_cursor.close()
                sqlite_conn.close()
            except Exception as sqlite_error:
                # Log SQLite error but don't fail the main operation
                print(f"SQLite deletion error: {sqlite_error}")
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'message': f'BIN {bin_data["bin_lower"]} deleted successfully'
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to delete BIN: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify BIN ID', 'Check for foreign key constraints']
            }
        }), 500

# Search and Utility Endpoints

@gateway_bp.route('/search', methods=['GET', 'POST'])
def search_bins():
    """Search BINs across all gateways"""
    try:
        # Get parameters from POST body or GET query params
        if request.method == 'POST':
            if not request.is_json:
                return jsonify({'error': 'Request body must be JSON'}), 400
            data = request.get_json()
            connection = data.get('connection', 'LOKAL')
            search_term = data.get('search', '').strip()
            use_range = data.get('use_range', False)
            page = int(data.get('page', 1))
            page_size = min(int(data.get('page_size', 50)), 100)  # Limit to 100 max
        else:
            # GET method - query parameters
            connection = request.args.get('connection', 'LOKAL')
            search_term = request.args.get('search', '').strip()
            use_range = request.args.get('use_range', 'false').lower() == 'true'
            page = int(request.args.get('page', 1))
            page_size = min(int(request.args.get('page_size', 50)), 100)  # Limit to 100 max
        
        if not search_term:
            return jsonify({
                'success': False,
                'error': 'Search term is required'
            }), 400
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Build search query
        if use_range:
            # Search by BIN range
            sql = """
                SELECT bd.id, bd.gateway_id, bd.bin_lower, bd.bin_upper,
                       bd.bin_type, bd.country, bd.currency, bd.region,
                       bg.name as gateway_name
                FROM bin_details bd
                LEFT JOIN bin_gateway bg ON bd.gateway_id = bg.id
                WHERE bd.bin_lower <= %s AND bd.bin_upper >= %s
                ORDER BY bd.bin_lower ASC
                LIMIT %s OFFSET %s
            """
            params = [search_term, search_term, page_size, offset]
            
            count_sql = """
                SELECT COUNT(*) as total
                FROM bin_details bd
                WHERE bd.bin_lower <= %s AND bd.bin_upper >= %s
            """
            count_params = [search_term, search_term]
        else:
            # Search by BIN prefix
            sql = """
                SELECT bd.id, bd.gateway_id, bd.bin_lower, bd.bin_upper,
                       bd.bin_type, bd.country, bd.currency, bd.region,
                       bg.name as gateway_name
                FROM bin_details bd
                LEFT JOIN bin_gateway bg ON bd.gateway_id = bg.id
                WHERE bd.bin_lower LIKE %s OR bd.bin_upper LIKE %s
                ORDER BY bd.bin_lower ASC
                LIMIT %s OFFSET %s
            """
            search_param = f'{search_term}%'
            params = [search_param, search_param, page_size, offset]
            
            count_sql = """
                SELECT COUNT(*) as total
                FROM bin_details bd
                WHERE bd.bin_lower LIKE %s OR bd.bin_upper LIKE %s
            """
            search_param = f'{search_term}%'
            count_params = [search_param, search_param]
        
        # Execute queries
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Get total count for pagination info
        mysql_cursor.execute(count_sql, count_params)
        total_result = mysql_cursor.fetchone()
        total_count = total_result['total'] if total_result else 0
        
        # Get search results
        mysql_cursor.execute(sql, params)
        results = mysql_cursor.fetchall()
        
        # Add holder names from JSON data (only if needed)
        if len(results) <= 50:  # Only enrich if reasonable amount
            for result in results:
                bin_number = result['bin_lower'][:6] if len(result['bin_lower']) >= 6 else result['bin_lower']
                result['bin_holder_name'] = get_bin_holder_name(bin_number)
        else:
            # Skip holder name enrichment for large datasets
            for result in results:
                result['bin_holder_name'] = ''
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'data': results,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_count': total_count,
                'has_more': len(results) == page_size
            },
            'search_term': search_term,
            'use_range': use_range,
            'connection': connection
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to search BINs: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify search term', 'Check table structure']
            }
        }), 500

@gateway_bp.route('/bins/move', methods=['POST'])
def move_bins():
    """Move BINs between gateways"""
    try:
        data = request.get_json()
        connection = data.get('connection', 'LOKAL')
        bin_ids = data.get('bin_ids', [])
        target_gateway_id = data.get('target_gateway_id')
        
        if not bin_ids:
            return jsonify({
                'success': False,
                'error': 'BIN IDs are required'
            }), 400
        
        if not target_gateway_id:
            return jsonify({
                'success': False,
                'error': 'Target gateway ID is required'
            }), 400
        
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Check if target gateway exists
        mysql_cursor.execute("SELECT id FROM bin_gateway WHERE id = %s", (target_gateway_id,))
        if not mysql_cursor.fetchone():
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': f'Target gateway with ID "{target_gateway_id}" not found'
            }), 404
        
        # Get current BINs to move
        placeholders = ','.join(['%s'] * len(bin_ids))
        mysql_cursor.execute(f"""
            SELECT id, gateway_id, bin_lower, bin_upper
            FROM bin_details WHERE id IN ({placeholders})
        """, bin_ids)
        
        bins_to_move = mysql_cursor.fetchall()
        
        if len(bins_to_move) != len(bin_ids):
            mysql_cursor.close()
            mysql_conn.close()
            return jsonify({
                'success': False,
                'error': 'Some BIN IDs not found'
            }), 404
        
        # Move BINs
        moved_count = 0
        for bin_data in bins_to_move:
            if bin_data['gateway_id'] != target_gateway_id:
                mysql_cursor.execute("""
                    UPDATE bin_details SET gateway_id = %s WHERE id = %s
                """, (target_gateway_id, bin_data['id']))
                moved_count += 1
        
        mysql_conn.commit()
        mysql_cursor.close()
        mysql_conn.close()
        
        return jsonify({
            'success': True,
            'message': f'{moved_count} BIN(s) moved successfully to gateway {target_gateway_id}',
            'moved_count': moved_count,
            'target_gateway_id': target_gateway_id
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to move BINs: {str(e)}',
            'error_details': {
                'error_type': 'Database Error',
                'error_message': str(e),
                'possible_causes': ['Check database connection', 'Verify BIN IDs', 'Verify target gateway']
            }
        }), 500

# CORS and OPTIONS handlers
@gateway_bp.route('/', methods=['OPTIONS'])
@gateway_bp.route('/<path:path>', methods=['OPTIONS'])
def handle_options(path=None):
    """Handle CORS preflight requests"""
    response = jsonify({'status': 'ok'})
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response
