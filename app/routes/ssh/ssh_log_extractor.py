import os
import re
import uuid
import hashlib
import paramiko
import tempfile
from datetime import datetime, timedelta
from flask import current_app, Blueprint, request, jsonify, send_file, Response, stream_with_context
import threading
import time
import zipfile
import shutil
import json

class SSHLogExtractor:
    """Class for handling SSH log extraction operations"""
    
    def __init__(self, host='10.132.130.77', username='renapp', password='renapp@123', connection_timeout=30, retry_count=3, verbose_logging=False, context_before=100, context_after=100, log_path_current='/app/jalin_renaissance/', log_path_backup='/app/renaissance_log_backup_jalin/', file_name_pattern='renaissance{date}*.txt'):
        self.host = host
        self.username = username
        self.password = password
        self.connection_timeout = connection_timeout
        self.retry_count = retry_count
        self.verbose_logging = verbose_logging
        self.context_before = context_before
        self.context_after = context_after
        self.ssh_client = None
        self.sftp_client = None
        self.task_id = str(uuid.uuid4())
        self.status = "Initializing"
        self.progress = 0
        self.success = False
        self.result_files = []
        self.error_message = None
        self.files_found = 0
        # Additional initial state
        self.current_file = ""
        self.compress_files = False
        self.log_entries = []
        self.file_line_ranges = {}  # Store file and line ranges
        self.created_at = datetime.now()  # Timestamp when task was created
        self.search_uuid = None  # Store the UUID being searched for
        # Configurable log paths and file pattern
        self.log_path_current = log_path_current
        self.log_path_backup = log_path_backup
        self.file_name_pattern = file_name_pattern
        self.current_log_path = self.log_path_current  # Default path
        self.using_backup = False  # Flag to indicate if we're using backup files
        # Cancellation support
        self.cancel_event = threading.Event()

    def request_cancel(self):
        """Signal cancellation for this task"""
        try:
            self.cancel_event.set()
            self.add_log_entry("Cancellation requested", is_important=True)
            self.status = "Cancellation requested"
        except Exception:
            # Be robust even if logging fails
            pass

    def is_cancelled(self):
        """Check whether cancellation was requested"""
        return self.cancel_event.is_set()

    def cleanup_extracted_txt(self):
        """Delete only extracted .txt result files in current path"""
        try:
            cleanup_cmd = f"rm -f {self.current_log_path}*.txt"
            self.add_log_entry(f"Executing cleanup of extracted files: {cleanup_cmd}", is_important=True)
            stdin, stdout, stderr = self.ssh_client.exec_command(cleanup_cmd)
            _ = stdout.read().decode().strip()
            err = stderr.read().decode().strip()
            if err and "No such file or directory" not in err:
                self.add_log_entry(f"Cleanup stderr: {err}")
        except Exception as e:
            self.add_log_entry(f"Cleanup exception: {str(e)}")

    def cleanup_remote_generated_files(self, include_zip=True):
        """Safely remove only the generated artifacts for this task on the remote server.
        - Removes extracted sections: {uuid}_*.txt
        - Optionally removes the temporary remote zip created during compression
        - Never touches any .tar.gz backup archives
        """
        try:
            if not self.search_uuid:
                return
            # Sweep both the active path and the default current path, in case path switched (e.g., backup mode)
            candidate_paths = []
            if getattr(self, 'current_log_path', None):
                candidate_paths.append(self.current_log_path)
            if getattr(self, 'log_path_current', None) and self.log_path_current not in candidate_paths:
                candidate_paths.append(self.log_path_current)

            for base in candidate_paths:
                # Delete only our task's extracted .txt sections
                txt_pattern = f"{base}{self.search_uuid}_*.txt"
                txt_cmd = f"rm -f {txt_pattern}"
                self.add_log_entry(f"Cleaning remote txt artifacts: {txt_cmd}", is_important=True)
                stdin, stdout, stderr = self.ssh_client.exec_command(txt_cmd)
                out = stdout.read().decode().strip()
                err = stderr.read().decode().strip()
                if err and 'No such file or directory' not in err:
                    self.add_log_entry(f"txt cleanup stderr ({base}): {err}")

                if include_zip:
                    zip_filename = f"{self.search_uuid or 'logs'}_logs.zip"
                    remote_zip_path = f"{base}{zip_filename}"
                    zip_cmd = f"rm -f {remote_zip_path}"
                    self.add_log_entry(f"Cleaning remote zip artifact: {zip_cmd}", is_important=True)
                    stdin, stdout, stderr = self.ssh_client.exec_command(zip_cmd)
                    out = stdout.read().decode().strip()
                    err = stderr.read().decode().strip()
                    if err and 'No such file or directory' not in err:
                        self.add_log_entry(f"zip cleanup stderr ({base}): {err}")
        except Exception as e:
            self.add_log_entry(f"Remote artifact cleanup exception: {str(e)}", is_error=True)

    def cleanup_remote_txt_zip_all(self):
        """Aggressively delete ALL .txt and .zip files in relevant remote folders.
        Notes:
        - This will NOT touch any .tar.gz backups.
        - Used when user wants a guaranteed clean directory after downloads.
        """
        try:
            candidate_paths = []
            if getattr(self, 'current_log_path', None):
                candidate_paths.append(self.current_log_path)
            if getattr(self, 'log_path_current', None) and self.log_path_current not in candidate_paths:
                candidate_paths.append(self.log_path_current)
            for base in candidate_paths:
                # Remove all .txt and .zip, but not .tar.gz
                cmd = f"rm -f {base}*.txt {base}*.zip"
                self.add_log_entry(f"Aggressive cleanup: {cmd}", is_important=True)
                stdin, stdout, stderr = self.ssh_client.exec_command(cmd)
                _ = stdout.read().decode().strip()
                err = stderr.read().decode().strip()
                if err and 'No such file or directory' not in err:
                    self.add_log_entry(f"Aggressive cleanup stderr ({base}): {err}")
        except Exception as e:
            self.add_log_entry(f"Aggressive cleanup exception: {str(e)}", is_error=True)

    def add_log_entry(self, message, is_error=False, is_important=False):
        """Add a log entry with timestamp
        
        Args:
            message (str): The log message
            is_error (bool): Whether this is an error message
            is_important (bool): Whether this is an important message that should always be shown
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        entry = f"[{timestamp}] {'ERROR: ' if is_error else ''}{message}"
        # Add to log_entries if verbose logging is enabled, it's an error, or it's important
        if self.verbose_logging or is_error or is_important:
            self.log_entries.append(entry)
        # Print to console if verbose logging is enabled or it's an error
        if self.verbose_logging or is_error:
            print(entry)
        return entry

    def connect(self):
        """Establish SSH connection to the server with retries"""
        retry_attempts = 0
        while retry_attempts <= self.retry_count:
            try:
                if retry_attempts > 0:
                    self.status = f"Retrying connection ({retry_attempts}/{self.retry_count})..."
                    self.add_log_entry(f"Retrying connection attempt {retry_attempts}/{self.retry_count}", is_important=True)
                    time.sleep(2)
                else:
                    self.status = "Connecting to SSH server..."
                    self.add_log_entry(f"Connecting to SSH server {self.host} as user {self.username}...", is_important=True)

                self.ssh_client = paramiko.SSHClient()
                self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.add_log_entry("Setting SSH host key policy to AutoAddPolicy")

                self.ssh_client.connect(
                    hostname=self.host,
                    username=self.username,
                    password=self.password,
                    timeout=self.connection_timeout
                )
                self.add_log_entry(f"Successfully established SSH connection to {self.host}", is_important=True)

                self.sftp_client = self.ssh_client.open_sftp()
                self.add_log_entry("Opened SFTP connection for file transfers", is_important=True)

                self.progress = 10
                self.status = "Connected to SSH server"
                return True
            except Exception as e:
                retry_attempts += 1
                error_msg = f"Connection attempt {retry_attempts} failed: {str(e)}"
                self.add_log_entry(error_msg, is_error=True)
                if retry_attempts > self.retry_count:
                    final_error_msg = f"Failed to connect to SSH server after {self.retry_count + 1} attempts: {str(e)}"
                    self.error_message = final_error_msg
                    self.status = self.error_message
                    self.add_log_entry(final_error_msg, is_error=True)
                    return False
        return False

    def find_log_files_with_uuid(self, search_uuid, log_date=None):
        """Find log files containing the specified UUID and get line numbers"""
        try:
            self.search_uuid = search_uuid
            self.status = "Determining log path based on date..."
            self.progress = 10

            # Always try current path first; if no matches and date provided, fallback to backup path
            self.current_log_path = self.log_path_current
            self.using_backup = False
            if log_date:
                self.add_log_entry(f"Using log date filter: {log_date}", is_important=True)
                try:
                    input_date = datetime.strptime(log_date, '%Y-%m-%d').date()
                    today = datetime.now().date()
                    yesterday = today - timedelta(days=1)
                    self.add_log_entry(f"Comparing dates - Input: {input_date}, Today: {today}, Yesterday: {yesterday}", is_important=True)
                except ValueError as e:
                    self.add_log_entry(f"Error parsing date {log_date}: {str(e)}. Proceeding with current logs path.", is_error=True)
            else:
                self.add_log_entry(f"No date specified, using current logs path: {self.current_log_path}", is_important=True)

            self.status = "Changing to log directory..."
            self.progress = 15
            self.add_log_entry(f"Executing command: cd {self.current_log_path}", is_important=True)
            stdin, stdout, stderr = self.ssh_client.exec_command(f'cd {self.current_log_path}')
            error = stderr.read().decode().strip()
            if error:
                self.error_message = f"Failed to change directory: {error}"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                return []

            self.add_log_entry(f"Successfully changed to directory: {self.current_log_path}", is_important=True)
            self.status = "Searching for log files containing UUID..."
            self.progress = 20

            file_pattern = self.file_name_pattern
            if log_date:
                date_obj = datetime.strptime(log_date, '%Y-%m-%d')
                formatted_date = date_obj.strftime('%Y%m%d')
                file_pattern = file_pattern.format(date=formatted_date)
                self.add_log_entry(f"Using date-specific file pattern: {file_pattern}", is_important=True)
            else:
                file_pattern = file_pattern.format(date="*")
                self.add_log_entry(f"Using default file pattern: {file_pattern}", is_important=True)

            self.add_log_entry(f"Searching for UUID: {search_uuid}", is_important=True)
            grep_command = f"grep -anH '{search_uuid}' {file_pattern}"
            sed_command = f"sed -n '1p;$p'"
            full_command = f"cd {self.current_log_path} && {grep_command} | {sed_command}"
            self.add_log_entry(f"Executing command: {full_command}", is_important=True)
            stdin, stdout, stderr = self.ssh_client.exec_command(full_command)
            output = stdout.read().decode().strip()
            error = stderr.read().decode().strip()
            if error:
                self.add_log_entry(f"Command returned error: {error}", is_error=True)
            if not output:
                # Fallback: if a specific date was provided, try in backup path for that date
                if log_date:
                    try:
                        date_obj = datetime.strptime(log_date, '%Y-%m-%d')
                        formatted_date = date_obj.strftime('%Y%m%d')
                        self.add_log_entry("No matches in current path. Falling back to backup path...", is_important=True)
                        return self.find_log_files_in_backup(search_uuid, formatted_date)
                    except Exception:
                        pass
                self.error_message = "No matching log entries found for this UUID"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                return []

            self.add_log_entry(f"Command output: {output}")
            lines = output.split('\n')
            files_with_ranges = {}
            for line in lines:
                match = re.match(r'([^:]+):(\d+):', line)
                if match:
                    filename = match.group(1)
                    line_num = int(match.group(2))
                    if filename not in files_with_ranges:
                        files_with_ranges[filename] = {
                            'first_line': line_num,
                            'last_line': line_num
                        }
                    else:
                        files_with_ranges[filename]['last_line'] = line_num

            log_files = []
            for filename, rng in files_with_ranges.items():
                first_line_num = rng['first_line']
                last_line_num = rng['last_line']
                start_line = max(1, first_line_num - self.context_before)
                end_line = last_line_num + self.context_after
                self.file_line_ranges[filename] = {
                    'start_line': start_line,
                    'end_line': end_line,
                    'first_match_line': first_line_num,
                    'last_match_line': last_line_num
                }
                self.add_log_entry(
                    f"Computed range for {filename}: start_line={start_line}, end_line={end_line} "
                    f"(first_match={first_line_num}, last_match={last_line_num}, "
                    f"ctx_before={self.context_before}, ctx_after={self.context_after})",
                    is_important=True
                )
                log_files.append(filename)

            self.files_found = len(log_files)
            file_list = ", ".join(log_files) if log_files else "No files found"
            self.add_log_entry(f"Found {len(log_files)} log files containing UUID: {file_list}", is_important=True)
            self.status = f"Found {len(log_files)} log files containing UUID"
            self.progress = 30
            return log_files
        except Exception as e:
            error_msg = f"Error finding log files: {str(e)}"
            self.error_message = error_msg
            self.status = self.error_message
            self.add_log_entry(error_msg, is_error=True)
            return []

    def find_log_files_in_backup(self, search_uuid, log_date):
        """Find log files in backup archives for older dates"""
        try:
            self.status = "Checking backup directory..."
            self.progress = 12
            self.add_log_entry(f"Looking for backup files with date: {log_date}", is_important=True)
            backup_date_dir = f"{self.log_path_backup}{log_date}"
            check_dir_cmd = f"[ -d {backup_date_dir} ] && echo 'exists' || echo 'not exists'"
            self.add_log_entry(f"Checking if backup directory exists: {backup_date_dir}", is_important=True)
            stdin, stdout, stderr = self.ssh_client.exec_command(check_dir_cmd)
            dir_exists = stdout.read().decode().strip()
            if dir_exists != 'exists':
                self.error_message = f"Backup directory {backup_date_dir} does not exist"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                return []

            base_name = self.file_name_pattern.split('{')[0].rstrip('*').rstrip('.txt')
            backup_file = f"{base_name}_log_backup_{log_date}.tar.gz"
            backup_file_path = f"{backup_date_dir}/{backup_file}"
            check_file_cmd = f"[ -f {backup_file_path} ] && echo 'exists' || echo 'not exists'"
            self.add_log_entry(f"Checking if backup archive exists: {backup_file_path}", is_important=True)
            stdin, stdout, stderr = self.ssh_client.exec_command(check_file_cmd)
            file_exists = stdout.read().decode().strip()
            if file_exists != 'exists':
                self.error_message = f"Backup archive {backup_file_path} does not exist"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                return []

            self.status = "Extracting backup archive..."
            self.progress = 13
            self.add_log_entry(f"Extracting backup archive: {backup_file_path}", is_important=True)
            extract_cmd = f"tar -xvf {backup_file_path} -C {backup_date_dir}"
            self.add_log_entry(f"Executing extraction command: {extract_cmd}", is_important=True)
            stdin, stdout, stderr = self.ssh_client.exec_command(extract_cmd)
            exit_status = stdout.channel.recv_exit_status()
            extract_error = stderr.read().decode().strip()
            if exit_status != 0:
                self.error_message = f"Failed to extract backup archive: {extract_error}"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                return []

            self.add_log_entry(f"Successfully extracted backup archive to {backup_date_dir}", is_important=True)
            self.status = "Searching for UUID in backup files..."
            self.progress = 15
            self.current_log_path = backup_date_dir + "/"
            backup_file_pattern = self.file_name_pattern.format(date=log_date)
            grep_command = f"grep -anH '{search_uuid}' {backup_file_pattern}"
            sed_command = f"sed -n '1p;$p'"
            full_command = f"cd {self.current_log_path} && {grep_command} | {sed_command}"
            self.add_log_entry(f"Executing command: {full_command}", is_important=True)
            stdin, stdout, stderr = self.ssh_client.exec_command(full_command)
            output = stdout.read().decode().strip()
            error = stderr.read().decode().strip()
            if error and "No such file or directory" not in error:
                self.add_log_entry(f"Command returned error: {error}", is_error=True)
            if not output:
                self.error_message = "No matching log entries found for this UUID in backup files"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                cleanup_cmd = f"rm -f {self.current_log_path}*.txt"
                stdin, stdout, stderr = self.ssh_client.exec_command(cleanup_cmd)
                return []

            self.add_log_entry(f"Command output: {output}")
            lines = output.split('\n')
            files_with_ranges = {}
            for line in lines:
                match = re.match(r'([^:]+):(\d+):', line)
                if match:
                    filename = match.group(1)
                    line_num = int(match.group(2))
                    if filename not in files_with_ranges:
                        files_with_ranges[filename] = {
                            'first_line': line_num,
                            'last_line': line_num
                        }
                    else:
                        files_with_ranges[filename]['last_line'] = line_num

            log_files = []
            for filename, rng in files_with_ranges.items():
                first_line_num = rng['first_line']
                last_line_num = rng['last_line']
                start_line = max(1, first_line_num - self.context_before)
                end_line = last_line_num + self.context_after
                self.file_line_ranges[filename] = {
                    'start_line': start_line,
                    'end_line': end_line,
                    'first_match_line': first_line_num,
                    'last_match_line': last_line_num
                }
                self.add_log_entry(
                    f"Computed range for {filename} (backup): start_line={start_line}, end_line={end_line} "
                    f"(first_match={first_line_num}, last_match={last_line_num}, "
                    f"ctx_before={self.context_before}, ctx_after={self.context_after})",
                    is_important=True
                )
                log_files.append(filename)

            self.files_found = len(log_files)
            file_list = ", ".join(log_files) if log_files else "No files found"
            self.add_log_entry(f"Found {len(log_files)} backup log files containing UUID: {file_list}", is_important=True)
            self.status = f"Found {len(log_files)} backup log files containing UUID"
            self.progress = 30
            return log_files
        except Exception as e:
            error_msg = f"Error finding log files in backup: {str(e)}"
            self.error_message = error_msg
            self.status = self.error_message
            self.add_log_entry(error_msg, is_error=True)
            return []

    def extract_log_sections(self, log_files, search_uuid):
        """Extract relevant log sections from each file using line numbers"""
        results = []
        total_files = len(log_files)
        if total_files == 0:
            self.status = "No log files found containing the UUID"
            self.add_log_entry("No log files found containing the UUID", is_important=True)
            return results
        progress_per_file = 60 / total_files
        for idx, log_file in enumerate(log_files):
            try:
                self.current_file = log_file
                file_progress_base = 30 + (idx * progress_per_file)
                self.status = f"Processing file {idx+1}/{total_files}: {log_file}"
                self.add_log_entry(f"Processing file {idx+1}/{total_files}: {log_file}", is_important=True)
                self.progress = int(file_progress_base)
                if log_file not in self.file_line_ranges:
                    self.add_log_entry(f"No line range information for {log_file}, skipping", is_error=True)
                    continue
                line_range = self.file_line_ranges[log_file]
                start_line = line_range['start_line']
                end_line = line_range['end_line']
                first_match_line = line_range['first_match_line']
                last_match_line = line_range['last_match_line']
                self.add_log_entry(f"Extracting lines {start_line} to {end_line} from {log_file}", is_important=True)
                self.add_log_entry(f"Original UUID matches were at lines {first_match_line} and {last_match_line}")
                output_filename = f"{search_uuid}_{idx+1}.txt"
                self.add_log_entry(f"Output filename: {output_filename}", is_important=True)
                sed_command = f"sed -n '{start_line},{end_line}p' {log_file} > {output_filename}"
                full_command = f"cd {self.current_log_path} && {sed_command}"
                self.add_log_entry(f"Executing command: {full_command}", is_important=True)
                stdin, stdout, stderr = self.ssh_client.exec_command(full_command)
                exit_status = stdout.channel.recv_exit_status()
                error = stderr.read().decode().strip()
                if exit_status != 0:
                    self.error_message = f"Error extracting log section: {error}"
                    self.add_log_entry(self.error_message, is_error=True)
                    continue
                check_command = f"ls -la {output_filename}"
                full_command = f"cd {self.current_log_path} && {check_command}"
                self.add_log_entry(f"Executing command: {full_command}")
                stdin, stdout, stderr = self.ssh_client.exec_command(full_command)
                file_info = stdout.read().decode().strip()
                self.add_log_entry(f"File created: {file_info}", is_important=True)
                wc_command = f"wc -l {output_filename}"
                full_command = f"cd {self.current_log_path} && {wc_command}"
                self.add_log_entry(f"Executing command: {full_command}")
                stdin, stdout, stderr = self.ssh_client.exec_command(full_command)
                line_count = stdout.read().decode().strip()
                self.add_log_entry(f"Line count in output file: {line_count}", is_important=True)
                self.progress = int(file_progress_base + (progress_per_file * 0.7))
                results.append({
                    'remote_file': output_filename,
                    'local_file': output_filename
                })
                self.add_log_entry(f"Successfully extracted log section to {output_filename}", is_important=True)
                self.progress = int(file_progress_base + progress_per_file)
            except Exception as e:
                error_msg = f"Error processing file {log_file}: {str(e)}"
                self.error_message = error_msg
                self.status = self.error_message
                self.add_log_entry(error_msg, is_error=True)
        return results

    def download_files(self, file_mappings, download_dir):
        """Download extracted log files to local directory"""
        if not file_mappings:
            self.status = "No files to download"
            self.add_log_entry("No files to download", is_important=True)
            return []
        try:
            os.makedirs(download_dir, exist_ok=True)
            self.add_log_entry(f"Created local download directory: {download_dir}", is_important=True)
            if self.compress_files:
                return self.compress_and_download_remote(file_mappings, download_dir)
            total_files = len(file_mappings)
            progress_per_file = 10 / total_files
            downloaded_files = []
            for idx, file_map in enumerate(file_mappings):
                remote_file = file_map['remote_file']
                local_file = os.path.join(download_dir, file_map['local_file'])
                self.status = f"Downloading file {idx+1}/{total_files}: {remote_file}"
                self.current_file = remote_file
                self.progress = 90 + int(idx * progress_per_file)
                remote_path = f"{self.current_log_path}{remote_file}"
                self.add_log_entry(f"Downloading file from {remote_path} to {local_file}", is_important=True)
                self.sftp_client.get(remote_path, local_file)
                remote_md5_cmd = f"md5sum {remote_path} | cut -d' ' -f1"
                self.add_log_entry(f"Executing command: {remote_md5_cmd}")
                stdin, stdout, stderr = self.ssh_client.exec_command(remote_md5_cmd)
                remote_md5 = stdout.read().decode().strip()
                with open(local_file, 'rb') as f:
                    local_md5 = hashlib.md5(f.read()).hexdigest()
                self.add_log_entry(f"Remote MD5: {remote_md5}", is_important=True)
                self.add_log_entry(f"Local MD5: {local_md5}", is_important=True)
                if remote_md5 == local_md5:
                    self.add_log_entry(f"MD5 verification successful for {remote_file}", is_important=True)
                    delete_cmd = f"rm {remote_path}"
                    self.add_log_entry(f"Executing cleanup command: {delete_cmd}", is_important=True)
                    stdin, stdout, stderr = self.ssh_client.exec_command(delete_cmd)
                    exit_status = stdout.channel.recv_exit_status()
                    if exit_status == 0:
                        self.add_log_entry(f"Cleanup successful: Deleted remote file {remote_file}", is_important=True)
                    else:
                        error = stderr.read().decode().strip()
                        self.add_log_entry(f"Cleanup failed for {remote_file}: {error}", is_error=True, is_important=True)
                    downloaded_files.append({
                        'name': os.path.basename(local_file),
                        'path': local_file,
                        'md5': local_md5
                    })
                else:
                    self.add_log_entry(f"MD5 verification failed for {remote_file}", is_error=True, is_important=True)
                    self.add_log_entry(f"Expected MD5: {remote_md5}, Actual MD5: {local_md5}", is_error=True, is_important=True)
            # Cleanup FIRST, then mark success
            try:
                # User requested deleting all .txt and .zip: do an aggressive cleanup sweep
                self.cleanup_remote_txt_zip_all()
            except Exception:
                pass

            self.progress = 100
            self.status = f"Successfully downloaded {len(downloaded_files)} files (remote cleaned)"
            self.success = True
            self.result_files = downloaded_files
            return downloaded_files
        except Exception as e:
            error_msg = f"Error downloading files: {str(e)}"
            self.error_message = error_msg
            self.status = self.error_message
            self.add_log_entry(error_msg, is_error=True)
            return []

    def compress_and_download_remote(self, file_mappings, download_dir):
        """Compress files on remote server and download the compressed file"""
        try:
            self.status = "Compressing files on remote server..."
            self.add_log_entry(f"Starting remote compression of {len(file_mappings)} files", is_important=True)
            remote_files = [file_map['remote_file'] for file_map in file_mappings]
            file_list = " ".join(remote_files)
            zip_filename = f"{self.search_uuid or 'logs'}_logs.zip"
            remote_zip_path = f"{self.current_log_path}{zip_filename}"
            self.add_log_entry(f"Creating ZIP archive on remote server: {remote_zip_path}", is_important=True)
            zip_command = f"cd {self.current_log_path} && zip -j {zip_filename} {file_list}"
            stdin, stdout, stderr = self.ssh_client.exec_command(zip_command)
            exit_status = stdout.channel.recv_exit_status()
            zip_error = stderr.read().decode().strip()
            if exit_status != 0:
                self.error_message = f"Failed to create ZIP archive: {zip_error}"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                return []
            # Verify zip file exists
            check_cmd = f"ls -la {remote_zip_path}"
            full_cmd = f"cd {self.current_log_path} && {check_cmd}"
            self.add_log_entry(f"Executing command: {full_cmd}")
            stdin, stdout, stderr = self.ssh_client.exec_command(full_cmd)
            file_info = stdout.read().decode().strip()
            if not file_info:
                self.add_log_entry("ZIP file not created on remote server", is_error=True)
                return []
            # Download zip
            self.progress = 90
            self.add_log_entry(f"Downloading ZIP file from {remote_zip_path} to {download_dir}", is_important=True)
            local_zip_path = os.path.join(download_dir, zip_filename)
            self.sftp_client.get(remote_zip_path, local_zip_path)
            # Verify MD5
            remote_md5_cmd = f"md5sum {remote_zip_path} | cut -d' ' -f1"
            self.add_log_entry(f"Executing command: {remote_md5_cmd}")
            stdin, stdout, stderr = self.ssh_client.exec_command(remote_md5_cmd)
            remote_md5 = stdout.read().decode().strip()
            with open(local_zip_path, 'rb') as f:
                local_md5 = hashlib.md5(f.read()).hexdigest()
            if remote_md5 != local_md5:
                self.add_log_entry("MD5 verification failed for zip", is_error=True, is_important=True)
                return []
            self.add_log_entry("MD5 verification success for zip", is_important=True)
            # Cleanup FIRST, then return result metadata
            try:
                # Aggressive cleanup per user request
                self.cleanup_remote_txt_zip_all()
            except Exception:
                pass

            return [{
                'name': os.path.basename(local_zip_path),
                'path': local_zip_path,
                'md5': local_md5
            }]
        except Exception as e:
            error_msg = f"Error compressing/downloading: {str(e)}"
            self.error_message = error_msg
            self.status = self.error_message
            self.add_log_entry(error_msg, is_error=True)
            return []

ssh_extractor_bp = Blueprint('ssh_extractor', __name__)

# SQLite DB used for storing connection profiles
DATABASE_FILE = os.path.join('instance', 'myapp_db.db')

def load_ssh_profiles():
    """Load SSH connection profiles from SQLite where connection_type='SSH'"""
    import sqlite3
    profiles = []
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM connection WHERE connection_type = 'SSH' ORDER BY name")
        rows = cur.fetchall()
        profiles = [dict(r) for r in rows]
    except Exception as e:
        print(f"Error loading SSH profiles: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return profiles

@ssh_extractor_bp.route('/start', methods=['POST'])
def api_start_extraction():
    """Start SSH log extraction task (standard JSON API).
    Body JSON fields:
      - uuid (required)
      - ssh_host, ssh_username, ssh_password (required)
      - compress_files (bool, default false)
      - force_new (bool, default false)
      - log_date (string, optional)
      - connection_timeout (int, default 30)
      - retry_count (int, default 3)
      - context_before (int, default 100)
      - context_after (int, default 100)
      - log_path_current (string, default '/app/jalin_renaissance/')
      - log_path_backup (string, default '/app/renaissance_log_backup_jalin/')
      - file_name_pattern (string, default 'renaissance{date}*.txt')
    Returns: { success, data: { task_id }, execution_time, error }
    """
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON', 'execution_time': None}), 400
    data = request.get_json()
    # Accept either direct SSH creds (ssh_host, ssh_username, ssh_password) OR profileId
    uuid_val = data.get('uuid')
    profile_id = data.get('profileId')
    ssh_host = data.get('ssh_host')
    ssh_username = data.get('ssh_username')
    ssh_password = data.get('ssh_password')
    if not uuid_val:
        return jsonify({'success': False, 'error': 'Missing required field: uuid', 'execution_time': None}), 400
    if not (ssh_host and ssh_username and ssh_password):
        if not profile_id:
            return jsonify({'success': False, 'error': 'Provide either SSH credentials (ssh_host, ssh_username, ssh_password) or profileId', 'execution_time': None}), 400
        # Resolve from SSH profiles
        profiles = load_ssh_profiles()
        profile = next((p for p in profiles if p.get('name') == profile_id), None)
        if not profile:
            return jsonify({'success': False, 'error': f'Profile {profile_id} not found', 'execution_time': None}), 404
        ssh_host = profile.get('host')
        ssh_username = profile.get('user') or profile.get('username')
        ssh_password = profile.get('password')
        if not (ssh_host and ssh_username and ssh_password):
            return jsonify({'success': False, 'error': f'Profile {profile_id} missing host/user/password', 'execution_time': None}), 400
    try:
        import time
        t0 = time.time()
        # Resolve download directory from app config
        download_dir = current_app.config.get('LOG_DOWNLOAD_DIR', os.path.join(current_app.root_path, 'downloads'))
        # Resolve download directory (supports absolute local paths)
        req_download_dir = data.get('download_dir')
        if req_download_dir and isinstance(req_download_dir, str) and len(req_download_dir.strip()) > 0:
            download_dir = os.path.expanduser(req_download_dir.strip())
        else:
            download_dir = current_app.config.get('LOG_DOWNLOAD_DIR') or os.path.join(tempfile.gettempdir(), 'ssh_logs')
        os.makedirs(download_dir, exist_ok=True)
        
        task_id = start_extraction_task(
            search_uuid=data['uuid'],
            download_dir=download_dir,
            compress_files=bool(data.get('compress_files', False)),
            log_date=data.get('log_date'),
            force_new=bool(data.get('force_new', False)),
            ssh_host=ssh_host,
            ssh_username=ssh_username,
            ssh_password=ssh_password,
            connection_timeout=int(data.get('connection_timeout', 30)),
            retry_count=int(data.get('retry_count', 3)),
            verbose_logging=bool(data.get('verbose_logging', True)),
            context_before=int(data.get('context_before', 100)),
            context_after=int(data.get('context_after', 100)),
            log_path_current=data.get('log_path_current', '/app/jalin_renaissance/'),
            log_path_backup=data.get('log_path_backup', '/app/renaissance_log_backup_jalin/'),
            file_name_pattern=data.get('file_name_pattern', 'renaissance{date}*.txt')
        )
        # If auto_download is requested, block and return the result file directly
        if bool(data.get('auto_download', False)):
            timeout_seconds = int(data.get('timeout_seconds', 600))  # default 10 minutes
            poll_interval = 1.0
            waited = 0.0
            while waited < timeout_seconds:
                task = tasks.get(task_id)
                if task and (getattr(task, 'success', False) or getattr(task, 'error_message', None) or getattr(task, 'progress', 0) >= 100):
                    break
                time.sleep(poll_interval)
                waited += poll_interval

            task = tasks.get(task_id)
            result_files = None
            if task and getattr(task, 'result_files', None):
                result_files = task.result_files
            else:
                persisted = recover_task(task_id)
                if persisted and persisted.get('result_files'):
                    result_files = persisted['result_files']

            if task and getattr(task, 'error_message', None):
                return jsonify({'success': False, 'error': task.error_message, 'execution_time': round(time.time()-t0, 6)}), 500
            if task and hasattr(task, 'cancel_event') and task.is_cancelled():
                return jsonify({'success': False, 'error': 'Task was canceled', 'execution_time': round(time.time()-t0, 6)}), 499
            if not result_files or len(result_files) == 0:
                return jsonify({'success': False, 'error': 'Result not available (timeout or no files)', 'execution_time': round(time.time()-t0, 6)}), 408

            if len(result_files) == 1 and str(result_files[0].get('name', '')).lower().endswith('.zip'):
                path = result_files[0].get('path')
                if not path or not os.path.isfile(path):
                    return jsonify({'success': False, 'error': 'Result file missing on disk', 'execution_time': round(time.time()-t0, 6)}), 410
                return send_file(path, as_attachment=True)

            # Otherwise zip on the fly
            temp_dir = tempfile.gettempdir()
            zip_name = f"{task_id}_results.zip"
            zip_path = os.path.join(temp_dir, zip_name)
            try:
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for file_info in result_files:
                        src = file_info.get('path')
                        arcname = file_info.get('name') or os.path.basename(src) if src else None
                        if not src or not os.path.isfile(src):
                            continue
                        zf.write(src, arcname=arcname)
            except Exception as e:
                return jsonify({'success': False, 'error': f'Failed to create zip: {str(e)}', 'execution_time': round(time.time()-t0, 6)}), 500
            return send_file(zip_path, as_attachment=True)

        # Non-blocking: provide initial progress and convenience URLs
        task = tasks.get(task_id)
        initial_status = {
            'task_id': task_id,
            'progress': getattr(task, 'progress', 0) if task else 0,
            'status': getattr(task, 'status', 'Queued') if task else 'Queued',
            'state': 'PENDING',
            'stream_url': f"/api/ssh/extractor/stream/logs?task_id={task_id}&from_index=0",
            'download_url': f"/api/ssh/extractor/result/download?task_id={task_id}"
        }
        return jsonify({
            'success': True,
            'data': initial_status,
            'execution_time': round(time.time() - t0, 6),
            'error': None
        })
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'details': traceback.format_exc(), 'execution_time': None}), 500


@ssh_extractor_bp.route('/stream/logs', methods=['GET'])
def api_stream_logs():
    """Server-Sent Events stream for task logs and status.
    Query: ?task_id=<id>&from_index=0
    If the client disconnects, the task is immediately canceled.
    """
    task_id = request.args.get('task_id', '')
    try:
        start_index = int(request.args.get('from_index', '0'))
    except Exception:
        start_index = 0
    if not task_id:
        return jsonify({'success': False, 'error': 'Missing required query: task_id', 'execution_time': None}), 400

    def event_stream(task_id, from_index):
        last_index = max(0, from_index)
        interval = 0.5
        try:
            while True:
                task = tasks.get(task_id)
                if not task:
                    persisted = recover_task(task_id)
                    if persisted:
                        # Emit final persisted state and end
                        payload = {
                            'state': 'SUCCESS' if persisted.get('success') else 'FAILURE',
                            'status': persisted.get('status'),
                            'progress': persisted.get('progress'),
                            'error': persisted.get('error_message'),
                            'files_found': persisted.get('files_found'),
                            'current_file': persisted.get('current_file'),
                            'log_entries': persisted.get('log_entries', [])[last_index:],
                            'next_index': len(persisted.get('log_entries', []))
                        }
                        yield f"data: {json.dumps(payload)}\n\n"
                    break

                # Build incremental payload
                entries = task.log_entries[last_index:] if hasattr(task, 'log_entries') else []
                last_index = last_index + len(entries)
                payload = {
                    'state': 'CANCELED' if (hasattr(task, 'cancel_event') and task.is_cancelled() and task.progress >= 100) else (
                             'CANCELLING' if (hasattr(task, 'cancel_event') and task.is_cancelled()) else (
                             'SUCCESS' if getattr(task, 'success', False) else (
                             'FAILURE' if getattr(task, 'error_message', None) else 'PENDING'))),
                    'status': getattr(task, 'status', ''),
                    'progress': getattr(task, 'progress', 0),
                    'error': getattr(task, 'error_message', None),
                    'files_found': getattr(task, 'files_found', 0),
                    'current_file': getattr(task, 'current_file', ''),
                    'log_entries': entries,
                    'next_index': last_index
                }
                yield f"data: {json.dumps(payload)}\n\n"

                # End conditions
                if getattr(task, 'progress', 0) >= 100 or getattr(task, 'success', False) or getattr(task, 'error_message', None):
                    break

                time.sleep(interval)
        except GeneratorExit:
            # Client disconnected normally
            t = tasks.get(task_id)
            if t and hasattr(t, 'request_cancel'):
                t.add_log_entry('Client disconnected - initiating cancellation', is_important=True)
                t.request_cancel()
                try:
                    persist_task(task_id, t)
                except Exception:
                    pass
            raise
        except Exception as e:
            # Unexpected error; cancel as safety
            t = tasks.get(task_id)
            if t and hasattr(t, 'request_cancel'):
                t.add_log_entry(f'Stream error ({str(e)}) - initiating cancellation', is_error=True)
                t.request_cancel()
                try:
                    persist_task(task_id, t)
                except Exception:
                    pass
            # Propagate end of stream
        finally:
            # Attempt to emit a final state if task still present
            t = tasks.get(task_id)
            if t:
                try:
                    final_payload = {
                        'state': 'CANCELED' if (hasattr(t, 'cancel_event') and t.is_cancelled()) else (
                                 'SUCCESS' if getattr(t, 'success', False) else (
                                 'FAILURE' if getattr(t, 'error_message', None) else 'PENDING')),
                        'status': getattr(t, 'status', ''),
                        'progress': getattr(t, 'progress', 0)
                    }
                    yield f"data: {json.dumps(final_payload)}\n\n"
                except Exception:
                    pass

    # Note: SSE requires proper headers and no buffering by proxies
    headers = {
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',  # for Nginx
        'Content-Type': 'text/event-stream',
        'Connection': 'keep-alive'
    }
    return Response(stream_with_context(event_stream(task_id, start_index)), headers=headers)


@ssh_extractor_bp.route('/result/download', methods=['GET'])
def api_result_download():
    """Download the finished result for a task.
    If the task produced a single zip, return it.
    If multiple files, zip them on the fly and return the new zip.
    Query: ?task_id=<id>
    """
    task_id = request.args.get('task_id', '')
    if not task_id:
        return jsonify({'success': False, 'error': 'Missing required query: task_id', 'execution_time': None}), 400
    try:
        import time
        t0 = time.time()
        task = tasks.get(task_id)
        result_files = None
        if task and getattr(task, 'result_files', None):
            result_files = task.result_files
        else:
            persisted = recover_task(task_id)
            if persisted and persisted.get('result_files'):
                result_files = persisted['result_files']

        if not result_files or len(result_files) == 0:
            return jsonify({'success': False, 'error': 'Result not available yet', 'execution_time': round(time.time()-t0, 6)}), 409

        # If single file and zip, just send it
        if len(result_files) == 1 and str(result_files[0].get('name', '')).lower().endswith('.zip'):
            path = result_files[0].get('path')
            if not path or not os.path.isfile(path):
                return jsonify({'success': False, 'error': 'Result file missing on disk', 'execution_time': round(time.time()-t0, 6)}), 410
            return send_file(path, as_attachment=True)

        # Otherwise, create a temporary zip containing all result files
        temp_dir = tempfile.gettempdir()
        zip_name = f"{task_id}_results.zip"
        zip_path = os.path.join(temp_dir, zip_name)
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_info in result_files:
                    src = file_info.get('path')
                    arcname = file_info.get('name') or os.path.basename(src) if src else None
                    if not src or not os.path.isfile(src):
                        continue
                    zf.write(src, arcname=arcname)
        except Exception as e:
            return jsonify({'success': False, 'error': f'Failed to create zip: {str(e)}', 'execution_time': round(time.time()-t0, 6)}), 500
        return send_file(zip_path, as_attachment=True)

    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'details': traceback.format_exc(), 'execution_time': None}), 500


def _is_path_allowed(path):
    """Optional allow-list enforcement for local file APIs."""
    try:
        if not path:
            return False
        # If ALLOWED_LOCAL_ROOTS configured, enforce
        roots = current_app.config.get('ALLOWED_LOCAL_ROOTS')
        if not roots:
            return True  # No restriction configured
        real = os.path.realpath(os.path.expanduser(path))
        for r in roots:
            rr = os.path.realpath(os.path.expanduser(r))
            if real.startswith(rr + os.sep) or real == rr:
                return True
        return False
    except Exception:
        return False


@ssh_extractor_bp.route('/local/list/download', methods=['GET', 'OPTIONS'])
def api_local_list_download():
    """Download a local file by filename from a directory.
    Query parameters:
    - filename: (required) Name of the file to download
    - path: (required) Directory path where the file is located
    - pattern: (optional) Pattern to match files, defaults to exact filename match
    """
    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        origin = request.headers.get('Origin', '*')
        response.headers.add('Access-Control-Allow-Origin', origin)
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
        response.headers.add('Access-Control-Allow-Methods', 'GET, OPTIONS')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response, 200
    try:
        import time
        import glob
        t0 = time.time()
        
        filename = request.args.get('filename', '')
        directory_path = request.args.get('path', '')
        pattern = request.args.get('pattern', filename)  # Default to exact filename
        
        if not filename:
            return jsonify({'success': False, 'error': 'Missing required query: filename', 'execution_time': None}), 400
        
        if not directory_path:
            return jsonify({'success': False, 'error': 'Missing required query: path', 'execution_time': None}), 400
        
        # Expand user directory and get real path
        directory_path = os.path.expanduser(directory_path)
        directory_path = os.path.realpath(directory_path)
        
        # Security check - ensure path is allowed
        if not _is_path_allowed(directory_path):
            return jsonify({'success': False, 'error': 'Path not allowed', 'execution_time': None}), 403
        
        # Ensure it's a directory
        if not os.path.isdir(directory_path):
            return jsonify({'success': False, 'error': 'Path is not a directory', 'execution_time': None}), 400
        
        # Find the file using pattern matching
        search_pattern = os.path.join(directory_path, pattern)
        matching_files = glob.glob(search_pattern)
        
        # Filter to only include files (not directories) and exact filename match if no pattern provided
        matching_files = [f for f in matching_files if os.path.isfile(f)]
        
        # If pattern was provided, still prefer exact filename match
        exact_match = os.path.join(directory_path, filename)
        if os.path.isfile(exact_match) and exact_match in matching_files:
            file_path = exact_match
        elif matching_files:
            # Use first match if exact match not found
            file_path = matching_files[0]
        else:
            return jsonify({
                'success': False, 
                'error': f'File "{filename}" not found in directory "{directory_path}"', 
                'execution_time': round(time.time() - t0, 6)
            }), 404
        
        # Final security check on resolved file path
        file_path = os.path.realpath(file_path)
        if not _is_path_allowed(file_path):
            return jsonify({'success': False, 'error': 'File path not allowed', 'execution_time': None}), 403
        
        # Verify file exists
        if not os.path.isfile(file_path):
            return jsonify({
                'success': False, 
                'error': 'File not found', 
                'execution_time': round(time.time() - t0, 6)
            }), 404
        
        # Download the file with original filename
        return send_file(file_path, as_attachment=True, download_name=filename)
        
    except Exception as e:
        import traceback
        return jsonify({
            'success': False, 
            'error': str(e), 
            'details': traceback.format_exc(), 
            'execution_time': None
        }), 500


@ssh_extractor_bp.route('/local/list', methods=['POST'])
def api_local_list():
    """List local files under a directory. Body JSON: { path, pattern? }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON', 'execution_time': None}), 400
    data = request.get_json()
    target = data.get('path')
    pattern = data.get('pattern', '*')
    try:
        import time, glob
        t0 = time.time()
        if not target:
            return jsonify({'success': False, 'error': 'Missing required field: path', 'execution_time': None}), 400
        target = os.path.expanduser(target)
        if not _is_path_allowed(target):
            return jsonify({'success': False, 'error': 'Path not allowed', 'execution_time': None}), 403
        if not os.path.isdir(target):
            return jsonify({'success': False, 'error': 'Not a directory', 'execution_time': None}), 400
        entries = []
        for p in glob.glob(os.path.join(target, pattern)):
            try:
                st = os.stat(p)
                entries.append({
                    'name': os.path.basename(p),
                    'path': os.path.realpath(p),
                    'is_dir': os.path.isdir(p),
                    'size': st.st_size,
                    'modified_at': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                })
            except Exception:
                continue
        return jsonify({'success': True, 'data': {'entries': entries}, 'execution_time': round(time.time()-t0, 6), 'error': None})
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'details': traceback.format_exc(), 'execution_time': None}), 500


