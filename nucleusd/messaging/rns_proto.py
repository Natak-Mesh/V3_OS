"""Pure helpers for the Reticulum/LXMF direct-message lane.

Everything here is side-effect free and unit-testable off-box: no sockets, no
threads, and no hard requirement on the ``rns``/``lxmf`` packages at import
time. The live stack wiring lives in ``rns_lane.py``; the LXMF address
derivation lives here so it can be exercised with real keys in tests.

Identifying a contact
---------------------
A contact is identified by its **lxmf.delivery destination hash** — the standard
Reticulum/LXMF address, 32 hex characters, that every LXMF client (Sideband,
MeshChat, NomadNet, other Nucleus nodes) shows and accepts. That hash is the
only thing the operator needs to add a contact; see ``valid_lxmf_hash``.

The hash is fully determined by the identity's public key (see
``lxmf_delivery_hash``). When we have a peer's public key — learned from an RNS
path response or carried in a mesh-pulled card — we recompute the hash and
reject any mismatch (``verify_card``): a key cannot claim an address it does not
derive. With the key in hand RNS can address the peer directly.

(Nucleus nodes can also discover each other over the WiFi mesh, where one node
pulls another's ``card`` — ``{v,name,id,lxmf_hash,pubkey}`` — over the local HTTP
API. That is an internal convenience, not a shareable wire format: anything
leaving the node is a plain destination hash.)
"""

from __future__ import annotations

from typing import Optional

# Reticulum app/aspect names for the standard LXMF delivery destination. Fixed
# by the LXMF spec ("lxmf"/"delivery"); the old custom nucleus.node pair is gone.
LXMF_APP_NAME = "lxmf"
LXMF_DELIVERY_ASPECT = "delivery"

# Version tag on the mesh-pulled card dict (internal HTTP convenience only).
CARD_VERSION = 1

# RNS/Identity hashing constants (RNS.Reticulum / RNS.Identity). Reproduced here
# so the address derivation is pure (no RNS import); locked to RNS by a test that
# compares against a hash produced by the real library.
_NAME_HASH_LEN = 10          # NAME_HASH_LENGTH (80 bits) in bytes
_TRUNC_HASH_LEN = 16         # TRUNCATED_HASHLENGTH (128 bits) in bytes
_PUBKEY_LEN = 64             # Identity.KEYSIZE (512 bits) in bytes: x25519 + ed25519


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



def valid_lxmf_hash(value: object) -> bool:
    """True iff ``value`` is a well-formed lxmf.delivery destination hash.

    A destination hash is TRUNCATED_HASHLENGTH (128 bits) — exactly 32 hex
    characters. This is the shape check for adding a contact by hash alone (no
    key yet); it does not and cannot verify the hash belongs to any identity.
    """
    if not isinstance(value, str):
        return False
    s = value.strip().lower()
    if len(s) != _TRUNC_HASH_LEN * 2:
        return False
    try:
        bytes.fromhex(s)
    except ValueError:
        return False
    return True


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

