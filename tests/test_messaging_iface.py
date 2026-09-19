"""Regression tests: multicast TX/RX must be scoped to the mesh interface.

Bug: the daemon runs unprivileged (User=natak), so SO_BINDTODEVICE (needs
CAP_NET_RAW) silently failed and multicast TX followed the default route out
eth0 instead of the wlan1 mesh; the RX join used INADDR_ANY (0.0.0.0), joining
on the default iface too. Net effect: sends "succeed" but never reach the mesh
and peer messages never arrive. The fix pins egress via IP_MULTICAST_IF and the
join via the interface address, both of which work without capabilities.

These tests exercise the socket-option setup against a real loopback interface
so they don't depend on wlan1 being present.
"""

import socket

from nucleusd.messaging import service
from nucleusd.messaging.service import MessagingService, iface_ipv4


def _loopback_name() -> str:
    # Loopback is always present and has an IPv4 addr in CI and on the node.
    for name in ("lo", "lo0"):
        if iface_ipv4(name):
            return name
    raise RuntimeError("no loopback iface with IPv4 found")


def _make_service(mcast_if: str):
    cfg = {
        "sender": "0042-nucleus",
        "dedupe_window_secs": 60,
        "history_limit": 100,
        "wifi_group": "239.10.10.60",
        "wifi_port": 17020,
        "lora": False,
        "mesh_ttl": 8,
    }
    import threading
    from nucleusd.messaging.store import MessageStore
    svc = MessagingService.__new__(MessagingService)
    svc.cfg = cfg
    svc.sender = cfg["sender"]
    svc.store = MessageStore(dedupe_window_secs=60, history_limit=100)
    svc.wifi_group = cfg["wifi_group"]
    svc.wifi_port = cfg["wifi_port"]
    svc.lora_enabled = False
    svc._wifi_tx = None
    svc._lora_tx = None
    svc._subs = {}
    svc._subs_lock = threading.Lock()
    svc._stop = threading.Event()
    return svc


def test_iface_ipv4_returns_address_for_loopback():
    ip = iface_ipv4(_loopback_name())
    assert ip is not None
    # dotted-quad
    assert socket.inet_aton(ip)


def test_iface_ipv4_missing_iface_returns_none():
    assert iface_ipv4("nonexistent-xyz0") is None


def test_wifi_tx_pins_egress_iface(monkeypatch):
    """TX socket must set IP_MULTICAST_IF to the mesh iface address (no caps)."""
    lo = _loopback_name()
    lo_ip = iface_ipv4(lo)
    monkeypatch.setattr(service, "MCAST_IF", lo)

    svc = _make_service(lo)
    svc._setup_wifi_tx()
    try:
        opt = svc._wifi_tx.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, 4)
        assert socket.inet_ntoa(opt) == lo_ip
        # TTL still applied from config.
        ttl = svc._wifi_tx.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL)
        assert ttl == 8
    finally:
        svc._wifi_tx.close()


def test_wifi_tx_no_bindtodevice_used(monkeypatch):
    """Must not rely on SO_BINDTODEVICE (requires CAP_NET_RAW we don't have)."""
    lo = _loopback_name()
    monkeypatch.setattr(service, "MCAST_IF", lo)
    calls = []

    real_setsockopt = socket.socket.setsockopt

    def spy(self, level, optname, value, *a):
        calls.append((level, optname))
        return real_setsockopt(self, level, optname, value, *a)

    monkeypatch.setattr(socket.socket, "setsockopt", spy)
    svc = _make_service(lo)
    svc._setup_wifi_tx()
    svc._wifi_tx.close()

    assert (socket.SOL_SOCKET, socket.SO_BINDTODEVICE) not in calls
    assert (socket.IPPROTO_IP, socket.IP_MULTICAST_IF) in calls
