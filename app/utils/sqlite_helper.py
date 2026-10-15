import os
import sqlite3
import json
from datetime import datetime

# Path to the SQLite database
SQLITE_DB_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'instance', 'itmcore_db.db')

def get_connection():
    """Get a connection to the SQLite database, creating it if needed"""
    initialize_db()
    return sqlite3.connect(SQLITE_DB_FILE)

def initialize_db():
    """Create the SQLite database and tables if they don't exist"""
    # Check if database file exists
    if not os.path.exists(SQLITE_DB_FILE):
        # Create the database
        conn = sqlite3.connect(SQLITE_DB_FILE)
        cursor = conn.cursor()
        
        # Create reference tables
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bin_gateway (
                id TEXT PRIMARY KEY,
                name TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversion (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type_id INTEGER,
                plugin_id INTEGER,
                mapfrom TEXT,
                mapto TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS participant (
                id INTEGER PRIMARY KEY,
                name TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cfg_msgclass (
                id INTEGER PRIMARY KEY,
                description TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cfg_msgtype (
                id INTEGER PRIMARY KEY,
                description TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transaction_status (
                id INTEGER PRIMARY KEY,
                description TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transaction_result (
                id INTEGER PRIMARY KEY,
                description TEXT
            )
        ''')
        
        conn.commit()
        conn.close()

def get_gateway_name(gateway_id):
    """Get gateway name from bin_gateway table"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM bin_gateway WHERE id = ?", (gateway_id,))
        result = cursor.fetchone()
        return result[0] if result else "Unknown"
    except Exception as e:
        print(f"Error getting gateway name: {e}")
        return "Error"
    finally:
        if conn:
            conn.close()

def get_result_description(result_id):
    """Get result description from transaction_result table"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT description FROM transaction_result WHERE id = ?", (result_id,))
        result = cursor.fetchone()
        return result[0] if result else "Unknown"
    except Exception as e:
        print(f"Error getting result description: {e}")
        return "Error"
    finally:
        if conn:
            conn.close()

def get_rc_efts(result_id, plugin_id=505):
    """Get RC EFTS from conversion table"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT mapto FROM conversion WHERE type_id = 10007 AND plugin_id = ? AND mapfrom = ?", 
            (plugin_id, str(result_id))
        )
        result = cursor.fetchone()
        return result[0] if result else "Unknown"
    except Exception as e:
        print(f"Error getting RC EFTS: {e}")
        return "Error"
    finally:
        if conn:
            conn.close()

def get_participant_name(participant_id):
    """Get participant name from participant table"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM participant WHERE id = ?", (participant_id,))
        result = cursor.fetchone()
        return result[0] if result else "Unknown"
    except Exception as e:
        print(f"Error getting participant name: {e}")
        return "Error"
    finally:
        if conn:
            conn.close()

def get_msgclass_description(msgclass_id):
    """Get message class description from cfg_msgclass table"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT description FROM cfg_msgclass WHERE id = ?", (msgclass_id,))
        result = cursor.fetchone()
        return result[0] if result else "Unknown"
    except Exception as e:
        print(f"Error getting message class description: {e}")
        return "Error"
    finally:
        if conn:
            conn.close()

def get_msgtype_description(msgtype_id):
    """Get message type description from cfg_msgtype table"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT description FROM cfg_msgtype WHERE id = ?", (msgtype_id,))
        result = cursor.fetchone()
        return result[0] if result else "Unknown"
    except Exception as e:
        print(f"Error getting message type description: {e}")
        return "Error"
    finally:
        if conn:
            conn.close()

def get_transaction_status_description(status_id):
    """Get transaction status description from transaction_status table"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT description FROM transaction_status WHERE id = ?", (status_id,))
        result = cursor.fetchone()
        return result[0] if result else "Unknown"
    except Exception as e:
        print(f"Error getting transaction status description: {e}")
        return "Error"
    finally:
        if conn:
            conn.close()

def batch_get_references(ids_dict):
    """Efficiently retrieve multiple reference values in a batch
    
    Args:
        ids_dict: Dictionary with table names as keys and lists of IDs as values
        
    Returns:
        Dictionary with table names as keys and dictionaries of ID->value mappings as values
    """
    conn = None
    results = {}
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Debug: Print incoming IDs
        print("Batch References - Input IDs:")
        for table, ids in ids_dict.items():
            print(f"  {table}: {ids}")
        
        # Process each table
        table_queries = {
            'bin_gateway': ("SELECT id, name FROM bin_gateway WHERE id IN ({placeholders})", 'id'),
            'participant': ("SELECT id, name FROM participant WHERE id IN ({placeholders})", 'id'),
            'transaction_result': ("SELECT id, description FROM transaction_result WHERE id IN ({placeholders})", 'id'),
            'cfg_msgclass': ("SELECT id, description FROM cfg_msgclass WHERE id IN ({placeholders})", 'id'),
            'cfg_msgtype': ("SELECT id, description FROM cfg_msgtype WHERE id IN ({placeholders})", 'id'),
            'transaction_status': ("SELECT id, description FROM transaction_status WHERE id IN ({placeholders})", 'id')
        }
        
        for table, ids in ids_dict.items():
            if not ids or table not in table_queries:
                continue
                
            # Deduplicate IDs
            unique_ids = list(set(ids))
            if not unique_ids:
                continue
                
            query_template, id_field = table_queries[table]
            placeholders = ','.join(['?'] * len(unique_ids))
            query = query_template.format(placeholders=placeholders)
            
            # Debug: Print query and params
            print(f"Query for {table}: {query}")
            print(f"Params: {unique_ids}")
            
            cursor.execute(query, unique_ids)
            rows = cursor.fetchall()
            
            # Create mapping of ID to value
            results[table] = {str(row[0]): row[1] for row in rows}
            
            # Debug: Print results
            print(f"Results for {table}: {results[table]}")
            
        # Special handling for conversion table
        if 'conversion' in ids_dict and ids_dict['conversion']:
            unique_ids = list(set(ids_dict['conversion']))
            if unique_ids:
                placeholders = ','.join(['?'] * len(unique_ids))
                query = f"SELECT mapfrom, mapto FROM conversion WHERE type_id = 10007 AND plugin_id = 505 AND mapfrom IN ({placeholders})"
                
                cursor.execute(query, unique_ids)
                rows = cursor.fetchall()
                
                results['conversion'] = {str(row[0]): row[1] for row in rows}
                
                # Debug: Print conversion results
                print(f"Conversion results: {results['conversion']}")
        
        return results
    except Exception as e:
        print(f"Error in batch_get_references: {e}")
        return {}
    finally:
        if conn:
            conn.close()

def enrich_transactions_with_sqlite_data(transactions):
    """Enrich transaction data with reference data from SQLite
    
    Args:
        transactions: List of transaction dictionaries
        
    Returns:
        List of enriched transaction dictionaries
    """
    if not transactions:
        return transactions
    
    # Collect all IDs that need to be looked up
    lookup_ids = {
        'bin_gateway': [],
        'participant': [],
        'transaction_result': [],
        'cfg_msgclass': [],
        'cfg_msgtype': [],
        'transaction_status': [],
        'conversion': []
    }
    
    for trx in transactions:
        # Gateway ID
        gateway_id = trx.get('Gateway ID')
        if gateway_id is not None:
            lookup_ids['bin_gateway'].append(gateway_id)
            
        # Participants
        acq_id = trx.get('Acq ID')
        if acq_id is not None:
            lookup_ids['participant'].append(acq_id)
            
        iss_id = trx.get('Iss ID')
        if iss_id is not None:
            lookup_ids['participant'].append(iss_id)
            
        # Result
        result = trx.get('Result')
        # Make sure Result is processed correctly, including 0
        print(f"Processing Result ID: {result}, type: {type(result)}")
        # Explicitly handle all result values, including 0
        if result is not None:
            try:
                # Convert to integer to normalize
                if isinstance(result, str) and result.strip():
                    result_int = int(result)
                    lookup_ids['transaction_result'].append(result_int)
                    lookup_ids['conversion'].append(str(result_int))
                else:
                    lookup_ids['transaction_result'].append(result)
                    lookup_ids['conversion'].append(str(result))
                print(f"Added Result ID: {result} to lookup_ids")
            except (ValueError, TypeError) as e:
                print(f"Error converting Result ID: {result}, Error: {e}")
                # Still try to use original value
                lookup_ids['transaction_result'].append(result)
                lookup_ids['conversion'].append(str(result))
        else:
            print(f"Warning: Result is None for transaction: {trx.get('Trx ID', 'Unknown')}")
            
        # Message Class
        msg_class = trx.get('Msg Class')
        if msg_class is not None:
            lookup_ids['cfg_msgclass'].append(msg_class)
            
        # Message Type
        msg_type = trx.get('Msg Type')
        if msg_type is not None:
            lookup_ids['cfg_msgtype'].append(msg_type)
            
        # Transaction Status
        status = trx.get('Trx Status')
        if status is not None:
            lookup_ids['transaction_status'].append(status)
    
    # Batch retrieve all reference data
    reference_data = batch_get_references(lookup_ids)
    
    # Enrich transactions with reference data
    enriched_transactions = []
    for trx in transactions:
        enriched_trx = dict(trx)  # Create a copy
        
        # Add Gateway Name
        gateway_id = trx.get('Gateway ID')
        if gateway_id is not None and 'bin_gateway' in reference_data:
            enriched_trx['Gateway Name'] = reference_data['bin_gateway'].get(gateway_id, '')
            
        # Add Acquirer Name (only if not already present from MySQL)
        acq_id = trx.get('Acq ID')
        if acq_id is not None and 'participant' in reference_data and not trx.get('Acquier Name'):
            enriched_trx['Acquier Name'] = reference_data['participant'].get(str(acq_id), '')
            
        # Add Issuer Name (only if not already present from MySQL)
        iss_id = trx.get('Iss ID')
        if iss_id is not None and 'participant' in reference_data and not trx.get('Issuer Name'):
            enriched_trx['Issuer Name'] = reference_data['participant'].get(str(iss_id), '')
            
        # Add Result Description (only if not already present from MySQL)
        result = trx.get('Result')
        print(f"Enriching Result Description for {trx.get('Trx ID')}: result={result}")
        if result is not None and 'transaction_result' in reference_data and not trx.get('Result Desc'):
            # Debug: Check if result exists in reference data
            result_str = str(result)
            if result_str in reference_data['transaction_result']:
                description = reference_data['transaction_result'].get(result_str)
                print(f"Found description for result {result}: '{description}'")
                enriched_trx['Result Desc'] = description
            else:
                print(f"Warning: Result {result} not found in reference data")
                print(f"Available keys: {list(reference_data['transaction_result'].keys())}")
                enriched_trx['Result Desc'] = ''
        else:
            print(f"Cannot add Result Description: result={result}, 'transaction_result' in reference_data={('transaction_result' in reference_data)}")
            
        # Add RC EFTS
        if result is not None and 'conversion' in reference_data:
            enriched_trx['RC EFTS'] = reference_data['conversion'].get(str(result), '')
            
        # Add Message Class Description (only if not already present from MySQL)
        msg_class = trx.get('Msg Class')
        if msg_class is not None and 'cfg_msgclass' in reference_data and not trx.get('Msg Class Description'):
            enriched_trx['Msg Class Description'] = reference_data['cfg_msgclass'].get(str(msg_class), '')
            
        # Add Message Type Description (only if not already present from MySQL)
        msg_type = trx.get('Msg Type')
        if msg_type is not None and 'cfg_msgtype' in reference_data and not trx.get('Msg Type Description'):
            enriched_trx['Msg Type Description'] = reference_data['cfg_msgtype'].get(str(msg_type), '')
            
        # Add Transaction Status Description (only if not already present from MySQL)
        status = trx.get('Trx Status')
        if status is not None and 'transaction_status' in reference_data and not trx.get('Trx Status Description'):
            enriched_trx['Trx Status Description'] = reference_data['transaction_status'].get(str(status), '')
            
        enriched_transactions.append(enriched_trx)
    
    return enriched_transactions 