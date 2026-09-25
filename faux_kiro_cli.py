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


# ── the model call (OpenAI-compatible, streaming) ──────────────────────────────

def _stream_model_reply(prompt_text: str, on_chunk) -> None:
    """POST to <BASE_URL>/chat/completions with stream=true; call on_chunk(text)
    for each delta. Falls back to a clear error string if the endpoint fails."""
    url = f"{BASE_URL}/chat/completions"
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt_text}],
        "stream": True,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
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
                    on_chunk(piece)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        on_chunk(f"[faux-backend ERROR {e.code}] {detail}")
        _log(f"HTTP {e.code} from {url}: {detail}")
    except Exception as e:  # noqa: BLE001 — surface any transport failure to the user
        on_chunk(f"[faux-backend ERROR] {type(e).__name__}: {e}")
        _log(f"error calling {url}: {e}")


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

            def on_chunk(piece: str, _sid=sid) -> None:
                _send({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": _sid,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": piece},
                    },
                }})

            _stream_model_reply(prompt_text, on_chunk)
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "end_turn"}})

        elif method == "session/cancel":
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
