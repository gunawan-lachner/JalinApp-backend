import os
import sqlite3
import json
import requests
from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
import urllib3
from urllib.parse import urlencode
import traceback
import re

# Disable SSL warnings for development
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Create Blueprint
graylog_data_bp = Blueprint('graylog_data', __name__)

# Database paths
CONFIG_DB_PATH = os.path.join('instance', 'myapp_db.db')

def get_db_connection():
    """Get a connection to the SQLite database"""
    conn = sqlite3.connect(CONFIG_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_graylog_config(connection_name=None):
    """Get GrayLog configuration from database based on connection name"""
    # Default values in case database access fails
    config = {
        'GRAYLOG_BASE_URL': 'http://10.132.6.4:9000',
        'GRAYLOG_USERNAME': 'admindev',
        'GRAYLOG_PASSWORD': 'J@lin2021'
    }
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if connection_name:
            # Get connection category from database
            cursor.execute(
                "SELECT category FROM connection WHERE name = ?",
                (connection_name,)
            )
            
            conn_row = cursor.fetchone()
            if conn_row:
                category = conn_row['category'].lower() if conn_row['category'] else ''
                print(f"Found category for connection {connection_name}: {category}")
            else:
                category = ''
                print(f"Connection {connection_name} not found, using default config")
        else:
            category = ''
            print("No connection name provided, using default config")
        
        # Determine which configuration to use based on connection category
        # Map connection categories to GrayLog configurations
        if any(prod_keyword in category for prod_keyword in ['production', 'prod']):
            param_name = "GRAYLOG_PRODUCTION"
            print(f"Using GRAYLOG_PRODUCTION configuration for category: {category}")
        else:
            param_name = "GRAYLOG_DEVELOPMENT"
            print(f"Using GRAYLOG_DEVELOPMENT configuration for category: {category}")
        
        # Get GrayLog configuration from parameters table
        cursor.execute('''
            SELECT field_name, content_value 
            FROM parameters 
            WHERE parameter_name = ? AND field_name LIKE 'GRAYLOG_%'
        ''', (param_name,))
        
        rows = cursor.fetchall()
        
        if rows:
            for row in rows:
                field_name = row[0]  # e.g., GRAYLOG_BASE_URL
                content_value = row[1]  # actual value
                
                # Map field names to config keys
                if field_name == 'GRAYLOG_BASE_URL':
                    config['GRAYLOG_BASE_URL'] = content_value
                elif field_name == 'GRAYLOG_USERNAME':
                    config['GRAYLOG_USERNAME'] = content_value
                elif field_name == 'GRAYLOG_PASSWORD':
                    config['GRAYLOG_PASSWORD'] = content_value
            
            print(f"Loaded GrayLog config for {param_name}: {config['GRAYLOG_BASE_URL']}")
        else:
            print(f"No GrayLog configuration found for {param_name}, using defaults")
        
        conn.close()
        
    except Exception as e:
        print(f"Error loading GrayLog configuration: {e}")
        # Fall back to default values defined above
    
    return config

def get_graylog_rules():
    """Fetch all active GrayLog rules from the database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, key_word, action, active, rules_mode, enable_link, url, extra_attributes, description
            FROM graylog_rules 
            WHERE active = 1
            ORDER BY id
        ''')
        
        rules = []
        for row in cursor.fetchall():
            rule = {
                'id': row[0],
                'key_word': row[1],
                'action': row[2],
                'active': row[3],
                'rules_mode': row[4],
                'enable_link': row[5],
                'url': row[6],
                'extra_attributes': json.loads(row[7]) if row[7] else {},
                'description': row[8]
            }
            rules.append(rule)
        
        conn.close()
        return rules
    except Exception as e:
        print(f"Error fetching GrayLog rules: {e}")
        return []

def apply_rules_to_message(message_text, rules):
    """Apply GrayLog rules to a message and return matching rules with styling"""
    matching_rules = []
    
    for rule in rules:
        if not rule['active']:
            continue
            
        key_word = rule['key_word']
        rules_mode = rule.get('rules_mode', 'all')
        
        try:
            # Check if the rule matches the message
            if rules_mode == 'matches':
                # Use regex matching
                if re.search(key_word, message_text, re.IGNORECASE):
                    matching_rules.append({
                        'rule_id': rule['id'],
                        'key_word': rule['key_word'],
                        'action': rule['action'],
                        'url': rule['url']
                    })
            else:
                # Use simple string matching (default 'all' mode)
                # Split the key_word by | for OR conditions
                keywords = key_word.split('|')
                for keyword in keywords:
                    keyword = keyword.strip()
                    if keyword.lower() in message_text.lower():
                        matching_rules.append({
                            'rule_id': rule['id'],
                            'key_word': rule['key_word'],
                            'action': rule['action'],
                            'url': rule['url']
                        })
                        break  # Found a match, no need to check other keywords
        except re.error as e:
            print(f"Regex error in rule {rule['id']}: {e}")
            # Fallback to simple string matching
            keywords = key_word.split('|')
            for keyword in keywords:
                keyword = keyword.strip()
                if keyword.lower() in message_text.lower():
                    matching_rules.append({
                        'rule_id': rule['id'],
                        'key_word': rule['key_word'],
                        'action': rule['action'],
                        'url': rule['url']
                    })
                    break
    
    return matching_rules

def enhance_message_with_rules(message, rules):
    """Enhance a message with rule matching information"""
    message_data = message.get('message', {})
    message_text = message_data.get('message', '')
    full_message = message_data.get('full_message', '')
    
    # Check both message and full_message against rules
    matching_rules = apply_rules_to_message(message_text, rules)
    if not matching_rules:
        matching_rules = apply_rules_to_message(full_message, rules)
    
    # Add rule information to the message
    enhanced_message = message.copy()
    
    # Add has_rules flag only
    if matching_rules:
        enhanced_message['has_rules'] = True
    else:
        enhanced_message['has_rules'] = False
    
    return enhanced_message

@graylog_data_bp.route('/', methods=['GET'])
def get_graylog_data():
    """Get GrayLog data for a specific UUID"""
    try:
        # Get parameters
        uuid = request.args.get('uuid')
        connection = request.args.get('connection', 'LOKAL')
        days_back = request.args.get('days_back', 2, type=int)
        limit = request.args.get('limit', 100, type=int)
        include_rules = request.args.get('include_rules', 'true').lower() == 'true'
        
        if not uuid:
            return jsonify({
                'success': False,
                'error': 'UUID parameter is required'
            }), 400
        
        # Calculate time range in UTC
        to_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        from_time = (datetime.utcnow() - timedelta(days=days_back)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        print(f"[DEBUG] Searching GrayLog for UUID: {uuid}")
        print(f"[DEBUG] Time range (UTC): {from_time} to {to_time}")
        print(f"[DEBUG] Connection: {connection}")
        print(f"[DEBUG] Include rules: {include_rules}")
        
        # Get GrayLog configuration
        graylog_config = get_graylog_config(connection)
        
        # Fetch GrayLog rules if requested
        rules = []
        if include_rules:
            rules = get_graylog_rules()
            print(f"[DEBUG] Loaded {len(rules)} active rules")
        
        # Construct GrayLog API URL
        url = f"{graylog_config['GRAYLOG_BASE_URL']}/api/search/universal/absolute"
        
        # Try multiple query formats for UUID search
        queries = [
            f'UUID:"{uuid}"',  # Standard UUID field search
            f'Message UUID: {uuid}',  # Message UUID format
            f'uuid:{uuid}',  # Lowercase uuid field
            f'"{uuid}"'  # Simple string search
        ]
        
        all_messages = []
        successful_queries = []
        
        for query in queries:
            try:
                # Set up the query parameters
                params = {
                    'query': query,
                    'from': from_time,
                    'to': to_time,
                    'decorate': 'true',
                    'limit': limit
                }
                
                # Set up headers with Basic Authentication
                headers = {
                    'Accept': 'application/json'
                }
                
                # Determine if SSL verification should be disabled
                verify_ssl = False if graylog_config['GRAYLOG_BASE_URL'].startswith('https://') else True
                
                # Print full URL with parameters for debugging
                full_url = f"{url}?{urlencode(params)}"
                print(f"[DEBUG] Trying query: {query}")
                print(f"[DEBUG] Full Graylog URL: {full_url}")
                
                # Make request to GrayLog using Basic Auth
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    auth=(graylog_config['GRAYLOG_USERNAME'], graylog_config['GRAYLOG_PASSWORD']),
                    verify=verify_ssl,
                    timeout=30
                )
                
                print(f"[DEBUG] Response status for query '{query}': {response.status_code}")
                
                if response.ok:
                    response_data = response.json()
                    messages = response_data.get('messages', [])
                    
                    if messages:
                        all_messages.extend(messages)
                        successful_queries.append(query)
                        print(f"[DEBUG] Found {len(messages)} messages with query: {query}")
                        # Don't break - collect all messages from all successful queries
                    else:
                        print(f"[DEBUG] No messages found with query: {query}")
                else:
                    print(f"[DEBUG] Error response for query '{query}': {response.text}")
                    
            except Exception as e:
                print(f"[DEBUG] Error with query '{query}': {str(e)}")
                continue
        
        # Remove duplicates based on message ID
        unique_messages = []
        seen_ids = set()
        for message in all_messages:
            # Access the message ID from the nested message structure
            message_data = message.get('message', {})
            message_id = message_data.get('_id') or message_data.get('gl2_message_id')
            if message_id and message_id not in seen_ids:
                unique_messages.append(message)
                seen_ids.add(message_id)
            elif not message_id:
                # If no message ID found, include the message anyway
                unique_messages.append(message)
        
        # Apply rules to messages if requested
        enhanced_messages = []
        messages_with_rules = 0
        
        for message in unique_messages:
            if include_rules and rules:
                enhanced_message = enhance_message_with_rules(message, rules)
                if enhanced_message['has_rules']:
                    messages_with_rules += 1
                enhanced_messages.append(enhanced_message)
            else:
                enhanced_messages.append(message)
        
        print(f"[DEBUG] Total unique messages found: {len(enhanced_messages)}")
        if include_rules:
            print(f"[DEBUG] Messages with matching rules: {messages_with_rules}")
        
        return jsonify({
            'success': True,
            'data': {
                'uuid': uuid,
                'connection': connection,
                'rules': rules if include_rules else [],
                'time_range': {
                    'from': from_time,
                    'to': to_time,
                    'days_back': days_back
                },
                'total_messages': len(enhanced_messages),
                'messages': enhanced_messages,
                'successful_queries': successful_queries,
                'search_info': {
                    'queries_tried': queries,
                    'successful_queries': successful_queries,
                    'total_raw_messages': len(all_messages),
                    'total_unique_messages': len(enhanced_messages),
                    'rules_applied': include_rules,
                    'total_rules_loaded': len(rules),
                    'messages_with_rules': messages_with_rules
                }
            }
        })
        
    except requests.exceptions.RequestException as e:
        # If connection error, return error response
        print(f"[DEBUG] Request exception: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error communicating with GrayLog: {str(e)}'
        }), 500
    
    except Exception as e:
        print(f"[DEBUG] Unexpected exception: {str(e)}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'An unexpected error occurred: {str(e)}'
        }), 500

@graylog_data_bp.route('/', methods=['POST'])
def get_graylog_data_post():
    """Get GrayLog data for a specific UUID using POST method"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400
        
        # Get parameters from request body
        uuid = data.get('uuid')
        connection = data.get('connection', 'LOKAL')
        days_back = data.get('days_back', 2)
        limit = data.get('limit', 100)
        include_rules = data.get('include_rules', True)
        
        if not uuid:
            return jsonify({
                'success': False,
                'error': 'UUID parameter is required'
            }), 400
        
        # Calculate time range in UTC
        to_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        from_time = (datetime.utcnow() - timedelta(days=days_back)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        print(f"[DEBUG] POST: Searching GrayLog for UUID: {uuid}")
        print(f"[DEBUG] POST: Time range (UTC): {from_time} to {to_time}")
        print(f"[DEBUG] POST: Connection: {connection}")
        print(f"[DEBUG] POST: Include rules: {include_rules}")
        
        # Get GrayLog configuration
        graylog_config = get_graylog_config(connection)
        
        # Fetch GrayLog rules if requested
        rules = []
        if include_rules:
            rules = get_graylog_rules()
            print(f"[DEBUG] POST: Loaded {len(rules)} active rules")
        
        # Construct GrayLog API URL
        url = f"{graylog_config['GRAYLOG_BASE_URL']}/api/search/universal/absolute"
        
        # Try multiple query formats for UUID search
        queries = [
            f'UUID:"{uuid}"',  # Standard UUID field search
            f'Message UUID: {uuid}',  # Message UUID format
            f'uuid:{uuid}',  # Lowercase uuid field
            f'"{uuid}"'  # Simple string search
        ]
        
        all_messages = []
        successful_queries = []
        
        for query in queries:
            try:
                # Set up the query parameters
                params = {
                    'query': query,
                    'from': from_time,
                    'to': to_time,
                    'decorate': 'true',
                    'limit': limit
                }
                
                # Set up headers with Basic Authentication
                headers = {
                    'Accept': 'application/json'
                }
                
                # Determine if SSL verification should be disabled
                verify_ssl = False if graylog_config['GRAYLOG_BASE_URL'].startswith('https://') else True
                
                # Print full URL with parameters for debugging
                full_url = f"{url}?{urlencode(params)}"
                print(f"[DEBUG] POST: Trying query: {query}")
                print(f"[DEBUG] POST: Full Graylog URL: {full_url}")
                
                # Make request to GrayLog using Basic Auth
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    auth=(graylog_config['GRAYLOG_USERNAME'], graylog_config['GRAYLOG_PASSWORD']),
                    verify=verify_ssl,
                    timeout=30
                )
                
                print(f"[DEBUG] POST: Response status for query '{query}': {response.status_code}")
                
                if response.ok:
                    response_data = response.json()
                    messages = response_data.get('messages', [])
                    
                    if messages:
                        all_messages.extend(messages)
                        successful_queries.append(query)
                        print(f"[DEBUG] POST: Found {len(messages)} messages with query: {query}")
                        # Don't break - collect all messages from all successful queries
                    else:
                        print(f"[DEBUG] POST: No messages found with query: {query}")
                else:
                    print(f"[DEBUG] POST: Error response for query '{query}': {response.text}")
                    
            except Exception as e:
                print(f"[DEBUG] POST: Error with query '{query}': {str(e)}")
                continue
        
        # Remove duplicates based on message ID
        unique_messages = []
        seen_ids = set()
        for message in all_messages:
            # Access the message ID from the nested message structure
            message_data = message.get('message', {})
            message_id = message_data.get('_id') or message_data.get('gl2_message_id')
            if message_id and message_id not in seen_ids:
                unique_messages.append(message)
                seen_ids.add(message_id)
            elif not message_id:
                # If no message ID found, include the message anyway
                unique_messages.append(message)
        
        # Apply rules to messages if requested
        enhanced_messages = []
        messages_with_rules = 0
        
        for message in unique_messages:
            if include_rules and rules:
                enhanced_message = enhance_message_with_rules(message, rules)
                if enhanced_message['has_rules']:
                    messages_with_rules += 1
                enhanced_messages.append(enhanced_message)
            else:
                enhanced_messages.append(message)
        
        print(f"[DEBUG] POST: Total unique messages found: {len(enhanced_messages)}")
        if include_rules:
            print(f"[DEBUG] POST: Messages with matching rules: {messages_with_rules}")
        
        return jsonify({
            'success': True,
            'data': {
                'uuid': uuid,
                'connection': connection,
                'rules': rules if include_rules else [],
                'time_range': {
                    'from': from_time,
                    'to': to_time,
                    'days_back': days_back
                },
                'total_messages': len(enhanced_messages),
                'messages': enhanced_messages,
                'successful_queries': successful_queries,
                'search_info': {
                    'queries_tried': queries,
                    'successful_queries': successful_queries,
                    'total_raw_messages': len(all_messages),
                    'total_unique_messages': len(enhanced_messages),
                    'rules_applied': include_rules,
                    'total_rules_loaded': len(rules),
                    'messages_with_rules': messages_with_rules
                }
            }
        })
        
    except requests.exceptions.RequestException as e:
        # If connection error, return error response
        print(f"[DEBUG] POST: Request exception: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error communicating with GrayLog: {str(e)}'
        }), 500
    
    except Exception as e:
        print(f"[DEBUG] POST: Unexpected exception: {str(e)}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'An unexpected error occurred: {str(e)}'
        }), 500

