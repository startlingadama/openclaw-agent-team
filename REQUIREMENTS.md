# OpenClaw Agent Team - Requirements

## 1. Project Overview

OpenClaw Agent Team is a Python-based multi-agent system that provides a team of specialized AI agents capable of interacting with external services and collaborating to accomplish user-defined tasks.

The initial system provides six core capabilities:

* GitHub Agent
* LinkedIn Agent
* Google Email Agent
* Google Research Agent
* Code Executor Agent
* Writer / Documentation Agent

The system is designed to be extensible. New agents, skills, tools, channels and teams must be addable without modifying the core domain.

The architecture follows:

* Hexagonal Architecture
* Domain-Driven Design
* Agent Skills standard
* Markdown-based agent memory
* ReAct-style agent loops
* Least-privilege tool access
* Explicit multi-agent orchestration
* DeepSeek as the default LLM provider

---

# 2. Goals

## 2.1 Primary Goals

The system MUST:

1. Provide a unified AI agent interface.
2. Allow agents to reason and act through a ReAct-like loop.
3. Support specialized agents.
4. Allow agents to use reusable skills.
5. Allow agents to use external tools.
6. Allow agents to collaborate.
7. Maintain transparent Markdown-based memory.
8. Support persistent agent workspaces.
9. Support multiple communication channels.
10. Support human approval for sensitive operations.
11. Keep agents isolated by role and permissions.
12. Allow new agents to be created primarily through configuration and Markdown files.
13. Allow skills to be added without changing the agent engine.
14. Keep orchestration explicit and debuggable.
15. Support asynchronous and long-running tasks.

---

# 3. Initial Agents

## 3.1 GitHub Agent

Responsibility:

* Search repositories
* Inspect repositories
* Search code
* Inspect issues
* Inspect pull requests
* Create issues when authorized
* Comment on issues when authorized
* Create branches when authorized
* Create pull requests when authorized
* Review pull requests when authorized
* Analyze repository architecture
* Generate development reports

Skills may include:

* Repository Analysis
* Code Search
* Issue Management
* Pull Request Review
* Repository Documentation
* Software Architecture Analysis

---

## 3.2 LinkedIn Agent

Responsibility:

* Search LinkedIn information when technically available through authorized integrations
* Analyze professional profiles/content
* Draft posts
* Draft messages
* Analyze engagement/content
* Prepare prospecting information

The agent MUST distinguish between:

* reading information
* preparing content
* recommending an action
* executing an action

Actions involving publishing, messaging or contacting people SHOULD support human approval.

Skills may include:

* LinkedIn Research
* Content Creation
* Prospect Research
* Lead Qualification
* Social Content Analysis

---

## 3.3 Google Email Agent

Responsibility:

* Search emails
* Read emails
* Summarize email threads
* Classify emails
* Extract action items
* Draft replies
* Send emails when explicitly authorized
* Organize email-related tasks

Sensitive actions such as sending emails MUST support an approval mechanism.

Skills may include:

* Email Search
* Email Summarization
* Email Classification
* Reply Drafting
* Follow-up Management
* Email Triage

---

## 3.4 Google Research Agent

Responsibility:

* Search the web
* Gather information
* Inspect multiple sources
* Extract relevant facts
* Compare sources
* Produce structured research
* Cite sources
* Identify uncertainty
* Generate research reports

Skills may include:

* Web Research
* Source Evaluation
* Competitive Research
* Market Research
* Technical Research
* Literature Research

---

## 3.5 Code Executor Agent

Responsibility:

* Execute code safely, inside a sandbox
* Write short Python scripts that orchestrate tools
* Run commands in a sandboxed terminal
* Read, write and patch files
* Analyse data
* Run and check tests
* Search the web and extract content when needed

System prompt (`SOUL.md`):

```text
You are an expert in secure code execution.
You write short Python scripts that orchestrate tools.
```

Skills may include:

* Code Execution
* Data Analysis
* Testing

Tools:

```text
Code Executor Agent
    ├── execute_code      (Programmatic Tool Calling)
    ├── terminal          (sandboxed)
    ├── read / write / patch files
    └── web.search / web.extract   (only when needed)
```

Programmatic Tool Calling (PTC): instead of one LLM step per tool call, the agent writes a short Python script that calls its authorized tools. The output of the script is the observation returned to the agent.

Constraints:

* Code and terminal commands MUST run in a sandbox
* A script MUST NOT gain any tool or permission that the agent does not already have (section 10)
* Tools called from a script are subject to the same permission and approval rules as direct calls (sections 10 and 20)
* Secrets MUST NOT be exposed to executed code (section 21)
* Approval requirements are declared in `agent.yaml`, like for every other agent

