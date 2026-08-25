import asyncio
import json

from ..service import QaseService, TestrailService
from ..support import Logger, Mappings, ConfigManager as Config, Pools


class Fields:
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
        self.logger = logger
        self.mappings = mappings
        self.config = config
        self.pools = pools

        self.refs_id = None
        self.system_fields = []

        self.map = {}
        self.logger.divider()

    def import_fields(self):
        return asyncio.run(self.import_fields_async())

    async def import_fields_async(self):
        self.logger.log('[Fields] Loading custom fields from Qase')
        qase_custom_fields = await self.pools.qs(self.qase.get_case_custom_fields)
        self.logger.log('[Fields] Loading custom fields from TestRail')
        testrail_custom_fields = await self.pools.tr(self.testrail.get_case_fields)
        self.logger.log('[Fields] Loading system fields from Qase')
        qase_system_fields = await self.pools.qs(self.qase.get_system_fields)
        for field in qase_system_fields:
            self.system_fields.append(field.to_dict())

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._create_types_map())
            tg.create_task(self._create_priorities_map())
            tg.create_task(self._create_result_statuses_map())

        total = len(testrail_custom_fields)
        
        self.logger.log(f'[Fields] Found {str(total)} custom fields')

        fields_to_import = self._get_fields_to_import(testrail_custom_fields)

        i = 0
        self.logger.print_status('Importing custom fields', i, total)
        self.mappings.stats.add_custom_field('testrail', total)
        async with asyncio.TaskGroup() as tg:
            for field in testrail_custom_fields:
                i += 1
                if field['name'] in fields_to_import and field['is_active']:
                    if field['type_id'] in self.mappings.custom_fields_type:
                        tg.create_task(self._create_custom_field(field, qase_custom_fields))
                else:
                    self.logger.log(f'[Fields] Skipping custom field: {field["name"]}')

                # Detect step fields dynamically by checking field configuration
                # Step fields have specific config options: has_expected, has_additional, or has_reference
                # These options are unique to step fields and indicate the field stores step data
                if field.get('configs'):
                    for config in field['configs']:
                        options = config.get('options', {})
                        # Check if this is a step field by looking for step-specific options
                        if (options.get('has_expected') is not None or 
                            options.get('has_additional') is not None or
                            options.get('has_reference') is not None):
                            # This is a step field - add to step_fields list
                            if field['name'] not in self.mappings.step_fields:
                                self.mappings.step_fields.append(field['name'])
                            break
                self.logger.print_status('Importing custom fields', i, total)

        await self._create_refs_field(qase_custom_fields)
        await self._create_testrail_original_id_field(qase_custom_fields)
        await self._create_estimate_field(qase_custom_fields)
        await self._create_assigned_to_field(qase_custom_fields)
        
        # Print detailed summary of all custom fields
        self._print_custom_fields_summary()
        
        return self.mappings

    def _get_fields_to_import(self, custom_fields):
        self.logger.log('[Fields] Building a map for fields to import')
        fields_to_import = self.config.get('cases.fields')
        if fields_to_import is not None and len(fields_to_import) == 0 and not fields_to_import:
            for field in custom_fields:
                if field['system_name'].startswith('custom_'):
                    fields_to_import.append(field['name'])
        return fields_to_import

    async def _create_custom_field(self, field, qase_fields):
        # Check if field has configurations
        if not field.get('configs') or len(field['configs']) == 0:
            self.logger.log(f'[Fields] Skipping field {field["name"]} - no configurations found')
            return

        self.logger.log(f'[Fields] Processing field {field["name"]} with {len(field["configs"])} configurations')

        # If field has only one configuration and is global, create a single field
        if len(field['configs']) == 1 and field['configs'][0]['context'].get('is_global', False):
            self.logger.log(f'[Fields] Creating global field for {field["name"]}')
            await self._create_single_global_field(field, qase_fields)
            return

        # If field has multiple configurations, create unique fields for each project
        if len(field['configs']) > 1:
            self.logger.log(f'[Fields] Creating project-specific fields for {field["name"]} with {len(field["configs"])} configurations')
            await self._create_project_specific_fields(field, qase_fields)
            return

        # If field has one configuration but is not global, create field for specific projects
        if len(field['configs']) == 1 and not field['configs'][0]['context'].get('is_global', False):
            project_ids = field['configs'][0]['context'].get('project_ids', [])
            self.logger.log(f'[Fields] Creating single project field for {field["name"]} for projects: {project_ids}')
            await self._create_single_project_field(field, qase_fields)
            return

    async def _create_single_global_field(self, field, qase_fields):
        """Create a single field that is enabled for all projects"""
        # Check if field already exists
        if qase_fields and len(qase_fields) > 0:
            for qase_field in qase_fields:
                if (qase_field.title == field['label'] and 
                    self.mappings.custom_fields_type[field['type_id']] == self.mappings.qase_fields_type[qase_field.type.lower()]):
                    self.logger.log(f'[Fields] Global custom field already exists: {field["label"]}')
                    
                    # Check if field needs to be updated
                    needs_update, update_data = self.qase.check_field_update_needed(field, qase_field, self.mappings)
                    
                    if needs_update:
                        self.logger.log(f'[Fields] Global field {field["label"]} needs update: {update_data}')
                        
                        # Update the field
                        update_success = await self.pools.qs(self.qase.update_custom_field, qase_field.id, update_data)
                        
                        if update_success:
                            self.logger.log(f'[Fields] Successfully updated global field {field["label"]}')
                            
                            # Refresh field data after update
                            if 'missing_values' in update_data or 'needs_mapping_update' in update_data:
                                # Get updated field to refresh values
                                updated_field = await self.pools.qs(self.qase.get_custom_field, qase_field.id)
                                
                                if updated_field and hasattr(updated_field, 'value') and updated_field.value:
                                    try:
                                        values_data = json.loads(updated_field.value) if isinstance(updated_field.value, str) else updated_field.value
                                        field['qase_values'] = {}
                                        for value in values_data:
                                            if hasattr(value, 'id') and hasattr(value, 'title'):
                                                field['qase_values'][value.id] = value.title
                                            elif isinstance(value, dict) and 'id' in value and 'title' in value:
                                                field['qase_values'][value['id']] = value['title']
                                        
                                        # Also create TestRail ID to Qase ID mapping
                                        if 'configs' in field and len(field['configs']) > 0:
                                            config = field['configs'][0]
                                            if 'options' in config and 'items' in config['options']:
                                                items = config['options']['items']
                                                if items:
                                                    # Parse items string into TestRail ID mapping
                                                    tr_values = {}
                                                    for line in items.split('\n'):
                                                        if ',' in line:
                                                            key, title = line.split(',', 1)
                                                            tr_values[key.strip()] = title.strip()
                                                    
                                                    # Create TestRail ID to Qase ID mapping
                                                    field['tr_key_to_qase_id'] = {}
                                                    self.logger.log(f'[Fields] Creating mapping for field {field["label"]} (qase_id: {field.get("qase_id")})')
                                                    self.logger.log(f'[Fields] TestRail values: {tr_values}')
                                                    self.logger.log(f'[Fields] Qase values: {field["qase_values"]}')
                                                    
                                                    for tr_key, tr_title in tr_values.items():
                                                        for qase_id, qase_title in field['qase_values'].items():
                                                            if tr_title.strip() == qase_title.strip():
                                                                field['tr_key_to_qase_id'][tr_key] = qase_id
                                                                self.logger.log(f'[Fields] Mapped: TestRail {tr_key} ("{tr_title}") -> Qase ID {qase_id} ("{qase_title}")')
                                                                break
                                                        else:
                                                            self.logger.log(f'[Fields] No match found for TestRail value {tr_key} ("{tr_title}")')
                                                    
                                                    self.logger.log(f'[Fields] Created TestRail to Qase mapping for field {field["label"]}: {field["tr_key_to_qase_id"]}')
                                        
                                    except (json.JSONDecodeError, AttributeError) as e:
                                        self.logger.log(f'[Fields] Error updating field mapping: {e}', 'warning')
                        else:
                            self.logger.log(f'[Fields] Failed to update global field {field["label"]}', 'warning')
                    
                    # Set up field data for later use
                    if qase_field.type.lower() in ("selectbox", "multiselect", "radio"):
                        if 'qase_values' not in field:
                            field['qase_values'] = {}
                            values = json.loads(qase_field.value)
                            for value in values:
                                field['qase_values'][value['id']] = value['title']
                    field['qase_id'] = qase_field.id
                    self.mappings.custom_fields[field['name']] = field
                    return

        # Create new global field
        data = self.qase.prepare_custom_field_data(field, self.mappings)
        qase_id = await self.pools.qs(self.qase.create_custom_field, data)
        if qase_id > 0:
            self.logger.log(f'[Fields] Global custom field created: {field["label"]}')
            field['qase_id'] = qase_id
            
            # Create tr_key_to_qase_id mapping for the newly created field
            if field.get('qase_values'):
                self._create_tr_key_to_qase_id_mapping(field)
            
            self.mappings.custom_fields[field['name']] = field
            self.mappings.stats.add_custom_field('qase')
        else:
            self.logger.log(f'[Fields] Failed to create global custom field: {field["label"]}', 'error')

    async def _create_single_project_field(self, field, qase_fields):
        """Create a single field for specific projects"""
        config = field['configs'][0]
        project_ids = config['context'].get('project_ids', [])
        
        # Check if field already exists
        if qase_fields and len(qase_fields) > 0:
            for qase_field in qase_fields:
                if (qase_field.title == field['label'] and 
                    self.mappings.custom_fields_type[field['type_id']] == self.mappings.qase_fields_type[qase_field.type.lower()]):
                    self.logger.log(f'[Fields] Project-specific custom field already exists: {field["label"]}')
                    
                    # Check if field needs to be updated
                    needs_update, update_data = self.qase.check_field_update_needed(field, qase_field, self.mappings)
                    
                    if needs_update:
                        self.logger.log(f'[Fields] Project field {field["label"]} needs update: {update_data}')
                        
                        # Update the field
                        update_success = await self.pools.qs(self.qase.update_custom_field, qase_field.id, update_data)
                        
                        if update_success:
                            self.logger.log(f'[Fields] Successfully updated project field {field["label"]}')
                            
                            # Refresh field data after update
                            if 'missing_values' in update_data or 'needs_mapping_update' in update_data:
                                # Get updated field to refresh values
                                updated_field = await self.pools.qs(self.qase.get_custom_field, qase_field.id)
                                
                                if updated_field and hasattr(updated_field, 'value') and updated_field.value:
                                    try:
                                        values_data = json.loads(updated_field.value) if isinstance(updated_field.value, str) else updated_field.value
                                        field['qase_values'] = {}
                                        for value in values_data:
                                            if hasattr(value, 'id') and hasattr(value, 'title'):
                                                field['qase_values'][value.id] = value.title
                                            elif isinstance(value, dict) and 'id' in value and 'title' in value:
                                                field['qase_values'][value['id']] = value['title']
                                    except (json.JSONDecodeError, AttributeError):
                                        pass
                        else:
                            self.logger.log(f'[Fields] Failed to update project field {field["label"]}', 'warning')
                    else:
                        self.logger.log(f'[Fields] Project field {field["label"]} is up to date')
                    
                    # Set up field data for later use
                    if qase_field.type.lower() in ("selectbox", "multiselect", "radio"):
                        if 'qase_values' not in field:
                            field['qase_values'] = {}
                            values = json.loads(qase_field.value)
                            for value in values:
                                field['qase_values'][value['id']] = value['title']
                        
                        # Always refresh the mapping when field exists
                        if 'configs' in field and len(field['configs']) > 0:
                            config = field['configs'][0]
                            if 'options' in config and 'items' in config['options']:
                                items = config['options']['items']
                                if items:
                                    # Parse items string into TestRail ID mapping
                                    tr_values = {}
                                    for line in items.split('\n'):
                                        if ',' in line:
                                            key, title = line.split(',', 1)
                                            tr_values[key.strip()] = title.strip()
                                    
                                    # Create TestRail ID to Qase ID mapping
                                    field['tr_key_to_qase_id'] = {}
                                    self.logger.log(f'[Fields] Creating mapping for project field {field["label"]} (qase_id: {field.get("qase_id")})')
                                    self.logger.log(f'[Fields] TestRail values: {tr_values}')
                                    self.logger.log(f'[Fields] Qase values: {field["qase_values"]}')
                                    
                                    for tr_key, tr_title in tr_values.items():
                                        for qase_id, qase_title in field['qase_values'].items():
                                            if tr_title.strip() == qase_title.strip():
                                                field['tr_key_to_qase_id'][tr_key] = qase_id
                                                self.logger.log(f'[Fields] Mapped: TestRail {tr_key} ("{tr_title}") -> Qase ID {qase_id} ("{qase_title}")')
                                                break
                                        else:
                                            self.logger.log(f'[Fields] No match found for TestRail value {tr_key} ("{tr_title}")')
                                            
                                    self.logger.log(f'[Fields] Refreshed TestRail to Qase mapping for project field {field["label"]}: {field["tr_key_to_qase_id"]}')
                    
                    field['qase_id'] = qase_field.id
                    self.mappings.custom_fields[field['name']] = field
                    return

        # Create new project-specific field
        data = self.qase.prepare_custom_field_data(field, self.mappings)
        qase_id = await self.pools.qs(self.qase.create_custom_field, data)
        if qase_id > 0:
            self.logger.log(f'[Fields] Project-specific custom field created: {field["label"]}')
            field['qase_id'] = qase_id
            
            # Create tr_key_to_qase_id mapping for the newly created field
            if field.get('qase_values'):
                self._create_tr_key_to_qase_id_mapping(field)
            
            self.mappings.custom_fields[field['name']] = field
            self.mappings.stats.add_custom_field('qase')
        else:
            self.logger.log(f'[Fields] Failed to create project-specific custom field: {field["label"]}', 'error')

    async def _create_project_specific_fields(self, field, qase_fields):
        """Create unique fields for each project when field has multiple configurations"""
        # Process each project configuration separately
        for config in field['configs']:
            if not config.get('context', {}).get('project_ids'):
                self.logger.log(f'[Fields] Skipping config for field {field["name"]} - no project_ids found')
                continue
                
            project_ids = config['context']['project_ids']
            self.logger.log(f'[Fields] Processing config for field {field["name"]} with project_ids: {project_ids}')
                
            for project_id in project_ids:
                if project_id not in self.mappings.project_map:
                    self.logger.log(f'[Fields] Skipping project {project_id} for field {field["name"]} - project not in mappings')
                    continue
                    
                project_code = self.mappings.project_map[project_id]
                field_name_with_project = f"{field['name']}_{project_code}"
                
                # Create field copy early for this project
                field_copy = field.copy()
                # Create project-specific label: original label + project code
                field_copy['label'] = f"{field['label']} {project_code}"
                field_copy['configs'] = [config]  # Use only this project's config
                
                self.logger.log(f'[Fields] Creating field {field_copy["label"]} for project {project_code}')
                
                # Store project information for validation
                field_copy['project_id'] = project_id
                field_copy['project_code'] = project_code
                
                # Check if field already exists for this project
                field_exists = False
                if qase_fields and len(qase_fields) > 0:
                    for qase_field in qase_fields:
                        if (qase_field.title == field_copy['label'] and 
                            self.mappings.custom_fields_type[field['type_id']] == self.mappings.qase_fields_type[qase_field.type.lower()]):
                            self.logger.log(f'[Fields] Custom field already exists for project {project_code}: {field_copy["label"]}')
                            
                            # Check if field needs to be updated
                            needs_update, update_data = self.qase.check_field_update_needed(field_copy, qase_field, self.mappings)
                            
                            if needs_update:
                                self.logger.log(f'[Fields] Project field {field_copy["label"]} needs update: {update_data}')
                                
                                # Update the field
                                update_success = await self.pools.qs(self.qase.update_custom_field, qase_field.id, update_data)
                                
                                if update_success:
                                    self.logger.log(f'[Fields] Successfully updated project field {field_copy["label"]}')
                                    
                                    # Refresh field data after update
                                    if 'missing_values' in update_data or 'needs_mapping_update' in update_data:
                                        # Get updated field to refresh values
                                        updated_field = await self.pools.qs(self.qase.get_custom_field, qase_field.id)
                                        
                                        if updated_field and hasattr(updated_field, 'value') and updated_field.value:
                                            try:
                                                values_data = json.loads(updated_field.value) if isinstance(updated_field.value, str) else updated_field.value
                                                field_copy['qase_values'] = {}
                                                for value in values_data:
                                                    if hasattr(value, 'id') and hasattr(value, 'title'):
                                                        field_copy['qase_values'][value.id] = value.title
                                                    elif isinstance(value, dict) and 'id' in value and 'title' in value:
                                                        field_copy['qase_values'][value['id']] = value['title']
                                                
                                                # Create TestRail ID to Qase ID mapping
                                                if 'configs' in field and len(field['configs']) > 0:
                                                    config = field['configs'][0]
                                                    if 'options' in config and 'items' in config['options']:
                                                        items = config['options']['items']
                                                        if items:
                                                            # Parse items string into TestRail ID mapping
                                                            tr_values = {}
                                                            for line in items.split('\n'):
                                                                if ',' in line:
                                                                    key, title = line.split(',', 1)
                                                                    tr_values[key.strip()] = title.strip()
                                                            
                                                            # Create TestRail ID to Qase ID mapping
                                                            field_copy['tr_key_to_qase_id'] = {}
                                                            self.logger.log(f'[Fields] Creating mapping for project field {field_copy["label"]} (qase_id: {qase_field.id})')
                                                            self.logger.log(f'[Fields] TestRail values: {tr_values}')
                                                            self.logger.log(f'[Fields] Qase values: {field_copy["qase_values"]}')
                                                            
                                                            for tr_key, tr_title in tr_values.items():
                                                                for qase_id, qase_title in field_copy['qase_values'].items():
                                                                    if tr_title.strip() == qase_title.strip():
                                                                        field_copy['tr_key_to_qase_id'][tr_key] = qase_id
                                                                        self.logger.log(f'[Fields] Mapped: TestRail {tr_key} ("{tr_title}") -> Qase ID {qase_id} ("{qase_title}")')
                                                                        break
                                                                else:
                                                                    self.logger.log(f'[Fields] No match found for TestRail value {tr_key} ("{tr_title}")')
                                                            
                                                            self.logger.log(f'[Fields] Created TestRail to Qase mapping for project field {field_copy["label"]}: {field_copy["tr_key_to_qase_id"]}')
                                            except (json.JSONDecodeError, AttributeError):
                                                pass
                                else:
                                    self.logger.log(f'[Fields] Failed to update project field {field_copy["label"]}', 'warning')
                            else:
                                self.logger.log(f'[Fields] Project field {field_copy["label"]} is up to date')
                            
                            # Set up field data for later use
                            if qase_field.type.lower() in ("selectbox", "multiselect", "radio"):
                                if 'qase_values' not in field_copy:
                                    field_copy['qase_values'] = {}
                                    values = json.loads(qase_field.value)
                                    for value in values:
                                        field_copy['qase_values'][value['id']] = value['title']
                                
                                # Create TestRail ID to Qase ID mapping for existing fields
                                if 'configs' in field_copy and len(field_copy['configs']) > 0:
                                    config = field_copy['configs'][0]
                                    if 'options' in config and 'items' in config['options']:
                                        items = config['options']['items']
                                        if items:
                                            # Parse items string into TestRail ID mapping
                                            tr_values = {}
                                            for line in items.split('\n'):
                                                if ',' in line:
                                                    key, title = line.split(',', 1)
                                                    tr_values[key.strip()] = title.strip()
                                            
                                            # Create TestRail ID to Qase ID mapping
                                            field_copy['tr_key_to_qase_id'] = {}
                                            self.logger.log(f'[Fields] Creating mapping for existing project field {field_copy["label"]} (qase_id: {qase_field.id})')
                                            self.logger.log(f'[Fields] TestRail values: {tr_values}')
                                            self.logger.log(f'[Fields] Qase values: {field_copy["qase_values"]}')
                                            
                                            for tr_key, tr_title in tr_values.items():
                                                for qase_id, qase_title in field_copy['qase_values'].items():
                                                    if tr_title.strip() == qase_title.strip():
                                                        field_copy['tr_key_to_qase_id'][tr_key] = qase_id
                                                        self.logger.log(f'[Fields] Mapped: TestRail {tr_key} ("{tr_title}") -> Qase ID {qase_id} ("{qase_title}")')
                                                        break
                                                else:
                                                    self.logger.log(f'[Fields] No match found for TestRail value {tr_key} ("{tr_title}")')
                                            
                                            self.logger.log(f'[Fields] Created TestRail to Qase mapping for existing project field {field_copy["label"]}: {field_copy["tr_key_to_qase_id"]}')
                            field_copy['qase_id'] = qase_field.id
                            # Store field mapping with project-specific key
                            self.mappings.custom_fields[f"{field['name']}_{project_code}"] = field_copy
                            field_exists = True
                            break
                
                if field_exists:
                    continue
                
                # Create project-specific field data
                data = self.qase.prepare_custom_field_data(field_copy, self.mappings)
                
                # Ensure field is only enabled for this specific project
                data['is_enabled_for_all_projects'] = False
                data['projects_codes'] = [project_code]
                
                qase_id = await self.pools.qs(self.qase.create_custom_field, data)
                if qase_id > 0:
                    self.logger.log(f'[Fields] Custom field created for project {project_code}: {field_copy["label"]}')
                    field_copy['qase_id'] = qase_id
                    
                    # Create tr_key_to_qase_id mapping for the newly created field
                    if field_copy.get('qase_values'):
                        self._create_tr_key_to_qase_id_mapping(field_copy)
                    
                    # Store field mapping with project-specific key
                    self.mappings.custom_fields[f"{field['name']}_{project_code}"] = field_copy
                    self.mappings.stats.add_custom_field('qase')
                else:
                    self.logger.log(f'[Fields] Failed to create custom field for project {project_code}: {field_copy["label"]}', 'error')
        
    async def _create_refs_field(self, qase_custom_fields):
        if self.config.get('testrail.refs.enable'):
            if qase_custom_fields and len(qase_custom_fields) > 0:
                for qase_field in qase_custom_fields:
                    if qase_field.title == 'Refs':
                        self.logger.log('Refs field found')
                        self.mappings.refs_id = qase_field.id
            
            if not self.mappings.refs_id:
                self.logger.log('[Fields] Refs field not found. Creating a new one')
                data = {
                    'title': 'Refs',
                    'entity': 0, # 0 - case, 1 - run, 2 - defect,
                    'type': 2,
                    'is_filterable': True,
                    'is_visible': True,
                    'is_required': False,
                    'is_enabled_for_all_projects': True,
                }
                self.mappings.refs_id = await self.pools.qs(self.qase.create_custom_field, data)

    async def _create_testrail_original_id_field(self, qase_custom_fields):
        if not self.config.get('cases.preserve_ids'):
            if qase_custom_fields and len(qase_custom_fields) > 0:
                for qase_field in qase_custom_fields:
                    if qase_field.title == 'TestRail Original ID':
                        self.logger.log('TestRail Original ID field found')
                        self.mappings.testrail_original_id_field_id = qase_field.id
            
            if not hasattr(self.mappings, 'testrail_original_id_field_id') or not self.mappings.testrail_original_id_field_id:
                self.logger.log('[Fields] TestRail Original ID field not found. Creating a new one')
                data = {
                    'title': 'TestRail Original ID',
                    'entity': 0, # 0 - case, 1 - run, 2 - defect,
                    'type': 1, # 1 - string
                    'is_filterable': True,
                    'is_visible': True,
                    'is_required': False,
                    'is_enabled_for_all_projects': True,
                }
                self.mappings.testrail_original_id_field_id = await self.pools.qs(self.qase.create_custom_field, data)

    async def _create_estimate_field(self, qase_custom_fields):
        """Create Estimate custom field for storing converted time estimates"""
        if qase_custom_fields and len(qase_custom_fields) > 0:
            for qase_field in qase_custom_fields:
                if qase_field.title == 'Estimate':
                    self.logger.log('Estimate field found')
                    self.mappings.estimate_field_id = qase_field.id
        
        if not hasattr(self.mappings, 'estimate_field_id') or not self.mappings.estimate_field_id:
            self.logger.log('[Fields] Estimate field not found. Creating a new one')
            data = {
                'title': 'Estimate',
                'entity': 0, # 0 - case, 1 - run, 2 - defect,
                'type': 1, # 1 - string
                'is_filterable': True,
                'is_visible': True,
                'is_required': False,
                'is_enabled_for_all_projects': True,
            }
            self.mappings.estimate_field_id = await self.pools.qs(self.qase.create_custom_field, data)

    async def _create_assigned_to_field(self, qase_custom_fields):
        """Create Assigned To custom field for storing assigned user"""
        if qase_custom_fields and len(qase_custom_fields) > 0:
            for qase_field in qase_custom_fields:
                if qase_field.title == 'Assigned To':
                    self.logger.log('Assigned To field found')
                    self.mappings.assigned_to_field_id = qase_field.id
        
        if not hasattr(self.mappings, 'assigned_to_field_id') or not self.mappings.assigned_to_field_id:
            self.logger.log('[Fields] Assigned To field not found. Creating a new one')
            data = {
                'title': 'Assigned To',
                'entity': 0, # 0 - case, 1 - run, 2 - defect,
                'type': 8, # 8 - user
                'is_filterable': True,
                'is_visible': True,
                'is_required': False,
                'is_enabled_for_all_projects': True,
            }
            self.mappings.assigned_to_field_id = await self.pools.qs(self.qase.create_custom_field, data)

    async def _create_types_map(self):
        self.logger.log('[Fields] Creating types map')

        tr_types = await self.pools.tr(self.testrail.get_case_types)
        qase_types = []

        for field in self.system_fields:
            if field['slug'] == 'type':
                for option in field['options']:
                    qase_types.append(option)

        for tr_type in tr_types:
            self.mappings.types[tr_type['id']] = 1
            for qase_type in qase_types:
                if tr_type['name'].lower() == qase_type['title'].lower():
                    self.mappings.types[tr_type['id']] = int(qase_type['id'])
        
        self.logger.log('[Fields] Types map was created')

    async def _create_priorities_map(self):
        self.logger.log('[Fields] Creating priorities map')

        tr_priorities = await self.pools.tr(self.testrail.get_priorities)
        qase_priorities = []

        for field in self.system_fields:
            if field['slug'] == 'priority':
                for option in field['options']:
                    qase_priorities.append(option)

        default_priority = 1
        for qase_priority in qase_priorities:
            if qase_priority['title'].lower() == 'high':
                default_priority = int(qase_priority['id'])
                self.mappings.default_priority = default_priority
                break

        for tr_priority in tr_priorities:
            self.mappings.priorities[tr_priority['id']] = default_priority
            for qase_priority in qase_priorities:
                if tr_priority['name'].lower() == qase_priority['title'].lower():
                    self.mappings.priorities[tr_priority['id']] = int(qase_priority['id'])

        self.logger.log('[Fields] Priorities map was created')

    async def _create_result_statuses_map(self):
        self.logger.log('[Fields] Creating statuses map')

        tr_statuses = await self.pools.tr(self.testrail.get_result_statuses)
        qase_statuses = []

        for field in self.system_fields:
            if field['slug'] == 'result_status':
                for option in field['options']:
                    qase_statuses.append(option)

        for tr_status in tr_statuses:
            self.mappings.result_statuses[tr_status['id']] = 'skipped'
            for qase_status in qase_statuses:
                if tr_status['label'].lower() == qase_status['title'].lower():
                    self.mappings.result_statuses[tr_status['id']] = qase_status['slug']

        self.logger.log('[Fields] Result statuses map was created')

    def _create_case_statuses_map(self):
        self.logger.log('[Fields] Creating case statuses map')

        tr_statuses = self.testrail.get_case_statuses()
        qase_statuses = []

        for field in self.system_fields:
            if field['slug'] == 'status':
                for option in field['options']:
                    qase_statuses.append(option)

        for tr_status in tr_statuses:
            self.mappings.case_statuses[tr_status['case_status_id']] = 1
            for qase_status in qase_statuses:
                if tr_status['name'].lower() == qase_status['slug'].lower():
                    self.mappings.case_statuses[tr_status['case_status_id']] = qase_status['id']

        self.logger.log('[Fields] Case statuses map was created')

    def _create_tr_key_to_qase_id_mapping(self, field: dict) -> None:
        """
        Create mapping between TestRail field values and Qase field values.
        This ensures that when we import test cases, we can correctly map TestRail values to Qase IDs.
        """
        if 'configs' not in field or len(field['configs']) == 0:
            return
        
        config = field['configs'][0]
        if 'options' not in config or 'items' not in config['options']:
            return
        
        items = config['options']['items']
        if not items:
            return
        
        # Parse items string into TestRail ID mapping
        tr_values = {}
        for line in items.split('\n'):
            if ',' in line:
                key, title = line.split(',', 1)
                tr_values[key.strip()] = title.strip()
        
        # Create TestRail ID to Qase ID mapping
        field['tr_key_to_qase_id'] = {}
        self.logger.log(f'[Fields] Creating mapping for field {field["label"]} (qase_id: {field.get("qase_id")})')
        self.logger.log(f'[Fields] TestRail values: {tr_values}')
        self.logger.log(f'[Fields] Qase values: {field["qase_values"]}')
        
        for tr_key, tr_title in tr_values.items():
            for qase_id, qase_title in field['qase_values'].items():
                if tr_title.strip() == qase_title.strip():
                    field['tr_key_to_qase_id'][tr_key] = qase_id
                    self.logger.log(f'[Fields] Mapped: TestRail {tr_key} ("{tr_title}") -> Qase ID {qase_id} ("{qase_title}")')
                    break
            else:
                self.logger.log(f'[Fields] No match found for TestRail value {tr_key} ("{tr_title}")')
        
        self.logger.log(f'[Fields] Created TestRail to Qase mapping for field {field["label"]}: {field["tr_key_to_qase_id"]}')

    def _print_custom_fields_summary(self):
        """Print detailed summary of all custom fields with their mappings"""
        self.logger.divider()
        self.logger.log('[Fields] ===== CUSTOM FIELDS SUMMARY =====')
        
        if not self.mappings.custom_fields:
            self.logger.log('[Fields] No custom fields found')
            return
        
        # Group fields by type for better organization
        global_fields = []
        project_fields = {}
        
        for field_name, field_data in self.mappings.custom_fields.items():
            if '_' in field_name and any(project_code in field_name for project_code in self.mappings.project_map.values()):
                # This is a project-specific field
                project_code = field_name.split('_')[-1]
                if project_code not in project_fields:
                    project_fields[project_code] = []
                project_fields[project_code].append((field_name, field_data))
            else:
                # This is a global field
                global_fields.append((field_name, field_data))
        
        # Print global fields
        if global_fields:
            self.logger.log('[Fields] --- GLOBAL FIELDS ---')
            for field_name, field_data in global_fields:
                self._print_field_details(field_name, field_data, is_global=True)
        
        # Print project-specific fields
        if project_fields:
            self.logger.log('[Fields] --- PROJECT-SPECIFIC FIELDS ---')
            for project_code in sorted(project_fields.keys()):
                self.logger.log(f'[Fields] Project: {project_code}')
                for field_name, field_data in project_fields[project_code]:
                    self._print_field_details(field_name, field_data, is_global=False)
        
        self.logger.log('[Fields] ===== END SUMMARY =====')
        self.logger.divider()

    def _print_field_details(self, field_name, field_data, is_global=True):
        """Print detailed information about a single field"""
        field_type = "Global" if is_global else "Project"
        
        self.logger.log(f'[Fields] {field_type} Field: {field_data.get("label", field_name)}')
        self.logger.log(f'[Fields]   ├─ Name: {field_name}')
        self.logger.log(f'[Fields]   ├─ TestRail ID: {field_data.get("id", "N/A")}')
        self.logger.log(f'[Fields]   ├─ Qase ID: {field_data.get("qase_id", "N/A")}')
        self.logger.log(f'[Fields]   ├─ Type: {field_data.get("type_id", "N/A")} ({self.mappings.custom_fields_type.get(field_data.get("type_id"), "Unknown")})')
        
        # Print field values
        if 'qase_values' in field_data and field_data['qase_values']:
            self.logger.log(f'[Fields]   ├─ Qase Values:')
            for qase_id, qase_title in field_data['qase_values'].items():
                self.logger.log(f'[Fields]   │  ├─ {qase_id}: "{qase_title}"')
        else:
            self.logger.log(f'[Fields]   ├─ Qase Values: None')
        
        # Print TestRail values from configs
        if 'configs' in field_data and field_data['configs']:
            config = field_data['configs'][0]
            if 'options' in config and 'items' in config['options']:
                items = config['options']['items']
                if items:
                    self.logger.log(f'[Fields]   ├─ TestRail Values:')
                    tr_values = {}
                    for line in items.split('\n'):
                        if ',' in line:
                            key, title = line.split(',', 1)
                            tr_values[key.strip()] = title.strip()
                    
                    for tr_key, tr_title in tr_values.items():
                        self.logger.log(f'[Fields]   │  ├─ {tr_key}: "{tr_title}"')
                else:
                    self.logger.log(f'[Fields]   ├─ TestRail Values: None')
            else:
                self.logger.log(f'[Fields]   ├─ TestRail Values: None')
        else:
            self.logger.log(f'[Fields]   ├─ TestRail Values: None')
        
        # Print mapping
        if 'tr_key_to_qase_id' in field_data and field_data['tr_key_to_qase_id']:
            self.logger.log(f'[Fields]   └─ Mapping (TestRail → Qase):')
            for tr_key, qase_id in field_data['tr_key_to_qase_id'].items():
                tr_title = "Unknown"
                qase_title = "Unknown"
                
                # Find TestRail title
                if 'configs' in field_data and field_data['configs']:
                    config = field_data['configs'][0]
                    if 'options' in config and 'items' in config['options']:
                        items = config['options']['items']
                        if items:
                            for line in items.split('\n'):
                                if ',' in line:
                                    key, title = line.split(',', 1)
                                    if key.strip() == tr_key:
                                        tr_title = title.strip()
                                        break
                
                # Find Qase title
                if 'qase_values' in field_data and field_data['qase_values']:
                    qase_title = field_data['qase_values'].get(qase_id, "Unknown")
                
                self.logger.log(f'[Fields]      ├─ {tr_key} ("{tr_title}") → {qase_id} ("{qase_title}")')
        else:
            self.logger.log(f'[Fields]   └─ Mapping: None')
        
        self.logger.log(f'[Fields]')
