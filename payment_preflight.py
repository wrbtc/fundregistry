#!/usr/bin/env python3
"""Run the Fund Registry Bitcoin payment backend preflight."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
APP_PATH = BASE_DIR / "app.py"


def load_app_module():
    spec = importlib.util.spec_from_file_location("fund_registry_app", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check Fund Registry paid-checkout Bitcoin readiness.")
    parser.add_argument(
        "--require-funds",
        action="store_true",
        help="Also require confirmed wallet funds, even though receive-only checkout does not need them.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    module = load_app_module()
    settings = module.build_settings()
    store = module.FundRegistryStore(settings)
    payload = store.payment_preflight_payload(require_funds=args.require_funds)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
