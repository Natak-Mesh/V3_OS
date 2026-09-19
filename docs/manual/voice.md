# Voice page (VOICE / PTT)

Mesh push-to-talk voice. The TUI **Voice** page shows daemon state and switches
channel; the full soft-PTT handset is a standalone page at `/voice`. For the
audio/transport internals see [voice.md](voice-internals.md).

## TUI Voice page

Status (auto-refreshed every 5s). `voice daemon unavailable` means the voice
daemon is down.

| Field | Meaning |
|-------|---------|
| PTT | `TX` (green) while transmitting, else `idle`. |
| Channel | Current channel number and label. |
| Talkers | Count of remote sources currently being received (green if >0). |
| Handset | `OpenVLM card <n>` if a hardware headset is attached, else `none (phone only)`. |
| Transport | Active transport (`ip` by default). |
| LoRa STT | Shown only when LoRa voice-text is enabled: STT ready state. |

Controls:
- **Set channel** — cycle-through picker of named channels; **Switch channel**
  applies the selection.
- **Open soft-PTT handset (/voice)** — opens the standalone handset page.

## Soft-PTT handset (`/voice`)

Standalone page (not part of the CLI/TUI shell). It forces HTTPS/443 on load
because mic capture needs a secure context and the `/voice-ws` WebSocket proxy
lives only on the nginx HTTPS vhost; opened over http it redirects to
`https://<host>/voice`.

Header shows `node`, `headset` hardware, and `link` (voice-ws state).

- **TALK** button — press & hold to transmit (or hold the Spacebar). Turns red
  while talking; disabled until the link is ready.
- **Transport** — tappable list to pick the audio transport.
- **Channel** — tappable list of named channels; loads from the daemon.
- **Receiving** — tags for who is currently talking (`No one talking` when idle).
- **BACK** — returns to the TUI Voice page (browser history), or the shell root.

Audio is 16 kHz mono S16_LE in 20 ms frames; the mic is downsampled to 16 kHz
and received frames are upsampled to the local AudioContext rate, with a small
(~80 ms) jitter buffer.
