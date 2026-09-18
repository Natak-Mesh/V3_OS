"""Config file I/O: load, validate, and atomically persist config.yaml.

Kept separate from schema.py so the schema stays pure (no filesystem). This is
the only module that reads/writes the on-disk YAML.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from .schema import NucleusConfig

# Live location on the node. The repo copy at config/config.yaml is the default
# shipped template; install.sh seeds this path from it on first install.
CONFIG_PATH = Path(os.environ.get("NUCLEUS_CONFIG", "/etc/nucleus/config.yaml"))


def load(path: Path | None = None) -> NucleusConfig:
    """Load and validate the config. Raises on missing file or invalid schema."""
    p = path or CONFIG_PATH
    with open(p, "r") as f:
        raw = yaml.safe_load(f) or {}
    return NucleusConfig.model_validate(raw)


def save(cfg: NucleusConfig, path: Path | None = None) -> None:
    """Atomically write config back to disk (validated model -> YAML).

    Atomic write (temp file + rename) so a crash mid-write can never leave a
    truncated config that would brick the next boot.
    """
    p = path or CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(mode="json", exclude_none=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
