# OpenClaw Agent Team - Architecture Decisions

## ADR-001 - Python

### Decision

Use Python as the primary implementation language.

### Reason

Python provides a strong ecosystem for:

* AI
* LLM providers
* web research
* GitHub integrations
* Google APIs
* automation
* rapid agent development

---

# ADR-002 - Hexagonal Architecture

### Decision

Use Hexagonal Architecture.

### Reason

External providers are expected to change.

Examples:

```text
DeepSeek → another LLM
Gmail → another email provider
GitHub → another source control provider
```

The domain must remain independent.

---

# ADR-003 - Domain-Driven Design

### Decision

Model the system around explicit domain concepts.

Core concepts:

```text
Agent
Team
Task
Skill
Tool
Memory
Message
Approval
Execution
```

### Reason

These concepts are fundamental to the product and should not be hidden behind framework abstractions.

---

# ADR-004 - Explicit ReAct Runtime

### Decision

Implement the agent loop explicitly.

```text
Observe
→ Reason
→ Act
→ Observe
```

### Reason

The execution flow must remain inspectable and debuggable.

The project does not need a framework to hide this relatively simple loop.

---

# ADR-005 - Markdown-First Memory

### Decision

Use Markdown files as the primary memory representation.

### Reason

Markdown is:

* human-readable
* Git-friendly
* easy to debug
* easy to edit
* transparent
* portable

The system can later add indexing without replacing Markdown as the source of truth.

---

# ADR-006 - Agent Skills Standard

### Decision

Use the Agent Skills directory structure.

```text
skill/
├── SKILL.md
├── scripts/
├── references/
└── assets/
```

### Reason

Skills become portable and reusable.

Agents can share skills without sharing their complete identities or memories.

---

# ADR-007 - Progressive Skill Disclosure

### Decision

Load skill metadata first and full instructions only when required.

### Reason

Loading every skill into every agent context increases:

* token usage
* latency
* noise
* potential instruction conflicts

Progressive disclosure keeps the active context small.

---

# ADR-008 - Separation of Profile, Skills and Tools

### Decision

Agents are composed from:

```text
Profile
+
Skills
+
Tools
+
Memory
```

### Reason

This allows specialization without creating a new Python class for every agent.

Creating a new agent should mostly be a configuration/content operation.

---

# ADR-009 - Least Privilege

### Decision

Agents receive only the tools required for their responsibilities.

### Reason

Reducing tool access reduces:

* security risk
* accidental actions
* prompt-injection impact
* operational complexity

---

# ADR-010 - Supervisor as Initial Team Pattern

### Decision

The first team orchestration model is Hub-and-Spoke.

```text
Supervisor
 ├── Specialist
 ├── Specialist
 └── Specialist
```

### Reason

It is simple to understand and provides centralized task coordination.

Peer-to-peer and shared-vault patterns remain supported by the domain.

---

# ADR-011 - DeepSeek

### Decision

DeepSeek is the default LLM provider.

### Reason

The provider is abstracted behind an LLM port, allowing future replacement.

The system must never embed DeepSeek-specific assumptions in the domain.

---

# ADR-012 - No CrewAI

### Decision

Do not use CrewAI as the orchestration framework.

### Reason

The project already defines its own domain model for:

```text
Agents
Teams
Tasks
Skills
Tools
Messages
```

Another abstraction layer would increase complexity without providing sufficient architectural value.

---

# ADR-013 - Limited LangChain Usage

### Decision

LangChain MAY be used selectively but is not the core runtime.

### Reason

Useful components can be reused while keeping execution explicit.

LangChain must remain behind infrastructure/application boundaries whenever practical.

---

# ADR-014 - Limited LangGraph Usage

### Decision

LangGraph is optional rather than mandatory.

### Reason

The core ReAct loop does not require a graph abstraction.

LangGraph may later be introduced for genuinely complex stateful workflows where explicit state graphs provide measurable value.

---

# ADR-015 - Human Approval

### Decision

Sensitive external actions require explicit approval policies.

Examples:

```text
Send email
Publish LinkedIn post
Send LinkedIn message
Create destructive GitHub operation
```

### Reason

