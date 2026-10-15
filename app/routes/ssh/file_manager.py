from flask import Blueprint, request, jsonify, send_file, current_app
import os
import io
import zipfile
import shutil
import stat
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
import paramiko
from werkzeug.utils import secure_filename
from app.utils.ssh_helper import get_connection_by_name, get_ssh_client, get_ssh_connections

file_manager_bp = Blueprint('file_manager', __name__)

# === Helper Functions ===

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


def format_permissions(mode):
    """Format file permissions in rwx format"""
    permissions = []
    for i in range(9):
        if mode & (1 << (8 - i)):
            permissions.append('rwx'[i % 3])
        else:
            permissions.append('-')
    return ''.join(permissions)


def parse_ls_output(output):
    """Parse ls -l output and return list of file info"""
    files = []
    lines = output.strip().split('\n')
    
    for line in lines:
        if not line.strip() or line.startswith('total'):
            continue
        
        current_app.logger.debug(f"Parsing line: {line}")
        
        # Use regex to parse ls -l output
        pattern = r'^(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\w{3})\s+(\d{1,2})\s+(\S+)\s+(.*)$'
        match = re.match(pattern, line)
        
        if not match:
            alt_pattern = r'^(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\w{3})\s+(\d{1,2})\s+(\S+)\s+(.*?)(?:\s+->\s+(.*))?$'
            match = re.match(alt_pattern, line)
        
        if match:
            permissions = match.group(1)
            links = match.group(2)
            owner = match.group(3)
            group = match.group(4)
            size = match.group(5)
            month = match.group(6)
            day = match.group(7)
            time_or_year = match.group(8)
            
            # Derive filename robustly
            base_line = re.sub(r"\s+->\s+.*$", "", line).rstrip()
            parts = base_line.split(maxsplit=8)
            if len(parts) >= 9:
                filename = parts[8].strip()
            else:
                filename = match.group(9).strip() if match.lastindex >= 9 else ''
            
            # Handle symbolic links
            if ' -> ' in filename:
                filename = filename.split(' -> ')[0].strip()
            
            filename = filename.strip()
            
            # Fallback extraction if filename is still empty
            if not filename:
                try:
                    base_line = re.sub(r"\s+->\s+.*$", "", line).rstrip()
                    parts = base_line.split(maxsplit=8)
                    if len(parts) >= 9:
                        filename = parts[8].strip()
                    if not filename:
                        m = re.search(r"([^\s]+)$", base_line)
                        if m:
                            filename = m.group(1)
                    if filename:
                        filename = os.path.basename(filename)
                except Exception as e:
                    current_app.logger.warning(f"Filename fallback failed: {e}")
                    filename = ''
            
            if not filename:
                current_app.logger.warning(f"Filename still empty after all fallbacks. Raw line: {line}")
                continue
            
            # Skip . and .. entries
            if filename in ['.', '..']:
                continue
            
            # Extract only the filename from the full path
            if '/' in filename:
                filename = os.path.basename(filename)
            
            # Convert size to integer
            try:
                size_int = int(size) if size.isdigit() else 0
            except ValueError:
                size_int = 0
            
            # Parse date and time to get mtime
            try:
                if ':' in time_or_year:  # Time format (HH:MM) - current year
                    current_year = datetime.now().year
                    date_time_str = f"{current_year} {month} {day} {time_or_year}"
                    mtime = datetime.strptime(date_time_str, '%Y %b %d %H:%M').timestamp()
                else:  # Year format (YYYY) - different year
                    date_time_str = f"{time_or_year} {month} {day}"
                    mtime = datetime.strptime(date_time_str, '%Y %b %d').timestamp()
            except ValueError as e:
                current_app.logger.warning(f"Failed to parse date '{month} {day}' time '{time_or_year}': {e}")
                mtime = datetime.now().timestamp()
            
            file_info = {
                'name': filename,
                'is_dir': permissions.startswith('d'),
                'size': size_int,
                'mtime': mtime,
                'permissions': permissions,
                'owner': owner,
                'group': group,
                'modified': datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
            }
            files.append(file_info)
        else:
            current_app.logger.warning(f"Failed to parse line: {line}")
    
    return files


