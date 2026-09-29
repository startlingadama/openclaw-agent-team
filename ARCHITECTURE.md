# OpenClaw Agent Team - Architecture

## 1. Architectural Principles

The system follows five major principles:

1. Domain-Driven Design
2. Hexagonal Architecture
3. Agent-native architecture
4. Markdown-first configuration and memory
5. Explicit orchestration

The architecture deliberately avoids making LangGraph, CrewAI or another agent framework the center of the system.

---

# 2. High-Level Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│                         CHANNELS                             │
│                                                              │
│       CLI        WebChat        Telegram        Future        │
└───────────────────────────┬──────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                    APPLICATION LAYER                         │
│                                                              │
│  Commands │ Task Management │ Team Orchestration │ Approval  │
└───────────────────────────┬──────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                       DOMAIN CORE                            │
│                                                              │
│ Agent │ Team │ Task │ Skill │ Memory │ Tool │ Message       │
│                                                              │
│              Agent Runtime / ReAct Loop                     │
└───────────────────────────┬──────────────────────────────────┘
                            │
                ┌───────────┼────────────┐
                ▼           ▼            ▼
          ┌──────────┐ ┌──────────┐ ┌─────────────┐
          │   LLM    │ │ Memory   │ │    Tools    │
          │   Port   │ │   Port   │ │    Ports    │
          └────┬─────┘ └────┬─────┘ └──────┬──────┘
               │            │              │
               ▼            ▼              ▼
          DeepSeek       Markdown       GitHub
                                        Google
                                        LinkedIn
                                        Web
```

---

# 3. Hexagonal Architecture

The application core is independent from infrastructure.

```text
                 ┌──────────────────────────┐
                 │       Infrastructure     │
                 │                          │
                 │ DeepSeek │ GitHub │ Gmail│
                 │ LinkedIn │ Web │ FS      │
                 └────────────┬─────────────┘
                              │
                         Adapters
                              │
                              ▼
                 ┌──────────────────────────┐
                 │       Application       │
                 │                          │
                 │ Use Cases / Services     │
                 └────────────┬─────────────┘
                              │
                              ▼
                 ┌──────────────────────────┐
                 │          Domain          │
                 │                          │
                 │ Agent │ Task │ Team      │
                 │ Skill │ Memory │ Tool    │
                 └──────────────────────────┘
```

The domain MUST NOT import infrastructure implementations.

---

# 4. Project Structure

```text
openclaw-agent-team/
│
├── README.md
├── requirements.md
├── ARCHITECTURE.md
├── DECISIONS.md
├── CHANGELOG.md
├── pyproject.toml
├── .env.example
├── .gitignore
│
├── src/
│   └── openclaw/
│       │
│       ├── domain/
│       │   ├── agents/
│       │   ├── teams/
│       │   ├── tasks/
│       │   ├── skills/
│       │   ├── memory/
│       │   ├── tools/
│       │   ├── messages/
│       │   └── shared/
│       │
│       ├── application/
│       │   ├── agents/
│       │   ├── teams/
│       │   ├── tasks/
│       │   ├── memory/
│       │   ├── approvals/
│       │   └── research/
│       │
│       ├── infrastructure/
│       │   ├── llm/
│       │   │   └── deepseek/
│       │   ├── memory/
│       │   │   └── markdown/
│       │   ├── tools/
│       │   │   ├── code/
│       │   │   ├── github/
│       │   │   ├── google/
│       │   │   ├── linkedin/
│       │   │   └── web/
│       │   ├── channels/
│       │   │   ├── cli/
│       │   │   ├── webchat/
│       │   │   └── telegram/
│       │   └── observability/
│       │
│       └── entrypoints/
│           ├── cli.py
│           └── web.py
│
├── agents/
│   ├── github/
│   │   ├── SOUL.md
│   │   ├── USER.md
│   │   ├── MEMORY.md
│   │   ├── AGENTS.md
│   │   └── HEARTBEAT.md
│   │
│   ├── linkedin/
│   ├── google-email/
│   ├── google-research/
│   ├── code-executor/
│   └── writer/
│
├── teams/
│   ├── default/
│   │   └── team.yaml
│   ├── executive/
│   ├── research/
│   └── growth/
│
├── skills/
│   ├── github/
│   ├── linkedin/
│   ├── google/
│   ├── research/
│   ├── code/
│   └── writing/
│
├── workspace/
│   ├── shared/
│   └── sessions/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
└── docs/
```

---

# 5. Domain Model

Core entities:

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

---

# 6. Agent Aggregate

```text
Agent
├── AgentId
├── Profile
├── Capabilities
├── Skills
├── ToolPermissions
├── MemoryReference
└── Status
```

An agent is not the LLM.

The LLM is an infrastructure dependency used by the agent runtime.

---

# 7. Skill Domain Model

```text
Skill
├── SkillId
├── Name
├── Description
├── Instructions
├── Scripts
├── References
└── Assets
```

The skill loader implements progressive disclosure.

```text
Skill Registry
      │
      ▼
