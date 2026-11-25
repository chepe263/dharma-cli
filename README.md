# dharma-cli

Un binario que se hace pasar por `kiro-cli` ante **Kiro Crew**, pero que por
dentro habla con **OpenCode Go** (vía `opencode acp`) o con **cualquier endpoint
compatible con OpenAI** (Ollama Cloud, un Ollama local, OpenAI, LM Studio…).
Objetivo: correr Kiro Crew sin pagar `kiro-cli`, sin el servicio de Amazon y sin
lock-in.

> **El nombre.** De la serie *Dharma & Greg*: Dharma es el espíritu libre, sin
> ataduras; Greg es el abogado formal y estructurado, atado a las reglas de su
> mundo. Opuestos que conviven porque se entienden. Aquí:
> **Greg es el `kiro-cli` real** — el oficial, atado al sistema (Amazon, login,
> créditos). **Dharma es este faux** — llega de fuera, sin esas ataduras (sin
> Amazon, sin lock-in), pero habla el mismo idioma que Greg y ocupa su lugar sin
> fricción. Y como la Dharma de la serie, su papel es **conectar** dos mundos que
> de otro modo no encajarían: une Kiro Crew (el mundo de Greg) con cualquier
> modelo que tú traigas, y los hace convivir. `dharma-cli` es la Dharma de tu
> kiro-cli: te libera del lock-in sin romper nada aguas abajo.

> Estado: **funciona de punta a punta.** Chat de texto, comandos de arranque sin
> Amazon, function calling / herramientas (crear, leer, editar, ejecutar), todo
> verificado corriendo en Docker por la interfaz web. Ver *Roadmap* para lo que
> falta pulir.

## Cómo funciona

Kiro Crew no habla con el modelo mediante "comandos"; lanza **un proceso** que
habla el protocolo **ACP** (Agent Client Protocol): mensajes JSON-RPC, uno por
línea, por stdin/stdout. Este proyecto es ese proceso. Traduce cada turno ACP en
una llamada a `<BASE_URL>/chat/completions` y devuelve la respuesta en streaming
como notificaciones ACP. Kiro Crew no nota la diferencia.

```
Kiro Crew  ──ACP (stdio)──►  dharma_cli.py  ──HTTP /v1/chat/completions──►  tu modelo
           ◄──streaming────                    ◄──────SSE (stream)──────────
```

## Backend recomendado: puente ACP a OpenCode (sin API key)

Si ya tienes OpenCode con un plan **Go**, no necesitas API key de Console ni
configurar headers: el propio CLI ya autentica y ya envía `x-opencode-session`.
Dharma actúa como **traductor de dialectos ACP**:

```
Kiro Crew ──ACP kiro (2025-08-22)──► dharma_opencode_acp.py ──ACP v1──► opencode acp ──► opencode-go/...
           ◄──tool_call / chunks────         ◄──tool_call / chunks──────
```

Traduce `initialize`, `session/new`, `session/set_model`
(→ `session/set_config_option`), `session/prompt`, `session/cancel`, cierre de
sesión y `session/request_permission`. Las herramientas y sus tarjetas llegan a
Kiro Crew igual que con el `kiro-cli` real.

El **steering en mitad del turno** también funciona: Kiro Crew envía
`_session/steer` (texto `<user_message>...</user_message>`); el puente responde
`{queued: true}`, emite `steering_queued` / `steering_consumed` y mete el texto
en el turno en curso de OpenCode como un segundo `session/prompt` (mecanismo de
steering de OpenCode). Sin esto, Kiro se quedaba "pensando" con el mensaje en
estado *steering*: el shim respondía `{}` y OpenCode nunca veía el texto.

El puente anuncia **`loadSession: true`** y mantiene **una sesión de OpenCode
por chat de Kiro** (`~/.kiro/crew/dharma_session_map.json`). El chat key es el
`sid=chat-<n>-<ts>` que sale en la URL del dashboard (`dashboard:chat-...`).
Kiro Crew hace muchas `session/new` efímeras por chat (título, sugerencias,
consolidación); todas las que traen el mismo `sessionKey` se traducen a un único
`session/load` de OpenCode: el historial se conserva y no se acumula una sesión
por solicitud. Las tareas de fondo sin `sessionKey` caen al `cwd`.

