"""Python prelude of every script run by `code.execute`: the `tools` object of the sandbox.

    result = tools.call("web.extract", url="https://example.org")

A call is one request line on stdout and one answer line on stdin (ADR-025). The call is made by
the agent's own permissions and approvals on the other side: a refusal, a rejection or a failure
raises `ToolCallError` here.
"""

PRELUDE = '''\
import json as _json
import sys as _sys


class ToolCallError(Exception):
    """The tool was denied, rejected by the human, or failed."""


class _Tools:
    def call(self, name, **arguments):
        _sys.stdout.flush()
        request = _json.dumps({"tool": name, "arguments": arguments})
        _sys.stdout.write("\\x00OPENCLAW-CALL " + request + "\\n")
        _sys.stdout.flush()
        line = _sys.stdin.readline()
        if not line:
            raise ToolCallError("the tool channel is closed")
        reply = _json.loads(line)
        if not reply.get("ok"):
            raise ToolCallError(reply.get("error", "tool call failed"))
        return reply.get("result")


tools = _Tools()
'''
