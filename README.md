# faux-kiro-acp

Un binario que se hace pasar por `kiro-cli` ante **Kiro Crew**, pero que por
dentro habla con **cualquier endpoint compatible con OpenAI** (Ollama Cloud, un
Ollama local, OpenAI, LM Studio…). Objetivo: correr Kiro Crew con tu propio
modelo, sin el servicio de Amazon y sin lock-in.

> Estado: **MVP / prueba de concepto.** El turno de conversación de texto funciona
> de punta a punta (verificado). Las herramientas (function calling) y el flujo de
> instalación por la web son los siguientes pasos — ver *Roadmap*.

## Cómo funciona

Kiro Crew no habla con el modelo mediante "comandos"; lanza **un proceso** que
habla el protocolo **ACP** (Agent Client Protocol): mensajes JSON-RPC, uno por
línea, por stdin/stdout. Este proyecto es ese proceso. Traduce cada turno ACP en
una llamada a `<BASE_URL>/chat/completions` y devuelve la respuesta en streaming
como notificaciones ACP. Kiro Crew no nota la diferencia.

```
Kiro Crew  ──ACP (stdio)──►  faux_kiro_cli.py  ──HTTP /v1/chat/completions──►  tu modelo
           ◄──streaming────                    ◄──────SSE (stream)──────────
```

## Configuración

Copia `.env.example` a `.env` y rellena:

| Variable | Qué es | Ejemplo (Ollama Cloud) |
|---|---|---|
| `FAUX_BASE_URL` | endpoint OpenAI-compatible | `https://ollama.com/v1` |
| `FAUX_API_KEY` | token Bearer | tu key de `https://ollama.com/settings/keys` |
| `FAUX_MODEL` | id del modelo | `gpt-oss:20b` |

Ollama Cloud tiene acceso API gratuito para pruebas. También sirve un Ollama local
(`http://localhost:11434/v1`, la key se ignora) o el OpenAI oficial.

## Verificar (sin gastar API)

```bash
python3 verify.py
```

Levanta un endpoint stub local (`stub_openai_server.py`, solo `127.0.0.1`), lanza
el faux apuntado a él, y reproduce el handshake exacto de Kiro Crew
(`initialize → session/new → session/set_mode → session/prompt`), comprobando el
texto en streaming y el fin de turno. Es la verificación de "capa 1": determinista,
gratis, offline.

## Usarlo con Kiro Crew de verdad

Kiro Crew resuelve el binario de kiro-cli por la variable `KIROCREW_KIRO_BIN`.
Apuntándola a un envoltorio que ejecute este script, Kiro Crew lo lanzará en su
lugar. (El flujo de instalación completo — que Kiro Crew no exija el login de
Amazon — es parte del *Roadmap*.)

## Archivos

- `faux_kiro_cli.py` — el faux backend (ACP ↔ OpenAI-compatible). Sin dependencias.
- `verify.py` — verificador de protocolo de capa 1.
- `stub_openai_server.py` — endpoint OpenAI falso, solo para las pruebas offline.
- `.env.example` — plantilla de configuración.

## Roadmap

1. **Herramientas (function calling).** Hoy funciona el texto simple. Falta mapear
   las peticiones de herramienta de ACP (`session/request_permission`) ↔ el campo
   `tools` de la API OpenAI (Ollama lo soporta). Es la pieza técnica de verdad.
2. **Flujo de primera vez / login.** Que Kiro Crew dé por satisfecho su
   prerrequisito sin el SSO de Amazon (con la API key configurada aquí).
3. **Backend de primera clase.** En vez de suplantar el binario, registrar un
   backend nuevo (`ACP_BACKEND_OPENAI`) siguiendo `harness-onboarding.md` de Kiro
   Crew — más robusto que imitar el dialecto exacto de kiro-cli.
4. **Docker + Playwright.** Contenedor desechable para el bucle autónomo
   construir→verificar→corregir, y Playwright para validar el uso real por la web.

## Créditos

Basado en la lectura del código abierto de Kiro Crew
(`github.com/kirodotdev/KiroCrew`, contrato ACP en
`docs/system-specs/modules/acp-client.md`).