The agent should assist with autonomous reasoning while preserving human control over consequential actions.

---

# ADR-016 - Channels as Adapters

### Decision

Channels are infrastructure adapters.

```text
CLI
WebChat
Telegram
```

all communicate with the same internal agent runtime. WebChat's approval and event-sink adapters
live in infrastructure; the HTTP entrypoint translates browser requests into application use
cases and exposes the execution stream without embedding agent-runtime logic in the channel.

### Reason

The agent should not contain channel-specific logic.

---

# ADR-017 - Markdown Configuration

### Decision

Agent identity and behavioral configuration should primarily live in Markdown.

### Reason

The goal is to make agent creation accessible without implementing a new Python class.

Example:

```text
agents/new-agent/
├── SOUL.md
├── USER.md
├── MEMORY.md
├── AGENTS.md
└── HEARTBEAT.md
```

---

# ADR-018 - No Vector Database for Initial Memory

### Decision

Do not make a vector database a requirement for MVP memory.

### Reason

The initial requirement is transparency.

Markdown remains the canonical memory.

Semantic indexing can be added later as an optimization.

---

# ADR-019 - Explicit Audit Trail

### Decision

Agent executions produce structured events.

### Reason

Agent systems are difficult to debug without knowing:

* what happened
* which skill was selected
* which tool was called
* what failed
* which approval was requested

---

# ADR-020 - External Actions Are Capabilities

### Decision

An agent does not inherently have the ability to perform an external action.

The capability comes from an authorized tool.

Example:

```text
Agent
  ↓
Permission
  ↓
Tool
  ↓
External Service
```

This keeps identity, reasoning and execution separate.

---

# ADR-021 - Memory History as a Per-Document Journal

### Decision

Record every memory change in an append-only journal next to the document.

```text
agents/<id>/MEMORY.md
agents/<id>/history/MEMORY.jsonl
```

Each entry holds the timestamp, the operation (write, update, archive), the section, and the content before and after.

### Reason

Memory is written by the agents themselves, so its changes must be inspectable (ADR-019).

The journal stays a plain text file: readable, Git-friendly, and separate from the Markdown source of truth (ADR-005).

Update and archive entries store only the affected section, so the journal stays small; a whole-document write stores the whole document.

A change that alters nothing is not recorded.

Restoring a previous version is not part of this decision.

---

# ADR-022 - Stateless Function-Calling Steps for the LLM Adapter

### Decision

At each ReAct step, render the full `DecisionContext` again and force the model to answer with exactly one function call.

```text
tools the agent may use
use_skill
finish
```

No conversation state is kept between steps.

### Reason

The runtime already owns the state (observations, loaded skills), so the adapter must not keep a second copy.

Function calling gives a structured decision without parsing free text.

Stateless steps avoid provider-specific details such as tool-call ids or reasoning passback, which keeps the adapter replaceable (ADR-011).

Tool output is untrusted: it is presented as data, and the runtime policy still decides whether a call runs (ADR-020).


---

# ADR-023 - Delegation Is a Supervisor-Only Tool Over the Existing Run Path

### Decision

A supervisor delegates through two tools, `team.members` and `team.delegate`. `team.delegate` builds a `Task` and runs the member with the same `RunAgent` use case as `openclaw run`.

```text
CEO
 ↓ team.delegate(agent, objective, ...)
DelegateTask   (supervisor? member? skills? depth?)
 ↓
RunAgent → AgentRuntime   (member's own tools, policy, approvals)
 ↓
result (status, answer, error) back to the CEO as an observation
```

The supervisor and the team are never LLM arguments: the supervisor is the caller, the team comes from `OPENCLAW_TEAM` or `team_scope`. Delegation is one level deep. `teams/<id>/team.yaml` is the only place that lists members.

### Reason

The runtime, the policy engine and the approval port already enforce least privilege (ADR-009, ADR-015, ADR-020); running the member through the same path means a delegation cannot widen anything.

A tool keeps the ReAct loop unchanged (ADR-004): the supervisor decides, the application layer checks and executes.

A member's answer is data for the supervisor, like any tool result (ADR-022). A failed member run is a result the supervisor can act on, not an exception that ends its own run.

