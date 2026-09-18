"""nucleusd — Nucleus V3 OS configuration daemon and control package."""

from pathlib import Path


def _read_version() -> str:
    """Single source of truth: the repo-root VERSION file.

    Falls back through the installed package metadata, then a sentinel, so an
    install without the file (e.g. a stripped wheel) never crashes on import.
    """
    vfile = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        return vfile.read_text().strip()
    except OSError:
        try:
            from importlib.metadata import version
            return version("nucleusd")
        except Exception:
            return "0.0.0"


__version__ = _read_version()

