from flask import Blueprint, request, jsonify
import sqlite3
import os
import json
import pymysql
from datetime import datetime
import pymysql.cursors
from google import genai
from google.genai import types
import re

queries_bp = Blueprint('queries', __name__)

def get_db_connection():
    """Get SQLite database connection"""
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'instance', 'myapp_db.db')
    return sqlite3.connect(db_path)

def load_profiles():
    """Load database profiles from SQLite"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, host, port, user, password, database FROM connection")
    profiles = []
    for row in cursor.fetchall():
        profiles.append({
            'name': row[0],
            'host': row[1],
            'port': row[2],
            'user': row[3],
            'password': row[4],
            'database': row[5]
        })
    conn.close()
    return profiles

def get_mysql_connection(profile_id='LOKAL'):
    """Get MySQL database connection using profile"""
    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    
    if not profile:
        raise Exception(f'Profile {profile_id} not found')
    
    return pymysql.connect(
        host=profile['host'],
        port=int(profile['port']) if profile.get('port') else 3306,
        user=profile['user'],
        password=profile['password'],
        database=profile['database'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def get_database_schema(profile_id='LOKAL'):
    """Get database schema (tables and columns) for AI context"""
    try:
        mysql_conn = get_mysql_connection(profile_id)
        mysql_cursor = mysql_conn.cursor()
        
        # Get all tables
        mysql_cursor.execute("SHOW TABLES")
        tables = mysql_cursor.fetchall()
        
        schema_info = {}
        
        for table in tables:
            table_name = list(table.values())[0] if isinstance(table, dict) else table[0]
            
            # Get table structure
            mysql_cursor.execute(f"DESCRIBE {table_name}")
            columns = mysql_cursor.fetchall()
            
            schema_info[table_name] = {
                'columns': columns
            }
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return schema_info
        
    except Exception as e:
        raise Exception(f"Failed to get database schema: {str(e)}")

def generate_sql_with_ai(user_request, profile_id='LOKAL'):
    """Generate SQL query using Google GenAI with structured output"""
    try:
        # Get database schema
        schema_info = get_database_schema(profile_id)
        
        # Format schema for AI prompt (optimized for token efficiency)
        schema_text = "DB Schema:\n"
        for table_name, table_info in schema_info.items():
            schema_text += f"\n{table_name}:\n"
            for col in table_info['columns']:
                col_dict = dict(col) if isinstance(col, dict) else {
                    'Field': col[0], 'Type': col[1], 'Null': col[2], 
                    'Key': col[3], 'Default': col[4], 'Extra': col[5]
                }
                schema_text += f"  {col_dict['Field']}({col_dict['Type']})"
                if col_dict['Key'] == 'PRI':
                    schema_text += "[PK]"
                if col_dict['Null'] == 'NO':
                    schema_text += "[NN]"
                schema_text += "\n"
        
        # Create optimized AI prompt
        prompt = f"""Generate SQL for: {user_request}

Schema:
{schema_text}

Rules:
- Use proper SQL syntax
- Include WHERE if filtering needed
- Use JOINs for multiple tables
- Add LIMIT if requested
- Use ORDER BY if sorting mentioned
- Only use existing tables/columns

