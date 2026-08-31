from __future__ import annotations

import json
from pathlib import Path
import sys
from importlib import resources
from typing import Any
import yaml

from .engine import DiagnosticRule


def _load_config_payload() -> dict[str, Any]:
    candidates = [
        Path.cwd() / "config" / "error_guide.yaml",
    ]
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidates.append(parent / "config" / "error_guide.yaml")

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "config" / "error_guide.yaml")
    if sys.executable:
        candidates.append(Path(sys.executable).parent / "config" / "error_guide.yaml")

    for path in candidates:
        if path.is_file():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if "rules" in data:
                    return data
            except Exception:
                pass

    # Fallback to internal core.yaml if error_guide.yaml is not found
    try:
        rule_path = resources.files("rby1_analyzer.diagnostics").joinpath("rules/core.yaml")
        return json.loads(rule_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_rules() -> tuple[DiagnosticRule, ...]:
    payload = _load_config_payload()
    rules: list[DiagnosticRule] = []
    
    # Format 1: error_guide.yaml format
    if "rules" in payload and payload.get("version") is not None:
        for item in payload.get("rules", []):
            meaning = str(item.get("meaning", item.get("title", "오류 발생")))
            observed = f"{meaning}: {{excerpt}}" if "{excerpt}" not in meaning else meaning
            rules.append(
                DiagnosticRule(
                    id=str(item.get("id", "unknown_error")),
                    version=int(item.get("version", 1)),
                    pattern=str(item.get("pattern", ".*")),
                    observed=observed,
                    possible_causes=tuple(str(x) for x in item.get("causes", [])),
                    checks=tuple(str(x) for x in item.get("checks", [])),
                    confidence_base=str(item.get("confidence", "low")),
                    required_evidence=(),
                )
            )
        return tuple(rules)

    # Format 2: Legacy core.yaml format
    for item in payload.get("rules", []):
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
