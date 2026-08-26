"""
Text utilities for TestRail to Qase migration.
Contains functions for text formatting and conversion.
"""

import re
from datetime import datetime


def convert_testrail_tables_to_markdown(text):
    """
    Convert TestRail table format to Markdown format.
    
    TestRail format:
    |||:Remote|:Shipped with Device
    ||Giga remote|  Giga
    ||Laredo  |  Austin
    
    Markdown format:
    | Remote | Shipped with Device |
    |--------|-------------------|
    | Giga remote | Giga |
    | Laredo | Austin |
    
    Note: The colon (:) prefix in TestRail column headers is automatically removed.
    
    Args:
        text (str): Text that may contain TestRail table format
        
    Returns:
        str: Text with TestRail tables converted to Markdown format
    """
    if text is None:
        return text
    
    lines = text.split('\n')
    result_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Check if this line starts a TestRail table (|||)
        if line.startswith('|||'):
            # Found a table header, process the entire table
            table_lines = [line]
            i += 1
            
            # Collect all data rows (lines starting with ||)
            while i < len(lines) and lines[i].startswith('||'):
                table_lines.append(lines[i])
                i += 1
            
            # Convert the table
            converted_table = convert_single_table(table_lines)
            result_lines.append(converted_table)
        else:
            # Regular line, add as is
            result_lines.append(line)
            i += 1
    
    return '\n'.join(result_lines)


def convert_single_table(table_lines):
    """
    Convert a single TestRail table to Markdown format.
    
    Args:
        table_lines (list): List of lines that form a TestRail table
        
    Returns:
        str: Markdown formatted table
    """
    if len(table_lines) < 2:  # Need at least header and one data row
        return '\n'.join(table_lines)
    
    # Process header line (starts with |||)
    header_line = table_lines[0]
    if not header_line.startswith('|||'):
        return '\n'.join(table_lines)
    
    # Extract header columns (remove ||| prefix and split by |)
    header_cols = [col.strip().lstrip(':') for col in header_line[3:].split('|') if col.strip()]
    
    # Create markdown table header
    markdown_table = '| ' + ' | '.join(header_cols) + ' |\n'
    markdown_table += '|' + '|'.join(['-' * (len(col) + 2) for col in header_cols]) + '|\n'
    
    # Process data rows (start with ||)
    for line in table_lines[1:]:
        if line.startswith('||'):
            # Extract data columns (remove || prefix and split by |)
            remaining_line = line[2:]  # Remove || prefix
            data_cols = []
            
            # Split by | but handle empty columns properly
            parts = remaining_line.split('|')
            for part in parts:
                data_cols.append(part.strip())
            
            # Ensure we have the right number of columns
            while len(data_cols) < len(header_cols):
                data_cols.append('')
            # Truncate if we have too many columns
            data_cols = data_cols[:len(header_cols)]
            markdown_table += '| ' + ' | '.join(data_cols) + ' |\n'
    
    return markdown_table


def format_links_as_markdown(text, project_code=None, config=None):
    """
    Format text by converting TestRail tables to Markdown and formatting URLs as Markdown links.
    Also replaces TestRail case links with Qase case links.
    
    Args:
        text (str): Text to format
        project_code (str, optional): Qase project code for replacing TestRail case links
        config (dict, optional): Config object to get Qase host settings
        
    Returns:
        str: Formatted text with tables converted and URLs as Markdown links
    """
    if text is None:
        return None
    
    # Ensure text is a string - don't process dicts or other types
    if not isinstance(text, str):
        return text

    # First convert TestRail tables to Markdown
    text = convert_testrail_tables_to_markdown(text)

    # Fix numbering
    text = fix_numbering(text)

    # Replace TestRail case links with Qase case links if project_code is provided
    if project_code and config:
        text = replace_testrail_case_links(text, project_code, config)
        
        # Format URLs as Markdown links (this may convert broken Qase URLs to markdown)
        # Pattern: Match URLs that are NOT already inside markdown links
        # Avoid matching URLs inside [brackets] or (parentheses) of existing markdown links
        # Simple approach: don't match if preceded by [ or ](, and don't match if followed by ] or )
        url_pattern = re.compile(r'(?<!\[)(?<!\]\()\b(http[s]?://[^\s\)\]]+)(?![\]\)])')
        text = url_pattern.sub(r'[\1](\1)', text)
        
        # After URL pattern converts plain URLs to markdown, fix any broken Qase links
        # that were just converted to markdown format
        text = replace_testrail_case_links(text, project_code, config)
    else:
        # Format URLs as Markdown links
        # Pattern: Match URLs that are NOT already inside markdown links
        # Avoid matching URLs inside [brackets] or (parentheses) of existing markdown links
        # Simple approach: don't match if preceded by [ or ](, and don't match if followed by ] or )
        url_pattern = re.compile(r'(?<!\[)(?<!\]\()\b(http[s]?://[^\s\)\]]+)(?![\]\)])')
        text = url_pattern.sub(r'[\1](\1)', text)

    return text


