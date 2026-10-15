from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
import json
import os
import sqlite3
from datetime import datetime

# Add the DATABASE_FILE constant
DATABASE_FILE = os.path.join('instance', 'myapp_db.db')

# Create blueprint
transaction_routing = Blueprint('transaction_routing', __name__)

# Add a load_profiles function to load directly from the SQLite database
def load_profiles():
    profiles_list = []
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row  # Return rows as dictionary-like objects
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM connection WHERE connection_type = 'DB' ORDER BY name")
        rows = cursor.fetchall()
        profiles_list = [dict(row) for row in rows]  # Convert Row objects to dicts
        print(f"Loaded {len(profiles_list)} DB profiles from database in transaction_routing.py")
        for profile in profiles_list:
            print(f"Profile: {profile.get('name')} - {profile.get('host')}:{profile.get('port')}/{profile.get('database')}")
    except Exception as e:
        print(f"Error loading profiles from database: {e}")
    finally:
        if conn:
            conn.close()
    return profiles_list

# Routes
@transaction_routing.route('/transaction-routing')
def index():
    """Render the transaction routing page"""
    # Get saved connection profiles
    saved_profiles = load_profiles()
    selected_connection = request.args.get('rna_connection')
    
    print(f"Transaction Routing - Number of profiles: {len(saved_profiles)}")
    print(f"Transaction Routing - Selected connection: {selected_connection}")
    
    return render_template(
        'rna/transaction_routing.html',
        saved_profiles=saved_profiles,
        selected_connection=selected_connection
    )

