from flask import Blueprint, request, jsonify, render_template_string, render_template
import sqlite3
import os
import json
import pymysql
import requests
from datetime import datetime
import pymysql.cursors
import re
import time
import jwt
import uuid

# JWT Configuration (same as auth.py)
SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your-secret-key-change-in-production')
ALGORITHM = "HS256"

def get_next_batch_id():
    """Get the next available batch ID (saved_id)"""
    try:
        sqlite_conn = get_sqlite_connection()
        sqlite_cursor = sqlite_conn.cursor()
        
        # Get the maximum saved_id from the database
        sqlite_cursor.execute("SELECT MAX(CAST(saved_id AS INTEGER)) FROM saved_transactions WHERE saved_id IS NOT NULL AND saved_id != ''")
        result = sqlite_cursor.fetchone()
        
        sqlite_cursor.close()
        sqlite_conn.close()
        
        # If no existing saved_id, start with 1, otherwise increment
        next_id = 1 if result[0] is None else result[0] + 1
        return next_id
    except Exception as e:
        print(f"Error getting next batch ID: {e}")
        return 1

def generate_saved_id():
    """Generate a unique saved_id for batch grouping"""
    return str(uuid.uuid4())

def get_current_user_id():
    """Extract user ID from JWT token in Authorization header"""
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            # For testing purposes, return a default user ID
            return 1
        
        token = auth_header.split(' ')[1]
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get('user_id')
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, Exception):
        # For testing purposes, return a default user ID
        return 1

transaction_bp = Blueprint('transaction', __name__)

def get_db_connection():
    """Get SQLite database connection"""
    # Get the project root (4 levels up from this file: routes/transaction/transaction.py -> routes -> app -> project_root)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    db_path = os.path.join(project_root, 'instance', 'myapp_db.db')
    
    # Ensure the directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
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

def get_transaction_attributes(transaction_ids, connection):
    """Get transaction attributes for given transaction IDs"""
    try:
        if not transaction_ids:
            return []
        
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Convert list to tuple for SQL IN clause
        if len(transaction_ids) == 1:
            placeholders = '%s'
            params = transaction_ids
        else:
            placeholders = ','.join(['%s'] * len(transaction_ids))
            params = transaction_ids
        
        sql = f"""
            SELECT id, transaction_id, field, content
            FROM transaction_attr 
            WHERE transaction_id IN ({placeholders})
            ORDER BY transaction_id, field ASC
        """
        
        mysql_cursor.execute(sql, params)
        attributes = mysql_cursor.fetchall()
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return attributes
    except Exception as e:
        print(f"Error fetching transaction attributes: {e}")
        return []

def get_transaction_endpoint_data(uuids, connection):
    """Get transaction endpoint data for given UUIDs"""
    try:
        if not uuids:
            return []
        
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Convert list to tuple for SQL IN clause
        if len(uuids) == 1:
            placeholders = '%s'
            params = uuids
        else:
            placeholders = ','.join(['%s'] * len(uuids))
            params = uuids
        
        sql = f"""
            SELECT id, uuid, date_time, participant_id, participant_name, 
                   message_direction, request_response, messages
            FROM transaction_endpoint_data 
            WHERE uuid IN ({placeholders})
            ORDER BY uuid, date_time ASC
        """
        
        mysql_cursor.execute(sql, params)
        endpoint_data = mysql_cursor.fetchall()
        
        mysql_cursor.close()
        mysql_conn.close()
        
        return endpoint_data
    except Exception as e:
        print(f"Error fetching endpoint data: {e}")
        return []

