"""Real-format ATAK fixtures for the node-notification tests.

SA_V1_HEX: one ATAK-CIV 5.4.0.28 (Pixel 4) SA datagram captured off br-lan on
0042-nucleus (239.2.3.1:6969, TAK Protocol V1 mesh header bf01bf), sender
10.20.42.55. Decodes (takproto) to uid ANDROID-a0c1758e6ca14314, type
a-f-G-U-C, contact callsign 0033 endpoint 10.20.42.55:4242:tcp.

BABEL_DUMP_*: babeld 1.13.1 monitor dumps in the exact format of the live node
(one wlan1 + one eth0 neighbour, as captured on 0042-nucleus), varied to
exercise link loss.
"""

SA_V1_HEX = (
    "bf01bf0a0012a1020a09612d662d472d552d431209556e646566696e65642a18414e44524f"
    "49442d613063313735386536636131343331343092c6b5cb9d343892c6b5cb9d34408a90ba"
    "cb9d344a036d2d6751892991442fb54240595fd218ada36853c061fca9f1d24da23a4069cd"
    "cccccccccc234071000000e0cf1263417aa7010a133c7569642044726f69643d2230303333"
    "222f3e121c0a1431302e32302e34322e35353a343234323a7463701204303033331a130a04"
    "4379616e120b5465616d204d656d626572220a0a0347505312034750532a02086432420a0e"
    "474f4f474c4520504958454c203412084154414b2d4349561a0233332222352e342e302e32"
    "3820286563623461303837292e313736343334303334372d4349563a0911a490f7dcd4bf47"
    "40"
)
SA_V1 = bytes.fromhex(SA_V1_HEX)
SA_UID = "ANDROID-a0c1758e6ca14314"
SA_CALLSIGN = "0033"
SA_SRC = "10.20.42.55"

# ATAK legacy XML SA (Protocol V0), same device, UDP endpoint advertised.
SA_XML_UDP = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    b'<event version="2.0" uid="ANDROID-a0c1758e6ca14314" type="a-f-G-U-C" '
    b'how="m-g" time="2026-11-14T09:38:17.636Z" start="2026-11-14T09:38:17.636Z" '
    b'stale="2026-11-14T09:39:32.636Z"><point lat="37.4" lon="-77.6" hae="26.6" '
    b'ce="9.9" le="9999999.0"/><detail><contact callsign="0033" '
    b'endpoint="10.20.42.55:4242:udp"/></detail></event>'
)

_HDR = (
    "BABEL 1.0\n"
    "version babeld-1.13.1\n"
    "host 0042-nucleus\n"
    "my-id 2e:cf:67:ff:fe:6d:8b:b6\n"
    "ok\n"
    "add interface wlan1 up true ipv6 fe80::b2a4:1dfc:eb1d:438e ipv4 10.20.1.42\n"
    "add interface br-lan up true ipv6 fe80::379:751b:776:51d3 ipv4 10.20.42.1\n"
    "add interface eth0 up true ipv6 fe80::2ecf:67ff:fe6d:8bb6 ipv4 10.0.0.14\n"
    "add interface halow0 up false\n"
)
WLAN_NBR = ("add neighbour 55a20a8670 address fe80::8ae5:2ce0:5629:f96e if wlan1 "
            "reach effb ureach 0000 rxcost 292 txcost 256 cost 292\n")
WLAN_NBR_INF = ("add neighbour 55a20a8670 address fe80::8ae5:2ce0:5629:f96e if wlan1 "
                "reach 0000 ureach 0000 rxcost 65535 txcost 256 cost 65535\n")
ETH_NBR = ("add neighbour 55a20a7c70 address fe80::8aa2:9eff:fee1:c8f2 if eth0 "
           "reach ffff ureach 0000 rxcost 96 txcost 96 cost 96\n")
ROUTES = (
    "add route 55a20a9030 prefix 10.20.47.0/24 from 0.0.0.0/0 installed yes "
    "id 8a:a2:9e:ff:fe:e1:c8:f2 metric 96 refmetric 0 via fe80::8aa2:9eff:fee1:c8f2 if eth0\n"
    "add route 55a20a89e0 prefix 10.20.47.0/24 from 0.0.0.0/0 installed no "
    "id 8a:a2:9e:ff:fe:e1:c8:f2 metric 292 refmetric 0 via fe80::8ae5:2ce0:5629:f96e if wlan1\n"
)

BABEL_DUMP_BOTH = _HDR + WLAN_NBR + ETH_NBR + ROUTES + "ok\n"
# wlan1 neighbour gone, same node still reachable over eth0.
BABEL_DUMP_ETH_ONLY = _HDR + ETH_NBR + ROUTES.splitlines(True)[0] + "ok\n"
# wlan1 neighbour still listed but at infinite cost (hellos lost).
BABEL_DUMP_WLAN_INF = _HDR + WLAN_NBR_INF + ETH_NBR + ROUTES + "ok\n"
# babeld closed mid-dump: header ok only, no end-of-dump ok.
BABEL_DUMP_TRUNCATED = _HDR + ETH_NBR
