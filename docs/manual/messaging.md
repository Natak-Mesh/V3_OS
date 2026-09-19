# Messaging page

One conversation, delivered over WiFi + LoRa into a single message store. A
message from a plain Meshtastic radio appears here like any other; a per-message
badge shows which transport(s) delivered it. For the transport/store internals
see [messaging.md](messaging-internals.md).

A hint at the top notes that messages send over WiFi + LoRa and the Via badge
shows which transport delivered each one.

## Message log

| Column | Meaning |
|--------|---------|
| When | Time since the message (e.g. `5s`, `3m`, `2h`). |
| From | `me` (green) for your own messages, else the sender name. |
| Message | Message text. |
| Via | Transport(s) that delivered it: `wifi`, `lora`, or `wifi+lora` (`—` if unknown). |

Shows the most recent 100 messages. `messaging service unavailable` means the
messaging daemon is down; `no messages yet` means an empty store.

## Compose

A pinned compose dock at the bottom holds a text input (max 200 chars) and a
**Send** button. Type a message and Send (or submit) to post it; the draft is
kept if you leave and return without sending.

## Live updates

New messages arrive over a WebSocket push (the initial frame is history). If the
socket can't be established it falls back to a 3s poll. Either way messages merge
into one cache, de-duplicated by id and ordered oldest→newest.
