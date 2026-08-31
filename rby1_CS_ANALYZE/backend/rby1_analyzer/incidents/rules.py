from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any, Mapping
import yaml


JOINT_NAME = re.compile(
    r"\b(?:right|left)_arm_[0-6]\b|\bhead_[0-2]\b|\btorso_[0-5]\b|"
    r"\b(?:right|left)_wheel\b",
    re.IGNORECASE,
)
POWER_RAIL = re.compile(r"(?<!\d)(5v|12v|24v|48v)(?!\d)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class IncidentRuleMatch:
    rule_id: str
    family: str
    title: str
    meaning: str
    severity: str
    role: str
    confidence: str
    confidence_reason: str
    causes: tuple[str, ...]
    checks: tuple[str, ...]
    remedies: tuple[str, ...]
    evidence_gaps: tuple[str, ...]
    group_window: float = 1.0
    specificity: int = 50
    major_category: str = "service_api"
    sub_category: str = "generic_error"


@dataclass(frozen=True, slots=True)
class CompiledRule:
    rule_id: str
    major_category: str
    sub_category: str
    family: str
    title: str
    compiled_pattern: re.Pattern
    meaning: str
    severity: str
    role: str
    confidence: str
    confidence_reason: str
    causes: tuple[str, ...]
    checks: tuple[str, ...]
    remedies: tuple[str, ...]
    evidence_gaps: tuple[str, ...]
    group_window: float
    specificity: int


def extract_entities(excerpt: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    joints = tuple(dict.fromkeys(match.group(0).lower() for match in JOINT_NAME.finditer(excerpt)))
    rails = tuple(dict.fromkeys(match.group(1).lower() for match in POWER_RAIL.finditer(excerpt)))
    return joints, rails


def _match(
    *,
    rule_id: str,
    family: str,
    title: str,
    meaning: str,
    severity: str = "error",
    role: str = "root",
    confidence: str = "medium",
    confidence_reason: str = "로그의 명시적 오류 문구와 알려진 장애 패턴이 일치합니다.",
    causes: tuple[str, ...] = (),
    checks: tuple[str, ...] = (),
    remedies: tuple[str, ...] = (),
    evidence_gaps: tuple[str, ...] = (),
    group_window: float = 1.0,
    specificity: int = 50,
    major_category: str = "service_api",
    sub_category: str = "generic_error",
) -> IncidentRuleMatch:
    return IncidentRuleMatch(
        rule_id=rule_id,
        family=family,
        title=title,
        meaning=meaning,
        severity=severity,
        role=role,
        confidence=confidence,
        confidence_reason=confidence_reason,
        causes=causes,
        checks=checks,
        remedies=remedies,
        evidence_gaps=evidence_gaps,
        group_window=group_window,
        specificity=specificity,
        major_category=major_category,
        sub_category=sub_category,
    )


def _load_yaml_config() -> dict[str, Any]:
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
                return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                pass
    return {}


def load_compiled_rules() -> tuple[list[dict[str, Any]], list[CompiledRule]]:
    payload = _load_yaml_config()
    categories = payload.get("categories", [])
    raw_rules = payload.get("rules", [])
    compiled: list[CompiledRule] = []
    for item in raw_rules:
        pattern_str = str(item.get("pattern", ".*"))
        compiled.append(
            CompiledRule(
                rule_id=str(item.get("id", "unknown_error")),
                major_category=str(item.get("major_category", "service_api")),
                sub_category=str(item.get("sub_category", "generic_error")),
                family=str(item.get("family", "unknown_error")),
                title=str(item.get("title", "오류")),
                compiled_pattern=re.compile(pattern_str, re.IGNORECASE),
                meaning=str(item.get("meaning", "")),
                severity=str(item.get("severity", "error")),
                role=str(item.get("role", "root")),
                confidence=str(item.get("confidence", "medium")),
                confidence_reason=str(
                    item.get("confidence_reason", "로그의 명시적 오류 문구와 알려진 장애 패턴이 일치합니다.")
                ),
                causes=tuple(str(x) for x in item.get("causes", [])),
                checks=tuple(str(x) for x in item.get("checks", [])),
                remedies=tuple(str(x) for x in item.get("remedies", [])),
                evidence_gaps=tuple(str(x) for x in item.get("evidence_gaps", [])),
                group_window=float(item.get("group_window", 1.0)),
                specificity=int(item.get("specificity", 50)),
            )
        )
    return categories, compiled


@dataclass(frozen=True, slots=True)
class CompiledCommand:
    id: str
    category: str
    name_ko: str
    compiled_pattern: re.Pattern
    description: str
    normal_condition: str
    abnormal_condition: str
    action_hint: str


def load_command_dictionary() -> list[CompiledCommand]:
    candidates = [
        Path.cwd() / "config" / "command_dictionary.yaml",
    ]
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidates.append(parent / "config" / "command_dictionary.yaml")

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "config" / "command_dictionary.yaml")
    if sys.executable:
        candidates.append(Path(sys.executable).parent / "config" / "command_dictionary.yaml")

    for path in candidates:
        if path.is_file():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                raw_cmds = data.get("commands", [])
                compiled: list[CompiledCommand] = []
                for c in raw_cmds:
                    pattern_str = str(c.get("pattern", ".*"))
                    compiled.append(
                        CompiledCommand(
                            id=str(c.get("id", "")),
                            category=str(c.get("category", "")),
                            name_ko=str(c.get("name_ko", "")),
                            compiled_pattern=re.compile(pattern_str, re.IGNORECASE),
                            description=str(c.get("description", "")),
                            normal_condition=str(c.get("normal_condition", "")),
                            abnormal_condition=str(c.get("abnormal_condition", "")),
                            action_hint=str(c.get("action_hint", "")),
                        )
                    )
                return compiled
            except Exception:
                pass
    return []


CATEGORIES, COMPILED_RULES = load_compiled_rules()
COMPILED_COMMANDS = load_command_dictionary()


def get_error_guide_categories() -> list[dict[str, Any]]:
    return CATEGORIES


def match_command_info(excerpt: str) -> dict[str, str] | None:
    for cmd in COMPILED_COMMANDS:
        if cmd.compiled_pattern.search(excerpt):
            return {
                "id": cmd.id,
                "category": cmd.category,
                "name_ko": cmd.name_ko,
                "description": cmd.description,
                "normal_condition": cmd.normal_condition,
                "abnormal_condition": cmd.abnormal_condition,
                "action_hint": cmd.action_hint,
            }
    return None


def classify_event(event: Mapping[str, object]) -> IncidentRuleMatch | None:
    excerpt = str(event.get("excerpt") or "")
    lowered = excerpt.lower()
    severity = str(event.get("severity") or "info").lower()
    category = str(event.get("category") or "unknown").lower()
    result = str(event.get("result") or "").lower()
    joints, _rails = extract_entities(excerpt)

    # Exclusions
    if "servo_on_command.timeout" in lowered and "requested:" in lowered:
        return None
    if "requested: request_header" in lowered and re.search(r"timeout\s*\{\s*\}", lowered):
        return None
    if "unlimited mode enabled, skipping limit check" in lowered:
        return None
    if "control was preempted or canceled by another request" in lowered:
        return None
    if "control was preempted (or cancelled) by another control" in lowered:
        return None
    if "loop stat" in lowered or re.search(r"\bcontrol manager stat(?:\s*\(|:)", lowered):
        return None
    if "robot states have been saved:" in lowered:
        return None
    if lowered.rstrip().endswith("control canceled") and severity in {"info", "debug"}:
        return None
    if "control ended with an unknown reason" in lowered:
        return None

    # Handle Category override shortcuts
    if category == "majorfault" and "control manager state changed" not in lowered:
        rule = next((r for r in COMPILED_RULES if r.rule_id == "major_fault_state"), None)
        if rule:
            return _match(
                rule_id=rule.rule_id,
                family=rule.family,
                title=rule.title,
                meaning=rule.meaning,
                severity=rule.severity,
                role=rule.role,
                confidence=rule.confidence,
                confidence_reason=rule.confidence_reason,
                causes=rule.causes,
                checks=rule.checks,
                remedies=rule.remedies,
                evidence_gaps=rule.evidence_gaps,
                group_window=rule.group_window,
                specificity=rule.specificity,
                major_category=rule.major_category,
                sub_category=rule.sub_category,
            )

    if category == "minorfault" and "control manager state changed" not in lowered:
        rule = next((r for r in COMPILED_RULES if r.rule_id == "minor_fault_state"), None)
        if rule:
            return _match(
                rule_id=rule.rule_id,
                family=rule.family,
                title=rule.title,
                meaning=rule.meaning,
                severity=rule.severity,
                role=rule.role,
                confidence=rule.confidence,
                confidence_reason=rule.confidence_reason,
                causes=rule.causes,
                checks=rule.checks,
                remedies=rule.remedies,
                evidence_gaps=rule.evidence_gaps,
                group_window=rule.group_window,
                specificity=rule.specificity,
                major_category=rule.major_category,
                sub_category=rule.sub_category,
            )

    # Special handling: Arm 6 specific check vs generic motor initialization
    no_data = "no data received" in lowered or "no data coming" in lowered or "no data comming" in lowered
    motor_init = "initialize motor" in lowered or "initializing motor" in lowered
    if no_data or (motor_init and any(word in lowered for word in ("failed", "timeout", "error"))):
        arm6 = {"right_arm_6", "left_arm_6"}
        if joints and set(joints).issubset(arm6):
            rule = next((r for r in COMPILED_RULES if r.rule_id == "isolated_arm6_servo_on_failure"), None)
            if rule:
                return _match(
                    rule_id=rule.rule_id,
                    family=rule.family,
                    title=rule.title,
                    meaning=rule.meaning,
                    severity=rule.severity,
                    role=rule.role,
                    confidence=rule.confidence,
                    confidence_reason=rule.confidence_reason,
                    causes=rule.causes,
                    checks=rule.checks,
                    remedies=rule.remedies,
                    evidence_gaps=rule.evidence_gaps,
                    group_window=rule.group_window,
                    specificity=rule.specificity,
                    major_category=rule.major_category,
                    sub_category=rule.sub_category,
                )
        rule = next((r for r in COMPILED_RULES if r.rule_id == "motor_communication_no_data"), None)
        if rule:
            return _match(
                rule_id=rule.rule_id,
                family=rule.family,
                title=rule.title,
                meaning=rule.meaning,
                severity=rule.severity,
                role=rule.role,
                confidence=rule.confidence,
                confidence_reason=rule.confidence_reason,
                causes=rule.causes,
                checks=rule.checks,
                remedies=rule.remedies,
                evidence_gaps=rule.evidence_gaps,
                group_window=rule.group_window,
                specificity=rule.specificity,
                major_category=rule.major_category,
                sub_category=rule.sub_category,
            )

    # Evaluate compiled rules in sequence (excluding unknown_error fallback which is handled last)
    for rule in COMPILED_RULES:
        if rule.rule_id in {"unknown_error", "isolated_arm6_servo_on_failure", "motor_communication_no_data"}:
            continue
        if rule.compiled_pattern.search(lowered):
            # Dynamic severity check for state timeout
            rule_severity = rule.severity
            if rule.rule_id == "joint_state_update_timeout" and "majorfault" in lowered:
                rule_severity = "critical"
            return _match(
                rule_id=rule.rule_id,
                family=rule.family,
                title=rule.title,
                meaning=rule.meaning,
                severity=rule_severity,
                role=rule.role,
                confidence=rule.confidence,
                confidence_reason=rule.confidence_reason,
                causes=rule.causes,
                checks=rule.checks,
                remedies=rule.remedies,
                evidence_gaps=rule.evidence_gaps,
                group_window=rule.group_window,
                specificity=rule.specificity,
                major_category=rule.major_category,
                sub_category=rule.sub_category,
            )

    # Fallback to generic unknown_error for error/critical severity
    if severity in {"error", "critical"} or category in {"failure", "timeout"} or result in {
        "failed",
        "failure",
        "timeout",
    }:
        rule = next((r for r in COMPILED_RULES if r.rule_id == "unknown_error"), None)
        derived_severity = "critical" if severity == "critical" else "error"
        if rule:
            return _match(
                rule_id=rule.rule_id,
                family=rule.family,
                title=rule.title,
                meaning=rule.meaning,
                severity=derived_severity,
                role=rule.role,
                confidence=rule.confidence,
                confidence_reason=rule.confidence_reason,
                causes=rule.causes,
                checks=rule.checks,
                remedies=rule.remedies,
                evidence_gaps=rule.evidence_gaps,
                group_window=rule.group_window,
                specificity=rule.specificity,
                major_category=rule.major_category,
                sub_category=rule.sub_category,
            )

    return None
