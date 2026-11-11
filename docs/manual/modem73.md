# modem73 soundmodem (analog radio)

Signal chain: `rnsd` → KISS over TCP `127.0.0.1:8001` → `modem73` (OFDM) →
DigiRig (USB audio + CP210x RTS PTT at `/dev/nucleus/modem`) → UV-5R.
`nucleus-modem73.service` runs `/opt/nucleus/bin/nucleus-modem73-up.sh`, rendered
from the `reticulum.modem73_*` keys in `config.yaml` by `nucleusctl apply`.

## Required settings

| Setting | Value | Why |
|---------|-------|-----|
| `reticulum.modem73_tx_level` (config.yaml) | `5` (default) | At `100`, OFDM's high peak-to-average ratio overdrives the UV-5R mic input / deviation limiter. Frames arrive but distorted (SNR ≈3.7 dB, BER 6–10%). |
| `reticulum.modem73_rx_gain` / `modem73_tx_gain` | `20` / `17` (default) | Sound-card capture / playback steps, pinned by the launch script on every start (AGC and the Mic→Speaker monitor loop forced off), so the radio's volume knob is the only unknown gain. |
| `reticulum.modem73_callsign` | blank (default) | Blank = the node id (`0042`). Sent in every frame, so the LIVE page shows who it heard. Max 9 chars `A-Z 0-9 /` (modem73 refuses anything else). |
| `--no-frag` (template, fixed) | — | Every offered mode's frame holds a full 500 B RNS packet. With fragmentation off, modem73 truncates oversize frames, which is why sub-512 B modes (BPSK 1/2: 256 B) were dropped. RNS refuses `fixed_mtu` < 500. |
| `--no-mfsk-rx` (template, fixed) | — | No MFSK mode is offered; its always-on decoders produced false "decodes" from receiver hiss. |
| `--csma-mode sync` (template, fixed) | `sync` | Radios run squelch-off, so receiver hiss is always present. The default `threshold` mode marks the channel busy above −30 dBFS, so hiss holds it permanently BUSY and the node never transmits. `sync` counts only a detected modem73 signal as busy. |
| `--csma-band vhf` (template, fixed) | `vhf` | Selects the VHF/UHF CSMA timing profile instead of modem73's `hf` default. Currently has **no effect**: the band only supplies values through `--csma-preset`, which isn't passed. The `CSMA:` line in `/var/log/modem73.log` still shows slot=500 ms, cw=8, burst=2, dither=250 ms. |
| `reticulum.modem73_mode` | `qpsk_1_2` (default) | TX mode only: receivers auto-detect, so nodes need not match. Offered modes send a full 500 B packet in < 4 s: `rdm_1200` (1150 bps, hardy fallback), OFDM `qpsk_1_2` (1577) up to `qam4096_5_6` (15959). `bpsk_1_2` was dropped (5 s/packet, 256 B frames); configs using it load as `qpsk_1_2`. |

Radio setup (manual, both nodes):

- Squelch **0 (off)**. This avoids squelch-open latency truncating the frame
  start.
- The volume knob is the only gain software can't set. Set it with the LIVE
  page (below) and leave it.

## Live tuning page (RETICULUM › NODE STATUS › MODEM73 LIVE)

Each section is a reading with its target and the control that fixes it:

| Section | Reading (target) | Fix |
|---|---|---|
| Heard from far node | sender, its mode, last 10 frames OK / CRC FAIL | far node: TX level, then Mode |
| RX level | dBFS of last decoded frame (−10…−30) | this radio's volume knob, RX gain |
| Signal quality | SNR (≥ 15 dB), BER (< 1 %) | far node: TX level / TX gain, then Mode |
| This node TX | frames sent, PTT, mode | TX level, TX gain, Mode, SEND TEST FRAME |

CRC-failed frames come from `/var/log/modem73.log` (the control port reports
decodes only); their SNR is the mean per-symbol SNR, and they carry no RX
level. Controls:

- **SEND TEST FRAME**: transmits one short frame now (keys the radio). The far
  node's LIVE page shows its level/SNR/BER. rnsd ignores it.
- **TX level / RX gain / TX gain — live**: change at once, kept until modem73
  restarts. The page warns when live values differ from saved ones.
- **Mode** + **SAVE + APPLY**: writes mode and levels to config.yaml and
  restarts modem73 (and rnsd when the mode changed).

Tuning order, one end at a time, with someone watching the other end's page:

1. **RX level first.** Far node sends test frames; on this node adjust the
   radio volume knob (coarse) and RX gain (fine) until level sits in
   **−10…−30 dBFS** (green). Level only updates when a frame decodes. If every
   frame is CRC FAIL, step the knob one way and keep the direction that raises
   SNR.
2. **Far-end TX level.** With level green, low SNR plus high BER means the far
   node's TX is overdriven. Lower **its** TX level (or TX gain below the 5 %
   floor) until SNR stops improving.
3. **Mode.** BER ≥ 8 % or CRC errors → step down (toward `rdm_1200`). SNR ≥
   15 dB with BER < 1 % → a faster mode will work. Each node sets its own TX
   mode.

First capture (2026-11-10, 0047 → 0042, 0047 at tx-level 100): level −12…−17
dBFS, SNR 4.9 dB, BER 6.5 %. That matches the overdrive signature in the bench
results below.

## Bench results (OFDM BPSK 1/2, `--csma-mode sync`)

Measured in the TUI from the RX node's Last SNR / BER, restarting the RX modem73
to clear stats before each step.

