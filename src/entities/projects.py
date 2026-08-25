import asyncio

from ..service import QaseService, TestrailService
from ..support import Logger, Mappings, ConfigManager as Config, Pools
from typing import Optional

import re


class Projects:
    def __init__(
            self,
            qase_service: QaseService,
            testrail_service: TestrailService,
            logger: Logger,
            mappings: Mappings,
            config: Config,
            pools: Pools,
    ):
        self.qase = qase_service
        self.testrail = testrail_service
        self.config = config
        self.logger = logger
        self.mappings = mappings
        self.pools = pools
        self.existing_codes = set()
        self.logger.divider()

    def import_projects(self):
        return asyncio.run(self.import_projects_async())

    async def import_projects_async(self):
        self.logger.log('Importing projects from TestRail')
        self._validate_selection()

        testrail_projects = await self._get_all_projects()
        if testrail_projects:
            total = len(testrail_projects)
            self.logger.log(f'Found {str(total)} projects')
            self.logger.print_status('Importing projects', total=total)

            async with asyncio.TaskGroup() as tg:
                for i, project in enumerate(testrail_projects):
                    tg.create_task(self.import_project(i, project, total))
        else:
            self.logger.log('No projects found in TestRail')
        return self.mappings

    async def import_project(self, i, project, total):
        self.logger.log(f'Importing project: {project["name"]}. Is Completed: {project["is_completed"]}')
        if self._check_import(project['name'], project['is_completed']):
            data = {
                "testrail_id": project['id'],
                "name": project['name'],
                "suite_mode": project['suite_mode']
            }
            code = await self._create_project(project['name'], project['announcement'])
            if code:
                data['code'] = code
                self.mappings.projects.append(data)
                self.mappings.project_map[project['id']] = data['code']
                self.mappings.stats.add_project(code, project['name'])
            else:
                self.logger.log(f'Failed to create project: {project["name"]}', 'error')
        else:
            self.logger.log(f'Skipping project: {project["name"]}')
        self.logger.print_status('Importing projects', i, total)

    # Fails fast on a project selection that would import nothing
    def _validate_selection(self) -> None:
        import_all = bool(self.config.get('projects.import_all'))
        projects_to_import = self.config.get('projects.import') or []
        if not import_all and not projects_to_import:
            raise ValueError(
                "No projects selected: 'projects.import_all' is false and 'projects.import' is empty. "
                "Set 'projects.import_all' to true to migrate every project, or list the project "
                "names to migrate in 'projects.import'."
            )
        status = self.config.get('testrail.project_status')
        if status and status not in ('all', 'active', 'completed'):
            raise ValueError(
                f"Invalid 'testrail.project_status': {status!r}. "
                "Valid values are 'all', 'active' or 'completed'."
            )

    async def _get_all_projects(self):
        offset = 0
        limit = 250
        projects = []
        while True:
            result = await self.pools.tr(self.testrail.get_projects, limit, offset)
            projects = projects + result['projects']
            if result['size'] < limit:
                break
            offset += limit
        
        return projects

    # Function checks if the project should be imported
    def _check_import(self, title: str, is_completed: bool) -> bool:
        import_all = bool(self.config.get('projects.import_all'))
        projects_to_import = self.config.get('projects.import') or []
        projects_to_exclude = self.config.get('projects.exclude') or []
        project_status = self.config.get('testrail.project_status') or 'all'

        # If project is completed and we want to import only active projects, skip the project
        if is_completed and project_status == 'active':
            return False

        # If project is active and we want to import only completed projects, skip the project
        if not is_completed and project_status == 'completed':
            return False

        # An explicit exclusion always wins, even when importing everything
        if title in projects_to_exclude:
            return False

        # When not importing everything, the project has to be named in the import list
        if not import_all and title not in projects_to_import:
            return False

        # In all other cases, import the project
        return True

    # Method generates short code that will be used as a project code in from a string    
    def _short_code(self, s: str) -> str:
        s = s.replace("-", " ")  # Replace dashes with spaces

        # Remove all characters except letters
        s = re.sub('[^a-zA-Z ]', '', s)

        words = s.split()
        # Ensure the first character is a letter and make it uppercase
        if len(words) > 1:  # if the string contains multiple words
            code = ''.join(word[0] for word in words).upper()
        else:
            code = s.upper()

        code = code.replace(" ", "")

        # Truncate to 10 characters
        code = code[:10]

        # Handle duplicates by adding a letter postfix
        original_code = code
        postfix = ''
        while code in self.existing_codes or len(code) < 2:
            postfix = self._next_postfix(postfix)
            code = (original_code[:10-len(postfix)] + postfix).upper()

        self.existing_codes.add(code)
        return code

    def _next_postfix(self, postfix):
        if not postfix:
            return 'A'  # Start with 'A' if no postfix
        elif postfix[-1] == 'Z':  # If last char is 'Z', increment previous char
            return self._next_postfix(postfix[:-1]) + 'A'
        else:  # Increment the last character
            return postfix[:-1] + chr(ord(postfix[-1]) + 1)
    
    # Method creates project in Qase
    async def _create_project(self, title: str, description: Optional[str]) -> Optional[str]:
        code = self._short_code(title)
        if await self.pools.qs(self.qase.create_project, title, description, code, self.mappings.group_id):
            return code
        return None
