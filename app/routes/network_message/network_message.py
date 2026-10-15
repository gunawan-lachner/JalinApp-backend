"""
Network Message API Routes
=========================
API endpoints for managing saved network packets/messages
"""
import os
import sqlite3
import socket
import ssl
import struct
import time
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from flask import Blueprint, request, jsonify
import threading

# Database path
DATABASE_FILE = os.path.join('instance', 'myapp_db.db')


class SavedPacketsDB:
    """
    Database handler for saved network packets
    
    This class manages all database operations for network messages/packets
    including CRUD operations, search, and folder management.
    """
    
    def __init__(self, db_path: str = None):
        """Initialize database connection"""
        self.db_path = db_path or DATABASE_FILE
        self.init_database()
    
    def init_database(self):
        """Initialize database and create table if not exists"""
        try:
            # Ensure instance directory exists
            os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else '.', exist_ok=True)
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Create table if not exists
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS saved_packets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        ascii_data TEXT,
                        hex_data TEXT NOT NULL,
                        target_address TEXT NOT NULL,
                        target_port INTEGER NOT NULL,
                        protocol TEXT NOT NULL,
                        folder TEXT DEFAULT 'Default',
                        comment TEXT DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create indexes for better performance
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_saved_packets_name 
                    ON saved_packets(name)
                ''')
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_saved_packets_protocol 
                    ON saved_packets(protocol)
                ''')
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_saved_packets_folder 
                    ON saved_packets(folder)
                ''')
                
                # Create traffic_logs table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS traffic_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        from_ip TEXT,
                        from_port TEXT,
                        to_address TEXT,
                        to_port TEXT,
                        method TEXT,
                        data TEXT,
                        error TEXT,
                        type TEXT DEFAULT 'OUT',
                        packet_id INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_traffic_logs_packet_id 
                    ON traffic_logs(packet_id)
                ''')
                
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_traffic_logs_time 
                    ON traffic_logs(time)
                ''')
                
                # Create connection_logs table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS connection_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        connection_id TEXT,
                        status TEXT,
                        protocol TEXT,
                        local_address TEXT,
                        remote_address TEXT,
                        details TEXT,
                        duration TEXT,
                        packet_id INTEGER,
                        message TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_connection_logs_packet_id 
                    ON connection_logs(packet_id)
                ''')
                
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_connection_logs_time 
                    ON connection_logs(time)
                ''')
                
                conn.commit()
                print(f"Database initialized successfully: {self.db_path}")
                
        except Exception as e:
            print(f"Error initializing database: {e}")
    
    def get_connection(self):
        """Get database connection"""
        return sqlite3.connect(self.db_path)
    
    def create_packet(self, packet_data: Dict) -> Optional[int]:
        """
        Create new packet
        
        Args:
            packet_data: Dictionary with packet fields (name, hex, address, port, protocol, etc.)
        
        Returns:
            Packet ID if successful, None otherwise
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO saved_packets 
                    (name, ascii_data, hex_data, target_address, target_port, protocol, folder, comment, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    packet_data['name'],
                    packet_data.get('ascii', ''),
                    packet_data['hex'],
                    packet_data['address'],
                    packet_data['port'],
                    packet_data['protocol'],
                    packet_data.get('folder', 'Default'),
                    packet_data.get('comment', ''),
                    datetime.now().isoformat()
                ))
                
                packet_id = cursor.lastrowid
                conn.commit()
                print(f"Packet created successfully with ID: {packet_id}")
                return packet_id
                
        except sqlite3.IntegrityError as e:
            print(f"Error: Packet name already exists - {e}")
            return None
        except Exception as e:
            print(f"Error creating packet: {e}")
            return None
    
    def get_all_packets(self) -> List[Dict]:
        """
        Get all saved packets
        
        Returns:
            List of packet dictionaries
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT id, name, ascii_data, hex_data, target_address, target_port, protocol, 
                           folder, comment, created_at, updated_at
                    FROM saved_packets 
                    ORDER BY created_at DESC
                ''')
                
                rows = cursor.fetchall()
                packets = []
                
                for row in rows:
                    packets.append({
                        'id': row[0],
                        'name': row[1],
                        'ascii': row[2] or '',
                        'hex': row[3],
                        'address': row[4],
                        'port': row[5],
                        'protocol': row[6],
                        'folder': row[7],
                        'comment': row[8] or '',
                        'created_at': row[9],
                        'updated_at': row[10]
                    })
                
                return packets
                
        except Exception as e:
            print(f"Error getting packets: {e}")
            return []
    
    def get_packet_by_id(self, packet_id: int) -> Optional[Dict]:
        """
        Get packet by ID
        
        Args:
            packet_id: Packet ID
        
        Returns:
            Packet dictionary or None if not found
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT id, name, ascii_data, hex_data, target_address, target_port, protocol, 
                           folder, comment, created_at, updated_at
                    FROM saved_packets 
                    WHERE id = ?
                ''', (packet_id,))
                
                row = cursor.fetchone()
                
                if row:
                    return {
                        'id': row[0],
                        'name': row[1],
                        'ascii': row[2] or '',
                        'hex': row[3],
                        'address': row[4],
                        'port': row[5],
                        'protocol': row[6],
                        'folder': row[7],
                        'comment': row[8] or '',
                        'created_at': row[9],
                        'updated_at': row[10]
                    }
                return None
                
        except Exception as e:
            print(f"Error getting packet by ID: {e}")
            return None
    
    def get_packet_by_name(self, name: str) -> Optional[Dict]:
        """
        Get packet by name
        
        Args:
            name: Packet name
        
        Returns:
            Packet dictionary or None if not found
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT id, name, ascii_data, hex_data, target_address, target_port, protocol, 
                           folder, comment, created_at, updated_at
                    FROM saved_packets 
                    WHERE name = ?
                ''', (name,))
                
                row = cursor.fetchone()
                
                if row:
                    return {
                        'id': row[0],
                        'name': row[1],
                        'ascii': row[2] or '',
                        'hex': row[3],
                        'address': row[4],
                        'port': row[5],
                        'protocol': row[6],
                        'folder': row[7],
                        'comment': row[8] or '',
                        'created_at': row[9],
                        'updated_at': row[10]
                    }
                return None
                
        except Exception as e:
            print(f"Error getting packet by name: {e}")
            return None
    
    def update_packet(self, packet_id: int, packet_data: Dict) -> bool:
        """
        Update existing packet
        
        Args:
            packet_id: Packet ID
            packet_data: Dictionary with packet fields to update
        
        Returns:
            True if successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    UPDATE saved_packets 
                    SET name = ?, ascii_data = ?, hex_data = ?, target_address = ?, 
                        target_port = ?, protocol = ?, folder = ?, comment = ?, updated_at = ?
                    WHERE id = ?
                ''', (
                    packet_data['name'],
                    packet_data.get('ascii', ''),
                    packet_data['hex'],
                    packet_data['address'],
                    packet_data['port'],
                    packet_data['protocol'],
                    packet_data.get('folder', 'Default'),
                    packet_data.get('comment', ''),
                    datetime.now().isoformat(),
                    packet_id
                ))
                
                if cursor.rowcount > 0:
                    conn.commit()
                    print(f"Packet updated successfully: {packet_id}")
                    return True
                else:
                    print(f"Packet not found: {packet_id}")
                    return False
                
        except Exception as e:
            print(f"Error updating packet: {e}")
            return False
    
    def delete_packet(self, packet_id: int) -> bool:
        """
        Delete packet by ID
        
        Args:
            packet_id: Packet ID
        
        Returns:
            True if successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('DELETE FROM saved_packets WHERE id = ?', (packet_id,))
                
                if cursor.rowcount > 0:
                    conn.commit()
                    print(f"Packet deleted successfully: {packet_id}")
                    return True
                else:
                    print(f"Packet not found: {packet_id}")
                    return False
                
        except Exception as e:
            print(f"Error deleting packet: {e}")
            return False
    
    def search_packets(self, search_term: str) -> List[Dict]:
        """
        Search packets by name, protocol, folder, or comment
        
        Args:
            search_term: Search term
        
        Returns:
            List of matching packet dictionaries
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT id, name, ascii_data, hex_data, target_address, target_port, protocol, 
                           folder, comment, created_at, updated_at
                    FROM saved_packets 
                    WHERE name LIKE ? OR protocol LIKE ? OR folder LIKE ? OR comment LIKE ?
                    ORDER BY created_at DESC
                ''', (f'%{search_term}%', f'%{search_term}%', f'%{search_term}%', f'%{search_term}%'))
                
                rows = cursor.fetchall()
                packets = []
                
                for row in rows:
                    packets.append({
                        'id': row[0],
                        'name': row[1],
                        'ascii': row[2] or '',
                        'hex': row[3],
                        'address': row[4],
                        'port': row[5],
                        'protocol': row[6],
                        'folder': row[7],
                        'comment': row[8] or '',
                        'created_at': row[9],
                        'updated_at': row[10]
                    })
                
                return packets
                
        except Exception as e:
            print(f"Error searching packets: {e}")
            return []
    
    def get_packets_by_protocol(self, protocol: str) -> List[Dict]:
        """
        Get packets by protocol
        
        Args:
            protocol: Protocol name (TCP, UDP, HTTP, HTTPS, etc.)
        
        Returns:
            List of packet dictionaries
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT id, name, ascii_data, hex_data, target_address, target_port, protocol, 
                           folder, comment, created_at, updated_at
                    FROM saved_packets 
                    WHERE protocol = ?
                    ORDER BY created_at DESC
                ''', (protocol,))
                
                rows = cursor.fetchall()
                packets = []
                
                for row in rows:
                    packets.append({
                        'id': row[0],
                        'name': row[1],
                        'ascii': row[2] or '',
                        'hex': row[3],
                        'address': row[4],
                        'port': row[5],
                        'protocol': row[6],
                        'folder': row[7],
                        'comment': row[8] or '',
                        'created_at': row[9],
                        'updated_at': row[10]
                    })
                
                return packets
                
        except Exception as e:
            print(f"Error getting packets by protocol: {e}")
            return []
    
    def get_packet_count(self) -> int:
        """
        Get total packet count
        
        Returns:
            Number of packets
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('SELECT COUNT(*) FROM saved_packets')
                count = cursor.fetchone()[0]
                
                return count
                
        except Exception as e:
            print(f"Error getting packet count: {e}")
            return 0
    
    def get_packets_by_folder(self, folder: str) -> List[Dict]:
        """
        Get packets by folder
        
        Args:
            folder: Folder name
        
        Returns:
            List of packet dictionaries in the folder
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT id, name, ascii_data, hex_data, target_address, target_port, protocol, 
                           folder, comment, created_at, updated_at
                    FROM saved_packets 
                    WHERE folder = ?
                    ORDER BY created_at DESC
                ''', (folder,))
                
                rows = cursor.fetchall()
                packets = []
                
                for row in rows:
                    packets.append({
                        'id': row[0],
                        'name': row[1],
                        'ascii': row[2] or '',
                        'hex': row[3],
                        'address': row[4],
                        'port': row[5],
                        'protocol': row[6],
                        'folder': row[7] or 'Default',
                        'comment': row[8] or '',
                        'created_at': row[9],
                        'updated_at': row[10]
                    })
                
                return packets
                
        except Exception as e:
            print(f"Error getting packets by folder: {e}")
            return []
    
    def get_all_folders(self) -> List[str]:
        """
        Get all unique folders
        
        Returns:
            List of folder names
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT DISTINCT folder 
                    FROM saved_packets 
                    WHERE folder IS NOT NULL AND folder != ''
                    ORDER BY folder
                ''')
                
                folders = [row[0] for row in cursor.fetchall()]
                return folders
                
        except Exception as e:
            print(f"Error getting folders: {e}")
            return []
    
    def add_traffic_log(self, log_data: Dict) -> Optional[int]:
        """Add a traffic log entry"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO traffic_logs 
                    (time, from_ip, from_port, to_address, to_port, method, data, error, type, packet_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    log_data.get('time', datetime.now().isoformat()),
                    log_data.get('fromIP') or log_data.get('from_ip'),
                    log_data.get('fromPort') or log_data.get('from_port'),
                    log_data.get('toAddress') or log_data.get('to_address'),
                    log_data.get('toPort') or log_data.get('to_port'),
                    log_data.get('method', ''),
                    log_data.get('data', ''),
                    log_data.get('error', ''),
                    log_data.get('type', 'OUT'),
                    log_data.get('packet_id') or log_data.get('packetId')
                ))
                
                log_id = cursor.lastrowid
                conn.commit()
                return log_id
                
        except Exception as e:
            print(f"Error adding traffic log: {e}")
            return None
    
    def get_traffic_logs(self, packet_id: Optional[int] = None, limit: int = 1000) -> List[Dict]:
        """Get traffic logs, optionally filtered by packet_id"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                if packet_id:
                    cursor.execute('''
                        SELECT id, time, from_ip, from_port, to_address, to_port, method, data, error, type, packet_id
                        FROM traffic_logs 
                        WHERE packet_id = ?
                        ORDER BY time DESC
                        LIMIT ?
                    ''', (packet_id, limit))
                else:
                    cursor.execute('''
                        SELECT id, time, from_ip, from_port, to_address, to_port, method, data, error, type, packet_id
                        FROM traffic_logs 
                        ORDER BY time DESC
                        LIMIT ?
                    ''', (limit,))
                
                rows = cursor.fetchall()
                logs = []
                
                for row in rows:
                    logs.append({
                        'id': row[0],
                        'time': row[1],
                        'fromIP': row[2] or '',
                        'fromPort': row[3] or '',
                        'toAddress': row[4] or '',
                        'toPort': row[5] or '',
                        'method': row[6] or '',
                        'data': row[7] or '',
                        'error': row[8] or '',
                        'type': row[9] or 'OUT',
                        'packet_id': row[10]
                    })
                
                return logs
                
        except Exception as e:
            print(f"Error getting traffic logs: {e}")
            return []
    
    def clear_traffic_logs(self, packet_id: Optional[int] = None) -> bool:
        """Clear traffic logs, optionally filtered by packet_id"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                if packet_id:
                    cursor.execute('DELETE FROM traffic_logs WHERE packet_id = ?', (packet_id,))
                else:
                    cursor.execute('DELETE FROM traffic_logs')
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error clearing traffic logs: {e}")
            return False
    
    def add_connection_log(self, log_data: Dict) -> Optional[int]:
        """Add a connection log entry"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO connection_logs 
                    (time, connection_id, status, protocol, local_address, remote_address, details, duration, packet_id, message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    log_data.get('time', datetime.now().isoformat()),
                    log_data.get('connectionId') or log_data.get('connection_id', ''),
                    log_data.get('status', ''),
                    log_data.get('protocol', ''),
                    log_data.get('localAddress') or log_data.get('local_address', ''),
                    log_data.get('remoteAddress') or log_data.get('remote_address', ''),
                    log_data.get('details', ''),
                    log_data.get('duration', ''),
                    log_data.get('packet_id') or log_data.get('packetId'),
                    log_data.get('message', '')
                ))
                
                log_id = cursor.lastrowid
                conn.commit()
                return log_id
                
        except Exception as e:
            print(f"Error adding connection log: {e}")
            return None
    
    def get_connection_logs(self, packet_id: Optional[int] = None, limit: int = 1000) -> List[Dict]:
        """Get connection logs, optionally filtered by packet_id"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                if packet_id:
                    cursor.execute('''
                        SELECT id, time, connection_id, status, protocol, local_address, remote_address, details, duration, packet_id, message
                        FROM connection_logs 
                        WHERE packet_id = ?
                        ORDER BY time DESC
                        LIMIT ?
                    ''', (packet_id, limit))
                else:
                    cursor.execute('''
                        SELECT id, time, connection_id, status, protocol, local_address, remote_address, details, duration, packet_id, message
                        FROM connection_logs 
                        ORDER BY time DESC
                        LIMIT ?
                    ''', (limit,))
                
                rows = cursor.fetchall()
                logs = []
                
                for row in rows:
                    logs.append({
                        'id': row[0],
                        'time': row[1],
                        'connectionId': row[2] or '',
                        'status': row[3] or '',
                        'protocol': row[4] or '',
                        'localAddress': row[5] or '',
                        'remoteAddress': row[6] or '',
                        'details': row[7] or '',
                        'duration': row[8] or '',
                        'packet_id': row[9],
                        'message': row[10] or ''
                    })
                
                return logs
                
        except Exception as e:
            print(f"Error getting connection logs: {e}")
            return []
    
    def clear_connection_logs(self, packet_id: Optional[int] = None) -> bool:
        """Clear connection logs, optionally filtered by packet_id"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                if packet_id:
                    cursor.execute('DELETE FROM connection_logs WHERE packet_id = ?', (packet_id,))
                else:
                    cursor.execute('DELETE FROM connection_logs')
                
                conn.commit()
                return True
                
        except Exception as e:
            print(f"Error clearing connection logs: {e}")
            return False


