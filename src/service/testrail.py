from ..repository.testrail import TestrailApiRepository
from ..api.testrail import TestrailApiClient


class TestrailService:
    def __init__(self, config, logger):
        self.api_repository = TestrailApiRepository(
            TestrailApiClient(
                base_url = config.get('testrail.api.host'),
                user = config.get('testrail.api.user'),
                token = config.get('testrail.api.password'),  # Use password for HTML auth
                api_token = config.get('testrail.api.token'),  # Use API token for API calls
                logger = logger,
                max_retries = 5,
                backoff_factor = 5,
                requests_per_minute = config.get('testrail.api.requests_per_minute') or 0
            )
        )

        self.logger = logger

        self.repository = self.api_repository
    
    def get_users(self, limit: int = 250, offset: int = 0):
        return self.repository.get_users(limit, offset)
    
    def get_groups(self, limit: int = 250, offset: int = 0):
        return self.repository.get_groups(limit, offset)
    
    def get_case_types(self):
        return self.repository.get_case_types()
    
    def get_result_statuses(self):
        return self.repository.get_result_statuses()
    
    def get_case_statuses(self):
        return self.repository.get_case_statuses()
    
    def get_priorities(self):
        return self.repository.get_priorities()
    
    def get_configurations(self, project_id: int):
        return self.repository.get_configurations(project_id)
    
    def get_case_fields(self):
        return self.repository.get_case_fields()
    
    def get_shared_steps(self, project_id: int, limit: int = 250, offset: int = 0):
        return self.repository.get_shared_steps(project_id, limit, offset)
    
    def get_projects(self, limit: int = 250, offset: int = 0):
        return self.repository.get_projects(limit, offset)
    
    def get_suites(self, project_id):
        return self.repository.get_suites(project_id)
    
    def get_sections(self, project_id: int, limit: int = 100, offset: int = 0, suite_id: int = 0):
        return self.repository.get_sections(project_id, limit, offset, suite_id)
    
    def get_cases(self, project_id: int, suite_id: int = 0, limit: int = 250, offset: int = 0):
        return self.repository.get_cases(project_id, suite_id, limit, offset)
    
    def get_runs(self, project_id: int, suite_id: int = 0, created_after: int = 0, limit: int = 250, offset: int = 0):
        return self.repository.get_runs(project_id, suite_id, created_after, limit, offset)

    def get_results(self, run_id: int, limit: int = 250, offset: int = 0):
        return self.repository.get_results(run_id, limit, offset)
    
    def get_attachment(self, attachment_id: int):
        return self.api_repository.get_attachment(attachment_id)
    
    def get_attachments_list(self):
        return self.api_repository.get_attachments_list()
    
    def get_attachments_case(self, case_id: int):
        return self.repository.get_attachments_case(case_id)
    
    def get_test(self, test_id: int):
        return self.repository.get_test(test_id)
    
    def get_tests(self, run_id: int, limit: int = 250, offset: int = 0):
        return self.repository.get_tests(run_id, limit, offset)
    
    def get_plans(self, project_id: int, limit: int = 250, offset: int = 0):
        return self.repository.get_plans(project_id, limit, offset)
    
    def get_plan(self, plan_id: int):
        try:
            return self.repository.get_plan(plan_id)
        except Exception as e:
            self.logger.log(f'[TestRail] Failed to get plan {plan_id}: {e}')
            return None
    
    def get_milestones(self, project_id: int, limit: int = 250, offset: int = 0):
        return self.repository.get_milestones(project_id, limit, offset)