name + description
      │
      ▼
Agent selects skill
      │
      ▼
load SKILL.md
      │
      ▼
execute procedure
```

---

# 8. Tool Domain Model

A tool is represented by:

```text
Tool
├── ToolId
├── Name
├── Description
├── InputSchema
├── OutputSchema
├── Permissions
└── RiskLevel
```

Risk levels:

```text
READ
WRITE
DESTRUCTIVE
EXTERNAL_COMMUNICATION
```

---

# 9. Agent Runtime

The runtime is the central execution component.

```text
AgentRuntime
      │
      ├── ContextBuilder
      ├── SkillResolver
      ├── ToolResolver
      ├── MemoryManager
      ├── LLM
      ├── PolicyEngine
      └── ActionExecutor
```

Execution:

```text
User Request
     ↓
Context Builder
     ↓
Memory
     ↓
Skill Discovery
     ↓
Tool Discovery
     ↓
LLM Decision
     ↓
Policy Engine
     ↓
Approval?
   ↙     ↘
 Yes      No
  ↓        ↓
Human    Execute
  ↓        ↓
  └──→ Observation
          ↓
       Next Loop
```

---

# 10. Orchestration

The first orchestration strategy is Supervisor.

```text
User
 ↓
CEO / Supervisor
 ├── GitHub Agent
 ├── LinkedIn Agent
 ├── Google Email Agent
 └── Research Agent
```

The supervisor does not execute specialist tools directly unless explicitly configured.

It delegates tasks.

---

# 11. Agent-to-Agent Communication

Communication uses messages.

```python
AgentMessage(
    sender="ceo",
    recipient="research",
    task_id="task-123",
    content="Research competitors",
    metadata={},
)
```

This avoids direct coupling.

---

# 12. Shared Memory

Shared memory is physically separate:

```text
workspace/
└── shared/
    ├── MEMORY.md
    ├── research/
    ├── reports/
    └── context/
```

Agents have:

```text
Private Memory
+
Shared Memory
```

Access policies determine which agents can read/write shared content.

---

# 13. Markdown Memory Adapter

The domain sees:

```python
MemoryRepository
```

Infrastructure provides:

```python
MarkdownMemoryRepository
```

Therefore:

```text
Domain
  ↓
MemoryRepository
  ↓
MarkdownMemoryRepository
  ↓
*.md
```

Future adapters can exist:

```text
SQLiteMemoryRepository
PostgresMemoryRepository
VectorMemoryRepository
```

without changing the domain.

---

# 14. LLM Adapter

Domain/application:

```python
LLMPort
```

Infrastructure:

```python
DeepSeekAdapter
```

Potential future implementations:

```text
DeepSeekAdapter
OpenAIAdapter
AnthropicAdapter
OllamaAdapter
```

---

# 15. External Tool Adapters

GitHub:

```text
GitHubPort
    ↓
GitHubAdapter
```

Google:

```text
GooglePort
    ↓
GoogleAdapter
```

LinkedIn:

```text
LinkedInPort
    ↓
LinkedInAdapter
```

Web:

```text
ResearchPort
    ↓
WebResearchAdapter
```

## 15.1 Code Executor

Code execution is served by a tool provider like any other external capability (ADR-020):

```text
ToolProvider (code)
    ├── execute_code      (Programmatic Tool Calling)
    ├── terminal          (sandboxed)
    └── files             (read / write / patch)
