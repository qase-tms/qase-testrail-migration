from .support import ConfigManager, Logger, Mappings, ThrottledThreadPoolExecutor, Pools
from .service import QaseService, DryRunQaseService, TestrailService, QaseScimService
from .entities import Users, Fields, Projects, Suites, Cases, Runs, Milestones, Configurations, Attachments, SharedSteps
from concurrent.futures import ThreadPoolExecutor


class TestRailImporter:
    def __init__(self, config: ConfigManager, logger: Logger, dry_run: bool = False) -> None:
        self.pools = Pools(
            qase_pool=ThrottledThreadPoolExecutor(max_workers=8, requests=230, interval=10),
            tr_pool=ThreadPoolExecutor(max_workers=8),
            logger=logger,
        )

        self.logger = logger
        self.config = config
        self.qase_scim_service = None
        
        self.dry_run = dry_run
        if dry_run:
            logger.log('[DRY-RUN] No data will be written to Qase.', 'warn')
            self.qase_service = DryRunQaseService(config, logger)
        else:
            self.qase_service = QaseService(config, logger)
        if config.get('qase.scim_token') and not dry_run:
            self.qase_scim_service = QaseScimService(config, logger)

        self.testrail_service = TestrailService(config, logger)

        self.active_project_code = None

        self.mappings = Mappings(self.qase_service.resolve_user_id(self.config.get('users.default')))

    def start(self):
        # Step 1. Build users map (if migration is enabled)
        if self.config.get('users.migrate') is not False:
            self.mappings = Users(
                self.qase_service,
                self.testrail_service,
                self.logger,
                self.mappings,
                self.config,
                self.pools,
                self.qase_scim_service,
            ).import_users()
        else:
            self.logger.log("User migration is disabled by configuration")

        # Step 2. Import project and build projects map
        self.mappings = Projects(
            self.qase_service, 
            self.testrail_service, 
            self.logger, 
            self.mappings,
            self.config,
            self.pools,
        ).import_projects()

        # Step 3. Import attachments
        self.mappings = Attachments(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.config,
            self.pools,
        ).import_all_attachments()

        # Step 4. Import custom fields
        self.mappings = Fields(
            self.qase_service, 
            self.testrail_service, 
            self.logger, 
            self.mappings,
            self.config,
            self.pools,
        ).import_fields()

        # Step 5. Import projects data in parallel
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            for project in self.mappings.projects:
                # Submit each project import to the thread pool
                future = executor.submit(self.import_project_data, project)
                futures.append(future)

            # Wait for all futures to complete
            for future in futures:
                # This will also re-raise any exceptions caught during execution of the callable
                future.result()

        self.mappings.stats.print()
        self.mappings.stats.save(str(self.config.get('prefix')))
        self.mappings.stats.save_xlsx(str(self.config.get('prefix')))
        print(f"\nqase-testrail-migration v{self.logger.version}")
        if self.logger.log_file:
            print(f"Full log: {self.logger.log_file}")

    def import_project_data(self, project):
        self.logger.print_group(f'Importing project: {project["name"]}'
                                + (' ('
                                   + project['suite_title']
                                   + ')' if 'suite_title' in project else ''))

        self.mappings = Configurations(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.pools,
        ).import_configurations(project)
        
        self.mappings = SharedSteps(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.pools,
            self.config,
        ).import_shared_steps(project)

        self.mappings = Milestones(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
        ).import_milestones(project)

        self.mappings = Suites(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.config,
            self.pools,
        ).import_suites(project)

        Cases(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.config,
            self.pools,
        ).import_cases(project)

        Runs(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.config,
            project,
            self.pools,
        ).import_runs()
