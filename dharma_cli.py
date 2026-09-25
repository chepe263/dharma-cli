#!/usr/bin/env python3
"""dharma-cli — a stand-in for kiro-cli that speaks ACP but talks to any
OpenAI-compatible endpoint (Ollama Cloud, a local Ollama, OpenAI, LM Studio, …).

Named for *Dharma & Greg*: Greg is the real kiro-cli — formal, official, bound to
its system (Amazon, login, credits). Dharma is this faux — a free spirit from
outside those bounds (no Amazon, no lock-in) that speaks Greg's language and takes
his place without friction. Like the show's Dharma, its role is to CONNECT two
worlds that wouldn't otherwise fit: it joins Kiro Crew to whatever model you bring.

Kiro Crew launches this exactly as it launches the real `kiro-cli acp`. It never
notices the difference: it sends ACP JSON-RPC frames over stdio and gets streamed
text back. Inside, this process forwards each turn to `<BASE_URL>/chat/completions`
and streams the reply back as ACP `agent_message_chunk` notifications.

CONFIG (via .env in the working dir, or real environment variables):
  DHARMA_BASE_URL   OpenAI-compatible base, e.g. https://ollama.com/v1
  DHARMA_API_KEY    Bearer token (Ollama Cloud: your ollama.com API key)
  DHARMA_MODEL      model id, e.g. gpt-oss:20b  or  gemma4:cloud
  DHARMA_APPROVAL   1 = ask before sensitive tools (default), 0 = run directly

No third-party dependencies — stdlib only (urllib). Python 3.8+.

Wire format confirmed against the KiroCrew clone at commit 07a05a2:
  transport = one JSON object per line, '\n'-terminated, no Content-Length;
  protocolVersion = "2025-08-22" (kiro dialect); argv = `acp --agent <name> [--model <id>]`.
"""
import json
import os
import sys
import urllib.request
import urllib.error

PROTOCOL_VERSION = "2025-08-22"


# ── config ───────────────────────────────────────────────────────────────────

def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments. Real env wins."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                os.environ.setdefault(key, val)
    except FileNotFoundError:
        pass


_load_dotenv()

BASE_URL = os.environ.get("DHARMA_BASE_URL", "https://ollama.com/v1").rstrip("/")
API_KEY = os.environ.get("DHARMA_API_KEY", "")
MODEL = os.environ.get("DHARMA_MODEL", "gpt-oss:20b")


# ── one-shot commands (faux-ted from the real kiro-cli output) ─────────────────
# KiroCrew runs these OUTSIDE ACP to check "is the agent installed / logged in".
# If any fail, the dashboard shows a "not logged in (kiro-cli login)" card and
# blocks. So the faux must answer them itself — this is what removes the Amazon
# SSO requirement. Shapes captured verbatim from `kiro-cli 2.24.0` on the host.


