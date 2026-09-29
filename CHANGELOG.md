# Changelog

All notable changes to this project will be documented here.

The project follows Semantic Versioning where applicable.

---

## [Unreleased]

### Added (Telegram delivers the files of a task)

* the Telegram channel sends the files a task produced (`.md`, `.tex`, `.pdf` of the writer) to the chat, as documents, after the answer (ADR-030): same rule and same download check as the WebChat, read from the events of the task, also for a writer reached through a delegation. A file that is too large (50 MB), that cannot be read or that Telegram refuses is named in the chat
* `TelegramClient.send_document` (multipart upload) and `TaskDocuments` (`infrastructure/channels/telegram/documents.py`), wired in `build_telegram_bot`

### Changed

* Telegram answers are sent as Telegram HTML: `to_telegram_html` converts the Markdown of the agents (headings, bold, italic, strike-through, code, code blocks, links, lists, block quotes, tables as lists) and escapes the rest; a message Telegram refuses is sent again as plain text; a code block cut by the 4000-character limit is closed and reopened (ADR-028)
* the `code-executor` agent no longer needs approval for `code.execute` and `code.terminal` (`approval_required: []`); its docs, skill and tests follow (ADR-025 amendment)

### Added

* Initial project requirements
* Hexagonal architecture specification
* DDD domain model
* Agent profile model
* Markdown-based memory model
* Agent Skills architecture
* Progressive skill disclosure
* Explicit ReAct runtime specification
* Multi-agent team architecture
* Supervisor orchestration pattern
* Peer-to-peer communication model
* Shared memory model
* Human approval model
* Least-privilege tool model
* DeepSeek LLM abstraction
* GitHub Agent specification
* LinkedIn Agent specification
* Google Email Agent specification
* Tavily web search backend next to Brave (`TAVILY_API_KEY`), with configurable provider order and automatic fallback (`WEB_SEARCH_PROVIDERS`)
* Google Research Agent specification
* Code Executor Agent specification (see "Added (Specification - Code Executor Agent)")
* CLI channel specification
* WebChat channel specification
* Telegram channel specification
* Architecture decision records
* WebChat HTTP channel implementation: auth, task lifecycle, SSE events, approvals, execution tracing and frontend contract compatibility
* Application `RunTask` use case, infrastructure approval/event-sink adapters, and execution history endpoints backed by `ReadHistory`
* WebChat channel split into HTTP transport, API facade, resource queries, task lifecycle, task store, and DTO/event serialization modules; `entrypoints/web.py` reduced to a compatibility factory

### Added (Phase 1 - Foundation)

* Python package skeleton `src/openclaw` (domain / application / infrastructure / entrypoints)
* `pyproject.toml` (uv, pytest, ruff), `.env.example`, `.gitignore`
* Agent profiles (SOUL/USER/MEMORY/AGENTS/HEARTBEAT) and `agent.yaml` for github, linkedin, google-email, google-research, ceo
* Team configurations (default, executive, research, growth)
* Skill stubs following the Agent Skills layout
* Shared workspace layout and test skeleton (architecture-rule and layout tests)
* Minimal CLI entrypoint
* `USER.md` is user memory (memory layer `user`), not part of `AgentProfile`
* Secrets loaded from `.env` with python-dotenv at CLI startup (real environment variables take precedence)
* Domain entities `Task` (REQUIREMENTS §18), `Team` with orchestration patterns (§15-17) and `MemoryLayer` (§14)

### Added (Phase 2 - Agent Runtime)

* Explicit async ReAct runtime in the application layer (`AgentRuntime`): observe -> reason -> act loop
* Runtime components: ContextBuilder, SkillResolver, ToolResolver, MemoryManager, ActionExecutor (application) and PolicyEngine (domain)
* Domain entities: Agent (`AgentId`), Skill (`SkillId`), Tool (risk levels), `AgentMessage`, `Approval`, `Execution`
* Ports: `LLMPort`, `ToolPort`, `SkillRepository`, `MemoryRepository` (read), `ApprovalPort`, `EventSink`
* Least-privilege tool permissions (`allowed` / `approval_required`) and approval by risk level
* Retry policy (opt-in, READ tools only), explicit error categories
* Structured execution events (task, agent, skill, tool, input, output, duration, status, approval, error)
* Unit tests for policy and runtime with fake ports; architecture guard tests (dependency direction, stdlib-only domain)

### Added (Phase 3 - Memory)