def replace_testrail_case_links(text, project_code, config):
    """
    Replace TestRail case links with Qase case links.
    
    Args:
        text (str): Text that may contain TestRail case links
        project_code (str): Qase project code
        config: Config object to get Qase and TestRail host settings
        
    Returns:
        str: Text with TestRail case links replaced with Qase case links
    """
    if not text or not project_code or not config:
        return text
    
    # Ensure text is a string - don't process dicts or other types
    if not isinstance(text, str):
        return text
    
    # Get TestRail host from config
    testrail_host = config.get('testrail.api.host')
    if not testrail_host:
        return text
    
    # Normalize TestRail host (remove trailing slash, extract base URL)
    testrail_host = testrail_host.rstrip('/')
    # Extract domain from URL (e.g., "https://affinipay.testrail.net" -> "affinipay.testrail.net")
    testrail_domain = re.sub(r'^https?://', '', testrail_host)
    
    # Build Qase app URL. Derived from qase.host, same rule as the API URL.
    from ..service.qase import qase_app_url as _qase_app_url

    qase_app_url = _qase_app_url(config)
    
    # Pattern 1: Match TestRail case links in markdown format with full URL
    # Matches: [C123456](https://{testrail_domain}/index.php?/cases/view/123456)
    # Also handles variations like /index.php?/cases/view/ or index.php?/cases/view/
    testrail_case_pattern_full = re.compile(
        rf'\[C(\d+)\]\(https?://{re.escape(testrail_domain)}/index\.php\?/cases/view/(\d+)\)',
        re.IGNORECASE
    )
    
    def replace_full_link(match):
        case_id = match.group(2)  # Case ID from the URL (use URL ID as it's authoritative)
        qase_link = f'[C{case_id}]({qase_app_url}/project/{project_code}?case={case_id})'
        return qase_link
    
    # Pattern 2: Match TestRail case links with relative URLs (no domain)
    # Matches: [C123456](index.php?/cases/view/123456) or [C123456](/index.php?/cases/view/123456)
    testrail_case_pattern_relative = re.compile(
        r'\[C(\d+)\]\(/?index\.php\?/cases/view/(\d+)\)',
        re.IGNORECASE
    )
    
    def replace_relative_link(match):
        case_id = match.group(2)  # Use URL ID as authoritative
        qase_link = f'[C{case_id}]({qase_app_url}/project/{project_code}?case={case_id})'
        return qase_link
    
    # Pattern 3: Match plain text case references [C123456] that are not already links
    # This handles cases where the link was removed or never had a link
    # We need to be careful not to match already-formatted markdown links
    testrail_case_pattern_text = re.compile(
        r'(?<!\]\()\[C(\d+)\](?!\()',
        re.IGNORECASE
    )
    
    def replace_text_reference(match):
        case_id = match.group(1)
        qase_link = f'[C{case_id}]({qase_app_url}/project/{project_code}?case={case_id})'
        return qase_link
    
    # Pattern 4: Match TestRail URLs that might be in plain URL format (before markdown conversion)
    # Matches: https://{testrail_domain}/index.php?/cases/view/123456
    # But NOT if they're already inside markdown links
    testrail_case_pattern_url = re.compile(
        rf'(?<!\]\()(https?://{re.escape(testrail_domain)}/index\.php\?/cases/view/(\d+))',
        re.IGNORECASE
    )
    
    def replace_url(match):
        case_id = match.group(2)
        qase_link = f'[C{case_id}]({qase_app_url}/project/{project_code}?case={case_id})'
        return qase_link
    
    # Pattern 5: Match plain relative TestRail URLs (not in markdown, no domain)
    # Matches: index.php?/cases/view/123456 or /index.php?/cases/view/123456
    testrail_case_pattern_relative_url = re.compile(
        r'(?<!\]\()(/?index\.php\?/cases/view/(\d+))',
        re.IGNORECASE
    )
    
    def replace_relative_url(match):
        case_id = match.group(2)
        qase_link = f'[C{case_id}]({qase_app_url}/project/{project_code}?case={case_id})'
        return qase_link
    
    # Pattern 4: Fix broken Qase links that were incorrectly replaced
    # These are links that have the Qase base URL but still have the TestRail path format
    # Matches both plain URLs and markdown links with broken Qase format:
    # - https://app.qase.io/project/index.php?/cases/view/123456
    # - [C123456](https://app.qase.io/project/index.php?/cases/view/123456)
    # - [text](https://app.qase.io/project/index.php?/cases/view/123456)
    # Also handles enterprise domains like app-customdomain.com
    
    # Build pattern to match Qase app URL (handles both cloud and enterprise)
    qase_app_pattern = rf'app{re.escape(delimiter)}{re.escape(main_host)}'
    
    # Pattern 4a: Fix broken Qase links in markdown format with [C123456] text
    broken_qase_pattern_markdown = re.compile(
        rf'\[C(\d+)\]\(https?://{qase_app_pattern}/project/index\.php\?/cases/view/(\d+)\)',
        re.IGNORECASE
    )
    
    def fix_broken_link_markdown(match):
        case_id = match.group(2)  # Use URL ID as authoritative
        qase_link = f'[C{case_id}]({qase_app_url}/project/{project_code}?case={case_id})'
        return qase_link
    
    # Pattern 4b: Fix broken Qase links in markdown format with arbitrary text [text](broken_url)
    broken_qase_pattern_markdown_text = re.compile(
        rf'\[([^\]]+)\]\(https?://{qase_app_pattern}/project/index\.php\?/cases/view/(\d+)\)',
        re.IGNORECASE
    )
    
    def fix_broken_link_markdown_text(match):
        link_text = match.group(1)
        case_id = match.group(2)
        # Extract case ID from link text if it's in [C123456] format, otherwise use URL ID
        case_id_match = re.search(r'C(\d+)', link_text, re.IGNORECASE)
        if case_id_match:
            case_id = case_id_match.group(1)
        qase_link = f'[C{case_id}]({qase_app_url}/project/{project_code}?case={case_id})'
        return qase_link
    
    # Pattern 4c: Fix broken Qase links in plain URL format (not in markdown)
    broken_qase_pattern_url = re.compile(
        rf'(?<!\]\()(https?://{qase_app_pattern}/project/index\.php\?/cases/view/(\d+))',
        re.IGNORECASE
    )
    
    def fix_broken_link_url(match):
        case_id = match.group(2)
        qase_link = f'[C{case_id}]({qase_app_url}/project/{project_code}?case={case_id})'
        return qase_link
    
    # Apply replacements in order:
    # 1. First fix any broken Qase links that were incorrectly replaced
    #    (must be done first, before other patterns process them)
    #    Order matters: markdown with text first, then markdown with [C], then plain URLs
    replaced_text = broken_qase_pattern_markdown_text.sub(fix_broken_link_markdown_text, text)
    replaced_text = broken_qase_pattern_markdown.sub(fix_broken_link_markdown, replaced_text)
    replaced_text = broken_qase_pattern_url.sub(fix_broken_link_url, replaced_text)
    
    # 2. Replace full markdown links with full TestRail URLs
    replaced_text = testrail_case_pattern_full.sub(replace_full_link, replaced_text)
    
    # 3. Replace markdown links with relative TestRail URLs (no domain)
    replaced_text = testrail_case_pattern_relative.sub(replace_relative_link, replaced_text)
    
    # 4. Then replace plain full URLs (in case they weren't converted to markdown yet)
    replaced_text = testrail_case_pattern_url.sub(replace_url, replaced_text)
    
    # 5. Replace plain relative URLs (no domain, not in markdown)
    replaced_text = testrail_case_pattern_relative_url.sub(replace_relative_url, replaced_text)
    
    # 6. Finally, replace plain text references [C123456] that aren't already links
    replaced_text = testrail_case_pattern_text.sub(replace_text_reference, replaced_text)
    
    return replaced_text


