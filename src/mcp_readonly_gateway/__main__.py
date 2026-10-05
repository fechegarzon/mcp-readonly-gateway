"""Command line entry point: `mcp-readonly-gateway --config policy.yaml`."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .gateway import Gateway
from .policy import PolicyError, load_policy
from .server import build_server

CALLER_ENV = "GATEWAY_CALLER"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcp-readonly-gateway",
        description="Serve a policy-gated, audited mailbox over MCP (stdio).",
    )
    parser.add_argument("--config", required=True, help="Path to the policy YAML file.")
    parser.add_argument(
        "--caller",
        default=os.environ.get(CALLER_ENV),
        help=f"Who this server acts for. Defaults to ${CALLER_ENV}.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the policy, print what this caller can do, and exit.",
    )
    args = parser.parse_args(argv)

    if not args.caller:
        parser.error(f"set --caller or ${CALLER_ENV}; there is no default caller.")

    try:
        policy = load_policy(args.config)
    except (OSError, PolicyError) as exc:
        print(f"mcp-readonly-gateway: {exc}", file=sys.stderr)
        return 2

    if args.caller not in policy.callers:
        known = ", ".join(sorted(policy.callers))
        print(
            f"mcp-readonly-gateway: caller {args.caller!r} is not in the policy. Known: {known}.",
            file=sys.stderr,
        )
        return 2

    gateway = Gateway.from_policy(policy, args.caller)

    if args.check:
        # stdout is free here because we are not serving MCP.
        print(json.dumps(gateway.call("gateway_info"), indent=2))
        return 0

    build_server(gateway).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
