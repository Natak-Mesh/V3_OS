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
    def __init__(self, enabled=True, region="US", tx_enabled=True):
        self.enabled = enabled
        self.region = region
        self.tx_enabled = tx_enabled


class _Cfg:
    def __init__(self, enabled=True, tx_enabled=True):
        self.node = _Node()
        self.meshtastic = _Mesh(enabled=enabled, tx_enabled=tx_enabled)


def _stub_common(monkeypatch, *, region_line="lora.region: US",
                 tx_line="lora.tx_enabled: True", cfg=None):
    """Stub config load, API wait and CLI so main() reaches the config read.

    fake_cli answers --get for the specific key being queried (region vs
    tx_enabled) and records --set calls on radio_init._set_calls for assertions.
    """
    monkeypatch.setattr(radio_init.cfgio, "load", lambda: cfg or _Cfg())
    monkeypatch.setattr(radio_init, "_wait_for_api", lambda *a, **k: True)
    sets: list[list[str]] = []
    radio_init._set_calls = sets  # type: ignore[attr-defined]

    def fake_cli(args, timeout=120):
        if "--set" in args:
            sets.append(args)
            return 0, ""
        if "--get" in args:
            key = args[args.index("--get") + 1]
            if key == "lora.tx_enabled":
                return 0, tx_line + "\n"
            return 0, region_line + "\n"
        return 0, ""

    monkeypatch.setattr(radio_init, "_cli", fake_cli)
    return sets


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


def _stub_read(monkeypatch):
    """Neutralise the radio-config cache read + radio-reboot sleep for TX tests."""
    monkeypatch.setattr(meshtastic_api, "_read_config_from_radio", lambda: None)
    monkeypatch.setattr(radio_init.time, "sleep", lambda *_a: None)


def test_tx_sync_pushes_when_radio_differs(monkeypatch):
    # config wants tx off, radio reports on -> push "false".
    sets = _stub_common(monkeypatch, tx_line="lora.tx_enabled: True",
                        cfg=_Cfg(tx_enabled=False))
    _stub_read(monkeypatch)
    radio_init.main()
    assert ["--set", "lora.tx_enabled", "false"] in sets


def test_tx_sync_noop_when_radio_matches(monkeypatch):
    # config wants tx on, radio already on -> no --set.
    sets = _stub_common(monkeypatch, tx_line="lora.tx_enabled: True",
                        cfg=_Cfg(tx_enabled=True))
    _stub_read(monkeypatch)
    radio_init.main()
    assert not any(c[:2] == ["--set", "lora.tx_enabled"] for c in sets)


def test_tx_sync_leaves_radio_alone_when_unreadable(monkeypatch):
    # --get for tx_enabled returns nothing parseable -> never --set.
    sets = _stub_common(monkeypatch, tx_line="",
                        cfg=_Cfg(tx_enabled=False))
    _stub_read(monkeypatch)
    radio_init.main()
    assert not any(c[:2] == ["--set", "lora.tx_enabled"] for c in sets)
