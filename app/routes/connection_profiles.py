from flask import Blueprint, request, jsonify
import sqlite3
import os

connection_bp = Blueprint('connection', __name__)

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
        cursor.execute('''
            UPDATE connection SET host=?, port=?, user=?, password=?, database=?, category=?, connection_type=?
            WHERE name=?
        ''', (
            data.get('host'), data.get('port'), data.get('user'), data.get('password'),
            data.get('database'), data.get('category'), data.get('connection_type'), profile_name
        ))
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'error': 'Profile not found'}), 404
        conn.commit()
        conn.close()
        return jsonify({'status': 'success'})
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
        # Try to connect using the profile
        import pymysql
        try:
            db_conn = pymysql.connect(
                host=profile['host'],
                port=int(profile['port']) if profile['port'] else 3306,
                user=profile['user'],
                password=profile['password'],
                database=profile['database'],
                connect_timeout=5
            )
            db_conn.close()
            return jsonify({'status': 'success', 'message': 'Connection successful!'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'Connection failed: {str(e)}'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500 