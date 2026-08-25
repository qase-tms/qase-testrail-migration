import asyncio
import sys

from ..service import QaseService, QaseScimService, TestrailService
from ..support import Logger, Mappings, ConfigManager as Config, Pools


class Users:
    def __init__(
        self,
        qase_service: QaseService,
        testrail_service: TestrailService,
        logger: Logger,
        mappings: Mappings,
        config: Config,
        pools: Pools,
        scim_service: QaseScimService = None,
    ):
        self.qase = qase_service
        self.scim = scim_service
        self.testrail = testrail_service
        self.logger = logger
        self.mappings = mappings
        self.config = config
        self.pools = pools
        self.map = {}  # This is a map of TestRail user ids to Qase user ids. Used for mapping users to groups
        self.active_ids = []  # This is a list of Qase active users that should be added to groups
        self.testrail_users = []
        self.logger.divider()

    def import_users(self):
        return asyncio.run(self.import_users_async())

    async def import_users_async(self):
        await self.get_testrail_users()

        if self.scim is not None:
            await self.create_users()
            if self.config.get('groups.create'):
                await self.create_root_group()
                await self.import_groups()

        await self.build_map()

        return self.mappings

    async def build_map(self):
        self.logger.log("[Users] Building users map")
        qase_users = await self.pools.qs_gen_all(self.qase.get_all_users)
        self.mappings.stats.add_user('qase', len(qase_users))
        self.mappings.stats.add_user('testrail', len(self.testrail_users))
        i = 0
        total = len(self.testrail_users)
        self.logger.print_status('Building users map', i, total)
        # Convert once rather than per TestRail user, and index by email
        qase_by_email = {}
        for qase_user in qase_users:
            qase_user = qase_user.to_dict() if hasattr(qase_user, 'to_dict') else qase_user
            qase_by_email[str(qase_user['email']).lower()] = qase_user['id']

        overrides = self.config.get('users.map') or {}
        overrides = {str(k).lower(): v for k, v in overrides.items()}

        for testrail_user in self.testrail_users:
            i += 1
            email = str(testrail_user['email']).lower()

            # 1. An explicit override in users.map always wins
            if email in overrides:
                target = overrides[email]
                resolved = qase_by_email.get(str(target).lower()) if '@' in str(target) else target
                if resolved is None:
                    self.logger.log(
                        f"[Users] users.map sends {testrail_user['email']} to {target}, "
                        f"which is not a Qase user. Using the default user instead.", 'warning')
                    resolved = self.mappings.default_user
                else:
                    self.logger.log(f"[Users] User {testrail_user['email']} mapped to {target} by users.map")
                self.mappings.users[testrail_user['id']] = resolved

            # 2. Otherwise match on email
            elif email in qase_by_email:
                self.mappings.users[testrail_user['id']] = qase_by_email[email]
                self.logger.log(f"[Users] User {testrail_user['email']} found in Qase")

            # 3. Otherwise fall back to the resolved default user
            else:
                self.mappings.users[testrail_user['id']] = self.mappings.default_user
                self.logger.log(f"[Users] User {testrail_user['email']} not found in Qase, using default user.")

            self.logger.print_status('Building users map', i, total)

    async def create_root_group(self):
        if self.config.get('groups.name') is not None:
            group_name = self.config.get('groups.name')
        else:
            group_name = 'TestRail Migration'
        self.logger.log(f"[Users] Creating group {group_name}")
        self.mappings.group_id = await self.pools.qs(self.scim.create_group, group_name)
        for id in self.active_ids:
            self.logger.log(f"[Users] Adding user {id} to group {group_name}")
            self.scim.add_user_to_group(self.mappings.group_id, id)

    # Whether to skip users who are deactivated in TestRail. Defaults to true:
    # creating deactivated accounts in Qase consumes seats.
    def _only_active(self) -> bool:
        value = self.config.get('users.only_active')
        return True if value is None else bool(value)

    async def create_users(self):
        # 1. Fetch: show progress on console so user sees we're loading before any prompt
        print("[Users] Loading users from Qase (SCIM)...", flush=True)
        self.logger.log("[Users] Loading users from Qase using SCIM")
        all_qase_users = await self.pools.qs_gen_all(self.scim.get_all_users)
        if not isinstance(all_qase_users, list):
            all_qase_users = list(all_qase_users) if hasattr(all_qase_users, '__iter__') else [all_qase_users]
        # Flatten if we got list of lists (from async_gen_all reduce)
        flattened = []
        for x in all_qase_users:
            if isinstance(x, list):
                flattened.extend(x)
            else:
                flattened.append(x)
        all_qase_users = flattened

        # 2. Compute which users will be created (no prompt yet)
        users_to_create = []
        for testrail_user in self.testrail_users:
            flag = False
            for qase_user in all_qase_users:
                qase_email = qase_user.get('userName', '').lower() if isinstance(qase_user, dict) else getattr(qase_user, 'userName', '').lower()
                if testrail_user['email'].lower() == qase_email:
                    self.logger.log("[Users] User found in Qase using SCIM, skipping creation.")
                    qase_user_id = qase_user.get('id') if isinstance(qase_user, dict) else getattr(qase_user, 'id', None)
                    self.map[testrail_user['id']] = qase_user_id
                    if testrail_user['is_active']:
                        self.active_ids.append(qase_user_id)
                    flag = True
            if not flag:
                if testrail_user['is_active'] is False and self._only_active():
                    self.logger.log(f"[Users] User {testrail_user['email']} is not active, skipping creation.")
                    continue
                users_to_create.append(testrail_user)

        # 3. Only then: show list on console and ask for confirmation
        if self.config.get('users.create') and len(users_to_create) > 0:
            self._display_user_creation_summary(users_to_create, all_qase_users)
            sys.stdout.flush()
            sys.stderr.flush()
            confirmation = input("\n[Users] Type 'yes' to proceed with user creation: ").strip().lower()
            if confirmation != 'yes':
                self.logger.log("[Users] User creation cancelled by user. Skipping user creation.")
                return

        # 4. Proceed with user creation only if user confirmed
        if self.config.get('users.create') and len(users_to_create) > 0:
            async with asyncio.TaskGroup() as tg:
                for testrail_user in users_to_create:
                    try:
                        tg.create_task(self.import_user(testrail_user))
                    except Exception as e:
                        self.logger.log(f"[Users] Failed to create user {testrail_user['email']}", 'error')
                        self.logger.log(f'{e}')
                        continue

    def _display_user_creation_summary(self, users_to_create, qase_users):
        """Display summary of users that will be created (to console and log)."""
        def out(msg):
            print(msg, flush=True)
            self.logger.log(msg.strip())

        out("")
        out("=" * 80)
        out("USER CREATION SUMMARY")
        out("=" * 80)

        testrail_host = self.config.get('testrail.api.host') or 'N/A'
        out(f"\nSource TestRail URL: {testrail_host}")

        out(f"\nExisting Qase Users ({len(qase_users)}):")
        if len(qase_users) > 0:
            out("-" * 80)
            sorted_users = sorted(qase_users, key=lambda u: u.get('userName', '').lower() if isinstance(u, dict) else getattr(u, 'userName', '').lower())
            for i, qase_user in enumerate(sorted_users[:50], 1):
                if isinstance(qase_user, dict):
                    email = qase_user.get('userName', 'N/A')
                    name = qase_user.get('name', {})
                    if isinstance(name, dict):
                        full_name = f"{name.get('givenName', '')} {name.get('familyName', '')}".strip() or 'N/A'
                    else:
                        full_name = str(name) if name else 'N/A'
                    active = qase_user.get('active', True)
                    status = "Active" if active else "Inactive"
                    out(f"  {i}. {full_name} ({email}) - {status}")
                else:
                    email = getattr(qase_user, 'userName', 'N/A')
                    name = getattr(qase_user, 'name', None)
                    if name:
                        full_name = f"{getattr(name, 'givenName', '')} {getattr(name, 'familyName', '')}".strip() or 'N/A'
                    else:
                        full_name = 'N/A'
                    active = getattr(qase_user, 'active', True)
                    status = "Active" if active else "Inactive"
                    out(f"  {i}. {full_name} ({email}) - {status}")
            if len(qase_users) > 50:
                out(f"  ... and {len(qase_users) - 50} more users")
            out("-" * 80)
        else:
            out("  No existing users found in Qase workspace")
            out("-" * 80)

        out(f"\nUsers to be created in Qase: {len(users_to_create)}")
        out("-" * 80)

        active_users = [u for u in users_to_create if u.get('is_active', True)]
        inactive_users = [u for u in users_to_create if not u.get('is_active', True)]

        if active_users:
            out(f"\nActive Users ({len(active_users)}):")
            for i, user in enumerate(active_users, 1):
                name = user.get('name', 'N/A')
                email = user.get('email', 'N/A')
                role = user.get('role', 'N/A')
                out(f"  {i}. {name} ({email}) - Role: {role}")

        if inactive_users:
            out(f"\nInactive Users ({len(inactive_users)}):")
            for i, user in enumerate(inactive_users, 1):
                name = user.get('name', 'N/A')
                email = user.get('email', 'N/A')
                role = user.get('role', 'N/A')
                out(f"  {i}. {name} ({email}) - Role: {role} [INACTIVE]")

        out("-" * 80)
        out(f"Total: {len(users_to_create)} users")
        out("=" * 80)
        out("")

    async def import_user(self, testrail_user):
        user_id = await self.create_user(testrail_user)
        self.map[testrail_user['id']] = user_id
        if testrail_user['is_active']:
            self.active_ids.append(user_id)

    async def get_testrail_users(self):
        self.logger.log("[Users] Getting users from TestRail")
        limit = 250
        offset = 0
        while True:
            users = await self.pools.tr(self.testrail.get_users, limit, offset)
            if 'users' in users and users['users'] is not None:
                users = users['users']

            self.testrail_users = self.testrail_users + users

            if len(users) < limit:
                break

            offset += limit
        self.logger.log(f"[Users] Found {len(self.testrail_users)} users in TestRail")

    async def create_user(self, testrail_user):
        # Function creates a new user in Qase
        self.logger.log(f"[Users] Creating user {testrail_user['email']} in Qase")
        parts = testrail_user['name'].split()
        if len(parts) == 2:
            first_name = parts[0]
            last_name = parts[1]
        else:
            first_name = testrail_user['name']
            last_name = ''

        user_id = await self.pools.qs(
            self.scim.create_user,
            testrail_user['email'],
            first_name,
            last_name,
            testrail_user['role'],
            testrail_user['is_active'],
        )
        self.logger.log(f"[Users] User {testrail_user['email']} created in Qase with id {user_id}")
        return user_id

    async def import_groups(self):
        self.logger.log("[Users] Importing groups from TestRail")
        groups = await self.pools.tr_gen_all(self.get_all_groups)
        self.logger.log(f"[Users] Found {len(groups)} groups in TestRail")

        async with asyncio.TaskGroup() as tg:
            for group in groups:
                tg.create_task(self.import_group(group))

    async def import_group(self, group):
        self.logger.log(f"[Users] Importing group {group['name']}")
        group_id = await self.pools.qs(self.scim.create_group, group['name'])

        async with asyncio.TaskGroup() as tg:
            for id in group['user_ids']:
                if id in self.map:
                    if self.map[id] in self.active_ids:
                        self.logger.log(f"[Users] Adding user {id} to group {group['name']}")
                        tg.create_task(self.pools.qs_task(self.scim.add_user_to_group, group_id, self.map[id]))
                    else:
                        self.logger.log(f"[Users] User {id} is not active, skipping adding to group {group['name']}")

    def get_all_groups(self, limit=250):
        self.logger.log("[Users] Loading all groups from TestRail")
        offset = 0
        while True:
            groups = self.testrail.get_groups(limit, offset)
            if 'groups' in groups and groups['groups'] is not None:
                groups = groups['groups']

            yield groups
            offset += limit
            if len(groups) < limit:
                break
