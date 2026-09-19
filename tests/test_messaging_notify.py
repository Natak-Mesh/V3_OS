"""Regression tests for the messaging daemon's live-push (subscribe/notify).

The web UI's live chat WS relies on the daemon pushing each NEW message to
subscribed local UDP listeners (the API WS bridge). These tests exercise that
mechanism directly against a real loopback UDP subscriber — no FastAPI, no
transports, no /etc config needed.
"""

import json
import socket

from nucleusd.messaging.service import MessagingService


def _make_service():
    cfg = {
        "sender": "0042-nucleus",
        "dedupe_window_secs": 60,
        "history_limit": 100,
        "wifi_group": "239.10.10.60",
        "wifi_port": 17020,
        "lora": False,
        "mesh_ttl": 8,
    }
    # persist off: point the store at no path by overriding after construction.
    svc = MessagingService.__new__(MessagingService)
    # Minimal init without touching the filesystem/PERSIST_PATH.
    import threading
    from nucleusd.messaging.store import MessageStore
    svc.cfg = cfg
    svc.sender = cfg["sender"]
    svc.store = MessageStore(dedupe_window_secs=60, history_limit=100)
    svc.wifi_group = cfg["wifi_group"]
    svc.wifi_port = cfg["wifi_port"]
    svc.lora_enabled = False
    svc._wifi_tx = None
    svc._lora_tx = None
    svc._notify_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    svc._subs = {}
    svc._subs_lock = threading.Lock()
    svc._stop = threading.Event()
    return svc


def test_notify_pushes_new_message_to_subscriber():
    svc = _make_service()
    sub = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sub.bind(("127.0.0.1", 0))
    sub.settimeout(2.0)
    svc._subscribe(sub.getsockname())

    msg, is_new = svc._ingest("0043-nucleus", "move to OP", "wifi", ts=1000.0)
    assert is_new is True

    data, _ = sub.recvfrom(65535)
    obj = json.loads(data.decode("utf-8"))
    assert obj["event"] == "message"
    assert obj["message"]["text"] == "move to OP"
    assert obj["message"]["sender"] == "0043-nucleus"
    assert obj["message"]["id"] == msg["id"]
    sub.close()


def test_notify_skips_duplicate_message():
    svc = _make_service()
    sub = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sub.bind(("127.0.0.1", 0))
    sub.settimeout(0.5)
    svc._subscribe(sub.getsockname())

    svc._ingest("0043-nucleus", "sitrep", "wifi", ts=1000.0)
    sub.recvfrom(65535)  # first (new) push

    # Same content over the other transport within the window → not new, no push.
    _, is_new = svc._ingest("0043-nucleus", "sitrep", "lora", ts=1000.3)
    assert is_new is False
    import socket as _s
    try:
        sub.recvfrom(65535)
        assert False, "duplicate should not have pushed a notification"
    except _s.timeout:
        pass
    sub.close()


def test_expired_subscriber_is_dropped():
    svc = _make_service()
    sub = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sub.bind(("127.0.0.1", 0))
    svc._subscribe(sub.getsockname())
    # Force the subscription to look expired.
    with svc._subs_lock:
        for k in svc._subs:
            svc._subs[k] = 0.0
    svc._ingest("0043-nucleus", "hi", "wifi", ts=1000.0)
    assert svc._subs == {}
    sub.close()