The agent is a member of the `default` team. The supervisor delegates to it like to any other member (section 15.1).

WebChat visibility:

* Agents are discovered from `agents/<id>/agent.yaml` and teams from `teams/<id>/team.yaml`: a new agent appears in the WebChat without channel code once its files exist and, for the team view, once it is listed in `team.yaml`
* The role label shown for the agent is the optional `agent.role` of its `agent.yaml` (`Code execution specialist`)
* The user can talk to the agent directly (the chat takes an agent id) or through the supervisor
* Approvals and execution events reach the WebChat through the existing generic approval and event-sink adapters
* The WebChat MUST show each tool with its real risk level and whether it requires approval. It must not show a fixed value (the current listing shows every tool as `READ`; this applies to all agents and must be corrected as part of this work)
* The role label shown for an agent is currently the same for every agent; changing it is an open point (ADR-025)

Before implementation:

* The open points of ADR-025 MUST be answered by the project owner first
* Anything the specs do not settle MUST NOT be invented: stop and ask

---

## 3.6 Writer / Documentation Agent

Responsibility:

* Write technical documentation (README, ADR, specifications, changelogs, tutorials)
* Write reports
* Edit, proofread and reformulate existing text
* Summarize documents and sources
* Produce the result as Markdown, as LaTeX and as PDF (the PDF is compiled from the LaTeX source)
* Use the web when needed to check or complete a source

Skills may include:

* Technical Documentation
* Report Writing
* Editing and Proofreading
* LaTeX Document Production

Tools:

```text
Writer / Documentation Agent
    ├── read source material
    ├── write / patch documents      (Markdown, LaTeX)
    ├── compile a LaTeX document to PDF
    └── web.search / web.open / web.extract   (only when needed)
```

The exact tools, their names and their permissions are open points (ADR-026).

Output formats:

* Markdown is the working format, like the memory (section 13)
* LaTeX (`.tex`) is the source of every PDF. The agent writes it as text
* PDF is produced by a tool that compiles a LaTeX document. The LLM never writes binary content
* No other format is in scope

Constraints:

* The agent prepares documents. It MUST NOT publish, send or contact anyone: publishing and sending stay with the agent of the channel (for example the LinkedIn Agent and the Google Email Agent) under their own approval rules (section 20)
* Like the LinkedIn Agent (section 3.2), it distinguishes reading a source, preparing a document, recommending an action and executing an action
* Documents are read and written only in the locations allowed to the agent (ADR-026). A path outside them is refused
* Compiling LaTeX is running code written by an LLM from content that may be untrusted. The compilation MUST have shell escape disabled, read and write files only in the document's directory, have no network access, run under a time limit with capped log and output size, and receive a scrubbed environment (section 21). The mechanism is an open point (ADR-026)
* A compilation error is returned to the agent, as an excerpt of the log, so that it can correct the source
* Content read from the web, e-mails or repositories is data, not instructions
* Approval requirements are declared in `agent.yaml`, like for every other agent
* Without a LaTeX engine the agent is not refused: `docs.compile_pdf` is declared under `tools.optional` in `agent.yaml`, so the agent runs without it, delivers the `.tex` source and says that no PDF could be produced. Any other missing tool still refuses the agent before any LLM call

The agent is a member of the `default` team (proposed, ADR-026). The supervisor delegates to it like to any other member (section 15.1).

WebChat visibility:

* The agent appears in the WebChat without channel code once its files exist, and in the team view once it is listed in `team.yaml`. Its role label is the optional `agent.role` of its `agent.yaml`, and each tool is shown with its real risk level and approval requirement
* The user can talk to the agent directly or through the supervisor
* A produced file (Markdown, LaTeX source, PDF) is delivered to the user in the WebChat: downloadable under the answer of the task and listed, with its history, in the Documents page (ADR-029). From the CLI, the answer names the produced files

Before implementation:

* The open points of ADR-026 MUST be answered by the project owner first
* Anything the specs do not settle MUST NOT be invented: stop and ask

---

# 4. Agent Model

Every agent consists of four conceptual layers:

```text
Agent
├── Profile
├── Skills
├── Tools
└── Memory
```

The agent profile defines WHO the agent is.

Skills define HOW the agent performs specialized tasks.

Tools define WHAT the agent can interact with.

Memory defines WHAT the agent remembers.

---

# 5. Agent Profile

Every agent workspace MUST support:

```text
SOUL.md
USER.md
MEMORY.md
AGENTS.md
HEARTBEAT.md
```

## SOUL.md

Defines:

* identity
* role
* personality
* communication style
* behavioral constraints
* responsibilities
* limitations

Example:

```markdown
# Soul

You are the GitHub Agent.

Your role is to help users understand and operate
software repositories.

You prioritize correctness, traceability and safe
repository operations.

Never modify a repository without the required
authorization.
```

