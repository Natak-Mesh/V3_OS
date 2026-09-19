"""Regression tests for the single-store cross-transport message de-dupe.

Real-format fixtures mirror what the two transports actually deliver:
  * WiFi  copy: sender = the node's hostname (e.g. '0042-nucleus'), plain text.
  * LoRa  copy: same sender name resolved from the Meshtastic node DB, same text.
Both must collapse into ONE stored entry tagged with both transports.
"""

import time

from nucleusd.messaging.store import MessageStore, content_key


def test_content_key_ignores_transport():
    assert content_key("0042-nucleus", "move to OP") == content_key("0042-nucleus", "move to OP")
    assert content_key("0042-nucleus", "a") != content_key("0043-nucleus", "a")


def test_wifi_then_lora_collapse_to_one_entry():
    s = MessageStore(dedupe_window_secs=60, history_limit=100)
    m1, new1 = s.ingest("0042-nucleus", "move to OP", "wifi", ts=1000.0)
    m2, new2 = s.ingest("0042-nucleus", "move to OP", "lora", ts=1000.4)
    assert new1 is True and new2 is False
    assert m1 is m2
    assert s.history() == [m1]
    assert m1["transports"] == ["wifi", "lora"]


def test_distinct_messages_not_merged():
    s = MessageStore(dedupe_window_secs=60, history_limit=100)
    s.ingest("0042-nucleus", "hello", "wifi", ts=1000.0)
    s.ingest("0042-nucleus", "world", "lora", ts=1000.1)
    assert len(s.history()) == 2


def test_duplicate_outside_window_is_new():
    s = MessageStore(dedupe_window_secs=30, history_limit=100)
    s.ingest("0042-nucleus", "sitrep", "wifi", ts=1000.0)
    m2, new2 = s.ingest("0042-nucleus", "sitrep", "lora", ts=1040.0)
    assert new2 is True
    assert len(s.history()) == 2


def test_meshtastic_only_sender_ingests():
    s = MessageStore(dedupe_window_secs=60, history_limit=100)
    # A plain Meshtastic radio (no WiFi copy ever arrives).
    m, new = s.ingest("Bravo6", "at the LZ", "lora", ts=1000.0)
    assert new is True
    assert m["transports"] == ["lora"]
    assert m["sender"] == "Bravo6"


def test_history_since_filter():
    s = MessageStore(dedupe_window_secs=60, history_limit=100)
    s.ingest("a", "1", "wifi", ts=1000.0)
    s.ingest("b", "2", "wifi", ts=2000.0)
    later = s.history(since=1500.0)
    assert [m["text"] for m in later] == ["2"]


def test_history_limit_trims_oldest():
    s = MessageStore(dedupe_window_secs=1, history_limit=3)
    for i in range(5):
        s.ingest(f"n{i}", f"msg{i}", "wifi", ts=1000.0 + i * 10)
    texts = [m["text"] for m in s.history()]
    assert texts == ["msg2", "msg3", "msg4"]


def test_out_of_order_arrival_sorted_by_ts():
    # A late LoRa copy arrives after a newer message; history must read by ts,
    # not by arrival order.
    s = MessageStore(dedupe_window_secs=5, history_limit=100)
    s.ingest("a", "first", "wifi", ts=1000.0)
    s.ingest("b", "third", "wifi", ts=1002.0)
    s.ingest("c", "second", "lora", ts=1001.0)   # arrives last, older ts
    assert [m["text"] for m in s.history()] == ["first", "second", "third"]


def test_persistence_reorders_arrival_order(tmp_path):
    # JSONL persisted in arrival order must load normalised to ts order.
    p = tmp_path / "messages.jsonl"
    with open(p, "w") as f:
        f.write('{"id":"x","sender":"a","text":"late","ts":1002.0,"transports":["wifi"],"mine":false}\n')
        f.write('{"id":"y","sender":"b","text":"early","ts":1000.0,"transports":["wifi"],"mine":false}\n')
    s = MessageStore(dedupe_window_secs=60, history_limit=100, persist_path=str(p))
    assert [m["text"] for m in s.history()] == ["early", "late"]


def test_persistence_roundtrip(tmp_path):
    p = tmp_path / "messages.jsonl"
    s = MessageStore(dedupe_window_secs=60, history_limit=100, persist_path=str(p))
    s.ingest("0042-nucleus", "persisted", "wifi", ts=1000.0)
    s2 = MessageStore(dedupe_window_secs=60, history_limit=100, persist_path=str(p))
    assert [m["text"] for m in s2.history()] == ["persisted"]
    # Loaded entries still absorb a late duplicate from the other transport.
    _m, new = s2.ingest("0042-nucleus", "persisted", "lora", ts=1000.0)
    assert new is False