Return JSON: {{"Message": "short brief explanation", "Query": "SQL query"}}"""
        
        # Configure Google GenAI client
        client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
        )
        
        # Configure structured output
        generate_content_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=types.Schema(
                type=types.Type.OBJECT,
                required=["Message", "Query"],
                properties={
                    "Message": types.Schema(
                        type=types.Type.STRING,
                    ),
                    "Query": types.Schema(
                        type=types.Type.STRING,
                    ),
                },
            ),
        )
        
        # Generate content
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=prompt),
                ],
            ),
        ]
        
        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=contents,
            config=generate_content_config,
        )
        
        # Parse JSON response
        try:
            result = json.loads(response.text)
            return result
        except json.JSONDecodeError:
            # If AI didn't return valid JSON, try to extract SQL from response
            response_text = response.text.strip()
            
            # Try to find SQL query in the response
            if 'SELECT' in response_text.upper():
                # Extract SQL query (everything after SELECT)
                sql_start = response_text.upper().find('SELECT')
                sql_query = response_text[sql_start:]
                
                return {
                    "Message": "AI generated SQL query (parsed from response)",
                    "Query": sql_query
                }
            else:
                raise Exception("AI response could not be parsed as JSON and no SQL query found")
        
    except Exception as e:
        raise Exception(f"AI query generation failed: {str(e)}")

@queries_bp.route('', methods=['GET'])
def get_queries():
    """Get all saved queries (primary route without trailing slash)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, sql_script, description, created_at, updated_at, parameters
            FROM queries
            ORDER BY name
        ''')
        rows = cursor.fetchall()
        conn.close()
        
        queries = []
        for row in rows:
            queries.append({
                'id': row[0],
                'name': row[1],
                'sql_script': row[2],
                'description': row[3],
                'created_at': row[4],
                'updated_at': row[5],
                'parameters': json.loads(row[6]) if row[6] else []
            })
        
        return jsonify({
            'success': True,
            'queries': queries
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@queries_bp.route('/<int:query_id>', methods=['GET'])
def get_query(query_id):
    """Get specific query by ID"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, sql_script, description, created_at, updated_at, parameters
            FROM queries
            WHERE id = ?
        ''', (query_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return jsonify({
                'success': False,
                'error': f'Query with ID {query_id} not found'
            }), 404
        
        return jsonify({
            'success': True,
            'query': {
                'id': row[0],
                'name': row[1],
                'sql_script': row[2],
                'description': row[3],
                'created_at': row[4],
                'updated_at': row[5],
                'parameters': json.loads(row[6]) if row[6] else []
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@queries_bp.route('/', methods=['POST'])
def create_query():
    """Create a new saved query"""
    try:
        data = request.get_json()
        
        if not data or 'name' not in data or 'sql_script' not in data:
            return jsonify({
                'success': False,
                'error': 'Name and SQL script are required'
            }), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO queries (name, sql_script, description, parameters, created_at, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
        ''', (
            data['name'],
            data['sql_script'],
            data.get('description', ''),
            json.dumps(data.get('parameters', []))
        ))
        
        query_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'query_id': query_id,
            'message': 'Query created successfully'
        }), 201
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@queries_bp.route('/<int:query_id>', methods=['PUT'])
def update_query(query_id):
    """Update an existing saved query"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body is required'
            }), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if query exists
        cursor.execute('SELECT id FROM queries WHERE id = ?', (query_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': f'Query with ID {query_id} not found'
            }), 404
        
        # Update query
        cursor.execute('''
            UPDATE queries 
            SET name = ?, sql_script = ?, description = ?, parameters = ?, updated_at = datetime('now')
            WHERE id = ?
        ''', (
            data.get('name', ''),
            data.get('sql_script', ''),
            data.get('description', ''),
            json.dumps(data.get('parameters', [])),
            query_id
        ))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Query updated successfully'
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@queries_bp.route('/<int:query_id>', methods=['DELETE'])
def delete_query(query_id):
    """Delete a saved query"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if query exists
        cursor.execute('SELECT id FROM queries WHERE id = ?', (query_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                'success': False,
                'error': f'Query with ID {query_id} not found'
            }), 404
        
        # Delete query
        cursor.execute('DELETE FROM queries WHERE id = ?', (query_id,))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Query deleted successfully'
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@queries_bp.route('/<int:query_id>/execute', methods=['POST'])
def execute_query(query_id):
    """Execute a saved query and return results"""
    try:
        data = request.get_json() or {}
        parameters = data.get('parameters', {})
        profile_id = data.get('profileId', 'LOKAL')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get query details
        cursor.execute('''
            SELECT sql_script, parameters, name
            FROM queries
            WHERE id = ?
        ''', (query_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return jsonify({
                'success': False,
                'error': f'Query with ID {query_id} not found',
                'query_id': query_id,
                'executed_sql': None,
                'parameters_used': [],
                'profile_id': profile_id
            }), 404
        
        sql_script = row[0]
        query_parameters = json.loads(row[1]) if row[1] else []
        query_name = row[2]
        
        # Execute query against MySQL database
        try:
            mysql_conn = get_mysql_connection(profile_id)
            mysql_cursor = mysql_conn.cursor(pymysql.cursors.DictCursor)
            
            # Replace parameter placeholders with actual values
            formatted_sql = sql_script
            param_values = []
            
            for param in query_parameters:
                param_name = param.get('name')
                param_value = parameters.get(param_name, param.get('default', ''))
                
                # Replace ? placeholders with actual values
                if '?' in formatted_sql:
                    formatted_sql = formatted_sql.replace('?', '%s', 1)
                    param_values.append(param_value)
            
            # Execute query (avoid Python %% formatting when no params)
            if param_values:
                mysql_cursor.execute(formatted_sql, param_values)
            else:
                mysql_cursor.execute(formatted_sql)
            
            # Get column names
            columns = [desc[0] for desc in mysql_cursor.description] if mysql_cursor.description else []
            
            # Get results
            results = mysql_cursor.fetchall()
            
            # Convert results to list of dictionaries
            data_rows = []
            for row in results:
                data_rows.append(dict(row))
            
            mysql_cursor.close()
            mysql_conn.close()
            
            return jsonify({
                'success': True,
                'query_id': query_id,
                'query_name': query_name,
                'columns': columns,
                'data': data_rows,
                'row_count': len(data_rows),
                'executed_sql': formatted_sql,
                'parameters_used': param_values,
                'profile_id': profile_id
            }), 200
            
        except pymysql.err.OperationalError as mysql_error:
            error_code = mysql_error.args[0] if mysql_error.args else 'Unknown'
            error_msg = mysql_error.args[1] if len(mysql_error.args) > 1 else str(mysql_error)
            
            error_details = {
                'error_type': 'MySQL Operational Error',
                'error_code': error_code,
                'error_message': error_msg,
                'possible_causes': []
            }
            
            # Provide specific guidance based on error codes
            if error_code == 1146:
                error_details['possible_causes'] = ['Table does not exist', 'Check table name spelling', 'Verify database schema']
            elif error_code == 1054:
                error_details['possible_causes'] = ['Column does not exist', 'Check column name spelling', 'Verify column exists in table']
            elif error_code == 1064:
                error_details['possible_causes'] = ['SQL syntax error', 'Check query syntax', 'Verify SQL statement']
            elif error_code == 1045:
                error_details['possible_causes'] = ['Access denied', 'Check database credentials', 'Verify user permissions']
            elif error_code == 2003:
                error_details['possible_causes'] = ['Cannot connect to database', 'Check database server', 'Verify connection settings']
            else:
                error_details['possible_causes'] = ['Check query syntax', 'Verify table/column names', 'Check database permissions']
            
            return jsonify({
                'success': False,
                'error': f'Query execution failed: {error_msg}',
                'error_details': error_details,
                'query_id': query_id,
                'query_name': query_name,
                'executed_sql': formatted_sql,
                'parameters_used': param_values,
                'profile_id': profile_id
            }), 500
            
        except pymysql.err.ProgrammingError as mysql_error:
            error_msg = mysql_error.args[1] if len(mysql_error.args) > 1 else str(mysql_error)
            
            return jsonify({
                'success': False,
                'error': f'Query syntax error: {error_msg}',
                'error_details': {
                    'error_type': 'MySQL Programming Error',
                    'error_message': error_msg,
                    'possible_causes': ['Check SQL syntax', 'Verify query structure', 'Check for missing quotes or brackets']
                },
                'query_id': query_id,
                'query_name': query_name,
                'executed_sql': formatted_sql,
                'parameters_used': param_values,
                'profile_id': profile_id
            }), 500
            
        except Exception as mysql_error:
            return jsonify({
                'success': False,
                'error': f'Database error: {str(mysql_error)}',
                'error_details': {
                    'error_type': 'Database Error',
                    'error_message': str(mysql_error),
                    'possible_causes': ['Check database connection', 'Verify query syntax', 'Check database permissions']
                },
                'query_id': query_id,
                'query_name': query_name,
                'executed_sql': formatted_sql,
                'parameters_used': param_values,
                'profile_id': profile_id
            }), 500
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}',
            'error_details': {
                'error_type': 'Server Error',
                'error_message': str(e),
                'possible_causes': ['Check server logs', 'Verify API configuration', 'Contact administrator']
            },
            'query_id': query_id,
            'executed_sql': None,
            'parameters_used': [],
            'profile_id': profile_id
        }), 500

@queries_bp.route('/execute-raw', methods=['POST'])
def execute_raw_query():
    """Execute a raw SQL query (for testing/development)"""
    try:
        data = request.get_json()
        
        if not data or 'sql_script' not in data:
            return jsonify({
                'success': False,
                'error': 'SQL script is required',
                'error_details': {
                    'error_type': 'Validation Error',
                    'error_message': 'Missing required field: sql_script',
                    'possible_causes': ['Provide sql_script in request body', 'Check request format']
                }
            }), 400
        
        sql_script = data['sql_script']
        parameters = data.get('parameters', [])
        profile_id = data.get('profileId', 'LOKAL')
        
        # Determine placeholder style and counts
        has_named = bool(re.search(r"%\([^)]+\)s", sql_script))
        num_positional = len(re.findall(r"(?<!%)%s", sql_script))
        has_placeholders = has_named or num_positional > 0
        
        # Execute query against MySQL database
        try:
            mysql_conn = get_mysql_connection(profile_id)
            mysql_cursor = mysql_conn.cursor(pymysql.cursors.DictCursor)
            
            # Validate parameters vs placeholders
            if has_named:
                if not isinstance(parameters, dict):
                    return jsonify({
                        'success': False,
                        'error': 'Named placeholders detected (e.g., %(name)s) but parameters are not a dict',
                        'error_details': {
                            'error_type': 'Parameter Mismatch',
                            'error_message': 'Provide parameters as an object/dict when using named placeholders',
                            'possible_causes': ['Use %(param)s in SQL with dict parameters', 'Or switch to %s placeholders with a list']
                        },
                        'executed_sql': sql_script,
                        'parameters_used': parameters,
                        'profile_id': profile_id
                    }), 400
                # Warn for stray % not part of placeholders
                stray_percent = re.search(r"%(?!\([^)]+\)s|s)", sql_script)
                if stray_percent:
                    return jsonify({
                        'success': False,
                        'error': 'SQL contains % that are not part of placeholders; escape as %% or move % into parameter value',
                        'error_details': {
                            'error_type': 'Percent Formatting Risk',
                            'error_message': 'Found literal % while using Python-style DB placeholders',
                            'possible_causes': ['Escape % as %% when also passing parameters', "Use LIKE %s with value '%BTN%' instead of embedding % in SQL"]
                        },
                        'executed_sql': sql_script,
                        'parameters_used': parameters,
                        'profile_id': profile_id
                    }), 400
                mysql_cursor.execute(sql_script, parameters)
            else:
                # Positional placeholders or none
                if isinstance(parameters, dict):
                    # Dict passed but no named placeholders
                    return jsonify({
                        'success': False,
                        'error': 'Dict parameters provided but no named placeholders in SQL',
                        'error_details': {
                            'error_type': 'Parameter Mismatch',
                            'possible_causes': ['Use %(param)s placeholders for dict parameters', 'Or pass parameters as a list for %s']
                        },
                        'executed_sql': sql_script,
                        'parameters_used': parameters,
                        'profile_id': profile_id
                    }), 400
                # If there are placeholders, ensure count matches
                if num_positional > 0:
                    if not isinstance(parameters, (list, tuple)):
                        return jsonify({
                            'success': False,
                            'error': 'Positional placeholders (%s) detected but parameters are not a list/tuple',
                            'error_details': {
                                'error_type': 'Parameter Mismatch',
                                'possible_causes': ['Pass parameters as an array', 'Or change SQL to use named placeholders']
                            },
                            'executed_sql': sql_script,
                            'parameters_used': parameters,
                            'profile_id': profile_id
                        }), 400
                    if len(parameters) != num_positional:
                        return jsonify({
                            'success': False,
                            'error': f'Placeholder/parameter count mismatch: found {num_positional} %s placeholders but received {len(parameters)} parameters',
                            'error_details': {
                                'error_type': 'Parameter Count Mismatch',
                                'possible_causes': ['Ensure number of %s matches parameters length', "Avoid embedding % in SQL when passing parameters; use LIKE %s with '%BTN%' value"]
                            },
                            'executed_sql': sql_script,
                            'parameters_used': parameters,
                            'profile_id': profile_id
                        }), 400
                    # Detect stray % that could cause formatting errors
                    stray_percent = re.search(r"%(?!s)", sql_script)
                    if stray_percent:
                        return jsonify({
                            'success': False,
                            'error': 'SQL contains % that are not part of %s placeholders; escape as %% or move % into parameter value',
                            'error_details': {
                                'error_type': 'Percent Formatting Risk',
                                'possible_causes': ["Use LIKE %s with value '%BTN%'", 'Escape literal % as %% when passing parameters']
                            },
                            'executed_sql': sql_script,
                            'parameters_used': parameters,
                            'profile_id': profile_id
                        }), 400
                    mysql_cursor.execute(sql_script, parameters)
                else:
                    # No placeholders: never pass parameters (prevents % formatting issues)
                    mysql_cursor.execute(sql_script)
                    parameters = []
            
            # Get column names
            columns = [desc[0] for desc in mysql_cursor.description] if mysql_cursor.description else []
            
            # Get results
            results = mysql_cursor.fetchall()
            
            # Convert results to list of dictionaries
            data_rows = [dict(row) for row in results]
            
            mysql_cursor.close()
            mysql_conn.close()
            
            return jsonify({
                'success': True,
                'columns': columns,
                'data': data_rows,
                'row_count': len(data_rows),
                'executed_sql': sql_script,
                'parameters_used': parameters,
                'profile_id': profile_id
            }), 200
            
        except pymysql.err.OperationalError as mysql_error:
            error_code = mysql_error.args[0] if mysql_error.args else 'Unknown'
            error_msg = mysql_error.args[1] if len(mysql_error.args) > 1 else str(mysql_error)
            
            error_details = {
                'error_type': 'MySQL Operational Error',
                'error_code': error_code,
                'error_message': error_msg,
                'possible_causes': []
            }
            
            # Provide specific guidance based on error codes
            if error_code == 1146:
                error_details['possible_causes'] = ['Table does not exist', 'Check table name spelling', 'Verify database schema']
            elif error_code == 1054:
                error_details['possible_causes'] = ['Column does not exist', 'Check column name spelling', 'Verify column exists in table']
            elif error_code == 1064:
                error_details['possible_causes'] = ['SQL syntax error', 'Check query syntax', 'Verify SQL statement']
            elif error_code == 1045:
                error_details['possible_causes'] = ['Access denied', 'Check database credentials', 'Verify user permissions']
            elif error_code == 2003:
                error_details['possible_causes'] = ['Cannot connect to database', 'Check database server', 'Verify connection settings']
            else:
                error_details['possible_causes'] = ['Check query syntax', 'Verify table/column names', 'Check database permissions']
            
            return jsonify({
                'success': False,
                'error': f'Query execution failed: {error_msg}',
                'error_details': error_details,
                'executed_sql': sql_script,
                'parameters_used': parameters,
                'profile_id': profile_id
            }), 500
            
        except pymysql.err.ProgrammingError as mysql_error:
            error_msg = mysql_error.args[1] if len(mysql_error.args) > 1 else str(mysql_error)
            
            return jsonify({
                'success': False,
                'error': f'Query syntax error: {error_msg}',
                'error_details': {
                    'error_type': 'MySQL Programming Error',
                    'error_message': error_msg,
                    'possible_causes': ['Check SQL syntax', 'Verify query structure', 'Check for missing quotes or brackets']
                },
                'executed_sql': sql_script,
                'parameters_used': parameters,
                'profile_id': profile_id
            }), 500
            
        except Exception as mysql_error:
            return jsonify({
                'success': False,
                'error': f'Database error: {str(mysql_error)}',
                'error_details': {
                    'error_type': 'Database Error',
                    'error_message': str(mysql_error),
                    'possible_causes': ['Check database connection', 'Verify query syntax', 'Check database permissions']
                },
                'executed_sql': sql_script,
                'parameters_used': parameters,
                'profile_id': profile_id
            }), 500
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}',
            'error_details': {
                'error_type': 'Server Error',
                'error_message': str(e),
                'possible_causes': ['Check server logs', 'Verify API configuration', 'Contact administrator']
            },
            'executed_sql': None,
            'parameters_used': [],
            'profile_id': profile_id
        }), 500

@queries_bp.route('/search', methods=['GET'])
def search_queries():
    """Search saved queries by name or description"""
    try:
        search_term = request.args.get('q', '').strip()
        
        if not search_term:
            return jsonify({
                'success': False,
                'error': 'Search term is required'
            }), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, sql_script, description, created_at, updated_at, parameters
            FROM queries
            WHERE name LIKE ? OR description LIKE ?
            ORDER BY name
        ''', (f'%{search_term}%', f'%{search_term}%'))
        
        rows = cursor.fetchall()
        conn.close()
        
        queries = []
        for row in rows:
            queries.append({
                'id': row[0],
                'name': row[1],
                'sql_script': row[2],
                'description': row[3],
                'created_at': row[4],
                'updated_at': row[5],
                'parameters': json.loads(row[6]) if row[6] else []
            })
        
        return jsonify({
            'success': True,
            'queries': queries,
            'search_term': search_term,
            'count': len(queries)
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@queries_bp.route('', methods=['OPTIONS'])
def options_handler_no_slash():
    """Handle OPTIONS for root path without trailing slash"""
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200

@queries_bp.route('/', methods=['OPTIONS'])
def options_handler_root():
    """Handle OPTIONS for root path with trailing slash"""
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200

@queries_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    """Handle OPTIONS for all other paths"""
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200

@queries_bp.route('/ai', methods=['POST'])
def ai_generate_query():
    """Generate SQL query using AI based on user request"""
    try:
        data = request.get_json()
        
        if not data or 'request' not in data:
            return jsonify({
                'success': False,
                'error': 'User request is required',
                'error_details': {
                    'error_type': 'Validation Error',
                    'error_message': 'Missing required field: request',
                    'possible_causes': ['Provide request in request body', 'Check request format']
                }
            }), 400
        
        user_request = data['request']
        profile_id = data.get('profileId', 'LOKAL')
        
        # Generate SQL using AI
        ai_result = generate_sql_with_ai(user_request, profile_id)
        
        return jsonify({
            'success': True,
            'ai_response': ai_result,
            'user_request': user_request,
            'profile_id': profile_id
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'AI query generation failed: {str(e)}',
            'error_details': {
                'error_type': 'AI Generation Error',
                'error_message': str(e),
                'possible_causes': ['Check AI API key', 'Verify database connection', 'Check user request format']
            },
            'user_request': data.get('request', '') if 'data' in locals() else '',
            'profile_id': data.get('profileId', 'LOKAL') if 'data' in locals() else 'LOKAL'
        }), 500

@queries_bp.route('/ai', methods=['OPTIONS'])
def ai_options():
    """Handle OPTIONS for AI endpoint"""
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200 