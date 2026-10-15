from flask import Blueprint, request, jsonify
import sqlite3
import os
from datetime import datetime

graylog_config_bp = Blueprint('graylog_config', __name__)

def get_db_connection():
    """Get SQLite database connection"""
    # Always resolve to the project-level instance directory, regardless of this file's nesting
    # Other modules use the same convention: os.path.join('instance', 'myapp_db.db')
    db_path = os.path.join('instance', 'myapp_db.db')
    return sqlite3.connect(db_path)

@graylog_config_bp.route('/configurations', methods=['GET'])
def get_graylog_configurations():
    """Get all Graylog configurations grouped by environment"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get all Graylog configurations
        cursor.execute('''
            SELECT id, parameter_name, field_name, content_value
            FROM parameters 
            WHERE parameter_name LIKE 'GRAYLOG%' OR field_name LIKE 'GRAYLOG%'
            ORDER BY parameter_name, field_name
        ''')
        rows = cursor.fetchall()
        conn.close()
        
        # Group configurations by environment
        configurations = {}
        for row in rows:
            env_name = row[1]  # parameter_name (e.g., GRAYLOG_PRODUCTION)
            field_name = row[2]  # field_name (e.g., GRAYLOG_BASE_URL)
            content_value = row[3]  # content_value
            
            if env_name not in configurations:
                configurations[env_name] = {
                    'environment': env_name,
                    'config': {}
                }
            
            # Extract the actual field name without GRAYLOG_ prefix
            clean_field_name = field_name.replace('GRAYLOG_', '')
            configurations[env_name]['config'][clean_field_name] = content_value
        
        # Convert to list format
        config_list = list(configurations.values())
        
        return jsonify({
            'success': True,
            'configurations': config_list
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@graylog_config_bp.route('/configurations/<environment>', methods=['GET'])
def get_graylog_configuration(environment):
    """Get Graylog configuration for specific environment"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get configuration for specific environment
        cursor.execute('''
            SELECT id, parameter_name, field_name, content_value
            FROM parameters 
            WHERE parameter_name = ? AND (field_name LIKE 'GRAYLOG%' OR field_name LIKE 'GRAYLOG%')
            ORDER BY field_name
        ''', (environment,))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return jsonify({
                'success': False,
                'error': f'Configuration for environment {environment} not found'
            }), 404
        
        # Build configuration object
        config = {
            'environment': environment,
            'config': {}
        }
        
        for row in rows:
            field_name = row[2]  # field_name
            content_value = row[3]  # content_value
            
            # Extract the actual field name without GRAYLOG_ prefix
            clean_field_name = field_name.replace('GRAYLOG_', '')
            config['config'][clean_field_name] = content_value
        
        return jsonify({
            'success': True,
            'configuration': config
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@graylog_config_bp.route('/configurations', methods=['POST'])
def create_graylog_configuration():
    """Create new Graylog configuration"""
    try:
        data = request.get_json()
        
        if not data or 'environment' not in data or 'config' not in data:
            return jsonify({
                'success': False,
                'error': 'Environment and config are required'
            }), 400
        
        environment = data['environment']
        config = data['config']
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if environment already exists
        cursor.execute("SELECT id FROM parameters WHERE parameter_name = ? AND field_name LIKE 'GRAYLOG%'", (environment,))
        if cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': f'Configuration for environment {environment} already exists'
            }), 400
        
        # Insert configuration entries
        inserted_ids = []
        for field_name, value in config.items():
            full_field_name = f'GRAYLOG_{field_name}'
            cursor.execute(
                "INSERT INTO parameters (parameter_name, field_name, content_value) VALUES (?, ?, ?)",
                (environment, full_field_name, value)
            )
            inserted_ids.append(cursor.lastrowid)
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Graylog configuration for {environment} created successfully',
            'inserted_ids': inserted_ids
        }), 201
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@graylog_config_bp.route('/configurations/<environment>', methods=['PUT'])
def update_graylog_configuration(environment):
    """Update Graylog configuration for specific environment"""
    try:
        data = request.get_json()
        
        if not data or 'config' not in data:
            return jsonify({
                'success': False,
                'error': 'Config is required'
            }), 400
        
        config = data['config']
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if environment exists
        cursor.execute("SELECT id FROM parameters WHERE parameter_name = ? AND field_name LIKE 'GRAYLOG%'", (environment,))
        existing_rows = cursor.fetchall()
        
        if not existing_rows:
            conn.close()
            return jsonify({
                'success': False,
                'error': f'Configuration for environment {environment} not found'
            }), 404
        
        # Update configuration entries
        updated_count = 0
        for field_name, value in config.items():
            full_field_name = f'GRAYLOG_{field_name}'
            cursor.execute(
                "UPDATE parameters SET content_value = ? WHERE parameter_name = ? AND field_name = ?",
                (value, environment, full_field_name)
            )
            updated_count += cursor.rowcount
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Graylog configuration for {environment} updated successfully',
            'updated_count': updated_count
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@graylog_config_bp.route('/configurations/<environment>', methods=['DELETE'])
def delete_graylog_configuration(environment):
    """Delete Graylog configuration for specific environment"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if environment exists
        cursor.execute("SELECT id FROM parameters WHERE parameter_name = ? AND field_name LIKE 'GRAYLOG%'", (environment,))
        existing_rows = cursor.fetchall()
        
        if not existing_rows:
            conn.close()
            return jsonify({
                'success': False,
                'error': f'Configuration for environment {environment} not found'
            }), 404
        
        # Delete configuration entries
        cursor.execute("DELETE FROM parameters WHERE parameter_name = ? AND field_name LIKE 'GRAYLOG%'", (environment,))
        deleted_count = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Graylog configuration for {environment} deleted successfully',
            'deleted_count': deleted_count
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@graylog_config_bp.route('/configurations/<environment>/<field_name>', methods=['PUT'])
def update_graylog_field(environment, field_name):
    """Update specific field in Graylog configuration"""
    try:
        data = request.get_json()
        
        if not data or 'value' not in data:
            return jsonify({
                'success': False,
                'error': 'Value is required'
            }), 400
        
        value = data['value']
        full_field_name = f'GRAYLOG_{field_name}'
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if field exists
        cursor.execute("SELECT id FROM parameters WHERE parameter_name = ? AND field_name = ?", (environment, full_field_name))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': f'Field {field_name} not found in {environment} configuration'
            }), 404
         
        # Update field value
        cursor.execute(
            "UPDATE parameters SET content_value = ? WHERE parameter_name = ? AND field_name = ?",
            (value, environment, full_field_name)
        )
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Field {field_name} updated successfully in {environment}',
            'field': field_name,
            'value': value
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@graylog_config_bp.route('/environments', methods=['GET'])
def get_graylog_environments():
    """Get list of available Graylog environments"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get unique environments
        cursor.execute('''
            SELECT DISTINCT parameter_name
            FROM parameters 
            WHERE parameter_name LIKE 'GRAYLOG%'
            ORDER BY parameter_name
        ''')
        environments = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            'success': True,
            'environments': environments
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@graylog_config_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    """Handle CORS preflight requests"""
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response 