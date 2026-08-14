"""Versioned data contracts and source-feasibility audits for Arrive90."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arrive90_data_contracts.source_audit import AuditInputs, run_source_audit

__all__ = ["AuditInputs", "run_source_audit"]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(name)
    from arrive90_data_contracts.source_audit import AuditInputs, run_source_audit

    return {"AuditInputs": AuditInputs, "run_source_audit": run_source_audit}[name]
