# USB devices

Nucleus recognises a small set of pluggable USB devices and binds each one to a
stable `/dev/nucleus/<role>` symlink. Services open that fixed path, never a
plug-order `/dev/ttyACM*`/`/dev/ttyUSB*` name, so a device always lands where its
owning service expects it — and a device that is **not** recognised gets no
symlink, so no Nucleus service ever opens it.

## Registry

The supported devices are a single table in `nucleusd/schema.py` (`USB_DEVICES`),
keyed by role, each listing the `(idVendor, idProduct)` pairs it matches:

| Role | Symlink | Supported device(s) | Used by |
|------|---------|---------------------|---------|
| `rnode` | `/dev/nucleus/rnode` | RAKwireless WisBlock RAK4631 (`239a:8029`) | Reticulum RNodeInterface — see [reticulum.md](reticulum.md) |

Only the RAK4631 is supported as an RNode for now.

## How it works

`nucleusctl apply` renders the registry into
`/etc/udev/rules.d/60-nucleus-usb.rules`. Each matching device:

- gets `SYMLINK+="nucleus/<role>"`, so it appears at `/dev/nucleus/<role>`;
- is marked `ID_MM_DEVICE_IGNORE=1` so ModemManager does not probe it;
- is marked `MTP_NO_PROBE=1` so `mtp-probe` does not send MTP handshake bytes to
  it (those can crash nRF52840 firmware, e.g. the RAK4631, until a hard reset) —
  the `60-` prefix orders this rule before the system `69-libmtp.rules`.

The rule file is a generated artifact (like every other rendered file): it is
written only when the registry changes, after which `apply` runs
`udevadm control --reload` + `udevadm trigger` so already-attached devices pick
up the new rule without a reboot. This supersedes the old
`60-meshtastic.rules`.

## Adding a device

1. Add the role and its `(vid, pid)` to `USB_DEVICES` in `nucleusd/schema.py`
   (lowercase hex, as udev matches `ATTRS{idVendor}`/`ATTRS{idProduct}`).
2. Have the owning service open `/dev/nucleus/<role>`.
3. `install.sh` → `nucleusctl apply` on the node; confirm the link:
   `ls -l /dev/nucleus/`.

A USB id may be claimed by only one role (enforced by a test), otherwise the
symlink would be ambiguous. Note that VID:PID identifies the board, not its
firmware — the RAK4631 reports `239a:8029` whether it runs RNode or Meshtastic.
