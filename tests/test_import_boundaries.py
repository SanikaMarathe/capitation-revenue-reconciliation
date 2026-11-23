"""Architecture rules enforced as tests (ARCHITECTURE.md section 4).

- engine/ (money math) may not import llm/ or agents/  -> the LLM can't influence a dollar
- engine/ and agents/ may not import storage.phi       -> neither can reach names/DOB
- agents/ may not import governance.approvals          -> agents propose, never commit
- governance/ may not import llm/                      -> approvals are human/deterministic
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

RULES = {
    "engine": ("llm", "agents", "storage.phi"),
    "agents": ("storage.phi", "governance.approvals"),
    "governance": ("llm",),
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{a.name}" for a in node.names}
    return found


@pytest.mark.parametrize("package,forbidden", RULES.items())
def test_forbidden_imports(package: str, forbidden: tuple[str, ...]) -> None:
    violations = []
    for file in (ROOT / package).rglob("*.py"):
        for imp in _imports(file):
            if any(imp == f or imp.startswith(f + ".") for f in forbidden):
                violations.append(f"{file.relative_to(ROOT)} imports {imp}")
    assert not violations, "\n".join(violations)
