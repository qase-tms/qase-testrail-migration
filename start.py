# Usage: python start.py [config.json] [--dry-run]
#   --dry-run  read everything from TestRail and report exactly what would be
#              created in Qase, without writing anything.
import os
import sys

from src import TestRailImporter, TestRailImporterSync
from src.support.config_manager import ConfigManager
from src.support.logger import Logger

_dry_run = '--dry-run' in sys.argv[1:] or bool(str(os.environ.get('QASE_DRY_RUN') or '').strip())
_args = [a for a in sys.argv[1:] if a != '--dry-run']
_config_path = _args[0] if _args else './config.json'

config = ConfigManager(config_file=_config_path)
config.load_config()

prefix = config.get('prefix') or ''

logger = Logger(
    level=config.get('logging.level'),
    write_to_file=config.get('logging.write_to_file') is not False,
    log_dir=config.get('logging.dir') or './logs',
    prefix=prefix,
)

if config.get('testrail.sync'):
    importer = TestRailImporterSync(config, logger, dry_run=_dry_run)
else:
    importer = TestRailImporter(config, logger, dry_run=_dry_run)

importer.start()
