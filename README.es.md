[English](README.md) | **Español**

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/semlog-logo-dark.svg">
    <img alt="semlog" src="docs/assets/semlog-logo-light.svg" width="360">
  </picture>
</p>

# semlog

**Registro estructurado en JSON sobre `logging` de la biblioteca estándar de Python, con nombres de campo de OpenTelemetry y W3C Trace Context.**

[![CI](https://github.com/flaviopifiator/semlog/actions/workflows/ci.yml/badge.svg)](https://github.com/flaviopifiator/semlog/actions/workflows/ci.yml)
[![Python 3.10-3.14](https://img.shields.io/badge/python-3.10--3.14-3776AB?logo=python&logoColor=white)](.github/workflows/ci.yml)
[![FastAPI 0.71+](https://img.shields.io/badge/FastAPI-%E2%89%A5%200.71-009688?logo=fastapi&logoColor=white)](.github/workflows/ci.yml)
[![Django 3.2.9+](https://img.shields.io/badge/Django-%E2%89%A5%203.2.9-092E20?logo=django&logoColor=white)](.github/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Runtime dependencies: 0](https://img.shields.io/badge/runtime%20dependencies-0-brightgreen)](pyproject.toml)
[![Requirements proven: 110/110](https://img.shields.io/badge/requirements%20proven-110%2F110-brightgreen)](STANDARDS.md)
[![PyPI](https://img.shields.io/pypi/v/semlog)](https://pypi.org/project/semlog/)

## Qué es semlog

semlog es una biblioteca de registro para servicios en Python. El código de la aplicación sigue llamando a `logging.getLogger(__name__)` y a los métodos estándar de `Logger`; semlog define la forma de cada registro resultante y lo escribe como un objeto JSON por línea en `stdout`.

El nombre es `semantic` más `log`, por las Semantic Conventions de OpenTelemetry: la especificación que fija cómo se llama cada campo de telemetría y qué significa. semlog aplica esos nombres a los registros, de modo que `service.name`, `trace_id` y `url.path` significan lo mismo en todos los servicios que los emiten.

Esa es la diferencia con el texto libre del mensaje. `f"User {user_id} purchased {quantity} units"` obliga a que lo lea una persona, o a que lo interprete una regla de parseo escrita una vez por servicio. Un campo con un nombre y un significado estables, en cambio, se puede consultar, agregar y usar en alertas entre servicios, sin ninguna regla de parseo.

Los middlewares WSGI, ASGI y de Django leen `traceparent`, `tracestate` y el `baggage` permitido de la solicitud entrante, así que todos los registros de una solicitud llevan el mismo `trace_id`, e `inject()` propaga ese contexto a las llamadas salientes; no se necesita ningún SDK de trazas. Los loggers de terceros que propagan hacia el logger raíz pasan por la misma tubería, y `capture_loggers` cubre los que instalan sus propios manejadores.

## Por qué semlog

Dos propiedades deciden si semlog encaja en un servicio que usted ya tiene:

- **Entra en un servicio que ya está en ejecución.** En [modo `hybrid`](#modos), cada línea que el servicio imprime hoy sigue imprimiéndose de forma idéntica byte a byte. Una llamada marcada con `semlog=True` queda oculta de esa salida impresa y se convierte en un registro JSON. No hace falta reescribir antes ninguna línea de registro existente. `SEMLOG_MODE=off` devuelve el proceso a su comportamiento anterior en el siguiente arranque, sin cambios de código.
- **Cero dependencias de tiempo de ejecución.** semlog está construido solo sobre la biblioteca estándar, y su rueda no declara ninguna entrada `Requires-Dist`. Eso importa donde cada dependencia nueva debe revisarse antes de llegar a producción.

## Filosofía

semlog toma unas pocas posiciones sobre qué es un registro y sobre qué puede exigirle una biblioteca de registro al código que la rodea.

- **Un registro es un contrato, no texto libre.** Los nombres de los campos, su orden y las reglas de `null` están especificados en [STANDARDS.md](STANDARDS.md) y publicados como JSON Schema ([`schemas/log-record.schema.json`](schemas/log-record.schema.json)). Eliminar o renombrar un campo es un cambio de versión mayor, y adoptar un renombrado posterior de las Semantic Conventions también lo es.
- **La biblioteca estándar alcanza.** semlog está construido con `logging`, `json`, `contextvars` y `queue`, y la rueda construida no declara ninguna entrada `Requires-Dist`, así que adoptarlo no agrega nada a una revisión de dependencias.
- **El código de la aplicación no debería tener que aprender una segunda API de registro.** Los puntos de llamada siguen usando `logging.getLogger(__name__)` y los métodos estándar de `Logger`, y la superficie pública son nueve nombres. semlog define la forma del registro en lugar de exigir que cada punto de llamada se reescriba contra un objeto logger propio de la biblioteca.
- **La adopción debe ser reversible.** Una biblioteca que solo se puede adoptar reescribiendo cada línea de registro existente no llega a adoptarse en un servicio que ya está en ejecución, así que el modo `hybrid` no reescribe ninguna, y `SEMLOG_MODE=off` retira semlog desde el entorno, sin cambios de código. Ver [Modos](#modos).
- **Una regla que ninguna prueba cita no es una regla.** Cada requisito normativo de [STANDARDS.md](STANDARDS.md) lleva un identificador y al menos una prueba que lo cita por ese identificador. La suite falla cuando un requisito no tiene ninguna prueba que lo pruebe, y cuando una prueba cita un identificador que no existe. Hoy STANDARDS.md declara 110 requisitos.
- **Un nombre de campo debería significar lo mismo para una persona, para una herramienta y para un agente.** Los nombres no se inventan aquí: `severity_text`, `severity_number`, `trace_id`, `span_id`, `service.*` y `telemetry.sdk.*` siguen el modelo de datos de logs de OpenTelemetry y sus Semantic Conventions, fijadas en la versión v1.44.0, y la propagación de trazas sigue W3C Trace Context. La guía para agentes viaja dentro del paquete, de modo que un agente de código aplica las mismas reglas sin conexión (`python -m semlog llm`).

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
{"timestamp":"2026-09-14T18:49:31.659966Z","severity_text":"INFO","severity_number":9,"event_name":"order.created","body":null,"otel.scope.name":"__main__","app.order.id":"ord_42","app.order.total":1500,"service.name":"checkout","service.namespace":null,"service.version":null,"service.instance.id":"b821b60b-7f5e-44a9-88f8-7cf734286abe","deployment.environment.name":null,"telemetry.sdk.name":"semlog","telemetry.sdk.version":"0.3.0","telemetry.sdk.language":"python"}
```

Tres reglas mantienen útiles los registros:

- El mensaje es un nombre de evento estático, como `order.created`, nunca un f-string.
- Las variables van en `extra`, bajo `app.` o bajo una clave de OpenTelemetry como `url.path`.
- `logger.exception(...)` se llama una sola vez, donde se maneja la excepción.

### API pública

semlog expone exactamente nueve nombres. Todo lo demás sigue siendo `logging` estándar.

| Nombre | Sirve para |
|---|---|
| `configure(...)` | Configurar el proceso una sola vez, al arrancar |
| `WSGIMiddleware(app)` | Envolver una aplicación WSGI: contexto de traza y `http.request.id` en cada registro de una solicitud |
| `ASGIMiddleware(app)` | Lo mismo, para una aplicación ASGI |
| `DjangoMiddleware` | Una entrada de `settings.MIDDLEWARE` que sirve vistas Django tanto síncronas como asíncronas |
| `operation(headers=None)` | Correlacionar trabajo fuera de HTTP, como jobs, consumidores de colas y comandos de CLI |
| `bind(attributes)` | Agregar campos a todos los registros posteriores de la solicitud u operación vigente |
| `inject(headers, *, trusted=True)` | Propagar el contexto de traza a una llamada saliente |
| `flush(timeout=None)` | Esperar a que se escriban todos los registros encolados, antes de `os._exit()` o en pruebas |
| `llm()` | Devolver la guía para agentes incluida en la versión instalada (también `python -m semlog llm`) |

Las firmas completas y su semántica están en la [guía para agentes](src/semlog/agent_guide.md).

## Recetas para frameworks

Cada framework tiene más de una forma soportada de integrarse, y las rutas no son equivalentes. Todos los ejemplos que siguen son completos y funcionan tal como están.

En todos ellos, `log_requests=True` agrega un evento de finalización `http.server.request` por solicitud: INFO cuando termina bien, ERROR cuando la aplicación lanza una excepción no manejada, con `http.request.method`, `url.path`, `http.response.status_code` y `event.duration` en nanosegundos. Nunca lleva `url.query`. El valor por defecto es `False`, que no agrega ningún evento. En modo `hybrid` ese evento necesita una línea más en la aplicación; ver [Modos](#modos).

### FastAPI

`ASGIMiddleware` es un middleware ASGI 3.0 puro y no importa FastAPI por su cuenta, así que las dos rutas que siguen se aplican sin cambios a una aplicación Starlette y a cualquier otra aplicación ASGI 3.0.

**Ruta 1, la propia pila de middlewares de la aplicación.**

```python
import logging

import semlog
from fastapi import FastAPI

semlog.configure(service_name="my-fastapi-service")

app = FastAPI()
app.add_middleware(semlog.ASGIMiddleware, log_requests=True)

logger = logging.getLogger(__name__)


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    logger.info("order.lookup.started", extra={"app.order.id": order_id})
    return {"id": order_id}
```

Un `GET /orders/ord_42` con un `traceparent` entrante imprime el registro propio del punto final y luego el evento de finalización, ambos con el `trace_id`, el `span_id` y el `http.request.id` de esa solicitud.

Hay una advertencia que pertenece solo a esta ruta. Un `@app.exception_handler(Exception)` global se ejecuta dentro del `ServerErrorMiddleware` más externo de Starlette, por encima de la capa que instala `add_middleware` y, por lo tanto, fuera del contexto vigente de semlog: los registros emitidos dentro de ese manejador no llevan `trace_id`, y el evento de finalización ERROR se emite antes de que se envíe la respuesta del manejador, así que su `http.response.status_code` es `null`.

**Ruta 2, envolver la aplicación.**

```python
import logging

import semlog
from fastapi import FastAPI
from semlog import ASGIMiddleware

semlog.configure(service_name="my-fastapi-service")

app = FastAPI()
logger = logging.getLogger(__name__)


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    logger.info("order.lookup.started", extra={"app.order.id": order_id})
    return {"id": order_id}


app = ASGIMiddleware(app, log_requests=True)
```

La línea que envuelve va después de registrar las rutas: `ASGIMiddleware` es un invocable ASGI, no una instancia de `FastAPI`, así que un `@app.get(...)` por debajo de ella lanza `AttributeError`. Ahora la capa de semlog es la más externa, de modo que un manejador de excepciones global se ejecuta dentro de su contexto: los registros que emite llevan el `trace_id` de la solicitud, y el evento de finalización lleva el `http.response.status_code` final real.

**Cuál elegir.** La ruta 1 si el servicio no tiene un manejador global de `Exception`, o si la pila de middlewares debe seguir bajo el control de FastAPI. La ruta 2 si sí lo tiene y esos registros necesitan el identificador de traza.

Un punto final `def` síncrono conserva el mismo contexto en cualquiera de las dos rutas: Starlette lo ejecuta en un hilo trabajador que hereda el contexto de quien lo llama.

### Django

`configure()` va en un `AppConfig.ready()`, no en `settings.py`. Django lee `settings.py`, luego aplica la configuración de logging del proyecto, y solo después llama a `ready()`. Un proyecto cuyo ajuste `LOGGING` declara una entrada `root`, que es la forma habitual, reemplaza en ese momento los manejadores del logger raíz: una llamada a `configure()` hecha desde `settings.py` pierde su tubería instantes después, el servicio vuelve a imprimir texto plano y el evento de finalización desaparece sin error alguno. Llamada desde `ready()`, se ejecuta después de esa configuración y la sobrevive.

**Ruta 1, la clase de middleware en `settings.MIDDLEWARE`.** Esta es la ruta recomendada.

```python
# settings.py
MIDDLEWARE = [
    "semlog.DjangoMiddleware",
    # ... other middleware ...
]
```

```python
# apps.py
import semlog
from django.apps import AppConfig


class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self):
        semlog.configure(service_name="my-django-service")
```

Colocada más externa (primera en `MIDDLEWARE`, la recomendación por defecto), cubre el logging propio de cualquier otro middleware, pero un `process_exception` competidor más cercano a la vista puede anticiparse al propio hook de semlog (ver abajo); colocada más cerca de la vista, en cambio, maximiza la prioridad de captura de excepciones a costa de una cobertura de contexto más estrecha. Elija la relación de compromiso que mejor se ajuste al despliegue.

`process_exception` guarda una excepción de vista no manejada sin absorberla, de modo que el evento de finalización sigue llevando el estado final real y una traza renderizada; el manejo de excepciones propio de Django nunca se modifica. Dos limitaciones permanentes, ninguna es un defecto: no puede observar una excepción lanzada por el propio código de otro middleware, ya que solo una excepción de vista llega hasta él, y un `process_exception` competidor registrado más cerca de la vista puede devolver una respuesta primero, anticipándose por completo al propio hook de semlog para esa solicitud.

Esa traza se renderiza una sola vez por instancia de excepción. El logger `django.request` propio de Django propaga hacia el logger raíz, así que en un servicio donde queda habilitado registra primero la misma excepción y renderiza allí la traza; el evento de finalización lleva entonces `exception.type` y `exception.message` sin repetirla.

El evento de finalización de esta ruta se habilita mediante el ajuste de Django [`SEMLOG_LOG_REQUESTS`](#semlog_log_requests) en lugar de una palabra clave del constructor, ya que Django instancia una entrada de `MIDDLEWARE` con un único argumento posicional.

**Ruta 2, envolver la aplicación WSGI o la ASGI.** Para un despliegue que prefiere mantener semlog completamente fuera de `MIDDLEWARE`, envuelva el invocable que importe el servidor.

```python
# wsgi.py
import os

from django.core.wsgi import get_wsgi_application

import semlog

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")

application = semlog.WSGIMiddleware(get_wsgi_application(), log_requests=True)
```

```python
# asgi.py
import os

from django.core.asgi import get_asgi_application

import semlog

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")

application = semlog.ASGIMiddleware(get_asgi_application(), log_requests=True)
```

Aquí `configure()` sigue perteneciendo a `AppConfig.ready()`, y `semlog.DjangoMiddleware` se queda fuera de `MIDDLEWARE`. Esta ruta renuncia al detalle de las excepciones, y eso tampoco es un defecto: Django convierte una excepción de vista en una respuesta 500 antes de que el middleware que la envuelve pueda verla, así que al envoltorio no le queda nada que renderizar y no puede adjuntar la traza. Su evento de finalización es un registro INFO con `http.response.status_code` 500 y sin ningún campo `exception.*`, donde la ruta 1 emite un registro ERROR que nombra la excepción. La vinculación del contexto de traza, `http.request.id` y los cuerpos en streaming se comportan igual en las dos.

Hay un detalle del proyecto que pertenece a esta ruta: importa `semlog` antes de que Django lea sus ajustes, así que un proyecto cuyo diccionario `LOGGING` omite `"disable_existing_loggers": False` deshabilita todos los loggers que ya existían, entre ellos el del evento de finalización de semlog, y el evento deja de aparecer sin error alguno. Mantenga esa clave en el diccionario.

**No combine las dos rutas.** Usar la clase junto con `WSGIMiddleware` o `ASGIMiddleware` envolviendo la misma aplicación no está soportado: cada capa analiza el `traceparent` entrante de forma independiente y genera su propio `span_id`, de modo que con el evento de finalización habilitado en ambas, la telemetría se duplica. Use solo una.

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

### SEMLOG_LOG_REQUESTS

No es un parámetro de `configure()`, y se mantiene deliberadamente fuera de la tabla anterior: Django instancia una entrada de `settings.MIDDLEWARE` con un único argumento posicional, así que ninguna palabra clave de `configure()` puede llegar a `DjangoMiddleware.__init__` a través de `settings.MIDDLEWARE` directamente. Configure el ajuste de Django en su lugar, para habilitar el propio evento de finalización de [`DjangoMiddleware`](#django), el mismo que `WSGIMiddleware`/`ASGIMiddleware` habilitan mediante su propia palabra clave del constructor `log_requests=True`:

```python
# settings.py
SEMLOG_LOG_REQUESTS = True
```

Por defecto `False`, se lee una sola vez, cuando Django construye el middleware. Un valor no booleano lanza `ValueError` nombrando el valor y su origen. Es un único booleano, no un objeto ni un diccionario de configuración, y no agrega ningún nombre público.

## Modos

`mode` selecciona cuánto de semlog está activo en un proceso: `"full"` (el valor por defecto, y la opción correcta para un servicio nuevo), `"hybrid"` y `"off"`.

Para un servicio que ya está en producción, con sus propias líneas de registro existentes, `hybrid` es la vía de adopción. Cada línea que el servicio ya imprime sigue imprimiéndose de forma idéntica byte a byte. Una llamada escrita con la palabra clave `semlog=True` queda oculta de esa misma salida impresa y se convierte en un registro JSON; `logger.info("event.name", extra={...}, semlog=True)` es la forma de una llamada así. `off` se comporta como si semlog nunca se hubiera instalado, salvo que la propia palabra clave nunca lanza una excepción, de modo que resulta seguro dejarla en los puntos de llamada durante una reversión. `full` es el estado final, y el valor por defecto para un servicio nuevo sin líneas de registro existentes que preservar: la vía de adopción es `hybrid`, y luego `full` una vez revisada su salida. `mode` se cambia por entorno, sin modificar código.

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

Leer `pyproject.toml` necesita `tomllib` de la biblioteca estándar, disponible desde Python 3.11 en adelante. En Python 3.10 esta fuente se omite por completo, y la resolución continúa con la siguiente. El archivo se busca a partir del directorio de trabajo actual (o de `configure(search_dir=...)`, si se indica) y hacia arriba por sus directorios padres. Una imagen de contenedor construida sin el árbol de fuentes del proyecto, o con su directorio de trabajo apuntando a otro lugar, con frecuencia no tiene ningún `pyproject.toml` que encontrar; esa fuente se omite entonces en silencio, igual que en Python 3.10. Una `SEMLOG_MODE` vacía cuenta como ausente y la resolución continúa con la siguiente fuente. `[tool.semlog].mode` es distinto: ahí una cadena vacía es un valor declarado real. Un valor fuera de `"full"`, `"hybrid"` y `"off"`, proveniente de cualquiera de las tres fuentes, lanza `ValueError` nombrando tanto el valor inválido como la fuente de la que proviene.

### El nivel del logger raíz en modo hybrid

`hybrid` nunca toca el logger raíz, deliberadamente, y eso incluye su nivel: la biblioteca estándar lo deja en `WARNING`. El filtrado por severidad de una llamada marcada sigue las reglas de nivel efectivo habituales de la biblioteca estándar, así que en un proceso que nunca definió un nivel para el logger raíz, un registro `INFO` marcado con `semlog=True` se filtra antes de que el enrutamiento de hybrid llegue a verlo, y no se escribe ningún JSON. El evento de finalización `http.server.request` es `INFO` cuando la solicitud termina bien, así que también desaparece, en silencio; el `ERROR`, emitido cuando la aplicación lanza una excepción, sí llega.

`configure(level=...)` no sirve aquí: ese parámetro define el nivel del logger raíz solo en modo `full`. En su lugar, defina el nivel del logger raíz en la aplicación, antes o después de `configure()`:

```python
import logging

import semlog

logging.getLogger().setLevel(logging.INFO)
semlog.configure(service_name="checkout", mode="hybrid")
```

Un servicio que ya llama a `logging.basicConfig(level=logging.INFO)` o aplica su propio `dictConfig` con un nivel para el logger raíz no necesita nada más. El modo `full` no se ve afectado: siempre define un nivel explícito para el logger raíz, `INFO` por defecto.

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
  "telemetry.sdk.version": "0.3.0",
  "telemetry.sdk.language": "python"
}
```

Las claves son cadenas planas con puntos. Los campos de traza aparecen solo cuando hay un contexto de traza vigente, y un valor opcional desconocido es `null`, nunca una cadena vacía. Las claves sensibles, como `password` o `token`, siempre se reemplazan por `"REDACTED"`. El orden completo de los campos, la correspondencia de severidades y las reglas de presencia están en [STANDARDS.md](STANDARDS.md), y el esquema formal es [`schemas/log-record.schema.json`](schemas/log-record.schema.json).

## Compatibilidad

La suite de pruebas se ejecuta en integración continua sobre CPython 3.10, 3.11, 3.12, 3.13 y 3.14; la versión 3.15 también se ejecuta y se permite que falle. El paquete es Python puro (rueda `py3-none-any`). Incluye un marcador `py.typed` (PEP 561), de modo que un verificador de tipos lee las anotaciones que el paquete trae, en lugar de tratarlo como no tipado. La integración con frameworks se prueba en integración continua contra estas versiones:

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
