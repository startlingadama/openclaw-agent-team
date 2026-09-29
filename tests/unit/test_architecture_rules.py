"""Guards the dependency direction defined in ARCHITECTURE.md section 21."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "openclaw"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def _violations(
    layer: str,
    forbidden: tuple[str, ...],
    *,
    skip: tuple[str, ...] = (),
    allowed: tuple[str, ...] = (),
) -> list[str]:
    bad = []
    for py in (SRC / layer).rglob("*.py"):
        if py.name in skip:
            continue
        for mod in _imports(py):
            if mod.startswith(forbidden) and not mod.startswith(allowed):
                bad.append(f"{py.relative_to(SRC)} imports {mod}")
    return bad


def test_domain_does_not_import_outer_layers():
    assert not _violations(
        "domain",
        ("openclaw.infrastructure", "openclaw.application", "openclaw.entrypoints"),
    )


def test_application_does_not_import_infrastructure_or_entrypoints():
    assert not _violations("application", ("openclaw.infrastructure", "openclaw.entrypoints"))


def _third_party(layer: str) -> list[str]:
    import sys

    bad = []
    for py in (SRC / layer).rglob("*.py"):
        for mod in _imports(py):
            top = mod.split(".")[0]
            if top != "openclaw" and top not in sys.stdlib_module_names:
                bad.append(f"{py.relative_to(SRC)} imports {mod}")
    return bad


def test_domain_depends_only_on_the_standard_library():
    assert not _third_party("domain")


def test_entrypoints_only_use_the_application_layer():
    """ARCHITECTURE section 21: entrypoints -> application -> domain.

    Two exceptions, both narrow:
    - `bootstrap.py` is the composition root: it is the one module that builds the concrete
      adapters, so it may import infrastructure and domain.
    - the shared error types (`domain.shared`) are the vocabulary every layer catches.
    """
    assert not _violations(
        "entrypoints",
        ("openclaw.domain", "openclaw.infrastructure"),
        skip=("bootstrap.py",),
        allowed=("openclaw.domain.shared",),
    )


def test_only_the_composition_root_builds_adapters():
    """No module outside `entrypoints/bootstrap.py` may import an infrastructure adapter to
    wire it: tests and infrastructure itself excepted (they are not scanned here)."""
    for layer in ("domain", "application"):
        assert not _violations(layer, ("openclaw.infrastructure",))
    assert not _violations(
        "entrypoints", ("openclaw.infrastructure",), skip=("bootstrap.py",)
    )


def test_infrastructure_does_not_depend_on_application_or_entrypoints():
    """ARCHITECTURE section 21: infrastructure implements domain ports only."""
    assert not _violations("infrastructure", ("openclaw.application", "openclaw.entrypoints"))
