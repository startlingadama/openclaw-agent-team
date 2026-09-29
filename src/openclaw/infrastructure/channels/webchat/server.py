"""HTTP and SSE transport for the WebChat channel."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import quote, unquote, urlparse

from openclaw.infrastructure.channels.webchat.api import WebChatAPI

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
_DOCUMENT_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".pdf": "application/pdf",
    ".tex": "text/x-tex; charset=utf-8",
}
log = logging.getLogger(__name__)


class OpenClawHTTPServer:
    """Stdlib HTTP server translating requests to the WebChat API facade."""

    def __init__(
        self,
        api: WebChatAPI,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        self.api = api
        self._sessions: dict[str, dict[str, Any]] = {}
        self._server = ThreadingHTTPServer((host, port), self._handler_factory())
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    def _handler_factory(self):
        api = self.api
        sessions = self._sessions

        class Handler(BaseHTTPRequestHandler):
            def do_OPTIONS(self) -> None:
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers", "Content-Type, Authorization, Accept"
                )
                self.end_headers()

            def do_GET(self) -> None:
                self._dispatch()

            def do_POST(self) -> None:
                self._dispatch()

            def do_PUT(self) -> None:
                self._dispatch()

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _dispatch(self) -> None:
                path = urlparse(self.path).path
                log.info("HTTP %s %s", self.command, path)
                try:
                    if path == "/api/auth/login":
                        body = self._json_body()
                        email = str(body.get("email") or "")
                        password = str(body.get("password") or "")
                        if not email or not password:
                            raise ValueError("email and password are required")
                        token = uuid.uuid4().hex
                        user = {
                            "id": token[:8],
                            "email": email,
                            "name": email.split("@", 1)[0].title(),
                        }
                        sessions[token] = user
                        self._json({"access_token": token, "user": user})
                        return
                    if path == "/api/auth/signup":
                        body = self._json_body()
                        email = str(body.get("email") or "")
                        password = str(body.get("password") or "")
                        name = str(body.get("name") or "")
                        if not email or not password:
                            raise ValueError("email and password are required")
                        token = uuid.uuid4().hex
                        user = {
                            "id": token[:8],
                            "email": email,
                            "name": name or email.split("@", 1)[0].title(),
                        }
                        sessions[token] = user
                        self._json({"access_token": token, "user": user})
                        return
                    if path == "/api/auth/logout":
                        self._json(None)
                        return
                    if path == "/api/auth/me":
                        auth = self.headers.get("Authorization", "").strip()
                        token = auth[7:] if auth.lower().startswith("bearer ") else ""
                        user = sessions.get(token)
                        if user is None:
                            self.send_response(401)
                            self.send_header("Access-Control-Allow-Origin", "*")
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({"detail": "Unauthorized"}).encode())
                            return
                        self._json(user)
                        return
                    if path == "/api/documents":
                        self._document_history()
                        return
                    if path.startswith("/api/documents/"):
                        self._document(unquote(path[len("/api/documents/") :]))
                        return
                    if path == "/api/health":
                        self._json(asyncio.run(api.health()))
                        return
                    if path == "/api/agents":
                        self._json(asyncio.run(api.list_agents()))
                        return
                    if path.startswith("/api/agents/"):
                        self._json(asyncio.run(api.get_agent(path[len("/api/agents/") :])))
                        return
                    if path == "/api/teams":
                        self._json(asyncio.run(api.list_teams()))
                        return
                    if path.startswith("/api/teams/"):
                        self._json(asyncio.run(api.get_team(path[len("/api/teams/") :])))
                        return
                    if path == "/api/tasks":
                        if self.command == "POST":
                            self._json(asyncio.run(api.create_task(self._json_body())))
                        else:
                            self._json(asyncio.run(api.list_tasks()))
                        return
                    if path.startswith("/api/tasks/") and path.endswith("/events"):
                        task_id = path[len("/api/tasks/") : -len("/events")]
                        self._sse(task_id)
                        return
                    if path.startswith("/api/tasks/"):
                        self._json(asyncio.run(api.get_task(path[len("/api/tasks/") :])))
                        return
                    if path == "/api/skills":
                        self._json(asyncio.run(api.list_skills()))
                        return
                    if path.startswith("/api/skills/"):
                        if path.endswith("/instructions"):
                            skill_id = unquote(path[len("/api/skills/") : -len("/instructions")])
                            self._json(asyncio.run(api.get_skill_instructions(skill_id)))
                            return
                        skill_id = unquote(path[len("/api/skills/") :])
                        self._json(asyncio.run(api.get_skill(skill_id)))
                        return
                    if path == "/api/tools":
                        self._json(asyncio.run(api.list_tools()))
                        return
                    if path.startswith("/api/memory/"):
                        reference = path[len("/api/memory/") :]
                        if self.command == "PUT":
                            agent_id, separator, layer = reference.rpartition("/")
                            if not separator:
                                raise ValueError("memory update path must include a layer")
                            body = self._json_body()
                            content = body.get("content")
                            if not isinstance(content, str):
                                raise ValueError("content must be a string")
                            result = api.write_memory(agent_id, layer, content)
                        else:
                            result = api.get_memory(reference)
                        self._json(asyncio.run(result))
                        return
                    if path == "/api/approvals":
                        self._json(asyncio.run(api.list_approvals()))
                        return
                    if path.startswith("/api/approvals/") and path.endswith("/approve"):
                        approval_id = path[len("/api/approvals/") : -len("/approve")]
                        self._json(asyncio.run(api.approve_approval(approval_id)))
                        return
                    if path.startswith("/api/approvals/") and path.endswith("/reject"):
                        approval_id = path[len("/api/approvals/") : -len("/reject")]
                        reason = str(self._json_body().get("reason") or "")
                        self._json(asyncio.run(api.reject_approval(approval_id, reason)))
                        return
                    if path == "/api/chat":
                        body = self._json_body()
                        self._json(
                            asyncio.run(
                                api.chat(
                                    agent_id=str(body.get("agent_id") or "google-research"),
                                    message=str(body.get("message") or ""),
                                )
                            )
                        )
                        return
                    if path == "/api/executions":
                        self._json(asyncio.run(api.list_executions()))
                        return
                    if path.startswith("/api/executions/"):
                        execution_ref = path[len("/api/executions/") :]
                        if execution_ref.endswith("/events"):
                            self._json(
                                asyncio.run(
                                    api.get_execution_events(execution_ref[: -len("/events")])
                                )
                            )
                        else:
                            self._json(asyncio.run(api.get_execution(execution_ref)))
                        return
                    self.send_response(404)
                    self.end_headers()
                except KeyError:
                    self.send_response(404)
                    self.end_headers()
                except ValueError as exc:
                    self.send_response(400)
                    self._json({"detail": str(exc)})
                except Exception as exc:  # pragma: no cover - transport safety net
                    self.send_response(500)
                    self._json({"detail": str(exc)})

            def _authenticated(self) -> bool:
                auth = self.headers.get("Authorization", "").strip()
                token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
                return token in sessions

            def _empty(self, status: int, **headers: str) -> None:
                self.send_response(status)
                self.send_header("Access-Control-Allow-Origin", "*")
                for name, value in headers.items():
                    self.send_header(name.replace("_", "-"), value)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _document_history(self) -> None:
                """Lists the documents of the documents directory, newest first (ADR-029):
                authenticated and GET only, like the download of a document."""
                if self.command != "GET":
                    self._empty(405, Allow="GET")
                    return
                if not self._authenticated():
                    self._empty(401)
                    return
                self._json(asyncio.run(api.list_documents()))

            def _document(self, relative: str) -> None:
                """Downloads a Markdown, LaTeX or PDF file of the documents directory (ADR-026,
                ADR-029): authenticated, GET only, and nothing outside that directory is served."""
                if self.command != "GET":
                    self._empty(405, Allow="GET")
                    return
                if not self._authenticated():
                    self._empty(401)
                    return
                path = api.find_document(relative)
                try:
                    handle = path.open("rb") if path is not None else None
                except OSError:
                    handle = None
                if path is None or handle is None:
                    self._empty(404)
                    return
                with handle:
                    size = path.stat().st_size
                    self.send_response(200)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
                    self.send_header("Content-Type", _DOCUMENT_TYPES[path.suffix.lower()])
                    self.send_header("Content-Length", str(size))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header(
                        "Content-Disposition",
                        f"attachment; filename*=UTF-8''{quote(path.name)}",
                    )
                    self.end_headers()
                    while chunk := handle.read(65536):
                        self.wfile.write(chunk)

            def _json_body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except json.JSONDecodeError:
                    payload = {}
                return payload if isinstance(payload, dict) else {}

            def _json(self, payload: Any) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers", "Content-Type, Authorization, Accept"
                )
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _sse(self, task_id: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                subscriber = api.tasks.subscribe(task_id)
                try:
                    while True:
                        try:
                            event = subscriber.get(timeout=1)
                        except queue.Empty:
                            continue
                        payload = json.dumps(event, ensure_ascii=False)
                        frame = f"event: {event.get('type', 'message')}\ndata: {payload}\n\n"
                        self.wfile.write(frame.encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    api.tasks.unsubscribe(task_id, subscriber)

        return Handler

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
            self._thread = None
