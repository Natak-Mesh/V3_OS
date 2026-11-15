# Tailscale page (TAILSCALE (VPN))

Live Tailscale VPN control. Outside the config pipeline (README §4): state
lives in tailscaled, not `config.yaml`; driven by `/api/v1/tailscale`.

## Quick help

Remote access to this node over Tailscale (VPN). Needs internet.

- **Connect** / **Disconnect** — turn the VPN on or off.
- **Connect (browser login)** — shows a login link; open it on any device.
- **Auth key** + **Join with auth key** — join a tailnet without a browser.
- **Switch tailnet** — move between tailnets this node already logged into.
- **Log out** — forget the current tailnet.