```

`web.search` and `web.extract` come from the existing web provider.

Programmatic Tool Calling (PTC): the agent writes a short Python script that calls its authorized tools.

```text
LLM decision: execute_code(script)
      ↓
PolicyEngine → Approval? (as for any tool call)
      ↓
Sandbox runs the script
      ↓ every tool call made by the script
ToolPort → PolicyEngine → Approval? → Tool execution
      ↓
Script output
      ↓
Observation (back to the ReAct loop)
```

Rules:

* the sandbox is the only place where code and terminal commands run
* a script reaches tools only through `ToolPort`, with the permissions of the calling agent: it cannot widen them (ADR-009)
* the sandbox has no access to provider credentials (credential isolation, REQUIREMENTS section 21)
* the script and its output are part of the execution trace (ADR-024)
* the sandbox is a subprocess with limits and no network (ADR-025); a script calls tools through a request/answer channel on its stdin/stdout, served by `ScriptToolBroker` through the domain port `ScriptToolPort`: same policy engine, same approvals (carrying the agent's id), same trace (`via: "script"`)
* a script cannot call `code.execute`
* where network isolation is not available (Windows) the provider is refused, unless `OPENCLAW_CODE_ALLOW_UNISOLATED=true` (ADR-025): the child then runs without network isolation, see the ADR for what stays in force

## 15.2 Code Executor: implementation map

Where each piece goes, following the existing conventions. Nothing here changes the dependency direction (section 21): the domain stays standard-library only and only `bootstrap.py` builds adapters.

| Piece | Place | Notes |
|---|---|---|
| Tool provider | `infrastructure/tools/code/` | implements the `ToolProvider` contract (`tool_names`, `get_spec`, `execute(agent, ...)`); configuration read from the environment; runs code and commands in the sandbox only |
| Registration | `entrypoints/bootstrap.py`, `_tool_providers` | one more `add("code", <needs>, <factory>)`; an absent or invalid provider is reported in `App.skipped`, never fatal; the agent is then refused with `MissingToolsError` before any LLM call |
| Agent | `agents/code-executor/` | `SOUL.md`, `USER.md`, `MEMORY.md`, `AGENTS.md`, `HEARTBEAT.md` and `agent.yaml`; the directory name and `agent.id` must agree; tools declared under `allowed` and `approval_required` (least privilege, REQUIREMENTS section 10) |
| Skills | `skills/code/<name>/SKILL.md` | YAML frontmatter with `name` and `description` and the required sections (REQUIREMENTS section 8); `scripts/`, `references/`, `assets/` |
| Team | `teams/default/team.yaml` | add the member; other teams only if decided |
| WebChat listing | `infrastructure/channels/webchat/resources.py` | show each tool's real risk level and approval requirement instead of a fixed `READ` |
| Settings | `.env.example` | every new setting documented |

Order of work:

1. answer the open points of ADR-025
2. tool provider and its tests
3. registration in `bootstrap.py`
4. agent profile, `agent.yaml` and skills
5. team membership
6. WebChat listing fix
7. documentation (below), lint, full test suite

Tests expected:

* unit: the policy engine asks for approval on the tools declared in `approval_required`; the agent profile and skills load
* integration, on real files in a temporary directory: the provider and the sandbox (limits, confinement, no secrets visible to executed code)
* through `build_app`: delegation from the supervisor, the approval carries the specialist's id, an absent provider gives `MissingToolsError` before any LLM call, every tool the agent may use is served by a provider
* WebChat: `/api/agents` lists `code-executor` with each tool's real permission; `/api/teams` lists it as a member of `default`
* the existing architecture guard tests pass unchanged; `ruff check` and the whole suite are green

Documentation to update when done: CHANGELOG (new phase entry), DECISIONS (ADR-025: open points become decisions), README (status), ARCHITECTURE (sandbox), `.env.example`.

## 15.3 Writer / Documentation Agent

The agent prepares documents. Its capabilities are tools served by providers like any other external capability (ADR-020):

```text
Writer / Documentation Agent
    ├── sources       (files, web, repositories: read only)
    ├── documents     (write / patch: Markdown, LaTeX)
    └── PDF           (LaTeX document -> PDF file, by compilation)
```

```text
LLM decision: write the document (LaTeX source)
      ↓
PolicyEngine → Approval? (as for any tool call)
      ↓
