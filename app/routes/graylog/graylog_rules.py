import os
import sqlite3
from flask import jsonify, request, Blueprint
import re

# Create Blueprint
graylog_rules_bp = Blueprint('graylog_rules', __name__)

# SQLite database file
DB_NAME = os.path.join('instance', 'myapp_db.db')

def get_db_connection():
    """Create a connection to the SQLite database"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # This enables column access by name
    return conn

def init_graylog_rules_table():
    """Initialize the graylog_rules table if it doesn't exist"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create graylog_rules table if it doesn't exist
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS graylog_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_word TEXT NOT NULL,
            action TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            rules_mode TEXT DEFAULT 'all',
            enable_link INTEGER DEFAULT 0,
            url TEXT DEFAULT '',
            extra_attributes TEXT DEFAULT '{}'
        )
        ''')
        
        # Check if the table is empty
        cursor.execute('SELECT COUNT(*) FROM graylog_rules')
        count = cursor.fetchone()[0]
        
        # If the table is empty, add default rules
        if count == 0:
            default_rules = [
                ('error|exception|failed|failure', 'background-color: #ffcccc; color: #990000;', 1, 'all', 0, '', '{}'),
                ('warning|warn', 'background-color: #fff3cd; color: #856404;', 1, 'all', 0, '', '{}'),
                ('success|completed|successful', 'background-color: #d4edda; color: #155724;', 1, 'all', 0, '', '{}'),
                ('timeout|timed out', 'background-color: #f8d7da; color: #721c24; text-decoration: underline;', 1, 'all', 0, '', '{}'),
                ('http.*(?:400|401|403|404|500|502|503)', 'background-color: #e2cfc4; color: #856404; font-weight: bold;', 1, 'all', 0, '', '{}'),
                ('card.*([0-9]{6})\\*{6}([0-9]{4})', 'background-color: #cce5ff; color: #004085; border-left: 5px solid #004085;', 1, 'all', 0, '', '{}'),
                ('trx_id: "([0-9]+)"', 'background-color: #d1ecf1; color: #0c5460; font-style: italic;', 1, 'all', 0, '', '{}'),
                ('database.*(connect|error|fail)', 'background-color: #f5c6cb; color: #721c24; border: 1px dashed #721c24;', 1, 'all', 0, '', '{}')
            ]
            
            cursor.executemany(
                'INSERT INTO graylog_rules (key_word, action, active, rules_mode, enable_link, url, extra_attributes) VALUES (?, ?, ?, ?, ?, ?, ?)',
                default_rules
            )
            
            print("Initialized default Graylog rules")
        else:
            # Check if we need to add new columns to existing table
            try:
                cursor.execute('SELECT rules_mode FROM graylog_rules LIMIT 1')
            except sqlite3.OperationalError:
                # Add missing columns to existing table
                print("Adding missing columns to graylog_rules table")
                cursor.execute('ALTER TABLE graylog_rules ADD COLUMN rules_mode TEXT DEFAULT "all"')
                cursor.execute('ALTER TABLE graylog_rules ADD COLUMN enable_link INTEGER DEFAULT 0')
                cursor.execute('ALTER TABLE graylog_rules ADD COLUMN url TEXT DEFAULT ""')
                cursor.execute('ALTER TABLE graylog_rules ADD COLUMN extra_attributes TEXT DEFAULT "{}"')
        
        conn.commit()
    except Exception as e:
        print(f"Error initializing graylog_rules table: {e}")
    finally:
        if conn:
            conn.close()

# Initialize the table when module is imported
init_graylog_rules_table()

@graylog_rules_bp.route('/', methods=['GET'])
def get_rules():
    """Get all Graylog rules"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get filter for active rules
        show_inactive = request.args.get('show_inactive', 'true').lower() == 'true'
        
        if show_inactive:
            cursor.execute('SELECT * FROM graylog_rules ORDER BY key_word')
        else:
            cursor.execute('SELECT * FROM graylog_rules WHERE active = 1 ORDER BY key_word')
            
        rules = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return jsonify({'success': True, 'rules': rules})
    
    except sqlite3.Error as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@graylog_rules_bp.route('/', methods=['POST'])
def add_rule():
    """Add a new rule to the database"""
    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        key_word = data.get('key_word')
        action = data.get('action')
        active = data.get('active', True)
        
        # Get the new fields
        rules_mode = data.get('rules_mode', 'all')
        enable_link = data.get('enable_link', False)
        url = data.get('url', '')
        extra_attributes = data.get('extra_attributes', '{}')
        
        if not key_word or not action:
            return jsonify({"success": False, "error": "key_word and action are required"}), 400
        
        # Try to compile the regex to validate it
        try:
            re.compile(key_word)
        except re.error as e:
            return jsonify({"success": False, "error": f"Invalid regex pattern: {str(e)}"}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if key_word already exists
        cursor.execute('SELECT id, action FROM graylog_rules WHERE key_word = ?', (key_word,))
        existing_rule = cursor.fetchone()
        
        if existing_rule:
            conn.close()
            return jsonify({
                "success": False, 
                "error": "Duplicate key_word", 
                "existing_id": existing_rule[0],
                "existing_action": existing_rule[1]
            }), 409
        
        # Insert new rule with all fields
        cursor.execute(
            'INSERT INTO graylog_rules (key_word, action, active, rules_mode, enable_link, url, extra_attributes) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (key_word, action, 1 if active else 0, rules_mode, 1 if enable_link else 0, url, extra_attributes)
        )
        
        # Get the ID of the newly inserted rule
        new_rule_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True, 
            "message": "Rule added successfully",
            "rule": {
                "id": new_rule_id,
                "key_word": key_word,
                "action": action,
                "active": active,
                "rules_mode": rules_mode,
                "enable_link": enable_link,
                "url": url,
                "extra_attributes": extra_attributes
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@graylog_rules_bp.route('/<int:rule_id>', methods=['PUT'])
def update_rule(rule_id):
    """Update an existing rule"""
    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        # Get fields to update
        key_word = data.get('key_word')
        action = data.get('action')
        active = data.get('active')
        
        # Get the new fields
        rules_mode = data.get('rules_mode')
        enable_link = data.get('enable_link')
        url = data.get('url')
        extra_attributes = data.get('extra_attributes')
        
        # Check if we're only updating the active status
        is_active_only_update = active is not None and key_word is None and action is None
        
        # For full updates, require key_word and action
        if not is_active_only_update and (key_word is None or action is None):
            return jsonify({"success": False, "error": "key_word and action are required for full updates"}), 400
        
        # Validate regex if provided
        if key_word:
            try:
                re.compile(key_word)
            except re.error as e:
                return jsonify({"success": False, "error": f"Invalid regex pattern: {str(e)}"}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if rule exists
        cursor.execute('SELECT * FROM graylog_rules WHERE id = ?', (rule_id,))
        existing_rule = cursor.fetchone()
        
        if not existing_rule:
            conn.close()
            return jsonify({"success": False, "error": "Rule not found"}), 404
        
        # Convert to dictionary for easier access
        existing_rule = dict(existing_rule)
        
        # Check for duplicate key_word if changing it
        if key_word and key_word != existing_rule['key_word']:
            cursor.execute('SELECT * FROM graylog_rules WHERE key_word = ? AND id != ?', (key_word, rule_id))
            duplicate = cursor.fetchone()
            
            if duplicate:
                conn.close()
                return jsonify({
                    "success": False, 
                    "error": "Duplicate key_word",
                    "existing_id": duplicate['id'],
                    "existing_action": duplicate['action']
                }), 409
        
        # Update the rule
        if is_active_only_update:
            # Only update active status
            cursor.execute(
                'UPDATE graylog_rules SET active = ? WHERE id = ?',
                (1 if active else 0, rule_id)
            )
            updated_key_word = existing_rule['key_word']
            updated_action = existing_rule['action']
            updated_rules_mode = existing_rule['rules_mode'] if 'rules_mode' in existing_rule else 'all'
            updated_enable_link = existing_rule['enable_link'] if 'enable_link' in existing_rule else 0
            updated_url = existing_rule['url'] if 'url' in existing_rule else ''
            updated_extra_attributes = existing_rule['extra_attributes'] if 'extra_attributes' in existing_rule else '{}'
        else:
            # Build the update query and parameters
            update_fields = ['key_word = ?', 'action = ?', 'active = ?']
            params = [key_word, action, 1 if active else 0]
            
            # Add the new fields if they're provided
            if rules_mode is not None:
                update_fields.append('rules_mode = ?')
                params.append(rules_mode)
            else:
                # Use existing value or default
                rules_mode = existing_rule.get('rules_mode', 'all')
                
            if enable_link is not None:
                update_fields.append('enable_link = ?')
                params.append(1 if enable_link else 0)
            else:
                # Use existing value or default
                enable_link = existing_rule.get('enable_link', 0)
                
            if url is not None:
                update_fields.append('url = ?')
                params.append(url)
            else:
                # Use existing value or default
                url = existing_rule.get('url', '')
                
            if extra_attributes is not None:
                update_fields.append('extra_attributes = ?')
                params.append(extra_attributes)
            else:
                # Use existing value or default
                extra_attributes = existing_rule.get('extra_attributes', '{}')
            
            # Add the rule_id to the parameters
            params.append(rule_id)
            
            # Execute the update query
            cursor.execute(
                f'UPDATE graylog_rules SET {", ".join(update_fields)} WHERE id = ?',
                params
            )
            
            updated_key_word = key_word
            updated_action = action
            updated_rules_mode = rules_mode
            updated_enable_link = enable_link
            updated_url = url
            updated_extra_attributes = extra_attributes
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True, 
            "message": "Rule updated successfully",
            "rule": {
                "id": rule_id,
                "key_word": updated_key_word,
                "action": updated_action,
                "active": 1 if active else 0,
                "rules_mode": updated_rules_mode,
                "enable_link": 1 if updated_enable_link else 0,
                "url": updated_url,
                "extra_attributes": updated_extra_attributes
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@graylog_rules_bp.route('/<int:rule_id>', methods=['DELETE'])
def delete_rule(rule_id):
    """Delete a rule from the database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if rule exists
        cursor.execute('SELECT * FROM graylog_rules WHERE id = ?', (rule_id,))
        rule = cursor.fetchone()
        
        if not rule:
            conn.close()
            return jsonify({"success": False, "error": "Rule not found"}), 404
        
        # Convert to dictionary to return in response
        rule_data = dict(rule)
        
        # Delete rule
        cursor.execute('DELETE FROM graylog_rules WHERE id = ?', (rule_id,))
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True, 
            "message": "Rule deleted successfully",
            "rule": rule_data
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@graylog_rules_bp.route('/replace', methods=['POST'])
def replace_rule():
    """Replace an existing rule with the same key_word"""
    try:
        data = request.json
        key_word = data.get('key_word')
        action = data.get('action')
        active = data.get('active', True)
        
        # Get the new fields
        rules_mode = data.get('rules_mode', 'all')
        enable_link = data.get('enable_link', False)
        url = data.get('url', '')
        extra_attributes = data.get('extra_attributes', '{}')
        
        # Validate required fields
        if not key_word or not action:
            return jsonify({'success': False, 'error': 'Key word and action are required'}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Update or insert rule with all fields
        cursor.execute(
            'INSERT OR REPLACE INTO graylog_rules (key_word, action, active, rules_mode, enable_link, url, extra_attributes) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (key_word, action, 1 if active else 0, rules_mode, 1 if enable_link else 0, url, extra_attributes)
        )
        
        conn.commit()
        rule_id = cursor.lastrowid
        conn.close()
        
        return jsonify({
            'success': True, 
            'message': 'Rule replaced successfully',
            'rule_id': rule_id,
            'rule': {
                'id': rule_id,
                'key_word': key_word,
                'action': action,
                'active': active,
                'rules_mode': rules_mode,
                'enable_link': enable_link,
                'url': url,
                'extra_attributes': extra_attributes
            }
        })
    
    except sqlite3.Error as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@graylog_rules_bp.route('/init-defaults', methods=['POST'])
def init_default_rules():
    """Initialize default rules if the table is empty"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if the table is empty
        cursor.execute('SELECT COUNT(*) FROM graylog_rules')
        count = cursor.fetchone()[0]
        
        if count > 0:
            conn.close()
            return jsonify({
                "success": False,
                "error": "Rules already exist in the database. Cannot initialize defaults.",
                "count": count
            }), 409
        
        # Add default rules
        default_rules = [
            ('error|exception|failed|failure', 'background-color: #ffcccc; color: #990000;', 1, 'all', 0, '', '{}'),
            ('warning|warn', 'background-color: #fff3cd; color: #856404;', 1, 'all', 0, '', '{}'),
            ('success|completed|successful', 'background-color: #d4edda; color: #155724;', 1, 'all', 0, '', '{}'),
            ('timeout|timed out', 'background-color: #f8d7da; color: #721c24; text-decoration: underline;', 1, 'all', 0, '', '{}'),
            ('http.*(?:400|401|403|404|500|502|503)', 'background-color: #e2cfc4; color: #856404; font-weight: bold;', 1, 'all', 0, '', '{}'),
            ('card.*([0-9]{6})\\*{6}([0-9]{4})', 'background-color: #cce5ff; color: #004085; border-left: 5px solid #004085;', 1, 'all', 0, '', '{}'),
            ('trx_id: "([0-9]+)"', 'background-color: #d1ecf1; color: #0c5460; font-style: italic;', 1, 'all', 0, '', '{}'),
            ('database.*(connect|error|fail)', 'background-color: #f5c6cb; color: #721c24; border: 1px dashed #721c24;', 1, 'all', 0, '', '{}')
        ]
        
        cursor.executemany(
            'INSERT INTO graylog_rules (key_word, action, active, rules_mode, enable_link, url, extra_attributes) VALUES (?, ?, ?, ?, ?, ?, ?)',
            default_rules
        )
        
        conn.commit()
        
        # Fetch the newly added rules
        cursor.execute('SELECT * FROM graylog_rules')
        rules = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "Default rules initialized successfully",
            "rules": rules
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500 