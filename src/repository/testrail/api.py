from ...api.testrail import TestrailApiClient

class TestrailApiRepository:
    def __init__(self, client: TestrailApiClient):
        self.client = client
    
    def get_all_users(self):
        return self.client.get('get_users')
    
    def get_users(self, limit = 250, offset = 0):
        return self.client.get('get_users/' + f'&limit={limit}&offset={offset}')
    
    def get_groups(self, limit = 250, offset = 0):
        return self.client.get('get_groups/' + f'&limit={limit}&offset={offset}')
    
    def get_case_types(self):
        return self.client.get('get_case_types')
    
    def get_result_statuses(self):
        return self.client.get('get_statuses')
    
    def get_case_statuses(self):
        return self.client.get('get_case_statuses')
    
    def get_priorities(self):
        return self.client.get('get_priorities')
    
    def get_case_fields(self):
        return self.client.get('get_case_fields')
    
    def get_configurations(self, project_id: int):
        return self.client.get('get_configs/' + str(project_id))
    
    def get_projects(self, limit = 250, offset = 0):
        return self.client.get('get_projects/' + f'&limit={limit}&offset={offset}')
    
    def get_suites(self, project_id, offset = 0, limit = 100):
        response = self.client.get('get_suites/' + str(project_id) + f'&limit={limit}')
        
        # Handle both list and dict responses from TestRail API
        if isinstance(response, dict) and 'suites' in response:
            suites = response['suites']
        elif isinstance(response, list):
            suites = response
        else:
            suites = []
        
        if (suites and len(suites) == limit):
            additional_suites = self.get_suites(project_id, offset + limit, limit)
            if isinstance(additional_suites, list):
                suites += additional_suites
        return suites
    
    def get_sections(self, project_id: int, limit: int = 100, offset: int = 0, suite_id: int = 0):
        uri = 'get_sections/' + str(project_id) + f'&limit={limit}&offset={offset}'
        if (suite_id > 0):
            uri += f'&suite_id={suite_id}'
        
        response = self.client.get(uri)
        
        # Handle both list and dict responses from TestRail API
        if isinstance(response, dict) and 'sections' in response:
            return response['sections']
        elif isinstance(response, list):
            return response
        else:
            return []
    
    def get_shared_steps(self, project_id: int, limit: int = 250, offset: int = 0):
        return self.client.get('get_shared_steps/' + str(project_id) + f'&limit={limit}&offset={offset}')
    
    def get_cases(self, project_id: int, suite_id: int = 0, limit: int = 250, offset: int = 0) -> dict:
        uri = 'get_cases/' + str(project_id) + f'&limit={limit}&offset={offset}'
        if (suite_id > 0):
            uri += f'&suite_id={suite_id}'
        return self.client.get(uri)
    
    def get_runs(self, project_id: int, suite_id: int = 0, created_after: int = 0, limit: int = 250, offset: int = 0):
        uri = 'get_runs/' + str(project_id) + f'&limit={limit}&offset={offset}'
        if (created_after > 0):
            uri += f'&created_after={created_after}'
        if (suite_id > 0):
            uri += f'&suite_id={suite_id}'
        return self.client.get(uri)
    
    def get_results(self, run_id: int, limit: int = 250, offset: int = 0):
        return self.client.get('get_results_for_run/' + str(run_id) + f'&limit={limit}&offset={offset}')
    
    def get_attachment(self, attachment):
        return self.client.get_attachment(attachment)
    
    def get_attachments_list(self):
        return self.client.get_attachments_list()
    
    def get_attachments_case(self, case_id: int):
        return self.client.get('get_attachments_for_case/' + str(case_id))
    
    def get_test(self, test_id: int):
        return self.client.get('get_test/' + str(test_id))
    
    def get_tests(self, run_id: int, limit: int = 250, offset: int = 0):
        return self.client.get('get_tests/' + str(run_id) + f'&limit={limit}&offset={offset}')
    
    def get_plans(self, project_id: int, limit: int = 250, offset: int = 0):
        return self.client.get('get_plans/' + str(project_id) + f'&limit={limit}&offset={offset}')
    
    def get_plan(self, plan_id: int):
        return self.client.get('get_plan/' + str(plan_id))
    
    def get_milestones(self, project_id: int, limit: int = 250, offset: int = 0):
        return self.client.get('get_milestones/' + str(project_id) + f'&limit={limit}&offset={offset}')
