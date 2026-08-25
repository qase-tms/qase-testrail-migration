"""Dry-run Qase service: reads hit the real API, writes are logged and faked.

Used by ``python start.py --dry-run``. The whole extraction and mapping
pipeline runs against real TestRail and real Qase data, so unmapped statuses,
missing custom fields, oversize values and attachment problems all surface,
but nothing is created in Qase.

Fake ids keep the mappings internally consistent, so the later steps
(suites, cases, runs, results) still execute and can be checked too.
"""

import itertools

from .qase import QaseService


class DryRunQaseService(QaseService):
    _fake_ids = itertools.count(90_000_000)

    def _dry(self, message: str):
        self.logger.log(f"[DRY-RUN] {message}")

    # ---- project and structure ---------------------------------------

    def create_project(self, title, description, code, group_id=None):
        self._dry(f"would create project {title!r} [{code}]")
        return True

    def create_suite(self, code, title, description, parent_id=None):
        parent = f" under suite {parent_id}" if parent_id else ""
        self._dry(f"[{code}] would create suite {title!r}{parent}")
        return next(self._fake_ids)

    def create_milestone(self, project_code, title, description, status, due_date):
        self._dry(f"[{project_code}] would create milestone {title!r} ({status})")
        return next(self._fake_ids)

    def create_shared_step(self, project_code, title, steps):
        self._dry(f"[{project_code}] would create shared step {title!r} with {len(steps or [])} step(s)")
        return f"dry-run-hash-{next(self._fake_ids)}"

    # ---- cases, runs and results -------------------------------------

    def create_cases(self, code, cases):
        self._dry(f"[{code}] would bulk-create {len(cases)} case(s)")
        return True

    def create_run(self, run, project_code, cases=[], milestone_id=None):
        name = run.get('name') if isinstance(run, dict) else run
        self._dry(f"[{project_code}] would create run {name!r} with {len(cases)} case(s)")
        return next(self._fake_ids)

    def complete_run(self, project_code, run_id):
        self._dry(f"[{project_code}] would complete run {run_id}")

    def send_bulk_results(self, tr_run, results, qase_run_id, qase_code, mappings, cases_map):
        self._dry(f"[{qase_code}] would send {len(results or [])} result(s) to run {qase_run_id}")

    def send_bulk_results_v2(self, tr_run, results, qase_run_id, qase_code, mappings, cases_map):
        self._dry(f"[{qase_code}] would send {len(results or [])} result(s) to run {qase_run_id} (v2)")

    # ---- fields, configurations and attachments ----------------------

    def create_custom_field(self, data):
        title = data.get('title') if isinstance(data, dict) else data
        self._dry(f"would create custom field {title!r}")
        return next(self._fake_ids)

    def update_custom_field(self, field_id, update_data):
        self._dry(f"would update custom field {field_id} with {sorted(update_data or {})}")
        return True

    def create_configuration_group(self, project_code, title):
        self._dry(f"[{project_code}] would create configuration group {title!r}")
        return next(self._fake_ids)

    def create_configuration(self, project_code, title, group_id):
        self._dry(f"[{project_code}] would create configuration {title!r} in group {group_id}")
        return next(self._fake_ids)

    def upload_attachment(self, code, attachment_data):
        name = None
        if isinstance(attachment_data, dict):
            name = attachment_data.get('name') or attachment_data.get('filename')
        name = name or 'unknown'
        self._dry(f"[{code}] would upload attachment {name!r}")
        # Callers read 'hash', 'filename' and 'url' off this, so the shape has
        # to match a real upload or the dry run fails where a real run would not
        fake_hash = f"dryrun{next(self._fake_ids)}"
        return {
            "hash": fake_hash,
            "filename": name,
            "url": f"https://dry-run.invalid/attachments/{fake_hash}",
        }
