import os
import sqlite3
import paramiko
import io
import time
from threading import Thread, Lock

# Constants
DATABASE_FILE = os.path.join('instance', 'myapp_db.db')
SSH_TIMEOUT = 10

# Global dictionary to store SSH client connections
ssh_clients = {}
ssh_lock = Lock()

def init_ssh_db():
    """Initialize the SSH-related database tables."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Create SSH snippets table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ssh_snippets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                commands TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Update connection table if needed
        cursor.execute("PRAGMA table_info(connection)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'connection_type' not in columns:
            cursor.execute("ALTER TABLE connection ADD COLUMN connection_type TEXT")
        
        conn.commit()
        print("SSH database tables initialized successfully.")
    except Exception as e:
        print(f"Error initializing SSH database tables: {e}")
    finally:
        if conn:
            conn.close()

def get_ssh_connections():
    """Get all SSH connections from the database."""
    conn = None
    connections = []
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, host, port, user, password, category
            FROM connection
            WHERE UPPER(connection_type) = 'SSH'
            ORDER BY category, name
        ''')
        connections = [dict(row) for row in cursor.fetchall()]
        print(f"Found {len(connections)} SSH connections")
    except Exception as e:
        print(f"Error getting SSH connections: {e}")
    finally:
        if conn:
            conn.close()
    return connections

def get_ssh_snippets():
    """Get all SSH snippets from the database."""
    conn = None
    snippets = []
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, description, commands
            FROM ssh_snippets
            ORDER BY name
        ''')
        snippets = [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error getting SSH snippets: {e}")
    finally:
        if conn:
            conn.close()
    return snippets

def get_ssh_snippet(snippet_id):
    """Get a specific SSH snippet by ID."""
    conn = None
    snippet = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, description, commands
            FROM ssh_snippets
            WHERE id = ?
        ''', (snippet_id,))
        row = cursor.fetchone()
        snippet = dict(row) if row else None
    except Exception as e:
        print(f"Error getting SSH snippet {snippet_id}: {e}")
    finally:
        if conn:
            conn.close()
    return snippet

def save_ssh_snippet(snippet_data):
    """Save a new or update an existing SSH snippet."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Check if this is an update or new snippet
        if 'id' in snippet_data and snippet_data['id']:
            cursor.execute('''
                UPDATE ssh_snippets
                SET name = ?, description = ?, commands = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                snippet_data['name'],
                snippet_data['description'],
                snippet_data['commands'],
                snippet_data['id']
            ))
            snippet_id = snippet_data['id']
        else:
            cursor.execute('''
                INSERT INTO ssh_snippets (name, description, commands)
                VALUES (?, ?, ?)
            ''', (
                snippet_data['name'],
                snippet_data['description'],
                snippet_data['commands']
            ))
            snippet_id = cursor.lastrowid
        
        conn.commit()
        return {'success': True, 'id': snippet_id}
    except Exception as e:
        print(f"Error saving SSH snippet: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        if conn:
            conn.close()

def delete_ssh_snippet(snippet_id):
    """Delete an SSH snippet by ID."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM ssh_snippets WHERE id = ?', (snippet_id,))
        conn.commit()
        return {'success': True}
    except Exception as e:
        print(f"Error deleting SSH snippet {snippet_id}: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        if conn:
            conn.close()

def get_connection_by_name(connection_name):
    """Get a connection by name from the database."""
    conn = None
    connection = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, host, port, user, password, category
            FROM connection
            WHERE name = ?
        ''', (connection_name,))
        row = cursor.fetchone()
        if row:
            connection = dict(row)
    except Exception as e:
        print(f"Error getting connection {connection_name}: {e}")
    finally:
        if conn:
            conn.close()
    return connection

