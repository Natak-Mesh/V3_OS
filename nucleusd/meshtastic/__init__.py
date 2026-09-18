"""Meshtastic integration: native meshtasticd radio config + ATAK CoT bridge.

Ported from Nucleus_OS (V2). The CoT bridge (cot_bridge.py) and radio helpers
are proven code; the only V3 change is that settings come from the single
/etc/nucleus/config.yaml instead of the old shell-style mesh.conf.
"""
