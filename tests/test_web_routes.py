"""nucleusd.api — web UI route regression tests.

The UI links to the extension-less path /voice (see web/app.js), but the app
serves static assets via StaticFiles, which only serves voice.html. Without an
explicit /voice route FastAPI returns 404 {"detail":"Not Found"}, which is what
the softPTT page surfaced. These tests pin that /voice (and /) serve the right
HTML so the regression can't return.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from nucleusd.api import app

client = TestClient(app)


def test_voice_route_serves_handset():
    r = client.get("/voice")
    assert r.status_code == 200
    # The soft-PTT handset, built on the standard V3 shell (not the old V2 port).
    assert 'id="ptt-btn"' in r.text
    assert 'id="shell"' in r.text
    assert '<link rel="stylesheet" href="cli.css">' in r.text


def test_index_route_serves_shell():
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="shell"' in r.text


def test_web_pages_have_no_flask_template_residue():
    """The web UI is served as static files, not Jinja/Flask templates. A ported
    page carrying `url_for(...)` or `{{ ... }}` renders those literally (broken
    CSS/asset links) — pin that no static page contains such residue."""
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    for f in web.glob("*.html"):
        text = f.read_text()
        assert "url_for" not in text, f"{f.name} contains Flask url_for()"
        assert "{{" not in text, f"{f.name} contains a Jinja expression"


def test_messaging_log_sticks_to_newest_message():
    """Regression: the messaging log must auto-scroll to the newest message.

    render() only followed the selected row, and the messaging page has no
    selectable rows, so pushed/polled messages landed below the fold and the
    page opened at the top. The fix marks the page `stickBottom` (app.js) and
    pins/forces the viewport to the bottom in render() (cli.js). Node isn't
    installed on this node, so we pin the wiring in the static assets rather
    than executing the JS.
    """
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    app_js = (web / "app.js").read_text()
    cli_js = (web / "cli.js").read_text()

    # The messaging page opts into stick-to-bottom behaviour.
    assert "stickBottom: true" in app_js, "messaging page lost stickBottom flag"

    # render() honours stickBottom: detect bottom, force a jump, pin the view.
    assert "stickBottom" in cli_js, "render() no longer reads stickBottom"
    assert "state.stickJump" in cli_js, "render() lost the forced-jump flag"
    assert "view.scrollTop = view.scrollHeight" in cli_js, \
        "render() no longer pins the viewport to the newest line"
