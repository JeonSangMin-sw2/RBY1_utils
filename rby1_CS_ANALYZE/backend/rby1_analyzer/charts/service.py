from __future__ import annotations

from dataclasses import dataclass
from math import isnan
from typing import Iterable, Literal, Sequence


SeriesKind = Literal["continuous", "discrete"]


@dataclass(frozen=True, slots=True)
class ChartPoint:
    time: float
    value: float


@dataclass(frozen=True, slots=True)
class ChartSeries:
    name: str
    kind: SeriesKind
    points: tuple[ChartPoint, ...]
    nan_count: int = 0


class DenseWindowError(ValueError):
    def __init__(self, required_points: int, suggested_window_seconds: float) -> None:
        super().__init__("required discrete transitions exceed the response limit")
        self.required_points = required_points
        self.suggested_window_seconds = suggested_window_seconds


def _in_window(points: Iterable[ChartPoint], start: float, end: float) -> list[ChartPoint]:
    return [point for point in points if start <= point.time <= end]


def _discrete_required(points: Sequence[ChartPoint]) -> list[ChartPoint]:
    if len(points) < 2:
        return list(points)
    indexes = {0, len(points) - 1}
    for index in range(1, len(points)):
        if points[index].value != points[index - 1].value:
            indexes.update((index - 1, index))
            if index + 1 < len(points):
                indexes.add(index + 1)
    return [points[index] for index in sorted(indexes)]


def _continuous_bucket(points: Sequence[ChartPoint], limit: int) -> list[ChartPoint]:
    clean = [point for point in points if not isnan(point.value)]
    if len(clean) <= limit:
        return clean
    bucket_count = max(1, limit // 4)
    selected: set[int] = {0, len(clean) - 1}
    for bucket in range(bucket_count):
        lo = bucket * len(clean) // bucket_count
        hi = max(lo + 1, (bucket + 1) * len(clean) // bucket_count)
        indexes = range(lo, min(hi, len(clean)))
        selected.add(min(indexes, key=lambda i: clean[i].value))
        selected.add(max(indexes, key=lambda i: clean[i].value))
        selected.add(lo)
        selected.add(min(hi - 1, len(clean) - 1))
    ordered = sorted(selected)
    if len(ordered) > limit:
        stride = (len(ordered) - 1) / (limit - 1)
        ordered = sorted({ordered[round(index * stride)] for index in range(limit)})
    return [clean[index] for index in ordered]


def window_series(
    series: Iterable[ChartSeries],
    *,
    start: float,
    end: float,
    selected: set[str] | None = None,
    max_points: int = 2_000,
) -> list[ChartSeries]:
                                                                                                                                                                                                                                                                               
    if end <= start:
        raise ValueError("end must be greater than start")
    if max_points < 4:
        raise ValueError("max_points must be at least 4")
    result: list[ChartSeries] = []
    for item in series:
        if selected is not None and item.name not in selected:
            continue
        points = _in_window(item.points, start, end)
        nan_count = sum(isnan(point.value) for point in points)
        if item.kind == "discrete":
            retained = _discrete_required(points)
            if len(retained) > max_points:
                ratio = max_points / len(retained)
                raise DenseWindowError(len(retained), max((end - start) * ratio, 0.001))
        else:
            retained = _continuous_bucket(points, max_points)
        result.append(ChartSeries(item.name, item.kind, tuple(retained), nan_count))
    return result
