#!/usr/bin/env python3
"""Puente ACP: Kiro Crew (dialecto kiro) <-> `opencode acp` (ACP v1).

Kiro Crew cree que está hablando con `kiro-cli acp`; en realidad habla con este
proceso, quetraduce un puñado de métodos y deja que OpenCode maneje la
autenticación, el header x-opencode-session, el historial, las herramientas y las
aprobaciones. Requiere Python 3.8+ y el binario `opencode` en PATH (o
DHARMA_OPENCODE_BIN). Sin API key en dharma.

Métodos traducidos:
  initialize               -> initialize (v1) + respuesta en dialecto kiro
  session/new              -> session/new (cwd + mcpServers)
  session/set_model        -> session/set_config_option (configId=model)
  session/prompt           -> session/prompt (notificaciones se reenvían tal cual)
  session/cancel           -> session/cancel
  session/close            -> session/close
  session/request_permission (del child) -> la misma petición al cliente kiro
Todo lo demás se responde vacío para no bloquear el handshake de Kiro Crew.
"""
import json
import os
import select
import subprocess
import sys

import dharma_cli as d

KIRO_PROTOCOL_VERSION = "2025-08-22"
CHILD_REQUEST_ID = 700000
KIRO_PERMISSION_ID = 800000

# Actualizaciones que Kiro Crew sabe renderizar. El resto (usage_update,
# available_commands_update, config_option_update, ...) se descarta.
FORWARD_UPDATES = {
    "agent_message_chunk",
    "agent_thought_chunk",
    "tool_call",
    "tool_call_update",
    "plan",
}


