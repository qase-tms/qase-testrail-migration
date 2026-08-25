from .stats import Stats


class Mappings:
    def __init__(self, default_user: int = 1):
        self.suites = {}
        self.users = {}
        self.types = {}
        self.priorities = {}
        self.result_statuses = {}
        self.case_statuses = {}
        self.custom_fields = {}
        self.milestones = {}
        self.configurations = {}
        self.projects = []
        self.attachments_map = {}
        self.shared_steps = {}
        self.default_priority = 1

        # A map of TestRail project ids to Qase project codes
        self.project_map = {}
        # Step fields. Used to determine if a field is a step field or not during import
        self.step_fields = []

        self.refs_id = None
        self.group_id = None
        
        # A map of TestRail case IDs to Qase case IDs for preserve_ids functionality
        self.case_id_mapping = {}

        # A map of TestRail custom fields types to Qase custom fields types
        self.custom_fields_type = {
            1: 1,
            2: 0,
            3: 2,
            4: 7,
            5: 4,
            6: 3,
            7: 8,
            8: 9,
            12: 6,
        }
        self.qase_fields_type = {
            "number": 0,
            "string": 1,
            "text": 2,
            "selectbox": 3,
            "checkbox": 4,
            "radio": 5,
            "multiselect": 6,
            "url": 7,
            "user": 8,
            "datetime": 9,
        }

        self.default_user = default_user
        self.stats = Stats()


    def get_user_id(self, id: int) -> int:
        if (id in self.users):
            return self.users[id]
        return self.default_user

    def get_case_id_mapping(self) -> dict:
        """
        Returns the mapping of original TestRail IDs to generated Qase IDs
        """
        return self.case_id_mapping

    def add_case_id_mapping(self, testrail_id: int, qase_id: int) -> None:
        """
        Adds a mapping of TestRail case ID -> Qase case ID
        """
        self.case_id_mapping[testrail_id] = qase_id

    def get_qase_case_id(self, testrail_id: int) -> int:
        """
        Returns the Qase case ID by TestRail ID
        """
        return self.case_id_mapping.get(testrail_id, testrail_id)  
