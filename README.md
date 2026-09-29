# OpenClaw Agent Team

A Python-based multi-agent platform inspired by OpenClaw and Hermes, designed around explicit ReAct agents, Markdown-based memory, reusable Agent Skills and specialized agent teams.

---

## Overview

OpenClaw Agent Team allows a user to interact with a team of specialized AI agents through a unified interface.

Initial agents:

```text
GitHub Agent
LinkedIn Agent
Google Email Agent
Google Research Agent
Code Executor Agent
Writer / Documentation Agent
```

The system can later support:

```text
Marketing
Sales
Finance
Operations
HR
Legal
CEO
DevOps
Research
...
```

---

# Architecture

```text
                     USER
                       │
             ┌─────────┴─────────┐
             │      CHANNELS      │
             │ CLI │ WebChat │ TG │
             └─────────┬─────────┘
                       │
                       ▼
                ┌─────────────┐
                │   SUPERVISOR│
                │     / CEO   │
                └──────┬──────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
      GitHub        Research      Email
       Agent          Agent        Agent
          │            │            │
          └────────────┼────────────┘
                       │
                       ▼
                  FINAL RESULT
```

---

# Core Concepts

## Agents

An agent is defined by:

```text
Profile
Skills
Tools
Memory
```

Agent identity is primarily Markdown-based.

Example:

```text
agents/github/
├── SOUL.md
├── USER.md
├── MEMORY.md
├── AGENTS.md
└── HEARTBEAT.md
```

---

## Skills

Skills are reusable capabilities.

```text
skills/
└── github/
    └── repository-analysis/
        ├── SKILL.md
        ├── scripts/
        ├── references/
        └── assets/
```

A skill can be reused by multiple agents.

---

## Tools

Tools provide access to external systems.

```text
GitHub
Google
LinkedIn
Web
Filesystem
Code execution (sandboxed)
Documents (Markdown, LaTeX and PDF)
```

Agents receive only authorized tools.

---

## Code Executor Agent

The Code Executor Agent runs code safely. It writes short Python scripts that orchestrate its tools (Programmatic Tool Calling).

```text
Code Executor Agent
├── execute_code       (Programmatic Tool Calling)
├── terminal           (sandboxed)
├── read / write / patch files
└── web.search / web.extract   (when needed)
```

Skills may include Code Execution, Data Analysis and Testing.

A script cannot widen the agent's permissions: every tool it calls goes through the same permission and approval rules as a direct call.

In the WebChat the agent appears like any other agent, with no channel code, and can be addressed directly or through the supervisor. The tool listing shows each tool's real risk level and approval requirement.

Status: implemented. The agent `code-executor` is a member of the `default` team; `code.execute` and `code.terminal` run without approval (ADR-025). The code tools are only offered when `OPENCLAW_CODE_SANDBOX=subprocess` (see `.env.example`). The label the WebChat shows for an agent is the optional `role` of its `agent.yaml`. On Windows the sandbox is refused unless `OPENCLAW_CODE_ALLOW_UNISOLATED=true` is set (no network isolation: see ADR-025).

---

## Writer / Documentation Agent

The Writer / Documentation Agent prepares documents: technical documentation (README, ADR, specifications, changelogs, tutorials), reports, and edits or summaries of existing text. It produces them as Markdown, as LaTeX and as PDF: the agent writes the LaTeX source and a tool compiles it to PDF.

```text
Writer / Documentation Agent
├── read source material
├── write / patch documents     (Markdown, LaTeX)
├── compile a LaTeX document to PDF
└── web.search / web.open / web.extract   (when needed)
```

Skills may include Technical Documentation, Report Writing, Editing and Proofreading, and LaTeX Document Production.

The agent only prepares documents: it has no tool that publishes, sends or contacts anyone. Publishing and sending stay with the channel agents (LinkedIn, Google Email) and their approvals. Compiling LaTeX is treated as running untrusted code: shell escape disabled, files limited to the document's directory, no network, time limit.

In the WebChat the agent appears like any other agent, with no channel code. The files a task produced (Markdown, LaTeX source, PDF) are offered for download under its answer in the chat, and the Documents page lists every document of the directory, newest first, to open or download. They are served by `GET /api/documents/<path>` and listed by `GET /api/documents` (authenticated, limited to the documents directory, ADR-029). Compiling needs a LaTeX engine installed on the machine (system software, not a Python dependency).

Status: implemented. The agent `writer` (`Documentation specialist`) is a member of the `default` team. Its tools are `docs.read`, `docs.write`, `docs.patch` and `docs.compile_pdf` (the only one that needs approval), plus `web.search`, `web.open`, `web.extract` and `memory.update`. Documents live in `workspace/shared/reports/` (`OPENCLAW_DOCS_DIR`); the engine is XeLaTeX by default (`OPENCLAW_DOCS_LATEX_ENGINE`, see `.env.example`). Without a LaTeX engine, or where network isolation is not permitted (Windows: set `OPENCLAW_DOCS_ALLOW_UNISOLATED=true` to compile anyway, without network isolation, ADR-027), `docs.compile_pdf` is not offered (it is declared `optional` in `agent.yaml`): the agent still runs, delivers the `.tex` source and says that no PDF could be produced on that machine.

---

## Telegram Channel

