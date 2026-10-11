"""Regression tests for config.normalize() — the update-path default merge.

normalize() rewrites /etc/nucleus/config.yaml through the schema so that keys a
new code release adds gain their defaults on an already-provisioned node, without
an operator editing the file. This is what makes nucleus-update.sh self-contained
(pull -> install.sh -> nucleusctl apply, which calls normalize).

Contract locked down here:
  * a config missing a newly-added key gets the schema default persisted,
  * operator-set values are preserved (defaults never clobber them),
  * it is idempotent (second run reports no change),
  * sections dropped from the schema are pruned from the file.
"""

import textwrap

from nucleusd import config


def _write(tmp_path, body):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return p


# A minimal, valid config that predates the firewall section and still
# carries a section the drone build dropped (voice) — i.e. exactly what an
# existing node looks like before an update.
_LEGACY = """
    node:
      id: 7
    mesh:
      subnet_prefix: 10.20.1
      password: pw123456
      channel: 6
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
    # Keys absent from the file gain their schema defaults.
    assert cfg.firewall.enabled is True
    assert cfg.mesh.mcast_ttl == 8
    assert "firewall:" in p.read_text()


def test_normalize_preserves_operator_values(tmp_path):
    p = _write(tmp_path, _LEGACY)
    config.normalize(p)
    cfg = config.load(p)
    # Operator-set values survive the rewrite; defaults do not clobber them.
    assert cfg.mesh.channel == 6
    assert cfg.node.id == 7


def test_normalize_is_idempotent(tmp_path):
    p = _write(tmp_path, _LEGACY)
    assert config.normalize(p) is True
    # Second run: nothing missing, nothing rewritten.
    assert config.normalize(p) is False


def test_normalize_respects_explicit_opt_out(tmp_path):
    # An operator who turned a feature off keeps it off through an update.
    p = _write(tmp_path, _LEGACY + "    firewall:\n      enabled: false\n")
    config.normalize(p)
    assert config.load(p).firewall.enabled is False


def test_normalize_prunes_removed_sections(tmp_path):
    # Sections for subsystems removed from this build are dropped on rewrite.
    p = _write(tmp_path, _LEGACY)
    config.normalize(p)
    assert "voice:" not in p.read_text()


def test_save_keeps_config_world_readable(tmp_path):
    # normalize()/save() must not leave the config 0600 (mkstemp's default), or
    # non-root consumers (natak-run nucleusctl) lose read access after
    # an atomic rewrite.
    import stat

    p = _write(tmp_path, _LEGACY)
    config.normalize(p)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o644
