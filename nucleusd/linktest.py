"""On-demand node-to-node link tests: ping + iperf3 over wlan1/halow0, rnprobe
over Reticulum.

Nothing runs in the background (1 GB nodes, README): iperf3 exists only for the
duration of one test. The testing node asks the peer's nucleusd
(POST /api/v1/linktest/iperf-server, sent over the radio under test) to spawn a
one-shot ``iperf3 -s -1`` bound to the peer's radio IP and wrapped in
``timeout``, then runs the client bound to its own radio IP + device, so the
traffic cannot fall back onto another radio. Babel's route choice is bypassed:
both ends are addressed on the link's own /24, which is a connected route.

Reticulum: ``rnprobe`` (run as the rnsd user against the shared instance) sends
probe packets to a contact's lxmf.delivery destination, which proves every
packet it receives. Reticulum picks the path itself, so the result reports the
interface the probe actually went over.

Outside the config pipeline (runtime diagnostics, like status.py). Parsers are
pure and fixture-tested; the subprocess/HTTP calls are thin wrappers.
"""

from __future__ import annotations

import ipaddress
import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import reticulum as reti
from .schema import HALOW_IFACE, NucleusConfig

LINKS = ("wifi", "halow")
IPERF_PORT = 5201
WEB_PORT = 8080
MAX_SECS = 30
# Peer iperf3 server lifetime beyond the test duration (control connect,
# teardown, slow HaLow links). `timeout` kills it if the client never arrives.
SERVER_GRACE_S = 15
SERVER_START_WAIT_S = 3.0
PEER_HTTP_TIMEOUT = 5
TCP_TABLE = "/proc/net/tcp"

# rnprobe ships in the same venv as nucleusd. It must run as the rnsd user with
# rnsd's config dir: as root it would write root-owned files into rnsd's storage.
RNPROBE = str(Path(sys.executable).parent / "rnprobe")
RETI_CONFIG_DIR = str(reti.TRANSPORT_IDENTITY.parents[1])
# Kept under nginx's default 60 s proxy_read_timeout (probe bounds in the router
# keep the worst case — path wait + 3 probes — below this).
RNPROBE_HARD_TIMEOUT = 55


class LinkTestError(RuntimeError):
    """The test could not run or the peer/tool failed."""


class LinkTestBusy(LinkTestError):
    """Another test (or iperf3 server) is already running on this node."""


class LinkTestBadRequest(LinkTestError):
    """Invalid target (e.g. testing against this node itself)."""


# ── addressing (derived from node ids, like every other address) ──

def local_addr(cfg: NucleusConfig, link: str) -> tuple[str, str]:
    """(device, this node's IP) on a link."""
    if link == "wifi":
        return "wlan1", cfg.mesh_ip
    if link == "halow":
        return HALOW_IFACE, cfg.halow_ip
    raise LinkTestBadRequest(f"unknown link: {link}")


def peer_addr(cfg: NucleusConfig, link: str, peer_id: int) -> str:
    """The peer's IP on a link: same /24 as ours, host part = its node id."""
    if peer_id == cfg.node.id:
        raise LinkTestBadRequest("peer is this node")
    _, ip = local_addr(cfg, link)
    return f"{ip.rsplit('.', 1)[0]}.{peer_id}"


