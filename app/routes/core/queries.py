from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
import sqlite3
import json
import re
import traceback
import mysql.connector
from mysql.connector import Error
import pymysql
import time
from datetime import datetime
import logging
import requests
from bs4 import BeautifulSoup
import os
import re
from app.config.redmine_config import REDMINE_USERNAME, REDMINE_PASSWORD, REDMINE_URL

# Configure logging
logging.basicConfig(level=logging.DEBUG, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                   handlers=[logging.FileHandler('column_order_debug.log'), 
                             logging.StreamHandler()])
logger = logging.getLogger('column_order')

# --- SQLite Configuration --- 
DATABASE_FILE = os.path.join('instance', 'myapp_db.db')

# Initialize Blueprint
queries_bp = Blueprint('core_queries', __name__)

def init_queries_table():
    """
    Creates the queries table in the SQLite database if it doesn't exist.
    This table will store saved SQL queries with parameters.
    """
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Create queries table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                sql_script TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                connection_name TEXT,
                parameters TEXT,
                created_by TEXT
            )
        ''')
        
        # Create query_parameters table to store parameters for each query
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS query_parameters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id INTEGER NOT NULL,
                param_name TEXT NOT NULL,
                param_type TEXT NOT NULL,
                param_default TEXT,
                param_description TEXT,
                param_order INTEGER,
                FOREIGN KEY (query_id) REFERENCES queries(id) ON DELETE CASCADE
            )
        ''')
        
        conn.commit()
        print("Queries tables initialized successfully.")
    except Exception as e:
        print(f"Error initializing queries tables: {e}")
    finally:
        if conn:
            conn.close()

