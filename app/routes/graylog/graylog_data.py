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

def _dedupe_graylog_messages(messages):
    """Deduplicate GrayLog messages by message id."""
    unique_messages = []
    seen_ids = set()
    for message in messages:
        message_data = message.get('message', {})
        message_id = message_data.get('_id') or message_data.get('gl2_message_id')
        if message_id and message_id not in seen_ids:
            unique_messages.append(message)
            seen_ids.add(message_id)
        elif not message_id:
            unique_messages.append(message)
    return unique_messages

def _search_graylog_views_api(base_url, headers, auth, verify_ssl, query, from_time, to_time, limit):
    """
    GrayLog 4.x Views Search API (same path the web UI uses).
    User 'monitoring' has access here even when legacy /api/search/universal/* returns 403.
    """
    create_url = f"{base_url}/api/views/search"
    payload = {
        "queries": [
            {
                "id": "query-1",
                "query": {"type": "elasticsearch", "query_string": query},
                "timerange": {
                    "type": "absolute",
                    "from": from_time,
                    "to": to_time,
                },
                "search_types": [
                    {
                        "id": "messages-1",
                        "type": "messages",
                        "limit": limit,
                        "offset": 0,
                        "sort": [{"field": "timestamp", "order": "DESC"}],
                    }
                ],
            }
        ]
    }

    create_resp = requests.post(
        create_url,
        headers=headers,
        auth=auth,
        json=payload,
        verify=verify_ssl,
        timeout=30,
    )
    if not create_resp.ok:
        return {
            "ok": False,
            "status": create_resp.status_code,
            "detail": (create_resp.text or create_resp.reason)[:500],
            "messages": [],
        }

    search_id = create_resp.json().get("id")
    if not search_id:
        return {
            "ok": False,
            "status": create_resp.status_code,
            "detail": "Views search created but no search id returned",
            "messages": [],
        }

    exec_resp = requests.post(
        f"{base_url}/api/views/search/{search_id}/execute",
        headers=headers,
        auth=auth,
        json={},
        verify=verify_ssl,
        timeout=60,
    )
    if not exec_resp.ok:
        return {
            "ok": False,
            "status": exec_resp.status_code,
            "detail": (exec_resp.text or exec_resp.reason)[:500],
            "messages": [],
        }

    exec_data = exec_resp.json()
    query_result = (exec_data.get("results") or {}).get("query-1") or {}
    search_type = (query_result.get("search_types") or {}).get("messages-1") or {}
    messages = search_type.get("messages") or []
    total = search_type.get("total_results")

    return {
        "ok": True,
        "status": exec_resp.status_code,
        "detail": None,
        "messages": messages,
        "total_results": total,
        "api": "views",
    }

def _search_graylog_legacy_api(base_url, headers, auth, verify_ssl, query, from_time, to_time, limit):
    """Fallback for older GrayLog that still exposes /api/search/universal/absolute."""
    url = f"{base_url}/api/search/universal/absolute"
    params = {
        "query": query,
        "from": from_time,
        "to": to_time,
        "decorate": "true",
        "limit": limit,
    }
    response = requests.get(
        url,
        params=params,
        headers=headers,
        auth=auth,
        verify=verify_ssl,
        timeout=30,
    )
    if not response.ok:
        return {
            "ok": False,
            "status": response.status_code,
            "detail": (response.text or response.reason)[:500],
            "messages": [],
        }

    messages = response.json().get("messages", [])
    return {
        "ok": True,
        "status": response.status_code,
        "detail": None,
        "messages": messages,
        "api": "legacy",
    }

