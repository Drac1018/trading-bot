from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_operator_bridge_docs.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_operator_bridge_docs", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_operator_doc_with_monitored_keys_requires_bridge(tmp_path) -> None:
    module = load_module()
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    target = docs_root / "release-sample-checklist.md"
    target.write_text(
        "# Sample\n\n- `decision_cycle_interval_minutes`\n",
        encoding="utf-8",
    )

    violations = module.collect_bridge_violations(docs_root)

    assert violations == [(target, ["decision_cycle_interval_minutes"])]


def test_operator_doc_with_bridge_section_passes(tmp_path) -> None:
    module = load_module()
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    target = docs_root / "release-sample-checklist.md"
    target.write_text(
        "# Sample\n\n## 운영자 표현과 내부 키\n\n- `decision_cycle_interval_minutes`\n",
        encoding="utf-8",
    )

    violations = module.collect_bridge_violations(docs_root)

    assert violations == []


def test_non_operator_doc_is_not_forced_even_with_keys(tmp_path) -> None:
    module = load_module()
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    target = docs_root / "api.md"
    target.write_text(
        "# API\n\n- `decision_cycle_interval_minutes`\n",
        encoding="utf-8",
    )

    violations = module.collect_bridge_violations(docs_root)

    assert violations == []