def fetch_transaction_attributes(transaction_ids, connection):
    """Fetch transaction attributes using RNA API"""
    try:
        if not transaction_ids:
            return {}
        
        result = {}
        for trx_id in transaction_ids:
            # Call the RNA API for attributes
            url = f"http://localhost:8005/api/transactions/{trx_id}/attributes?profileId={connection}"
            print(f"[DEBUG] Fetching attributes from: {url}")
            response = requests.get(url, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                attributes = data.get('attributes', [])
                print(f"[DEBUG] Found {len(attributes)} attributes for transaction {trx_id}")
                
                # Group by transaction ID
                trx_id_str = str(trx_id)
                if trx_id_str not in result:
                    result[trx_id_str] = []
                
                for attr in attributes:
                    result[trx_id_str].append({
                        'id': attr.get('id', ''),
                        'field': attr.get('field', ''),
                        'content': attr.get('content', ''),
                        'transaction_id': attr.get('transaction_id', '')
                    })
            else:
                print(f"[DEBUG] API returned status {response.status_code} for transaction {trx_id}: {response.text}")
        
        print(f"[DEBUG] Total transactions with attributes: {len(result)}")
        return result
    except Exception as e:
        print(f"Error fetching transaction attributes: {e}")
        import traceback
        traceback.print_exc()
        return {}

def fetch_transaction_endpoints(transactions_to_save, connection):
    """Fetch transaction endpoint data using RNA API"""
    try:
        if not transactions_to_save:
            return {}
        
        result = {}
        for transaction in transactions_to_save:
            trx_id = transaction.get('trx_id')
            uuid = transaction.get('uuid')
            
            if not uuid:
                print(f"[DEBUG] No UUID found for transaction {trx_id}")
                continue
                
            # Call the RNA API for endpoints using UUID
            url = f"http://localhost:8005/api/transactions/{uuid}/endpoints?profileId={connection}"
            print(f"[DEBUG] Fetching endpoints from: {url}")
            response = requests.get(url, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                endpoints = data.get('endpoints', [])
                print(f"[DEBUG] Found {len(endpoints)} endpoints for UUID {uuid}")
                
                # Group by UUID instead of transaction ID
                if uuid not in result:
                    result[uuid] = []
                
                for endpoint in endpoints:
                    # Format date_time if it exists
                    dt_value = endpoint.get('dt', '')
                    if dt_value:
                        try:
                            if hasattr(dt_value, 'strftime'):
                                dt_formatted = dt_value.strftime('%Y-%m-%d %H:%M:%S')
                            else:
                                dt_formatted = str(dt_value)
                        except:
                            dt_formatted = str(dt_value)
                    else:
                        dt_formatted = ''
                    
                    result[uuid].append({
                        'id': endpoint.get('id', ''),
                        'dt': dt_formatted,
                        'msg_direction': endpoint.get('msg_direction', ''),
                        'request_response': str(endpoint.get('request_response', '')),
                        'message': endpoint.get('message', ''),
                        'participant_id': endpoint.get('participant_id', ''),
                        'state': endpoint.get('state', ''),
                        'uuid': endpoint.get('uuid', ''),
                        'worker_id': endpoint.get('worker_id', '')
                    })
            else:
                print(f"[DEBUG] API returned status {response.status_code} for UUID {uuid}: {response.text}")
        
        print(f"[DEBUG] Total UUIDs with endpoints: {len(result)}")
        return result
    except Exception as e:
        print(f"Error fetching transaction endpoints: {e}")
        import traceback
        traceback.print_exc()
        return {}

def fetch_graylog_data(transactions_to_save, connection, date_start=None, date_end=None):
    """Fetch GrayLog data for given transactions using the new GrayLog data API"""
    try:
        if not transactions_to_save:
            print("[DEBUG] No transactions to save")
            return {}
        
        print(f"[DEBUG] Fetching GrayLog data for {len(transactions_to_save)} transactions")
        result = {}
        
        # Import the direct GrayLog data fetching function
        from app.routes.graylog.graylog_data import fetch_graylog_data_direct
        
        # Process each transaction
        for transaction in transactions_to_save:
            uuid = transaction.get('uuid')
            if not uuid:
                print(f"[DEBUG] No UUID found in transaction: {transaction}")
                continue
                
            print(f"[DEBUG] Processing UUID: {uuid}")
            try:
                # Call the direct GrayLog data function
                response = fetch_graylog_data_direct(
                    uuid=uuid,
                    connection=connection,
                    days_back=7,  # Default to 7 days
                    limit=100,    # Default limit
                    include_rules=True  # Include rules for color coding
                )
                
                print(f"[DEBUG] GrayLog response for {uuid}: {response.get('success')}")
                
                # Check if the response is successful
                if response.get('success'):
                    graylog_data = response.get('data', {})
                    messages = graylog_data.get('messages', [])
                    
                    print(f"[DEBUG] Found {len(messages)} messages for UUID {uuid}")
                    
                    # Store the GrayLog data for this UUID
                    result[uuid] = {
                        'messages': messages,
                        'total_messages': graylog_data.get('total_messages', 0),
                        'rules': graylog_data.get('rules', []),
                        'time_range': graylog_data.get('time_range', {}),
                        'search_info': graylog_data.get('search_info', {})
                    }
                else:
                    print(f"GrayLog API error for UUID {uuid}: {response.get('error', 'Unknown error')}")
                    result[uuid] = {'messages': [], 'error': response.get('error', 'Unknown error')}
                    
            except Exception as trx_error:
                print(f"Error processing transaction with UUID {uuid}: {trx_error}")
                result[uuid] = {'messages': [], 'error': str(trx_error)}
        
        print(f"[DEBUG] Final result: {len(result)} UUIDs processed")
        return result
        
    except Exception as e:
        print(f"Error in fetch_graylog_data: {e}")
        return {}

def get_graylog_data(uuids, connection, date_start=None, date_end=None):
    """Get GrayLog data for given UUIDs (placeholder for now)"""
    # This would typically connect to GrayLog API
    # For now, return empty list as placeholder
    # In a real implementation, this would query GrayLog API with UUIDs and date range
    return []

def format_sql(sql, params):
    """Format SQL query for logging"""
    if not params:
        return sql
    try:
        return sql % tuple(params)
    except:
        return f"{sql} with params: {params}"

def get_sqlite_connection():
    """Get SQLite database connection for saved transactions"""
    # Use absolute path from project root to ensure consistency
    # Get the project root (3 levels up from this file: routes/transaction/transaction.py -> routes -> app -> project_root)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    db_path = os.path.join(project_root, 'instance', 'mytrx_db.db')
    
    # Ensure the directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    return sqlite3.connect(db_path)

@transaction_bp.route('/save', methods=['POST'])
def save_transactions():
    """Save selected transactions with their associated data to SQLite"""
    try:
        data = request.get_json()
        # Extract data from request
        connection = data.get('connection')
        save_name = data.get('saveName')
        description = data.get('description', '')
        transactions_to_save = data.get('transactions', [])
        save_graylog = data.get('saveGraylog', False)  # Default to False
        existing_saved_id = data.get('saved_id')  # New parameter to add to existing batch
        
        # Get user_id from JWT token
        user_id = get_current_user_id()
        if not user_id:
            return jsonify({
                'success': False,
                'error': 'Authentication required. Please provide valid JWT token.'
            }), 401
        
        if not connection or not transactions_to_save:
            return jsonify({
                'success': False,
                'error': 'Missing required fields: connection or transactions'
            }), 400
        
        if not transactions_to_save:
            return jsonify({
                'success': False,
                'error': 'No transactions selected for saving'
            }), 400
        
        # Extract transaction IDs and UUIDs
        transaction_ids = [t.get('trx_id') for t in transactions_to_save if t.get('trx_id')]
        uuids = [t.get('uuid') for t in transactions_to_save if t.get('uuid')]
        
        if not transaction_ids:
            return jsonify({
                'success': False,
                'error': 'No valid transaction IDs provided'
            }), 400
        
        # Get SQLite connection
        sqlite_conn = get_sqlite_connection()
        sqlite_cursor = sqlite_conn.cursor()
        
        # Determine the saved_id to use
        if existing_saved_id:
            # Check if the existing saved_id belongs to the current user and get batch info
            sqlite_cursor.execute("""
                SELECT save_name, description FROM saved_transactions 
                WHERE saved_id = ? AND user_id = ?
                LIMIT 1
            """, (existing_saved_id, user_id))
            
            existing_batch = sqlite_cursor.fetchone()
            if not existing_batch:
                return jsonify({
                    'success': False,
                    'error': f'Saved batch with ID {existing_saved_id} not found or access denied'
                }), 404
            
            batch_saved_id = existing_saved_id
            save_name = existing_batch[0]  # Use existing batch name
            description = existing_batch[1]  # Use existing batch description
            operation_type = "added_to_existing"
        else:
            # Create new batch
            if not save_name:
                return jsonify({
                    'success': False,
                    'error': 'Save name is required for new batches'
                }), 400
            
            batch_saved_id = get_next_batch_id()
            operation_type = "new_batch"
        
        # Get transaction data from MySQL
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        # Get main transaction data
        placeholders = ','.join(['%s'] * len(transaction_ids))
        mysql_cursor.execute(f"""
            SELECT id, uuid, participant_acq_id, participant_iss_id, msgclass, msgtype, 
                   status, dt, amount, result, refuuid, dttimeout, reversaltries, tid
            FROM transaction
            WHERE id IN ({placeholders})
        """, transaction_ids)
        
        transactions = mysql_cursor.fetchall()
        
        # Get and save transaction attributes first
        attributes_data = fetch_transaction_attributes(transaction_ids, connection)
        
        # Get participant name mapping
        participant_mapping = get_participant_name_mapping()
        
        # Save transaction details with enhanced data from attributes
        for trx in transactions:
            trx_id_str = str(trx['id'])
            trx_attributes = attributes_data.get(trx_id_str, [])
            
            # Create a mapping of field names to values from attributes
            field_mapping = {}
            for attr in trx_attributes:
                field_mapping[attr.get('field', '')] = attr.get('content', '')
            
            # Get participant names from mapping
            acq_id = trx.get('participant_acq_id')
            iss_id = trx.get('participant_iss_id')
            acquier_name = participant_mapping.get(acq_id, str(acq_id) if acq_id else '')
            issuer_name = participant_mapping.get(iss_id, str(iss_id) if iss_id else '')
            
            # Create a saved_transactions record for each transaction with the same saved_id
            sqlite_cursor.execute("""
                INSERT INTO saved_transactions (save_name, description, created_at, trx_id, uuid, user_id, saved_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (save_name, description, datetime.now().isoformat(), trx['id'], trx['uuid'], user_id, batch_saved_id))
            
            saved_transaction_id = sqlite_cursor.lastrowid
            
            # Insert transaction details
            sqlite_cursor.execute("""
                INSERT INTO saved_transaction_details (
                    saved_transaction_id, acq_id, acquier_name, amount, card_number, date_n_time,
                    date_n_time_time_out, gateway_id, gateway_name, iss_id, issuer_name,
                    msg_class, msg_class_description, msg_type, msg_type_description,
                    principal_code, procode, rc_efts, rc_host, rrn, ref_uuid, result,
                    result_desc, reversal_tries, tid, trx_id, trx_status, trx_status_description, uuid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                batch_saved_id,  # saved_transaction_id gets the batch_saved_id value
                acq_id,
                acquier_name,  # Use mapped name
                trx.get('amount'),
                field_mapping.get('IN.MASK_PAN', ''),  # Card Number
                trx.get('dt'),  # Date n Time
                trx.get('dttimeout'),  # Date n Time Time Out
                field_mapping.get('IN.GATEWAY_ID', ''),  # Gateway ID
                '',  # Gateway Name (not in attributes)
                iss_id,
                issuer_name,  # Use mapped name
                trx.get('msgclass'),  # Msg Class
                'transaction5',  # Msg Class Description (hardcoded)
                trx.get('msgtype'),  # Msg Type
                'Transfer, Transfer Reversal',  # Msg Type Description (hardcoded)
                field_mapping.get('IN.DEBIT_LEG_PRINCIPAL_CODE', ''),  # Principal Code
                field_mapping.get('IN.PROCCODE', ''),  # Procode
                '',  # RC EFTS (not in attributes)
                field_mapping.get('OR.RC_HOST', field_mapping.get('IN.RC_HOST', '')),  # RC Host - prefer OR over IN
                field_mapping.get('OR.RRN', field_mapping.get('IN.RRN', '')),  # RRN - prefer OR over IN
                trx.get('refuuid'),  # Ref UUID
                trx.get('result'),  # Result
                'std approval',  # Result Desc (hardcoded)
                trx.get('reversaltries'),  # Reversal Tries
                trx.get('tid'),  # TID
                trx['id'],  # trx_id
                trx.get('status'),  # Trx Status
                'Transaction completed',  # Trx Status Description (hardcoded)
                trx['uuid']  # uuid
            ))
        
        # Save attributes for each transaction
        for i, trx in enumerate(transactions):
            trx_id_str = str(trx['id'])
            trx_attributes = attributes_data.get(trx_id_str, [])
            current_saved_transaction_id = saved_transaction_id # Use the last saved_transaction_id
            
            for attr in trx_attributes:
                sqlite_cursor.execute("""
                    INSERT INTO saved_transaction_attributes (
                        saved_transaction_id, original_id, field, content, transaction_id
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    batch_saved_id,  # saved_transaction_id gets the batch_saved_id value
                    attr.get('id', ''), attr.get('field', ''),
                    attr.get('content', ''), attr.get('transaction_id', '')
                ))
        
        # Get and save transaction endpoints
        endpoints_data = fetch_transaction_endpoints(transactions_to_save, connection)
        for i, trx in enumerate(transactions):
            trx_uuid = trx['uuid']
            trx_endpoints = endpoints_data.get(trx_uuid, [])
            current_saved_transaction_id = saved_transaction_id # Use the last saved_transaction_id
            
            for endpoint in trx_endpoints:
                sqlite_cursor.execute("""
                    INSERT INTO saved_transaction_endpoints (
                        saved_transaction_id, original_id, dt, message, msg_direction,
                        participant_id, request_response, state, uuid, worker_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    batch_saved_id,  # saved_transaction_id gets the batch_saved_id value
                    endpoint.get('id', ''), endpoint.get('dt', ''),
                    endpoint.get('message', ''), endpoint.get('msg_direction', ''),
                    endpoint.get('participant_id', ''), endpoint.get('request_response', ''),
                    endpoint.get('state', ''), endpoint.get('uuid', ''), endpoint.get('worker_id', '')
                ))
        
        # Get and save GrayLog data (only if enabled)
        graylog_count = 0
        if save_graylog:
            print(f"[DEBUG] GrayLog saving enabled, fetching data for {len(transactions_to_save)} transactions")
            graylog_data = fetch_graylog_data(transactions_to_save, connection)
            print(f"[DEBUG] GrayLog data fetched: {len(graylog_data)} UUIDs")
            
            for i, trx in enumerate(transactions):
                trx_uuid = trx['uuid']
                trx_graylog_data = graylog_data.get(trx_uuid, {})
                trx_graylog_messages = trx_graylog_data.get('messages', [])
                current_saved_transaction_id = saved_transaction_id # Use the last saved_transaction_id
                
                print(f"[DEBUG] Processing GrayLog for UUID {trx_uuid}: {len(trx_graylog_messages)} messages")
                
                for graylog_message in trx_graylog_messages:
                    # Extract message data from the GrayLog message structure
                    message_data = graylog_message.get('message', {})
                    
                    # Get the actual message content
                    message_content = message_data.get('message', '')
                    if not message_content:
                        message_content = message_data.get('full_message', '')
                    
                    # Get timestamp
                    timestamp = message_data.get('timestamp', '')
                    
                    # Get level (severity)
                    level = message_data.get('level', '')
                    if not level:
                        level = message_data.get('Severity', '')
                    
                    # Get source
                    source = message_data.get('source', '')
                    if not source:
                        source = message_data.get('SourceModuleName', '')
                    
                    # Get transaction UUID
                    transaction_uuid = message_data.get('UUID', trx_uuid)
                    
                    print(f"[DEBUG] Saving GrayLog message: {message_content[:50]}...")
                    
                    sqlite_cursor.execute("""
                        INSERT INTO saved_transaction_graylogs (
                            saved_transaction_id, message, timestamp, level, 
                            source, uuid
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        current_saved_transaction_id,  # saved_transaction_id
                        message_content, timestamp, level, source, transaction_uuid
                    ))
                    graylog_count += 1
                    
            print(f"[DEBUG] Total GrayLog messages saved: {graylog_count}")
        
        # Commit all changes
        sqlite_conn.commit()
        
        # Close connections
        mysql_cursor.close()
        mysql_conn.close()
        sqlite_cursor.close()
        sqlite_conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Successfully {"added" if operation_type == "added_to_existing" else "saved"} {len(transactions)} transactions to {"existing batch" if operation_type == "added_to_existing" else "new batch"}' + (f' with {graylog_count} GrayLog entries' if save_graylog and graylog_count > 0 else ''),
            'data': {
                'saved_transaction_id': saved_transaction_id,
                'saved_id': batch_saved_id,
                'save_name': save_name,
                'transactions_count': len(transactions),
                'attributes_count': sum(len(attrs) for attrs in attributes_data.values()),
                'endpoints_count': sum(len(endpoints) for endpoints in endpoints_data.values()),
                'graylog_count': graylog_count,
                'graylog_enabled': save_graylog,
                'operation_type': operation_type
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to save transactions: {str(e)}'
        }), 500

@transaction_bp.route('/save/simple', methods=['GET'])
def get_saved_transactions_simple():
    """Get simple list of saved transactions grouped by saved_id for the authenticated user"""
    try:
        # Get user_id from JWT token
        user_id = get_current_user_id()
        if not user_id:
            return jsonify({
                'success': False,
                'error': 'Authentication required. Please provide valid JWT token.'
            }), 401
        
        sqlite_conn = get_sqlite_connection()
        sqlite_cursor = sqlite_conn.cursor()
        
        # Get all saved transactions for the authenticated user grouped by saved_id
        sqlite_cursor.execute("""
            SELECT 
                saved_id,
                save_name,
                description,
                MIN(created_at) as created_at,
                COUNT(*) as transaction_count,
                GROUP_CONCAT(trx_id) as trx_ids,
                GROUP_CONCAT(uuid) as uuids
            FROM saved_transactions
            WHERE user_id = ?
            GROUP BY saved_id, save_name, description
            ORDER BY MIN(created_at) DESC
        """, (user_id,))
        
        saved_transactions = []
        for row in sqlite_cursor.fetchall():
            # Split the concatenated trx_ids and uuids into lists
            trx_ids = row[5].split(',') if row[5] else []
            uuids = row[6].split(',') if row[6] else []
            
            # Create list of transaction objects
            transactions = []
            for i in range(len(trx_ids)):
                transactions.append({
                    'trx_id': trx_ids[i],
                    'uuid': uuids[i] if i < len(uuids) else ''
                })
            
            saved_transactions.append({
                'saved_id': row[0],
                'save_name': row[1],
                'description': row[2],
                'created_at': row[3],
                'transaction_count': row[4],
                'user_id': user_id,
                'transactions': transactions
            })
        
        sqlite_cursor.close()
        sqlite_conn.close()
        
        return jsonify({
            'success': True,
            'data': saved_transactions,
            'total_count': len(saved_transactions)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to get saved transactions: {str(e)}'
        }), 500

@transaction_bp.route('/save', methods=['GET'])
def get_saved_transactions():
    """Get list of saved transactions for the authenticated user with counts"""
    try:
        # Get user_id from JWT token
        user_id = get_current_user_id()
        if not user_id:
            return jsonify({
                'success': False,
                'error': 'Authentication required. Please provide valid JWT token.'
            }), 401
        
        sqlite_conn = get_sqlite_connection()
        sqlite_cursor = sqlite_conn.cursor()
        
        # Get all saved transactions for the authenticated user with counts
        sqlite_cursor.execute("""
            SELECT 
                st.id,
                st.save_name,
                st.description,
                st.created_at,
                st.trx_id,
                st.uuid,
                st.user_id,
                st.saved_id,
                COUNT(DISTINCT std.trx_id) as transaction_count,
                COUNT(DISTINCT sta.id) as attributes_count,
                COUNT(DISTINCT ste.id) as endpoints_count,
                COUNT(DISTINCT stg.id) as graylog_count
            FROM saved_transactions st
            LEFT JOIN saved_transaction_details std ON st.saved_id = std.saved_transaction_id
            LEFT JOIN saved_transaction_attributes sta ON st.saved_id = sta.saved_transaction_id
            LEFT JOIN saved_transaction_endpoints ste ON st.saved_id = ste.saved_transaction_id
            LEFT JOIN saved_transaction_graylogs stg ON st.saved_id = stg.saved_transaction_id
            WHERE st.user_id = ?
            GROUP BY st.id
            ORDER BY st.created_at DESC
        """, (user_id,))
        
        saved_transactions = []
        for row in sqlite_cursor.fetchall():
            saved_transactions.append({
                'id': row[0],
                'save_name': row[1],
                'description': row[2],
                'created_at': row[3],
                'trx_id': row[4],
                'uuid': row[5],
                'user_id': row[6],
                'saved_id': row[7],
                'transaction_count': row[8],
                'attributes_count': row[9],
                'endpoints_count': row[10],
                'graylog_count': row[11]
            })
        
        sqlite_cursor.close()
        sqlite_conn.close()
        
        return jsonify({
            'success': True,
            'data': saved_transactions,
            'user_id': user_id
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to get saved transactions: {str(e)}'
        }), 500

@transaction_bp.route('/save/<int:saved_id>', methods=['GET'])
def get_saved_transaction_details(saved_id):
    """Get detailed data for a specific saved transaction (authenticated user only)"""
    try:
        # Get user_id from JWT token
        user_id = get_current_user_id()
        if not user_id:
            return jsonify({
                'success': False,
                'error': 'Authentication required. Please provide valid JWT token.'
            }), 401
        
        sqlite_conn = get_sqlite_connection()
        sqlite_cursor = sqlite_conn.cursor()
        
        # Get saved transaction info with user_id filter
        sqlite_cursor.execute("""
            SELECT id, save_name, description, created_at, trx_id, uuid, user_id, saved_id
            FROM saved_transactions
            WHERE saved_id = ? AND user_id = ?
        """, (saved_id, user_id))
        
        saved_transaction = sqlite_cursor.fetchone()
        if not saved_transaction:
            return jsonify({
                'success': False,
                'error': 'Saved transaction not found or access denied'
            }), 404
        
        # Get transaction details
        sqlite_cursor.execute("""
            SELECT trx_id, uuid, acq_id, acquier_name, amount, card_number, date_n_time, 
                   date_n_time_time_out, gateway_id, gateway_name, iss_id, issuer_name, 
                   msg_class, msg_class_description, msg_type, msg_type_description, 
                   principal_code, procode, rc_efts, rc_host, rrn, ref_uuid, result, 
                   result_desc, reversal_tries, tid, trx_status, trx_status_description
            FROM saved_transaction_details
            WHERE saved_transaction_id = ?
        """, (str(saved_transaction[7]),))  # Convert saved_id to string
        
        transaction_details = []
        for row in sqlite_cursor.fetchall():
            transaction_details.append({
                'trx_id': row[0],
                'uuid': row[1],
                'acq_id': row[2],
                'acquier_name': row[3],
                'amount': row[4],
                'card_number': row[5],
                'date_n_time': row[6],
                'date_n_time_time_out': row[7],
                'gateway_id': row[8],
                'gateway_name': row[9],
                'iss_id': row[10],
                'issuer_name': row[11],
                'msg_class': row[12],
                'msg_class_description': row[13],
                'msg_type': row[14],
                'msg_type_description': row[15],
                'principal_code': row[16],
                'procode': row[17],
                'rc_efts': row[18],
                'rc_host': row[19],
                'rrn': row[20],
                'ref_uuid': row[21],
                'result': row[22],
                'result_desc': row[23],
                'reversal_tries': row[24],
                'tid': row[25],
                'trx_status': row[26],
                'trx_status_description': row[27]
            })
        
        # Get attributes
        sqlite_cursor.execute("""
            SELECT original_id, field, content, transaction_id
            FROM saved_transaction_attributes
            WHERE saved_transaction_id = ?
            ORDER BY transaction_id, field
        """, (str(saved_transaction[7]),))  # Convert saved_id to string
        
        attributes = []
        for row in sqlite_cursor.fetchall():
            attributes.append({
                'original_id': row[0],
                'field': row[1],
                'content': row[2],
                'transaction_id': row[3]
            })
        
        # Get endpoints
        sqlite_cursor.execute("""
            SELECT original_id, dt, message, msg_direction, participant_id, 
                   request_response, state, uuid, worker_id
            FROM saved_transaction_endpoints
            WHERE saved_transaction_id = ?
            ORDER BY dt
        """, (str(saved_transaction[7]),))  # Convert saved_id to string
        
        endpoints = []
        for row in sqlite_cursor.fetchall():
            endpoints.append({
                'original_id': row[0],
                'dt': row[1],
                'message': row[2],
                'msg_direction': row[3],
                'participant_id': row[4],
                'request_response': row[5],
                'state': row[6],
                'uuid': row[7],
                'worker_id': row[8]
            })
        
        # Get GrayLog data
        sqlite_cursor.execute("""
            SELECT message, timestamp, level, source, uuid
            FROM saved_transaction_graylogs
            WHERE saved_transaction_id = ?
            ORDER BY timestamp
        """, (str(saved_transaction[0]),))  # Use the id (saved_transaction[0]) instead of saved_id
        
        graylog_data = []
        for row in sqlite_cursor.fetchall():
            graylog_data.append({
                'message': row[0],
                'timestamp': row[1],
                'level': row[2],
                'source': row[3],
                'uuid': row[4]
            })
        
        sqlite_cursor.close()
        sqlite_conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'saved_transaction': {
                    'id': saved_transaction[0],
                    'save_name': saved_transaction[1],
                    'description': saved_transaction[2],
                    'created_at': saved_transaction[3],
                    'trx_id': saved_transaction[4],
                    'uuid': saved_transaction[5],
                    'user_id': saved_transaction[6],
                    'saved_id': saved_transaction[7]
                },
                'transaction_details': transaction_details,
                'attributes': attributes,
                'endpoints': endpoints,
                'graylog_data': graylog_data,
                'transaction_details_count': len(transaction_details),
                'attributes_count': len(attributes),
                'endpoints_count': len(endpoints),
                'graylog_count': len(graylog_data)
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to get saved transaction details: {str(e)}'
        }), 500

@transaction_bp.route('/save/<saved_id>', methods=['DELETE'])
def delete_saved_transaction_batch(saved_id):
    """Delete all data for a specific saved_id (authenticated user only)"""
    try:
        # Get user_id from JWT token
        user_id = get_current_user_id()
        if not user_id:
            return jsonify({
                'success': False,
                'error': 'Authentication required. Please provide valid JWT token.'
            }), 401
        
        sqlite_conn = get_sqlite_connection()
        sqlite_cursor = sqlite_conn.cursor()
        
        # First, check if the saved_id exists and belongs to the authenticated user
        sqlite_cursor.execute("""
            SELECT COUNT(*) as count, save_name
            FROM saved_transactions
            WHERE saved_id = ? AND user_id = ?
        """, (saved_id, user_id))
        
        result = sqlite_cursor.fetchone()
        if not result or result[0] == 0:
            return jsonify({
                'success': False,
                'error': 'Saved transaction batch not found or access denied'
            }), 404
        
        batch_name = result[1]
        transaction_count = result[0]
        
        # Get counts of related data before deletion for response
        sqlite_cursor.execute("""
            SELECT 
                COUNT(DISTINCT std.id) as details_count,
                COUNT(DISTINCT sta.id) as attributes_count,
                COUNT(DISTINCT ste.id) as endpoints_count,
                COUNT(DISTINCT stg.id) as graylog_count
            FROM saved_transactions st
            LEFT JOIN saved_transaction_details std ON st.saved_id = std.saved_transaction_id
            LEFT JOIN saved_transaction_attributes sta ON st.saved_id = sta.saved_transaction_id
            LEFT JOIN saved_transaction_endpoints ste ON st.saved_id = ste.saved_transaction_id
            LEFT JOIN saved_transaction_graylogs stg ON st.saved_id = stg.saved_transaction_id
            WHERE st.saved_id = ? AND st.user_id = ?
        """, (saved_id, user_id))
        
        counts = sqlite_cursor.fetchone()
        details_count = counts[0] if counts[0] else 0
        attributes_count = counts[1] if counts[1] else 0
        endpoints_count = counts[2] if counts[2] else 0
        graylog_count = counts[3] if counts[3] else 0
        
        # Delete all related data first (since foreign keys are disabled)
        # Delete in reverse order of dependencies
        sqlite_cursor.execute("""
            DELETE FROM saved_transaction_graylogs
            WHERE saved_transaction_id = ?
        """, (saved_id,))
        
        sqlite_cursor.execute("""
            DELETE FROM saved_transaction_endpoints
            WHERE saved_transaction_id = ?
        """, (saved_id,))
        
        sqlite_cursor.execute("""
            DELETE FROM saved_transaction_attributes
            WHERE saved_transaction_id = ?
        """, (saved_id,))
        
        sqlite_cursor.execute("""
            DELETE FROM saved_transaction_details
            WHERE saved_transaction_id = ?
        """, (saved_id,))
        
        # Finally, delete all saved_transactions records with the specified saved_id
        sqlite_cursor.execute("""
            DELETE FROM saved_transactions
            WHERE saved_id = ? AND user_id = ?
        """, (saved_id, user_id))
        
        deleted_count = sqlite_cursor.rowcount
        
        # Commit the changes
        sqlite_conn.commit()
        
        # Close connections
        sqlite_cursor.close()
        sqlite_conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Successfully deleted batch "{batch_name}" with {transaction_count} transactions',
            'data': {
                'saved_id': saved_id,
                'batch_name': batch_name,
                'transactions_deleted': transaction_count,
                'details_deleted': details_count,
                'attributes_deleted': attributes_count,
                'endpoints_deleted': endpoints_count,
                'graylog_deleted': graylog_count,
                'total_records_deleted': deleted_count
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to delete saved transaction batch: {str(e)}'
        }), 500

# Transaction Export Endpoints

@transaction_bp.route('/export/html', methods=['POST'])
def export_transaction_html():
    """Export transaction data to HTML with related data"""
    try:
        data = request.get_json()
        connection = data.get('connection', 'LOKAL')
        search = data.get('search', '')
        page = int(data.get('page', 1))
        page_size = int(data.get('page_size', 20))
        export_all = data.get('exportAll', False)
        selected_transaction_ids = data.get('selectedTransactionIds', [])  # Parameter for selected transaction IDs
        scenario_data = data.get('scenarioData', {})
        include_attributes = data.get('includeAttributes', True)
        include_endpoints = data.get('includeEndpoints', True)
        include_graylog = data.get('includeGrayLog', True)
        date_start = data.get('dateStart', '')
        date_end = data.get('dateEnd', '')
        
        # Build SQL query - using the actual transaction table structure
        sql = """
            SELECT id, uuid, participant_acq_id, participant_iss_id, msgclass, msgtype, 
                   status, dt, amount, result, refuuid, dttimeout, reversaltries, tid
            FROM transaction
        """
        
        where_clause = ""
        params = []
        
        # Handle selected transaction IDs export
        if selected_transaction_ids and len(selected_transaction_ids) > 0:
            # Export specific transaction IDs
            placeholders = ','.join(['%s'] * len(selected_transaction_ids))
            where_clause = f" WHERE id IN ({placeholders})"
            params = selected_transaction_ids
        elif search:
            # Search functionality
            where_clause = " WHERE uuid LIKE %s OR tid LIKE %s OR refuuid LIKE %s"
            params = [f'%{search}%', f'%{search}%', f'%{search}%']
        
        sql += where_clause
        sql += " ORDER BY dt DESC"
        
        # Add pagination only if not exporting selected transaction IDs and not exporting all
        if not selected_transaction_ids and not export_all:
            offset = (page - 1) * page_size
            sql += " LIMIT %s OFFSET %s"
            params.extend([page_size, offset])
        
        # Execute query
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        mysql_cursor.execute(sql, params)
        transactions = mysql_cursor.fetchall()
        
        mysql_cursor.close()
        mysql_conn.close()
        
        # Get additional data if requested - using API endpoints
        attributes_data_dict = {}
        endpoint_data_dict = {}
        graylog_data_dict = {}
        
        if transactions:
            # Extract transaction IDs and UUIDs
            transaction_ids = [t['id'] for t in transactions]
            
            # Get transaction attributes using API
            if include_attributes:
                attributes_data_dict = fetch_transaction_attributes(transaction_ids, connection)
            
            # Get endpoint data using API
            if include_endpoints:
                # Convert transactions to the format expected by fetch_transaction_endpoints
                transactions_for_endpoints = []
                for transaction in transactions:
                    transactions_for_endpoints.append({
                        'trx_id': transaction.get('id'),
                        'uuid': transaction.get('uuid')
                    })
                transaction_endpoints_by_uuid = fetch_transaction_endpoints(transactions_for_endpoints, connection)
                
                # Convert from UUID-grouped to transaction ID-grouped for template
                for transaction in transactions:
                    trx_id = str(transaction.get('id'))
                    uuid_key = transaction.get('uuid')
                    if uuid_key and uuid_key in transaction_endpoints_by_uuid:
                        endpoint_data_dict[trx_id] = transaction_endpoints_by_uuid[uuid_key]
            
            # Get GrayLog data using fetch_graylog_data
            if include_graylog:
                # Convert transactions to the format expected by fetch_graylog_data
                transactions_for_graylog = []
                for transaction in transactions:
                    transactions_for_graylog.append({
                        'trx_id': transaction.get('id'),
                        'uuid': transaction.get('uuid')
                    })
                graylog_data_by_uuid = fetch_graylog_data(transactions_for_graylog, connection, date_start, date_end)
                
                # Convert from UUID-grouped to transaction ID-grouped for template
                for transaction in transactions:
                    trx_id = str(transaction.get('id'))
                    uuid_key = transaction.get('uuid')
                    if uuid_key and uuid_key in graylog_data_by_uuid:
                        graylog_data_dict[trx_id] = graylog_data_by_uuid[uuid_key].get('messages', [])
        
        # Get plugin data
        plugin_data = get_plugin_data(connection)
        
        # Generate HTML using the rna_saved_transaction.html template
        html_content = generate_transaction_html_template(
            transactions, scenario_data, connection, search, page, page_size, export_all,
            include_attributes, include_endpoints, include_graylog, date_start, date_end,
            attributes_data_dict, endpoint_data_dict, graylog_data_dict, plugin_data
        )
        
        # Generate filename with current date
        current_date = datetime.now().strftime('%Y-%m-%d')
        filename = f"transaction_export_{current_date}.html"
        
        return html_content, 200, {
            'Content-Type': 'text/html',
            'Content-Disposition': f'attachment; filename="{filename}"'
        }
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to export transactions: {str(e)}'
        }), 500

def generate_transaction_html_template(transactions, scenario_data, connection, search, page, page_size, export_all, 
                                     include_attributes=True, include_endpoints=True, include_graylog=True,
                                     date_start=None, date_end=None, attributes_data=None, endpoint_data=None, graylog_data=None, plugin_data=None):
    """Generate HTML content for transaction export using html-export.html template"""
    
    # Format transactions data to match template expectations
    columns = ['ID', 'UUID', 'Participant Acq ID', 'Participant Iss ID', 'Message Class', 'Message Type', 
               'Status', 'Date/Time', 'Amount', 'Result', 'Reference UUID', 'Timeout', 'Reversal Tries', 'TID']
    
    rows = []
    transaction_ids = []
    
    for transaction in transactions:
        row = []
        transaction_id = transaction.get('id', '')
        transaction_ids.append(transaction_id)
        
        # Format each field
        row.append(str(transaction.get('id', '')))
        row.append(str(transaction.get('uuid', '')))
        row.append(str(transaction.get('participant_acq_id', '')))
        row.append(str(transaction.get('participant_iss_id', '')))
        row.append(str(transaction.get('msgclass', '')))
        row.append(str(transaction.get('msgtype', '')))
        row.append(str(transaction.get('status', '')))
        
        # Format date/time
        dt = transaction.get('dt', '')
        if dt:
            try:
                dt_formatted = dt.strftime('%Y-%m-%d %H:%M:%S') if hasattr(dt, 'strftime') else str(dt)
            except:
                dt_formatted = str(dt)
        else:
            dt_formatted = ''
        row.append(dt_formatted)
        
        # Format amount
        amount = transaction.get('amount', 0)
        if amount:
            try:
                amount_cents = float(amount)
                amount_formatted = "{:,.2f}".format(amount_cents / 100)
            except:
                amount_formatted = str(amount)
        else:
            amount_formatted = "0.00"
        row.append(amount_formatted)
        
        row.append(str(transaction.get('result', '')))
        row.append(str(transaction.get('refuuid', '')))
        
        # Format timeout
        dttimeout = transaction.get('dttimeout', '')
        if dttimeout:
            try:
                timeout_formatted = dttimeout.strftime('%Y-%m-%d %H:%M:%S') if hasattr(dttimeout, 'strftime') else str(dttimeout)
            except:
                timeout_formatted = str(dttimeout)
        else:
            timeout_formatted = ''
        row.append(timeout_formatted)
        
        row.append(str(transaction.get('reversaltries', '')))
        row.append(str(transaction.get('tid', '')))
        
        rows.append(row)
    
    # Get transaction IDs
    transaction_ids = [str(t.get('id', '')) for t in transactions]
    
    # Use passed data if available, otherwise fetch it
    transaction_attributes = attributes_data if attributes_data is not None else {}
    transaction_endpoints = endpoint_data if endpoint_data is not None else {}
    graylog_data_by_trx = graylog_data if graylog_data is not None else {}
    
    # If data was not passed, fetch it (fallback for backward compatibility)
    if not transaction_attributes and include_attributes:
        transaction_attributes = fetch_transaction_attributes(transaction_ids, connection)
    
    if not transaction_endpoints and include_endpoints:
        # Convert transactions to the format expected by fetch_transaction_endpoints
        transactions_for_endpoints = []
        for transaction in transactions:
            transactions_for_endpoints.append({
                'trx_id': transaction.get('id'),
                'uuid': transaction.get('uuid')
            })
        transaction_endpoints_by_uuid = fetch_transaction_endpoints(transactions_for_endpoints, connection)
        
        # Convert from UUID-grouped to transaction ID-grouped for template
        transaction_endpoints = {}
        for transaction in transactions:
            trx_id = str(transaction.get('id'))
            uuid = transaction.get('uuid')
            if uuid and uuid in transaction_endpoints_by_uuid:
                transaction_endpoints[trx_id] = transaction_endpoints_by_uuid[uuid]
    
    if not graylog_data_by_trx and include_graylog:
        # Convert transactions to the format expected by fetch_graylog_data
        transactions_for_graylog = []
        for transaction in transactions:
            transactions_for_graylog.append({
                'trx_id': transaction.get('id'),
                'uuid': transaction.get('uuid')
            })
        graylog_data_by_uuid = fetch_graylog_data(transactions_for_graylog, connection, date_start, date_end)
        
        # Convert from UUID-grouped to transaction ID-grouped for template
        graylog_data_by_trx = {}
        for transaction in transactions:
            trx_id = str(transaction.get('id'))
            uuid = transaction.get('uuid')
            if uuid and uuid in graylog_data_by_uuid:
                graylog_data_by_trx[trx_id] = graylog_data_by_uuid[uuid].get('messages', [])
    
    # Get GrayLog rules for template
    graylog_rules = []
    if include_graylog:
        from app.routes.graylog.graylog_data import get_graylog_rules
        graylog_rules = get_graylog_rules()
    
    # Prepare data for template
    template_data = {
        'scenario_testing': {
            'title': scenario_data.get('title', 'Transaction Export'),
            'scenario': scenario_data.get('scenarioTesting', ''),
            'expected_result': scenario_data.get('expectedResult', ''),
            'actual_result': scenario_data.get('actualResult', '')
        },
        'export_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'export_all': export_all,
        'include_graylog': bool(graylog_data_by_trx),
        'columns': columns,
        'rows': rows,
        'transaction_attributes': transaction_attributes,
        'transaction_endpoints': transaction_endpoints,
        'graylog_data': graylog_data_by_trx,
        'graylog_rules': graylog_rules, # Add GrayLog rules to template data
        'plugin_data': plugin_data or [] # Add plugin data to template data
    }
    
    try:
        # Read the template file directly from app/tmp
        # Get the app directory (3 levels up from this file: routes/transaction/transaction.py -> routes -> app)
        app_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        template_path = os.path.join(app_dir, 'tmp', 'html-export.html')
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()
        
        # Use Jinja2 template rendering
        from jinja2 import Template
        
        # Create Jinja2 template and render with data
        template = Template(template_content)
        html_content = template.render(**template_data)
        
        return html_content
    except Exception as e:
        print(f"Error generating HTML: {e}")
        return f"<html><body><h1>Error generating HTML</h1><p>{str(e)}</p></body></html>"

# CORS and OPTIONS handlers
@transaction_bp.route('/export/selected', methods=['POST'])
def export_selected_transactions():
    """Export selected transactions by transaction IDs"""
    try:
        data = request.get_json()
        connection = data.get('connection', 'LOKAL')
        selected_transaction_ids = data.get('selectedTransactionIds', [])
        scenario_data = data.get('scenarioData', {})
        include_attributes = data.get('includeAttributes', True)
        include_endpoints = data.get('includeEndpoints', True)
        include_graylog = data.get('includeGrayLog', True)
        date_start = data.get('dateStart', '')
        date_end = data.get('dateEnd', '')
        
        if not selected_transaction_ids or len(selected_transaction_ids) == 0:
            return jsonify({
                'success': False,
                'error': 'No transaction IDs selected for export'
            }), 400
        
        # Build SQL query for selected transaction IDs
        sql = """
            SELECT id, uuid, participant_acq_id, participant_iss_id, msgclass, msgtype, 
                   status, dt, amount, result, refuuid, dttimeout, reversaltries, tid
            FROM transaction
            WHERE id IN ({})
            ORDER BY dt DESC
        """.format(','.join(['%s'] * len(selected_transaction_ids)))
        
        # Execute query
        mysql_conn = get_mysql_connection(connection)
        mysql_cursor = mysql_conn.cursor()
        
        mysql_cursor.execute(sql, selected_transaction_ids)
        transactions = mysql_cursor.fetchall()
        
        mysql_cursor.close()
        mysql_conn.close()
        
        if not transactions:
            return jsonify({
                'success': False,
                'error': f'No transactions found for the selected transaction IDs: {selected_transaction_ids}'
            }), 404
        
        # Get additional data if requested
        attributes_data = []
        endpoint_data = []
        graylog_data = []
        
        # Extract transaction IDs and UUIDs
        transaction_ids = [t['id'] for t in transactions]
        uuids = [t['uuid'] for t in transactions]
        
        # Get transaction attributes
        if include_attributes:
            attributes_data = get_transaction_attributes(transaction_ids, connection)
        
        # Get endpoint data
        if include_endpoints:
            endpoint_data = get_transaction_endpoint_data(uuids, connection)
        
        # Get GrayLog data
        if include_graylog:
            graylog_data = get_graylog_data(uuids, connection, date_start, date_end)
        
        # Get plugin data
        plugin_data = get_plugin_data(connection)
        
        # Generate HTML using the rna_saved_transaction.html template
        html_content = generate_transaction_html_template(
            transactions, scenario_data, connection, '', 1, len(transactions), False,
            include_attributes, include_endpoints, include_graylog, date_start, date_end,
            attributes_data, endpoint_data, graylog_data, plugin_data
        )
        
        # Generate filename with current date
        current_date = datetime.now().strftime('%Y-%m-%d')
        filename = f"transaction_export_{current_date}.html"
        
        return html_content, 200, {
            'Content-Type': 'text/html',
            'Content-Disposition': f'attachment; filename="{filename}"'
        }
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to export selected transactions: {str(e)}'
        }), 500

@transaction_bp.route('/', methods=['OPTIONS'])
@transaction_bp.route('/<path:path>', methods=['OPTIONS'])
def handle_options(path=None):
    """Handle CORS preflight requests"""
    response = jsonify({'status': 'ok'})
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

def fetch_transaction_data(transaction_ids, connection):
    """Fetch complete transaction data using RNA API"""
    try:
        if not transaction_ids:
            return {}
        
        result = {}
        for trx_id in transaction_ids:
            # Call the RNA API for complete transaction data
            url = f"http://localhost:3005/api/rna/transactions/transactions/{trx_id}?profileId={connection}"
            response = requests.get(url)
            
            if response.status_code == 200:
                data = response.json()
                transaction_data = data.get('transaction', {})
                
                # Group by transaction ID
                trx_id_str = str(trx_id)
                result[trx_id_str] = transaction_data
        
        return result
    except Exception as e:
        print(f"Error fetching transaction data: {e}")
        return {}

def get_participant_name_mapping():
    """Map participant IDs to their full names based on the JSON structure"""
    return {
        # Acquirer mappings
        1705: "EFTS - BRI",
        1702: "009",  # From previous test
        # Issuer mappings  
        1806: "ECP - BRI",
        1709: "1709",  # From previous test
        984: "984",    # From previous test
    }
