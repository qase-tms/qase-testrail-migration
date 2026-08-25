import asyncio
import math

from ..service import QaseService, TestrailService
from ..support import Logger, Mappings, ConfigManager as Config, Pools, format_links_as_markdown, html_to_markdown
from .attachments import Attachments

from datetime import datetime


class Runs:
    def __init__(
            self,
            qase_service: QaseService,
            testrail_service: TestrailService,
            logger: Logger,
            mappings: Mappings, config: Config,
            project: list,
            pools: Pools,
    ):
        self.qase = qase_service
        self.testrail = testrail_service
        self.config = config
        self.logger = logger
        self.mappings = mappings
        self.project = project
        self.pools = pools

        self.attachments = Attachments(self.qase, self.testrail, self.logger, self.mappings, self.config, self.pools)

        self.configurations = self.mappings.configurations[self.project['code']]

        self.created_after = self.config.get('runs.created_after')
        self.index = []
        self.logger.divider()

    def import_runs(self) -> None:
        return asyncio.run(self.import_runs_async())

    async def import_runs_async(self) -> None:
        self.logger.log(f'[{self.project["code"]}][Runs] Importing runs from TestRail project {self.project["name"]}')
        await self._build_index()
        self.logger.log(f'[{self.project["code"]}][Runs] Found {str(len(self.index))} runs')
        self.index.sort(key=lambda x: x['created_on'])
        i = 0
        async with asyncio.TaskGroup() as tg:
            for run in self.index:
                i += 1
                self.logger.print_status(f'[{self.project["code"]}] Importing runs', i, len(self.index), 1)
                tg.create_task(self._import_run(run))

    async def _build_index(self) -> None:
        self.logger.log(f'[{self.project["code"]}][Runs] Building index for project {self.project["name"]}')
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._build_runs_index())
            tg.create_task(self._build_plans_index())
        total_before_filter = len(self.index)
        if self.created_after is not None and self.created_after > 0:
            self.index = [r for r in self.index if (r.get('created_on') or 0) >= self.created_after]
            self.logger.log(
                f'[{self.project["code"]}][Runs] Filtered by created_after (>= {self.created_after}): '
                f'{len(self.index)} runs (from {total_before_filter} total)'
            )
        self.mappings.stats.add_entity_count(self.project['code'], 'runs', 'testrail', len(self.index))

    async def _build_runs_index(self) -> None:
        self.logger.log(f'[{self.project["code"]}][Runs] Building runs index')
        limit = 250
        offset = 0

        data = {
            'project_id': self.project['testrail_id'],
            'created_after': self.created_after,
            'limit': limit,
        }

        while True:
            data['offset'] = offset
            runs = await self.pools.tr(self.testrail.get_runs, **data)
            self.logger.log(f'[{self.project["code"]}][Runs] Found {str(len(runs["runs"]))} runs in TestRail')
            for run in runs['runs']:
                self.index.append({
                    'id': run['id'],
                    'name': run['name'],
                    'description': run['description'],
                    'created_on': run['created_on'],
                    'completed_on': run['completed_on'],
                    'is_completed': run['is_completed'],
                    'milestone_id': run['milestone_id'],
                    'config_ids': run['config_ids'],
                    'author_id': self.mappings.get_user_id(run['created_by']),
                })

            if runs['size'] < limit:
                break

            offset = offset + limit
        self.logger.log(f'[{self.project["code"]}][Runs] Items in index: {str(len(self.index))}')

    async def _build_plans_index(self) -> None:
        self.logger.log(f'[{self.project["code"]}][Runs] Building plans index')
        limit = 250
        offset = 0
        plans_processed = 0

        while True:
            self.logger.log(f'[{self.project["code"]}][Runs] Fetching plans from TestRail')
            plans = await self.pools.tr(self.testrail.get_plans, self.project['testrail_id'], limit, offset)
            for plan in plans['plans']:
                plans_processed += 1
                plan = self.testrail.get_plan(plan['id'])
                if plan is not None and 'entries' in plan and plan['entries'] and len(plan['entries']) > 0:
                    self.logger.log(f'[{self.project["code"]}][Runs] Fetching runs for plan {plan["id"]}')
                    for entry in plan['entries']:
                        for run in entry['runs']:
                            self.index.append({
                                'id': run['id'],
                                'name': run['name'],
                                'plan_name': plan['name'],
                                'description': run['description'],
                                'created_on': run['created_on'],
                                'completed_on': run['completed_on'],
                                'plan_id': plan['id'],
                                'config_ids': run['config_ids'],
                                'is_completed': run['is_completed'],
                                'milestone_id': run['milestone_id'],
                                'author_id': self.mappings.get_user_id(run['created_by']),
                            })
                if plans_processed % 50 == 0:
                    msg = f'[{self.project["code"]}][Runs] Processed {plans_processed} plans, {len(self.index)} runs in index so far'
                    self.logger.log(msg)
                    print(f'  {msg}', flush=True)
            if plans['size'] < limit:
                break

            offset = offset + limit
        self.logger.log(f'[{self.project["code"]}][Runs] Items in index: {str(len(self.index))}')

    async def _import_run(self, run: list) -> None:
        # Process description: replace attachments, convert HTML to markdown, format links
        if run.get('description'):
            run['description'] = self.attachments.check_and_replace_attachments(run['description'], self.project['code'])
            run['description'] = html_to_markdown(run['description'], remove_html=False)
            run['description'] = format_links_as_markdown(run['description'], self.project['code'], self.config)
        
        # Load testrail tests from the run ()
        cases_map = await self.__get_cases_for_run(run)
        self.logger.log(
            f'[{self.project["code"]}][Runs] Found {str(len(cases_map))} cases in the run {run["name"]} [{run["id"]}]')

        milestone_id = self.mappings.milestones[self.project['code']][run['milestone_id']] if run['milestone_id'] in \
                                                                                              self.mappings.milestones[
                                                                                                  self.project[
                                                                                                      'code']] else None

        if run['config_ids'] is not None and len(run['config_ids']) > 0:
            run['configurations'] = self._replace_config_ids(run['config_ids'])

            # Import results for the run
        await self._import_results_for_run(run, cases_map, milestone_id)

    def _replace_config_ids(self, config_ids: list) -> list:
        configs = []
        for config_id in config_ids:
            if config_id in self.configurations:
                configs.append(self.configurations[config_id])
        return configs

    async def _import_results_for_run(self, run: list, cases_map: dict, milestone_id: int) -> None:
        limit = 250
        offset = 0
        run_results = []

        while True:
            self.logger.log(f'[{self.project["code"]}][Runs] Fetching results for the run {run["name"]} [{run["id"]}]')
            results = await self.pools.tr(self.testrail.get_results, run['id'], limit, offset)
            if results is None:
                self.logger.log(f'[{self.project["code"]}][Runs] No results returned for the run {run["name"]} [{run["id"]}]', 'warning')
                break
            if 'results' not in results:
                self.logger.log(f'[{self.project["code"]}][Runs] Invalid response format for the run {run["name"]} [{run["id"]}]', 'warning')
                break
            run_results = run_results + self._clean_results(results['results'])
            offset = offset + limit
            if 'size' not in results or results['size'] < limit:
                break

        # Create a new test run in Qase
        run["created_on"] = max(0, min(
            [result["created_on"] if "created_on" in result and bool(result["created_on"]) else math.nan for result in
             run_results]
            + [run["created_on"] if bool(run["created_on"]) else math.nan],
            key=lambda x: (math.isnan(x), x)
        ))

        qase_run_id = await self.pools.qs(self.qase.create_run, run, self.project['code'], [v['qase_case_id'] for v in cases_map.values()],
                                          milestone_id)

        if not bool(qase_run_id):
            self.logger.log(
                f'[{self.project["code"]}][Runs] Failed to create a new run in Qase for TestRail run {run["name"]} [{run["id"]}]',
                'error')
            return

        self.logger.log(f'[{self.project["code"]}][Runs] Created a new run in Qase: {qase_run_id}')
        self.mappings.stats.add_entity_count(self.project['code'], 'runs', 'qase')

        self.logger.log(
            f'[{self.project["code"]}][Runs] Found {str(len(run_results))} results for the run {run["name"]} [{run["id"]}]')

        self.logger.log(f'[{self.project["code"]}][Runs] Merging comments for the run {run["name"]} [{run["id"]}]')
        run_results = self._merge_comments(run_results)

        self.logger.log(f'[{self.project["code"]}][Runs] Sorting results for the run {run["name"]} [{run["id"]}]')
        run_results = sorted(run_results, key=lambda x: x['created_on'])

        i = 0
        async with asyncio.TaskGroup() as tg:
            for chunk in self._chunk_list_generator(run_results, 500):
                i += 1
                self.logger.log(
                    f'[{self.project["code"]}][Runs] Importing results [Chunk {i}] for the run {run["name"]} [{run["id"]}]')
                tg.create_task(self._import_results(run, qase_run_id, cases_map, chunk))

        if run['is_completed']:
            await self.pools.tr(self.qase.complete_run, self.project['code'], qase_run_id)

    @staticmethod
    def _chunk_list_generator(results, chunk_size=500):
        """Yield successive chunks from input_list."""
        for i in range(0, len(results), chunk_size):
            yield results[i:i + chunk_size]

    def _clean_results(self, results: list) -> list:
        clean_results = []
        for result in results:
            self.logger.log(f'[{self.project["code"]}][Runs] Cleaning result: id={result.get("id")}, test_id={result.get("test_id")}, status_id={result.get("status_id")}, comment={result.get("comment")[:100] if result.get("comment") else None}, attachments={result.get("attachments")}, attachment_ids={result.get("attachment_ids")}, version={result.get("version")}')
            
            # Initialize attachments list if it doesn't exist
            if 'attachments' not in result:
                result['attachments'] = []

            if result.get('comment'):
                result_id = str(result.get('id')) if result.get('id') is not None else None
                test_id = str(result.get('test_id')) if result.get('test_id') is not None else None
                
                # Extract attachment hashes for the attachments list
                result['attachments'] = self.attachments.check_and_replace_attachments_from_string_array(
                    result['comment'], self.project['code'], result_id=result_id, test_id=test_id)
                self.logger.log(f'[{self.project["code"]}][Runs][{result["id"]}] Result attachments: {result["attachments"]}')
                
                # Process comment text: replace attachments, convert HTML to markdown, format links
                result['comment'] = self.attachments.check_and_replace_attachments(
                    result['comment'], self.project['code'], result_id=result_id, test_id=test_id)
                result['comment'] = html_to_markdown(result['comment'], remove_html=False)
                result['comment'] = format_links_as_markdown(result['comment'], self.project['code'], self.config)

            if result['status_id'] != 3:
                if result.get('attachment_ids') and len(result['attachment_ids']) > 0:
                    result_id = str(result.get('id')) if result.get('id') is not None else None
                    test_id = str(result.get('test_id')) if result.get('test_id') is not None else None
                    result['attachments'].extend(self.attachments.check_and_replace_attachments_array(
                        result['attachment_ids'], self.project['code'], result_id=result_id, test_id=test_id))
                    self.logger.log(f'[{self.project["code"]}][Runs][{result["id"]}] Result attachments after check_and_replace_attachments_array: {result["attachments"]}')
                if 'attachment_ids' in result:
                    del result['attachment_ids']
                if 'version' in result:
                    del result['version']
                clean_results.append(result)

        return clean_results

    @staticmethod
    def _merge_comments(results: list) -> list:
        comments = {}
        cleaned = []
        for result in results:
            if result['status_id'] == None:
                if result['test_id'] not in comments:
                    comments[result['test_id']] = []
                comments[result['test_id']].append(result)
            else:
                cleaned.append(result)

        for result in cleaned:
            if result['test_id'] in comments and len(comments[result['test_id']]) > 0:
                for comment in comments[result['test_id']]:
                    comment_date = datetime.fromtimestamp(result['created_on'])
                    additional_comment = f"\n On {comment_date} a comment was added: \n {str(comment['comment'] if comment['comment'] else '')}"
                    if result['comment'] is None:
                        result['comment'] = additional_comment
                    else:
                        result['comment'] = str(result['comment']) + additional_comment
                    if 'attachments' in comment:
                        if 'attachments' not in result:
                            result['attachments'] = comment['attachments']
                        else:
                            result['attachments'] += comment['attachments']
                del comments[result['test_id']]
            else:
                result['comments'] = []

        return cleaned

    async def _import_results(self, tr_run, qase_run_id, cases_map, results) -> None:
        await self.pools.qs(
            self.qase.send_bulk_results_v2,
            tr_run,
            results,
            qase_run_id,
            self.project['code'],
            self.mappings,
            cases_map,
        )

    @staticmethod
    def _merge_comments_with_same_test_id(test_results):
        # Initialize a new list to hold the processed results
        processed_results = []
        # Create a dictionary to map test_id to its corresponding index in the processed_results
        test_id_to_index = {}

        for result in test_results:
            # Check if the result is a comment
            if result['status_id'] is None:
                test_id = result['test_id']
                # If the comment is for a test_id that exists in processed_results
                if test_id in test_id_to_index:
                    index = test_id_to_index[test_id]
                    # Merge the comment and attachments with the previous result
                    comment_date = datetime.utcfromtimestamp(result['created_on']).strftime('%A, %d %B %Y %H:%M:%S')
                    additional_comment = f"\n On {comment_date} a comment was added: \n {result['comment']}"
                    processed_results[index]['comment'] += additional_comment
                    if 'attachments' in result:
                        processed_results[index].setdefault('attachments', []).extend(result['attachments'])
                # If the comment is not for a test_id that exists in processed_results, ignore it
            else:
                # Add the non-comment result to the processed_results
                processed_results.append(result)
                test_id_to_index[result['test_id']] = len(processed_results) - 1

        return processed_results

    async def __get_cases_for_run(self, run: list) -> dict:
        cases_map = {}
        limit = 250
        offset = 0
        process = True
        
        # Build a map of case_id -> title for cases in this run
        case_titles_map = {}
        case_ids_missing_title = set()

        # First pass: collect test data and try to get titles from test objects
        while process:
            tests = await self.pools.tr(self.testrail.get_tests, run['id'], limit, offset)
            if tests['size'] < limit:
                process = False
            offset = offset + limit
            for test in tests['tests']:
                if test['case_id']:
                    # Try to get title from test object first (TestRail API may include it)
                    if 'title' in test and test['title']:
                        case_titles_map[test['case_id']] = test['title']
                    else:
                        # Mark this case_id as needing title lookup
                        case_ids_missing_title.add(test['case_id'])
        
        # If we have cases missing titles, fetch them from cases API
        if case_ids_missing_title:
            # Fetch cases to get their titles
            cases_limit = 250
            cases_offset = 0
            cases_process = True
            while cases_process and case_ids_missing_title:
                cases_response = await self.pools.tr(
                    self.testrail.get_cases, 
                    self.project['testrail_id'], 
                    0,  # suite_id
                    cases_limit, 
                    cases_offset
                )
                if isinstance(cases_response, dict) and 'cases' in cases_response:
                    cases = cases_response['cases']
                    if len(cases) < cases_limit:
                        cases_process = False
                elif isinstance(cases_response, list):
                    cases = cases_response
                    if len(cases) < cases_limit:
                        cases_process = False
                else:
                    cases = []
                    cases_process = False
                
                cases_offset += cases_limit
                for case in cases:
                    if case['id'] in case_ids_missing_title:
                        case_titles_map[case['id']] = case.get('title', f"Test case {case['id']}")
                        case_ids_missing_title.discard(case['id'])
                        # Stop if we found all missing titles
                        if not case_ids_missing_title:
                            cases_process = False
                            break
        
        # Second pass: build the cases_map with titles
        process = True
        offset = 0
        while process:
            tests = await self.pools.tr(self.testrail.get_tests, run['id'], limit, offset)
            if tests['size'] < limit:
                process = False
            offset = offset + limit
            for test in tests['tests']:
                if test['case_id']:
                    # Use case ID mapping if available
                    qase_case_id = self.mappings.get_qase_case_id(test['case_id'])
                    # Get title from our map, or use fallback
                    case_title = case_titles_map.get(
                        test['case_id'], 
                        test.get('title', f"Test case {test['case_id']}")
                    )
                    # Store both qase_case_id and title for each test
                    cases_map[test['id']] = {
                        'qase_case_id': qase_case_id,
                        'title': case_title
                    }
                    # Log that we're storing title in cases_map
                    self.logger.log(f'[{self.project["code"]}][Runs] Storing in cases_map: test_id={test["id"]}, case_id={test["case_id"]}, qase_case_id={qase_case_id}, title="{case_title}"')
                    if qase_case_id != test['case_id']:
                        self.logger.log(f'[{self.project["code"]}][Runs] Mapped TestRail case ID {test["case_id"]} to Qase case ID {qase_case_id}')
                    else:
                        self.logger.log(f'[{self.project["code"]}][Runs] No mapping found for case ID {test["case_id"]}, using original ID')
        return cases_map