class NetworkPacketSender:
    """Service for sending network packets"""
    
    def __init__(self, db: SavedPacketsDB):
        self.db = db
        self.tcp_connections = {}  # Store persistent TCP connections
        self.lock = threading.Lock()
    
    def _format_timestamp(self) -> str:
        """Format current timestamp as [MM/DD/YYYY, HH:MM:SS.mmm AM/PM]"""
        now = datetime.now()
        # Format: [11/12/2025, 12:09:43.086 PM]
        hour = now.hour
        minute = now.minute
        second = now.second
        microsecond = now.microsecond
        millisecond = microsecond // 1000
        
        # Convert to 12-hour format
        if hour == 0:
            hour_12 = 12
            period = 'AM'
        elif hour < 12:
            hour_12 = hour
            period = 'AM'
        elif hour == 12:
            hour_12 = 12
            period = 'PM'
        else:
            hour_12 = hour - 12
            period = 'PM'
        
        return f"[{now.month:02d}/{now.day:02d}/{now.year}, {hour_12:02d}:{minute:02d}:{second:02d}.{millisecond:03d} {period}]"
    
    def _add_connection_log(self, connection_id: str, status: str, protocol: str, 
                           local_address: str, remote_address: str, message: str, 
                           packet_id: Optional[int] = None):
        """Add a connection log with formatted timestamp"""
        timestamp = self._format_timestamp()
        formatted_message = f"{timestamp} {message}"
        self.db.add_connection_log({
            'connectionId': connection_id,
            'status': status,
            'protocol': protocol,
            'localAddress': local_address,
            'remoteAddress': remote_address,
            'message': formatted_message,
            'packet_id': packet_id
        })
    
    def _add_traffic_log(self, from_ip: str, from_port: str, to_address: str, 
                        to_port: str, method: str, data: str, log_type: str, 
                        packet_id: Optional[int] = None):
        """Add a traffic log with formatted timestamp"""
        timestamp = self._format_timestamp()
        formatted_data = f"{timestamp} {data}"
        self.db.add_traffic_log({
            'fromIP': from_ip,
            'fromPort': from_port,
            'toAddress': to_address,
            'toPort': to_port,
            'method': method,
            'data': formatted_data,
            'type': log_type,
            'packet_id': packet_id
        })
    
    def _log_info(self, message: str, packet_id: Optional[int] = None):
        """Add an informational log (connection log with INFO status)"""
        timestamp = self._format_timestamp()
        log_id = f"INFO-{int(time.time() * 1000)}"
        self.db.add_connection_log({
            'connectionId': log_id,
            'status': 'INFO',
            'protocol': 'TCP',
            'localAddress': '',
            'remoteAddress': '',
            'message': f"{timestamp} {message}",
            'packet_id': packet_id
        })
    
    def hex_to_bytes(self, hex_string: str) -> bytes:
        """Convert hex string to bytes"""
        hex_string = hex_string.replace(' ', '').replace('\n', '').replace('\r', '')
        try:
            return bytes.fromhex(hex_string)
        except ValueError:
            # Try to handle invalid hex by removing invalid characters
            hex_string = ''.join(c for c in hex_string if c in '0123456789abcdefABCDEF')
            return bytes.fromhex(hex_string) if hex_string else b''
    
    def send_tcp_packet(self, packet: Dict, persistent: bool = False, packet_id: Optional[int] = None) -> Tuple[bool, str]:
        """Send TCP packet"""
        try:
            address = packet.get('address', '') or packet.get('target_address', '')
            port_str = packet.get('port', '') or packet.get('target_port', '')
            try:
                port = int(port_str)
            except (ValueError, TypeError):
                return False, f"Invalid port: {port_str}"
            hex_data = packet.get('hex', '') or packet.get('hex_data', '')
            
            if not address or not port:
                return False, "Address and port are required"
            
            # Convert hex to bytes
            data = self.hex_to_bytes(hex_data)
            if not data:
                return False, "Invalid hex data"
            
            connection_key = f"{address}:{port}"
            
            # Check for persistent connection
            sock = None
            if persistent and connection_key in self.tcp_connections:
                sock = self.tcp_connections[connection_key]
                try:
                    # Test if connection is still alive
                    sock.settimeout(1)
                    sock.send(b'')
                except:
                    # Connection is dead, remove it
                    try:
                        sock.close()
                    except:
                        pass
                    del self.tcp_connections[connection_key]
                    sock = None
            
            # Store connection_id for logging
            connection_id = None
            if persistent and connection_key in self.tcp_connections:
                # Get existing connection_id from stored connection if possible
                connection_id = f"TCP-{int(time.time() * 1000)}"
            
            # Create new connection if needed
            if sock is None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)  # 10 second timeout
                
                # Log connection establishment
                connection_id = f"TCP-{int(time.time() * 1000)}"
                self._add_connection_log(
                    connection_id=connection_id,
                    status='ESTABLISHING',
                    protocol='TCP',
                    local_address='',
                    remote_address=f"{address}:{port}",
                    message=f"[ESTABLISHING] {connection_id} (TCP): Initiating TCP connection",
                    packet_id=packet_id
                )
                
                try:
                    sock.connect((address, port))
                    
                    # Log successful connection
                    local_addr = sock.getsockname()
                    self._add_connection_log(
                        connection_id=connection_id,
                        status='ESTABLISHED',
                        protocol='TCP',
                        local_address=f"{local_addr[0]}:{local_addr[1]}",
                        remote_address=f"{address}:{port}",
                        message=f"[ESTABLISHED] {connection_id} (TCP): TCP connection established successfully",
                        packet_id=packet_id
                    )
                    
                    # Store persistent connection
                    if persistent:
                        with self.lock:
                            self.tcp_connections[connection_key] = sock
                        
                except Exception as e:
                    sock.close()
                    self._add_connection_log(
                        connection_id=connection_id,
                        status='FAILED',
                        protocol='TCP',
                        local_address='',
                        remote_address=f"{address}:{port}",
                        message=f"[FAILED] {connection_id} (TCP): Connection failed: {str(e)}",
                        packet_id=packet_id
                    )
                    return False, f"Connection failed: {str(e)}"
            else:
                # Reusing existing connection, generate connection_id for logging
                connection_id = f"TCP-{int(time.time() * 1000)}"
            
            # Send data with length prefix (4 bytes, network byte order)
            data_length = len(data)
            length_prefix = struct.pack('>I', data_length)  # Big-endian 4-byte integer
            full_data = length_prefix + data
            total_length = len(full_data)
            
            # Get local address for logging
            try:
                local_addr = sock.getsockname()
            except:
                local_addr = ('127.0.0.1', 0)
            
            # Log expected byte length (for sending)
            # Log sending hex data
            hex_clean = hex_data.replace(' ', '')
            self._add_traffic_log(
                from_ip=local_addr[0] if isinstance(local_addr, tuple) else '127.0.0.1',
                from_port=str(local_addr[1]) if isinstance(local_addr, tuple) else '0',
                to_address=address,
                to_port=str(port),
                method='TCP',
                data=f"Sending hex data: {hex_clean} (length: {data_length} bytes, with prefix: {total_length} bytes)",
                log_type='OUT',
                packet_id=packet_id
            )
            
            # Send in chunks of 512 bytes
            chunk_size = 512
            total_sent = 0
            chunks_sent = 0
            
            # Log chunk sending info
            self._log_info(f"Sending {total_length} bytes in chunks of {chunk_size} bytes", packet_id)
            
            try:
                # Send length prefix first
                prefix_sent = sock.send(length_prefix)
                total_sent += prefix_sent
                self._log_info(f"Sent length prefix: {prefix_sent} bytes", packet_id)
                
                # Send data in chunks
                while total_sent < len(full_data):
                    remaining = len(full_data) - total_sent
                    chunk = full_data[total_sent:total_sent + min(chunk_size, remaining)]
                    sent = sock.send(chunk)
                    total_sent += sent
                    chunks_sent += 1
                    if sent == 0:
                        raise socket.error("Connection closed")
                    self._log_info(f"Sent chunk: {sent} bytes, Total: {total_sent}/{total_length}", packet_id)
                
                self._log_info(f"Successfully sent {data_length} bytes in {chunks_sent} chunks", packet_id)
                self._log_info("Waiting for response...", packet_id)
                
                # Receive response
                sock.settimeout(5)  # 5 second timeout for response
                response = b''
                raw_data_received = 0
                
                try:
                    # Try to read length prefix first
                    length_prefix_bytes = sock.recv(4)
                    raw_data_received += len(length_prefix_bytes)
                    
                    if len(length_prefix_bytes) == 4:
                        expected_length = struct.unpack('>I', length_prefix_bytes)[0]
                        # Log expected byte length for received response
                        self._log_info(f"Expected byte length: {expected_length}", packet_id)
                        
                        # Read the expected length
                        while len(response) < expected_length:
                            remaining = expected_length - len(response)
                            chunk = sock.recv(min(4096, remaining))
                            if not chunk:
                                break
                            response += chunk
                            raw_data_received += len(chunk)
                    else:
                        # No length prefix, read available data
                        response = length_prefix_bytes
                        sock.settimeout(1)  # Short timeout for remaining data
                        try:
                            while True:
                                chunk = sock.recv(4096)
                                if not chunk:
                                    break
                                response += chunk
                                raw_data_received += len(chunk)
                        except socket.timeout:
                            pass
                        
                        self._log_info(f"Received raw data: {raw_data_received} bytes", packet_id)
                        self._log_info("No valid length prefix detected, treating as raw data", packet_id)
                    
                    if response:
                        self._log_info(f"Received total: {raw_data_received} bytes", packet_id)
                        
                except socket.timeout:
                    # No response received, that's okay
                    self._log_info("No response received (timeout)", packet_id)
                    pass
                except Exception as e:
                    self._log_info(f"Error receiving response: {str(e)}", packet_id)
                    return False, f"Error receiving response: {str(e)}"
                
                # Log response
                response_hex = response.hex().upper() if response else ""
                if response:
                    # Get response length and addresses for logging
                    response_len = len(response)
                    local_ip = local_addr[0] if isinstance(local_addr, tuple) else '127.0.0.1'
                    local_port = local_addr[1] if isinstance(local_addr, tuple) else '0'
                    
                    # Log the response IN traffic log (matching user's format)
                    self._add_traffic_log(
                        from_ip=address,
                        from_port=str(port),
                        to_address=local_ip,
                        to_port=str(local_port),
                        method='TCP',
                        data=f"[IN] {address}:{port} -> {local_ip}:{local_port} (TCP): Response: TCP packet sent successfully. Response: {response_hex}",
                        log_type='IN',
                        packet_id=packet_id
                    )
                    
                    # Also log the full response hex as a separate info log
                    self._log_info(f"FULL RESPONSE HEX: TCP packet sent successfully. Response: {response_hex}", packet_id)
                else:
                    self._log_info("No response data received", packet_id)
                
                # Close connection if not persistent
                if not persistent:
                    try:
                        local_addr = sock.getsockname()
                    except:
                        local_addr = ('127.0.0.1', 0)
                    sock.close()
                    self._add_connection_log(
                        connection_id=connection_id,
                        status='CLOSED',
                        protocol='TCP',
                        local_address=f"{local_addr[0]}:{local_addr[1]}" if isinstance(local_addr, tuple) else '',
                        remote_address=f"{address}:{port}",
                        message=f"[CLOSED] {connection_id} (TCP): Connection closed normally",
                        packet_id=packet_id
                    )
                else:
                    # Return connection info for persistent connections
                    return True, response_hex if response else "Sent successfully (no response)"
                
                return True, response_hex if response else "Sent successfully (no response)"
                
            except Exception as e:
                if not persistent:
                    try:
                        sock.close()
                    except:
                        pass
                return False, f"Error sending data: {str(e)}"
                
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def send_udp_packet(self, packet: Dict, packet_id: Optional[int] = None) -> Tuple[bool, str]:
        """Send UDP packet"""
        try:
            address = packet.get('address', '') or packet.get('target_address', '')
            port_str = packet.get('port', '') or packet.get('target_port', '')
            try:
                port = int(port_str)
            except (ValueError, TypeError):
                return False, f"Invalid port: {port_str}"
            hex_data = packet.get('hex', '') or packet.get('hex_data', '')
            
            if not address or not port:
                return False, "Address and port are required"
            
            # Convert hex to bytes
            data = self.hex_to_bytes(hex_data)
            if not data:
                return False, "Invalid hex data"
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(5)
            
            try:
                sock.sendto(data, (address, port))
                
                # Log sending
                local_addr = sock.getsockname()
                self.db.add_traffic_log({
                    'fromIP': local_addr[0],
                    'fromPort': str(local_addr[1]),
                    'toAddress': address,
                    'toPort': str(port),
                    'method': 'UDP',
                    'data': f"Sending hex data: {hex_data.replace(' ', '')} (length: {len(data)} bytes)",
                    'type': 'OUT',
                    'packet_id': packet_id
                })
                
                # Try to receive response
                try:
                    response, addr = sock.recvfrom(4096)
                    response_hex = response.hex().upper()
                    
                    # Log response
                    self.db.add_traffic_log({
                        'fromIP': address,
                        'fromPort': str(port),
                        'toAddress': local_addr[0],
                        'toPort': str(local_addr[1]),
                        'method': 'UDP',
                        'data': f"FULL RESPONSE HEX: {response_hex}",
                        'type': 'IN',
                        'packet_id': packet_id
                    })
                    
                    return True, response_hex
                except socket.timeout:
                    return True, "Sent successfully (no response)"
                    
            finally:
                sock.close()
                
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def send_http_packet(self, packet: Dict, packet_id: Optional[int] = None) -> Tuple[bool, str]:
        """Send HTTP packet"""
        try:
            address = packet.get('address', '') or packet.get('target_address', '')
            port_str = packet.get('port', '80') or packet.get('target_port', '80')
            try:
                port = int(port_str)
            except (ValueError, TypeError):
                port = 80  # Default to port 80 for HTTP
            hex_data = packet.get('hex', '') or packet.get('hex_data', '')
            
            if not address:
                return False, "Address is required"
            
            # Convert hex to bytes or use as ASCII
            if hex_data:
                data = self.hex_to_bytes(hex_data)
            else:
                data = packet.get('ascii', '').encode('utf-8')
            
            if not data:
                return False, "No data to send"
            
            # Use TCP for HTTP
            result = self.send_tcp_packet({
                'address': address,
                'port': port,
                'hex': data.hex()
            }, persistent=False, packet_id=packet_id)
            
            return result
            
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def send_https_packet(self, packet: Dict, packet_id: Optional[int] = None) -> Tuple[bool, str]:
        """Send HTTPS packet (SSL/TLS over TCP)"""
        try:
            address = packet.get('address', '') or packet.get('target_address', '')
            port_str = packet.get('port', '443') or packet.get('target_port', '443')
            try:
                port = int(port_str)
            except (ValueError, TypeError):
                port = 443  # Default to port 443 for HTTPS
            hex_data = packet.get('hex', '') or packet.get('hex_data', '')
            
            if not address:
                return False, "Address is required"
            
            # Convert hex to bytes or use as ASCII
            if hex_data:
                data = self.hex_to_bytes(hex_data)
            else:
                data = packet.get('ascii', '').encode('utf-8')
            
            if not data:
                return False, "No data to send"
            
            # Create TCP connection
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            
            try:
                # Wrap with SSL
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                
                sock.connect((address, port))
                ssock = context.wrap_socket(sock, server_hostname=address)
                
                # Send data
                ssock.send(data)
                
                # Log sending
                local_addr = ssock.getsockname()
                self.db.add_traffic_log({
                    'fromIP': local_addr[0],
                    'fromPort': str(local_addr[1]),
                    'toAddress': address,
                    'toPort': str(port),
                    'method': 'HTTPS',
                    'data': f"Sending hex data: {hex_data.replace(' ', '') if hex_data else data.hex().upper()} (length: {len(data)} bytes)",
                    'type': 'OUT',
                    'packet_id': packet_id
                })
                
                # Receive response
                ssock.settimeout(5)
                try:
                    response = ssock.recv(4096)
                    response_hex = response.hex().upper()
                    
                    # Log response
                    self.db.add_traffic_log({
                        'fromIP': address,
                        'fromPort': str(port),
                        'toAddress': local_addr[0],
                        'toPort': str(local_addr[1]),
                        'method': 'HTTPS',
                        'data': f"FULL RESPONSE HEX: {response_hex}",
                        'type': 'IN',
                        'packet_id': packet_id
                    })
                    
                    return True, response_hex
                except socket.timeout:
                    return True, "Sent successfully (no response)"
                    
            finally:
                try:
                    ssock.close()
                except:
                    pass
                try:
                    sock.close()
                except:
                    pass
                    
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def send_ssl_packet(self, packet: Dict, packet_id: Optional[int] = None) -> Tuple[bool, str]:
        """Send SSL packet (same as HTTPS)"""
        return self.send_https_packet(packet, packet_id)
    
    def close_tcp_connection(self, address: str, port: int) -> bool:
        """Close a persistent TCP connection"""
        connection_key = f"{address}:{port}"
        with self.lock:
            if connection_key in self.tcp_connections:
                try:
                    self.tcp_connections[connection_key].close()
                except:
                    pass
                del self.tcp_connections[connection_key]
                return True
        return False