Document tool writes the .tex file in the allowed location
      ↓
LLM decision: compile to PDF
      ↓
PolicyEngine → Approval? (as for any tool call)
      ↓
Compile tool runs the LaTeX engine on the .tex file, under restrictions, and writes the PDF
      ↓
Observation: the produced files, or an excerpt of the log on error
      ↓ (back to the ReAct loop: the agent can correct the source and compile again)
```

Rules:

* the agent has no tool that publishes, sends or contacts anyone; the supervisor asks a channel agent to do that, with that agent's own approvals (ADR-015, ADR-023)
* a PDF is binary output: the compile tool produces it from a LaTeX document, the LLM never writes binary content
* compiling LaTeX is running code written by an LLM: shell escape disabled; reads and writes only in the document's directory (no input of a file outside it); no network; wall-clock time limit; capped log and output; scrubbed environment (credential isolation, REQUIREMENTS section 21). These are the principles of the code sandbox (ADR-025); whether compilation runs in that sandbox or in a dedicated mechanism is an open point of ADR-026
* document tools read and write only in the allowed locations; paths come from an LLM and are never trusted (no `..`, no symbolic link leaving the location)
* the providers are registered by `bootstrap.py` like the others; one that is absent or invalid is reported in `App.skipped` and the agent is refused with `MissingToolsError` before any LLM call, except for a tool the agent declares under `tools.optional` (the writer's `docs.compile_pdf`): the agent then runs without it
* the tool calls and their results are part of the execution trace (ADR-024)
* facts of the current code that frame ADR-026: the `code.*` file tools handle text only, are confined to the calling agent's own directory and are offered only where the code sandbox is available; the WebChat has no route to download a file; the project has no PDF library and does not require a LaTeX engine, which is system software (section 23)

## 15.4 Writer / Documentation Agent: implementation map

Where each piece goes, following the existing conventions. Nothing here changes the dependency direction (section 21): the domain stays standard-library only and only `bootstrap.py` builds adapters.

| Piece | Place | Notes |
|---|---|---|
| Tool provider(s) | `infrastructure/tools/docs/` | document tools and LaTeX compilation; implements the `ToolProvider` contract; configuration read from the environment; confinement to the allowed locations; the LaTeX engine is looked up on the machine and its absence is reported, never fatal |
| Registration | `entrypoints/bootstrap.py`, `_tool_providers` | one more `add(...)`; what it needs to be offered is decided in ADR-026 |
| Agent | `agents/writer/` | `SOUL.md`, `USER.md`, `MEMORY.md`, `AGENTS.md`, `HEARTBEAT.md` and `agent.yaml`; the directory name and `agent.id` must agree; optional `agent.role` (one line, 1 to 40 characters); tools under `allowed` and `approval_required` (least privilege, REQUIREMENTS section 10) |
| Skills | `skills/writing/<name>/SKILL.md` | YAML frontmatter with `name` and `description` and the required sections (REQUIREMENTS section 8); `scripts/`, `references/`, `assets/` |
| Team | `teams/default/team.yaml` | add the member; other teams only if decided |
| WebChat | none for the listing | real permissions and role label are already shown; download route and documents listing (ADR-026, ADR-029) |
| Settings | `.env.example` | every new setting documented (engine, time limit, locations) |

Order of work:

1. answer the open points of ADR-026
2. tool provider(s) and their tests, LaTeX compilation included
3. registration in `bootstrap.py`
4. agent profile, `agent.yaml` and skills
5. team membership
6. download route, only if decided
7. documentation (below), lint, full test suite

Tests expected:

* unit: the agent profile and skills load; the policy engine asks for approval on the tools declared in `approval_required`; the compile command is built with shell escape disabled and a time limit (fake runner, no LaTeX needed)
* integration, on real files in a temporary directory: the document tools (a path outside the allowed locations, `..` and a symbolic link leaving them are refused; a missing source is refused)
* integration with a LaTeX engine, skipped when none is installed: a valid `.tex` gives a non-empty file that starts with `%PDF`; an invalid one returns a log excerpt; a source that tries to run a command or to read a file outside its directory does not succeed; the time limit ends a source that never finishes
* through `build_app`: delegation from the supervisor, the approval carries the specialist's id, an absent provider gives `MissingToolsError` before any LLM call (except the optional compile tool: without an engine the agent runs and is not offered it), every tool the agent may use is served by a provider, and the agent has no publishing or sending tool
* WebChat: `/api/agents` lists the agent with its role label and each tool's real permission; `/api/teams` lists it as a member of `default`; if a download route is decided: authentication is required, a file outside the allowed location gives 404, the content type is `application/pdf`
* the existing architecture guard tests pass unchanged; `ruff check` and the whole suite are green

Documentation to update when done: CHANGELOG (new phase entry), DECISIONS (ADR-026: open points become decisions), README (status), ARCHITECTURE (providers), `.env.example`.

## 15.5 Writer / Documentation Agent: as implemented

```text
infrastructure/tools/docs/
├── files.py      DocumentFiles: the one path check (tools, download route and listing)
├── latex.py      compile command, class and package allowlist, log excerpt
└── provider.py   DocsConfig, DocsToolProvider (docs.read / write / patch / compile_pdf)
```

* `bootstrap.py` registers `DocsToolProvider` in `_tool_providers` without credentials. `docs.compile_pdf` is only served when a LaTeX engine is found and `SubprocessSandbox` can be built (network isolation); otherwise `DocsToolProvider.compile_unavailable` gives the reason, reported in `App.skipped["docs.compile_pdf"]`
* the compilation runs the engine through the code sandbox mechanism, in the directory of the `.tex` file, with `env openin_any=p openout_any=p shell_escape=f <engine> -no-shell-escape -interaction=nonstopmode -halt-on-error -file-line-error <file>` (plus `--nosocket` for LuaLaTeX). Before the run: a symbolic link in the directory, a class or package outside the allowlist, or too many or too large `.tex` files refuse the compilation. A failure returns an excerpt of the `.log` (around the first error) in the tool error
* `build_webchat_api` gives `WebChatAPI` a `DocumentFiles` for the documents directory; `OpenClawHTTPServer` serves `GET /api/documents/<path>` (401 without a session token, 405 for other methods, 404 for anything `DocumentFiles.downloadable` refuses); `GET /api/documents` lists the documents (`DocumentFiles.listing`, 401 without a session token, 405 for other methods) and `GET /api/tasks/<id>` adds the `documents` the task produced (`channels/webchat/documents.py` reads the `docs.*` tool events, ADR-029)
* settings: `OPENCLAW_DOCS_DIR`, `OPENCLAW_DOCS_LATEX_ENGINE`, `OPENCLAW_DOCS_LATEX_TIMEOUT`, `OPENCLAW_DOCS_LATEX_PACKAGES`, `OPENCLAW_DOCS_ALLOW_UNISOLATED` (see `.env.example`)
* without network isolation (ADR-027, explicit opt-in): the same restrictions minus the network and, on Windows, the resource limits. Windows has no `env` program, so `latex.build_command(use_env=False)` omits it and `latex.windows_environment` gives the TeX settings and the engine's `PATH` to `SubprocessSandbox.run(env=...)`
* dependency direction unchanged: the domain is untouched, only `bootstrap.py` builds the adapters

---

# 16. Channels

Channels are adapters.

```text
CLIAdapter
WebChatAdapter
TelegramAdapter
```

All convert external events into:

```text
InboundMessage
```

and responses into:

```text
OutboundMessage
```

The agent core knows nothing about Telegram, HTTP or CLI.

The WebChat channel is split into focused adapters under `infrastructure/channels/webchat`:
HTTP/SSE transport, frontend DTO serialization, active task state, resource queries, and approval
and event-sink ports. `entrypoints/web.py` is only a compatibility factory delegating composition
to `bootstrap.py`. Execution history is read through the application `ReadHistory` use case and
live events are forwarded through the domain `EventSink` port.

Agents and teams are discovered from `agents/<id>/agent.yaml` and `teams/<id>/team.yaml`, so a new agent needs no channel code to appear. The resource queries show each tool with its real risk level and approval requirement, and each agent with the optional `agent.role` label of its `agent.yaml` (`AI specialist` when absent; the label is display text and never reaches the LLM). The same label is the member's responsibility in `/api/teams`. A produced file (a Markdown document, a LaTeX source or a PDF) is served by `GET /api/documents/<relative path>`: it needs a session token, accepts only `.md`, `.tex` and `.pdf` files of the documents directory and answers 404 for anything else (ADR-026, ADR-029, section 15.5). `GET /api/documents` lists them newest first, and a task carries the `documents` it produced, which the frontend shows under the answer in the chat and in its Documents page (ADR-029).

The Telegram channel (ADR-028) lives in `infrastructure/channels/telegram`: `client.py` (Bot API over HTTPS, configuration), `adapter.py` (long-polling loop, routing, `InboundMessage` / `OutboundMessage`), `approval.py` (inline-button approvals) and `formatting.py` (Markdown to Telegram HTML, message splitting) and `documents.py` (the files a task produced, sent after its answer, ADR-030). It never imports the application layer: it talks to a `TelegramBackend` protocol that `application/tasks/channel.py` (`ChannelBackend`: `RunTeam`, `RunAgent`, the agent list) implements and `bootstrap.build_telegram_bot` wires. Only authorized users in private chats are served; a task runs in the background, one per chat; an approval is a message with Approve / Reject buttons whose chat is carried by a context variable of the run (`chat_scope`), so a delegated agent or a script of the sandbox asks the chat that started the task. After the answer, the files of the writer that the task produced (`.md`, `.tex`, `.pdf`, found in its events, checked by `DocumentFiles` like the WebChat download route) are sent to the chat as Telegram documents; the files of a delegated run belong to the task at the root of the delegation chain (ADR-030).

---

# 17. LangChain / LangGraph Position

LangChain/LangGraph are NOT the architectural center.

They MAY be used as infrastructure utilities for:

* model wrappers
* tool schemas
* integrations
* structured outputs
* optional graph execution

The agent runtime remains an explicit application component.

This preserves:

* debuggability
* observability
* control
* low abstraction overhead
* portability
* predictable execution

---

# 18. Why No CrewAI

CrewAI-style abstractions are intentionally avoided.

The project already has explicit concepts:

```text
Agent
Team
Task
Skill
Tool
Memory
Message
```

Introducing another agent abstraction would duplicate the domain model.

---

# 19. Execution Trace

Every execution SHOULD generate:

```text
execution/
├── execution.json
├── events.jsonl
└── result.md
```

Example:

```text
Task Started
    ↓