def _list_models_catalog() -> dict:
    """Ask the OpenAI-compatible endpoint for its real model list (GET /v1/models)
    and map it to the shape KiroCrew parses. Falls back to the configured model."""
    def _fallback() -> dict:
        return {"models": [
            {"model_name": "auto", "description": "dharma default", "model_id": "auto",
             "context_window_tokens": 128000, "rate_multiplier": 0.0, "rate_unit": "Free"},
            {"model_name": MODEL, "description": f"dharma model ({MODEL})", "model_id": MODEL,
             "context_window_tokens": 128000, "rate_multiplier": 0.0, "rate_unit": "Free"},
        ], "default_model": "auto"}

    try:
        req = urllib.request.Request(f"{BASE_URL}/models", method="GET")
        if API_KEY:
            req.add_header("Authorization", f"Bearer {API_KEY}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        if not ids:
            return _fallback()
        # El modelo del .env (MODEL) es el DEFAULT y va PRIMERO; luego el resto.
        ordered = [MODEL] + [m for m in ids if m != MODEL]
        models = [{"model_name": mid, "description": f"dharma: {mid}", "model_id": mid,
                   "context_window_tokens": 128000, "rate_multiplier": 0.0, "rate_unit": "Free"}
                  for mid in ordered]
        return {"models": models, "default_model": MODEL}
    except Exception as e:  # noqa: BLE001
        _log(f"list-models: /models falló ({type(e).__name__}: {e}); usando fallback")
        return _fallback()


def _handle_one_shot() -> bool:
    """If argv is a known one-shot command, print the faux answer and return
    True (caller should exit 0). Otherwise return False (fall through to ACP)."""
    argv = sys.argv[1:]
    if not argv:
        return False

    # `kiro-cli --version`  -> "kiro-cli <ver>"
    if argv[0] == "--version":
        print("kiro-cli 2.24.0-dharma")
        return True

    # `kiro-cli acp --help`  -> KiroCrew's readiness probe runs this to confirm
    # the `acp` subcommand exists (kiro_prerequisite._probe_acp_support). The
    # real `acp` session still drives the protocol over stdio when invoked
    # WITHOUT --help. Answer success so the acp-support gate clears.
    if argv[0] == "acp" and "--help" in argv:
        print("Usage: kiro-cli acp [OPTIONS]")
        return True

    # `kiro-cli whoami [--format json]`  -> identity. We fake a satisfied login.
    if argv[0] == "whoami":
        if "--format" in argv and "json" in argv:
            print(json.dumps({
                "accountType": "Dharma",
                "email": "dharma@localhost",
                "region": "local",
                "startUrl": BASE_URL,
            }))
        else:
            print("Logged in with dharma backend")
            print(f"Endpoint: {BASE_URL}")
        return True

    # `kiro-cli chat --list-models --format json`  -> model catalog.
    # Dharma pregunta al endpoint OpenAI-compatible su lista REAL de modelos
    # (GET /v1/models) y la traduce al formato que KiroCrew parsea. Si la consulta
    # falla, cae al modelo configurado (para no dejar el selector vacío).
    if argv[0] == "chat" and "--list-models" in argv:
        print(json.dumps(_list_models_catalog()))
        return True

    # `kiro-cli login ...` — nothing to do; a faux backend needs no SSO.
    if argv[0] == "login":
        print("dharma: no login needed (API key from .env)")
        return True

    return False


# ── ACP stdio plumbing ─────────────────────────────────────────────────────────

def _agent_from_argv() -> str:
    argv = sys.argv[1:]
    if "--agent" in argv:
        i = argv.index("--agent")
        if i + 1 < len(argv):
            return argv[i + 1]
    return "kirocrew"


def _model_from_argv() -> str:
    """E1: KiroCrew passes the chosen model as `--model <id>` at spawn."""
    argv = sys.argv[1:]
    if "--model" in argv:
        i = argv.index("--model")
        if i + 1 < len(argv) and argv[i + 1] and argv[i + 1] != "auto":
            return argv[i + 1]
    return ""


# E1: the active model for this process. Starts from --model (selector) or the
# configured default; session/set_model updates it live.
ACTIVE_MODEL = _model_from_argv() or MODEL

# E2: require tool approval unless explicitly disabled (DHARMA_APPROVAL=0).
APPROVAL = os.environ.get("DHARMA_APPROVAL", "1") not in ("0", "false", "no", "")

# Máximo de rondas de herramientas por turno (cada ronda = 1 llamada al modelo
# que puede pedir varias tools). Freno anti-bucle; 6 era muy poco para tareas de
# agente reales. Configurable vía .env.
try:
    MAX_ROUNDS = max(1, int(os.environ.get("DHARMA_MAX_ROUNDS", "25")))
except ValueError:
    MAX_ROUNDS = 25

# E2: tools that MUTATE or execute need approval; pure reads run without asking.
SENSITIVE_TOOLS = {"execute_bash", "fs_write", "fs_append", "str_replace", "delete_file"}

# E2: monotonic id for our server->client permission requests.
_PERM_ID = 9000

# Cancelación: session/cancel la enciende; _run_turn la revisa entre rondas.
# Nota: corta entre rondas de herramientas, no a mitad de una generación HTTP en
# curso (el bucle de stdin está ocupado durante la llamada al modelo).
_CANCELLED = False

# Memoria de conversación a nivel de sesión (persiste entre turnos). Arranca con
# un system prompt que le da a Dharma su papel.
SESSION_MESSAGES = [{
    "role": "system",
    "content": (
        "Eres Dharma, un asistente que corre dentro de KiroCrew a través de un "
        "backend propio. Ayudas con tareas de código y del sistema. Tienes "
        "herramientas: execute_bash, fs_read, fs_write, fs_append, str_replace, "
        "delete_file, list_directory, file_search, grep_search, web_fetch. Úsalas "
        "cuando haga falta actuar; no solo describas, hazlo. Recuerdas el contexto "
        "de la conversación."
    ),
}]


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _log(msg: str) -> None:
    # stderr is diagnostic only; Kiro Crew reads it as warnings, never as content.
    sys.stderr.write(f"[dharma] {msg}\n")
    sys.stderr.flush()


# ── herramientas propias del faux (function calling) ───────────────────────────
# En el dialecto kiro-cli el BACKEND ejecuta las herramientas; KiroCrew no las
# corre. Así que el faux ofrece SUS PROPIAS tools al modelo OpenAI vía el campo
# `tools`, las ejecuta él mismo cuando el modelo las pide, anuncia cada uso a
# KiroCrew como un `tool_call`/`tool_call_update`, y realimenta el resultado al
# modelo hasta que produce la respuesta final de texto.

def _tool(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required},
    }}


