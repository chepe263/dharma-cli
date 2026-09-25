#!/usr/bin/env python3
"""Stub OpenAI-compatible endpoint — offline verification, STREAMING + tool calls.

Streams SSE like a real endpoint (matches dharma's _chat_stream). If the request
carries `tools` and no tool result yet, it streams a tool_call for execute_bash;
after seeing the tool result, it streams final text quoting it.
Binds 127.0.0.1 only. Not part of the product.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _sse(self, obj):
        self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
        self.wfile.flush()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length))
        except Exception:
            req = {}
        messages = req.get("messages", [])
        has_tools = bool(req.get("tools"))
        saw_tool_result = any(m.get("role") == "tool" for m in messages)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        if has_tools and not saw_tool_result:
            # stream a tool_call (fragmented, like real endpoints)
            self._sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "type": "function",
                 "function": {"name": "execute_bash", "arguments": ""}}]}}]})
            self._sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "{\"command\":"}}]}}]})
            self._sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": " \"echo hola-desde-tool\"}"}}]}}]})
        else:
            tool_out = next((m.get("content", "") for m in messages if m.get("role") == "tool"), "")
            for word in f"La herramienta devolvió: {tool_out.strip()}".split(" "):
                self._sse({"choices": [{"delta": {"content": word + " "}}]})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8631
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
