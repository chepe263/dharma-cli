#!/usr/bin/env python3
"""Verificación en vivo del puente ACP: simula Kiro Crew contra dharma, que a su
vez lanza `opencode acp`. Usa modelos gratis de OpenCode Go (sin coste).

  python3 verify_acp_bridge.py

Comprueba: handshake, streaming, herramientas y que `session/load` restaura la
conversación en un proceso NUEVO (clave para no acumular sesiones basura).
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


def spawn():
    env = dict(os.environ)
    env["DHARMA_BACKEND"] = "opencode-acp"
    env["DHARMA_OPENCODE_MODEL"] = MODEL
    env["DHARMA_OPENCODE_EXTRA_ARGS"] = ""
    return subprocess.Popen(
        [sys.executable, "dharma_cli.py", "--agent", "kirocrew"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=HERE, env=env,
    )


class Client:
    def __init__(self, proc):
        self.proc = proc

    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def read(self):
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read()[-1500:] if self.proc.stderr else ""
            raise Fail(f"backend cerró stdout: {err}")
        return json.loads(line)

    def await_response(self, rid):
        while True:
            msg = self.read()
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    raise Fail(f"error ACP: {msg['error']}")
                return msg

    def prompt(self, rid, sid, text, deadline_secs=150):
        self.send({"jsonrpc": "2.0", "id": rid, "method": "session/prompt",
                   "params": {"sessionId": sid,
                              "prompt": [{"type": "text", "text": text}]}})
        chunks, tools, permissions = [], set(), 0
        deadline = time.time() + deadline_secs
        while time.time() < deadline:
            msg = self.read()
            if msg.get("method") == "session/request_permission":
                permissions += 1
                options = (msg.get("params") or {}).get("options") or []
                oid = next((o["optionId"] for o in options
                            if o.get("kind") == "allow_once"),
                           options[0]["optionId"] if options else "allow_once")
                self.send({"jsonrpc": "2.0", "id": msg["id"],
                           "result": {"outcome": {"outcome": "selected", "optionId": oid}}})
            elif msg.get("method") == "session/update":
                upd = (msg.get("params") or {}).get("update") or {}
                su = upd.get("sessionUpdate")
                if su == "agent_message_chunk":
                    chunks.append((upd.get("content") or {}).get("text", ""))
                elif su in ("tool_call", "tool_call_update"):
                    tools.add(su)
            elif msg.get("id") == rid and "method" not in msg:
                stop = msg.get("result", {}).get("stopReason", "")
                if stop not in ("end_turn", ""):
                    raise Fail(f"stopReason={stop}")
                return "".join(chunks).strip(), tools, permissions
        raise Fail("timeout esperando la respuesta de OpenCode")

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def handshake(c, sid=None):
    c.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-08-22",
        "clientInfo": {"name": "kirocrew", "version": "verify"},
        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                               "terminal": False},
    }})
    r = c.await_response(1)
    result = r.get("result", {})
    if result.get("protocolVersion") != "2025-08-22":
        raise Fail("initialize: dialecto kiro incorrecto")
    if not result.get("agentCapabilities", {}).get("loadSession"):
        raise Fail("initialize: loadSession no está anunciado")
    if sid is None:
        c.send({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {
            "cwd": HERE, "mcpServers": [],
        }})
        sid = c.await_response(2).get("result", {}).get("sessionId")
        if not sid:
            raise Fail("session/new sin sessionId")
    else:
        c.send({"jsonrpc": "2.0", "id": 2, "method": "session/load", "params": {
            "sessionId": sid, "cwd": HERE, "mcpServers": [],
        }})
        r = c.await_response(2)
        if "error" in r or not r.get("result", {}).get("sessionId"):
            raise Fail(f"session/load falló: {r}")
    return sid


def main() -> int:
    results = []
    proc = spawn()
    c = Client(proc)
    try:
        sid = handshake(c)
        results.append(f"✅ initialize + session/new (loadSession=True): {sid}")

        text, _, _ = c.prompt(3, sid, "Recuerda la palabra ZANAHORIA y responde solo OK")
        if "OK" not in text.upper():
            raise Fail(f"respuesta inesperada: {text!r}")
        results.append(f"✅ session/prompt: {text!r}")

        text, tools, perms = c.prompt(
            5, sid, "Usa la herramienta read para leer /etc/hostname y dime qué contiene")
        if "tool_call" not in tools or "tool_call_update" not in tools:
            raise Fail("los eventos de herramienta no llegaron a Kiro Crew")
        results.append(f"✅ herramientas reenviadas ({perms} aprobaciones): {text[:120]!r}")
    finally:
        c.close()

    # Proceso NUEVO: session/load debe recuperar la misma conversación.
    proc2 = spawn()
    c2 = Client(proc2)
    try:
        sid2 = handshake(c2, sid=sid)
        if sid2 != sid:
            raise Fail(f"session/load devolvió otro id: {sid2!r} != {sid!r}")
        text, _, _ = c2.prompt(3, sid2, "¿Qué palabra te dije que recordaras? Responde solo la palabra")
        if "ZANAHORIA" not in text.upper():
            raise Fail(f"session/load no conservó el contexto: {text!r}")
        results.append(f"✅ session/load en proceso nuevo conserva el contexto: {text!r}")
    finally:
        c2.close()

    for r in results:
        print(r)
    print("\n🎉 PUENTE ACP FUNCIONANDO: Kiro Crew -> dharma -> opencode acp -> Go")
    return 0


if __name__ == "__main__":
    sys.exit(main())
