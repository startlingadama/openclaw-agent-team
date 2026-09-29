"""The document tools on real files in a temporary directory (no LaTeX engine involved)."""

import asyncio

import pytest

from openclaw.domain.shared.errors import ToolError, ValidationError
from openclaw.domain.tools.model import RiskLevel, ToolCall
from openclaw.infrastructure.tools.docs import DocsConfig, DocsToolProvider

AGENT = "writer"


@pytest.fixture
def docs(tmp_path):
    root = tmp_path / "reports"
    return DocsToolProvider(DocsConfig(root, engine_path=None)), root


def call(p, name, **arguments):
    return asyncio.run(p.execute(ToolCall(name, arguments), AGENT))


def test_write_read_and_patch_a_markdown_document(docs):
    p, root = docs
    assert "written: a/notes.md" in call(
        p, "docs.write", path="a/notes.md", content="# Title\nbody"
    )
    assert (root / "a" / "notes.md").read_text() == "# Title\nbody"
    assert call(p, "docs.read", path="a/notes.md") == "# Title\nbody"
    call(p, "docs.patch", path="a/notes.md", old="body", new="text")
    assert call(p, "docs.read", path="a/notes.md") == "# Title\ntext"


def test_patch_needs_a_unique_text(docs):
    p, _ = docs
    call(p, "docs.write", path="n.md", content="x x")
    with pytest.raises(ToolError, match="found 2"):
        call(p, "docs.patch", path="n.md", old="x", new="y")
    with pytest.raises(ToolError, match="found 0"):
        call(p, "docs.patch", path="n.md", old="z", new="y")


def test_a_missing_document_is_refused(docs):
    p, _ = docs
    with pytest.raises(ToolError, match="not found"):
        call(p, "docs.read", path="nope.md")
    with pytest.raises(ToolError, match="not found"):
        call(p, "docs.patch", path="nope.tex", old="a", new="b")


@pytest.mark.parametrize(
    "bad",
    [
        "../x.md",
        "a/../../x.md",
        "/etc/passwd.md",
        "a\\..\\x.md",
        ".hidden.md",
        "a/.git/x.md",
        "x.py",
        "x.pdf",
        "x",
        ".",
    ],
)
def test_paths_that_leave_the_directory_or_have_another_type_are_refused(docs, bad):
    p, root = docs
    with pytest.raises(ValidationError):
        call(p, "docs.write", path=bad, content="x")
    assert not (root.parent / "x.md").exists()


def test_a_symbolic_link_leaving_the_directory_is_refused(docs, tmp_path):
    p, root = docs
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "file.md").symlink_to(outside / "secret.md")
    with pytest.raises(ValidationError, match="leaves"):
        call(p, "docs.read", path="link/secret.md")
    with pytest.raises(ValidationError, match="leaves"):
        call(p, "docs.write", path="link/new.md", content="x")
    with pytest.raises(ValidationError, match="leaves"):
        call(p, "docs.read", path="file.md")
    assert not (outside / "new.md").exists()


def test_arguments_are_validated(docs):
    p, _ = docs
    with pytest.raises(ValidationError):
        call(p, "docs.write", path="a.md", content=5)
    with pytest.raises(ValidationError, match="too long"):
        call(p, "docs.write", path="a.md", content="x" * 200_001)
    with pytest.raises(ValidationError, match="required"):
        call(p, "docs.read")


def test_risk_levels(docs):
    p, _ = docs
    assert p.get_spec("docs.read").risk_level == RiskLevel.READ
    for name in ("docs.write", "docs.patch"):
        assert p.get_spec(name).risk_level == RiskLevel.WRITE
    assert not any(n.startswith(("publish", "send")) for n in p.tool_names)
