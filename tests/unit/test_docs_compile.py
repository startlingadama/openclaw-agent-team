"""The compile command and its restrictions, with a fake runner (no LaTeX engine needed)."""

import asyncio

import pytest

from openclaw.domain.shared.errors import ToolError, ValidationError
from openclaw.domain.tools.model import ToolCall
from openclaw.infrastructure.tools.code.sandbox import SandboxLimits, SandboxResult
from openclaw.infrastructure.tools.docs import DocsConfig, DocsToolProvider, latex

AGENT = "writer"
GOOD = "\\documentclass{article}\n\\usepackage{amsmath}\n\\begin{document}Hi\\end{document}\n"


class FakeRunner:
    def __init__(self, result=None, make_pdf=True, timeout_s=7.0):
        self.limits = SandboxLimits(timeout_s=timeout_s)
        self.result = result or SandboxResult(0, "", "")
        self.make_pdf = make_pdf
        self.calls = []

    async def run(self, argv, cwd, *, env=None):
        self.calls.append((argv, cwd))
        self.env = env
        if self.make_pdf:
            (cwd / "doc.pdf").write_bytes(b"%PDF-1.5 fake")
        return self.result


def provider(tmp_path, runner, engine_path="/usr/bin/xelatex"):
    return DocsToolProvider(DocsConfig(tmp_path, engine_path=engine_path), sandbox=runner)


def call(p, name, **arguments):
    return asyncio.run(p.execute(ToolCall(name, arguments), AGENT))


def test_the_command_disables_shell_escape_and_confines_tex_files():
    argv = latex.build_command("/usr/bin/xelatex", "doc.tex", env_path="/usr/bin/env")
    assert argv[0] == "/usr/bin/env"
    assert {"openin_any=p", "openout_any=p", "shell_escape=f"} <= set(argv)
    assert "-no-shell-escape" in argv
    assert "-halt-on-error" in argv and "-interaction=nonstopmode" in argv
    assert argv[-1] == "doc.tex"
    assert "--nosocket" not in argv
    assert "--nosocket" in latex.build_command("/usr/bin/lualatex", "doc.tex")


def test_compilation_runs_in_the_document_directory_with_the_time_limit(tmp_path):
    runner = FakeRunner(timeout_s=7.0)
    p = provider(tmp_path, runner)
    call(p, "docs.write", path="a/doc.tex", content=GOOD)
    out = call(p, "docs.compile_pdf", path="a/doc.tex")
    ((argv, cwd),) = runner.calls
    assert cwd == (tmp_path / "a").resolve()
    assert argv[-1] == "doc.tex" and "-no-shell-escape" in argv
    assert runner.limits.timeout_s == 7.0
    assert out == {"pdf": "a/doc.pdf", "tex": "a/doc.tex", "bytes": 13}


def test_a_failed_run_returns_a_log_excerpt(tmp_path):
    runner = FakeRunner(SandboxResult(1, "", ""), make_pdf=False)
    p = provider(tmp_path, runner)
    call(p, "docs.write", path="a/doc.tex", content=GOOD)
    (tmp_path / "a" / "doc.log").write_text(
        "noise\n./doc.tex:3: Undefined control sequence.\nl.3 \\foo\n"
    )
    with pytest.raises(ToolError, match="Undefined control sequence"):
        call(p, "docs.compile_pdf", path="a/doc.tex")


def test_a_timeout_is_reported(tmp_path):
    runner = FakeRunner(SandboxResult(None, "", "", timed_out=True), make_pdf=False)
    p = provider(tmp_path, runner)
    call(p, "docs.write", path="a/doc.tex", content=GOOD)
    with pytest.raises(ToolError, match="time limit"):
        call(p, "docs.compile_pdf", path="a/doc.tex")


def test_a_stale_pdf_is_not_kept_after_a_failure(tmp_path):
    runner = FakeRunner(SandboxResult(1, "! error", ""), make_pdf=False)
    p = provider(tmp_path, runner)
    call(p, "docs.write", path="a/doc.tex", content=GOOD)
    (tmp_path / "a" / "doc.pdf").write_bytes(b"%PDF old")
    with pytest.raises(ToolError):
        call(p, "docs.compile_pdf", path="a/doc.tex")
    assert not (tmp_path / "a" / "doc.pdf").exists()


def test_packages_and_classes_outside_the_allowlist_are_refused_before_any_run(tmp_path):
    runner = FakeRunner()
    p = provider(tmp_path, runner)
    call(
        p,
        "docs.write",
        path="a/doc.tex",
        content="\\documentclass{beamer}\n\\usepackage{shellesc,amsmath}\n",
    )
    with pytest.raises(ValidationError, match="class beamer.*package shellesc"):
        call(p, "docs.compile_pdf", path="a/doc.tex")
    assert runner.calls == []


def test_a_commented_package_is_not_a_declaration():
    assert (
        latex.forbidden(
            "% \\usepackage{evil}\n\\usepackage{amsmath}", frozenset(), frozenset({"amsmath"})
        )
        == []
    )


def test_a_symbolic_link_in_the_document_directory_refuses_the_compilation(tmp_path):
    runner = FakeRunner()
    p = provider(tmp_path, runner)
    call(p, "docs.write", path="a/doc.tex", content=GOOD)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    (tmp_path / "a" / "link.txt").symlink_to(outside)
    with pytest.raises(ValidationError, match="symbolic link"):
        call(p, "docs.compile_pdf", path="a/doc.tex")
    assert runner.calls == []


