"""Operator manual lookup for the web UI's per-page help panel.

The manual (docs/manual/*.md) is the single source of the help text. install.sh
copies it to MANUAL_DIR; each manual page carries a short `## Quick help`
section near the top, and the UI's `?` control shows only that section.

Pure text handling plus one file read — no other filesystem/hardware access.
"""

from __future__ import annotations

import re
from pathlib import Path

# install.sh syncs docs/manual/*.md here (cleared first, so removed pages go).
MANUAL_DIR = Path("/opt/nucleus/manual")

QUICK_HELP = "Quick help"

# Manual page names are bare file stems (e.g. "monitor", "README"): no dots or
# slashes, so a request can never leave MANUAL_DIR.
_PAGE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def valid_page(page: str) -> bool:
    return bool(_PAGE_RE.match(page or ""))


def section(text: str, heading: str = QUICK_HELP) -> str | None:
    """Body of the `## <heading>` section: up to the next `#`/`##` heading.

    Headings inside fenced code blocks (shell comments like `# 1. Stop ...`)
    are ignored. Returns None if the section is absent.
    """
    out: list[str] = []
    inside = False
    fenced = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced and re.match(r"^#{1,2} ", line):
            if inside:
                break
            if line[3:].strip() == heading and line.startswith("## "):
                inside = True
                continue
        if inside:
            out.append(line)
    if not inside:
        return None
    return "\n".join(out).strip()


def title(text: str) -> str:
    """The manual page's `# ` title, or ""."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def quick_help(page: str, heading: str | None = None,
               base: Path | None = None) -> dict | None:
    """{page, title, markdown} for one manual page's help, else None.

    `heading` selects another `## ` section of the same page instead of Quick
    help (detail pages such as the link test point at their own section).
    """
    if not valid_page(page):
        return None
    path = (base or MANUAL_DIR) / f"{page}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    body = section(text, heading or QUICK_HELP)
    if body is None:
        return None
    return {"page": page, "title": title(text), "markdown": body}
