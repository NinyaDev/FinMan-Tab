"""
Loads config.yaml at startup and exposes it as a dict.
"""

import logging
from pathlib import Path
import yaml
CONFIG_FILE = Path("config.yaml")
log = logging.getLogger(__name__)

def _load_config():
    if not CONFIG_FILE.exists():
        log.error(f"{CONFIG_FILE} not found.")
        raise SystemExit(1)
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f)
CONFIG = _load_config()