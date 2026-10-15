from flask import Blueprint, request, jsonify
import requests
from bs4 import BeautifulSoup
import logging
import re
from app.config.redmine_config import REDMINE_USERNAME, REDMINE_PASSWORD, REDMINE_URL
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Blueprint
redmine_bp = Blueprint('redmine', __name__)

class RedmineConnector:
    """Handles Redmine connection and data extraction"""
    
    def __init__(self):
        self.base_url = REDMINE_URL
        self.username = REDMINE_USERNAME
        self.password = REDMINE_PASSWORD
        self.session = None
        
    def login(self):
        """Login to Redmine and establish session"""
        try:
            self.session = requests.Session()
            
            # Get login page to retrieve authenticity token
            login_page = self.session.get(f"{self.base_url}/login", timeout=15)
            login_page.raise_for_status()
            
            login_soup = BeautifulSoup(login_page.text, 'html.parser')
            
            # Find the authenticity token
            auth_token = login_soup.select_one('input[name="authenticity_token"]')
            if not auth_token:
                logger.error("Authenticity token not found on login page")
                return False
                
            auth_token = auth_token.get('value')
            
            # Prepare login data
            login_data = {
                'username': self.username,
                'password': self.password,
                'login': 'Login',
                'authenticity_token': auth_token
            }
            
            # Perform login
            login_response = self.session.post(f"{self.base_url}/login", data=login_data, timeout=15)
            login_response.raise_for_status()
            
            # Check if login was successful
            if '/login' in login_response.url or 'login-form' in login_response.text:
                logger.error("Login failed - still on login page")
                return False
                
            logger.info("Redmine login successful")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error during login: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during login: {e}")
            return False
    
    def get_issue(self, ticket_number):
        """Fetch issue information from Redmine"""
        try:
            if not self.session:
                if not self.login():
                    return None
            
            # Construct the issue URL
            issue_url = f"{self.base_url}/issues/{ticket_number}"
            logger.info(f"Fetching issue from: {issue_url}")
            
            # Get the issue page
            response = self.session.get(issue_url, timeout=15)
            response.raise_for_status()
            
            # Check if we were redirected to login
            if '/login' in response.url:
                logger.warning("Session expired, attempting to relogin")
                if not self.login():
                    return None
                response = self.session.get(issue_url, timeout=15)
                response.raise_for_status()
            
            # Parse the HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Check if issue exists
            error_message = soup.select_one('#errorExplanation')
            if error_message and "not found" in error_message.text.lower():
                logger.warning(f"Issue #{ticket_number} not found")
                return None
            
            # Extract issue information
            issue_data = self._extract_issue_data(soup, ticket_number)
            
            return issue_data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error fetching issue: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching issue: {e}")
            return None
    
    def search_issues_by_ids(self, issue_ids):
        """Search multiple Redmine issues by their IDs"""
        try:
            if not self.session:
                if not self.login():
                    return None
            
            results = []
            not_found = []
            
            for issue_id in issue_ids:
                try:
                    issue_data = self.get_issue(issue_id)
                    if issue_data:
                        results.append(issue_data)
                    else:
                        not_found.append(issue_id)
                except Exception as e:
                    logger.error(f"Error fetching issue {issue_id}: {e}")
                    not_found.append(issue_id)
            
            return {
                'found': results,
                'not_found': not_found,
                'total_requested': len(issue_ids),
                'total_found': len(results),
                'total_not_found': len(not_found)
            }
            
        except Exception as e:
            logger.error(f"Error in search_issues_by_ids: {e}")
            return None
    
    def _extract_issue_data(self, soup, ticket_number):
        """Extract issue data from HTML"""
        try:
            # Extract issue number
            issue_number = f"Issue #{ticket_number}"
            
            # Extract title - try multiple selectors
            title_selectors = [
                '#content > h2',
                'h2',
                '.issue h3',
                '#content h3'
            ]
            
            title = ""
            for selector in title_selectors:
                title_elem = soup.select_one(selector)
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    if title and title != issue_number:
                        break
            
            # Extract description - try multiple selectors
            description_selectors = [
                '#issue_description_wiki.wiki',
                '.description .wiki',
                '.issue .description',
                '#issue_description',
                '.wiki'
            ]
            
            description = ""
            for selector in description_selectors:
                desc_elem = soup.select_one(selector)
                if desc_elem:
                    description = desc_elem.get_text(separator='\n\n', strip=True)
                    if description:
                        break
            
            # Extract status
            status_selectors = [
                '.issue .attributes .status',
                '.status',
                '[class*="status"]'
            ]
            
            status = ""
            for selector in status_selectors:
                status_elem = soup.select_one(selector)
                if status_elem:
                    status = status_elem.get_text(strip=True)
                    if status:
                        break
            
            # Extract priority
            priority_selectors = [
                '.issue .attributes .priority',
                '.priority',
                '[class*="priority"]'
            ]
            
            priority = ""
            for selector in priority_selectors:
                priority_elem = soup.select_one(selector)
                if priority_elem:
                    priority = priority_elem.get_text(strip=True)
                    if priority:
                        break
            
            # Extract assignee
            assignee_selectors = [
                '.issue .attributes .assigned-to',
                '.assigned-to',
                '[class*="assigned-to"]'
            ]
            
            assignee = ""
            for selector in assignee_selectors:
                assignee_elem = soup.select_one(selector)
                if assignee_elem:
                    assignee = assignee_elem.get_text(strip=True)
                    if assignee:
                        break
            
            # Extract created date
            created_selectors = [
                '.issue .attributes .created-on',
                '.created-on',
                '[class*="created-on"]'
            ]
            
            created_date = ""
            for selector in created_selectors:
                created_elem = soup.select_one(selector)
                if created_elem:
                    created_date = created_elem.get_text(strip=True)
                    if created_date:
                        break
            
            return {
                'issue_number': issue_number,
                'title': title,
                'description': description,
                'status': status,
                'priority': priority,
                'assignee': assignee,
                'created_date': created_date,
                'url': f"{self.base_url}/issues/{ticket_number}"
            }
            
        except Exception as e:
            logger.error(f"Error extracting issue data: {e}")
            return None