@ssh_extractor_bp.route('/local/read', methods=['POST'])
def api_local_read():
    """Read local file content. Body JSON: { path, offset_bytes?, max_bytes? }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON', 'execution_time': None}), 400
    data = request.get_json()
    path = data.get('path')
    offset = int(data.get('offset_bytes', 0))
    max_bytes = int(data.get('max_bytes', 1024 * 1024))  # default 1MB
    try:
        import time
        t0 = time.time()
        if not path:
            return jsonify({'success': False, 'error': 'Missing required field: path', 'execution_time': None}), 400
        path = os.path.expanduser(path)
        if not _is_path_allowed(path):
            return jsonify({'success': False, 'error': 'Path not allowed', 'execution_time': None}), 403
        if not os.path.isfile(path):
            return jsonify({'success': False, 'error': 'File not found', 'execution_time': None}), 404
        size = os.path.getsize(path)
        if offset < 0:
            offset = 0
        if offset > size:
            offset = size
        to_read = min(max_bytes, size - offset)
        with open(path, 'rb') as f:
            f.seek(offset)
            content = f.read(to_read)
        return jsonify({'success': True, 'data': {
            'path': os.path.realpath(path),
            'offset_bytes': offset,
            'read_bytes': len(content),
            'total_bytes': size,
            'content_b64': content.decode('latin1') if isinstance(content, bytes) else content
        }, 'execution_time': round(time.time()-t0, 6), 'error': None})
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'details': traceback.format_exc(), 'execution_time': None}), 500


@ssh_extractor_bp.route('/local/download', methods=['GET'])
def api_local_download():
    """Download a local file via HTTP attachment. Query: ?path="""
    try:
        import time
        t0 = time.time()
        path = request.args.get('path', '')
        path = os.path.expanduser(path)
        if not path:
            return jsonify({'success': False, 'error': 'Missing required query: path', 'execution_time': None}), 400
        if not _is_path_allowed(path):
            return jsonify({'success': False, 'error': 'Path not allowed', 'execution_time': None}), 403
        if not os.path.isfile(path):
            return jsonify({'success': False, 'error': 'File not found', 'execution_time': None}), 404
        return send_file(path, as_attachment=True)
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'details': traceback.format_exc(), 'execution_time': None}), 500


@ssh_extractor_bp.route('/cancel', methods=['POST'])
def api_cancel_task():
    """Request cancellation of a running task. Body: { task_id }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON', 'execution_time': None}), 400
    data = request.get_json()
    task_id = data.get('task_id')
    if not task_id:
        return jsonify({'success': False, 'error': 'Missing required field: task_id', 'execution_time': None}), 400
    try:
        import time
        t0 = time.time()
        task = tasks.get(task_id)
        if not task:
            # Attempt recovery from persistence (best-effort, but cannot cancel a non-running task)
            if recover_task(task_id):
                return jsonify({'success': False, 'error': 'Task not running (recovered from persistence). Cannot cancel.', 'execution_time': round(time.time() - t0, 6)}), 409
            return jsonify({'success': False, 'error': 'Task not found', 'execution_time': round(time.time() - t0, 6)}), 404
        if hasattr(task, 'request_cancel'):
            task.request_cancel()
            return jsonify({
                'success': True,
                'data': {'task_id': task_id, 'status': 'Cancellation requested'},
                'execution_time': round(time.time() - t0, 6),
                'error': None
            })
        else:
            return jsonify({'success': False, 'error': 'Task cannot be canceled', 'execution_time': round(time.time() - t0, 6)}), 409
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'details': traceback.format_exc(), 'execution_time': None}), 500

    


