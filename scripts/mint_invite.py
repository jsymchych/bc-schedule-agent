#!/usr/bin/env python3
"""Mint a single-use signed invite URL for schedule-demo.

Usage:
  INVITE_SIGNING_SECRET=... python3 scripts/mint_invite.py --label prospect
  INVITE_SIGNING_SECRET=... python3 scripts/mint_invite.py --label jordan --ttl-days 7 \\
      --base-url https://schedule-demo.kitchenstack-ai.com
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bc_schedule_agent.invite_token import invite_url, mint_invite  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Mint schedule-demo signed invite URL")
    parser.add_argument("--label", default="invite", help="Operator label (not a secret)")
    parser.add_argument("--ttl-days", type=float, default=14.0, help="Invite expiry days")
    parser.add_argument(
        "--base-url",
        default=os.environ.get(
            "INVITE_BASE_URL", "https://schedule-demo.kitchenstack-ai.com"
        ),
        help="Host base URL",
    )
    parser.add_argument(
        "--secret",
        default=os.environ.get("INVITE_SIGNING_SECRET", ""),
        help="Or set INVITE_SIGNING_SECRET",
    )
    args = parser.parse_args()
    secret = (args.secret or "").strip()
    if not secret:
        print("REFUSE: INVITE_SIGNING_SECRET (or --secret) required", file=sys.stderr)
        return 2
    token = mint_invite(
        secret,
        label=args.label,
        ttl_seconds=max(60, int(args.ttl_days * 86400)),
    )
    print(invite_url(args.base_url, token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
