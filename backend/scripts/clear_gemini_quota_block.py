"""Clear a persisted Gemini quota stop only after provider reset is confirmed.

Run with ``--confirm-provider-reset`` only after the provider/account owner has
confirmed quota restoration. Without the flag, this command makes no change.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=BACKEND_DIR / ".env")

from services.provider_registry import gemini_credentials  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-provider-reset", action="store_true")
    args = parser.parse_args()
    if not args.confirm_provider_reset:
        print("No change. Confirm the provider quota has reset, then pass --confirm-provider-reset.")
        return 2
    gemini_credentials.clear_quota_exhaustion(provider_reset_confirmed=True)
    print("Persistent Gemini quota block cleared after operator confirmation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