@ssh_extractor_bp.route('/status', methods=['POST'])
def api_task_status():
    """Get task status by task_id (standard JSON API). Body: { task_id }"""
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON', 'execution_time': None}), 400
    data = request.get_json()
    task_id = data.get('task_id')
    if not task_id:
        return jsonify({'success': False, 'error': 'Missing required field: task_id', 'execution_time': None}), 400
    try:
        import time
        t0 = time.time()
        status = get_task_status(task_id)
        return jsonify({
            'success': True,
            'data': status,
            'execution_time': round(time.time() - t0, 6),
            'error': None
        })
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'details': traceback.format_exc(), 'execution_time': None}), 500


@ssh_extractor_bp.route('/clear-cache', methods=['POST'])
def api_clear_cache():
    """Clear tasks and persisted tasks. Returns cleared count."""
    try:
        import time
        t0 = time.time()
        cleared = clear_all_tasks()
        return jsonify({
            'success': True,
            'data': {'cleared_count': cleared},
            'execution_time': round(time.time() - t0, 6),
            'error': None
        })
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'details': traceback.format_exc(), 'execution_time': None}), 500


@ssh_extractor_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200


@ssh_extractor_bp.route('/logs/tail', methods=['POST'])
def api_tail_logs():
    """Incremental log fetch for live display.
    Body JSON: { task_id: string, from_index: int (optional, default 0) }
    Returns: { entries: [..], next_index: int, status, progress, current_file, success, error_message }
    """
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Request body must be JSON', 'execution_time': None}), 400
    data = request.get_json()
    task_id = data.get('task_id')
    from_index = int(data.get('from_index', 0))
    if not task_id:
        return jsonify({'success': False, 'error': 'Missing required field: task_id', 'execution_time': None}), 400
    try:
        import time
        t0 = time.time()
        task = tasks.get(task_id)
        # If not in memory, try to use persistence snapshot if present
        summary = {
            'status': None,
            'progress': 0,
            'current_file': None,
            'success': False,
            'error_message': None
        }
        entries = []
        if task is not None and hasattr(task, 'log_entries'):
            total = len(task.log_entries)
            if from_index < 0 or from_index > total:
                from_index = 0
            entries = task.log_entries[from_index:]
            summary.update({
                'status': getattr(task, 'status', None),
                'progress': getattr(task, 'progress', 0),
                'current_file': getattr(task, 'current_file', None),
                'success': getattr(task, 'success', False),
                'error_message': getattr(task, 'error_message', None),
            })
            next_index = total
        else:
            # Fallback to persisted snapshot if available
            try:
                persisted = recover_task(task_id)
            except Exception:
                persisted = None
            if persisted:
                logs = persisted.get('log_entries', [])
                total = len(logs)
                if from_index < 0 or from_index > total:
                    from_index = 0
                entries = logs[from_index:]
                summary.update({
                    'status': persisted.get('status'),
                    'progress': persisted.get('progress', 0),
                    'current_file': persisted.get('current_file'),
                    'success': persisted.get('success', False),
                    'error_message': persisted.get('error_message'),
                })
                next_index = total
            else:
                return jsonify({'success': False, 'error': 'Task not found', 'execution_time': None}), 404

        return jsonify({
            'success': True,
            'data': {
                'entries': entries,
                'next_index': next_index,
                **summary
            },
            'execution_time': round(time.time() - t0, 6),
            'error': None
        })
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'details': traceback.format_exc(), 'execution_time': None}), 500

    def connect(self):
        """Establish SSH connection to the server"""
        retry_attempts = 0
        
        while retry_attempts <= self.retry_count:
            try:
                if retry_attempts > 0:
                    self.status = f"Retrying connection ({retry_attempts}/{self.retry_count})..."
                    self.add_log_entry(f"Retrying connection attempt {retry_attempts}/{self.retry_count}", is_important=True)
                    time.sleep(2)  # Wait 2 seconds before retry
                else:
                    self.status = "Connecting to SSH server..."
                    self.add_log_entry(f"Connecting to SSH server {self.host} as user {self.username}...", is_important=True)
                
                self.ssh_client = paramiko.SSHClient()
                self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.add_log_entry("Setting SSH host key policy to AutoAddPolicy")
                
                self.ssh_client.connect(
                    hostname=self.host,
                    username=self.username,
                    password=self.password,
                    timeout=self.connection_timeout
                )
                self.add_log_entry(f"Successfully established SSH connection to {self.host}", is_important=True)
                
                self.sftp_client = self.ssh_client.open_sftp()
                self.add_log_entry("Opened SFTP connection for file transfers", is_important=True)
                
                self.progress = 10
                self.status = "Connected to SSH server"
                return True
            except Exception as e:
                retry_attempts += 1
                error_msg = f"Connection attempt {retry_attempts} failed: {str(e)}"
                self.add_log_entry(error_msg, is_error=True)
                
                if retry_attempts > self.retry_count:
                    final_error_msg = f"Failed to connect to SSH server after {self.retry_count + 1} attempts: {str(e)}"
                    self.error_message = final_error_msg
                    self.status = self.error_message
                    self.add_log_entry(final_error_msg, is_error=True)
                    return False
        
        return False
    
    def find_log_files_with_uuid(self, search_uuid, log_date=None):
        """Find log files containing the specified UUID and get line numbers"""
        try:
            self.search_uuid = search_uuid  # Store the UUID being searched for
            self.status = "Determining log path based on date..."
            self.progress = 10
            
            # Determine if we need to use current or backup logs based on date
            if log_date:
                self.add_log_entry(f"Using log date filter: {log_date}", is_important=True)
                
                # Parse the input date and compare with today and yesterday
                try:
                    # Convert input date string to datetime object
                    input_date = datetime.strptime(log_date, '%Y-%m-%d').date()
                    today = datetime.now().date()
                    yesterday = today - timedelta(days=1)
                    
                    self.add_log_entry(f"Comparing dates - Input: {input_date}, Today: {today}, Yesterday: {yesterday}", is_important=True)
                    
                    if input_date == today or input_date == yesterday:
                        # Use current logs path for today and yesterday
                        self.current_log_path = self.log_path_current
                        self.using_backup = False
                        self.add_log_entry(f"Using current logs path for date {log_date}: {self.current_log_path}", is_important=True)
                    else:
                        # Use backup logs path for older dates
                        self.current_log_path = self.log_path_backup
                        self.using_backup = True
                        self.add_log_entry(f"Using backup logs path for date {log_date}: {self.current_log_path}", is_important=True)
                        
                        # Format the date for the backup directory (YYYYMMDD format)
                        formatted_date = input_date.strftime('%Y%m%d')
                        
                        # Check if we need to extract backup files
                        if self.using_backup:
                            return self.find_log_files_in_backup(search_uuid, formatted_date)
                except ValueError as e:
                    self.add_log_entry(f"Error parsing date {log_date}: {str(e)}. Using current logs path.", is_error=True)
                    self.current_log_path = self.log_path_current
                    self.using_backup = False
            else:
                # Default to current logs if no date specified
                self.current_log_path = self.log_path_current
                self.using_backup = False
                self.add_log_entry(f"No date specified, using current logs path: {self.current_log_path}", is_important=True)
            
            self.status = "Changing to log directory..."
            self.progress = 15
            
            # Change to the log directory
            self.add_log_entry(f"Executing command: cd {self.current_log_path}", is_important=True)
            stdin, stdout, stderr = self.ssh_client.exec_command(f'cd {self.current_log_path}')
            error = stderr.read().decode().strip()
            if error:
                self.error_message = f"Failed to change directory: {error}"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                return []
            
            self.add_log_entry(f"Successfully changed to directory: {self.current_log_path}", is_important=True)
            self.status = "Searching for log files containing UUID..."
            self.progress = 20
            
            # Construct the file pattern based on log_date
            file_pattern = self.file_name_pattern
            if log_date:
                # Convert date to YYYYMMDD format without hyphens
                date_obj = datetime.strptime(log_date, '%Y-%m-%d')
                formatted_date = date_obj.strftime('%Y%m%d')
                file_pattern = file_pattern.format(date=formatted_date)
                self.add_log_entry(f"Using date-specific file pattern: {file_pattern}", is_important=True)
            else:
                file_pattern = file_pattern.format(date="*")
                self.add_log_entry(f"Using default file pattern: {file_pattern}", is_important=True)
            
            # Log the UUID being searched for
            self.add_log_entry(f"Searching for UUID: {search_uuid}", is_important=True)
            
            # Find log files containing the UUID with line numbers and get first and last occurrences
            grep_command = f"grep -anH '{search_uuid}' {file_pattern}"
            sed_command = f"sed -n '1p;$p'"
            full_command = f"cd {self.current_log_path} && {grep_command} | {sed_command}"
            self.add_log_entry(f"Executing command: {full_command}", is_important=True)
            
            stdin, stdout, stderr = self.ssh_client.exec_command(full_command)
            
            output = stdout.read().decode().strip()
            error = stderr.read().decode().strip()
            
            if error:
                self.add_log_entry(f"Command returned error: {error}", is_error=True)
            
            if not output:
                self.error_message = "No matching log entries found for this UUID"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                return []
            
            # Parse the output to get file names and line numbers
            self.add_log_entry(f"Command output: {output}")
            
            # Process each line of the output
            lines = output.split('\n')
            files_with_ranges = {}
            for line in lines:
                match = re.match(r'([^:]+):(\d+):', line)
                if match:
                    filename = match.group(1)
                    line_num = int(match.group(2))
                    if filename not in files_with_ranges:
                        files_with_ranges[filename] = {
                            'first_line': line_num,
                            'last_line': line_num
                        }
                    else:
                        files_with_ranges[filename]['last_line'] = line_num
            
            # Compute extraction ranges using context_before/after exactly as specified
            log_files = []
            for filename, rng in files_with_ranges.items():
                first_line_num = rng['first_line']
                last_line_num = rng['last_line']
                start_line = max(1, first_line_num - self.context_before)
                end_line = last_line_num + self.context_after
                
                # Store the file and line range
                self.file_line_ranges[filename] = {
                    'start_line': start_line,
                    'end_line': end_line,
                    'first_match_line': first_line_num,
                    'last_match_line': last_line_num
                }
                
                self.add_log_entry(f"Found UUID in file {filename} from line {first_line_num} to {last_line_num}", is_important=True)
                self.add_log_entry(f"Will extract lines {start_line} to {end_line} (±{self.context_before} to ±{self.context_after} lines)", is_important=True)
                
                files.append(filename)
            
            self.files_found = len(files)
            
            file_list = ", ".join(files) if files else "No files found"
            self.add_log_entry(f"Found {len(files)} log files containing UUID: {file_list}", is_important=True)
            
            self.status = f"Found {len(files)} log files containing UUID"
            self.progress = 30
            
            return files
        except Exception as e:
            error_msg = f"Error finding log files: {str(e)}"
            self.error_message = error_msg
            self.status = self.error_message
            self.add_log_entry(error_msg, is_error=True)
            return []
    
    def find_log_files_in_backup(self, search_uuid, log_date):
        """Find log files in backup archives for older dates"""
        try:
            self.status = "Checking backup directory..."
            self.progress = 12
            
            # log_date should already be in YYYYMMDD format
            self.add_log_entry(f"Looking for backup files with date: {log_date}", is_important=True)
            
            # Step 1: Check if backup directory exists
            backup_date_dir = f"{self.log_path_backup}{log_date}"
            check_dir_cmd = f"[ -d {backup_date_dir} ] && echo 'exists' || echo 'not exists'"
            self.add_log_entry(f"Checking if backup directory exists: {backup_date_dir}", is_important=True)
            
            stdin, stdout, stderr = self.ssh_client.exec_command(check_dir_cmd)
            dir_exists = stdout.read().decode().strip()
            
            if dir_exists != 'exists':
                self.error_message = f"Backup directory {backup_date_dir} does not exist"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                return []
            
            # Step 2: Check if backup tar.gz file exists - use configurable pattern
            # Extract base name from file_name_pattern for backup file naming
            base_name = self.file_name_pattern.split('{')[0].rstrip('*').rstrip('.txt')
            backup_file = f"{base_name}_log_backup_{log_date}.tar.gz"
            # File tar.gz berada di dalam folder tanggal, bukan di folder induk
            backup_file_path = f"{backup_date_dir}/{backup_file}"
            check_file_cmd = f"[ -f {backup_file_path} ] && echo 'exists' || echo 'not exists'"
            self.add_log_entry(f"Checking if backup archive exists: {backup_file_path}", is_important=True)
            
            stdin, stdout, stderr = self.ssh_client.exec_command(check_file_cmd)
            file_exists = stdout.read().decode().strip()
            
            if file_exists != 'exists':
                self.error_message = f"Backup archive {backup_file_path} does not exist"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                return []
            
            # Step 3: Extract the tar.gz file
            self.status = "Extracting backup archive..."
            self.progress = 13
            self.add_log_entry(f"Extracting backup archive: {backup_file_path}", is_important=True)
            
            # Extract the archive
            extract_cmd = f"tar -xvf {backup_file_path} -C {backup_date_dir}"
            self.add_log_entry(f"Executing extraction command: {extract_cmd}", is_important=True)
            
            stdin, stdout, stderr = self.ssh_client.exec_command(extract_cmd)
            # Wait for extraction to complete
            exit_status = stdout.channel.recv_exit_status()
            extract_output = stdout.read().decode().strip()
            extract_error = stderr.read().decode().strip()
            
            if exit_status != 0:
                self.error_message = f"Failed to extract backup archive: {extract_error}"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                return []
            
            self.add_log_entry(f"Successfully extracted backup archive to {backup_date_dir}", is_important=True)
            
            # Step 4: Search for UUID in extracted files
            self.status = "Searching for UUID in backup files..."
            self.progress = 15
            
            # Update current log path to the backup directory
            self.current_log_path = backup_date_dir + "/"
            
            # Use configurable file pattern for searching in backup files
            backup_file_pattern = self.file_name_pattern.format(date=log_date)
            # Find log files containing the UUID with line numbers and get first and last occurrences
            grep_command = f"grep -anH '{search_uuid}' {backup_file_pattern}"
            sed_command = f"sed -n '1p;$p'"
            full_command = f"cd {self.current_log_path} && {grep_command} | {sed_command}"
            self.add_log_entry(f"Executing command: {full_command}", is_important=True)
            
            stdin, stdout, stderr = self.ssh_client.exec_command(full_command)
            
            output = stdout.read().decode().strip()
            error = stderr.read().decode().strip()
            
            if error and "No such file or directory" not in error:
                self.add_log_entry(f"Command returned error: {error}", is_error=True)
            
            if not output:
                self.error_message = "No matching log entries found for this UUID in backup files"
                self.status = self.error_message
                self.add_log_entry(self.error_message, is_error=True)
                
                # Step 5: Clean up extracted files even if no matches found
                self.add_log_entry("Cleaning up extracted files...", is_important=True)
                cleanup_cmd = f"rm -f {self.current_log_path}*.txt"
                stdin, stdout, stderr = self.ssh_client.exec_command(cleanup_cmd)
                
                return []
            
            # Parse the output to get file names and line numbers
            self.add_log_entry(f"Command output: {output}")
            
            # Process each line of the output
            files = []
            self.file_line_ranges = {}
            
            # Split by empty line (which separates different files)
            file_sections = output.split('\n\n')
            
            for section in file_sections:
                if not section.strip():
                    continue
                
                lines = section.strip().split('\n')
                if len(lines) < 1:
                    continue
                
                # Parse the first line to get filename and first line number
                first_line_match = re.match(r'([^:]+):(\d+):', lines[0])
                if not first_line_match:
                    continue
                
                filename = first_line_match.group(1)
                first_line_num = int(first_line_match.group(2))
                
                # If there's a second line, get the last line number
                last_line_num = first_line_num
                if len(lines) > 1:
                    last_line_match = re.match(r'([^:]+):(\d+):', lines[-1])
                    if last_line_match and last_line_match.group(1) == filename:
                        last_line_num = int(last_line_match.group(2))
                
                # Calculate the line range (use context_before and context_after parameters)
                start_line = max(1, first_line_num - self.context_before)
                end_line = last_line_num + self.context_after
                
                # Store the file and line range
                self.file_line_ranges[filename] = {
                    'start_line': start_line,
                    'end_line': end_line,
                    'first_match_line': first_line_num,
                    'last_match_line': last_line_num
                }
                
                self.add_log_entry(f"Found UUID in backup file {filename} from line {first_line_num} to {last_line_num}", is_important=True)
                self.add_log_entry(f"Will extract lines {start_line} to {end_line} (±{self.context_before} to ±{self.context_after} lines)", is_important=True)
                
                files.append(filename)
            
            self.files_found = len(files)
            
            file_list = ", ".join(files) if files else "No files found"
            self.add_log_entry(f"Found {len(files)} backup log files containing UUID: {file_list}", is_important=True)
            
            self.status = f"Found {len(files)} backup log files containing UUID"
            self.progress = 30
            
            return files
            
        except Exception as e:
            error_msg = f"Error finding log files in backup: {str(e)}"
            self.error_message = error_msg
            self.status = self.error_message
            self.add_log_entry(error_msg, is_error=True)
            return []
    
    def extract_log_sections(self, log_files, search_uuid):
        """Extract relevant log sections from each file using line numbers"""
        results = []
        total_files = len(log_files)
        
        if total_files == 0:
            self.status = "No log files found containing the UUID"
            self.add_log_entry("No log files found containing the UUID", is_important=True)
            return results
            
        progress_per_file = 60 / total_files
        
        for idx, log_file in enumerate(log_files):
            try:
                self.current_file = log_file
                file_progress_base = 30 + (idx * progress_per_file)
                self.status = f"Processing file {idx+1}/{total_files}: {log_file}"
                self.add_log_entry(f"Processing file {idx+1}/{total_files}: {log_file}", is_important=True)
                self.progress = int(file_progress_base)
                
                # Get the line range for this file
                if log_file not in self.file_line_ranges:
                    self.add_log_entry(f"No line range information for {log_file}, skipping", is_error=True)
                    continue
                
                line_range = self.file_line_ranges[log_file]
                start_line = line_range['start_line']
                end_line = line_range['end_line']
                first_match_line = line_range['first_match_line']
                last_match_line = line_range['last_match_line']
                
                self.add_log_entry(f"Extracting lines {start_line} to {end_line} from {log_file}", is_important=True)
                self.add_log_entry(f"Original UUID matches were at lines {first_match_line} and {last_match_line}")
                
                # Create output filename
                output_filename = f"{search_uuid}_{idx+1}.txt"
                self.add_log_entry(f"Output filename: {output_filename}", is_important=True)
                
                # Extract log section using sed with line numbers
                sed_command = f"sed -n '{start_line},{end_line}p' {log_file} > {output_filename}"
                full_command = f"cd {self.current_log_path} && {sed_command}"
                self.add_log_entry(f"Executing command: {full_command}", is_important=True)
                
                stdin, stdout, stderr = self.ssh_client.exec_command(full_command)
                
                # Wait for command to complete
                exit_status = stdout.channel.recv_exit_status()
                error = stderr.read().decode().strip()
                
                if exit_status != 0:
                    self.error_message = f"Error extracting log section: {error}"
                    self.add_log_entry(self.error_message, is_error=True)
                    continue
                
                # Check if the output file was created and get its size
                check_command = f"ls -la {output_filename}"
                full_command = f"cd {self.current_log_path} && {check_command}"
                self.add_log_entry(f"Executing command: {full_command}")
                
                stdin, stdout, stderr = self.ssh_client.exec_command(full_command)
                file_info = stdout.read().decode().strip()
                self.add_log_entry(f"File created: {file_info}", is_important=True)
                
                # Count lines in the output file
                wc_command = f"wc -l {output_filename}"
                full_command = f"cd {self.current_log_path} && {wc_command}"
                self.add_log_entry(f"Executing command: {full_command}")
                
                stdin, stdout, stderr = self.ssh_client.exec_command(full_command)
                line_count = stdout.read().decode().strip()
                self.add_log_entry(f"Line count in output file: {line_count}", is_important=True)
                
                self.progress = int(file_progress_base + (progress_per_file * 0.7))
                
                # Add to results
                results.append({
                    'remote_file': output_filename,
                    'local_file': output_filename
                })
                
                self.add_log_entry(f"Successfully extracted log section to {output_filename}", is_important=True)
                self.progress = int(file_progress_base + progress_per_file)
                
            except Exception as e:
                error_msg = f"Error processing file {log_file}: {str(e)}"
                self.error_message = error_msg
                self.status = self.error_message
                self.add_log_entry(error_msg, is_error=True)
                
        return results
    
    def download_files(self, file_mappings, download_dir):
        """Download extracted log files to local directory"""
        if not file_mappings:
            self.status = "No files to download"
            self.add_log_entry("No files to download", is_important=True)
            return []
            
        try:
            # Create download directory if it doesn't exist
            os.makedirs(download_dir, exist_ok=True)
            self.add_log_entry(f"Created local download directory: {download_dir}", is_important=True)
            
            # If compression is requested, compress files on the remote server first
            if self.compress_files:
                return self.compress_and_download_remote(file_mappings, download_dir)
            
            total_files = len(file_mappings)
            progress_per_file = 10 / total_files  # 10% of progress dedicated to downloads
            
            downloaded_files = []
            
            for idx, file_map in enumerate(file_mappings):
                remote_file = file_map['remote_file']
                local_file = os.path.join(download_dir, file_map['local_file'])
                
                self.status = f"Downloading file {idx+1}/{total_files}: {remote_file}"
                self.current_file = remote_file
                self.progress = 90 + int(idx * progress_per_file)
                
                # Download file
                remote_path = f"{self.current_log_path}{remote_file}"
                self.add_log_entry(f"Downloading file from {remote_path} to {local_file}", is_important=True)
                
                self.sftp_client.get(remote_path, local_file)
                
                # Verify file integrity with MD5
                remote_md5_cmd = f"md5sum {remote_path} | cut -d' ' -f1"
                self.add_log_entry(f"Executing command: {remote_md5_cmd}")
                
                stdin, stdout, stderr = self.ssh_client.exec_command(remote_md5_cmd)
                remote_md5 = stdout.read().decode().strip()
                
                # Calculate local MD5
                with open(local_file, 'rb') as f:
                    local_md5 = hashlib.md5(f.read()).hexdigest()
                
                self.add_log_entry(f"Remote MD5: {remote_md5}", is_important=True)
                self.add_log_entry(f"Local MD5: {local_md5}", is_important=True)
                
                if remote_md5 == local_md5:
                    self.add_log_entry(f"MD5 verification successful for {remote_file}", is_important=True)
                    
                    # Delete remote file after successful download
                    delete_cmd = f"rm {remote_path}"
                    self.add_log_entry(f"Executing cleanup command: {delete_cmd}", is_important=True)
                    
                    stdin, stdout, stderr = self.ssh_client.exec_command(delete_cmd)
                    exit_status = stdout.channel.recv_exit_status()
                    
                    if exit_status == 0:
                        self.add_log_entry(f"Cleanup successful: Deleted remote file {remote_file}", is_important=True)
                    else:
                        error = stderr.read().decode().strip()
                        self.add_log_entry(f"Cleanup failed for {remote_file}: {error}", is_error=True, is_important=True)
                    
                    downloaded_files.append({
                        'name': os.path.basename(local_file),
                        'path': local_file,
                        'md5': local_md5
                    })
                else:
                    self.add_log_entry(f"MD5 verification failed for {remote_file}", is_error=True, is_important=True)
                    self.add_log_entry(f"Expected MD5: {remote_md5}, Actual MD5: {local_md5}", is_error=True, is_important=True)
            
            self.progress = 100
            self.status = f"Successfully downloaded {len(downloaded_files)} files"
            self.success = True
            self.result_files = downloaded_files
            
            return downloaded_files
            
        except Exception as e:
            error_msg = f"Error downloading files: {str(e)}"
            self.error_message = error_msg
            self.status = self.error_message
            self.add_log_entry(error_msg, is_error=True)
            return []
    
    def compress_and_download_remote(self, file_mappings, download_dir):
        """Compress files on remote server and download the compressed file"""
        try:
            self.status = "Compressing files on remote server..."
            self.add_log_entry(f"Starting remote compression of {len(file_mappings)} files", is_important=True)
            
            # Ensure task remains in tasks dictionary during processing
            if not hasattr(self, 'task_id') or self.task_id not in tasks:
                self.add_log_entry("Warning: Task not found in tasks dictionary during compression", is_error=True)
            
            # Get list of files to compress
            remote_files = [file_map['remote_file'] for file_map in file_mappings]
            file_list = " ".join(remote_files)
            
            # Calculate total size of files to compress
            total_size_cmd = f"du -ch {self.current_log_path}{' ' + self.current_log_path}".join(remote_files) + " | grep total"
            self.add_log_entry(f"Checking total size of files to compress")
            stdin, stdout, stderr = self.ssh_client.exec_command(total_size_cmd)
            total_size = stdout.read().decode().strip()
            if total_size:
                self.add_log_entry(f"Total size of files to compress: {total_size}", is_important=True)
            
            # Use the original search UUID for the filename
            # Extract it from log entries
            search_uuid = None
            for entry in self.log_entries:
                if "Starting log extraction for UUID:" in entry:
                    search_uuid = entry.split("UUID:")[1].strip()
                    break
            
            # If not found in logs, fallback to the first file's prefix
            if not search_uuid:
                search_uuid = file_mappings[0]['remote_file'].split('_')[0]
                self.add_log_entry(f"Warning: Using fallback UUID from filename: {search_uuid}", is_error=True)
            
            # Create a ZIP file on the remote server
            zip_filename = f"{search_uuid}_logs.zip"
            remote_zip_path = f"{self.current_log_path}{zip_filename}"
            self.add_log_entry(f"Creating ZIP archive on remote server: {remote_zip_path}", is_important=True)
            
            # Track compression start time
            compression_start = datetime.now()
            
            # Create ZIP file on the remote server
            zip_command = f"cd {self.current_log_path} && zip -j {zip_filename} {file_list}"
            self.add_log_entry(f"Executing command: {zip_command}", is_important=True)
            
            stdin, stdout, stderr = self.ssh_client.exec_command(zip_command)
            exit_status = stdout.channel.recv_exit_status()
            error = stderr.read().decode().strip()
            
            if exit_status != 0:
                self.error_message = f"Error creating ZIP file: {error}"
                self.add_log_entry(self.error_message, is_error=True)
                self.add_log_entry("Falling back to local compression")
                return self.download_and_compress_locally(file_mappings, download_dir)
            
            # Calculate compression time
            compression_time = (datetime.now() - compression_start).total_seconds()
            self.add_log_entry(f"Remote compression completed in {compression_time:.2f} seconds", is_important=True)
            
            # Delete the original files on the remote server
            for remote_file in remote_files:
                delete_cmd = f"rm {self.current_log_path}{remote_file}"
                self.add_log_entry(f"Executing cleanup command: {delete_cmd}", is_important=True)
                
                stdin, stdout, stderr = self.ssh_client.exec_command(delete_cmd)
                exit_status = stdout.channel.recv_exit_status()
                
                if exit_status == 0:
                    self.add_log_entry(f"Cleanup successful: Deleted remote file {remote_file}", is_important=True)
                else:
                    error = stderr.read().decode().strip()
                    self.add_log_entry(f"Cleanup failed for {remote_file}: {error}", is_error=True, is_important=True)
            
            # Verify ZIP file was created
            check_cmd = f"ls -la {zip_filename}"
            full_cmd = f"cd {self.current_log_path} && {check_cmd}"
            self.add_log_entry(f"Executing command: {full_cmd}")
            
            # Persist task state before critical command
            if hasattr(self, 'task_id'):
                persist_task(self.task_id, self)
                self.add_log_entry("Task state persisted before ZIP verification", is_important=True)
            
            stdin, stdout, stderr = self.ssh_client.exec_command(full_cmd)
            file_info = stdout.read().decode().strip()
            
            # Persist task state after critical command
            if hasattr(self, 'task_id'):
                persist_task(self.task_id, self)
                self.add_log_entry("Task state persisted after ZIP verification", is_important=True)
            
            if not file_info:
                self.add_log_entry("ZIP file not created on remote server", is_error=True)
                self.add_log_entry("Falling back to local compression")
                return self.download_and_compress_locally(file_mappings, download_dir)
                
            self.add_log_entry(f"Remote ZIP file created: {file_info}", is_important=True)
            
            # Get ZIP file size
            size_cmd = f"du -h {remote_zip_path}"
            self.add_log_entry(f"Checking compressed file size")
            
            # Persist task state before size check
            if hasattr(self, 'task_id'):
                persist_task(self.task_id, self)
                self.add_log_entry("Task state persisted before size check", is_important=True)
            
            stdin, stdout, stderr = self.ssh_client.exec_command(size_cmd)
            zip_size = stdout.read().decode().strip()
            
            # Persist task state after size check
            if hasattr(self, 'task_id'):
                persist_task(self.task_id, self)
                self.add_log_entry("Task state persisted after size check", is_important=True)
            
            if zip_size:
                self.add_log_entry(f"Compressed file size: {zip_size}", is_important=True)
                
                # Try to calculate compression ratio if we have both sizes
                if total_size and zip_size:
                    try:
                        # Extract numeric values from du output (assumes format like "10K file.txt")
                        total_size_val = total_size.split()[0]
                        zip_size_val = zip_size.split()[0]
                        self.add_log_entry(f"Compression ratio: Original: {total_size_val}, Compressed: {zip_size_val}", is_important=True)
                    except Exception as e:
                        self.add_log_entry(f"Could not calculate compression ratio: {str(e)}")
            
            # Download the ZIP file
            local_zip_path = os.path.join(download_dir, zip_filename)
            self.status = f"Downloading compressed file: {zip_filename}"
            self.progress = 90
            self.add_log_entry(f"Downloading ZIP file from {remote_zip_path} to {local_zip_path}", is_important=True)
            
            # Persist task state before download
            if hasattr(self, 'task_id'):
                persist_task(self.task_id, self)
                self.add_log_entry("Task state persisted before download", is_important=True)
            
            try:
                self.sftp_client.get(remote_zip_path, local_zip_path)
                if hasattr(self, 'task_id'):
                    persist_task(self.task_id, self)
                    self.add_log_entry("Task state persisted after download", is_important=True)
            except Exception as e:
                self.error_message = f"Download failed: {str(e)}"
                if hasattr(self, 'task_id'):
                    persist_task(self.task_id, self)
                    self.add_log_entry("Task state persisted after download error", is_error=True)
                raise
            
            # Persist task state after download
            if hasattr(self, 'task_id'):
                persist_task(self.task_id, self)
                self.add_log_entry("Task state persisted after download", is_important=True)
            
            # Verify file integrity with MD5
            remote_md5_cmd = f"md5sum {remote_zip_path} | cut -d' ' -f1"
            self.add_log_entry(f"Executing command: {remote_md5_cmd}")
            
            # Persist task state before MD5 check
            if hasattr(self, 'task_id'):
                persist_task(self.task_id, self)
                self.add_log_entry("Task state persisted before MD5 verification", is_important=True)
            
            stdin, stdout, stderr = self.ssh_client.exec_command(remote_md5_cmd)
            remote_md5 = stdout.read().decode().strip()
            
            # Persist task state after MD5 check
            if hasattr(self, 'task_id'):
                persist_task(self.task_id, self)
                self.add_log_entry("Task state persisted after MD5 verification", is_important=True)
            
            # Calculate local MD5
            with open(local_zip_path, 'rb') as f:
                local_md5 = hashlib.md5(f.read()).hexdigest()
            
            self.add_log_entry(f"Remote MD5: {remote_md5}", is_important=True)
            self.add_log_entry(f"Local MD5: {local_md5}", is_important=True)
            
            if remote_md5 == local_md5:
                self.add_log_entry(f"MD5 verification successful for {zip_filename}", is_important=True)
                
                # Delete remote ZIP file after successful download
                delete_cmd = f"rm {remote_zip_path}"
                self.add_log_entry(f"Executing cleanup command: {delete_cmd}", is_important=True)
                
                stdin, stdout, stderr = self.ssh_client.exec_command(delete_cmd)
                exit_status = stdout.channel.recv_exit_status()
                
                if exit_status == 0:
                    self.add_log_entry(f"Cleanup successful: Deleted remote ZIP file", is_important=True)
                else:
                    error = stderr.read().decode().strip()
                    self.add_log_entry(f"Cleanup failed for ZIP file: {error}", is_error=True, is_important=True)
                
                downloaded_files = [{
                    'name': zip_filename,
                    'path': local_zip_path,
                    'md5': local_md5
                }]
                
                self.progress = 100
                self.status = "Successfully downloaded compressed file"
                self.success = True
                self.result_files = downloaded_files
                
                # Persist final task state
                if hasattr(self, 'task_id'):
                    persist_task(self.task_id, self)
                    self.add_log_entry("Final task state persisted successfully", is_important=True)
                
                # Ensure task remains in tasks dictionary after completion
                if hasattr(self, 'task_id') and self.task_id in tasks:
                    self.add_log_entry("Task successfully completed and remains in tasks dictionary", is_important=True)
                else:
                    self.add_log_entry("Warning: Task not found in tasks dictionary after completion", is_error=True)
                    # Try to recover from persistence
                    if hasattr(self, 'task_id'):
                        persisted_task = recover_task(self.task_id)
                        if persisted_task:
                            self.add_log_entry("Task recovered from persistence after completion", is_important=True)
                
                return downloaded_files
            else:
                self.add_log_entry(f"MD5 verification failed for {zip_filename}", is_error=True, is_important=True)
                self.add_log_entry(f"Expected MD5: {remote_md5}, Actual MD5: {local_md5}", is_error=True, is_important=True)
                self.add_log_entry("Falling back to local compression")
                
                # Delete the corrupted local file
                try:
                    os.remove(local_zip_path)
                    self.add_log_entry(f"Deleted corrupted local ZIP file", is_important=True)
                except Exception as e:
                    self.add_log_entry(f"Failed to delete corrupted local ZIP file: {str(e)}", is_error=True)
                
                return self.download_and_compress_locally(file_mappings, download_dir)
            
            self.result_files = downloaded_files
            return downloaded_files
            
        except Exception as e:
            error_msg = f"Error during remote compression: {str(e)}"
            self.error_message = error_msg
            self.status = self.error_message
            self.add_log_entry(error_msg, is_error=True)
            
            # Persist error state
            if hasattr(self, 'task_id'):
                persist_task(self.task_id, self)
                self.add_log_entry("Error state persisted", is_important=True)
            
            # Ensure task remains in tasks dictionary even on error
            if hasattr(self, 'task_id') and self.task_id in tasks:
                self.add_log_entry("Task error occurred but task remains in tasks dictionary", is_important=True)
            else:
                self.add_log_entry("Warning: Task not found in tasks dictionary during error handling", is_error=True)
                # Try to recover from persistence
                if hasattr(self, 'task_id'):
                    persisted_task = recover_task(self.task_id)
                    if persisted_task:
                        self.add_log_entry("Task recovered from persistence during error handling", is_important=True)
            
            self.add_log_entry("Falling back to local compression")
            return self.download_and_compress_locally(file_mappings, download_dir)
    
    def download_and_compress_locally(self, file_mappings, download_dir):
        """Fallback method to download files and compress them locally"""
        self.add_log_entry("Using fallback method: download files and compress locally", is_important=True)
        
        # First download all files
        total_files = len(file_mappings)
        progress_per_file = 10 / total_files
        
        downloaded_files = []
        
        for idx, file_map in enumerate(file_mappings):
            remote_file = file_map['remote_file']
            local_file = os.path.join(download_dir, file_map['local_file'])
            
            self.status = f"Downloading file {idx+1}/{total_files}: {remote_file}"
            self.current_file = remote_file
            self.progress = 90 + int(idx * progress_per_file)
            
            # Download file
            remote_path = f"{self.current_log_path}{remote_file}"
            self.add_log_entry(f"Downloading file from {remote_path} to {local_file}", is_important=True)
            
            self.sftp_client.get(remote_path, local_file)
            
            # Verify file integrity with MD5
            remote_md5_cmd = f"md5sum {remote_path} | cut -d' ' -f1"
            self.add_log_entry(f"Executing command: {remote_md5_cmd}")
            
            stdin, stdout, stderr = self.ssh_client.exec_command(remote_md5_cmd)
            remote_md5 = stdout.read().decode().strip()
            
            # Calculate local MD5
            with open(local_file, 'rb') as f:
                local_md5 = hashlib.md5(f.read()).hexdigest()
            
            self.add_log_entry(f"Remote MD5: {remote_md5}", is_important=True)
            self.add_log_entry(f"Local MD5: {local_md5}", is_important=True)
            
            if remote_md5 == local_md5:
                self.add_log_entry(f"MD5 verification successful for {remote_file}", is_important=True)
                
                # Delete remote file after successful download
                delete_cmd = f"rm {remote_path}"
                self.add_log_entry(f"Executing cleanup command: {delete_cmd}", is_important=True)
                
                stdin, stdout, stderr = self.ssh_client.exec_command(delete_cmd)
                exit_status = stdout.channel.recv_exit_status()
                
                if exit_status == 0:
                    self.add_log_entry(f"Cleanup successful: Deleted remote file {remote_file}", is_important=True)
                else:
                    error = stderr.read().decode().strip()
                    self.add_log_entry(f"Cleanup failed for {remote_file}: {error}", is_error=True, is_important=True)
                
                downloaded_files.append({
                    'name': os.path.basename(local_file),
                    'path': local_file
                })
            else:
                self.add_log_entry(f"MD5 verification failed for {remote_file}", is_error=True, is_important=True)
                self.add_log_entry(f"Expected MD5: {remote_md5}, Actual MD5: {local_md5}", is_error=True, is_important=True)
        
        # Now compress the files locally
        if downloaded_files:
            self.add_log_entry("Compressing downloaded files locally...")
            
            # Extract original search UUID from log entries
            search_uuid = None
            for entry in self.log_entries:
                if "Starting log extraction for UUID:" in entry:
                    search_uuid = entry.split("Starting log extraction for UUID:")
                    if len(search_uuid) > 1:
                        search_uuid = search_uuid[1].strip()
                    break
            
            # If not found in logs, fallback to the first file's prefix
            if not search_uuid and file_mappings:
                search_uuid = file_mappings[0]['remote_file'].split('_')[0]
                self.add_log_entry(f"Warning: Using fallback UUID from filename: {search_uuid}", is_error=True)
            
            compressed_files = self.compress_downloaded_files(downloaded_files, download_dir, search_uuid)
            if compressed_files:
                downloaded_files = compressed_files
        
        self.progress = 100
        self.status = f"Successfully downloaded and compressed {len(downloaded_files)} files"
        self.success = True
        self.result_files = downloaded_files
        
        return downloaded_files
    
    def compress_downloaded_files(self, downloaded_files, download_dir, uuid_prefix):
        """Compress downloaded files into a ZIP archive"""
        try:
            self.status = "Compressing files locally..."
            self.add_log_entry(f"Starting local compression of {len(downloaded_files)} files", is_important=True)
            
            # Calculate total size of files to compress
            total_size_bytes = 0
            for file_info in downloaded_files:
                file_path = file_info['path']
                file_size = os.path.getsize(file_path)
                total_size_bytes += file_size
                self.add_log_entry(f"File to compress: {file_info['name']}, size: {self._format_size(file_size)}")
            
            self.add_log_entry(f"Total size of files to compress: {self._format_size(total_size_bytes)}", is_important=True)
            
            # Create a ZIP file
            zip_filename = f"{uuid_prefix}_logs.zip"
            zip_path = os.path.join(download_dir, zip_filename)
            
            self.add_log_entry(f"Creating ZIP archive: {zip_path}", is_important=True)
            
            # Track compression start time
            compression_start = datetime.now()
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_info in downloaded_files:
                    file_path = file_info['path']
                    file_name = file_info['name']
                    self.add_log_entry(f"Adding file to ZIP: {file_name}")
                    zipf.write(file_path, file_name)
            
            # Calculate compression time
            compression_time = (datetime.now() - compression_start).total_seconds()
            self.add_log_entry(f"Compression completed in {compression_time:.2f} seconds", is_important=True)
            
            # Verify the ZIP file was created
            if not os.path.exists(zip_path):
                self.error_message = "Failed to create ZIP file"
                self.add_log_entry(self.error_message, is_error=True)
                return None
            
            zip_size = os.path.getsize(zip_path)
            self.add_log_entry(f"ZIP file created successfully, size: {self._format_size(zip_size)}", is_important=True)
            
            # Calculate compression ratio
            if total_size_bytes > 0:
                compression_ratio = (1 - (zip_size / total_size_bytes)) * 100
                self.add_log_entry(f"Compression ratio: {compression_ratio:.2f}% (Original: {self._format_size(total_size_bytes)}, Compressed: {self._format_size(zip_size)})", is_important=True)
            
            # Delete the original files
            self.add_log_entry(f"Starting cleanup of {len(downloaded_files)} original local files", is_important=True)
            for file_info in downloaded_files:
                try:
                    os.remove(file_info['path'])
                    self.add_log_entry(f"Cleanup successful: Deleted original file: {file_info['path']}", is_important=True)
                except Exception as e:
                    self.add_log_entry(f"Cleanup failed for {file_info['path']}: {str(e)}", is_error=True, is_important=True)
            
            # Calculate MD5 checksum for the ZIP file
            with open(zip_path, 'rb') as f:
                zip_md5 = hashlib.md5(f.read()).hexdigest()
            
            self.add_log_entry(f"Local ZIP MD5 checksum: {zip_md5}", is_important=True)
            
            # Return the compressed file info
            return [{
                'name': zip_filename,
                'path': zip_path,
                'md5': zip_md5
            }]
            
        except Exception as e:
            error_msg = f"Error compressing files: {str(e)}"
            self.error_message = error_msg
            self.status = self.error_message
            self.add_log_entry(error_msg, is_error=True)
            return None
    
    def _format_size(self, size_bytes):
        """Format file size in human-readable format"""
        if size_bytes < 1024:
            return f"{size_bytes} bytes"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes/1024:.2f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes/(1024*1024):.2f} MB"
        else:
            return f"{size_bytes/(1024*1024*1024):.2f} GB"
    
    def close(self):
        """Close SSH and SFTP connections"""
        if self.sftp_client:
            self.sftp_client.close()
            self.add_log_entry("Closed SFTP connection")
        if self.ssh_client:
            self.ssh_client.close()
            self.add_log_entry("Closed SSH connection")
    
    def extract_logs(self, search_uuid, download_dir, compress_files=False, log_date=None):
        """Main method to extract logs containing the UUID"""
        try:
            self.status = "Starting log extraction"
            self.progress = 5
            self.compress_files = compress_files
            
            self.add_log_entry(f"Starting log extraction for UUID: {search_uuid}", is_important=True)
            self.add_log_entry(f"Compression enabled: {compress_files}", is_important=True)
            self.add_log_entry(f"Context settings - Before: {self.context_before} lines, After: {self.context_after} lines", is_important=True)
            
            if log_date:
                self.add_log_entry(f"Using log date filter: {log_date}", is_important=True)
            
            # Connect to SSH server
            self.status = "Connecting to SSH server"
            self.progress = 10
            self.add_log_entry("Setting SSH host key policy to AutoAddPolicy")
            
            if not self.connect():
                return False
                
            # Find log files containing the UUID
            log_files = self.find_log_files_with_uuid(search_uuid, log_date)
            if not log_files:
                self.status = "No log files found containing the UUID"
                self.error_message = "No matching log entries found for this UUID"
                self.add_log_entry("No log files found containing the UUID", is_important=True)
                self.close()
                return False
                
            file_mappings = self.extract_log_sections(log_files, search_uuid)
            if not file_mappings:
                self.status = "Failed to extract log sections"
                self.add_log_entry("Failed to extract log sections", is_important=True)
                self.close()
                return False
                
            downloaded_files = self.download_files(file_mappings, download_dir)
            if not downloaded_files:
                self.status = "Failed to download files"
                self.add_log_entry("Failed to download files", is_important=True)
                self.close()
                return False
                
            # Add summary of the process
            self.add_log_entry("=== EXTRACTION PROCESS SUMMARY ===", is_important=True)
            self.add_log_entry(f"Files found: {len(log_files)}", is_important=True)
            self.add_log_entry(f"Sections extracted: {len(file_mappings)}", is_important=True)
            self.add_log_entry(f"Files downloaded: {len(downloaded_files)}", is_important=True)
            
            # Add MD5 verification summary
            self.add_log_entry("=== MD5 VERIFICATION SUMMARY ===", is_important=True)
            for file_info in downloaded_files:
                file_path = file_info['path']
                file_name = file_info['name']
                if 'md5' in file_info:
                    self.add_log_entry(f"File: {file_name}, MD5: {file_info['md5']}", is_important=True)
                else:
                    # Calculate MD5 if not already in the file_info
                    with open(file_path, 'rb') as f:
                        file_md5 = hashlib.md5(f.read()).hexdigest()
                    self.add_log_entry(f"File: {file_name}, MD5: {file_md5}", is_important=True)
                    # Add MD5 to file_info for future reference
                    file_info['md5'] = file_md5
            
            # Add cleanup summary
            self.add_log_entry("=== CLEANUP SUMMARY ===", is_important=True)
            self.add_log_entry(f"Remote files cleaned: {len(file_mappings)}", is_important=True)
            if compress_files:
                self.add_log_entry(f"Local files compressed: {len(downloaded_files) - 1}", is_important=True)
                self.add_log_entry(f"Final file size: {self._format_size(os.path.getsize(downloaded_files[0]['path']))}", is_important=True)
            
            # Clean up backup files if we were using them
            if self.using_backup:
                self.add_log_entry("Cleaning up extracted backup files...", is_important=True)
                cleanup_cmd = f"rm -f {self.current_log_path}*.txt"
                stdin, stdout, stderr = self.ssh_client.exec_command(cleanup_cmd)
                cleanup_output = stdout.read().decode().strip()
                cleanup_error = stderr.read().decode().strip()
                if cleanup_error:
                    self.add_log_entry(f"Error during cleanup: {cleanup_error}", is_error=True)
                else:
                    self.add_log_entry("Successfully cleaned up backup files", is_important=True)
            
            self.close()
            self.add_log_entry("Log extraction completed successfully", is_important=True)
            return True
            
        except Exception as e:
            error_msg = f"Error during log extraction: {str(e)}"
            self.error_message = error_msg
            self.status = self.error_message
            self.add_log_entry(error_msg, is_error=True)
            self.close()
            return False

