from flask import Blueprint, request, jsonify, current_app
import os
import posixpath
import json
import threading
import time
import hashlib
import stat
from werkzeug.utils import secure_filename
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, asdict
from pathlib import Path
import paramiko
from app.utils.ssh_helper import get_connection_by_name, get_ssh_client, get_ssh_connections

ssh_transfer_bp = Blueprint('ssh_transfer', __name__)

# === Data Classes ===

@dataclass
class TransferProgress:
    """Data class for tracking transfer progress"""
    filename: str
    total_size: int
    transferred: int
    speed: float  # bytes per second
    eta: int  # estimated time in seconds
    status: str  # 'pending', 'transferring', 'completed', 'failed', 'paused'
    error: Optional[str] = None
    md5_local: Optional[str] = None
    md5_remote: Optional[str] = None
    md5_match: Optional[bool] = None
    owner_changed: Optional[bool] = None
    original_owner: Optional[str] = None
    permissions_changed: Optional[bool] = None
    original_permissions: Optional[str] = None


# === Core Transfer Classes ===

class SSHFileTransfer:
    """Advanced SSH file transfer with resume capability and MD5 verification"""

    def __init__(self, ssh_client: paramiko.SSHClient):
        self.ssh = ssh_client
        self.sftp = None
        self.transfers: Dict[str, TransferProgress] = {}
        self.transfer_lock = threading.Lock()
        self._ensure_sftp()

    def _ensure_sftp(self):
        """Ensure SFTP connection is established"""
        if not self.sftp:
            self.sftp = self.ssh.open_sftp()

    def calculate_md5(self, file_path: str) -> str:
        """Calculate MD5 hash of a local file"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def get_remote_md5(self, remote_path: str) -> str:
        """Get MD5 hash of remote file"""
        try:
            stdin, stdout, stderr = self.ssh.exec_command(f"md5sum '{remote_path}'")
            output = stdout.read().decode().strip()
            if output:
                return output.split()[0]
        except Exception as e:
            current_app.logger.error(f"Error getting remote MD5: {e}")
        return ""

    def upload_file_with_resume(self, local_path: str, remote_path: str,
                                progress_callback: Optional[Callable] = None,
                                transfer_id: Optional[str] = None) -> Dict:
        """Upload file with resume capability"""
        try:
            local_size = os.path.getsize(local_path)
            if not transfer_id:
                transfer_id = f"{local_path}_{remote_path}"

            # Initialize transfer progress
            with self.transfer_lock:
                self.transfers[transfer_id] = TransferProgress(
                    filename=os.path.basename(local_path),
                    total_size=local_size,
                    transferred=0,
                    speed=0,
                    eta=0,
                    status='pending'
                )

            # Check if remote file exists and get its size
            remote_size = 0
            try:
                remote_attrs = self.sftp.stat(remote_path)
                remote_size = remote_attrs.st_size
            except:
                pass

            # Calculate local MD5
            local_md5 = self.calculate_md5(local_path)
            with self.transfer_lock:
                self.transfers[transfer_id].md5_local = local_md5

            # If remote file exists and sizes match, verify MD5
            if remote_size == local_size:
                remote_md5 = self.get_remote_md5(remote_path)
                if remote_md5 and remote_md5 == local_md5:
                    with self.transfer_lock:
                        self.transfers[transfer_id].status = 'completed'
                        self.transfers[transfer_id].transferred = local_size
                        self.transfers[transfer_id].md5_remote = remote_md5
                        self.transfers[transfer_id].md5_match = True

                    # File exists, verify owner and permissions
                    self._verify_and_set_owner_permissions(remote_path, transfer_id)

                    return {'success': True, 'message': 'File already exists, MD5 matches, owner and permissions verified'}

            # Start transfer
            with self.transfer_lock:
                self.transfers[transfer_id].status = 'transferring'

            start_time = time.time()
            last_update = start_time

            def progress_callback_wrapper(transferred, to_be_transferred):
                nonlocal last_update
                current_time = time.time()

                with self.transfer_lock:
                    if transfer_id in self.transfers:
                        progress = self.transfers[transfer_id]
                        progress.transferred = transferred

                        # Calculate speed
                        if current_time > last_update:
                            time_diff = current_time - last_update
                            if time_diff > 0:
                                progress.speed = transferred / time_diff
                                if progress.speed > 0:
                                    progress.eta = int((local_size - transferred) / progress.speed)

                        last_update = current_time

                        # Call external progress callback if provided
                        if progress_callback:
                            try:
                                progress_callback(transfer_id, progress)
                            except Exception as e:
                                current_app.logger.error(f"Progress callback error: {e}")

            # Upload file with progress tracking
            current_app.logger.info(f"Starting upload of {local_path} to {remote_path}")

            # Set timeout for SFTP operations
            self.sftp.get_channel().settimeout(300)  # 5 minutes timeout

            try:
                self.sftp.put(local_path, remote_path, callback=progress_callback_wrapper)
                current_app.logger.info(f"Upload completed for {local_path}")
            except Exception as upload_error:
                current_app.logger.error(f"Upload failed for {local_path}: {upload_error}")
                raise upload_error

            # Verify upload with MD5
            current_app.logger.info(f"Verifying MD5 for {remote_path}")
            remote_md5 = self.get_remote_md5(remote_path)

            # Check and change file ownership to renapp and permissions to 0755
            self._verify_and_set_owner_permissions(remote_path, transfer_id)

            with self.transfer_lock:
                if transfer_id in self.transfers:
                    progress = self.transfers[transfer_id]
                    progress.status = 'completed'
                    progress.transferred = local_size
                    progress.md5_remote = remote_md5
                    progress.md5_match = (remote_md5 == local_md5)

                    if not progress.md5_match:
                        progress.status = 'failed'
                        progress.error = 'MD5 mismatch after upload'
                        return {
                            'success': False,
                            'error': 'MD5 verification failed after upload',
                            'local_md5': local_md5,
                            'remote_md5': remote_md5
                        }

            return {'success': True, 'message': 'File uploaded successfully'}

        except Exception as e:
            current_app.logger.error(f"Upload error: {e}")
            with self.transfer_lock:
                if transfer_id in self.transfers:
                    self.transfers[transfer_id].status = 'failed'
                    self.transfers[transfer_id].error = str(e)

            return {'success': False, 'error': str(e)}

    def upload_multiple_files(self, file_list: List[Dict],
                              progress_callback: Optional[Callable] = None,
                              master_transfer_id: Optional[str] = None) -> Dict:
        """Upload multiple files sequentially"""
        results = []
        total_files = len(file_list)

        for i, file_info in enumerate(file_list):
            local_path = file_info['local_path']
            remote_path = file_info['remote_path']

            # Create unique transfer ID for this file
            file_transfer_id = f"{master_transfer_id}_file_{i}" if master_transfer_id else f"{local_path}_{remote_path}"

            # Update overall progress
            if progress_callback:
                progress_callback('overall', {
                    'current_file': i + 1,
                    'total_files': total_files,
                    'filename': os.path.basename(local_path)
                })

            # Try upload with retry
            max_retries = 2
            retry_count = 0
            result = None

            while retry_count <= max_retries:
                try:
                    result = self.upload_file_with_resume(local_path, remote_path, progress_callback, file_transfer_id)
                    if result['success']:
                        break
                    else:
                        current_app.logger.warning(f"Upload failed for {local_path}, retry {retry_count + 1}/{max_retries + 1}")
                        retry_count += 1
                        if retry_count <= max_retries:
                            time.sleep(2)  # Wait before retry
                except Exception as e:
                    current_app.logger.error(f"Upload exception for {local_path}: {e}, retry {retry_count + 1}/{max_retries + 1}")
                    retry_count += 1
                    if retry_count <= max_retries:
                        time.sleep(2)  # Wait before retry
                    else:
                        result = {'success': False, 'error': str(e)}

            results.append({
                'local_path': local_path,
                'remote_path': remote_path,
                'result': result
            })

            # Check if we should continue on failure
            if not result['success'] and not file_info.get('continue_on_failure', True):
                break

        return {
            'success': all(r['result']['success'] for r in results),
            'results': results
        }

    def get_transfer_progress(self, transfer_id: str) -> Optional[Dict]:
        """Get progress of a specific transfer as a JSON-serializable dict"""
        with self.transfer_lock:
            progress = self.transfers.get(transfer_id)
            return asdict(progress) if progress is not None else None

    def get_all_transfers(self) -> Dict[str, Dict]:
        """Get all transfer progress as JSON-serializable dicts"""
        with self.transfer_lock:
            return {transfer_id: asdict(progress) for transfer_id, progress in self.transfers.items()}

    def pause_transfer(self, transfer_id: str) -> bool:
        """Pause a transfer"""
        with self.transfer_lock:
            if transfer_id in self.transfers:
                self.transfers[transfer_id].status = 'paused'
                return True
        return False

    def resume_transfer(self, transfer_id: str) -> bool:
        """Resume a paused transfer"""
        with self.transfer_lock:
            if transfer_id in self.transfers:
                self.transfers[transfer_id].status = 'transferring'
                return True
        return False

    def cancel_transfer(self, transfer_id: str) -> bool:
        """Cancel a transfer"""
        with self.transfer_lock:
            if transfer_id in self.transfers:
                self.transfers[transfer_id].status = 'cancelled'
                return True
        return False

    def _verify_and_set_owner_permissions(self, remote_path: str, transfer_id: str):
        """Verify and set file owner and permissions."""
        try:
            current_app.logger.info(f"Checking ownership and permissions for {remote_path}")
            # Check current owner
            stdin, stdout, stderr = self.ssh.exec_command(f"ls -l '{remote_path}' | awk '{{print $3}}'")
            current_owner = stdout.read().decode().strip()

            with self.transfer_lock:
                if transfer_id in self.transfers:
                    self.transfers[transfer_id].original_owner = current_owner

            if current_owner != "renapp":
                current_app.logger.info(f"Changing owner from {current_owner} to renapp for {remote_path}")
                stdin, stdout, stderr = self.ssh.exec_command(f"sudo chown renapp:renapp '{remote_path}'")
                error = stderr.read().decode().strip()
                if error:
                    current_app.logger.error(f"Error changing owner: {error}")
                    with self.transfer_lock:
                        if transfer_id in self.transfers:
                            self.transfers[transfer_id].owner_changed = False
                else:
                    with self.transfer_lock:
                        if transfer_id in self.transfers:
                            self.transfers[transfer_id].owner_changed = True
            else:
                with self.transfer_lock:
                    if transfer_id in self.transfers:
                        self.transfers[transfer_id].owner_changed = False

            # Check current permissions
            stdin, stdout, stderr = self.ssh.exec_command(f"stat -c '%a' '{remote_path}'")
            current_perms = stdout.read().decode().strip()

            with self.transfer_lock:
                if transfer_id in self.transfers:
                    self.transfers[transfer_id].original_permissions = current_perms

            if current_perms != "755":
                current_app.logger.info(f"Changing permissions from {current_perms} to 755 for {remote_path}")
                stdin, stdout, stderr = self.ssh.exec_command(f"sudo chmod 755 '{remote_path}'")
                error = stderr.read().decode().strip()
                if error:
                    current_app.logger.error(f"Error changing permissions: {error}")
                    with self.transfer_lock:
                        if transfer_id in self.transfers:
                            self.transfers[transfer_id].permissions_changed = False
                else:
                    with self.transfer_lock:
                        if transfer_id in self.transfers:
                            self.transfers[transfer_id].permissions_changed = True
            else:
                with self.transfer_lock:
                    if transfer_id in self.transfers:
                        self.transfers[transfer_id].permissions_changed = False
        except Exception as e:
            current_app.logger.error(f"Error setting ownership/permissions: {e}")

    def cleanup_transfer(self, transfer_id: str):
        """Remove transfer from tracking"""
        with self.transfer_lock:
            if transfer_id in self.transfers:
                del self.transfers[transfer_id]


class MultiServerFileTransfer:
    """Handle file transfers to multiple SSH servers sequentially"""

    def __init__(self):
        self.transfers = {}
        self.current_server = None
        self.current_server_index = 0
        self.total_servers = 0
        self.transfer_lock = threading.Lock()
        self.server_statuses = {}  # Track status of each server: 'pending', 'transferring', 'completed', 'failed'

    def transfer_to_multiple_servers(self, file_list: List[Dict],
                                     server_connections: List[Dict],
                                     progress_callback: Optional[Callable] = None) -> Dict:
        """Transfer files to multiple servers sequentially"""
        results = {}
        self.total_servers = len(server_connections)

        # Initialize server statuses
        for server_info in server_connections:
            server_name = server_info['name']
            self.server_statuses[server_name] = 'pending'

        for i, server_info in enumerate(server_connections):
            server_name = server_info['name']
            self.current_server = server_name
            self.current_server_index = i

            # Update server status to 'transferring'
            self.server_statuses[server_name] = 'transferring'

            # Update overall progress
            if progress_callback:
                progress_callback('server_progress', {
                    'current_server': i + 1,
                    'total_servers': self.total_servers,
                    'server_name': server_name,
                    'server_statuses': self.server_statuses.copy()
                })

            # Get SSH client for this server
            client_result = get_ssh_client(server_info)

            if not client_result['success']:
                self.server_statuses[server_name] = 'failed'
                results[server_name] = {
                    'success': False,
                    'error': f"Failed to connect to {server_name}: {client_result['error']}"
                }

                # Update progress with failure
                if progress_callback:
                    progress_callback('server_status', {
                        'server_name': server_name,
                        'status': 'failed',
                        'error': client_result['error']
                    })
                continue

            # Create file transfer handler
            transfer_handler = SSHFileTransfer(client_result['client'])

            # Store transfer handler for progress tracking (before starting transfer)
            with self.transfer_lock:
                self.transfers[server_name] = transfer_handler

            # Create a wrapper for the progress callback to include server info
            def server_progress_callback(transfer_id, progress):
                if progress_callback:
                    # Add server information to the progress data
                    progress_callback(f"{server_name}:{transfer_id}", {
                        'server_name': server_name,
                        'server_index': i,
                        'total_servers': self.total_servers,
                        'progress': progress
                    })

            # Transfer files to this server
            transfer_result = transfer_handler.upload_multiple_files(file_list, server_progress_callback)

            # Update server status based on result
            self.server_statuses[server_name] = 'completed' if transfer_result['success'] else 'failed'

            results[server_name] = {
                'success': transfer_result['success'],
                'results': transfer_result['results']
            }

            # Update progress with completion status
            if progress_callback:
                progress_callback('server_status', {
                    'server_name': server_name,
                    'status': self.server_statuses[server_name],
                    'result': transfer_result
                })

        return {
            'success': all(r['success'] for r in results.values()),
            'server_results': results
        }

    def get_server_progress(self, server_name: str) -> Dict:
        """Get progress for a specific server"""
        with self.transfer_lock:
            if server_name in self.transfers:
                transfer_handler = self.transfers[server_name]
                return {
                    'transfers': transfer_handler.get_all_transfers(),
                    'status': self.server_statuses.get(server_name, 'unknown')
                }
        return {}

    def get_all_progress(self) -> Dict:
        """Get progress for all servers"""
        with self.transfer_lock:
            progress_data = {}
            for server, handler in self.transfers.items():
                progress_data[server] = handler.get_all_transfers()

            # Include metadata about current server being processed
            return {
                'servers': progress_data,
                'current_server': self.current_server,
                'current_server_index': self.current_server_index,
                'total_servers': self.total_servers,
                'server_statuses': self.server_statuses.copy()
            }

    def pause_transfer(self, server_name: str, transfer_id: str) -> bool:
        """Pause a transfer on a specific server"""
        with self.transfer_lock:
            if server_name in self.transfers:
                return self.transfers[server_name].pause_transfer(transfer_id)
        return False

    def resume_transfer(self, server_name: str, transfer_id: str) -> bool:
        """Resume a transfer on a specific server"""
        with self.transfer_lock:
            if server_name in self.transfers:
                return self.transfers[server_name].resume_transfer(transfer_id)
        return False

    def cancel_transfer(self, server_name: str, transfer_id: str) -> bool:
        """Cancel a transfer on a specific server"""
        with self.transfer_lock:
            if server_name in self.transfers:
                return self.transfers[server_name].cancel_transfer(transfer_id)
        return False


# === Global Transfer Storage ===

active_transfers = {}
transfer_lock = threading.Lock()


def get_upload_dir():
    """Get or create upload directory for temporary file storage"""
    upload_dir = os.path.join(current_app.root_path, 'uploads', 'ssh_transfers')
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


def format_file_size(size_bytes):
    """Format file size in human readable format"""
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    
    return f"{size_bytes:.1f} {size_names[i]}"


# === API Endpoints ===

@ssh_transfer_bp.route('', methods=['POST'], strict_slashes=False)
@ssh_transfer_bp.route('/', methods=['POST'], strict_slashes=False)
def upload_files():
    """
    Upload files to a single SSH server.
    Endpoint: /api/ssh/transfer
    Request JSON body:
      - connection_name: SSH connection name (required)
      - remote_path: Remote directory path (default: '/tmp')
      - files: List of file paths to upload (required)
    """
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    data = request.get_json()
    connection_name = data.get('connection_name') or data.get('connectionName')
    remote_path = data.get('remote_path', '/tmp')
    files = data.get('files', [])
    
    if not connection_name:
        return jsonify({'error': 'Missing connection_name parameter'}), 400
    
    if not files or not isinstance(files, list):
        return jsonify({'error': 'Missing or invalid files parameter'}), 400
    
    try:
        # Get connection info
        connection_info = get_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'error': f'Connection "{connection_name}" not found'}), 404
        
        # Get SSH client
        client_result = get_ssh_client(connection_info)
        if not client_result['success']:
            return jsonify({'error': f'Failed to connect: {client_result["error"]}'}), 400
        
        # Create transfer handler
        transfer_handler = SSHFileTransfer(client_result['client'])
        
        # Prepare file list
        file_list = []
        for file_path in files:
            if not os.path.exists(file_path):
                return jsonify({'error': f'File not found: {file_path}'}), 400
            
            file_list.append({
                'local_path': file_path,
                'remote_path': posixpath.join(remote_path, os.path.basename(file_path)),
                'continue_on_failure': True
            })
        
        # Generate transfer ID
        transfer_id = f"transfer_{int(time.time())}_{int(threading.current_thread().ident)}"
        
        # Store transfer info
        with transfer_lock:
            active_transfers[transfer_id] = {
                'handler': transfer_handler,
                'connection_name': connection_name,
                'files': file_list,
                'type': 'single_server',
                'status': 'pending',
                'start_time': time.time()
            }
        
        # Start transfer in background thread
        def transfer_thread():
            try:
                result = transfer_handler.upload_multiple_files(file_list, None, transfer_id)
                final_progress = transfer_handler.get_all_transfers()
                
                with transfer_lock:
                    if transfer_id in active_transfers:
                        active_transfers[transfer_id]['status'] = 'completed' if result['success'] else 'failed'
                        active_transfers[transfer_id]['result'] = result
                        active_transfers[transfer_id]['completed_time'] = time.time()
                        active_transfers[transfer_id]['cached_progress'] = final_progress
            except Exception as e:
                current_app.logger.exception(f"Transfer thread error for {transfer_id}")
                with transfer_lock:
                    if transfer_id in active_transfers:
                        active_transfers[transfer_id]['status'] = 'failed'
                        active_transfers[transfer_id]['error'] = str(e)
        
        thread = threading.Thread(target=transfer_thread)
        thread.start()
        
        return jsonify({
            'success': True,
            'message': f'Transfer started for {len(file_list)} files to {connection_name}',
            'transfer_id': transfer_id
        })
        
    except Exception as e:
        current_app.logger.exception("Upload error")
        return jsonify({'error': str(e)}), 500


@ssh_transfer_bp.route('/multiple', methods=['POST'])
def upload_to_multiple_servers():
    """
    Upload files to multiple SSH servers sequentially.
    Endpoint: /api/ssh/transfer/multiple
    Request JSON body:
      - server_names: List of SSH connection names (required)
      - remote_path: Remote directory path (default: '/tmp')
      - files: List of file paths to upload (required)
    """
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    data = request.get_json()
    server_names = data.get('server_names', [])
    remote_path = data.get('remote_path', '/tmp')
    files = data.get('files', [])
    
    if not server_names or not isinstance(server_names, list):
        return jsonify({'error': 'Missing or invalid server_names parameter'}), 400
    
    if not files or not isinstance(files, list):
        return jsonify({'error': 'Missing or invalid files parameter'}), 400
    
    try:
        # Validate all files exist
        for file_path in files:
            if not os.path.exists(file_path):
                return jsonify({'error': f'File not found: {file_path}'}), 400
        
        # Get server connections
        server_connections = []
        for server_name in server_names:
            connection_info = get_connection_by_name(server_name)
            if not connection_info:
                return jsonify({'error': f'Connection "{server_name}" not found'}), 404
            server_connections.append(connection_info)
        
        # Prepare file list
        file_list = []
        for file_path in files:
            file_list.append({
                'local_path': file_path,
                'remote_path': posixpath.join(remote_path, os.path.basename(file_path)),
                'continue_on_failure': True
            })
        
        # Create multi-server transfer handler
        multi_transfer = MultiServerFileTransfer()
        transfer_id = f"multi_transfer_{int(time.time())}_{int(threading.current_thread().ident)}"
        
        # Store transfer info
        with transfer_lock:
            active_transfers[transfer_id] = {
                'handler': multi_transfer,
                'type': 'multi_server',
                'servers': server_names,
                'files': file_list,
                'status': 'pending',
                'start_time': time.time()
            }
        
        # Start transfer in background thread
        def transfer_thread():
            try:
                multi_transfer.transfer_to_multiple_servers(file_list, server_connections, None)
                final_progress = multi_transfer.get_all_progress()
                server_progress = final_progress.get('servers', {})
                server_statuses = final_progress.get('server_statuses', {})
                
                with transfer_lock:
                    if transfer_id in active_transfers:
                        active_transfers[transfer_id]['status'] = 'completed'
                        active_transfers[transfer_id]['completed_time'] = time.time()
                        active_transfers[transfer_id]['cached_progress'] = server_progress
                        active_transfers[transfer_id]['server_statuses'] = server_statuses
            except Exception as e:
                current_app.logger.exception(f"Multi-server transfer error for {transfer_id}")
                with transfer_lock:
                    if transfer_id in active_transfers:
                        active_transfers[transfer_id]['status'] = 'failed'
                        active_transfers[transfer_id]['error'] = str(e)
        
        thread = threading.Thread(target=transfer_thread)
        thread.start()
        
        return jsonify({
            'success': True,
            'message': f'Transfer started for {len(file_list)} files to {len(server_names)} servers',
            'transfer_id': transfer_id
        })
        
    except Exception as e:
        current_app.logger.exception("Multi-server upload error")
        return jsonify({'error': str(e)}), 500


@ssh_transfer_bp.route('/progress/<transfer_id>', methods=['GET'])
def get_transfer_progress(transfer_id):
    """
    Get progress of a specific transfer.
    Endpoint: /api/ssh/transfer/progress/<transfer_id>
    """
    try:
        with transfer_lock:
            if transfer_id not in active_transfers:
                return jsonify({'error': 'Transfer not found'}), 404
            
            transfer_info = active_transfers[transfer_id]
            handler = transfer_info['handler']
            
            # Check if transfer is already completed/failed
            if transfer_info.get('status') in ['completed', 'failed']:
                if transfer_info.get('type') == 'multi_server':
                    return jsonify({
                        'success': True,
                        'type': 'multi_server',
                        'status': transfer_info['status'],
                        'servers': transfer_info['servers'],
                        'result': transfer_info.get('result'),
                        'error': transfer_info.get('error'),
                        'cached_progress': transfer_info.get('cached_progress', {}),
                        'server_statuses': transfer_info.get('server_statuses', {}),
                        'current_server': transfer_info.get('servers', [])[-1] if transfer_info.get('servers') else None,
                        'current_server_index': len(transfer_info.get('servers', [])) - 1 if transfer_info.get('servers') else 0,
                        'total_servers': len(transfer_info.get('servers', []))
                    })
                else:
                    return jsonify({
                        'success': True,
                        'type': 'single_server',
                        'status': transfer_info['status'],
                        'connection_name': transfer_info['connection_name'],
                        'result': transfer_info.get('result'),
                        'error': transfer_info.get('error'),
                        'cached_progress': transfer_info.get('cached_progress', {})
                    })
            
            if transfer_info.get('type') == 'multi_server':
                # Multi-server transfer
                progress_data = handler.get_all_progress()
                
                # Extract server progress and metadata
                server_progress = progress_data.get('servers', {})
                current_server = progress_data.get('current_server')
                current_server_index = progress_data.get('current_server_index', 0)
                total_servers = progress_data.get('total_servers', 0)
                server_statuses = progress_data.get('server_statuses', {})
                
                # Determine overall status
                overall_status = 'transferring'
                if current_server_index >= total_servers - 1:
                    # Check if all servers are completed or failed
                    all_done = all(status in ['completed', 'failed'] for status in server_statuses.values())
                    if all_done:
                        overall_status = 'completed'
                        # Mark transfer as completed for future reference
                        transfer_info['status'] = 'completed'
                        # Cache the progress data
                        transfer_info['cached_progress'] = server_progress
                        transfer_info['server_statuses'] = server_statuses
                        transfer_info['completed_time'] = time.time()
                
                return jsonify({
                    'success': True,
                    'type': 'multi_server',
                    'status': overall_status,
                    'servers': transfer_info['servers'],
                    'progress': server_progress,
                    'current_server': current_server,
                    'current_server_index': current_server_index,
                    'total_servers': total_servers,
                    'server_statuses': server_statuses
                })
            else:
                # Single server transfer
                progress = handler.get_all_transfers()
                
                # Check if all files are completed
                all_completed = all((p.get('status') == 'completed') for p in progress.values())
                if all_completed:
                    # Mark transfer as completed in active_transfers
                    transfer_info['status'] = 'completed'
                
                return jsonify({
                    'success': True,
                    'type': 'single_server',
                    'status': 'transferring' if not all_completed else 'completed',
                    'connection_name': transfer_info['connection_name'],
                    'progress': progress,
                    'all_completed': all_completed
                })
                
    except Exception as e:
        current_app.logger.exception("Error getting transfer progress")
        return jsonify({'error': str(e)}), 500


@ssh_transfer_bp.route('/pause/<transfer_id>', methods=['POST'])
def pause_transfer(transfer_id):
    """
    Pause a transfer.
    Endpoint: /api/ssh/transfer/pause/<transfer_id>
    """
    try:
        with transfer_lock:
            if transfer_id not in active_transfers:
                return jsonify({'error': 'Transfer not found'}), 404
            
            transfer_info = active_transfers[transfer_id]
            handler = transfer_info['handler']
            
            if transfer_info.get('type') == 'multi_server':
                # Pause all server transfers
                for server_name in transfer_info['servers']:
                    server_progress = handler.get_server_progress(server_name)
                    for transfer_id_inner in server_progress.get('transfers', {}):
                        handler.pause_transfer(server_name, transfer_id_inner)
            else:
                # Pause single server transfers
                for transfer_id_inner in handler.get_all_transfers():
                    handler.pause_transfer(transfer_id_inner)
            
            return jsonify({'success': True, 'message': 'Transfer paused'})
            
    except Exception as e:
        current_app.logger.exception("Error pausing transfer")
        return jsonify({'error': str(e)}), 500


@ssh_transfer_bp.route('/resume/<transfer_id>', methods=['POST'])
def resume_transfer(transfer_id):
    """
    Resume a paused transfer.
    Endpoint: /api/ssh/transfer/resume/<transfer_id>
    """
    try:
        with transfer_lock:
            if transfer_id not in active_transfers:
                return jsonify({'error': 'Transfer not found'}), 404
            
            transfer_info = active_transfers[transfer_id]
            handler = transfer_info['handler']
            
            if transfer_info.get('type') == 'multi_server':
                # Resume all server transfers
                for server_name in transfer_info['servers']:
                    server_progress = handler.get_server_progress(server_name)
                    for transfer_id_inner in server_progress.get('transfers', {}):
                        handler.resume_transfer(server_name, transfer_id_inner)
            else:
                # Resume single server transfers
                for transfer_id_inner in handler.get_all_transfers():
                    handler.resume_transfer(transfer_id_inner)
            
            return jsonify({'success': True, 'message': 'Transfer resumed'})
            
    except Exception as e:
        current_app.logger.exception("Error resuming transfer")
        return jsonify({'error': str(e)}), 500


@ssh_transfer_bp.route('/cancel/<transfer_id>', methods=['POST'])
def cancel_transfer(transfer_id):
    """
    Cancel a transfer.
    Endpoint: /api/ssh/transfer/cancel/<transfer_id>
    """
    try:
        with transfer_lock:
            if transfer_id not in active_transfers:
                return jsonify({'error': 'Transfer not found'}), 404
            
            transfer_info = active_transfers[transfer_id]
            handler = transfer_info['handler']
            
            if transfer_info.get('type') == 'multi_server':
                # Cancel all server transfers
                for server_name in transfer_info['servers']:
                    server_progress = handler.get_server_progress(server_name)
                    for transfer_id_inner in server_progress.get('transfers', {}):
                        handler.cancel_transfer(server_name, transfer_id_inner)
            else:
                # Cancel single server transfers
                for transfer_id_inner in handler.get_all_transfers():
                    handler.cancel_transfer(transfer_id_inner)
            
            # Remove from active transfers
            del active_transfers[transfer_id]
            
            return jsonify({'success': True, 'message': 'Transfer cancelled'})
            
    except Exception as e:
        current_app.logger.exception("Error cancelling transfer")
        return jsonify({'error': str(e)}), 500


@ssh_transfer_bp.route('/active', methods=['GET'])
def get_active_transfers():
    """
    Get list of active transfers.
    Endpoint: /api/ssh/transfer/active
    """
    try:
        with transfer_lock:
            transfer_list = []
            for transfer_id, transfer_info in active_transfers.items():
                transfer_list.append({
                    'transfer_id': transfer_id,
                    'type': transfer_info.get('type', 'single_server'),
                    'status': transfer_info.get('status', 'transferring'),
                    'connection_name': transfer_info.get('connection_name'),
                    'servers': transfer_info.get('servers', []),
                    'files_count': len(transfer_info.get('files', [])),
                    'result': transfer_info.get('result'),
                    'error': transfer_info.get('error')
                })
            
            return jsonify({'success': True, 'transfers': transfer_list})
            
    except Exception as e:
        current_app.logger.exception("Error getting active transfers")
        return jsonify({'error': str(e)}), 500


@ssh_transfer_bp.route('/browse-remote', methods=['POST', 'OPTIONS'])
def browse_remote():
    """
    Browse files and directories on a remote server.
    Endpoint: /api/ssh/transfer/browse-remote
    Request JSON body:
      - connection_name: SSH connection name (required)
      - path: Remote path to browse (default: '/')
    """
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        origin = request.headers.get('Origin', '*')
        response.headers.add('Access-Control-Allow-Origin', origin)
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
        response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response, 200
    
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    data = request.get_json()
    connection_name = data.get('connection_name') or data.get('connectionName')
    path = data.get('path', '/')
    
    if not connection_name:
        return jsonify({'error': 'Missing connection_name parameter'}), 400
    
    try:
        connection_info = get_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'error': f'Connection "{connection_name}" not found'}), 404
        
        client_result = get_ssh_client(connection_info)
        if not client_result['success']:
            return jsonify({'error': f'Failed to connect: {client_result["error"]}'}), 400
        
        ssh = client_result['client']
        sftp = ssh.open_sftp()
        
        # First, check if the path exists and get its attributes
        try:
            path_attrs = sftp.stat(path)
        except IOError as e:
            # Paramiko raises IOError with error codes
            error_code = getattr(e, 'errno', None)
            error_msg = str(e).lower()
            
            # Check for permission denied (errno 13 or error message)
            if error_code == 13 or 'permission denied' in error_msg or 'permission' in error_msg:
                return jsonify({
                    'success': False,
                    'error': f'Permission denied: Cannot access {path}',
                    'path': path,
                    'files': []
                }), 403
            # Check for file not found (errno 2 or error message)
            elif error_code == 2 or 'no such file' in error_msg or 'not found' in error_msg:
                return jsonify({
                    'success': False,
                    'error': f'Path not found: {path}',
                    'path': path,
                    'files': []
                }), 404
            else:
                return jsonify({
                    'success': False,
                    'error': f'Cannot access path: {str(e)}',
                    'path': path,
                    'files': []
                }), 400
        except Exception as e:
            error_msg = str(e).lower()
            if 'permission denied' in error_msg or 'permission' in error_msg:
                return jsonify({
                    'success': False,
                    'error': f'Permission denied: Cannot access {path}',
                    'path': path,
                    'files': []
                }), 403
            elif 'no such file' in error_msg or 'not found' in error_msg:
                return jsonify({
                    'success': False,
                    'error': f'Path not found: {path}',
                    'path': path,
                    'files': []
                }), 404
            return jsonify({
                'success': False,
                'error': f'Cannot access path: {str(e)}',
                'path': path,
                'files': []
            }), 400
        
        # Check if path is a directory (S_ISDIR)
        if not stat.S_ISDIR(path_attrs.st_mode):
            return jsonify({
                'success': False,
                'error': f'Path is not a directory: {path}',
                'path': path,
                'files': []
            }), 400
        
        # Try to list directory contents
        files = []
        try:
            for attr in sftp.listdir_attr(path):
                is_dir = 'd' in attr.longname
                files.append({
                    'name': attr.filename,
                    'is_dir': is_dir,
                    'size': attr.st_size,
                    'mtime': attr.st_mtime
                })
        except IOError as e:
            # Paramiko raises IOError with error codes
            error_code = getattr(e, 'errno', None)
            error_msg = str(e).lower()
            
            # Check for permission denied (errno 13 or error message)
            if error_code == 13 or 'permission denied' in error_msg or 'permission' in error_msg:
                return jsonify({
                    'success': False,
                    'error': f'Permission denied: Cannot read directory contents of {path}',
                    'path': path,
                    'files': []
                }), 403
            # Check for file not found (errno 2 or error message)
            elif error_code == 2 or 'no such file' in error_msg or 'not found' in error_msg:
                return jsonify({
                    'success': False,
                    'error': f'Directory not found or no longer exists: {path}',
                    'path': path,
                    'files': []
                }), 404
            else:
                return jsonify({
                    'success': False,
                    'error': f'Failed to list directory: {str(e)}',
                    'path': path,
                    'files': []
                }), 400
        except Exception as e:
            error_msg = str(e).lower()
            if 'permission denied' in error_msg or 'permission' in error_msg:
                return jsonify({
                    'success': False,
                    'error': f'Permission denied: Cannot read directory contents of {path}',
                    'path': path,
                    'files': []
                }), 403
            elif 'no such file' in error_msg or 'not found' in error_msg:
                return jsonify({
                    'success': False,
                    'error': f'Directory not found or no longer exists: {path}',
                    'path': path,
                    'files': []
                }), 404
            else:
                return jsonify({
                    'success': False,
                    'error': f'Failed to list directory: {str(e)}',
                    'path': path,
                    'files': []
                }), 400
        
        # Sort directories first, then files (empty list is fine)
        files.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        
        return jsonify({
            'success': True,
            'path': path,
            'files': files,
            'file_count': len(files),
            'message': f'Directory is empty' if len(files) == 0 else f'Found {len(files)} items'
        })
        
    except Exception as e:
        current_app.logger.exception("Remote browse error")
        return jsonify({'error': str(e)}), 500


@ssh_transfer_bp.route('/browse-local', methods=['POST'])
def browse_local():
    """
    Browse local files to select files for upload.
    Endpoint: /api/ssh/transfer/browse-local
    Request JSON body:
      - directory: Local directory path (default: '~')
      - extension: File extension filter (default: '.so')
    """
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    data = request.get_json()
    directory = data.get('directory', '')
    file_extension = data.get('extension', '.so')
    
    # Handle empty directory or home directory
    if not directory or directory == '~':
        directory = os.path.expanduser('~')
    elif directory.startswith('~'):
        directory = os.path.expanduser(directory)
    
    try:
        if not os.path.exists(directory):
            return jsonify({'error': f'Directory not found: {directory}'}), 404
        
        files = []
        for root, dirs, filenames in os.walk(directory):
            for filename in filenames:
                if filename.endswith(file_extension):
                    file_path = os.path.join(root, filename)
                    try:
                        file_size = os.path.getsize(file_path)
                        files.append({
                            'name': filename,
                            'path': file_path,
                            'size': file_size,
                            'size_formatted': format_file_size(file_size)
                        })
                    except:
                        continue
        
        # Sort by name
        files.sort(key=lambda x: x['name'].lower())
        
        return jsonify({'success': True, 'files': files, 'directory': directory})
        
    except Exception as e:
        current_app.logger.exception("Local browse error")
        return jsonify({'error': str(e)}), 500


@ssh_transfer_bp.route('/verify-md5', methods=['POST'])
def verify_md5():
    """
    Verify MD5 of files on remote server.
    Endpoint: /api/ssh/transfer/verify-md5
    Request JSON body:
      - connection_name: SSH connection name (required)
      - files: List of file info dicts with 'remote_path' and optionally 'local_path' (required)
    """
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    data = request.get_json()
    connection_name = data.get('connection_name') or data.get('connectionName')
    files = data.get('files', [])
    
    if not connection_name:
        return jsonify({'error': 'Missing connection_name parameter'}), 400
    
    if not files or not isinstance(files, list):
        return jsonify({'error': 'Missing or invalid files parameter'}), 400
    
    try:
        # Get connection info
        connection_info = get_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'error': f'Connection "{connection_name}" not found'}), 404
        
        # Get SSH client
        client_result = get_ssh_client(connection_info)
        if not client_result['success']:
            return jsonify({'error': f'Failed to connect: {client_result["error"]}'}), 400
        
        # Create transfer handler for MD5 verification
        transfer_handler = SSHFileTransfer(client_result['client'])
        
        results = []
        for file_info in files:
            remote_path = file_info.get('remote_path')
            local_path = file_info.get('local_path')
            
            if not remote_path:
                results.append({
                    'remote_path': remote_path,
                    'success': False,
                    'error': 'Missing remote_path'
                })
                continue
            
            try:
                # Get remote MD5
                remote_md5 = transfer_handler.get_remote_md5(remote_path)
                
                if local_path and os.path.exists(local_path):
                    # Compare with local MD5
                    local_md5 = transfer_handler.calculate_md5(local_path)
                    md5_match = (remote_md5 == local_md5)
                    
                    results.append({
                        'remote_path': remote_path,
                        'local_path': local_path,
                        'remote_md5': remote_md5,
                        'local_md5': local_md5,
                        'md5_match': md5_match,
                        'success': True
                    })
                else:
                    # Just return remote MD5
                    results.append({
                        'remote_path': remote_path,
                        'remote_md5': remote_md5,
                        'success': True
                    })
                    
            except Exception as e:
                results.append({
                    'remote_path': remote_path,
                    'success': False,
                    'error': str(e)
                })
        
        return jsonify({'success': True, 'results': results})
        
    except Exception as e:
        current_app.logger.exception("MD5 verification error")
        return jsonify({'error': str(e)}), 500


@ssh_transfer_bp.route('/cleanup', methods=['POST'])
def cleanup_old_transfers():
    """
    Clean up old completed/failed transfers.
    Endpoint: /api/ssh/transfer/cleanup
    Request JSON body (optional):
      - current_transfer_id: Transfer ID to keep (exclude from cleanup)
    """
    try:
        with transfer_lock:
            current_time = time.time()
            cleaned_count = 0
            
            # Get current transfer ID from request (if any)
            data = request.get_json() or {} if request.is_json else {}
            current_transfer_id = data.get('current_transfer_id')
            
            # Find transfers to clean up (older than 1 hour)
            transfers_to_clean = []
            for transfer_id, transfer_info in active_transfers.items():
                # Skip the current transfer being displayed
                if current_transfer_id and transfer_id == current_transfer_id:
                    continue
                    
                if transfer_info.get('status') in ['completed', 'failed']:
                    # Check if transfer is older than 1 hour
                    if 'completed_time' not in transfer_info:
                        transfer_info['completed_time'] = current_time
                    elif current_time - transfer_info['completed_time'] > 3600:  # 1 hour
                        transfers_to_clean.append(transfer_id)
            
            # Clean up old transfers
            for transfer_id in transfers_to_clean:
                del active_transfers[transfer_id]
                cleaned_count += 1
            
            return jsonify({
                'success': True,
                'message': f'Cleaned up {cleaned_count} old transfers',
                'cleaned_count': cleaned_count,
                'remaining_count': len(active_transfers)
            })
            
    except Exception as e:
        current_app.logger.exception("Cleanup error")
        return jsonify({'error': str(e)}), 500


@ssh_transfer_bp.route('', methods=['OPTIONS'], strict_slashes=False)
@ssh_transfer_bp.route('/', methods=['OPTIONS'], strict_slashes=False)
@ssh_transfer_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path=None):
    """Handle CORS preflight requests"""
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200

