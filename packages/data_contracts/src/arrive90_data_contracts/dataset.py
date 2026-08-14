"""Frozen chronological split and audit-slice contracts for travel-time-v1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

from arrive90_data_contracts.realtime import require_utc
from arrive90_data_contracts.travel_time import DownstreamOutcomeState

BOSTON = ZoneInfo("America/New_York")


class DatasetSplit(StrEnum):
    """Complete, nonoverlapping 2024 service-date splits."""

    TRAINING = "TRAINING"
    MODEL_VALIDATION = "MODEL_VALIDATION"
    CALIBRATION = "CALIBRATION"
    FINAL_TEST = "FINAL_TEST"


class PeakPeriod(StrEnum):
    """Frozen weekday clock classification at the prediction anchor."""

    PEAK = "PEAK"
    OFF_PEAK = "OFF_PEAK"


class DestinationClass(StrEnum):
    """Frozen downstream destination-offset classes."""

    IMMEDIATE = "IMMEDIATE"
    MEDIUM = "MEDIUM"
    LONG = "LONG"
    TERMINAL = "TERMINAL"


class ScheduledRemainingClass(StrEnum):
    """Frozen scheduled remaining-duration classes."""

    SHORT = "SHORT"
    MEDIUM = "MEDIUM"
    LONG = "LONG"


class ScheduleDeviationClass(StrEnum):
    """Frozen absolute anchor schedule-deviation classes."""

    LOW = "LOW"
    TYPICAL = "TYPICAL"
    HIGH = "HIGH"


class ObservationGapClass(StrEnum):
    """Frozen most-recent observation-gap classes."""

    MISSING = "MISSING"
    LOW = "LOW"
    TYPICAL = "TYPICAL"
    HIGH = "HIGH"


def chronological_split(service_date: date) -> DatasetSplit:
    """Assign one service date to exactly one frozen 2024 split."""

    if date(2024, 1, 1) <= service_date <= date(2024, 7, 31):
        return DatasetSplit.TRAINING
    if date(2024, 8, 1) <= service_date <= date(2024, 9, 30):
        return DatasetSplit.MODEL_VALIDATION
    if date(2024, 10, 1) <= service_date <= date(2024, 10, 31):
        return DatasetSplit.CALIBRATION
    if date(2024, 11, 1) <= service_date <= date(2024, 12, 31):
        return DatasetSplit.FINAL_TEST
    raise ValueError("service date is outside the frozen 2024 dataset scope")


def peak_period(anchor_utc: datetime) -> PeakPeriod:
    """Classify one UTC anchor using the frozen Boston weekday clock intervals."""

    require_utc(anchor_utc, "anchor_utc")
    local = anchor_utc.astimezone(BOSTON)
    local_time = local.timetz().replace(tzinfo=None)
    is_peak_clock = time(7) <= local_time < time(10) or time(16) <= local_time < time(19)
    return (
        PeakPeriod.PEAK
        if local.isoweekday() in {1, 2, 3, 4, 5} and is_peak_clock
        else PeakPeriod.OFF_PEAK
    )


def destination_class(offset: int, *, is_terminal: bool) -> DestinationClass:
    """Classify one retained destination under the frozen offset rules."""

    if is_terminal:
        return DestinationClass.TERMINAL
    if offset == 1:
        return DestinationClass.IMMEDIATE
    if 2 <= offset <= 4:
        return DestinationClass.MEDIUM
    if 5 <= offset <= 8:
        return DestinationClass.LONG
    raise ValueError("destination offset must be between one and eight")


def scheduled_remaining_class(seconds: int) -> ScheduledRemainingClass:
    """Classify one positive scheduled remaining duration through 30 minutes."""

    if 1 <= seconds <= 600:
        return ScheduledRemainingClass.SHORT
    if seconds <= 1_200:
        return ScheduledRemainingClass.MEDIUM
    if seconds <= 1_800:
        return ScheduledRemainingClass.LONG
    raise ValueError("scheduled remaining seconds must be in (0, 1800]")


def schedule_deviation_class(seconds: float) -> ScheduleDeviationClass:
    """Classify the absolute anchor schedule deviation."""

    absolute = abs(seconds)
    if absolute <= 60:
        return ScheduleDeviationClass.LOW
    if absolute <= 300:
        return ScheduleDeviationClass.TYPICAL
    return ScheduleDeviationClass.HIGH


def observation_gap_class(seconds: float | None) -> ObservationGapClass:
    """Classify one optional prior-observation gap through the episode boundary."""

    if seconds is None:
        return ObservationGapClass.MISSING
    if not 0 <= seconds <= 600:
        raise ValueError("observation gap seconds must be in [0, 600]")
    if seconds <= 75:
        return ObservationGapClass.LOW
    if seconds <= 180:
        return ObservationGapClass.TYPICAL
    return ObservationGapClass.HIGH


@dataclass(frozen=True, slots=True)
class RetentionAuditRow:
    """Outcome-safe projection used by line retention, including on final-test dates."""

    example_id: str
    episode_id: str
    anchor_observation_id: str
    service_date: date
    split: DatasetSplit
    route_id: str
    direction_id: int
    peak_period: PeakPeriod
    schedule_match_state: str
    outcome_state: DownstreamOutcomeState
    interval_width_seconds: float | None
    likelihood_eligible: bool
    destination_offset: int | None

    def __post_init__(self) -> None:
        for field, value in (
            ("example_id", self.example_id),
            ("episode_id", self.episode_id),
            ("anchor_observation_id", self.anchor_observation_id),
            ("route_id", self.route_id),
            ("schedule_match_state", self.schedule_match_state),
        ):
            if not value:
                raise ValueError(f"{field} must be nonempty")
        if self.split is not chronological_split(self.service_date):
            raise ValueError("audit row split does not match its service date")
        if isinstance(self.direction_id, bool) or self.direction_id not in (0, 1):
            raise ValueError("direction_id must be zero or one")
        if self.interval_width_seconds is not None and self.interval_width_seconds <= 0:
            raise ValueError("interval width must be positive or null")
        if self.destination_offset is not None and not 1 <= self.destination_offset <= 8:
            raise ValueError("destination offset must be between one and eight or null")
