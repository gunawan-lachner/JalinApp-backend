import time
import sqlite3
import os
from flask import Blueprint, request, render_template, session, flash, redirect, url_for, jsonify, send_file, current_app
import mysql.connector
from mysql.connector import Error
from io import BytesIO
from datetime import datetime
import pandas as pd
import pymysql

from app.config.queries import (
    RNA_TRANSACTION_BASE_QUERY,
    RNA_TRANSACTION_ATTR_QUERY,
    RNA_TRANSACTION_ATTR_SEARCH_QUERY,
    TRANSACTION_FIELDS,
    TRANSACTION_ATTR_FIELDS,
    DEFAULT_QUERY_PARAMS,
    TRANSACTION_ENDPOINT_DATA_QUERY
)
from app.utils.sqlite_helper import enrich_transactions_with_sqlite_data

# Add the DATABASE_FILE constant
DATABASE_FILE = os.path.join('instance', 'myapp_db.db')
print(f"DATABASE_FILE path: {os.path.abspath(DATABASE_FILE)}")
print(f"DATABASE_FILE exists: {os.path.exists(DATABASE_FILE)}")

rna_transaction_bp = Blueprint('rna_transactions', __name__)

# Create a new blueprint for export endpoints
export_bp = Blueprint('export', __name__)

@export_bp.route('/rna_transaction_html', methods=['POST'])
def export_rna_transaction_html():
    # Move the logic from the old app-level export_rna_transaction_html here
    export_function = current_app.view_functions.get('export_rna_transaction_html')
    if export_function:
        return export_function()
    return jsonify({'error': 'Export function not found'}), 404

