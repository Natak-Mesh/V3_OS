"""radio_init boot-time behaviour. Run off-box: `pytest` from repo root.

Covers the boot config-read: nucleus-meshtastic-init must cache the radio
config at boot (same as the UI "Read" button) so peers can see this node's
channel and offer a one-click Join without an operator pressing Read first.

meshtastic_api / radio_init import only stdlib at module level (the meshtastic
library is imported inside functions we stub out), so no hardware is needed.
"""

from nucleusd.meshtastic import meshtastic_api, radio_init


class _Node:
    hostname = "0042-nucleus"
    short = "0042"


class _Mesh:
    def __init__(self, enabled=True, region="US"):
        self.enabled = enabled
        self.region = region


class _Cfg:
    def __init__(self, enabled=True):
        self.node = _Node()
        self.meshtastic = _Mesh(enabled=enabled)


def _stub_common(monkeypatch, *, region_line="lora.region: US"):
    """Stub config load, API wait and CLI so main() reaches the config read."""
    monkeypatch.setattr(radio_init.cfgio, "load", lambda: _Cfg())
    monkeypatch.setattr(radio_init, "_wait_for_api", lambda *a, **k: True)

    def fake_cli(args, timeout=120):
        if "--get" in args:
            return 0, region_line + "\n"
        return 0, ""

    monkeypatch.setattr(radio_init, "_cli", fake_cli)


def test_main_caches_radio_config(monkeypatch):
    _stub_common(monkeypatch)
    called = []
    monkeypatch.setattr(
        meshtastic_api, "_read_config_from_radio", lambda: called.append(True)
    )

    radio_init.main()

    assert called == [True]


def test_main_config_read_failure_does_not_raise(monkeypatch):
    # Best-effort: a radio read error must never crash boot init.
    _stub_common(monkeypatch)

    def boom():
        raise RuntimeError("radio connect failed")

    monkeypatch.setattr(meshtastic_api, "_read_config_from_radio", boom)

    radio_init.main()  # must return normally


def test_main_skips_when_meshtastic_disabled(monkeypatch):
    monkeypatch.setattr(radio_init.cfgio, "load", lambda: _Cfg(enabled=False))
    called = []
    monkeypatch.setattr(
        meshtastic_api, "_read_config_from_radio", lambda: called.append(True)
    )

    radio_init.main()

    assert called == []
