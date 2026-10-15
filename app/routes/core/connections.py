from flask import Blueprint, request, jsonify
import sqlite3
import os

connection_bp = Blueprint('core_connections', __name__)

DATABASE_FILE = os.path.join('instance', 'myapp_db.db')

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

@connection_bp.route('/profiles', methods=['GET'])
def list_connection_profiles():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM connection ORDER BY name")
        profiles = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({'profiles': profiles})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@connection_bp.route('/profiles', methods=['POST'])
def create_connection_profile():
    data = request.get_json()
    required_fields = ['name', 'host', 'port', 'user', 'password', 'database', 'category', 'connection_type']
    if not all(f in data for f in required_fields):
        return jsonify({'error': 'Missing required fields'}), 400
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO connection (name, host, port, user, password, database, category, connection_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['name'], data['host'], data['port'], data['user'], data['password'],
            data['database'], data['category'], data['connection_type']
        ))
        conn.commit()
        conn.close()
        return jsonify({'status': 'success'}), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Profile with this name already exists'}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@connection_bp.route('/profiles/<profile_name>', methods=['PUT'])
def update_connection_profile(profile_name):
    data = request.get_json()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Keep existing password when client sends blank (edit form tip)
        password = data.get('password')
        if password is None or str(password).strip() == '':
            cursor.execute('SELECT password FROM connection WHERE name=?', (profile_name,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return jsonify({'error': 'Profile not found'}), 404
            password = row['password'] if isinstance(row, dict) else row[0]

        new_name = (data.get('name') or profile_name).strip() or profile_name
        cursor.execute('''
            UPDATE connection SET name=?, host=?, port=?, user=?, password=?, database=?, category=?, connection_type=?
            WHERE name=?
        ''', (
            new_name,
            data.get('host'), data.get('port'), data.get('user'), password,
            data.get('database'), data.get('category'), data.get('connection_type') or 'DB',
            profile_name
        ))
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'error': 'Profile not found'}), 404
        conn.commit()
        conn.close()
        return jsonify({'status': 'success', 'name': new_name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@connection_bp.route('/profiles/<profile_name>', methods=['DELETE'])
def delete_connection_profile(profile_name):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM connection WHERE name=?', (profile_name,))
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'error': 'Profile not found'}), 404
        conn.commit()
        conn.close()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@connection_bp.route('/test', methods=['POST'])
def test_connection():
    data = request.get_json()
    profile_name = data.get('name')
    if not profile_name:
        return jsonify({'status': 'error', 'message': 'Missing profile name'}), 400
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM connection WHERE name = ?", (profile_name,))
        profile = cursor.fetchone()
        conn.close()
        if not profile:
            return jsonify({'status': 'error', 'message': 'Profile not found'}), 404
        # Route test based on connection_type
        conn_type = profile['connection_type'] if 'connection_type' in profile.keys() else None
        if conn_type and conn_type.upper() == 'SSH':
            # Test SSH connectivity using paramiko
            try:
                import paramiko
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                host = profile['host']
                port = int(profile['port']) if profile['port'] else 22
                user = profile['user']
                password = profile['password']
                timeout = 5
                ssh.connect(hostname=host, port=port, username=user, password=password, timeout=timeout)
                ssh.close()
                return jsonify({
                    'status': 'success',
                    'message': f'SSH connection successful to {host}:{port} as {user}',
                    'connection_type': 'SSH'
                })
            except Exception as e:
                return jsonify({
                    'status': 'error',
                    'message': f'SSH connection failed: {str(e)}',
                    'connection_type': 'SSH'
                })
        else:
            # Default: test as DB connection (MySQL/MariaDB) using PyMySQL
            try:
                import pymysql
                db_conn = pymysql.connect(
                    host=profile['host'],
                    port=int(profile['port']) if profile['port'] else 3306,
                    user=profile['user'],
                    password=profile['password'],
                    database=profile['database'],
                    connect_timeout=5
                )
                db_conn.close()
                return jsonify({'status': 'success', 'message': 'Connection successful!', 'connection_type': conn_type or 'DB'})
            except Exception as e:
                return jsonify({'status': 'error', 'message': f'Connection failed: {str(e)}', 'connection_type': conn_type or 'DB'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@connection_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200 