# Catálogo alineado con la doc oficial https://kiro.dev/docs/tools/
# (nombres y categorías canónicos: read / write / shell / web). En kiro-cli son
# built-in del agente; aquí las implementa el faux con los MISMOS nombres.
TOOLS_SPEC = [
    # ── shell ──
    _tool("execute_bash", "Ejecuta un comando de shell y devuelve su salida.",
          {"command": {"type": "string", "description": "El comando a ejecutar"}}, ["command"]),
    # ── read ──
    _tool("fs_read", "Lee el contenido de un archivo de texto.",
          {"path": {"type": "string", "description": "Ruta del archivo"}}, ["path"]),
    _tool("list_directory", "Lista el contenido de un directorio.",
          {"path": {"type": "string", "description": "Ruta del directorio"}}, ["path"]),
    _tool("file_search", "Búsqueda difusa de rutas de archivo (glob).",
          {"pattern": {"type": "string", "description": "Patrón glob, ej **/*.py"},
           "path": {"type": "string", "description": "Directorio base (opcional)"}}, ["pattern"]),
    _tool("grep_search", "Búsqueda de contenido por regex en archivos.",
          {"pattern": {"type": "string", "description": "Regex a buscar"},
           "path": {"type": "string", "description": "Directorio base (opcional)"}}, ["pattern"]),
    # ── write ──
    _tool("fs_write", "Crea o sobrescribe un archivo con el contenido dado.",
          {"path": {"type": "string", "description": "Ruta del archivo"},
           "content": {"type": "string", "description": "Contenido completo"}}, ["path", "content"]),
    _tool("fs_append", "Añade contenido al final de un archivo existente.",
          {"path": {"type": "string", "description": "Ruta del archivo"},
           "content": {"type": "string", "description": "Contenido a añadir"}}, ["path", "content"]),
    _tool("str_replace", "Reemplaza texto exacto en un archivo (edición puntual).",
          {"path": {"type": "string"}, "old": {"type": "string", "description": "Texto a reemplazar"},
           "new": {"type": "string", "description": "Texto nuevo"}}, ["path", "old", "new"]),
    _tool("delete_file", "Borra un archivo.",
          {"path": {"type": "string", "description": "Ruta del archivo a borrar"}}, ["path"]),
    # ── web ──
    _tool("web_fetch", "Descarga y extrae el texto de una URL.",
          {"url": {"type": "string", "description": "URL a descargar"}}, ["url"]),
    _tool("web_search", "Busca en la web información actual.",
          {"query": {"type": "string", "description": "Consulta de búsqueda"}}, ["query"]),
    # ── session ──
    _tool("todo_list", "Lleva una lista de tareas de la sesión para organizar el trabajo. "
                       "action=add añade una tarea, complete la marca hecha (por texto o índice), "
                       "list muestra la lista.",
          {"action": {"type": "string", "enum": ["add", "complete", "list"]},
           "item": {"type": "string", "description": "Texto de la tarea (para add/complete)"}},
          ["action"]),
]

# Lista de tareas de la sesión (para la herramienta todo_list).
TODOS = []  # cada uno: {"text": str, "done": bool}


