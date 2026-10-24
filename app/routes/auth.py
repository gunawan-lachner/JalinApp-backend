from flask import Blueprint, request, jsonify
import sqlite3
import bcrypt
from datetime import datetime, timedelta
import os
import jwt

auth_bp = Blueprint('auth', __name__)

# JWT Configuration
SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your-secret-key-change-in-production')
ALGORITHM = "HS256"

def get_db_connection():
    """Get SQLite database connection"""
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'instance', 'myapp_db.db')
    return sqlite3.connect(db_path)

def hash_password(password):
    """Hash password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed_password):
    """Check password against hash"""
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_user_by_username(username):
    """Get user by username with role information"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT u.id, u.username, u.password, r.role_name, r.id_role, r.can_crud, u.full_name
        FROM user u
        LEFT JOIN role r ON u.id_role = r.id_role
        WHERE u.username = ?
    ''', (username,))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        return {
            'id': user[0],
            'username': user[1],
            'password': user[2],
            'role_name': user[3],
            'id_role': user[4],
            'can_crud': bool(user[5]),
            'full_name': user[6] or '',
        }
    return None

def create_token(user_data):
    """Create JWT token"""
    payload = {
        'user_id': user_data['id'],
        'username': user_data['username'],
        'role_name': user_data['role_name'],
        'id_role': user_data['id_role'],
        'can_crud': user_data['can_crud'],
        'exp': datetime.utcnow() + timedelta(days=7),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token):
    """Verify JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

@auth_bp.route('/login', methods=['POST', 'OPTIONS'])
def login():
    """JWT Login endpoint - Main auth endpoint"""
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        origin = request.headers.get('Origin', '*')
        response.headers.add('Access-Control-Allow-Origin', origin)
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        data = request.get_json()
        
        if not data or 'username' not in data or 'password' not in data:
            return jsonify({
                'success': False,
                'error': 'Username and password are required'
            }), 400
        
        username = data['username']
        password = data['password']
        
        # Get user from database
        user_data = get_user_by_username(username)
        
        if not user_data:
            return jsonify({
                'success': False,
                'error': 'Invalid username or password'
            }), 401
        
        # Check password
        if not check_password(password, user_data['password']):
            return jsonify({
                'success': False,
                'error': 'Invalid username or password'
            }), 401
        
        # Update last login
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE user SET last_login = ? WHERE id = ?",
            (datetime.now().isoformat(), user_data['id'])
        )
        conn.commit()
        conn.close()
        
        # Create JWT token
        token = create_token(user_data)
        
        return jsonify({
            'success': True,
            'message': 'Login successful',
            'token': token,
            'user': {
                'id': user_data['id'],
                'username': user_data['username'],
                'full_name': user_data.get('full_name') or '',
                'role_name': user_data['role_name'],
                'id_role': user_data['id_role'],
                'can_crud': user_data['can_crud']
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/me', methods=['GET'])
def get_current_user():
    """Get current user info from JWT token - Main auth endpoint"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401

        # Prefer fresh DB data so full_name updates without re-login
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.id, u.username, u.full_name, r.role_name, r.id_role, r.can_crud
            FROM user u
            LEFT JOIN role r ON u.id_role = r.id_role
            WHERE u.id = ?
        ''', (payload['user_id'],))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({
                'success': False,
                'error': 'User not found'
            }), 404
        
        return jsonify({
            'success': True,
            'user': {
                'id': row[0],
                'username': row[1],
                'full_name': row[2] or '',
                'role_name': row[3],
                'id_role': row[4],
                'can_crud': bool(row[5]),
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/users', methods=['GET'])
def get_users():
    """Get all users (admin only) - Main auth endpoint"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.id, u.username, u.full_name, r.role_name, r.id_role, r.can_crud, u.last_login
            FROM user u
            LEFT JOIN role r ON u.id_role = r.id_role
            ORDER BY u.id
        ''')
        users = cursor.fetchall()
        conn.close()
        
        user_list = []
        for user in users:
            user_list.append({
                'id': user[0],
                'username': user[1],
                'full_name': user[2] or '',
                'role_name': user[3],
                'id_role': user[4],
                'can_crud': bool(user[5]),
                'last_login': user[6]
            })
        
        return jsonify({
            'success': True,
            'users': user_list
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/roles', methods=['GET'])
def get_roles():
    """Get all roles - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id_role, role_name, can_crud
            FROM role
            ORDER BY id_role
        ''')
        roles = cursor.fetchall()
        conn.close()
        
        role_list = []
        for role in roles:
            role_list.append({
                'id_role': role[0],
                'role_name': role[1],
                'can_crud': bool(role[2])
            })
        
        return jsonify({
            'success': True,
            'roles': role_list
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/pages', methods=['GET'])
def get_pages():
    """Get all pages with role assignments - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get all pages with their assigned roles
        cursor.execute('''
            SELECT p.id_page, p.name, p.path, p.page_order, p.icon, p.parent_id,
                   GROUP_CONCAT(r.role_name) as role_names,
                   GROUP_CONCAT(r.id_role) as role_ids
            FROM page p
            LEFT JOIN page_role pr ON p.id_page = pr.id_page
            LEFT JOIN role r ON pr.id_role = r.id_role
            GROUP BY p.id_page, p.name, p.path, p.page_order, p.icon, p.parent_id
            ORDER BY p.page_order ASC, p.name ASC
        ''')
        pages = cursor.fetchall()
        conn.close()
        
        page_list = []
        for page in pages:
            role_names = page[6].split(',') if page[6] else []
            role_ids = [int(x) for x in page[7].split(',')] if page[7] else []
            
            page_list.append({
                'id_page': page[0],
                'name': page[1],
                'path': page[2],
                'page_order': page[3],
                'icon': page[4],
                'parent_id': page[5],
                'role_names': role_names,
                'role_ids': role_ids
            })
        
        return jsonify({
            'success': True,
            'pages': page_list
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/user-pages', methods=['GET'])
def get_user_pages():
    """Get current user's accessible pages"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        user_role_id = payload['id_role']
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get pages accessible to user's role
        cursor.execute('''
            SELECT DISTINCT p.id_page, p.name, p.path, p.page_order, p.icon, p.parent_id
            FROM page p
            INNER JOIN page_role pr ON p.id_page = pr.id_page
            WHERE pr.id_role = ?
            ORDER BY p.page_order ASC, p.name ASC
        ''', (user_role_id,))
        pages = cursor.fetchall()

        # Also include parent menu pages for accessible children
        # so nested menus still render even if parent role was omitted
        page_map = {}
        for page in pages:
            page_map[page[0]] = {
                'id_page': page[0],
                'name': page[1],
                'path': page[2],
                'page_order': page[3],
                'icon': page[4],
                'parent_id': page[5],
            }

        parent_ids = {
            page['parent_id']
            for page in page_map.values()
            if page.get('parent_id')
        }
        missing_parent_ids = [pid for pid in parent_ids if pid not in page_map]
        if missing_parent_ids:
            placeholders = ','.join('?' * len(missing_parent_ids))
            cursor.execute(f'''
                SELECT id_page, name, path, page_order, icon, parent_id
                FROM page
                WHERE id_page IN ({placeholders})
            ''', missing_parent_ids)
            for parent in cursor.fetchall():
                page_map[parent[0]] = {
                    'id_page': parent[0],
                    'name': parent[1],
                    'path': parent[2],
                    'page_order': parent[3],
                    'icon': parent[4],
                    'parent_id': parent[5],
                }

        conn.close()

        page_list = sorted(
            page_map.values(),
            key=lambda p: (p.get('page_order') or 0, p.get('name') or '')
        )
        
        return jsonify({
            'success': True,
            'pages': page_list
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# New endpoints for managing page-role assignments

@auth_bp.route('/pages/<int:page_id>/roles', methods=['GET'])
def get_page_roles(page_id):
    """Get roles assigned to a specific page - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if page exists
        cursor.execute("SELECT name FROM page WHERE id_page = ?", (page_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Page not found'
            }), 404
        
        # Get roles assigned to this page
        cursor.execute('''
            SELECT r.id_role, r.role_name
            FROM role r
            INNER JOIN page_role pr ON r.id_role = pr.id_role
            WHERE pr.id_page = ?
            ORDER BY r.role_name
        ''', (page_id,))
        roles = cursor.fetchall()
        conn.close()
        
        role_list = []
        for role in roles:
            role_list.append({
                'id_role': role[0],
                'role_name': role[1]
            })
        
        return jsonify({
            'success': True,
            'roles': role_list
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/pages/<int:page_id>/roles', methods=['POST'])
def assign_roles_to_page(page_id):
    """Assign roles to a page - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        data = request.get_json()
        
        if not data or 'role_ids' not in data:
            return jsonify({
                'success': False,
                'error': 'role_ids array is required'
            }), 400
        
        role_ids = data['role_ids']
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if page exists
        cursor.execute("SELECT name FROM page WHERE id_page = ?", (page_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Page not found'
            }), 404
        
        # Validate all role IDs exist
        for role_id in role_ids:
            cursor.execute("SELECT role_name FROM role WHERE id_role = ?", (role_id,))
            if not cursor.fetchone():
                conn.close()
                return jsonify({
                    'success': False,
                    'error': f'Role with ID {role_id} not found'
                }), 400
        
        # Remove existing assignments for this page
        cursor.execute("DELETE FROM page_role WHERE id_page = ?", (page_id,))
        
        # Add new assignments
        for role_id in role_ids:
            cursor.execute(
                "INSERT INTO page_role (id_page, id_role) VALUES (?, ?)",
                (page_id, role_id)
            )
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Roles assigned to page successfully'
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/pages/<int:page_id>/roles/<int:role_id>', methods=['DELETE'])
def remove_role_from_page(page_id, role_id):
    """Remove a role from a page - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if page exists
        cursor.execute("SELECT name FROM page WHERE id_page = ?", (page_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Page not found'
            }), 404
        
        # Check if role exists
        cursor.execute("SELECT role_name FROM role WHERE id_role = ?", (role_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Role not found'
            }), 404
        
        # Remove the assignment
        cursor.execute(
            "DELETE FROM page_role WHERE id_page = ? AND id_role = ?",
            (page_id, role_id)
        )
        
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Role is not assigned to this page'
            }), 400
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Role removed from page successfully'
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/users', methods=['POST'])
def create_user():
    """Create new user - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        data = request.get_json()
        
        if not data or 'username' not in data or 'password' not in data or 'id_role' not in data:
            return jsonify({
                'success': False,
                'error': 'Username, password, and id_role are required'
            }), 400
        
        username = data['username']
        password = data['password']
        id_role = data['id_role']
        full_name = (data.get('full_name') or '').strip()
        
        # Hash password
        hashed_password = hash_password(password)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if username already exists
        cursor.execute("SELECT id FROM user WHERE username = ?", (username,))
        if cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Username already exists'
            }), 400
        
        # Check if role exists
        cursor.execute("SELECT role_name FROM role WHERE id_role = ?", (id_role,))
        role_result = cursor.fetchone()
        if not role_result:
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Role not found'
            }), 400
        
        # Create user
        cursor.execute(
            "INSERT INTO user (username, password, id_role, full_name) VALUES (?, ?, ?, ?)",
            (username, hashed_password, id_role, full_name)
        )
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'User created successfully',
            'user': {
                'id': user_id,
                'username': username,
                'full_name': full_name,
                'role_name': role_result[0],
                'id_role': id_role
            }
        }), 201
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/users/<int:user_id>', methods=['PUT'])
def update_user(user_id):
    """Update user - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        data = request.get_json()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if user exists
        cursor.execute("SELECT username FROM user WHERE id = ?", (user_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'User not found'
            }), 404
        
        # Update fields
        update_fields = []
        update_values = []
        
        if 'username' in data:
            # Check if new username already exists
            cursor.execute("SELECT id FROM user WHERE username = ? AND id != ?", (data['username'], user_id))
            if cursor.fetchone():
                conn.close()
                return jsonify({
                    'success': False,
                    'error': 'Username already exists'
                }), 400
            update_fields.append("username = ?")
            update_values.append(data['username'])

        if 'full_name' in data:
            update_fields.append("full_name = ?")
            update_values.append((data.get('full_name') or '').strip())
        
        if 'id_role' in data:
            # Check if role exists
            cursor.execute("SELECT role_name FROM role WHERE id_role = ?", (data['id_role'],))
            role_result = cursor.fetchone()
            if not role_result:
                conn.close()
                return jsonify({
                    'success': False,
                    'error': 'Role not found'
                }), 400
            update_fields.append("id_role = ?")
            update_values.append(data['id_role'])
        
        if 'password' in data and data['password']:
            hashed_password = hash_password(data['password'])
            update_fields.append("password = ?")
            update_values.append(hashed_password)
        
        if not update_fields:
            conn.close()
            return jsonify({
                'success': False,
                'error': 'No fields to update'
            }), 400
        
        update_values.append(user_id)
        query = f"UPDATE user SET {', '.join(update_fields)} WHERE id = ?"
        cursor.execute(query, update_values)
        conn.commit()
        
        # Get updated user data
        cursor.execute('''
            SELECT u.id, u.username, u.full_name, r.role_name, r.id_role
            FROM user u
            LEFT JOIN role r ON u.id_role = r.id_role
            WHERE u.id = ?
        ''', (user_id,))
        user = cursor.fetchone()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'User updated successfully',
            'user': {
                'id': user[0],
                'username': user[1],
                'full_name': user[2] or '',
                'role_name': user[3],
                'id_role': user[4]
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    """Delete user - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if user exists
        cursor.execute("SELECT username FROM user WHERE id = ?", (user_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'User not found'
            }), 404
        
        # Delete user
        cursor.execute("DELETE FROM user WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'User deleted successfully'
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/roles', methods=['POST'])
def create_role():
    """Create new role - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        data = request.get_json()
        
        if not data or 'role_name' not in data:
            return jsonify({
                'success': False,
                'error': 'Role name is required'
            }), 400
        
        role_name = data['role_name']
        can_crud = data.get('can_crud', False)  # Default to False if not provided
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if role already exists
        cursor.execute("SELECT id_role FROM role WHERE role_name = ?", (role_name,))
        if cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Role already exists'
            }), 400
        
        # Create role
        cursor.execute("INSERT INTO role (role_name, can_crud) VALUES (?, ?)", (role_name, can_crud))
        role_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Role created successfully',
            'role': {
                'id_role': role_id,
                'role_name': role_name,
                'can_crud': can_crud
            }
        }), 201
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/roles/<int:role_id>', methods=['PUT'])
def update_role(role_id):
    """Update role - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        data = request.get_json()
        
        if not data or 'role_name' not in data:
            return jsonify({
                'success': False,
                'error': 'Role name is required'
            }), 400
        
        role_name = data['role_name']
        can_crud = data.get('can_crud')  # Optional parameter
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if role exists
        cursor.execute("SELECT role_name FROM role WHERE id_role = ?", (role_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Role not found'
            }), 404
        
        # Check if new role name already exists
        cursor.execute("SELECT id_role FROM role WHERE role_name = ? AND id_role != ?", (role_name, role_id))
        if cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Role name already exists'
            }), 400
        
        # Update role
        cursor.execute("UPDATE role SET role_name = ?, can_crud = ? WHERE id_role = ?", (role_name, can_crud, role_id))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Role updated successfully',
            'role': {
                'id_role': role_id,
                'role_name': role_name,
                'can_crud': can_crud
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/roles/<int:role_id>', methods=['DELETE'])
def delete_role(role_id):
    """Delete role - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if role exists
        cursor.execute("SELECT role_name FROM role WHERE id_role = ?", (role_id,))
        role_result = cursor.fetchone()
        if not role_result:
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Role not found'
            }), 404
        
        # Check if role is being used by users
        cursor.execute("SELECT COUNT(*) FROM user WHERE id_role = ?", (role_id,))
        user_count = cursor.fetchone()[0]
        if user_count > 0:
            conn.close()
            return jsonify({
                'success': False,
                'error': f'Cannot delete role. {user_count} user(s) are assigned to this role.'
            }), 400
        
        # Check if role is being used by pages
        cursor.execute("SELECT COUNT(*) FROM page_role WHERE id_role = ?", (role_id,))
        page_count = cursor.fetchone()[0]
        if page_count > 0:
            conn.close()
            return jsonify({
                'success': False,
                'error': f'Cannot delete role. {page_count} page(s) are assigned to this role.'
            }), 400
        
        # Delete role
        cursor.execute("DELETE FROM role WHERE id_role = ?", (role_id,))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Role deleted successfully'
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/pages', methods=['POST'])
def create_page():
    """Create new page - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        data = request.get_json()
        
        if not data or 'name' not in data or 'path' not in data:
            return jsonify({
                'success': False,
                'error': 'Name and path are required'
            }), 400
        
        name = data['name']
        path = data['path']
        role_ids = data.get('role_ids', [])  # Optional role assignments
        page_order = data.get('page_order', 0)  # Optional page order
        icon = data.get('icon')  # Optional icon
        parent_id = data.get('parent_id')  # Optional parent menu
        if parent_id in ('', None):
            parent_id = None
        else:
            try:
                parent_id = int(parent_id)
            except (TypeError, ValueError):
                return jsonify({
                    'success': False,
                    'error': 'Invalid parent_id'
                }), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if path already exists
        cursor.execute("SELECT id_page FROM page WHERE path = ?", (path,))
        if cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Page path already exists'
            }), 400

        # Validate parent menu (one-level nesting only)
        if parent_id is not None:
            cursor.execute(
                "SELECT id_page, parent_id FROM page WHERE id_page = ?",
                (parent_id,)
            )
            parent_row = cursor.fetchone()
            if not parent_row:
                conn.close()
                return jsonify({
                    'success': False,
                    'error': 'Parent page not found'
                }), 400
            if parent_row[1] is not None:
                conn.close()
                return jsonify({
                    'success': False,
                    'error': 'Nested submenus deeper than one level are not allowed'
                }), 400
        
        # Create page
        cursor.execute(
            "INSERT INTO page (name, path, page_order, icon, parent_id) VALUES (?, ?, ?, ?, ?)",
            (name, path, page_order, icon, parent_id)
        )
        page_id = cursor.lastrowid
        
        # Assign roles if provided
        if role_ids:
            # Validate all role IDs exist
            for role_id in role_ids:
                cursor.execute("SELECT role_name FROM role WHERE id_role = ?", (role_id,))
                if not cursor.fetchone():
                    conn.close()
                    return jsonify({
                        'success': False,
                        'error': f'Role with ID {role_id} not found'
                    }), 400
            
            # Add role assignments
            for role_id in role_ids:
                cursor.execute(
                    "INSERT INTO page_role (id_page, id_role) VALUES (?, ?)",
                    (page_id, role_id)
                )
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Page created successfully',
            'page': {
                'id_page': page_id,
                'name': name,
                'path': path,
                'page_order': page_order,
                'icon': icon,
                'parent_id': parent_id,
                'role_ids': role_ids
            }
        }), 201
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/pages/<int:page_id>', methods=['PUT'])
def update_page(page_id):
    """Update page - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        data = request.get_json()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if page exists
        cursor.execute("SELECT name FROM page WHERE id_page = ?", (page_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Page not found'
            }), 404
        
        # Update fields
        update_fields = []
        update_values = []
        
        if 'name' in data:
            update_fields.append("name = ?")
            update_values.append(data['name'])
        
        if 'path' in data:
            # Check if new path already exists
            cursor.execute("SELECT id_page FROM page WHERE path = ? AND id_page != ?", (data['path'], page_id))
            if cursor.fetchone():
                conn.close()
                return jsonify({
                    'success': False,
                    'error': 'Page path already exists'
                }), 400
            update_fields.append("path = ?")
            update_values.append(data['path'])
        
        if 'page_order' in data:
            update_fields.append("page_order = ?")
            update_values.append(data['page_order'])
        
        if 'icon' in data:
            update_fields.append("icon = ?")
            update_values.append(data['icon'])

        if 'parent_id' in data:
            parent_id = data.get('parent_id')
            if parent_id in ('', None):
                parent_id = None
            else:
                try:
                    parent_id = int(parent_id)
                except (TypeError, ValueError):
                    conn.close()
                    return jsonify({
                        'success': False,
                        'error': 'Invalid parent_id'
                    }), 400

            if parent_id == page_id:
                conn.close()
                return jsonify({
                    'success': False,
                    'error': 'A page cannot be its own parent'
                }), 400

            if parent_id is not None:
                cursor.execute(
                    "SELECT id_page, parent_id FROM page WHERE id_page = ?",
                    (parent_id,)
                )
                parent_row = cursor.fetchone()
                if not parent_row:
                    conn.close()
                    return jsonify({
                        'success': False,
                        'error': 'Parent page not found'
                    }), 400
                if parent_row[1] is not None:
                    conn.close()
                    return jsonify({
                        'success': False,
                        'error': 'Nested submenus deeper than one level are not allowed'
                    }), 400

                # Prevent turning a parent with children into a child
                cursor.execute(
                    "SELECT COUNT(*) FROM page WHERE parent_id = ?",
                    (page_id,)
                )
                if cursor.fetchone()[0] > 0:
                    conn.close()
                    return jsonify({
                        'success': False,
                        'error': 'This page already has children and cannot become a submenu'
                    }), 400

            update_fields.append("parent_id = ?")
            update_values.append(parent_id)
        
        if update_fields:
            update_values.append(page_id)
            query = f"UPDATE page SET {', '.join(update_fields)} WHERE id_page = ?"
            cursor.execute(query, update_values)
        
        # Handle role assignments if provided
        if 'role_ids' in data:
            role_ids = data['role_ids']
            
            # Validate all role IDs exist
            for role_id in role_ids:
                cursor.execute("SELECT role_name FROM role WHERE id_role = ?", (role_id,))
                if not cursor.fetchone():
                    conn.close()
                    return jsonify({
                        'success': False,
                        'error': f'Role with ID {role_id} not found'
                    }), 400
            
            # Remove existing assignments
            cursor.execute("DELETE FROM page_role WHERE id_page = ?", (page_id,))
            
            # Add new assignments
            for role_id in role_ids:
                cursor.execute(
                    "INSERT INTO page_role (id_page, id_role) VALUES (?, ?)",
                    (page_id, role_id)
                )
        
        conn.commit()
        
        # Get updated page data with roles
        cursor.execute('''
            SELECT p.id_page, p.name, p.path, p.page_order, p.icon, p.parent_id,
                   GROUP_CONCAT(r.role_name) as role_names,
                   GROUP_CONCAT(r.id_role) as role_ids
            FROM page p
            LEFT JOIN page_role pr ON p.id_page = pr.id_page
            LEFT JOIN role r ON pr.id_role = r.id_role
            WHERE p.id_page = ?
            GROUP BY p.id_page, p.name, p.path, p.page_order, p.icon, p.parent_id
        ''', (page_id,))
        page = cursor.fetchone()
        conn.close()
        
        role_names = page[6].split(',') if page[6] else []
        role_ids = [int(x) for x in page[7].split(',')] if page[7] else []
        
        return jsonify({
            'success': True,
            'message': 'Page updated successfully',
            'page': {
                'id_page': page[0],
                'name': page[1],
                'path': page[2],
                'page_order': page[3],
                'icon': page[4],
                'parent_id': page[5],
                'role_names': role_names,
                'role_ids': role_ids
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/pages/<int:page_id>', methods=['DELETE'])
def delete_page(page_id):
    """Delete page - Admin only"""
    try:
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'success': False,
                'error': 'Authorization header required'
            }), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401
        
        # Check if user is admin
        if payload['role_name'] != 'admin':
            return jsonify({
                'success': False,
                'error': 'Access denied. Admin privileges required.'
            }), 403
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if page exists
        cursor.execute("SELECT name FROM page WHERE id_page = ?", (page_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Page not found'
            }), 404

        # Detach children before deleting parent menu
        cursor.execute(
            "UPDATE page SET parent_id = NULL WHERE parent_id = ?",
            (page_id,)
        )
        
        # Delete page
        cursor.execute("DELETE FROM page WHERE id_page = ?", (page_id,))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Page deleted successfully'
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@auth_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200 