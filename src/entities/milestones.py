from ..service import QaseService, TestrailService
from ..support import Logger, Mappings


class Milestones:
    def __init__(self, qase_service: QaseService, testrail_service: TestrailService, logger: Logger, mappings: Mappings) -> Mappings:
        self.qase = qase_service
        self.testrail = testrail_service
        self.logger = logger
        self.mappings = mappings

        self.map = {}
        self.logger.divider()
        self.i = 0

    def import_milestones(self, project) -> Mappings:
        self.logger.log(f"[{project['code']}][Milestones] Importing milestones")
        limit = 250
        offset = 0

        milestones = []
        while True:
            tr_milestones = self.testrail.get_milestones(project['testrail_id'], limit, offset)
            milestones += tr_milestones['milestones']
            if tr_milestones['size'] < limit:
                break
            offset += limit
        self.logger.log(f"[{project['code']}][Milestones] Found {len(milestones)} milestones")

        self.logger.print_status(f'[{project["code"]}] Importing milestones', self.i, len(milestones), 1)
        self.import_milestone_list(milestones, project['code'])
        
        return self.mappings
    
    def import_milestone_list(self, milestones, code, prefix = ''):
        for milestone in milestones:
            self.mappings.stats.add_entity_count(code, 'milestones', 'testrail')
            id = self.import_milestone(milestone, code, prefix)
            if id:
                self.mappings.stats.add_entity_count(code, 'milestones', 'qase')
                self.map[milestone['id']] = id
            self.i += 1
            self.logger.print_status(f'[{code}] Importing milestones', self.i, len(milestones), 1)

            if 'milestones' in milestone and len(milestone['milestones']) > 0:
                self.import_milestone_list(milestone['milestones'], code, milestone['name'])
            
        self.mappings.milestones[code] = self.map
        
    def import_milestone(self, milestone, code, prefix = ''):
        self.logger.log(f"[{code}][Milestones] Importing milestone {milestone['name']}")

        name = milestone['name']
        if prefix != '':
            name = '[' + prefix + '] ' + name
            
        return self.qase.create_milestone(
            code, 
            title=name, 
            description=milestone['description'],
            status=milestone['is_completed'],
            due_date=milestone['due_on']
        )