def _run_tool(name: str, args: dict) -> str:
    """Execute a faux tool locally. Names mirror kiro.dev/docs/tools."""
    import subprocess
    import os as _os
    import glob as _glob
    import re as _re

    def _abs(p):
        return p if _os.path.isabs(p) else _os.path.join(_os.path.expanduser("~"), p)

    try:
        if name == "execute_bash":
            out = subprocess.run(args.get("command", ""), shell=True,
                                 capture_output=True, text=True, timeout=30)
            return (out.stdout + out.stderr)[:4000] or "(sin salida)"
        if name == "fs_read":
            with open(_abs(args.get("path", "")), "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()[:4000]
        if name == "list_directory":
            base = _abs(args.get("path", "."))
            return "\n".join(sorted(_os.listdir(base)))[:4000] or "(vacío)"
        if name == "file_search":
            base = _abs(args.get("path", "."))
            hits = _glob.glob(_os.path.join(base, "**", args.get("pattern", "*")), recursive=True)
            return "\n".join(hits[:100])[:4000] or "(sin coincidencias)"
        if name == "grep_search":
            base = _abs(args.get("path", "."))
            rx = _re.compile(args.get("pattern", ""))
            found = []
            for root, _dirs, files in _os.walk(base):
                for f in files:
                    fp = _os.path.join(root, f)
                    try:
                        with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                            for i, ln in enumerate(fh, 1):
                                if rx.search(ln):
                                    found.append(f"{fp}:{i}: {ln.strip()}")
                                    if len(found) >= 100:
                                        raise StopIteration
                    except (OSError, StopIteration):
                        if len(found) >= 100:
                            break
                if len(found) >= 100:
                    break
            return "\n".join(found)[:4000] or "(sin coincidencias)"
        if name == "fs_write":
            path = _abs(args.get("path", ""))
            _os.makedirs(_os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(args.get("content", ""))
            return f"escrito {len(args.get('content',''))} bytes en {path}"
        if name == "fs_append":
            path = _abs(args.get("path", ""))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(args.get("content", ""))
            return f"añadido a {path}"
        if name == "str_replace":
            path = _abs(args.get("path", ""))
            with open(path, "r", encoding="utf-8") as fh:
                data = fh.read()
            old = args.get("old", "")
            if old not in data:
                return "(no se encontró el texto a reemplazar)"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(data.replace(old, args.get("new", ""), 1))
            return f"reemplazado en {path}"
        if name == "delete_file":
            path = _abs(args.get("path", ""))
            _os.remove(path)
            return f"borrado {path}"
        if name == "web_fetch":
            # B2: GET real con límites de la doc oficial (10MB / 30s).
            url = args.get("url", "")
            r = urllib.request.Request(url, headers={"User-Agent": "dharma-cli"})
            with urllib.request.urlopen(r, timeout=30) as resp:
                raw = resp.read(10 * 1024 * 1024).decode("utf-8", "replace")
            # extracción muy simple de texto: quita etiquetas HTML
            text = _re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=_re.S | _re.I)
            text = _re.sub(r"<[^>]+>", " ", text)
            text = _re.sub(r"\s+", " ", text).strip()
            return text[:4000] or "(sin contenido)"
        if name == "todo_list":
            action = args.get("action", "list")
            item = args.get("item", "")
            if action == "add" and item:
                TODOS.append({"text": item, "done": False})
            elif action == "complete" and item:
                for t in TODOS:
                    if item == t["text"] or (item.isdigit() and TODOS.index(t) == int(item) - 1):
                        t["done"] = True
                        break
            if not TODOS:
                return "(lista de tareas vacía)"
            return "\n".join(
                f"{i+1}. [{'x' if t['done'] else ' '}] {t['text']}"
                for i, t in enumerate(TODOS)
            )
        if name == "web_search":
            return "(web_search no implementada: requiere una API de búsqueda; usa web_fetch de una URL, o execute_bash con curl)"
        return f"(herramienta desconocida: {name})"
    except Exception as e:  # noqa: BLE001
        return f"(error ejecutando {name}: {type(e).__name__}: {e})"


# ── llamada al modelo OpenAI-compatible (no streaming, para el bucle de tools) ──

def _chat_stream(messages: list, on_text) -> dict:
    """One /chat/completions call with tools, STREAMING (B1).

    Calls on_text(piece) for each text delta so the UI writes live, and
    accumulates any tool_calls (which arrive fragmented in streaming) into a
    complete list. Returns the assistant message: {content, tool_calls}.
    Uses ACTIVE_MODEL (E1) so the dashboard's model selector takes effect.
    """
    url = f"{BASE_URL}/chat/completions"
    body = json.dumps({
        "model": ACTIVE_MODEL,
        "messages": messages,
        "tools": TOOLS_SPEC,
        "stream": True,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    content_parts = []
    tool_acc = {}  # index -> {id, name, arguments(str)}
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
                on_text(piece)
            for tcd in (delta.get("tool_calls") or []):
                idx = tcd.get("index", 0)
                slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tcd.get("id"):
                    slot["id"] = tcd["id"]
                fn = tcd.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

    tool_calls = [{"id": s["id"] or f"call_{i}", "type": "function",
                   "function": {"name": s["name"], "arguments": s["arguments"]}}
                  for i, s in sorted(tool_acc.items())]
    msg = {"role": "assistant", "content": "".join(content_parts) or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_kind(name: str) -> str:
    """ACP toolCall 'kind' que concuerda con la herramienta. Debe ser coherente
    entre el session/update tool_call y la petición de permiso, o KiroCrew no
    renderiza la tarjeta (fs_write se quedaba bloqueado sin pedir permiso)."""
    if name in ("fs_write", "fs_append", "str_replace"):
        return "edit"
    if name == "delete_file":
        return "delete"
    if name in ("fs_read", "list_directory", "file_search", "grep_search"):
        return "read"
    if name in ("web_fetch", "web_search"):
        return "fetch"
    return "execute"


def _tool_title(name: str, args: dict) -> str:
    """Título CORTO para el tool_call/permiso. NUNCA incluir args completo:
    el contenido de un fs_write son miles de chars y hay un límite de 256 en la
    ruta ACP (el título se validaba como 'tool name' y rebasaba -> RECHAZADO,
    la verdadera causa del 'fs_write bloqueado'). Mostramos nombre + un dato
    identificador (ruta/url/comando), recortado."""
    hint = ""
    if isinstance(args, dict):
        hint = str(args.get("path") or args.get("url") or args.get("command")
                   or args.get("query") or args.get("pattern") or "")
    title = f"{name} {hint}".strip() if hint else name
    return title[:200]


def _request_permission(session_id, call_id, name, args) -> bool:
    """E2: ask KiroCrew to approve a sensitive tool. Returns True if approved."""
    global _PERM_ID
    _PERM_ID += 1
    rid = _PERM_ID
    _send({"jsonrpc": "2.0", "id": rid, "method": "session/request_permission", "params": {
        "sessionId": session_id,
        "toolCall": {"toolCallId": call_id, "title": _tool_title(name, args), "kind": _tool_kind(name)},
        "options": [
            {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
            {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
        ],
    }})
    # wait for KiroCrew's response to this exact id
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("id") == rid and "method" not in m:
            outcome = ((m.get("result") or {}).get("outcome") or {})
            oc = outcome.get("outcome")
            opt = outcome.get("optionId", "")
            return oc == "selected" and "reject" not in str(opt)
        # a cancel mid-wait means no
        if m.get("method") == "session/cancel":
            return False
    return False


# ── el bucle de function calling: prompt -> tools -> texto final ───────────────

def _run_turn(prompt_text: str, session_id: str, msg_id) -> None:
    """Drive one ACP turn: streaming text (B1), tool calls with approval (E2),
    on the selected model (E1), with conversation memory across turns.
    Closes the prompt request with stopReason."""
    # Memoria de conversación: SESSION_MESSAGES persiste entre turnos, así Dharma
    # recuerda lo anterior ("crea X" -> "ahora edítalo" funciona). El proceso vive
    # toda la sesión; antes se reiniciaba cada turno y era amnésico.
    SESSION_MESSAGES.append({"role": "user", "content": prompt_text})
    messages = SESSION_MESSAGES  # alias: trabajamos sobre el historial vivo
    max_rounds = MAX_ROUNDS

    def emit_text(text: str) -> None:
        if not text:
            return
        _send({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": session_id,
            "update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text", "text": text}},
        }})

    try:
        for _round in range(max_rounds):
            if _CANCELLED:
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "cancelled"}})
                return
            reply = _chat_stream(messages, emit_text)  # texto ya sale en vivo
            tool_calls = reply.get("tool_calls") or []

            if not tool_calls:
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "end_turn"}})
                return

            messages.append(reply)
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                call_id = tc.get("id") or f"dharma-tool-{name}"

                _send({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": session_id,
                    "update": {"sessionUpdate": "tool_call", "toolCallId": call_id,
                               "title": _tool_title(name, args), "kind": _tool_kind(name),
                               "status": "pending", "rawInput": args},
                }})

                # E2: pedir permiso para herramientas sensibles
                if APPROVAL and name in SENSITIVE_TOOLS:
                    if not _request_permission(session_id, call_id, name, args):
                        _send({"jsonrpc": "2.0", "method": "session/update", "params": {
                            "sessionId": session_id,
                            "update": {"sessionUpdate": "tool_call_update", "toolCallId": call_id,
                                       "status": "failed",
                                       "content": [{"type": "text", "text": "rechazada por el usuario"}]},
                        }})
                        messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                         "name": name, "content": "(el usuario rechazó esta herramienta)"})
                        continue

                result = _run_tool(name, args)
                _send({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": session_id,
                    "update": {"sessionUpdate": "tool_call_update", "toolCallId": call_id,
                               "status": "completed",
                               "content": [{"type": "text", "text": result[:500]}]},
                }})
                messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                 "name": name, "content": result})

        emit_text(f"\n[dharma] Alcancé el límite de {MAX_ROUNDS} rondas de herramientas "
                  f"en este turno. Si la tarea necesita más pasos, súbelo con "
                  f"DHARMA_MAX_ROUNDS en el .env, o pídeme que continúe.")
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "max_turn_requests"}})

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        emit_text(f"[dharma ERROR {e.code}] {detail}")
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "end_turn"}})
        _log(f"HTTP {e.code}: {detail}")
    except Exception as e:  # noqa: BLE001
        emit_text(f"[dharma ERROR] {type(e).__name__}: {e}")
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "end_turn"}})
        _log(f"error en el turno: {e}")


