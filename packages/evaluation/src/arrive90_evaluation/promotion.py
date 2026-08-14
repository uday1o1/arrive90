"""Predeclared release outcome without post-test threshold changes."""

from __future__ import annotations

from enum import StrEnum


class ReleaseMode(StrEnum):
    LEARNED_RECOMMENDATION = "LEARNED_RECOMMENDATION"
    MODEL_FREE_RECOMMENDATION = "MODEL_FREE_RECOMMENDATION"
    HISTORICAL_EXPLORER = "HISTORICAL_EXPLORER"


def choose_release_mode(*, learned_passed: bool, model_free_passed: bool) -> ReleaseMode:
    if learned_passed:
        return ReleaseMode.LEARNED_RECOMMENDATION
    if model_free_passed:
        return ReleaseMode.MODEL_FREE_RECOMMENDATION
    return ReleaseMode.HISTORICAL_EXPLORER
