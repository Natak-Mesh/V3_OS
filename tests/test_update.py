"""nucleusd.update — status-file parsing + exit-code mapping regression tests.

The update script restarts nucleusd mid-run, so the on-disk status/log files are
the only source of truth for progress. These tests pin the real file format the
script writes (see system/bin/nucleus-update.sh) and the exit-code → message map.
"""

from nucleusd import update


# Real format the script's on_exit trap / running header writes.
STATUS_RUNNING = "status=running\npid=12345\nstarted=1758268800\n"
STATUS_SUCCESS = "status=finished\nrc=0\nfinished=1758268900\n"
STATUS_UPTODATE = "status=finished\nrc=1\nfinished=1758268900\n"
STATUS_DIRTY = "status=finished\nrc=3\nfinished=1758268900\n"

LOG_SAMPLE = (
    "[2026-09-19 06:00:00] ===== nucleus-update started =====\n"
    "[2026-09-19 06:00:00] repo: /home/natak/V3_OS\n"
    "[2026-09-19 06:00:01] network OK\n"
    "[2026-09-19 06:00:03] ===== update SUCCEEDED =====\n"
)


def test_parse_status_running():
    st = update._parse_status(STATUS_RUNNING)
    assert st["status"] == "running"
    assert st["pid"] == "12345"


def test_progress_success(tmp_path, monkeypatch):
    status_f = tmp_path / "status"
    log_f = tmp_path / "log"
    status_f.write_text(STATUS_SUCCESS)
    log_f.write_text(LOG_SAMPLE)
    monkeypatch.setattr(update, "UPDATE_STATUS", str(status_f))
    monkeypatch.setattr(update, "UPDATE_LOG", str(log_f))

    p = update.progress()
    assert p["status"] == "finished"
    assert p["rc"] == 0
    assert p["message"] == update.RC_MESSAGES[0]
    assert p["log"][-1].endswith("update SUCCEEDED =====")


def test_progress_uptodate_and_dirty(tmp_path, monkeypatch):
    status_f = tmp_path / "status"
    monkeypatch.setattr(update, "UPDATE_STATUS", str(status_f))
    monkeypatch.setattr(update, "UPDATE_LOG", str(tmp_path / "missing"))

    status_f.write_text(STATUS_UPTODATE)
    assert update.progress()["message"] == update.RC_MESSAGES[1]

    status_f.write_text(STATUS_DIRTY)
    assert update.progress()["message"] == update.RC_MESSAGES[3]


def test_progress_idle_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "UPDATE_STATUS", str(tmp_path / "nope"))
    monkeypatch.setattr(update, "UPDATE_LOG", str(tmp_path / "nope2"))
    p = update.progress()
    assert p["status"] == "idle"
    assert p["rc"] is None
    assert p["log"] == []


def test_rc_messages_cover_all_script_codes():
    # Exit codes 0-8 are defined by system/bin/nucleus-update.sh.
    for code in range(0, 9):
        assert code in update.RC_MESSAGES


def test_version_info_installed_is_package_version(monkeypatch, tmp_path):
    # With no .git, version_info reports installed (__version__) and an error,
    # never crashing — confirms installed is the running package version.
    monkeypatch.setattr(update, "REPO_DIR", tmp_path)
    info = update.version_info()
    assert info["installed"] == update.__version__
    assert info["error"]
    assert info["available"] is None
