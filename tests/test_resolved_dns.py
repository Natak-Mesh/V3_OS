"""Node DNS works on a mesh-only node (no eth0 uplink).

Regression (node 0047): eth0 down -> no DHCP DNS; `resolvectl dns` showed every
link empty and `getent hosts github.com` failed, while `ping 8.8.8.8` worked via
the Babel default (via 10.20.1.56 dev wlan1). A per-link `resolvectl dns wlan1`
was ignored because wlan1 is DORMANT ("Current Scopes: none"); only a global
`DNS=` in a resolved.conf.d drop-in fixed it.
"""

from nucleusd import apply
from nucleusd.apply import render_all
from nucleusd.schema import NucleusConfig

RESOLVED = "/etc/systemd/resolved.conf.d/90-nucleus.conf"


def cfg(**extra):
    return NucleusConfig.model_validate({
        "node": {"id": 47},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        **extra,
    })


def test_resolved_dropin_sets_global_dns():
    # Exact content of the working hand fix on 0047.
    r = render_all(cfg())[RESOLVED]
    assert "\n[Resolve]\nDNS=8.8.8.8\n" in r
    assert "FallbackDNS" not in r  # ignored once any link has a DNS server


def test_resolved_dns_follows_br_lan_dns():
    r = render_all(cfg(br_lan={"dns": "1.1.1.1"}))[RESOLVED]
    assert "DNS=1.1.1.1\n" in r


def test_resolved_target_decoupled_from_net_group():
    t = [t for t in apply.TARGETS if t.dest == apply.RESOLVED_CONF]
    assert len(t) == 1
    assert t[0].units == ("systemd-resolved",)
    assert "systemd-resolved" not in apply.NET_GROUP_ORDER


def _stub_apply(monkeypatch, tmp_path, restarted):
    monkeypatch.setattr(apply, "RESOLVED_CONF", tmp_path / "90-nucleus.conf")
    monkeypatch.setattr(apply, "TARGETS", [apply.Target(
        "resolved-nucleus.conf.j2", tmp_path / "90-nucleus.conf", ("systemd-resolved",))])
    monkeypatch.setattr(apply, "_restart", lambda units: restarted.extend(units))
    for name in ("_reconcile_rf", "_reconcile_halow", "_reconcile_meshtastic",
                 "_reconcile_modem73", "_reconcile_reticulum", "_reconcile_eth0_nm",
                 "_reconcile_local_names"):
        monkeypatch.setattr(apply, name, lambda *a, **k: [])
    for name in ("_reconcile_meshtastic_tx", "_chown_reticulum", "_reload_nginx",
                 "_reload_udev"):
        monkeypatch.setattr(apply, name, lambda *a, **k: None)
    monkeypatch.setattr(apply, "_ensure_web_cert", lambda *a, **k: False)
    monkeypatch.setattr(apply, "NGINX_ENABLED", tmp_path / "nginx-enabled")
    monkeypatch.setattr(apply, "NGINX_VHOST", tmp_path / "nginx-vhost")
    monkeypatch.setattr(apply, "NGINX_DEFAULT", tmp_path / "nginx-default")
    monkeypatch.setattr(apply.rf, "set_iface_blocked", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(apply.rf, "iface_present", lambda iface, **kw: False)


def test_apply_restarts_resolved_only_on_change(monkeypatch, tmp_path):
    restarted = []
    _stub_apply(monkeypatch, tmp_path, restarted)
    apply.apply(cfg())
    assert restarted == ["systemd-resolved"]  # no mesh group bounce
    assert (tmp_path / "90-nucleus.conf").read_text().endswith("DNS=8.8.8.8\n")
    restarted.clear()
    apply.apply(cfg())  # unchanged -> idempotent
    assert restarted == []
