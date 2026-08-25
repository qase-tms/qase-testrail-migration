import asyncio
import json
import re
import hashlib
import time

from ..service import QaseService, TestrailService
from ..support import Logger, Mappings, ConfigManager as Config, Pools, format_links_as_markdown, convert_testrail_date_to_iso, convert_estimate_time_to_hours, html_to_markdown

from qase.api_client_v1.models import TestStepCreate, TestCasebulkCasesInner
from .attachments import Attachments

from typing import List, Optional, Union

from urllib.parse import quote
from datetime import datetime

# Constant for maximum safe ID (int32)
MAX_SAFE_ID = 2**31 - 1  # 2,147,483,647


class Cases:
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
        self.attachments = Attachments(self.qase, self.testrail, self.logger, self.mappings, self.config, self.pools)
        self.total = 0
        self.logger.divider()

        self.project = None
        # Store JIRA links for attaching after case creation

    def import_cases(self, project: dict):
        return asyncio.run(self.import_cases_async(project))

    async def import_cases_async(self, project: dict):
        self.project = project
        # Reset JIRA links for this project

        async with asyncio.TaskGroup() as tg:
            if self.project['suite_mode'] in (2, 3):
                suites = await self.pools.tr(self.testrail.get_suites, self.project['testrail_id'])
                for suite in suites:
                    tg.create_task(self.import_cases_for_suite(suite['id']))
            else:
                tg.create_task(
                    self.import_cases_for_suite(None))  # Assuming None is a valid suite_id when suite_mode is not 3
        
        # Log statistics for ID mapping
        if self.mappings.case_id_mapping:
            self.logger.log(f'[{self.project["code"]}][Tests] Created {len(self.mappings.case_id_mapping)} ID mappings for cases')
            if self.config.get('cases.preserve_ids'):
                # If preserve_ids=true, save original IDs, but hash large ones
                large_ids = [orig_id for orig_id in self.mappings.case_id_mapping.keys() if orig_id > MAX_SAFE_ID]
                if large_ids:
                    self.logger.log(f'[{self.project["code"]}][Tests] {len(large_ids)} cases had large IDs that were hashed for safety (preserve_ids=true)')
                else:
                    self.logger.log(f'[{self.project["code"]}][Tests] All case IDs were preserved from TestRail (preserve_ids=true)')
            else:
                # If preserve_ids=false, all IDs were regenerated
                self.logger.log(f'[{self.project["code"]}][Tests] All case IDs were regenerated due to preserve_ids=false')
            
            # Check if all generated IDs are safe
            unsafe_ids = [qase_id for qase_id in self.mappings.case_id_mapping.values() if qase_id > MAX_SAFE_ID]
            if unsafe_ids:
                self.logger.log(f'[{self.project["code"]}][Tests] WARNING: {len(unsafe_ids)} generated IDs are still too large! This should not happen.')
            else:
                self.logger.log(f'[{self.project["code"]}][Tests] All generated IDs are within safe range (≤ {MAX_SAFE_ID})')
        else:
            self.logger.log(f'[{self.project["code"]}][Tests] No ID mappings created')
        
    async def import_cases_for_suite(self, suite_id):
        offset = 0
        # Set limit based on enterprise setting: 20 for enterprise, 100 for cloud
        limit = 20 if self.config.get('qase.dedicated_cluster') else 100
        while True:
            count = await self.process_cases(suite_id, offset, limit)
            if count < limit:
                break
            offset += limit

    async def process_cases(self, suite_id: int, offset: int, limit: int):
        try:
            if suite_id is None:
                suite_id = 0
            cases = await self.pools.tr(self.testrail.get_cases, self.project['testrail_id'], suite_id, limit, offset)
            self.mappings.stats.add_entity_count(self.project['code'], 'cases', 'testrail', cases['size'])
            if cases:
                self.logger.print_status('[' + self.project['code'] + '] Importing test cases', self.total,
                                         self.total + cases['size'], 1)
                self.logger.log(
                    f'[{self.project["code"]}][Tests] Importing {cases["size"]} cases from {offset} to {offset + limit} for suite {suite_id}')
                data = await self._prepare_cases(cases)
                prepared_count = len(data)
                if prepared_count != cases['size']:
                    self.logger.log(f'[{self.project["code"]}][Tests] Warning: Prepared {prepared_count} cases out of {cases["size"]} requested for suite {suite_id} (offset {offset})', 'warning')
                if data:
                    if self.config.get('qase.dedicated_cluster'):
                        time.sleep(5)  # To avoid hitting rate limits
                    status = await self.pools.qs(self.qase.create_cases, self.project['code'], data)
                    if status:
                        # Count actual cases created, not requested
                        self.mappings.stats.add_entity_count(self.project['code'], 'cases', 'qase', prepared_count)
                    else:
                        self.logger.log(f'[{self.project["code"]}][Tests] Failed to create {prepared_count} cases in Qase for suite {suite_id} (offset {offset})', 'error')
                else:
                    self.logger.log(f'[{self.project["code"]}][Tests] No cases prepared for suite {suite_id} (offset {offset}) - all cases may have failed', 'warning')
                self.total = self.total + cases['size']
                self.logger.print_status('[' + self.project['code'] + '] Importing test cases', self.total, self.total,
                                         1)
            return cases['size']
        except Exception as e:
            self.logger.log(f"[{self.project['code']}][Tests] Error processing cases for suite {suite_id}: {e}",
                            'error')
            return 0

    async def _prepare_cases(self, cases: List) -> List:
        result = []
        async with asyncio.TaskGroup() as tg:
            for case in cases['cases']:
                tg.create_task(self._prepare_case(case, result))

        return result

    async def _prepare_case(self, case, result):
        original_id = None
        safe_id = None
        try:
            original_id = case['id']
            
            # Check preserve_ids setting
            if self.config.get('cases.preserve_ids'):
                # preserve_ids enabled - save original IDs from TestRail
                if original_id <= MAX_SAFE_ID:  # ID fits in int32
                    # Save original ID
                    safe_id = int(original_id)
                    self.logger.log(f'[{self.project["code"]}][Tests] preserve_ids enabled, using original ID: {safe_id} for case {case["title"]}')
                else:
                    # ID too large, but preserve_ids=true, so hash it
                    hashed_id = int(hashlib.md5(str(original_id).encode()).hexdigest()[:8], 16)
                    safe_id = hashed_id % MAX_SAFE_ID  # Limit hash to safe range
                    self.logger.log(f'[{self.project["code"]}][Tests] preserve_ids enabled, original ID {original_id} too large, using hashed ID: {safe_id} for case {case["title"]}')
            else:
                # preserve_ids disabled - generate new IDs for all cases
                if original_id <= MAX_SAFE_ID:  # ID fits in int32
                    # Generate new ID even for small ones
                    import time
                    safe_id = int(time.time() * 1000) % MAX_SAFE_ID
                    self.logger.log(f'[{self.project["code"]}][Tests] preserve_ids disabled, generated new ID: {safe_id} for case {case["title"]} (original: {original_id})')
                else:
                    # ID too large, hash it
                    hashed_id = int(hashlib.md5(str(original_id).encode()).hexdigest()[:8], 16)
                    safe_id = hashed_id % MAX_SAFE_ID  # Limit hash to safe range
                    self.logger.log(f'[{self.project["code"]}][Tests] preserve_ids disabled, original ID {original_id} too large, using hashed ID: {safe_id} for case {case["title"]}')
            
            # Additional safety check - all IDs must fit in int32
            if safe_id > MAX_SAFE_ID:
                # If ID is still too large, force it to safe range
                safe_id = safe_id % MAX_SAFE_ID
                self.logger.log(f'[{self.project["code"]}][Tests] WARNING: Generated ID was still too large, forced to safe range: {safe_id}')
            
            data = {
                'id': safe_id,
                'title': case['title'],
                'created_at': str(datetime.fromtimestamp(case['created_on'])),
                'updated_at': str(datetime.fromtimestamp(case['updated_on'])),
                'author_id': self.mappings.get_user_id(case['created_by']),
                'steps': [],
                'attachments': [],
                'params': [],  # Must be empty array, not None
                'is_flaky': 0,
                'custom_field': {},
            }
            
            # Save original ID in custom field if preserve_ids is enabled
            if not self.config.get('cases.preserve_ids') and hasattr(self.mappings, 'testrail_original_id_field_id'):
                data['custom_field'][str(self.mappings.testrail_original_id_field_id)] = str(original_id)
                self.logger.log(f'[{self.project["code"]}][Tests] Stored original ID {original_id} in custom field for case {case["title"]}')

            # Process standard description field if it exists
            if case.get('description'):
                description = self.attachments.check_and_replace_attachments(case['description'], self.project['code'])
                description = html_to_markdown(description, remove_html=False)
                description = format_links_as_markdown(description, self.project['code'], self.config)
                data['description'] = description
                self.logger.log(f'[{self.project["code"]}][Tests] Processed description field for case {case["title"]}')
            
            # import custom fields
            data = self._import_custom_fields_for_case(case=case, data=data)
            data = await self._get_attachments_for_case(case=case, data=data)

            data = self._set_priority(case=case, data=data)
            data = self._set_type(case=case, data=data)
            data = self._set_status(case=case, data=data)
            data = self._set_suite(case=case, data=data)
            data = self._set_refs(case=case, data=data)
            data = self._set_milestone(case=case, data=data, code=self.project['code'])
            data = self._set_estimate(case=case, data=data)
            data = self._set_assigned_to(case=case, data=data)
            
            # Check if this case has shared steps (dicts with 'shared' key in steps)
            has_shared_steps = False
            if 'steps' in data and data['steps']:
                for step in data['steps']:
                    if isinstance(step, dict) and 'shared' in step:
                        has_shared_steps = True
                        break

            # If case has shared steps, append as dict (don't create TestCasebulkCasesInner)
            # because Pydantic will try to convert shared step dicts to TestStepCreate objects
            if has_shared_steps:
                result.append(data)
                # Save mapping only after successful preparation
                if original_id and safe_id:
                    self.mappings.add_case_id_mapping(original_id, safe_id)
                    self.logger.log(f'[{self.project["code"]}][Tests] Created ID mapping: TestRail {original_id} -> Qase {safe_id}')
                self.logger.log(f"Prepared test with shared steps (as dict): {data['title']} - {str(data.get('suite_id', 'N/A'))}")
            else:
                # No shared steps - safe to create TestCasebulkCasesInner object
                try:
                    # Ensure params and parameters are always arrays, not None (Pydantic might default to None)
                    # Pydantic models serialize None as null in JSON, but Qase API requires empty arrays
                    if 'params' not in data or data.get('params') is None:
                        data['params'] = []
                    if 'parameters' not in data or data.get('parameters') is None:
                        data['parameters'] = []
                    result.append(
                        TestCasebulkCasesInner(
                            **data
                        )
                    )
                    # Save mapping only after successful preparation
                    if original_id and safe_id:
                        self.mappings.add_case_id_mapping(original_id, safe_id)
                        self.logger.log(f'[{self.project["code"]}][Tests] Created ID mapping: TestRail {original_id} -> Qase {safe_id}')
                    self.logger.log("Prepared test: " + data['title'] + " - " + str(data['suite_id']))
                except Exception as validation_error:
                    # If validation fails, try to add as dict anyway (for cases with complex data)
                    self.logger.log(f'[{self.project["code"]}][Tests] Validation failed for case {data["title"]}, adding as dict: {validation_error}', 'warning')
                    result.append(data)
                    # Save mapping only after successful preparation
                    if original_id and safe_id:
                        self.mappings.add_case_id_mapping(original_id, safe_id)
                        self.logger.log(f'[{self.project["code"]}][Tests] Created ID mapping: TestRail {original_id} -> Qase {safe_id}')
                    self.logger.log(f"Prepared test (as dict due to validation): {data['title']} - {str(data.get('suite_id', 'N/A'))}")
        except Exception as e:
            import traceback
            import json
            self.logger.log(f'[{self.project["code"]}][Tests] Failed to prepare case {case.get("title", "Unknown")} (ID: {case.get("id", "Unknown")}): {e}', 'error')
            self.logger.log(f'[{self.project["code"]}][Tests] Traceback: {traceback.format_exc()}', 'error')
            # Log the data that was prepared up to the point of failure
            try:
                if 'data' in locals() and data:
                    payload_dict = data.copy()
                    # Convert TestStepCreate objects to dicts for logging
                    if 'steps' in payload_dict and payload_dict['steps']:
                        steps_list = []
                        for step in payload_dict['steps']:
                            if hasattr(step, 'to_dict'):
                                steps_list.append(step.to_dict())
                            elif isinstance(step, dict):
                                steps_list.append(step)
                            else:
                                steps_list.append(str(step))
                        payload_dict['steps'] = steps_list
                    self.logger.log(f'[{self.project["code"]}][Tests] PAYLOAD (failed preparation) for case "{case.get("title", "Unknown")}": {json.dumps(payload_dict, indent=2, default=str)}')
            except Exception as log_error:
                self.logger.log(f'[{self.project["code"]}][Tests] Could not log payload: {log_error}', 'error')
            # Try to log case data if available
            try:
                self.logger.log(f'[{self.project["code"]}][Tests] Case ID: {case.get("id")}, Title: {case.get("title")}', 'error')
            except:
                pass

    def _set_refs(self, case: dict, data: dict) -> dict:
        if not (self.mappings.refs_id and case.get('refs') and self.config.get('testrail.refs.enable')):
            return data

        refs = [ref.strip() for ref in case['refs'].split(',')]
        url = (self.config.get('testrail.refs.url') or '').rstrip('/')

        processed_refs = [self._get_ref(ref, url) for ref in refs]
        data['custom_field'][str(self.mappings.refs_id)] = '\n'.join(processed_refs)
        
        return data

    @staticmethod
    def _get_ref(ref: str, url: str) -> str:
        # Already a full URL, link it as-is
        if ref.startswith('http'):
            return f"[{ref}]({quote(ref, safe='/:')})"
        # No issue tracker URL configured: keep the reference as plain text
        # rather than inventing a link that goes nowhere. The
        # link_jira_issues helper reads issue keys out of this field either
        # way, so nothing downstream depends on the markdown.
        if not url:
            return ref
        return f"[{ref}]({quote(f'{url}/{ref}', safe='/:')})"
    
    def _collect_attachment_hashes_from_text_fields(self, case: dict, data: dict) -> set:
        """
        Collect attachment hashes from all text fields that may contain inline attachment references.
        This ensures attachments referenced in markdown (description, preconditions, steps, etc.)
        are registered in Qase so case creation doesn't fail with "attachment not found" errors.
        
        We check the original case data (before markdown replacement) to extract attachment IDs,
        then look up their hashes from the attachments_map.
        
        Returns a set of unique attachment hashes.
        """
        attachment_hashes = set()
        
        # Collect from custom fields
        for field_name in case:
            if field_name.startswith('custom_'):
                field_value = case[field_name]
                if field_value:
                    attachment_ids = self.attachments.check_attachments(str(field_value))
                    for attachment_id in attachment_ids:
                        if attachment_id in self.mappings.attachments_map:
                            attachment_hashes.add(self.mappings.attachments_map[attachment_id]['hash'])
        
        # Collect from BDD scenario steps
        if 'custom_testrail_bdd_scenario' in case and case['custom_testrail_bdd_scenario']:
            try:
                parsed_data = json.loads(case['custom_testrail_bdd_scenario'])
                for step in parsed_data:
                    if 'content' in step and step['content']:
                        attachment_ids = self.attachments.check_attachments(str(step['content']))
                        for attachment_id in attachment_ids:
                            if attachment_id in self.mappings.attachments_map:
                                attachment_hashes.add(self.mappings.attachments_map[attachment_id]['hash'])
            except Exception:
                pass  # Invalid JSON, skip
        
        # Collect from step fields
        for field_name in case:
            normalized_name = field_name[len('custom_'):] if field_name.startswith('custom_') else field_name
            # Check base name (without project suffix) for project-specific step fields
            base_name = normalized_name
            if normalized_name.endswith(f"_{self.project['code']}"):
                base_name = normalized_name[:-len(f"_{self.project['code']}")]
            if field_name.startswith('custom_') and base_name in self.mappings.step_fields and case[field_name]:
                for step in case[field_name]:
                    if 'content' in step and step['content']:
                        attachment_ids = self.attachments.check_attachments(str(step['content']))
                        for attachment_id in attachment_ids:
                            if attachment_id in self.mappings.attachments_map:
                                attachment_hashes.add(self.mappings.attachments_map[attachment_id]['hash'])
                    
                    if 'expected' in step and step['expected']:
                        attachment_ids = self.attachments.check_attachments(str(step['expected']))
                        for attachment_id in attachment_ids:
                            if attachment_id in self.mappings.attachments_map:
                                attachment_hashes.add(self.mappings.attachments_map[attachment_id]['hash'])
                    
                    if 'additional_info' in step and step['additional_info']:
                        attachment_ids = self.attachments.check_attachments(str(step['additional_info']))
                        for attachment_id in attachment_ids:
                            if attachment_id in self.mappings.attachments_map:
                                attachment_hashes.add(self.mappings.attachments_map[attachment_id]['hash'])
        
        return attachment_hashes

    def _collect_attachment_ids_from_text_fields(self, case: dict, data: dict) -> set:
        """
        Collect attachment IDs from all text fields that may contain inline attachment references.
        Returns a set of attachment IDs found in text fields.
        """
        attachment_ids = set()
        
        def extract_from_text(text: str) -> None:
            """Helper to extract attachment IDs from text and add to set."""
            if text:
                attachment_ids.update(self.attachments.check_attachments(str(text)))
        
        # Collect from custom fields
        for field_name, field_value in case.items():
            if field_name.startswith('custom_'):
                extract_from_text(field_value)
        
        # Collect from BDD scenario steps
        bdd_field = case.get('custom_testrail_bdd_scenario')
        if bdd_field:
            try:
                for step in json.loads(bdd_field):
                    extract_from_text(step.get('content'))
            except (json.JSONDecodeError, TypeError):
                pass  # Invalid JSON, skip
        
        # Collect from step fields
        for field_name, field_value in case.items():
            if (field_name.startswith('custom_') and 
                field_name[7:] in self.mappings.step_fields and 
                field_value):
                # Handle both JSON string format (type_id 13) and array format (type_id 10)
                step_data = field_value
                if isinstance(step_data, str):
                    try:
                        parsed_data = json.loads(step_data)
                        if not isinstance(parsed_data, list):
                            parsed_data = [parsed_data]
                        step_data = parsed_data
                    except (json.JSONDecodeError, TypeError):
                        continue  # Invalid JSON, skip
                elif not isinstance(step_data, list):
                    continue  # Unexpected format, skip
                
                for step in step_data:
                    if isinstance(step, dict):
                        for step_field in ('content', 'expected', 'additional_info'):
                            extract_from_text(step.get(step_field))
        
        return attachment_ids

    async def _get_attachments_for_case(self, case: dict, data: dict) -> dict:
        self.logger.log(f'[{self.project["code"]}][Tests] Getting attachments for case {case["title"]}')
        try:
            attachments = await self.pools.tr(self.testrail.get_attachments_case, case['id'])
        except Exception as e:
            self.logger.log(f'[{self.project["code"]}][Tests] Failed to get attachments for case {case["title"]}: {e}',
                            'error')
            return data
        self.logger.log(
            f'[{self.project["code"]}][Tests] Found {len(attachments["attachments"])} case-level attachments for case {case["title"]}')
        
        inline_attachment_ids = self._collect_attachment_ids_from_text_fields(case, data)
        inline_attachment_ids = {str(id) for id in inline_attachment_ids}
        self.logger.log(f'[{self.project["code"]}][Tests] Found {len(inline_attachment_ids)} inline attachment IDs in text fields: {inline_attachment_ids}')
        
        case_level_attachment_ids = set()
        for attachment in attachments['attachments']:
            try:
                attachment_id = attachment.get('data_id') or attachment.get('id')
                if attachment_id is None:
                    self.logger.log(f'[{self.project["code"]}][Tests] Warning: Attachment has no id or data_id: {attachment}', 'warning')
                    continue
                
                attachment_id_str = str(attachment_id)
                case_level_attachment_ids.add(attachment_id_str)
                
                if attachment_id_str in inline_attachment_ids:
                    self.logger.log(f'[{self.project["code"]}][Tests] Skipped case-level attachment {attachment_id_str} for case {case["title"]} - already referenced in text fields')
                    continue
                attachment_key = attachment_id_str if attachment_id_str in self.mappings.attachments_map else (attachment_id if attachment_id in self.mappings.attachments_map else None)
                if attachment_key:
                    data['attachments'].append(self.mappings.attachments_map[attachment_key]['hash'])
                    self.logger.log(f'[{self.project["code"]}][Tests] Added case-level attachment {attachment_id_str} (hash: {self.mappings.attachments_map[attachment_key]["hash"]}) to case {case["title"]}')
                else:
                    self.logger.log(f'[{self.project["code"]}][Tests] Warning: Case-level attachment {attachment_id_str} not found in attachments_map for case {case["title"]}', 'warning')
            except Exception as e:
                self.logger.log(
                    f'[{self.project["code"]}][Tests] Failed to get attachment for case {case["title"]}: {e}', 'error')
        
        inline_only_ids = inline_attachment_ids - case_level_attachment_ids
        if inline_only_ids:
            self.logger.log(f'[{self.project["code"]}][Tests] Found {len(inline_only_ids)} inline-only attachments for case {case["title"]} (not added to case-level attachments): {inline_only_ids}')
        
        both_level_ids = inline_attachment_ids & case_level_attachment_ids
        if both_level_ids:
            self.logger.log(f'[{self.project["code"]}][Tests] Found {len(both_level_ids)} attachments that are both case-level and inline for case {case["title"]}: {both_level_ids}')
        
        return data

    # Done
    def _import_custom_fields_for_case(self, case: dict, data: dict) -> dict:
        for field_name in case:
            if field_name.startswith('custom_'):
                normalized_name = self.__normalize_custom_field_name(field_name[len('custom_'):])
                
                # Check if this is a step field FIRST (before checking custom_fields mappings)
                # Step fields should be processed even if they're not in custom_fields mappings
                base_name = normalized_name
                if normalized_name.endswith(f"_{self.project['code']}"):
                    base_name = normalized_name[:-len(f"_{self.project['code']}")]
                
                # If it's a step field, process it immediately and skip custom field processing
                # Check if field exists and has data (not None, not empty string, not empty list)
                has_step_data = case[field_name] is not None and (
                    (isinstance(case[field_name], list) and len(case[field_name]) > 0) or
                    (isinstance(case[field_name], str) and len(case[field_name].strip()) > 0)
                )
                is_step_field = base_name in self.mappings.step_fields and has_step_data
                
                if is_step_field:
                    # Process step fields (handle project-specific variants)
                    steps = []
                    i = 1
                    
                    # Detect format: if it's a string, parse as JSON; if it's already a list, use directly
                    step_data = case[field_name]
                    if isinstance(step_data, str):
                        # JSON string format (e.g., type_id 13 BDD scenario fields)
                        try:
                            parsed_data = json.loads(step_data)
                            if not isinstance(parsed_data, list):
                                parsed_data = [parsed_data]
                            step_data = parsed_data
                        except Exception as e:
                            self.logger.log(
                                f'[{self.project["code"]}][Tests] Case {case["title"]} has invalid JSON in step field {field_name}: {e}',
                                'warning')
                            continue
                    elif not isinstance(step_data, list):
                        # Unexpected format
                        self.logger.log(
                            f'[{self.project["code"]}][Tests] Case {case["title"]} has unexpected format for step field {field_name}: {type(step_data)}',
                            'warning')
                        continue
                    
                    # Process steps (now guaranteed to be a list)
                    for step in step_data:
                        # Check if this step references a shared step
                        if 'shared_step_id' in step and step['shared_step_id']:
                            shared_step_id = step['shared_step_id']
                            # Look up the Qase shared step hash from mappings
                            project_shared_steps = self.mappings.shared_steps.get(self.project['code'], {})
                            qase_shared_step_hash = project_shared_steps.get(shared_step_id)
                            
                            if qase_shared_step_hash:
                                # Create a shared step reference as a dict (Qase API format)
                                steps.append({
                                    'shared': qase_shared_step_hash
                                })
                                self.logger.log(f'[{self.project["code"]}][Tests] Case {case["title"]} step {i} references shared step TestRail ID {shared_step_id} -> Qase hash {qase_shared_step_hash}')
                                i += 1
                            else:
                                # Shared step not found in mappings, log warning and process as regular step
                                self.logger.log(f'[{self.project["code"]}][Tests] Case {case["title"]} step {i} references shared step TestRail ID {shared_step_id} but mapping not found. Processing as regular step.', 'warning')
                                # Fall through to regular step processing below
                                action = self.attachments.check_and_replace_attachments(step.get('content', ''), self.project['code'])
                                expected = self.attachments.check_and_replace_attachments(step.get('expected', ''), self.project['code'])
                                input_data = self.attachments.check_and_replace_attachments(step.get('additional_info', ''),
                                                                                            self.project['code'])
                                
                                # Convert HTML to markdown for all step fields
                                action = html_to_markdown(action, remove_html=False) if action else action
                                expected = html_to_markdown(expected, remove_html=False) if expected else expected
                                input_data = html_to_markdown(input_data, remove_html=False) if input_data else input_data

                                action = action.strip()
                                expected = expected.strip()
                                input_data = input_data.strip()

                                if (action != '' or (action == '' and expected != '')):
                                    if action == '' or action == ' ':
                                        action = 'No action'
                                    steps.append(
                                        TestStepCreate(
                                            action=format_links_as_markdown(action, self.project['code'], self.config),
                                            expected_result=format_links_as_markdown(expected, self.project['code'], self.config),
                                            data=format_links_as_markdown(input_data, self.project['code'], self.config),
                                            position=i,
                                            attachments=[]  # Always use empty array, not None
                                        )
                                    )
                                    i += 1
                        else:
                            # Regular step processing (no shared_step_id)
                            # Process step fields: replace attachments, convert HTML to markdown, format links
                            action = self.attachments.check_and_replace_attachments(step.get('content', ''), self.project['code'])
                            expected = self.attachments.check_and_replace_attachments(step.get('expected', ''), self.project['code'])
                            input_data = self.attachments.check_and_replace_attachments(step.get('additional_info', ''),
                                                                                        self.project['code'])
                            
                            # Convert HTML to markdown for all step fields
                            action = html_to_markdown(action, remove_html=False) if action else action
                            expected = html_to_markdown(expected, remove_html=False) if expected else expected
                            input_data = html_to_markdown(input_data, remove_html=False) if input_data else input_data

                            action = action.strip()
                            expected = expected.strip()
                            input_data = input_data.strip()

                            # Handle both formats: steps with only 'content' (JSON string format) and full step objects (array format)
                            if (action != '' or (action == '' and expected != '')):
                                if action == '' or action == ' ':
                                    action = 'No action'
                                steps.append(
                                    TestStepCreate(
                                        action=format_links_as_markdown(action, self.project['code'], self.config),
                                        expected_result=format_links_as_markdown(expected, self.project['code'], self.config) if expected else None,
                                        data=format_links_as_markdown(input_data, self.project['code'], self.config) if input_data else None,
                                        position=i,
                                        attachments=[]  # Always use empty array, not None
                                    )
                                )
                                i += 1
                            else:
                                self.logger.log(f'[{self.project["code"]}][Tests] Case {case["title"]} has invalid step {step}',
                                                'warning')
                    if steps:
                        data['steps'] = steps
                    continue  # Skip custom field processing for step fields
                
                # Look for project-specific field first (skip if it's a step field)
                project_specific_key = f"{normalized_name}_{self.project['code']}"
                if project_specific_key in self.mappings.custom_fields and case[field_name]:
                    custom_field = self.mappings.custom_fields[project_specific_key]
                    self.logger.log(f'[{self.project["code"]}][Tests] Using project-specific field {project_specific_key} for case {case["title"]} with value: {case[field_name]}')
                    self.logger.log(f'[{self.project["code"]}][Tests] Field type: {custom_field["type_id"]} (6=selectbox, 12=multiselect)')
                    self.logger.log(f'[{self.project["code"]}][Tests] Field qase_id: {custom_field["qase_id"]}')
                    self.logger.log(f'[{self.project["code"]}][Tests] Field name: {custom_field["name"]}')
                    
                    # Importing step

                    if custom_field['type_id'] in (6, 12):
                        # Importing dropdown and multiselect values
                        value = self._validate_custom_field_values(custom_field, case[field_name])
                        if value:
                            if type(value) == str or type(value) == int:
                                # Single value - use proper mapping if available
                                if custom_field.get('tr_key_to_qase_id') and str(value) in custom_field['tr_key_to_qase_id']:
                                    qase_id = custom_field['tr_key_to_qase_id'][str(value)]
                                    data['custom_field'][str(custom_field['qase_id'])] = str(qase_id)
                                    self.logger.log(f'[{self.project["code"]}][Tests] Set field {custom_field["name"]} using mapping {value} -> {qase_id}')
                                else:
                                    data['custom_field'][str(custom_field['qase_id'])] = str(value)
                                    self.logger.log(f'[{self.project["code"]}][Tests] Set field {custom_field["name"]} to value: {str(value)}')
                            elif type(value) == list:
                                if custom_field['type_id'] == 12:
                                    if not custom_field.get('project_id'):
                                        validated_values = self._validate_custom_field_values(custom_field, value)
                                        if validated_values:
                                            # Convert validated TestRail values to Qase IDs
                                            qase_values = []
                                            for v in validated_values:
                                                # Find the corresponding Qase ID for this TestRail value
                                                testrail_key = str(v)
                                                if custom_field.get('tr_key_to_qase_id') and testrail_key in custom_field['tr_key_to_qase_id']:
                                                    qase_id = custom_field['tr_key_to_qase_id'][testrail_key]
                                                    qase_values.append(str(qase_id))
                                                elif custom_field.get('qase_values') and testrail_key in custom_field['qase_values']:
                                                    # Fallback to old logic if tr_key_to_qase_id not available
                                                    qase_id = custom_field['qase_values'][testrail_key]
                                                    qase_values.append(str(qase_id))
                                                else:
                                                    self.logger.log(f'[{self.project["code"]}][Tests] Warning: TestRail value {v} not found in mapping for field {custom_field["name"]}', 'warning')
                                            
                                            if qase_values:
                                                data['custom_field'][str(custom_field['qase_id'])] = ','.join(qase_values)
                                                self.logger.log(f'[{self.project["code"]}][Tests] Set global multiselect field {custom_field["name"]} to values: {",".join(qase_values)}')
                                            else:
                                                self.logger.log(f'[{self.project["code"]}][Tests] No valid Qase IDs found for field {custom_field["name"]}', 'warning')
                                        else:
                                            self.logger.log(f'[{self.project["code"]}][Tests] Global field {custom_field["name"]} validation failed for value: {value}')
                                    else:
                                        # For project-specific fields, use proper mapping
                                        qase_values = []
                                        for v in value:
                                            testrail_key = str(v)
                                            if custom_field.get('tr_key_to_qase_id') and testrail_key in custom_field['tr_key_to_qase_id']:
                                                qase_id = custom_field['tr_key_to_qase_id'][testrail_key]
                                                qase_values.append(str(qase_id))
                                            else:
                                                # Fallback - use value directly without +1 offset
                                                qase_values.append(str(v))
                                        data['custom_field'][str(custom_field['qase_id'])] = ','.join(qase_values)
                                        self.logger.log(f'[{self.project["code"]}][Tests] Set project-specific multiselect field {custom_field["name"]} to values: {",".join(qase_values)}')
                                else:  # single select (type_id = 6)
                                    # For single select, take first value only
                                    if custom_field.get('tr_key_to_qase_id') and str(value[0]) in custom_field['tr_key_to_qase_id']:
                                        qase_id = custom_field['tr_key_to_qase_id'][str(value[0])]
                                        data['custom_field'][str(custom_field['qase_id'])] = str(qase_id)
                                        self.logger.log(f'[{self.project["code"]}][Tests] Set single select field {custom_field["name"]} using mapping {value[0]} -> {qase_id}')
                                    else:
                                        # Fallback - use value directly without +1 offset
                                        data['custom_field'][str(custom_field['qase_id'])] = str(value[0])
                                        self.logger.log(f'[{self.project["code"]}][Tests] Set single select field {custom_field["name"]} to value: {str(value[0])}')
                    elif custom_field['type_id'] == 8:
                        field_value = str(case[field_name])
                        converted_date = convert_testrail_date_to_iso(field_value)
                        data['custom_field'][str(custom_field['qase_id'])] = converted_date
                        self.logger.log(f'[{self.project["code"]}][Tests] Set datepicker field "{custom_field["name"]}" to converted date: "{converted_date}" (original: "{field_value}")')
                    else:
                        field_value = str(self.attachments.check_and_replace_attachments(case[field_name], self.project['code']))
                        field_value = html_to_markdown(field_value, remove_html=False)
                        
                        # Don't format links as markdown for URL fields - they should remain plain URLs
                        # Check if this is a URL field by checking the Qase field type mapping
                        is_url_field = (custom_field.get('type_id') and 
                                       custom_field['type_id'] in self.mappings.custom_fields_type and
                                       self.mappings.custom_fields_type[custom_field['type_id']] == 7)  # 7 is Qase URL type
                        if not is_url_field:
                            field_value = format_links_as_markdown(field_value, self.project['code'], self.config)
                        
                        if normalized_name == 'preconds':
                            data['preconditions'] = field_value
                            self.logger.log(f'[{self.project["code"]}][Tests] Set preconds field value to preconditions system field (skipped custom field)')
                        else:
                            data['custom_field'][str(custom_field['qase_id'])] = field_value
                            self.logger.log(f'[{self.project["code"]}][Tests] Set field "{custom_field["name"]}" to value: "{field_value}"')
                            
                # Fallback to original field name for backward compatibility (skip if it's a step field)
                elif not is_step_field and normalized_name in self.mappings.custom_fields and case[field_name]:
                    custom_field = self.mappings.custom_fields[normalized_name]
                    self.logger.log(f'[{self.project["code"]}][Tests] Using global field {normalized_name} for case {case["title"]} with value: {case[field_name]}')
                    self.logger.log(f'[{self.project["code"]}][Tests] Field type: {custom_field["type_id"]} (6=selectbox, 12=multiselect)')
                    self.logger.log(f'[{self.project["code"]}][Tests] Field qase_id: {custom_field["qase_id"]}')
                    self.logger.log(f'[{self.project["code"]}][Tests] Field name: {custom_field["name"]}')
                    # Importing step

                    if custom_field['type_id'] in (6, 12):
                        # Importing dropdown and multiselect values
                        value = self._validate_custom_field_values(custom_field, case[field_name])
                        if value:
                            if type(value) == str or type(value) == int:
                                # Single value - use proper mapping if available
                                if custom_field.get('tr_key_to_qase_id') and str(value) in custom_field['tr_key_to_qase_id']:
                                    qase_id = custom_field['tr_key_to_qase_id'][str(value)]
                                    data['custom_field'][str(custom_field['qase_id'])] = str(qase_id)
                                    self.logger.log(f'[{self.project["code"]}][Tests] Set global field {custom_field["name"]} using mapping {value} -> {qase_id}')
                                else:
                                    # Fallback - use value directly without +1 offset
                                    data['custom_field'][str(custom_field['qase_id'])] = str(value)
                                    self.logger.log(f'[{self.project["code"]}][Tests] Set global field {custom_field["name"]} to value: {str(value)}')
                            elif type(value) == list:
                                # Multiple values - handle based on field type
                                if custom_field['type_id'] == 12:  # multiselect
                                    # For multiselect, pass comma-separated string
                                    if not custom_field.get('project_id'):
                                        # For global fields, use validated values directly
                                        validated_values = self._validate_custom_field_values(custom_field, value)
                                        if validated_values:
                                            # Convert validated TestRail values to Qase IDs
                                            qase_values = []
                                            for v in validated_values:
                                                # Find the corresponding Qase ID for this TestRail value
                                                testrail_key = str(v)
                                                if custom_field.get('tr_key_to_qase_id') and testrail_key in custom_field['tr_key_to_qase_id']:
                                                    qase_id = custom_field['tr_key_to_qase_id'][testrail_key]
                                                    qase_values.append(str(qase_id))
                                                elif custom_field.get('qase_values') and testrail_key in custom_field['qase_values']:
                                                    # Fallback to old logic if tr_key_to_qase_id not available
                                                    qase_id = custom_field['qase_values'][testrail_key]
                                                    qase_values.append(str(qase_id))
                                                else:
                                                    self.logger.log(f'[{self.project["code"]}][Tests] Warning: TestRail value {v} not found in mapping for field {custom_field["name"]}', 'warning')
                                            
                                            if qase_values:
                                                data['custom_field'][str(custom_field['qase_id'])] = ','.join(qase_values)
                                                self.logger.log(f'[{self.project["code"]}][Tests] Set global multiselect field {custom_field["name"]} to values: {",".join(qase_values)}')
                                            else:
                                                self.logger.log(f'[{self.project["code"]}][Tests] No valid Qase IDs found for field {custom_field["name"]}', 'warning')
                                        else:
                                            self.logger.log(f'[{self.project["code"]}][Tests] Global field {custom_field["name"]} validation failed for value: {value}')
                                    else:
                                        # For project-specific fields, use proper mapping
                                        qase_values = []
                                        for v in value:
                                            testrail_key = str(v)
                                            if custom_field.get('tr_key_to_qase_id') and testrail_key in custom_field['tr_key_to_qase_id']:
                                                qase_id = custom_field['tr_key_to_qase_id'][testrail_key]
                                                qase_values.append(str(qase_id))
                                            else:
                                                # Fallback - use value directly without +1 offset
                                                qase_values.append(str(v))
                                        data['custom_field'][str(custom_field['qase_id'])] = ','.join(qase_values)
                                        self.logger.log(f'[{self.project["code"]}][Tests] Set project-specific multiselect field {custom_field["name"]} to values: {",".join(qase_values)}')
                                else:  # single select (type_id = 6)
                                    # For single select, take first value only
                                    if custom_field.get('tr_key_to_qase_id') and str(value[0]) in custom_field['tr_key_to_qase_id']:
                                        qase_id = custom_field['tr_key_to_qase_id'][str(value[0])]
                                        data['custom_field'][str(custom_field['qase_id'])] = str(qase_id)
                                        self.logger.log(f'[{self.project["code"]}][Tests] Set single select field {custom_field["name"]} using mapping {value[0]} -> {qase_id}')
                                    else:
                                        # Fallback - use value directly without +1 offset
                                        data['custom_field'][str(custom_field['qase_id'])] = str(value[0])
                                        self.logger.log(f'[{self.project["code"]}][Tests] Set single select field {custom_field["name"]} to value: {str(value[0])}')
                        else:
                            self.logger.log(f'[{self.project["code"]}][Tests] Global field {custom_field["name"]} validation failed for value: {value}')
                            return data
                    elif custom_field['type_id'] == 8:
                        # Handle datepicker fields (type 8) - convert TestRail date format to ISO format
                        field_value = str(case[field_name])
                        converted_date = convert_testrail_date_to_iso(field_value)
                        data['custom_field'][str(custom_field['qase_id'])] = converted_date
                        self.logger.log(f'[{self.project["code"]}][Tests] Set global datepicker field "{custom_field["name"]}" to converted date: "{converted_date}" (original: "{field_value}")')
                    else:
                        # Handle non-dropdown fields (text, number, etc.)
                        field_value = str(self.attachments.check_and_replace_attachments(case[field_name], self.project['code']))
                        field_value = html_to_markdown(field_value, remove_html=False)
                        
                        # Don't format links as markdown for URL fields - they should remain plain URLs
                        # Check if this is a URL field by checking the Qase field type mapping
                        is_url_field = (custom_field.get('type_id') and 
                                       custom_field['type_id'] in self.mappings.custom_fields_type and
                                       self.mappings.custom_fields_type[custom_field['type_id']] == 7)  # 7 is Qase URL type
                        if not is_url_field:
                            field_value = format_links_as_markdown(field_value, self.project['code'], self.config)
                        
                        # Special handling for preconds field - only set preconditions system field, skip custom field
                        if normalized_name == 'preconds':
                            data['preconditions'] = field_value
                            self.logger.log(f'[{self.project["code"]}][Tests] Set preconds field value to preconditions system field (skipped custom field)')
                        else:
                            data['custom_field'][str(custom_field['qase_id'])] = field_value
                            self.logger.log(f'[{self.project["code"]}][Tests] Set global field {custom_field["name"]} to text value')
                else:
                    # Field not found in custom_fields mappings
                    self.logger.log(f'[{self.project["code"]}][Tests] No field found for {normalized_name} or {project_specific_key}')
        return data

    # Done. Method validates if custom field value exists (skip)
    def _validate_custom_field_values(self, custom_field: dict, value: Union[str, List]) -> Optional[Union[str, list]]:
        """Validate custom field values against field configuration"""
        if not value:
            return None

        # For project-specific fields, use the field's own config
        if custom_field.get('project_id') and custom_field.get('project_code'):
            configs = custom_field['configs']
            self.logger.log(f'[{self.project["code"]}][Tests] Using project-specific config for field {custom_field["name"]}')
        else:
            # For global fields, find config for current project
            configs = custom_field['configs']
            project_id = self.project['testrail_id']
            matching_config = None
            
            for config in configs:
                if config['context'].get('project_ids') and project_id in config['context']['project_ids']:
                    matching_config = config
                    break
            
            if matching_config:
                configs = [matching_config]
                self.logger.log(f'[{self.project["code"]}][Tests] Using project-specific config for global field {custom_field["name"]}')
            else:
                # Use first config for global fields
                configs = [configs[0]]
                self.logger.log(f'[{self.project["code"]}][Tests] Using first config for field {custom_field["name"]}')

        if not configs:
            self.logger.log(f'[{self.project["code"]}][Tests] No configs found for field {custom_field["name"]}', 'warning')
            return None

        config = configs[0]
        items = config['options'].get('items', '')
        
        if not items:
            self.logger.log(f'[{self.project["code"]}][Tests] No items found in config for field {custom_field["name"]}', 'warning')
            return None

        # Parse items string into values dict
        values = {}
        for line in items.split('\n'):
            if ',' in line:
                key, title = line.split(',', 1)
                values[key.strip()] = title.strip()

        self.logger.log(f'[{self.project["code"]}][Tests] Field {custom_field["name"]} has {len(values)} valid values: {values}')

        if isinstance(value, list):
            filtered_values = []
            
            for item in value:
                if str(item) in values.keys():
                    filtered_values.append(item)
                else:
                    self.logger.log(
                        f'[{self.project["code"]}][Tests] Custom field {custom_field["name"]} has invalid value {item} (not in {list(values.keys())})',
                        'warning')

            if filtered_values:
                return filtered_values
            else:
                self.logger.log(f'[{self.project["code"]}][Tests] No valid values found for field {custom_field["name"]}', 'warning')
                return None
        else:
            # Single value
            if str(value) in values.keys():
                return [value]
            else:
                self.logger.log(
                    f'[{self.project["code"]}][Tests] Custom field {custom_field["name"]} has invalid value {value} (not in {list(values.keys())})',
                    'warning')
                return None

    def __split_values(self, string: str, delimiter: str = ',') -> dict:
        items = string.split('\n')  # split items into a list
        result = {}
        for item in items:
            if item != '' and item != None:
                key, value = item.split(delimiter)
                result[key] = value
        return result

    # Done
    def _set_priority(self, case: dict, data: dict) -> dict:
        data['priority'] = self.mappings.priorities[case['priority_id']] if case[
                                                                                'priority_id'] in self.mappings.priorities else self.mappings.default_priority
        return data

    # Done
    def _set_type(self, case: dict, data: dict) -> dict:
        data['type'] = self.mappings.types[case['type_id']] if case['type_id'] in self.mappings.types else 1
        return data

    def _set_status(self, case: dict, data: dict) -> dict:
        # Not used yet, as testrail doesn't return case statuses
        return data
        data['status'] = self.mappings.case_statuses[case['status_id']] if case[
                                                                               'status_id'] in self.mappings.case_statuses else 1
        return data

    # Done
    def _set_suite(self, case: dict, data: dict) -> dict:
        suite_id = self._get_suite_id(section_id=case['section_id'])
        if (suite_id):
            data['suite_id'] = suite_id
        return data

    # Done
    def _get_suite_id(self, section_id: Optional[int] = None) -> int:
        if (section_id and section_id in self.mappings.suites[self.project['code']]):
            return self.mappings.suites[self.project['code']][section_id]
        return None

    def _set_milestone(self, case: dict, data: dict, code: str) -> dict:
        if case['milestone_id'] and code in self.mappings.milestones and case['milestone_id'] in \
                self.mappings.milestones[code]:
            data['milestone_id'] = self.mappings.milestones[code][case['milestone_id']]
        return data

    def _set_estimate(self, case: dict, data: dict) -> dict:
        """Set estimate field with converted time value"""
        if hasattr(self.mappings, 'estimate_field_id') and self.mappings.estimate_field_id:
            # Check if case has estimate field
            if 'estimate' in case and case['estimate']:
                # Convert estimate time to hours
                converted_estimate = convert_estimate_time_to_hours(case['estimate'])
                data['custom_field'][str(self.mappings.estimate_field_id)] = converted_estimate
                self.logger.log(f'[{self.project["code"]}][Tests] Set estimate field to: "{converted_estimate}" (original: "{case["estimate"]}")')
            else:
                self.logger.log(f'[{self.project["code"]}][Tests] Case {case["title"]} has no estimate value')
        else:
            self.logger.log(f'[{self.project["code"]}][Tests] Estimate field not available in mappings')
        return data

    def _set_assigned_to(self, case: dict, data: dict) -> dict:
        """Set assigned to field with mapped user ID"""
        if hasattr(self.mappings, 'assigned_to_field_id') and self.mappings.assigned_to_field_id:
            # Check if case has case_assignedto_id field
            if 'case_assignedto_id' in case and case['case_assignedto_id']:
                # Map TestRail user ID to Qase user ID
                assigned_user_id = self.mappings.get_user_id(case['case_assignedto_id'])
                data['custom_field'][str(self.mappings.assigned_to_field_id)] = str(assigned_user_id)
                self.logger.log(f'[{self.project["code"]}][Tests] Set assigned to field to user ID: {assigned_user_id} (TestRail user ID: {case["case_assignedto_id"]})')
            else:
                self.logger.log(f'[{self.project["code"]}][Tests] Case {case["title"]} has no assigned user')
        else:
            self.logger.log(f'[{self.project["code"]}][Tests] Assigned To field not available in mappings')
        return data




    def get_case_id_mapping(self) -> dict:
        """
        Returns the mapping of original TestRail IDs to generated Qase IDs
        """
        return self.mappings.case_id_mapping

    def __normalize_custom_field_name(self, field_name: str) -> str:
        """
        Normalize custom field name by removing common prefixes.
        Supports both 'case_numbers' and 'numbers' -> 'numbers'
        """
        # Remove common prefixes that might be added to field names
        # Note: 'test_' is excluded to avoid conflicts with fields like 'custom_test_data'
        prefixes_to_remove = ['case_', 'tr_']
        
        for prefix in prefixes_to_remove:
            if field_name.startswith(prefix):
                field_name = field_name[len(prefix):]
                break
        
        return field_name
