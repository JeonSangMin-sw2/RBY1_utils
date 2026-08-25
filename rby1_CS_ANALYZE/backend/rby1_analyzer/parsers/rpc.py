from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import BinaryIO, Callable, Iterator

from rby1_analyzer.timeline.time import TimeObservation, parse_log_time

_LEVEL = re.compile(rb"\[(trace|debug|info|warning|warn|error|critical)\]", re.I)
_COMPONENT = re.compile(
    rb"\[(?:trace|debug|info|warning|warn|error|critical)\]\s*"
    rb"(?:\[\d{2}:\d{2}:\d{2}(?:\.\d+)?\]\s*)?"
    rb"\[(?!(?:\d{2}:){2}\d{2}(?:\.\d+)?\])([^\]]+)\]",
    re.I,
)
_COMMAND = re.compile(rb"\b(?:command|request|rpc)\b\s*[:=]?\s*([A-Za-z][\w./-]*)", re.I)
_SERVICE_COMMAND_REQUEST = re.compile(
    rb"\[Service::[^\]]*Command[^\]]*\]\s*Requested\s*:",
    re.I,
)
_COMMAND_ENUM = re.compile(rb"\b(COMMAND_[A-Z0-9_]+)\b")
_OPERATION_STEP = re.compile(
    rb"\b(?:engaging|disengaging) brakes\b|\bbrakes (?:engaged|disengaged)\b|"
    rb"\bswitched to gravity compensation mode\b",
    re.I,
)
_RESULT = re.compile(rb"\b(success|succeeded|completed|failed|failure|timeout|cancelled|canceled)\b", re.I)
_QUOTED_VALUE = re.compile(rb'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_TIMEOUT_ARGUMENT = re.compile(
    rb"\btimeout\s*\{\s*(?:(?:seconds|nanos)\s*:\s*-?\d+\s*)*\}",
    re.I,
)
_JOINT_NUMBER = re.compile(rb"\b(?:joint|j)\s*[:=#]?\s*(\d{1,3})\b", re.I)
_JOINT_NAME = re.compile(
    rb"\b(?:joint(?:\(s\))?|for)\s*['\"]?((?:right|left)_arm_[0-6]|head_[0-2]|torso_[0-5]|(?:right|left)_wheel)['\"]?",
    re.I,
)
_FAULT = re.compile(rb"\b(MinorFault|MajorFault)\b", re.I)


@dataclass(frozen=True)
class ParsedEvent:
    source: str
    line: int
    byte_offset: int
    raw_digest: str
    raw_excerpt: str
    severity: str
    category: str
    signature: str
    component: str | None = None
    joint: int | str | None = None
    command: str | None = None
    result: str | None = None
    time_observations: tuple[TimeObservation, ...] = field(default_factory=tuple)


def _category(raw: bytes, result: str | None) -> str:
    fault = _FAULT.search(raw)
    if fault:
        return fault.group(1).decode("ascii").lower()
    if result == "timeout":
        return "timeout"
    if result in {"failed", "failure", "cancelled", "canceled"}:
        return "failure"
    if result in {"success", "succeeded", "completed"}:
        return "success"
    if re.search(rb"\bstate (?:changed|transitioned)\b", raw, re.I):
        return "state_transition"
    if _COMMAND.search(raw) or _SERVICE_COMMAND_REQUEST.search(raw):
        return "command"
    if _OPERATION_STEP.search(raw):
        return "operation"
    return "unknown"


def _result(raw: bytes) -> str | None:
                                                                                     
    sanitized = _QUOTED_VALUE.sub(b"", raw)
                                                                                        
    sanitized = _TIMEOUT_ARGUMENT.sub(b"", sanitized)
    match = _RESULT.search(sanitized)
    return match.group(1).decode("ascii").lower() if match else None


def parse_rpc_log(
    stream: BinaryIO,
    source: str,
    *,
    max_line_bytes: int = 4 * 1024 * 1024,
    cancel_check: Callable[[], None] | None = None,
    cancel_interval: int = 10 * 1024 * 1024,
    progress_callback: Callable[[int], None] | None = None,
    progress_interval: int = 1024 * 1024,
    event_filter: Callable[[bytes], bool] | None = None,
) -> Iterator[ParsedEvent]:
                                                                                                                                                                                                               
    offset = 0
    next_cancel_check = cancel_interval
    next_progress = progress_interval
    line_number = 0
    read_chunk_bytes = 64 * 1024
    while True:
        line_offset = offset
        retained = bytearray()
        digest = hashlib.sha256()
        consumed = False
        while True:
            chunk = stream.readline(read_chunk_bytes)
            if not chunk:
                break
            consumed = True
            digest.update(chunk)
            if len(retained) < max_line_bytes:
                retained.extend(chunk[: max_line_bytes - len(retained)])
            offset += len(chunk)
            if cancel_check is not None and offset >= next_cancel_check:
                cancel_check()
                next_cancel_check = offset + cancel_interval
            if progress_callback is not None and offset >= next_progress:
                progress_callback(offset)
                next_progress = offset + progress_interval
            if chunk.endswith(b"\n"):
                break
        if not consumed:
            break
        line_number += 1
        raw = bytes(retained)
        if event_filter is not None and not event_filter(raw):
            continue
        level = _LEVEL.search(raw)
        component = _COMPONENT.search(raw)
        command = _COMMAND.search(raw) or _COMMAND_ENUM.search(raw)
        joint_number = _JOINT_NUMBER.search(raw)
        joint_name = _JOINT_NAME.search(raw)
        result = _result(raw)
        severity = level.group(1).decode("ascii").lower() if level else "info"
        if severity == "warn":
            severity = "warning"
        excerpt = raw.rstrip(b"\r\n").decode("utf-8", errors="replace")[:4096]
        category = _category(raw, result)
        signature_seed = re.sub(r"\b\d+(?:\.\d+)?\b", "#", excerpt.lower())[:512]
        yield ParsedEvent(
            source=source,
            line=line_number,
            byte_offset=line_offset,
            raw_digest=digest.hexdigest(),
            raw_excerpt=excerpt,
            severity=severity,
            category=category,
            signature=hashlib.sha256(signature_seed.encode()).hexdigest()[:24],
            component=component.group(1).decode("utf-8", "replace").strip() if component else None,
            joint=(
                int(joint_number.group(1))
                if joint_number
                else joint_name.group(1).decode("ascii").lower()
                if joint_name
                else None
            ),
            command=command.group(1).decode("utf-8", "replace") if command else None,
            result=result,
            time_observations=tuple(parse_log_time(excerpt, line_number)),
        )
    if progress_callback is not None:
        progress_callback(offset)
