"""Hard-line (fiber/copper) node link over eth0 — schema, render, apply.

Regression: node 0042 <-> 0047 over a fiber media converter on eth0. ssh worked
(plain unicast) but ATAK never crossed: smcroute had no eth0 routes, babeld did
not run on eth0, the TTL=1 bump only covered br-lan (and lived in the mesh-up
script, which aborted on RF-kill), and with mesh.enabled: false babeld/smcroute
were disabled outright. Fixture is 0042's live /etc/nucleus/config.yaml.
"""

import textwrap

import yaml

from nucleusd import config as cfgio
from nucleusd.apply import render_all
from nucleusd.schema import NucleusConfig

FIREWALL = "/opt/nucleus/bin/nucleus-firewall.sh"
BABELD = "/etc/babeld.conf"
SMCROUTE = "/etc/smcroute.conf"
MESH_UP = "/opt/nucleus/bin/nucleus-mesh-up.sh"
ATAK_GROUPS = ("239.2.3.1", "224.10.10.1", "239.255.255.1", "239.255.255.2", "239.255.255.12")

# Node 0042's config.yaml at the time of the bug (no eth0.babel/mcast_mode
# keys yet — they must default ON). Trimmed to the sections the schema needs.
NODE42_YAML = textwrap.dedent("""\
    node:
      id: 42
    mesh:
      enabled: false
      ssid: natak_mesh
      channel: 3
      password: '52235223'
      subnet_prefix: 10.20.1
      country: US
      mcast_ttl: 8
      mesh_802_ttl: 8
      rts_threshold: 500
    br_lan:
      dhcp_pool_offset: 10
      dhcp_pool_size: 50
      dns: 8.8.8.8
    ap:
      channel: 36
      password: '52235223'
    eth0:
      mode: wan
      dhcp_pool_offset: 10
      dhcp_pool_size: 100
    halow:
      enabled: true
      bandwidth_mhz: 1
      channel: 27
      prim_1mhz_index: 0
    web:
      user: admin
      password: '52235223'
      eth0_access: true
    firewall:
      enabled: true
""")


def node42(**eth0):
    d = yaml.safe_load(NODE42_YAML)
    d["eth0"].update(eth0)
    return NucleusConfig.model_validate(d)


# --- schema ------------------------------------------------------------------

def test_defaults_on_for_legacy_config():
    c = node42()
    assert c.eth0.babel is True
    assert c.eth0.mcast_mode == "br_lan"
    # mesh off must NOT take routing/multicast down with it.
    assert c.babeld_enabled is True
    assert c.smcroute_enabled is True


def test_routing_off_only_when_every_link_off():
    d = yaml.safe_load(NODE42_YAML)
    d["halow"]["enabled"] = False
    d["eth0"].update(babel=False, mcast_mode="off")
    c = NucleusConfig.model_validate(d)
    assert c.babeld_enabled is False
    assert c.smcroute_enabled is False