| Direction | Change | SNR | BER |
|-----------|--------|-----|-----|
| 0054 → 0042 | tx-level 50, RX Carrier −4.8 dB | 5.8 dB | 4.83% |
| 0054 → 0042 | RX volume set to Carrier −20..−30 dB | unchanged | unchanged |
| 0054 → 0042 | tx-level 20 | 7.0 dB | 2.59% |
| 0054 → 0042 | tx-level 10 | 11.7 dB | 0.51% |
| 0054 → 0042 | tx-level 5 | 12.7 dB | 0.42% |
| 0042 → 0054 | tx-level 5 | 12.6 dB | 0.56% |

SNR rose as TX level fell, which confirms TX overdrive. Changing RX level had
no effect, which rules out RX clipping. Going from 10 to 5 gained only +1 dB,
so overdrive is mostly resolved at 5.

## Bench results (OFDM QAM16 1/2, TX gain)

Two separate TX drive controls, same names everywhere:

| Name | config.yaml | Where it acts | Live control |
|------|-------------|---------------|--------------|
| **TX level** | `reticulum.modem73_tx_level` (5-100 %) | modem73 output (`--tx-level`, control port `tx_drive` = TX level / 100) | LIVE page TX level |
| **TX gain** | `reticulum.modem73_tx_gain` (0-37) | DigiRig ALSA `Speaker Playback Volume` (amixer simple control `Speaker`, card `Device` = `modem73_alsa_card`), step = (TX gain − 37) dB | LIVE page TX gain, or `amixer` below |

TX level is at its 5 % minimum; raising it makes the TX signal worse. To
lower TX drive further, lower TX gain on the TX node:

```bash
amixer -c Device sset Speaker <TX gain>   # TX gain 0-37 = -37..0 dB
amixer -c Device sget Speaker             # read back current TX gain
```

The `amixer` value is live only: every modem73 start resets it to
`reticulum.modem73_tx_gain`. To keep a TX gain, set that key in config.yaml
(or use the LIVE page SAVE + APPLY).

Test: TX level 5 % on 0047; only TX gain changed.

| Direction | TX gain | SNR | BER |
|-----------|---------|-----|-----|
| 0047 → 0042 | 17 (−20 dB) | not recorded | slightly above 14's |
| 0047 → 0042 | 14 (−23 dB) | ~14 dB | ~1 % |
| 0047 → 0042 | 11 (−26 dB) | not recorded | slightly above 14's |

TX gain 14 gave the lowest BER. Changing TX gain did not raise SNR.

## Running the modem73 TUI

The LIVE page replaces this for routine tuning. Use the TUI only for things
the page doesn't show (idle carrier level, BUSY detector state).

```bash
# 1. Stop the service. It holds KISS port 8001 and the audio device.
sudo systemctl stop nucleus-modem73

# 2. Launch the TUI: the service's flags without --headless.
modem73 --callsign 0042 --device "USB Audio Device, USB Audio" --tx-level 5 --modem ofdm --modulation QPSK --rate 1/2 --no-frag --no-mfsk-rx --ptt com --com-port /dev/nucleus/modem --com-line rts --csma-mode sync --csma-band vhf

# 3. Quit with q, then restart the service.
sudo systemctl start nucleus-modem73
```

The TUI flags must match the service. Compare with
`/opt/nucleus/bin/nucleus-modem73-up.sh`, or with `pgrep -a modem73` while the
service is running. rnsd reconnects to port 8001 automatically, so anything
it sends while the TUI is up goes out over the radio.

Bench test: run the TUI on both nodes, send a test pattern from one, and read
Last SNR / BER on the other. Restart the RX TUI to clear stats between runs.

## Verifying with Reticulum

- `rnstatus`: `TCPInterface[MODEM73 Soundmodem/127.0.0.1:8001]` should be `Up` at
  the mode's bitrate (1577 bps for `qpsk_1_2`). Its ↑/↓ counters prove RNS
  frames crossed the radio.
- `rnpath <destination_hash>` / `rnpath -t`: shows the interface a path uses.
  RNS keeps **one** path per destination. If the nodes also peer over eth0 or
  the Wi-Fi mesh, the path shows the faster interface, because the slower
  modem73 copy of the announce is dropped as a duplicate. Use the counters,
  or remove the other interfaces, to prove the radio path.
- `tail -f /var/log/modem73.log`: `TX: <n> bytes` lines on the sender, decode
  lines on the receiver.

## Sound-card gains

The DigiRig's ALSA controls (card `Device`) are managed by nucleusd:
`modem73_rx_gain` → `Mic Capture Volume` (0-35, −12…+23 dB) and
`modem73_tx_gain` → `Speaker Playback Volume` (0-37, −37…0 dB). They are set
on every modem73 start and adjusted live from the LIVE page.

- **RX gain** scales audio after the radio. It can recover the level after a
  bumped volume knob, as long as the knob isn't at zero.
- **TX gain** lowers TX drive below the `tx_level` floor of 5 %.
- Inspect by hand: `amixer -c Device contents`.

## Troubleshooting

- `Port 8001 is already in use`: another modem73 is running (the service, or a
  manual instance). Run `pgrep -a modem73`. Use `sudo kill <pid>` if it's
  root-owned.
- Constant **BUSY** in the TUI: squelch-off hiss with `threshold` CSMA. Confirm
  `--csma-mode sync` is in the flags.
- Low SNR / high BER: lower the TX level first, then check RX Carrier.
