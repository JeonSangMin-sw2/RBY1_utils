from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from rby1_analyzer.parsers.rpc import ParsedEvent


@dataclass(frozen=True)
class Correlation:
    request_line: int
    result_line: int
    basis: str
    delta: float | None
    confidence: str
    explanation: str
    causal: bool = False


def correlate_commands(events: Iterable[ParsedEvent]) -> list[Correlation]:
    pending: dict[tuple[str | None, str], ParsedEvent] = {}
    correlations: list[Correlation] = []
    for event in events:
        if event.command and not event.result:
            pending[(event.component, event.command)] = event
            continue
        if not event.result:
            continue
        candidates = [item for key, item in pending.items() if key[0] == event.component and (not event.command or key[1] == event.command)]
        if not candidates:
            continue
        request = candidates[-1]
        pending.pop((request.component, request.command or ""), None)
        explicit = bool(event.command and event.command == request.command)
        request_time = request.time_observations[0] if request.time_observations else None
        result_time = event.time_observations[0] if event.time_observations else None
        compatible = bool(request_time and result_time and request_time.basis == result_time.basis)
        delta = result_time.value - request_time.value if compatible and request_time.value is not None and result_time.value is not None else None
        correlations.append(Correlation(
            request.line, event.line, request_time.basis if compatible and request_time else "source_sequence", delta,
            "high" if explicit else "low",
            "명령과 결과가 명시적으로 일치하는 근거입니다."
            if explicit
            else "시간 및 기록 순서에 따른 연관 정보이며, 인과관계를 의미하지는 않습니다.",
        ))
    return correlations