@graylog_data_bp.route('/batch', methods=['POST'])
def get_graylog_data_batch():
    """Get GrayLog data for multiple UUIDs"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400
        
        uuids = data.get('uuids', [])
        connection = data.get('connection', 'LOKAL')
        days_back = data.get('days_back', 2)
        limit = data.get('limit', 100)
        
        if not uuids:
            return jsonify({
                'success': False,
                'error': 'UUIDs list is required'
            }), 400
        
        if not isinstance(uuids, list):
            return jsonify({
                'success': False,
                'error': 'UUIDs must be a list'
            }), 400
        
        print(f"[DEBUG] Searching GrayLog for {len(uuids)} UUIDs")
        print(f"[DEBUG] Connection: {connection}")
        
        # Get GrayLog configuration
        graylog_config = get_graylog_config(connection)
        
        # Calculate time range in UTC
        to_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        from_time = (datetime.utcnow() - timedelta(days=days_back)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        results = {}
        errors = []
        
        for uuid in uuids:
            try:
                # Try multiple query formats for UUID search
                queries = [
                    f'UUID:"{uuid}"',  # Standard UUID field search
                    f'Message UUID: {uuid}',  # Message UUID format
                    f'uuid:{uuid}',  # Lowercase uuid field
                    f'"{uuid}"'  # Simple string search
                ]
                
                all_messages = []
                successful_queries = []
                
                for query in queries:
                    try:
                        # Construct GrayLog API URL
                        url = f"{graylog_config['GRAYLOG_BASE_URL']}/api/search/universal/absolute"
                        
                        # Set up the query parameters
                        params = {
                            'query': query,
                            'from': from_time,
                            'to': to_time,
                            'decorate': 'true',
                            'limit': limit
                        }
                        
                        # Set up headers with Basic Authentication
                        headers = {
                            'Accept': 'application/json'
                        }
                        
                        # Determine if SSL verification should be disabled
                        verify_ssl = False if graylog_config['GRAYLOG_BASE_URL'].startswith('https://') else True
                        
                        # Make request to GrayLog using Basic Auth
                        response = requests.get(
                            url,
                            params=params,
                            headers=headers,
                            auth=(graylog_config['GRAYLOG_USERNAME'], graylog_config['GRAYLOG_PASSWORD']),
                            verify=verify_ssl,
                            timeout=30
                        )
                        
                        if response.ok:
                            response_data = response.json()
                            messages = response_data.get('messages', [])
                            
                            if messages:
                                all_messages.extend(messages)
                                successful_queries.append(query)
                                print(f"[DEBUG] Found {len(messages)} messages for UUID {uuid} with query: {query}")
                                # Don't break - collect all messages from all successful queries
                            else:
                                print(f"[DEBUG] No messages found for UUID {uuid} with query: {query}")
                        else:
                            print(f"[DEBUG] Error response for UUID {uuid} with query '{query}': {response.status_code}")
                            
                    except Exception as e:
                        print(f"[DEBUG] Error with UUID {uuid} and query '{query}': {str(e)}")
                        continue
                
                # Remove duplicates based on message ID
                unique_messages = []
                seen_ids = set()
                for message in all_messages:
                    # Access the message ID from the nested message structure
                    message_data = message.get('message', {})
                    message_id = message_data.get('_id') or message_data.get('gl2_message_id')
                    if message_id and message_id not in seen_ids:
                        unique_messages.append(message)
                        seen_ids.add(message_id)
                    elif not message_id:
                        # If no message ID found, include the message anyway
                        unique_messages.append(message)
                
                results[uuid] = {
                    'success': True,
                    'total_messages': len(unique_messages),
                    'messages': unique_messages,
                    'successful_queries': successful_queries,
                    'search_info': {
                        'queries_tried': queries,
                        'successful_queries': successful_queries,
                        'total_raw_messages': len(all_messages),
                        'total_unique_messages': len(unique_messages)
                    }
                }
                
            except Exception as e:
                results[uuid] = {
                    'success': False,
                    'error': f'Error processing UUID {uuid}: {str(e)}'
                }
                errors.append(f"UUID {uuid}: {str(e)}")
        
        return jsonify({
            'success': True,
            'data': {
                'connection': connection,
                'time_range': {
                    'from': from_time,
                    'to': to_time,
                    'days_back': days_back
                },
                'total_uuids': len(uuids),
                'successful_queries': len([r for r in results.values() if r.get('success')]),
                'failed_queries': len(errors),
                'errors': errors if errors else None,
                'results': results
            }
        })
        
    except Exception as e:
        print(f"[DEBUG] Unexpected exception: {str(e)}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'An unexpected error occurred: {str(e)}'
        }), 500

@graylog_data_bp.route('/search', methods=['POST'])
def search_graylog_data():
    """Search GrayLog data with custom query"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400
        
        query = data.get('query')
        connection = data.get('connection', 'LOKAL')
        days_back = data.get('days_back', 2)
        limit = data.get('limit', 100)
        
        if not query:
            return jsonify({
                'success': False,
                'error': 'Query parameter is required'
            }), 400
        
        print(f"[DEBUG] Searching GrayLog with query: {query}")
        print(f"[DEBUG] Connection: {connection}")
        
        # Get GrayLog configuration
        graylog_config = get_graylog_config(connection)
        
        # Calculate time range in UTC
        to_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        from_time = (datetime.utcnow() - timedelta(days=days_back)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        # Construct GrayLog API URL
        url = f"{graylog_config['GRAYLOG_BASE_URL']}/api/search/universal/absolute"
        
        # Set up the query parameters
        params = {
            'query': query,
            'from': from_time,
            'to': to_time,
            'decorate': 'true',
            'limit': limit
        }
        
        # Set up headers with Basic Authentication
        headers = {
            'Accept': 'application/json'
        }
        
        # Determine if SSL verification should be disabled
        verify_ssl = False if graylog_config['GRAYLOG_BASE_URL'].startswith('https://') else True
        
        # Make request to GrayLog using Basic Auth
        response = requests.get(
            url,
            params=params,
            headers=headers,
            auth=(graylog_config['GRAYLOG_USERNAME'], graylog_config['GRAYLOG_PASSWORD']),
            verify=verify_ssl,
            timeout=30
        )
        
        print(f"[DEBUG] Response status: {response.status_code}")
        
        if response.ok:
            # If successful, return the data
            response_data = response.json()
            messages = response_data.get('messages', [])
            
            print(f"[DEBUG] Total messages: {len(messages)}")
            
            return jsonify({
                'success': True,
                'data': {
                    'query': query,
                    'connection': connection,
                    'time_range': {
                        'from': from_time,
                        'to': to_time,
                        'days_back': days_back
                    },
                    'total_messages': len(messages),
                    'messages': messages,
                    'graylog_response': response_data
                }
            })
        else:
            # If API error, return error response
            print(f"[DEBUG] Error response: {response.text}")
            return jsonify({
                'success': False,
                'error': f'GrayLog API error: {response.status_code}',
                'details': response.text
            }), 500
            
    except requests.exceptions.RequestException as e:
        # If connection error, return error response
        print(f"[DEBUG] Request exception: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error communicating with GrayLog: {str(e)}'
        }), 500
    
    except Exception as e:
        print(f"[DEBUG] Unexpected exception: {str(e)}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'An unexpected error occurred: {str(e)}'
        }), 500