def test_normalize_persists_new_eth0_keys(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(NODE42_YAML)
    assert cfgio.normalize(p) is True
    on_disk = yaml.safe_load(p.read_text())
    assert on_disk["eth0"]["babel"] is True
    assert on_disk["eth0"]["mcast_mode"] == "br_lan"
    assert "mcast_bridge" not in on_disk["eth0"]
    assert on_disk["eth0"]["mode"] == "wan"  # operator value preserved


def test_nucleus_nets_collapse_overlaps():
    # br-lan 10.20.42.0/24 and mesh 10.20.1.0/24 fold into 10.20.0.0/16 (nft
    # rejects overlapping elements in one anonymous set).
    assert node42().nucleus_nets == ["10.20.0.0/16", "10.30.1.0/24"]


# --- babeld ------------------------------------------------------------------

def test_babeld_runs_on_eth0_as_wired():
    b = render_all(node42())[BABELD]
    assert "interface eth0 type wired rxcost 96 hello-interval 4" in b
    assert "redistribute ip 10.20.42.0/24 allow" in b


def test_babeld_eth0_off():
    assert "interface eth0" not in render_all(node42(babel=False))[BABELD]


# --- smcroute ----------------------------------------------------------------

def test_smcroute_bridges_eth0_and_brlan_no_echo():
    s = render_all(node42())[SMCROUTE]
    for g in ATAK_GROUPS:
        assert f"mgroup from eth0 group {g}\n" in s
        assert f"mroute from eth0 group {g} to br-lan\n" in s
        assert f"mroute from br-lan group {g} to eth0\n" in s
        # No-echo: never back out the ingress interface.
        assert f"mroute from eth0 group {g} to eth0" not in s


def test_smcroute_no_eth0_radio_relay():
    # Wire <-> radio-mesh multicast relay is deferred (loop risk): never render it.
    lines = render_all(node42())[SMCROUTE].splitlines()
    from_eth0 = [l for l in lines if l.startswith("mroute from eth0")]
    assert from_eth0 and all(l.endswith("to br-lan") for l in from_eth0)
    for radio in ("wlan1", "halow0"):
        assert not [l for l in lines
                    if l.startswith(f"mroute from {radio}") and l.endswith("to eth0")]


def test_smcroute_eth0_off():
    assert "eth0" not in render_all(node42(mcast_mode="off"))[SMCROUTE]


# --- TTL bump (moved out of mesh-up) ------------------------------------------

def test_ttl_bump_in_firewall_not_mesh_up():
    r = render_all(node42())
    f = r[FIREWALL]
    assert "table ip nucleus_mcast {" in f
    assert ('iifname "br-lan" ip daddr { 239.2.3.1, 224.10.10.1, 239.255.255.0/24 } '
            'ip ttl set 8 comment "nucleus_mcast_ttl"') in f
    # eth0: only TTL==1 (an EUD directly on the wire) is raised.
    assert ('iifname "eth0" ip daddr { 239.2.3.1, 224.10.10.1, 239.255.255.0/24 } '
            'ip ttl 1 ip ttl set 8 comment "nucleus_mcast_ttl"') in f
    # Rebuilt each run + legacy ip mangle rules removed.
    assert f.index("nft delete table ip nucleus_mcast") < f.index("table ip nucleus_mcast {")
    assert "nft delete rule ip mangle PREROUTING handle" in f
    assert "nucleus_mcast_ttl" not in r[MESH_UP]
    assert "ip ttl set" not in r[MESH_UP]


def test_ttl_bump_eth0_off():
    f = render_all(node42(mcast_mode="off"))[FIREWALL]
    assert 'iifname "br-lan"' in f
    assert 'iifname "eth0" ip daddr' not in f


def test_ttl_bump_disabled_by_mcast_ttl_zero():
    d = yaml.safe_load(NODE42_YAML)
    d["mesh"]["mcast_ttl"] = 0
    f = render_all(NucleusConfig.model_validate(d))[FIREWALL]
    assert "table ip nucleus_mcast {" not in f
    assert "nft delete table ip nucleus_mcast" in f  # stale table still cleared


# --- NAT ---------------------------------------------------------------------

def test_wan_nat_exempts_nucleus_nets_and_multicast():
    f = render_all(node42())[FIREWALL]
    assert ('oifname "eth0" ip daddr != { 10.20.0.0/16, 10.30.1.0/24, 224.0.0.0/4 } '
            'masquerade comment "nucleus_wan_nat"') in f
    # Legacy unconditional masquerade in `ip nat` is removed, never re-added.
    assert "nft delete rule ip nat POSTROUTING handle" in f
    assert 'nft add rule ip nat POSTROUTING oifname "eth0" masquerade' not in f


def test_lan_mode_no_nat():
    f = render_all(node42(mode="lan"))[FIREWALL]
    assert "masquerade comment" not in f
    assert "nft delete table ip nucleus_nat" in f  # stale WAN NAT cleared



# --- UFW ---------------------------------------------------------------------

def test_ufw_eth0_inbound_babel_and_atak():
    f = render_all(node42())[FIREWALL]
    assert "ufw allow in on eth0 to any port 6696 proto udp comment 'babel over ethernet'" in f
    for dst in ("239.2.3.1", "224.10.10.1", "239.255.255.0/24"):
        assert f"ufw allow in on eth0 to {dst} comment 'atak multicast over ethernet'" in f


def test_ufw_eth0_forwarding_wan_mode():
    f = render_all(node42())[FIREWALL]
    # eth0 -> internal only from Nucleus nets (upstream WAN hosts stay out).
    for out in ("br-lan", "wlan1", "halow0"):
        for net in ("10.20.0.0/16", "10.30.1.0/24"):
            assert (f"ufw route allow in on eth0 out on {out} from {net} "
                    "comment 'eth0 node link'") in f
    for grp in ("239.2.3.1", "224.10.10.1", "239.255.255.0/24"):
        assert f"ufw route allow in on eth0 out on br-lan to {grp} comment 'eth0 node link'" in f
    # Any-source eth0 -> internal forwarding is never opened.
    assert "ufw route allow in on eth0 out on br-lan comment" not in f
    assert "ufw route allow in on eth0 out on br-lan\n" not in f
    # WAN egress rules already cover internal -> eth0: no duplicates.
    assert "out on eth0 to 10.20.0.0/16" not in f


def test_ufw_eth0_forwarding_lan_mode():
    f = render_all(node42(mode="lan"))[FIREWALL]
    for src in ("br-lan", "wlan1", "halow0"):
        assert (f"ufw route allow in on {src} out on eth0 to 10.20.0.0/16 "
                "comment 'eth0 node link'") in f
    assert "ufw route allow in on br-lan out on eth0 to 239.2.3.1 comment 'eth0 node link'" in f
    # Still no generic internet egress in LAN mode.
    assert "ufw route allow in on br-lan out on eth0\n" not in f


def test_ufw_eth0_link_off():
    f = render_all(node42(babel=False, mcast_mode="off"))[FIREWALL]
    assert "port 6696" not in f
    assert "atak multicast over ethernet" not in f
    assert "eth0 node link" not in f



# --- eth0.mcast_mode: mesh_relay (RF-silent node on the wire -> radio meshes) --

def relay42(**eth0):
    return node42(mcast_mode="mesh_relay", **eth0)


def test_legacy_mcast_bridge_bool_migrates():
    assert node42(mcast_bridge=True).eth0.mcast_mode == "br_lan"
    assert node42(mcast_bridge=False).eth0.mcast_mode == "off"
    # Explicit mcast_mode wins over the legacy key.
    assert node42(mcast_bridge=True, mcast_mode="mesh_relay").eth0.mcast_mode == "mesh_relay"


def test_mesh_relay_not_default():
    c = node42()
    assert c.eth0.mcast_mode == "br_lan"
    assert c.eth0_mcast_relay_ifaces == []
    assert c.eth0_mcast_source_subnets == []


def test_mesh_relay_requires_babel():
    import pytest
    with pytest.raises(ValueError, match="requires eth0.babel"):
        relay42(babel=False)


def test_mesh_relay_rejects_own_id_and_bad_ids():
    import pytest
    with pytest.raises(ValueError, match="this node's id"):
        relay42(mcast_source_node_ids=[42])
    with pytest.raises(ValueError, match="not in 1-254"):
        relay42(mcast_source_node_ids=[255])


def test_mesh_relay_source_subnets():
    c = relay42(mcast_source_node_ids=[47, 17, 47])
    assert c.eth0.mcast_source_node_ids == [17, 47]
    assert c.eth0_mcast_source_subnets == ["10.20.17.0/24", "10.20.47.0/24"]
    # Kept but unused outside mesh_relay.
    assert node42(mcast_source_node_ids=[47]).eth0_mcast_source_subnets == []


def test_smcroute_mesh_relay_routes_no_echo():
    s = render_all(relay42())[SMCROUTE]
    for g in ATAK_GROUPS:
        for radio in ("wlan1", "halow0"):  # node 0042 has HaLow enabled
            assert f"mroute from eth0 group {g} to {radio}\n" in s
            assert f"mroute from {radio} group {g} to eth0\n" in s
        assert f"mroute from eth0 group {g} to br-lan\n" in s
        assert f"mroute from eth0 group {g} to eth0" not in s
    # Still never wlan1 <-> halow0.
    assert "mroute from wlan1 group 239.2.3.1 to halow0" not in s
    assert "mroute from halow0 group 239.2.3.1 to wlan1" not in s


def test_smcroute_mesh_relay_wlan1_only_without_halow():
    d = yaml.safe_load(NODE42_YAML)
    d["halow"]["enabled"] = False
    d["eth0"]["mcast_mode"] = "mesh_relay"
    s = render_all(NucleusConfig.model_validate(d))[SMCROUTE]
    assert "mroute from eth0 group 239.2.3.1 to wlan1\n" in s
    assert "halow0" not in s


def test_ufw_mesh_relay_forwarding():
    f = render_all(relay42())[FIREWALL]
    for out in ("br-lan", "wlan1", "halow0"):
        assert f"ufw route allow in on eth0 out on {out} to 239.2.3.1 comment 'eth0 node link'" in f
    f = render_all(relay42(mode="lan"))[FIREWALL]
    for src in ("wlan1", "halow0"):
        assert f"ufw route allow in on {src} out on eth0 to 239.2.3.1 comment 'eth0 node link'" in f


def test_mesh_relay_exclude_source_gateways():
    c = relay42(mcast_source_node_ids=[47, 17])
    assert c.eth0_mcast_source_exclude_ips == ["10.20.17.1", "10.20.47.1"]
    assert node42(mcast_source_node_ids=[47]).eth0_mcast_source_exclude_ips == []
