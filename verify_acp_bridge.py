#!/usr/bin/env python3
"""Verificación en vivo del puente ACP: simula Kiro Crew contra dharma, que a su
vez lanza `opencode acp`. Usa un modelo gratis de OpenCode Go (sin coste).

  python3 verify_acp_bridge.py
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("DHARMA_OPENCODE_MODEL", "hungerg-free")


class Fail(Exception):
    pass


def main() -> int:
    env = dict(os.environ)
    env["DHARMA_BACKEND"] = "opencode-acp"
    env["DHARMA_OPENCODE_MODEL"] = MODEL
    env["DHARMA_OPENCODE_EXTRA_ARGS"] = ""
    proc = subprocess.Popen(
        [sys.executable, "dharma_cli.py", "--agent", "kirocrew"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=HERE, env=env,
    )

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def read():
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read()[-1500:] if proc.stderr else ""
            raise Fail(f"backend cerró stdout: {err}")
        return json.loads(line)

    def await_response(rid):
        while True:
            msg = read()
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    raise Fail(f"error ACP: {msg['error']}")
                return msg

    results = []
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-08-22",
            "clientInfo": {"name": "kirocrew", "version": "verify"},
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
        }})
        r = await_response(1)
        if r.get("result", {}).get("protocolVersion") != "2025-08-22":
            raise Fail("initialize: dialecto kiro incorrecto")
        results.append("✅ initialize: dialecto kiro -> ACP v1 traducido")

        send({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {
            "cwd": HERE, "mcpServers": [],
        }})
        r = await_response(2)
        sid = r.get("result", {}).get("sessionId")
        if not sid:
            raise Fail("session/new sin sessionId")
        results.append(f"✅ session/new: {sid}")

        send({"jsonrpc": "2.0", "id": 3, "method": "session/prompt", "params": {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": "Responde solo la palabra OK"}],
        }})
        chunks = []
        permissions = 0
        deadline = time.time() + 120
        while time.time() < deadline:
            msg = read()
            if msg.get("method") == "session/request_permission":
                permissions += 1
                options = (msg.get("params") or {}).get("options") or []
                oid = next((o["optionId"] for o in options
                            if o.get("kind") == "allow_once"),
                           options[0]["optionId"] if options else "allow_once")
                send({"jsonrpc": "2.0", "id": msg["id"],
                      "result": {"outcome": {"outcome": "selected", "optionId": oid}}})
            elif msg.get("method") == "session/update":
                upd = (msg.get("params") or {}).get("update") or {}
                if upd.get("sessionUpdate") == "agent_message_chunk":
                    chunks.append((upd.get("content") or {}).get("text", ""))
            elif msg.get("id") == 3 and "method" not in msg:
                stop = msg.get("result", {}).get("stopReason", "")
                if stop not in ("end_turn", ""):
                    raise Fail(f"stopReason={stop}")
                break
        else:
            raise Fail("timeout esperando la respuesta de OpenCode")

        text = "".join(chunks).strip()
        if "OK" not in text.upper():
            raise Fail(f"respuesta inesperada: {text!r}")
        results.append(f"✅ session/prompt: streaming OK ({permissions} aprobaciones)")
        results.append(f"   respuesta: {text!r}")

        # Segundo turno: una herramienta real. OpenCode la ejecuta y sus eventos
        # tool_call / tool_call_update deben llegar a Kiro Crew a través del puente.
        send({"jsonrpc": "2.0", "id": 5, "method": "session/prompt", "params": {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": "Usa la herramienta read para leer /etc/hostname y dime qué contiene"}],
        }})
        saw_tool = False
        saw_tool_done = False
        chunks2 = []
        deadline = time.time() + 120
        while time.time() < deadline:
            msg = read()
            if msg.get("method") == "session/request_permission":
                options = (msg.get("params") or {}).get("options") or []
                oid = next((o["optionId"] for o in options
                            if o.get("kind") == "allow_once"),
                           options[0]["optionId"] if options else "allow_once")
                send({"jsonrpc": "2.0", "id": msg["id"],
                      "result": {"outcome": {"outcome": "selected", "optionId": oid}}})
            elif msg.get("method") == "session/update":
                upd = (msg.get("params") or {}).get("update") or {}
                su = upd.get("sessionUpdate")
                if su == "tool_call":
                    saw_tool = True
                elif su == "tool_call_update" and upd.get("status") == "completed":
                    saw_tool_done = True
                elif su == "agent_message_chunk":
                    chunks2.append((upd.get("content") or {}).get("text", ""))
            elif msg.get("id") == 5 and "method" not in msg:
                break
        else:
            raise Fail("timeout en el turno con herramienta")
        if not saw_tool or not saw_tool_done:
            raise Fail("los eventos tool_call/tool_call_update no llegaron a Kiro Crew")
        results.append("✅ herramientas: tool_call + tool_call_update reenviados")
        results.append(f"   respuesta 2: {''.join(chunks2).strip()[:160]!r}")
    except Fail as e:
        for r in results:
            print(r)
        print(f"❌ FALLO: {e}")
        return 1
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    for r in results:
        print(r)
    print("\n🎉 PUENTE ACP FUNCIONANDO: Kiro Crew -> dharma -> opencode acp -> Go")
    return 0


if __name__ == "__main__":
    sys.exit(main())
