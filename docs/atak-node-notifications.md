# ATAK node notifications

Status: planned; not implemented. Work on `feature/atak-node-notifications`.

## Goal

Deliver node-generated notifications as GeoChat messages to the ATAK device
connected to this Nucleus node, without an ATAK plugin. Initially:

1. When an individual Wi-Fi mesh neighbor disconnects, identify the neighbor
   and the lost connection, even if other neighbors or links remain connected.
2. When an incoming Reticulum/LXMF message arrives, notify the ATAK device.
   The notification could also include the sender and message text; decide the
   exact contents before implementation.

Assume one ATAK device per node. Deliver notifications by unicast to that
device, not by multicast. Do not silently fall back to multicast if the
device cannot be identified or reached.

## Transport and existing code

- `nucleusd/meshtastic/cot_bridge.py` already listens for ATAK Protocol V1 on
  `br-lan` and injects CoT XML (Protocol V0) on multicast. This V1-in/XML-out
  combination is intentional; preserve the existing LoRa bridge behavior.
- ATAK's [GeoChat implementation](https://github.com/deptofdefense/AndroidTacticalAssaultKit-CIV/blob/main/atak/ATAK/app/src/main/java/com/atakmap/android/chat/GeoChatService.java)
  creates `b-t-f` events and dispatches individual messages to a contact.
  Its [contact transport rules](https://github.com/deptofdefense/AndroidTacticalAssaultKit-CIV/blob/main/commoncommo/core/contact-rxtx-rules.txt)
  describe direct TCP and UDP delivery: TCP endpoints commonly advertise port
  4242, while UDP chat uses port 17012. Follow the actual device's transport
  and settings; do not assume the source port of an incoming datagram is the
  destination port.
- Discover the local ATAK device from its traffic/contact information, as ATAK
  does for contacts. Restrict discovery to the local node's network, handle
  address changes and stale information, and avoid treating the node's own
  packets or traffic from other mesh nodes as the local device. Determine the
  precise discovery and reconnect behavior during implementation.
- Construct a properly addressed direct GeoChat event with a stable node
  sender identity and unique message ID. Use an XML library for escaping and
  verify the event and delivery against a real ATAK device. Unicast avoids
  multicast distribution but does not by itself encrypt data or authenticate
  the receiving device.

## Event sources and behavior

### Wi-Fi mesh neighbor loss

Use the per-interface neighbor information in `nucleusd/status.py` as the
starting point, particularly Babel neighbors on `wlan1`. Track individual
links, not `neighbour_nodes()`'s combined per-node count, so loss of Wi-Fi is
reported even when the same neighbor remains reachable over another interface.
Include a useful neighbor identifier and the lost interface in the message.

Establish a baseline on startup; don't report existing absence as a disconnect.
Handle flapping and duplicate observations without notification floods. A
failed or incomplete status read must not be mistaken for all neighbors
disconnecting: `babel_neighbours()` currently returns an empty list both for
an empty result and for a failed dump, so the implementation must distinguish
those cases before comparing snapshots.

### Reticulum message arrival

`nucleusd/messaging/service.py` receives inbound LXMF messages in
`_on_rns_message()`, stores them, and notifies local subscribers through
`_notify_rns()`. Use the inbound event rather than polling the message store;
do not notify for outbound messages or delivery-state updates. Avoid
duplicates and preserve the existing messaging behavior.

LXMF messages are direct messages. If sender names or message text are sent to
ATAK, they leave the LXMF protection boundary. Don't include message content in
logs, and don't broadcast it. Decide the notification contents and any
additional transport protection before enabling content forwarding.

## Implementation and verification

- Add a unicast GeoChat delivery path without disturbing existing multicast,
  Meshtastic receive-only, or message relay behavior. Choose the appropriate
  service boundary and local event handoff after inspecting their lifecycles;
  notifications should not depend unnecessarily on LoRa being available.
- Make unavailable targets and send failures observable. Define whether
  notifications are dropped or queued when ATAK is disconnected, including
  privacy and expiry implications; never imply delivery without evidence.
- Add focused tests for GeoChat formatting and escaping, local-device
  discovery, unicast destination selection and failures, individual Wi-Fi
  link loss, startup/read failures/flapping, and inbound-only LXMF events.
- Bench-test with an actual node and ATAK device: confirm the selected unicast
  transport and port, chat presentation and recipient, each notification
  trigger, behavior when ATAK disconnects or changes address, and that no
  notification is sent via multicast.

## Later: commands from ATAK chat

Command handling is outside the initial notification scope. A later phase
could accept explicitly addressed chat commands to query status or affect
radio behavior. Chat names and multicast source addresses are not
authentication; define authorization, replay protection, rate limits, audit
logging, and recovery from connectivity-changing commands before enabling
control. Existing receive-only and radio safety controls must remain effective.
