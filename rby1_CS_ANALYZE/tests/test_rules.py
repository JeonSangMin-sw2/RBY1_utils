from pathlib import Path
import pytest
from rby1_analyzer.incidents.rules import (
    load_compiled_rules,
    extract_flags_from_text,
    match_combinations,
)


def test_load_compiled_rules():
    categories, compiled_rules, compiled_flags, compiled_combinations = load_compiled_rules()
    assert len(categories) >= 6
    assert len(compiled_rules) >= 25
    assert len(compiled_flags) >= 10
    assert len(compiled_combinations) >= 5

    # Check that all rules have required fields
    for rule in compiled_rules:
        assert rule.rule_id
        assert rule.title
        assert rule.compiled_pattern is not None
        assert rule.severity in ("critical", "error", "warning", "info")
        assert rule.role in ("root", "reaction", "status", "warning")
        assert rule.confidence in ("high", "medium", "low")
        assert isinstance(rule.causes, tuple)
        assert isinstance(rule.checks, tuple)
        assert isinstance(rule.remedies, tuple)
        assert isinstance(rule.evidence_gaps, tuple)

    # Check flags
    for flag in compiled_flags:
        assert flag.flag_id
        assert flag.label
        assert flag.icon
        assert flag.compiled_pattern is not None
        assert flag.cause
        assert flag.check
        assert flag.remedy

    # Check combinations
    for combo in compiled_combinations:
        assert combo.id
        assert len(combo.flags) >= 2
        assert combo.title
        assert len(combo.causes) > 0
        assert len(combo.checks) > 0
        assert len(combo.remedies) > 0


def test_flag_extraction_and_combinations():
    # Test text containing both timeout and major fault
    log_text = "FSM state changed to MajorFault due to joint state update timeout"
    flags = extract_flags_from_text(log_text, category="MajorFault")
    assert "is_major_fault" in flags
    assert "is_timeout" in flags

    # Test combination matching
    matched_combos = match_combinations(flags)
    assert len(matched_combos) >= 1
    assert matched_combos[0].id == "combo_major_fault_timeout"
    assert "is_major_fault" in matched_combos[0].flags
    assert "is_timeout" in matched_combos[0].flags


def test_cmd_failure_flow_role_classification():
    import re
    # Debug log with Result: Failed -> should be cmd_failed
    debug_log = "[07/28/26 11:33:01.244041] [debug] [00:33:27.374206] [Service::PowerService::PowerCommand] Result: Failed"
    lowered = debug_log.lower()
    has_error_tag = bool(re.search(r"\[(error|critical|fatal)\]", lowered))
    has_debug_or_info_tag = bool(re.search(r"\[(debug|info|trace)\]", lowered))
    is_debug_cmd_failure = (
        (has_debug_or_info_tag)
        and not has_error_tag
        and "result: failed" in lowered
    )
    assert is_debug_cmd_failure is True

    # Error log with Result: Failed -> should NOT be cmd_failed (it's real error)
    error_log = "[07/28/26 11:33:01.244041] [error] [00:33:27.374206] [Hardware::ExecutePowerCommand] Result: Failed"
    lowered_err = error_log.lower()
    has_error_tag_err = bool(re.search(r"\[(error|critical|fatal)\]", lowered_err))
    is_debug_cmd_failure_err = (
        not has_error_tag_err
        and "result: failed" in lowered_err
    )
    assert is_debug_cmd_failure_err is False
