"""Real-format meshtastic-lib packet dicts for the Meshtastic client tests.

These are the exact dicts the meshtastic lib (2.7.11) publishes on
"meshtastic.receive" — generated from real MeshPacket protobufs via
MessageToDict, with decoded.payload as bytes (what the lib substitutes).
"""

ME = 805795615        # 0x3007771f — this node (0042)
PEER = 567591628      # 0x21d4c2cc — 0052

CH1_BROADCAST = {
    "from": PEER, "to": 4294967295, "channel": 1,
    "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"hello ch1"},
    "id": 1234567, "rxTime": 1791650000,
}
PRIMARY_BROADCAST = {
    "from": PEER, "to": 4294967295,
    "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"hello all"},
    "id": 1234568, "rxTime": 1791650001,
}
DM_REACTION = {
    "from": PEER, "to": ME,
    "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"\xf0\x9f\x91\x8d",
                "replyId": 999, "emoji": 1},
    "id": 1234569, "rxTime": 1791650002,
}
DM_TO_OTHER = {
    "from": PEER, "to": 0x67c599e4,
    "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"not for us"},
    "id": 1234570, "rxTime": 1791650003,
}
ACK = {"from": PEER, "to": ME, "id": 55,
       "decoded": {"portnum": "ROUTING_APP", "payload": b"\x18\x00",
                   "requestId": 777, "routing": {"errorReason": "NONE"}}}
NAK_LOCAL = {"from": ME, "to": ME, "id": 56,
             "decoded": {"portnum": "ROUTING_APP", "requestId": 777,
                         "routing": {"errorReason": "MAX_RETRANSMIT"}}}
IMPLICIT_ACK_LOCAL = {"from": ME, "to": ME, "id": 57,
                      "decoded": {"portnum": "ROUTING_APP", "requestId": 777,
                                  "routing": {"errorReason": "NONE"}}}
TRACEROUTE = {"portnum": "TRACEROUTE_APP",
              "traceroute": {"route": [1741003236], "snrTowards": [40, 22],
                             "routeBack": [1741003236], "snrBack": [30, -128]}}