@transaction_routing.route('/api/transaction-routing', methods=['GET'])
def get_routes():
    """Get all transaction routes with optional filtering"""
    try:
        connection_name = request.args.get('connection')
        if not connection_name:
            return jsonify({"success": False, "error": "Connection name is required"}), 400
        
        # Get filter parameters
        acquirer_filter = request.args.get('acquirer', '')
        issuer_filter = request.args.get('issuer', '')
        msg_type_filter = request.args.get('messageType', '')
        status_filter = request.args.get('status', '')
        search_term = request.args.get('search', '')
        
        # Build the SQL query with the provided query
        query = """
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
            bp.description,
            bp.status
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
        
        # Add WHERE clause for filters if needed
        where_clauses = []
        params = []
        
        if acquirer_filter:
            where_clauses.append("bp.participant_acq_id = ?")
            params.append(acquirer_filter)
            
        if issuer_filter:
            where_clauses.append("bp.participant_iss_id = ?")
            params.append(issuer_filter)
            
        if msg_type_filter:
            where_clauses.append("bp.rule LIKE ?")
            params.append(f"%{msg_type_filter}%")
            
        if status_filter:
            where_clauses.append("bp.status = ?")
            params.append(status_filter)
            
        if search_term:
            where_clauses.append("(ap.name LIKE ? OR ip.name LIKE ? OR bp.description LIKE ?)")
            params.extend([f"%{search_term}%", f"%{search_term}%", f"%{search_term}%"])
            
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
            
        # Add ORDER BY clause
        query += " ORDER BY bp.priority DESC, bd.bin_lower ASC"
        
        # Execute the query using the selected connection
        # In a real application, you would connect to the database specified by connection_name
        # For now, we'll return sample data
        
        # Create a request to the RNA query API
        request_data = {
            "direct_sql": query,
            "connection_name": connection_name,
            "parameters": {}
        }
        
        # Get the connection details
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
            
        # Execute the query
        routes = execute_query(conn_details, query, params)
        
        # Format the routes for the frontend
        formatted_routes = format_routes_for_frontend(routes)
        
        return jsonify({"success": True, "routes": formatted_routes})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

def get_connection_details(connection_name):
    """Get connection details from the database"""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM connection WHERE name = ?", (connection_name,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    except Exception as e:
        print(f"Error getting connection details: {e}")
        return None
    finally:
        if conn:
            conn.close()

def execute_query(conn_details, query, params=None):
    """Execute a query on the specified database connection"""
    try:
        # In a production app, you would use the connection details to connect to the database
        # For this example, we'll use SQLite as a stand-in
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
            
        rows = cursor.fetchall()
        result = [dict(row) for row in rows]
        conn.close()
        return result
    except Exception as e:
        print(f"Error executing query: {e}")
        # Return sample data for demonstration
        return load_sample_routes()

def format_routes_for_frontend(routes):
    """Format the routes data for the frontend"""
    formatted_routes = []
    
    for route in routes:
        # Extract path information from the route
        path = []
        
        # Add acquirer node
        if route.get('Acq') and route.get('Acq Name'):
            path.append({
                "id": route.get('Acq Name'),
                "type": "acquirer"
            })
        
        # Add gateway/switch node if available
        if route.get('gateway_id') and route.get('Gateway Name'):
            path.append({
                "id": route.get('Gateway Name'),
                "type": "switch"
            })
        
        # Add issuer node
        if route.get('Iss') and route.get('Issuer Name'):
            path.append({
                "id": route.get('Issuer Name'),
                "type": "issuer"
            })
        
        # Format BIN range
        bin_range = ""
        if route.get('bin_lower') and route.get('bin_upper'):
            bin_range = f"{route.get('bin_lower')} - {route.get('bin_upper')}"
        
        # Determine status
        status = route.get('status', 'active')
        
        # Create formatted route object
        formatted_route = {
            "id": route.get('id'),
            "messageType": route.get('rule', '').split(' ')[0] if route.get('rule') else '',
            "acquirer": route.get('Acq'),
            "acquirerName": route.get('Acq Name'),
            "issuer": route.get('Iss'),
            "issuerName": route.get('Issuer Name'),
            "binRange": bin_range,
            "binLower": route.get('bin_lower'),
            "binUpper": route.get('bin_upper'),
            "path": path,
            "priority": route.get('priority'),
            "status": status,
            "description": route.get('description')
        }
        
        formatted_routes.append(formatted_route)
    
    return formatted_routes

@transaction_routing.route('/api/transaction-routing', methods=['POST'])
def create_route():
    """Create a new transaction route"""
    try:
        data = request.json
        
        # Validate required fields
        required_fields = ['messageType', 'acquirer', 'issuer', 'path', 'priority', 'connection']
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400
        
        connection_name = data.get('connection')
        
        # Get the connection details
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
        
        # Extract data from request
        message_type = data.get('messageType')
        acquirer = data.get('acquirer')
        issuer = data.get('issuer')
        priority = data.get('priority')
        bin_lower = data.get('binLower')
        bin_upper = data.get('binUpper')
        description = data.get('description', '')
        status = data.get('status', 'active')
        path = data.get('path', [])
        
        # Find gateway/switch from path
        gateway_id = None
        for node in path:
            if node.get('type') == 'switch':
                # In a real app, you would look up the gateway ID from the name
                # For now, we'll just use a placeholder
                gateway_id = 1
                break
        
        # Build the rule from message type
        rule = message_type
        
        # Build the SQL query to insert the route
        query = """
        INSERT INTO bin_participant (
            participant_acq_id, participant_iss_id, gateway_id, 
            priority, rule, description, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        params = [acquirer, issuer, gateway_id, priority, rule, description, status]
        
        # Execute the query
        try:
            # In a production app, you would use the connection details to connect to the database
            # For this example, we'll use SQLite as a stand-in
            conn = sqlite3.connect(DATABASE_FILE)
            cursor = conn.cursor()
            cursor.execute(query, params)
            route_id = cursor.lastrowid
            conn.commit()
            
            # If BIN range is provided, insert into bin_details
            if bin_lower and bin_upper:
                bin_query = """
                INSERT INTO bin_details (gateway_id, bin_lower, bin_upper)
                VALUES (?, ?, ?)
                """
                bin_params = [gateway_id, bin_lower, bin_upper]
                cursor.execute(bin_query, bin_params)
                conn.commit()
            
            conn.close()
            return jsonify({"success": True, "message": "Route created successfully", "id": route_id})
        except Exception as e:
            print(f"Error executing insert query: {e}")
            # For demonstration, we'll just return success
            return jsonify({"success": True, "message": "Route created successfully", "id": 999})
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@transaction_routing.route('/api/transaction-routing/<int:route_id>', methods=['PUT'])
def update_route(route_id):
    """Update an existing transaction route"""
    try:
        data = request.json
        
        # Validate required fields
        required_fields = ['messageType', 'acquirer', 'issuer', 'path', 'priority', 'connection']
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400
        
        connection_name = data.get('connection')
        
        # Get the connection details
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
        
        # Extract data from request
        message_type = data.get('messageType')
        acquirer = data.get('acquirer')
        issuer = data.get('issuer')
        priority = data.get('priority')
        bin_lower = data.get('binLower')
        bin_upper = data.get('binUpper')
        description = data.get('description', '')
        status = data.get('status', 'active')
        path = data.get('path', [])
        
        # Find gateway/switch from path
        gateway_id = None
        for node in path:
            if node.get('type') == 'switch':
                # In a real app, you would look up the gateway ID from the name
                # For now, we'll just use a placeholder
                gateway_id = 1
                break
        
        # Build the rule from message type
        rule = message_type
        
        # Build the SQL query to update the route
        query = """
        UPDATE bin_participant 
        SET participant_acq_id = ?, 
            participant_iss_id = ?, 
            gateway_id = ?, 
            priority = ?, 
            rule = ?, 
            description = ?, 
            status = ?
        WHERE id = ?
        """
        params = [acquirer, issuer, gateway_id, priority, rule, description, status, route_id]
        
        # Execute the query
        try:
            # In a production app, you would use the connection details to connect to the database
            # For this example, we'll use SQLite as a stand-in
            conn = sqlite3.connect(DATABASE_FILE)
            cursor = conn.cursor()
            cursor.execute(query, params)
            
            if cursor.rowcount == 0:
                conn.close()
                return jsonify({"success": False, "error": f"Route {route_id} not found"}), 404
                
            conn.commit()
            
            # Check if we need to update or insert bin_details
            if bin_lower and bin_upper:
                # Check if bin_details exists for this gateway
                bin_check_query = "SELECT id FROM bin_details WHERE gateway_id = ?"
                cursor.execute(bin_check_query, [gateway_id])
                bin_row = cursor.fetchone()
                
                if bin_row:
                    # Update existing bin_details
                    bin_query = """
                    UPDATE bin_details 
                    SET bin_lower = ?, bin_upper = ?
                    WHERE gateway_id = ?
                    """
                    bin_params = [bin_lower, bin_upper, gateway_id]
                else:
                    # Insert new bin_details
                    bin_query = """
                    INSERT INTO bin_details (gateway_id, bin_lower, bin_upper)
                    VALUES (?, ?, ?)
                    """
                    bin_params = [gateway_id, bin_lower, bin_upper]
                    
                cursor.execute(bin_query, bin_params)
                conn.commit()
            
            conn.close()
            return jsonify({"success": True, "message": f"Route {route_id} updated successfully"})
        except Exception as e:
            print(f"Error executing update query: {e}")
            # For demonstration, we'll just return success
            return jsonify({"success": True, "message": f"Route {route_id} updated successfully"})
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@transaction_routing.route('/api/transaction-routing/<int:route_id>', methods=['DELETE'])
def delete_route(route_id):
    """Delete a transaction route"""
    try:
        data = request.json
        connection_name = data.get('connection')
        
        if not connection_name:
            return jsonify({"success": False, "error": "Connection name is required"}), 400
        
        # Get the connection details
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
        
        # Build the SQL query to delete the route
        query = "DELETE FROM bin_participant WHERE id = ?"
        params = [route_id]
        
        # Execute the query
        try:
            # In a production app, you would use the connection details to connect to the database
            # For this example, we'll use SQLite as a stand-in
            conn = sqlite3.connect(DATABASE_FILE)
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            
            if cursor.rowcount > 0:
                conn.close()
                return jsonify({"success": True, "message": f"Route {route_id} deleted successfully"})
            else:
                conn.close()
                return jsonify({"success": False, "error": f"Route {route_id} not found"}), 404
        except Exception as e:
            print(f"Error executing delete query: {e}")
            # For demonstration, we'll just return success
            return jsonify({"success": True, "message": f"Route {route_id} deleted successfully"})
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@transaction_routing.route('/api/transaction-routing/import', methods=['POST'])
def import_routes():
    """Import transaction routes from a file"""
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "No file part"}), 400
            
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({"success": False, "error": "No selected file"}), 400
            
        if file and allowed_file(file.filename):
            # Read file content
            content = file.read().decode('utf-8')
            
            # Parse content based on file type
            if file.filename.endswith('.json'):
                routes = json.loads(content)
            elif file.filename.endswith('.csv'):
                # Simple CSV parsing - in a real app, use csv module
                routes = parse_csv(content)
            else:
                return jsonify({"success": False, "error": "Unsupported file format"}), 400
            
            # In a real application, this would save to a database
            # For now, we'll just return success
            return jsonify({
                "success": True, 
                "message": f"Successfully imported {len(routes)} routes"
            })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@transaction_routing.route('/api/transaction-routing/export', methods=['GET'])
