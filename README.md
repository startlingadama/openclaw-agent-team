# OpenClaw Agent Team - Frontend

Web control center for the OpenClaw Agent Team runtime. It lets a user chat with a team of specialized agents, follow tasks live, approve sensitive actions, browse documents produced by the agents, and inspect skills, tools, memory and executions.

The frontend does not run any agent logic: the Python backend is the single source of truth and exposes everything through a REST API and Server-Sent Events.

Backend repository: [startlingadama/openclaw-agent-team](https://github.com/startlingadama/openclaw-agent-team)

---

## Features

| Area | What it does |
|---|---|
| **Chat** | Talk to the team supervisor or directly to one agent; live progress of the task; files produced by the writer offered under the answer |
| **Tasks** | Create, list and follow tasks with their status and events |
| **Approvals** | Approve or reject the sensitive actions agents ask for (send e-mail, publish, create issue, compile PDF...) |
| **Documents** | Every Markdown, LaTeX and PDF file produced by the agents, newest first, to open or download |
| **Agents and Teams** | Agent profiles, roles, tools and skills; team composition |
| **Skills, Tools, Memory** | Skill instructions, the tool catalog with risk levels and approval requirements, agent memory |
| **Activity, Executions, Logs** | Live and historical view of what the agents did |
| **Settings** | Backend URL and connection status |

A command palette searches pages, agents and tasks, and the interface uses a dark theme by default.

---

## Architecture

```text
                                       BROWSER
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                   R O U T E S   ·   T A N S T A C K   R O U T E R                   │
│                                                                                     │
│    ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐    │
│    │  Overview │   │    Chat   │   │   Tasks   │   │ Approvals │   │ Documents │    │
│    └───────────┘   └───────────┘   └───────────┘   └───────────┘   └───────────┘    │
│    ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐    │
│    │   Agents  │   │   Teams   │   │   Skills  │   │   Tools   │   │   Memory  │    │
│    └───────────┘   └───────────┘   └───────────┘   └───────────┘   └───────────┘    │
│            ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐            │
│            │  Activity │   │ Executions│   │    Logs   │   │  Settings │            │
│            └───────────┘   └───────────┘   └───────────┘   └───────────┘            │
│                                                                                     │
└──────────────────────────────────────────┬──────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                      S T A T E                                      │
│                                                                                     │
│  ┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐   │
│  │         Auth         │    │      Chat store      │    │     React Query      │   │
│  │     bearer token     │    │  task + live stream  │    │   cache + polling    │   │
│  └──────────────────────┘    └──────────────────────┘    └──────────────────────┘   │
│                                                                                     │
└──────────────────────────────────────────┬──────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                    A P I   L A Y E R   ·   s r c / l i b / a p i                    │
│                                                                                     │
│  ┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐   │
│  │     REST client      │    │   ExecutionStream    │    │    Query options     │   │
│  │    typed requests    │    │     SSE + retry      │    │      cache keys      │   │
│  └──────────────────────┘    └──────────────────────┘    └──────────────────────┘   │
│                                                                                     │
└──────────────────────────────────────────┬──────────────────────────────────────────┘
                                           │
                                      REST  ·  SSE
                                           ▼
                          ┌─────────────────────────────────┐
                          │          Python backend         │
                          │      http://127.0.0.1:8000      │
                          │       OpenClaw Agent Team       │
                          └─────────────────────────────────┘
```

* Components never call `fetch` directly: every backend call goes through the typed client in `src/lib/api`.
* Execution events arrive over authenticated SSE (`fetch` + `ReadableStream`, so the bearer token can be sent) and are written into the React Query cache by `useExecutionStream`.
* The chat store lives above the router outlet: a task waiting for approval keeps being tracked while you browse other pages, and the conversation is intact when you come back.
* Authentication is owned by the backend (bearer token); the client-side gate only protects the pages.
* Without a reachable backend, pages show the connection error returned by the client. No mock data and no simulated agent results are ever displayed.

---

## Backend API

| Endpoint | Purpose |
|---|---|
| `POST /api/auth/login`, `signup`, `logout` and `GET /api/auth/me` | Authentication |
| `GET /api/health` | Connection status |
| `GET /api/agents`, `/api/agents/{id}` | Agents |
| `GET, POST /api/tasks`, `GET /api/tasks/{id}` | Tasks |
| `GET /api/tasks/{id}/events` | Live task events (SSE) |
| `GET /api/approvals`, `POST /api/approvals/{id}/approve`, `/reject` | Approvals |
| `POST /api/chat` | Chat |
| `GET /api/skills`, `/api/skills/{id}`, `/api/skills/{id}/instructions` | Skills |
| `GET /api/tools` | Tools |
| `GET /api/memory/{agentId}` | Agent memory |
| `GET /api/teams`, `/api/teams/{id}` | Teams |
| `GET /api/executions`, `/api/executions/{id}`, `/api/executions/{id}/events` | Execution history |
| `GET /api/documents`, `/api/documents/{path}` | Documents produced by the agents |

---

## Getting Started

You need Node.js and npm ([install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating)).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```

The frontend talks to the backend at `http://127.0.0.1:8000` by default. Start the backend first (see the [backend repository](https://github.com/startlingadama/openclaw-agent-team) for its setup):

```sh
uv run openclaw web
```

### Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Development server |
| `npm run build` | Production build |
| `npm run preview` | Preview the production build |
| `npm run lint` | ESLint |
| `npm run format` | Prettier |

---

## Configuration

The backend URL is resolved in this order:

1. The URL saved in **Settings** (stored in the browser)
2. `VITE_API_BASE_URL` (build-time, see `.env.example`)
3. `http://127.0.0.1:8000`

Only `http` and `https` URLs are accepted.

```sh
VITE_API_BASE_URL=http://127.0.0.1:8000
```

---

## Project Structure

```text
src/
├── routes/        File-based routes (TanStack Start): chat, tasks, approvals, documents, agents, teams, skills, tools, memory, activity, executions, logs, settings, login
├── components/    App shell, command palette, chat and task dialogs, Markdown rendering, UI primitives (shadcn/ui)
├── lib/
│   ├── api/       Typed REST client, SSE stream, React Query options and hooks
│   ├── auth.tsx   Session and bearer token
│   └── chat-store.tsx   Chat state, task tracking and live events
└── styles.css     Tailwind theme tokens
```

---

## Built With

* TanStack Start and TanStack Router
* TypeScript
* React 19
* TanStack Query
* Tailwind CSS 4 and shadcn/ui (Radix)
* react-markdown with remark-gfm
* Lucide icons, Recharts, Sonner

---

## Author

Developed by **Adama Coulibaly**  
AI Engineer | Finance Enthusiast