# Dictionary to store task information
tasks = {}
# Default cache expiry time in hours
CACHE_EXPIRY_HOURS = 1

# Task persistence mechanism
task_persistence = {}

def persist_task(task_id, task_data):
    """Persist task data to prevent loss during processing"""
    task_persistence[task_id] = {
        'task_id': task_id,
        'progress': task_data.progress if hasattr(task_data, 'progress') else 0,
        'success': task_data.success if hasattr(task_data, 'success') else False,
        'status': task_data.status if hasattr(task_data, 'status') else 'Unknown',
        'error_message': task_data.error_message if hasattr(task_data, 'error_message') else None,
        'files_found': task_data.files_found if hasattr(task_data, 'files_found') else 0,
        'current_file': task_data.current_file if hasattr(task_data, 'current_file') else '',
        'result_files': task_data.result_files if hasattr(task_data, 'result_files') else [],
        'log_entries': task_data.log_entries if hasattr(task_data, 'log_entries') else [],
        'created_at': task_data.created_at if hasattr(task_data, 'created_at') else datetime.now(),
        'search_uuid': task_data.search_uuid if hasattr(task_data, 'search_uuid') else None,
        'host': task_data.host if hasattr(task_data, 'host') else '',
        'username': task_data.username if hasattr(task_data, 'username') else '',
        'context_before': task_data.context_before if hasattr(task_data, 'context_before') else 100,
        'context_after': task_data.context_after if hasattr(task_data, 'context_after') else 100,
        'last_updated': datetime.now()
    }
    print(f"[PERSISTENCE] Task {task_id} persisted with progress {task_data.progress if hasattr(task_data, 'progress') else 0}")

