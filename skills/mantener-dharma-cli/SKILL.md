---
name: mantener-dharma-cli
description: Retomar y re-verificar dharma-cli (backend LLM faux para KiroCrew) cuando kiro-cli o el protocolo ACP cambian.
triggers: dharma-cli, dharma, faux kiro-cli, backend openai kirocrew, reemplazar backend kiro, acp faux, kirocrew sin amazon
---

# mantener-dharma-cli

Skill de mantenimiento que VIAJA con este repo. Quien clone dharma-cli se lleva
las instrucciones para retomarlo, arreglarlo o re-verificarlo cuando KiroCrew o
kiro-cli cambien. (Para cargarlo como skill en una instalación de KiroCrew,
cópialo a tu directorio de skills, o léelo directamente.)

## Cuándo usarlo

Al retomar, arreglar o extender **dharma-cli** — el binario que se hace pasar por
`kiro-cli` para correr KiroCrew con un endpoint OpenAI-compatible (ej. Ollama
Cloud), sin Amazon. Sobre todo cuando **kiro-cli cambió de versión o el protocolo
ACP cambió** y algo dejó de funcionar.

## Principio rector

**Leer la fuente real, no adivinar el protocolo.** La verdad vive en el código de
KiroCrew, no en la memoria de una sesión. Empezar SIEMPRE por `PROVENANCE.md` de
este repo — trae el commit de KiroCrew de referencia exacto.

## Pasos

1. **Leer `PROVENANCE.md`** (en la raíz del repo): commit de KiroCrew de
   referencia, versión de kiro-cli, `protocolVersion` ACP, y los archivos fuente
   que definen el contrato.
2. **Ubicar el código fuente de KiroCrew** y comparar contra el commit de
   referencia: `acp/types.py` (constantes/versión), `acp/harness/kiro.py`
   (argv + protocolVersion), `acp/_dispatch.py` (tool_call/permisos/stopReason),
   y `testing/fake_acp_backend.py` (la PLANTILLA canónica del propio proyecto).
3. **Correr `python3 verify.py`** (verificador de protocolo offline con stub). Si
   pasa, el handshake + turno + tool-loop siguen compatibles.
4. **Levantar el Docker**: `cd docker && docker compose up -d --force-recreate`,
   sacar token con `./login.sh`, abrir el dashboard y probar un chat real.
5. Si un one-shot falla, **recapturar la salida real del kiro-cli nuevo**
   (`--version`, `whoami --format json`, `chat --list-models --format json`,
   `acp --help`) y ajustar `_handle_one_shot` en `dharma_cli.py`.
6. Re-verificar (3-4) y commitear.

## Gotchas

- **Tras mover/renombrar archivos montados en Docker: `up --force-recreate`, NO
  `restart`** — un restart no rehace el bind mount y KiroCrew cae al kiro-cli real
  ("not logged in"), lo que confunde el diagnóstico.
- El error "kiro-cli is not logged in" casi nunca es login: suele ser que KiroCrew
  no encontró el faux (ruta de montaje mala) y cayó al binario real.
- **El BACKEND ejecuta las herramientas, no KiroCrew.** El faux emite `tool_call`
  cuando el modelo (function calling) las pide, las ejecuta, y realimenta el
  resultado hasta el texto final.
- Usar NOMBRES CANÓNICOS de herramientas (`execute_bash`, `fs_read`, `fs_write`…,
  de `config/defaults.json` y kiro.dev/docs/tools), no inventados.
- Config: `DHARMA_*` con fallback a `FAUX_*` (no romper .env viejos).
- La aprobación es POR HERRAMIENTA, no por capacidad: bloquear `fs_write` pero
  permitir `execute_bash` deja al modelo escribir con `echo > archivo`. Aceptable
  para uso personal; documentado, no un bug.
- `web_search` no implementada (necesita API de pago); `web_fetch` sí.
