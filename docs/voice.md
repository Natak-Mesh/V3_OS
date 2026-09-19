# Mesh PTT Voice — hardware + soft PTT

Real-time push-to-talk voice between Nucleus nodes, carried as **UDP multicast**
directly on the wlan1 802.11s mesh (`239.10.10.N:5555`, PCM S16_LE 16 kHz, 20 ms
frames). One daemon per node (`nucleus-voice.service` → `nucleusd.voice.daemon`)
with two interchangeable PTT front-ends that put identical frames on the air:

- **Hardware PTT** — OpenVLM CM108 (`0d8c:0012`) tactical headset. Mic/speaker
  via ALSA, PTT via the HID GPIO. Hot-plugs: attaches/detaches automatically;
  everything else keeps running on nodes with no OpenVLM.
- **Soft PTT** — a phone/browser on the node AP opens the `/voice` page. The
  phone's own mic/speaker are the handset; audio streams to the daemon over a
  WebSocket (nginx proxies `wss://<host>/voice-ws` → 127.0.0.1:5557). Requires
  HTTPS (browser mic policy).

On receive, each talker gets a small jitter buffer and a self-clocked 20 ms mixer
overlays all active talkers (no floor control), fanning the mix to every playback
sink (OpenVLM `aplay` and every connected phone).

## Optional LoRa voice-text (STT/TTS)

When `voice.lora_enabled` is set, speech is transcribed locally (Vosk streaming
STT) while PTT is held, sent as **one standard-size Meshtastic text packet**
through the CoT bridge relay, and spoken on the receiving node with Piper TTS
(and shown in the `/voice` message log). One utterance = one packet; never
fragmented. Needs the Vosk/Piper models installed on the node.

## Ports / relays

- Voice mcast: UDP `239.10.10.N:5555`. Control: UDP 127.0.0.1:5556.
- Soft-PTT WS: 127.0.0.1:5557 (via nginx `/voice-ws`).
- LoRa voice-text relay (through CoT bridge): 5558/5559.
- LoRa Codec2 stream relay (advanced): 4244/4245.

## Config (`/etc/nucleus/config.yaml`, `voice:` section)

`enabled`, `channel`, `channels`, `jitter_ms`, `tx_gain`, plus LoRa voice-text
(`lora_enabled`, `lora_max_secs`, `lora_portnum`, `lora_hop_limit`,
`stt_engine`, `stt_model`, `stt_grammar`, `stt_cleanup`) and the advanced
Codec2 stream (`stream_enabled`, `stream_portnum`). Validated by
`nucleusd.schema.VoiceConfig`. There is **no separate .conf file** — this is the
single node config, edited via the web UI / `PUT /api/v1/config` /
`nucleusctl apply`.

The daemon runs as root (hidraw access) and is safe on nodes without an OpenVLM:
the mesh + soft-PTT paths run regardless.
