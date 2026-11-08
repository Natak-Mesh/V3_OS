"""Meshtastic client API (/api/v1/meshtastic/client...). Off-box.

The API only READS the bridge's /run chat store + node file and relays radio
actions to the bridge's control socket; these tests point the paths at temp
files and stub the bridge.
"""

import json

import pytest
from fastapi.testclient import TestClient

from nucleusd.api import app
from nucleusd.meshtastic import meshchat
from nucleusd.meshtastic import meshtastic_api as mx
from tests.meshtastic_fixtures import ME, PEER

client = TestClient(app)

NODES_FILE = {
    "timestamp": 1791650000, "my_num": ME, "tx_enabled": True,
    "me": {"num": ME, "id": "!3007771f", "short_name": "0042", "long_name": "0042-nucleus",
           "position": {"lat": 45.0, "lon": -122.0, "alt": None, "time": None},
           "channel_util": 3.5, "air_util_tx": 0.4},
    "channels": [{"index": 0, "name": "Natak"}, {"index": 1, "name": "Ops"}],
    "nodes": [{"num": PEER, "id": "!21d4c2cc", "short_name": "0052",
               "long_name": "0052-nucleus", "last_heard": 1791650000,
               "position": {"lat": 45.01, "lon": -122.0, "alt": None, "time": None}}],
    "traceroutes": {},
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "chat.db"
    nodes = tmp_path / "nodes.json"
    nodes.write_text(json.dumps(NODES_FILE))
    monkeypatch.setattr(meshchat, "CHAT_DB_PATH", str(db))
    monkeypatch.setattr(meshchat, "CLIENT_NODES_PATH", str(nodes))
    monkeypatch.setattr(mx, "_prefs", meshchat.Prefs(str(tmp_path / "prefs.json")))
    monkeypatch.setattr(mx, "_service_is_active", lambda: True)
    monkeypatch.setattr(mx.time, "time", lambda: 1791650100)
    store = meshchat.ChatStore(str(db))
    store.add("ch1", PEER, "ops up", 1791650050.0, 11)
    store.add("!21d4c2cc", PEER, "dm hi", 1791650060.0, 12)
    return store


def test_overview_lists_channels_then_dms(env):
    d = client.get("/api/v1/meshtastic/client").json()
    assert [c["chat"] for c in d["chats"]] == ["ch0", "ch1", "!21d4c2cc"]
    assert [c["title"] for c in d["chats"]] == ["Natak", "Ops", "0052-nucleus"]
    assert [c["unread"] for c in d["chats"]] == [0, 1, 1]
    assert d["status"]["online"] == 1 and d["status"]["channel_util"] == 3.5


def test_messages_marks_read_and_names_sender(env):
    d = client.get("/api/v1/meshtastic/client/messages", params={"chat": "ch1"}).json()
    assert d["messages"][0]["from"] == "0052" and d["messages"][0]["text"] == "ops up"
    chats = client.get("/api/v1/meshtastic/client").json()["chats"]
    assert {c["chat"]: c["unread"] for c in chats}["ch1"] == 0


def test_bad_chat_key_is_400(env):
    r = client.get("/api/v1/meshtastic/client/messages", params={"chat": "ch99"})
    assert r.status_code == 400


def test_ignored_node_hidden(env):
    r = client.put("/api/v1/meshtastic/client/nodes/!21d4c2cc/ignored", json={"on": True})
    assert r.status_code == 200
    chats = client.get("/api/v1/meshtastic/client").json()["chats"]
    assert "!21d4c2cc" not in [c["chat"] for c in chats]
    msgs = client.get("/api/v1/meshtastic/client/messages", params={"chat": "ch1"}).json()
    assert msgs["messages"] == []


def test_nodes_distance_and_favorite(env):
    client.put("/api/v1/meshtastic/client/nodes/!21d4c2cc/favorite", json={"on": True})
    n = client.get("/api/v1/meshtastic/client/nodes").json()["nodes"][0]
    assert n["favorite"] is True and n["distance_m"] == 1112 and n["bearing"] == 0


def test_send_relays_to_bridge(env, monkeypatch):
    sent = []
    monkeypatch.setattr(mx, "_client_rpc", lambda req: sent.append(req) or {"ok": True, "id": 5})
    r = client.post("/api/v1/meshtastic/client/messages",
                    json={"chat": "!21d4c2cc", "text": "hey", "reply_id": 12})
    assert r.status_code == 200
    assert sent == [{"cmd": "send", "chat": "!21d4c2cc", "text": "hey", "reply_id": 12}]


def test_node_action_validation(env):
    r = client.post("/api/v1/meshtastic/client/nodes/!21d4c2cc/reboot")
    assert r.status_code == 400
    r = client.post("/api/v1/meshtastic/client/nodes/bogus/traceroute")
    assert r.status_code == 400


def test_bridge_down_is_404(env, monkeypatch):
    monkeypatch.setattr(mx, "_service_is_active", lambda: False)
    r = client.post("/api/v1/meshtastic/client/nodes/!21d4c2cc/traceroute")
    assert r.status_code == 404
