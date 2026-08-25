import requests
import json
from typing import List, Dict, Any


class QaseApiClient:
    """Qase API client for direct HTTP calls (bypasses SDK validation)."""
    
    def __init__(self, base_url: str, api_token: str, logger=None, max_retries: int = 3, backoff_factor: int = 1):
        self.base_url = base_url.rstrip('/')
        self.api_token = api_token
        self.logger = logger
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        
        self.headers = {
            'Token': api_token,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
    
    def _request(self, method: str, endpoint: str, json: Dict[str, Any] = None) -> Dict[str, Any]:
        """Make a generic HTTP request to the Qase API."""
        url = f"{self.base_url}{endpoint}"
        
        for attempt in range(self.max_retries + 1):
            try:
                if method.upper() == 'POST':
                    response = requests.post(url, headers=self.headers, json=json)
                elif method.upper() == 'GET':
                    response = requests.get(url, headers=self.headers)
                elif method.upper() == 'PUT':
                    response = requests.put(url, headers=self.headers, json=json)
                elif method.upper() == 'PATCH':
                    response = requests.patch(url, headers=self.headers, json=json)
                elif method.upper() == 'DELETE':
                    response = requests.delete(url, headers=self.headers)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 401:
                    if self.logger:
                        self.logger.log(f"Authentication failed: {response.status_code} - {response.text}", 'error')
                    return {'status': False, 'error': 'Authentication failed'}
                elif response.status_code >= 500 and attempt < self.max_retries:
                    if self.logger:
                        self.logger.log(f"Server error ({response.status_code}), retrying... (attempt {attempt + 1}/{self.max_retries})")
                    import time
                    time.sleep(self.backoff_factor * (2 ** attempt))
                    continue
                else:
                    if self.logger:
                        self.logger.log(f"Request failed: {response.status_code} - {response.text}", 'error')
                    return {'status': False, 'error': response.text}
                    
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries:
                    if self.logger:
                        self.logger.log(f"Request exception, retrying... (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                    import time
                    time.sleep(self.backoff_factor * (2 ** attempt))
                    continue
                else:
                    if self.logger:
                        self.logger.log(f"Request failed after {self.max_retries} retries: {str(e)}", 'error')
                    return {'status': False, 'error': str(e)}
        
        return {'status': False, 'error': 'Max retries exceeded'}
    
    def create_cases_bulk(self, project_code: str, cases: List[Dict[str, Any]]) -> bool:
        """Create test cases in bulk (used for cases with shared steps)."""
        url = f"{self.base_url}/case/{project_code}/bulk"
        payload = {"cases": cases}
        
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(url, headers=self.headers, json=payload)
                
                if response.status_code == 200:
                    # Parse response to check how many cases were actually created
                    try:
                        response_data = response.json()
                        # Qase API returns result.ids array with created case IDs
                        created_count = 0
                        if isinstance(response_data, dict):
                            if 'result' in response_data and isinstance(response_data['result'], dict):
                                if 'ids' in response_data['result']:
                                    created_count = len(response_data['result']['ids'])
                                elif 'id' in response_data['result']:
                                    # Single case created
                                    created_count = 1
                            elif 'ids' in response_data:
                                created_count = len(response_data['ids'])
                        
                        if created_count > 0:
                            if created_count != len(cases):
                                if self.logger:
                                    self.logger.log(f"WARNING: Only {created_count} out of {len(cases)} cases were created successfully!", 'warning')
                                    self.logger.log(f"Response: {response.text[:500]}", 'warning')
                            else:
                                if self.logger:
                                    self.logger.log(f"Successfully created {created_count} cases with shared steps")
                        else:
                            # No cases created despite 200 response
                            if self.logger:
                                self.logger.log(f"WARNING: API returned 200 but no cases were created! Response: {response.text[:500]}", 'warning')
                    except Exception as e:
                        # If we can't parse response, log it for debugging
                        if self.logger:
                            self.logger.log(f"Successfully created {len(cases)} cases with shared steps (could not parse response: {e})")
                    
                    return True
                elif response.status_code == 401:
                    if self.logger:
                        self.logger.log(f"Failed to create cases with shared steps: {response.status_code} - {response.text}", 'error')
                    return False
                elif response.status_code >= 500 and attempt < self.max_retries:
                    if self.logger:
                        self.logger.log(f"Server error ({response.status_code}), retrying... (attempt {attempt + 1}/{self.max_retries})")
                    import time
                    time.sleep(self.backoff_factor * (2 ** attempt))
                    continue
                else:
                    if self.logger:
                        error_msg = response.text
                        self.logger.log(f"Failed to create cases with shared steps: {response.status_code} - {error_msg}", 'error')
                        
                        # Try to parse error to identify problematic shared steps
                        try:
                            error_data = json.loads(error_msg)
                            if 'errorMessage' in error_data:
                                error_message = error_data['errorMessage']
                                self.logger.log(f"Error message: {error_message}", 'error')
                                
                                # If it's a shared step error, try to identify which cases are affected
                                if 'shared step' in error_message.lower() or 'shared steps' in error_message.lower():
                                    self.logger.log(f"Shared step error detected. Attempting to identify problematic cases...", 'error')
                                    # Log all shared step hashes in the batch to help identify the issue
                                    for idx, case in enumerate(cases):
                                        if 'steps' in case:
                                            shared_hashes = []
                                            for step in case.get('steps', []):
                                                if isinstance(step, dict) and 'shared' in step:
                                                    shared_hashes.append(step['shared'])
                                            if shared_hashes:
                                                self.logger.log(f"Case {idx + 1} (title: {case.get('title', 'Unknown')}) references shared steps: {shared_hashes}", 'error')
                        except:
                            pass
                        
                        if cases:
                            self.logger.log(f"Request payload (first case): {json.dumps(cases[0], indent=2, default=str)}", 'error')
                    return False
                    
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries:
                    if self.logger:
                        self.logger.log(f"Request exception, retrying... (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                    import time
                    time.sleep(self.backoff_factor * (2 ** attempt))
                    continue
                else:
                    if self.logger:
                        self.logger.log(f"Failed to create cases with shared steps after {self.max_retries} retries: {str(e)}", 'error')
                    return False
        
        return False
