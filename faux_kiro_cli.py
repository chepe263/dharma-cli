#!/usr/bin/env python3
"""faux kiro-cli — a fake kiro-cli that speaks ACP but talks to any
OpenAI-compatible endpoint (Ollama Cloud, a local Ollama, OpenAI, LM Studio, …).

Kiro Crew launches this exactly as it launches the real `kiro-cli acp`. It never
notices the difference: it sends ACP JSON-RPC frames over stdio and gets streamed
text back. Inside, this process forwards each turn to `<BASE_URL>/chat/completions`
and streams the reply back as ACP `agent_message_chunk` notifications.

CONFIG (via .env in the working dir, or real environment variables):
  FAUX_BASE_URL   OpenAI-compatible base, e.g. https://ollama.com/v1   (no trailing slash needed)
  FAUX_API_KEY    Bearer token for that endpoint (Ollama Cloud: your ollama.com API key)
  FAUX_MODEL      model id, e.g. gpt-oss:20b  or  gemma4:cloud

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
BASE_URL = os.environ.get("FAUX_BASE_URL", "https://ollama.com/v1").rstrip("/")
API_KEY = os.environ.get("FAUX_API_KEY", "")
MODEL = os.environ.get("FAUX_MODEL", "gpt-oss:20b")


# ── one-shot commands (faux-ted from the real kiro-cli output) ─────────────────
# KiroCrew runs these OUTSIDE ACP to check "is the agent installed / logged in".
# If any fail, the dashboard shows a "not logged in (kiro-cli login)" card and
# blocks. So the faux must answer them itself — this is what removes the Amazon
# SSO requirement. Shapes captured verbatim from `kiro-cli 2.24.0` on the host.

def _handle_one_shot() -> bool:
    """If argv is a known one-shot command, print the faux answer and return
    True (caller should exit 0). Otherwise return False (fall through to ACP)."""
    argv = sys.argv[1:]
    if not argv:
        return False

    # `kiro-cli --version`  -> "kiro-cli <ver>"
    if argv[0] == "--version":
        print("kiro-cli 2.24.0-faux")
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
                "accountType": "FauxBackend",
                "email": "faux@localhost",
                "region": "local",
                "startUrl": BASE_URL,
            }))
        else:
            print("Logged in with faux backend")
            print(f"Endpoint: {BASE_URL}")
        return True

    # `kiro-cli chat --list-models --format json`  -> model catalog.
    # We advertise the single configured model (plus "auto" as default) in the
    # exact shape KiroCrew parses (models[].model_name/model_id, default_model).
    if argv[0] == "chat" and "--list-models" in argv:
        catalog = {
            "models": [
                {"model_name": "auto", "description": "faux default", "model_id": "auto",
                 "context_window_tokens": 128000, "rate_multiplier": 0.0, "rate_unit": "Free"},
                {"model_name": MODEL, "description": f"faux backend model ({MODEL})",
                 "model_id": MODEL, "context_window_tokens": 128000,
                 "rate_multiplier": 0.0, "rate_unit": "Free"},
            ],
            "default_model": "auto",
        }
        print(json.dumps(catalog))
        return True

    # `kiro-cli login ...` — nothing to do; a faux backend needs no SSO.
    if argv[0] == "login":
        print("faux backend: no login needed (API key comes from .env)")
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


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _log(msg: str) -> None:
    # stderr is diagnostic only; Kiro Crew reads it as warnings, never as content.
    sys.stderr.write(f"[faux] {msg}\n")
    sys.stderr.flush()


# ── herramientas propias del faux (function calling) ───────────────────────────
# En el dialecto kiro-cli el BACKEND ejecuta las herramientas; KiroCrew no las
# corre. Así que el faux ofrece SUS PROPIAS tools al modelo OpenAI vía el campo
# `tools`, las ejecuta él mismo cuando el modelo las pide, anuncia cada uso a
# KiroCrew como un `tool_call`/`tool_call_update`, y realimenta el resultado al
# modelo hasta que produce la respuesta final de texto.

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "ejecutar_bash",
            "description": "Ejecuta un comando de shell en el contenedor y devuelve su salida.",
            "parameters": {
                "type": "object",
                "properties": {"comando": {"type": "string", "description": "El comando a ejecutar"}},
                "required": ["comando"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "leer_archivo",
            "description": "Lee el contenido de un archivo de texto y lo devuelve.",
            "parameters": {
                "type": "object",
                "properties": {"ruta": {"type": "string", "description": "Ruta absoluta del archivo"}},
                "required": ["ruta"],
            },
        },
    },
]


def _run_tool(name: str, args: dict) -> str:
    """Execute a faux tool locally. Returns the result string the model sees."""
    import subprocess
    try:
        if name == "ejecutar_bash":
            cmd = args.get("comando", "")
            out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            return (out.stdout + out.stderr)[:4000] or "(sin salida)"
        if name == "leer_archivo":
            with open(args.get("ruta", ""), "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()[:4000]
        return f"(herramienta desconocida: {name})"
    except Exception as e:  # noqa: BLE001
        return f"(error ejecutando {name}: {type(e).__name__}: {e})"


# ── llamada al modelo OpenAI-compatible (no streaming, para el bucle de tools) ──

def _chat_once(messages: list) -> dict:
    """One non-streaming /chat/completions call with tools. Returns the message
    object of choices[0] (may carry content and/or tool_calls), or raises."""
    url = f"{BASE_URL}/chat/completions"
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS_SPEC,
        "stream": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return (data.get("choices") or [{}])[0].get("message") or {}


# ── el bucle de function calling: prompt -> tools -> texto final ───────────────

def _run_turn(prompt_text: str, session_id: str, msg_id) -> None:
    """Drive one ACP turn with tool support. Emits agent_message_chunk /
    tool_call / tool_call_update, then closes the prompt request with stopReason."""
    messages = [{"role": "user", "content": prompt_text}]
    max_rounds = 6  # cota de seguridad contra bucles de herramientas

    def emit_text(text: str) -> None:
        _send({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": session_id,
            "update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text", "text": text}},
        }})

    try:
        for _round in range(max_rounds):
            reply = _chat_once(messages)
            tool_calls = reply.get("tool_calls") or []

            if not tool_calls:
                # respuesta final de texto
                emit_text(reply.get("content") or "")
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "end_turn"}})
                return

            # el modelo pidió herramientas: anúncialas, ejecútalas, realimenta
            messages.append(reply)  # el turno del asistente con los tool_calls
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                call_id = tc.get("id") or f"faux-tool-{name}"

                # 1. anunciar a KiroCrew que se usa una herramienta (visible en la UI)
                _send({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": session_id,
                    "update": {"sessionUpdate": "tool_call", "toolCallId": call_id,
                               "title": f"{name} {args}", "kind": "execute",
                               "status": "pending", "rawInput": args},
                }})
                # 2. ejecutar la herramienta (el backend ejecuta, no KiroCrew)
                result = _run_tool(name, args)
                # 3. cerrar el tool_call en la UI
                _send({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": session_id,
                    "update": {"sessionUpdate": "tool_call_update", "toolCallId": call_id,
                               "status": "completed",
                               "content": [{"type": "text", "text": result[:500]}]},
                }})
                # 4. realimentar el resultado al modelo
                messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                 "name": name, "content": result})

        # se agotó el presupuesto de rondas
        emit_text("[faux-backend] límite de rondas de herramientas alcanzado.")
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "end_turn"}})

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        emit_text(f"[faux-backend ERROR {e.code}] {detail}")
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "end_turn"}})
        _log(f"HTTP {e.code}: {detail}")
    except Exception as e:  # noqa: BLE001
        emit_text(f"[faux-backend ERROR] {type(e).__name__}: {e}")
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "end_turn"}})
        _log(f"error en el turno: {e}")


def _extract_prompt_text(params: dict) -> str:
    blocks = params.get("prompt") or []
    parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    return " ".join(p for p in parts if p).strip()


# ── main ACP loop ──────────────────────────────────────────────────────────────

def main() -> int:
    # One-shot commands (--version / whoami / chat --list-models / login) are
    # answered and we exit — they must NOT fall into the ACP stdin loop.
    if _handle_one_shot():
        return 0

    agent = _agent_from_argv()
    session_id = "faux-session-1"
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
                "agentInfo": {"name": "faux-kiro-cli", "version": "0.1.0"},
            }})

        elif method == "session/new":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "sessionId": session_id,
                "modes": {
                    "currentModeId": agent,
                    "availableModes": [{"id": agent, "name": agent, "description": ""}],
                },
            }})

        elif method in ("session/set_mode", "session/set_model"):
            if msg_id is not None:
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        elif method == "session/prompt":
            params = msg.get("params") or {}
            sid = params.get("sessionId", session_id)
            prompt_text = _extract_prompt_text(params)
            # _run_turn drives the full function-calling loop and closes the
            # prompt request itself (with stopReason).
            _run_turn(prompt_text, sid, msg_id)

        elif method == "session/cancel":
            pass  # notification, nothing to answer

        elif method in ("_kiro.dev/session/terminate", "session/close"):
            # KiroCrew tears a session down with this. If we don't answer the
            # REQUEST, its teardown waits 5s and times out every close. Answer
            # empty so the session ends promptly.
            if msg_id is not None:
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

    return 0


if __name__ == "__main__":
    sys.exit(main())
