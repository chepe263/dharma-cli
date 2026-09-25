#!/usr/bin/env python3
"""Verifier (capa 1) — plays Kiro Crew's ACP client against the faux backend,
which in turn calls a local stub OpenAI endpoint. Proves the WHOLE loop
(ACP handshake + HTTP call + SSE streaming) offline and free.

Drives the exact handshake Kiro Crew's real client.py performs (confirmed against
the clone at commit 07a05a2): initialize -> session/new -> session/set_mode ->
session/prompt, reading streamed agent_message_chunk notifications + stopReason.

Run:  python3 verify.py
For a real run against Ollama Cloud, set .env and use dharma_cli.py directly
from Kiro Crew instead (see README).
"""
import json
import os
import subprocess
import sys
import time

PROTOCOL_VERSION = "2025-08-22"
AGENT = "kirocrew"
STUB_PORT = 8631


class Fail(Exception):
    pass


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))

    # 1. boot the stub OpenAI endpoint (127.0.0.1 only)
    stub = subprocess.Popen(
        [sys.executable, "stub_openai_server.py", str(STUB_PORT)], cwd=here
    )
    time.sleep(0.6)

    # 2. launch the faux backend pointed at the stub
    env = dict(os.environ)
    env["DHARMA_BASE_URL"] = f"http://127.0.0.1:{STUB_PORT}/v1"
    env["DHARMA_API_KEY"] = "stub-ignored"
    env["DHARMA_MODEL"] = "stub-model"
    proc = subprocess.Popen(
        [sys.executable, "dharma_cli.py", "--agent", AGENT],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
        cwd=here, env=env,
    )

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def read():
        line = proc.stdout.readline()
        if not line:
            raise Fail("backend closed stdout unexpectedly")
        return json.loads(line)

    def await_response(req_id):
        while True:
            msg = read()
            if msg.get("id") == req_id and "method" not in msg:
                return msg

    results = []
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": {"name": "kirocrew", "version": "verifier"},
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
        }})
        r = await_response(1)
        if r.get("result", {}).get("protocolVersion") != PROTOCOL_VERSION:
            raise Fail("initialize: protocolVersion mismatch")
        results.append("✅ initialize: handshake OK")

        send({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {
            "cwd": "/tmp", "mcpServers": [],
        }})
        r = await_response(2)
        sid = r.get("result", {}).get("sessionId")
        mode_ids = [m.get("id") for m in r.get("result", {}).get("modes", {}).get("availableModes", [])]
        if not sid:
            raise Fail("session/new: no sessionId")
        if AGENT not in mode_ids:
            raise Fail(f"session/new: agent not in modes {mode_ids}")
        results.append(f"✅ session/new: sessionId={sid!r}, agent en modes")

        send({"jsonrpc": "2.0", "id": 3, "method": "session/set_mode", "params": {
            "sessionId": sid, "modeId": AGENT,
        }})
        results.append("✅ session/set_mode: enviado")

        send({"jsonrpc": "2.0", "id": 4, "method": "session/prompt", "params": {
            "sessionId": sid, "prompt": [{"type": "text", "text": "corre un comando"}],
        }})
        streamed = []
        saw_tool_call = False
        saw_tool_update = False
        while True:
            msg = read()
            if msg.get("method") == "session/update":
                upd = msg.get("params", {}).get("update", {})
                su = upd.get("sessionUpdate")
                if su == "agent_message_chunk":
                    streamed.append(upd.get("content", {}).get("text", ""))
                elif su == "tool_call":
                    saw_tool_call = True
                elif su == "tool_call_update":
                    saw_tool_update = True
            elif msg.get("id") == 4 and "method" not in msg:
                stop = msg.get("result", {}).get("stopReason", "")
                if stop not in ("end_turn", ""):
                    raise Fail(f"session/prompt: stopReason {stop!r}")
                break
        text = "".join(streamed).strip()
        if not saw_tool_call:
            raise Fail("no se emitió ningún tool_call")
        if not saw_tool_update:
            raise Fail("no se emitió tool_call_update (cierre de la herramienta)")
        if "ERROR" in text:
            raise Fail(f"el backend reportó error → {text!r}")
        if "hola-desde-tool" not in text:
            raise Fail(f"el texto final no refleja el resultado de la herramienta → {text!r}")
        results.append("✅ session/prompt: tool_call + tool_call_update + texto final")
        results.append("✅ function calling: el modelo pidió una herramienta, el faux la ejecutó y realimentó el resultado")
        results.append(f"   respuesta final: {text!r}")

    except Fail as e:
        for r in results:
            print(r)
        print(f"❌ FALLO: {e}")
        return 1
    finally:
        for p in (proc, stub):
            try:
                if p is proc:
                    p.stdin.close()
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                p.kill()

    for r in results:
        print(r)
    print("\n🎉 VIABLE de punta a punta: ACP handshake + turno + llamada HTTP/SSE al endpoint, sin Amazon.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
