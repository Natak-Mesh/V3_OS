"""Pure helpers for the Reticulum/LXMF direct-message lane.

Everything here is side-effect free and unit-testable off-box: no sockets, no
threads, and no hard requirement on the ``rns``/``lxmf`` packages at import time
(the msgpack codec and ``qrcode`` are imported lazily inside the few functions
that need them). The live stack wiring lives in ``rns_lane.py``; the contact
card wire format and the LXMF address derivation live here so they can be
exercised with real keys in tests.

Contact cards (replacing the old custom announce)
-------------------------------------------------
Nodes no longer flood a custom ``nucleus.node`` announce to advertise
themselves. Instead each node exposes a **contact card** — the minimum another
node needs to message it — shared out-of-band: pulled over the WiFi mesh from a
peer's web API, pasted as a link, or scanned from a QR code. A card is::

    {"v": 1, "name": <str>, "id": <int>,
     "lxmf_hash": <hex>,       # the node's lxmf.delivery destination hash
     "pubkey": <hex>}          # the node identity's 64-byte public key

The ``lxmf_hash`` is fully determined by ``pubkey`` (see ``lxmf_delivery_hash``),
so an importer recomputes it and rejects any card whose two halves disagree
(``verify_card``) — a card cannot claim an address it does not hold the key for.
With the public key in hand the importing node builds an outbound LXMF
destination and messages the peer directly, with no announce ever required.

The wire form of a card is a compact msgpack map, base64url-encoded, behind the
``nucleus-rns://`` scheme so it round-trips cleanly through a URL/QR/paste box.
"""

from __future__ import annotations

import base64
from typing import Optional

# Reticulum app/aspect names for the standard LXMF delivery destination. Fixed
# by the LXMF spec ("lxmf"/"delivery"); the old custom nucleus.node pair is gone.
LXMF_APP_NAME = "lxmf"
LXMF_DELIVERY_ASPECT = "delivery"

# Contact-card wire format. The scheme fronts a base64url msgpack blob so the
# whole card is one copy-pasteable token that also fits in a QR code.
CARD_SCHEME = "nucleus-rns"
CARD_VERSION = 1

# RNS/Identity hashing constants (RNS.Reticulum / RNS.Identity). Reproduced here
# so the address derivation is pure (no RNS import); locked to RNS by a test that
# compares against a hash produced by the real library.
_NAME_HASH_LEN = 10          # NAME_HASH_LENGTH (80 bits) in bytes
_TRUNC_HASH_LEN = 16         # TRUNCATED_HASHLENGTH (128 bits) in bytes
_PUBKEY_LEN = 64             # Identity.KEYSIZE (512 bits) in bytes: x25519 + ed25519


def _umsgpack():
    """Lazy msgpack codec, reusing the one vendored in RNS when available.

    Falls back to the standalone ``umsgpack``/``msgpack`` so the pure functions
    remain usable (and testable) on hosts without the full RNS install.
    """
    try:
        import RNS.vendor.umsgpack as mp  # type: ignore
        return mp
    except Exception:  # pragma: no cover - exercised only off-box
        try:
            import umsgpack as mp  # type: ignore
            return mp
        except Exception:
            import msgpack as mp  # type: ignore
            return mp


def _sha256(data: bytes) -> bytes:
    import hashlib
    return hashlib.sha256(data).digest()


def lxmf_delivery_hash(pubkey: bytes) -> str:
    """Derive a node's lxmf.delivery destination hash (hex) from its public key.

    Mirrors ``RNS.Destination.hash(identity, "lxmf", "delivery")`` exactly:
    the destination hash is ``SHA256( SHA256("lxmf.delivery")[:10] + idhash )``
    truncated to 16 bytes, where ``idhash = SHA256(pubkey)[:16]``. Pure so the
    importer can validate a card with no RNS stack; a test pins it to the real
    library's output. Raises ValueError on a wrong-sized key.
    """
    if len(pubkey) != _PUBKEY_LEN:
        raise ValueError(f"public key must be {_PUBKEY_LEN} bytes, got {len(pubkey)}")
    name_hash = _sha256(f"{LXMF_APP_NAME}.{LXMF_DELIVERY_ASPECT}".encode())[:_NAME_HASH_LEN]
    id_hash = _sha256(pubkey)[:_TRUNC_HASH_LEN]
    return _sha256(name_hash + id_hash)[:_TRUNC_HASH_LEN].hex()



def encode_card(card: dict) -> str:
    """Serialise a contact card to a ``nucleus-rns://<base64url>`` link.

    ``card`` is the API/JSON form (hex strings); on the wire the hash and key are
    packed as raw bytes to keep the token (and its QR code) small. Raises
    ValueError if the card is missing fields or carries a malformed key/hash.
    """
    try:
        pub = bytes.fromhex(card["pubkey"])
        lxmf = bytes.fromhex(card["lxmf_hash"])
    except (KeyError, ValueError) as e:
        raise ValueError(f"card has a missing/invalid field: {e}") from e
    blob = {
        "v": CARD_VERSION,
        "n": str(card.get("name", "")),
        "id": int(card.get("id", 0)),
        "l": lxmf,
        "p": pub,
    }
    packed = _umsgpack().packb(blob)
    token = base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")
    return f"{CARD_SCHEME}://{token}"


def decode_card(link: Optional[str]) -> Optional[dict]:
    """Parse a card link (or bare base64url token) back to the API/JSON form.

    Returns None for anything that is not a well-formed v1 card: wrong scheme,
    bad base64/msgpack, not a dict, wrong version, or malformed hash/key lengths.
    Never raises, so a pasted/scanned garbage string can't crash the importer.
    This validates *shape* only — call ``verify_card`` to confirm the address
    actually matches the key.
    """
    if not link or not isinstance(link, str):
        return None
    token = link.strip()
    if token.startswith(f"{CARD_SCHEME}://"):
        token = token[len(CARD_SCHEME) + 3:]
    pad = "=" * (-len(token) % 4)
    try:
        packed = base64.urlsafe_b64decode(token + pad)
        obj = _umsgpack().unpackb(packed)
    except Exception:
        return None
    if not isinstance(obj, dict) or obj.get("v") != CARD_VERSION:
        return None
    pub, lxmf = obj.get("p"), obj.get("l")
    if not (isinstance(pub, (bytes, bytearray)) and len(pub) == _PUBKEY_LEN):
        return None
    if not (isinstance(lxmf, (bytes, bytearray)) and len(lxmf) == _TRUNC_HASH_LEN):
        return None
    return {
        "v": CARD_VERSION,
        "name": str(obj.get("n", "")),
        "id": int(obj.get("id", 0)),
        "lxmf_hash": bytes(lxmf).hex(),
        "pubkey": bytes(pub).hex(),
    }


def verify_card(card: Optional[dict]) -> bool:
    """True iff the card's ``lxmf_hash`` matches the hash derived from its key.

    This is the trust check on import: a node cannot present a card for an
    address whose identity key it does not hold, because the address is a hash of
    that very key. Any shape/parse error returns False.
    """
    if not isinstance(card, dict):
        return False
    try:
        expected = lxmf_delivery_hash(bytes.fromhex(card["pubkey"]))
    except (KeyError, ValueError):
        return False
    return expected == str(card.get("lxmf_hash", "")).lower()


def card_qr_svg(link: str) -> bytes:
    """Render a card link as an SVG QR code (offline, scannable by another node).

    ``qrcode`` is a hard dependency of the package but imported lazily here so
    this module stays importable on minimal dev hosts. Raises ValueError on an
    empty link.
    """
    import io
    if not link:
        raise ValueError("empty card link")
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage,
                      box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()

