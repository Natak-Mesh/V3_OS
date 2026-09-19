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
