"""Fail closed unless a travel-time-v1.2 milestone report is ACCEPTED."""

from __future__ import annotations

from arrive90_data_contracts.gate_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
