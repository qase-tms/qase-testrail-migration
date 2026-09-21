"""Preflight check: validate config and connectivity BEFORE running a migration.

Run:  python preflight.py [config.json]

Checks, in order:
  1. Config file exists and parses
  2. Required keys are present and are not still placeholders
  3. Every key the code reads is covered by the config
  4. Project selection would actually select something
  5. TestRail auth works, and every project in projects.import exists there
  6. Qase auth works, and users.default resolves to a real Qase user

Exit code 0 means every check passed, 1 means at least one failed.
Nothing is written to Qase or TestRail. This is read-only.
"""

import json
import os
import sys

from src.support.config_manager import ConfigManager
from src.support.logger import Logger

# STANDARD.md section 2: Python 3.11 minimum. asyncio.TaskGroup is used by the
# entity importers and does not exist before 3.11; 3.10 reaches end of life in
# October 2026. Fail here rather than partway into a run.
if sys.version_info < (3, 11):
    sys.exit(
        f"This migration requires Python 3.11 or newer "
        f"(found {sys.version_info.major}.{sys.version_info.minor})."
    )


_PLACEHOLDER_MARKERS = ("<", ">", "your-", "YOUR_", "changeme", "xxxx", "example.com")

_results = []


def _report(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))
    _results.append(ok)


def _warn(name, detail=""):
    print(f"  WARN  {name}" + (f": {detail}" if detail else ""))


def _looks_placeholder(value):
    return any(marker in str(value) for marker in _PLACEHOLDER_MARKERS)


def _finish():
    print()
    failed = _results.count(False)
    if failed:
        print(f"{failed} check(s) failed. Fix the above before running start.py.")
        return 1
    print("All checks passed. Safe to run: python start.py")
    return 0


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "./config.json"

    print("\n--- Config ---")
    if not os.path.exists(config_path):
        _report(f"Config file {config_path}", False, "not found, copy config.example.json to config.json")
        return _finish()
    try:
        with open(config_path) as f:
            json.load(f)
    except json.JSONDecodeError as e:
        _report(f"Config file {config_path}", False, f"invalid JSON: {e}")
        return _finish()
    _report(f"Config file {config_path}", True, "parses")

    config = ConfigManager(config_file=config_path)
    config.load_config()

    required = {
        "qase.api_token": "Qase API token",
        "testrail.api.host": "TestRail instance URL",
        "testrail.api.user": "TestRail user email",
        "testrail.api.token": "TestRail API key",
        "users.default": "fallback Qase user",
    }
    config_ok = True
    for key, label in required.items():
        value = str(config.get(key) or "").strip()
        if not value or _looks_placeholder(value):
            _report(f"{key} ({label})", False, "missing or still a placeholder")
            config_ok = False
        else:
            _report(f"{key} ({label})", True)

    if config.get("users.create") and not str(config.get("qase.scim_token") or "").strip():
        _report("qase.scim_token", False, "users.create is true, which needs a SCIM token")
        config_ok = False

    level = str(config.get("logging.level") or "info").lower()
    if level not in Logger.LEVELS:
        _report("logging.level", False, f"{level!r} is not one of {', '.join(Logger.LEVELS)}")
        config_ok = False

    print("\n--- Project selection ---")
    import_all = bool(config.get("projects.import_all"))
    to_import = [str(p).strip() for p in (config.get("projects.import") or []) if str(p).strip()]
    real = [p for p in to_import if not _looks_placeholder(p)]
    if import_all:
        _report("projects.import_all", True, "every TestRail project will be migrated")
    elif real:
        _report("projects.import", True, f"{real}")
    else:
        _report("projects.import", False,
                "import_all is false and no real project names are listed, so nothing would be migrated")
        config_ok = False

    status = config.get("testrail.project_status")
    if status and status not in ("all", "active", "completed"):
        _report("testrail.project_status", False, f"{status!r} is not 'all', 'active' or 'completed'")
        config_ok = False

    if not config_ok:
        print("\nSkipping connectivity checks until the config is valid.")
        return _finish()

    logger = Logger(level="error", write_to_file=False)

    print("\n--- TestRail ---")
    try:
        from src.service import TestrailService
        testrail = TestrailService(config, logger)
        result = testrail.get_projects(limit=250, offset=0)
        projects = result.get("projects", result) if isinstance(result, dict) else result
        names = [p.get("name") for p in projects]
        _report("TestRail authentication", True, f"{len(names)} project(s) visible")

        if not import_all:
            for wanted in real:
                if wanted in names:
                    _report(f"Project {wanted!r}", True, "found in TestRail")
                else:
                    _report(f"Project {wanted!r}", False, "not found in TestRail, check the exact name")
    except Exception as e:
        _report("TestRail authentication", False, f"{type(e).__name__}: {e}")
        return _finish()

    print("\n--- Qase ---")
    try:
        from src.service import QaseService
        qase = QaseService(config, logger)
        default = config.get("users.default")
        user_id = qase.resolve_user_id(default)
        _report("Qase authentication", True)
        _report(f"users.default ({default})", True, f"resolves to user id {user_id}")
    except Exception as e:
        _report("Qase authentication", False, f"{type(e).__name__}: {e}")

    return _finish()


if __name__ == "__main__":
    sys.exit(main())
