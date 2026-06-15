#!/usr/bin/env python3
"""Admin helper for Fund Registry promo codes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("FUND_REGISTRY_DISABLE_AUTO_APP", "1")

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import app as fund_app  # noqa: E402


def build_store(db_path: Path) -> fund_app.FundRegistryStore:
    settings = fund_app.FundRegistrySettings(db_path=db_path)
    return fund_app.FundRegistryStore(settings)


def parse_expires_at(raw_value: str | None) -> object:
    if not raw_value:
        return None
    try:
        return fund_app.parse_timestamp(raw_value)
    except ValueError as exc:
        raise SystemExit(f"Invalid --expires-at value: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Fund Registry promo codes.")
    parser.add_argument(
        "--db",
        default=str(fund_app.DEFAULT_DB_PATH),
        help="Path to fundregistry SQLite DB (default: %(default)s)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create a promo code.")
    create_parser.add_argument("code", help="Promo code string.")
    create_parser.add_argument(
        "--tiers",
        choices=("badge", "vanity", "both"),
        default="badge",
        help="Which tier(s) the code can activate.",
    )
    create_parser.add_argument("--max-uses", type=int, default=1, help="Maximum uses. 0 means unlimited.")
    create_parser.add_argument(
        "--expires-at",
        help="UTC ISO timestamp, for example 2026-03-31T23:59:59Z. Omit for no expiry.",
    )

    subparsers.add_parser("list", help="List promo codes.")

    revoke_parser = subparsers.add_parser("revoke", help="Revoke a promo code.")
    revoke_parser.add_argument("code", help="Promo code string.")

    args = parser.parse_args()
    store = build_store(Path(args.db))

    if args.command == "create":
        valid_for_badge = args.tiers in {"badge", "both"}
        valid_for_vanity = args.tiers in {"vanity", "both"}
        created = store.create_promo_code(
            code=args.code,
            valid_for_badge=valid_for_badge,
            valid_for_vanity=valid_for_vanity,
            max_uses=args.max_uses,
            expires_at=parse_expires_at(args.expires_at),
        )
        print(json.dumps(created, indent=2, sort_keys=True))
        return 0

    if args.command == "list":
        print(json.dumps(store.list_promo_codes(), indent=2, sort_keys=True))
        return 0

    if args.command == "revoke":
        revoked = store.revoke_promo_code(args.code)
        print(json.dumps(revoked, indent=2, sort_keys=True))
        return 0

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