def fix_numbering(text):
    """
    Fix numbering in text by converting 0-based numbering to 1-based numbering.
    
    This function finds lines that start with a number followed by a dot and space,
    and converts them from 0-based to 1-based numbering. Each separate block of
    numbered lines is numbered independently starting from 1.
    
    Example:
    Input:
    0. Select Settings
    0. Select Parental controls.
    Some unnumbered text here
    0. Enter a pin code such as 1111.
    0. Select OK.
    
    Output:
    1. Select Settings
    2. Select Parental controls.
    Some unnumbered text here
    1. Enter a pin code such as 1111.
    2. Select OK.
    
    Args:
        text (str): Text that may contain 0-based numbered lines
        
    Returns:
        str: Text with corrected 1-based numbering
    """
    if text is None:
        return None
    
    lines = text.split('\n')
    result_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Check if this line starts with a number followed by a dot and space
        numbering_match = re.match(r'^(\d+)\. ', line)
        
        if numbering_match:
            # Found a numbered line, process the entire block of numbered lines
            block_start = i
            block_number = 1  # Start numbering from 1 for this block
            
            # Process all consecutive numbered lines
            while i < len(lines):
                current_line = lines[i]
                current_match = re.match(r'^(\d+)\. ', current_line)
                
                if current_match:
                    # Replace the number with the new sequential number
                    new_line = re.sub(r'^\d+\. ', f'{block_number}. ', current_line)
                    result_lines.append(new_line)
                    block_number += 1
                    i += 1
                else:
                    # Non-numbered line, break the block
                    break
        else:
            # Non-numbered line, add as is
            result_lines.append(line)
            i += 1
    
    return '\n'.join(result_lines)


