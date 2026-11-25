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
import hashlib
import json
import os
import select
import subprocess
import sys
import time

import dharma_cli as d

KIRO_PROTOCOL_VERSION = "2025-08-22"
# Registro de las sesiones de OpenCode que crea este puente. Solo se usa para
# limpiar las viejas: nunca toca sesiones creadas por el usuario en OpenCode.
SESSION_REGISTRY = os.path.expanduser("~/.kiro/crew/dharma_opencode_sessions.json")
# Un workspace (cwd) = UNA sesión de OpenCode. Kiro Crew crea muchas sesiones
# efímeras (título, sugerencias, tareas); todas comparten la misma conversación
# de OpenCode. El trabajo paralelo se hace con subagentes (task) dentro de ella.
SESSION_MAP = os.path.expanduser("~/.kiro/crew/dharma_session_map.json")
# Volcado diagnóstico de las tramas ACP (DHARMA_ACP_DUMP=ruta). Se usa para
# descubrir qué identificador estable envía Kiro Crew en session/new. Nunca
# subir este archivo a git: puede contener prompts del usuario.
DUMP_PATH = os.environ.get("DHARMA_ACP_DUMP", "").strip()
_REDACT_KEYS = ("token", "secret", "password", "api_key", "apikey", "credential", "cookie")


