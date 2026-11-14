"""
Nucleus V3 OS — configuration schema (single source of truth).

The entire node configuration lives in one YAML file (config/config.yaml,
installed to /etc/nucleus/config.yaml). This module defines the pydantic model
that validates that file and computes every *derived* value so an operator only
ever sets a handful of primitives (chiefly the mesh/AP passwords). The node
identity (`node.id`) is parsed from the system hostname by default, so a freshly
flashed node needs no identity edits at all.

Design rules:
  * Operators set primitives; the schema derives the rest.
  * Validation lives here, so every consumer — the REST API, the CLI, and the
    template renderer — shares one contract.
  * Nothing here touches the live system; this module is pure and unit-testable
    off-box (no root, no hardware).
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# --- USB device registry -----------------------------------------------------
# Single source of truth for the pluggable USB devices Nucleus recognises and
# the stable name each one is bound to by the generated udev rule. Keyed by
# role; each value is a dict carrying:
#   * "kind" — the udev match kind: "tty" (a serial device bound to a stable
#     /dev/nucleus/<role> symlink) or "net" (a network interface renamed to a
#     fixed ifname).
#   * "ids"  — the supported (idVendor, idProduct) pairs as lowercase hex
#     (exactly how udev's ATTRS{idVendor}/{idProduct} match them).
#   * "ifname" — net kind only: the fixed interface name the device is renamed
#     to (e.g. "halow0").
# A device whose USB id is not listed here gets no symlink/rename, so no Nucleus
# service ever opens it.
#
#   rnode — Reticulum RNode LoRa radios attached over USB. Only the RAK4631
#           (nRF52840 + SX1262) is supported for now. (tty)
#   halow — Morse Micro MM8108 Wi-Fi HaLow (802.11ah) transceiver, presented as
#           a network interface renamed to halow0. (net)
#   modem — the serial/PTT side of a soundcard-TNC adapter (e.g. DigiRig Mobile),
#           a Silicon Labs CP210x UART bridge whose RTS/DTR line keys the radio's
#           PTT. Bound to /dev/nucleus/modem so modem73's PTT never depends on the
#           plug-order /dev/ttyUSB* name. NOTE: the CP210x USB id (10c4:ea60) is
#           generic to countless serial adapters, unlike the RAK-specific rnode id,
#           so if another CP210x device is plugged in this symlink is ambiguous.
#           (tty)
USB_DEVICES: dict[str, dict] = {
    "rnode": {
        "kind": "tty",
        "ids": [("239a", "8029")],  # RAKwireless WisBlock RAK4631
    },
    "modem": {
        "kind": "tty",
        "ids": [("10c4", "ea60")],  # Silicon Labs CP210x UART bridge (DigiRig)
        # Device-start the modem73 service when this adapter appears, so it never
        # races udev creating /dev/nucleus/modem (the PTT-open race). rnode has no
        # service key — it's just an rnsd interface — so its tty rule stays plain.
        "service": "nucleus-modem73",
    },
    "halow": {
        "kind": "net",
        "ids": [("325b", "8100")],  # Morse Micro MM8108 HaLow transceiver
        "ifname": "halow0",
    },
}

# Stable path a supported RNode is bound to by the generated udev rule.
RNODE_DEVICE_PATH = "/dev/nucleus/rnode"

# Stable path the modem73 serial/PTT adapter is bound to by the generated udev
# rule (parallels RNODE_DEVICE_PATH), so modem73's --com-port never depends on
# the plug-order /dev/ttyUSB* name.
MODEM_DEVICE_PATH = "/dev/nucleus/modem"

# --- modem73 mode registry ---------------------------------------------------
# Single source of truth mapping a named, operator-selectable mode to the
# modem73 settings that produce it AND the exact on-air bitrate modem73 reports
# for it. The two sides must agree: the launch script (nucleus-modem73-up.sh)
# passes the modem flags, and the RNS TCPClientInterface (reticulum config) is
# told the matching `bitrate` so its framing, timeouts and announce rate-cap
# are sized to the real link. Deriving both from one entry means they can
# never drift. The live web UI (nucleusd/modem73) reads the same table.
#
# Families:
#   * ofdm   — modem73's "fast family": hundreds of PSK/QAM carriers in
#     2400 Hz; the mode to "use for anything over FM" (modem73 manual).
#     Modulation order + FEC code rate trade throughput for required SNR.
#   * robust — RDM-1200: a hardier single-mode fallback for weak links that
#     still moves a full packet in ~3.5 s.
#
# Selection rule (squad-level data link): a full 500 B Reticulum packet must go
# out in under ~4 s, i.e. >= ~1000 bps. That drops OFDM BPSK (788 bps), every
# MFSK mode and every other RDM mode (<= 781 bps). Configs still naming the
# dropped bpsk_1_2 are migrated to qpsk_1_2 (see ReticulumConfig).
#
# `payload` is the PHY frame capacity in bytes modem73 reports (get_config
# payload_size, 2 B of it is a length prefix). Every mode here is >= 512 B, so
# a 500 B RNS packet always fits one frame: no modem fragmentation is needed
# (modem73 TRUNCATES an oversize frame when --frag is off). RNS itself refuses
# fixed_mtu < 500, so MODEM73_MIN_PAYLOAD is enforced by tests.
#
# `bitrate`/`payload` are what modem73 2.4.2 prints/reports for that exact mode
# with NORMAL frames — measured on node 0042 via the control port, not estimated.
MODEM73_MODES: dict[str, dict] = {
    #  key            family     modulation  rate   bitrate(bps) payload(B)
    "rdm_1200":    {"family": "robust", "modulation": "RDM-1200", "rate": None,
                    "bitrate": 1150, "payload": 512},
    "qpsk_1_2":    {"family": "ofdm", "modulation": "QPSK",    "rate": "1/2",
                    "bitrate": 1577, "payload": 512},
    "qam16_1_2":   {"family": "ofdm", "modulation": "QAM16",   "rate": "1/2",
                    "bitrate": 3155, "payload": 1024},
    "qam16_2_3":   {"family": "ofdm", "modulation": "QAM16",   "rate": "2/3",
                    "bitrate": 4215, "payload": 1368},
    "qam64_2_3":   {"family": "ofdm", "modulation": "QAM64",   "rate": "2/3",
                    "bitrate": 6406, "payload": 2736},
    "qam64_3_4":   {"family": "ofdm", "modulation": "QAM64",   "rate": "3/4",
                    "bitrate": 7193, "payload": 3072},
    "qam256_3_4":  {"family": "ofdm", "modulation": "QAM256",  "rate": "3/4",
                    "bitrate": 9464, "payload": 3072},
    "qam256_5_6":  {"family": "ofdm", "modulation": "QAM256",  "rate": "5/6",
                    "bitrate": 10500, "payload": 3408},
    "qam4096_5_6": {"family": "ofdm", "modulation": "QAM4096", "rate": "5/6",
                    "bitrate": 15959, "payload": 6816},
}

# Smallest PHY frame a mode may have: a 500 B RNS packet + 2 B length prefix.
MODEM73_MIN_PAYLOAD = 502

# Dropped modes → their replacement, applied when a config is loaded so an
# older config.yaml still validates (and `nucleusctl apply` normalizes it).
MODEM73_MODE_MIGRATIONS: dict[str, str] = {"bpsk_1_2": "qpsk_1_2"}

# Default mode: QPSK 1/2 — modem73's own default, and the slowest OFDM mode
# that keeps a full packet under the airtime budget.
MODEM73_DEFAULT_MODE = "qpsk_1_2"


def modem73_mode_flags(mode: str) -> str:
    """modem73 CLI flags selecting `mode` (from MODEM73_MODES)."""
    m = MODEM73_MODES[mode]
    if m["family"] == "robust":
        return f"--robust-mode {m['modulation']}"
    return f"--modem ofdm --modulation {m['modulation']} --rate {m['rate']}"


def modem73_mode_from_live(cfg: dict) -> Optional[str]:
    """Map a modem73 control-port get_config dict back to a MODEM73_MODES key.

    Returns None when the live modem runs a mode outside the registry (e.g. one
    set from the TUI)."""
    t = cfg.get("modem_type")
    for key, m in MODEM73_MODES.items():
        if m["family"] == "robust" and t == 2 and cfg.get("modulation") == m["modulation"]:
            return key
        if (m["family"] == "ofdm" and t == 0 and cfg.get("modulation") == m["modulation"]
                and cfg.get("code_rate") == m["rate"]):
            return key
    return None

# Fixed interface name the HaLow radio is renamed to by the generated udev rule
# (parallels RNODE_DEVICE_PATH). The interface the driver enumerates as
# (wlan2/wlan3) is not stable across reloads, so everything downstream — the
# networkd file, supplicant, babeld, smcroute, messaging/voice — uses this name.
HALOW_IFACE = "halow0"

# ATAK multicast destinations (CoT/SA, GeoChat/discovery, voice) that get the
# TTL bump and are accepted on eth0. Single source for the firewall template.
ATAK_MCAST_DSTS: tuple[str, ...] = ("239.2.3.1", "224.10.10.1", "239.255.255.0/24")

# US S1G channel table — single source of truth, transcribed from the Morse
# driver's dot11ah/s1g_channels_rules.c (us_s1g_channels[]). Keyed by channel
# bandwidth in MHz; each value is the tuple of valid S1G channel numbers at that
# width. HaLow config/API/UI use these real S1G numbers, never the 5 GHz
# stand-ins the driver reports to iw (see docs/halow.md §6).
HALOW_CHANNELS: dict[int, tuple[int, ...]] = {
    1: (1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35,
        37, 39, 41, 43, 45, 47, 49, 51),
    2: (2, 6, 10, 14, 18, 22, 26, 30, 34, 38, 42, 46, 50),
    4: (8, 16, 24, 32, 40, 48),
    8: (12, 28, 44),
}

# US operating class per channel bandwidth (MHz). Confirmed against the Morse
# hostap fork defaults (DEFAULT_MORSE_IBSS_OP_CLASS 71 is the 8 MHz case).
HALOW_OP_CLASS: dict[int, int] = {1: 68, 2: 69, 4: 70, 8: 71}

HalowBandwidth = Literal[1, 2, 4, 8]

# Deferred HaLow work (docs/halow.md §10.9) — do NOT build without a decision:
#   * Cross-mesh multicast relay (wlan1<->halow0 via smcroute, one-cross TTL guard
#     + per-node halow.mcast_relay flag). Deferred §10.0.3; revisit only if
#     HaLow-only nodes must exchange *multicast* with WiFi-only nodes (unicast
#     already crosses via Babel). No mcast_relay key is added until then.
#   * rf.py on/off for HaLow (rfkill soft-block like wlan1/AP). halow.enabled +
#     the service reconcile already give config-driven on/off.
#   * Non-US regulatory tables. Add per-country HALOW_CHANNELS/op-class maps when
#     needed; until then the country gate fails fast (see _halow_country_gate).

# RNode radio limits — single source of truth for ReticulumConfig's rnode_*
# fields, so the API/UI/tests read bounds from the schema instead of copying
# them. The RAK4631's SX1262 covers roughly 150 MHz-960 MHz; the LoRa bandwidth
# is one of the chip's discrete steps (Hz).
RNODE_FREQ_MIN_HZ = 150_000_000
RNODE_FREQ_MAX_HZ = 960_000_000
RNODE_BANDWIDTHS: tuple[int, ...] = (
    7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000,
)
RNodeBandwidth = Literal[
    7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000
]


def _ll_from_seed(seed: str) -> str:
    """Deterministically derive a unique IPv6 link-local address from a seed.

    Babel needs a stable, per-node-unique fe80::/64 address on each interface.
    We hash the seed and pack 64 bits into the interface identifier so the same
    node always gets the same address without the operator hand-picking one
    (the old mesh.conf required pasting these by hand).
    """
    h = hashlib.sha256(seed.encode()).digest()
    iid = h[:8]  # 64-bit interface identifier
    parts = [f"{iid[i] << 8 | iid[i + 1]:x}" for i in range(0, 8, 2)]
    return "fe80::" + ":".join(parts)


def _id_from_hostname() -> Optional[int]:
    """Parse the node id from the system hostname (e.g. '0042-nucleus' -> 42).

    Hostname is the provisioning-time identity (set when the SD card is flashed),
    so a freshly-imaged node needs zero config edits. Returns None if the
    hostname doesn't match the NNNN-nucleus pattern.
    """
    host = socket.gethostname().split(".")[0]
    m = re.match(r"^(\d{1,4})-nucleus$", host)
    return int(m.group(1)) if m else None


class NodeConfig(BaseModel):
    """Identity of this node. `id` drives most derived addressing.

    `id` is optional: when omitted it is parsed from the system hostname
    (NNNN-nucleus). An explicit value overrides the hostname.
    """

    id: Optional[int] = Field(None, ge=1, le=254, description="Node serial (1-254). Defaults to the id parsed from the hostname.")
    name: Optional[str] = Field(None, description="Hostname override; defaults to system hostname / '<id:04d>-nucleus'.")

    @model_validator(mode="after")
    def _resolve_id(self) -> "NodeConfig":
        if self.id is None:
            derived = _id_from_hostname()
            if derived is None:
                raise ValueError(
                    "node.id not set and could not be parsed from hostname "
                    f"'{socket.gethostname()}' (expected NNNN-nucleus)"
                )
            object.__setattr__(self, "id", derived)
        return self

    @property
    def hostname(self) -> str:
        return self.name or f"{self.id:04d}-nucleus"

    @property
    def short(self) -> str:
        return f"{self.id:04d}"


class MeshConfig(BaseModel):
    """802.11s wireless mesh on wlan1 + Babel/multicast tuning."""

    enabled: bool = Field(
        True,
        description="Run the wlan1 802.11s mesh (nucleus-mesh/babeld/smcroute + phy1 RF). "
                    "Persists across reboots; enforced at boot by nucleus-rf.service.",
    )
    ssid: str = "natak_mesh"
    channel: int = Field(3, ge=1, le=13, description="2.4 GHz channel.")
    password: str = Field(..., min_length=8, description="SAE/WPA3 mesh passphrase.")
    subnet_prefix: str = Field("10.20.1", description="First three octets of the mesh /24.")
    country: str = "US"

    # Multicast / TTL tuning — ported verbatim from Nucleus_OS mesh.conf.
    mcast_ttl: int = Field(8, ge=0, le=64, description="IP TTL forced on br-lan multicast ingress.")
    mesh_802_ttl: int = Field(8, ge=0, le=31, description="802.11s L2 mesh_ttl / mesh_element_ttl.")
    rts_threshold: int = Field(500, ge=0, description="RTS/CTS threshold bytes (0=off).")

    @property
    def frequency(self) -> int:
        """2.4 GHz centre frequency for the channel (matches config_generation.sh)."""
        return 2407 + self.channel * 5

    @property
    def subnet(self) -> str:
        return f"{self.subnet_prefix}.0/24"

    @field_validator("subnet_prefix")
    @classmethod
    def _valid_prefix(cls, v: str) -> str:
        ipaddress.ip_address(f"{v}.0")  # raises if malformed
        return v


class BrLanConfig(BaseModel):
    """Wired/AP-side bridge (br-lan): wlan0 AP + eth0 LAN clients live here."""

    subnet_prefix: Optional[str] = Field(
        None,
        description="First three octets of the br-lan /24. Default: '10.20.<node.id>'.",
    )
    dhcp_pool_offset: int = 10
    dhcp_pool_size: int = 50
    dns: str = "8.8.8.8"


class ApConfig(BaseModel):
    """Onboard access point on wlan0 (5 GHz), bridged into br-lan by hostapd."""

    channel: int = Field(36, description="5 GHz channel.")
    password: str = Field(..., min_length=8)
    name: Optional[str] = Field(None, description="SSID; defaults to '<id:04d>-nucleus-ap'.")


Eth0McastMode = Literal["off", "br_lan", "mesh_relay"]


class Eth0Config(BaseModel):
    """eth0 defaults to WAN (DHCP client / internet gateway)."""

    mode: str = Field("wan", pattern="^(wan|lan)$")
    static_ip: Optional[str] = None
    dhcp_pool_offset: int = 10
    dhcp_pool_size: int = 100
    # Hard-line (fiber/copper) node-to-node links over eth0. Both OFF by default
    # (opt-in per node: docs/manual/config.md "Wired node link") and independent
    # of eth0 mode (wan or lan).
    babel: bool = Field(
        False,
        description="Run Babel routing on eth0 so nodes joined by a hard line exchange "
                    "routes over it. Wired cost is lower than radio, so the wire is "
                    "preferred whenever both paths exist. Off by default.",
    )
    mcast_mode: Eth0McastMode = Field(
        "off",
        description="ATAK multicast (CoT, discovery, voice) over eth0 via smcroute. "
                    "off (default) = none; br_lan = eth0<->br-lan only; mesh_relay = also "
                    "eth0<->wlan1/halow0, so an RF-silent node on the wire reaches "
                    "the radio meshes through this node. At most ONE mesh_relay node "
                    "per eth0 segment (two loop multicast wire<->mesh).",
    )
    mcast_source_node_ids: list[int] = Field(
        default_factory=list,
        description="mesh_relay only: node ids of the RF-silent nodes on the wire. "
                    "Their br-lan subnets (10.20.<id>.0/24) are accepted as sources "
                    "for the CoT bridge's multicast -> LoRa TX.",
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_mcast_bridge(cls, data):
        # Legacy bool `mcast_bridge` (<= feat/eth0-fiber-link first cut) maps to
        # mcast_mode: true -> br_lan, false -> off. An explicit mcast_mode wins.
        if isinstance(data, dict) and "mcast_bridge" in data:
            data = dict(data)
            legacy = str(data.pop("mcast_bridge")).strip().lower()
            data.setdefault("mcast_mode",
                            "br_lan" if legacy in ("true", "1", "yes", "on") else "off")
        return data

    @field_validator("mcast_source_node_ids")
    @classmethod
    def _valid_source_ids(cls, v: list[int]) -> list[int]:
        for n in v:
            if not 1 <= n <= 254:
                raise ValueError(f"mcast_source_node_ids: {n} not in 1-254")
        return sorted(set(v))

    @property
    def mcast_bridge(self) -> bool:
        """True when any eth0 ATAK multicast bridging is on (br_lan or mesh_relay)."""
        return self.mcast_mode != "off"

    @property
    def mcast_mesh_relay(self) -> bool:
        return self.mcast_mode == "mesh_relay"



class HeartbeatConfig(BaseModel):
    """Presence heartbeat sent by the CoT bridge.

    A tiny periodic broadcast so peers keep this node in their LoRa node list
    even when no ATAK traffic is flowing. Read live by cot-bridge (no restart
    needed). Nodes drop off a peer's list after 15 min of silence.
    """

    enabled: bool = Field(True, description="Send the periodic presence heartbeat.")
    interval_secs: int = Field(
        300, ge=60, le=3600,
        description="Seconds between heartbeats (60–3600). Default 300 (5 min).",
    )


class MeshtasticConfig(BaseModel):
    """Meshtastic LoRa radio (native meshtasticd) + ATAK CoT bridge.

    meshtasticd runs natively (apt package) and exposes its API on TCP
    localhost:4403. We render /etc/meshtasticd/config.yaml directly rather than
    relying on config.d overlay merging (which is unreliable) — the `hat` value
    selects the LoRa pin block and `gps` selects the GPS wiring.
    """

    enabled: bool = Field(True, description="Run meshtasticd + expose the radio to the CoT bridge.")
    tx_enabled: bool = Field(
        True,
        description="Allow the LoRa radio to transmit (firmware lora.tx_enabled). "
                    "False = receive-only: the radio stays powered and keeps RX, but never TX. "
                    "Persists across reboots.",
    )
    region: str = Field("US", description="LoRa region code (US, EU_868, ...). Radio won't TX until set.")
    hat: str = Field(
        "rak6421-slot2",
        pattern="^(rak6421-slot1|rak6421-slot2|auto)$",
        description="LoRa HAT/slot: rak6421-slot1 | rak6421-slot2 | auto.",
    )
    gps: str = Field(
        "uart",
        pattern="^(off|uart|i2c)$",
        description="GPS wiring: off | uart | i2c.",
    )
    gps_serial_path: str = Field("/dev/ttyS0", description="Serial device for a UART GPS.")
    i2c_device: str = Field("/dev/i2c-1", description="I2C bus device for an I2C GPS.")
    cot_bridge: bool = Field(True, description="Run the ATAK CoT <-> LoRa bridge (cot-bridge.service).")
    tx_min_interval_secs: int = Field(
        2, ge=0, le=3600,
        description="Min seconds between LoRa TX of the same CoT UID (0 = no limit). Read live by cot-bridge.",
    )
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig, description="Presence heartbeat settings.")


class VoiceConfig(BaseModel):
    """Mesh PTT voice (nucleus-voice.service).

    Real-time push-to-talk voice over the wlan1 802.11s mesh (UDP multicast).
    Two interchangeable PTT front-ends feed the same transport:
      * Hardware PTT — OpenVLM CM108 tactical headset (hot-plug), and
      * Soft PTT     — a phone/browser on the node AP, via the /voice page (WS).
    An optional LoRa voice-text path (Vosk STT → one Meshtastic text packet →
    Piper TTS) is relayed through the CoT bridge. See docs/manual/voice-internals.md.
    """

    enabled: bool = Field(True, description="Run the mesh PTT voice daemon (nucleus-voice.service).")
    channel: int = Field(1, ge=1, le=254, description="Startup voice channel (multicast group 239.10.10.N).")
    channels: str = Field(
        "1:Command",
        description='Named channel list "N:Label,N:Label" (e.g. "1:Command,2:Squad").',
    )
    jitter_ms: int = Field(80, ge=20, le=500, description="Per-source RX jitter buffer before playback (ms).")
    tx_gain: float = Field(4.0, ge=0.0, le=32.0, description="Software mic gain for the OpenVLM (hardware) path.")
    # LoRa voice-text (STT/TTS) — optional, off by default (needs models).
    lora_enabled: bool = Field(False, description="Enable LoRa voice-text (Vosk STT -> Meshtastic text -> Piper TTS).")
    lora_max_secs: float = Field(30.0, ge=1.0, le=60.0, description="Max speech captured per LoRa utterance (s).")
    lora_portnum: int = Field(260, ge=1, le=511, description="Meshtastic app portnum for LoRa voice-text.")
    lora_hop_limit: int = Field(0, ge=0, le=7, description="Hop limit for LoRa voice packets (0 = direct RF only).")
    stt_engine: str = Field(
        "vosk", pattern="^(vosk|sherpa)$",
        description="STT engine: vosk (fielded default) | sherpa (opt-in).",
    )
    stt_model: str = Field("", description="STT model override (dir name under the model dir, or abs path).")
    stt_grammar: str = Field("", description="Optional phrase-list file constraining the recognizer (vosk only).")
    stt_cleanup: bool = Field(True, description="HPF + WebRTC noise-suppression on the STT mic tap.")
    tx_cleanup: bool = Field(True, description="HPF + WebRTC noise-suppression on the live TX mic (IP + Codec2 stream).")
    # Live Codec2 voice stream over LoRa (VLoRa-compatible) — advanced/off.
    stream_enabled: bool = Field(True, description="Enable live Codec2 voice streaming over LoRa.")
    stream_portnum: int = Field(256, ge=1, le=511, description="Meshtastic app portnum for the Codec2 stream.")


class MessagingConfig(BaseModel):
    """Text messaging that delivers over two parallel transports into one store.

    A single message service (nucleus-messaging.service) owns one message store
    and fans every outbound message out over BOTH:
      * WiFi   — UDP multicast on the wlan1 802.11s mesh (fast, free, multi-hop).
      * LoRa   — standard Meshtastic TEXT_MESSAGE_APP (portnum 1) via the CoT
                 bridge, so plain Meshtastic radios/phones on the same channel
                 interoperate transparently.
    Inbound messages from either transport (including Meshtastic-only radios)
    land in the same store and are de-duplicated by (sender, text) within a
    short window. See docs/manual/messaging-internals.md.
    """

    enabled: bool = Field(True, description="Run the text messaging service (nucleus-messaging.service).")
    wifi_group: str = Field(
        "239.10.10.60",
        description="UDP multicast group for the WiFi transport (on the wlan1 mesh).",
    )
    wifi_port: int = Field(17020, ge=1, le=65535, description="UDP port for the WiFi transport.")
    lora: bool = Field(True, description="Also send/receive over Meshtastic LoRa (standard text, interoperable).")
    dedupe_window_secs: int = Field(
        60, ge=5, le=600,
        description="Window for matching the LoRa and WiFi copies of one message (5–600s).",
    )
    history_limit: int = Field(
        500, ge=10, le=10000,
        description="Max messages retained in the store / returned to the UI.",
    )
    display_name: Optional[str] = Field(
        None, max_length=32,
        description="This node's name in WiFi/LoRa chat and in its Reticulum (LXMF) "
                    "announces. Blank = the hostname.",
    )
    rns: "RnsMessagingConfig" = Field(default_factory=lambda: RnsMessagingConfig())

    @field_validator("display_name", mode="before")
    @classmethod
    def _clean_display_name(cls, v):
        # Blank means "use the hostname"; control chars would corrupt the WiFi
        # frame / LXMF app_data and the UI, so reject them outright.
        if v is None:
            return None
        v = str(v).strip()
        if not v:
            return None
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
            raise ValueError("display_name must not contain control characters")
        return v


class RnsMessagingConfig(BaseModel):
    """Reticulum (LXMF) direct-message transport for the messaging daemon.

    A third transport alongside WiFi multicast and LoRa, but unlike those it is
    NOT fanned into the shared broadcast store: LXMF messages are addressed to a
    single node, so they are kept as per-peer conversations. The daemon attaches
    to the already-running rnsd shared instance as a client (it never starts a
    second Reticulum stack); if rnsd is down the lane is simply skipped.

    Node discovery is NOT done with a custom flooded announce anymore. Each node
    publishes a contact card (its lxmf.delivery address + identity public key)
    that peers import out-of-band — over the WiFi mesh, by pasting a link, or by
    scanning a QR — see ``messaging/rns_proto.py`` and ``rns_contacts.py``. The
    only thing announced is the standard ``lxmf.delivery`` destination, and that
    is MANUAL by default (``announce_mode: manual``): nothing is broadcast until
    an operator presses "Announce" (or sends a message). Set ``announce_mode:
    auto`` to also re-announce on ``announce_interval_secs``.

    The identity is created once at runtime under /var/lib/nucleus/rns/ and is a
    NODE identity (phones never hold it).
    """

    enabled: bool = Field(
        True,
        description="Enable the Reticulum/LXMF direct-message lane in nucleus-messaging.",
    )
    announce_mode: Literal["manual", "auto"] = Field(
        "manual",
        description="manual = never announce lxmf.delivery automatically (operator triggers it); auto = re-announce every announce_interval_secs.",
    )
    announce_interval_secs: int = Field(
        1800, ge=60, le=86400,
        description="In auto mode, how often to re-announce the lxmf.delivery destination.",
    )
    propagation_node: bool = Field(
        False,
        description="Run an LXMF propagation node (store-and-forward for offline peers). Extra memory/disk; off by default.",
    )


class ReticulumConfig(BaseModel):
    """Reticulum Network Stack daemon (rnsd).

    rnsd runs as user 'natak' (its config lives in ~natak/.reticulum/config),
    providing a cryptographic mesh transport that rides on top of the 802.11s
    WiFi mesh (AutoInterface on wlan1) and, optionally, reaches off-mesh peers
    over TCP. Like every other subsystem here the operator sets primitives and
    the rendered config is produced by `nucleusctl apply` — the file is an
    artifact, never hand-edited.

    Transports:
      * AutoInterface on wlan1 — auto-peers with other Reticulum nodes on the
        802.11s mesh over IPv6 link-local (no IP infra needed).
      * TCPServer on br-lan — lets wired/AP-side clients attach over TCP.
      * TCPClient entry node — an optional uplink to a public-IP Reticulum node
        so the local mesh joins the wider network.
      * RNode — LoRa RNode radio attached over USB (on by default; harmless if
        no radio is plugged in — rnsd just retries the port).
    """

    enabled: bool = Field(True, description="Run the Reticulum daemon (rnsd.service).")
    transport: bool = Field(
        True,
        description="Act as a Reticulum transport node (route/relay for peers). Suits always-on nodes.",
    )
    loglevel: int = Field(4, ge=0, le=7, description="rnsd log verbosity (0=critical .. 7=extreme).")

    # AutoInterface over the 802.11s WiFi mesh.
    auto_interface: bool = Field(True, description="AutoInterface peering on the wlan1 mesh.")
    auto_device: str = Field("wlan1", description="Interface AutoInterface peers over.")

    # TCPServer on br-lan for wired/AP-side clients.
    tcp_server: bool = Field(True, description="Expose a TCPServerInterface on br-lan.")
    tcp_server_port: int = Field(4242, ge=1, le=65535, description="Listen port for the br-lan TCPServer.")

    # Optional uplink to a public-IP entry node.
    entry_node: bool = Field(True, description="Connect out to a public-IP Reticulum entry node over TCP.")
    entry_node_host: str = Field("173.230.150.24", description="Entry node hostname / IP.")
    entry_node_port: int = Field(4243, ge=1, le=65535, description="Entry node TCP port.")

    # modem73 soundmodem over KISS-on-TCP (on by default; harmless if modem73
    # isn't running — rnsd just retries 127.0.0.1:8001). modem73 is a KISS TNC
    # frontend for the aicodix OFDM modem, driving an analogue radio over a USB
    # sound device + serial PTT (e.g. a DigiRig). It exposes a KISS socket on TCP
    # 8001, so this uses RNS's built-in TCPClientInterface with kiss_framing
    # (manual: "connect via ... software-based soundmodems") rather than a custom
    # interface. fixed_mtu/bitrate tell RNS the link is a slow radio channel so
    # its framing and timeouts are sized correctly.
    modem73_enabled: bool = Field(True, description="Enable a TCPClientInterface (KISS framing) to the modem73 soundmodem.")
    # modem73 process (nucleus-modem73.service) settings. The RNS interface above
    # talks to this process over KISS-on-TCP; these drive the process itself —
    # which USB sound card to use and how PTT is keyed. The audio device is matched
    # by NAME (modem73 --list-audio), never a card index, so it's stable across
    # reboots/plug order. PTT keys the radio over the CP210x serial line's control
    # signal (RTS on a DigiRig) at MODEM_DEVICE_PATH.
    modem73_audio_device: str = Field(
        "USB Audio Device, USB Audio",
        description="modem73 audio device name (from 'modem73 --list-audio'), used for both capture and playback.",
    )
    modem73_ptt_line: Literal["rts", "dtr", "both", "-rts", "-dtr", "-both"] = Field(
        "rts", description="Serial control line that keys PTT on the modem adapter (DigiRig = rts)."
    )
    # Callsign sent in every frame (modem73 PHY header); receivers show it per
    # frame, so it identifies the sending node on the live page. Blank = the
    # node short id ("0042"). Must pass modem73's own check (<= 9 chars of
    # A-Z a-z 0-9 / space) or modem73 refuses to start.
    modem73_callsign: Optional[str] = Field(
        None, description="modem73 station callsign (<= 9 chars, A-Z 0-9 /). Blank = node id (e.g. 0042)."
    )
    # 5 = bench-tested on UV-5R: higher overdrives the radio's mic input (OFDM
    # peak-to-average), showing as low SNR / high BER at the far end. Tune live
    # on the modem73 Live page while the far node watches SNR/BER.
    modem73_tx_level: int = Field(5, ge=5, le=100, description="modem73 TX audio output level, 5-100 percent.")
    # Sound-card mixer, pinned by the launch script on every start so the only
    # unknown gain left in the chain is the radio's volume knob. Raw ALSA steps
    # of the DigiRig's C-Media codec (card id from /proc/asound/cards). AGC and
    # the Mic->Speaker monitor loop are always forced off.
    modem73_alsa_card: str = Field(
        "Device", description="ALSA card id of the modem sound card (see /proc/asound/cards)."
    )
    modem73_rx_gain: int = Field(
        15, ge=0, le=35,
        description="Sound-card capture gain step ('Mic Capture Volume' 0-35 = -12..+23 dB).",
    )
    modem73_tx_gain: int = Field(
        11, ge=0, le=37,
        description="Sound-card playback step ('Speaker Playback Volume' 0-37 = -37..0 dB).",
    )
    # Mode: selects an entry from MODEM73_MODES, which drives BOTH the modem73
    # launch flags and the RNS interface bitrate so the two always agree.
    # Receivers auto-detect the mode, so nodes need not match: each node's mode
    # only sets what IT transmits. rdm_1200 is the hardy fallback; higher QAM is
    # faster but needs a cleaner signal.
    modem73_mode: Literal[
        "rdm_1200",
        "qpsk_1_2",
        "qam16_1_2",
        "qam16_2_3",
        "qam64_2_3",
        "qam64_3_4",
        "qam256_3_4",
        "qam256_5_6",
        "qam4096_5_6",
    ] = Field(
        MODEM73_DEFAULT_MODE,
        description="modem73 TX mode; sets on-air rate and the matching RNS bitrate.",
        # Publish the per-mode metadata (modulation, code rate, on-air bitrate) from
        # MODEM73_MODES into the field's JSON schema, so the web UI can label the
        # mode dropdown with real rates read from the one contract — no second copy
        # of the rate table in the frontend.
        json_schema_extra={"modes": MODEM73_MODES},
    )

    @field_validator("modem73_mode", mode="before")
    @classmethod
    def _migrate_modem73_mode(cls, v):
        # Dropped modes (too slow for a squad data link) map to their
        # replacement so an older config.yaml still loads.
        return MODEM73_MODE_MIGRATIONS.get(v, v)

    @field_validator("modem73_callsign", mode="before")
    @classmethod
    def _clean_modem73_callsign(cls, v):
        # Blank, or the old "N0CALL" placeholder default, means "use the node
        # id". Anything else must pass modem73's own callsign check, which
        # otherwise aborts modem73 at startup.
        if v is None:
            return None
        v = str(v).strip()
        if not v or v.upper() == "N0CALL":
            return None
        if len(v) > 9 or not re.fullmatch(r"[A-Za-z0-9/ ]+", v):
            raise ValueError("modem73_callsign must be <= 9 chars of A-Z a-z 0-9 / space")
        return v

    # RNode LoRa radio attached over USB (on by default). Bound to the
    # stable RNODE_DEVICE_PATH symlink by the generated udev rule, so the config
    # never depends on which /dev/ttyACM* a given board enumerates as. Only the
    # RAK4631 is supported (see USB_DEVICES). Radio parameters must match on
    # every node that should talk over this link.
    rnode_enabled: bool = Field(True, description="Enable an RNodeInterface on a USB LoRa radio.")
    rnode_frequency: int = Field(
        914875000,
        ge=RNODE_FREQ_MIN_HZ,
        le=RNODE_FREQ_MAX_HZ,
        description="RNode centre frequency in Hz (avoid the Meshtastic channel).",
    )
    rnode_bandwidth: RNodeBandwidth = Field(
        250000, description="RNode LoRa bandwidth in Hz (one of the SX1262 steps)."
    )
    rnode_txpower: int = Field(22, ge=0, le=22, description="RNode TX power in dBm (RAK4631 max 22).")
    rnode_spreadingfactor: int = Field(9, ge=5, le=12, description="RNode LoRa spreading factor (5-12).")
    rnode_codingrate: int = Field(5, ge=5, le=8, description="RNode LoRa coding rate denominator (5-8 → 4/5-4/8).")


class TakCertConfig(BaseModel):
    """X.509 metadata baked into the TAK Server PKI (cert-metadata.sh).

    These values are substituted verbatim into /opt/tak/certs/cert-metadata.sh
    before the CA/server/webadmin certs are generated. CA common names are
    derived from the node hostname (no spaces allowed), not set here.
    """

    country: str = Field("US", description="X.509 C — 2-letter country code.")
    state: str = Field("FL", description="X.509 ST — state / province.")
    city: str = Field("Tampa", description="X.509 L — city / locality.")
    organization: str = Field("NATAK", description="X.509 O — organization.")
    organizational_unit: str = Field("TAK", description="X.509 OU — org unit.")


class TakConfig(BaseModel):
    """TAK Server — optional, off on most nodes. Either/or: official OR OTS.

    `official` (tak.gov .deb + MediaMTX) is provisioned by the one-shot script
    nucleus-tak-setup.sh; `opentakserver` is installed manually with the
    upstream OpenTAKServer Raspberry Pi installer. Neither is part of the
    `nucleusctl apply` render loop: installing a TAK server and generating PKI
    are irreversible, per-node-optional actions that don't belong in the
    idempotent apply pipeline. The cert/enrollment fields below apply to the
    official server only (OTS generates its own CA).
    """

    variant: str = Field(
        "none",
        pattern="^(none|official|opentakserver)$",
        description=(
            "TAK Server variant: none (default) | official (tak.gov .deb) "
            "| opentakserver (OpenTAKServer Pi installer)."
        ),
    )
    cert: TakCertConfig = Field(default_factory=TakCertConfig)
    enrollment_validity_days: int = Field(
        365, ge=1, le=3650,
        description="Validity (days) of client certs issued via auto-enrollment.",
    )
    keystore_pass: str = Field(
        "atakatak",
        description="Signing keystore password (cert-metadata CAPASS + CoreConfig).",
    )


class WebConfig(BaseModel):
    """Web UI access control.

    The UI is reachable without a password from trusted source networks (the
    802.11s mesh, the local br-lan, the node's own AP, Tailscale, and
    localhost). Requests arriving from any other source — in practice, anything
    coming in over eth0 (a home LAN, or a public network) — must authenticate
    with HTTP Basic auth (user `admin`, password below). See the manual to
    change the default.

    The password is stored in clear text here, as an operator-set primitive:
    config.yaml is root-owned, and anyone with root already has the rendered
    htpasswd file. The derived artifact (the htpasswd line nginx reads) is
    produced by `nucleusctl apply`.
    """

    user: str = Field("admin", description="HTTP Basic auth username for eth0 access.")
    password: str = Field(
        "52235223", min_length=6,
        description="HTTP Basic auth password for web UI access over eth0.",
    )
    eth0_access: bool = Field(
        True,
        description="Allow the web UI on eth0 (port 80), password-protected. Off = eth0 fully denied.",
    )


class FirewallConfig(BaseModel):
    """Host firewall (UFW) control.

    The ruleset is rebuilt from this config on every mesh bring-up: the
    rendered `nucleus-mesh-up.sh` runs `ufw --force reset` first, so the live
    rules always reflect the current config and never accumulate stale rules
    from earlier applies. This only manages UFW's own rules — the nftables NAT
    masquerade, the multicast-TTL mangle rules, and Tailscale's chains are set
    up separately and are not touched by the reset.

    `enabled: false` renders `ufw --force disable` instead of the rule block,
    turning the host firewall off entirely.
    """

    enabled: bool = Field(
        True,
        description="Enable the host firewall (UFW). Off = ufw disabled entirely.",
    )


class HalowConfig(BaseModel):
    """Wi-Fi HaLow (802.11ah) sub-GHz mesh on a Morse MM8108 USB radio (halow0).

    A second 802.11s mesh alongside wlan1, on the US 900 MHz band, for long-range
    low-rate links. On by default and inert on a node without the radio: the
    service is bound to the halow0 device and only runs while it exists, and the
    babeld/UFW/smcroute/reticulum stanzas for a missing interface sit idle.

    The operator sets the S1G channel + bandwidth in REAL HaLow units (US channel
    numbers 1-51, bandwidth 1/2/4/8 MHz) — never the 5 GHz stand-ins the driver
    reports to iw (docs/halow.md §6). SSID/password/country are shared with the
    wlan1 mesh (mesh.*), so both meshes stay in lock-step without duplication.
    """

    enabled: bool = Field(
        True,
        description="Run the HaLow 802.11s mesh on halow0 (nucleus-halow + babeld/"
                    "smcroute/UFW stanzas). Inert on a node with no HaLow radio.",
    )
    bandwidth_mhz: HalowBandwidth = Field(
        1, description="HaLow channel bandwidth in MHz (1/2/4/8). Must match on every node."
    )
    channel: int = Field(
        27, description="US S1G channel number (see HALOW_CHANNELS). Default 27 = 915.5 MHz, 1 MHz."
    )
    prim_1mhz_index: int = Field(
        0, ge=0, le=7,
        description="Primary 1 MHz channel index. Valid range depends on bandwidth "
                    "(1 MHz->0; 2 MHz->0-1; 4 MHz->0-3; 8 MHz->0-7).",
    )

    @model_validator(mode="after")
    def _valid_channel_and_prim(self) -> "HalowConfig":
        valid = HALOW_CHANNELS.get(self.bandwidth_mhz, ())
        if self.channel not in valid:
            raise ValueError(
                f"halow.channel {self.channel} invalid for bandwidth "
                f"{self.bandwidth_mhz} MHz; valid channels: {', '.join(map(str, valid))}"
            )
        # Primary 1 MHz index must fit the number of 1 MHz sub-channels in the BW.
        max_idx = self.bandwidth_mhz - 1
        if not 0 <= self.prim_1mhz_index <= max_idx:
            raise ValueError(
                f"halow.prim_1mhz_index {self.prim_1mhz_index} invalid for bandwidth "
                f"{self.bandwidth_mhz} MHz; valid range 0-{max_idx}"
            )
        return self


class NucleusConfig(BaseModel):
    """Top-level node configuration = the whole contract."""

    node: NodeConfig = Field(default_factory=NodeConfig)
    mesh: MeshConfig
    br_lan: BrLanConfig = Field(default_factory=BrLanConfig)
    ap: ApConfig
    eth0: Eth0Config = Field(default_factory=Eth0Config)
    halow: HalowConfig = Field(default_factory=HalowConfig)
    meshtastic: MeshtasticConfig = Field(default_factory=MeshtasticConfig)
    messaging: MessagingConfig = Field(default_factory=MessagingConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    reticulum: ReticulumConfig = Field(default_factory=ReticulumConfig)
    tak: TakConfig = Field(default_factory=TakConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    firewall: FirewallConfig = Field(default_factory=FirewallConfig)

    @field_validator("node", mode="before")
    @classmethod
    def _node_null_to_default(cls, v):
        # `node:` with all keys commented parses as None; treat as defaults.
        return NodeConfig() if v is None else v

    @property
    def messaging_name(self) -> str:
        """Name this node uses in chat + LXMF announces (display_name, else hostname)."""
        return self.messaging.display_name or self.node.hostname

    # ---- Derived addressing (computed, never hand-entered) ----------------
    @property
    def mesh_ip(self) -> str:
        return f"{self.mesh.subnet_prefix}.{self.node.id}"

    @property
    def br_lan_prefix(self) -> str:
        # Per-node /24: 10.20.<id>.0. Each node's LAN must be a distinct subnet
        # so babeld can redistribute it and remote clients stay reachable.
        return self.br_lan.subnet_prefix or f"10.20.{self.node.id}"

    @property
    def br_lan_subnet(self) -> str:
        return f"{self.br_lan_prefix}.0/24"

    @property
    def br_lan_ip(self) -> str:
        return f"{self.br_lan_prefix}.1"

    @property
    def eth0_lan_ip(self) -> str:
        # Per-node eth0 LAN gateway (old scheme): 10.10.<id>.1.
        return self.eth0.static_ip or f"10.10.{self.node.id}.1"

    @property
    def babeld_enabled(self) -> bool:
        """babeld runs while ANY routed link needs it: wlan1, HaLow, or eth0."""
        return self.mesh.enabled or self.halow.enabled or self.eth0.babel

    @property
    def smcroute_enabled(self) -> bool:
        """smcroute runs while ANY multicast-bridged link needs it."""
        return self.mesh.enabled or self.halow.enabled or self.eth0.mcast_bridge

    @property
    def nucleus_nets(self) -> list[str]:
        """Every Nucleus-owned IPv4 net: mesh, all nodes' br-lan, HaLow.

        Order-preserving; entries covered by a broader one are dropped (nft
        rejects overlapping elements in an anonymous set).
        """
        nets = [self.mesh.subnet, "10.20.0.0/16", self.br_lan_subnet, "10.30.1.0/24"]
        parsed = [ipaddress.ip_network(n) for n in dict.fromkeys(nets)]
        keep = [n for n in parsed
                if not any(n != o and n.subnet_of(o) for o in parsed)]
        return [str(n) for n in keep]

    @property
    def eth0_link_routes(self) -> list[dict]:
        """UFW forwarding rules for hard-line node links over eth0.

        Each entry is {"in", "out", "src", "dst"}; None = any. Two kinds:

          * routed unicast (eth0.babel): between eth0 and every internal link
            (br-lan, wlan1, halow0), limited to Nucleus nets — eth0 -> internal
            by SOURCE, internal -> eth0 by DESTINATION — so upstream WAN hosts
            still cannot route in and LAN mode gains no generic egress.
          * bridged ATAK multicast (eth0.mcast_mode != off): eth0 <-> br-lan, plus
            eth0 <-> wlan1/halow0 in mesh_relay, to the ATAK groups, any source
            (matches what smcroute forwards).

        In WAN mode `<iface> -> eth0` is already allowed (any destination) by
        the internet-egress rules, so that direction is only added in LAN mode.
        """
        rules: list[dict] = []
        lan = self.eth0.mode != "wan"
        if self.eth0.babel:
            for iface in ["br-lan", "wlan1"] + ([HALOW_IFACE] if self.halow.enabled else []):
                for net in self.nucleus_nets:
                    rules.append({"in": "eth0", "out": iface, "src": net, "dst": None})
                if lan:
                    for net in self.nucleus_nets:
                        rules.append({"in": iface, "out": "eth0", "src": None, "dst": net})
        if self.eth0.mcast_bridge:
            for iface in ["br-lan"] + (self.mesh_ifaces if self.eth0.mcast_mesh_relay else []):
                for grp in ATAK_MCAST_DSTS:
                    rules.append({"in": "eth0", "out": iface, "src": None, "dst": grp})
                    if lan:
                        rules.append({"in": iface, "out": "eth0", "src": None, "dst": grp})
        return rules

    @property
    def eth0_mcast_relay_ifaces(self) -> list[str]:
        """Radio-mesh interfaces smcroute relays eth0 ATAK multicast to/from.

        Empty unless eth0.mcast_mode == mesh_relay. wlan1 always, halow0 when
        HaLow is enabled (same set messaging/voice fan out over).
        """
        return self.mesh_ifaces if self.eth0.mcast_mesh_relay else []

    @property
    def eth0_mcast_source_subnets(self) -> list[str]:
        """br-lan subnets of the RF-silent nodes listed in eth0.mcast_source_node_ids.

        Assumes those nodes use the default br-lan scheme (10.20.<id>.0/24).
        Empty unless mesh_relay. Consumed by cot-bridge's multicast -> LoRa
        source filter.
        """
        if not self.eth0.mcast_mesh_relay:
            return []
        return [f"10.20.{n}.0/24" for n in self.eth0.mcast_source_node_ids]

    @property
    def eth0_mcast_source_exclude_ips(self) -> list[str]:
        """br-lan gateway IPs (10.20.<id>.1) of the RF-silent source nodes.

        Excluded from the cot-bridge's multicast -> LoRa sources. A source node's
        own cot-bridge injects the LoRa CoT it receives on its br-lan from this
        address; without the exclusion this node would re-transmit that CoT on
        LoRa. EUDs get DHCP leases from the pool, never .1. Empty unless
        mesh_relay.
        """
        if not self.eth0.mcast_mesh_relay:
            return []
        return [f"10.20.{n}.1" for n in self.eth0.mcast_source_node_ids]

    @property
    def nat_exempt_dsts(self) -> list[str]:
        """Destinations never masqueraded out eth0 in WAN mode.

        Nucleus nets reached over a hard line and multicast keep their real
        source, so Babel routing and ATAK bridging across eth0 stay symmetric
        and the eth0 -> internal source filter (eth0_link_routes) matches.
        """
        return self.nucleus_nets + ["224.0.0.0/4"]

    @property
    def mcast_ttl_ingress(self) -> list[dict]:
        """Ingress interfaces whose ATAK multicast gets its TTL raised to mcast_ttl.

        ATAK emits TTL=1, which the kernel will not forward. br-lan is bumped
        unconditionally (as before). eth0 is bumped only when TTL==1 (an EUD
        directly on the wire); packets a peer node already forwarded with
        headroom keep their decremented TTL.
        """
        if self.mesh.mcast_ttl == 0:
            return []
        rules = [{"iface": "br-lan", "only_ttl1": False}]
        if self.eth0.mcast_bridge:
            rules.append({"iface": "eth0", "only_ttl1": True})
        return rules

    @property
    def mesh_ipv6_ll(self) -> str:
        return _ll_from_seed(f"{self.node.hostname}-mesh")

    @property
    def br_lan_ipv6_ll(self) -> str:
        return _ll_from_seed(f"{self.node.hostname}-brlan")

    @property
    def ap_name(self) -> str:
        return self.ap.name or f"{self.node.short}-nucleus-ap"

    @property
    def modem73_callsign(self) -> str:
        """Callsign modem73 transmits: configured value, else the node short id."""
        return self.reticulum.modem73_callsign or self.node.short

    # ---- TAK PKI names (derived from hostname; CN cannot contain spaces) ----
    @property
    def tak_root_ca_name(self) -> str:
        return f"{self.node.hostname}-root"

    @property
    def tak_intermediate_ca_name(self) -> str:
        return f"{self.node.hostname}-ca"

    @property
    def web_htpasswd(self) -> str:
        """htpasswd line for nginx auth_basic_user_file.

        Uses nginx's `{SHA}` scheme (base64 of SHA-1) rather than crypt: it is
        deterministic (same password -> same line), so re-rendering with an
        unchanged password produces an identical file and apply stays
        idempotent. crypt() was also removed from the stdlib in Python 3.13.
        """
        import base64
        digest = hashlib.sha1(self.web.password.encode()).digest()
        return f"{self.web.user}:{{SHA}}{base64.b64encode(digest).decode()}"


    @property
    def web_trusted_cidrs(self) -> list[str]:
        """Source networks that skip web auth (mesh, br-lan, tailscale)."""
        return [
            f"{self.mesh.subnet_prefix}.0/24",   # 802.11s mesh (wlan1)
            f"{self.br_lan_prefix}.0/24",        # br-lan (AP + wired LAN)
            "100.64.0.0/10",                     # Tailscale (CGNAT range)
        ]

    @property
    def halow_ssid(self) -> str:
        # Shares the wlan1 mesh SSID with a _halow suffix so the two meshes are
        # paired but distinct 802.11s networks.
        return f"{self.mesh.ssid}_halow"

    @property
    def halow_ip(self) -> str:
        # 10.30.1.0/24 is free today (wlan1 10.20.1.x, br-lan 10.20.<id>.x,
        # eth0 10.10.<id>.x). Per-node host = node.id, mirroring the mesh scheme.
        return f"10.30.1.{self.node.id}"

    @property
    def halow_ipv6_ll(self) -> str:
        return _ll_from_seed(f"{self.node.hostname}-halow")

    @property
    def halow_op_class(self) -> int:
        return HALOW_OP_CLASS[self.halow.bandwidth_mhz]

    @property
    def halow_freq_mhz(self) -> float:
        # Display-only real HaLow centre frequency (ch 27 -> 915.5 MHz).
        return 902 + 0.5 * self.halow.channel

    @property
    def mesh_ifaces(self) -> list[str]:
        """Mesh transport interfaces messaging/voice/status fan out over.

        Always wlan1; halow0 is appended when HaLow is enabled. Consumed by the
        messaging + voice daemons (send/join on each) and status.
        """
        return ["wlan1"] + ([HALOW_IFACE] if self.halow.enabled else [])

    @model_validator(mode="after")
    def _no_subnet_collision(self) -> "NucleusConfig":
        if self.mesh.subnet_prefix == self.br_lan_prefix:
            raise ValueError(
                f"mesh subnet ({self.mesh.subnet_prefix}) collides with br-lan "
                f"({self.br_lan_prefix}); with the default scheme this happens when "
                f"node.id == 1 — set a distinct node.id or br_lan.subnet_prefix"
            )
        return self

    @model_validator(mode="after")
    def _eth0_mcast_relay_gate(self) -> "NucleusConfig":
        # mcast_source_node_ids may stay set with another mode (kept for the next
        # switch back to mesh_relay); it is only consumed in mesh_relay.
        e = self.eth0
        if e.mcast_mesh_relay:
            if not e.babel:
                raise ValueError("eth0.mcast_mode: mesh_relay requires eth0.babel: true")
            if self.node.id in e.mcast_source_node_ids:
                raise ValueError(
                    f"eth0.mcast_source_node_ids must not contain this node's id ({self.node.id})"
                )
        return self

    @model_validator(mode="after")
    def _halow_country_gate(self) -> "NucleusConfig":
        # Only the US S1G regulatory tables are defined (HALOW_CHANNELS /
        # HALOW_OP_CLASS). Fail fast rather than bring up a mesh on the wrong
        # band; add per-country tables when another regdomain is needed.
        if self.halow.enabled and self.mesh.country != "US":
            raise ValueError(
                f"halow.enabled requires mesh.country == 'US' (got "
                f"{self.mesh.country!r}); only US HaLow channels are defined. "
                f"See docs/halow.md §10.2."
            )
        return self

    def render_context(self) -> dict:
        """Flat dict handed to Jinja2 templates.

        Templates stay dumb: they only substitute these pre-computed values, so
        all logic/validation remains here in one place.
        """
        return {
            "hostname": self.node.hostname,
            "node_id": self.node.id,
            "node_short": self.node.short,
            "mesh_ssid": self.mesh.ssid,
            "mesh_channel": self.mesh.channel,
            "mesh_frequency": self.mesh.frequency,
            "mesh_password": self.mesh.password,
            "mesh_country": self.mesh.country,
            "mesh_ip": self.mesh_ip,
            "mesh_subnet": self.mesh.subnet,
            "mesh_ipv6_ll": self.mesh_ipv6_ll,
            "mesh_mcast_ttl": self.mesh.mcast_ttl,
            "mesh_802_ttl": self.mesh.mesh_802_ttl,
            "mesh_rts_threshold": self.mesh.rts_threshold,
            # --- Wi-Fi HaLow (802.11ah) second mesh on halow0 ---
            "halow_enabled": self.halow.enabled,
            "halow_iface": HALOW_IFACE,
            "halow_ssid": self.halow_ssid,
            "halow_password": self.mesh.password,
            "halow_country": self.mesh.country,
            "halow_channel": self.halow.channel,
            "halow_bandwidth_mhz": self.halow.bandwidth_mhz,
            "halow_op_class": self.halow_op_class,
            "halow_prim_1mhz_index": self.halow.prim_1mhz_index,
            "halow_ip": self.halow_ip,
            "halow_ipv6_ll": self.halow_ipv6_ll,
            "halow_802_ttl": self.mesh.mesh_802_ttl,
            "halow_freq_mhz": self.halow_freq_mhz,
            # Exposed under _derived so a future UI builds valid channel/bandwidth
            # dropdowns from the schema, never a hard-coded list.
            "halow_channels": HALOW_CHANNELS,
            "halow_op_classes": HALOW_OP_CLASS,
            "br_lan_ip": self.br_lan_ip,
            "br_lan_subnet": self.br_lan_subnet,
            "br_lan_ipv6_ll": self.br_lan_ipv6_ll,
            "br_lan_dhcp_offset": self.br_lan.dhcp_pool_offset,
            "br_lan_dhcp_size": self.br_lan.dhcp_pool_size,
            "br_lan_dns": self.br_lan.dns,
            "ap_name": self.ap_name,
            "ap_channel": self.ap.channel,
            "ap_password": self.ap.password,
            "eth0_mode": self.eth0.mode,
            "eth0_static_ip": self.eth0_lan_ip,
            "eth0_dhcp_offset": self.eth0.dhcp_pool_offset,
            "eth0_dhcp_size": self.eth0.dhcp_pool_size,
            "eth0_babel": self.eth0.babel,
            "eth0_mcast_bridge": self.eth0.mcast_bridge,
            "eth0_mcast_mode": self.eth0.mcast_mode,
            "eth0_mcast_relay_ifaces": self.eth0_mcast_relay_ifaces,
            "eth0_mcast_source_subnets": self.eth0_mcast_source_subnets,
            "eth0_mcast_source_exclude_ips": self.eth0_mcast_source_exclude_ips,
            "eth0_link_routes": self.eth0_link_routes,
            "mcast_ttl_ingress": self.mcast_ttl_ingress,
            "atak_mcast_dsts": list(ATAK_MCAST_DSTS),
            "atak_mcast_dsts_nft": ", ".join(ATAK_MCAST_DSTS),
            "nat_exempt_dsts_nft": ", ".join(self.nat_exempt_dsts),
            "mesh_enabled": self.meshtastic.enabled,
            "mesh_region": self.meshtastic.region,
            "mesh_hat": self.meshtastic.hat,
            "mesh_gps": self.meshtastic.gps,
            "mesh_gps_serial_path": self.meshtastic.gps_serial_path,
            "mesh_i2c_device": self.meshtastic.i2c_device,
            "mesh_cot_bridge": self.meshtastic.cot_bridge,
            "mesh_owner": self.node.hostname,
            "mesh_owner_short": self.node.short,
            # --- Reticulum (rnsd) ---
            "reti_enabled": self.reticulum.enabled,
            "reti_transport": self.reticulum.transport,
            "reti_loglevel": self.reticulum.loglevel,
            "reti_auto_interface": self.reticulum.auto_interface,
            "reti_auto_device": self.reticulum.auto_device,
            # AutoInterface peers over every mesh radio: wlan1 always, halow0 too
            # when HaLow is enabled. reti_auto_device stays the single wlan1 value
            # (back-compat); the template uses this comma-joined list.
            "reti_auto_devices": ", ".join(
                [self.reticulum.auto_device]
                + ([HALOW_IFACE] if self.halow.enabled else [])
                + ["eth0"]
            ),
            "reti_tcp_server": self.reticulum.tcp_server,
            "reti_tcp_server_device": "br-lan",
            "reti_tcp_server_port": self.reticulum.tcp_server_port,
            "reti_entry_node": self.reticulum.entry_node,
            "reti_entry_node_host": self.reticulum.entry_node_host,
            "reti_entry_node_port": self.reticulum.entry_node_port,
            "reti_rnode_enabled": self.reticulum.rnode_enabled,
            "reti_rnode_port": RNODE_DEVICE_PATH,
            "reti_rnode_frequency": self.reticulum.rnode_frequency,
            "reti_rnode_bandwidth": self.reticulum.rnode_bandwidth,
            "reti_rnode_txpower": self.reticulum.rnode_txpower,
            "reti_rnode_spreadingfactor": self.reticulum.rnode_spreadingfactor,
            "reti_rnode_codingrate": self.reticulum.rnode_codingrate,
            # modem73 soundmodem: built-in TCPClientInterface with KISS framing to
            # modem73's local KISS-on-TCP socket. Host/port/mtu are fixed constants
            # (one source of truth shared with the modem73 service); the bitrate is
            # derived from the operator-selected OFDM mode (see below) so it always
            # matches the modem's on-air rate — the two sides must agree.
            "reti_modem73_enabled": self.reticulum.modem73_enabled,
            "reti_modem73_host": "127.0.0.1",
            "reti_modem73_port": 8001,
            "reti_modem73_fixed_mtu": 500,
            # Bitrate is DERIVED from the selected mode (MODEM73_MODES) so it
            # always matches the modem73 launch flags below — RNS is told the real
            # on-air rate, not a placeholder, so its framing/timeouts and the
            # announce rate-cap are sized correctly.
            "reti_modem73_bitrate": MODEM73_MODES[self.reticulum.modem73_mode]["bitrate"],
            # modem73 process (nucleus-modem73.service) — audio/PTT/KISS flags.
            # Port/control-port mirror the RNS interface constants above so the two
            # sides can never disagree; the serial PTT device is the stable symlink.
            "modem73_audio_device": self.reticulum.modem73_audio_device,
            "modem73_ptt_line": self.reticulum.modem73_ptt_line,
            "modem73_ptt_device": MODEM_DEVICE_PATH,
            "modem73_callsign": self.modem73_callsign,
            "modem73_tx_level": self.reticulum.modem73_tx_level,
            "modem73_kiss_port": 8001,
            "modem73_control_port": 8073,
            # Mode flags (--modem ofdm --modulation/--rate, or --robust-mode) from
            # the same registry entry that sets the RNS bitrate above.
            "modem73_mode_flags": modem73_mode_flags(self.reticulum.modem73_mode),
            # Sound-card mixer pinned by the launch script (see ReticulumConfig).
            "modem73_alsa_card": self.reticulum.modem73_alsa_card,
            "modem73_rx_gain": self.reticulum.modem73_rx_gain,
            "modem73_tx_gain": self.reticulum.modem73_tx_gain,
            # --- USB device registry (generated udev rule) ---
            "usb_devices": USB_DEVICES,
            # --- Web UI auth (nginx) ---
            "web_htpasswd": self.web_htpasswd,
            "web_trusted_cidrs": self.web_trusted_cidrs,
            "web_eth0_access": self.web.eth0_access,
            # --- Host firewall (ufw) ---
            "firewall_enabled": self.firewall.enabled,
        }