def convert_testrail_date_to_iso(date_string):
    """
    Convert TestRail date format to ISO format for Qase datetime fields.
    
    TestRail formats supported:
    - M/D/YYYY (e.g., "3/23/2023")
    - MM/D/YYYY (e.g., "03/23/2023")
    - M/DD/YYYY (e.g., "3/23/2023")
    - MM/DD/YYYY (e.g., "03/23/2023")
    
    Output format: YYYY-MM-DD HH:MM:SS (e.g., "2023-03-23 00:00:00")
    
    Args:
        date_string (str): Date string in TestRail format
        
    Returns:
        str: Date string in ISO format, or original string if conversion fails
    """
    if not date_string or not isinstance(date_string, str):
        return date_string
    
    # Remove any whitespace
    date_string = date_string.strip()
    
    # Try different date formats
    date_formats = [
        '%m/%d/%Y',      # M/D/YYYY or MM/DD/YYYY
        '%m/%d/%y',      # M/D/YY or MM/DD/YY
        '%d/%m/%Y',      # D/M/YYYY or DD/MM/YYYY
        '%d/%m/%y',      # D/M/YY or DD/MM/YY
        '%Y-%m-%d',      # YYYY-MM-DD
        '%Y/%m/%d',      # YYYY/MM/DD
    ]
    
    for date_format in date_formats:
        try:
            parsed_date = datetime.strptime(date_string, date_format)
            # Convert to ISO format with time set to 00:00:00
            return parsed_date.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
    
    # If no format matches, return original string
    return date_string