def recover_task(task_id):
    """Recover task data from persistence if it's missing from tasks dictionary"""
    if task_id in task_persistence:
        print(f"[RECOVERY] Recovering task {task_id} from persistence")
        return task_persistence[task_id]
    return None

def update_persisted_task(task_id, **kwargs):
    """Update persisted task data"""
    if task_id in task_persistence:
        task_persistence[task_id].update(kwargs)
        task_persistence[task_id]['last_updated'] = datetime.now()
        print(f"[PERSISTENCE] Updated task {task_id} in persistence")

def cleanup_persisted_task(task_id):
    """Clean up persisted task data"""
    if task_id in task_persistence:
        del task_persistence[task_id]
        print(f"[PERSISTENCE] Cleaned up persisted task {task_id}")

def debug_task_lifecycle(task_id, action, details=""):
    """Debug function to track task lifecycle"""
    print(f"[TASK_LIFECYCLE] {action} - Task ID: {task_id} - Details: {details}")
    print(f"[TASK_LIFECYCLE] Current tasks: {list(tasks.keys())}")
    if task_id in tasks:
        task = tasks[task_id]
        if hasattr(task, 'progress'):
            print(f"[TASK_LIFECYCLE] Task progress: {task.progress}, success: {task.success}")
        else:
            print(f"[TASK_LIFECYCLE] Task is dict: {task}")
    else:
        print(f"[TASK_LIFECYCLE] Task {task_id} not found in tasks dictionary")

