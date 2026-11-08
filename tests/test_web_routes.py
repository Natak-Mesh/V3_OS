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


def test_messaging_rns_routes_degrade_cleanly():
    """The RNS direct-message routes relay to the messaging daemon's control
    socket. They must never 500: either the daemon answers (200) or it's
    unreachable (503) — but not an unhandled error. Response shape isn't pinned
    here because the daemon wherever the suite runs may be absent, an older
    snapshot, or the current build."""
    assert client.get("/api/v1/messaging/rns/status").status_code in (200, 503)
    assert client.get("/api/v1/messaging/rns/contacts").status_code in (200, 503)
    assert client.get("/api/v1/messaging/rns/card").status_code in (200, 503)
    # Per-contact path status (read-only) and the explicit path-request POST.
    assert client.get("/api/v1/messaging/rns/contacts/abc/path").status_code in (200, 503)
    assert client.post("/api/v1/messaging/rns/contacts/abc/path").status_code in (200, 400, 503)
    # Rename (nickname) and remove a contact — both relay cleanly.
    assert client.patch("/api/v1/messaging/rns/contacts/abc",
                        json={"name": "x"}).status_code in (200, 400, 503)
    assert client.delete("/api/v1/messaging/rns/contacts/abc").status_code in (200, 400, 503)
    assert client.get("/api/v1/messaging/rns/latest").status_code in (200, 503)


def test_reticulum_nodes_route_removed():
    """The announce-driven /reticulum/nodes endpoint is gone — discovery moved to
    the contact book at /messaging/rns/contacts."""
    assert client.get("/api/v1/reticulum/nodes").status_code == 404


def test_modem73_page_wires_ofdm_mode():
    """The modem73 config page must expose the OFDM mode selector and persist it.

    Node isn't installed on this node, so pin the wiring in the static app.js
    rather than executing the JS: the page loads modem73_mode into its working
    copy, renders an 'OFDM mode' dropdown whose options come from the schema
    contract (enum + per-mode bitrate), and saveModem73 writes the key back.
    """
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    app_js = (web / "app.js").read_text()
    # Working copy loads the current mode from the config.
    assert "mode: r.modem73_mode," in app_js, "modem73 page lost the mode load"
    # Dropdown is schema-driven (enum + bitrate from the published `modes` meta),
    # not a hardcoded rate table in the frontend.
    assert 'label: "OFDM mode"' in app_js, "no OFDM mode dropdown on the page"
    assert "P.modem73_mode" in app_js, "mode dropdown isn't driven by the schema"
    assert "mo.modes" in app_js, "mode labels don't read bitrate from the contract"
    # Save persists the selected mode.
    assert "cfg.reticulum.modem73_mode = MO.mode;" in app_js, \
        "saveModem73 no longer persists the OFDM mode"


def test_rns_direct_message_ui_wired():
    """The Reticulum landing page IS the contact list (NomadNet-style): each saved
    contact opens a per-peer conversation and carries an inline req-path button.
    Pin that wiring in the static assets (no Node here)."""
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    app_js = (web / "app.js").read_text()
    cli_js = (web / "cli.js").read_text()
    reti_block = app_js[app_js.index("  reticulum: {"):app_js.index("  rns_node: {")]
    assert "/rns/contacts" in reti_block, "landing page no longer lists contacts"
    assert 'S.go("rns_chat"' in reti_block, "contact row no longer opens a conversation"
    # Explicit path discovery stays operator-triggered, inline on the row.
    assert "onRun: (S) => rnsRequestPath(S, c.lxmf_hash)" in reti_block, \
        "req-path button no longer inline on the contact row"
    assert "rns_chat:" in app_js, "conversation page missing"
    assert "function rnsSend" in app_js, "RNS send handler removed"
    assert "function rnsAddHash" in app_js, "add-by-destination-hash not wired"
    assert "function rnsRequestPath" in app_js, "path request handler removed"
    assert '/path"' in app_js, "contact path endpoint not wired"
    assert 'obj.event === "rns_message"' in app_js, "WS no longer routes rns_message"
    # The shell must support param-passing navigation for the per-peer page.
    assert "go(pageKey, params = null)" in cli_js, "S.go param navigation removed"


def test_rns_landing_shows_only_saved_contacts():
    """Heard announces must NOT be listed on the landing page: an internet-
    connected rnsd could flood it. Only a count + link to the Network page."""
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    app_js = (web / "app.js").read_text()
    reti_block = app_js[app_js.index("  reticulum: {"):app_js.index("  rns_node: {")]
    assert "rnsHeardAdd" not in reti_block, "heard announces listed on the landing page"
    assert "heard.forEach" not in reti_block, "heard announces listed on the landing page"
    assert 'to: "rns_heard"' in reti_block, "no link to the Network page"
    assert 'to: "rns_add"' in reti_block and 'to: "rns_node"' in reti_block


def test_rns_landing_has_my_name_and_announce():
    """My name (renamable) + announce live in the landing-page header."""
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    app_js = (web / "app.js").read_text()
    reti_block = app_js[app_js.index("  reticulum: {"):app_js.index("  rns_node: {")]
    assert "rnsSetMyName(S, me)" in reti_block, "no rename-me on the landing page"
    assert "onRun: rnsAnnounce" in reti_block, "no announce on the landing page"
    assert '"/api/v1/messaging/name"' in app_js, "rename not wired to the name API"


def test_rns_contact_admin_lives_on_contact_page():
    """Rename/remove are contact admin, not messaging: they live on the
    per-contact page (rns_contact), reached from the conversation, never on the
    message log itself. The old Modify Contacts / Direct Messages pages are gone."""
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    app_js = (web / "app.js").read_text()
    chat_block = app_js[app_js.index("  rns_chat: {"):app_js.index("  rns_contact: {")]
    assert "rnsRename" not in chat_block, "rename button back on the message log"
    assert "rnsRemove" not in chat_block, "remove button back on the message log"
    assert 'to: "rns_contact"' in chat_block, "conversation has no link to its contact page"
    contact_block = app_js[app_js.index("  rns_contact: {"):app_js.index("  voice: {")]
    assert "rnsRename(S, peer" in contact_block, "rename not on the contact page"
    assert "rnsRemove(S, peer" in contact_block, "remove not on the contact page"
    for gone in ("rns_nodes:", "rns_edit:", "rns_ifaces:"):
        assert gone not in app_js, f"obsolete page {gone} still defined"


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
