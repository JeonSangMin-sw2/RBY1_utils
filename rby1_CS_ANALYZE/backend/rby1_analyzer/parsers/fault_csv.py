from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import IO, Callable, Iterator

from rby1_analyzer.timeline.time import TimeObservation, parse_fault_time


@dataclass(frozen=True)
class FaultSample:
    source: str
    row: int
    values: dict[str, float | str | None]
    time_observations: tuple[TimeObservation, ...]


def _value(value: str) -> float | str | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return stripped


def parse_fault_csv(
    stream: IO[str],
    source: str,
    *,
    cancel_check: Callable[[], None] | None = None,
    cancel_interval: int = 10 * 1024 * 1024,
    progress_callback: Callable[[int], None] | None = None,
    progress_interval: int = 1024 * 1024,
) -> Iterator[FaultSample]:
                                                                      
    bytes_since_check = 0
    offset = 0
    next_progress = progress_interval

    def lines() -> Iterator[str]:
        nonlocal bytes_since_check, next_progress, offset
        for line in stream:
            line_size = len(line.encode("utf-8", errors="replace"))
            bytes_since_check += line_size
            offset += line_size
            if cancel_check is not None and bytes_since_check >= cancel_interval:
                cancel_check()
                bytes_since_check = 0
            if progress_callback is not None and offset >= next_progress:
                progress_callback(offset)
                next_progress = offset + progress_interval
            if not line.lstrip().startswith("#"):
                yield line

    reader = csv.DictReader(lines())
    for row_number, row in enumerate(reader, 2):
        clean = {str(key): _value(value or "") for key, value in row.items() if key is not None}
        yield FaultSample(source, row_number, clean, tuple(parse_fault_time(row, row_number)))
    if progress_callback is not None:
        progress_callback(offset)
