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


def normalize(path: Path | None = None) -> bool:
    """Rewrite config.yaml through the schema so new keys gain their defaults.

    Loads the live config (schema.py fills in any keys the file omits) and writes
    the fully-populated model back. This is how a code update brings new features'
    config keys online on an already-provisioned node without an operator editing
    the file: the schema is the single source of truth for defaults, and this
    persists them. Operator-set values are preserved (they override defaults on
    load); only missing keys are added.

    Idempotent: returns True only if the on-disk content actually changed, so it
    is safe to run on every apply. Note the rewrite is schema-normalized YAML, so
    hand-written comments/ordering in the live file are not preserved (the repo
    config/config.yaml keeps the commented reference).
    """
    p = path or CONFIG_PATH
    before = p.read_text() if p.exists() else None
    cfg = load(p)
    save(cfg, p)
    return p.read_text() != before


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