def add_directory_to_zip(sftp, dir_path, zip_path, zip_file):
    """Recursively add directory contents to ZIP file"""
    try:
        for item in sftp.listdir_attr(dir_path):
            item_path = os.path.join(dir_path, item.filename).replace('\\', '/')
            item_zip_path = os.path.join(zip_path, item.filename).replace('\\', '/')
            
            if 'd' in item.longname:
                add_directory_to_zip(sftp, item_path, item_zip_path, zip_file)
            else:
                file_data = io.BytesIO()
                sftp.getfo(item_path, file_data)
                file_data.seek(0)
                zip_file.writestr(item_zip_path, file_data.getvalue())
    except Exception as e:
        current_app.logger.warning(f"Failed to add directory {dir_path} to ZIP: {e}")


def delete_directory_recursive(sftp, dir_path):
    """Recursively delete a directory and its contents"""
    try:
        for item in sftp.listdir_attr(dir_path):
            item_path = os.path.join(dir_path, item.filename).replace('\\', '/')
            is_dir = stat.S_ISDIR(item.st_mode)
            
            if is_dir:
                delete_directory_recursive(sftp, item_path)
            else:
                sftp.remove(item_path)
                current_app.logger.debug(f"Deleted file: {item_path}")
        
        sftp.rmdir(dir_path)
        current_app.logger.debug(f"Deleted directory: {dir_path}")
    except Exception as e:
        current_app.logger.warning(f"Failed to delete directory {dir_path}: {e}")
        raise


# === API Endpoints ===