# Add export endpoints to the RNA transaction blueprint
@rna_transaction_bp.route('/transactions/export/csv', methods=['POST'])
def export_transactions_csv():
    """Export transactions to CSV format"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Extract data from request
        rows = data.get('rows', [])
        columns = data.get('columns', [])
        connection_info = data.get('connectionInfo')
        
        if not rows or not columns:
            return jsonify({'error': 'No rows or columns data provided'}), 400
        
        # Create DataFrame
        df = pd.DataFrame(rows, columns=columns)
        
        # Create CSV in memory
        csv_buffer = BytesIO()
        df.to_csv(csv_buffer, index=False, encoding='utf-8')
        csv_buffer.seek(0)
        
        # Generate filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'rna_transactions_{timestamp}.csv'
        
        return send_file(
            csv_buffer,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        print(f"Error in CSV export: {e}")
        return jsonify({'error': f'Export failed: {str(e)}'}), 500

@rna_transaction_bp.route('/transactions/export/excel', methods=['POST'])
def export_transactions_excel():
    """Export transactions to Excel format"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Extract data from request
        rows = data.get('rows', [])
        columns = data.get('columns', [])
        connection_info = data.get('connectionInfo')
        
        if not rows or not columns:
            return jsonify({'error': 'No rows or columns data provided'}), 400
        
        # Create DataFrame
        df = pd.DataFrame(rows, columns=columns)
        
        # Create Excel in memory
        excel_buffer = BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl', mode='w') as writer:
            df.to_excel(writer, sheet_name='Transactions', index=False)
        excel_buffer.seek(0)
        
        # Generate filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'rna_transactions_{timestamp}.xlsx'
        
        return send_file(
            excel_buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        print(f"Error in Excel export: {e}")
        return jsonify({'error': f'Export failed: {str(e)}'}), 500

@rna_transaction_bp.route('/transactions/export/pdf', methods=['POST'])
def export_transactions_pdf():
    """Export transactions to PDF format"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Extract data from request
        rows = data.get('rows', [])
        columns = data.get('columns', [])
        connection_info = data.get('connectionInfo')
        
        if not rows or not columns:
            return jsonify({'error': 'No rows or columns data provided'}), 400
        
        # Import PDF generation libraries
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
        from reportlab.lib import colors
        
        # Create PDF in memory
        pdf_buffer = BytesIO()
        doc = SimpleDocTemplate(pdf_buffer, pagesize=landscape(letter))
        
        # Create table data
        table_data = [columns] + rows
        
        # Create table
        table = Table(table_data)
        
        # Style the table
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ])
        table.setStyle(style)
        
        # Build PDF
        doc.build([table])
        pdf_buffer.seek(0)
        
        # Generate filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'rna_transactions_{timestamp}.pdf'
        
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        print(f"Error in PDF export: {e}")
        return jsonify({'error': f'Export failed: {str(e)}'}), 500

@rna_transaction_bp.route('/transactions/export/html', methods=['POST'])
def export_transactions_html():
    """Export transactions to HTML format with plugin data"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Extract data from request
        rows = data.get('rows', [])
        columns = data.get('columns', [])
        connection_info = data.get('connectionInfo')
        connection_name = connection_info.get('name') if connection_info else 'LOKAL'
        
        if not rows or not columns:
            return jsonify({'error': 'No rows or columns data provided'}), 400
        
        # Get plugin data
        plugin_data = get_plugin_data(connection_name)
        
        # Create DataFrame
        df = pd.DataFrame(rows, columns=columns)
        
        # Generate HTML with plugin data
        plugin_table_html = ""
        if plugin_data:
            plugin_df = pd.DataFrame(plugin_data)
            plugin_table_html = f"""
            <div style="margin: 20px 0;">
                <h2>Plugin Data</h2>
                <p>Connection Profile: {connection_name}</p>
                {plugin_df.to_html(index=False, classes='table table-striped')}
            </div>
            """
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>RNA Transactions Export</title>
            <style>
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #f2f2f2; font-weight: bold; }}
                tr:nth-child(even) {{ background-color: #f9f9f9; }}
                .header {{ text-align: center; margin: 20px 0; }}
                .section {{ margin: 30px 0; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>RNA Transactions Export</h1>
                <p>Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p>Connection Profile: {connection_name}</p>
            </div>
            
            <div class="section">
                <h2>Transaction Data</h2>
                {df.to_html(index=False, classes='table table-striped')}
            </div>
            
            {plugin_table_html}
        </body>
        </html>
        """
        
        # Create HTML buffer
        html_buffer = BytesIO()
        html_buffer.write(html_content.encode('utf-8'))
        html_buffer.seek(0)
        
        # Generate filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'rna_transactions_{timestamp}.html'
        
        return send_file(
            html_buffer,
            mimetype='text/html',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        print(f"Error in HTML export: {e}")
        return jsonify({'error': f'Export failed: {str(e)}'}), 500

# Add a load_profiles function to load directly from the SQLite database
def load_profiles():
    profiles_list = []
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row  # Return rows as dictionary-like objects
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM connection WHERE connection_type = 'DB' ORDER BY name")
        rows = cursor.fetchall()
        profiles_list = [dict(row) for row in rows]  # Convert Row objects to dicts
        print(f"Loaded {len(profiles_list)} DB profiles from database")
        for profile in profiles_list:
            print(f"Profile: {profile.get('name')} - {profile.get('host')}:{profile.get('port')}/{profile.get('database')}")
    except Exception as e:
        print(f"Error loading profiles from database: {e}")
    finally:
        if conn:
            conn.close()
    return profiles_list

def get_plugin_data(connection_name):
    """Get plugin data from the plugin table based on connection profile"""
    try:
        # Get connection profile
        profiles = load_profiles()
        profile = next((p for p in profiles if p.get('name') == connection_name), None)
        
        if not profile:
            print(f"Profile {connection_name} not found")
            return []
        
        # Connect to MySQL database
        conn = pymysql.connect(
            host=profile['host'],
            port=int(profile['port']) if profile.get('port') else 3306,
            user=profile['user'],
            password=profile['password'],
            database=profile['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        
        with conn.cursor() as cursor:
            # Get all plugin data
            sql = "SELECT * FROM plugin ORDER BY id"
            cursor.execute(sql)
            plugin_data = cursor.fetchall()
        
        conn.close()
        return plugin_data
        
    except Exception as e:
        print(f"Error getting plugin data: {e}")
        return []

# Mapping from UI field names to DB columns or attribute fields
FIELD_MAP = {
    "Trx ID": {"type": "column", "db_field": "id"},
    "TID": {"type": "column", "db_field": "tid"},
    "UUID": {"type": "column", "db_field": "uuid"},
    "RRN": {"type": "attr", "attr_field": "OR.RRN"},
    "Card Number": {"type": "attr", "attr_field": "IN.MASK_PAN"},
    "Procode": {"type": "attr", "attr_field": "IN.PROCCODE"},
    "Amount": {"type": "column", "db_field": "amount"},
    "RC Host": {"type": "attr", "attr_field": "IR.RC"},
    "Result": {"type": "column", "db_field": "result"},
    "Acq ID": {"type": "column", "db_field": "participant_acq_id"},
    "Iss ID": {"type": "column", "db_field": "participant_iss_id"},
    "Gateway ID": {"type": "attr", "attr_field": "IN.GATEWAY_ID"},
    "Principal Code": {"type": "attr", "attr_field": "IR.PRINCIPALS_CODE"},
    "Msg Class": {"type": "column", "db_field": "msgclass"},
    "Msg Type": {"type": "column", "db_field": "msgtype"},
    "Trx Status": {"type": "column", "db_field": "status"},
    "Ref UUID": {"type": "column", "db_field": "refuuid"},
    "Reversal Tries": {"type": "column", "db_field": "reversaltries"}
}

@rna_transaction_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200

@rna_transaction_bp.route('/', methods=['GET', 'POST'])
def rna_transaction_optimized():
    """
    Optimized RNA Transaction API endpoint. Always returns JSON.
    """
    import datetime
    import pymysql
    from app.utils.timezone_utils import convert_to_utc
    from app.config.queries import RNA_TRANSACTION_QUERY, DEFAULT_QUERY_PARAMS
    
    try:
        # Get saved profiles directly from the database instead of session
        saved_profiles = load_profiles()
        
        # Handle both form data and JSON data
        if request.is_json:
            data = request.get_json()
            selected_connection = data.get('rna_connection')
            date_start = data.get('date_start')
            date_end = data.get('date_end')
            search_term = (data.get('search_term') or '').strip()
            page = int(data.get('page', 1))
            page_size = int(data.get('page_size', 10))
            # Handle dynamic filters from JSON
            dynamic_filters = []
            for i in range(20):
                field = data.get(f'filter_field_{i}')
                operator = data.get(f'filter_operator_{i}')
                value = data.get(f'filter_value_{i}')
                if field and operator:
                    dynamic_filters.append({'field': field, 'operator': operator, 'value': value})
        else:
            selected_connection = request.form.get('rna_connection') or request.args.get('rna_connection')
            date_start = request.form.get('date_start')
            date_end = request.form.get('date_end')
            search_term = (request.form.get('search_term') or request.args.get('search_term') or '').strip()
            page = int(request.form.get('page', 1) if request.method == 'POST' else request.args.get('page', 1))
            page_size = int(request.form.get('page_size', 10) if request.method == 'POST' else request.args.get('page_size', 10))
            # Handle dynamic filters from form data
            dynamic_filters = []
            for i in range(20):
                field = request.form.get(f'filter_field_{i}')
                operator = request.form.get(f'filter_operator_{i}')
                value = request.form.get(f'filter_value_{i}')
                if field and operator:
                    dynamic_filters.append({'field': field, 'operator': operator, 'value': value})
        
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        default_date_start = f"{today} 00:00:01"
        default_date_end = f"{today} 23:59:59"
    
        # Use provided dates or defaults
        if not date_start:
            date_start = default_date_start
        if not date_end:
            date_end = default_date_end
        
        filterFields = [
            {"label": "Trx ID", "value": "Trx ID"},
            {"label": "TID", "value": "TID"},
            {"label": "UUID", "value": "UUID"},
            {"label": "RRN", "value": "RRN"},
            {"label": "Card Number", "value": "Card Number"},
            {"label": "Procode", "value": "Procode"},
            {"label": "Amount", "value": "Amount"},
            {"label": "RC Host", "value": "RC Host"},
            {"label": "Result", "value": "Result"},
            {"label": "Acq ID", "value": "Acq ID"},
            {"label": "Iss ID", "value": "Iss ID"},
            {"label": "Gateway ID", "value": "Gateway ID"},
            {"label": "Principal Code", "value": "Principal Code"},
            {"label": "Msg Class", "value": "Msg Class"},
            {"label": "Msg Type", "value": "Msg Type"},
            {"label": "Trx Status", "value": "Trx Status"},
            {"label": "Ref UUID", "value": "Ref UUID"},
            {"label": "Reversal Tries", "value": "Reversal Tries"}
        ]
        filterOperators = [
            {"label": "=", "value": "="},
            {"label": ">", "value": ">"},
            {"label": "<", "value": "<"},
            {"label": ">=", "value": ">="},
            {"label": "<=", "value": "<="},
            {"label": "!=", "value": "!="},
            {"label": "LIKE", "value": "LIKE"}
        ]
    
        offset = (page - 1) * page_size
        results = []
        total_rows = 0
        execution_time = 0
        error = None
        
        if request.method == 'POST':
            import time
            start_time = time.time()
            executed_sql = None
            # Find the selected profile
            if not selected_connection:
                error = 'Please select a connection profile first.'
                profile = None
            else:
                profile = next((p for p in saved_profiles if p.get('name') == selected_connection), None)
                if not profile:
                    error = 'Connection profile not found.'
                else:
                    try:
                        # Convert local time to UTC
                        date_start_utc = convert_to_utc(date_start)
                        date_end_utc = convert_to_utc(date_end)
                        # Build WHERE clause and params
                        where_clauses = []
                        params = []
                        # Add date range condition
                        where_clauses.append("trx.dt BETWEEN %s AND %s")
                        params += [date_start_utc, date_end_utc]
                        # Add dynamic filters
                        for f in dynamic_filters:
                            mapping = FIELD_MAP.get(f['field'])
                            if mapping:
                                if mapping["type"] == "column":
                                    where_clauses.append(f"`trx`.`{mapping['db_field']}` {f['operator']} %s")
                                    params.append(f['value'])
                                elif mapping["type"] == "attr":
                                    # Use EXISTS subquery for attribute fields
                                    where_clauses.append(
                                        f"(EXISTS (SELECT 1 FROM transaction_attr rcin WHERE rcin.transaction_id = trx.id AND rcin.field = '{mapping['attr_field']}' AND rcin.content {f['operator']} %s))"
                                    )
                                    params.append(f['value'])
                            else:
                                # Fallback: treat as a column on trx
                                where_clauses.append(f"`trx`.`{f['field']}` {f['operator']} %s")
                                params.append(f['value'])
                        # Add quick search (server-side) to keep pagination consistent
                        if search_term:
                            like_param = f"%{search_term}%"
                            where_clauses.append(
                                """(
                                    CAST(trx.id AS CHAR) LIKE %s
                                    OR CAST(trx.tid AS CHAR) LIKE %s
                                    OR CAST(trx.uuid AS CHAR) LIKE %s
                                    OR CAST(trx.refuuid AS CHAR) LIKE %s
                                    OR EXISTS (
                                        SELECT 1 FROM transaction_attr ta
                                        WHERE ta.transaction_id = trx.id
                                        AND ta.field IN ('OR.RRN', 'IN.MASK_PAN', 'IN.PROCCODE', 'IR.RC', 'IN.GATEWAY_ID', 'IR.PRINCIPALS_CODE')
                                        AND CAST(ta.content AS CHAR) LIKE %s
                                    )
                                )"""
                            )
                            params += [like_param, like_param, like_param, like_param, like_param]
                        # Combine all WHERE conditions
                        where_sql = ' AND '.join(where_clauses) if where_clauses else DEFAULT_QUERY_PARAMS['where_clause']
                        # Build query parameters
                        query_params = {
                            'where_clause': where_sql,
                            'limit': page_size,
                            'offset': offset
                        }
                        sql = RNA_TRANSACTION_QUERY.format(**query_params)
                        # For display: interpolate params into SQL (unsafe, for display only)
                        def interpolate(sql, params):
                            for p in params:
                                if isinstance(p, str):
                                    p_str = f"'{p}'"
                                elif p is None:
                                    p_str = 'NULL'
                                else:
                                    p_str = str(p)
                                sql = sql.replace('%s', p_str, 1)
                            return sql
                        executed_sql = interpolate(sql, params)
                        # Connect to database
                        conn = pymysql.connect(
                            host=profile['host'],
                            port=int(profile['port']) if profile.get('port') else 3306,
                            user=profile['user'],
                            password=profile['password'],
                            database=profile['database'],
                charset='utf8mb4',
                            cursorclass=pymysql.cursors.DictCursor
                        )
                        with conn.cursor() as cursor:
                            # Execute the query
                            cursor.execute(sql, params)
                            raw_results = cursor.fetchall()
                            # Enrich results with SQLite data
                            results = enrich_transactions_with_sqlite_data(raw_results)
                            # Get total count for pagination
                            count_sql = f"""
                                SELECT COUNT(*) AS total   
                                FROM transaction trx FORCE INDEX (PRIMARY)  
                                WHERE {where_sql}  
                                AND `trx`.`participant_acq_id` IN (1702,1703,1704,1705)  
                            """
                            cursor.execute(count_sql, params)
                            total_rows = cursor.fetchone()['total']
                        conn.close()
                        end_time = time.time()
                        execution_time = end_time - start_time
                    except Exception as ex:
                        end_time = time.time()
                        execution_time = end_time - start_time
                        error = str(ex)
                        print(f"Error in rna_transaction_optimized: {ex}")
                        import traceback
                        traceback.print_exc()
                return jsonify({
                    "results": results,
                    "total_rows": total_rows,
                    "page": page,
                    "page_size": page_size,
                    "execution_time": execution_time,
                    "error": error,
                    "executed_sql": executed_sql
                })
        else:
            # For GET, return available filter fields, operators, and profiles
            return jsonify({
                "filterFields": filterFields,
                "filterOperators": filterOperators,
                "profiles": saved_profiles
            })
    except Exception as ex:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(ex)}), 500

@rna_transaction_bp.route('/profiles', methods=['GET'])
def get_db_profiles():
    profiles = load_profiles()
    return jsonify({'profiles': profiles}) 

@rna_transaction_bp.route('/<trx_id>/attributes', methods=['GET'])
def get_transaction_attributes(trx_id):
    profile_id = request.args.get('profileId')
    if not profile_id:
        return jsonify({'error': 'Missing profileId parameter'}), 400
    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'error': f'Profile {profile_id} not found'}), 404
    try:
        conn = pymysql.connect(
            host=profile['host'],
            port=int(profile['port']) if profile.get('port') else 3306,
            user=profile['user'],
            password=profile['password'],
            database=profile['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM transaction_attr WHERE transaction_id = %s", (trx_id,))
            attributes = cursor.fetchall()
        conn.close()
        if not attributes:
            return jsonify({'attributes': [], 'message': 'No attributes found for this transaction'}), 404
        return jsonify({'attributes': attributes})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@rna_transaction_bp.route('/<uuid>/endpoints', methods=['GET'])
def get_transaction_endpoints(uuid):
    profile_id = request.args.get('profileId')
    if not profile_id:
        return jsonify({'error': 'Missing profileId parameter'}), 400
    profiles = load_profiles()
    profile = next((p for p in profiles if p.get('name') == profile_id), None)
    if not profile:
        return jsonify({'error': f'Profile {profile_id} not found'}), 404
    try:
        conn = pymysql.connect(
            host=profile['host'],
            port=int(profile['port']) if profile.get('port') else 3306,
            user=profile['user'],
            password=profile['password'],
            database=profile['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        with conn.cursor() as cursor:
            cursor.execute(TRANSACTION_ENDPOINT_DATA_QUERY, (uuid,))
            endpoints = cursor.fetchall()
        conn.close()
        if not endpoints:
            return jsonify({'endpoints': [], 'message': 'No endpoints found for this transaction'}), 404
        return jsonify({'endpoints': endpoints})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_transaction_endpoints: {e}")
        print(f"Error details: {error_details}")
        return jsonify({'error': str(e), 'details': error_details}), 500 