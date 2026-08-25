import base64
import time
import requests
import re
import http.client
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from ..support.rate_limiter import RateLimiter


class TestrailApiClient:
    def __init__(self, base_url, user, token, logger, max_retries=7, backoff_factor=5, api_token=None, requests_per_minute=0):
        if not base_url.endswith('/'):
            base_url += '/'
        self.__url = base_url + 'index.php?/api/v2/'
        self._attachment_url = base_url + 'index.php?/attachments/get/'
        self.logger = logger
        self.base_url = base_url

        # Use Basic Auth with API token for API v2 calls
        if api_token:
            auth_user = user
            auth_token = api_token
        else:
            auth_user = user
            auth_token = token
            
        self.auth = str(
            base64.b64encode(
                bytes('%s:%s' % (auth_user, auth_token), 'utf-8')
            ),
            'ascii'
        ).strip()

        self.headers = {
            'Authorization': 'Basic ' + self.auth,
            'Content-Type': 'application/json',
        }
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.page_size = 30
        
        # Initialize rate limiter
        self.rate_limiter = RateLimiter(requests_per_minute)
        if self.rate_limiter.is_enabled():
            self.logger.log(f'Rate limiting enabled: {requests_per_minute} requests per minute')

        # Create a session object for HTML-based operations (attachments)
        self.session = requests.Session()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept': '*/*',
            'Connection': 'keep-alive',
        }
        login_response = self.session.post(base_url + 'index.php?/auth/login/', data={'name': user, 'password': token, 'rememberme': '1'}, headers=headers)

        soup = BeautifulSoup(login_response.content, 'html.parser')

        # Find the input tag with name="_token" and extract the value attribute
        csrf_input = soup.find('input', {'name': '_token'})
        if csrf_input and 'value' in csrf_input.attrs:
            self.csrf_token = csrf_input['value']
        else:
            self.logger.log('CSRF token not found, using fallback approach')
            self.csrf_token = None

        if login_response.status_code != 200:
            self.logger.log('Failed to login to TestRail API and get auth cookie')
            self.session = None

    def get(self, uri):
        return self.send_request(requests.get, uri)

    def send_request(self, request_method, uri, payload=None):
        url = self.__url + uri
        method_name = request_method.__name__.upper()
        
        # Log request details (debug level)
        self.logger.log(f'[TestRail API Request] {method_name} {url}', level='debug')
        if payload:
            self.logger.log(f'[TestRail API Request] Payload: {payload}', level='debug')
        
        for attempt in range(self.max_retries + 1):
            try:
                # Apply rate limiting before making the request
                self.rate_limiter.wait_if_needed()
                
                response = request_method(url, headers=self.headers, data=payload)
                
                # Log response status (info level for visibility)
                self.logger.log(f'[TestRail API] {method_name} {uri} -> Status: {response.status_code}')
                
                # Log detailed response information (debug level)
                self.logger.log(f'[TestRail API Response] Full URL: {url}', level='debug')
                self.logger.log(f'[TestRail API Response] Headers: {dict(response.headers)}', level='debug')
                
                # Log response body
                try:
                    response_text = response.text
                    # Truncate very long responses for readability
                    if len(response_text) > 5000:
                        self.logger.log(f'[TestRail API Response] Body (truncated, {len(response_text)} chars): {response_text[:5000]}...', level='debug')
                    else:
                        self.logger.log(f'[TestRail API Response] Body: {response_text}', level='debug')
                except Exception as e:
                    self.logger.log(f'[TestRail API Response] Failed to read response body: {str(e)}', level='debug')
                
                if response.status_code == 429:
                    # Rate limit exceeded - wait and retry
                    retry_delay = self.rate_limiter.get_retry_delay()
                    self.logger.log(f'Rate limit exceeded (429), waiting {retry_delay:.2f} seconds before retry')
                    time.sleep(retry_delay)
                    continue
                elif response.status_code <= 201:
                    return self.process_response(response, uri)
                elif response.status_code == 403:
                    self.logger.log(f'Access denied (403) for URL: {url}')
                    raise APIError('Access denied.')
                elif response.status_code == 400:
                    self.logger.log(f'Invalid data or entity not found (400) for URL: {url} | {response.text}')
                    raise APIError('Invalid data or entity not found.')
                else:
                    self.logger.log(f'Server error ({response.status_code}) for URL: {url}, attempt {attempt + 1}')
                    time.sleep(self.backoff_factor * (2 ** attempt))
            except (requests.exceptions.Timeout, http.client.RemoteDisconnected, ConnectionResetError, requests.exceptions.ConnectionError) as e:
                self.logger.log(f'Connection error for URL: {url}, attempt {attempt + 1}: {str(e)}')
                time.sleep(self.backoff_factor * (2 ** attempt))
            
            if attempt == self.max_retries:
                raise APIError('Max retries reached or server error.')

    def process_response(self, response, uri):
        try:
            json_data = response.json()
            # Log parsed JSON response (truncate if too large)
            json_str = json.dumps(json_data, indent=2)
            if len(json_str) > 5000:
                self.logger.log(f'[TestRail API Response] Parsed JSON (truncated, {len(json_str)} chars): {json_str[:5000]}...', level='debug')
            else:
                self.logger.log(f'[TestRail API Response] Parsed JSON: {json_str}', level='debug')
            return json_data
        except Exception as e:
            self.logger.log(f'[TestRail API Response] Failed to parse JSON response: {str(e)} | Response text: {response.text[:500]}', level='debug')
            raise APIError('Failed to parse JSON response')
            
    def get_attachment(self, id):
        if not self.session:
            self.logger.log('Failed to login to TestRail API and get auth cookie')
            return self.get(f'get_attachment/{id}')
        else:
            return self.session.get(f'{self._attachment_url}{id}')
        
    def fetch_data(self, offset):
        data = {
            'offset': offset,
            'order_by': 'created_on',
            'order_dir': 'desc',
            '_token': self.csrf_token
        }
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
        }
        self.logger.log(f'Getting attachments list, offset: {offset}')
        try:
            response = self.session.post(self.base_url + 'index.php?/attachments/overview/0', data=data, headers=headers)
            response_data = response.json()
            # Extract only the needed fields (id and project_id) from each item
            return [{"id": item["id"], "project_id": item["project_id"]} for item in response_data['data']]
        except Exception as e:
            self.logger.log(f'Failed to get attachments list, offset: {offset}: {e}')
            return None

    def get_attachments_list(self):
        max_workers = 24
        total_items = 120000
        attachments = []
        stop = False
        next_offset = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self.fetch_data, offset) for offset in range(0, max_workers * self.page_size, self.page_size)]

            while futures:
                for future in as_completed(futures):
                    futures.remove(future)
                    result = future.result()
                    if result is None:  # If a future returned None, stop submitting new tasks
                        stop = True
                        self.logger.log('No more attachments to process')
                    else:
                        attachments.extend(result)
                        if not stop:
                            next_offset += self.page_size
                            if next_offset < total_items:  # Ensure we do not exceed total_items
                                futures.append(executor.submit(self.fetch_data, next_offset))
                            else:
                                stop = True  # No more offsets to process

        return attachments
        
        

class APIError(Exception):
    pass
