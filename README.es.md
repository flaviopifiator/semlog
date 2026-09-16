[English](README.md) | **Español**

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/semlog-logo-dark.svg">
    <img alt="semlog" src="docs/assets/semlog-logo-light.svg" width="360">
  </picture>
</p>

# semlog

**Registro estructurado en JSON sobre `logging` de la biblioteca estándar de Python: un contrato de registro estable y respaldado por un esquema, con nombres de campo de OpenTelemetry, W3C Trace Context y cero dependencias.**

[![CI](https://github.com/flaviopifiator/semlog/actions/workflows/ci.yml/badge.svg)](https://github.com/flaviopifiator/semlog/actions/workflows/ci.yml)
[![Python 3.10-3.14](https://img.shields.io/badge/python-3.10--3.14-3776AB?logo=python&logoColor=white)](.github/workflows/ci.yml)
[![FastAPI 0.71+](https://img.shields.io/badge/FastAPI-%E2%89%A5%200.71-009688?logo=fastapi&logoColor=white)](.github/workflows/ci.yml)
[![Django 3.2.9+](https://img.shields.io/badge/Django-%E2%89%A5%203.2.9-092E20?logo=django&logoColor=white)](.github/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Runtime dependencies: 0](https://img.shields.io/badge/runtime%20dependencies-0-brightgreen)](pyproject.toml)
[![Requirements proven: 104/104](https://img.shields.io/badge/requirements%20proven-104%2F104-brightgreen)](STANDARDS.md)
[![PyPI](https://img.shields.io/pypi/v/semlog)](https://pypi.org/project/semlog/)

## Por qué semlog

El código de la aplicación sigue usando `logging.getLogger(__name__)`; semlog define la forma de cada registro y convierte esa forma en un contrato.

- **Un contrato JSON estable.** El orden de los campos, sus tipos y las reglas de `null` están especificados en [STANDARDS.md](STANDARDS.md) y publicados como JSON Schema ([`schemas/log-record.schema.json`](schemas/log-record.schema.json)). Eliminar o renombrar un campo es un cambio de versión mayor.
- **Nombres de campo de OpenTelemetry.** `severity_text`, `severity_number`, `trace_id`, `span_id`, `service.*` y `telemetry.sdk.*` siguen el modelo de datos de logs de OpenTelemetry, con las convenciones semánticas fijadas en la versión v1.44.0.
- **W3C Trace Context integrado.** Los middlewares WSGI y ASGI leen `traceparent`, `tracestate` y el `baggage` permitido, e `inject()` los propaga en las llamadas salientes. No se necesita ningún SDK de trazas.
- **Cero dependencias de tiempo de ejecución.** semlog está construido íntegramente sobre la biblioteca estándar. Los loggers de terceros que propagan hacia el logger raíz pasan por la misma tubería, y `capture_loggers` cubre los que instalan sus propios manejadores.
- **Trazabilidad de requisitos.** Cada requisito normativo de STANDARDS.md tiene un identificador y al menos una prueba que lo cita; la suite de pruebas falla cuando un requisito no tiene ninguna prueba que lo cite.

## Instalación

semlog requiere Python 3.10 o posterior.

```bash
pip install semlog
```

Con uv, para añadir semlog a un proyecto:

```bash
uv add semlog
```

O para instalarlo en un entorno:

```bash
uv pip install semlog
```

## Guía rápida

Guarde este código como `app.py`:

```python
import logging

import semlog

semlog.configure(service_name="checkout")

logger = logging.getLogger(__name__)
logger.info("order.created", extra={"app.order.id": "ord_42", "app.order.total": 1500})
```

Ejecute `python app.py`. Imprime una línea JSON:

```json
{"timestamp":"2026-09-14T18:49:31.659966Z","severity_text":"INFO","severity_number":9,"event_name":"order.created","body":null,"otel.scope.name":"__main__","app.order.id":"ord_42","app.order.total":1500,"service.name":"checkout","service.namespace":null,"service.version":null,"service.instance.id":"b821b60b-7f5e-44a9-88f8-7cf734286abe","deployment.environment.name":null,"telemetry.sdk.name":"semlog","telemetry.sdk.version":"0.2.0","telemetry.sdk.language":"python"}
```

Tres reglas mantienen útiles los registros:

- El mensaje es un nombre de evento estático, como `order.created`, nunca un f-string.
- Las variables van en `extra`, bajo `app.` o bajo una clave de OpenTelemetry como `url.path`.
- `logger.exception(...)` se llama una sola vez, donde se maneja la excepción.

### API pública

semlog expone exactamente ocho nombres. Todo lo demás sigue siendo `logging` estándar.

| Nombre | Sirve para |
|---|---|
| `configure(...)` | Configurar el proceso una sola vez, al arrancar |
| `WSGIMiddleware(app)` | Envolver una aplicación WSGI: contexto de traza y `http.request.id` en cada registro de una solicitud |
| `ASGIMiddleware(app)` | Lo mismo, para una aplicación ASGI |
| `operation(headers=None)` | Correlacionar trabajo fuera de HTTP, como jobs, consumidores de colas y comandos de CLI |
| `bind(attributes)` | Agregar campos a todos los registros posteriores de la solicitud u operación vigente |
| `inject(headers, *, trusted=True)` | Propagar el contexto de traza a una llamada saliente |
| `flush(timeout=None)` | Esperar a que se escriban todos los registros encolados, antes de `os._exit()` o en pruebas |
| `llm()` | Devolver la guía para agentes incluida en la versión instalada (también `python -m semlog llm`) |

Las firmas completas y su semántica están en la [guía para agentes](src/semlog/agent_guide.md).

## Recetas para frameworks

### FastAPI

```python
import logging

import semlog
from fastapi import FastAPI
from semlog import ASGIMiddleware

semlog.configure(service_name="my-fastapi-service")

app = FastAPI()
app = ASGIMiddleware(app, log_requests=True)
# log_requests=True emits one http.server.request event per request (INFO on
# success, ERROR when the application raises an unhandled exception), carrying
# http.request.method, url.path, http.response.status_code and event.duration
# (nanoseconds). It never includes url.query. Default is False: no extra event.

logger = logging.getLogger(__name__)


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    logger.info("order.lookup.started", extra={"app.order.id": order_id})
    ...


@app.get("/reports/{report_id}")
def generate_report_sync(report_id: str):
    # A sync `def` endpoint also keeps trace context: Starlette runs it in a
    # threadpool worker with an explicit contextvars.copy_context().
    logger.info("report.generation.started", extra={"app.report.id": report_id})
    ...
```

### Django

Envuelva el punto de entrada WSGI o ASGI y desactive la configuración de logging propia de Django, para que no reemplace el manejador raíz que instala `configure()`.

```python
# wsgi.py
import semlog
from django.core.wsgi import get_wsgi_application
from semlog import WSGIMiddleware

semlog.configure(service_name="my-django-service")
application = WSGIMiddleware(get_wsgi_application(), log_requests=True)
```

```python
# asgi.py
import semlog
from django.core.asgi import get_asgi_application
from semlog import ASGIMiddleware

semlog.configure(service_name="my-django-service")
application = ASGIMiddleware(get_asgi_application(), log_requests=True)
```

```python
# settings.py
LOGGING_CONFIG = None  # Django must not replace the root handler configure() installs
```

## Configuración

Todos los parámetros de `configure()` son de solo palabra clave (*keyword-only*); no existe un objeto ni un diccionario de configuración. Un valor o una combinación inválidos lanzan `ValueError` de inmediato, al arrancar, nunca más tarde en tiempo de ejecución.

| Grupo | Parámetro | Valor por defecto | Notas |
|---|---|---|---|
| Identidad | `service_name` | detectado | parámetro > `OTEL_SERVICE_NAME` > `OTEL_RESOURCE_ATTRIBUTES` > `pyproject.toml` |
| Identidad | `service_version` | detectado | parámetro > `OTEL_RESOURCE_ATTRIBUTES` > versión del paquete instalado > `pyproject.toml` |
| Identidad | `service_namespace` | `None` | parámetro > `OTEL_RESOURCE_ATTRIBUTES` |
| Identidad | `service_instance_id` | un UUIDv4 por proceso | parámetro > `OTEL_RESOURCE_ATTRIBUTES` |
| Identidad | `environment` | `None` | parámetro > `OTEL_RESOURCE_ATTRIBUTES` |
| Identidad | `identity` | `None` | un valor por cada nivel nombrado en `identity_levels` |
| Identidad | `identity_levels` | `("role", "component")` | nombres de los niveles que componen el campo `{namespace}.identity` |
| Salida | `level` | `"INFO"` | nivel efectivo del logger raíz |
| Salida | `namespace` | `"app"` | raíz del espacio de nombres de los atributos personalizados |
| Salida | `capture_loggers` | `()` | loggers de terceros a los que se retiran sus propios manejadores y se activa la propagación |
| Salida | `search_dir` | `None` (directorio de trabajo actual) | directorio desde el que la detección de `pyproject.toml` busca hacia arriba |
| Privacidad | `redact_keys` | `()` | se suman a la lista de censura integrada, que no se puede desactivar |
| Contexto distribuido | `baggage_allow` | `()` | claves de *baggage* copiadas como atributos; valor por defecto del proceso para los middlewares y `operation()` |
| Contexto distribuido | `baggage_prefix` | `"baggage."` | prefijo de las claves de los atributos copiados desde *baggage* |
| Contexto distribuido | `accept_inbound_baggage` | `True` | `False` ignora por completo un encabezado `baggage` entrante, para fronteras de confianza públicas |
| Límites | `max_attributes` | 128 (o variable de entorno) | ver la sección 6 de STANDARDS.md |
| Límites | `max_attribute_length` | sin límite (o variable de entorno) | ver la sección 6 de STANDARDS.md |
| Transporte | `queue` | `True` | `False` escribe de forma síncrona, sin cola interna ni hilo escritor |
| Transporte | `queue_size` | `10000` | cantidad máxima de líneas en la cola |
| Transporte | `overflow` | `"block"` | `"block"` o `"drop"`, ver [Desborde de la cola](#desborde-de-la-cola) |
| Modo | `mode` | `None` (se resuelve a `"full"`) | `"full"`, `"hybrid"` o `"off"`; parámetro > `SEMLOG_MODE` > `[tool.semlog].mode` (3.11+) > `"full"` |
| Catálogo | `catalog` | `None` | documento del catálogo de eventos (JSON) |
| Catálogo | `catalog_mode` | `"off"` sin catálogo, `"warn"` con catálogo | `"off"`, `"warn"` o `"strict"` |

### Precedencia y variables de entorno

Para los parámetros de identidad y de límites que siguen, el orden de precedencia, de mayor a menor, es siempre:

1. un parámetro explícito de `configure()`;
2. la variable de entorno `OTEL_*`;
3. `pyproject.toml`, solo en Python 3.11 y posteriores;
4. el valor por defecto.

`mode` sigue su propio orden de precedencia, descrito en [Modos](#modos).

Variables de entorno reconocidas:

| Variable | Define |
|---|---|
| `OTEL_SERVICE_NAME` | `service.name` (`service_name`) |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace` (`service_namespace`), `service.version` (`service_version`), `service.instance.id` (`service_instance_id`) y `deployment.environment.name` (`environment`); también es el respaldo de `service.name` (`service_name`) |
| `OTEL_LOGRECORD_ATTRIBUTE_COUNT_LIMIT`, `OTEL_ATTRIBUTE_COUNT_LIMIT` | el límite `max_attributes` |
| `OTEL_LOGRECORD_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` | el límite `max_attribute_length` |
| `SEMLOG_MODE` | `mode` (ver [Modos](#modos)) |

Cuando las dos variables de un límite están definidas, la variable `OTEL_LOGRECORD_*` gana sobre la genérica `OTEL_ATTRIBUTE_*`.

Detección desde `pyproject.toml`:

- En Python 3.11 y posteriores, `service_name` y `service_version` se leen de la sección `[project]` con `tomllib` de la biblioteca estándar; `service_name` usa `[tool.poetry]` como respaldo.
- Python 3.10 no incluye `tomllib`, así que en 3.10 `pyproject.toml` no se lee y un diagnóstico de arranque indica el motivo.

### Desborde de la cola

Cada registro se renderiza en el hilo que hace la llamada y se encola para un único hilo escritor, que lo escribe en `stdout`. Cuando la cola se llena, `overflow` define qué ocurre:

| Modo | Comportamiento | Cuándo usarlo |
|---|---|---|
| `overflow="block"` (por defecto) | La llamada espera hasta que haya espacio en la cola; no se pierde ningún registro | No se puede perder ningún registro y una espera ocasional es aceptable |
| `overflow="drop"` | La llamada nunca espera. Al 90 % de ocupación se descartan los registros por debajo de `WARNING`; el 10 % restante se reserva para `WARNING`, `ERROR` y `CRITICAL`. Cada descarte se cuenta y se reporta | La latencia de la aplicación importa más que la completitud del registro |
| `queue=False` | Escritura síncrona, sin cola ni hilo escritor | Scripts cortos, depuración, entornos sin hilos |

## Modos

`mode` selecciona cuánto de semlog está activo en un proceso: `"full"` (el valor por defecto, y la opción correcta para un servicio nuevo), `"hybrid"` y `"off"`.

Para un servicio que ya está en producción, con sus propias líneas de registro existentes, `hybrid` es la vía de adopción: cada línea que el servicio ya imprime sigue imprimiéndose de forma idéntica byte a byte, y una llamada escrita con la palabra clave `semlog=True` queda oculta de esa misma salida impresa y se convierte, en su lugar, en un registro JSON. `logger.info("event.name", extra={...}, semlog=True)` es la forma de una llamada que adopta semlog de esta manera. `off` se comporta como si semlog nunca se hubiera instalado, salvo que la propia palabra clave nunca lanza una excepción, de modo que resulta seguro dejarla en los puntos de llamada durante una reversión. `full` es el estado final, y el valor por defecto para un servicio nuevo que no tiene líneas de registro existentes que preservar: la vía de adopción es `hybrid`, y luego `full` una vez revisada su salida. `mode` se cambia por entorno, sin necesidad de modificar código.

- **`full`**: cada llamada de registro pasa por la tubería de semlog y se convierte en un registro JSON por línea, exactamente como se muestra en [Salida](#salida).
- **`hybrid`**: `configure()` nunca toca los manejadores ni el nivel existentes del logger raíz. Una llamada hecha con `semlog=True` queda oculta de todo `StreamHandler` y se emite, en su lugar, como un registro JSON de semlog; cualquier otra llamada sigue imprimiéndose exactamente igual que antes de instalar semlog. Ver [Limitaciones](#limitaciones) para el alcance exacto de esta supresión.
- **`off`**: `configure()` no instala nada y no toca el logger raíz. `semlog=True` sigue sin lanzar nunca una excepción, pero no produce salida JSON ni ningún otro efecto propio; `operation()`, `bind()` y ambos middlewares siguen funcionando como mecanismos de paso inertes.

### Fuentes de configuración

`mode` se resuelve, en orden de precedencia:

1. el argumento de palabra clave `mode` de `configure()`;
2. la variable de entorno `SEMLOG_MODE`;
3. la clave `mode` bajo `[tool.semlog]` en `pyproject.toml`;
4. el valor por defecto, `"full"`.

```toml
[tool.semlog]
mode = "hybrid"
```

Leer `pyproject.toml` necesita `tomllib` de la biblioteca estándar, disponible desde Python 3.11 en adelante; en Python 3.10 esta fuente se omite por completo, y la resolución continúa con la siguiente. El archivo se busca a partir del directorio de trabajo actual (o de `configure(search_dir=...)`, si se indica) y hacia arriba por sus directorios padres; una imagen de contenedor construida sin el árbol de fuentes del proyecto presente, o sin el directorio de trabajo apuntando a él, con frecuencia no tiene ningún `pyproject.toml` que encontrar, en cuyo caso esta fuente se omite en silencio, igual que en Python 3.10. Una `SEMLOG_MODE` vacía se trata como ausente, igual que si no estuviera definida, y la resolución continúa con la siguiente fuente; esto no aplica a `[tool.semlog].mode`, donde una cadena vacía es un valor declarado real. Un valor presente pero fuera de `"full"`, `"hybrid"`, `"off"`, proveniente de cualquiera de las tres fuentes, lanza `ValueError` nombrando tanto el valor inválido como la fuente de la que proviene.

### Limitaciones

- **La supresión de hybrid está limitada a `StreamHandler.handle`.** Solo las instancias de `logging.StreamHandler` y sus subclases que llegan a ese método quedan ocultas de un registro marcado. Un manejador fuera de ese despacho síncrono, como un `logging.handlers.QueueHandler` emparejado con un `QueueListener`, o un `logging.handlers.MemoryHandler`, puede seguir renderizando un registro marcado como texto; lo mismo puede ocurrir con una subclase de `StreamHandler` que sobrescribe `handle()` sin llamar a `super().handle()`. Nada de esto es un defecto, solo el límite documentado de lo que alcanza un parche a nivel de método.
- **`off` es un interruptor de inicio de proceso, no un cambio en caliente.** Un proceso que arranca en modo `off` (o con `SEMLOG_MODE=off`) se comporta como si semlog nunca se hubiera instalado. Reconfigurar un proceso ya en ejecución de `full` o `hybrid` a `off` deja instalada, sin desmontar, la tubería JSON que ya estaba adjunta al logger raíz.
- **Desinstalar semlog mientras quedan llamadas marcadas lanza `TypeError`.** Si `semlog` se retira de un servicio que aún tiene puntos de llamada con `semlog=True`, una llamada habilitada en ese punto de llamada falla con `TypeError`, en lugar de fallar en silencio. Quite la palabra clave de los puntos de llamada antes de desinstalar.

## Salida

Cada registro es un objeto JSON por línea, en UTF-8. Este registro se emitió dentro de un `operation()` que recibió un encabezado `traceparent`, y aquí se muestra con sangría para facilitar la lectura; semlog nunca agrega sangría a su salida:

```json
{
  "timestamp": "2026-09-14T18:49:31.719135Z",
  "severity_text": "INFO",
  "severity_number": 9,
  "event_name": "payment.authorization.completed",
  "body": null,
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "6914640b649ae6de",
  "trace_flags": "01",
  "http.request.id": "01a0a140-c547-72e5-9455-a8b81f4a94d7",
  "otel.scope.name": "payments.service",
  "app.payment.amount": 15000,
  "app.identity": "api.payments",
  "service.name": "payments",
  "service.namespace": null,
  "service.version": "1.4.0",
  "service.instance.id": "936c21f2-7be1-4006-933a-e84d39621fe7",
  "deployment.environment.name": "production",
  "telemetry.sdk.name": "semlog",
  "telemetry.sdk.version": "0.2.0",
  "telemetry.sdk.language": "python"
}
```

Las claves son cadenas planas con puntos. Los campos de traza aparecen solo cuando hay un contexto de traza vigente, y un valor opcional desconocido es `null`, nunca una cadena vacía. Las claves sensibles, como `password` o `token`, siempre se reemplazan por `"REDACTED"`. El orden completo de los campos, la correspondencia de severidades y las reglas de presencia están en [STANDARDS.md](STANDARDS.md), y el esquema formal es [`schemas/log-record.schema.json`](schemas/log-record.schema.json).

## Compatibilidad

La suite de pruebas se ejecuta en integración continua sobre CPython 3.10, 3.11, 3.12, 3.13 y 3.14; la versión 3.15 también se ejecuta y se permite que falle. El paquete es Python puro (rueda `py3-none-any`). La integración con frameworks se prueba en integración continua contra estas versiones:

| Framework | Versión | Python | Rol |
|---|---|---|---|
| FastAPI (Starlette 0.17.1) | 0.71.0 | 3.10 | mínima |
| FastAPI (Starlette 1.6.0) | 0.141.1 | 3.10, 3.14 | más reciente |
| Django | 3.2.9 | 3.10 | mínima |
| Django | 5.2 LTS | 3.10, 3.14 | más reciente |
| Django | 6.1 | 3.12, 3.14 | más reciente |

FastAPI se prueba con puntos finales `async def` y `def` síncronos. Django se prueba con vistas síncronas sobre WSGI y con vistas asíncronas y síncronas sobre ASGI.

## Documentación

- [STANDARDS.md](STANDARDS.md): el contrato normativo del registro, la correspondencia de severidades, los límites de extensión, las garantías de la tubería y la política de compatibilidad, con identificadores de requisito y un anexo de trazabilidad (en inglés).
- [`schemas/log-record.schema.json`](schemas/log-record.schema.json) y [`schemas/event-catalog.schema.json`](schemas/event-catalog.schema.json): JSON Schema 2020-12 para cada registro y para el catálogo de eventos.
- [Guía para agentes](src/semlog/agent_guide.md): la referencia del sitio de la llamada para agentes de código, con reglas y listas de verificación de migración y de revisión, incluida dentro del paquete (`python -m semlog llm`). [llms.txt](llms.txt) es el punto de entrada del repositorio para agentes. Ambos documentos están en inglés.
- [SECURITY.md](SECURITY.md): cómo reportar una vulnerabilidad de forma privada (en inglés).
- [CHANGELOG.md](CHANGELOG.md): los cambios notables, en el formato [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). semlog sigue [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Contribuir

Las contribuciones son bienvenidas como pull requests hacia `main`. Los mensajes de commit siguen Conventional Commits, y todo cambio de código sigue un desarrollo guiado por pruebas estricto. [AGENTS.md](AGENTS.md) contiene los comandos de preparación, pruebas, lint y construcción, la política de herramientas y las reglas de protección de ramas. Las vulnerabilidades de seguridad se reportan de forma privada, como describe [SECURITY.md](SECURITY.md), nunca en un issue público.

## Licencia

semlog se distribuye bajo la [licencia Apache 2.0](LICENSE) (SPDX: `Apache-2.0`).
