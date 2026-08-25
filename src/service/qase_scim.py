from ..support import ConfigManager, Logger

from ..api import QaseScimClient

from qase.api_client_v1.exceptions import ApiException
from ..exceptions import ImportException


class QaseScimService:
    def __init__(self, config: ConfigManager, logger: Logger):
        self.config = config
        self.logger = logger

        self.client = QaseScimClient(
            base_url=self.config.get('qase.scim_host'),
            token=self.config.get('qase.scim_token'), 
            ssl=bool(self.config.get('qase.ssl'))
        )

    def create_user(self, email, first_name, last_name, roleTitle, is_active=True):
        try:
            payload = {
                'schemas': ['urn:ietf:params:scim:schemas:core:2.0:User'],
                'userName': email,
                'name': {
                    'familyName': last_name,
                    'givenName': first_name
                },
                'active': is_active,
                'roleTitle': roleTitle
            }
            response = self.client.create_user(payload)

            return response['id']
        except ApiException as e:
            raise ImportException(f'Failed to create user: {e}')
        
    def get_all_users(self, limit=100):
        offset = 0
        while True:
            response = self.client.get_users(limit, offset)
            users = response['Resources']
            yield users
            offset += limit
            if len(users) < limit:
                break
        
    def create_group(self, group_name):
        try:
            payload = {
                'schemas': ['urn:ietf:params:scim:schemas:core:2.0:Group'],
                'displayName': group_name
            }
            response = self.client.create_group(payload)
            return response['id']
        except ApiException as e:
            raise ImportException(f'Failed to create group: {e}')
        
    def add_user_to_group(self, group_id, user_id):
        try:
            self.client.add_user_to_group(group_id, user_id)
        except ApiException as e:
            raise ImportException(f'Failed to add user to group: {e}')
        return
        
