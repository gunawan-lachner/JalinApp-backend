import sqlite3
import json
import os
from datetime import datetime

DATABASE_FILE = os.path.join('instance', 'rna_rules_db.db')

def init_db():
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Create rules table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rule_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 0,
                workflow_data TEXT
            )
        ''')
        
        # Create table for individual rules
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_set_id INTEGER,
                rule_type TEXT NOT NULL,
                rule_json TEXT NOT NULL,
                order_position INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (rule_set_id) REFERENCES rule_sets(id) ON DELETE CASCADE
            )
        ''')
        
        # Create table for rule conditions
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rule_conditions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER,
                field_name TEXT NOT NULL,
                operator TEXT NOT NULL,
                value TEXT NOT NULL,
                logic_operator TEXT DEFAULT 'AND',
                group_id INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (rule_id) REFERENCES rules(id) ON DELETE CASCADE
            )
        ''')
        
        # Check if workflow_data column exists, add it if not
        cursor.execute("PRAGMA table_info(rule_sets)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'workflow_data' not in columns:
            cursor.execute("ALTER TABLE rule_sets ADD COLUMN workflow_data TEXT")
        
        conn.commit()
        print("RNA Rules database initialized successfully.")
    except Exception as e:
        print(f"Error initializing RNA Rules database: {e}")
    finally:
        if conn:
            conn.close()

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def get_rule_sets():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rule_sets ORDER BY updated_at DESC")
        rule_sets = [dict(row) for row in cursor.fetchall()]
        return rule_sets
    finally:
        conn.close()

def get_rule_set(rule_set_id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rule_sets WHERE id = ?", (rule_set_id,))
        rule_set = cursor.fetchone()
        
        if rule_set:
            rule_set = dict(rule_set)
            # Get all rules for this rule set
            cursor.execute("SELECT * FROM rules WHERE rule_set_id = ? ORDER BY order_position", (rule_set_id,))
            rules = [dict(row) for row in cursor.fetchall()]
            
            # For each rule, get its conditions
            for rule in rules:
                rule_id = rule['id']
                cursor.execute("SELECT * FROM rule_conditions WHERE rule_id = ?", (rule_id,))
                conditions = [dict(row) for row in cursor.fetchall()]
                rule['conditions'] = conditions
                
                # Parse the JSON string to Python object
                rule['rule_json'] = json.loads(rule['rule_json'])
            
            rule_set['rules'] = rules
            
            # Parse workflow data if it exists
            if rule_set.get('workflow_data'):
                try:
                    rule_set['workflow_data'] = json.loads(rule_set['workflow_data'])
                except:
                    rule_set['workflow_data'] = None
            
            return rule_set
        return None
    finally:
        conn.close()

def create_rule_set(name, description=""):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO rule_sets (name, description) VALUES (?, ?)",
            (name, description)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def update_rule_set(rule_set_id, name, description, is_active):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE rule_sets SET name = ?, description = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, description, is_active, rule_set_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def delete_rule_set(rule_set_id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # First delete associated rules (cascade will handle conditions)
        cursor.execute("DELETE FROM rules WHERE rule_set_id = ?", (rule_set_id,))
        cursor.execute("DELETE FROM rule_sets WHERE id = ?", (rule_set_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def create_rule(rule_set_id, rule_type, rule_json, order_position):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO rules (rule_set_id, rule_type, rule_json, order_position) VALUES (?, ?, ?, ?)",
            (rule_set_id, rule_type, json.dumps(rule_json), order_position)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def update_rule(rule_id, rule_type, rule_json, order_position):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE rules SET rule_type = ?, rule_json = ?, order_position = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (rule_type, json.dumps(rule_json), order_position, rule_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def delete_rule(rule_id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Delete the rule (cascade will handle conditions)
        cursor.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def save_rule_conditions(rule_id, conditions):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # First delete existing conditions for this rule
        cursor.execute("DELETE FROM rule_conditions WHERE rule_id = ?", (rule_id,))
        
        # Then insert new conditions
        for condition in conditions:
            cursor.execute(
                "INSERT INTO rule_conditions (rule_id, field_name, operator, value, logic_operator, group_id) VALUES (?, ?, ?, ?, ?, ?)",
                (rule_id, condition['field_name'], condition['operator'], condition['value'], 
                 condition.get('logic_operator', 'AND'), condition.get('group_id', 0))
            )
        conn.commit()
        return True
    finally:
        conn.close()

def export_rule_set_to_file(rule_set_id, file_path):
    rule_set = get_rule_set(rule_set_id)
    if not rule_set:
        return False
    
    # Convert rule set to the format expected by rules.txt
    rules_output = ["rule"]  # First line is always "rule"
    
    for rule in rule_set['rules']:
        rule_text = rule['rule_json'].get('rule_text', '')
        if rule_text:
            rules_output.append(rule_text)
    
    # Write to file
    with open(file_path, 'w') as f:
        f.write('\n'.join(rules_output))
    
    return True

def import_rules_from_file(file_path, rule_set_name=None):
    if not os.path.exists(file_path):
        return False
    
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    # Skip the first line if it's "rule"
    if lines and lines[0].strip().lower() == "rule":
        lines = lines[1:]
    
    # Create a new rule set
    if not rule_set_name:
        rule_set_name = f"Imported Rules {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    rule_set_id = create_rule_set(rule_set_name, "Imported from file")
    
    # Add each line as a rule
    for i, line in enumerate(lines):
        line = line.strip()
        if line:
            rule_json = {
                "rule_text": line,
                "parsed_components": parse_rule_text(line)
            }
            create_rule(rule_set_id, "condition", rule_json, i)
    
    return rule_set_id

def parse_rule_text(rule_text):
    """
    Parse a rule text into structured components.
    This is a basic implementation and can be expanded based on rule complexity.
    """
    # Remove outer parentheses if present
    rule_text = rule_text.strip()
    if rule_text.startswith('(') and rule_text.endswith(')'):
        rule_text = rule_text[1:-1]
    
    # Split by logical operators
    components = []
    
    # Handle simple cases with && and ||
    if '&&' in rule_text:
        parts = rule_text.split('&&')
        for part in parts:
            components.append({
                "type": "condition",
                "text": part.strip(),
                "operator": "&&"
            })
    elif '||' in rule_text:
        parts = rule_text.split('||')
        for part in parts:
            components.append({
                "type": "condition",
                "text": part.strip(),
                "operator": "||"
            })
    else:
        # Single condition
        components.append({
            "type": "condition",
            "text": rule_text,
            "operator": None
        })
    
    return components

def save_workflow_data(rule_set_id, workflow_data):
    """Save workflow data for a rule set"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Check if the rule set exists
        cursor.execute("SELECT id FROM rule_sets WHERE id = ?", (rule_set_id,))
        rule_set = cursor.fetchone()
        if not rule_set:
            return False
        
        # Update the rule set with workflow data
        cursor.execute(
            "UPDATE rule_sets SET workflow_data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(workflow_data), rule_set_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"Error saving workflow data: {e}")
        return False
    finally:
        conn.close()

# Initialize the database when this module is imported
if not os.path.exists(DATABASE_FILE):
    init_db() 