`team.delegate` has risk level WRITE: the member may have acted, so the runtime must never retry it automatically.

---

# ADR-024 - Execution Trace as Files Derived From Events

### Decision

Every execution writes its own directory under `executions/`:

```text
executions/<start>_<agent>_<id>/
├── events.jsonl
├── execution.json
└── result.md
```

`events.jsonl` is the source. `execution.json` (status, steps, timing, parent and delegated executions) and `result.md` (request and answer, for a human) are derived from the events alone, by an `EventSink`. The runtime does not know about files.

A delegation is a run of its own, so it has its own directory. The link is made by the runtime: `task_started` carries `parent_execution_id`, taken from a context variable that only the runtime sets. The parent's `execution.json` lists its `children`.

### Reason

* the trace answers ADR-019 after the run, which is when it is needed: who asked whom, what was called, what came back
* files need no service, are readable with any tool, and can later feed history and evaluation
* deriving the summaries from the events keeps one source of truth and lets any other sink rebuild them

### Consequences

* the trace is on by default, as ARCHITECTURE section 19 asks; `OPENCLAW_TRACE=off` disables it
* tool inputs and outputs are recorded as they are (long strings are cut), so a trace may hold what the tools read, e-mails included: the directory is created private (0700) and is git-ignored
* recording never stops a run: a write failure is logged once per execution
* `execution.json` says `running` until the final event, so an interrupted run stays visible
* no retention, no rotation, no index: every listing reads the summaries it needs from disk
* the history is read back through the `ExecutionHistory` port (`openclaw runs`, `openclaw show`); the files stay the source, so evaluation can read them the same way

---

# ADR-025 - Code Executor Agent Writes Short Python Scripts That Orchestrate Tools

### Decision

The team gets a Code Executor Agent (`code-executor`). Its system prompt is:

```text
You are an expert in secure code execution.
You write short Python scripts that orchestrate tools.
```

Its skills may include Code Execution, Data Analysis and Testing. Its tools are:

```text
execute_code      (Programmatic Tool Calling)
terminal          (sandboxed)
read / write / patch files
web.search / web.extract   (only when needed)
```

Programmatic Tool Calling (PTC): the agent writes a short Python script that calls its authorized tools, and the output of the script is the observation returned to the agent.

The agent is created like any other agent (ADR-008, ADR-017): profile files, `agent.yaml`, skills, and a tool provider for the capability. It joins the `default` team as a member.

### Reason

* a script can chain several tool calls in one step, so intermediate results do not each need an LLM step
* code execution is an external action, so it comes from an authorized tool and not from the agent itself (ADR-020)
* running the agent through the existing path keeps least privilege and approvals unchanged (ADR-009, ADR-015)

### Consequences

* code and terminal commands run in a sandbox only
* a script reaches tools only through `ToolPort`, with the permissions of the calling agent: it cannot widen them
* executed code has no access to provider credentials
* the script and its output are recorded in the execution trace (ADR-024)
* delegation to the agent uses the existing supervisor path (ADR-023)
* retries follow the existing rule (READ tools only)
* the WebChat lists the agent with no channel code (agents and teams are read from files); its tool listing must show each tool's real risk level and approval requirement, which also corrects the fixed `READ` shown for every agent today
* ARCHITECTURE section 15.2 gives the implementation map, the order of work and the tests expected

### Decisions of the project owner (they replace the open points)

* sandbox mechanism: a subprocess with limits (time, memory, output size), started with network isolation and a scrubbed environment; it refuses to start where isolation is not available
* network access from the sandbox: none
* languages: Python only for now
* how a script calls tools: through a channel on the sandbox's stdin/stdout. A request is one line on stdout, the answer one line on stdin; the calls go through a domain port (`ScriptToolPort`) implemented in the application layer by the same policy, approval and execution path as a direct call, in the execution that started the script. A script cannot call `code.execute`
* teams: `default`; other teams may be created for specific needs
* tool names, risk levels and approvals: proposed by the implementer from the existing conventions, to be confirmed by the project owner:

