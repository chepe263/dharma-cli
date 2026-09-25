# dharma-cli — features pendientes (por prioridad)

Estado base: handshake ACP, gate de login sin Amazon, chat de texto, function
calling multi-turno, herramientas de archivos/shell, lista de modelos real del
endpoint, y cierre de sesión — todo funcionando y publicado.

Este spec ordena lo que falta en tres categorías. Se implementan **esenciales** y
**bueno-tener**; las **no necesarias** se dejan documentadas para el futuro.

---

## Esenciales (hacer)

### E1 — Respetar el `--model` del selector
Hoy la lista de modelos es real, pero al elegir uno el dharma lo ignora y sigue
con `DHARMA_MODEL` del `.env`. KiroCrew pasa el modelo elegido por el flag
`--model` al arrancar y/o por `session/set_model` en vivo. Dharma debe:
- leer `--model <id>` del argv al arrancar y usarlo como modelo activo;
- al recibir `session/set_model` (params con el modelo), actualizar el modelo
  activo de la sesión en vez de solo responder `{}`.
Impacto: el selector de modelos por fin sirve.

### E2 — Aprobación de herramientas (allow/reject)
Hoy dharma ejecuta las herramientas directo, sin pedir permiso. Como
`execute_bash` corre comandos reales, esto es un riesgo. Debe emitir
`session/request_permission` antes de ejecutar una herramienta sensible (shell,
escritura, borrado) y respetar la respuesta de KiroCrew
(`result.outcome.outcome == "selected"|"cancelled"`, `optionId`). Un `cancelled`
o un reject NO ejecuta la herramienta y lo refleja en el `tool_call_update`
(`status: "failed"`). Lectura pura (fs_read, list_directory, search) puede seguir
sin permiso. Configurable con una variable (ej. `DHARMA_APPROVAL=1`) para poder
desactivarlo en pruebas.
Impacto: pasa de "juguete" a "usable con confianza".

---

## Bueno tener (hacer)

### B1 — Streaming de texto de verdad
Hoy `_chat_once` usa `"stream": False` y la respuesta llega de golpe. Cambiar a
streaming (SSE de `/chat/completions`) y emitir cada fragmento como su propio
`agent_message_chunk`, para que el texto se escriba en vivo en el chat. Ojo: el
bucle de function calling necesita detectar tool_calls, que en streaming llegan
fragmentados — hay que acumular los deltas de `tool_calls` antes de ejecutarlos.

### B2 — `web_fetch` real
Hoy declarada pero devuelve "no implementada". Implementar como un GET con urllib
que descarga la URL y devuelve el texto (con límite de tamaño y timeout, como la
doc oficial: 10MB / 30s). `web_search` se queda como no-necesaria (necesita un
motor de búsqueda externo).

---

## No necesarias (documentar, no hacer)

- **web_search real**: necesita una API de búsqueda externa (de pago o con clave).
  Fuera de alcance para un backend gratis; el modelo puede usar `web_fetch` +
  `execute_bash` con curl si hay red.
- **Reasoning effort / thinking**: KiroCrew tiene control de esfuerzo de
  razonamiento; solo importa con modelos que "piensan". Traducirlo es marginal.
- **code (análisis AST), introspect, todo_list, invoke_subagent**: herramientas
  del catálogo atadas a maquinaria interna de Kiro; complejas y poco críticas para
  uso general.
- **Contador de créditos**: concepto de Amazon; cosmético (la tarjeta roja).
- **/api/instances**: función multi-instancia/flota; no aplica a este caso.
