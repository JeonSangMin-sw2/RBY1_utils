from pathlib import Path
import pytest
from rby1_analyzer.incidents.rules import load_compiled_rules

def test_load_compiled_rules():
    categories, compiled_rules = load_compiled_rules()
    assert len(categories) >= 6
    assert len(compiled_rules) >= 25
    
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