def search_graylog_by_uuid(uuid, graylog_config, days_back=7, limit=100):
    """
    Search GrayLog for a UUID. Prefer Views API (GrayLog 4.x UI), fall back to legacy.
    Auth/connectivity failures are returned as error instead of empty results.
    """
    to_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
    from_time = (datetime.utcnow() - timedelta(days=days_back)).strftime('%Y-%m-%dT%H:%M:%S.000Z')

    base_url = (graylog_config.get('GRAYLOG_BASE_URL') or '').rstrip('/')
    username = graylog_config.get('GRAYLOG_USERNAME') or ''
    password = graylog_config.get('GRAYLOG_PASSWORD') or ''
    verify_ssl = False if base_url.startswith('https://') else True
    auth = (username, password)

    queries = [
        f'UUID:"{uuid}"',
        f'"{uuid}"',
        f'uuid:{uuid}',
    ]

    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-Requested-By': 'JalinApps',
    }

    all_messages = []
    successful_queries = []
    query_errors = []
    auth_error = None
    used_api = None

    for query in queries:
        if auth_error:
            break

        print(f"[DEBUG] Trying GrayLog query via Views API: {query}")
        try:
            result = _search_graylog_views_api(
                base_url, headers, auth, verify_ssl, query, from_time, to_time, limit
            )

            # If Views endpoint itself is missing, fall back to legacy once
            if not result["ok"] and result.get("status") == 404:
                print("[DEBUG] Views API not available, falling back to legacy absolute search")
                result = _search_graylog_legacy_api(
                    base_url, headers, auth, verify_ssl, query, from_time, to_time, limit
                )

            if result["ok"]:
                used_api = result.get("api") or used_api or "views"
                successful_queries.append(query)
                messages = result.get("messages") or []
                if messages:
                    all_messages.extend(messages)
                    print(
                        f"[DEBUG] Found {len(messages)} messages "
                        f"(total_results={result.get('total_results')}) with query: {query}"
                    )
                    # Primary UUID query already matched — stop early
                    if query.startswith('UUID:'):
                        break
                else:
                    print(f"[DEBUG] No messages found with query: {query}")
            else:
                query_errors.append({
                    'query': query,
                    'status': result.get('status'),
                    'detail': result.get('detail'),
                })
                print(f"[DEBUG] GrayLog error for '{query}': {result.get('status')} {result.get('detail')}")
                if result.get('status') in (401, 403):
                    auth_error = (
                        f"GrayLog menolak akses search API (HTTP {result.get('status')}). "
                        f"User '{username}' di {base_url}. Detail: {result.get('detail')}"
                    )
        except requests.exceptions.RequestException as e:
            query_errors.append({'query': query, 'status': None, 'detail': str(e)})
            print(f"[DEBUG] Request error with query '{query}': {e}")
            auth_error = f"Tidak bisa terhubung ke GrayLog di {base_url}: {e}"
            break
        except Exception as e:
            query_errors.append({'query': query, 'status': None, 'detail': str(e)})
            print(f"[DEBUG] Error with query '{query}': {e}")
            continue

    unique_messages = _dedupe_graylog_messages(all_messages)

    return {
        'messages': unique_messages,
        'successful_queries': successful_queries,
        'queries_tried': queries,
        'query_errors': query_errors,
        'error': auth_error,
        'api_used': used_api,
        'time_range': {
            'from': from_time,
            'to': to_time,
            'days_back': days_back,
        },
        'graylog_url': base_url,
        'graylog_username': username,
    }

def build_graylog_response(uuid, connection, days_back, limit, include_rules):
    """Shared handler body for GET/POST graylog data endpoints."""
    print(f"[DEBUG] Searching GrayLog for UUID: {uuid}")
    print(f"[DEBUG] Connection: {connection}, days_back={days_back}, include_rules={include_rules}")

    graylog_config = get_graylog_config(connection)
    rules = get_graylog_rules() if include_rules else []
    if include_rules:
        print(f"[DEBUG] Loaded {len(rules)} active rules")

    search_result = search_graylog_by_uuid(
        uuid=uuid,
        graylog_config=graylog_config,
        days_back=days_back,
        limit=limit,
    )

    if search_result.get('error') and not search_result.get('messages'):
        return jsonify({
            'success': False,
            'error': search_result['error'],
            'data': {
                'uuid': uuid,
                'connection': connection,
                'graylog_url': search_result.get('graylog_url'),
                'graylog_username': search_result.get('graylog_username'),
                'time_range': search_result.get('time_range'),
                'query_errors': search_result.get('query_errors', []),
                'total_messages': 0,
                'messages': [],
            }
        }), 403 if 'permission' in (search_result['error'] or '').lower() or '403' in (search_result['error'] or '') else 502

    enhanced_messages = []
    messages_with_rules = 0
    for message in search_result['messages']:
        if include_rules and rules:
            enhanced_message = enhance_message_with_rules(message, rules)
            if enhanced_message.get('has_rules'):
                messages_with_rules += 1
            enhanced_messages.append(enhanced_message)
        else:
            enhanced_messages.append(message)

    print(f"[DEBUG] Total unique messages found: {len(enhanced_messages)}")

    return jsonify({
        'success': True,
        'data': {
            'uuid': uuid,
            'connection': connection,
            'rules': rules if include_rules else [],
            'time_range': search_result['time_range'],
            'total_messages': len(enhanced_messages),
            'messages': enhanced_messages,
            'successful_queries': search_result['successful_queries'],
            'search_info': {
                'queries_tried': search_result['queries_tried'],
                'successful_queries': search_result['successful_queries'],
                'query_errors': search_result.get('query_errors', []),
                'total_raw_messages': len(search_result['messages']),
                'total_unique_messages': len(enhanced_messages),
                'rules_applied': include_rules,
                'total_rules_loaded': len(rules),
                'messages_with_rules': messages_with_rules,
                'graylog_url': search_result.get('graylog_url'),
                'graylog_username': search_result.get('graylog_username'),
            }
        }
    })

