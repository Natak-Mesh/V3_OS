"""Nucleus text messaging: one store, two parallel transports (WiFi + LoRa).

See service.py for the single message store + fan-out, and router.py for the
FastAPI layer mounted under /api/v1/messaging by nucleusd.api.
"""