def _mask(value) -> str:
    """Huella corta en vez del secreto: permite comparar si dos tramas traen
    la misma credencial sin escribirla en el dump."""
    return "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _redact(value):
    if isinstance(value, dict):
        # env entries are {"name": "...TOKEN...", "value": "..."}: the secret
        # lives in `value`, so judge the sibling `name` too.
        named = str(value.get("name", "")).lower()
        named_secret = any(s in named for s in _REDACT_KEYS)
        return {
            k: (_mask(v)
                if (any(s in str(k).lower() for s in _REDACT_KEYS)
                    or (named_secret and str(k).lower() == "value"))
                else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value[:80]]
    if isinstance(value, str):
        return value[:3000]
    return value


def _dump(direction: str, msg: dict) -> None:
    if not DUMP_PATH:
        return
    try:
        line = json.dumps({"ts": time.time(), "dir": direction, "msg": _redact(msg)},
                          ensure_ascii=False) + "\n"
        fd = os.open(DUMP_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError):
        pass
CHILD_REQUEST_ID = 700000
KIRO_PERMISSION_ID = 800000
# Métodos de Kiro que se pueden acknowledgear vacío SIN ocultar una función:
# son extensiones del dialecto kiro que el harness ACP no necesita ejecutar.
# Cualquier OTRO método se responde -32601 (fail closed): responder {} a un
# método no soportado fue exactamente lo que hizo invisible el steering.
EMPTY_OK_METHODS = {
    "session/set_mode",
    "_kiro.dev/commands/execute",
}

# Actualizaciones que Kiro Crew sabe renderizar. El resto (usage_update,
# available_commands_update, config_option_update, ...) se descarta.
FORWARD_UPDATES = {
    "agent_message_chunk",
    "agent_thought_chunk",
    "tool_call",
    "tool_call_update",
    "plan",
}


def _registry_read() -> list:
    try:
        with open(SESSION_REGISTRY, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _registry_add(session_id: str) -> None:
    """Anotar una sesión NUEVA del puente para poder limpiarla después."""
    rows = [r for r in _registry_read() if r.get("id") != session_id]
    rows.append({"id": session_id, "created": time.time()})
    try:
        os.makedirs(os.path.dirname(SESSION_REGISTRY), exist_ok=True)
        with open(SESSION_REGISTRY, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
    except OSError as exc:  # noqa: BLE001
        d._log(f"no pude registrar la sesión para limpieza: {exc}")


def _map_read() -> dict:
    try:
        with open(SESSION_MAP, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _map_get(key: str) -> str:
    sid = _map_read().get(key, "")
    return sid if isinstance(sid, str) else ""


def _map_set(key: str, session_id: str) -> None:
    data = _map_read()
    data[key] = session_id
    try:
        os.makedirs(os.path.dirname(SESSION_MAP), exist_ok=True)
        with open(SESSION_MAP, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError as exc:  # noqa: BLE001
        d._log(f"no pude persistir el mapa de sesiones: {exc}")


def _session_key(params: dict, cwd: str) -> str:
    """Clave estable para agrupar las sesiones efímeras de Kiro Crew.

    El `sid=chat-<n>-<ts>` de la URL del dashboard ES el session key de Kiro.
    Llega al backend de dos formas:
      * `KIROCREW_SESSION_KEY` en el env del hijo (ruta AcpClient, 1 proc/sesión);
      * `KIROCREW_SESSION_LABEL` dentro de `mcpServers[].env` (ruta AcpRuntime,
        la que usa este gateway), gracias al opt-in `x-kirocrew-session-label`
        en el spec del agente.
    Ni el `KIROCREW_STUB_SESSION_TOKEN` (cambia por proceso) ni el `modeId`
    (identifica el agente) sirven: se descartaron con el volcado ACP.
    """
    meta = params.get("_meta") or {}
    kiro_meta = meta.get("kiro") or {}
    sid = str(kiro_meta.get("sessionKey") or "").strip()
    if sid:
        return "chat-" + sid
    env_key = os.environ.get("KIROCREW_SESSION_KEY", "").strip()
    if env_key:
        return "chat-" + env_key
    for server in params.get("mcpServers") or []:
        for entry in server.get("env") or []:
            name = str(entry.get("name", ""))
            if "SESSION_LABEL" in name.upper():
                value = str(entry.get("value", "")).strip()
                if value:
                    return "chat-" + value
    return "cwd-" + hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:24]


def _verify_permission_posture(bridge: "Bridge") -> None:
    """Copiado del adaptador `opencode` de Kiro Crew: sembrar `ask` no basta,
    hay que leer la configuración RESUELTA y comprobar que entró en vigor.
    Si no, las tools de OpenCode correrían sin pasar por la UI de Kiro.
    """
    try:
        proc = subprocess.run(
            [d.OPENCODE_BIN, "debug", "config"], env=bridge.child_env,
            cwd=os.getcwd(), capture_output=True, text=True, timeout=30,
        )
        out = proc.stdout or ""
        start = out.find("{")
        if proc.returncode != 0 or start < 0:
            raise ValueError(f"exit {proc.returncode}, sin JSON")
        raw = (json.loads(out[start:]) or {}).get("permission")
        if isinstance(raw, str):
            ok = raw == "ask"
        elif isinstance(raw, dict) and raw:
            values = {v for v in raw.values() if isinstance(v, str)}
            ok = values == {"ask"}
        else:
            ok = False
        _dump("diag", {"permission_readback": "ask" if ok else str(raw)[:200]})
        if not ok:
            d._log("WARNING: OpenCode no resolvió permission=ask; las herramientas "
                   "pueden ejecutarse sin aprobación de Kiro. Revisa OPENCODE_CONFIG_CONTENT")
        else:
            d._log("permission read-back: ask confirmado")
    except Exception as exc:  # noqa: BLE001
        d._log(f"WARNING: no pude verificar permission con `opencode debug config`: {exc}")


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
        self.steer_text = {}       # child_id -> texto del steer (para el echo)
        self.active_prompts = set()  # child_ids de prompts en curso
        self.steer_queue = []        # steers esperando a que el turno termine
        self.session_id = ""
        self.last_new_params = {}  # para reintentar session/new si un load falla
        self.pending_key = ""      # clave (chat) del session/new en curso
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

    def _reply_session(self, kiro_id) -> None:
        """Respuesta común a session/new y session/load en dialecto kiro."""
        self.reply_kiro(kiro_id, {
            "sessionId": self.session_id,
            "modes": {
                "currentModeId": self.agent,
                "availableModes": [{"id": self.agent, "name": self.agent, "description": ""}],
            },
        })

    def _apply_model(self) -> None:
        """Fijar el modelo elegido también al restaurar una sesión."""
        model = d._opencode_model_id(d.ACTIVE_MODEL)
        self.to_child("session/set_config_option", {
            "sessionId": self.session_id, "configId": "model", "value": model,
        }, None, "set_model")

    def _send_steer(self, sid: str, text: str) -> None:
        """Meter un steer en OpenCode. Si hay un prompt activo, se encola:
        OpenCode rechaza un segundo session/prompt mientras el primero corre
        ('Session already has an active ACP prompt'). Es el mismo criterio
        que _steer_via_concurrent_prompt del adaptador opencode de Kiro Crew.
        """
        if not text:
            return
        if self.active_prompts:
            self.steer_queue.append((sid, text))
            d._log(f"steer encolado ({len(text)} chars, turno activo)")
            return
        cid = self.to_child("session/prompt", {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": text}],
        }, None, "steer_prompt")
        self.active_prompts.add(cid)
        self.steer_text[cid] = text
        d._log(f"steer -> OpenCode ({len(text)} chars)")

    def _drain_steers(self) -> None:
        if self.active_prompts or not self.steer_queue:
            return
        sid, text = self.steer_queue.pop(0)
        self._send_steer(sid, text)

    def _steer_update(self, sid: str, kind: str, text: str) -> None:
        msg = {"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": sid,
            "update": {"sessionUpdate": kind, "content": text},
        }}
        _dump("dharma->kiro", msg)
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
            self.last_new_params = {"cwd": cwd, "mcpServers": params.get("mcpServers") or []}
            self.pending_key = _session_key(params, cwd)
            existing = _map_get(self.pending_key)
            if existing:
                # Mismo chat de Kiro -> misma conversación de OpenCode, aunque
                # Kiro abra muchas sesiones ACP efímeras para ese chat.
                self.session_id = existing
                d._log(f"session/new [{self.pending_key}] -> reutiliza {existing}")
                self.to_child("session/load", {"sessionId": existing,
                                                **self.last_new_params}, kid, "load")
            else:
                self.to_child("session/new", dict(self.last_new_params), kid, "session/new_first")
            return

        if method == "session/load":
            params = msg.get("params") or {}
            cwd = params.get("cwd") or os.getcwd()
            if not os.path.isabs(str(cwd)):
                cwd = os.getcwd()
            self.session_id = params.get("sessionId") or ""
            self.last_new_params = {"cwd": cwd, "mcpServers": params.get("mcpServers") or []}
            self.pending_key = _session_key(params, cwd)
            self.to_child("session/load", {
                "sessionId": self.session_id,
                **self.last_new_params,
            }, kid, "load")
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
            cid = self.to_child("session/prompt", msg.get("params") or {}, kid, "prompt")
            self.active_prompts.add(cid)
            return

        # Kiro Crew steering (kiro-cli `_session/steer`): responde que encoló,
        # avisa al cliente y mete el texto en el turno en curso de OpenCode como
        # un SEGUNDO session/prompt (es el mecanismo de steering de OpenCode).
        if method == "_session/steer":
            params = msg.get("params") or {}
            text = str(params.get("message") or "").strip()
            if text.startswith("<user_message>") and text.endswith("</user_message>"):
                text = text[len("<user_message>"):-len("</user_message>")].strip()
            sid = params.get("sessionId") or self.session_id
            self.reply_kiro(kid, {"queued": True})
            self._steer_update(sid, "steering_queued", text)
            self._send_steer(sid, text)
            return

        if method == "_session/steer/clear":
            self.reply_kiro(kid, {})
            self._steer_update((msg.get("params") or {}).get("sessionId") or self.session_id,
                               "steering_cleared", "")
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

        # Fail closed: si Kiro inventa un método nuevo, un {} lo haría creer
        # que funcionó (y dejaría el chat colgado esperando una señal).
        if method in EMPTY_OK_METHODS:
            self.reply_kiro(kid, {})
        else:
            d._log(f"método ACP no soportado por el puente: {method}")
            self.reply_kiro(kid, error={"code": -32601,
                                        "message": f"Method not found: {method}"})

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

        if kind == "steer_prompt":
            self.active_prompts.discard(cid)
            text = self.steer_text.pop(cid, "")
            if error:
                d._log(f"steer rechazado por OpenCode: {error}")
                # Un rechazo por turno activo se reintenta cuando éste cierre.
                if "active ACP prompt" in json.dumps(error):
                    self.steer_queue.append((self.session_id, text))
            else:
                # Señal autoritativa para Kiro: el steer fue consumido.
                self._steer_update(self.session_id, "steering_consumed", text)
            self._drain_steers()
            return

        if kind == "prompt":
            self.active_prompts.discard(cid)
            self.reply_kiro(kid, result, error)
            self._drain_steers()
            return

        if kind == "initialize":
            self.init_waiting = None
            self.reply_kiro(kid, {
                "protocolVersion": KIRO_PROTOCOL_VERSION,
                # Kiro Crew reutiliza las conversaciones con session/load en vez
                # de crear una sesión nueva en cada arranque/worker efímero.
                "agentCapabilities": {"loadSession": True},
                "agentInfo": {"name": "dharma-cli (opencode acp bridge)",
                              "version": "2.27.0"},
            })
            return

        if kind in ("session/new", "session/new_first", "load_retry"):
            self.session_id = (result or {}).get("sessionId", "")
            if kind in ("session/new", "session/new_first"):
                _registry_add(self.session_id)  # solo las nuevas, no las cargadas
            if self.pending_key:
                _map_set(self.pending_key, self.session_id)
            d._log(f"opencode acp session ({kind}): {self.session_id}")
            self._reply_session(kid)
            self._apply_model()
            return

        if kind == "load":
            if error:
                d._log(f"session/load falló, creando una nueva: {error}")
                self.to_child("session/new", dict(self.last_new_params), kid, "load_retry")
                return
            self._reply_session(kid)
            self._apply_model()
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
    # Diagnóstico: ¿qué marca de sesión trae el spawn de Kiro Crew?
    env_marks = {}
    for name in ("KIROCREW_SESSION_KEY", "KIROCREW_CHANNEL_ID", "KIROCREW_SLACK_TEAM_ID",
                 "KIRO_CLI_CLIENT_APPLICATION", "KIROCREW_SPAWNED", "KIROCREW_RUNTIME_AGENT"):
        value = os.environ.get(name, "")
        env_marks[name] = _mask(value) if value and "KEY" in name else value
    _dump("spawn-env", {"argv": sys.argv[1:], "env": env_marks})

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

    _verify_permission_posture(bridge)

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
                        _dump("kiro->dharma", msg)
                        bridge.on_kiro(msg)
                    else:
                        _dump("opencode->dharma", msg)
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
