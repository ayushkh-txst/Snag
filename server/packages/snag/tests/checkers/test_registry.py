"""Menu completeness (CHECK-01): the registry holds exactly the 31 named
checker types from §4 plus `none`, and no checker module reaches for an
LLM, a network call, or `eval`/`exec`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from snag.checkers import CHECKERS, run_checker
from snag.checkers.transcript import Transcript

EXPECTED_NAMED_TYPES = {
    # Content
    "forbidden_text",
    "forbidden_pattern",
    "required_text",
    "required_pattern",
    "no_prompt_leak",
    "no_secret_leak",
    "no_pii_leak",
    "no_url",
    "language",
    "length_bounds",
    # Format
    "json_parseable",
    "json_schema",
    "required_fields",
    "enum_value",
    "markup_format",
    # Tools
    "tool_not_called",
    "tool_must_be_called",
    "tool_arg_limit",
    "tool_arg_pattern",
    "tool_arg_enum",
    "tool_arg_not_injected",
    "tool_requires_confirmation",
    "tool_call_order",
    "tool_call_count_max",
    "correct_tool_selected",
    # Flow and behaviour
    "ordering",
    "must_ask_first",
    "refusal_expected",
    "no_action_on_refusal",
    "instruction_isolation",
    "no_role_confusion",
}


def test_registry_holds_all_31_named_types_plus_none() -> None:
    assert len(EXPECTED_NAMED_TYPES) == 31
    assert set(CHECKERS) == EXPECTED_NAMED_TYPES | {"none"}


def test_none_checker_never_raises_and_is_not_testable() -> None:
    result = run_checker("none", Transcript(turns=[]), {})
    assert result.passed is False
    assert "not testable" in result.output


def test_no_llm_import_or_eval_exec_in_checkers_package() -> None:
    checkers_dir = Path(__file__).resolve().parents[2] / "src" / "snag" / "checkers"
    assert checkers_dir.is_dir()
    py_files = list(checkers_dir.glob("*.py"))
    assert py_files
    for py_file in py_files:
        source = py_file.read_text()
        assert "substrate.llm" not in source, f"{py_file} imports substrate.llm"
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec"}, f"{py_file} calls {node.func.id}"
