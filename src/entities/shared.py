import asyncio

from ..service import QaseService, TestrailService
from ..support import Logger, Mappings, ConfigManager as Config, Pools, format_links_as_markdown, html_to_markdown
from .attachments import Attachments


class SharedSteps:
    def __init__(
            self,
            qase_service: QaseService,
            testrail_service: TestrailService,
            logger: Logger,
            mappings: Mappings,
            pools: Pools,
            config: Config,
    ):
        self.qase = qase_service
        self.testrail = testrail_service
        self.logger = logger
        self.mappings = mappings
        self.pools = pools
        self.config = config

        # Initialize attachments handler
        self.attachments = Attachments(self.qase, self.testrail, self.logger, self.mappings, self.config, self.pools)

        self.map = {}
        self.logger.divider()
        self.i = 0

    def import_shared_steps(self, project) -> Mappings:
        return asyncio.run(self.import_shared_steps_async(project))

    async def import_shared_steps_async(self, project) -> Mappings:
        self.logger.log(f"[{project['code']}][Shared Steps] Importing shared steps")
        limit = 250
        offset = 0

        shared_steps = []
        while True:
            tr_shared = self.testrail.get_shared_steps(project['testrail_id'], limit, offset)
            shared_steps += tr_shared['shared_steps']
            if tr_shared['size'] < limit:
                break
            offset += limit

        self.mappings.stats.add_entity_count(project['code'], 'shared_steps', 'testrail', len(shared_steps))
            
        self.logger.log(f"[{project['code']}][Shared Steps] Found {len(shared_steps)} shared steps")

        self.logger.print_status(f'[{project["code"]}] Importing shared steps', self.i, len(shared_steps), 1)
        async with asyncio.TaskGroup() as tg:
            for step in shared_steps:
                tg.create_task(self.create_shared_step(project, step, len(shared_steps)))

        self.mappings.shared_steps[project["code"]] = self.map
        
        return self.mappings

    async def create_shared_step(self, project, step, cnt):
        # Process steps: replace attachments, convert HTML to markdown, format links
        processed_steps = []
        if step.get('custom_steps_separated'):
            for step_item in step['custom_steps_separated']:
                # Process action/content field
                action = step_item.get('content', '')
                if action:
                    action = self.attachments.check_and_replace_attachments(action, project['code'])
                    action = html_to_markdown(action, remove_html=False)
                    action = format_links_as_markdown(action, project['code'], self.config)
                action = action.strip() if action else ''
                
                if action == '':
                    action = 'No action'
                
                # Process expected field
                expected = step_item.get('expected', '')
                if expected:
                    expected = self.attachments.check_and_replace_attachments(expected, project['code'])
                    expected = html_to_markdown(expected, remove_html=False)
                    expected = format_links_as_markdown(expected, project['code'], self.config)
                expected = expected.strip() if expected else None
                
                processed_steps.append({
                    'content': action,
                    'expected': expected
                })
        
        id = await self.pools.qs(
            self.qase.create_shared_step,
            project["code"],
            step['title'],
            processed_steps,
        )
        if id:
            self.mappings.stats.add_entity_count(project['code'], 'shared_steps', 'qase')
            # Always update the mapping with the hash returned by Qase
            # This ensures we use the correct hash even if content changed (e.g., after link replacement)
            old_hash = self.map.get(step['id'])
            self.map[step['id']] = id
            if old_hash and old_hash != id:
                self.logger.log(f'[{project["code"]}][Shared Steps] Hash changed for shared step "{step["title"]}" (TestRail ID: {step["id"]}): {old_hash} -> {id}. This may indicate content changed (e.g., after link replacement).', 'warning')
            self.logger.log(f'[{project["code"]}][Shared Steps] Created shared step: {step["title"]} (TestRail ID: {step["id"]}) -> Qase hash: {id}')
        else:
            self.logger.log(f'[{project["code"]}][Shared Steps] Failed to create shared step: {step["title"]} (TestRail ID: {step["id"]}). Cases referencing this shared step will fail.', 'error')
        self.i += 1
        self.logger.print_status(f'[{project["code"]}] Importing shared steps', self.i, cnt, 1)
