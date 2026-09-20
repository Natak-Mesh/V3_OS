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
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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


class Eth0Config(BaseModel):
    """eth0 defaults to WAN (DHCP client / internet gateway)."""

    mode: str = Field("wan", pattern="^(wan|lan)$")
    static_ip: Optional[str] = None
    dhcp_pool_offset: int = 10
    dhcp_pool_size: int = 100



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
    region: str = Field("US", description="LoRa region code (US, EU_868, ...). Radio won't TX until set.")
    hat: str = Field(
        "rak6421-slot1",
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
    # Live Codec2 voice stream over LoRa (VLoRa-compatible) — advanced/off.
    stream_enabled: bool = Field(False, description="Enable live Codec2 voice streaming over LoRa.")
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
      * KISS — optional packet-radio TNC on a serial device (off by default).
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

    # Optional KISS packet-radio TNC (off by default).
    kiss_enabled: bool = Field(False, description="Enable a KISSInterface on a serial TNC.")
    kiss_port: str = Field("/dev/rfcomm0", description="Serial device for the KISS TNC.")
    kiss_speed: int = Field(115200, ge=1, description="KISS serial baud rate.")


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
    """Official TAK Server (+ MediaMTX) — optional, off on most nodes.

    This block is consumed by the one-shot provisioning script
    (nucleus-tak-setup.sh), NOT the `nucleusctl apply` render loop: installing
    the tak.gov .deb and generating PKI are irreversible, per-node-optional
    actions that don't belong in the idempotent apply pipeline. The script reads
    these values to run unattended (cert metadata, CA names, enrollment config).
    """

    variant: str = Field(
        "none",
        pattern="^(none|official)$",
        description="TAK Server variant: none (default) | official (tak.gov .deb).",
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


class NucleusConfig(BaseModel):
    """Top-level node configuration = the whole contract."""

    node: NodeConfig = Field(default_factory=NodeConfig)
    mesh: MeshConfig
    br_lan: BrLanConfig = Field(default_factory=BrLanConfig)
    ap: ApConfig
    eth0: Eth0Config = Field(default_factory=Eth0Config)
    meshtastic: MeshtasticConfig = Field(default_factory=MeshtasticConfig)
    messaging: MessagingConfig = Field(default_factory=MessagingConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    reticulum: ReticulumConfig = Field(default_factory=ReticulumConfig)
    tak: TakConfig = Field(default_factory=TakConfig)

    @field_validator("node", mode="before")
    @classmethod
    def _node_null_to_default(cls, v):
        # `node:` with all keys commented parses as None; treat as defaults.
        return NodeConfig() if v is None else v

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
    def mesh_ipv6_ll(self) -> str:
        return _ll_from_seed(f"{self.node.hostname}-mesh")

    @property
    def br_lan_ipv6_ll(self) -> str:
        return _ll_from_seed(f"{self.node.hostname}-brlan")

    @property
    def ap_name(self) -> str:
        return self.ap.name or f"{self.node.short}-nucleus-ap"

    # ---- TAK PKI names (derived from hostname; CN cannot contain spaces) ----
    @property
    def tak_root_ca_name(self) -> str:
        return f"{self.node.hostname}-root"

    @property
    def tak_intermediate_ca_name(self) -> str:
        return f"{self.node.hostname}-ca"

    @model_validator(mode="after")
    def _no_subnet_collision(self) -> "NucleusConfig":
        if self.mesh.subnet_prefix == self.br_lan_prefix:
            raise ValueError(
                f"mesh subnet ({self.mesh.subnet_prefix}) collides with br-lan "
                f"({self.br_lan_prefix}); with the default scheme this happens when "
                f"node.id == 1 — set a distinct node.id or br_lan.subnet_prefix"
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
            "reti_tcp_server": self.reticulum.tcp_server,
            "reti_tcp_server_device": "br-lan",
            "reti_tcp_server_port": self.reticulum.tcp_server_port,
            "reti_entry_node": self.reticulum.entry_node,
            "reti_entry_node_host": self.reticulum.entry_node_host,
            "reti_entry_node_port": self.reticulum.entry_node_port,
            "reti_kiss_enabled": self.reticulum.kiss_enabled,
            "reti_kiss_port": self.reticulum.kiss_port,
            "reti_kiss_speed": self.reticulum.kiss_speed,
        }