def get_db_connection():
    """Create and return a connection to the SQLite database."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def load_profiles():
    """Load all connection profiles from the database."""
    profiles_list = []
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM connection ORDER BY name")
        rows = cursor.fetchall()
        profiles_list = [dict(row) for row in rows]
    except Exception as e:
        print(f"Error loading profiles from database: {e}")
    finally:
        if conn:
            conn.close()
    return {"profiles": profiles_list}

def extract_parameters(sql_script):
    """
    Extract parameter placeholders from SQL script.
    Finds '?' or named parameters like ':param_name' or '%(param_name)s'.
    Returns a list of unique parameter names.
    """
    parameters = []
    
    # Find standard ? placeholders
    question_marks = sql_script.count('?')
    for i in range(question_marks):
        parameters.append({
            'name': f'param{i+1}',
            'type': 'string',
            'default': '',
            'description': f'Parameter {i+1}'
        })
    
    # Find named parameters like :param_name or %(param_name)s
    named_params = re.findall(r':([a-zA-Z0-9_]+)', sql_script)
    mysql_params = re.findall(r'%\(([a-zA-Z0-9_]+)\)s', sql_script)
    
    # Add named parameters with deduplication
    unique_names = set()
    for param in named_params + mysql_params:
        if param not in unique_names:
            unique_names.add(param)
            parameters.append({
                'name': param,
                'type': 'string',
                'default': '',
                'description': f'Parameter {param}'
            })
    
    return parameters

def get_db_connection_by_name(connection_name):
    """Get database connection details by name"""
    conn = None
    connection_info = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM connection WHERE name = ?", (connection_name,))
        row = cursor.fetchone()
        if row:
            connection_info = dict(row)
    except Exception as e:
        print(f"Error getting connection info: {e}")
    finally:
        if conn:
            conn.close()
    return connection_info

def connect_to_database(connection_info):
    """Connect to MySQL/MariaDB database using the provided connection info"""
    if connection_info['connection_type'] == 'DB':
        try:
            connection = mysql.connector.connect(
                host=connection_info['host'],
                port=connection_info['port'],
                user=connection_info['user'],
                password=connection_info['password'],
                database=connection_info['database'],
                charset='utf8mb4',
                collation='utf8mb4_unicode_ci'
            )
            return connection
        except Error as e:
            print(f"MySQL Connection Error: {e}")
            return None
    elif connection_info['connection_type'] == 'mariadb':
        try:
            connection = pymysql.connect(
                host=connection_info['host'],
                port=connection_info['port'],
                user=connection_info['user'],
                password=connection_info['password'],
                database=connection_info['database'],
                charset='utf8mb4'
            )
            return connection
        except Exception as e:
            print(f"MariaDB Connection Error: {e}")
            return None
    else:
        print(f"Unsupported connection type: {connection_info['connection_type']}")
        return None

@queries_bp.route('/queries')
def queries_page():
    """Render the main queries page."""
    # Initialize the queries table if it doesn't exist
    init_queries_table()
    
    # Get saved connection profiles
    profiles = load_profiles()
    
    # Set default dates for filtering
    today = datetime.now()
    default_date_start = f"{today.strftime('%Y-%m-%d')} 00:00:01"
    default_date_end = f"{today.strftime('%Y-%m-%d')} 23:59:59"
    
    # Get connection from session or use None
    selected_connection = session.get('selected_qry_connection', '')
    
    return render_template(
        'rna/rna_queries.html',
        #saved_profiles=profiles['profiles'], <- this is the old way
        saved_profiles = [p for p in load_profiles()['profiles'] if p.get('connection_type') == 'DB'], #this is the new way, only show DB connection
        selected_connection=selected_connection,
        default_date_start=default_date_start,
        default_date_end=default_date_end
    )

@queries_bp.route('/queries/list')
def list_queries():
    """Get list of all saved queries."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, description, created_at, updated_at, connection_name
            FROM queries
            ORDER BY name
        """)
        queries = [dict(row) for row in cursor.fetchall()]
        return jsonify({'status': 'success', 'queries': queries})
    except Exception as e:
        print(f"Error listing queries: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn:
            conn.close()

@queries_bp.route('/queries/get/<int:query_id>')
def get_query(query_id):
    """Get a specific query by ID."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get query details
        cursor.execute("""
            SELECT id, name, sql_script, description, created_at, updated_at, 
                   connection_name, parameters, created_by
            FROM queries
            WHERE id = ?
        """, (query_id,))
        query = cursor.fetchone()
        
        if not query:
            return jsonify({'status': 'error', 'message': 'Query not found'}), 404
        
        query_dict = dict(query)
        
        # If parameters are stored as JSON string, parse them
        if query_dict['parameters']:
            try:
                query_dict['parameters'] = json.loads(query_dict['parameters'])
            except:
                query_dict['parameters'] = []
        else:
            query_dict['parameters'] = []
        
        return jsonify({'status': 'success', 'query': query_dict})
    except Exception as e:
        print(f"Error getting query: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn:
            conn.close()

@queries_bp.route('/queries/get_by_name/<query_name>')
def get_query_by_name(query_name):
    """Get a specific query by name."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get query details
        cursor.execute("""
            SELECT id, name, sql_script, description, created_at, updated_at, 
                   connection_name, parameters, created_by
            FROM queries
            WHERE name = ?
        """, (query_name,))
        query = cursor.fetchone()
        
        if not query:
            return jsonify({'status': 'error', 'message': 'Query not found'}), 404
        
        query_dict = dict(query)
        
        # If parameters are stored as JSON string, parse them
        if query_dict['parameters']:
            try:
                query_dict['parameters'] = json.loads(query_dict['parameters'])
            except:
                query_dict['parameters'] = []
        else:
            query_dict['parameters'] = []
        
        return jsonify({'status': 'success', 'query': query_dict})
    except Exception as e:
        print(f"Error getting query: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn:
            conn.close()

@queries_bp.route('/queries/save', methods=['POST'])
def save_query():
    """Save a new query or update an existing one."""
    data = request.get_json()
    
    query_id = data.get('id')
    name = data.get('name')
    sql_script = data.get('sql_script')
    description = data.get('description', '')
    parameters = data.get('parameters', [])
    created_by = data.get('created_by', '')
    
    if not name or not sql_script:
        return jsonify({'status': 'error', 'message': 'Name and SQL script are required'}), 400
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # If parameters are empty, extract them from the SQL script
        if not parameters:
            parameters = extract_parameters(sql_script)
        
        # Serialize parameters to JSON string
        parameters_json = json.dumps(parameters)
        
        if query_id:  # Update existing query
            cursor.execute("""
                UPDATE queries
                SET name = ?, sql_script = ?, description = ?, updated_at = CURRENT_TIMESTAMP,
                    parameters = ?, created_by = ?
                WHERE id = ?
            """, (name, sql_script, description, parameters_json, created_by, query_id))
        else:  # Create new query
            cursor.execute("""
                INSERT INTO queries (name, sql_script, description, parameters, created_by)
                VALUES (?, ?, ?, ?, ?)
            """, (name, sql_script, description, parameters_json, created_by))
            query_id = cursor.lastrowid
        
        conn.commit()
        return jsonify({'status': 'success', 'query_id': query_id})
    except Exception as e:
        print(f"Error saving query: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn:
            conn.close()

@queries_bp.route('/queries/delete/<int:query_id>', methods=['DELETE'])
def delete_query(query_id):
    """Delete a query by ID."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if query exists
        cursor.execute("SELECT id FROM queries WHERE id = ?", (query_id,))
        if not cursor.fetchone():
            return jsonify({'status': 'error', 'message': 'Query not found'}), 404
        
        # Delete the query
        cursor.execute("DELETE FROM queries WHERE id = ?", (query_id,))
        conn.commit()
        
        return jsonify({'status': 'success'})
    except Exception as e:
        print(f"Error deleting query: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn:
            conn.close()

@queries_bp.route('/queries/execute', methods=['POST'])
def execute_query():
    """Execute a query with provided parameters."""
    logger.debug("Starting execute_query function")
    data = request.get_json()
    
    query_id = data.get('query_id')
    query_name = data.get('query_name')
    direct_sql = data.get('direct_sql')
    parameters = data.get('parameters', {})
    connection_name = data.get('connection_name')
    page = data.get('page', 1)
    page_size = data.get('page_size', 50)
    
    if not connection_name:
        return jsonify({
            'status': 'error', 
            'message': 'Connection name is required'
        }), 400
        
    if not (query_id or query_name or direct_sql):
        return jsonify({
            'status': 'error', 
            'message': 'Either query_id, query_name, or direct_sql is required'
        }), 400
    
    conn = None
    try:
        # Get SQL script - either from database or directly provided
        sql_script = None
        
        if direct_sql:
            # Use direct SQL
            sql_script = direct_sql
            # If this is direct SQL, there's no query ID to save parameter defaults
            query_id = None
        else:
            # Get from database
            conn = get_db_connection()
            cursor = conn.cursor()
            
            if query_id:
                cursor.execute("SELECT * FROM queries WHERE id = ?", (query_id,))
            else:
                cursor.execute("SELECT * FROM queries WHERE name = ?", (query_name,))
            
            query = cursor.fetchone()
            if not query:
                return jsonify({'status': 'error', 'message': 'Query not found'}), 404
            
            sql_script = query['sql_script']
            query_id = query['id']
            
            # If saved query has parameters defined, use them to populate missing parameters
            if query['parameters']:
                try:
                    saved_params = json.loads(query['parameters'])
                    # Use saved default values for any parameters not provided
                    for param in saved_params:
                        if param['name'] not in parameters and param['default']:
                            parameters[param['name']] = param['default']
                except:
                    pass
        
        logger.debug(f"SQL script to execute: {sql_script}")
        
        # Extract table name if this is a SELECT * query
        select_all_query = "SELECT *" in sql_script.upper()
        table_name = None
        schema_name = None
        
        if select_all_query:
            logger.debug("Detected SELECT * query")
            # First remove SQL comments to avoid confusion in parsing
            sql_no_comments = re.sub(r'--.*$', '', sql_script, flags=re.MULTILINE)
            sql_no_comments = re.sub(r'/\*.*?\*/', '', sql_no_comments, flags=re.DOTALL)
            
            # Extract table name using a simpler approach
            from_parts = sql_no_comments.upper().split('FROM')
            if len(from_parts) > 1:
                # Get everything after FROM until the next keyword
                after_from = from_parts[1].strip()
                # Look for the first word after FROM, which should be the table name
                # It might be followed by WHERE, JOIN, ORDER BY, etc.
                for keyword in [' WHERE ', ' JOIN ', ' LEFT ', ' RIGHT ', ' INNER ', ' ORDER ', ' GROUP ', ' HAVING ', ' LIMIT ', ';']:
                    if keyword in after_from:
                        after_from = after_from.split(keyword)[0]
                
                # Now after_from should contain just the table name, possibly with an alias
                table_parts = after_from.strip().split()
                if table_parts:
                    full_table_name = table_parts[0].strip().replace('`', '')
                    # Check for schema.table format
                    if '.' in full_table_name:
                        schema_parts = full_table_name.split('.')
                        schema_name = schema_parts[0]
                        table_name = schema_parts[1]
                    else:
                        table_name = full_table_name
                    logger.debug(f"Extracted table info - schema: {schema_name}, table: {table_name}")
        
        # Get database connection info
        connection_info = get_db_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'status': 'error', 'message': 'Connection not found'}), 404
        
        # Connect to the database
        db_conn = connect_to_database(connection_info)
        if not db_conn:
            return jsonify({'status': 'error', 'message': 'Failed to connect to database'}), 500
        
        try:
            # Extract parameter positions from SQL query
            # We need to look for ? placeholders and see if they map to column names in WHERE clauses
            # This can help us build more meaningful parameter names
            question_marks_count = sql_script.count('?')
            
            # Find column names in WHERE clauses with ? placeholders
            where_clause_regex = re.compile(r'(?:WHERE|AND|OR)\s+([a-zA-Z0-9_\.]+)\s*=\s*\?', re.IGNORECASE)
            where_matches = where_clause_regex.findall(sql_script)
            
            # Prepare parameters based on SQL script and provided parameters
            param_values = []
            if '?' in sql_script:
                # For positional parameters
                for i in range(question_marks_count):
                    # Try to get parameter name from WHERE clause
                    param_name = None
                    if i < len(where_matches):
                        # Extract the field name from the table.field or just field
                        field_name = where_matches[i].split('.')[-1]
                        if field_name in parameters:
                            param_name = field_name
                    
                    # If we couldn't get a field name, use the default param{i+1}
                    if not param_name:
                        param_name = f"param{i+1}"
                    
                    param_values.append(parameters.get(param_name, ''))
            elif any(p in sql_script for p in [':', '%(', ')']):
                # For named parameters
                param_values = parameters
            
            # For SELECT * queries, we need to first get the table structure to ensure correct column ordering
            table_columns = []
            if select_all_query and table_name:
                try:
                    # Get current database if no schema specified
                    if not schema_name:
                        try:
                            info_cursor = db_conn.cursor(dictionary=True)
                            info_cursor.execute("SELECT DATABASE()")
                            current_db = info_cursor.fetchone()
                            if current_db and current_db.get('DATABASE()'):
                                schema_name = current_db.get('DATABASE()')
                                logger.debug(f"Using current database: {schema_name}")
                            info_cursor.close()
                        except Exception as e:
                            logger.error(f"Error getting current database: {e}")
                    
                    # Get column order directly from INFORMATION_SCHEMA
                    info_cursor = db_conn.cursor(dictionary=True)
                    query = """
                        SELECT COLUMN_NAME 
                        FROM INFORMATION_SCHEMA.COLUMNS 
                        WHERE TABLE_NAME = %s AND TABLE_SCHEMA = %s
                        ORDER BY ORDINAL_POSITION
                    """
                    logger.debug(f"Executing INFORMATION_SCHEMA query with params: table={table_name}, db={schema_name}")
                    info_cursor.execute(query, (table_name, schema_name))
                    info_rows = info_cursor.fetchall()
                    
                    if info_rows:
                        table_columns = [row['COLUMN_NAME'] for row in info_rows]
                        logger.debug(f"Retrieved {len(table_columns)} columns from INFORMATION_SCHEMA: {table_columns}")
                    else:
                        logger.warning(f"No columns found in INFORMATION_SCHEMA for {schema_name}.{table_name}")
                    
                    info_cursor.close()
                except Exception as e:
                    logger.error(f"Error fetching column order from INFORMATION_SCHEMA: {e}")
                    traceback.print_exc()
            
            # Execute the query
            cursor = db_conn.cursor(dictionary=True)
            start_time = time.time()
            logger.debug(f"Executing SQL with params: {param_values}")
            cursor.execute(sql_script, param_values)
            
            # Get column names from cursor description
            columns = [column[0] for column in cursor.description] if cursor.description else []
            logger.debug(f"Original columns from cursor: {columns}")
            
            # For SELECT * queries, reorder columns based on table structure
            if select_all_query and table_columns:
                logger.debug("Reordering columns based on table structure")
                
                # Create a mapping of columns to their positions
                column_positions = {col: idx for idx, col in enumerate(table_columns)}
                
                # Sort columns based on their order in the table structure
                # First, make sure all columns from cursor are in our mapping
                for col in columns:
                    if col not in column_positions:
                        column_positions[col] = 999  # Assign a high position to unknown columns
                
                # Now sort based on positions
                columns = sorted(columns, key=lambda col: column_positions.get(col, 999))
                logger.debug(f"Final ordered columns: {columns}")
            
            # Fetch all results for pagination
            all_results = cursor.fetchall()
            total_count = len(all_results)
            
            # Apply pagination
            page = int(page)  # Ensure page is an integer
            page_size = int(page_size)  # Ensure page_size is an integer
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            results = all_results[start_idx:end_idx] if total_count > 0 else []
            
            execution_time = time.time() - start_time
            
            # Convert results to list of dicts with the proper column order
            if results:
                # Ensure dictionaries have fields in the correct order
                # by creating new ordered dicts
                ordered_results = []
                for row in results:
                    ordered_row = {}
                    for col in columns:
                        ordered_row[col] = row.get(col)
                    ordered_results.append(ordered_row)
                results = ordered_results
                logger.debug(f"Reordered results to match column order, sample: {results[0].keys() if results else []}")
            
            # Save parameters as default values for next time if using a saved query
            if query_id and parameters:
                update_parameter_defaults(query_id, parameters)
            
            logger.debug(f"Query executed successfully, returning {len(results)} results")
            return jsonify({
                'status': 'success',
                'results': results,
                'columns': columns,  # Include the columns in order
                'count': len(results),
                'total_count': total_count,
                'page': page,
                'page_size': page_size,
                'total_pages': max(1, (total_count + page_size - 1) // page_size),
                'execution_time': execution_time,
                'query': sql_script
            })
        finally:
            db_conn.close()
            
    except Exception as e:
        logger.error(f"Error executing query: {e}")
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn:
            conn.close()

def update_parameter_defaults(query_id, parameters):
    """Update default values for parameters based on execution values."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get current parameters
        cursor.execute("SELECT parameters FROM queries WHERE id = ?", (query_id,))
        query = cursor.fetchone()
        
        if query and query['parameters']:
            current_params = json.loads(query['parameters'])
            
            # Update default values
            for param in current_params:
                if param['name'] in parameters:
                    param['default'] = parameters[param['name']]
            
            # Save updated parameters
            cursor.execute(
                "UPDATE queries SET parameters = ? WHERE id = ?",
                (json.dumps(current_params), query_id)
            )
            conn.commit()
    except Exception as e:
        print(f"Error updating parameter defaults: {e}")
    finally:
        if conn:
            conn.close()

@queries_bp.route('/queries/list_tables', methods=['POST'])
def list_tables():
    """List all tables in the selected database with detailed metadata."""
    data = request.get_json()
    connection_name = data.get('connection_name')
    
    if not connection_name:
        return jsonify({'status': 'error', 'message': 'Connection name is required'}), 400
        
    try:
        # Get connection details from the session or database
        connection_info = get_db_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'status': 'error', 'message': f'Connection {connection_name} not found'}), 404
            
        # Connect to the database
        db_conn = connect_to_database(connection_info)
        if not db_conn:
            return jsonify({'status': 'error', 'message': 'Failed to connect to database'}), 500
            
        cursor = db_conn.cursor(dictionary=True)
        
        # Determine database type
        db_type = connection_info.get('db_type', 'mysql').lower()
        
        # Query to get table information based on database type
        if db_type in ['mysql', 'mariadb']:
            # For MySQL/MariaDB
            try:
                # Get table information from information_schema
                query = """
                    SELECT 
                        t.TABLE_SCHEMA as table_schema,
                        t.TABLE_NAME as table_name, 
                        t.TABLE_TYPE as table_type,
                        t.ENGINE as engine,
                        t.TABLE_ROWS as table_rows,
                        t.DATA_LENGTH as data_length,
                        t.CREATE_TIME as create_time,
                        t.UPDATE_TIME as update_time,
                        t.TABLE_COMMENT as table_comment
                    FROM 
                        information_schema.TABLES t
                    WHERE 
                        t.TABLE_SCHEMA = %s
                    ORDER BY 
                        t.TABLE_NAME
                """
                cursor.execute(query, (connection_info.get('database'),))
                tables = cursor.fetchall()
                
                # Convert datetime objects to strings for JSON serialization
                for table in tables:
                    if table.get('create_time'):
                        table['create_time'] = table['create_time'].strftime('%Y-%m-%d %H:%M:%S')
                    if table.get('update_time'):
                        table['update_time'] = table['update_time'].strftime('%Y-%m-%d %H:%M:%S')
                    
                    # IMPORTANT: Ensure all tables have the same schema name to avoid duplicates
                    table['table_schema'] = connection_info.get('database')
            except Exception as e:
                # Fallback to simpler approach if the detailed query fails
                cursor.execute("SHOW TABLES")
                table_names = [row[f"Tables_in_{connection_info['database']}"] for row in cursor.fetchall()]
                
                tables = []
                for table_name in table_names:
                    tables.append({
                        'table_schema': connection_info['database'],
                        'table_name': table_name,
                        'table_type': 'BASE TABLE',
                        'engine': 'InnoDB',
                        'table_rows': 0,
                        'data_length': 0,
                        'create_time': '',
                        'update_time': '',
                        'table_comment': ''
                    })
        
        # For SQLite
        elif db_type == 'sqlite':
            cursor.execute("SELECT name as table_name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            
            # Add additional metadata for SQLite tables
            for table in tables:
                table['table_schema'] = 'main'
                table['table_type'] = 'BASE TABLE'
                table['engine'] = 'SQLite'
                table['data_length'] = 0
                table['create_time'] = ''
                table['update_time'] = ''
                table['table_comment'] = ''
                
                # Try to get row count
                try:
                    count_cursor = db_conn.cursor()
                    count_cursor.execute(f"SELECT COUNT(*) as count FROM {table['table_name']}")
                    count_result = count_cursor.fetchone()
                    table['table_rows'] = count_result['count'] if count_result else 0
                except Exception:
                    table['table_rows'] = 0
        
        # Default for other database types
        else:
            # Simple fallback that should work for most databases
            cursor.execute("SHOW TABLES")
            tables = []
            for row in cursor.fetchall():
                table_name = list(row.values())[0]  # Get first column value
                tables.append({
                    'table_schema': connection_info['database'],
                    'table_name': table_name,
                    'table_type': 'BASE TABLE',
                    'engine': db_type,
                    'table_rows': 0,
                    'data_length': 0,
                    'create_time': '',
                    'update_time': '',
                    'table_comment': ''
                })
        
        cursor.close()
        db_conn.close()
        
        return jsonify({
            'status': 'success',
            'tables': tables
        })
        
    except Exception as e:
        print(f"Error listing tables: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@queries_bp.route('/queries/table_data', methods=['POST'])
def get_table_data():
    """Get data from a specific table."""
    data = request.get_json()
    connection_name = data.get('connection_name')
    table_name = data.get('table_name')
    page = int(data.get('page', 1))
    page_size = int(data.get('page_size', 100))
    
    if not connection_name or not table_name:
        return jsonify({'status': 'error', 'message': 'Connection name and table name are required'}), 400
    
    try:
        # Get connection info
        connection_info = get_db_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'status': 'error', 'message': 'Connection not found'}), 404
        
        # Connect to the database
        db_conn = connect_to_database(connection_info)
        if not db_conn:
            return jsonify({'status': 'error', 'message': 'Failed to connect to database'}), 500
        
        try:
            cursor = db_conn.cursor(dictionary=True)
            
            # Get total count
            cursor.execute(f"SELECT COUNT(*) as total FROM `{table_name}`")
            total = cursor.fetchone()['total']
            
            # Get data with pagination
            offset = (page - 1) * page_size
            cursor.execute(f"SELECT * FROM `{table_name}` LIMIT %s OFFSET %s", (page_size, offset))
            results = cursor.fetchall()
            
            # Get column names
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
            columns = [column['Field'] for column in cursor.fetchall()]
            
            return jsonify({
                'status': 'success',
                'results': results,
                'columns': columns,
                'total': total,
                'page': page,
                'page_size': page_size,
                'total_pages': (total + page_size - 1) // page_size
            })
        finally:
            db_conn.close()
            
    except Exception as e:
        print(f"Error getting table data: {e}")
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@queries_bp.route('/queries/new')
def new_query():
    """Render the create new query page."""
    # Get saved connection profiles
    profiles = load_profiles()
    
    return render_template(
        'rna/rna_query_edit.html',
        saved_profiles=profiles['profiles'],
        query=None,
        mode='new'
    )

@queries_bp.route('/queries/edit/<int:query_id>')
def edit_query(query_id):
    """Render the edit query page."""
    # Get saved connection profiles
    profiles = load_profiles()
    
    # Get query details
    conn = None
    query = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, sql_script, description, created_at, updated_at, 
                   connection_name, parameters, created_by
            FROM queries
            WHERE id = ?
        """, (query_id,))
        query = cursor.fetchone()
        
        if not query:
            flash('Query not found', 'error')
            return redirect(url_for('rna_qry.queries_page'))
        
        query = dict(query)
        
        # If parameters are stored as JSON string, parse them
        if query['parameters']:
            try:
                query['parameters'] = json.loads(query['parameters'])
            except:
                query['parameters'] = []
        else:
            query['parameters'] = []
            
    except Exception as e:
        print(f"Error getting query: {e}")
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('rna_qry.queries_page'))
    finally:
        if conn:
            conn.close()
    
    return render_template(
        'rna/rna_query_edit.html',
        saved_profiles=profiles['profiles'],
        query=query,
        mode='edit'
    )