def clean_expired_persistence():
    """Clean expired tasks from persistence"""
    current_time = datetime.now()
    expired_persisted_tasks = []
    for task_id, task_data in list(task_persistence.items()):
        # Only clean up if task is finished (progress >= 100 or success True/False) and expired
        if (task_data['progress'] >= 100 or task_data['success']) and \
           (current_time - task_data['last_updated'] > timedelta(hours=CACHE_EXPIRY_HOURS)):
            expired_persisted_tasks.append(task_id)
    for task_id in expired_persisted_tasks:
        del task_persistence[task_id]
        print(f"[PERSISTENCE_CLEANUP] Removed expired persisted task: {task_id}")
    return len(expired_persisted_tasks)

def clean_expired_tasks():
    """Clean expired tasks from the tasks dictionary"""
    current_time = datetime.now()
    expired_tasks = []
    print(f"[CLEANUP] Starting cleanup check. Current tasks: {list(tasks.keys())}")
    print(f"[CLEANUP] Current persisted tasks: {list(task_persistence.keys())}")
    for task_id, task in list(tasks.items()):
        if isinstance(task, dict):
            continue  # Skip tasks that are still initializing
        # Don't remove tasks that are still in progress (progress < 100)
        if hasattr(task, 'progress') and task.progress < 100:
            print(f"[CLEANUP] Skipping task {task_id} - still in progress (progress: {task.progress})")
            continue
        # Only clean up if task is finished and expired
        if (task.progress >= 100 or task.success) and \
           (current_time - getattr(task, 'last_updated', getattr(task, 'created_at', current_time)) > timedelta(hours=CACHE_EXPIRY_HOURS)):
            print(f"[CLEANUP] Task {task_id} expired. Created: {getattr(task, 'created_at', 'N/A')}, Last updated: {getattr(task, 'last_updated', 'N/A')}, Age: {current_time - getattr(task, 'created_at', current_time)}")
            expired_tasks.append(task_id)
    for task_id in expired_tasks:
        print(f"[CLEANUP] Removing expired task: {task_id}")
        del tasks[task_id]
    expired_persisted = clean_expired_persistence()
    print(f"[CLEANUP] Cleanup complete. Removed {len(expired_tasks)} tasks and {expired_persisted} persisted tasks.")
    print(f"[CLEANUP] Remaining tasks: {list(tasks.keys())}")
    print(f"[CLEANUP] Remaining persisted tasks: {list(task_persistence.keys())}")
    return len(expired_tasks)