Kiro Crew **no manda ese `sid` en `session/new`** en la ruta `AcpRuntime` (es la
que usa este gateway; `KIROCREW_SESSION_KEY` solo se exporta en `AcpClient`).
Para exponerlo sin tocar `/opt` (root, no editable) se usa un overlay
`sitecustomize.py` cargado con `PYTHONPATH` desde la unidad systemd: envuelve
`AcpRuntime.create_session` y `build_session_new_params` y añade
`params["_meta"]["kiro"]["sessionKey"]`. Archivo:
`~/.config/kirocrew-dharma/sitecustomize.py`. Si se quita esa variable de
`PYTHONPATH`, dharma cae al fallback por `cwd`.

El trabajo paralelo se hace con los subagentes (`task`) de OpenCode dentro de
esa sesión; sus frames con `_meta.opencode/child-session` y sus peticiones de
permiso se reenvían a Kiro Crew tal cual.

Las sesiones creadas se anotan en `~/.kiro/crew/dharma_opencode_sessions.json`;
el temporizador semanal `kirocrew-session-cleanup.timer` borra las viejas
(conserva las 20 más recientes y hasta 14 días), incluidas las sesiones
**hijas** de los subagentes, y nunca toca la sesión activa del mapa. Para forzar:

```bash
python3 cleanup_opencode_sessions.py            # simulación
python3 cleanup_opencode_sessions.py --apply    # borrar
```

```bash
# .env
DHARMA_BACKEND=opencode-acp
DHARMA_OPENCODE_BIN=opencode
DHARMA_OPENCODE_MODEL=hungerg-free     # gratis; también hy4-flash-free, beam-free...

python3 verify_acp_bridge.py           # prueba en vivo, sin coste con modelos free
```

> ¿Por qué no seleccionar directamente el backend `opencode` de Kiro Crew?
> Kiro Crew 0.13.x solo acepta OpenCode **major 1.x**; con OpenCode 2.x el launch
> se rechaza (`_OPENCODE_TESTED_MAJOR = 1`). El puente evita ese cerrojo porque
> Kiro Crew cree que está usando el harness `kiro`.

### Modo HTTP (alternativa)

Con `DHARMA_BACKEND=openai-http` (el default de siempre) dharma llama directo al
endpoint. Para OpenCode Go:

```bash
DHARMA_BASE_URL=https://opencode.ai/inference/go/openai/v1
DHARMA_API_KEY=oc_sk_...                       # API key de OpenCode Console
DHARMA_MODEL=hy4-flash-free
```

Dharma añade automáticamente `Authorization: Bearer ...`, un `User-Agent` propio
y un **`x-opencode-session` estable por conversación** (ligado al `sessionId`
ACP), que es lo que Go exige para enrutar y reutilizar la caché de prompt.
También puedes inyectar headers arbitrarios con `DHARMA_EXTRA_HEADERS` (JSON).

## Instalar en una máquina nueva (Docker — recomendado)

La forma limpia y portable. Requiere solo Docker.

```bash
git clone <este-repo> dharma-cli && cd dharma-cli
./install.sh          # pide API key/modelo, detecta la IP, arranca el contenedor
./login.sh            # saca el token de login (una vez)
```

Abre `http://127.0.0.1:5477` (o `http://<IP-del-host>:5477` desde otra máquina).

Para arrancar al boot (opcional):

```bash
./systemd/install-service.sh
```

Todo el estado (chats, config, memoria) vive en el volumen Docker
`dharma-kirocrew-home` y sobrevive a reinicios y recreaciones del contenedor.

### La imagen del contenedor

El `compose.yaml` trae **dos fuentes** para la imagen (Node/.NET/PHP horneados):
la **publicada** en GHCR (`ghcr.io/chepe263/dharma-kirocrew:latest`, hecha por
GitHub Actions) y la **build local** (`docker/Dockerfile`). Ojo con el matiz de
Docker Compose: `docker compose up` a secas usa la imagen local si ya existe y,
si no, la **construye** — NO baja la de GHCR sola. Por eso, para usar la de
GitHub hay que pedirla explícitamente:

```bash
cd docker

# ── Usar la imagen PUBLICADA en GitHub (bajar, no compilar) ──
docker compose pull            # baja ghcr.io/chepe263/dharma-kirocrew:latest
docker compose up -d           # la usa
# (o en un paso)  docker compose up -d --pull always

# ── Construir LOCALMENTE en su lugar (usa CPU tuyo) ──
docker compose build
docker compose up -d

# ── Con perfil de base de datos (MariaDB) ──
docker compose --profile db up -d --pull always
```

`install.sh` hace el camino de imagen publicada por ti. Para fijar otra imagen o
tag: `export DHARMA_IMAGE=ghcr.io/chepe263/dharma-kirocrew:sha-abc1234`.