def get_ssh_client(connection_info):
    """Get or create an SSH client for the given connection."""
    connection_name = connection_info['name']
    
    with ssh_lock:
        # Check if we already have a client for this connection
        if connection_name in ssh_clients:
            client = ssh_clients[connection_name]
            # Check if the connection is still active
            try:
                client.get_transport().send_ignore()
                return {'success': True, 'client': client, 'message': 'Connection already established'}
            except:
                # Connection is closed, remove it
                del ssh_clients[connection_name]
                return {'success': False, 'error': 'Connection lost, reconnecting...'}
        
        # Create a new client
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            print(f"Connecting to {connection_info['host']}:{connection_info['port']} as {connection_info['user']}")
            client.connect(
                hostname=connection_info['host'],
                port=int(connection_info['port']),
                username=connection_info['user'],
                password=connection_info['password'],
                timeout=SSH_TIMEOUT
            )
            ssh_clients[connection_name] = client
            return {'success': True, 'client': client, 'message': 'Connection established successfully'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

def check_connection_status(connection_name):
    """Check if a connection is active."""
    connection_info = get_connection_by_name(connection_name)
    if not connection_info:
        return {'connected': False, 'message': f"Connection '{connection_name}' not found"}
    
    with ssh_lock:
        if connection_name in ssh_clients:
            client = ssh_clients[connection_name]
            try:
                client.get_transport().send_ignore()
                return {'connected': True, 'message': 'Connection active'}
            except:
                # Connection is closed, remove it
                del ssh_clients[connection_name]
                return {'connected': False, 'message': 'Connection lost'}
        
        return {'connected': False, 'message': 'Not connected'}

def establish_connection(connection_name):
    """Establish a connection to the SSH server."""
    connection_info = get_connection_by_name(connection_name)
    if not connection_info:
        return {'success': False, 'error': f"Connection '{connection_name}' not found"}
    
    result = get_ssh_client(connection_info)
    if result['success']:
        return {'success': True, 'message': result['message']}
    else:
        return {'success': False, 'error': result['error']}

def execute_ssh_command(connection_name, command):
    """Execute a command on the SSH server."""
    connection_info = get_connection_by_name(connection_name)
    if not connection_info:
        return {'success': False, 'error': f"Connection '{connection_name}' not found"}
    
    # Get SSH client
    client_result = get_ssh_client(connection_info)
    if not client_result['success']:
        return {'success': False, 'error': client_result['error']}
    
    client = client_result['client']
    
    # Execute command. Success follows the remote exit code.
    # Stderr alone is not a failure: scripts often print warnings there
    # (for example mv) while still writing the real log to stdout.
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=SSH_TIMEOUT)
        output = stdout.read().decode('utf-8', errors='replace')
        error_text = stderr.read().decode('utf-8', errors='replace')
        exit_code = stdout.channel.recv_exit_status()

        result = {
            'success': exit_code == 0,
            'output': output,
            'exit_code': exit_code,
        }
        if error_text.strip():
            result['error'] = error_text
        elif exit_code != 0:
            result['error'] = f'Command exited with status {exit_code}'
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}

def execute_ssh_snippet(connection_name, snippet_id):
    """Execute a snippet (multiple commands) on the SSH server."""
    snippet = get_ssh_snippet(snippet_id)
    if not snippet:
        return {'success': False, 'error': f"Snippet ID {snippet_id} not found"}
    
    commands = snippet['commands'].strip().split('\n')
    outputs = []
    
    for command in commands:
        command = command.strip()
        if not command or command.startswith('#'):
            # Skip empty lines and comments
            continue
        
        result = execute_ssh_command(connection_name, command)
        text = result.get('output') or ''
        err = result.get('error') or ''
        if err:
            text = text + ('\n' if text else '') + err
        outputs.append(text)
        if not result['success']:
            return {
                'success': False,
                'error': err or f"Command exited with status {result.get('exit_code')}",
                'exit_code': result.get('exit_code'),
                'output': result.get('output') or '',
                'snippet_name': snippet['name'],
                'commands': commands[:commands.index(command) + 1],
                'outputs': outputs
            }
    
    return {
        'success': True,
        'snippet_name': snippet['name'],
        'commands': [cmd for cmd in commands if cmd.strip() and not cmd.strip().startswith('#')],
        'outputs': outputs
    }

def close_ssh_connection(connection_name):
    """Close an SSH connection."""
    with ssh_lock:
        if connection_name in ssh_clients:
            try:
                ssh_clients[connection_name].close()
                del ssh_clients[connection_name]
                return {'success': True, 'message': 'Connection closed'}
            except Exception as e:
                return {'success': False, 'error': str(e)}
        return {'success': True, 'message': 'Connection not found, already closed'}