# Global Redmine connector instance
redmine_connector = RedmineConnector()

@redmine_bp.route('/issues/<ticket_number>', methods=['GET'])
def get_issue(ticket_number):
    """Get Redmine issue information"""
    try:
        # Validate ticket number
        if not ticket_number or not str(ticket_number).isdigit():
            return jsonify({
                'success': False,
                'error': 'Invalid ticket number. Must be a numeric value.'
            }), 400
        
        # Get issue data
        issue_data = redmine_connector.get_issue(ticket_number)
        
        if not issue_data:
            return jsonify({
                'success': False,
                'error': f'Issue #{ticket_number} not found or could not be retrieved.'
            }), 404
        
        return jsonify({
            'success': True,
            'data': issue_data
        })
        
    except Exception as e:
        logger.error(f"Error in get_issue endpoint: {e}")
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }), 500

@redmine_bp.route('/test', methods=['GET'])
def test_connection():
    """Test Redmine connection"""
    try:
        if redmine_connector.login():
            return jsonify({
                'success': True,
                'message': 'Redmine connection successful',
                'url': REDMINE_URL,
                'username': REDMINE_USERNAME
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to connect to Redmine. Check credentials and network connection.'
            }), 401
            
    except Exception as e:
        logger.error(f"Error testing Redmine connection: {e}")
        return jsonify({
            'success': False,
            'error': f'Connection test failed: {str(e)}'
        }), 500

@redmine_bp.route('/search', methods=['POST'])
def search_issues():
    """Search Redmine issues by issue IDs"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body is required'
            }), 400
        
        # Check for different search types
        issue_ids = data.get('issue_ids', [])
        query = data.get('query', '')
        
        # If issue_ids are provided, search by IDs
        if issue_ids:
            if not isinstance(issue_ids, list):
                return jsonify({
                    'success': False,
                    'error': 'issue_ids must be a list of numeric values'
                }), 400
            
            # Validate that all IDs are numeric
            for issue_id in issue_ids:
                if not str(issue_id).isdigit():
                    return jsonify({
                        'success': False,
                        'error': f'Invalid issue ID: {issue_id}. Must be a numeric value.'
                    }), 400
            
            # Search for issues by IDs
            search_results = redmine_connector.search_issues_by_ids(issue_ids)
            
            if search_results is None:
                return jsonify({
                    'success': False,
                    'error': 'Failed to perform search. Check Redmine connection.'
                }), 500
            
            return jsonify({
                'success': True,
                'search_type': 'issue_ids',
                'data': search_results
            })
        
        # If query is provided, search by text (placeholder for future implementation)
        elif query:
            return jsonify({
                'success': False,
                'error': 'Text-based search not yet implemented. Use issue_ids instead.',
                'suggestion': 'Provide a list of issue_ids to search for specific issues.',
                'example': {
                    'issue_ids': [33000, 32999, 32998]
                }
            }), 501
        
        # No search parameters provided
        else:
            return jsonify({
                'success': False,
                'error': 'Either issue_ids or query must be provided',
                'example': {
                    'issue_ids': [33000, 32999, 32998],
                    'query': 'search term (not implemented yet)'
                }
            }), 400
        
    except Exception as e:
        logger.error(f"Error in search_issues endpoint: {e}")
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }), 500

@redmine_bp.route('/search/ids', methods=['POST'])
def search_issues_by_ids():
    """Search Redmine issues by a list of issue IDs"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body is required'
            }), 400
        
        issue_ids = data.get('issue_ids', [])
        
        if not issue_ids:
            return jsonify({
                'success': False,
                'error': 'issue_ids list is required'
            }), 400
        
        if not isinstance(issue_ids, list):
            return jsonify({
                'success': False,
                'error': 'issue_ids must be a list of numeric values'
            }), 400
        
        # Validate that all IDs are numeric
        for issue_id in issue_ids:
            if not str(issue_id).isdigit():
                return jsonify({
                    'success': False,
                    'error': f'Invalid issue ID: {issue_id}. Must be a numeric value.'
                }), 400
        
        # Search for issues by IDs
        search_results = redmine_connector.search_issues_by_ids(issue_ids)
        
        if search_results is None:
            return jsonify({
                'success': False,
                'error': 'Failed to perform search. Check Redmine connection.'
            }), 500
        
        return jsonify({
            'success': True,
            'search_type': 'issue_ids',
            'data': search_results
        })
        
    except Exception as e:
        logger.error(f"Error in search_issues_by_ids endpoint: {e}")
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }), 500

@redmine_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        return jsonify({
            'success': True,
            'service': 'Redmine API',
            'status': 'healthy',
            'base_url': REDMINE_URL,
            'timestamp': str(datetime.now())
        })
    except Exception as e:
        logger.error(f"Error in health check: {e}")
        return jsonify({
            'success': False,
            'service': 'Redmine API',
            'status': 'unhealthy',
            'error': str(e)
        }), 500