def run_extraction_task(task_id, search_uuid, download_dir, compress_files=False, log_date=None):
    """Run the log extraction task in a separate thread"""
    try:
        debug_task_lifecycle(task_id, "EXTRACTION_START", "Starting extraction process")
        
        # Get the extractor instance from the tasks dictionary
        extractor = tasks.get(task_id)
        if not extractor:
            print(f"Error: Task {task_id} not found")
            debug_task_lifecycle(task_id, "EXTRACTION_ERROR", "Task not found at start")
            return None
        
        # Persist initial task state
        persist_task(task_id, extractor)
        extractor.add_log_entry("Initial task state persisted", is_important=True)
            
        # Run the extraction process (inline to avoid attribute issues)
        extractor.status = "Starting log extraction"
        extractor.progress = 5
        extractor.compress_files = compress_files
        extractor.add_log_entry(f"Starting log extraction for UUID: {search_uuid}", is_important=True)
        extractor.add_log_entry(f"Compression enabled: {compress_files}", is_important=True)
        extractor.add_log_entry(f"Context settings - Before: {extractor.context_before} lines, After: {extractor.context_after} lines", is_important=True)
        if log_date:
            extractor.add_log_entry(f"Using log date filter: {log_date}", is_important=True)

        # 1) Connect
        if extractor.is_cancelled():
            extractor.status = "Task canceled before connect"
            extractor.success = False
            extractor.progress = 100
            persist_task(task_id, extractor)
            extractor.add_log_entry("Final canceled state persisted", is_important=True)
            return task_id
        extractor.status = "Connecting to SSH server"
        extractor.progress = 10
        extractor.add_log_entry("Setting SSH host key policy to AutoAddPolicy")
        if not extractor.connect():
            success = False
        else:
            # 2) Find matching files (current path first, fallback to backup if needed)
            if extractor.is_cancelled():
                extractor.status = "Task canceled"
                try:
                    extractor.cleanup_extracted_txt()
                except Exception:
                    pass
                extractor.success = False
                extractor.progress = 100
                persist_task(task_id, extractor)
                extractor.add_log_entry("Final canceled state persisted", is_important=True)
                return task_id
            log_files = extractor.find_log_files_with_uuid(search_uuid, log_date)
            if not log_files:
                extractor.status = "No log files found containing the UUID"
                extractor.error_message = "No matching log entries found for this UUID"
                extractor.add_log_entry("No log files found containing the UUID", is_important=True)
                success = False
            else:
                # 3) Extract sections
                if extractor.is_cancelled():
                    extractor.status = "Task canceled during extraction"
                    try:
                        extractor.cleanup_extracted_txt()
                    except Exception:
                        pass
                    extractor.success = False
                    extractor.progress = 100
                    persist_task(task_id, extractor)
                    extractor.add_log_entry("Final canceled state persisted", is_important=True)
                    return task_id
                file_mappings = extractor.extract_log_sections(log_files, search_uuid)
                if not file_mappings:
                    extractor.status = "Failed to extract log sections"
                    extractor.add_log_entry("Failed to extract log sections", is_important=True)
                    success = False
                else:
                    # 4) Download (and compress if requested)
                    if extractor.is_cancelled():
                        extractor.status = "Task canceled during download"
                        try:
                            extractor.cleanup_extracted_txt()
                        except Exception:
                            pass
                        extractor.success = False
                        extractor.progress = 100
                        persist_task(task_id, extractor)
                        extractor.add_log_entry("Final canceled state persisted", is_important=True)
                        return task_id
                    downloaded_files = extractor.download_files(file_mappings, download_dir)
                    success = bool(downloaded_files)
                    if not success:
                        extractor.status = "Failed to download files"
                        extractor.add_log_entry("Failed to download files", is_important=True)

        # Add a small delay to ensure all operations are complete
        import time
        time.sleep(0.5)
        # Final remote cleanup sweep (safe, UUID-scoped). This ensures no generated artifacts remain.
        try:
            if extractor and hasattr(extractor, 'cleanup_remote_generated_files'):
                extractor.add_log_entry("Performing final remote cleanup sweep", is_important=True)
                extractor.cleanup_remote_generated_files(include_zip=True)
        except Exception:
            pass

        if success:
            print(f"Task {task_id} completed successfully")
            debug_task_lifecycle(task_id, "EXTRACTION_SUCCESS", "Task completed successfully")
            # Ensure task is marked as successful
            extractor.success = True
            extractor.progress = 100
            extractor.status = "Task completed successfully"
            
            # Persist final success state
            persist_task(task_id, extractor)
            extractor.add_log_entry("Final success state persisted", is_important=True)
        else:
            print(f"Task {task_id} failed: {extractor.error_message}")
            debug_task_lifecycle(task_id, "EXTRACTION_FAILED", f"Task failed: {extractor.error_message}")
            # Ensure task is marked as failed with proper error message
            if not extractor.error_message:
                extractor.error_message = "No matching log entries found for this UUID"
            extractor.success = False
            extractor.progress = 100  # Mark as complete even if failed
            extractor.status = extractor.error_message
            
            # Persist final failure state
            persist_task(task_id, extractor)
            extractor.add_log_entry("Final failure state persisted", is_important=True)
            
        debug_task_lifecycle(task_id, "EXTRACTION_COMPLETE", f"Success: {success}, Progress: {extractor.progress}")
        return task_id
    except Exception as e:
        print(f"Error in extraction task: {str(e)}")
        debug_task_lifecycle(task_id, "EXTRACTION_EXCEPTION", f"Exception: {str(e)}")
        if task_id in tasks:
            tasks[task_id].error_message = f"Task failed: {str(e)}"
            tasks[task_id].status = f"Error: {str(e)}"
            tasks[task_id].success = False
            tasks[task_id].progress = 100
            tasks[task_id].add_log_entry(f"Task failed: {str(e)}", is_error=True)
            
            # Persist exception state
            persist_task(task_id, tasks[task_id])
            tasks[task_id].add_log_entry("Exception state persisted", is_important=True)
        return None

