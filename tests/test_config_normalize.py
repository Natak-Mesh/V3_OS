"""Regression tests for config.normalize() — the update-path default merge.

normalize() rewrites /etc/nucleus/config.yaml through the schema so that keys a
new code release adds gain their defaults on an already-provisioned node, without
an operator editing the file. This is what makes the web-UI update self-contained
(git pull -> install.sh -> nucleusctl apply, which calls normalize).

Contract locked down here:
  * a config missing a newly-added key gets the schema default persisted,
  * operator-set values are preserved (defaults never clobber them),
  * it is idempotent (second run reports no change),
  * it reaches the voice daemon's own direct reader (load_config).
"""

import textwrap

from nucleusd import config
from nucleusd.voice import daemon


def _write(tmp_path, body):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return p


# A minimal, valid config that predates the stream_enabled key — i.e. exactly
# what an existing node looks like before an update introduces the feature.
_LEGACY = """
    node:
      id: 7
    mesh:
      subnet_prefix: 10.20.1
      password: pw123456
    ap:
      password: ap123456
    voice:
      channel: 2
      lora_enabled: true
"""


def test_normalize_adds_missing_default(tmp_path):
    p = _write(tmp_path, _LEGACY)
    changed = config.normalize(p)
    assert changed is True
    cfg = config.load(p)
    # New key gains its schema default (True) even though the file never had it.
    assert cfg.voice.stream_enabled is True
    assert cfg.voice.stream_portnum == 256


def test_normalize_preserves_operator_values(tmp_path):
    p = _write(tmp_path, _LEGACY)
    config.normalize(p)
    cfg = config.load(p)
    # Operator-set values survive the rewrite; defaults do not clobber them.
    assert cfg.voice.channel == 2
    assert cfg.voice.lora_enabled is True


def test_normalize_is_idempotent(tmp_path):
    p = _write(tmp_path, _LEGACY)
    assert config.normalize(p) is True
    # Second run: nothing missing, nothing rewritten.
    assert config.normalize(p) is False


def test_normalize_respects_explicit_opt_out(tmp_path):
    # An operator who turned the feature off keeps it off through an update.
    p = _write(tmp_path, _LEGACY + "      stream_enabled: false\n")
    config.normalize(p)
    assert config.load(p).voice.stream_enabled is False


def test_save_keeps_config_world_readable(tmp_path):
    # normalize()/save() must not leave the config 0600 (mkstemp's default), or
    # non-root consumers (web UI, natak-run nucleusctl, voice daemon) lose read
    # access after an atomic rewrite.
    import stat

    p = _write(tmp_path, _LEGACY)
    config.normalize(p)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o644


def test_normalized_key_reaches_voice_daemon(tmp_path, monkeypatch):
    # The daemon reads config.yaml directly (not via schema), so the persisted
    # key is what actually enables streaming on the node.
    p = _write(tmp_path, _LEGACY)
    config.normalize(p)
    monkeypatch.setattr(daemon, "CONFIG_YAML", str(p))
    assert daemon.load_config()["VOICE_LORA_STREAM_ENABLED"] == "true"
