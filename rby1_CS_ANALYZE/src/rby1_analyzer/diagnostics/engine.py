from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from rby1_analyzer.parsers.rpc import ParsedEvent


@dataclass(frozen=True)
class DiagnosticRule:
    id: str
    version: int
    pattern: str
    observed: str
    possible_causes: tuple[str, ...]
    checks: tuple[str, ...]
    confidence_base: str = "low"
    required_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Diagnostic:
    rule_id: str
    rule_version: int
    observed: tuple[str, ...]
    possible_causes: tuple[str, ...]
    confidence: str
    checks: tuple[str, ...]
    evidence: tuple[str, ...]


def evaluate_event(event: ParsedEvent, rules: Iterable[DiagnosticRule]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    haystack = event.raw_excerpt
    for rule in rules:
        if not re.search(rule.pattern, haystack, re.I):
            continue
        evidence = tuple(field for field in rule.required_evidence if getattr(event, field, None) not in (None, ""))
        confidence = rule.confidence_base
        if confidence == "high" and len(evidence) != len(rule.required_evidence):
            confidence = "medium" if evidence else "low"
        causes = rule.possible_causes
        if confidence == "low":
            causes = tuple(re.sub(r"\b(?:caused by|causes|because of)\b", "may be consistent with", cause, flags=re.I) for cause in causes)
        diagnostics.append(Diagnostic(rule.id, rule.version, (rule.observed.format(excerpt=event.raw_excerpt),), causes, confidence, rule.checks, (f"{event.source}:{event.line}:{event.raw_digest}",)))
    return diagnostics