```text
code.execute      WRITE   approval_required
code.terminal     WRITE   approval_required
code.read_file    READ    allowed
code.write_file   WRITE   allowed (confined to the agent's sandbox directory)
code.patch_file   WRITE   allowed (confined to the agent's sandbox directory)
web.search / web.extract   as declared by the web provider
```

### Implemented

* agent `code-executor`, skills `code/*`, membership of `default`, real permissions in the WebChat listing (CHANGELOG, Phase 10 part 2). Tool names, risk levels and approvals are the ones proposed above; `code.execute` and `code.terminal` require approval

### Decided later: opt-in without isolation (Windows)

* default unchanged: the sandbox refuses to be built where network isolation is not available
* `OPENCLAW_CODE_ALLOW_UNISOLATED=true` lets the same `subprocess` mechanism run anyway where isolation is not available. The child keeps a wall-clock timeout, a capped output, a scrubbed environment, its own working directory and, on Windows, a process-tree kill (`taskkill /T`). It loses the network isolation and, without the `resource` module, the CPU, memory and file-size limits
* consequence, accepted by the project owner for local use: executed code can read any file the user can read (including `.env` and credentials) and use the network. The human approval required on `code.execute` and `code.terminal` is then the main barrier: read each script before approving. A warning is logged at start
* where isolation is available it is always used; the setting changes nothing there
* `code.terminal` uses `cmd.exe /c` on Windows

### Decided later: role label shown by the WebChat

* an optional `role` under `agent:` in `agent.yaml`: a single-line text of 1 to 40 characters, otherwise the agent is refused with a clear error. It is display text only and is never sent to the LLM
* absent: the WebChat shows `AI specialist`, so existing agents keep working
* the same label is the member's `responsibility` in `/api/teams` (the format of `team.yaml` does not change); a member without an agent file shows `AI specialist` and never breaks the listing
* labels: `ceo` Supervisor, `github` GitHub specialist, `linkedin` LinkedIn specialist, `google-email` Email specialist, `google-research` Research specialist, `code-executor` Code execution specialist

### Still open

* none

---

# ADR-026 - Writer / Documentation Agent Prepares Documents as Markdown, LaTeX and PDF

### Decision

The team gets a Writer / Documentation Agent. It writes and edits technical documentation and reports, summarizes sources, and produces the result as Markdown, as LaTeX and as PDF. The PDF is written in LaTeX: the agent writes the `.tex` source and a tool compiles it to PDF.

The agent only prepares documents: it has no tool that publishes, sends or contacts anyone. It is created like any other agent (ADR-008, ADR-017): profile files, `agent.yaml`, skills, and tool providers for its capabilities.

### Reason

* writing a document is kept apart from publishing or sending it: channel agents keep those actions and their approvals (ADR-015)
* a PDF is binary output, so it must come from an authorized tool (ADR-020), not from text written by the LLM
* LaTeX is text: the agent can write it, correct it from a compilation log and keep it as the source of the PDF
* running the agent through the existing path keeps least privilege and approvals unchanged (ADR-009)

### Consequences

* the agent has no publishing or sending tool; when a document must be published or sent, the supervisor asks the channel agent to do it, with that agent's own approvals (ADR-023)
* document tools and LaTeX compilation read and write only in the allowed locations, and refuse any other path
* compiling LaTeX is running code written by an LLM from content that may be untrusted (REQUIREMENTS section 21): shell escape disabled, files limited to the document's directory, no network, time limit, capped log and output, scrubbed environment
* a compilation error goes back to the agent as a log excerpt, so it can correct the source and compile again
* the tool calls and their results are recorded in the execution trace (ADR-024)
* the WebChat lists the agent with no channel code, with its `agent.role` label and each tool's real risk level and approval requirement
* ARCHITECTURE sections 15.3 and 15.4 give the design, the implementation map, the order of work and the tests expected

### Confirmed

* id `writer`, role label `Documentation specialist`
* system prompt (`SOUL.md`): "You are an expert technical writer. You prepare clear, accurate documents and you never publish or send them."
* skills `writing/technical-documentation`, `writing/report-writing`, `writing/editing-proofreading`, `writing/latex-documents`
* member of the `default` team, and of no other team
* output formats: Markdown, LaTeX and PDF only

### Facts checked in the code before the implementation

