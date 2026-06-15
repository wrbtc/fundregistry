#!/usr/bin/env python3
"""Run the Fund Registry expiry/tombstone lifecycle sweep."""

from __future__ import annotations

import importlib.util
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


def main() -> None:
    module = load_app_module()
    settings = module.build_settings()
    store = module.FundRegistryStore(settings)
    store.sweep_pages()
    print("fund-registry sweep complete")


if __name__ == "__main__":
    main()
