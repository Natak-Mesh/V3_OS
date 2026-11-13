"""Publish <serial>.local over mDNS on br-lan and eth0.

avahi-daemon announces the system hostname (<serial>-nucleus.local) on each
interface with that interface's own address. This adds the short name the same
way, so a phone on the AP gets the br-lan IP and a PC on eth0 gets the eth0 IP.
Talks to avahi over D-Bus (dbus_fast). Re-publishes when an address changes;
exits non-zero (systemd restarts it) if avahi drops the record.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess

IFACES = ("br-lan", "eth0")
AVAHI = "org.freedesktop.Avahi"
PROTO_INET = 0
FLAG_NO_REVERSE = 16          # reverse (PTR) stays with the system hostname
STATE_COLLISION, STATE_FAILURE = 3, 4
POLL_S = 10


def iface_ipv4s(ip_output: str) -> dict[str, str]:
    """{iface: first global IPv4} for IFACES, from `ip -4 -o addr show scope global`."""
    out: dict[str, str] = {}
    for line in ip_output.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] in IFACES and parts[2] == "inet":
            out.setdefault(parts[1], parts[3].split("/")[0])
    return out


def _current() -> dict[str, str]:
    return iface_ipv4s(subprocess.run(
        ["ip", "-4", "-o", "addr", "show", "scope", "global"],
        capture_output=True, text=True, check=False,
    ).stdout)


async def _run(name: str) -> None:
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    async def call(path, iface, member, signature="", body=()):
        r = await bus.call(Message(destination=AVAHI, path=path, interface=iface,
                                   member=member, signature=signature, body=list(body)))
        if r.message_type == MessageType.ERROR:
            raise RuntimeError(f"avahi {member}: {r.error_name} {r.body}")
        return r.body

    group, published = None, None
    while True:
        addrs = _current()
        if group and (await call(group, f"{AVAHI}.EntryGroup", "GetState"))[0] in (
                STATE_COLLISION, STATE_FAILURE):
            raise RuntimeError(f"avahi dropped {name}")
        if addrs != published:
            if group:
                await call(group, f"{AVAHI}.EntryGroup", "Free")
                group = None
            if addrs:
                group = (await call("/", f"{AVAHI}.Server", "EntryGroupNew"))[0]
                for ifname, ip in addrs.items():
                    await call(group, f"{AVAHI}.EntryGroup", "AddAddress", "iiuss",
                               (socket.if_nametoindex(ifname), PROTO_INET,
                                FLAG_NO_REVERSE, name, ip))
                await call(group, f"{AVAHI}.EntryGroup", "Commit")
            published = addrs
            print(f"mdns_alias: {name} -> {addrs}", flush=True)
        await asyncio.sleep(POLL_S)


def main() -> None:
    asyncio.run(_run(os.environ["NUCLEUS_MDNS_NAME"]))


if __name__ == "__main__":
    main()