def caller_allowed(cfg: NucleusConfig, host: str) -> bool:
    """Only mesh peers (wlan1/halow0 subnets) or localhost may start a server."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    nets = [ipaddress.ip_network(f"{local_addr(cfg, link)[1].rsplit('.', 1)[0]}.0/24")
            for link in LINKS]
    return any(ip in n for n in nets)


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except FileNotFoundError as e:
        raise LinkTestError(f"{cmd[0]} not installed") from e
    except subprocess.TimeoutExpired as e:
        raise LinkTestError(f"{Path(cmd[0]).name} timed out after {int(timeout)} s") from e


# ── ping ─────────────────────────────────────────────────────────

_PING_STATS = re.compile(r"(\d+) packets transmitted, (\d+) received")
_PING_RTT = re.compile(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms")


def parse_ping(out: str) -> dict | None:
    """iputils `ping -q` summary -> counts, loss %, rtt (ms). None if absent."""
    m = _PING_STATS.search(out or "")
    if not m:
        return None
    sent, recv = int(m.group(1)), int(m.group(2))
    res = {"sent": sent, "received": recv,
           "loss_pct": round(100 * (sent - recv) / sent, 1) if sent else 100.0,
           "rtt_ms": None}
    r = _PING_RTT.search(out)
    if r:
        res["rtt_ms"] = dict(zip(("min", "avg", "max", "mdev"),
                                 (float(x) for x in r.groups())))
    return res


def ping(cfg: NucleusConfig, link: str, peer_id: int, count: int = 5) -> dict:
    """ICMP ping to the peer's link IP, forced out the link's device."""
    dev, _ = local_addr(cfg, link)
    pip = peer_addr(cfg, link, peer_id)
    r = _run(["ping", "-n", "-q", "-c", str(count), "-i", "0.2", "-W", "2",
              "-I", dev, pip], timeout=count * 0.2 + 10)
    res = parse_ping(r.stdout)
    if res is None:
        raise LinkTestError((r.stderr or r.stdout).strip() or "ping failed")
    return {"link": link, "dev": dev, "peer_ip": pip, **res}


# ── iperf3 ───────────────────────────────────────────────────────

def parse_iperf(text: str) -> dict:
    """iperf3 -J client output -> throughput summary. Raises on iperf3 error."""
    try:
        d = json.loads(text)
    except (TypeError, ValueError) as e:
        raise LinkTestError("iperf3 produced no JSON output") from e
    if d.get("error"):
        raise LinkTestError(f"iperf3: {d['error']}")
    end = d.get("end") or {}
    sent, recv = end.get("sum_sent") or {}, end.get("sum_received") or {}
    if "bits_per_second" not in recv:
        raise LinkTestError("iperf3 returned no result")
    reverse = bool(((d.get("start") or {}).get("test_start") or {}).get("reverse"))
    return {
        # send = this node -> peer; receive = peer -> this node (-R).
        "direction": "receive" if reverse else "send",
        # Goodput delivered at the receiving end — the number that matters.
        "mbps": round(recv["bits_per_second"] / 1e6, 2),
        "mbps_sender": round(sent.get("bits_per_second", 0) / 1e6, 2),
        "bytes": recv.get("bytes"),
        "seconds": round(recv.get("seconds", 0), 2),
        "retransmits": sent.get("retransmits"),
    }


def listening_in(table: str, ip: str, port: int) -> bool:
    """True if /proc/net/tcp text has a LISTEN socket on ip:port.

    The kernel prints the IPv4 address as a host-order u32 in hex, i.e. the
    octets reversed on little-endian (aarch64): 10.20.1.42 -> 2A01140A.
    """
    want = "".join(f"{int(o):02X}" for o in reversed(ip.split("."))) + f":{port:04X}"
    for line in table.splitlines()[1:]:
        f = line.split()
        if len(f) > 3 and f[1] == want and f[3] == "0A":
            return True
    return False


def _listening(ip: str, port: int) -> bool:
    try:
        return listening_in(Path(TCP_TABLE).read_text(), ip, port)
    except OSError:
        return False


_server_lock = threading.Lock()
_server_proc: subprocess.Popen | None = None


def start_server(cfg: NucleusConfig, link: str, secs: int) -> dict:
    """Spawn a one-shot iperf3 server on this node's link IP (peer side).

    ``-1`` exits after one test; ``timeout`` bounds it if no client connects.
    Returns once the socket is listening, so the caller can connect at once.
    """
    global _server_proc
    dev, ip = local_addr(cfg, link)
    lifetime = int(secs) + SERVER_GRACE_S
    with _server_lock:
        if _server_proc is not None and _server_proc.poll() is None:
            raise LinkTestBusy("an iperf3 test is already running on this node")
        try:
            proc = subprocess.Popen(
                ["timeout", str(lifetime), "iperf3", "-s", "-1", "-B", ip,
                 "-p", str(IPERF_PORT)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except OSError as e:
            raise LinkTestError(f"cannot start iperf3: {e}") from e
        _server_proc = proc
        # Reap on exit so a finished server never lingers as a zombie.
        threading.Thread(target=proc.wait, daemon=True).start()
    deadline = time.monotonic() + SERVER_START_WAIT_S
    while time.monotonic() < deadline:
        if _listening(ip, IPERF_PORT):
            return {"ok": True, "ip": ip, "port": IPERF_PORT, "lifetime_s": lifetime}
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    proc.terminate()
    raise LinkTestError(f"iperf3 server did not start on {ip} "
                        f"(is iperf3 installed and {dev} up?)")


def _peer_start_server(peer_ip: str, link: str, secs: int) -> None:
    """Ask the peer's nucleusd to start its one-shot server (over the link)."""
    url = f"http://{peer_ip}:{WEB_PORT}/api/v1/linktest/iperf-server"
    req = urllib.request.Request(
        url, data=json.dumps({"link": link, "secs": secs}).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=PEER_HTTP_TIMEOUT) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        # An older peer has no such route: its static-file mount at / answers a
        # POST with 405 (404 if the mount is absent).
        if e.code in (404, 405):
            raise LinkTestError(f"peer {peer_ip} does not support link tests "
                                f"(update it)") from e
        try:
            detail = json.loads(e.read().decode()).get("detail") or e.reason
        except Exception:
            detail = e.reason
        if e.code == 409:
            raise LinkTestBusy(f"peer {peer_ip}: {detail}") from e
        raise LinkTestError(f"peer {peer_ip} could not start iperf3: {detail}") from e
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise LinkTestError(f"peer {peer_ip} unreachable: {reason}") from e


