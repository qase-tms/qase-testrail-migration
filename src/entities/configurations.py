from ..service import QaseService, TestrailService
from ..support import Logger, Mappings, Pools

import asyncio


class Configurations:
    def __init__(
            self,
            qase_service: QaseService,
            testrail_service: TestrailService,
            logger: Logger,
            mappings: Mappings,
            pools: Pools,
    ):
        self.qase = qase_service
        self.testrail = testrail_service
        self.logger = logger
        self.mappings = mappings
        self.pools = pools

        self.map = {}
        self.logger.divider()

    def import_configurations(self, project) -> Mappings:
        return asyncio.run(self.import_configurations_async(project))

    async def import_configurations_async(self, project) -> Mappings:
        self.logger.log(f"[{project['code']}][Configurations] Importing configurations")
        configs = self.testrail.get_configurations(project['testrail_id'])
        if configs:
            self.logger.log(f"[{project['code']}][Configurations] Found {len(configs)} configurations")
            async with asyncio.TaskGroup() as tg:
                for group in configs:
                    tg.create_task(self.create_configuration_group(project, group))
        else:
            self.logger.log(f"[{project['code']}][Configurations] No configurations found")

        self.mappings.configurations[project['code']] = self.map
        
        return self.mappings

    async def create_configuration_group(self, project, group):
        self.logger.log(f"[{project['code']}][Configurations] Importing configuration group {group['name']}")

        group_id = await self.pools.qs(self.qase.create_configuration_group, project['code'], group['name'])

        if 'configs' in group and group_id:
            async with asyncio.TaskGroup() as tg:
                for config in group['configs']:
                    tg.create_task(self.create_configuration(project, config, group_id))

    async def create_configuration(self, project, config, group_id):
        self.mappings.stats.add_entity_count(project['code'], 'configurations', 'testrail')
        id = await self.pools.qs(self.qase.create_configuration, project['code'], config['name'], group_id)
        if id:
            self.mappings.stats.add_entity_count(project['code'], 'configurations', 'qase')
            self.map[config['id']] = id