def convert_estimate_time_to_hours(estimate_string):
    """
    Convert TestRail estimate time format to simplified format.
    
    TestRail API returns detailed format like: '1wk 1d 1hr 1min 1sec'
    We need to return simplified format like: '1 week 1 day'
    
    Conversion rules:
    - Take only the first two time units from the estimate
    - For first two values: keep them separate without combining
    - Apply rounding only if there are more than 2 values
    - Return in simplified format (not converted to hours)
    
    Examples:
    - "1wk 1d 1hr 1min 1sec" -> "1 week 1 day"
    - "5hr 30min" -> "5 hours 30 minutes" (keep separate)
    - "1hr 1min 1sec" -> "1 hour 1 minute"
    - "2wk 3d 2hr 30min" -> "2 week 3 day"
    
    Args:
        estimate_string (str): Estimate string in TestRail API format
        
    Returns:
        str: Simplified estimate format, or original string if conversion fails
    """
    if not estimate_string or not isinstance(estimate_string, str):
        return estimate_string
    
    # Remove any whitespace
    estimate_string = estimate_string.strip()
    
    if not estimate_string:
        return estimate_string
    
    # Parse the estimate string to extract time units
    import re
    import math
    
    # Pattern to match number + unit pairs
    pattern = r'(\d+(?:\.\d+)?)\s*(wk|week|d|day|hr|hour|h|min|minute|m|sec|second)s?'
    matches = re.findall(pattern, estimate_string, re.IGNORECASE)
    
    if not matches:
        # If no matches found, return original string
        return estimate_string
    
    # Take the first two time units, but handle special cases
    first_two_units = matches[:2]
    
    # Special case: if we have "1d 3h 50m", we need to take days and combine hours+minutes
    if len(matches) >= 3:
        first_unit = matches[0][1].lower()
        second_unit = matches[1][1].lower()
        third_unit = matches[2][1].lower()
        
        # If first is days, second is hours, third is minutes, combine hours+minutes
        if (first_unit in ['d', 'day'] and 
            second_unit in ['hr', 'hour', 'h'] and 
            third_unit in ['min', 'minute', 'm']):
            try:
                days = float(matches[0][0])
                hours = float(matches[1][0])
                minutes = float(matches[2][0])
                
                result_parts = []
                
                # Round days
                rounded_days = math.ceil(days)
                if rounded_days > 0:
                    result_parts.append(f"{rounded_days} day{'s' if rounded_days != 1 else ''}")
                
                # Combine hours and minutes
                total_hours = hours + (minutes / 60)
                rounded_hours = math.ceil(total_hours)
                if rounded_hours > 0:
                    result_parts.append(f"{rounded_hours} hour{'s' if rounded_hours != 1 else ''}")
                
                # Return early for this special case
                if result_parts:
                    return ' '.join(result_parts)
                else:
                    return estimate_string
            except ValueError:
                pass
    
    result_parts = []
    
    # Special handling for hours + minutes combination
    if len(first_two_units) == 2:
        first_value_str, first_unit = first_two_units[0]
        second_value_str, second_unit = first_two_units[1]
        
        first_unit_lower = first_unit.lower()
        second_unit_lower = second_unit.lower()
        
        # If first is hours and second is minutes, keep them separate for first two values
        if (first_unit_lower in ['hr', 'hour', 'h'] and second_unit_lower in ['min', 'minute', 'm']):
            try:
                hours = float(first_value_str)
                minutes = float(second_value_str)
                
                # For first two values, keep them separate without rounding
                rounded_hours = math.ceil(hours)
                rounded_minutes = math.ceil(minutes)
                
                if rounded_hours > 0:
                    result_parts.append(f"{rounded_hours} hour{'s' if rounded_hours != 1 else ''}")
                if rounded_minutes > 0:
                    result_parts.append(f"{rounded_minutes} minute{'s' if rounded_minutes != 1 else ''}")
            except ValueError:
                pass
        # Special handling for days + hours combination (like "1d 3h 50m")
        elif (first_unit_lower in ['d', 'day'] and second_unit_lower in ['hr', 'hour', 'h']):
            try:
                days = float(first_value_str)
                hours = float(second_value_str)
                
                # Round days
                rounded_days = math.ceil(days)
                if rounded_days > 0:
                    result_parts.append(f"{rounded_days} day{'s' if rounded_days != 1 else ''}")
                
                # Round hours
                rounded_hours = math.ceil(hours)
                if rounded_hours > 0:
                    result_parts.append(f"{rounded_hours} hour{'s' if rounded_hours != 1 else ''}")
            except ValueError:
                pass
        else:
            # Regular processing for other combinations
            for value_str, unit in first_two_units:
                try:
                    value = float(value_str)
                    unit_lower = unit.lower()
                    
                    # Apply rounding to individual values
                    rounded_value = math.ceil(value)
                    
                    # Skip zero values
                    if rounded_value == 0:
                        continue
                    
                    # Convert to full word format
                    if unit_lower in ['wk', 'week']:
                        result_parts.append(f"{rounded_value} week{'s' if rounded_value != 1 else ''}")
                    elif unit_lower in ['d', 'day']:
                        result_parts.append(f"{rounded_value} day{'s' if rounded_value != 1 else ''}")
                    elif unit_lower in ['hr', 'hour', 'h']:
                        result_parts.append(f"{rounded_value} hour{'s' if rounded_value != 1 else ''}")
                    elif unit_lower in ['min', 'minute', 'm']:
                        result_parts.append(f"{rounded_value} minute{'s' if rounded_value != 1 else ''}")
                    elif unit_lower in ['sec', 'second']:
                        result_parts.append(f"{rounded_value} second{'s' if rounded_value != 1 else ''}")
                            
                except (ValueError, KeyError):
                    # Skip invalid values or units
                    continue
    else:
        # Single unit processing
        for value_str, unit in first_two_units:
            try:
                value = float(value_str)
                unit_lower = unit.lower()
                
                # Apply rounding to individual values
                rounded_value = math.ceil(value)
                
                # Skip zero values
                if rounded_value == 0:
                    continue
                
                # Convert to full word format
                if unit_lower in ['wk', 'week']:
                    result_parts.append(f"{rounded_value} week{'s' if rounded_value != 1 else ''}")
                elif unit_lower in ['d', 'day']:
                    result_parts.append(f"{rounded_value} day{'s' if rounded_value != 1 else ''}")
                elif unit_lower in ['hr', 'hour', 'h']:
                    result_parts.append(f"{rounded_value} hour{'s' if rounded_value != 1 else ''}")
                elif unit_lower in ['min', 'minute', 'm']:
                    result_parts.append(f"{rounded_value} minute{'s' if rounded_value != 1 else ''}")
                elif unit_lower in ['sec', 'second']:
                    result_parts.append(f"{rounded_value} second{'s' if rounded_value != 1 else ''}")
                        
            except (ValueError, KeyError):
                # Skip invalid values or units
                continue
    
    # Return the result
    if result_parts:
        return ' '.join(result_parts)
    else:
        # If no valid conversion, return original string
        return estimate_string