_test_lock = threading.Lock()


def iperf(cfg: NucleusConfig, link: str, peer_id: int, secs: int = 5,
          reverse: bool = False) -> dict:
    """TCP throughput to/from a peer over one radio (client side)."""
    dev, ip = local_addr(cfg, link)
    pip = peer_addr(cfg, link, peer_id)
    if not _test_lock.acquire(blocking=False):
        raise LinkTestBusy("a link test is already running on this node")
    try:
        _peer_start_server(pip, link, secs)
        cmd = ["iperf3", "-c", pip, "-p", str(IPERF_PORT), "-B", f"{ip}%{dev}",
               "-t", str(secs), "-J", "--connect-timeout", "3000"]
        if reverse:
            cmd.append("-R")
        r = _run(cmd, timeout=secs + 20)
        res = parse_iperf(r.stdout)
    finally:
        _test_lock.release()
    return {"link": link, "dev": dev, "peer_ip": pip, **res}



# ── Reticulum (rnprobe) ──────────────────────────────────────────

# rnprobe draws a braille spinner with backspaces; strip it before parsing.
_SPINNER = re.compile("[\b⢄⢂⢁⡁⡈⡐⡠]")
_SENT_PROBE = re.compile(r"^Sent probe \d+ \(\d+ bytes\) to <[0-9a-f]+>"
                         r"(?: via <[0-9a-f]+>)?(?: on (?P<iface>.+))?$")
_RTT = re.compile(r"^Round-trip time is (?P<v>[\d.]+) (?P<u>milliseconds|seconds) "
                  r"over (?P<hops>\d+) hops?(?P<rest>.*)$")
_RSSI = re.compile(r"\[RSSI (-?[\d.]+) dBm\]")
_SNR = re.compile(r"\[SNR (-?[\d.]+) dB\]")
_LQ = re.compile(r"\[Link Quality ([\d.]+)%\]")
_SUMMARY = re.compile(r"^Sent (\d+), received (\d+), packet loss ([\d.]+)%$")


def parse_rnprobe(out: str) -> dict:
    """rnprobe -v output (spinner and all) -> probe summary."""
    lines = [ln.strip() for ln in re.split(r"[\r\n]", _SPINNER.sub("", out or ""))]
    lines = [ln for ln in lines if ln]
    res = {"sent": 0, "received": 0, "loss_pct": None, "rtt_ms": [],
           "rtt_avg_ms": None, "hops": None, "interface": None,
           "rssi_dbm": None, "snr_db": None, "quality_pct": None, "error": None}
    for ln in lines:
        if m := _SENT_PROBE.match(ln):
            res["interface"] = m.group("iface") or res["interface"]
        elif m := _RTT.match(ln):
            v = float(m.group("v"))
            res["rtt_ms"].append(round(v * 1000 if m.group("u") == "seconds" else v, 3))
            res["hops"] = int(m.group("hops"))
            rest = m.group("rest")
            for key, rx in (("rssi_dbm", _RSSI), ("snr_db", _SNR), ("quality_pct", _LQ)):
                if mm := rx.search(rest):
                    res[key] = float(mm.group(1))
        elif m := _SUMMARY.match(ln):
            res["sent"], res["received"] = int(m.group(1)), int(m.group(2))
            res["loss_pct"] = float(m.group(3))
        elif ln == "Path request timed out":
            res["error"] = "no path to destination (path request timed out)"
    if res["rtt_ms"]:
        res["rtt_avg_ms"] = round(sum(res["rtt_ms"]) / len(res["rtt_ms"]), 3)
    if not res["sent"] and not res["error"]:
        res["error"] = lines[-1] if lines else "rnprobe produced no output"
    return res


def rnprobe(dest: str, probes: int = 3, timeout: int = 10) -> dict:
    """Probe a Reticulum contact's lxmf.delivery destination via rnsd."""
    # Without a shared instance rnprobe would start its own Reticulum stack on
    # rnsd's config (incl. the RNode serial port) — refuse unless rnsd is up.
    try:
        reti.transport()
    except reti.ReticulumError as e:
        raise LinkTestError(f"rnsd is not running: {e}") from e
    if not _test_lock.acquire(blocking=False):
        raise LinkTestBusy("a link test is already running on this node")
    try:
        r = _run(["runuser", "-u", reti.RETI_USER, "--", RNPROBE,
                  "--config", RETI_CONFIG_DIR, "-v", "-n", str(probes),
                  "-t", str(timeout), "-w", "1", "lxmf.delivery", dest.lower()],
                 timeout=RNPROBE_HARD_TIMEOUT)
    finally:
        _test_lock.release()
    return {"dest": dest.lower(), **parse_rnprobe(r.stdout + r.stderr)}

