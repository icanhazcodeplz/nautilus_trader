#!/usr/bin/env python3
"""Sync macOS system clock with Apple's time servers. Requires sudo."""

import subprocess
import sys

APPLE_TIME_SERVER = "time.apple.com"


def main() -> int:
    try:
        result = subprocess.run(
            ["sudo", "sntp", "-sS", APPLE_TIME_SERVER],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("sntp not found (expected on macOS by default).", file=sys.stderr)
        return 1

    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode

    print(f"Clock synced with {APPLE_TIME_SERVER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
