#!/usr/bin/env python3
"""Run the Fund Registry Bitcoin anchor backend preflight."""

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
    parser = argparse.ArgumentParser(description="Check Fund Registry tier3 Bitcoin anchor readiness.")
    parser.add_argument(
        "--allow-no-funds",
        action="store_true",
        help="Validate wallet RPC wiring without requiring confirmed anchor funds.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    module = load_app_module()
    settings = module.build_settings()
    store = module.FundRegistryStore(settings)
    payload = store.anchor_preflight_payload(require_funds=not args.allow_no_funds)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