They framed the open points (the WebChat now has the download route decided below):

* `code.read_file`, `code.write_file` and `code.patch_file` handle text only (200 000 characters at most), are confined to the calling agent's own directory (`<workdir>/<agent-id>`, by default `workspace/sandbox/<agent-id>`), and are offered only when `OPENCLAW_CODE_SANDBOX` is set and the sandbox is usable; otherwise they are skipped and an agent that needs them is refused before any LLM call
* `code.write_file` cannot write a PDF (binary); a `.tex` file is text
* the project has no PDF library and does not require a LaTeX engine; an engine is system software installed on the machine
* `code.terminal` runs a shell command in the sandbox (no network, limits, scrubbed environment) and needs approval; whether an engine installed on the machine can be reached from that sandbox has not been checked
* the WebChat has no route to download a file
* `workspace/shared/` holds `reports/`, `research/` and `context/`

### Decisions (they replace the open points)

* LaTeX engine: `xelatex` by default (accents and non-Latin scripts are written as UTF-8 text), `pdflatex` and `lualatex` selectable with `OPENCLAW_DOCS_LATEX_ENGINE`. It is system software found on the machine, never a Python dependency. Document classes `article` and `report`; packages `fontspec`, `geometry`, `hyperref`, `amsmath`, `graphicx`, `booktabs`, `listings` (changed with `OPENCLAW_DOCS_LATEX_PACKAGES`). The allowlist is a guard rail checked on every `.tex` file of the document's directory, not the security boundary: the boundary is the restrictions below
* where compilation runs: a dedicated tool (`docs.compile_pdf`) with its own restrictions, not the code sandbox and not `code.terminal`. It reuses the sandbox mechanism: shell escape disabled (`-no-shell-escape`, `shell_escape=f`), TeX paranoid file mode (`openin_any=p`, `openout_any=p`: no absolute path, no `..`, no dotfile) so files are limited to the document's directory, a directory that holds a symbolic link is refused, no network (`unshare --net`), wall-clock time limit (60 s by default), CPU, memory and file-size limits, capped output, scrubbed environment. Like the code sandbox it fails closed: without network isolation the tool is not offered. One call is one engine run
* file tools: a dedicated document provider (`docs.*`), independent from the code sandbox; paths are relative to the documents directory and are checked by one shared component (`DocumentFiles`)
* location: `workspace/shared/reports/` for `.md`, `.tex` and PDF (`OPENCLAW_DOCS_DIR`); each LaTeX document in its own subdirectory
* tool names, risk levels, approvals: `docs.read` READ; `docs.write`, `docs.patch` and `docs.compile_pdf` WRITE (compiling writes files and has no external effect); only `docs.compile_pdf` is in `approval_required`
* absence of an engine: `docs.compile_pdf` is not offered by the provider and the reason is reported in `App.skipped`. The writer is not refused: the compile tool is declared optional (see the next decision), the agent runs without it, and delivers the `.tex` source
* optional tools: `tools.optional` in `agent.yaml` is a list of tool names that must also appear in `allowed` or `approval_required` (otherwise the agent is refused with a clear error). It marks a permitted tool whose absence is tolerated: `RunAgent` no longer counts it among the missing tools, so the agent runs when no provider serves it. It adds no permission and changes no approval: a served optional tool is checked by the policy engine exactly like any other, and `docs.compile_pdf` stays in `approval_required`. `ToolPermissions` gets an `optional` set (`is_optional`), `YamlAgentRepository` reads the key. Every other permitted tool that is absent still refuses the agent before any LLM call (ADR-009). Only the `writer` declares `docs.compile_pdf` optional
* what the agent sees without an engine: `ToolResolver` already filters out a tool no provider serves, so the LLM is not offered `docs.compile_pdf`. `AGENTS.md` of the writer tells it to deliver the `.tex` source and to say that the PDF could not be produced on this machine
* download: `GET /api/documents/<relative path>`, authenticated with the WebChat session token, GET only, `.pdf` (`application/pdf`) and `.tex` only, 404 for anything outside the documents directory
* GitHub read tools: no
* teams: `default` only

### Still open