@graylog_data_bp.route('/', methods=['GET'])
def get_graylog_data():
    """Get GrayLog data for a specific UUID"""
    try:
        uuid = request.args.get('uuid')
        connection = request.args.get('connection', 'LOKAL')
        days_back = request.args.get('days_back', 7, type=int)
        limit = request.args.get('limit', 100, type=int)
        include_rules = request.args.get('include_rules', 'true').lower() == 'true'

        if not uuid:
            return jsonify({
                'success': False,
                'error': 'UUID parameter is required'
            }), 400

        return build_graylog_response(uuid, connection, days_back, limit, include_rules)

    except requests.exceptions.RequestException as e:
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

        uuid = data.get('uuid')
        connection = data.get('connection', 'LOKAL')
        days_back = data.get('days_back', 7)
        limit = data.get('limit', 100)
        include_rules = data.get('include_rules', True)

        if not uuid:
            return jsonify({
                'success': False,
                'error': 'UUID parameter is required'
            }), 400

        return build_graylog_response(uuid, connection, days_back, limit, include_rules)

    except requests.exceptions.RequestException as e:
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
        config = get_graylog_config(connection)
        if not config or not config.get('GRAYLOG_BASE_URL'):
            return {
                'success': False,
                'error': f'GrayLog configuration not found for connection: {connection}'
            }

        rules = get_graylog_rules() if include_rules else []
        search_result = search_graylog_by_uuid(
            uuid=uuid,
            graylog_config=config,
            days_back=days_back,
            limit=limit,
        )

        if search_result.get('error') and not search_result.get('messages'):
            return {
                'success': False,
                'error': search_result['error'],
            }

        unique_messages = {}
        for msg in search_result.get('messages') or []:
            message_data = msg.get('message', {})
            message_id = message_data.get('_id') or message_data.get('gl2_message_id')
            if message_id and message_id not in unique_messages:
                if include_rules and rules:
                    unique_messages[message_id] = enhance_message_with_rules(msg, rules)
                else:
                    unique_messages[message_id] = msg
            elif not message_id:
                key = f"noid-{len(unique_messages)}"
                unique_messages[key] = (
                    enhance_message_with_rules(msg, rules) if include_rules and rules else msg
                )

        time_range = search_result.get('time_range') or {}
        response_data = {
            'uuid': uuid,
            'connection': connection,
            'rules': rules if include_rules else [],
            'time_range': {
                'start': time_range.get('from'),
                'end': time_range.get('to'),
                'days_back': days_back,
            },
            'total_messages': len(unique_messages),
            'messages': list(unique_messages.values()),
            'search_info': {
                'query': f'Multiple queries: {", ".join(search_result.get("successful_queries") or [])}',
                'api_used': search_result.get('api_used'),
                'total_raw_messages': len(search_result.get('messages') or []),
                'total_unique_messages': len(unique_messages),
                'rules_applied': include_rules,
                'total_rules_loaded': len(rules) if rules else 0,
                'messages_with_rules': sum(
                    1 for msg in unique_messages.values() if msg.get('has_rules', False)
                ),
            },
        }

        return {
            'success': True,
            'data': response_data,
        }

    except Exception as e:
        return {
            'success': False,
            'error': f'Error fetching GrayLog data: {str(e)}',
        }