def test_only_tex_documents_are_compiled(tmp_path):
    p = provider(tmp_path, FakeRunner())
    call(p, "docs.write", path="a/notes.md", content="# x")
    with pytest.raises(ValidationError, match="only a .tex"):
        call(p, "docs.compile_pdf", path="a/notes.md")


def test_without_an_engine_the_compile_tool_is_not_offered(tmp_path):
    p = DocsToolProvider(DocsConfig(tmp_path, engine_path=None))
    assert "docs.compile_pdf" not in p.tool_names
    assert p.get_spec("docs.compile_pdf") is None
    assert p.tool_names == {"docs.read", "docs.write", "docs.patch"}
    assert "xelatex" in p.compile_unavailable


def test_the_configuration_is_validated(tmp_path):
    with pytest.raises(ValidationError, match="OPENCLAW_DOCS_LATEX_ENGINE"):
        DocsConfig.from_env({"OPENCLAW_DOCS_LATEX_ENGINE": "tex --shell-escape"}, tmp_path)
    with pytest.raises(ValidationError, match="OPENCLAW_DOCS_LATEX_TIMEOUT"):
        DocsConfig.from_env({"OPENCLAW_DOCS_LATEX_TIMEOUT": "0"}, tmp_path)
    config = DocsConfig.from_env({"OPENCLAW_DOCS_LATEX_TIMEOUT": "5"}, tmp_path)
    assert config.timeout_s == 5 and config.engine == "xelatex"


# -- Windows: no `env` program, and the opt-in to compile without network isolation (ADR-027) ----
def test_without_env_the_command_has_no_env_program_and_the_same_flags():
    argv = latex.build_command(r"C:\tex\xelatex.exe", "doc.tex", use_env=False)
    assert argv[0] == r"C:\tex\xelatex.exe"
    assert not any(part.startswith(("openin_any", "openout_any", "shell_escape")) for part in argv)
    assert "-no-shell-escape" in argv and argv[-1] == "doc.tex"


def test_the_windows_environment_holds_the_settings_and_no_credential():
    environ = {
        "SystemRoot": r"C:\Windows",
        "APPDATA": r"C:\Users\me\AppData\Roaming",
        "DEEPSEEK_API_KEY": "sk-secret",
        "GITHUB_TOKEN": "ghp_secret",
    }
    env = latex.windows_environment(r"C:\tex\bin\xelatex.exe", environ)
    assert env["openin_any"] == "p" and env["openout_any"] == "p" and env["shell_escape"] == "f"
    assert env["PATH"].split(";")[0] == r"C:\tex\bin"
    assert env["APPDATA"] == r"C:\Users\me\AppData\Roaming"
    assert "sk-secret" not in "".join(env.values()) and "ghp_secret" not in "".join(env.values())


def test_on_windows_the_settings_go_through_the_sandbox_environment(tmp_path, monkeypatch):
    from openclaw.infrastructure.tools.docs import provider as docs_provider

    monkeypatch.setattr(docs_provider, "_windows", lambda: True)
    runner = FakeRunner()
    p = provider(tmp_path, runner, engine_path="/usr/bin/xelatex")
    call(p, "docs.write", path="a/doc.tex", content=GOOD)
    call(p, "docs.compile_pdf", path="a/doc.tex")
    ((argv, _cwd),) = runner.calls
    assert argv[0] == "/usr/bin/xelatex" and "-no-shell-escape" in argv
    assert runner.env["shell_escape"] == "f" and runner.env["openout_any"] == "p"


def test_the_opt_in_is_read_from_the_environment_and_off_by_default(tmp_path):
    assert DocsConfig.from_env({}, tmp_path).allow_unisolated is False
    on = DocsConfig.from_env({"OPENCLAW_DOCS_ALLOW_UNISOLATED": "true"}, tmp_path)
    assert on.allow_unisolated is True
    off = DocsConfig.from_env({"OPENCLAW_DOCS_ALLOW_UNISOLATED": "no"}, tmp_path)
    assert off.allow_unisolated is False


class NoIsolation:
    """Stands for SubprocessSandbox on a machine without network isolation."""

    def __init__(self, limits, allow_unisolated=False):
        if not allow_unisolated:
            raise ValidationError("sandbox needs Linux with `unshare` (network isolation)")
        self.limits = limits
        self.isolated = False


def test_without_isolation_and_without_the_opt_in_the_compile_tool_is_not_offered(
    tmp_path, monkeypatch
):
    from openclaw.infrastructure.tools.docs import provider as docs_provider

    monkeypatch.setattr(docs_provider, "SubprocessSandbox", NoIsolation)
    config = DocsConfig(tmp_path, engine_path="/usr/bin/xelatex")
    p = DocsToolProvider(config)
    assert p.get_spec("docs.compile_pdf") is None
    assert "OPENCLAW_DOCS_ALLOW_UNISOLATED" in p.compile_unavailable
    assert p.get_spec("docs.write") is not None


def test_with_the_opt_in_the_compile_tool_is_offered_and_says_it_is_not_network_isolated(
    tmp_path, monkeypatch
):
    from openclaw.infrastructure.tools.docs import provider as docs_provider

    monkeypatch.setattr(docs_provider, "SubprocessSandbox", NoIsolation)
    config = DocsConfig(tmp_path, engine_path="/usr/bin/xelatex", allow_unisolated=True)
    p = DocsToolProvider(config)
    spec = p.get_spec("docs.compile_pdf")
    assert spec is not None and p.compile_unavailable is None and p.compile_isolated is False
    assert "no network" not in spec.description
