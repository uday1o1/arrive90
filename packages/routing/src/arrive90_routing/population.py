"""Outcome-independent historical query population generation."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from arrive90_data_contracts.candidates import HistoricalBaseQuery, HistoricalDeadlineVariant


def encode_key(values: tuple[str, ...]) -> bytes:
    output = bytearray()
    for value in values:
        encoded = value.encode()
        output.extend(len(encoded).to_bytes(4, "big"))
        output.extend(encoded)
    return bytes(output)


@dataclass(frozen=True)
class StationPair:
    origin_station_id: str
    destination_station_id: str
    stratum: str

    def __post_init__(self) -> None:
        if self.origin_station_id == self.destination_station_id:
            raise ValueError("origin and destination must differ")


@dataclass(frozen=True)
class PopulationConfig:
    public_seed: str = "arrive90-v1-public-query-seed"
    maximum_pairs_per_stratum: int = 12
    readiness_horizons_minutes: tuple[int, ...] = (0, 5, 10, 15)
    query_start_local: time = time(6)
    query_end_local: time = time(23)
    query_step_minutes: int = 30
    deadline_slacks_minutes: tuple[int, ...] = tuple(range(5, 181, 5))
    observation_horizon_minutes: int = 210
    agency_timezone: str = "America/New_York"
    query_generation_version: str = "historical-query-v1"

    def __post_init__(self) -> None:
        if self.maximum_pairs_per_stratum <= 0 or self.query_step_minutes <= 0:
            raise ValueError("population limits must be positive")
        if self.query_end_local < self.query_start_local:
            raise ValueError("query local interval must be increasing")
        if not self.readiness_horizons_minutes:
            raise ValueError("readiness horizon inventory cannot be empty")
        if not self.deadline_slacks_minutes:
            raise ValueError("deadline inventory cannot be empty")


@dataclass(frozen=True)
class QueryPopulation:
    selected_pairs: tuple[StationPair, ...]
    base_queries: tuple[HistoricalBaseQuery, ...]
    deadline_variants: tuple[HistoricalDeadlineVariant, ...]
    manifest_hash: str


def _json_bytes(value: object) -> bytes:
    def default(item: object) -> str:
        if isinstance(item, (date, datetime, time)):
            return item.isoformat()
        raise TypeError(f"cannot encode {type(item).__name__}")

    return (
        json.dumps(value, default=default, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _digest(seed: str, values: tuple[str, ...]) -> bytes:
    return hmac.digest(seed.encode(), encode_key(values), "sha256")


def select_station_pairs(
    pairs: Iterable[StationPair], config: PopulationConfig
) -> tuple[StationPair, ...]:
    unique = set(pairs)
    by_stratum: dict[str, list[StationPair]] = defaultdict(list)
    for pair in unique:
        by_stratum[pair.stratum].append(pair)
    selected: list[StationPair] = []
    for stratum in sorted(by_stratum, key=str.encode):
        ordered = sorted(
            by_stratum[stratum],
            key=lambda pair: (
                _digest(
                    config.public_seed,
                    (
                        "station-pair",
                        stratum,
                        pair.origin_station_id,
                        pair.destination_station_id,
                    ),
                ),
                pair.origin_station_id.encode(),
                pair.destination_station_id.encode(),
            ),
        )
        selected.extend(ordered[: config.maximum_pairs_per_stratum])
    return tuple(
        sorted(
            selected,
            key=lambda pair: (
                pair.stratum.encode(),
                pair.origin_station_id.encode(),
                pair.destination_station_id.encode(),
            ),
        )
    )


def _local_query_times(service_date: date, config: PopulationConfig) -> tuple[datetime, ...]:
    zone = ZoneInfo(config.agency_timezone)
    current = datetime.combine(service_date, config.query_start_local, zone)
    end = datetime.combine(service_date, config.query_end_local, zone)
    result: list[datetime] = []
    while current <= end:
        result.append(current.astimezone(UTC))
        current += timedelta(minutes=config.query_step_minutes)
    return tuple(result)


def _base_identifier(values: tuple[str, ...]) -> str:
    return hashlib.sha256(encode_key(values)).hexdigest()


def _lattice() -> tuple[tuple[str, int], ...]:
    return tuple((target, cap) for target in ("0.80", "0.90", "0.95") for cap in range(0, 21))


def generate_query_population(
    pairs: Iterable[StationPair],
    service_dates: Iterable[date],
    *,
    schedule_version_by_date: Mapping[date, str],
    split_by_date: Mapping[date, str],
    config: PopulationConfig | None = None,
) -> QueryPopulation:
    """Generate and balance the frozen population using no realized outcomes."""

    config = config or PopulationConfig()
    selected_pairs = select_station_pairs(pairs, config)
    dates = tuple(sorted(set(service_dates)))
    if set(dates) - schedule_version_by_date.keys():
        raise ValueError("a retained service date is missing a schedule version")
    if set(dates) - split_by_date.keys():
        raise ValueError("a retained service date is missing a chronological split")
    bases: list[HistoricalBaseQuery] = []
    pending_variants: list[tuple[str, HistoricalBaseQuery, int, datetime, bytes, bytes]] = []
    for service_date in dates:
        for pair in selected_pairs:
            for query_time in _local_query_times(service_date, config):
                for horizon in config.readiness_horizons_minutes:
                    ready = query_time + timedelta(minutes=horizon)
                    base_values = (
                        service_date.isoformat(),
                        pair.origin_station_id,
                        pair.destination_station_id,
                        query_time.isoformat(),
                        str(horizon),
                        schedule_version_by_date[service_date],
                        config.query_generation_version,
                    )
                    query_id = _base_identifier(base_values)
                    base = HistoricalBaseQuery(
                        query_id=query_id,
                        query_time_utc=query_time,
                        service_date=service_date,
                        origin_station_id=pair.origin_station_id,
                        destination_station_id=pair.destination_station_id,
                        ready_at_utc=ready,
                        observation_horizon_utc=ready
                        + timedelta(minutes=config.observation_horizon_minutes),
                        schedule_version_id=schedule_version_by_date[service_date],
                        query_generation_version=config.query_generation_version,
                        sampling_stratum=pair.stratum,
                        base_query_weight=1.0,
                        chronological_split=split_by_date[service_date],
                    )
                    bases.append(base)
                    for slack in config.deadline_slacks_minutes:
                        deadline = ready + timedelta(minutes=slack)
                        encoded = encode_key((query_id, str(slack), deadline.isoformat()))
                        assignment_digest = hmac.digest(
                            config.public_seed.encode(), encoded, "sha256"
                        )
                        pending_variants.append(
                            (
                                split_by_date[service_date],
                                base,
                                slack,
                                deadline,
                                assignment_digest,
                                encoded,
                            )
                        )
    lattice = _lattice()
    variants: list[HistoricalDeadlineVariant] = []
    by_split: dict[str, list[tuple[str, HistoricalBaseQuery, int, datetime, bytes, bytes]]] = (
        defaultdict(list)
    )
    for item in pending_variants:
        by_split[item[0]].append(item)
    for split in sorted(by_split, key=str.encode):
        ordered = sorted(by_split[split], key=lambda item: (item[4], item[5]))
        for index, (_, base, slack, deadline, assignment_digest, encoded) in enumerate(ordered):
            target, cap = lattice[index % len(lattice)]
            variants.append(
                HistoricalDeadlineVariant(
                    variant_id=hashlib.sha256(encoded).hexdigest(),
                    base_query_id=base.query_id,
                    deadline_utc=deadline,
                    deadline_slack_minutes=slack,
                    variant_weight=base.base_query_weight / len(config.deadline_slacks_minutes),
                    assigned_reliability_target=target,
                    assigned_maximum_extra_time_minutes=cap,
                    assignment_digest=assignment_digest.hex(),
                )
            )
    bases_tuple = tuple(sorted(bases, key=lambda base: base.query_id.encode()))
    variants_tuple = tuple(sorted(variants, key=lambda variant: variant.variant_id.encode()))
    manifest = hashlib.sha256()
    for pair in selected_pairs:
        manifest.update(
            encode_key((pair.origin_station_id, pair.destination_station_id, pair.stratum))
        )
    for base in bases_tuple:
        manifest.update(encode_key((base.query_id, base.chronological_split)))
    for variant in variants_tuple:
        manifest.update(
            encode_key(
                (
                    variant.variant_id,
                    variant.assigned_reliability_target,
                    str(variant.assigned_maximum_extra_time_minutes),
                )
            )
        )
    return QueryPopulation(selected_pairs, bases_tuple, variants_tuple, manifest.hexdigest())


def write_query_population(population: QueryPopulation, output: Path) -> None:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("query population output must be a fresh directory")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "base_query_count": len(population.base_queries),
        "deadline_variant_count": len(population.deadline_variants),
        "manifest_hash": population.manifest_hash,
        "selected_pair_count": len(population.selected_pairs),
        "selected_pairs": [asdict(pair) for pair in population.selected_pairs],
    }
    (output / "manifest.json").write_bytes(_json_bytes(manifest))
    (output / "base_queries.jsonl").write_bytes(
        b"".join(_json_bytes(asdict(query)) for query in population.base_queries)
    )
    (output / "deadline_variants.jsonl").write_bytes(
        b"".join(_json_bytes(asdict(variant)) for variant in population.deadline_variants)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    raw = json.loads(arguments.input.read_text(encoding="utf-8"))
    pairs = tuple(StationPair(**item) for item in raw["station_pairs"])
    dates = tuple(date.fromisoformat(value) for value in raw["service_dates"])
    schedule_versions = {
        date.fromisoformat(key): value for key, value in raw["schedule_version_by_date"].items()
    }
    splits = {date.fromisoformat(key): value for key, value in raw["split_by_date"].items()}
    population = generate_query_population(
        pairs,
        dates,
        schedule_version_by_date=schedule_versions,
        split_by_date=splits,
    )
    write_query_population(population, arguments.output)
    print(
        json.dumps(
            {
                "base_query_count": len(population.base_queries),
                "deadline_variant_count": len(population.deadline_variants),
                "manifest_hash": population.manifest_hash,
                "selected_pair_count": len(population.selected_pairs),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
