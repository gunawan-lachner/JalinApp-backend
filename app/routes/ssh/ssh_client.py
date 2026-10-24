from flask import Blueprint, request, jsonify, current_app
from app.utils.ssh_helper import (
    get_ssh_connections, get_ssh_snippets, get_ssh_snippet,
    save_ssh_snippet, delete_ssh_snippet, execute_ssh_command,
    execute_ssh_snippet, close_ssh_connection, check_connection_status,
    establish_connection, get_server_status
)

ssh_client_bp = Blueprint('ssh_client', __name__)

# SSH Client API
# Base prefix will be registered as /api/ssh/client

@ssh_client_bp.route('/connections', methods=['GET'])
def api_get_connections():
    try:
        connections = get_ssh_connections()
        return jsonify({'results': connections})
    except Exception as e:
        current_app.logger.exception("Error fetching SSH connections")
        return jsonify({'error': str(e)}), 500

@ssh_client_bp.route('/server-status', methods=['POST'])
def api_server_status():
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    connection_name = data.get('connection_name') or data.get('connectionName')
    fields = data.get('fields', [])
    if not connection_name:
        return jsonify({'error': 'Missing connection_name parameter'}), 400
    result = get_server_status(connection_name, fields)
    status = 200 if result.get('success', True) else 400
    return jsonify(result), status

@ssh_client_bp.route('/connection/status/<connection_name>', methods=['GET'])
def api_connection_status(connection_name: str):
    try:
        result = check_connection_status(connection_name)
        return jsonify(result)
    except Exception as e:
        current_app.logger.exception("Error checking connection status")
        return jsonify({'error': str(e)}), 500

@ssh_client_bp.route('/connection/establish', methods=['POST'])
def api_establish():
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    connection_name = data.get('connection_name') or data.get('connectionName')
    if not connection_name:
        return jsonify({'error': 'Missing connection_name parameter'}), 400
    try:
        result = establish_connection(connection_name)
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        current_app.logger.exception("Error establishing SSH connection")
        return jsonify({'error': 'An internal error occurred while establishing the connection.'}), 500

@ssh_client_bp.route('/execute', methods=['POST'])
def api_execute_command():
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    connection_name = data.get('connection_name') or data.get('connectionName')
    command = data.get('command')
    if not connection_name or not command:
        return jsonify({'error': 'Missing connection_name and/or command parameter'}), 400
    try:
        result = execute_ssh_command(connection_name, command)
        # A command that actually ran (even with a non-zero exit) is HTTP 200
        # so the client can show stdout and stderr. Connection failures stay 400.
        status = 400 if result.get('exit_code') is None and not result.get('success') else 200
        return jsonify(result), status
    except Exception as e:
        current_app.logger.exception("Error executing SSH command")
        return jsonify({'error': str(e)}), 500

@ssh_client_bp.route('/command', methods=['POST'])
def api_execute_command_bulk():
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    connection = data.get('connection') or {}
    command = data.get('command')
    if not connection or not command:
        return jsonify({'error': 'Missing connection and/or command parameter'}), 400
    try:
        name = connection.get('name') or connection.get('connection_name')
        if not name:
            return jsonify({'error': 'Invalid connection data'}), 400
        current_app.logger.info(f"Executing command '{command}' on {name}")
        result = execute_ssh_command(name, command)
        status = 400 if result.get('exit_code') is None and not result.get('success') else 200
        return jsonify(result), status
    except Exception as e:
        current_app.logger.exception("Bulk command execution error")
        return jsonify({'error': f'Execution error: {str(e)}'}), 500

@ssh_client_bp.route('/execute-snippet', methods=['POST'])
def api_execute_snippet():
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    connection_name = data.get('connection_name') or data.get('connectionName')
    snippet_id = data.get('snippet_id') or data.get('snippetId')
    if not connection_name or not snippet_id:
        return jsonify({'error': 'Missing connection_name and/or snippet_id parameter'}), 400
    try:
        result = execute_ssh_snippet(connection_name, int(snippet_id))
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        current_app.logger.exception("Error executing SSH snippet")
        return jsonify({'error': str(e)}), 500

@ssh_client_bp.route('/close', methods=['POST'])
def api_close_connection():
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    data = request.get_json() or {}
    connection_name = data.get('connection_name') or data.get('connectionName')
    if not connection_name:
        return jsonify({'error': 'Missing connection_name parameter'}), 400
    try:
        result = close_ssh_connection(connection_name)
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        current_app.logger.exception("Error closing SSH connection")
        return jsonify({'error': str(e)}), 500

# Snippet CRUD
@ssh_client_bp.route('/snippets', methods=['GET'])
def api_get_snippets():
    try:
        snippets = get_ssh_snippets()
        return jsonify({'results': snippets})
    except Exception as e:
        current_app.logger.exception("Error fetching SSH snippets")
        return jsonify({'error': str(e)}), 500

@ssh_client_bp.route('/snippet/<int:snippet_id>', methods=['GET'])
def api_get_single_snippet(snippet_id: int):
    try:
        snippet = get_ssh_snippet(snippet_id)
        if snippet:
            return jsonify(snippet)
        return jsonify({'error': f'Snippet with ID {snippet_id} not found'}), 404
    except Exception as e:
        current_app.logger.exception("Error fetching SSH snippet")
        return jsonify({'error': str(e)}), 500

@ssh_client_bp.route('/snippet', methods=['POST'])
def api_save_snippet():
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    snippet_data = request.get_json() or {}
    try:
        result = save_ssh_snippet(snippet_data)
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        current_app.logger.exception("Error saving SSH snippet")
        return jsonify({'error': str(e)}), 500

@ssh_client_bp.route('/snippet/<int:snippet_id>', methods=['DELETE'])
def api_delete_snippet(snippet_id: int):
    try:
        result = delete_ssh_snippet(snippet_id)
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        current_app.logger.exception("Error deleting SSH snippet")
        return jsonify({'error': str(e)}), 500

# OPTIONS handler for CORS (mirrors participant API style)
@ssh_client_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200
