from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


BRIDGE_SECTION_PATTERN = re.compile(r"^##\s+운영자 표현과 내부 키\s*$", re.MULTILINE)
MONITORED_KEYS = (
    "decision_cycle_interval_minutes",
    "ai_call_interval_minutes",
    "exchange_sync_interval_seconds",
    "market_refresh_interval_minutes",
    "event_source_provider",
    "event_source_bls_enrichment_url",
    "event_source_bea_enrichment_url",
    "strategy_engine",
    "trigger_type",
    "holding_profile",
    "entry_mode",
)
FILENAME_HINTS = (
    "ops",
    "runbook",
    "checklist",
    "incident",
    "guide",
)
ALWAYS_MONITORED_FILES = {
    "architecture.md",
    "execution-flow.md",
    "strategy-engine-rule-surface.md",
}
EXCLUDED_FILES = {
    "operator-bridge-template.md",
}


def has_bridge_section(text: str) -> bool:
    return BRIDGE_SECTION_PATTERN.search(text) is not None


def present_monitored_keys(text: str) -> list[str]:
    return [key for key in MONITORED_KEYS if key in text]


def is_bridge_required(path: Path) -> bool:
    name = path.name.lower()
    if name in EXCLUDED_FILES:
        return False
    if name in ALWAYS_MONITORED_FILES:
        return True
    return any(hint in name for hint in FILENAME_HINTS)


def collect_bridge_violations(docs_root: Path) -> list[tuple[Path, list[str]]]:
    violations: list[tuple[Path, list[str]]] = []
    for path in sorted(docs_root.rglob("*.md")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        keys = present_monitored_keys(text)
        if not keys:
            continue
        if not is_bridge_required(path):
            continue
        if has_bridge_section(text):
            continue
        violations.append((path, keys))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check operator-facing docs for the standard operator bridge section.",
    )
    parser.add_argument(
        "docs_root",
        nargs="?",
        default="docs",
        help="Directory containing markdown docs to scan.",
    )
    args = parser.parse_args(argv)

    docs_root = Path(args.docs_root)
    if not docs_root.exists():
        print(f"[bridge-check] docs root not found: {docs_root}", file=sys.stderr)
        return 1

    violations = collect_bridge_violations(docs_root)
    if not violations:
        print("[bridge-check] OK: operator-facing docs include the standard bridge section.")
        return 0

    print("[bridge-check] Missing `운영자 표현과 내부 키` section in operator-facing docs:", file=sys.stderr)
    for path, keys in violations:
        joined = ", ".join(keys)
        print(f"  - {path.as_posix()} (keys: {joined})", file=sys.stderr)
    print(
        "[bridge-check] Add the standard section using docs/operator-bridge-template.md.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