class _Lines:
    """Lee líneas de un fd sin bloquear, con buffer propio (select + os.read)."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.buf = b""
        self.lines = []
        os.set_blocking(fd, False)

    def pump(self) -> None:
        while True:
            try:
                chunk = os.read(self.fd, 65536)
            except BlockingIOError:
                return
            except OSError:
                return
            if not chunk:
                return
            self.buf += chunk
            while b"\n" in self.buf:
                raw, self.buf = self.buf.split(b"\n", 1)
                if raw.strip():
                    self.lines.append(raw.decode("utf-8", "replace"))

    def pop(self):
        return self.lines.pop(0) if self.lines else None


class Bridge:
    def __init__(self, agent: str) -> None:
        self.agent = agent
        self.child_request_id = CHILD_REQUEST_ID
        self.kiro_permission_id = KIRO_PERMISSION_ID
        self.pending = {}          # child_id -> (kiro_id, kind)
        self.perm_to_child = {}    # kiro_id -> child_id
        self.perm_to_kiro = {}     # child_id -> kiro_id
        self.session_id = ""
        self.init_waiting = None   # kiro_id del initialize
        self.child_env = dict(os.environ)
        # Para que OpenCode pida permiso por herramienta y Kiro Crew muestre la
        # tarjeta de aprobación. El usuario puede traer su propio valor.
        self.child_env.setdefault(
            "OPENCODE_CONFIG_CONTENT", json.dumps({"permission": {"*": "ask"}}))

    # ── envío ───────────────────────────────────────────────────────────────
    def to_kiro(self, obj: dict) -> None:
        d._send(obj)

    def to_child(self, method: str, params: dict, kiro_id=None, kind="") -> int:
        cid = self.child_request_id
        self.child_request_id += 1
        self.pending[cid] = (kiro_id, kind)
        self.child.stdin.write((json.dumps({
            "jsonrpc": "2.0", "id": cid, "method": method, "params": params,
        }) + "\n").encode("utf-8"))
        self.child.stdin.flush()
        return cid

    def reply_kiro(self, kiro_id, result=None, error=None) -> None:
        if kiro_id is None:
            return
        msg = {"jsonrpc": "2.0", "id": kiro_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result if result is not None else {}
        self.to_kiro(msg)

    # ── arrival de mensajes ─────────────────────────────────────────────────
    def on_kiro(self, msg: dict) -> None:
        method = msg.get("method")
        kid = msg.get("id")

        if method is None:  # respuesta de Kiro a una petición nuestra
            if kid in self.perm_to_child:
                cid = self.perm_to_child.pop(kid)
                self.perm_to_kiro.pop(cid, None)
                out = {"jsonrpc": "2.0", "id": cid}
                if "error" in msg:
                    out["error"] = msg["error"]
                else:
                    out["result"] = msg.get("result", {})
                self._child_write(out)
            return

        if method == "initialize":
            self.init_waiting = kid
            self.to_child("initialize", {
                "protocolVersion": 1,
                "clientInfo": {"name": "dharma-bridge", "version": "0.2.0"},
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": True,
                },
            }, kid, "initialize")
            return

        if method == "session/new":
            params = msg.get("params") or {}
            cwd = params.get("cwd") or os.getcwd()
            if not os.path.isabs(str(cwd)):
                cwd = os.getcwd()
            self.to_child("session/new", {
                "cwd": cwd,
                "mcpServers": params.get("mcpServers") or [],
            }, kid, "session/new")
            return

        if method == "session/set_model":
            params = msg.get("params") or {}
            chosen = params.get("modelId") or params.get("model") or ""
            if chosen and chosen != "auto":
                model = d._opencode_model_id(str(chosen))
                d._save_model(str(chosen))
                self.to_child("session/set_config_option", {
                    "sessionId": params.get("sessionId") or self.session_id,
                    "configId": "model", "value": model,
                }, kid, "set_model")
                d._log(f"set_model -> {model}")
            else:
                self.reply_kiro(kid, {})
            return

        if method == "session/prompt":
            self.to_child("session/prompt", msg.get("params") or {}, kid, "prompt")
            return

        if method == "session/cancel":
            self.to_child("session/cancel", msg.get("params") or {}, kid, "cancel")
            return

        if method in ("session/close", "_kiro.dev/session/terminate"):
            sid = (msg.get("params") or {}).get("sessionId") or self.session_id
            if sid:
                self._child_write({"jsonrpc": "2.0", "id": self.child_request_id,
                                   "method": "session/close",
                                   "params": {"sessionId": sid}})
                self.child_request_id += 1
            self.reply_kiro(kid, {})
            return

        if method == "session/set_mode":
            self.reply_kiro(kid, {})
            return

        # extensiones kiro que OpenCode no conoce: acknowledgment
        self.reply_kiro(kid, {})

    def _child_write(self, obj: dict) -> None:
        self.child.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.child.stdin.flush()

    def on_child(self, msg: dict) -> None:
        method = msg.get("method")
        cid = msg.get("id")

        if method == "session/update":
            upd = (msg.get("params") or {}).get("update") or {}
            if upd.get("sessionUpdate") not in FORWARD_UPDATES:
                return
            self.to_kiro({"jsonrpc": "2.0", "method": "session/update",
                          "params": msg.get("params")})
            return

        if method == "session/request_permission":
            kid = self.kiro_permission_id
            self.kiro_permission_id += 1
            self.perm_to_child[kid] = cid
            self.perm_to_kiro[cid] = kid
            self.to_kiro({"jsonrpc": "2.0", "id": kid,
                          "method": "session/request_permission",
                          "params": msg.get("params")})
            return

        if method is not None:
            return  # notificaciones del child que Kiro no necesita

        entry = self.pending.pop(cid, None)
        if not entry:
            return
        kid, kind = entry
        result, error = msg.get("result"), msg.get("error")

        if kind == "initialize":
            self.init_waiting = None
            self.reply_kiro(kid, {
                "protocolVersion": KIRO_PROTOCOL_VERSION,
                "agentCapabilities": {"loadSession": False},
                "agentInfo": {"name": "dharma-cli (opencode acp bridge)",
                              "version": "2.27.0"},
            })
            return

        if kind == "session/new":
            self.session_id = (result or {}).get("sessionId", "")
            d._log(f"opencode acp session: {self.session_id}")
            self.reply_kiro(kid, {
                "sessionId": self.session_id,
                "modes": {
                    "currentModeId": self.agent,
                    "availableModes": [{"id": self.agent, "name": self.agent, "description": ""}],
                },
            })
            # Aplicar el modelo configurado nada más crear la sesión.
            model = d._opencode_model_id(d.ACTIVE_MODEL)
            self.to_child("session/set_config_option", {
                "sessionId": self.session_id, "configId": "model", "value": model,
            }, None, "set_model")
            return

        if kind == "set_model":
            self.reply_kiro(kid, {})
            return

        self.reply_kiro(kid, result, error)


def main() -> int:
    if d._handle_one_shot():
        return 0

    agent = d._agent_from_argv()
    bridge = Bridge(agent)
    cmd = [d.OPENCODE_BIN, "acp"] + d.OPENCODE_EXTRA_ARGS
    d._log(f"starting ACP bridge: {cmd[0]} acp | agent={agent} | model={d.MODEL}")

    try:
        bridge.child = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=bridge.child_env, bufsize=0,
        )
    except FileNotFoundError:
        d._send({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": "dharma-error",
            "update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text", "text":
                                   f"[dharma ERROR] no se encontró {d.OPENCODE_BIN!r} en PATH"}},
        }})
        return 1

    kiro = _Lines(sys.stdin.fileno())
    child = _Lines(bridge.child.stdout.fileno())
    try:
        while True:
            if bridge.child.poll() is not None and not child.lines:
                break
            readable, _, _ = select.select([kiro.fd, child.fd], [], [], 1.0)
            for fd in readable:
                src = kiro if fd == kiro.fd else child
                src.pump()
                while src.lines:
                    line = src.pop()
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if src is kiro:
                        bridge.on_kiro(msg)
                    else:
                        bridge.on_child(msg)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            bridge.child.stdin.close()
        except OSError:
            pass
        bridge.child.terminate()
        try:
            bridge.child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            bridge.child.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
