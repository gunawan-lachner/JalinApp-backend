from flask import Blueprint, request, jsonify, session, send_file
import sqlite3
import os
import pymysql
import json
import io
from datetime import datetime
import csv

# Create the blueprint

tables_bp = Blueprint('core_tables', __name__)

DATABASE_FILE = os.path.join('instance', 'myapp_db.db')

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def get_db_tables(conn):
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        return tables

def load_profiles():
    """Load database profiles from SQLite"""
    profiles_list = []
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM connection WHERE connection_type = 'DB' ORDER BY name")
        rows = cursor.fetchall()
        profiles_list = [dict(row) for row in rows]
    except Exception as e:
        print(f"Error loading profiles from database: {e}")
    finally:
        if conn:
            conn.close()
    return profiles_list

def get_connection_by_name(profile_name):
    """Get connection details by profile name"""
    profiles = load_profiles()
    return next((p for p in profiles if p.get('name') == profile_name), None)

@tables_bp.route('/get_tables', methods=['POST'])
def get_tables():
    try:
        host = request.form['host']
        user = request.form['user']
        password = request.form['password']
        database = request.form['database']
        port = int(request.form.get('port', 3306))
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database
        )
        tables = get_db_tables(conn)
        conn.close()
        return jsonify({"success": True, "tables": tables})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@tables_bp.route('/download', methods=['POST'])
def download():
    cols = json.loads(request.form['cols'])
    data = json.loads(request.form['data'])
    table = request.form['table']
    filetype = request.form['filetype']
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if filetype == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(cols)
        for row in data:
            writer.writerow(row)
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode()),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'{table}_diff_{timestamp}.csv'
        )
    elif filetype == 'sql':
        output = io.StringIO()
        for row in data:
            values = ', '.join([
                "'" + str(item).replace("'", "\\'") + "'" if item is not None else 'NULL'
                for item in row
            ])
            output.write(f"INSERT INTO `{table}` ({', '.join([f'`{c}`' for c in cols])}) VALUES ({values});\n")
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode()),
            mimetype='text/sql',
            as_attachment=True,
            download_name=f'{table}_diff_{timestamp}.sql'
        )
    elif filetype == 'json':
        output = []
        for row in data:
            row_dict = {}
            for i, col in enumerate(cols):
                row_dict[col] = row[i]
            output.append(row_dict)
        return send_file(
            io.BytesIO(json.dumps(output, indent=2).encode()),
            mimetype='application/json',
            as_attachment=True,
            download_name=f'{table}_diff_{timestamp}.json'
        )
    else:
        return "Invalid filetype", 400

@tables_bp.route('/get_table_columns', methods=['POST'], strict_slashes=False)
def get_table_columns():
    """Get table columns for a specific database profile and table"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        profile = data.get('profile')
        table_name = data.get('table_name')
        
        if not all([profile, table_name]):
            return jsonify({'error': 'Missing required parameters'}), 400
        
        # Get connection details
        conn_details = get_connection_by_name(profile)
        if not conn_details:
            return jsonify({'error': f'Profile {profile} not found'}), 404
        
        # Connect to database
        conn = pymysql.connect(
            host=conn_details['host'],
            port=int(conn_details['port']) if conn_details.get('port') else 3306,
            user=conn_details['user'],
            password=conn_details['password'],
            database=conn_details['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        
        # Get table columns
        with conn.cursor() as cursor:
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
            columns = cursor.fetchall()
        
        # Get primary keys
        with conn.cursor() as cursor:
            cursor.execute(f"SHOW INDEX FROM `{table_name}` WHERE Key_name = 'PRIMARY'")
            primary_keys = [row['Column_name'] for row in cursor.fetchall()]
        
        # Get unique indexes (potential alternative comparison keys)
        with conn.cursor() as cursor:
            cursor.execute(f"SHOW INDEX FROM `{table_name}` WHERE Non_unique = 0 AND Key_name != 'PRIMARY'")
            unique_indexes = {}
            for row in cursor.fetchall():
                key_name = row['Key_name']
                if key_name not in unique_indexes:
                    unique_indexes[key_name] = []
                unique_indexes[key_name].append(row['Column_name'])
        
        conn.close()
        
        return jsonify({
            'table_name': table_name,
            'profile': profile,
            'columns': [col['Field'] for col in columns],
            'primary_keys': primary_keys,
            'unique_indexes': unique_indexes,
            'column_details': columns
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@tables_bp.route('/get_source_tables', methods=['POST'], strict_slashes=False)
def get_source_tables():
    """Get tables from source database"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        profile_name = data.get('profile')
        if not profile_name:
            return jsonify({'error': 'Profile name is required'}), 400
        
        # Get connection details
        profile = get_connection_by_name(profile_name)
        if not profile:
            return jsonify({'error': f'Profile {profile_name} not found'}), 404
        
        # Connect to database
        conn = pymysql.connect(
            host=profile['host'],
            port=int(profile['port']) if profile.get('port') else 3306,
            user=profile['user'],
            password=profile['password'],
            database=profile['database'],
            charset='utf8mb4'
        )
        
        tables = get_db_tables(conn)
        conn.close()
        
        return jsonify({'tables': tables})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@tables_bp.route('/get_target_tables', methods=['POST'], strict_slashes=False)
