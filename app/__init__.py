import os
import json
from datetime import datetime, timedelta
from flask_cors import CORS
from flask import Flask, request
from dotenv import load_dotenv

import sqlite3

# Load environment variables from .env.local
load_dotenv('.env.local')

# Import the organized blueprint registration function
from app.routes import register_blueprints


# Custom JSON encoder to handle datetime objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

def create_app():
    """Application factory pattern for creating Flask app"""
    # Resolve project-level instance directory (one level above the app/ package)
    project_root = os.path.dirname(os.path.dirname(__file__))
    project_instance_dir = os.path.join(project_root, 'instance')
    os.makedirs(project_instance_dir, exist_ok=True)

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        instance_path=project_instance_dir,
        instance_relative_config=True,
    )
    
    # Configure CORS — allow localhost + LAN origins for multi-device access
    CORS(
        app,
        resources={r"/api/*": {
            "origins": "*",
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Accept", "Authorization", "ngrok-skip-browser-warning"],
            "supports_credentials": False
        }},
    )
    
    # Add after_request handler for CORS
    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get('Origin')
        # Only allow CORS for API routes
        if origin and request.path.startswith('/api/'):
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        return response

    # Centralized preflight for all API routes to avoid 308 redirects on trailing slashes
    @app.route('/api', methods=['OPTIONS'], strict_slashes=False)
    @app.route('/api/', methods=['OPTIONS'], strict_slashes=False)
    @app.route('/api/<path:path>', methods=['OPTIONS'])
    def api_cors_preflight(path=None):
        origin = request.headers.get('Origin', '*')
        resp = app.response_class(response=json.dumps({'status': 'ok'}), status=200, mimetype='application/json')
        resp.headers['Access-Control-Allow-Origin'] = origin
        resp.headers['Access-Control-Allow-Credentials'] = 'true'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        return resp
    
    # Configure app
    app.secret_key = os.urandom(24)
    app.json_encoder = DateTimeEncoder
    
    # Configure session settings
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)  # Sessions last 7 days

    # Configuration for SSH log extractor
    app.config['LOG_DOWNLOAD_DIR'] = os.path.join(app.root_path, 'downloads')
    app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'uploads')
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size
    app.config['ALLOWED_EXTENSIONS'] = {'txt', 'log', 'zip'}
    
    # Register all blueprints using the organized structure
    register_blueprints(app)
    
    # Initialize database
    init_db(app)
    
    return app

