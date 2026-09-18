#!/usr/bin/env python3
"""
Meshtastic Radio Configurator + CoT Bridge status (V3)
======================================================
Pure helper functions (no web framework) for reading/applying the Meshtastic
radio config and reporting CoT bridge status. The FastAPI layer in router.py
maps these to HTTP routes.

Radio configurator: read/apply radio config and share the channel URL
(QR code) without ever needing the phone app. In V3 the radio is served by
native meshtasticd over TCP (localhost:4403); TCP supports multiple clients,
so the meshtastic CLI runs concurrently with the cot-bridge (no bridge pause
needed). The serial-path fallbacks below are retained only for robustness.

Ported from Nucleus_OS (V2); settings now come from /etc/nucleus/config.yaml.
"""

import base64
import glob
import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

MESH_CONF_PATH = os.environ.get("NUCLEUS_CONFIG", "/etc/nucleus/config.yaml")

# meshtastic CLI invoked as a module — mesh-web.service's PATH does not
# include ~/.local/bin where the `meshtastic` entry point lives.
MESHTASTIC_CMD = [sys.executable, "-m", "meshtastic"]

# Cached parsed radio config (survives page reloads without touching radio)
CONFIG_CACHE_PATH = "/tmp/meshtastic_config.json"
EXPORT_TMP_PATH = "/tmp/meshtastic_export.yaml"

# Serial release delay after stopping the bridge (SerialInterface takes a
# moment to fully release the port after the process exits)
SERIAL_RELEASE_SECS = 2

# After a config write the radio reboots; wait for it to come back
RADIO_REBOOT_WAIT_SECS = 30

# meshtasticd TCP connection (same constants as cot_bridge.py)
MESHTASTICD_HOST = "localhost"
MESHTASTICD_PORT = 4403

# Only one radio config operation at a time (they stop/start the bridge
# and hold the serial port)
_config_lock = threading.Lock()

# ── Async operation state ──────────────────────────────────────
# Radio config operations take 30-120s (CLI writes + radio reboots),
# far too long to hold a single HTTP request open (browsers time out).
# Instead, operations run in a background thread and the frontend
# polls /api/meshtastic/config/op-status until done.
_op_state = {
    'op': None,          # 'read' | 'apply' | 'channel_url'
    'status': 'idle',    # 'idle' | 'running' | 'done' | 'error'
    'error': None,
    'config': None,
    'started_at': None,
    'finished_at': None,
}
_op_state_lock = threading.Lock()


def _set_op_state(**kw):
    with _op_state_lock:
        _op_state.update(kw)


def _get_op_state():
    with _op_state_lock:
        return dict(_op_state)


def _load_mesh_cfg():
    """Load the meshtastic section from the V3 config.yaml (dict, never raises)."""
    try:
        import yaml
        with open(MESH_CONF_PATH) as f:
            raw = yaml.safe_load(f) or {}
        return raw.get("meshtastic", {}) or {}
    except Exception:
        return {}


def _is_meshtasticd():
    """True when the radio is served by native meshtasticd (TCP localhost:4403).

    V3 always uses native meshtasticd when meshtastic is enabled — there is no
    USB-serial path anymore — but we keep the serial fallbacks below so the
    ported helpers behave identically if meshtasticd is down.
    """
    return bool(_load_mesh_cfg().get("enabled", True))


def _start_op(op_name, work_fn):
    """Run work_fn (inside a bridge-pause) in a background thread.

    Acquires the config lock; returns False if another operation is
    already running. work_fn must return the parsed config dict or
    raise RuntimeError. The result lands in _op_state for polling.
    """
    if not _config_lock.acquire(blocking=False):
        return False
    _set_op_state(op=op_name, status='running', error=None, config=None,
                  started_at=int(time.time()), finished_at=None)

    def runner():
        try:
            with _bridge_paused():
                parsed = work_fn()
            _set_op_state(status='done', config=parsed,
                          finished_at=int(time.time()))
        except Exception as e:
            _set_op_state(status='error', error=str(e),
                          finished_at=int(time.time()))
        finally:
            _config_lock.release()

    threading.Thread(target=runner, daemon=True).start()
    return True


