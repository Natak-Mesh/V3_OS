# Nucleus architecture and data flows

These are complementary views of one Nucleus node, not a map of a particular
deployment. They show possible paths, not live status or a promise of delivery.
An enabled service may still lack its radio, a matching peer, or a usable link.
See the linked manual pages for setup and troubleshooting.

**Reading the diagrams:** arrows show the direction of the depicted flow;
double arrows show two-way communication. Dashed lines indicate an optional or
conditional path. Interface names such as `wlan1` are node-side interfaces.
The diagrams omit addresses, ports and settings that are not needed to explain
the view.

## 1. Connections around a node

This is a connection map, not a forwarding diagram. A line to the Nucleus does
not mean every other line can carry the same traffic.

```mermaid
flowchart LR
    phone["Phone / ATAK device"] <--> ap["AP: wlan0"]
    ap <--> lan["Local LAN: br-lan"]
    lan <--> node["Nucleus services"]
    node <--> wifi["Wi-Fi mesh: wlan1"]
    wifi <--> wifiPeers["Other Wi-Fi mesh nodes"]
    node -.-> halow["HaLow mesh: halow0"]
    halow -.-> halowPeers["Other HaLow nodes"]
    node <--> eth["Ethernet: eth0"]
    eth <--> wired["Internet / LAN or wired node"]
    node -.-> meshtastic["Meshtastic radio"]
    meshtastic -.-> meshtasticPeers["Meshtastic peers"]
    node -.-> rnode["RNode LoRa radio"]
    rnode -.-> rnsPeers["Reticulum peers"]
    node -.-> modem["modem73 + audio / PTT adapter"]
    modem -.-> analog["Analog radio"]
    node -.-> headset["OpenVLM headset"]
```

The dashed hardware links can communicate both ways when present and active;
dashes are used for their optional nature, not to imply transmit-only. The
Meshtastic and RNode LoRa radios are **separate** links with different
software and protocols. `eth0` can be a WAN or LAN connection; wired node
peering requires additional configuration. Babel handles IP unicast routing
over the enabled mesh interfaces and, when configured, Ethernet. Optional
Tailscale and TAK servers are not physical interfaces and are omitted here.

| Connection | Relevant settings or requirement |
|---|---|
| Wi-Fi mesh | `mesh.enabled`, matching mesh settings and an available `wlan1` radio. |
| HaLow | `halow.enabled`, supported radio present, and matching channel, bandwidth and primary index. |
| Ethernet node link | `eth0.babel` for Babel routing; `eth0.mcast_mode` separately controls ATAK multicast. |
| Meshtastic | `meshtastic.enabled`, a configured radio and compatible channel; transmit also depends on `meshtastic.tx_enabled`. |
| Reticulum radios | `reticulum.rnode_enabled` or `reticulum.modem73_enabled`, plus the appropriate radio and hardware. |

