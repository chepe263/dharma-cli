# PROVENANCE — contra qué se construyó Dharma

Dharma imita el protocolo que **kiro-cli** habla con **Kiro Crew** (ACP). Ese
protocolo puede cambiar entre versiones. Este documento fija la referencia exacta
usada, para que un LLM o una sesión futura pueda re-husmear el código fuente y
detectar incompatibilidades o adoptar mejoras.

## Referencia exacta

- **Kiro Crew (código fuente de referencia):**
  repo `github.com/kirodotdev/KiroCrew`, commit
  **`07a05a2b1c13b5f3b55a7e428b31178e450a09a4`** (rama `main`, 2026-09-25).
- **kiro-cli (binario imitado):** versión **2.24.0** (host de desarrollo).
- **Protocolo ACP:** dialecto kiro, `protocolVersion = "2025-08-22"`, JSON-RPC 2.0
  por stdio, una línea por mensaje (`\n`), sin `Content-Length`.

## Archivos fuente que definen el contrato (dónde mirar si algo cambia)

En el repo de Kiro Crew, bajo `src/kiro_crew/`:

- `acp/client.py` — spawn del proceso, handshake, lectura/escritura de frames.
- `acp/harness/kiro.py` — el argv (`acp --agent <name> [--model <id>]`) y la
  versión de protocolo del dialecto kiro.
- `acp/_dispatch.py` — parseo de `session/update`, `tool_call`, permisos, stopReason.
- `acp/types.py` — constantes de métodos/updates/outcomes.
- `testing/fake_acp_backend.py` — **la plantilla canónica**: el propio backend
  ACP falso del proyecto (responde `--version`/`whoami`/`acp --help`, emite
  `tool_call`, pide `request_permission`). Si algo de Dharma diverge, comparar aquí.
- `cloud/login_target.py` (`parse_whoami_output`) — cómo se decide logueado/no.
- `config/defaults.json` — el agente `kirocrew` y su lista de herramientas.

Docs oficiales usadas:
- `docs/system-specs/modules/acp-client.md` — el contrato de sesión ACP completo.
- `docs/system-specs/modules/harness-onboarding.md` — cómo se da de alta un backend.
- `https://kiro.dev/docs/tools/` — catálogo canónico de herramientas built-in.

## Cómo re-verificar si kiro-cli / Kiro Crew cambian

1. Clonar/actualizar Kiro Crew y anotar el commit nuevo.
2. Comparar el nuevo `acp/types.py` y `acp/harness/kiro.py` contra los de
   `07a05a2`: si cambió `PROTOCOL_VERSION`, el argv, o los nombres de método,
   Dharma necesita ajuste.
3. Correr `verify.py` de este repo (verificador de protocolo de capa 1). Si pasa,
   el handshake + turno + tool loop siguen compatibles.
4. Levantar el Docker (`docker/compose.yaml`) contra la imagen nueva y probar un
   chat: si arranca `healthy` y responde, la integración vive.
5. Capturar de nuevo la salida real de los one-shot del kiro-cli nuevo
   (`--version`, `whoami --format json`, `chat --list-models --format json`,
   `acp --help`) y comparar con lo que Dharma emite en `_handle_one_shot`.

## Nota de método

Todo Dharma se construyó LEYENDO la fuente real (código del clon, el fake-backend
oficial, la doc de tools) en vez de adivinar el protocolo. Esa es la forma de
mantenerlo: ante cualquier duda, la fuente de verdad es el código de Kiro Crew en
el commit de referencia, no la memoria de una sesión.