Agent Selected
    ↓
Skill Loaded
    ↓
Tool Called
    ↓
Tool Result
    ↓
LLM Decision
    ↓
Approval
    ↓
Final Result
```

The trace is written by `FileTraceSink` under `executions/<start>_<agent>_<id>/` (ADR-024):

* `events.jsonl` is the source, one event per line, appended as the run goes
* `execution.json` and `result.md` are derived from the events
* a delegated task is an execution of its own; `parent_execution_id` (child) and `children` (parent) link it to the supervisor's execution

---

# 20. Configuration

Configuration belongs outside Python code whenever possible.

Example:

```yaml
agent:
  id: github
  profile: agents/github
  role: GitHub specialist   # optional display label (WebChat), 1 to 40 characters

skills:
  - github/repository-analysis
  - github/code-search

tools:
  allowed:
    - github.search_repository
    - github.search_code
  approval_required: []   # permitted, each call needs human approval
  optional: []            # permitted tools whose absence is tolerated (the agent runs without them)

llm:
  provider: deepseek
  model: deepseek-chat
```

---

# 21. Dependency Direction

```text
entrypoints
     ↓
application
     ↓
domain

infrastructure
     ↓
implements
     ↓
domain ports
```

Never:

```text
domain → infrastructure
```

---

# 22. Testing Strategy

## Unit Tests

Test:

* domain entities
* policies
* task state transitions
* skill resolution
* memory logic
* permission rules

## Integration Tests

Test:

* DeepSeek adapter
* GitHub adapter
* Google adapter
* Markdown memory
* web research

## End-to-End

Test:

```text
User
 ↓
Agent
 ↓
Skill
 ↓
Tool
 ↓
External Service
 ↓
Response
```

---

# 23. Deployment

Initial deployment:

```text
Python Runtime
      │
      ├── Agent Runtime
      ├── CLI
      ├── Web API
      └── Background Worker
```

Containerization:

```text
Docker
```

Future:

```text
Kubernetes
```

Only when operational complexity justifies it.