def html_to_markdown(html_text, remove_html=False):
    """
    Convert HTML to Markdown or remove HTML tags.
    
    Args:
        html_text (str): HTML text to convert
        remove_html (bool): If True, remove HTML tags. If False, convert to markdown.
        
    Returns:
        str: Converted markdown text or text without HTML tags
    """
    if html_text is None:
        return None
    
    if not isinstance(html_text, str):
        return html_text
    
    # Try to use html2text if available
    try:
        import html2text
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = False
        h.body_width = 0  # Don't wrap lines
        h.unicode_snob = True
        h.escape_snob = True
        
        if remove_html:
            # Just remove HTML tags, keep text content
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_text, 'html.parser')
            return soup.get_text(separator=' ', strip=True)
        else:
            # Convert to markdown
            markdown_text = h.handle(html_text)
            # Clean up extra whitespace - especially fix list spacing
            # Remove blank lines between list items
            markdown_text = re.sub(r'(\n[-*] .+?)\n+(\n[-*] )', r'\1\2', markdown_text)
            markdown_text = re.sub(r'(\n[-*] .+?)\n{2,}(\n[-*] )', r'\1\n\2', markdown_text)
            # Remove multiple consecutive newlines (3+ becomes 2)
            markdown_text = re.sub(r'\n{3,}', '\n\n', markdown_text)
            # Remove trailing whitespace from each line
            markdown_text = re.sub(r'[ \t]+$', '', markdown_text, flags=re.MULTILINE)
            return markdown_text.strip()
    except ImportError:
        # Fallback to BeautifulSoup if html2text is not available
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_text, 'html.parser')
            
            if remove_html:
                # Just remove HTML tags
                return soup.get_text(separator=' ', strip=True)
            else:
                # Basic HTML to markdown conversion
                # Convert common HTML elements to markdown
                text = str(soup)
                
                # Convert <strong> and <b> to **
                text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
                
                # Convert <em> and <i> to *
                text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
                
                # Convert <a href="...">text</a> to [text](url)
                text = re.sub(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.DOTALL | re.IGNORECASE)
                
                # Convert <p> to newlines
                text = re.sub(r'<p[^>]*>', '\n\n', text, flags=re.IGNORECASE)
                text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
                
                # Convert <br> and <br/> to newlines
                text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
                
                # Convert <ul> and <ol> lists
                # First, normalize list structure by removing extra whitespace in list tags
                text = re.sub(r'<ul[^>]*>\s*', '<ul>', text, flags=re.IGNORECASE)
                text = re.sub(r'<ol[^>]*>\s*', '<ol>', text, flags=re.IGNORECASE)
                text = re.sub(r'\s*</ul>', '</ul>', text, flags=re.IGNORECASE)
                text = re.sub(r'\s*</ol>', '</ol>', text, flags=re.IGNORECASE)
                
                # Convert <li> items - strip whitespace and add single newline
                # Use a function to properly handle content and spacing
                def convert_list_item(match):
                    content = match.group(1).strip()
                    return f'- {content}\n'
                
                text = re.sub(r'<li[^>]*>(.*?)</li>', convert_list_item, text, flags=re.DOTALL | re.IGNORECASE)
                
                # Convert list container tags - add newline only before opening tag and after closing tag
                # But ensure we don't add extra newlines if they already exist
                text = re.sub(r'<ul[^>]*>', '\n', text, flags=re.IGNORECASE)
                text = re.sub(r'</ul>', '\n', text, flags=re.IGNORECASE)
                text = re.sub(r'<ol[^>]*>', '\n', text, flags=re.IGNORECASE)
                text = re.sub(r'</ol>', '\n', text, flags=re.IGNORECASE)
                
                # Convert <h1> to #
                text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'# \1\n\n', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'## \1\n\n', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'### \1\n\n', text, flags=re.DOTALL | re.IGNORECASE)
                
                # Remove all remaining HTML tags
                text = re.sub(r'<[^>]+>', '', text)
                
                # Decode HTML entities
                import html
                text = html.unescape(text)
                
                # Clean up extra whitespace - especially fix list spacing
                # Remove blank lines between list items (lines that are just whitespace between lines starting with - or *)
                # This pattern matches: list item, then one or more blank lines, then another list item
                text = re.sub(r'(\n[-*] .+?)\n+(\n[-*] )', r'\1\2', text)
                # Also handle cases where there are blank lines after list items but before the next list item
                text = re.sub(r'(\n[-*] .+?)\n{2,}(\n[-*] )', r'\1\n\2', text)
                # Remove multiple consecutive newlines (3+ becomes 2)
                text = re.sub(r'\n{3,}', '\n\n', text)
                # Normalize spaces/tabs
                text = re.sub(r'[ \t]+', ' ', text)
                # Remove trailing whitespace from each line
                text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
                
                return text.strip()
        except ImportError:
            # Last resort: simple regex-based removal
            if remove_html:
                # Remove HTML tags
                text = re.sub(r'<[^>]+>', '', html_text)
                # Decode HTML entities
                import html
                text = html.unescape(text)
                return text.strip()
            else:
                # Basic conversion without BeautifulSoup
                text = html_text
                # Convert <strong> and <b> to **
                text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
                # Convert <em> and <i> to *
                text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
                # Convert <a href="...">text</a> to [text](url)
                text = re.sub(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.DOTALL | re.IGNORECASE)
                
                # Convert <ul> and <ol> lists
                # Normalize list structure
                text = re.sub(r'<ul[^>]*>\s*', '<ul>', text, flags=re.IGNORECASE)
                text = re.sub(r'<ol[^>]*>\s*', '<ol>', text, flags=re.IGNORECASE)
                text = re.sub(r'\s*</ul>', '</ul>', text, flags=re.IGNORECASE)
                text = re.sub(r'\s*</ol>', '</ol>', text, flags=re.IGNORECASE)
                
                # Convert <li> items
                def convert_list_item(match):
                    content = match.group(1).strip()
                    return f'- {content}\n'
                
                text = re.sub(r'<li[^>]*>(.*?)</li>', convert_list_item, text, flags=re.DOTALL | re.IGNORECASE)
                
                # Convert list container tags
                text = re.sub(r'<ul[^>]*>', '\n', text, flags=re.IGNORECASE)
                text = re.sub(r'</ul>', '\n', text, flags=re.IGNORECASE)
                text = re.sub(r'<ol[^>]*>', '\n', text, flags=re.IGNORECASE)
                text = re.sub(r'</ol>', '\n', text, flags=re.IGNORECASE)
                
                # Remove all remaining HTML tags
                text = re.sub(r'<[^>]+>', '', text)
                # Decode HTML entities
                import html
                text = html.unescape(text)
                
                # Clean up extra whitespace - especially fix list spacing
                # Remove blank lines between list items (lines that are just whitespace between lines starting with - or *)
                # This pattern matches: list item, then one or more blank lines, then another list item
                text = re.sub(r'(\n[-*] .+?)\n+(\n[-*] )', r'\1\2', text)
                # Also handle cases where there are blank lines after list items but before the next list item
                text = re.sub(r'(\n[-*] .+?)\n{2,}(\n[-*] )', r'\1\n\2', text)
                # Remove multiple consecutive newlines (3+ becomes 2)
                text = re.sub(r'\n{3,}', '\n\n', text)
                # Normalize spaces/tabs
                text = re.sub(r'[ \t]+', ' ', text)
                # Remove trailing whitespace from each line
                text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
                
                return text.strip()
