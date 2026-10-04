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


def test_messaging_log_stays_pinned_when_keyboard_opens():
    """Regression: focusing the compose box on a phone opens the keyboard, which
    shrinks/pans the viewport without a re-render, so the newest messages fell
    out of view above the text entry box. cli.js must re-pin stickBottom pages
    on viewport resize and on compose focus."""
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    cli_js = (web / "cli.js").read_text()
    assert "function repinLog()" in cli_js, "keyboard re-pin handler removed"
    assert 'window.addEventListener("resize", repinLog)' in cli_js, \
        "no re-pin on window resize"
    assert 'visualViewport.addEventListener("resize", repinLog)' in cli_js, \
        "no re-pin on visualViewport resize (mobile keyboard)"
    assert '"focusin"' in cli_js, "no re-pin when the compose input takes focus"


def test_messaging_log_is_bottom_anchored():
    """Regression: a short log sat at the TOP of the viewport, so when the phone
    keyboard pushed the page up the messages went out of view. stickBottom pages
    must anchor content to the bottom, directly above the compose box."""
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    cli_js = (web / "cli.js").read_text()
    cli_css = (web / "cli.css").read_text()
    assert 'view.classList.toggle("stick-bottom", !!stick)' in cli_js, \
        "render() no longer marks stickBottom pages"
    assert "#view.stick-bottom > :first-child { margin-top: auto; }" in cli_css, \
        "log is no longer bottom-anchored"


def test_reticulum_nodes_route_degrades_cleanly():
    """GET /api/v1/reticulum/nodes must return an empty list (200) rather than
    erroring when the messaging daemon / RNS lane isn't reachable — the UI
    renders a clean 'none discovered' state from it."""
    r = client.get("/api/v1/reticulum/nodes")
    assert r.status_code == 200
    assert r.json() == {"nodes": []}


def test_messaging_rns_routes_degrade_cleanly():
    """The RNS direct-message routes relay to the messaging daemon's control
    socket. They must never 500: either the daemon answers (200) or it's
    unreachable (503) — but not an unhandled error. Response shape isn't pinned
    here because the daemon wherever the suite runs may be absent, an older
    snapshot, or the current build."""
    assert client.get("/api/v1/messaging/rns/status").status_code in (200, 503)
    assert client.get("/api/v1/messaging/rns/peers").status_code in (200, 503)


def test_rns_direct_message_ui_wired():
    """The messaging page links to the Reticulum node list, which opens a
    per-peer conversation; pin that wiring in the static assets (no Node here)."""
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    app_js = (web / "app.js").read_text()
    cli_js = (web / "cli.js").read_text()
    # The Direct Messages link lives on the Reticulum page (not Messaging, whose
    # stickBottom log auto-scrolls and makes the link hard to click).
    reti_block = app_js[app_js.index("reticulum: {"):app_js.index("meshtastic: {")]
    assert 'to: "rns_nodes"' in reti_block, "Reticulum page lost the Direct Messages link"
    msg_block = app_js[app_js.index("messaging: {"):app_js.index("rns_nodes:")]
    assert 'to: "rns_nodes"' not in msg_block, "Direct Messages link should not be on the Messaging page"
    assert "rns_nodes:" in app_js and "rns_chat:" in app_js, "RNS pages missing"
    assert 'S.go("rns_chat"' in app_js, "node list no longer opens a conversation"
    assert "function rnsSend" in app_js, "RNS send handler removed"
    assert 'obj.event === "rns_message"' in app_js, "WS no longer routes rns_message"
    # The shell must support param-passing navigation for the per-peer page.
    assert "go(pageKey, params = null)" in cli_js, "S.go param navigation removed"


def test_peer_join_is_a_selectable_item():
    """Regression: the peer Join action was an inline <button onclick> injected
    into a `content` block, which the shell cursor skips (and any rebuild wiped).
    Joinable peers must be rendered as shell `button` items instead."""
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    app_js = (web / "app.js").read_text()
    assert 'onclick="joinPeer' not in app_js, "Join is an unselectable inline button again"
    assert "peers-slot" not in app_js, "peer results injected into DOM instead of items"
    assert "...peerItems()" in app_js, "meshtastic page no longer renders peer items"
    assert "onEnter: (S) => joinPeer(S, p.ip)" in app_js, "Join button item lost"
