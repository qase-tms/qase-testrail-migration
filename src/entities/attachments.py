import asyncio
import re
from typing import List, Optional, Set
from urllib.parse import unquote

from ..service import QaseService, TestrailService
from ..support import Logger, Mappings, ConfigManager as Config, Pools


class Attachments:
    _ID_PATTERN = r'[a-f0-9-]{1,64}'
    # Pattern for markdown: ![](index.php?/attachments/get/123)
    _MARKDOWN_PATTERN = re.compile(rf'!\[\]\(index\.php\?/attachments/get/({_ID_PATTERN})\)')
    # Pattern for HTML attachment references in various formats
    _HTML_ATTACHMENT_PATTERN = re.compile(
        rf'(?:index\.php\?/attachments/get/|data-attachment-id=["\']|data-original-src=["\']index\.php\?/attachments/get/)({_ID_PATTERN})',
        re.IGNORECASE
    )
    # Pattern for HTML img tags with various src formats
    _HTML_IMG_PATTERN = re.compile(
        rf'<img[^>]*(?:src=["\']index\.php\?/attachments/get/({_ID_PATTERN})["\']'
        rf'|data-attachment-id=["\']({_ID_PATTERN})["\']'
        rf'|data-original-src=["\']index\.php\?/attachments/get/({_ID_PATTERN})["\'])[^>]*>',
        re.IGNORECASE
    )
    _FILENAME_PATTERN = re.compile(r"filename\*=UTF-8''(.+?)(?:;|$)", re.IGNORECASE)
    _PREFIX_PATTERN = re.compile(r'^E_')
    _VIDEO_EXTENSIONS = frozenset(['.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv', '.m4v',
                                   '.3gp', '.ogv', '.mpg', '.mpeg', '.asf', '.rm', '.rmvb', '.vob'])
    
    def __init__(self, qase_service: QaseService, testrail_service: TestrailService,
                 logger: Logger, mappings: Mappings, config: Config, pools: Pools):
        self.qase = qase_service
        self.testrail = testrail_service
        self.logger = logger
        self.config = config
        self.mappings = mappings
        self.pools = pools

    def check_and_replace_attachments(self, string: str, code: str, result_id: str = None, test_id: str = None) -> str:
        """Check for attachments and replace them if found."""
        if not string:
            return str(string)
        if self.check_attachments(string):
            return self.replace_attachments(string, code, result_id, test_id)
        return str(string)

    def _normalize_attachment_id(self, attachment_id: str) -> str:
        """Remove 'E_' prefix if present."""
        return self._PREFIX_PATTERN.sub('', str(attachment_id))

    def _get_attachment_hash(self, attachment_id: str, code: str, result_id: str = None, test_id: str = None) -> Optional[str]:
        """Get attachment hash from map, with failover if not found."""
        normalized_id = self._normalize_attachment_id(attachment_id)
        
        if normalized_id not in self.mappings.attachments_map:
            self.logger.log(f'[{code}][Attachments] Attachment {normalized_id} not found in attachments_map', 'warning')
            self.replace_failover(normalized_id, code, result_id, test_id)
        
        attachment_data = self.mappings.attachments_map.get(normalized_id)
        if attachment_data and 'hash' in attachment_data:
            return attachment_data['hash']
        return None

    def check_and_replace_attachments_from_string_array(self, string: str, code: str, result_id: str = None, test_id: str = None) -> list:
        """Extract attachment hashes from a string containing attachment references."""
        result = []
        for aid in self.check_attachments(string):
            if aid and not isinstance(aid, int):
                h = self._get_attachment_hash(aid, code, result_id, test_id)
                if h:
                    result.append(h)
        return result

    def check_and_replace_attachments_array(self, attachments: list, code: str, result_id: str = None, test_id: str = None) -> list:
        """Convert a list of attachment IDs to their corresponding hashes."""
        result = []
        for attachment in attachments:
            if attachment:
                try:
                    h = self._get_attachment_hash(self._normalize_attachment_id(attachment), code, result_id, test_id)
                    if h:
                        result.append(h)
                except Exception as e:
                    self.logger.log(f'[{code}][Attachments] Error processing attachment {attachment}: {e}', 'error')
        return result

    def check_attachments(self, string: str) -> List[str]:
        """
        Extract attachment IDs from both markdown and HTML image formats.
        Returns a list of unique attachment IDs found in the string.
        Optimized to use a single unified pattern for better performance.
        """
        if not string:
            return []
        
        attachment_ids: Set[str] = set()
        string_str = str(string)
        
        for match in self._MARKDOWN_PATTERN.finditer(string_str):
            attachment_ids.add(match.group(1))
        for match in self._HTML_ATTACHMENT_PATTERN.finditer(string_str):
            attachment_ids.add(match.group(1))
        
        return list(attachment_ids)

    def _get_attachment_meta(self, data) -> tuple:
        """Extract filename and content from attachment data."""
        match = self._FILENAME_PATTERN.search(data.headers.get('Content-Disposition', ''))
        return (unquote(match.group(1)) if match else "attachment", data.content)

    def replace_attachments(self, string: str, code: str, result_id: str = None, test_id: str = None) -> str:
        """
        Replace both markdown and HTML image references with Qase markdown format.
        Converts: ![](index.php?/attachments/get/123) or <img src="..."> to ![filename](qase_url)
        """
        if not string:
            return str(string)
        
        string = self._PREFIX_PATTERN.sub('', string)
        try:
            for match in list(self._MARKDOWN_PATTERN.finditer(string)):
                attachment_id = match.group(1)
                if attachment_id not in self.mappings.attachments_map:
                    self.logger.log(f'[{code}][Attachments] Attachment {attachment_id} not found in attachments_map', 'warning')
                    self.replace_failover(attachment_id, code, result_id, test_id)
                string = self.replace_string_markdown(string, code, attachment_id)
            for match in reversed(list(self._HTML_IMG_PATTERN.finditer(string))):
                attachment_id = next((g for g in match.groups() if g), None)
                if attachment_id and attachment_id not in self.mappings.attachments_map:
                    self.logger.log(f'[{code}][Attachments] Attachment {attachment_id} not found in attachments_map (HTML)', 'warning')
                    self.replace_failover(attachment_id, code, result_id, test_id)
                if attachment_id:
                    string = self.replace_string_html(string, code, attachment_id, match.group(0))
        except Exception as e:
            self.logger.log(f'[{code}][Attachments] Exception when replacing attachments: {e}', 'error')
        return string

    def replace_failover(self, attachment_id: str, code: str, result_id: str = None, test_id: str = None):
        """Upload attachment on-demand if not found in map."""
        try:
            result_info = f' for result ({", ".join([f"result_id={result_id}", f"test_id={test_id}"][:bool(result_id) + bool(test_id)])})' if (result_id or test_id) else ''
            self.logger.log(f'[{code}][Attachments] Replacing attachment {attachment_id} in failover{result_info}')
            qase_attachment = self.qase.upload_attachment(code, self._get_attachment_meta(self.testrail.get_attachment(attachment_id)))
            if qase_attachment:
                self.mappings.attachments_map[attachment_id] = qase_attachment
                self.logger.log(f'[{code}][Attachments] Attachment {attachment_id} replaced in failover{result_info}')
            else:
                self.logger.log(f'[{code}][Attachments] Attachment {attachment_id} not replaced in failover{result_info}', 'error')
        except Exception as e:
            self.logger.log(f'[{code}][Attachments] Exception when calling Qase->upload_attachment in failover{result_info}: {e}', 'error')

    def _is_video_file(self, filename: str) -> bool:
        """Check if a file is a video based on its extension."""
        return filename and filename.lower().endswith(tuple(self._VIDEO_EXTENSIONS))
    
    def _get_markdown_for_attachment(self, attachment_id: str) -> Optional[str]:
        """Get markdown representation for an attachment."""
        if attachment_id not in self.mappings.attachments_map:
            return None
        
        attachment_data = self.mappings.attachments_map[attachment_id]
        filename = attachment_data["filename"]
        url = attachment_data["url"]
        return f'[{filename}]({url})' if self._is_video_file(filename) else f'![{filename}]({url})'
    
    def replace_string_markdown(self, string: str, code: str, attachment_id: str) -> str:
        """Replace markdown format image/video reference with Qase markdown format."""
        markdown = self._get_markdown_for_attachment(attachment_id)
        if not markdown:
            return string
        
        pattern = re.compile(f'!\\[\\]\\(index\\.php\\?/attachments/get/{re.escape(attachment_id)}\\)')
        return pattern.sub(markdown, string)
    
    def replace_string_html(self, string: str, code: str, attachment_id: str, html_tag: str) -> str:
        """Replace HTML img tag with Qase markdown format."""
        markdown = self._get_markdown_for_attachment(attachment_id)
        if not markdown:
            return string
        
        escaped_tag = re.escape(html_tag)
        return re.sub(escaped_tag, markdown, string)

    def import_all_attachments(self) -> Mappings:
        return asyncio.run(self.import_all_attachments_async())

    async def import_all_attachments_async(self) -> Mappings:
        self.logger.log('[Attachments] Importing all attachments')
        attachments_raw = self.testrail.get_attachments_list()
        self.mappings.stats.add_attachment('testrail', len(attachments_raw))

        async with asyncio.TaskGroup() as tg:
            for attachment in attachments_raw:
                tg.create_task(self.import_raw_attachment(attachment))

        self.logger.log(f'[Attachments] Imported {len(attachments_raw)} attachments')

        return self.mappings

    async def import_raw_attachment(self, attachment):
        self.logger.log(f'[Attachments] Importing attachment: {attachment["id"]}')

        project_ids = attachment['project_id'] if isinstance(attachment['project_id'], list) else [
            attachment['project_id']]

        if not project_ids:
            self.logger.log(f'[Attachments] Attachment {attachment["id"]} is not linked to any project', 'warning')
            return

        if len(project_ids) > 1:
            self.logger.log(f'[Attachments] Attachment {attachment["id"]} is linked to multiple projects', 'warning')

        project_id = project_ids[0]

        if project_id not in self.mappings.project_map:
            self.logger.log(f'[Attachments] Attachment {attachment["id"]} is not linked to any project', 'error')
            return

        code = self.mappings.project_map[project_id]

        try:
            meta = self._get_attachment_meta(await self.pools.tr(self.testrail.get_attachment, attachment['id']))
        except Exception as e:
            self.logger.log(f'[{code}][Attachments] Exception when calling TestRail->get_attachment: {e}', 'error')
            return

        try:
            qase_attachment = await self.pools.qs(self.qase.upload_attachment, code, meta)
            if qase_attachment:
                self.mappings.attachments_map[attachment['id']] = qase_attachment
                self.logger.log(f'[{code}][Attachments] Attachment {attachment["id"]} imported')
                self.mappings.stats.add_attachment('qase')
            else:
                self.logger.log(f'[{code}][Attachments] Attachment {attachment["id"]} not imported', 'error')
        except Exception as e:
            self.logger.log(f'[{code}][Attachments] Exception when calling Qase->upload_attachment: {e}', 'error')