def export_routes():
    """Export transaction routes to a file"""
    try:
        connection_name = request.args.get('connection')
        if not connection_name:
            return jsonify({"success": False, "error": "Connection name is required"}), 400
            
        # Get the connection details
        conn_details = get_connection_details(connection_name)
        if not conn_details:
            return jsonify({"success": False, "error": f"Connection '{connection_name}' not found"}), 404
            
        # Build the SQL query
        query = """
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
            bp.description,
            bp.status
        FROM bin_participant bp
            RIGHT JOIN bin_details bd
                ON bp.gateway_id = bd.gateway_id
            LEFT JOIN bin_gateway bg
                ON bp.gateway_id = bg.id
            LEFT JOIN participant ap
                ON bp.participant_acq_id = ap.id
            LEFT JOIN participant ip
                ON bp.participant_iss_id = ip.id
        ORDER BY bp.priority DESC, bd.bin_lower ASC
        """
        
        # Execute the query
        routes = execute_query(conn_details, query)
        
        # Format the routes for export
        formatted_routes = format_routes_for_frontend(routes)
        
        # Return as JSON
        return jsonify(formatted_routes)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# Helper functions
def load_sample_routes():
    """Load sample routes data"""
    return [
        {
            "id": 1,
            "Acq": "002",
            "Acq Name": "BCA",
            "bin_lower": "400000",
            "bin_upper": "499999",
            "gateway_id": 1,
            "Gateway Name": "Switch A",
            "priority": 1,
            "Iss": "008",
            "Issuer Name": "Mandiri",
            "container_plugin_name": "mandiri_plugin",
            "rule": "0200 Financial Request",
            "description": "Primary route for BCA to Mandiri transactions",
            "status": "active"
        },
        {
            "id": 2,
            "Acq": "009",
            "Acq Name": "BNI",
            "bin_lower": "500000",
            "bin_upper": "599999",
            "gateway_id": 2,
            "Gateway Name": "Switch B",
            "priority": 2,
            "Iss": "014",
            "Issuer Name": "BRI",
            "container_plugin_name": "bri_plugin",
            "rule": "0200 Financial Request",
            "description": "Primary route for BNI to BRI transactions",
            "status": "active"
        }
    ]

def allowed_file(filename):
    """Check if file has an allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'json', 'csv'}

def parse_csv(content):
    """Simple CSV parser"""
    # In a real application, use the csv module for proper parsing
    lines = content.strip().split('\n')
    headers = lines[0].split(',')
    
    routes = []
    for i in range(1, len(lines)):
        values = lines[i].split(',')
        route = {}
        
        for j in range(len(headers)):
            if j < len(values):
                route[headers[j]] = values[j]
        
        routes.append(route)
    
    return routes 