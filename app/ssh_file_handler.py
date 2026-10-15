import os
import io
import zipfile
from datetime import datetime
from typing import List, Dict, Any, Optional
import paramiko
from flask import send_file, jsonify

class SSHFileHandler:
    def __init__(self, ssh_client: paramiko.SSHClient):
        self.ssh = ssh_client
        self.sftp = None
    
    def _ensure_sftp(self):
        """Ensure SFTP connection is established"""
        if not self.sftp:
            self.sftp = self.ssh.open_sftp()
    
    def list_directory(self, path: str) -> Dict[str, Any]:
        """List contents of a directory"""
        try:
            self._ensure_sftp()
            items = []
            
            for entry in self.sftp.listdir_attr(path):
                item = {
                    'name': entry.filename,
                    'size': entry.st_size,
                    'type': 'directory' if self._is_dir(entry) else 'file',
                    'permissions': self._format_permissions(entry.st_mode),
                    'modified': datetime.fromtimestamp(entry.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                }
                items.append(item)
            
            # Sort: directories first, then files, both alphabetically
            items.sort(key=lambda x: (x['type'] != 'directory', x['name'].lower()))
            
            return {'success': True, 'items': items}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def download_file(self, path: str) -> Optional[io.BytesIO]:
        """Download a single file"""
        try:
            self._ensure_sftp()
            data = io.BytesIO()
            self.sftp.getfo(path, data)
            data.seek(0)
            return data
        except Exception as e:
            return None
    
    def download_as_zip(self, items: List[Dict[str, str]]) -> Optional[io.BytesIO]:
        """Download multiple files as ZIP"""
        try:
            self._ensure_sftp()
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for item in items:
                    path = item['path']
                    name = item['name']
                    
                    # If it's a directory, add all its contents recursively
                    if self._is_path_dir(path):
                        self._add_dir_to_zip(zip_file, path, name)
                    else:
                        data = io.BytesIO()
                        self.sftp.getfo(path, data)
                        data.seek(0)
                        zip_file.writestr(name, data.getvalue())
            
            zip_buffer.seek(0)
            return zip_buffer
        except Exception as e:
            return None
    
    def create_folder(self, path: str) -> Dict[str, Any]:
        """Create a new folder"""
        try:
            self._ensure_sftp()
            self.sftp.mkdir(path)
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def rename(self, old_path: str, new_path: str) -> Dict[str, Any]:
        """Rename/move a file or directory"""
        try:
            self._ensure_sftp()
            self.sftp.rename(old_path, new_path)
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def delete(self, path: str) -> Dict[str, Any]:
        """Delete a file or directory"""
        try:
            self._ensure_sftp()
            if self._is_path_dir(path):
                self._rmdir_recursive(path)
            else:
                self.sftp.remove(path)
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def upload_files(self, path: str, files: List[Any]) -> Dict[str, Any]:
        """Upload multiple files"""
        try:
            self._ensure_sftp()
            for file in files:
                target_path = os.path.join(path, file.filename)
                file_obj = io.BytesIO(file.read())
                self.sftp.putfo(file_obj, target_path)
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _is_dir(self, entry: paramiko.SFTPAttributes) -> bool:
        """Check if an SFTP entry is a directory"""
        return entry.st_mode & 0o40000 == 0o40000
    
    def _is_path_dir(self, path: str) -> bool:
        """Check if a path is a directory"""
        try:
            return self._is_dir(self.sftp.stat(path))
        except:
            return False
    
    def _format_permissions(self, mode: int) -> str:
        """Format file permissions in Unix style"""
        perms = ['---', '--x', '-w-', '-wx', 'r--', 'r-x', 'rw-', 'rwx']
        result = ''
        
        # Type
        if mode & 0o40000:  # Directory
            result += 'd'
        elif mode & 0o100000:  # Regular file
            result += '-'
        else:  # Something else
            result += '?'
        
        # User, Group, Others
        for shift in [6, 3, 0]:
            result += perms[(mode >> shift) & 0o7]
        
        return result
    
    def _rmdir_recursive(self, path: str):
        """Recursively remove a directory and its contents"""
        for entry in self.sftp.listdir_attr(path):
            entry_path = os.path.join(path, entry.filename)
            if self._is_dir(entry):
                self._rmdir_recursive(entry_path)
            else:
                self.sftp.remove(entry_path)
        self.sftp.rmdir(path)
    
    def _add_dir_to_zip(self, zip_file: zipfile.ZipFile, path: str, arcname: str):
        """Add a directory and its contents to a ZIP file"""
        for entry in self.sftp.listdir_attr(path):
            entry_path = os.path.join(path, entry.filename)
            entry_arcname = os.path.join(arcname, entry.filename)
            
            if self._is_dir(entry):
                self._add_dir_to_zip(zip_file, entry_path, entry_arcname)
            else:
                data = io.BytesIO()
                self.sftp.getfo(entry_path, data)
                data.seek(0)
                zip_file.writestr(entry_arcname, data.getvalue()) 