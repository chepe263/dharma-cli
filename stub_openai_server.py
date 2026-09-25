#!/usr/bin/env python3
"""Stub OpenAI-compatible endpoint — offline verification, now with tool calls.

Simulates a model that uses function calling: if the request carries `tools`
and the conversation has no tool result yet, it asks to call `ejecutar_bash`;
once it sees the tool's result (a role=tool message), it returns final text
that quotes the result. Non-streaming JSON (matches the faux's _chat_once).
Binds 127.0.0.1 only. Not part of the product.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length))
        except Exception:
            req = {}
        messages = req.get("messages", [])
        has_tools = bool(req.get("tools"))
        saw_tool_result = any(m.get("role") == "tool" for m in messages)

        if has_tools and not saw_tool_result:
            # first round: ask to run a tool
            msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "execute_bash",
                                 "arguments": json.dumps({"command": "echo hola-desde-tool"})},
                }],
            }
        else:
            # after the tool result: final text quoting it
            tool_out = next((m.get("content", "") for m in messages if m.get("role") == "tool"), "")
            msg = {"role": "assistant",
                   "content": f"La herramienta devolvió: {tool_out.strip()}"}

        resp = {"choices": [{"message": msg, "finish_reason": "stop"}]}
        payload = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8631
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
