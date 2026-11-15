"""Per-page web UI help (`?` panel) from the operator manual.

The manual (docs/manual/*.md) is the only source of the help text: install.sh
copies it to /opt/nucleus/manual, nucleusd/manual.py extracts each page's
`## Quick help` (or a named `##` section) and the shell shows it. These tests
use the real manual files as fixtures, so a renamed/removed section or a page
`help` key pointing nowhere fails here instead of showing "help not installed"
on a node.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nucleusd import api, manual

ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / "docs" / "manual"
WEB = ROOT / "nucleusd" / "web"
APP_JS = (WEB / "app.js").read_text()
CLI_JS = (WEB / "cli.js").read_text()

client = TestClient(api.app)


def _page_help_keys() -> dict:
    """{page key: help value} for every PAGES entry in app.js."""
    return dict(re.findall(
        r'^  ([a-z_0-9]+): \{\n    title: [^\n]+\n    help: "([^"]+)",', APP_JS, re.M))


def _page_keys() -> list:
    return re.findall(r'^  ([a-z_0-9]+): \{\n    title: ', APP_JS, re.M)


@pytest.fixture
def installed(monkeypatch):
    """Point the API at the repo manual, as install.sh would copy it."""
    monkeypatch.setattr(manual, "MANUAL_DIR", MANUAL)


# --- every UI page has help, and it resolves against the real manual ---------

def test_every_page_declares_help():
    pages = _page_keys()
    helps = _page_help_keys()
    assert len(pages) > 20
    missing = [p for p in pages if p not in helps]
    assert not missing, f"pages without a help key: {missing}"


@pytest.mark.parametrize("page,key", sorted(_page_help_keys().items()))
def test_page_help_resolves_in_manual(page, key):
    name, _, heading = key.partition("#")
    path = MANUAL / f"{name}.md"
    assert path.is_file(), f"{page}: help → missing docs/manual/{name}.md"
    body = manual.section(path.read_text(), heading or manual.QUICK_HELP)
    assert body, f"{page}: docs/manual/{name}.md has no '## {heading or 'Quick help'}'"


def test_quick_help_stays_lightweight():
    """Quick help is the short version — the full page is the manual."""
    for path in MANUAL.glob("*.md"):
        body = manual.section(path.read_text())
        if body is not None:
            assert len(body.splitlines()) <= 15, f"{path.name} Quick help too long"


# --- section extraction ------------------------------------------------------

def test_section_stops_at_next_h2_and_ignores_fenced_hashes():
    text = (
        "# Title\n\nintro\n\n## Quick help\n\n- one\n\n```bash\n# not a heading\n"
        "## nor this\n```\nafter fence\n\n## Next\n\nother\n"
    )
    body = manual.section(text)
    assert body.startswith("- one")
    assert "# not a heading" in body and "after fence" in body
    assert "other" not in body
    assert manual.section(text, "Next") == "other"
    assert manual.section(text, "Absent") is None
    assert manual.title(text) == "Title"


def test_section_from_real_monitor_page():
    text = (MANUAL / "monitor.md").read_text()
    qh = manual.section(text)
    assert "Mesh routes" in qh and "## Radio-off alerts" not in qh
    lt = manual.section(text, "Link test")
    assert "PING" in lt and "THROUGHPUT" in lt


# --- API -----------------------------------------------------------------------

def test_api_returns_quick_help(installed):
    r = client.get("/api/v1/manual/monitor")
    assert r.status_code == 200
    d = r.json()
    assert d["page"] == "monitor"
    assert d["title"].startswith("Monitor page")
    assert "Mesh routes" in d["markdown"]


def test_api_returns_named_section(installed):
    r = client.get("/api/v1/manual/monitor", params={"section": "Link test"})
    assert r.status_code == 200
    assert "TRACEROUTE" in r.json()["markdown"]


@pytest.mark.parametrize("page", ["nope", "..%2Fapi", "..", "monitor.md", "a.b"])
def test_api_404_for_unknown_or_unsafe_page(installed, page):
    assert client.get(f"/api/v1/manual/{page}").status_code == 404


def test_api_404_for_absent_section(installed):
    r = client.get("/api/v1/manual/monitor", params={"section": "Nope"})
    assert r.status_code == 404


def test_api_404_when_manual_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(manual, "MANUAL_DIR", tmp_path)
    assert client.get("/api/v1/manual/monitor").status_code == 404


def test_valid_page_rejects_traversal():
    for bad in ("../etc/passwd", "a/b", "monitor.md", "", ".."):
        assert not manual.valid_page(bad)
    assert manual.valid_page("README") and manual.valid_page("eth0-mesh-relay")


# --- install + shell wiring -----------------------------------------------------

def test_install_sh_syncs_manual():
    sh = (ROOT / "install.sh").read_text()
    assert 'rm -rf "$OPT/manual"' in sh, "stale manual pages would linger"
    assert '"$REPO"/docs/manual/*.md "$OPT/manual/"' in sh
    assert str(manual.MANUAL_DIR) == "/opt/nucleus/manual"


def test_shell_help_control_wired():
    html = (WEB / "index.html").read_text()
    assert 'id="c-help"' in html
    assert 'getElementById("c-help")' in CLI_JS
    assert 'cHelp.addEventListener("click", toggleHelp)' in CLI_JS
    assert 'case "h": case "?":' in CLI_JS
    assert '"/api/v1/manual/"' in CLI_JS
    assert "help-panel" in CLI_JS and ".content.help-panel" in (WEB / "cli.css").read_text()
    # Leaving a page closes its help; BACK closes help before navigating.
    assert "state.help = false;\n    stopTimer();" in CLI_JS
    assert "if (state.help) { state.help = false; render(true); return; }" in CLI_JS


def test_config_quick_help_explains_commonly_changed_settings():
    """Regression: config `?` help only covered Save/Apply, not what the
    non-obvious settings do. Each commonly changed field (label as shown in
    app.js) gets a plain note in config.md's Quick help."""
    body = manual.section((MANUAL / "config.md").read_text())
    cfg_page = APP_JS[APP_JS.index("  config: {"):APP_JS.index("  update: {")]
    for label in ["Babel routing", "ATAK mcast mode", "Relay source node IDs",
                  "mcast TTL", "CoT bridge", "Allow web UI over ethernet"]:
        assert f'label: "{label}' in cfg_page, f"{label} not on the config page"
        assert f"**{label}**" in body, f"config Quick help has no note for {label}"
    assert "**eth0 Mode**" in body
    # TX rate limit lives on its own page; its ? shows that manual section.
    assert _page_help_keys()["mt_txrate"] == "meshtastic#TX rate limit"


def test_help_button_in_header_not_control_bar():
    """Regression: ? in the control bar split BACK and made the bottom controls
    uneven on phones. It belongs in the header; the bar keeps exactly
    ▲ ENTER ▼ BACK in the original even 3-column grid."""
    html = (WEB / "index.html").read_text()
    css = (WEB / "cli.css").read_text()
    hdr = re.search(r'<div id="hdr">(.*?)\n    </div>', html, re.S).group(1)
    ctrl = re.search(r'<div id="ctrl">(.*?)\n    </div>', html, re.S).group(1)
    assert 'id="c-help"' in hdr
    assert 'id="c-help"' not in ctrl
    assert re.findall(r'<button id="([^"]+)"', ctrl) == ["c-up", "c-enter", "c-down", "c-back"]
    assert "has-help" not in html and "has-help" not in css
    assert 'grid-template-areas: "up enter down" "up back down";' in css


def test_help_markdown_is_escaped_before_formatting():
    """Manual text goes through escHtml before the inline markup is added."""
    inline = CLI_JS[CLI_JS.index("function mdInline"):CLI_JS.index("function mdToHtml")]
    assert inline.index("escHtml(t)") < inline.index("<code>")