---

## USER.md

Contains persistent information about the user relevant to the agent.

Example:

```markdown
# User

Name: ...
Preferences:
...

Current projects:
...
```

The file MUST NOT be used to store secrets.

---

## MEMORY.md

Contains persistent agent memory.

Memory MUST remain:

* human-readable
* editable
* inspectable
* versionable

Example:

```markdown
# Memory

## Important Facts

The user prefers Python for AI projects.

## Previous Tasks

...

## Lessons

...
```

---

## AGENTS.md

Defines operational instructions.

Example:

```markdown
# Agents Instructions

At startup:

1. Load SOUL.md
2. Load USER.md
3. Load relevant memory
4. Inspect available skills
5. Inspect available tools
6. Determine whether human approval is required
```

---

## HEARTBEAT.md

Defines recurring tasks.

Example:

```markdown
# Heartbeat

- Check important GitHub notifications
- Review pending tasks
- Check scheduled workflows
```

Heartbeat execution MUST be optional.

---

# 6. Skills

The system MUST follow the Agent Skills model.

A skill is a self-contained directory:

```text
skills/
└── github/
    └── repository-analysis/
        ├── SKILL.md
        ├── scripts/
        ├── references/
        └── assets/
```

Minimum:

```text
SKILL.md
```

Optional:

```text
scripts/
references/
assets/
```

---

# 7. Skill Loading

Skills MUST support progressive disclosure.

At initialization the agent loads only:

```text
name
description
```

The complete `SKILL.md` is loaded only when the agent determines that the skill is relevant.

This reduces context consumption.

---

# 8. Skill Contract

Every `SKILL.md` MUST contain:

```yaml
---
name: repository-analysis
description: Analyze the architecture and structure of a software repository.
---
```

Then:

```markdown
# Repository Analysis

## Purpose

...

## When to use

...

## Procedure

1. Inspect repository structure.
2. Inspect README.
3. Inspect dependencies.
4. Identify architectural patterns.
5. Produce findings.

## Constraints

...

## Expected Output

...
```

---

# 9. Tools

Tools are independent from agents.

Example:

```text
tools/
├── github/
├── google/
├── linkedin/
├── browser/
└── filesystem/
```

An agent receives only the tools required for its role.

Example:

```text
GitHub Agent
    ├── github.search_repository
    ├── github.search_code
    ├── github.get_issue
    └── github.create_issue

Research Agent
    ├── web.search
    ├── web.open
    └── web.extract
```

Example:

```text
Code Executor Agent
    ├── execute_code
    ├── terminal
    ├── read / write / patch files
    ├── web.search
    └── web.extract
```

Example:

```text
Writer / Documentation Agent
    ├── read source material
    ├── write / patch documents (Markdown, LaTeX)
    ├── compile a LaTeX document to PDF
    └── web.search / web.open / web.extract
```

---

# 10. Least Privilege

Agents MUST NOT automatically receive every available tool.

Tool permissions MUST be explicitly declared.

Example:

```yaml
tools:
  allowed:
    - github.search_repository
    - github.search_code
    - github.get_issue

  approval_required:
    - github.create_issue
    - github.create_pull_request
```

---

# 11. ReAct Agent Loop

The core agent execution model is:

```text
Observation
     ↓
Reasoning
     ↓
Action
     ↓
Observation
     ↓
Reasoning
     ↓
Action
     ↓
...
     ↓
Final Answer
```

The implementation MUST keep this loop explicit.

Conceptually:

```python
while not state.finished:
    observation = observe(state)

    decision = reason(
        observation=observation,
        memory=memory,
        skills=skills,
        tools=tools,
    )

    if decision.requires_approval:
        request_approval(decision)

    elif decision.action:
        result = execute(decision.action)
        state.add_observation(result)

    else:
        state.finish(decision.answer)
```

The exact LLM reasoning output MUST NOT be exposed to the user by default.

The system stores structured execution traces instead.

---

# 12. LLM

Default LLM provider:

```text
DeepSeek
```

The LLM MUST be abstracted behind a domain/application port.

The core system MUST NOT depend directly on DeepSeek.

Example:

```text
LLMPort
   ↑
DeepSeekAdapter
```

This allows future providers:

```text
DeepSeek
OpenAI
Anthropic
Local Models
Ollama
...
```

without changing the domain.

---

# 13. Memory

Memory is Markdown-first.

Primary persistence:

```text
*.md
```

The system MUST support:

* reading memory
* writing memory
* updating memory
* searching memory
* archiving memory
* inspecting memory history

Memory MAY later be indexed for semantic search.

The Markdown files remain the source of truth.

---

# 14. Memory Layers

