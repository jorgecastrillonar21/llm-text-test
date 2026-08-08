"""The dependency rule, enforced instead of merely documented.

docs/architecture.md claims `api -> application -> domain` with infrastructure
implementing application ports. That claim was false for two releases: the turn
service and the context builder imported SQLAlchemy and the ORM models directly.
Documentation cannot fail a build, so this does.

Deliberately a plain AST walk over the source tree rather than an architecture
framework. It has one job, no configuration, and no dependency of its own.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# Third-party packages and sibling layers each inner layer must stay clear of.
INFRASTRUCTURE_LEAKS = ("sqlalchemy", "fastapi", "httpx", "app.infrastructure", "app.api")

FORBIDDEN: dict[str, tuple[str, ...]] = {
    # Application owns orchestration and talks to storage through its own ports.
    "application": INFRASTRUCTURE_LEAKS,
    # Domain is the innermost layer: no frameworks, and no application either.
    "domain": (*INFRASTRUCTURE_LEAKS, "app.application"),
}


def _imported_modules(source: str, filename: str) -> set[str]:
    """Every module name a file imports, absolute form only.

    A relative import inside these layers cannot reach another layer, so `level > 0`
    is resolved no further than the names it mentions.
    """
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def _violates(module: str, forbidden: str) -> bool:
    """True when `module` is `forbidden` or lives under it.

    Prefix-matched on dotted boundaries so `app.application` never matches a
    hypothetical `app.applications`.
    """
    return module == forbidden or module.startswith(f"{forbidden}.")


def _python_files(layer: str) -> list[Path]:
    return sorted((APP_ROOT / layer).rglob("*.py"))


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_has_no_forbidden_imports(layer: str) -> None:
    forbidden = FORBIDDEN[layer]
    files = _python_files(layer)

    # Guards the guard: a bad path would otherwise make this pass vacuously.
    assert files, f"no Python files found under app/{layer} -- has the tree moved?"

    violations: list[str] = []
    for path in files:
        imported = _imported_modules(path.read_text(encoding="utf-8"), str(path))
        violations.extend(
            f"{path.relative_to(APP_ROOT).as_posix()} imports {module!r} (forbidden: {rule})"
            for module in sorted(imported)
            for rule in forbidden
            if _violates(module, rule)
        )

    assert not violations, "dependency rule violated:\n  " + "\n  ".join(violations)


def test_the_checker_detects_a_planted_violation() -> None:
    """A test that can never fail is worse than no test. Prove this one can."""
    source = "from sqlalchemy import select\nimport app.infrastructure.db.models\n"

    imported = _imported_modules(source, "<planted>")

    assert any(_violates(module, rule) for module in imported for rule in FORBIDDEN["application"])


def test_prefix_matching_does_not_catch_unrelated_neighbours() -> None:
    assert _violates("app.infrastructure.db.models", "app.infrastructure")
    assert _violates("app.infrastructure", "app.infrastructure")
    assert not _violates("app.infrastructures", "app.infrastructure")
    assert not _violates("app.applications", "app.application")
