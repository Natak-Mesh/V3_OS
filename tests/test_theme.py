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
    assert 'const THEMES = ["classic", "htop", "day", "dark", "night"]' in app_js
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


# ── Readability themes (day / dark / night) ─────────────────────

READABLE = ("day", "dark", "night")
# Text/status tokens that are drawn as foreground on --bg-primary.
FG_TOKENS = ("--text-primary", "--text-secondary", "--ok", "--off", "--warn", "--accent")


def _theme_vars(css: str, name: str) -> dict:
    import re
    block = css.split(f'[data-theme="{name}"] {{', 1)[1].split("}", 1)[0]
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


def _contrast(a: str, b: str) -> float:
    """WCAG 2.x contrast ratio between two #rrggbb colours."""
    def lum(h):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b_ = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b_)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_readable_themes_defined_with_atkinson_font():
    css = (WEB / "cli.css").read_text()
    for name in READABLE:
        assert f'[data-theme="{name}"] {{' in css, f"{name} theme block missing"
        block = css.split(f'[data-theme="{name}"] {{', 1)[1].split("}", 1)[0]
        assert '"Atkinson Hyperlegible Mono"' in block, f"{name} not using Atkinson"
        # 15px * 1.1 = 16.5px base: the >=16px body-text floor.
        assert "--fscale: 1.1;" in block


def test_readable_theme_contrast_floors():
    """day/dark: every foreground token >= 7:1 (WCAG AAA) on the page and card
    background. night: >= 4.5:1 (AA) — pure red tops out ~5.25:1 on black, so AAA
    is unreachable without leaving the red band that preserves night vision."""
    css = (WEB / "cli.css").read_text()
    for name, floor in (("day", 7.0), ("dark", 7.0), ("night", 4.5)):
        v = _theme_vars(css, name)
        for tok in FG_TOKENS:
            for bg in ("--bg-primary", "--bg-card"):
                ratio = _contrast(v[tok], v[bg])
                assert ratio >= floor, f"{name} {tok} on {bg}: {ratio:.2f} < {floor}"
        # Selected rows draw --on-accent text on an --accent fill.
        assert _contrast(v["--on-accent"], v["--accent"]) >= floor
        # Editing rows draw --text-primary on --bg-edit.
        assert _contrast(v["--text-primary"], v["--bg-edit"]) >= floor


def test_night_theme_is_red_band_only():
    """Night must emit no blue/green-dominant light: every colour's red channel
    dominates, so nothing bleaches dark adaptation."""
    css = (WEB / "cli.css").read_text()
    for tok, hexv in _theme_vars(css, "night").items():
        r, g, b = (int(hexv[i:i + 2], 16) for i in (1, 3, 5))
        assert r >= g and r >= b, f"night {tok}={hexv} is not red-dominant"
    assert '[data-theme="night"] .qr' in css, "night must dim the white QR card"


def test_atkinson_font_and_licence_served():
    css = (WEB / "cli.css").read_text()
    assert 'url("atkinson-mono.woff2")' in css
    assert 'url("atkinson-mono-bold.woff2")' in css
    for path in ("/atkinson-mono.woff2", "/atkinson-mono-bold.woff2"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} not served"
        assert r.content[:4] == b"wOF2", f"{path} is not a woff2 file"
    assert (WEB / "atkinson-mono-LICENSE.txt").exists()