`uv run openclaw telegram` starts the bot (long polling, no public address needed). Free text goes to the supervisor of the team (`TELEGRAM_TEAM`, default `OPENCLAW_TEAM`); `/run <agent> <task>` gives a task to one agent; `/agents` lists them.

The bot can send e-mails and run code, so `TELEGRAM_ALLOWED_USER_IDS` (numeric Telegram ids) is mandatory and only these users, in private chats, are served. An action that needs approval arrives as a message with Approve / Reject buttons; no answer within `TELEGRAM_APPROVAL_TIMEOUT` seconds is a rejection. One task runs at a time per chat. The files the writer produced during the task (Markdown, LaTeX, PDF) are sent to the chat as documents after the answer (ADR-030). See ADR-028 and `.env.example`.

Status: implemented and tested against a fake Bot API; not yet run against the real Telegram servers. Not done: group chats, images and voice messages.

---

## Memory

Memory uses Markdown as the canonical representation.

```text
MEMORY.md
USER.md
```

This makes agent memory:

* transparent
* editable
* versionable
* inspectable

---

# ReAct Runtime

The core execution loop is intentionally explicit:

```text
Observation
     ↓
Reasoning
     ↓
Action
     ↓
Observation
     ↓
...
     ↓
Final Answer
```

The runtime controls:

* context
* memory
* skills
* tools
* permissions
* approvals
* execution
* errors

---

# LLM

Default provider:

```text
DeepSeek
```

The provider is abstracted behind an application port.

This means the system can later support other models without modifying the domain.

---

# Multi-Agent Teams

The initial team uses a Supervisor architecture.

```text
                   CEO
                    │
       ┌────────────┼────────────┐
       ↓            ↓            ↓
   GitHub       Research       Email
```

Other patterns are supported conceptually:

### Peer-to-Peer

```text
Marketing ↔ Finance
     ↕          ↕
   Sales ↔ Operations
```

### Shared Vault

```text
Agent A ──┐
Agent B ──┼── Shared Memory
Agent C ──┘
```

---

# Project Structure

```text
openclaw-agent-team/
├── src/
├── agents/
├── skills/
├── teams/
├── workspace/
├── tests/
├── docs/
│
├── README.md
├── requirements.md
├── ARCHITECTURE.md
├── DECISIONS.md
└── CHANGELOG.md
```

---

# Technology

Core:

```text
Python
DeepSeek
Markdown
```

Architecture:

```text
DDD
Hexagonal Architecture
ReAct
Agent Skills
```

Optional infrastructure:

```text
LangChain
LangGraph
```

Integrations:

```text
GitHub
Google
LinkedIn
Web
```

Channels:

```text
CLI
WebChat
Telegram
```

---

# Design Philosophy

The project follows a simple principle:

> Agents should be composed from text, skills and capabilities rather than hard-coded classes.

A new agent should ideally require:

```text
SOUL.md
USER.md
MEMORY.md
AGENTS.md
HEARTBEAT.md
```

A new capability should ideally require:

```text
SKILL.md
```

A new external integration requires a tool adapter.

---

# Security

The system follows least privilege.

Agents do not automatically receive access to all tools.

Sensitive actions can require human approval.

Examples:

```text
Send email
Publish LinkedIn content
Send messages
Modify repositories
Delete resources
```

Secrets are never stored in Markdown memory.

---

# Development

Install dependencies:

```bash
uv sync
```

Run the CLI:

```bash
uv run openclaw
```

Run tests:

```bash
uv run pytest
```

Run linting:

```bash
uv run ruff check .
```

---

# Environment

Create:

```text
.env
```

from:

```text
.env.example
```

Required configuration will include the selected LLM provider and credentials for enabled integrations.

The WebChat server reads `OPENCLAW_WEB_HOST` and `OPENCLAW_WEB_PORT` from the process environment;
the CLI loads the project `.env` through `python-dotenv` without overriding variables already set
by the shell. Start it with `uv run openclaw web` (or `uv run openclaw run web`).

The Telegram bot reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS` (mandatory), and optionally `TELEGRAM_TEAM`, `TELEGRAM_APPROVAL_TIMEOUT` and `TELEGRAM_API_URL`. Start it with `uv run openclaw telegram`.

---

# Documentation

Architecture:

```text
ARCHITECTURE.md
```

Requirements:

```text
requirements.md
```

Architecture decisions:

```text
DECISIONS.md
```

Changes:

```text
CHANGELOG.md
```

---

# Roadmap

## Phase 1 - Foundation

* Project bootstrap
* Domain model
* Hexagonal architecture
* Configuration
* Logging

## Phase 2 - Agent Runtime

* ReAct loop
* Context management
* Tool execution
* Skill loading
* Memory

## Phase 3 - Integrations

* GitHub
* Google
* Web research
* LinkedIn
* Code execution (sandboxed)
* Documents (Markdown, LaTeX and PDF)

## Phase 4 - Teams

* Supervisor
* Delegation
* Agent messaging
* Shared memory

## Phase 5 - Channels

* CLI
* WebChat (HTTP/SSE channel with auth, task lifecycle, approvals and live event streaming)
* Telegram (long polling, allow-list, approvals as inline buttons)

## Phase 6 - Production

* Authentication
* Approval policies
* Observability
* Persistent execution history
* Security hardening
* Evaluation framework

---

# License

License to be defined.