Details: [network stack](../README.md#3-network-stack),
[HaLow](manual/halow.md), [wired node link](manual/config.md#wired-node-link-eth0--turning-it-on),
[Reticulum](manual/reticulum.md), [Tailscale](manual/tailscale.md).

## 2. ATAK traffic

### Multicast

This example follows **outbound** ATAK multicast from a device on the local AP.
The bridge's LoRa path applies to supported CoT messages, not all ATAK
multicast traffic.

```mermaid
flowchart LR
    eud["Local ATAK device"] --> lan["br-lan"]
    lan --> mcast["smcroute<br/>ATAK multicast"]
    mcast --> wifi["wlan1 mesh"]
    mcast -.-> halow["halow0 mesh"]
    mcast -.-> eth["eth0<br/>configured modes only"]
    lan --> bridge["CoT bridge<br/>filter and encode"]
    bridge -.-> radio["meshtasticd + radio"]
    radio -.-> lora["Meshtastic LoRa"]
```

In the reverse direction, mesh multicast can be forwarded to `br-lan`;
received LoRa CoT is decoded by the bridge and injected into `br-lan`.
Ethernet relay has additional paths and loop-prevention constraints.

| Path | Conditions and boundaries |
|---|---|
| `br-lan` ↔ `wlan1` / `halow0` | ATAK multicast groups are routed without echoing back to the ingress interface. HaLow requires its enabled, present radio. |
| `wlan1` ↔ `halow0` | **No direct ATAK multicast forwarding** between these meshes. This is separate from the voice daemon's application-level fan-out shown below. |
| `br-lan` ↔ `eth0` | `eth0.mcast_mode: br_lan` or `mesh_relay`; `off` installs no eth0 multicast routes. |
| `eth0` ↔ radio meshes | `mesh_relay` only; at most one relay per Ethernet segment and other wired nodes must be RF-silent on the same meshes. |
| CoT → LoRa | `meshtastic.enabled`, `meshtastic.cot_bridge`, `meshtastic.tx_enabled`, working radio and accepted source; rate limits also apply. For relayed wired sources, configure `eth0.mcast_source_node_ids`. |

The node raises the TTL of incoming local ATAK multicast so it can be forwarded.
See [Ethernet mesh relay](manual/eth0-mesh-relay.md) for the complete
two-node topology, source filtering and constraints.

### Unicast

ATAK can also send traffic to a specific IP endpoint. This example shows a
device reaching a device on another node's LAN; the reverse direction needs
its own usable route and permitted traffic. The middle links are alternative
paths, not a requirement to use all three.

```mermaid
flowchart LR
    a["ATAK device A"] --> lana["Node A<br/>br-lan"]
    lana --> routea["Node A<br/>IP forwarding"]
    routea --> wifi["wlan1 mesh"]
    routea -.-> halow["halow0 mesh"]
    routea -.-> wire["eth0 wired node link"]
    wifi --> routeb["Node B<br/>IP forwarding"]
    halow -.-> routeb
    wire -.-> routeb
    routeb --> lanb["Node B<br/>br-lan"]
    lanb --> b["ATAK device B"]
```

| Path | Conditions and boundaries |
|---|---|
| Between node LANs | Devices need reachable destination addresses and routes through their nodes; Babel distributes node LAN routes across working links. Firewall rules and the destination application must permit the traffic. |
| Via `eth0` | The wired node link needs `eth0.babel` on participating nodes and appropriate firewall rules. Unicast routing is independent of `eth0.mcast_mode`. |
| Same LAN | Devices can communicate directly on the LAN without traversing the mesh. |
| TAK server | A configured ATAK client can also use IP unicast to a reachable TAK Server or OpenTAKServer. A server is optional and may be local or remote; access and credentials depend on its setup. |

This is normal IP forwarding, not ATAK multicast routing through `smcroute`;
the CoT-to-LoRa bridge shown above is not a general unicast IP router. The
node can also send its own [ATAK notifications](atak-node-notifications.md)
as unicast to a known local device.

## 3. Meshtastic chat

The Meshtastic client provides channel chats and direct messages through the
local radio. The CoT bridge owns the radio connection and also maintains the
chat history.

```mermaid
flowchart LR
    ui["Meshtastic chat UI"] <--> api["Meshtastic API"]
    api <--> bridge["CoT bridge<br/>radio client"]
    bridge --> store["Chat history<br/>in RAM"]
    store --> api
    bridge <--> daemon["meshtasticd"]
    daemon <--> radio["Meshtastic radio"]
    radio <--> peers["Meshtastic peers"]
```

| Path | Conditions and boundaries |
|---|---|
| Chat | `meshtastic.enabled`, `meshtastic.cot_bridge`, a running bridge, a usable radio and compatible peer channel settings. |
| Sending | `meshtastic.tx_enabled` must be on; receive-only mode still allows reception. |
| History | Chat history is kept in `/run` and cleared on reboot; preferences such as favorites and read marks persist. |

This is Meshtastic chat over LoRa, not ATAK GeoChat or Reticulum direct
messaging. See the [Meshtastic client](manual/meshtastic.md#meshtastic-client).

## 4. Push-to-talk voice

The operator selects one **transmit** transport; the voice daemon can listen
on its available receive paths independently. This is Nucleus PTT, not ATAK's
own multicast voice groups.

```mermaid
flowchart LR
    headset["OpenVLM headset"] <--> voice["Voice daemon"]
    browser["Phone / browser"] <--> nginx["nginx<br/>HTTPS + WebSocket"]
    nginx <--> voice
    voice --> ip["IP audio multicast"]
    ip --> wifi["wlan1 mesh"]
    ip -.-> halow["halow0 mesh"]
    voice -.-> text["Speech to text"]
    voice -.-> codec["Codec2 audio"]
    text -.-> bridge["CoT bridge"]
    codec -.-> bridge
    bridge -.-> radio["Meshtastic LoRa"]
```

The arrows show outgoing audio; received audio travels back to the daemon and
its local playback devices. For voice-text, the remote node converts received
text to speech; it is not a live audio stream.

| Path | Conditions and boundaries |
|---|---|
| IP voice | `voice.enabled`; matching voice channel and usable mesh interface. Sends on `wlan1` and enabled, available `halow0`, with duplicate frames suppressed on receive. |
| Browser PTT | HTTPS and a working WebSocket through nginx; no OpenVLM headset is required. |
| LoRa voice-text | `voice.lora_enabled`, installed speech models and a working Meshtastic bridge/radio. A completed utterance is sent as text, then spoken at the receiver. |
| LoRa Codec2 | `voice.stream_enabled`, usable codec and Meshtastic bridge/radio. This is a separate selected transport from voice-text. |

LoRa transmit also depends on `meshtastic.tx_enabled`. See
[voice internals](manual/voice-internals.md) and [voice controls](manual/voice.md).

## 5. Reticulum and direct messages

Reticulum is a separate network stack; its RNode radio is not the Meshtastic
radio. The Nucleus LXMF messaging lane attaches to the existing shared `rnsd`
instance rather than starting a second stack.

```mermaid
flowchart LR
    ui["Nucleus UI / API"] <--> lxmf["LXMF direct-message lane"]
    lxmf <--> conversations["Per-contact conversation store"]
    lxmf <--> rnsd["rnsd<br/>shared Reticulum instance"]
    client["Sideband / other RNS client"] <-.-> tcp["br-lan TCP server"]
    tcp <-.-> rnsd
    rnsd <--> auto["AutoInterface<br/>wlan1 / eth0 / halow0"]
    auto <--> peers["Reticulum peers"]
    rnsd <-.-> rnode["RNode LoRa"]
    rnsd <-.-> modem["modem73"]
    modem <-.-> analog["Analog radio"]
```

| Path | Conditions and boundaries |
|---|---|
| Nucleus direct messages | `messaging.enabled`, `messaging.rns.enabled` and running `rnsd`; a usable contact identity and path are needed. Conversations persist separately from Meshtastic chat. |
| AutoInterface | `reticulum.enabled` and `reticulum.auto_interface`; peer reachability depends on the configured interfaces and their links. HaLow requires its radio. |
| Client on AP/LAN | `reticulum.tcp_server`; the client connects to the node's `br-lan` address and configured TCP port. |
| RNode / modem73 | Corresponding `reticulum.rnode_enabled` / `reticulum.modem73_enabled` setting and working hardware. Automatic announces are not flooded onto these slow radios; an operator can announce over a selected radio. |

The rendered Reticulum template currently disables the public entry-node
interface even though entry-node settings exist. See
[Reticulum](manual/reticulum.md).

## 6. Configuration and apply

```mermaid
flowchart LR
    ui["Web UI / API"] --> validate["Schema validation"]
    validate --> config["Saved config.yaml"]
    edit["Direct config edit"] --> config
    config --> apply["Apply or dry run"]
    apply --> derive["Validate and derive values"]
    derive --> render["Render and compare templates"]
    render -->|dry run| report["Report proposed changes"]
    render -->|apply| files["Generated system files"]
    files --> services["Affected services<br/>restart / reload / enable"]
    cli["nucleusctl apply"] --> apply
```

The API validates and saves configuration separately from applying it. A dry
run reports intended changes without writing generated files or restarting
services. Apply updates files only when their contents change and reconciles
the affected services.

Not every operation goes through this pipeline: Tailscale has its own live
state, the AP radio toggle is non-persistent, and official TAK Server or
OpenTAKServer is separately installed. See the
[config pipeline](../README.md#1-core-logic--the-config-pipeline) and
[exceptions](../README.md#4-outside-the-pipeline-intentional).

When documenting a particular node, record the configuration and observed
interface/service status separately; neither alone proves that a peer is
reachable. Avoid including passwords, auth keys or other secrets in diagrams.