@graylog_data_bp.route('/config', methods=['GET'])
def get_graylog_config_endpoint():
    """Get current GrayLog configuration"""
    try:
        connection = request.args.get('connection', 'LOKAL')
        
        config = get_graylog_config(connection)
        
        # Remove password from response for security
        safe_config = {
            'GRAYLOG_BASE_URL': config['GRAYLOG_BASE_URL'],
            'GRAYLOG_USERNAME': config['GRAYLOG_USERNAME'],
            'GRAYLOG_PASSWORD': '***'  # Hide password
        }
        
        return jsonify({
            'success': True,
            'data': {
                'connection': connection,
                'config': safe_config
            }
        })
        
    except Exception as e:
        print(f"[DEBUG] Error getting config: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error getting GrayLog configuration: {str(e)}'
        }), 500

@graylog_data_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    response = jsonify({'status': 'ok'})
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
    return response

def fetch_graylog_data_direct(uuid, connection, days_back=7, limit=100, include_rules=True):
    """Direct function to fetch GrayLog data without Flask request context"""
    try:
        # Get GrayLog configuration
        config = get_graylog_config(connection)
        if not config:
            return {
                'success': False,
                'error': f'GrayLog configuration not found for connection: {connection}'
            }
        
        # Check if we have the required configuration
        if not config.get('GRAYLOG_BASE_URL'):
            return {
                'success': False,
                'error': f'GrayLog base URL not found in configuration'
            }
        
        # Get GrayLog rules if requested
        rules = []
        if include_rules:
            rules = get_graylog_rules()
        
        # Calculate time range in UTC
        to_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        from_time = (datetime.utcnow() - timedelta(days=days_back)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        # Construct GrayLog API URL
        url = f"{config['GRAYLOG_BASE_URL']}/api/search/universal/absolute"
        
        # Try multiple query formats for UUID search
        queries = [
            f'UUID:"{uuid}"',  # Standard UUID field search
            f'Message UUID: {uuid}',  # Message UUID format
            f'uuid:{uuid}',  # Lowercase uuid field
            f'"{uuid}"'  # Simple string search
        ]
        
        all_messages = []
        successful_queries = []
        
        for query in queries:
            try:
                # Set up the query parameters
                params = {
                    'query': query,
                    'from': from_time,
                    'to': to_time,
                    'decorate': 'true',
                    'limit': limit
                }
                
                # Set up headers with Basic Authentication
                headers = {
                    'Accept': 'application/json'
                }
                
                # Determine if SSL verification should be disabled
                verify_ssl = False if config['GRAYLOG_BASE_URL'].startswith('https://') else True
                
                # Make request to GrayLog using Basic Auth
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    auth=(config['GRAYLOG_USERNAME'], config['GRAYLOG_PASSWORD']),
                    verify=verify_ssl,
                    timeout=30
                )
                
                if response.ok:
                    response_data = response.json()
                    messages = response_data.get('messages', [])
                    
                    if messages:
                        all_messages.extend(messages)
                        successful_queries.append(query)
                    else:
                        pass # No else block here, just continue
                else:
                    pass # No else block here, just continue
                    
            except Exception as e:
                continue
        
        # Remove duplicates based on message ID
        unique_messages = {}
        
        for msg in all_messages:
            message_data = msg.get('message', {})
            message_id = message_data.get('_id') or message_data.get('gl2_message_id')
            
            if message_id and message_id not in unique_messages:
                unique_messages[message_id] = msg
        
        # Enhance messages with rules if requested
        if include_rules and rules:
            for message in unique_messages.values():
                enhanced_message = enhance_message_with_rules(message, rules)
                message.update(enhanced_message)
        
        # Prepare response data
        response_data = {
            'uuid': uuid,
            'connection': connection,
            'rules': rules if include_rules else [],
            'time_range': {
                'start': from_time,
                'end': to_time,
                'days_back': days_back
            },
            'total_messages': len(unique_messages.values()),
            'messages': list(unique_messages.values()),
            'search_info': {
                'query': f'Multiple queries: {", ".join(successful_queries)}',
                'total_raw_messages': len(all_messages),
                'total_unique_messages': len(unique_messages),
                'rules_applied': include_rules,
                'total_rules_loaded': len(rules) if rules else 0,
                'messages_with_rules': sum(1 for msg in unique_messages.values() if msg.get('has_rules', False))
            }
        }
        
        return {
            'success': True,
            'data': response_data
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': f'Error fetching GrayLog data: {str(e)}'
        }