# --- Database Initialization ---
def init_db(app: Flask):
    """Initialize database with tables in app.instance_path"""
    db_path = os.path.join(app.instance_path, 'myapp_db.db')
    os.makedirs(app.instance_path, exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Create user table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        id_role INTEGER,
        last_login DATETIME,
        FOREIGN KEY (id_role) REFERENCES role (id_role)
        )
    ''')

    # Migrate user columns for existing databases
    cursor.execute("PRAGMA table_info(user)")
    user_columns = {row[1] for row in cursor.fetchall()}
    if 'full_name' not in user_columns:
        cursor.execute("ALTER TABLE user ADD COLUMN full_name TEXT")
    
    # Create role table
    cursor.execute(''' 
    CREATE TABLE IF NOT EXISTS role (
        id_role INTEGER PRIMARY KEY AUTOINCREMENT,
        role_name TEXT UNIQUE NOT NULL,
        can_crud BOOLEAN DEFAULT 0
    )
    ''')
    
    # Create page table (remove id_role column)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS page (
            id_page INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            path TEXT UNIQUE NOT NULL,
            page_order INTEGER DEFAULT 0
        )
    ''')

    # Migrate page columns for existing databases
    cursor.execute("PRAGMA table_info(page)")
    page_columns = {row[1] for row in cursor.fetchall()}
    if 'icon' not in page_columns:
        cursor.execute("ALTER TABLE page ADD COLUMN icon TEXT")
    if 'parent_id' not in page_columns:
        cursor.execute("ALTER TABLE page ADD COLUMN parent_id INTEGER")
    
    # Create page_role junction table for many-to-many relationship
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS page_role (
            id_page_role INTEGER PRIMARY KEY AUTOINCREMENT,
            id_page INTEGER NOT NULL,
            id_role INTEGER NOT NULL,
            UNIQUE(id_page, id_role),
            FOREIGN KEY (id_page) REFERENCES page (id_page) ON DELETE CASCADE,
            FOREIGN KEY (id_role) REFERENCES role (id_role) ON DELETE CASCADE
        )
    ''')
    
    # Insert default roles if they don't exist
    cursor.execute("SELECT COUNT(*) FROM role")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO role (role_name, can_crud) VALUES (?, ?)", ('admin', 1))
        cursor.execute("INSERT INTO role (role_name, can_crud) VALUES (?, ?)", ('user', 0))
        cursor.execute("INSERT INTO role (role_name, can_crud) VALUES (?, ?)", ('manager', 1))
    
    # Insert default admin user if it doesn't exist
    cursor.execute("SELECT COUNT(*) FROM user WHERE username = 'admin'")
    if cursor.fetchone()[0] == 0:
        import bcrypt
        hashed_password = bcrypt.hashpw('admin123'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cursor.execute("INSERT INTO user (username, password, id_role) VALUES (?, ?, ?)", 
                      ('admin', hashed_password, 1))
    
    # Insert default pages if they don't exist
    cursor.execute("SELECT COUNT(*) FROM page")
    if cursor.fetchone()[0] == 0:
        default_pages = [
            ('Dashboard', '/', 1),
            ('Participant', '/participant', 2),
            ('Transactions', '/transaction', 3),
            ('Settings', '/settings', 4),
            ('Connection', '/connections', 5),
            ('Compare', '/compare', 6),
        ]
        for name, path, order in default_pages:
            cursor.execute("INSERT INTO page (name, path, page_order) VALUES (?, ?, ?)", (name, path, order))
    
    # Assign pages to roles (many-to-many)
    cursor.execute("SELECT COUNT(*) FROM page_role")
    if cursor.fetchone()[0] == 0:
        # Admin can access all pages
        cursor.execute("SELECT id_page FROM page")
        pages = cursor.fetchall()
        for page in pages:
            cursor.execute("INSERT INTO page_role (id_page, id_role) VALUES (?, ?)", (page[0], 1))
        
        # User can access dashboard and transactions
        cursor.execute("SELECT id_page FROM page WHERE name IN ('Dashboard', 'Transactions')")
        user_pages = cursor.fetchall()
        for page in user_pages:
            cursor.execute("INSERT INTO page_role (id_page, id_role) VALUES (?, ?)", (page[0], 2))
        
        # Manager can access dashboard, transactions, and reports
        cursor.execute("SELECT id_page FROM page WHERE name IN ('Dashboard', 'Transactions', 'Reports')")
        manager_pages = cursor.fetchall()
        for page in manager_pages:
            cursor.execute("INSERT INTO page_role (id_page, id_role) VALUES (?, ?)", (page[0], 3))

    # Ensure IDE parent menu exists and nest RENJS under it when present
    cursor.execute(
        "SELECT id_page FROM page WHERE path = ? OR lower(name) = ?",
        ('/ide', 'ide'),
    )
    ide_row = cursor.fetchone()
    ide_id = None
    if not ide_row:
        cursor.execute(
            "INSERT INTO page (name, path, page_order, icon, parent_id) VALUES (?, ?, ?, ?, NULL)",
            ('IDE', '/ide', 0, 'FiCode'),
        )
        ide_id = cursor.lastrowid
        cursor.execute(
            "SELECT id_page FROM page WHERE path = ? OR lower(name) = ?",
            ('/renjs', 'renjs'),
        )
        renjs_for_roles = cursor.fetchone()
        if renjs_for_roles:
            cursor.execute(
                "SELECT id_role FROM page_role WHERE id_page = ?",
                (renjs_for_roles[0],),
            )
            for (role_id,) in cursor.fetchall():
                cursor.execute(
                    "INSERT OR IGNORE INTO page_role (id_page, id_role) VALUES (?, ?)",
                    (ide_id, role_id),
                )
        cursor.execute("SELECT id_role FROM role WHERE role_name = 'admin'")
        admin_role = cursor.fetchone()
        if admin_role:
            cursor.execute(
                "INSERT OR IGNORE INTO page_role (id_page, id_role) VALUES (?, ?)",
                (ide_id, admin_role[0]),
            )
    else:
        ide_id = ide_row[0]

    cursor.execute(
        "SELECT id_page, parent_id FROM page WHERE path = ? OR lower(name) = ?",
        ('/renjs', 'renjs'),
    )
    renjs_row = cursor.fetchone()
    if renjs_row and renjs_row[1] is None and ide_id:
        cursor.execute(
            "UPDATE page SET parent_id = ? WHERE id_page = ?",
            (ide_id, renjs_row[0]),
        )

    # Ensure Container Plugin menu exists under IDE
    cursor.execute(
        "SELECT id_page, parent_id FROM page WHERE path = ? OR lower(name) = ?",
        ('/container-plugin', 'container plugin'),
    )
    cp_row = cursor.fetchone()
    if not cp_row and ide_id:
        cursor.execute(
            "INSERT INTO page (name, path, page_order, icon, parent_id) VALUES (?, ?, ?, ?, ?)",
            ('Container Plugin', '/container-plugin', 3, 'FiCpu', ide_id),
        )
        cp_id = cursor.lastrowid
        # Copy roles from IDE / RENJS; always include admin
        cursor.execute(
            "SELECT id_role FROM page_role WHERE id_page = ?",
            (ide_id,),
        )
        for (role_id,) in cursor.fetchall():
            cursor.execute(
                "INSERT OR IGNORE INTO page_role (id_page, id_role) VALUES (?, ?)",
                (cp_id, role_id),
            )
        cursor.execute("SELECT id_role FROM role WHERE role_name = 'admin'")
        admin_role = cursor.fetchone()
        if admin_role:
            cursor.execute(
                "INSERT OR IGNORE INTO page_role (id_page, id_role) VALUES (?, ?)",
                (cp_id, admin_role[0]),
            )
    elif cp_row and cp_row[1] is None and ide_id:
        cursor.execute(
            "UPDATE page SET parent_id = ? WHERE id_page = ?",
            (ide_id, cp_row[0]),
        )

    # Ensure GMM menu exists under IDE
    cursor.execute(
        "SELECT id_page, parent_id FROM page WHERE path = ? OR lower(name) = ?",
        ('/gmm', 'gmm'),
    )
    gmm_row = cursor.fetchone()
    if not gmm_row and ide_id:
        cursor.execute(
            "INSERT INTO page (name, path, page_order, icon, parent_id) VALUES (?, ?, ?, ?, ?)",
            ('GMM', '/gmm', 4, 'FiLayers', ide_id),
        )
        gmm_id = cursor.lastrowid
        cursor.execute(
            "SELECT id_role FROM page_role WHERE id_page = ?",
            (ide_id,),
        )
        for (role_id,) in cursor.fetchall():
            cursor.execute(
                "INSERT OR IGNORE INTO page_role (id_page, id_role) VALUES (?, ?)",
                (gmm_id, role_id),
            )
        cursor.execute("SELECT id_role FROM role WHERE role_name = 'admin'")
        admin_role = cursor.fetchone()
        if admin_role:
            cursor.execute(
                "INSERT OR IGNORE INTO page_role (id_page, id_role) VALUES (?, ?)",
                (gmm_id, admin_role[0]),
            )
    elif gmm_row and gmm_row[1] is None and ide_id:
        cursor.execute(
            "UPDATE page SET parent_id = ? WHERE id_page = ?",
            (ide_id, gmm_row[0]),
        )

    conn.commit()
    conn.close()
    print("Database initialized successfully.")

# Create the app instance
app = create_app()

if __name__ == '__main__':
    app.run(debug=True)