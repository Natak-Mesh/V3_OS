"""modem73 live link tuning: control-port client, log tail, mixer, API router.

Runtime diagnostics + live tuning of the running modem73 soundmodem, outside
the config pipeline except for an explicit "save" that writes the modem73_*
keys and runs apply (see router.py).
"""
