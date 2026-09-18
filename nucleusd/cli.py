"""nucleusctl — command-line front-end.

Thin wrapper over the same modules the API uses, so config/apply/status behave
identically whether driven by a human at the shell, the web UI, or an external
app. This is what an operator runs after editing /etc/nucleus/config.yaml.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import config as cfgio
from . import status as statusmod
from .apply import apply as apply_config
from .apply import render_all


def _load_or_die():
    try:
        return cfgio.load()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_validate(_args) -> None:
    cfg = _load_or_die()
    print(f"ok: config valid (node {cfg.node.id} = {cfg.node.hostname})")


def cmd_render(args) -> None:
    cfg = _load_or_die()
    rendered = render_all(cfg)
    if args.dest:
        print(rendered.get(args.dest, f"error: no target {args.dest}"))
    else:
        for dest in rendered:
            print(dest)


def cmd_apply(args) -> None:
    cfg = _load_or_die()
    result = apply_config(cfg, dry_run=args.dry_run)
    tag = "would change" if args.dry_run else "changed"
    if result.changed:
        print(f"{tag}:")
        for c in result.changed:
            print(f"  {c}")
        if result.units_restarted:
            print("restarted: " + ", ".join(result.units_restarted))
    else:
        print("no changes — system already in sync")


def cmd_status(_args) -> None:
    print(json.dumps(statusmod.collect(), indent=2))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="nucleusctl", description="Nucleus V3 OS control")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="validate config.yaml").set_defaults(func=cmd_validate)

    pr = sub.add_parser("render", help="render templates (no writes)")
    pr.add_argument("dest", nargs="?", help="print one rendered target by dest path")
    pr.set_defaults(func=cmd_render)

    pa = sub.add_parser("apply", help="render + write changed files + restart units")
    pa.add_argument("--dry-run", action="store_true", help="show what would change")
    pa.set_defaults(func=cmd_apply)

    sub.add_parser("status", help="live runtime status as JSON").set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