def get_server_status(connection_name, fields=None):
    """Get server monitoring info via SSH for the given connection name.
    
    Args:
        connection_name: Name of the SSH connection
        fields: List of specific fields to retrieve (optional)
        
    Returns:
        Dictionary with server status information
    """
    import re
    connection_info = get_connection_by_name(connection_name)
    if not connection_info:
        return {'success': False, 'error': f"Connection '{connection_name}' not found"}
    result = get_ssh_client(connection_info)
    if not result['success']:
        return {'success': False, 'error': result.get('error', 'SSH connection error')}
    client = result['client']
    
    # Default fields if not specified
    if fields is None or not fields:
        fields = ['os', 'cpu', 'memory', 'disk', 'uptime', 'hostname']
        
    try:
        def run(cmd):
            stdin, stdout, stderr = client.exec_command(cmd)
            return stdout.read().decode('utf-8'), stderr.read().decode('utf-8')
            
        # Initialize result variables
        result = {'success': True}
        import time as t
        
        # CPU usage
        if any(field in fields for field in ['cpu', 'all']):
            cpu_out, _ = run("top -bn1 | grep 'Cpu(s)' || top -b -n1 | grep 'Cpu(s)'")
            cpu_user = cpu_irq = 0.0
            if cpu_out:
                m = re.search(r'Cpu\(s\):\s*([\d\.]+)\s*us,.*?([\d\.]+)\s*id,.*?([\d\.]+)\s*wa,.*?([\d\.]+)\s*hi,.*?([\d\.]+)\s*si', cpu_out)
                if m:
                    cpu_user = float(m.group(1))
                    cpu_irq = float(m.group(4)) + float(m.group(5))
                else:
                    m = re.search(r'([\d\.]+)\s*us,.*?([\d\.]+)\s*sy,.*?([\d\.]+)\s*ni,.*?([\d\.]+)\s*id,.*?([\d\.]+)\s*wa,.*?([\d\.]+)\s*hi,.*?([\d\.]+)\s*si', cpu_out)
                    if m:
                        cpu_user = float(m.group(1))
                        cpu_irq = float(m.group(6)) + float(m.group(7))
            result['cpu_user'] = cpu_user
            result['cpu_interrupt'] = cpu_irq
            
        # Memory usage
        mem_out = None
        if any(field in fields for field in ['memory', 'swap', 'all']):
            mem_out, _ = run("free -m")
            mem_total = mem_free = mem_used = mem_percent = None
            for line in mem_out.splitlines():
                if line.lower().startswith('mem:'):
                    parts = line.split()
                    if len(parts) >= 3:
                        mem_total = int(parts[1])
                        mem_free = int(parts[3])
                        mem_used = mem_total - mem_free
                        mem_percent = round((mem_used / mem_total) * 100, 1) if mem_total else None
            
            result['mem_total'] = mem_total
            result['mem_free'] = mem_free
            result['mem_used'] = mem_used
            result['mem_percent'] = mem_percent
            
        # Disk usage
        if any(field in fields for field in ['disk', 'all']):
            disk_out, _ = run("df -B1 / | tail -1")
            disk_total = disk_free = disk_used = disk_percent = None
            parts = disk_out.split()
            if len(parts) >= 5:
                try:
                    disk_total = int(parts[1])
                    disk_free = int(parts[3])
                    disk_used = disk_total - disk_free
                    disk_percent = round((disk_used / disk_total) * 100, 1) if disk_total else None
                except Exception:
                    disk_total = disk_free = disk_used = disk_percent = None
                    
            result['disk_total'] = disk_total
            result['disk_free'] = disk_free
            result['disk_used'] = disk_used
            result['disk_percent'] = disk_percent
            
        # Network usage (kbps, snapshot 1 detik)
        if any(field in fields for field in ['network', 'all']):
            net_in = net_out = None
            netdev_out1, _ = run("cat /proc/net/dev | grep ':'")
            t.sleep(1)
            netdev_out2, _ = run("cat /proc/net/dev | grep ':'")
            
            def get_bytes(dev_out):
                for line in dev_out.splitlines():
                    if not line.strip().startswith('lo:'):
                        cols = line.split()
                        if len(cols) >= 10:
                            try:
                                return int(cols[1]), int(cols[9])
                            except Exception:
                                return None, None
                return None, None
                
            in1, out1 = get_bytes(netdev_out1)
            in2, out2 = get_bytes(netdev_out2)
            if in1 is not None and in2 is not None and out1 is not None and out2 is not None:
                net_in = round((in2-in1)*8/1024, 2)  # kbps
                net_out = round((out2-out1)*8/1024, 2)  # kbps
                
            result['net_in'] = net_in
            result['net_out'] = net_out
            
        # Uptime
        if any(field in fields for field in ['uptime', 'all']):
            uptime_out, _ = run("uptime -p")
            result['uptime'] = uptime_out.strip()
            
        # Hostname
        if any(field in fields for field in ['hostname', 'all']):
            hostname_out, _ = run("hostname")
            result['hostname'] = hostname_out.strip()
            
        # OS info
        if any(field in fields for field in ['os', 'all']):
            os_name = os_version = None
            os_out, _ = run("cat /etc/os-release || uname -a")
            if 'PRETTY_NAME' in os_out:
                m = re.search(r'PRETTY_NAME="([^"]+)"', os_out)
                if m:
                    os_name = m.group(1)
            if not os_name:
                os_name = os_out.strip().split('\n')[0]
            os_version = os_out.strip().split('\n')[0]
            
            result['os'] = os_name
            result['os_version'] = os_version
            
        # Response time (ping localhost)
        if any(field in fields for field in ['response_time', 'all']):
            start = t.time()
            run("echo 1")
            result['response_time'] = int((t.time() - start) * 1000)
            
        # Device availability (dummy: always 100%)
        if any(field in fields for field in ['availability', 'all']):
            result['availability'] = 100.0
            
        # Load average
        if any(field in fields for field in ['loadavg', 'all']):
            loadavg_out, _ = run("cat /proc/loadavg")
            result['loadavg'] = loadavg_out.strip().split()[:3] if loadavg_out else None
            
        # Jumlah proses
        if any(field in fields for field in ['num_proc', 'all']):
            proc_out, _ = run("ps -e | wc -l")
            result['num_proc'] = int(proc_out.strip()) if proc_out.strip().isdigit() else None
            
        # Swap usage
        if any(field in fields for field in ['swap', 'all']):
            swap_total = swap_free = swap_used = swap_percent = None
            
            # If mem_out wasn't fetched yet, get it now
            if mem_out is None:
                mem_out, _ = run("free -m")
                
            for line in mem_out.splitlines():
                if line.lower().startswith('swap:'):
                    parts = line.split()
                    if len(parts) >= 3:
                        swap_total = int(parts[1])
                        swap_free = int(parts[3])
                        swap_used = swap_total - swap_free
                        swap_percent = round((swap_used / swap_total) * 100, 1) if swap_total else None
                        
            result['swap_total'] = swap_total
            result['swap_free'] = swap_free
            result['swap_used'] = swap_used
            result['swap_percent'] = swap_percent
            
        # Top 1 proses CPU
        if any(field in fields for field in ['topcpu', 'all']):
            topcpu_out, _ = run("ps -eo pid,comm,%cpu --sort=-%cpu | head -2 | tail -1")
            result['topcpu'] = topcpu_out.strip()
            
        # Top 1 proses Mem
        if any(field in fields for field in ['topmem', 'all']):
            topmem_out, _ = run("ps -eo pid,comm,%mem --sort=-%mem | head -2 | tail -1")
            result['topmem'] = topmem_out.strip()
            
        # Server time
        if any(field in fields for field in ['server_time', 'all']):
            time_out, _ = run("date '+%Y-%m-%d %H:%M:%S'")
            result['server_time'] = time_out.strip()
            
        # Status service penting
        if any(field in fields for field in ['services', 'all']):
            def check_service(name):
                out, _ = run(f"systemctl is-active {name}")
                return out.strip() if out else None
                
            result['services'] = {
                'sshd': check_service('sshd'),
                'nginx': check_service('nginx'),
                'mysqld': check_service('mysqld'),
                'httpd': check_service('httpd'),
            }
            
        # Temperature (jika ada)
        if any(field in fields for field in ['temperature', 'all']):
            temp_out, _ = run("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null")
            temperature = None
            if temp_out.strip().isdigit():
                temperature = round(int(temp_out.strip()) / 1000, 1)
                
            result['temperature'] = temperature
            
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}