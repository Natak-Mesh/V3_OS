"""
Nucleus V3 OS — configuration schema (single source of truth).

The entire node configuration lives in one YAML file (config/config.yaml,
installed to /etc/nucleus/config.yaml). This module defines the pydantic model
that validates that file and computes every *derived* value so an operator only
ever sets a handful of primitives (chiefly `node.id`).

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



class NucleusConfig(BaseModel):
    """Top-level node configuration = the whole contract."""

    node: NodeConfig = Field(default_factory=NodeConfig)
    mesh: MeshConfig
    br_lan: BrLanConfig = Field(default_factory=BrLanConfig)
    ap: ApConfig
    eth0: Eth0Config = Field(default_factory=Eth0Config)

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
        }
