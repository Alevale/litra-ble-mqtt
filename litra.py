#!/usr/bin/env python3
"""Convenience wrapper so `python3 litra.py <cmd>` keeps working.

The real implementation lives in the `litra_ble` package (litra_ble/cli.py).
Once installed (`pip install .`) you can also just run `litra <cmd>`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from litra_ble.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
