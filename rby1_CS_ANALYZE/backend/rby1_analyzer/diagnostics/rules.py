from __future__ import annotations

import json
from importlib import resources

from .engine import DiagnosticRule


def load_rules() -> tuple[DiagnosticRule, ...]:
    rule_path = resources.files("rby1_analyzer.diagnostics").joinpath("rules/core.yaml")
    payload = json.loads(rule_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported diagnostic rule schema")
    rules: list[DiagnosticRule] = []
    for item in payload["rules"]:
        rules.append(
            DiagnosticRule(
                id=str(item["id"]),
                version=int(item["version"]),
                pattern=str(item["pattern"]),
                observed=str(item["observed"]),
                possible_causes=tuple(map(str, item["possible_causes"])),
                checks=tuple(map(str, item["checks"])),
                confidence_base=str(item.get("confidence_base", "low")),
                required_evidence=tuple(map(str, item.get("required_evidence", []))),
            )
        )
    return tuple(rules)


DEFAULT_RULES = load_rules()
