#!/usr/bin/env python3
"""Stub OpenAI-compatible endpoint — for offline verification only.

Serves POST /v1/chat/completions with a streamed SSE reply, exactly like Ollama
Cloud / OpenAI would, so verify.py can exercise the WHOLE path (ACP + HTTP + SSE)
without spending a real API token. Binds to 127.0.0.1 only.

Not part of the product — it stands in for the real LLM during tests.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw)
            user_msg = req["messages"][-1]["content"]
        except Exception:
            user_msg = ""
        reply = f"stub-LLM contestó a: {user_msg.upper()}"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        # stream the reply in a few word-chunks, OpenAI SSE shape
        for word in reply.split(" "):
            chunk = {"choices": [{"delta": {"content": word + " "}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8631
    httpd = HTTPServer(("127.0.0.1", port), Handler)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
