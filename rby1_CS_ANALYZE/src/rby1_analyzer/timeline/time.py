from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
from typing import Iterable, Mapping

_WALL = re.compile(r"(?P<stamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")
_RBY_WALL = re.compile(r"\[(?P<stamp>\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\]")
_RBY_RELATIVE = re.compile(r"\[(?P<stamp>\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]")
_RELATIVE = re.compile(r"(?:robot[_ -]?time|elapsed|relative)[=: ]+(?P<seconds>\d+(?:\.\d+)?)", re.I)
_REQUEST_EPOCH = re.compile(r"request_timestamp\s*\{\s*seconds:\s*(?P<seconds>\d+)(?:\s+nanos:\s*(?P<nanos>\d+))?", re.I)


@dataclass(frozen=True)
class TimeObservation:
    basis: str
    value: float | None
    raw: str
    source_sequence: int
    precision: str
    timezone_known: bool
    parse_status: str = "parsed"
    discontinuity_group: int = 0
    monotonic: bool = True


def _iso_value(raw: str) -> tuple[float | None, bool]:
    timezone_known = raw.endswith("Z") or bool(re.search(r"[+-]\d{2}:?\d{2}$", raw))
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp(), timezone_known
    except ValueError:
        return None, timezone_known


def parse_log_time(line: str, source_sequence: int) -> list[TimeObservation]:
    observations: list[TimeObservation] = []
    if match := _WALL.search(line):
        raw = match.group("stamp")
        value, tz_known = _iso_value(raw)
        precision = "microsecond" if "." in raw else "second"
        observations.append(TimeObservation("log_wall", value, raw, source_sequence, precision, tz_known, "parsed" if value is not None else "invalid"))
    elif match := _RBY_WALL.search(line):
        raw = match.group("stamp")
        try:
            parsed = datetime.strptime(raw, "%m/%d/%y %H:%M:%S.%f").replace(tzinfo=timezone.utc)
            value = parsed.timestamp()
            status = "parsed"
        except ValueError:
            value = None
            status = "invalid"
        observations.append(
            TimeObservation("log_wall", value, raw, source_sequence, "microsecond", False, status)
        )
    if match := _RBY_RELATIVE.search(line):
        raw = match.group("stamp")
        hours, minutes, seconds = raw.split(":")
        value = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        observations.append(
            TimeObservation("log_robot_relative", value, raw, source_sequence, "microsecond", False)
        )
    if match := _RELATIVE.search(line):
        raw = match.group("seconds")
        observations.append(TimeObservation("log_robot_relative", float(raw), raw, source_sequence, "decimal", False))
    if match := _REQUEST_EPOCH.search(line):
        seconds = float(match.group("seconds"))
        nanos = match.group("nanos")
        if nanos:
            seconds += int(nanos) / 1_000_000_000
        observations.append(
            TimeObservation("request_header_epoch", seconds, match.group(0), source_sequence, "nanosecond", True)
        )
    return observations


def parse_fault_time(row: Mapping[str, str], source_sequence: int) -> list[TimeObservation]:
    result: list[TimeObservation] = []
    for key, raw in row.items():
        lowered = key.lower()
        if not raw:
            continue
        if lowered in {"timestamp", "wall_time", "datetime"}:
            stripped = raw.strip()
            try:
                numeric = float(stripped)
            except ValueError:
                value, tz_known = _iso_value(stripped)
                result.append(TimeObservation("fault_wall", value, raw, source_sequence, "decimal" if "." in raw else "second", tz_known, "parsed" if value is not None else "invalid"))
            else:
                result.append(TimeObservation("fault_sample_relative", numeric, raw, source_sequence, "decimal", False))
        elif lowered in {"time", "sample_time", "elapsed", "seconds"}:
            try:
                result.append(TimeObservation("fault_sample_relative", float(raw), raw, source_sequence, "decimal", False))
            except ValueError:
                result.append(TimeObservation("fault_sample_relative", None, raw, source_sequence, "unknown", False, "invalid"))
    return result


def assign_discontinuity_groups(observations: Iterable[TimeObservation]) -> list[TimeObservation]:
                                                                                       
    groups: dict[str, int] = {}
    previous: dict[str, float] = {}
    assigned: list[TimeObservation] = []
    for observation in sorted(observations, key=lambda item: item.source_sequence):
        group = groups.setdefault(observation.basis, 0)
        monotonic = True
        if observation.value is not None and observation.basis in previous and observation.value < previous[observation.basis]:
            group += 1
            groups[observation.basis] = group
            monotonic = False
        if observation.value is not None:
            previous[observation.basis] = observation.value
        assigned.append(replace(observation, discontinuity_group=group, monotonic=monotonic))
    return assigned