# Initialize Blueprint
network_message_bp = Blueprint('network_message', __name__)

# Initialize database instance
saved_packets_db = SavedPacketsDB()

# Initialize packet sender
packet_sender = NetworkPacketSender(saved_packets_db)


@network_message_bp.route('', methods=['GET'], strict_slashes=False)
@network_message_bp.route('/', methods=['GET'], strict_slashes=False)
def get_all_packets():
    """Get all saved packets"""
    try:
        packets = saved_packets_db.get_all_packets()
        return jsonify({
            'success': True,
            'data': packets,
            'count': len(packets)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Define /send route FIRST (before any other POST routes) to ensure it matches correctly
@network_message_bp.route('/send', methods=['POST'], strict_slashes=False)
def send_packet():
    """Send a packet"""
    try:
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Request body must be JSON'
            }), 400
        
        data = request.get_json()
        packet = data.get('packet', {})
        
        if not packet:
            return jsonify({
                'success': False,
                'error': 'Packet data is required'
            }), 400
        
        # Get packet ID if provided
        packet_id = packet.get('id') or data.get('packet_id')
        
        # Get protocol and persistent TCP setting
        protocol = packet.get('protocol', 'TCP').upper()
        persistent_tcp = data.get('persistentTcp', False) or data.get('persistent_tcp', False)
        
        # Send packet based on protocol
        if protocol == 'TCP':
            success, message = packet_sender.send_tcp_packet(packet, persistent=persistent_tcp, packet_id=packet_id)
        elif protocol == 'UDP':
            success, message = packet_sender.send_udp_packet(packet, packet_id=packet_id)
        elif protocol == 'HTTP':
            success, message = packet_sender.send_http_packet(packet, packet_id=packet_id)
        elif protocol in ['HTTPS', 'SSL']:
            success, message = packet_sender.send_https_packet(packet, packet_id=packet_id)
        else:
            return jsonify({
                'success': False,
                'error': f'Unsupported protocol: {protocol}'
            }), 400
        
        # Get logs for this packet (includes logs just created during send)
        # Use a higher limit to ensure we get all recent logs
        traffic_logs = saved_packets_db.get_traffic_logs(packet_id=packet_id, limit=500) if packet_id else saved_packets_db.get_traffic_logs(limit=100)
        connection_logs = saved_packets_db.get_connection_logs(packet_id=packet_id, limit=500) if packet_id else saved_packets_db.get_connection_logs(limit=100)
        
        return jsonify({
            'success': success,
            'message': message,
            'data': {
                'trafficLogs': traffic_logs,
                'connectionLogs': connection_logs
            },
            'trafficLogs': traffic_logs,  # Keep for backward compatibility
            'connectionLogs': connection_logs  # Keep for backward compatibility
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Define /<int:packet_id>/send IMMEDIATELY after /send to ensure it matches before /<int:packet_id>
# This route must be defined before any other /<int:packet_id> routes
@network_message_bp.route('/<int:packet_id>/send', methods=['POST'], strict_slashes=False)
def send_packet_by_id(packet_id):
    """Send a packet by ID"""
    try:
        # Get packet from database
        packet = saved_packets_db.get_packet_by_id(packet_id)
        if not packet:
            return jsonify({
                'success': False,
                'error': 'Packet not found'
            }), 404
        
        # Get optional parameters from request
        data = request.get_json() or {}
        persistent_tcp = data.get('persistentTcp', False) or data.get('persistent_tcp', False)
        
        # Send packet
        protocol = packet.get('protocol', 'TCP').upper()
        
        if protocol == 'TCP':
            success, message = packet_sender.send_tcp_packet(packet, persistent=persistent_tcp, packet_id=packet_id)
        elif protocol == 'UDP':
            success, message = packet_sender.send_udp_packet(packet, packet_id=packet_id)
        elif protocol == 'HTTP':
            success, message = packet_sender.send_http_packet(packet, packet_id=packet_id)
        elif protocol in ['HTTPS', 'SSL']:
            success, message = packet_sender.send_https_packet(packet, packet_id=packet_id)
        else:
            return jsonify({
                'success': False,
                'error': f'Unsupported protocol: {protocol}'
            }), 400
        
        # Get logs for this packet (includes logs just created during send)
        # Use a higher limit to ensure we get all recent logs
        traffic_logs = saved_packets_db.get_traffic_logs(packet_id=packet_id, limit=500) if packet_id else saved_packets_db.get_traffic_logs(limit=100)
        connection_logs = saved_packets_db.get_connection_logs(packet_id=packet_id, limit=500) if packet_id else saved_packets_db.get_connection_logs(limit=100)
        
        return jsonify({
            'success': success,
            'message': message,
            'data': {
                'trafficLogs': traffic_logs,
                'connectionLogs': connection_logs
            },
            'trafficLogs': traffic_logs,  # Keep for backward compatibility
            'connectionLogs': connection_logs  # Keep for backward compatibility
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Define specific routes BEFORE parameterized routes to avoid conflicts
@network_message_bp.route('/traffic-logs', methods=['POST'])
def add_traffic_log():
    """Add a traffic log entry"""
    try:
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Request body must be JSON'
            }), 400
        
        data = request.get_json()
        log_id = saved_packets_db.add_traffic_log(data)
        
        if log_id:
            return jsonify({
                'success': True,
                'log_id': log_id
            }), 201
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to add traffic log'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/traffic-logs/clear', methods=['POST'])
def clear_traffic_logs():
    """Clear traffic logs"""
    try:
        data = request.get_json() or {}
        packet_id = data.get('packet_id')
        
        success = saved_packets_db.clear_traffic_logs(packet_id=packet_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Traffic logs cleared successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to clear traffic logs'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/connection-logs', methods=['POST'])
def add_connection_log():
    """Add a connection log entry"""
    try:
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Request body must be JSON'
            }), 400
        
        data = request.get_json()
        log_id = saved_packets_db.add_connection_log(data)
        
        if log_id:
            return jsonify({
                'success': True,
                'log_id': log_id
            }), 201
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to add connection log'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/connection-logs/clear', methods=['POST'])
def clear_connection_logs():
    """Clear connection logs"""
    try:
        data = request.get_json() or {}
        packet_id = data.get('packet_id')
        
        success = saved_packets_db.clear_connection_logs(packet_id=packet_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Connection logs cleared successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to clear connection logs'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/connections/close', methods=['POST'])