def start_extraction_task(search_uuid, download_dir, compress_files=False, log_date=None, force_new=False, ssh_host='10.132.130.77', ssh_username='renapp', ssh_password='renapp@123', connection_timeout=30, retry_count=3, verbose_logging=False, context_before=100, context_after=100, log_path_current='/app/jalin_renaissance/', log_path_backup='/app/renaissance_log_backup_jalin/', file_name_pattern='renaissance{date}*.txt'):
    """Start a new extraction task or return an existing one"""
    # Clean up expired tasks first
    clean_expired_tasks()
    
    # Check if there's an existing task for this UUID that's still valid
    if not force_new:
        for task_id, task in tasks.items():
            if isinstance(task, SSHLogExtractor) and task.search_uuid == search_uuid and task.success and task.result_files:
                # Check if the task is still valid (less than 1 hour old)
                if datetime.now() - task.created_at < timedelta(hours=1):
                    print(f"Found existing task {task_id} for UUID {search_uuid}")
                    return task_id
    
    # Create a new task
    return _create_new_task(search_uuid, download_dir, compress_files, log_date, ssh_host, ssh_username, ssh_password, connection_timeout, retry_count, verbose_logging, context_before, context_after, log_path_current, log_path_backup, file_name_pattern)

def _create_new_task(search_uuid, download_dir, compress_files=False, log_date=None, ssh_host='10.132.130.77', ssh_username='renapp', ssh_password='renapp@123', connection_timeout=30, retry_count=3, verbose_logging=False, context_before=100, context_after=100, log_path_current='/app/jalin_renaissance/', log_path_backup='/app/renaissance_log_backup_jalin/', file_name_pattern='renaissance{date}*.txt'):
    """Create and start a new extraction task"""
    # Create an extractor instance first to get the task_id
    extractor = SSHLogExtractor(
        host=ssh_host, 
        username=ssh_username, 
        password=ssh_password,
        connection_timeout=connection_timeout,
        retry_count=retry_count,
        verbose_logging=verbose_logging,
        context_before=context_before,
        context_after=context_after,
        log_path_current=log_path_current,
        log_path_backup=log_path_backup,
        file_name_pattern=file_name_pattern
    )
    
    # Store the task_id before starting the thread
    task_id = extractor.task_id
    
    # Store the task in the global dictionary
    tasks[task_id] = extractor
    
    # Set the search UUID
    extractor.search_uuid = search_uuid
    
    debug_task_lifecycle(task_id, "CREATED", f"UUID: {search_uuid}")
    
    # Start the extraction in a separate thread
    task_thread = threading.Thread(
        target=run_extraction_task,
        args=(task_id, search_uuid, download_dir, compress_files, log_date)
    )
    task_thread.daemon = True
    task_thread.start()
    
    debug_task_lifecycle(task_id, "STARTED", "Thread started")
    
    # Return the task_id immediately
    return task_id

def get_task_status(task_id):
    """Get the status of a task"""
    debug_task_lifecycle(task_id, "STATUS_CHECK", "Requesting status")
    
    task = tasks.get(task_id)
    if not task:
        # Try to recover from persistence
        persisted_task = recover_task(task_id)
        if persisted_task:
            print(f"Task {task_id} recovered from persistence")
            debug_task_lifecycle(task_id, "RECOVERED", "From persistence")
            
            # Return status from persisted data
            if persisted_task['success']:
                return {
                    "state": "SUCCESS",
                    "status": persisted_task['status'],
                    "progress": 100,
                    "files_found": persisted_task['files_found'],
                    "result_files": persisted_task['result_files'],
                    "ssh_connection": f"{persisted_task['host']} ({persisted_task['username']})",
                    "log_entries": persisted_task['log_entries'],
                    "created_at": persisted_task['created_at'].strftime('%Y-%m-%d %H:%M:%S'),
                    "expires_at": (persisted_task['created_at'] + timedelta(hours=CACHE_EXPIRY_HOURS)).strftime('%Y-%m-%d %H:%M:%S'),
                    "context_before": persisted_task['context_before'],
                    "context_after": persisted_task['context_after']
                }
            elif persisted_task['error_message']:
                return {
                    "state": "FAILURE",
                    "status": persisted_task['error_message'],
                    "progress": persisted_task['progress'],
                    "error": persisted_task['error_message'],
                    "files_found": persisted_task['files_found'],
                    "current_file": persisted_task['current_file'],
                    "ssh_connection": f"{persisted_task['host']} ({persisted_task['username']})",
                    "log_entries": persisted_task['log_entries'],
                    "created_at": persisted_task['created_at'].strftime('%Y-%m-%d %H:%M:%S'),
                    "expires_at": (persisted_task['created_at'] + timedelta(hours=CACHE_EXPIRY_HOURS)).strftime('%Y-%m-%d %H:%M:%S'),
                    "context_before": persisted_task['context_before'],
                    "context_after": persisted_task['context_after']
                }
            else:
                return {
                    "state": "PENDING",
                    "status": persisted_task['status'],
                    "progress": persisted_task['progress'],
                    "files_found": persisted_task['files_found'],
                    "current_file": persisted_task['current_file'],
                    "ssh_connection": f"{persisted_task['host']} ({persisted_task['username']})",
                    "log_entries": persisted_task['log_entries'],
                    "created_at": persisted_task['created_at'].strftime('%Y-%m-%d %H:%M:%S'),
                    "expires_at": (persisted_task['created_at'] + timedelta(hours=CACHE_EXPIRY_HOURS)).strftime('%Y-%m-%d %H:%M:%S'),
                    "context_before": persisted_task['context_before'],
                    "context_after": persisted_task['context_after']
                }
        
        # Add debugging information
        print(f"Task {task_id} not found in tasks dictionary. Available tasks: {list(tasks.keys())}")
        print(f"Available persisted tasks: {list(task_persistence.keys())}")
        debug_task_lifecycle(task_id, "NOT_FOUND", "Task not in dictionary or persistence")
        return {"state": "FAILURE", "status": "Task not found", "progress": 0}
    
    debug_task_lifecycle(task_id, "FOUND", f"Task found, progress: {task.progress if hasattr(task, 'progress') else 'N/A'}")
    
    # Update persistence with current task state
    persist_task(task_id, task)
    
    if isinstance(task, dict):
        return {
            "state": "PENDING",
            "status": task.get("status", "Unknown"),
            "progress": task.get("progress", 0)
        }
    
    # Check if result files still exist
    if task.success and task.result_files:
        missing_files = []
        for file_info in task.result_files:
            if not os.path.exists(file_info['path']):
                missing_files.append(file_info['name'])
        
        if missing_files:
            task.success = False
            task.error_message = f"Result files not found: {', '.join(missing_files)}"
            task.add_log_entry(task.error_message, is_error=True)
    
    # Cancellation-aware state reporting
    if hasattr(task, 'cancel_event') and task.is_cancelled():
        if task.progress >= 100:
            return {
                "state": "CANCELED",
                "status": task.status or "Task canceled",
                "progress": task.progress,
                "error": None,
                "files_found": task.files_found,
                "current_file": task.current_file,
                "ssh_connection": f"{task.host} ({task.username})",
                "log_entries": task.log_entries,
                "created_at": task.created_at
            }
        else:
            return {
                "state": "CANCELLING",
                "status": task.status or "Cancellation in progress",
                "progress": task.progress,
                "error": None,
                "files_found": task.files_found,
                "current_file": task.current_file,
                "ssh_connection": f"{task.host} ({task.username})",
                "log_entries": task.log_entries,
                "created_at": task.created_at
            }

    if task.error_message:
        return {
            "state": "FAILURE",
            "status": task.error_message,
            "progress": task.progress,
            "error": task.error_message,
            "files_found": task.files_found,
            "current_file": task.current_file,
            "ssh_connection": f"{task.host} ({task.username})",
            "log_entries": task.log_entries,
            "created_at": task.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            "expires_at": (task.created_at + timedelta(hours=CACHE_EXPIRY_HOURS)).strftime('%Y-%m-%d %H:%M:%S'),
            "context_before": task.context_before,
            "context_after": task.context_after
        }
    
    if task.success:
        return {
            "state": "SUCCESS",
            "status": task.status,
            "progress": 100,
            "files_found": task.files_found,
            "result_files": task.result_files,
            "ssh_connection": f"{task.host} ({task.username})",
            "log_entries": task.log_entries,
            "created_at": task.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            "expires_at": (task.created_at + timedelta(hours=CACHE_EXPIRY_HOURS)).strftime('%Y-%m-%d %H:%M:%S'),
            "context_before": task.context_before,
            "context_after": task.context_after
        }
    
    return {
        "state": "PENDING",
        "status": task.status,
        "progress": task.progress,
        "files_found": task.files_found,
        "current_file": task.current_file,
        "ssh_connection": f"{task.host} ({task.username})",
        "log_entries": task.log_entries,
        "created_at": task.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        "expires_at": (task.created_at + timedelta(hours=CACHE_EXPIRY_HOURS)).strftime('%Y-%m-%d %H:%M:%S'),
        "context_before": task.context_before,
        "context_after": task.context_after
    }

def clear_all_tasks():
    """Clear all tasks from the cache"""
    global tasks, task_persistence
    old_task_count = len(tasks)
    old_persistence_count = len(task_persistence)
    
    tasks = {}
    task_persistence = {}
    
    print(f"Cleared all {old_task_count} tasks and {old_persistence_count} persisted tasks from cache")
    return old_task_count + old_persistence_count