@file_manager_bp.route('', methods=['POST', 'OPTIONS'], strict_slashes=False)
@file_manager_bp.route('/', methods=['POST', 'OPTIONS'], strict_slashes=False)
def list_directory():
    """
    List directory contents with detailed information.
    Endpoint: /api/ssh/file-manager
    Request JSON body:
      - connection_name: SSH connection name (required)
      - path: Remote path to browse (default: '/')
      - filter: Optional filter pattern (e.g., '*.so', '*.txt,*.log')
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
    filter_pattern = data.get('filter', '')
    
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
        
        try:
            # Always list the base directory first to ensure folders are present
            base_cmd = f'ls -l "{path}"'
            current_app.logger.debug(f"Executing base command for folders: {base_cmd}")
            stdin, stdout, stderr = ssh.exec_command(base_cmd)
            base_output = stdout.read().decode('utf-8')
            base_error = stderr.read().decode('utf-8')
            
            if base_error:
                current_app.logger.error(f"Base ls -l error: {base_error}")
                return jsonify({'error': f'Error listing directory: {base_error}'}), 400
            
            base_entries = parse_ls_output(base_output)
            base_dirs = [e for e in base_entries if e.get('is_dir')]
            
            files = []
            warning = None
            
            # Build ls command with filter if provided
            if filter_pattern.strip():
                patterns = [p.strip() for p in filter_pattern.split(',') if p.strip()]
                if patterns:
                    if len(patterns) == 1:
                        # Single pattern
                        base_path = '/' if path == '/' else path.rstrip('/')
                        pattern = patterns[0]
                        
                        if base_path == '/':
                            command = f'find / -maxdepth 1 -type f -iname "{pattern}" -exec ls -l {{}} \\;'
                        else:
                            command = f'find "{base_path}" -maxdepth 1 -type f -iname "{pattern}" -exec ls -l {{}} \\;'
                        
                        current_app.logger.debug(f"Executing filter command: {command}")
                        stdin, stdout, stderr = ssh.exec_command(command)
                        output = stdout.read().decode('utf-8')
                        error = stderr.read().decode('utf-8')
                        
                        if error and ("No such file or directory" in error or "find:" in error):
                            return jsonify({
                                'success': True,
                                'path': path,
                                'files': base_dirs,
                                'file_count': len(base_dirs),
                                'warning': f'No files found matching filter "{pattern}". Showing folders only.'
                            })
                        elif error:
                            return jsonify({'error': f'Error listing directory: {error}'}), 400
                        
                        files = [f for f in parse_ls_output(output) if not f.get('is_dir')]
                        
                        if not files:
                            return jsonify({
                                'success': True,
                                'path': path,
                                'files': base_dirs,
                                'file_count': len(base_dirs),
                                'warning': f'No files found matching filter "{pattern}". Showing folders only.'
                            })
                    else:
                        # Multiple patterns
                        all_files = []
                        failed_patterns = []
                        
                        for pattern in patterns:
                            base_path = '/' if path == '/' else path.rstrip('/')
                            if base_path == '/':
                                command = f'find / -maxdepth 1 -type f -iname "{pattern}" -exec ls -l {{}} \\;'
                            else:
                                command = f'find "{base_path}" -maxdepth 1 -type f -iname "{pattern}" -exec ls -l {{}} \\;'
                            
                            stdin, stdout, stderr = ssh.exec_command(command)
                            output = stdout.read().decode('utf-8')
                            error = stderr.read().decode('utf-8')
                            
                            if error and "No such file or directory" in error:
                                failed_patterns.append(pattern)
                            elif error:
                                failed_patterns.append(pattern)
                            else:
                                pattern_files = [f for f in parse_ls_output(output) if not f.get('is_dir')]
                                all_files.extend(pattern_files)
                        
                        # Remove duplicates
                        seen_files = set()
                        unique_files = []
                        for file in all_files:
                            if file['name'] not in seen_files:
                                seen_files.add(file['name'])
                                unique_files.append(file)
                        
                        files = unique_files
                        
                        if failed_patterns:
                            if len(failed_patterns) == len(patterns):
                                files = []
                                warning = f'No files found matching any of the patterns: {", ".join(failed_patterns)}. Showing folders only.'
                            else:
                                warning = f'Some filter patterns not found: {", ".join(failed_patterns)}. Showing files matching other patterns.'
                else:
                    files = [f for f in base_entries if not f.get('is_dir')]
            else:
                files = [f for f in base_entries if not f.get('is_dir')]
            
            # Combine directories and files
            name_seen = set()
            combined = []
            for d in base_dirs:
                if d.get('name') and d['name'] not in name_seen:
                    name_seen.add(d['name'])
                    combined.append(d)
            for f in files:
                if f.get('name') and f['name'] not in name_seen:
                    name_seen.add(f['name'])
                    combined.append(f)
            
            combined.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
            
            response_data = {
                'success': True,
                'path': path,
                'files': combined,
                'file_count': len(combined)
            }
            
            if warning:
                response_data['warning'] = warning
            
            return jsonify(response_data)
            
        except Exception as e:
            current_app.logger.exception("Error listing directory")
            return jsonify({'error': f'Failed to list directory: {str(e)}'}), 500
        finally:
            sftp.close()
            
    except Exception as e:
        current_app.logger.exception("Directory listing error")
        return jsonify({'error': str(e)}), 500


@file_manager_bp.route('/disk-usage', methods=['POST'])
def get_disk_usage():
    """
    Get disk usage information for the server.
    Endpoint: /api/ssh/file-manager/disk-usage
    Request JSON body:
      - connection_name: SSH connection name (required)
    """
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    data = request.get_json()
    connection_name = data.get('connection_name') or data.get('connectionName')
    
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
        
        # Execute df command to get disk usage
        stdin, stdout, stderr = ssh.exec_command('df -h /')
        output = stdout.read().decode('utf-8')
        error = stderr.read().decode('utf-8')
        
        if error:
            return jsonify({'error': f'Error getting disk usage: {error}'}), 400
        
        # Parse df output
        lines = output.strip().split('\n')
        if len(lines) < 2:
            return jsonify({'error': 'Invalid disk usage output'}), 400
        
        data_line = lines[1].split()
        if len(data_line) < 6:
            return jsonify({'error': 'Invalid disk usage data'}), 400
        
        total_str = data_line[1]
        used_str = data_line[2]
        available_str = data_line[3]
        percent_str = data_line[4].replace('%', '')
        
        # Convert to bytes
        def parse_size(size_str):
            size_str = size_str.upper()
            if 'G' in size_str:
                return float(size_str.replace('G', '')) * 1024 * 1024 * 1024
            elif 'M' in size_str:
                return float(size_str.replace('M', '')) * 1024 * 1024
            elif 'K' in size_str:
                return float(size_str.replace('K', '')) * 1024
            else:
                return float(size_str)
        
        total = parse_size(total_str)
        used = parse_size(used_str)
        available = parse_size(available_str)
        percent = float(percent_str)
        
        usage_info = {
            'total': total,
            'used': used,
            'available': available,
            'percent': percent,
            'total_formatted': total_str,
            'used_formatted': used_str,
            'available_formatted': available_str
        }
        
        return jsonify({'success': True, 'usage': usage_info})
        
    except Exception as e:
        current_app.logger.exception("Disk usage error")
        return jsonify({'error': str(e)}), 500


@file_manager_bp.route('/download', methods=['POST'])
def download_file():
    """
    Download a single file from remote server.
    Endpoint: /api/ssh/file-manager/download
    Request JSON body:
      - connection_name: SSH connection name (required)
      - path: Full path to the file (required)
    """
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    data = request.get_json()
    connection_name = data.get('connection_name') or data.get('connectionName')
    path = data.get('path')
    
    if not connection_name or not path:
        return jsonify({'error': 'Missing connection_name or path parameter'}), 400
    
    try:
        connection_info = get_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'error': f'Connection "{connection_name}" not found'}), 404
        
        client_result = get_ssh_client(connection_info)
        if not client_result['success']:
            return jsonify({'error': f'Failed to connect: {client_result["error"]}'}), 400
        
        ssh = client_result['client']
        sftp = ssh.open_sftp()
        
        try:
            # Check if file exists
            file_attr = sftp.stat(path)
            if not file_attr:
                return jsonify({'error': 'File not found'}), 404
            
            # Create a BytesIO object to hold the file data
            file_data = io.BytesIO()
            sftp.getfo(path, file_data)
            file_data.seek(0)
            
            # Get filename from path
            filename = os.path.basename(path)
            
            return send_file(
                file_data,
                as_attachment=True,
                download_name=filename,
                mimetype='application/octet-stream'
            )
            
        except IOError as e:
            error_code = getattr(e, 'errno', None)
            if error_code == 2:
                return jsonify({'error': 'File not found'}), 404
            elif error_code == 13:
                return jsonify({'error': 'Permission denied'}), 403
            else:
                return jsonify({'error': f'Failed to download file: {str(e)}'}), 400
        except Exception as e:
            current_app.logger.exception("Download error")
            return jsonify({'error': f'Failed to download file: {str(e)}'}), 500
        finally:
            sftp.close()
            
    except Exception as e:
        current_app.logger.exception("File download error")
        return jsonify({'error': str(e)}), 500


@file_manager_bp.route('/bulk-download', methods=['POST'])
def bulk_download():
    """
    Download multiple files as a ZIP archive.
    Endpoint: /api/ssh/file-manager/bulk-download
    Request JSON body:
      - connection_name: SSH connection name (required)
      - path: Base directory path (required)
      - files: Array of filenames to download (required)
    """
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    data = request.get_json()
    connection_name = data.get('connection_name') or data.get('connectionName')
    path = data.get('path')
    files = data.get('files', [])
    
    if not connection_name or not path or not files:
        return jsonify({'error': 'Missing required parameters'}), 400
    
    try:
        connection_info = get_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'error': f'Connection "{connection_name}" not found'}), 404
        
        client_result = get_ssh_client(connection_info)
        if not client_result['success']:
            return jsonify({'error': f'Failed to connect: {client_result["error"]}'}), 400
        
        ssh = client_result['client']
        sftp = ssh.open_sftp()
        
        try:
            # Create a ZIP file in memory
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for filename in files:
                    file_path = os.path.join(path, filename).replace('\\', '/')
                    
                    try:
                        # Check if it's a directory
                        file_attr = sftp.stat(file_path)
                        if 'd' in file_attr.longname:
                            add_directory_to_zip(sftp, file_path, filename, zip_file)
                        else:
                            file_data = io.BytesIO()
                            sftp.getfo(file_path, file_data)
                            file_data.seek(0)
                            zip_file.writestr(filename, file_data.getvalue())
                    except Exception as e:
                        current_app.logger.warning(f"Failed to add {filename} to ZIP: {e}")
                        continue
            
            zip_buffer.seek(0)
            
            return send_file(
                zip_buffer,
                as_attachment=True,
                download_name='selected_files.zip',
                mimetype='application/zip'
            )
            
        except Exception as e:
            current_app.logger.exception("Bulk download error")
            return jsonify({'error': f'Failed to create ZIP: {str(e)}'}), 500
        finally:
            sftp.close()
            
    except Exception as e:
        current_app.logger.exception("Bulk download error")
        return jsonify({'error': str(e)}), 500


@file_manager_bp.route('/rename', methods=['POST'])
def rename_file():
    """
    Rename a file or directory.
    Endpoint: /api/ssh/file-manager/rename
    Request JSON body:
      - connection_name: SSH connection name (required)
      - path: Base directory path (required)
      - old_name: Current filename (required)
      - new_name: New filename (required)
    """
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    data = request.get_json()
    connection_name = data.get('connection_name') or data.get('connectionName')
    path = data.get('path')
    old_name = data.get('old_name')
    new_name = data.get('new_name')
    
    if not all([connection_name, path, old_name, new_name]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    try:
        connection_info = get_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'error': f'Connection "{connection_name}" not found'}), 404
        
        client_result = get_ssh_client(connection_info)
        if not client_result['success']:
            return jsonify({'error': f'Failed to connect: {client_result["error"]}'}), 400
        
        ssh = client_result['client']
        sftp = ssh.open_sftp()
        
        try:
            old_path = os.path.join(path, old_name).replace('\\', '/')
            new_path = os.path.join(path, new_name).replace('\\', '/')
            
            # Check if old file exists
            try:
                sftp.stat(old_path)
            except IOError as e:
                error_code = getattr(e, 'errno', None)
                if error_code == 2:
                    return jsonify({'error': 'Source file not found'}), 404
                else:
                    return jsonify({'error': f'Cannot access source file: {str(e)}'}), 400
            
            # Check if new file already exists
            try:
                sftp.stat(new_path)
                return jsonify({'error': 'Target file already exists'}), 400
            except IOError:
                pass  # This is what we want
            
            # Rename the file
            sftp.rename(old_path, new_path)
            
            return jsonify({'success': True, 'message': f'Renamed "{old_name}" to "{new_name}"'})
            
        except Exception as e:
            current_app.logger.exception("Rename error")
            return jsonify({'error': f'Failed to rename file: {str(e)}'}), 500
        finally:
            sftp.close()
            
    except Exception as e:
        current_app.logger.exception("File rename error")
        return jsonify({'error': str(e)}), 500


@file_manager_bp.route('/delete', methods=['POST'])
def delete_files():
    """
    Delete files or directories.
    Endpoint: /api/ssh/file-manager/delete
    Request JSON body:
      - connection_name: SSH connection name (required)
      - path: Base directory path (required)
      - files: Array of filenames to delete (required)
    """
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    data = request.get_json()
    connection_name = data.get('connection_name') or data.get('connectionName')
    path = data.get('path')
    files = data.get('files', [])
    
    if not all([connection_name, path, files]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    try:
        connection_info = get_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'error': f'Connection "{connection_name}" not found'}), 404
        
        client_result = get_ssh_client(connection_info)
        if not client_result['success']:
            return jsonify({'error': f'Failed to connect: {client_result["error"]}'}), 400
        
        ssh = client_result['client']
        sftp = ssh.open_sftp()
        
        deleted_files = []
        errors = []
        
        try:
            for filename in files:
                file_path = os.path.join(path, filename).replace('\\', '/')
                
                try:
                    # Get file attributes to check if it's a directory
                    file_attr = sftp.stat(file_path)
                    is_dir = stat.S_ISDIR(file_attr.st_mode)
                    
                    if is_dir:
                        delete_directory_recursive(sftp, file_path)
                    else:
                        sftp.remove(file_path)
                    
                    deleted_files.append(filename)
                    current_app.logger.debug(f"Successfully deleted: {file_path}")
                    
                except Exception as e:
                    error_msg = f"{filename}: {str(e)}"
                    errors.append(error_msg)
                    current_app.logger.warning(f"Failed to delete {file_path}: {e}")
                    continue
            
            response_data = {
                'success': True,
                'message': f'Deleted {len(deleted_files)} items',
                'deleted_count': len(deleted_files)
            }
            
            if errors:
                response_data['warnings'] = errors
            
            return jsonify(response_data)
                
        except Exception as e:
            current_app.logger.exception("Delete error")
            return jsonify({'error': f'Failed to delete files: {str(e)}'}), 500
        finally:
            sftp.close()
            
    except Exception as e:
        current_app.logger.exception("File delete error")
        return jsonify({'error': str(e)}), 500


@file_manager_bp.route('/create-folder', methods=['POST'])
def create_folder():
    """
    Create a new directory.
    Endpoint: /api/ssh/file-manager/create-folder
    Request JSON body:
      - connection_name: SSH connection name (required)
      - path: Base directory path (required)
      - folder_name: Name of the new folder (required)
    """
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    data = request.get_json()
    connection_name = data.get('connection_name') or data.get('connectionName')
    path = data.get('path')
    folder_name = data.get('folder_name')
    
    if not all([connection_name, path, folder_name]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    try:
        connection_info = get_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'error': f'Connection "{connection_name}" not found'}), 404
        
        client_result = get_ssh_client(connection_info)
        if not client_result['success']:
            return jsonify({'error': f'Failed to connect: {client_result["error"]}'}), 400
        
        ssh = client_result['client']
        sftp = ssh.open_sftp()
        
        try:
            folder_path = os.path.join(path, folder_name).replace('\\', '/')
            
            # Check if folder already exists
            try:
                sftp.stat(folder_path)
                return jsonify({'error': 'Folder already exists'}), 400
            except IOError:
                pass  # This is what we want
            
            # Create the directory
            sftp.mkdir(folder_path)
            
            return jsonify({'success': True, 'message': f'Created folder "{folder_name}"'})
            
        except Exception as e:
            current_app.logger.exception("Create folder error")
            return jsonify({'error': f'Failed to create folder: {str(e)}'}), 500
        finally:
            sftp.close()
            
    except Exception as e:
        current_app.logger.exception("Create folder error")
        return jsonify({'error': str(e)}), 500


@file_manager_bp.route('/upload', methods=['POST'])
def upload_files():
    """
    Upload files to the remote server.
    Endpoint: /api/ssh/file-manager/upload
    Request form data:
      - connection_name: SSH connection name (required)
      - path: Remote directory path (required)
      - files: File uploads (required)
    """
    connection_name = request.form.get('connection_name')
    path = request.form.get('path')
    files = request.files.getlist('files')
    
    if not connection_name or not path or not files:
        return jsonify({'error': 'Missing required parameters'}), 400
    
    try:
        connection_info = get_connection_by_name(connection_name)
        if not connection_info:
            return jsonify({'error': f'Connection "{connection_name}" not found'}), 404
        
        client_result = get_ssh_client(connection_info)
        if not client_result['success']:
            return jsonify({'error': f'Failed to connect: {client_result["error"]}'}), 400
        
        ssh = client_result['client']
        sftp = ssh.open_sftp()
        
        uploaded_files = []
        errors = []
        
        try:
            for file in files:
                if file.filename == '':
                    continue
                
                # Secure the filename
                filename = secure_filename(file.filename)
                file_path = os.path.join(path, filename).replace('\\', '/')
                
                try:
                    # Upload the file
                    sftp.putfo(file, file_path)
                    uploaded_files.append(filename)
                    
                except Exception as e:
                    errors.append(f"{file.filename}: {str(e)}")
                    current_app.logger.warning(f"Failed to upload {file.filename}: {e}")
                    continue
            
            response_data = {
                'success': True,
                'message': f'Uploaded {len(uploaded_files)} files',
                'uploaded_count': len(uploaded_files)
            }
            
            if errors:
                response_data['warnings'] = errors
            
            return jsonify(response_data)
                
        except Exception as e:
            current_app.logger.exception("Upload error")
            return jsonify({'error': f'Failed to upload files: {str(e)}'}), 500
        finally:
            sftp.close()
            
    except Exception as e:
        current_app.logger.exception("File upload error")
        return jsonify({'error': str(e)}), 500


@file_manager_bp.route('/connections', methods=['GET'])
def get_connections():
    """
    Get list of available SSH connections.
    Endpoint: /api/ssh/file-manager/connections
    """
    try:
        connections = get_ssh_connections()
        
        connection_list = []
        for conn in connections:
            connection_list.append({
                'name': conn['name'],
                'host': conn['host'],
                'port': conn['port'],
                'username': conn['user']
            })
        
        return jsonify({'success': True, 'connections': connection_list})
        
    except Exception as e:
        current_app.logger.exception("Get connections error")
        return jsonify({'error': str(e)}), 500


@file_manager_bp.route('', methods=['OPTIONS'], strict_slashes=False)
@file_manager_bp.route('/', methods=['OPTIONS'], strict_slashes=False)
@file_manager_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path=None):
    """Handle CORS preflight requests"""
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200

