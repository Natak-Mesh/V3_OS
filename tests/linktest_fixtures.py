"""Real-format tool output for the link-test tests, captured on 0042-nucleus.

iperf3 3.18 -J (trimmed to the keys the parser reads), iputils ping -q, rnprobe
-v (RNS 1.x, spinner backspaces included) and /proc/net/tcp.
"""

import json

IPERF_SEND = json.dumps({
    "start": {"connected": [{"socket": 5, "local_host": "10.20.1.42", "local_port": 52112,
                             "remote_host": "10.20.1.47", "remote_port": 5201}],
              "version": "iperf 3.18",
              "test_start": {"protocol": "TCP", "num_streams": 1, "blksize": 131072,
                             "omit": 0, "duration": 5, "bytes": 0, "blocks": 0,
                             "reverse": 0, "tos": 0, "target_bitrate": 0, "bidir": 0,
                             "fqrate": 0, "interval": 1}},
    "intervals": [],
    "end": {"sum_sent": {"start": 0, "end": 5.002192, "seconds": 5.002192,
                         "bytes": 14024704, "bits_per_second": 22429449.3,
                         "retransmits": 12, "sender": True},
            "sum_received": {"start": 0, "end": 5.032469, "seconds": 5.032469,
                             "bytes": 13893632, "bits_per_second": 22086572.1,
                             "sender": True},
            "cpu_utilization_percent": {"host_total": 9.2, "remote_total": 7.7}},
})

IPERF_RECEIVE = json.dumps({
    "start": {"connected": [], "version": "iperf 3.18",
              "test_start": {"protocol": "TCP", "num_streams": 1, "duration": 5,
                             "reverse": 1}},
    "intervals": [],
    "end": {"sum_sent": {"start": 0, "end": 5.003269, "seconds": 5.003269,
                         "bytes": 2621440, "bits_per_second": 4191558.4,
                         "retransmits": 0, "sender": False},
            "sum_received": {"start": 0, "end": 5.002988, "seconds": 5.002988,
                             "bytes": 2490368, "bits_per_second": 3982211.9,
                             "sender": False}},
})

# Exact iperf3 -J output when no server is listening on the peer.
IPERF_REFUSED = (
    '{\n\t"start":\t{\n\t\t"connected":\t[],\n\t\t"version":\t"iperf 3.18",\n'
    '\t\t"system_info":\t"Linux 0042-nucleus 6.18.50+rpt-rpi-v8 #1 SMP PREEMPT '
    'Debian 1:6.18.50-1+rpt1 (2026-09-11) aarch64"\n\t},\n\t"intervals":\t[],\n'
    '\t"end":\t{\n\t},\n\t"error":\t"unable to connect to server - server may have '
    'stopped running or use a different port, firewall issue, etc.: Connection refused"\n}\n'
)

PING_OK = (
    "PING 10.20.1.47 (10.20.1.47) from 10.20.1.42 wlan1: 56(84) bytes of data.\n"
    "\n--- 10.20.1.47 ping statistics ---\n"
    "3 packets transmitted, 3 received, 0% packet loss, time 402ms\n"
    "rtt min/avg/max/mdev = 0.622/1.056/1.755/0.498 ms\n"
)
PING_DEAD = (
    "PING 10.30.1.99 (10.30.1.99) from 10.30.1.42 halow0: 56(84) bytes of data.\n"
    "\n--- 10.30.1.99 ping statistics ---\n"
    "3 packets transmitted, 0 received, 100% packet loss, time 407ms\n\n"
)
PING_NO_DEV_STDERR = "ping: SO_BINDTODEVICE nosuch0: No such device\n"

_SPIN = "\b\b⢄ \b\b⢂ \b\b⢁ \b\b⡁ \b\b⡈ \b\b⡐ "
RNPROBE_OK = (
    "Path to <fa65075c2a28f00976d5542e7c87ebfa> requested   " + _SPIN + "\n"
    "Sent probe 1 (16 bytes) to <fa65075c2a28f00976d5542e7c87ebfa> via "
    "<34dd0a1dd2aae0d5c90c9b6a6879b458> on AutoInterfacePeer[eth0/fe80::8aa2:9eff:fee1:c8f2]"
    "   \b\b⢄ \b\b \nValid reply from <fa65075c2a28f00976d5542e7c87ebfa>\n"
    "Round-trip time is 10.353 milliseconds over 1 hop\n\n\n"
    "Sent probe 2 (16 bytes) to <fa65075c2a28f00976d5542e7c87ebfa> via "
    "<34dd0a1dd2aae0d5c90c9b6a6879b458> on AutoInterfacePeer[eth0/fe80::8aa2:9eff:fee1:c8f2]"
    "   \b\b⢄ \b\b \nValid reply from <fa65075c2a28f00976d5542e7c87ebfa>\n"
    "Round-trip time is 8.211 milliseconds over 1 hop\n\n"
    "Sent 2, received 2, packet loss 0.0%\n"
)
# Over the RNode: rnprobe appends the shared instance's RF stats to the RTT line.
RNPROBE_RNODE = (
    "Sent probe 1 (16 bytes) to <fa65075c2a28f00976d5542e7c87ebfa> on "
    "RNodeInterface[RNode LoRa]   \b\b⢄ \b\b \n"
    "Valid reply from <fa65075c2a28f00976d5542e7c87ebfa>\n"
    "Round-trip time is 1.874 seconds over 1 hop [RSSI -97 dBm] [SNR 6.25 dB] "
    "[Link Quality 82.0%]\n\n"
    "\rSent probe 2 (16 bytes) to <fa65075c2a28f00976d5542e7c87ebfa> on "
    "RNodeInterface[RNode LoRa]   \b\b⢄ \n"
    "                                                          \rProbe timed out\n"
    "Sent 2, received 1, packet loss 50.0%\n"
)
RNPROBE_NOPATH = (
    "Path to <00112233445566778899aabbccddeeff> requested   " + _SPIN + "\b\b⢄ \n"
    "                                                          \nPath request timed out\n"
)

# /proc/net/tcp with iperf3 listening on 10.20.1.42:5201 (0x1451).
PROC_NET_TCP = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    "   0: 0100007F:1538 00000000:0000 0A 00000000:00000000 00:00000000 00000000   103        0 7895 1 00000000c42a4ba3 100 0 0 10 0\n"
    "   1: 012A140A:1092 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 15176 1 0000000019fd7f21 100 0 0 10 0\n"
    "   2: 2A01140A:1451 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 77231 1 00000000e030247e 100 0 0 10 0\n"
    "   3: 2A01140A:1451 2F01140A:D0A4 01 00000000:00000000 00:00000000 00000000     0        0 77232 1 00000000e030247f 20 4 30 10 -1\n"
)
