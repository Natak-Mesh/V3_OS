# Power page

Reboot or power off this node from the web UI. Both actions hit the node
immediately and take its mesh links down with it.

## Actions

- **Reboot** — prompts for confirmation, then reboots the node. The web UI drops
  while the node restarts and returns on its own once nucleusd is back.
- **Power off** — prompts for confirmation, then shuts the node down. It will
  **not** come back without physical access to power-cycle it.

Each action returns as soon as systemd has queued the transition, so the "node
going down" message appears just before connectivity is lost. There is no
progress log — unlike an update, the node does not stay up to report back.

## How it works

The page calls `POST /api/v1/power/reboot` / `POST /api/v1/power/poweroff`. The
API relays to `nucleusd/power.py`, which runs `systemctl reboot` / `poweroff`.
nucleusd runs as root, so no sudo is needed.
