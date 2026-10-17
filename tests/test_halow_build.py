"""nucleus-halow-build.sh stamp fast-path (docs/halow.md §10.8).

Pure shell-logic test: a matching stamp must make the script a no-op (no apt, no
git, no dkms), and a mismatching/absent stamp must proceed past the fast path.
We don't run a real build — we point PATH at stubs and assert the early exit, so
these are safe off-box and need no network/root.
"""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "system/bin/nucleus-halow-build.sh"

# The stamp string the script computes (must mirror the tags in the script).
TAGS = ("driver=mm8108-2.1.0 firmware=mm8108-2.0.0 "
        "hostap=mm8108-2.1.0 cli=mm8108-2.1.0")


def _stub_env(tmp_path):
    """PATH with stub apt-get/git/dkms/depmod that fail loudly if ever called."""
    bind = tmp_path / "bin"
    bind.mkdir()
    for tool in ("apt-get", "git", "dkms", "depmod", "make", "install"):
        p = bind / tool
        p.write_text("#!/bin/bash\necho \"STUB $0 called\" >&2\nexit 99\n")
        p.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bind}:{env['PATH']}"
    env["HALOW_DIR"] = str(tmp_path / "halow")
    return env


def _run(env):
    # Bypass the root check by pretending (the script only gates write actions,
    # all of which are stubbed/not reached on the fast path). Run via bash with a
    # fake id by prepending a stub 'id' too is overkill — instead run under the
    # current uid and assert on the fast path only when root; otherwise assert the
    # root gate. Here we focus on the stamp branch by faking id -> 0.
    binid = Path(env["PATH"].split(":")[0]) / "id"
    binid.write_text('#!/bin/bash\n[ "$1" = -u ] && echo 0 || exec /usr/bin/id "$@"\n')
    binid.chmod(0o755)
    return subprocess.run(["bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True)


def test_matching_stamp_is_noop(tmp_path):
    env = _stub_env(tmp_path)
    kver = subprocess.run(["uname", "-r"], capture_output=True, text=True).stdout.strip()
    halow_dir = Path(env["HALOW_DIR"])
    halow_dir.mkdir(parents=True)
    (halow_dir / ".built").write_text(f"{TAGS} kernel={kver}")

    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "up to date" in r.stdout
    # No stub tool was invoked (none would have exited 99 / printed STUB).
    assert "STUB" not in r.stderr


def test_absent_stamp_proceeds_and_hits_a_stub(tmp_path):
    env = _stub_env(tmp_path)
    r = _run(env)
    # No stamp -> past the fast path -> it reaches a stubbed tool (apt-get) and
    # aborts there (exit 99 under `set -e`), proving it did NOT no-op.
    assert r.returncode != 0
    assert "up to date" not in r.stdout
    assert "STUB" in r.stderr