def _service_is_active():
    """Check if cot-bridge.service is currently running."""
    try:
        result = subprocess.run(
            ['sudo', 'systemctl', 'is-active', 'cot-bridge.service'],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == 'active'
    except Exception:
        return False


def _service_is_enabled():
    """Check if cot-bridge.service is enabled (starts on boot)."""
    try:
        result = subprocess.run(
            ['systemctl', 'is-enabled', 'cot-bridge.service'],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == 'enabled'
    except Exception:
        return False


def _radio_detected():
    """Check if a Meshtastic radio is available.

    For meshtasticd (TCP): check if the TCP port is accepting connections.
    For USB serial: check if /dev/ttyACM* exists.
    """
    if _is_meshtasticd():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect((MESHTASTICD_HOST, MESHTASTICD_PORT))
            s.close()
            return True
        except (OSError, socket.timeout):
            return False
    return bool(glob.glob('/dev/ttyACM*'))


def status():
    """Bridge/radio status dict. Bridge enable/disable is done via the normal
    config.yaml + apply flow (meshtastic.cot_bridge), not here."""
    m = _load_mesh_cfg()
    return {
        'bridge_enabled': bool(m.get('cot_bridge', True)) and bool(m.get('enabled', True)),
        'service_active': _service_is_active(),
        'service_enabled': _service_is_enabled(),
        'radio_detected': _radio_detected(),
    }


def bridge_logs():
    """Return last 50 cot-bridge journal lines + health summary."""
    try:
        result = subprocess.run(
            ['journalctl', '-u', 'cot-bridge.service', '-n', '50',
             '--no-pager', '-o', 'cat'],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split('\n') if result.stdout.strip() else []
    except Exception:
        lines = []

    # Parse health from the lines
    health = 'unknown'
    last_activity = None
    error_msg = None

    import time as _time
    from datetime import datetime as _dt

    now = _time.time()
    for line in reversed(lines):
        # Find last TX or RX line for activity timestamp
        if last_activity is None and ('[INFO]' in line and ('TX →' in line or 'RX ←' in line)):
            try:
                # Parse timestamp from -o cat line: "19:16:34 [INFO] ..."
                ts_str = line.split()[0]
                today = _dt.now()
                ts = _dt.strptime(ts_str, "%H:%M:%S").replace(
                    year=today.year, month=today.month, day=today.day)
                last_activity = int(now - ts.timestamp())
            except Exception:
                pass

        # Find errors/warnings
        if error_msg is None and ('[WARNING]' in line or '[ERROR]' in line):
            try:
                error_msg = line.split(']', 2)[-1].strip()
            except Exception:
                error_msg = line

    if last_activity is not None:
        if last_activity < 120:
            health = 'healthy'
        else:
            health = 'stale'
    elif lines:
        health = 'no_traffic'
    else:
        health = 'no_logs'

    # Override to error if recent warning/error found and no activity since
    if error_msg and (last_activity is None or last_activity > 60):
        health = 'error'

    return {
        'lines': lines,
        'health': health,
        'last_activity_secs': last_activity,
        'last_error': error_msg,
    }


# ═══════════════════════════════════════════════════════════════
#  RADIO CONFIGURATOR
#  Read/apply radio config + share channel URL, via the
#  bridge-pause pattern. See docs/meshtastic/meshtastic_configurator.md
# ═══════════════════════════════════════════════════════════════

# Editable field map. To add a new field later:
#   1. Add an entry here (name -> validator)
#   2. Add its CLI args in _build_command_groups()
#   3. Add it to _parse_export() so reads pick it up
#   4. Add an input to the Radio Config panel in meshtastic.html
VALID_MODEM_PRESETS = {
    'LONG_FAST', 'LONG_SLOW', 'LONG_MODERATE',
    'MEDIUM_FAST', 'MEDIUM_SLOW',
    'SHORT_FAST', 'SHORT_SLOW', 'SHORT_TURBO',
}

VALID_ROLES = {
    'CLIENT', 'CLIENT_MUTE', 'CLIENT_HIDDEN', 'TRACKER',
    'TAK', 'TAK_TRACKER', 'SENSOR', 'ROUTER',
    'ROUTER_CLIENT', 'ROUTER_LATE', 'REPEATER', 'LOST_AND_FOUND',
}

# LoRa region codes (RegionCode enum names). UNSET is excluded — the UI
# must never write UNSET back (that disables TX).
VALID_REGIONS = {
    'US', 'EU_433', 'EU_868', 'CN', 'JP', 'ANZ', 'KR', 'TW', 'RU',
    'IN', 'NZ_865', 'TH', 'LORA_24', 'UA_433', 'UA_868', 'MY_433',
    'MY_919', 'SG_923', 'PH_433', 'PH_868', 'PH_915', 'ANZ_433',
    'KZ_433', 'KZ_863', 'NP_865', 'BR_902',
}

# PSK modes accepted by --ch-set psk. Anything else is treated as an
# explicit key (base64:... or a hex/simpleN string the CLI understands).
PSK_KEYWORDS = {'random', 'default', 'none'}

# Port the nucleusd web app (this API's host) listens on. Peer nodes run
# the same app, so peer discovery fetches their config over this port.
WEB_PORT = 8080

# Per-peer HTTP timeout when polling mesh neighbours for their config.
PEER_HTTP_TIMEOUT = 3


def _validate_changes(changes):
    """Validate an apply request. Returns error string or None."""
    if not isinstance(changes, dict) or not changes:
        return 'No changes provided'

    allowed = {'owner', 'owner_short', 'modem_preset', 'hop_limit',
               'tx_power', 'role', 'channel_name', 'psk_random',
               'region', 'frequency_slot', 'psk'}
    unknown = set(changes) - allowed
    if unknown:
        return f'Unknown fields: {", ".join(sorted(unknown))}'

    if 'owner' in changes:
        v = str(changes['owner']).strip()
        if not v or len(v) > 39:
            return 'Long name must be 1-39 characters'
    if 'owner_short' in changes:
        v = str(changes['owner_short']).strip()
        if not v or len(v) > 4:
            return 'Short name must be 1-4 characters'
    if 'modem_preset' in changes:
        if str(changes['modem_preset']) not in VALID_MODEM_PRESETS:
            return f'Invalid modem preset: {changes["modem_preset"]}'
    if 'hop_limit' in changes:
        try:
            v = int(changes['hop_limit'])
        except (TypeError, ValueError):
            return 'Hop limit must be a number'
        if not 1 <= v <= 7:
            return 'Hop limit must be 1-7'
    if 'tx_power' in changes:
        try:
            v = int(changes['tx_power'])
        except (TypeError, ValueError):
            return 'TX power must be a number'
        if not 0 <= v <= 30:
            return 'TX power must be 0-30 dBm'
    if 'role' in changes:
        if str(changes['role']) not in VALID_ROLES:
            return f'Invalid role: {changes["role"]}'
    if 'channel_name' in changes:
        v = str(changes['channel_name']).strip()
        if not v or len(v) > 11:
            return 'Channel name must be 1-11 characters'
    if 'psk_random' in changes:
        if not isinstance(changes['psk_random'], bool):
            return 'psk_random must be true/false'
    if 'region' in changes:
        if str(changes['region']) not in VALID_REGIONS:
            return f'Invalid region: {changes["region"]}'
    if 'frequency_slot' in changes:
        try:
            v = int(changes['frequency_slot'])
        except (TypeError, ValueError):
            return 'Frequency slot must be a number'
        # 0 = auto (derive slot from channel name hash). Upper bound
        # depends on region/preset; 104 is the widest case (LORA_24).
        if not 0 <= v <= 104:
            return 'Frequency slot must be 0-104 (0 = auto)'
    if 'psk' in changes:
        v = str(changes['psk']).strip()
        if not v:
            return 'PSK must not be empty (use "none" to disable)'

    return None


def _build_command_groups(changes):
    """Translate validated changes into meshtastic CLI invocations.

    Returns a list of arg-lists. Groups are run sequentially with a
    radio-reboot wait between them (each config commit reboots the radio).
    Owner + lora settings go in one invocation; channel settings in another
    (mixing --set and --ch-set in one command is unreliable per meshtastic
    docs).
    """
    groups = []

    # Group 1: owner + lora config (--set-owner / --set)
    args = []
    if 'owner' in changes:
        args += ['--set-owner', str(changes['owner']).strip()]
    if 'owner_short' in changes:
        args += ['--set-owner-short', str(changes['owner_short']).strip()]
    if 'region' in changes:
        args += ['--set', 'lora.region', str(changes['region'])]
    if 'modem_preset' in changes:
        args += ['--set', 'lora.modem_preset', str(changes['modem_preset'])]
    if 'frequency_slot' in changes:
        args += ['--set', 'lora.channel_num',
                 str(int(changes['frequency_slot']))]
    if 'hop_limit' in changes:
        args += ['--set', 'lora.hop_limit', str(int(changes['hop_limit']))]
    if 'tx_power' in changes:
        args += ['--set', 'lora.tx_power', str(int(changes['tx_power']))]
    if 'role' in changes:
        args += ['--set', 'device.role', str(changes['role'])]
    if args:
        groups.append(args)

    # Group 2: primary channel settings (--ch-set ... --ch-index 0)
    args = []
    if 'channel_name' in changes:
        args += ['--ch-set', 'name', str(changes['channel_name']).strip()]
    # Explicit psk value takes precedence over the legacy psk_random bool.
    if 'psk' in changes:
        args += ['--ch-set', 'psk', str(changes['psk']).strip()]
    elif changes.get('psk_random'):
        args += ['--ch-set', 'psk', 'random']
    if args:
        groups.append(args + ['--ch-index', '0'])

    return groups


def _run_meshtastic(args, timeout=120):
    """Run a meshtastic CLI command. Returns (returncode, combined_output).

    If meshtasticd is enabled, --host localhost is injected so the CLI
    connects via TCP instead of USB serial auto-detect.
    """
    cmd = list(MESHTASTIC_CMD)
    if _is_meshtasticd():
        cmd += ['--host', MESHTASTICD_HOST]
    try:
        result = subprocess.run(
            cmd + args,
            capture_output=True, text=True, timeout=timeout
        )
        out = (result.stdout or '') + (result.stderr or '')
        return result.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return -1, f'meshtastic CLI timed out after {timeout}s'
    except Exception as e:
        return -1, str(e)


def _wait_for_radio(max_wait=RADIO_REBOOT_WAIT_SECS):
    """Wait for the radio to come back after a config-write reboot.

    For meshtasticd (TCP): wait for the TCP port to accept connections,
    then a short settle delay. Much faster than USB serial.

    For USB serial: wait for /dev/ttyACM* to (re)appear, then a longer
    settle delay to cover the radio's delayed self-reboot after config
    commits.
    """
    if _is_meshtasticd():
        # TCP: wait for meshtasticd to accept connections again
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect((MESHTASTICD_HOST, MESHTASTICD_PORT))
                s.close()
                break
            except (OSError, socket.timeout):
                time.sleep(1)
        # Short settle — meshtasticd handles the radio reboot internally
        time.sleep(5)
    else:
        # USB serial: wait for the port to (re)appear
        deadline = time.time() + max_wait
        while time.time() < deadline:
            if bool(glob.glob('/dev/ttyACM*')):
                break
            time.sleep(1)
        # Firmware settle time after the port appears. Must be long enough
        # to cover the radio's DELAYED self-reboot after a config commit
        # (owner changes reboot several seconds after the CLI returns) —
        # otherwise the bridge restarts, connects, and then loses the
        # radio mid-reboot.
        time.sleep(15)


@contextmanager
def _bridge_paused():
    """Stop the CoT bridge (if running) for the duration of a radio
    operation, then restart it.

    For meshtasticd (TCP): no-op — TCP supports multiple clients, so the
    CLI can talk to the radio concurrently with the cot-bridge.

    For USB serial: the bridge must be stopped to release the serial port.
    """
    if _is_meshtasticd():
        # TCP: no bridge pause needed
        yield
        return

    was_active = _service_is_active()
    if was_active:
        subprocess.run(
            ['sudo', 'systemctl', 'stop', 'cot-bridge.service'],
            capture_output=True, text=True, timeout=15
        )
        time.sleep(SERIAL_RELEASE_SECS)
    try:
        yield
    finally:
        if was_active:
            subprocess.run(
                ['sudo', 'systemctl', 'start', 'cot-bridge.service'],
                capture_output=True, text=True, timeout=15
            )


def _decode_channel_url(url):
    """Decode a meshtastic channel URL into channel summaries.

    Returns a list of {index, name, has_psk, psk_fingerprint} dicts, or
    [] on failure. The psk_fingerprint is a short hex digest of the key
    so two nodes' channels can be compared without exposing the key.
    Used both for display and to validate pasted URLs before touching
    the radio.
    """
    try:
        import hashlib
        from meshtastic.protobuf import apponly_pb2
        part = url.split('#', 1)[1]
        part += '=' * (-len(part) % 4)
        channel_set = apponly_pb2.ChannelSet()
        channel_set.ParseFromString(base64.urlsafe_b64decode(part))
        channels = []
        for i, s in enumerate(channel_set.settings):
            fp = ''
            if s.psk:
                fp = hashlib.sha256(s.psk).hexdigest()[:8]
            channels.append({
                'index': i,
                'name': s.name or '(default)',
                'has_psk': bool(s.psk),
                'psk_fingerprint': fp,
            })
        return channels
    except Exception:
        return []


def _parse_export(yaml_text):
    """Parse a --export-config YAML into the key fields the UI edits."""
    import yaml
    data = yaml.safe_load(yaml_text) or {}
    cfg = data.get('config', {}) or {}
    lora = cfg.get('lora', {}) or {}
    device = cfg.get('device', {}) or {}

    channel_url = data.get('channel_url', '') or data.get('channelUrl', '') or ''
    channels = _decode_channel_url(channel_url)

    return {
        'owner': data.get('owner', ''),
        'owner_short': data.get('owner_short', ''),
        'region': lora.get('region', 'UNSET'),
        'modem_preset': lora.get('modemPreset', 'LONG_FAST'),
        'frequency_slot': lora.get('channelNum', 0),
        'hop_limit': lora.get('hopLimit', 3),
        'tx_power': lora.get('txPower', 0),
        'role': device.get('role', 'CLIENT'),
        'channel_url': channel_url,
        'channels': channels,
        'channel_name': channels[0]['name'] if channels else '',
        'psk_fingerprint': channels[0]['psk_fingerprint'] if channels else '',
    }


def _read_config_from_radio():
    """Read config directly from the radio via the meshtastic Python API
    and parse it. Caller must hold the lock and have the bridge paused.
    Raises RuntimeError on failure.

    Does NOT use `meshtastic --export-config`: on meshtasticd/firmware 2.8
    that CLI path hangs forever in Node.get_ringtone() (the firmware only
    acks the request, never answers, and the CLI loop has no timeout).
    Only the fields the UI needs are read here.
    """
    try:
        from meshtastic.protobuf import config_pb2
        if _is_meshtasticd():
            from meshtastic.tcp_interface import TCPInterface
            iface = TCPInterface(hostname=MESHTASTICD_HOST,
                                 portNumber=MESHTASTICD_PORT)
        else:
            from meshtastic.serial_interface import SerialInterface
            iface = SerialInterface()
    except Exception as e:
        raise RuntimeError(f'Radio connect failed: {e}')

    try:
        node = iface.localNode
        # Bounded wait for localConfig (interface already waits for the
        # initial config handshake, but guard against a missing lora block).
        deadline = time.time() + 15
        while time.time() < deadline and not node.localConfig.HasField('lora'):
            time.sleep(0.2)
        if not node.localConfig.HasField('lora'):
            raise RuntimeError('Timed out waiting for radio config')

        lora = node.localConfig.lora
        device = node.localConfig.device
        channel_url = node.getURL() or ''
        channels = _decode_channel_url(channel_url)

        parsed = {
            'owner': iface.getLongName() or '',
            'owner_short': iface.getShortName() or '',
            'region': config_pb2.Config.LoRaConfig.RegionCode.Name(lora.region),
            'modem_preset': config_pb2.Config.LoRaConfig.ModemPreset.Name(
                lora.modem_preset),
            'frequency_slot': lora.channel_num,
            'hop_limit': lora.hop_limit,
            'tx_power': lora.tx_power,
            'role': config_pb2.Config.DeviceConfig.Role.Name(device.role),
            'channel_url': channel_url,
            'channels': channels,
            'channel_name': channels[0]['name'] if channels else '',
            'psk_fingerprint': (channels[0]['psk_fingerprint']
                                if channels else ''),
        }
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f'Config read failed: {e}')
    finally:
        try:
            iface.close()
        except Exception:
            pass

    parsed['read_at'] = int(time.time())
    _write_cache(parsed)
    return parsed


def _write_cache(parsed):
    """Atomically write the parsed config cache."""
    tmp = CONFIG_CACHE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(parsed, f)
    os.replace(tmp, CONFIG_CACHE_PATH)


def _read_cache():
    """Read the parsed config cache, or None if never read."""
    try:
        with open(CONFIG_CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return None


# ── Mesh peer discovery + one-click channel join ────────────────
# Nodes are discovered over the wifi mesh via Babel's kernel routes.
# Each peer runs this same nucleusd web app, so we fetch its
# /api/v1/meshtastic/config to compare channel identity and, on
# request, apply its channel URL locally (join). Only the channel
# identity (name/PSK/region/preset/slot via --ch-set-url) is copied —
# per-node settings (owner, role, tx_power, hop_limit) are untouched.


def _babel_peer_ips():
    """Return mesh node IPs discovered from Babel routes.

    `ip route show proto babel` gives next-hop IPs, each of which is a
    reachable mesh node running nucleusd.
    """
    ips = []
    try:
        import re
        result = subprocess.run(
            ['ip', 'route', 'show', 'proto', 'babel', 'dev', 'wlan1'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            m = re.search(r'via\s+(\S+)', line)
            if m and m.group(1) not in ips:
                ips.append(m.group(1))
    except Exception:
        pass
    return ips


def _fetch_peer_config(ip):
    """Fetch and summarise one peer's radio config. Returns a dict with
    reachability + channel identity, never raises."""
    import urllib.request
    entry = {
        'ip': ip,
        'reachable': False,
        'channel_name': '',
        'psk_fingerprint': '',
        'modem_preset': '',
        'region': '',
        'frequency_slot': None,
        'has_channel_url': False,
    }
    try:
        url = f'http://{ip}:{WEB_PORT}/api/v1/meshtastic/config'
        with urllib.request.urlopen(url, timeout=PEER_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        cfg = (data or {}).get('config') or {}
        entry['reachable'] = True
        entry['channel_name'] = cfg.get('channel_name', '')
        entry['psk_fingerprint'] = cfg.get('psk_fingerprint', '')
        entry['modem_preset'] = cfg.get('modem_preset', '')
        entry['region'] = cfg.get('region', '')
        entry['frequency_slot'] = cfg.get('frequency_slot')
        entry['has_channel_url'] = bool(cfg.get('channel_url'))
    except Exception:
        pass
    return entry


def peers():
    """List mesh nodes discovered over wifi and their channel identity.

    Returns {'peers': [...], 'local': {...}} so the frontend can diff each
    peer against this node and offer a one-click Join. Only nodes reachable
    over the wifi mesh appear — isolated nodes use the QR / URL-paste path.
    No radio access, so this is safe to call any time.
    """
    ips = _babel_peer_ips()
    results = []
    if ips:
        threads = []
        out = {}

        def worker(peer_ip):
            out[peer_ip] = _fetch_peer_config(peer_ip)

        for ip in ips:
            t = threading.Thread(target=worker, args=(ip,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=PEER_HTTP_TIMEOUT + 1)
        results = [out[ip] for ip in ips if ip in out]

    local = _read_cache() or {}
    return {
        'peers': results,
        'local': {
            'channel_name': local.get('channel_name', ''),
            'psk_fingerprint': local.get('psk_fingerprint', ''),
            'modem_preset': local.get('modem_preset', ''),
            'region': local.get('region', ''),
            'frequency_slot': local.get('frequency_slot'),
        },
    }


def config_join_peer(host: str):
    """Join a mesh peer's LoRa channel by fetching its channel URL and
    applying it locally (background op — poll op-status).

    Copies channel identity only (name/PSK/region/preset/slot). Local
    settings (owner, role, tx_power, hop_limit) are left untouched.
    """
    if not _radio_detected():
        raise RadioBadRequest('No radio detected')
    host = str(host or '').strip()
    if not host:
        raise RadioBadRequest('No peer host provided')
    # Only allow peers we actually discovered over the mesh (prevents this
    # endpoint being used to pull config from arbitrary hosts).
    if host not in _babel_peer_ips():
        raise RadioBadRequest('Host is not a known mesh peer')

    import urllib.request
    try:
        url = f'http://{host}:{WEB_PORT}/api/v1/meshtastic/config'
        with urllib.request.urlopen(url, timeout=PEER_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        peer_url = ((data or {}).get('config') or {}).get('channel_url', '')
    except Exception as e:
        raise RadioBadRequest(f'Failed to read peer config: {e}')

    if not peer_url or not _decode_channel_url(peer_url):
        raise RadioBadRequest('Peer has no valid channel URL')

    def work():
        rc, out = _run_meshtastic(['--ch-set-url', peer_url])
        if rc != 0:
            raise RuntimeError(f'Channel join failed: {out[-300:]}')
        _wait_for_radio()
        return _read_config_from_radio()

    if not _start_op('join_peer', work):
        raise RadioBusy('Another radio operation is in progress')
    return {'success': True, 'started': True}


# ── Exceptions the FastAPI router maps to HTTP status codes ─────
class RadioBadRequest(Exception):
    """400 — bad input or no radio detected."""


class RadioBusy(Exception):
    """409 — another radio operation is already running."""


class RadioNotReady(Exception):
    """404 — no cached config / channel URL yet."""


def config_cached():
    """Return the last-read radio config (instant — no radio access)."""
    return {'config': _read_cache(), 'busy': _config_lock.locked()}


def config_op_status():
    """Poll the state of the current/last radio config operation.

    Radio operations run in a background thread (they take 30-120s, too long
    for one HTTP request). The frontend polls this until status is done/error.
    """
    return _get_op_state()


def config_read():
    """Start reading config from the radio (background, poll op-status)."""
    if not _radio_detected():
        raise RadioBadRequest('No radio detected')
    if not _start_op('read', _read_config_from_radio):
        raise RadioBusy('Another radio operation is in progress')
    return {'success': True, 'started': True}


def config_apply(changes: dict):
    """Start applying changed fields to the radio (background, poll op-status).
    Radio reboots per config group — 30-120s total."""
    if not _radio_detected():
        raise RadioBadRequest('No radio detected')
    err = _validate_changes(changes)
    if err:
        raise RadioBadRequest(err)
    groups = _build_command_groups(changes)
    if not groups:
        raise RadioBadRequest('No changes provided')

    def work():
        for args in groups:
            rc, out = _run_meshtastic(args)
            if rc != 0:
                raise RuntimeError(f'Config write failed: {out[-300:]}')
            # Config commit reboots the radio — wait before next command
            _wait_for_radio()
        # Re-read so the cache/UI reflect what the radio actually has
        return _read_config_from_radio()

    if not _start_op('apply', work):
        raise RadioBusy('Another radio operation is in progress')
    return {'success': True, 'started': True}


def config_channel_url(url: str):
    """Apply a pasted channel URL (QR-code equivalent) to the radio.

    This is how config is shared between nodes: copy the URL from one node's
    Share panel, paste it here on another node."""
    if not _radio_detected():
        raise RadioBadRequest('No radio detected')
    url = str(url or '').strip()
    if not url:
        raise RadioBadRequest('No URL provided')
    if not _decode_channel_url(url):  # validate before touching the radio
        raise RadioBadRequest('Invalid channel URL — could not decode')

    def work():
        rc, out = _run_meshtastic(['--ch-set-url', url])
        if rc != 0:
            raise RuntimeError(f'Channel URL apply failed: {out[-300:]}')
        _wait_for_radio()
        return _read_config_from_radio()

    if not _start_op('channel_url', work):
        raise RadioBusy('Another radio operation is in progress')
    return {'success': True, 'started': True}


def config_qr_svg() -> bytes:
    """Render the cached channel URL as an SVG QR code (works offline).

    Scannable by the official Meshtastic app (BLE/handheld users) or any
    camera app (to copy the URL for pasting into another node's web UI).
    Raises RadioNotReady if no channel URL has been read yet.
    """
    import io
    url = (_read_cache() or {}).get('channel_url', '')
    if not url:
        raise RadioNotReady('No channel URL — read config first')
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage,
                      box_size=14, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()