def _extract_prompt_text(params: dict) -> str:
    blocks = params.get("prompt") or []
    parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    return " ".join(p for p in parts if p).strip()


# ── main ACP loop ──────────────────────────────────────────────────────────────

def main() -> int:
    global ACTIVE_MODEL, _CANCELLED
    # One-shot commands (--version / whoami / chat --list-models / login) are
    # answered and we exit — they must NOT fall into the ACP stdin loop.
    if _handle_one_shot():
        return 0

    agent = _agent_from_argv()
    session_id = "dharma-session-1"
    _log(f"started: base_url={BASE_URL} model={MODEL} agent={agent} auth={'yes' if API_KEY else 'no'}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "agentCapabilities": {"loadSession": False},
                "agentInfo": {"name": "dharma-cli", "version": "0.1.0"},
            }})

        elif method == "session/new":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "sessionId": session_id,
                "modes": {
                    "currentModeId": agent,
                    "availableModes": [{"id": agent, "name": agent, "description": ""}],
                },
            }})

        elif method == "session/set_model":
            # E1: apply the model the dashboard selector chose, live.
            p = msg.get("params") or {}
            chosen = p.get("modelId") or p.get("model") or ""
            if chosen and chosen != "auto":
                ACTIVE_MODEL = chosen
                _log(f"set_model: modelo activo -> {ACTIVE_MODEL}")
            if msg_id is not None:
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        elif method == "session/set_mode":
            if msg_id is not None:
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        elif method == "session/prompt":
            params = msg.get("params") or {}
            sid = params.get("sessionId", session_id)
            prompt_text = _extract_prompt_text(params)
            _CANCELLED = False  # nuevo turno, limpiar cancelación previa
            # _run_turn drives the full function-calling loop and closes the
            # prompt request itself (with stopReason).
            _run_turn(prompt_text, sid, msg_id)

        elif method == "session/cancel":
            _CANCELLED = True  # _run_turn lo revisa entre rondas

        elif method in ("_kiro.dev/session/terminate", "session/close"):
            # KiroCrew tears a session down with this. If we don't answer the
            # REQUEST, its teardown waits 5s and times out every close. Answer
            # empty so the session ends promptly.
            if msg_id is not None:
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

    return 0


if __name__ == "__main__":
    sys.exit(main())