* the WebChat login accepts any e-mail and password and hands out a session token, and no other route checks that token: the download route is authenticated exactly as far as that mechanism is. Keep the server on `127.0.0.1` (the default) until the login is real

---

# ADR-027 - Opt-in LaTeX Compilation Without Network Isolation (Windows)

### Decision

`docs.compile_pdf` needs network isolation (`unshare --net`, Linux). On a machine without it, the tool is not offered (ADR-026) and the writer delivers the `.tex` source. A user who accepts the trade-off can turn on `OPENCLAW_DOCS_ALLOW_UNISOLATED=true`: the compilation then runs without network isolation. Off by default; where isolation is available it is always used and the setting changes nothing.

### Reason

* the project is used on Windows, where `unshare` does not exist and where a PDF is still wanted
* it follows ADR-025, which already lets the code sandbox run unisolated on explicit opt-in, with a human approval on each run
* the compilation stays under `approval_required`: a human approves every run

### Consequences

* what still applies without isolation: shell escape disabled (`-no-shell-escape`, `shell_escape=f`), TeX paranoid file mode (`openin_any=p`, `openout_any=p`), a directory holding a symbolic link refused, the class and package allowlist, the wall-clock limit, capped output, and a scrubbed environment (no credential is passed to the engine)
* what is lost: the engine can use the network (a TeX distribution may download packages) and, on Windows, the CPU, memory and file-size limits do not exist. Confinement to the document's directory relies on TeX's own file mode, not on the operating system
* Windows has no `env` program: the TeX settings are given to the sandbox as extra environment variables of the child (`SubprocessSandbox.run(..., env=...)`), together with a `PATH` that holds the engine's directory and the location variables a TeX distribution needs (`APPDATA`, `LOCALAPPDATA`, `ProgramData`, `ProgramFiles`, `SystemDrive`). No other variable of the parent is passed
* the description of the tool shown to the LLM no longer says "no network" when the compilation is not isolated
* without the opt-in the reason reported in `App.skipped["docs.compile_pdf"]` names the setting

### Not checked

* the Windows path was written and unit-tested with fakes on Linux; it has not been run with a real MiKTeX or TeX Live installation. The behaviour of a distribution that asks before installing a package (a dialog) is not covered: install the packages beforehand

### Still open

* none


---

# ADR-028 - Telegram Channel: Long Polling, Explicit Allow-List, Approvals as Inline Buttons

### Decision

