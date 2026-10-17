"""Display-theme (classic / htop) — regression tests.

The theme is a pure client-side preference: a [data-theme] attribute on <html>
drives CSS variables (palette, font, base size). It is chosen on the NODE
SETTINGS page and stored in localStorage, applied before first paint so there is
no flash of the wrong palette. The Terminus console font is bundled (no runtime
download) and served by the same StaticFiles mount as the rest of the UI.

Node/JS isn't installed here, so the UI wiring is pinned in the static assets;
the font + licence are checked over the live HTTP routes.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from nucleusd.api import app

client = TestClient(app)

WEB = Path(__file__).resolve().parent.parent / "nucleusd" / "web"


def test_theme_variables_defined_in_css():
    css = (WEB / "cli.css").read_text()
    # Classic default palette + the htop override block both scope the tokens.
    assert ":root {" in css
    assert '[data-theme="htop"] {' in css
    # Font + size are tokenised so a theme can swap them, and the base rules use
    # the variables rather than a hardcoded font/size.
    assert "--font:" in css and "--fs:" in css
    assert "font-family: var(--font);" in css
    assert "font-size: calc(var(--fs) * var(--fscale));" in css


def test_all_font_sizes_scale_with_theme():
    """Every font-size must scale with --fscale. The Terminus bitmap font renders
    ~20% smaller than DejaVu at the same px, so the htop theme bumps --fscale to
    1.2; a bare `font-size: Npx` would ignore that and render too small (the
    original bug). Pin that no unscaled pixel font-size survives in either file."""
    import re
    bare = re.compile(r"font-size:\s*\d+px")
    for name in ("cli.css", "voice.html"):
        text = (WEB / name).read_text()
        offenders = bare.findall(text)
        assert not offenders, f"{name} has unscaled font-size(s): {offenders}"
    css = (WEB / "cli.css").read_text()
    assert "--fscale: 1;" in css          # classic unchanged
    assert "--fscale: 1.4;" in css        # htop bumped


def test_htop_theme_uses_bundled_terminus():
    css = (WEB / "cli.css").read_text()
    assert '@font-face' in css and 'font-family: "Terminus"' in css
    assert 'url("terminus.woff2")' in css
    assert '"Terminus"' in css.split('[data-theme="htop"] {', 1)[1]


def test_theme_picker_on_node_settings_page():
    app_js = (WEB / "app.js").read_text()
    assert 'const THEMES = ["classic", "htop"]' in app_js
    assert "function setTheme" in app_js and "function getTheme" in app_js
    assert 'localStorage.setItem("nucleus-theme"' in app_js
    # The picker is a field row inside the node-settings page.
    assert 'key: "theme"' in app_js


def test_theme_applied_before_paint_in_html():
    # Both standalone HTML entry points must set data-theme before the stylesheet
    # loads, so neither flashes the default palette on open.
    for name in ("index.html", "voice.html"):
        text = (WEB / name).read_text()
        pre = text.split('href="cli.css"', 1)[0]
        assert 'localStorage.getItem("nucleus-theme")' in pre, \
            f"{name} does not apply the theme before cli.css"
        assert "setAttribute(\"data-theme\"" in pre, f"{name} missing data-theme loader"


def test_terminus_font_and_licence_served():
    for path, ctype in (("/terminus.woff2", "font/woff2"),
                        ("/terminus-bold.woff2", "font/woff2")):
        r = client.get(path)
        assert r.status_code == 200, f"{path} not served"
        assert len(r.content) > 1000, f"{path} looks empty"
    # The OFL licence ships alongside the font.
    assert (WEB / "terminus-LICENSE.txt").exists()