def get_target_tables():
    """Get tables from target database (same as source for now)"""
    return get_source_tables()

@tables_bp.route('/compare_tables', methods=['POST'], strict_slashes=False)
def compare_tables():
    """Compare tables between source and target databases with enhanced field-level comparison"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        source_profile = data.get('source_profile')
        target_profile = data.get('target_profile')
        source_table = data.get('source_table')
        target_table = data.get('target_table')
        query = data.get('query')  # New: support for custom query
        column_mapping = data.get('column_mapping', {})
        compare_options = data.get('compare_options', {})
        
        # Enhanced comparison options
        ignore_case = compare_options.get('ignore_case', False)
        ignore_trailing_spaces = compare_options.get('ignore_trailing_spaces', False)
        exclude_fields = compare_options.get('exclude_fields', [])  # Fields to exclude from comparison
        # Default comparison key: auto for query mode, primary for table mode
        comparison_key = compare_options.get('comparison_key', 'auto' if query else 'primary')
        debug = compare_options.get('debug', False)  # Debug mode for detailed logging
        
        # Check if using query mode or table mode
        if query:
            # Query mode: use custom query for both databases
            if not all([source_profile, target_profile, query]):
                return jsonify({'error': 'Missing required parameters for query mode: source_profile, target_profile, query'}), 400
        else:
            # Table mode: compare actual tables
            if not all([source_profile, target_profile, source_table, target_table]):
                return jsonify({'error': 'Missing required parameters for table mode: source_profile, target_profile, source_table, target_table'}), 400
        
        # Get connection details
        source_conn_details = get_connection_by_name(source_profile)
        target_conn_details = get_connection_by_name(target_profile)
        
        if not source_conn_details:
            return jsonify({'error': f'Source profile {source_profile} not found'}), 404
        if not target_conn_details:
            return jsonify({'error': f'Target profile {target_profile} not found'}), 404
        
        # Connect to source database
        source_conn = pymysql.connect(
            host=source_conn_details['host'],
            port=int(source_conn_details['port']) if source_conn_details.get('port') else 3306,
            user=source_conn_details['user'],
            password=source_conn_details['password'],
            database=source_conn_details['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        
        # Connect to target database
        target_conn = pymysql.connect(
            host=target_conn_details['host'],
            port=int(target_conn_details['port']) if target_conn_details.get('port') else 3306,
            user=target_conn_details['user'],
            password=target_conn_details['password'],
            database=target_conn_details['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        
        # Get table structure and primary keys
        def get_primary_keys(connection, table_name):
            """Get primary key columns for a table"""
            with connection.cursor() as cursor:
                cursor.execute(f"SHOW INDEX FROM `{table_name}` WHERE Key_name = 'PRIMARY'")
                primary_keys = [row['Column_name'] for row in cursor.fetchall()]
                return primary_keys
        
        def get_table_columns(connection, table_name):
            """Get all columns for a table"""
            with connection.cursor() as cursor:
                cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
                columns = [row['Field'] for row in cursor.fetchall()]
                return columns
        
        # Get primary keys and columns based on mode
        if query:
            # Query mode: get columns from query results
            with source_conn.cursor() as cursor:
                cursor.execute(query)
                source_data = cursor.fetchall()
                source_columns = list(source_data[0].keys()) if source_data else []
            
            with target_conn.cursor() as cursor:
                cursor.execute(query)
                target_data = cursor.fetchall()
                target_columns = list(target_data[0].keys()) if target_data else []
            
            # For query mode, we'll determine comparison keys after getting data
            source_primary_keys = []
            target_primary_keys = []
        else:
            # Table mode: get primary keys and columns from table metadata
            source_primary_keys = get_primary_keys(source_conn, source_table)
            target_primary_keys = get_primary_keys(target_conn, target_table)
            source_columns = get_table_columns(source_conn, source_table)
            target_columns = get_table_columns(target_conn, target_table)
        
        if debug:
            print(f"DEBUG: Source primary keys: {source_primary_keys}")
            print(f"DEBUG: Target primary keys: {target_primary_keys}")
            print(f"DEBUG: Source columns: {source_columns}")
            print(f"DEBUG: Target columns: {target_columns}")
        
        # Determine comparison keys based on comparison_key option
        if comparison_key == 'primary' or (query and comparison_key == 'auto'):
            if query:
                # Query mode: try to find common columns that could serve as comparison keys
                common_columns = [col for col in source_columns if col in target_columns]
                # Prefer uuid over id when available
                if 'uuid' in common_columns:
                    source_comparison_keys = ['uuid']
                    target_comparison_keys = ['uuid']
                elif 'UUID' in common_columns:
                    source_comparison_keys = ['UUID']
                    target_comparison_keys = ['UUID']
                elif 'id' in common_columns:
                    source_comparison_keys = ['id']
                    target_comparison_keys = ['id']
                elif 'ID' in common_columns:
                    source_comparison_keys = ['ID']
                    target_comparison_keys = ['ID']
                else:
                    # Use first common column as comparison key
                    if common_columns:
                        source_comparison_keys = [common_columns[0]]
                        target_comparison_keys = [common_columns[0]]
                    else:
                        return jsonify({
                            'error': 'No common columns found between source and target query results'
                        }), 400
            else:
                # Table mode: Use detected primary keys
                if source_primary_keys != target_primary_keys:
                    return jsonify({
                        'error': f'Primary key mismatch: source has {source_primary_keys}, target has {target_primary_keys}'
                    }), 400
                
                if not source_primary_keys:
                    return jsonify({
                        'error': f'No primary key found for table {source_table}'
                    }), 400
                
                source_comparison_keys = source_primary_keys
                target_comparison_keys = target_primary_keys
        else:
            # Use custom comparison key(s)
            if isinstance(comparison_key, str):
                comparison_keys = [comparison_key]
            else:
                comparison_keys = comparison_key
            
            # Validate that comparison keys exist in both tables/query results
            for key in comparison_keys:
                if key not in source_columns:
                    error_msg = f'Comparison key "{key}" not found in source'
                    if query:
                        error_msg += ' query results'
                    else:
                        error_msg += f' table {source_table}'
                    return jsonify({'error': error_msg}), 400
                    
                if key not in target_columns:
                    error_msg = f'Comparison key "{key}" not found in target'
                    if query:
                        error_msg += ' query results'
                    else:
                        error_msg += f' table {target_table}'
                    return jsonify({'error': error_msg}), 400
            
            source_comparison_keys = comparison_keys
            target_comparison_keys = comparison_keys
        
        if debug:
            print(f"DEBUG: Using comparison keys: {source_comparison_keys}")
            print(f"DEBUG: Comparison mode: {'auto' if (query and comparison_key == 'auto') else ('primary' if comparison_key == 'primary' else 'custom')}")
        
        # Determine which fields to compare (automatically exclude comparison keys and excluded fields)
        fields_to_compare = [f for f in source_columns if f in target_columns and f not in exclude_fields and f not in source_comparison_keys]
        
        # Get data for table mode (query mode already got data above)
        if not query:
            # Table mode: get all data from specified tables
            with source_conn.cursor() as cursor:
                cursor.execute(f"SELECT * FROM `{source_table}`")
                source_data = cursor.fetchall()
            
            with target_conn.cursor() as cursor:
                cursor.execute(f"SELECT * FROM `{target_table}`")
                target_data = cursor.fetchall()
        else:
            # Query mode: validate query for security
            if not _validate_query_security(query):
                return jsonify({'error': 'Query contains potentially unsafe operations'}), 400
        
        # Simple single-record comparison path for query mode without comparison_key
        if query and compare_options.get('comparison_key') is None:
            if len(source_data) != 1 or len(target_data) != 1:
                return jsonify({
                    'error': 'Query-mode without comparison_key requires exactly 1 row from each database',
                    'source_count': len(source_data),
                    'target_count': len(target_data)
                }), 400

            source_record = source_data[0]
            target_record = target_data[0]
            simple_fields_to_compare = [f for f in source_columns if f in target_columns and f not in exclude_fields]

            def normalize_value(value, ignore_case, ignore_trailing_spaces):
                if value is None:
                    return None
                if isinstance(value, str):
                    if ignore_trailing_spaces:
                        value = value.rstrip()
                    if ignore_case:
                        value = value.lower()
                return value

            differences = {}
            for field in simple_fields_to_compare:
                s_val = source_record.get(field)
                t_val = target_record.get(field)
                ns = normalize_value(s_val, ignore_case, ignore_trailing_spaces)
                nt = normalize_value(t_val, ignore_case, ignore_trailing_spaces)
                if ns != nt:
                    differences[field] = {
                        'source': s_val,
                        'target': t_val,
                        'normalized_source': ns,
                        'normalized_target': nt
                    }

            # Close connections
            source_conn.close()
            target_conn.close()

            is_identical = len(differences) == 0
            summary = {
                'only_in_source': 0,
                'only_in_target': 0,
                'different': 0 if is_identical else 1,
                'identical': 1 if is_identical else 0,
                'total_records_compared': 1,
                'fields_compared': simple_fields_to_compare,
                'comparison_keys_used': [],
                'comparison_mode': 'single_record',
                'data_mode': 'query',
                'comparison_options': {
                    'ignore_case': ignore_case,
                    'ignore_trailing_spaces': ignore_trailing_spaces,
                    'exclude_fields': exclude_fields,
                    'comparison_key': None
                },
                'query': query
            }

            return jsonify({
                'summary': summary,
                'only_in_source': [],
                'only_in_target': [],
                'different': [] if is_identical else [{
                    'source_record': source_record,
                    'target_record': target_record,
                    'differences': differences,
                    'fields_with_differences': list(differences.keys())
                }],
                'identical': [source_record] if is_identical else []
            })

        if debug:
            print(f"DEBUG: Source data count: {len(source_data)}")
            print(f"DEBUG: Target data count: {len(target_data)}")
            if source_data:
                print(f"DEBUG: Source sample record: {source_data[0]}")
            if target_data:
                print(f"DEBUG: Target sample record: {target_data[0]}")
            print(f"DEBUG: Comparing ALL records (no limit)")
        
        # Create lookup dictionaries for efficient comparison using comparison keys
        def create_comparison_key_string(record, comparison_keys):
            """Create a string representation of the comparison key values"""
            key_parts = []
            for key in comparison_keys:
                value = record.get(key)
                if value is None:
                    value = ''
                key_parts.append(str(value))
            return '|'.join(key_parts)
        
        # Create lookup dictionaries using comparison keys
        source_lookup = {create_comparison_key_string(row, source_comparison_keys): row for row in source_data}
        target_lookup = {create_comparison_key_string(row, target_comparison_keys): row for row in target_data}
        
        source_keys = set(source_lookup.keys())
        target_keys = set(target_lookup.keys())
        
        only_in_source = [row for row in source_data if create_comparison_key_string(row, source_comparison_keys) not in target_keys]
        only_in_target = [row for row in target_data if create_comparison_key_string(row, target_comparison_keys) not in source_keys]
        
        # Enhanced field-level comparison for common primary keys
        common_keys = source_keys & target_keys
        identical = []
        different = []
        
        if debug:
            print(f"DEBUG: Common comparison keys found: {len(common_keys)}")
            print(f"DEBUG: Fields to compare: {fields_to_compare}")
            print(f"DEBUG: Sample common comparison key: {list(common_keys)[:5] if common_keys else 'None'}")
            print(f"DEBUG: Comparison keys used: {source_comparison_keys}")
            print(f"DEBUG: Comparison mode: {'primary' if comparison_key == 'primary' else 'custom'}")
        
        def normalize_value(value, ignore_case, ignore_trailing_spaces):
            """Normalize value based on comparison options"""
            if value is None:
                return None
            
            if isinstance(value, str):
                if ignore_trailing_spaces:
                    value = value.rstrip()
                if ignore_case:
                    value = value.lower()
            return value
        
        for primary_key_string in common_keys:
            source_record = source_lookup[primary_key_string]
            target_record = target_lookup[primary_key_string]
            
            # Compare each field
            differences = {}
            is_identical = True
            
            for field in fields_to_compare:
                source_value = source_record.get(field)
                target_value = target_record.get(field)
                
                # Normalize values based on options
                normalized_source = normalize_value(source_value, ignore_case, ignore_trailing_spaces)
                normalized_target = normalize_value(target_value, ignore_case, ignore_trailing_spaces)
                
                if normalized_source != normalized_target:
                    is_identical = False
                    differences[field] = {
                        'source': source_value,
                        'target': target_value,
                        'normalized_source': normalized_source,
                        'normalized_target': normalized_target
                    }
            
            if is_identical:
                identical.append(source_record)
            else:
                # Create a record showing the differences
                diff_record = {
                    'comparison_key': primary_key_string,
                    'comparison_key_columns': source_comparison_keys,
                    'source_record': source_record,
                    'target_record': target_record,
                    'differences': differences,
                    'fields_with_differences': list(differences.keys())
                }
                different.append(diff_record)
        
        # Close connections
        source_conn.close()
        target_conn.close()
        
        # Prepare response
        summary = {
            'only_in_source': len(only_in_source),
            'only_in_target': len(only_in_target),
            'different': len(different),
            'identical': len(identical),
            'total_records_compared': len(common_keys),
            'fields_compared': fields_to_compare,
            'comparison_keys_used': source_comparison_keys,
            'comparison_mode': ('auto' if (query and comparison_key == 'auto') else ('primary' if comparison_key == 'primary' else 'custom')),
            'data_mode': 'query' if query else 'table',
            'comparison_options': {
                'ignore_case': ignore_case,
                'ignore_trailing_spaces': ignore_trailing_spaces,
                'exclude_fields': exclude_fields,
                'comparison_key': comparison_key
            }
        }
        
        # Add mode-specific information to summary
        if query:
            summary['query'] = query
        else:
            summary['source_table'] = source_table
            summary['target_table'] = target_table
        
        return jsonify({
            'summary': summary,
            'only_in_source': only_in_source,
            'only_in_target': only_in_target,
            'different': different,
            'identical': identical
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@tables_bp.route('/compare_tables_by_query', methods=['POST'], strict_slashes=False)
def compare_tables_by_query():
    """Compare tables between source and target databases using custom SQL queries"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        source_profile = data.get('source_profile')
        target_profile = data.get('target_profile')
        query = data.get('query')
        comparison_options = data.get('comparison_options', {})
        
        # Enhanced comparison options
        ignore_case = comparison_options.get('ignore_case', False)
        ignore_trailing_spaces = comparison_options.get('ignore_trailing_spaces', False)
        exclude_fields = comparison_options.get('exclude_fields', [])
        comparison_key = comparison_options.get('comparison_key', 'auto')  # 'auto', 'primary', or custom column(s)
        debug = comparison_options.get('debug', False)
        limit_results = comparison_options.get('limit_results', 10000)  # Limit for performance
        
        if not all([source_profile, target_profile, query]):
            return jsonify({'error': 'Missing required parameters: source_profile, target_profile, query'}), 400
        
        # Validate query for security (basic SQL injection prevention)
        if not _validate_query_security(query):
            return jsonify({'error': 'Query contains potentially unsafe operations'}), 400
        
        # Get connection details
        source_conn_details = get_connection_by_name(source_profile)
        target_conn_details = get_connection_by_name(target_profile)
        
        if not source_conn_details:
            return jsonify({'error': f'Source profile {source_profile} not found'}), 404
        if not target_conn_details:
            return jsonify({'error': f'Target profile {target_profile} not found'}), 404
        
        # Connect to source database
        source_conn = pymysql.connect(
            host=source_conn_details['host'],
            port=int(source_conn_details['port']) if source_conn_details.get('port') else 3306,
            user=source_conn_details['user'],
            password=source_conn_details['password'],
            database=source_conn_details['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        
        # Connect to target database
        target_conn = pymysql.connect(
            host=target_conn_details['host'],
            port=int(target_conn_details['port']) if target_conn_details.get('port') else 3306,
            user=target_conn_details['user'],
            password=target_conn_details['password'],
            database=target_conn_details['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        
        # Execute query on source database
        with source_conn.cursor() as cursor:
            if limit_results:
                limited_query = f"{query} LIMIT {limit_results}"
            else:
                limited_query = query
            cursor.execute(limited_query)
            source_data = cursor.fetchall()
        
        # Execute same query on target database
        with target_conn.cursor() as cursor:
            if limit_results:
                limited_query = f"{query} LIMIT {limit_results}"
            else:
                limited_query = query
            cursor.execute(limited_query)
            target_data = cursor.fetchall()
        
        if debug:
            print(f"DEBUG: Source query results count: {len(source_data)}")
            print(f"DEBUG: Target query results count: {len(target_data)}")
            if source_data:
                print(f"DEBUG: Source sample record: {source_data[0]}")
            if target_data:
                print(f"DEBUG: Target sample record: {target_data[0]}")
            print(f"DEBUG: Query executed: {query}")
        
        # Get column information
        source_columns = list(source_data[0].keys()) if source_data else []
        target_columns = list(target_data[0].keys()) if target_data else []
        
        if not source_columns or not target_columns:
            return jsonify({
                'error': 'One or both queries returned no data or no columns',
                'source_columns': source_columns,
                'target_columns': target_columns
            }), 400
        
        # Determine comparison keys
        if comparison_key == 'auto':
            # Try to find common columns that could serve as comparison keys
            common_columns = [col for col in source_columns if col in target_columns]
            if 'id' in common_columns:
                source_comparison_keys = ['id']
                target_comparison_keys = ['id']
            elif 'ID' in common_columns:
                source_comparison_keys = ['ID']
                target_comparison_keys = ['ID']
            else:
                # Use all common columns as comparison key
                source_comparison_keys = common_columns[:3]  # Limit to first 3 columns
                target_comparison_keys = common_columns[:3]
        elif comparison_key == 'primary':
            # This doesn't make sense for custom queries, fall back to auto
            source_comparison_keys = source_columns[:1]
            target_comparison_keys = target_columns[:1]
        else:
            # Custom comparison key(s)
            if isinstance(comparison_key, str):
                comparison_keys = [comparison_key]
            else:
                comparison_keys = comparison_key
            
            source_comparison_keys = [key for key in comparison_keys if key in source_columns]
            target_comparison_keys = [key for key in comparison_keys if key in target_columns]
        
        if not source_comparison_keys or not target_comparison_keys:
            return jsonify({
                'error': 'No valid comparison keys found between source and target results',
                'source_columns': source_columns,
                'target_columns': target_columns,
                'requested_comparison_key': comparison_key
            }), 400
        
        if debug:
            print(f"DEBUG: Using comparison keys: {source_comparison_keys}")
            print(f"DEBUG: Comparison mode: {comparison_key}")
        
        # Determine which fields to compare (exclude comparison keys and excluded fields)
        fields_to_compare = [f for f in source_columns if f in target_columns and f not in exclude_fields and f not in source_comparison_keys]
        
        # Create lookup dictionaries for efficient comparison
        def create_comparison_key_string(record, comparison_keys):
            """Create a string representation of the comparison key values"""
            key_parts = []
            for key in comparison_keys:
                value = record.get(key)
                if value is None:
                    value = ''
                key_parts.append(str(value))
            return '|'.join(key_parts)
        
        # Create lookup dictionaries using comparison keys
        source_lookup = {create_comparison_key_string(row, source_comparison_keys): row for row in source_data}
        target_lookup = {create_comparison_key_string(row, target_comparison_keys): row for row in target_data}
        
        source_keys = set(source_lookup.keys())
        target_keys = set(target_lookup.keys())
        
        only_in_source = [row for row in source_data if create_comparison_key_string(row, source_comparison_keys) not in target_keys]
        only_in_target = [row for row in target_data if create_comparison_key_string(row, target_comparison_keys) not in source_keys]
        
        # Enhanced field-level comparison for common keys
        common_keys = source_keys & target_keys
        identical = []
        different = []
        
        if debug:
            print(f"DEBUG: Common comparison keys found: {len(common_keys)}")
            print(f"DEBUG: Fields to compare: {fields_to_compare}")
        
        def normalize_value(value, ignore_case, ignore_trailing_spaces):
            """Normalize value based on comparison options"""
            if value is None:
                return None
            
            if isinstance(value, str):
                if ignore_trailing_spaces:
                    value = value.rstrip()
                if ignore_case:
                    value = value.lower()
            return value
        
        for primary_key_string in common_keys:
            source_record = source_lookup[primary_key_string]
            target_record = target_lookup[primary_key_string]
            
            # Compare each field
            differences = {}
            is_identical = True
            
            for field in fields_to_compare:
                source_value = source_record.get(field)
                target_value = target_record.get(field)
                
                # Normalize values based on options
                normalized_source = normalize_value(source_value, ignore_case, ignore_trailing_spaces)
                normalized_target = normalize_value(target_value, ignore_case, ignore_trailing_spaces)
                
                if normalized_source != normalized_target:
                    is_identical = False
                    differences[field] = {
                        'source': source_value,
                        'target': target_value,
                        'normalized_source': normalized_source,
                        'normalized_target': normalized_target
                    }
            
            if is_identical:
                identical.append(source_record)
            else:
                # Create a record showing the differences
                diff_record = {
                    'comparison_key': primary_key_string,
                    'comparison_key_columns': source_comparison_keys,
                    'source_record': source_record,
                    'target_record': target_record,
                    'differences': differences,
                    'fields_with_differences': list(differences.keys())
                }
                different.append(diff_record)
        
        # Close connections
        source_conn.close()
        target_conn.close()
        
        # Prepare response
        summary = {
            'only_in_source': len(only_in_source),
            'only_in_target': len(only_in_target),
            'different': len(different),
            'identical': len(identical),
            'total_records_compared': len(common_keys),
            'fields_compared': fields_to_compare,
            'comparison_keys_used': source_comparison_keys,
            'comparison_mode': comparison_key,
            'query': query,
            'source_columns': source_columns,
            'target_columns': target_columns,
            'comparison_options': {
                'ignore_case': ignore_case,
                'ignore_trailing_spaces': ignore_trailing_spaces,
                'exclude_fields': exclude_fields,
                'comparison_key': comparison_key,
                'limit_results': limit_results
            }
        }
        
        return jsonify({
            'summary': summary,
            'only_in_source': only_in_source,
            'only_in_target': only_in_target,
            'different': different,
            'identical': identical
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _validate_query_security(query):
    """Basic SQL injection prevention - validate query for safety"""
    if not query or not isinstance(query, str):
        return False
    
    # Convert to lowercase for checking
    query_lower = query.lower().strip()
    
    # Dangerous operations to block
    dangerous_operations = [
        'drop ', 'delete ', 'truncate ', 'alter ', 'create ', 'insert ', 'update ',
        'grant ', 'revoke ', 'exec ', 'execute ', 'sp_', 'xp_', '--', '/*', '*/',
        'union ', 'information_schema', 'mysql.', 'sys.', 'performance_schema'
    ]
    
    # Check for dangerous operations
    for operation in dangerous_operations:
        if operation in query_lower:
            return False
    
    # Must start with SELECT
    if not query_lower.startswith('select'):
        return False
    
    return True 

@tables_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200 