def close_connection():
    """Close a persistent TCP connection"""
    try:
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Request body must be JSON'
            }), 400
        
        data = request.get_json()
        address = data.get('address')
        port = data.get('port')
        
        if not address or not port:
            return jsonify({
                'success': False,
                'error': 'Address and port are required'
            }), 400
        
        success = packet_sender.close_tcp_connection(address, port)
        
        return jsonify({
            'success': success,
            'message': 'Connection closed' if success else 'Connection not found'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/status', methods=['GET'])
def get_network_status():
    """Get network service status"""
    try:
        return jsonify({
            'success': True,
            'activeConnections': len(packet_sender.tcp_connections),
            'totalTrafficLogs': len(saved_packets_db.get_traffic_logs()),
            'totalConnectionLogs': len(saved_packets_db.get_connection_logs())
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/<int:packet_id>', methods=['GET'], strict_slashes=False)
def get_packet_by_id(packet_id):
    """Get packet by ID"""
    try:
        packet = saved_packets_db.get_packet_by_id(packet_id)
        if packet:
            return jsonify({
                'success': True,
                'data': packet
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Packet not found'
            }), 404
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('', methods=['POST'], strict_slashes=False)
@network_message_bp.route('/', methods=['POST'], strict_slashes=False)
def create_packet():
    """Create new packet"""
    try:
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Request body must be JSON'
            }), 400
        
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['name', 'hex', 'address', 'port', 'protocol']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        # Map frontend field names to database field names
        db_data = {
            'name': data['name'],
            'ascii': data.get('ascii', ''),
            'hex': data['hex'],
            'address': data['address'],
            'port': data['port'],
            'protocol': data['protocol'],
            'folder': data.get('folder', 'Default'),
            'comment': data.get('comment', '')
        }
        
        # Create packet
        packet_id = saved_packets_db.create_packet(db_data)
        
        if packet_id:
            return jsonify({
                'success': True,
                'message': 'Packet created successfully',
                'packet_id': packet_id
            }), 201
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to create packet. Packet name may already exist.'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/<int:packet_id>', methods=['PUT'], strict_slashes=False)
def update_packet(packet_id):
    """Update existing packet"""
    try:
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Request body must be JSON'
            }), 400
        
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['name', 'hex', 'address', 'port', 'protocol']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        # Map frontend field names to database field names
        db_data = {
            'name': data['name'],
            'ascii': data.get('ascii', ''),
            'hex': data['hex'],
            'address': data['address'],
            'port': data['port'],
            'protocol': data['protocol'],
            'folder': data.get('folder', 'Default'),
            'comment': data.get('comment', '')
        }
        
        # Update packet
        success = saved_packets_db.update_packet(packet_id, db_data)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Packet updated successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Packet not found or update failed'
            }), 404
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/<int:packet_id>', methods=['DELETE'], strict_slashes=False)
def delete_packet(packet_id):
    """Delete packet by ID"""
    try:
        success = saved_packets_db.delete_packet(packet_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Packet deleted successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Packet not found'
            }), 404
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/search', methods=['GET'])
def search_packets():
    """Search packets by name, protocol, folder, or comment"""
    try:
        search_term = request.args.get('q', '')
        if not search_term:
            return jsonify({
                'success': False,
                'error': 'Search term is required (parameter: q)'
            }), 400
        
        packets = saved_packets_db.search_packets(search_term)
        return jsonify({
            'success': True,
            'data': packets,
            'count': len(packets),
            'search_term': search_term
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/count', methods=['GET'])
def get_packet_count():
    """Get total packet count"""
    try:
        count = saved_packets_db.get_packet_count()
        return jsonify({
            'success': True,
            'count': count
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/folders', methods=['GET'])
def get_all_folders():
    """Get all unique folders"""
    try:
        folders = saved_packets_db.get_all_folders()
        return jsonify({
            'success': True,
            'data': folders,
            'count': len(folders)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/folder/<folder>', methods=['GET'])
def get_packets_by_folder(folder):
    """Get packets by folder"""
    try:
        packets = saved_packets_db.get_packets_by_folder(folder)
        return jsonify({
            'success': True,
            'data': packets,
            'count': len(packets),
            'folder': folder
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('/protocol/<protocol>', methods=['GET'])
def get_packets_by_protocol(protocol):
    """Get packets by protocol"""
    try:
        packets = saved_packets_db.get_packets_by_protocol(protocol)
        return jsonify({
            'success': True,
            'data': packets,
            'count': len(packets),
            'protocol': protocol
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@network_message_bp.route('', methods=['OPTIONS'], strict_slashes=False)
@network_message_bp.route('/', methods=['OPTIONS'], strict_slashes=False)
@network_message_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path=None):
    """Handle CORS preflight requests"""
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200