@queries_bp.route('/set_connection', methods=['POST'])
def set_connection():
    """Set the current connection for queries."""
    data = request.get_json()
    connection_name = data.get('connection_name')
    
    if connection_name:
        session['selected_qry_connection'] = connection_name
        return jsonify({'status': 'success'})
    else:
        return jsonify({'status': 'error', 'message': 'Connection name is required'}), 400

# Add this new function to maintain a cache of table structure
def ensure_table_structure_cache():
    """Ensure the table_structure_cache table exists in myapp_db.db"""
    try:
        logger.debug("Creating table_structure_cache table if it doesn't exist")
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Create the table structure cache table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS table_structure_cache (
                db_name TEXT,
                table_name TEXT,
                column_order TEXT,
                last_updated TIMESTAMP,
                PRIMARY KEY (db_name, table_name)
            )
        ''')
        conn.commit()
        conn.close()
        logger.debug("Table structure cache table created/verified successfully")
    except Exception as e:
        logger.error(f"Error ensuring table structure cache: {e}")
        traceback.print_exc()

# Add this function to get or update table column order
def get_or_update_table_structure(db_conn, db_name, table_name):
    """Get column order from cache or update from database if needed"""
    try:
        logger.debug(f"Getting column order for {db_name}.{table_name}")
        # First try to get from cache
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT column_order, last_updated FROM table_structure_cache WHERE db_name = ? AND table_name = ?", 
            (db_name, table_name)
        )
        result = cursor.fetchone()
        
        current_time = datetime.now()
        cache_max_age = 24 * 60 * 60  # Cache for 24 hours
        
        if result and (current_time - datetime.fromisoformat(result[1])).total_seconds() < cache_max_age:
            # Use cached column order if it's not too old
            columns = json.loads(result[0])
            logger.debug(f"Using cached column order for {db_name}.{table_name}: {columns}")
            return columns
        
        logger.debug(f"Cache miss or expired for {db_name}.{table_name}, fetching from database")
        # Need to fetch from the database
        columns = []
        info_cursor = db_conn.cursor(dictionary=True)
        
        try:
            # MySQL/MariaDB query for column order
            query = """
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_NAME = %s AND TABLE_SCHEMA = %s
                ORDER BY ORDINAL_POSITION
            """
            
            # Get current database name if not provided
            if not db_name:
                info_cursor.execute("SELECT DATABASE()")
                db_row = info_cursor.fetchone()
                if db_row:
                    db_name = db_row.get('DATABASE()') or ''
                    logger.debug(f"Got current database name: {db_name}")
            
            logger.debug(f"Executing INFORMATION_SCHEMA query with params: table={table_name}, db={db_name}")
            info_cursor.execute(query, (table_name, db_name))
            info_rows = info_cursor.fetchall()
            
            if info_rows:
                columns = [row['COLUMN_NAME'] for row in info_rows]
                logger.debug(f"Retrieved {len(columns)} columns from INFORMATION_SCHEMA")
                
                # Update the cache
                cursor.execute(
                    "INSERT OR REPLACE INTO table_structure_cache VALUES (?, ?, ?, ?)",
                    (db_name, table_name, json.dumps(columns), current_time.isoformat())
                )
                conn.commit()
                logger.debug(f"Updated column cache for {db_name}.{table_name}: {columns}")
            else:
                logger.warning(f"No columns found in INFORMATION_SCHEMA for {db_name}.{table_name}")
            
        except Exception as e:
            logger.error(f"Error fetching column order from database: {e}")
            traceback.print_exc()
        finally:
            info_cursor.close()
            
        return columns
    except Exception as e:
        logger.error(f"Error accessing table structure cache: {e}")
        traceback.print_exc()
        return []
    finally:
        if conn:
            conn.close()

@queries_bp.route('/check_table_structure', methods=['GET'])
def check_table_structure():
    """API endpoint to test the table structure cache functionality"""
    table_name = request.args.get('table')
    db_name = request.args.get('db')
    connection_name = request.args.get('connection')
    
    if not table_name or not connection_name:
        return jsonify({
            'status': 'error',
            'message': 'Table name and connection name are required'
        }), 400
    
    try:
        # Ensure cache table exists
        ensure_table_structure_cache()
        
        # Get database connection info
        connection_info = get_db_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'status': 'error', 'message': 'Connection not found'}), 404
        
        # Connect to the database
        db_conn = connect_to_database(connection_info)
        if not db_conn:
            return jsonify({'status': 'error', 'message': 'Failed to connect to database'}), 500
        
        try:
            # If no database name provided, get the current one
            if not db_name:
                info_cursor = db_conn.cursor(dictionary=True)
                info_cursor.execute("SELECT DATABASE()")
                current_db = info_cursor.fetchone()
                if current_db and current_db.get('DATABASE()'):
                    db_name = current_db.get('DATABASE()')
                info_cursor.close()
            
            # Get column structure
            columns = get_or_update_table_structure(db_conn, db_name, table_name)
            
            # Check if we have cached structure
            conn = sqlite3.connect(DATABASE_FILE)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT column_order, last_updated FROM table_structure_cache WHERE db_name = ? AND table_name = ?", 
                (db_name, table_name)
            )
            cache_info = cursor.fetchone()
            
            # Execute a simple SELECT * to get actual columns from the database
            test_cursor = db_conn.cursor(dictionary=True)
            test_cursor.execute(f"SELECT * FROM {table_name} LIMIT 0")
            actual_columns = [column[0] for column in test_cursor.description] if test_cursor.description else []
            
            return jsonify({
                'status': 'success',
                'cached_columns': columns,
                'cache_exists': cache_info is not None,
                'cache_last_updated': cache_info[1] if cache_info else None,
                'actual_columns': actual_columns,
                'db_name': db_name,
                'table_name': table_name
            })
        finally:
            db_conn.close()
            
    except Exception as e:
        logger.error(f"Error checking table structure: {e}")
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@queries_bp.route('/test_column_order', methods=['GET'])
def test_column_order():
    """Test endpoint for column ordering with SELECT * queries"""
    table_name = request.args.get('table')
    connection_name = request.args.get('connection')
    
    if not table_name or not connection_name:
        return jsonify({
            'status': 'error',
            'message': 'Table name and connection name are required'
        }), 400
    
    try:
        # Get database connection info
        connection_info = get_db_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'status': 'error', 'message': 'Connection not found'}), 404
        
        # Connect to the database
        db_conn = connect_to_database(connection_info)
        if not db_conn:
            return jsonify({'status': 'error', 'message': 'Failed to connect to database'}), 500
        
        try:
            # Get current database
            schema_name = None
            try:
                info_cursor = db_conn.cursor(dictionary=True)
                info_cursor.execute("SELECT DATABASE()")
                current_db = info_cursor.fetchone()
                if current_db and current_db.get('DATABASE()'):
                    schema_name = current_db.get('DATABASE()')
                    logger.debug(f"Using current database: {schema_name}")
                info_cursor.close()
            except Exception as e:
                logger.error(f"Error getting current database: {e}")
            
            # Get table structure directly from INFORMATION_SCHEMA
            info_cursor = db_conn.cursor(dictionary=True)
            table_columns = []
            try:
                query = """
                    SELECT COLUMN_NAME 
                    FROM INFORMATION_SCHEMA.COLUMNS 
                    WHERE TABLE_NAME = %s AND TABLE_SCHEMA = %s
                    ORDER BY ORDINAL_POSITION
                """
                logger.debug(f"Executing INFORMATION_SCHEMA query with params: table={table_name}, db={schema_name}")
                info_cursor.execute(query, (table_name, schema_name))
                info_rows = info_cursor.fetchall()
                
                if info_rows:
                    table_columns = [row['COLUMN_NAME'] for row in info_rows]
                    logger.debug(f"Retrieved {len(table_columns)} columns from INFORMATION_SCHEMA: {table_columns}")
            except Exception as e:
                logger.error(f"Error fetching column order from INFORMATION_SCHEMA: {e}")
            
            info_cursor.close()
            
            # Execute a SELECT * query
            cursor = db_conn.cursor(dictionary=True)
            sql = f"SELECT * FROM {table_name} LIMIT 5"
            cursor.execute(sql)
            
            # Get original column order from cursor
            original_columns = [column[0] for column in cursor.description] if cursor.description else []
            logger.debug(f"Original columns from cursor: {original_columns}")
            
            # Create mapping for reordering
            column_positions = {col: idx for idx, col in enumerate(table_columns)}
            
            # Ensure all columns from cursor are in our mapping
            for col in original_columns:
                if col not in column_positions:
                    column_positions[col] = 999
            
            # Sort columns based on positions
            ordered_columns = sorted(original_columns, key=lambda col: column_positions.get(col, 999))
            logger.debug(f"Reordered columns: {ordered_columns}")
            
            # Fetch results
            results = cursor.fetchall()
            
            # Reorder result columns
            ordered_results = []
            for row in results:
                ordered_row = {}
                for col in ordered_columns:
                    ordered_row[col] = row.get(col)
                ordered_results.append(ordered_row)
            
            cursor.close()
            
            # Return everything for debugging
            return jsonify({
                'status': 'success',
                'table_name': table_name,
                'schema_name': schema_name,
                'table_structure_columns': table_columns,
                'original_query_columns': original_columns,
                'reordered_columns': ordered_columns,
                'results': ordered_results,
                'column_match': ordered_columns == table_columns
            })
        
        finally:
            db_conn.close()
    
    except Exception as e:
        logger.error(f"Error in test_column_order: {e}")
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@queries_bp.route('/fetch_redmine_info/<ticket_number>', methods=['GET'])
def fetch_redmine_info(ticket_number):
    """
    Fetch information from a Redmine ticket.
    
    Args:
        ticket_number: The Redmine ticket number to fetch information for.
        
    Returns:
        JSON response with bug number, title, and description.
    """
    try:
        # Construct the Redmine URL
        redmine_url = f"{REDMINE_URL}/issues/{ticket_number}"
        logging.info(f"Fetching Redmine information from URL: {redmine_url}")
        
        # Set up session for handling cookies and authentication
        session = requests.Session()
        
        # Use credentials from config
        username = REDMINE_USERNAME
        password = REDMINE_PASSWORD
        
        # First, get the login page to retrieve the authenticity token
        login_page = session.get(f"{REDMINE_URL}/login", timeout=10)
        login_soup = BeautifulSoup(login_page.text, 'html.parser')
        
        # Find the authenticity token
        auth_token = login_soup.select_one('input[name="authenticity_token"]')
        if auth_token:
            auth_token = auth_token.get('value')
        
        # Prepare login data
        login_data = {
            'username': username,
            'password': password,
            'login': 'Login',
            'authenticity_token': auth_token
        }
        
        # Perform login
        login_response = session.post(f"{REDMINE_URL}/login", data=login_data, timeout=10)
        
        # Check if login was successful by looking for login form in the response
        login_check_soup = BeautifulSoup(login_response.text, 'html.parser')
        login_form = login_check_soup.select_one('form#login-form')
        
        if login_form:
            # Login form is still present, which means login failed
            error_message = login_check_soup.select_one('#flash_error')
            error_text = error_message.text.strip() if error_message else "Invalid credentials"
            return jsonify({
                'success': False,
                'error': f"Redmine login failed: {error_text}"
            }), 401
        
        # Now fetch the issue page using the authenticated session
        response = session.get(redmine_url, timeout=10)
        
        # Check if the request was successful
        if response.status_code != 200:
            return jsonify({
                'success': False,
                'error': f"Failed to fetch Redmine information. Status code: {response.status_code}"
            }), 400
            
        # Check if we were redirected to the login page
        if '/login' in response.url:
            return jsonify({
                'success': False,
                'error': "Session expired or authentication failed. Please check your Redmine credentials."
            }), 401
            
        # Parse the HTML content
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Check if the issue exists
        error_message = soup.select_one('#errorExplanation')
        if error_message and "not found" in error_message.text:
            return jsonify({
                'success': False,
                'error': f"Issue #{ticket_number} not found."
            }), 404
        
        # Extract information using the provided XPath equivalents
        bug_no_element = soup.select_one('#content > h2')
        bug_title_element = soup.select_one('#content > div:nth-child(2) > div:nth-child(3) > div > h3')
        
        # Coba ekstrak deskripsi bug dengan berbagai selector
        bug_description_element = None
        
        # Coba cari di elemen .description yang merupakan kontainer deskripsi di Redmine
        description_container = soup.select_one('.description')
        
        # Coba juga dengan selector yang lebih spesifik untuk deskripsi Redmine
        issue_description = soup.select_one('#issue_description')
        
        # Coba dengan selector alternatif
        alt_description = soup.select_one('.wiki')
        
        # Coba dengan selector yang sangat spesifik untuk Redmine
        issue_content = soup.select_one('.issue .attributes')
        description_div = soup.select_one('.issue .description .wiki')
        
        # Simpan semua container yang ditemukan
        description_containers = []
        if description_container:
            description_containers.append(description_container)
        if issue_description:
            description_containers.append(issue_description)
        if alt_description:
            description_containers.append(alt_description)
        if description_div:
            description_containers.append(description_div)
        
        # Fallback selectors if the primary ones don't work
        if not bug_no_element:
            bug_no_element = soup.select_one('h2')
            
        if not bug_title_element:
            # Try to find any h3 element in the content area
            bug_title_element = soup.select_one('#content h3')
            
        if not bug_description_element:
            # Try to find any strong element in a paragraph in the content area
            bug_description_element = soup.select_one('#content p strong')
            # If still not found, try to find the first paragraph in the description area
            if not bug_description_element:
                bug_description_element = soup.select_one('#content .description p')
        
        # Extract text from elements if they exist
        bug_no = bug_no_element.text.strip() if bug_no_element else f"Issue #{ticket_number}"
        bug_title = bug_title_element.text.strip() if bug_title_element else ""
        
        # Ekstrak deskripsi bug dengan cara yang lebih komprehensif
        bug_description = ""
        
        # Coba ekstrak dari semua container yang ditemukan
        for container in description_containers:
            # Coba metode 1: Ambil semua paragraf
            paragraphs = container.find_all('p')
            if paragraphs:
                # Gabungkan semua paragraf dengan pemisah baris baru
                extracted_text = "\n\n".join([p.text.strip() for p in paragraphs])
                if extracted_text and len(extracted_text) > len(bug_description):
                    bug_description = extracted_text
                    continue
            
            # Coba metode 2: Ambil semua teks dari container
            extracted_text = container.text.strip()
            if extracted_text and len(extracted_text) > len(bug_description):
                bug_description = extracted_text
        
        # Jika masih tidak ada deskripsi, coba ekstrak dari seluruh halaman
        if not bug_description:
            # Coba cari elemen dengan id 'content'
            content = soup.select_one('#content')
            if content:
                # Cari semua elemen dengan class 'wiki'
                wiki_elements = content.select('.wiki')
                for wiki in wiki_elements:
                    extracted_text = wiki.text.strip()
                    if extracted_text and len(extracted_text) > len(bug_description):
                        bug_description = extracted_text
        
        # Log the extracted information for debugging
        logging.info(f"Extracted bug_no: {bug_no}")
        logging.info(f"Extracted bug_title: {bug_title}")
        logging.info(f"Extracted bug_description length: {len(bug_description)}")
        logging.info(f"Extracted bug_description: {bug_description[:100]}..." if bug_description else "No description found")
        
        # Debug untuk melihat struktur HTML
        if not bug_description:
            logging.info("Description container found: %s", bool(description_container))
            if description_container:
                logging.info("Description container HTML: %s", description_container.prettify()[:500])
        
        # Ekstrak deskripsi langsung dari HTML jika masih kosong
        if not bug_description:
            # Coba cari langsung dengan selector yang sangat spesifik
            description_divs = soup.select('.issue .description')
            for div in description_divs:
                # Cari elemen dengan class 'wiki'
                wiki = div.select_one('.wiki')
                if wiki:
                    bug_description = wiki.get_text(separator='\n\n', strip=True)
                    if bug_description:
                        logging.info("Found description using direct selector")
                        break
        
        # Jika masih kosong, coba metode ekstraksi yang lebih agresif
        if not bug_description:
            # Cari semua elemen div dengan class 'wiki'
            all_wiki_divs = soup.select('div.wiki')
            for wiki_div in all_wiki_divs:
                text = wiki_div.get_text(separator='\n\n', strip=True)
                if text and len(text) > len(bug_description):
                    bug_description = text
                    logging.info("Found description using aggressive selector")
        
        # Jika masih kosong, coba mencari elemen dengan atribut data-description
        if not bug_description:
            description_elements = soup.select('[data-description]')
            for elem in description_elements:
                text = elem.get('data-description', '')
                if text and len(text) > len(bug_description):
                    bug_description = text
                    logging.info("Found description using data-description attribute")
        
        # Coba cari elemen dengan ID tertentu yang mungkin berisi deskripsi
        if not bug_description:
            # Berdasarkan contoh yang diberikan, ini adalah selector yang paling tepat
            issue_description_wiki = soup.select_one('#issue_description_wiki.wiki')
            logging.info(f"Selector '#issue_description_wiki.wiki' found: {issue_description_wiki is not None}")
            
            if issue_description_wiki:
                text = issue_description_wiki.get_text(separator='\n\n', strip=True)
                if text:
                    bug_description = text
                    logging.info("Found description using exact selector: #issue_description_wiki.wiki")
                    logging.info(f"Description content: {text[:100]}...")
            
            # Fallback ke selector lain jika yang spesifik tidak ditemukan
            if not bug_description:
                # Coba dengan selector alternatif untuk elemen yang sama
                issue_description_alt = soup.select_one('div#issue_description_wiki')
                if issue_description_alt:
                    text = issue_description_alt.get_text(separator='\n\n', strip=True)
                    if text:
                        bug_description = text
                        logging.info("Found description using alternative selector: div#issue_description_wiki")
            
            # Jika masih tidak ada, coba dengan ID lainnya
            if not bug_description:
                possible_ids = ['description', 'issue-description', 'issue_description', 'content-description', 'issue_description_wiki']
                for id_name in possible_ids:
                    elem = soup.select_one(f'#{id_name}')
                    if elem:
                        text = elem.get_text(separator='\n\n', strip=True)
                        if text and len(text) > len(bug_description):
                            bug_description = text
                            logging.info(f"Found description using ID selector: #{id_name}")
        
        # Coba ekstrak secara manual dengan mencari pola HTML yang spesifik
        if not bug_description:
            # Cari pola HTML untuk div dengan ID issue_description_wiki
            pattern = r'<div id="issue_description_wiki" class="wiki">(.*?)</div>'
            match = re.search(pattern, response.text, re.DOTALL)
            if match:
                # Extract the content
                html_content = match.group(1)
                # Parse the HTML content
                soup_content = BeautifulSoup(html_content, 'html.parser')
                # Get text
                text = soup_content.get_text(separator='\n\n', strip=True)
                if text:
                    bug_description = text
                    logging.info("Found description using regex pattern extraction")
        
        # Jika masih kosong, simpan HTML lengkap untuk analisis
        if not bug_description:
            # Simpan HTML lengkap untuk analisis
            with open('redmine_response.html', 'w', encoding='utf-8') as f:
                f.write(response.text)
            logging.info("Saved complete HTML response to redmine_response.html for analysis")
        
        # Jika bug_description masih kosong, berikan pesan default
        if not bug_description:
            bug_description = "Deskripsi tidak dapat diekstrak. Silakan isi secara manual."
            logging.warning("Failed to extract bug description")
        
        # Return the extracted information
        return jsonify({
            'success': True,
            'bug_no': bug_no,
            'bug_title': bug_title,
            'bug_description': bug_description
        })
    except Exception as e:
        # Log the error
        logging.error(f"Error fetching Redmine information: {str(e)}")
        logging.error(traceback.format_exc())
        
        # Return an error response
        return jsonify({
            'success': False,
            'error': f"Error fetching Redmine information: {str(e)}"
        }), 500 