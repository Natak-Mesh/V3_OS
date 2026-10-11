# Nucleus Manual

Operator notes for the **`drone` build**: an airborne Raspberry Pi node that
rides the 802.11s mesh and talks to a flight controller over the GPIO UART.
The node is managed over ssh only.

## Access

- `ssh natak@<hostname>.local` (avahi mDNS), or by IP over the mesh
  (`10.20.1.<id>`), the AP / br-lan (`10.20.<id>.1`) or eth0.
- ssh on eth0 is always allowed by the host firewall.

## Layout

```
 wlan1 802.11s (2.4GHz) ── mesh ──► nucleus-mesh, babeld (L3), smcroute (multicast)
 wlan0 AP (5GHz) + eth0 ──────────► br-lan, hostapd, brlan-setup (operator access)
 GPIO UART (ttyS0) ───────────────► flight controller (no serial console/getty)
```

## Day-to-day

```bash
sudo nano /etc/nucleus/config.yaml   # edit the only config file
sudo nucleusctl validate             # check it
sudo nucleusctl apply --dry-run      # preview changed files + units
sudo nucleusctl apply                # render + restart affected units
nucleusctl status                    # interfaces, units, Babel neighbours/routes (JSON)
```

`apply` runs `config.yaml → schema (derive) → render templates →
write-if-changed → restart affected units`, and is idempotent.

## Update

```bash
sudo /opt/nucleus/bin/nucleus-update.sh
```

Pulls the `drone` branch, re-runs `install.sh`, then `nucleusctl apply`. It
does not reboot. The log is at `/var/log/nucleus-update.log`, and the exit status
is in `/var/log/nucleus-update.status`. Exit codes: 0 updated, 1 up-to-date,
2 offline, 3 dirty tree, 4 pull failed, 5 install.sh failed, 6 apply failed,
8 env error.

## Power

```bash
sudo systemctl reboot
sudo systemctl poweroff
```

## Reference

- [Config](config.md): every `config.yaml` key.
- [Firewall](firewall.md): the host firewall (UFW), driven from `config.yaml`.
- [README](../../README.md): internals.