The Telegram channel is an adapter (ADR-016) that receives updates by long polling (`getUpdates`) with `httpx`, and answers in Telegram HTML (converted from the agents' Markdown, with a plain-text fallback).

* access: `TELEGRAM_ALLOWED_USER_IDS` (numeric Telegram ids) is mandatory; without it the channel does not start. Only text messages of these users in private chats are served; everything else is ignored, without an answer, and a warning without the text is logged
* routing: a free text goes to the supervisor of `TELEGRAM_TEAM` (default: `OPENCLAW_TEAM`) through `RunTeam`; `/run <agent> <task>` gives the task to one agent; `/agents` lists them; `/start` and `/help` show the commands
* approvals (ADR-015): each request is a message with Approve / Reject buttons and its own id (the lesson of the WebChat fix: never the execution's id). Only the user who started the task can answer. No answer within `TELEGRAM_APPROVAL_TIMEOUT` seconds (default 300), or a request that cannot be sent, is a rejection: nothing is ever approved by default
* the chat of an approval comes from a context variable set around each run (`chat_scope`), not from a table execution -> chat: it follows the asynchronous call chain, so a delegated agent or a script of the code sandbox (ADR-025) asks the chat that started the task
* one task at a time per chat: a second message gets "a task is already running"; different chats run concurrently. The run is a background task so that the polling loop stays free to receive the button presses
* answers are sent with `parse_mode="HTML"`, cut into messages of at most 4000 characters, with the "typing" indicator while the task runs. The agents write Markdown, which Telegram does not render: `to_telegram_html` (`formatting.py`) converts headings to bold, `**bold**`, `*italic*`, `~~strike~~`, `` `code` ``, fenced code blocks, links, bullet lists and block quotes to the HTML tags Telegram supports, turns tables into `• column: value` lists (no table in Telegram; a grid overflows a phone) and escapes everything else (`&`, `<`, `>`). A code block cut by the message limit is closed and reopened in the next message
* if Telegram refuses a formatted message (`can't parse entities`, for instance a badly nested tag), the same text is sent again as plain text: an answer is never lost to formatting
* `RunTeam` accepts an optional `task_id`, so that the trace of a Telegram task carries the id of the task

### Reason

* long polling needs no public HTTPS address and no new dependency; a webhook can be added as another transport of the same adapter
* the bot can send e-mails, publish and run code: an open bot is not acceptable, hence an allow-list with no default
* the channel keeps ADR-016: the agent core, the domain and the application know nothing about Telegram; the adapter reaches the application through a small `TelegramBackend` protocol implemented by `ChannelBackend` (application) and wired in `bootstrap.py`
* HTML needs only three characters escaped, unlike MarkdownV2 (about twenty, and one omission is a 400 error), so arbitrary agent output is safe; the plain-text fallback covers the remaining nesting errors. This replaces the first version of this ADR, which sent plain text and showed `##`, `**` and `|` as they were written

### Consequences

* new package `infrastructure/channels/telegram/` (`client`, `adapter`, `approval`, `formatting`, `messages`, `bot`), `application/tasks/channel.py`, `build_telegram_bot` in `bootstrap.py`, and the command `openclaw telegram`
* settings: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS`, `TELEGRAM_TEAM`, `TELEGRAM_APPROVAL_TIMEOUT`, `TELEGRAM_API_URL` (see `.env.example`)
* the bot token is part of every request URL: it is never logged and never put in an error message (network errors are re-raised without their cause)
* a network or API failure of the polling loop is retried with a growing delay (or the `retry_after` of Telegram); a rejected token stops the bot
* a task that is still running when the bot stops is cancelled; its pending approval disappears with it

### Not checked

* the channel has been tested against a fake Bot API (`httpx.MockTransport`) and fake ports; it has not been run against the real Telegram servers
* an unknown `TELEGRAM_TEAM` is only reported in the chat, at the first message

### Still open

* group chats, images and voice messages
* a webhook transport

---

# ADR-029 - Documents of the Writer Are Downloadable From the Chat and Listed in a Documents Page

### Decision

The WebChat gives the user the files the writer produced, in two places:

* under the answer of a task in the chat: each file the task produced, with a download button
* a `Documents` page (sidebar, Workspace group): the history of every file of the documents directory, newest first, grouped by document folder; each one can be opened (Markdown and LaTeX are shown as text, a PDF in a viewer) and downloaded

It amends the download decision of ADR-026: Markdown files (`.md`) are downloadable too, next to `.pdf` and `.tex`. The writer's output formats are Markdown, LaTeX and PDF; a Markdown-only result could not be downloaded before.

### Reason

* a task can produce several files (a `.md`, a `.tex` and its PDF): the chat must offer all of them, not only the answer text
* the files stay on disk after the task: the directory is the durable history, the chat is not
* the route and the path check already existed (ADR-026): the same `DocumentFiles` check serves the listing, so a document is listed exactly when it can be downloaded

### Consequences

* `GET /api/documents` lists the documents (401 without a session token, 405 for other methods). Each entry: `path`, `name`, `kind` (`md`, `tex`, `pdf`), `size`, `modified` (ISO, UTC), `folder`, and `taskId`, `agentId`, `taskTitle` when the task that produced it is known (otherwise `null`)
* `GET /api/documents/<path>` also serves `.md` (`text/markdown; charset=utf-8`); everything else about it is unchanged (authenticated, GET only, `attachment`, 404 outside the directory or for any other extension)
* `GET /api/tasks/<id>` has a `documents` list: the files that task produced and that still exist. A file counts when a `docs.write`, `docs.patch` or `docs.compile_pdf` call of the task has a `tool.called` event followed by a `tool.completed` event without error; a successful compilation counts for the `.tex` source and its PDF. It is read from the task events, not guessed from file dates
* the listing skips symbolic links, dotfiles and dot-directories, lists only `.md`, `.tex` and `.pdf` (LaTeX auxiliary files stay out) and stops at 2000 entries
* the task events live in memory (`TaskStore`): after a restart the files are still listed, without the task they came from
* when a supervisor delegates to the writer, the task is the supervisor's task: `agentId` is the agent the user talked to
* the Telegram channel (ADR-028) delivers the files of a task after its answer, see ADR-030

### Still open

* the WebChat login and session token are still the ones described in ADR-026: keep the server on `127.0.0.1` until the login is real

---

# ADR-025 (amendment) - The code-executor no longer asks for approval

### Decision

* `code.execute` and `code.terminal` move from `approval_required` to `allowed` in `agents/code-executor/agent.yaml`, which now declares `approval_required: []`. They are WRITE tools, which the policy engine does not force to approval (only `destructive` and `external_communication` are), so they run at once
* nothing else changes: the sandbox (subprocess, timeout, output cap, scrubbed environment), the confinement of `code.write_file` and `code.patch_file`, the ban on a script calling `code.execute`, and the rule that a script may only use the tools of the agent and through the policy engine

### Reason

* requested by the project owner

### Consequences

* accepted by the project owner for local use: on Windows the sandbox has no network isolation (`OPENCLAW_CODE_ALLOW_UNISOLATED=true`), so a script written by the LLM, or steered by a web page it read, can read any file the user can read (including `.env`) and use the network, and no human sees it before it runs. The sandbox limits (time, output, environment) are then the only barrier. The approval requirement that ADR-025 named as the main barrier is gone
* delegation from the CEO no longer asks anything for code tasks; approvals for other agents (e-mail, GitHub, LinkedIn, writer's PDF compilation) are unchanged
* to bring it back, list the two tools under `approval_required` again


---

# ADR-030 - Telegram Sends the Files a Task Produced

### Decision

After the answer of a task, the Telegram channel sends to the same chat each file the task produced, as a Telegram document (`sendDocument`). It is the same rule as the WebChat (ADR-029): the files are the `.md`, `.tex` and `.pdf` that a `docs.write`, `docs.patch` or `docs.compile_pdf` call of the task wrote without error, read from the events of the task, not guessed from file dates. A successful compilation counts for the `.tex` source and its PDF.

* the files are sent in the order they were produced, after the answer text, also when the task did not complete
* a file that no longer exists, or that the download check refuses, is skipped
* a file above 50 MB (the limit of the Bot API) is not sent: the chat gets one line naming it
* a file that Telegram refuses, or that cannot be read, is named in the chat (`Could not send <name>.`): it stays on disk, and the user must not believe it was delivered. The other files are still sent
* nothing new is asked of the user: no command, no setting

### Reason

* the writer's PDF was only reachable from the WebChat; on Telegram the user got the answer text and no file
* the chat is already the authorized user's private chat (ADR-028): the files go where the answer goes
* the download check is the one of the WebChat route (`DocumentFiles`), so the two channels can never serve a path the other refuses (ADR-026)

### Consequences

* `TelegramClient.send_document` uploads the file as a multipart form; the bot token is never in an error message, as for every call
* `TaskDocuments` (`infrastructure/channels/telegram/documents.py`) is an event sink next to the trace, wired in `build_telegram_bot`. It keeps the paths of each task until the adapter collects them (once); tasks never collected are dropped beyond 200
* a delegated run is a task of its own (`DelegateTask` creates a new task id), but the user asked the task of the supervisor: the first event of a run names its parent execution, and the files are attributed to the task at the root of the chain. The scripts of the code sandbox (ADR-025) run inside their execution and need nothing more
* a result is paired with the call of its own execution, so two runs of one task cannot mix their calls
* the adapter still never imports the application layer: it receives a small `TaskFiles` source from the composition root

### Not checked

* tested against a fake Bot API (`httpx.MockTransport`) like the rest of the channel (ADR-028); not run against the real Telegram servers
* only the files of the documents directory are sent: the files of `code.write_file` (sandbox directory) are not
* the WebChat reads the events of its own task store, which are attributed by the task id of each event: this ADR did not change it, and whether a file written by a delegated writer is listed under the supervisor's task in the WebChat (ADR-029) was not checked

### Still open

* none