La imagen la construye y publica GitHub Actions
(`.github/workflows/publicar-imagen.yml`) en cada cambio del `docker/Dockerfile`,
con botón manual (Run workflow), y semanalmente para rehornear sobre el KiroCrew
oficial más reciente.

> **Paso manual una sola vez (dueño del repo):** tras la PRIMERA corrida del
> workflow, el paquete en GHCR nace **privado**. Para poder `docker pull` sin
> `docker login`, hazlo público en GitHub → tu perfil → Packages →
> `dharma-kirocrew` → Package settings → Change visibility → Public.
> Mientras siga privado, cada máquina debe `docker login ghcr.io` primero, o
> construir la imagen localmente con `docker compose build`.
No hay venvs ni rutas del sistema que alinear: el contenedor encapsula todo.

## Configuración

Copia `.env.example` a `.env` y rellena:

| Variable | Qué es | Ejemplo (Ollama Cloud) |
|---|---|---|
| `DHARMA_BASE_URL` | endpoint OpenAI-compatible | `https://ollama.com/v1` |
| `DHARMA_API_KEY` | token Bearer | tu key de `https://ollama.com/settings/keys` |
| `DHARMA_MODEL` | id del modelo (default de la lista) | `gpt-oss:20b` |
| `DHARMA_APPROVAL` | 1 = pedir permiso para tools sensibles, 0 = directo | `1` |
| `DHARMA_MAX_ROUNDS` | máx. rondas de herramientas por turno | `25` |
| `DHARMA_MAX_HISTORY` | máx. mensajes de historial (ventana deslizante, 0=∞) | `40` |
| `DHARMA_PORT` | puerto del dashboard (Docker) | `5477` |

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

## Probarlo con Kiro Crew de verdad (Docker)

El experimento vive en `docker/`. Corre la imagen oficial `kirocrew:stable` con
este faux montado y `KIROCREW_KIRO_BIN` apuntándolo, así Kiro Crew lo lanza en
lugar de kiro-cli.

```bash
cd docker && docker compose up -d      # levanta el gateway con el faux
cd .. && ./login.sh                     # mintea token e imprime el link listo
```

`login.sh` detecta el puerto publicado y la IP del host y te imprime el enlace ya
armado (variante `localhost` y variante IP-LAN), para no cambiar a mano el host y
el puerto del link que imprime `kirocrew token`.

Notas del experimento (descubiertas montando y viendo dónde falla):
- El contenedor bloquea user-namespaces, así que se usa `KIROCREW_ALLOW_UNSANDBOXED=1`
  (el contenedor es la única frontera — aceptable para pruebas).
- El puerto por defecto es `5477` (el `5476` suele estar ocupado por un Kiro Crew real).
- Para abrir desde otra máquina, la IP:puerto debe estar en `KIROCREW_CORS_ORIGINS`.

Estado verificado en vivo: el gateway arranca `healthy`, el dashboard carga por
web, y un turno de chat de **texto** responde contra el modelo configurado, sin
Amazon. El **function calling** (multi-turno con herramientas) es el siguiente
paso — ver *Roadmap*.

## Archivos

- `dharma_cli.py` — el faux backend: one-shots, modo HTTP y despacho al puente ACP.
- `dharma_opencode_acp.py` — puente ACP kiro ↔ ACP v1 hacia `opencode acp`.
- `verify.py` — verificador offline del modo HTTP (comprueba el header de sesión).
- `verify_acp_bridge.py` — verificador en vivo del puente ACP (modelos free).
- `stub_openai_server.py` — endpoint OpenAI falso, solo para las pruebas offline.
- `.env.example` — plantilla de configuración.

## Roadmap

1. ~~Herramientas (function calling).~~ **Hecho** en ambos modos: el modo HTTP
   ejecuta las tools propias; el puente ACP reenvía las de OpenCode.
2. ~~Flujo de primera vez / login.~~ **Hecho**: los one-shots (`whoami`,
   `--list-models`, `login`) se responden sin Amazon SSO.
3. **Arranque de Kiro Crew sin CLI.** Dejar un lanzador que exporte
   `KIROCREW_KIRO_BIN` hacia dharma y fije `agent.acp_backend: "kiro"`.
4. **Docker + Playwright.** Contenedor desechable para el bucle autónomo
   construir→verificar→corregir, y Playwright para validar el uso real por la web.

## Créditos

Basado en la lectura del código abierto de Kiro Crew
(`github.com/kirodotdev/KiroCrew`, commit de referencia y archivos fuente en `PROVENANCE.md`; contrato ACP en
`docs/system-specs/modules/acp-client.md`).
