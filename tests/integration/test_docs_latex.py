"""LaTeX compilation with a real engine. Skipped when no engine (or no isolation) is available."""

import asyncio
import time

import pytest

from openclaw.domain.shared.errors import ToolError
from openclaw.domain.tools.model import ToolCall
from openclaw.infrastructure.tools.docs import DocsConfig, DocsToolProvider

AGENT = "writer"


def make(tmp_path, timeout="60"):
    config = DocsConfig.from_env({"OPENCLAW_DOCS_LATEX_TIMEOUT": timeout}, tmp_path / "reports")
    p = DocsToolProvider(config)
    if "docs.compile_pdf" not in p.tool_names:
        pytest.skip(p.compile_unavailable)
    return p


def call(p, name, **arguments):
    return asyncio.run(p.execute(ToolCall(name, arguments), AGENT))


def doc(body):
    return "\\documentclass{article}\n\\begin{document}\n" + body + "\n\\end{document}\n"


def test_a_valid_document_gives_a_pdf(tmp_path):
    p = make(tmp_path)
    call(p, "docs.write", path="d/doc.tex", content=doc("Bonjour, é à ç ü — 中文 no."))
    out = call(p, "docs.compile_pdf", path="d/doc.tex")
    pdf = tmp_path / "reports" / "d" / "doc.pdf"
    assert out["pdf"] == "d/doc.pdf" and out["bytes"] > 0
    assert pdf.read_bytes().startswith(b"%PDF")


def test_an_invalid_document_returns_a_log_excerpt(tmp_path):
    p = make(tmp_path)
    call(p, "docs.write", path="d/doc.tex", content=doc("\\undefinedcommand"))
    with pytest.raises(ToolError, match="Undefined control sequence"):
        call(p, "docs.compile_pdf", path="d/doc.tex")
    assert not (tmp_path / "reports" / "d" / "doc.pdf").exists()


def test_a_source_cannot_run_a_command(tmp_path):
    p = make(tmp_path)
    call(p, "docs.write", path="d/doc.tex", content=doc("\\immediate\\write18{touch pwned.txt}ok"))
    try:
        call(p, "docs.compile_pdf", path="d/doc.tex")
    except ToolError:
        pass  # refusing is fine too: what matters is that nothing ran
    assert not (tmp_path / "reports" / "d" / "pwned.txt").exists()


def test_a_source_cannot_read_outside_its_directory(tmp_path):
    p = make(tmp_path)
    (tmp_path / "reports").mkdir(exist_ok=True)
    (tmp_path / "secret.tex").write_text("TOPSECRETMARKER")
    call(p, "docs.write", path="d/doc.tex", content=doc("\\input{../../secret.tex}"))
    with pytest.raises(ToolError):
        call(p, "docs.compile_pdf", path="d/doc.tex")
    call(p, "docs.write", path="d/abs.tex", content=doc(f"\\input{{{tmp_path / 'secret.tex'}}}"))
    with pytest.raises(ToolError):
        call(p, "docs.compile_pdf", path="d/abs.tex")
    assert not (tmp_path / "reports" / "d" / "doc.pdf").exists()


def test_a_source_that_never_ends_is_stopped(tmp_path):
    p = make(tmp_path, timeout="3")
    call(p, "docs.write", path="d/doc.tex", content=doc("\\def\\a{\\a}\\a"))
    started = time.monotonic()
    with pytest.raises(ToolError, match="limit"):
        call(p, "docs.compile_pdf", path="d/doc.tex")
    assert time.monotonic() - started < 20
