"""FastAPI app: REST config API + self-served web UI.

One always-on process serves both the machine contract (/api/v1/...) and the
human UI (mounted at /). The web UI is static assets that call the same API, so
there is exactly one implementation of every operation.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import config as cfgio
from . import status as statusmod
from . import update as updatemod
from .apply import apply as apply_config
from .schema import NucleusConfig

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="Nucleus V3 OS", version=__version__)

# State-changing methods that a cross-site page could trigger in a logged-in /
# trusted client's browser. GET/HEAD/OPTIONS are exempt (they read only).
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@app.middleware("http")
async def _reject_cross_origin_writes(request: Request, call_next):
    """CSRF guard: block browser mutations coming from another origin.

    A browser always attaches an `Origin` header on a cross-site POST/PUT (and on
    same-site ones for these methods too). If it is present and does not match the
    host the request was addressed to, the request came from a different site, so
    we refuse it. Non-browser clients (curl, scripts, ATAK, peer nodes) send no
    Origin and are unaffected, so the machine API keeps working unchanged.
    """
    if request.method in _MUTATING_METHODS:
        origin = request.headers.get("origin")
        if origin:
            origin_host = urlparse(origin).hostname or ""
            # Host header without any :port (nginx forwards the original Host).
            target_host = (request.headers.get("host") or "").rsplit(":", 1)[0]
            if origin_host != target_host:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "cross-origin request refused"},
                )
    return await call_next(request)

# Meshtastic radio configurator + CoT bridge status/control endpoints.
try:
    from .meshtastic.router import router as meshtastic_router
    app.include_router(meshtastic_router)
except Exception:  # meshtastic deps optional off-box / on non-radio nodes
    pass

# Text messaging (WiFi + LoRa) endpoints — thin layer over the messaging daemon.
try:
    from .messaging.router import router as messaging_router
    app.include_router(messaging_router)
except Exception:
    pass

# Voice status/channel endpoints — thin layer over the nucleus-voice daemon's
# control socket. Audio stays on the daemon's own WebSocket (/voice-ws).
try:
    from .voice.router import router as voice_router
    app.include_router(voice_router)
except Exception:
    pass

# Tailscale connection control (on/off, tailnet switch) — thin layer over the
# tailscale CLI. Outside the config pipeline (see nucleusd/tailscale.py).
try:
    from .tailscale_router import router as tailscale_router
    app.include_router(tailscale_router)
except Exception:
    pass


@app.get("/api/v1/version")
def get_version() -> dict:
    """Report the running Nucleus OS version (from the VERSION file)."""
    return {"version": __version__}


# Sent in place of the real web password on read; echoed back unchanged means
# "keep the stored password" on write (see put_config).
_PASSWORD_PLACEHOLDER = ""


@app.get("/api/v1/config")
def get_config() -> dict:
    """Return the current validated config plus derived values.

    The web UI password is redacted: the raw API must not hand out the eth0
    login secret to every trusted-network client that reads the config. The
    derived htpasswd line (a SHA-1 hash of it) is dropped for the same reason.
    """
    cfg = cfgio.load()
    data = cfg.model_dump(mode="json")
    if "web" in data and "password" in data["web"]:
        data["web"]["password"] = _PASSWORD_PLACEHOLDER
    derived = cfg.render_context()
    derived.pop("web_htpasswd", None)
    data["_derived"] = derived
    return data


@app.put("/api/v1/config")
def put_config(new: dict) -> dict:
    """Validate and persist a full replacement config. Does NOT apply.

    Kept separate from apply so a client can stage config and apply atomically
    (or review a dry-run first).

    Because GET redacts the web password, a client that round-trips the config
    sends back a blank one. A blank/absent web password here means "unchanged":
    we fill in the currently-stored value before validating, so a save never
    silently wipes the password.
    """
    web = new.get("web")
    if isinstance(web, dict) and not web.get("password"):
        try:
            web["password"] = cfgio.load().web.password
        except Exception:
            pass  # no valid current config; let schema defaults/validation run
    try:
        cfg = NucleusConfig.model_validate(new)
    except Exception as e:  # pydantic ValidationError -> 422
        raise HTTPException(status_code=422, detail=str(e))
    cfgio.save(cfg)
    return {"ok": True, "hostname": cfg.node.hostname}


@app.post("/api/v1/apply")
def post_apply(dry_run: bool = False) -> dict:
    """Render templates and (re)start affected units from the saved config."""
    cfg = cfgio.load()
    result = apply_config(cfg, dry_run=dry_run)
    return {
        "dry_run": result.dry_run,
        "changed": result.changed,
        "units_restarted": result.units_restarted,
    }


@app.get("/api/v1/status")
def get_status() -> dict:
    """Live runtime status: interfaces, services, Babel neighbours."""
    return statusmod.collect()


# --- TAK Server (optional; provisioned by nucleus-tak-setup.sh) --------------
# Outside the config pipeline like tailscale: presence is detected live, and the
# cert downloads serve the files nucleus-tak-setup.sh staged.
TAK_CERT_DIR = Path("/opt/nucleus/tak-certs")
TAK_DIR = Path("/opt/tak")


@app.get("/api/v1/tak/status")
def get_tak_status() -> dict:
    """TAK Server presence + service state + downloadable certs."""
    import subprocess
    installed = TAK_DIR.is_dir()
    service = "not installed"
    if installed:
        r = subprocess.run(["systemctl", "is-active", "takserver.service"],
                           capture_output=True, text=True, check=False)
        service = r.stdout.strip() or "unknown"
    certs = sorted(p.name for p in TAK_CERT_DIR.glob("*.p12")) \
        if TAK_CERT_DIR.is_dir() else []
    return {"installed": installed, "service": service, "certs": certs}


@app.get("/api/v1/tak/certs/{name}")
def get_tak_cert(name: str) -> FileResponse:
    """Download a staged TAK cert (webadmin.p12 / intermediate truststore)."""
    # Basename-only: no path traversal out of the staging dir.
    path = TAK_CERT_DIR / Path(name).name
    if not path.is_file() or path.suffix != ".p12":
        raise HTTPException(status_code=404, detail="no such cert")
    return FileResponse(str(path), media_type="application/x-pkcs12",
                        filename=path.name)


@app.get("/api/v1/update/check")
def get_update_check() -> dict:
    """Compare the installed (running) version with the git remote."""
    return updatemod.version_info()


@app.post("/api/v1/update/start")
def post_update_start() -> dict:
    """Launch the node update in the background (detached via systemd-run)."""
    return updatemod.start_update()


@app.get("/api/v1/update/progress")
def get_update_progress() -> dict:
    """Stream update status + log from the on-disk state files."""
    return updatemod.progress()


# --- Web UI (mounted last so it doesn't shadow /api routes) -----------------
if WEB_DIR.exists():
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(WEB_DIR / "index.html"))

    # The UI links to /voice (extension-less); StaticFiles won't serve that
    # without .html, so map it explicitly. Defined before the mount so it isn't
    # shadowed.
    @app.get("/voice")
    def voice_page() -> FileResponse:
        return FileResponse(str(WEB_DIR / "voice.html"))

    app.mount("/", StaticFiles(directory=str(WEB_DIR)), name="web")