* `MemoryRepository` port extended to read / write / update / archive / search (REQUIREMENTS §13)
* `MemoryReference` (layer + owning agent) and `MemoryHit` domain models
* `MarkdownMemoryRepository` (`infrastructure/memory/markdown`): agent memory (`agents/<id>/MEMORY.md`), user memory (`agents/<id>/USER.md`), shared team memory (`workspace/shared/MEMORY.md`)
* `memory.update` tool (`MemoryToolProvider`): the agent writes its own agent/user memory through a dedicated, permissioned tool; the memory owner is the caller, never an LLM argument
* `ToolPort.execute` now receives the calling agent (agent and credential isolation)
* Archiving moves a section to `archive/<file>-<date>.md` next to the source file
* Section-level updates on `##` headings, atomic writes, CRLF preservation, agent-id validation (no path traversal)
* Unit tests (Markdown logic), integration tests (real files), architecture guard for `infrastructure`

### Added (Phase 4 - Skills)

* `SkillLoader` (`infrastructure/skills`) implementing the `SkillRepository` port: skills read from `skills/<group>/<name>/SKILL.md` or `skills/<name>/SKILL.md` (REQUIREMENTS §6, §24)
* Progressive disclosure (ADR-007): name and description first, full `SKILL.md` only on demand
* Skill contract validation (REQUIREMENTS §8): YAML frontmatter with `name` and `description`, required sections; `scripts/`, `references/` and `assets/` listed by path only
* Skill ids checked against path traversal; explicit `SkillError` on unknown or invalid skills
* Skill registry: `SkillRepository.discover()` lists name and description of every available skill (MVP #3), sorted by id; a broken skill is reported explicitly
* Integration tests including the 13 project skills and the runtime discovering then loading a skill

### Added (Phase 5 - Tools)

* `ToolRegistry` (`domain/tools/registry.py`) implementing `ToolPort`: dispatches each tool name to the provider that serves it, passing the calling agent
* `ToolProvider` contract (declared `tool_names`, `get_spec`, `execute`); all-or-nothing registration, duplicate and inconsistent tools refused
* `MemoryToolProvider` registered through it; permissions stay enforced by the `PolicyEngine`, not by the registry

### Added (Phase 6 - Memory history)

* `MemoryRepository.history(reference, limit)`: changes of one document, newest first, read-only (REQUIREMENTS §13)
* Domain models `MemoryChange` and `MemoryOperation` (write / update / archive) with the previous and new content
* `MarkdownMemoryRepository` records every `write`, `update` and `archive` in `history/<file>.jsonl` next to the source file, inside the document lock; a change that alters nothing is not recorded
* Update and archive entries hold the `## section` block only, write entries the whole document (ADR-021)
* Integration tests (real files, concurrency, corrupted history, unicode) and a unit test for `get_section`

### Added (Phase 7 - DeepSeek adapter)

* `DeepSeekAdapter` (`infrastructure/llm/deepseek`) implementing `LLMPort` on DeepSeek's OpenAI-compatible chat completions API (httpx)
* Each ReAct step is stateless: the context is rendered into one system and one user message; the LLM answers by calling exactly one function (a permitted tool, `use_skill` or `finish`) with `tool_choice=required` (ADR-022)
* Tool names are made wire-safe (`memory.update` -> `memory_update`) and mapped back; control functions cannot be shadowed by a tool
* Errors mapped to REQUIREMENTS §23 categories: 401/403 `AuthenticationError`; 429, 5xx, timeouts, network and malformed output `LLMError(retryable=True)` with bounded exponential backoff; other 4xx and truncated answers not retried
* Tool results are marked as data, not instructions, and truncated (`max_observation_chars`); the API key is never put in an error message
* `DeepSeekConfig.from_env` (`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, optional `DEEPSEEK_BASE_URL`); `deepseek-reasoner` refused (no tool calling)
* New dependency: `httpx`
* Unit tests (prompt, parsing), integration tests on a mocked HTTP transport (no network), and a full `AgentRuntime` loop with the adapter

### Added (Phase 8 - Composition root and `openclaw run`)

* `entrypoints/bootstrap.py`: the composition root. `build_app()` builds DeepSeek (mandatory), the Markdown memory, the skill loader and the tool registry, and returns an `App` (`run_agent`, `tools`, `skipped`, `aclose`)
* Tool providers by credentials: `memory.update` and `web.*` always; `github.*`, `google.*`, `linkedin.*` only when `GITHUB_TOKEN`, `GOOGLE_TOKEN_FILE`, `LINKEDIN_ACCESS_TOKEN` are set. A provider that is absent or invalid is reported in `App.skipped`, never fatal; `web.search` is reported separately when no search key matches `WEB_SEARCH_PROVIDERS`
* `openclaw run <agent> "<task>" [-v]`: runs one agent to completion. Answer on stdout, errors on stderr; exit codes 0 (completed), 1 (run failed or step guard hit), 2 (configuration or usage error). `-v` traces every step on stderr
* `RunAgent` use case (application): loads the agent, refuses to start (`MissingToolsError`) when a permitted tool has no provider, before any LLM call, then runs the runtime
* `AgentRepository` port and `YamlAgentRepository` (`infrastructure/agents`): `agents/<id>/agent.yaml` + `SOUL.md`, `AGENTS.md`, `HEARTBEAT.md` -> `Agent`; directory name and `agent.id` must agree; the profile always lives in `agents/<id>`
* `CliApprovalPort` (terminal approval; without a terminal nothing is approved) and `ConsoleEventSink`
* Composition-root defaults: step guard on (`OPENCLAW_MAX_STEPS`, default 25) and one retry for READ tools; new settings `OPENCLAW_HOME`, `OPENCLAW_MAX_STEPS`
* Architecture guard: `bootstrap.py` is the only entrypoint module allowed to import infrastructure and domain; other entrypoints use the application layer and the shared error types only
* The `llm:` block of `agent.yaml` is not read yet: one DeepSeek adapter, configured from the environment, serves every agent

### Added (Phase 9 - Supervisor delegation)

* `TeamRepository` port (`domain/teams`) and `YamlTeamRepository` (`infrastructure/teams`): `teams/<id>/team.yaml` is the only source of truth for team membership; directory name and `team.id` must agree, the supervisor cannot be a member, no duplicate members, agent ids checked against path traversal. New error category `TeamError`
* `DelegateTask` use case (`application/teams`): the supervisor of the active team hands a `Task` (REQUIREMENTS §18) to one of its members through `RunAgent`; the member runs under its own permissions and approvals, the sender of its task is the supervisor
* Enforced in code, never by the LLM: the caller must be the team supervisor, the target must be a member, `required_skills` must belong to the member, delegation is one level deep, and the member's own missing tools are refused before any LLM call
* Tools `team.members` (READ) and `team.delegate` (WRITE, so never retried automatically), served by `TeamToolProvider`. A failed or partial member run is returned to the supervisor as a result (`status`, `answer`, `error`), not raised
* Active team: `OPENCLAW_TEAM` (default `default`) or `team_scope(...)` around a run; context variables, so concurrent runs cannot leak into each other
* `RunAgent.__call__` accepts optional `sender` and `task_id` (defaults unchanged)
* `agents/ceo`: may use `team.members` / `team.delegate`; the duplicated `team:` block of `agent.yaml` is removed (it was never read); `AGENTS.md` describes the delegation procedure
* Tests: team repository (real files), delegation rules (fake ports), CEO -> specialist through `build_app` on the project files

### Added (Phase 9 - Team commands)

* `openclaw team run <team> "<task>"`: the task goes to the team's supervisor and every delegation stays inside that team (`team_scope`), whatever `OPENCLAW_TEAM` says. `RunTeam` use case (`application/teams`); only the supervisor pattern can be run. `-v` traces every agent
* `openclaw team list`: one line per team (pattern, supervisor, members). Needs no LLM key; one invalid `team.yaml` is reported without hiding the others
* Approval prompt on the terminal now names the agent that asks (`Approval needed from [github]: ...`), so a specialist's request is not mistaken for the CEO's
* Tests: `RunTeam` (fake ports), CLI commands, prompt content, and through `build_app`: a specialist's approval carries the specialist's id, `team run` needs no `OPENCLAW_TEAM`

### Added (Phase 9 - Persistent execution trace)

* Every execution is written under `executions/<start>_<agent>_<id>/` (ARCHITECTURE §19, ADR-024): `events.jsonl` (appended as the run goes), `execution.json` (status, steps, timing, error, parent and delegated executions) and `result.md` (request and answer). `execution.json` reads `running` until the run ends
* `FileTraceSink` (`infrastructure/observability`) is an `EventSink`: the two summaries are derived from the events alone, the runtime knows nothing about files. Long strings are cut (20 000 characters), the directory is private (0700), a write failure is logged once and never stops the run
* Delegation link: `task_started` now carries `parent_execution_id` and `final_result` carries `steps`. The parent id comes from a context variable set by the runtime (`application/agents/lineage.py`), so a specialist run by `team.delegate` points to the supervisor's execution and the supervisor's `execution.json` lists it under `children`
* `CompositeEventSink`: `-v` prints events on stderr and still writes the trace. An injected `sink` replaces the whole default stack (tests never write to disk)
* `OPENCLAW_TRACE=off` disables the trace (default on). `executions/` was already in `.gitignore`. The trace keeps tool inputs and outputs, e-mail contents included
* Tests: sink on hand-built events (files, linking both ways, interleaved runs, truncation, write failure, permissions), and through `build_app` on the project files (CEO -> specialist leave two linked traces; trace off; `-v`; injected sink)

### Added (Phase 9 - Execution history)

* `openclaw runs [-n N] [--all]`: recent executions, newest first (id, start time, agent, status, steps, duration, request). A delegation is part of its supervisor's run, so it is listed only with `--all`
* `openclaw show <execution> [--events]`: one execution and, indented under it, what it delegated (who asked whom, request, answer, error). `<execution>` is an execution id, a task id or a trace directory name, a prefix is enough; an ambiguous prefix lists the candidates. `--events` adds every step of every run. Times are UTC, like the trace names and `events.jsonl`
* `ExecutionHistory` port (`domain/tasks`), `FileExecutionHistory` (`infrastructure/observability`) and `ReadHistory` use case (`application/tasks`). Children are found by `parent_execution_id` rather than by the supervisor's `children` list, so a supervisor that was killed before it ended still shows its delegations. Unreadable trace directories are skipped with a warning; a line cut by a crash is ignored. New error category `HistoryError`
* Reading history needs no LLM key and no provider (`load_history` in the bootstrap). The request shown by `runs` comes from the first line of `events.jsonl`, so traces written before this change are listed too
* A run that never ended (killed, Ctrl-C) stays `running` in the list: there is no way to tell it from a run still in progress
* Tests: reader and use case on traces written by the real `FileTraceSink` (ordering, limit, prefixes, ambiguity, cut lines, corrupt directories, loop guard), the CLI output, and a real CEO -> specialist run read back through the CLI

### Fixed (GitHub "how many repositories do I have")

* `github.search_repository` failed with HTTP 422 on `user:@me` (a `gh` CLI shorthand the REST API refuses) and the agent had no way to know whose token it held
* New READ tool `github.get_authenticated_user` (login, name, `public_repos`, and `owned_private_repos` / `total_private_repos` when the token may see them); allowed for the `github` agent
* `user:@me` is now replaced by the token owner's login in `github.search_repository` and `github.search_code` (login fetched once per provider, validated as a GitHub name)
* HTTP error messages now include the provider's reason from `errors[].message` (GitHub 422 said only "Validation Failed"), so the model can correct its call instead of repeating it
* `agents/github/AGENTS.md`: procedure for "my repositories" questions
* Tests on a mocked transport, including a guard that every tool the `github` agent may use is served by the provider
* Optional `GITHUB_USERNAME` in `.env`: the account "my repositories" and `user:@me` mean (a leading `@` is accepted, an invalid name disables the GitHub tools with an explicit reason). Without it, the owner of the token is used. `github.get_authenticated_user` reports it as `default_username`
* `OPENCLAW_TEAM` documented in `.env.example`

### Changed (step limit)

* Reaching `max_steps` no longer discards the work: the runtime gives the LLM one last step (`DecisionContext.final_step`) where only `finish` is offered, with a prompt asking to state what was established and what could not be verified. The run stays `max_steps_exceeded`; the answer is partial and `error` says so. If the model cannot conclude (error, or it asks for a tool), there is no answer, as before
* `openclaw run`: a partial answer is printed on stdout, `incomplete: ...` goes on stderr, exit code stays 1

### Changed (WebChat clean-up)

* Architecture rules: `entrypoints/web.py` only imports `bootstrap` (`build_webchat_api`, `build_webchat_server`); the adapters live in `infrastructure/channels/webchat` and import nothing from `application` or `entrypoints`. The guard tests on entrypoints and infrastructure pass
* Approvals go through the API: `WebChatApprovalController` implements the approval port, keeps pending approvals per execution and task, and suspends the run until `GET /api/approvals`, `POST /api/approvals/<id>/approve` or `/reject` (with a reason) resolves it
* Real-time events: `WebChatEventSink` forwards every execution event to the channel and `GET /api/tasks/<id>/events` streams them as Server-Sent Events (`text/event-stream`); it logs only safe summaries (event type, tool, status, duration, error type)
* Tests and lint: the whole suite runs to the end (see "Fixed (tests)") and `ruff check` is clean
* Docs: README (WebChat channel, `OPENCLAW_WEB_HOST` / `OPENCLAW_WEB_PORT`) and ARCHITECTURE (WebChat adapters split) describe the current channel

### Fixed (tests)

* `test_dotenv_file_is_loaded_without_touching_environment` started a real web server through `main(["web"])` and never returned; `_start_web_server` is now replaced in that test, so the whole suite runs to the end

### Added (Specification - Code Executor Agent)

* Documentation only, nothing is implemented yet: REQUIREMENTS section 3.5, section 9 (tools example) and section 1, ARCHITECTURE section 15.1 and project structure, README (agents, tools, Code Executor Agent, roadmap), ADR-025
* Agent `code-executor`, member of the `default` team. System prompt: an expert in secure code execution who writes short Python scripts that orchestrate tools
* Skills may include Code Execution, Data Analysis and Testing
* Tools: `execute_code` (Programmatic Tool Calling), sandboxed `terminal`, read / write / patch files, `web.search` / `web.extract` when needed
* Rules: code runs in a sandbox only; a script reaches tools only through `ToolPort` with the calling agent's permissions, so it cannot widen them; no provider credentials in the sandbox; script and output are traced (ADR-024)
* WebChat: no channel code is needed for the agent to appear (agents and teams are read from files); the tool listing must show each tool's real risk level and approval requirement instead of the fixed `READ` shown today for every agent (REQUIREMENTS section 3.5, ARCHITECTURE sections 15.2 and 16)
* ARCHITECTURE section 15.2: implementation map (where each piece goes), order of work, tests expected, documentation to update
* ADR-025 lists the open points: sandbox mechanism, other languages, network access, risk level and approval policy per tool, final tool names, how a script calls tools, teams other than `default`, role label shown by the WebChat. The implementer stops there and asks

### Added (Phase 10 - Code Executor, part 1: sandbox and tools)

* `SubprocessSandbox` (`infrastructure/tools/code/sandbox.py`): child process behind `unshare --net` (no network), CPU, memory, file-size and open-files limits, wall-clock timeout, capped output, scrubbed environment; refuses to be built where isolation is not permitted
* `CodeToolProvider`: `code.execute` (Python), `code.terminal`, `code.read_file`, `code.write_file`, `code.patch_file`; files confined to `<workdir>/<agent-id>`
* Registered in `bootstrap.py` only when `OPENCLAW_CODE_SANDBOX=subprocess`; otherwise reported in `App.skipped`. Settings in `.env.example`
* ADR-025: open points turned into decisions (a few remain open)
* Script-to-tool channel (PTC): a script calls `tools.call("<tool>", **arguments)`; one request line on the sandbox's stdout, one answer line on its stdin (`SubprocessSandbox.run_channel`). The time a request takes to be served (tool, human approval) does not count against the timeout
* `ScriptToolBroker` (`application/agents/script_tools.py`) implements the new domain port `ScriptToolPort`: the calls of a script go through the caller's permissions, the `PolicyEngine`, the approval port (the approval carries the agent's id) and `ToolPort`, like a direct call. Events are recorded in the execution that started the script, with `via: "script"` (ADR-024). Outside a run nothing is executed
* `lineage` also carries the task id of the running execution (`execution_scope(execution_id, task_id)`)
* A script cannot call `code.execute` (no script started from a script)
* Tests: broker (fake ports), sandbox and provider on a real subprocess (no network, no secrets, timeout, output cap, memory, confinement, symlink, channel, approval time not counted). Suite: 427 tests, ruff clean
* Not done yet: agent `code-executor`, skills, team membership, WebChat tool listing, tests through `build_app`

### Added (Phase 10 - Code Executor, part 2: agent, skills, team, WebChat listing)

* Agent `code-executor` (`agents/code-executor/`: `SOUL.md`, `USER.md`, `MEMORY.md`, `AGENTS.md`, `HEARTBEAT.md`, `agent.yaml`). Allowed: `memory.update`, `code.read_file`, `code.write_file`, `code.patch_file`, `web.search`, `web.extract`. Approval required: `code.execute`, `code.terminal`
* Skills `code/code-execution`, `code/data-analysis`, `code/testing`
* Team `default`: `code-executor` is a member
* WebChat: the tool listing of each agent shows the tool's real risk level, and `APPROVAL REQUIRED` for the tools the agent declares under `approval_required`, instead of a fixed `READ` (`WebChatResources._agent_tool`)
* Tests: `tests/integration/test_code_executor.py` (profile and skills, every tool served, refusal before any LLM call without the sandbox, delegation, approval carrying the specialist's id, script calls under policy and approval, WebChat listing); `test_team_repository.py` updated for the new member
* Fixed: `sandbox.py` imported `resource` (Unix only) at module load, so `openclaw` crashed at start on Windows; the import is now done when the limits are applied. Where isolation is unavailable (Windows) the `code` provider is reported in `App.skipped` and `code-executor` is refused with `MissingToolsError`, as specified
* Not run for this delivery: the full suite (only the new tests and the team repository tests)
* Still open: the role label shown by the WebChat (still "AI specialist" for every agent)

### Added (WebChat role label)

* `agent.role` in `agent.yaml` (optional, single line, 1 to 40 characters, refused otherwise), carried by `Agent.role` (domain, standard library only) and read by `YamlAgentRepository`
* WebChat: `/api/agents` shows the declared role instead of the fixed `AI specialist`, which stays as the default; `/api/teams` uses it as each member's responsibility
* The six agents declare their role (Supervisor, GitHub specialist, LinkedIn specialist, Email specialist, Research specialist, Code execution specialist)
* Tests: loader (default, trim, invalid values, project agents), WebChat agents and teams, fallback, team member without agent file
* Not run for this delivery: the full suite (targeted tests and `ruff check` only)

### Fixed (CLI arguments)

* `openclaw run <agent> -v "task"` was refused with `unrecognized arguments` (argparse matched the optional `task` to nothing before the flag). The text after the flag is now part of the task, so `-v` works before or after it (`parse_args` in `entrypoints/cli.py`); an unknown flag is still an error
* Tests: `tests/unit/test_cli_args.py`

### Added (Code Executor without isolation, opt-in)

* `OPENCLAW_CODE_ALLOW_UNISOLATED=true`: the `subprocess` mechanism can run where `unshare --net` is not available (Windows). Off by default: the sandbox still refuses to be built. Where isolation is available it is used regardless
* Without isolation the child keeps the timeout, the output cap, a scrubbed environment, its own working directory and a process-tree kill on Windows (`taskkill /F /T`); it has no network isolation and, without `resource`, no CPU / memory / file-size limits. `code.terminal` runs through `cmd.exe /c` on Windows. A warning is logged at start
* Executed code can read the user's files and use the network in this mode: approvals on `code.execute` and `code.terminal` are the barrier
* Tests: `tests/integration/test_code_unisolated.py` (default stays closed, opt-in, environment, timeout, output cap, broker, Windows helpers, composition root). The Windows path itself was not run on a real Windows machine

### Added (Specification - Writer / Documentation Agent)

* Documentation only, nothing is implemented yet: REQUIREMENTS section 3.6 (and sections 1 and 9), ARCHITECTURE sections 15.3 and 15.4 (and project structure and section 16), README (agents, tools, Writer / Documentation Agent, roadmap), ADR-026
* The agent writes and edits documentation and reports, summarizes sources, and produces them as Markdown, as LaTeX and as PDF: it writes the `.tex` source and a tool compiles it to PDF
* Rules: the agent only prepares documents, it has no tool that publishes, sends or contacts anyone (channel agents keep that, with their approvals); documents are read and written only in the allowed locations; compiling LaTeX is treated as running untrusted code (shell escape disabled, files limited to the document's directory, no network, time limit, capped log, scrubbed environment); a compilation error goes back to the agent as a log excerpt
* ADR-026 proposes, for confirmation: id `writer`, role label, system prompt, four skills (including LaTeX document production), membership of `default`, tool set
* ADR-026 records what the current code shows: the `code.*` file tools are text only, confined to the agent's own directory and offered only where the code sandbox is available; the project has no PDF library and requires no LaTeX engine; the WebChat has no file download route
* ADR-026 open points: LaTeX engine, where compilation runs, behaviour without an engine, file tools (reuse `code.*` or a dedicated provider), output location, tool names and approvals, download route in the WebChat, GitHub read access, teams. The implementer stops there and asks

### Added (Phase 11 - Writer / Documentation Agent, part 1: document tools and LaTeX compilation)

* `infrastructure/tools/docs/`: `DocsToolProvider` with `docs.read` (READ), `docs.write` and `docs.patch` (WRITE) for Markdown and LaTeX documents, and `docs.compile_pdf` (WRITE) that compiles a `.tex` document to a PDF next to it
* Documents live under one directory (`OPENCLAW_DOCS_DIR`, default `workspace/shared/reports`); paths from an LLM are never trusted (`DocumentFiles`: no absolute path, no `..`, no dotfile, no symbolic link leaving the directory, only `.md` and `.tex`)
* LaTeX compilation: XeLaTeX (`OPENCLAW_DOCS_LATEX_ENGINE`, also `pdflatex` or `lualatex`), shell escape disabled, TeX paranoid file mode (reads and writes only inside the directory of the `.tex` file), no network (the code sandbox's `unshare --net`), time limit (`OPENCLAW_DOCS_LATEX_TIMEOUT`, default 60 s), CPU, memory and file-size limits, capped output, scrubbed environment; a directory holding a symbolic link is refused; document classes `article` and `report` and an allowlist of packages (`OPENCLAW_DOCS_LATEX_PACKAGES`); a compilation error goes back to the agent as a log excerpt; a stale PDF is removed before each run
* Registered by `bootstrap.py` without credentials; without a LaTeX engine, or where network isolation is not permitted, `docs.compile_pdf` is not offered and the reason is reported in `App.skipped`
* Tests: fake-runner unit tests of the compile command, integration tests on real files, integration tests with a real engine (skipped when none is installed: valid PDF, log excerpt, source that runs a command or reads outside its directory, source that never ends)

### Added (Phase 11 - Writer / Documentation Agent, part 2: agent, skills, team)

* Agent `writer` (`agent.role`: `Documentation specialist`): profile files, `agent.yaml` with `memory.update`, `web.search`, `web.open`, `web.extract`, `docs.read`, `docs.write`, `docs.patch`, and `docs.compile_pdf` under `approval_required`; no publishing or sending tool, no GitHub tool
* Skills `writing/technical-documentation`, `writing/report-writing`, `writing/editing-proofreading`, `writing/latex-documents`
* Member of the `default` team only
* Tests through `build_app`: delegation, the approval carries the specialist's id, `MissingToolsError` before any LLM call when a provider is absent, every permitted tool is served, no publishing tool, WebChat listing of the role and of each tool's real permission

### Added (Phase 11 - Writer / Documentation Agent, part 3: download route)

* WebChat route `GET /api/documents/<relative path>`: needs a session token (401 otherwise), GET only, serves only `.pdf` (`application/pdf`) and `.tex` files of the documents directory and answers 404 for anything else (missing file, other type, `..`, absolute path, symbolic link leaving the directory)
* `DocumentFiles` (`infrastructure/tools/docs/files.py`) is the one path check shared by the document tools and the route

### Changed (Phase 11)

* ADR-026: the open points became decisions (one point remains open, see the ADR); ARCHITECTURE sections 15.4, 15.5 and 16, README (Writer status) and `.env.example` updated
* `test_bootstrap.py` ignores `docs.compile_pdf` in `App.skipped` (it depends on the machine); the project agent and team lists in the repository tests include the writer

### Added (Phase 11 - Writer / Documentation Agent, part 4: optional tools)

* `tools.optional` in `agent.yaml`: permitted tools whose absence is tolerated. Each name must also be in `allowed` or `approval_required`, otherwise the agent is refused with an explicit error. It grants no permission and changes no approval
* `ToolPermissions.optional` and `ToolPermissions.is_optional` (domain, standard library only); `YamlAgentRepository` reads and validates the key
* `RunAgent` ignores an absent optional tool: the writer runs on a machine without a LaTeX engine, without `docs.compile_pdf`. Any other absent permitted tool still raises `MissingToolsError` before any LLM call
* Writer: `docs.compile_pdf` is declared optional (it stays under `approval_required`); `AGENTS.md` tells the agent to deliver the `.tex` source and to say that no PDF could be produced when the tool is not among its tools
* Tests: domain and policy, agent repository (read, default, not permitted, malformed, only the writer's compile tool is optional in the project), `RunAgent` with fake ports, writer through `build_app` without an engine (runs, the LLM is not offered the tool, a compile call is denied) and a still-required tool that is missing

### Changed (Phase 11, part 4)

* ADR-026: the last open point about the absence of an engine is decided; README (Writer status), ARCHITECTURE and REQUIREMENTS updated
* `test_writer_agent.py`: the test that expected `MissingToolsError` without an engine now expects the agent to run

### Added (Phase 11 - Writer / Documentation Agent, part 5: PDF on Windows)

* `OPENCLAW_DOCS_ALLOW_UNISOLATED=true` (ADR-027): explicit opt-in to compile LaTeX without network isolation, for machines without `unshare` (Windows). Off by default; where isolation is available it changes nothing. Shell escape stays disabled, TeX keeps its restricted file mode, time limit, capped output and scrubbed environment; every compilation still needs approval
* Windows: no `env` program, so `latex.build_command(..., use_env=False)` and `latex.windows_environment` give the TeX settings, a `PATH` with the engine's directory and the TeX distribution's location variables through `SubprocessSandbox.run(..., env=...)` (new optional argument; nothing else of the parent environment is passed)
* `DocsToolProvider.compile_isolated`; the tool description no longer claims "no network" when not isolated; the `App.skipped` reason names the setting
* Tests: fake-runner tests of the Windows command and environment (no credential passed), of the opt-in read from the environment, and of the provider with and without the opt-in on a machine without isolation. Not run on a real Windows TeX installation

### Added (Phase 12 - Telegram channel)

* Telegram channel (ADR-028): long polling with `httpx`, mandatory allow-list of user ids (`TELEGRAM_ALLOWED_USER_IDS`), private chats only; free text goes to the supervisor of the team, `/run <agent> <task>`, `/agents`, `/help`
* approvals in the chat as Approve / Reject buttons, one id per request; no answer in time (`TELEGRAM_APPROVAL_TIMEOUT`) or a request that cannot be sent is a rejection; the chat comes from a context variable of the run, so delegated agents and sandbox scripts ask the right chat
* one task at a time per chat, run in the background; plain-text answers cut into messages of at most 4000 characters; "typing" indicator
* `infrastructure/channels/telegram/` (`client`, `adapter`, `approval`, `formatting`, `messages`, `bot`), `application/tasks/channel.py` (`ChannelBackend`), `build_telegram_bot` in `bootstrap.py`, command `openclaw telegram`
* `.env.example`: `TELEGRAM_ALLOWED_USER_IDS`, `TELEGRAM_TEAM`, `TELEGRAM_APPROVAL_TIMEOUT`, `TELEGRAM_API_URL`
* Tests: configuration, formatting, Bot API client (token never in an error, rate limit), adapter and approvals on fakes (including the real ReAct runtime), polling retries, and the composition root with a fake LLM and a fake Bot API. Not run against the real Telegram servers

### Changed (Phase 12)

* `RunTeam.__call__` accepts an optional `task_id` (passed to the supervisor's run)

### Fixed (WebChat approvals)

* Every approval request of an execution shared the id of that execution, so a second request of the same run (a script calling an approval-required tool, a retry of `code.execute`) looked like the one already answered and the run waited for an answer nobody could give. Each request now has its own id (`WebChatApprovalController`); `/api/approvals` also returns `executionId`, and `taskId` is still resolved through the execution
* Tests: `tests/unit/test_webchat_approval.py` (sequential and simultaneous requests of one execution, unknown id). Not run

### Added (Phase 11 - Writer / Documentation Agent, part 6: documents in the WebChat)

* WebChat route `GET /api/documents`: the documents of the documents directory, newest first (`path`, `name`, `kind`, `size`, `modified`, `folder`, and the task that produced it when known). Needs a session token (401 otherwise), GET only
* `GET /api/tasks/<id>` has a `documents` list: the files the task produced and that still exist, read from its `docs.write`, `docs.patch` and `docs.compile_pdf` events (`channels/webchat/documents.py`)
* `DocumentFiles.listing` and `describe`: a document is listed exactly when it can be downloaded; symbolic links, dotfiles and LaTeX auxiliary files are skipped
* Frontend: download buttons under the answer in the chat, a `Documents` page (list grouped by document folder, viewer for Markdown, LaTeX and PDF, download) and a sidebar entry
* ADR-029; ARCHITECTURE, REQUIREMENTS and README updated

### Changed (Phase 11, part 6)

* `GET /api/documents/<path>` also serves `.md` (`text/markdown; charset=utf-8`), which ADR-026 excluded; the download test now expects it

### Planned
* Peer-to-peer and shared-vault team patterns
* Asynchronous and long-running tasks, heartbeat execution

---

## Versioning

### 0.x

Experimental development.

Breaking changes are allowed.

### 1.x

Stable public architecture and APIs.