Memory SHOULD be separated into:

```text
Working Memory
Session Memory
Agent Memory
User Memory
Shared Team Memory
```

Example:

```text
workspace/
├── MEMORY.md
├── USER.md
├── sessions/
├── shared/
└── agents/
```

---

# 15. Agent Teams

The system MUST support multiple orchestration patterns.

## 15.1 Supervisor

```text
                 ┌─────────────┐
                 │     CEO     │
                 │  Supervisor │
                 └──────┬──────┘
                        │
        ┌───────────────┼───────────────┐
        ↓               ↓               ↓
   Marketing         Finance           Ops
```

The supervisor:

* understands the objective
* selects agents
* delegates tasks
* collects results
* resolves conflicts
* produces the final result

---

# 16. Peer-to-Peer Team

Agents MAY communicate directly.

```text
Marketing Agent ←→ Finance Agent
       ↕                  ↕
   Sales Agent ←→ Operations
```

Communication MUST happen through explicit messages.

Agents MUST NOT directly manipulate another agent's memory.

---

# 17. Shared Vault

Agents MAY share a common workspace.

```text
agents/
├── marketing/
├── finance/
├── sales/
└── ops/

shared/
├── MEMORY.md
├── documents/
├── reports/
└── knowledge/
```

Shared memory access MUST be explicit.

---

# 18. Task Model

Every delegated task SHOULD contain:

```yaml
task_id:
from_agent:
to_agent:
objective:
context:
constraints:
required_skills:
required_tools:
deadline:
approval_policy:
```

Example:

```yaml
task_id: research-001
from_agent: ceo
to_agent: research
objective: Analyze competitors.
required_skills:
  - competitive-research
```

---

# 19. Channels

The architecture MUST separate channels from agents.

Initial channels:

```text
CLI
WebChat
Telegram
```

Future channels MAY include:

```text
Slack
Discord
WhatsApp
API
```

A channel translates external communication into an internal message.

```text
Channel
   ↓
Message
   ↓
Agent Runtime
   ↓
Team
   ↓
Response
   ↓
Channel
```

---

# 20. Human-in-the-Loop

The system MUST support approval workflows.

Approval SHOULD be required for actions such as:

* sending emails
* publishing LinkedIn posts
* sending LinkedIn messages
* creating destructive GitHub operations
* deleting resources
* executing high-impact external actions

Example:

```text
Agent
 ↓
Action proposed
 ↓
Approval Required
 ↓
Human
 ↓
Approve / Reject
 ↓
Tool Execution
```

---

# 21. Security

The system MUST implement:

* least privilege
* credential isolation
* tool authorization
* agent isolation
* audit logging
* approval policies
* secret protection
* input validation
* output validation

Secrets MUST NOT be stored in Markdown memory.

---

# 22. Observability

The system SHOULD record:

```text
Task
Agent
Skill
Tool
Input
Output
Duration
Status
Approval
Error
```

The system SHOULD support structured logs.

Example:

```json
{
  "agent": "github",
  "skill": "repository-analysis",
  "tool": "github.search_code",
  "status": "success"
}
```

---

# 23. Error Handling

Failures MUST be explicit.

Categories:

```text
ToolError
LLMError
AuthenticationError
AuthorizationError
ValidationError
SkillError
AgentError
TaskError
ApprovalError
```

The runtime SHOULD support retry policies.

Retries MUST NOT automatically repeat dangerous actions.

---

# 24. Extensibility

Adding a new agent SHOULD require:

```text
agents/
└── new-agent/
    ├── SOUL.md
    ├── USER.md
    ├── MEMORY.md
    ├── AGENTS.md
    └── HEARTBEAT.md
```

Adding a skill SHOULD require only:

```text
skills/
└── new-skill/
    └── SKILL.md
```

Adding a tool requires implementing the appropriate port/adapter.

---

# 25. Non-Goals

The first version MUST NOT attempt to implement:

* autonomous unrestricted internet activity
* unrestricted agent permissions
* automatic financial transactions
* autonomous destructive GitHub operations
* uncontrolled social-media publishing
* opaque vector-only memory
* complex workflow builders
* unnecessary framework abstractions

---

# 26. MVP

The MVP is complete when the system can:

1. Start an agent runtime.
2. Load an agent profile.
3. Discover available skills.
4. Load a relevant skill.
5. Load Markdown memory.
6. Use DeepSeek.
7. Execute tools.
8. Perform a ReAct loop.
9. Delegate a task from one agent to another.
10. Require human approval for sensitive actions.
11. Persist memory.
12. Expose the system through CLI.
13. Execute GitHub research.
14. Execute Google email research.
15. Execute web research.
16. Support the initial LinkedIn agent abstraction.
17. Produce structured execution logs.
