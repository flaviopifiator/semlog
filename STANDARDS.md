# SEMLOG log record contract standard

This document is normative. It defines the exact contract of every record SEMLOG emits, the guarantees of the transport pipeline, the compatibility policy, and the references that back each rule. The README describes how to use the library; this document describes what every record MUST satisfy and why.

**DOC-001**: README.md and STANDARDS.md, together with the two JSON Schema documents, MUST be explicitly approved by the user before any implementation code is written. No work from Phase 2 onward begins without that recorded approval. Planned test: task 1.9 (user review gate).

> **Design decision (DOC-001).** Rationale: the code follows a contract that was already explicitly approved, instead of evolving alongside it. Rejected alternative: documenting after writing the code, which would describe accidental behavior instead of a deliberate decision.

## 1. Conventions

**DOC-002**: All normative text of this document, and every default document of the repository, MUST be written in English, and every normative keyword MUST be written in capitals as defined by the table in this section. STANDARDS.md MUST NOT be maintained in any other language. README.md MUST be written in English, and README.es.md MUST be its complete Spanish translation, in a neutral, professional register: both MUST link to each other at the top of the document and MUST keep the same heading structure, the same byte-identical code blocks, the same configuration parameters and the same badges. Test: self-review, task 1.8; automated parity check between README.md and README.es.md.

> **Design decision (DOC-002).** Rationale: the general audience of an open-source project reads English, and a single normative language keeps one authoritative copy of the contract; the README also keeps a Spanish translation for Spanish-speaking readers, checked for parity. Rejected alternative: keeping this document in Spanish with bilingual keywords, or in two languages, which would reduce accessibility for that audience or let the copies diverge; or keeping bilingual README copies without an automated parity check, which would diverge over time.

This document follows the conventions of RFC 2119 and its clarification in RFC 8174: normative keywords are written in capitals. They carry normative force only when they appear like this, in capitals; the same words in lowercase are descriptive, not normative (DOC-003; [RFC2119], [RFC8174]).

| Keyword (RFC 2119/8174) | Meaning |
|---|---|
| MUST / SHALL | Absolute requirement |
| MUST NOT / SHALL NOT | Absolute prohibition |
| SHOULD | Recommendation; an exception requires understanding and weighing the consequences |
| SHOULD NOT | Recommendation against |
| MAY | Truly optional |
| REQUIRED | Equivalent to MUST |
| RECOMMENDED | Equivalent to SHOULD |
| OPTIONAL | Equivalent to MAY |

Every normative rule of this document declares its backing according to DOC-003 (section 1.1): an external reference from section 12, or a documented design decision with its own rationale. Annex A summarizes the rules with their backing and their planned test.

### 1.1 Requirement identifiers and backing

Every normative requirement identifier in this document follows the grammar `[A-Z]{2,4}-[0-9]{3}` (two to four capital letters, a hyphen and three digits), is unique across the whole document, and uses exactly one fixed prefix per capability.

| Prefix | Capability |
|---|---|
| LRC | Log record contract |
| LP | Pipeline |
| LM | Logging modes |
| SI | Service identity |
| TCP | Trace context |
| HTM | HTTP middleware |
| DOC | Documentation |
| CP | Compatibility and performance |
| TRC | Backing and traceability |
| CMA | Compliance mapping |
| PRV | Package provenance |
| OSF | OpenSSF signals |

Only a line that starts with the bold identifier followed by a colon and a space (`**ID**: `) counts as a requirement declaration. A mention in parentheses or inside a sentence does not declare a new requirement. A similar-looking external identifier, such as `CWE-117`, is NOT a SEMLOG requirement even when this document cites it in square brackets.

**TRC-001**: Every normative requirement identifier in STANDARDS.md MUST follow the grammar `[A-Z]{2,4}-[0-9]{3}` (two to four capital letters, a hyphen and three digits), MUST be unique across the whole document, and MUST use exactly one fixed prefix per capability. This document MUST document this grammar explicitly. Test: task 2.1/2.2.

> **Design decision (TRC-001).** Rationale: a strict grammar allows deterministic extraction with a single standard-library pattern. Rejected alternative: free-form identifiers, which would prevent reliable extraction.

**TRC-002**: Every automated conformance test that proves a normative requirement MUST cite at least one requirement identifier it proves, in a form that a standard-library-only script can extract deterministically. A test MAY cite more than one identifier when it proves several requirements together. Test: task 2.1/2.2.

> **Design decision (TRC-002).** Rationale: traceability is verified in both directions, from requirement to test and from test to requirement. Rejected alternative: a hand-maintained traceability matrix, which goes stale over time.

**TRC-003**: A continuous integration check, implemented using only the Python standard library, MUST run on every change and MUST fail when: (a) a requirement identifier declared in STANDARDS.md has no conformance test citing it; (b) a conformance test cites a requirement identifier not declared in STANDARDS.md; (c) a declared requirement has no backing of either of the two types defined by DOC-003. The mechanism for extracting identifiers from STANDARDS.md and from the tests is a design-phase decision; this requirement does not prescribe file names, module names or a specific parsing technique. Test: task 2.1/2.2.

> **Design decision (TRC-003).** Rationale: the check is enforced in the normal continuous integration suite, without depending on a manual step. Rejected alternative: manual review of traceability compliance.

**TRC-004**: This document's requirement-to-clause traceability annex MUST carry an explicit "Backing" column stating, for every requirement row, whether its backing is (a) external, naming the specific reference identifier or identifiers, or (b) a design decision. Test: task 1.20.

> **Design decision (TRC-004).** Rationale: the backing type of each requirement is visible at a glance and can be checked mechanically. Rejected alternative: leaving the backing only implicit in the text of the rule.

**DOC-003**: Every normative requirement of this document MUST declare its backing, which MUST be exactly one of two types: (a) an external normative source: a reference from section 12, such as an RFC, a W3C specification, an OTel specification, an ISO/IEC standard, an OWASP publication, the Python documentation, or a PEP; or (b) a documented design decision: an explicit rationale paragraph in this document, marked as a project decision, stating why the rule exists and which alternative was considered. Every normative requirement MUST also be proven by at least one conformance test citing its requirement identifier (TRC-001/TRC-002), enforced by the continuous integration check defined in TRC-003. Test: task 1.20, 2.1/2.2.

> **Design decision (DOC-003).** Rationale: every rule must be auditable through an external source or an explicit decision, in addition to a test that proves it. Rejected alternative: requiring external citations only, which would force weak or strained citations for the project's own policy rules.

## 2. Log record contract

**LRC-001**: The system MUST emit exactly one RFC 8259-conformant JSON object per record, encoded in UTF-8, terminated by a newline, with no indentation or pretty-printing ([RFC8259], [JSONLINES]). Test: task 2.13/2.27.

**LRC-002**: Every record MUST carry a `timestamp` field in RFC 3339, in UTC, with microsecond precision and the `Z` designator ([RFC3339], [ISO8601-1]). Format: `YYYY-MM-DDThh:mm:ss.ffffffZ`. Test: task 2.13.

**LRC-004**: `event_name` and `body` MUST be static strings, with no variables interpolated at the call site; variables MUST be passed only as `extra` fields, never embedded in the message text through string formatting ([MSGTEMPLATES], [OTEL-LOGS-DATAMODEL]). Test: task 2.13.

**LRC-005**: When a trace context is bound, every record MUST carry `trace_id`, `span_id` (lowercase hexadecimal) and `trace_flags` at the top level; when no context is bound, these fields MUST be omitted, never emitted as `null` or as an empty string ([OTEL-NONOTLP-TRACE], [RFC4648]). Test: task 2.13.

**LRC-012**: Optional envelope fields whose value is legitimately unknown MUST be represented as JSON `null` when included, and MUST NOT use an empty string as an "unknown" marker ([OTEL-LOGS-DATAMODEL]). Test: task 2.13.

> **Design decision (LRC-012).** Rationale: distinguishing "unknown" from "empty" keeps a stable column set in the published schema. Rejected alternative: using an empty string as an "unknown" marker, which is ambiguous; or omitting the field, which produces an unstable column set.

### 2.1 Key order and presence

| # | Key | Presence | Type |
|---|---|---|---|
| 1 | `timestamp` | always | string |
| 2 | `severity_text` | always | string |
| 3 | `severity_number` | always | integer (1-24) |
| 4 | `event_name` | always; `null` for third-party text | string or null |
| 5 | `body` | always; `null` for catalog events | string or null |
| 6-8 | `trace_id`, `span_id`, `trace_flags` | only inside an operation | string |
| 9 | `http.request.id` | only inside an HTTP operation | string (UUIDv7) |
| 10 | `otel.scope.name` | always | string |
| 11-14 | `error.type`, `exception.type`, `exception.message`, `exception.stacktrace`; `code.stacktrace` | only with `exc_info` / `stack_info` | string |
| 15 | call-site, bound and *baggage* attributes | as provided, after limits are applied | per value |
| 16 | `{ns}.dropped_attributes_count`, `{ns}.truncated_attributes_count` | only when greater than 0 (library-specific) | integer |
| 17 | `{ns}.identity` | when configured | string |
| 18-22 | `service.name`, `service.namespace`, `service.version`, `service.instance.id`, `deployment.environment.name` | always; `null` when unknown | string or null |
| 23-25 | `telemetry.sdk.name`, `telemetry.sdk.version`, `telemetry.sdk.language` | always | string |

Keys are flat, lowercase, dot-separated, in `snake_case`; they are never expanded into nested objects. The schema published in `schemas/log-record.schema.json` (DOC-004) formalizes this contract with `propertyNames`, `dependentRequired` (the three trace fields are required together) and the types of this table. That schema's `$id` is still a placeholder (`https://semlog.invalid/...`); it will be replaced by the public repository URL at the first public release.

**DOC-004**: The log record contract and the event catalog MUST be published as JSON Schema 2020-12 documents ([JSONSCHEMA]), `schemas/log-record.schema.json` and `schemas/event-catalog.schema.json` respectively, formalizing `propertyNames`, `dependentRequired` and the type of each field. Test: task 1.6/1.7.

### 2.2 Conformant example

```json
{"timestamp":"2026-09-10T17:52:14.460123Z","severity_text":"INFO","severity_number":9,"event_name":"payment.authorization.completed","body":null,"trace_id":"4bf92f3577b34da6a3ce929d0e0e4736","span_id":"53995c3f42cd8ad8","trace_flags":"01","http.request.id":"0199358a-7c2e-7b1d-8f3a-2c9e4b6d1a0f","otel.scope.name":"payments.service","app.payment.amount":15000,"app.identity":"api.payments","service.name":"payments","service.namespace":null,"service.version":"1.4.0","service.instance.id":"5f1c2d9e-3b4a-4c8e-9f10-2a6b7c8d9e0f","deployment.environment.name":"production","telemetry.sdk.name":"semlog","telemetry.sdk.version":"1.0.0","telemetry.sdk.language":"python"}
```

### 2.3 Service identity resolution

**SI-001**: `service.name` MUST be resolved, from highest to lowest precedence: explicit constructor parameter > `OTEL_SERVICE_NAME` > the `service.name` key inside `OTEL_RESOURCE_ATTRIBUTES` > PEP 621 `[project].name` in `pyproject.toml` (read with `tomllib`) > `[tool.poetry].name` as a fallback. On Python 3.10, where the standard library does not provide `tomllib`, the system MUST NOT read `pyproject.toml` and MUST NOT bundle a third-party TOML reader; on that version resolution uses only the explicit parameter, the environment variables or the installed package metadata. A forced value (parameter or environment variable) MUST ALWAYS win over any detected value, on every supported Python version ([PEP621], [OTEL-SDK-ENV]). Test: task 2.1/2.2.

**SI-002**: `service.version` MUST be resolved first from `OTEL_RESOURCE_ATTRIBUTES` or the explicit parameter, and otherwise from the installed package version (metadata) or the `version` field of `pyproject.toml` ([OTEL-SDK-ENV], [PEP621], [PEP440]). Test: task 2.1/2.2.

**SI-003**: The system MUST emit `service.namespace`, `service.instance.id`, `deployment.environment.name` and `telemetry.sdk.name`/`version`/`language`, each independently overridable through `OTEL_RESOURCE_ATTRIBUTES` or an explicit parameter. `telemetry.sdk.name` MUST default to `"semlog"` and `telemetry.sdk.language` to `"python"`; `telemetry.sdk.version` MUST default to the installed version of the library. The library's own internal diagnostic loggers MUST be created under the `"semlog"` logger namespace, distinct from the user-configurable custom attribute namespace (LRC-007, `"app"` by default) ([OTEL-SEMCONV]). Test: task 2.1/2.2.

**SI-004**: The system MUST support an ordered, configurable list of identity levels (`identity_levels`, `("role", "component")` by default), composed into exactly one field emitted under the configured custom namespace, with no organization-specific default. Test: task 2.1/2.2.

> **Design decision (SI-004).** Rationale: composite identity is resolved without hard-coding the taxonomy of any particular organization. Rejected alternative: fixed organization fields, or completely free-form attributes with no structure.

**SI-005**: When `pyproject.toml` is absent, cannot be read, or (on Python 3.10) is deliberately not read because `tomllib` does not exist, the system MUST continue along the documented precedence chain without raising an exception, and MUST signal, through a startup diagnostic, that no project metadata was resolved from `pyproject.toml`; the diagnostic for the Python 3.10 case MUST be distinguishable from the diagnostic for a missing or unreadable file ([PEP621]). Test: task 2.1/2.2.

## 3. Severity table

**LRC-003**: Every record MUST carry both `severity_text` and `severity_number` (OTel range 1-24), derived from an explicit, documented mapping between the Python level and OTel; RFC 5424 severities MUST be used only as an informative cross-reference, never presented as a one-to-one mapping ([OTEL-LOGS-DATAMODEL]; [RFC5424], informative reference). Test: task 2.13.

**DOC-006**: This table MUST publish the mapping between Python levels and OTel severity, according to the OTel logs data model, with RFC 5424 severities used only as an informative reference (External: [OTEL-LOGS-DATAMODEL], [RFC5424]). Test: self-review, task 1.8.

| Python level | `levelno` | `severity_text` | `severity_number` | OTel short name | RFC 5424 (informative only) |
|---|---|---|---|---|---|
| DEBUG | 10 | DEBUG | 5 | DEBUG | 7 Debug |
| INFO | 20 | INFO | 9 | INFO | 6 Informational |
| WARNING | 30 | WARNING | 13 | WARN | 4 Warning |
| ERROR | 40 | ERROR | 17 | ERROR | 3 Error |
| CRITICAL | 50 | CRITICAL | 21 | FATAL | 2 Critical |

For custom levels, the band is computed by tens (1-9 → TRACE 1-4, 10-19 → DEBUG 5-8, ..., 50 and above → FATAL 21-24); within the band, the offset is `min(levelno % 10, 3)`, capped at 24. RFC 5424 Notice (5), Alert (1) and Emergency (0) have no equivalent Python level and are left out of this table.

## 4. Naming authority and pinned semconv version

**LRC-006**: Attribute keys (OTel/ECS-named or custom) MUST be flat dot-separated strings (no nested JSON objects) and MUST NOT collide with the 23 reserved `LogRecord` names of the standard library (`args, asctime, created, exc_info, exc_text, filename, funcName, levelname, levelno, lineno, message, module, msecs, msg, name, pathname, process, processName, relativeCreated, stack_info, taskName, thread, threadName`) ([OTEL-SEMCONV], version pinned below). Test: task 2.15.

**LRC-011**: A collision with a reserved name (LRC-006) MUST fail at the call site with a clear, documented exception type, and MUST NOT be silently absorbed by the pipeline or discovered only in the writer ([LOGRECORD]). Test: task 2.15/2.16.

Naming authority: semantic attribute keys follow OpenTelemetry Semantic Conventions. **Pinned version: v1.44.0.** Adopting a later semantic convention rename is a major contract version change (see section 11).

## 5. Custom namespace and event catalog

**LRC-007**: Custom fields MUST be emitted flat under exactly one configurable namespace root (with no organization name hard-coded), and MUST be declared per event in an event catalog before use ([OTEL-ATTR-NAMING]). Test: task 2.17/2.18.

**LRC-008**: The system MUST support three catalog validation modes: `off`, `warn` and `strict`. In `off`, no check runs. In `warn`, an undeclared `event_name` or custom field MUST NOT block emission; instead, the system MUST produce exactly one diagnostic line per record that violates the catalog, listing every violation of that record. In `strict`, an undeclared event or field MUST raise an exception at the call site before emission. Test: task 2.19/2.20.

> **Design decision (LRC-008).** Rationale: `warn` serves gradual rollout, `strict` serves tests and continuous integration, and `off` serves when no catalog exists yet. Rejected alternative: always requiring `strict` mode (a new key would break production), or relying only on static analysis outside runtime.

**LRC-013**: Attribute keys MUST belong to one of a closed set of groups: OTel logs data model envelope fields, the two ECS fields (`event.duration`, `http.request.id`), W3C trace fields, service identity fields, the *baggage* prefix group (keys under the configured `baggage_prefix`), the declared custom namespace, and OTel semantic convention keys when explicitly declared in the event catalog. The system MUST NOT allow a call site to build a dynamically named key at runtime ([OTEL-SEMCONV]). Test: task 2.17/2.18.

Event name grammar: `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`. The catalog is published as a JSON Schema 2020-12 document at `schemas/event-catalog.schema.json` (DOC-004); the catalog's attribute keys follow the same dotted grammar as the log record contract. As in the log record schema (section 2.1), its `$id` is a placeholder that will be replaced by the public repository URL at the first public release. Publication test: task 1.7.

## 6. Extension limits

**LRC-009**: An attribute value that is not JSON-serializable MUST be converted to its string representation instead of raising an exception. An attribute value that exceeds the configured maximum length MUST be truncated instead of rejected, and the truncation MUST be reflected through a distinct indicator ([OTEL-SDK-ENV]). Test: task 2.21/2.22.

**LRC-010**: The system MUST enforce a configurable maximum attribute count through the `max_attributes` parameter (128 by default) and a configurable maximum value length through `max_attribute_length` (no limit by default). Both limits MUST also be configurable through environment variables, resolved with the following precedence: explicit parameter > log-record-specific environment variable > generic environment variable > default value. When the count overflows, the system MUST drop the excess attributes (never raise). When the length overflows, string and bytes values MUST be truncated to the configured limit, applied per element in an array and recursively per value in a map. The system MUST record two library-specific counters, `{ns}.dropped_attributes_count` and `{ns}.truncated_attributes_count`, since no standard OTel field exists for either; both MUST be clearly documented as library-specific fields, not as standard OTel fields ([OTEL-SDK-ENV], verified against the living OpenTelemetry specification). Test: task 2.21/2.22.

| Limit | Parameter | Log-record-specific variable | Generic variable | Default value |
|---|---|---|---|---|
| Attribute count | `max_attributes` | `OTEL_LOGRECORD_ATTRIBUTE_COUNT_LIMIT` (stable) | `OTEL_ATTRIBUTE_COUNT_LIMIT` (stable) | 128 |
| Value length | `max_attribute_length` | `OTEL_LOGRECORD_ATTRIBUTE_VALUE_LENGTH_LIMIT` (stable) | `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` (stable) | no limit |

The OpenTelemetry specification does not state the precedence order between the log-record-specific variable and the generic one; this library documents, as its own decision, that the log-record-specific variable wins when both are set.

**DOC-005**: This section documents the attribute limits, the overflow behavior and the `null` versus empty string rule (section 2), each with its backing reference. Test: self-review, task 1.8.

> **Design decision (DOC-005).** Rationale: the limits and their backing references live in the same section, so that each limit can be audited in one place. Rejected alternative: scattering the extension limits across several sections of the document.

## 7. Errors, exactly once

**LP-004**: Exceptions MUST be captured in `error.type`, `exception.type`, `exception.message` and `exception.stacktrace` (including the chained `__cause__`/`__context__`), rendered before the queue, and logged exactly once at the boundary that handles them, never re-logged by intermediate handlers ([OTEL-SEMCONV], [OWASP-ASVS]). Test: task 2.25/2.26.

## 8. Trace context, baggage and trust boundary

**TCP-001**: The system MUST parse and accept an inbound `traceparent` with version `00`, and `trace-id` and `parent-id` in lowercase hexadecimal, both different from all zeros ([TRACECONTEXT]). Test: task 2.7/4.8.

**TCP-002**: A malformed header (wrong length, uppercase hexadecimal, wrong number of fields) or an all-zeros `trace-id`/`parent-id` MUST be treated as absent, generating a new trace context ([TRACECONTEXT]). Test: task 2.7/4.8.

**TCP-003**: A `traceparent` with version `ff` MUST be treated as invalid/absent. A `traceparent` with an unknown intermediate version (`01`-`fe`) MUST be parsed using only the version 00 field format (trace-id, parent-id, trace-flags), ignoring any additional trailing field, according to the specification's forward-compatibility rule ([TRACECONTEXT]). Test: task 2.7/4.8.

**TCP-004**: The system MUST generate a new `span_id` for each server-side operation, reusing the inbound `trace_id`, and MUST pass `tracestate` through unmodified when present ([TRACECONTEXT]). Test: task 2.7.

**TCP-005**: The trace/baggage/identity context MUST be bound through `contextvars` (PEP 567) and MUST be reset to its previous value on exit, guaranteeing isolation between concurrent asynchronous tasks and synchronous work delegated to a thread pool ([PEP567]). Test: task 2.5/2.6/3.7.

**TCP-006**: The system MUST provide a function that injects `traceparent` and `baggage` into a header mapping supplied by the caller, without depending on any specific HTTP client library ([TRACECONTEXT], [BAGGAGE]). Test: task 3.11/3.12.

**TCP-007**: The system MUST parse and propagate inbound W3C *baggage*, MUST copy only the allowed keys into log record attributes under a configurable prefix (`baggage.` by default), and MUST enforce the specification's limits of 64 members and 8192 bytes, dropping the members that exceed the limit in header order instead of dropping the whole header ([BAGGAGE]). Test: task 2.9/2.10/4.9.

**TCP-008**: *Baggage* MUST be removed from outbound propagation at a configurable trust boundary, while remaining available for the internal log record attributes already captured ([BAGGAGE]). Test: task 3.11/3.12.

**TCP-009**: The system MUST NOT introduce any `X-`-prefixed or otherwise non-standard correlation header ([RFC6648]). Test: task 3.11/3.12.

**TCP-010**: `http.request.id` MUST be an RFC 9562-conformant UUIDv7: the standard library implementation on Python 3.14+, and an in-house implementation conforming to RFC 9562 §5.7 on Python 3.10-3.13, both producing correct version/variant bits and time-ordered values ([RFC9562]). Test: task 2.11/2.12.

**TCP-011**: The system MUST document coexistence with another W3C-conformant propagator in the same process, without assuming exclusive ownership of the `traceparent` header key ([TRACECONTEXT]). Test: task 3.11/3.12.

### 8.1 Inbound parsing behavior

| Inbound `traceparent` | Result |
|---|---|
| valid: version `00`, exactly 55 characters, lowercase hexadecimal, ids not all zeros | keeps `trace_id`, generates a new `span_id`, keeps `trace_flags` and `tracestate` |
| version `01`-`fe` | parses the version 00 fields, ignores additional trailing fields (TCP-003) |
| version `ff`, wrong length or alphabet, uppercase hexadecimal, all-zeros ids, repeated header | treated as absent (TCP-002) |
| absent | new `trace_id` (16 bytes) and `span_id` (8 bytes) generated with `secrets` (CSPRNG), `trace_flags="01"`, no `tracestate` |

Length is checked before any pattern is applied. `tracestate` is kept only when the `traceparent` was valid, and is dropped above 32 members.

### 8.2 HTTP middlewares (WSGI and ASGI)

**HTM-001**: The system MUST implement a PEP 3333-conformant WSGI middleware that parses the inbound trace/baggage headers, binds the context, generates a request identifier, and resets the context only after the response iterable has been fully consumed ([PEP3333], [TRACECONTEXT]). Test: task 3.1/3.2.

**HTM-002**: The system MUST implement a pure ASGI middleware, operating at the level of the callable for the `http` `scope`, and MUST NOT depend on Starlette's `BaseHTTPMiddleware` ([ASGI]). Test: task 3.3/3.4.

**HTM-003**: The context MUST stay correctly bound throughout a streaming response body (a WSGI generator or several ASGI `http.response.body` messages) and MUST be reset without leaking into a later request on the same worker thread/task ([PEP3333], [ASGI]). Test: task 3.1/3.3.

**HTM-004**: The bound context MUST be reset even when the wrapped application raises an unhandled exception; the middleware MUST NOT swallow or alter the exception ([PEP3333], [ASGI]). Test: task 3.1/3.3.

**HTM-005**: The ASGI middleware MUST pass `lifespan` `scope` events through without trying to bind an HTTP request context, and MUST NOT raise exceptions on the lifespan `startup`/`shutdown` events ([ASGI]). Test: task 3.3/3.4.

**HTM-006**: Concurrent requests handled by the same process/thread/task pool MUST NOT observe each other's `trace_id`/`span_id`/baggage ([PEP567]). Test: task 3.5/3.6.

**HTM-007**: The system MAY emit a single optional completion event, `http.server.request`, per request. This event MUST be disabled by default (`log_requests=False`) and MUST be emitted only when the corresponding middleware is explicitly configured with `log_requests=True`. When enabled, the event MUST carry `http.request.method`, `url.path`, `http.response.status_code` and `event.duration` (ECS long, nanoseconds). The event MUST NOT include `url.query` under any circumstances. The event MUST be logged at `INFO` severity when the wrapped application responds normally (any status code), and at `ERROR` when an unhandled exception escapes the wrapped application; in that case, the exception traceback MUST be rendered exactly once, within the scope of this event ([ECS-EVENT], verified). In `hybrid` mode, this event MUST route to JSON only (LM-003), even when the enabling middleware instance sits alongside legacy `StreamHandler`-based logging; it MUST NOT also render as legacy text. In `off` mode, the middleware MUST NOT emit this event under any configuration, and setting `log_requests=True` MUST have no observable effect (LM-005). Test: `tests/test_log_requests.py`, `tests/test_hybrid.py::HybridRequestEventTests`, `tests/test_off_mode.py::OffRequestEventTests`.

(Previously: applied only within the always-JSON pipeline of full mode; now scoped explicitly per mode.)

### 8.3 Outbound injection and `operation()`

`inject(headers, trusted=True)`: with no current operation, the mapping is left unchanged. A `traceparent` already present (in any capitalization) is left intact: another propagator already owns that call (TCP-011). Otherwise `traceparent = 00-{trace_id}-{span_id}-{flags}` is set, `tracestate` if it was inherited, and `baggage` (raw members) only when `trusted=True` (TCP-008). No other header is added (TCP-009).

`operation(headers=None)` is the same primitive for non-HTTP work: with headers, it parses them like an inbound request; without headers inside an operation, it starts a child (same `trace_id`, new `span_id`); without headers outside any operation, it starts a new trace. `bind(attributes)` adds scoped fields that merge, never replace, avoiding the known `LoggerAdapter` pitfall of replacing `extra`.

**TCP-012**: Calling `bind()` when no operation (`operation()`) or middleware scope is current MUST NOT raise an exception and MUST NOT modify any context (bound or not). On the first such out-of-scope call in a process, the system MUST emit exactly one WARNING-level record through the library's internal diagnostic channel (the `semlog` logger namespace), and that record MUST include the location of the call site. Every later out-of-scope call to `bind()` in the same process MUST be a silent no-op, emitting no additional diagnostic. In `off` mode (LM-005), this diagnostic MUST be suppressed entirely: no diagnostic is ever emitted for an out-of-scope `bind()` call in that mode, consistent with LM-005's silent-diagnostics guarantee. This requirement adds, removes or renames no public API name; the public surface stays at 8 names. Test: `tests/test_bind.py`, `tests/test_off_mode.py::OffDiagnosticsTests`.

(Previously: the WARNING-once behavior applied in every mode; now suppressed entirely in `off` mode.)

> **Design decision (TCP-012).** Rationale: an out-of-scope call to `bind()` has no well-defined target context. Rejected alternative: (1) binding it anyway, which would produce observably different behavior depending on the server type, since an ASGI task copies the context when it is created and a WSGI request does not, and process-wide fields already have their own mechanism (`configure(identity=...)`); (2) a silent no-op with no diagnostic at all, which would hide a caller's bug indefinitely; (3) raising an exception, which would violate the rule that logging never interrupts the application, since a helper shared between request code and a batch job would fail only in the job.

## 9. Redaction and privacy

**LP-005**: Redaction of the configured sensitive keys MUST always be active (it cannot be disabled at runtime) and MUST be applied recursively to nested values before serialization ([OWASP-LOGGING], [OWASP-ASVS]). Test: task 2.23/2.24.

**DOC-007**: The following compliance references are cited as support, without transcribing the text of any clause, and are explicitly marked as unverified against their primary source in this revision:

- ISO/IEC 27001:2022, Annex A, controls 8.11 (data masking), 8.15 (logging) and 8.16 (monitoring activities) ([ISO27001]): **[unverified against the primary source]**.
- ISO/IEC 27002:2022 ([ISO27002]): **[unverified against the primary source]**.
- PCI DSS v4.0.1, requirements 10 and 3.4.1 ([PCIDSS]): **[unverified against the primary source]**.
- LGPD (Law 13.709/2018), Art. 6, item III ([LGPD-A6]): **[unverified against the primary source]**.

Test: self-review, task 1.8.

Key redaction matches keys case-insensitively, on the full key or its last dot-separated segment, recursively in maps and arrays; the redacted value is replaced by a fixed marker. Documented note: `url.path` can contain personal data and is not covered by key-based redaction; free third-party text in `body` is not redacted or validated against the catalog either. This is a known limitation, not a normative rule.

### 9.1 Audit control mapping

**CMA-001**: This document MUST include an informative annex that maps SEMLOG requirements to: OWASP ASVS 5.0.0 V16, pinned to the `v5.0.0` tag; controls 8.15 and 8.17 of ISO/IEC 27001:2022 Annex A, cited by number and title with paraphrase only, never with the copyrighted clause text; NIST SP 800-53 Rev. 5 AU-3 and AU-8; PCI DSS v4.0.1 10.2.2 (per-event content: user identification, event type, date and time, success or failure, origin, identity of the affected resource) and 10.6 (time synchronization), each cited by identifier and paraphrase (this numbering is verified against secondary sources, since the primary PCI SSC text is restricted); CWE-117, CWE-532, CWE-778; the OWASP Logging Cheat Sheet; and OWASP Top 10:2025, A09:2025 "Security Logging and Alerting Failures" (the annex MAY additionally name A09:2021 "Security Logging and Monitoring Failures" as the previous edition). SOC 2 CC7.2 MUST be excluded from the annex (External: [OWASP-ASVS], [ISO27001], [ISO27002], [NIST-SP800-53R5], [PCIDSS], [CWE-117], [CWE-532], [CWE-778], [OWASP-LOGGING], [OWASP-TOP10]). Test: task 4.30.

**CMA-002**: For each control in the annex, the entry MUST state, in two distinguishable parts, what SEMLOG's requirements and tests support versus what remains the responsibility of the adopting organization. Test: task 4.30.

> **Design decision (CMA-002).** Rationale: adopters see exactly where SEMLOG's responsibility ends and their own begins. Rejected alternative: a single "maps to" column, which on its own would imply compliance.

**CMA-003**: The annex MUST state explicitly, in immediate proximity to the mapping, that using SEMLOG does not make an adopting organization compliant with any listed control or standard, and that SEMLOG holds no certification of its own. Test: task 4.30.

> **Design decision (CMA-003).** Rationale: regulatory compliance is a property of the adopting organization, not something a library can confer on its own. Rejected alternative: silence on this point, which would lead the reader to infer a compliance claim that was never made.

**CMA-004**: The annex MUST state that SEMLOG "contributes" to a control only when a corresponding SEMLOG requirement, with its conformance test, is already declared elsewhere in this document; it MUST NOT claim a contribution to a control without such a requirement (External: [OWASP-ASVS], [NIST-SP800-53R5], [PCIDSS], [CWE-117]). Test: task 4.30.

## 10. Pipeline guarantees

**LP-001**: The system MUST be implemented entirely on top of the standard library's `logging`, and the built package MUST declare zero runtime dependencies ([PEP621]). Test: task 4.3/4.4.

**CP-003**: The built package MUST declare zero runtime (non-development) dependencies, regardless of the development, testing or benchmark tools chosen ([PEP621], [PEP517], [PEP518]). Test: task 4.3/4.4.

**LP-002**: Context capture (trace, allowed *baggage*, identity), redaction and full JSON rendering (including exception formatting) MUST happen before the record reaches the queue transport, so that the queue moves only an already finished string ([QUEUEHANDLER]). Test: task 2.29/2.30.

**LP-003**: The writer MUST start lazily, on the first record emitted in each process, and MUST NOT start at import time. The system MUST also register `os.register_at_fork` hooks: the `before` hook drains and stops the writer before the `fork`; `after_in_parent` restarts the writer immediately if it was running before the `fork`; `after_in_child` resets the writer to its lazy-start state. A server-specific hook MUST NOT be necessary for correct behavior ([QUEUEHANDLER]). Test: task 2.33/2.35.

**LP-006**: The pipeline MUST NOT try to log its own delivery failures through the same transport path it is serving; a documented fallback mechanism (for example, writing to `stderr`, counted dropping) MUST handle writer-side failures without deadlock or infinite recursion ([QUEUEHANDLER]). Resolving the output stream, in both the queue-side writer (`Writer.handle`) and any direct/synchronous emission path, MUST be robust to a replaced `sys.stdout` that lacks a `.buffer` attribute (for example, a text-only proxy installed by a process supervisor): the writer MUST fall back to a stream it can write encoded bytes to and MUST keep operating instead of terminating. Test: `tests/test_transport.py::WriterStreamResolutionTests`.

(Previously: covered only queue-side delivery failures through a documented fallback; now also explicitly covers unguarded stream-attribute resolution at both call sites.)

**LP-007**: The internal queue MUST have a configurable maximum size (`queue_size`, 10000 lines by default) and MUST NOT grow without bound. The system MUST support two overflow policies, `block` (default) and `drop` (optional). With `overflow="block"` and the queue full, the call site MUST wait until space is available; no record is lost. With `overflow="drop"`, the call site MUST NOT wait; at 90 % of `queue_size`, records below `WARNING` are dropped; the remaining 10 % is reserved for `WARNING`, `ERROR` and `CRITICAL`, which are dropped only when the queue is completely full. Every dropped record MUST be counted and reported through a `{ns}.log.records_dropped` diagnostic line ([QUEUEHANDLER], [QUEUELIB]). Test: task 2.31/2.32.

**LP-008**: The pipeline MUST be safe for concurrent use from multiple threads and MUST behave correctly across process forks (`fork`), with no corrupted shared file descriptors or locks ([QUEUELIB], [QUEUEHANDLER], [OS-FORK]). Test: task 2.35/2.37.

**LP-009**: The synchronous call-site path MUST NOT perform input/output on `stdout` by itself; the actual write MUST happen only on the writer thread. With `overflow="block"`, a call site MAY wait for queue space when the queue is full; this wait MUST be explicit and documented, and MUST NOT be confused with waiting for an input/output operation. The queue's internal mutex MUST NOT be held during input/output; it MUST be held only during the brief in-memory enqueue/dequeue operation ([QUEUEHANDLER], [QUEUELIB], [12FACTOR]). Test: task 2.31/2.37.

**LP-010**: The configuration entry point MUST NOT silently leave the root logger at the standard library's default `WARNING` level; it MUST set an explicit, configurable effective level (`INFO` by default) ([LOGGINGHOWTO]). This requirement applies only in `full` mode. In `hybrid` and `off` mode, the root logger's level MUST be left exactly as found (LM-003, LM-005). Test: `tests/test_modes.py::RootLevelScopeTests`, `tests/test_configure.py::ExplicitRootLevelTests`.

(Previously: applied unconditionally to every `configure()` call; now scoped to `full` mode, since `hybrid` and `off` must not touch the root logger.)

**LP-011**: `flush(timeout=None)` MUST detect that the writer thread is no longer alive and MUST return without waiting out the full timeout in that case, instead of blocking until the timeout elapses waiting for a marker that will never be processed. Test: `tests/test_transport.py::FlushDeadWriterTests`.

> **Design decision (LP-011).** Rationale: a dead writer thread can never process the flush marker, so waiting out the full timeout only delays shutdown and test suites without any chance of success. Rejected alternative: keeping the existing behavior, which grows a supervising process's stop time by more than 20x while waiting on an already-dead writer.

**LP-012**: The shutdown routine (`_shutdown`, which takes no argument) MUST NOT raise when semlog's installed handler is not currently attached to the root logger, for example because `configure()` never completed, or the handler was already removed. Test: `tests/test_transport.py::ShutdownNonRootTests`.

> **Design decision (LP-012).** Rationale: shutdown must be safe to call from any process state, including partially configured or already-torn-down states, since a supervising process and test teardown both call it unpredictably. Rejected alternative: assuming the handler is always present, which raises from handler-removal code in states already observed in practice.

**CP-006**: The pipeline (per LP-007) MUST demonstrate bounded queue memory and its defined overflow policy under sustained load that exceeds the writer's throughput ([QUEUELIB]). Test: task 2.31/5.2.

**CP-007**: The synchronous call-site path MUST NOT block on a shared lock held during input/output; a benchmark scenario with several concurrent threads MUST show that throughput does not collapse under contention ([QUEUEHANDLER]). Test: task 2.37/5.2.

**CP-016**: The log record flow MUST consist of exactly three fixed pipeline stages: a formatter (run before the queue, on the caller's thread, performing context enrichment, redaction, limit enforcement, exception rendering and JSON encoding as a single unit), a queue handler, and a writer thread. There MUST NOT be a separate filter stage before the formatter; context, redaction and limits MUST be part of the formatter itself. There MUST NOT be a ports/adapters layer, a dependency-injection container, or additional domain/application/infrastructure layers. Test: task 2.29/2.30.

> **Design decision (CP-016).** Rationale: with exactly three fixed stages, a single reviewer can follow the complete path of a call. Rejected alternative: introducing additional stages or filter layers, which would only add value for rendering after the queue, when the call's context has already been lost.

**CP-017**: The library MUST publish, and enforce in continuous integration, a maximum budget of **1500 non-blank, non-comment lines** of the package's own source code, excluding the tests, the UUIDv7 fallback implementation for 3.10-3.13, and the package data (including the agent guide of DOC-011). Package data is documentation content, not executable source code, and is excluded by construction because only Python source files are counted, with the standard library's `tokenize`. The budget keeps the implementation small enough to be read and reviewed as a single unit. This budget is a guardrail against unbounded growth, never a reason to sacrifice clarity: the flatness of the architecture is already guaranteed by the class budget of this same requirement and by CP-016, not by the line limit itself. Within that same budget, the package source code MUST define at most 6 classes and no abstract base class (`abc.ABC`, `ABCMeta` or `typing.Protocol`), and MUST NOT create classes through factories (`collections.namedtuple`, `typing.NamedTuple` or three-argument `type()`), for the same reason: keeping the implementation small and free of indirect class layers that would make it harder to review as a single unit. Test: task 4.5/4.6.

(Previously: a maximum budget of 1,000 lines, without the clarification that the limit is a guardrail against unbounded growth and not a design goal in itself; then raised to 1,200 with that clarification added; now 1,500, to fit the logging-modes work without compressing existing code below reviewable size.)

> **Design decision (CP-017).** Rationale: the library stays small enough to be read and reviewed in a single session, both in lines of code and in the number and shape of its classes. Rejected alternative: setting no line budget, or setting a budget that also counts package data unrelated to the executable source code; allowing an unbounded number of classes or dynamically built classes, which would make it harder to audit the type surface at a glance.

**CP-015**: The public surface of the library MUST consist of exactly eight names: `configure`, `WSGIMiddleware`, `ASGIMiddleware`, `operation`, `bind`, `inject`, `flush` and `llm`. All configuration MUST be expressed as keyword-only parameters of `configure()`; the system MUST NOT expose a configuration object or a configuration dictionary. The event catalog MUST be accepted as a JSON document (data), not as a configuration object or class. Any addition to this surface MUST be a deliberate, SemVer-relevant, documented change ([SEMVER]). Test: task 3.17/4.3.

`flush(timeout=None)`, part of CP-015's public surface, MUST drain the queue deterministically: it enqueues a marker and returns only when everything enqueued before it has been written. It is used before `os._exit` (prefork workers, `multiprocessing` children) and in tests, to make sure records have already been written before assertions (CP-015; design §6.4). Test: task 2.39/2.40.

**CP-018**: `semlog.llm()` MUST return the full text of the packaged agent guide (DOC-011) as a `str`, read through the standard library's `importlib.resources`. It MUST NOT perform any network access, MUST NOT depend on any package outside the standard library, MUST NOT read any file outside the installed `semlog` package, and MUST NOT raise an exception when the package is correctly installed. Running `python -m semlog llm` MUST write to `stdout` those same bytes encoded in UTF-8, byte-identical to what `semlog.llm()` returns, without adding a newline of its own and without writing anything to `stderr`, and MUST exit with status code 0. Any other use of arguments (none, an unknown subcommand, extra arguments) MUST print a single usage line to `stderr`, nothing to `stdout`, and exit with status code 2. This entry point MUST NOT declare a console script: it is invoked only as `python -m semlog llm`, to guarantee that it always runs with the interpreter where that SEMLOG is installed ([AGENTSMD]). Test: task 3.15/3.16, 4.21-4.24.

**CP-019**: This change's internal per-mode performance comparison (before/after measurements for `full`, `hybrid` marked, `hybrid` unmarked and `off`, and the added cost of the class-level `Logger._log` patch on plain stdlib calls, measured with standard library tools per CP-012) MUST be produced entirely outside the repository: its script and measured results MUST NOT be tracked by the repository's version control. The script's first line and the first line of its printed results MUST each carry a literal content marker identifying them as this comparison's own artifacts, so a conformance check can recognize and flag either one if it is ever added to the tracked tree by mistake. A figure from this comparison MUST NOT appear in README.md, README.es.md, BENCHMARKS.md or CHANGELOG.md; the first three files already carry CP-005's generic no-benchmark-figures test, and this requirement extends that same coverage to CHANGELOG.md. Test: `tests/test_doc_conformance.py::Cp019InternalBenchmarkUntrackedTests`.

> **Design decision (CP-019).** Rationale: the maintainer required a one-time per-mode performance check before shipping this change, distinct from CP-005's ongoing loguru/structlog comparison; keeping its script and results untracked, on top of CP-005's already-tested no-figures policy, avoids publishing a comparison that goes stale immediately after this release and avoids duplicating CP-005's reproducible, ongoing benchmark machinery for a one-time check. Rejected alternative: publishing this comparison's methodology and harness in BENCHMARKS.md alongside CP-005's, which was rejected because it would present a one-time internal regression check as an ongoing, reproducible benchmark subject the maintainer explicitly did not want committed.

### 10.1 Execution modes

**LM-001**: The system MUST expose an execution mode through the keyword-only `mode` parameter of `configure()`, accepting exactly `"full"` (default), `"hybrid"`, `"off"`. When not given explicitly, `mode` MUST resolve from, in order: the `SEMLOG_MODE` environment variable; the `mode` key under `[tool.semlog]` in `pyproject.toml` (read with `tomllib`, Python 3.11+ only, mirroring SI-001/SI-005's existing precedent, skipped on 3.10); then the `"full"` default. An empty `SEMLOG_MODE` MUST be treated as absent, falling through to the next source, matching the common deployment idiom of a variable declared but left blank; a value that is present but outside the three accepted strings, from any source including an explicitly empty string under `[tool.semlog].mode`, MUST raise `ValueError` naming the invalid value and its source. `mode` MUST stay wording-distinct from `catalog_mode`/LRC-008's `off`/`warn`/`strict` catalog validation strictness; the two MUST never be conflated in documentation or diagnostics. Test: `tests/test_modes.py::ModeResolutionTests`.

> **Design decision (LM-001).** Rationale: gradual production rollout requires a kill switch reachable without code changes; explicit > env > pyproject > default mirrors SI-001's already-established precedence pattern; treating an empty environment variable as absent matches the common shell/container idiom of a declared-but-blank variable, while an empty string under `[tool.semlog].mode` is a real, deliberately written value with no such idiom to excuse it. Rejected alternative: a single environment-only switch, which would prevent per-call testing and per-service overrides; or raising on an empty `SEMLOG_MODE` too, which would break that common idiom for no benefit.

**LM-002**: Once `semlog` has been imported, every call to any `logging.Logger` method (`debug`, `info`, `warning`, `error`, `critical`, `exception`, `log`) MUST accept the keyword-only argument `semlog=True`, in every mode, including before `configure()` runs. This keyword MUST NEVER raise. Caller-visible `LogRecord` metadata (`filename`, `lineno`, `funcName`, `module`) MUST be identical to the same call site without `semlog=True`, on Python 3.10 through 3.14. The caller's `extra` dict object MUST NOT be mutated. The internal marker MUST NEVER appear, under any name, in JSON output, legacy text output, or the caller's `extra` dict after the call returns. Test: `tests/test_call_site_keyword.py`.

> **Design decision (LM-002).** Rationale: a marked call made from framework startup hooks (for example `AppConfig.ready()` or a WSGI preload hook) must never crash the host process; arming the patch on the existing `Logger` class at import time, not inside `configure()`, guarantees this without adding a class beyond the CP-017 budget. Rejected alternative: arming inside `configure()`, which reproduces the exact preload-time crash observed with an unpatched `Logger._log`; or a `setLoggerClass()` custom Logger, which breaches CP-017's class budget and misses loggers already obtained before the switch.

**LM-003**: In `hybrid` mode, `configure()` MUST NOT modify the root logger's existing handlers or level. A record logged with `semlog=True` MUST be additionally emitted as one semlog JSON record, exactly once, through the existing three-stage pipeline (CP-016). That JSON emission MUST be hidden from any `logging.StreamHandler` instance or subclass in the hierarchy; every other handler (for example, an observer handler such as an error-tracking integration built on a non-`StreamHandler` `logging.Handler` subclass) MUST still receive the marked record unchanged. An unmarked call MUST produce output on every `StreamHandler` byte-identical to a process where `semlog` was never imported. Records the library owns internally (the `"semlog"` diagnostics logger, and `http.server.request` from `log_requests=True`) MUST always route to JSON only, never to a `StreamHandler`. Severity filtering MUST follow the standard library's effective-level rules for marked calls. `configure(mode="hybrid", capture_loggers=...)` MUST raise `ValueError` when `capture_loggers` is given. Test: `tests/test_hybrid.py`.

> **Design decision (LM-003).** Rationale: legacy dashboards need byte-identical `StreamHandler` output while observer handlers such as error-tracking integrations must keep receiving every record; suppression must be scoped by handler type, not by a global patch that silences every handler. Rejected alternative: a global `Handler.handle` patch, lab-proven to also block observer handlers that are `logging.Handler` subclasses but not `StreamHandler` subclasses.

**LM-004**: README.md, README.es.md (DOC-012) and the agent guide (DOC-011) MUST document, as a known limitation of hybrid mode, that LM-003's suppression is implemented as a patch on `logging.StreamHandler.handle` itself, reaching only calls that go through that exact method: a handler outside that synchronous dispatch path -- for example a `logging.handlers.QueueHandler` paired with a `QueueListener`, a `logging.handlers.MemoryHandler`, or any other custom handler that renders legacy text through a stream it manages internally without being a `StreamHandler` subclass -- MAY still receive and render marked records, and so MAY a `StreamHandler` subclass that overrides `handle()` without calling `super().handle()`, since that override never reaches the patched method; a subclass that calls `super().handle()` remains correctly suppressed. None of this MUST be treated as a defect. Test: `tests/test_readme_i18n.py::ReadmeModesParityTests`, `tests/test_agent_docs_anti_drift.py::AgentGuideModesTests`, `tests/test_hybrid.py::HybridRoutingTests`.

> **Design decision (LM-004).** Rationale: reliably distinguishing every legacy-rendering handler from every observer handler is not possible by class alone, and a method-level patch cannot reach an override that never calls it; the boundary must be documented rather than heuristically guessed or fixed by a more invasive patch. Rejected alternative: auto-detecting "legacy-like" handlers beyond `StreamHandler`, which would be unreliable; or patching at the instance level to reach every override, which would require rewriting every existing handler instance instead of the one shared class method.

**LM-005**: In `off` mode, `configure()` MUST behave as if `semlog` were never imported, except that `semlog=True` MUST never raise (LM-002). A marked call MUST produce output byte-identical to the same call without the keyword. `configure()` MUST NOT install any handler and MUST NOT modify the root logger. Every internal diagnostic MUST be silent. `operation()` MUST keep working as a context manager, and `bind()` MUST return without effect; `WSGIMiddleware` and `ASGIMiddleware` (including `log_requests=True`) MUST keep their documented pass-through mechanics (headers still forwarded, the wrapped application still called) but MUST emit no record, field, or side effect of their own. Test: `tests/test_off_mode.py`, `tests/test_modes.py::PreConfigureOffTests`.

> **Design decision (LM-005).** Rationale: a rollback switch that leaves any semlog-owned side effect running is not a real rollback; the maintainer explicitly chose "as if not installed" over filtering marked logs. Rejected alternative: silencing marked logs while keeping diagnostics/`log_requests` active, rejected as a partial disable, not a real kill switch.

## 11. Compatibility and support policy

**CP-001**: The system MUST support CPython 3.10 to 3.15; version 3.15 MAY be configured as "allowed to fail" in continuous integration until the dependent frameworks declare support ([PYVERSIONS], [SPEC0]). Test: task 4.1/4.16.

**CP-002**: The system MUST pass automated tests, run under the standard library's `unittest`, against FastAPI (`async def` and synchronous `def` endpoints) and Django (synchronous views over WSGI, asynchronous views over ASGI), at both the minimum and the latest supported versions:

| Row | Framework/version | Python versions | Role |
|---|---|---|---|
| 1 | FastAPI 0.71.0 (Starlette 0.17.1 pinned) | 3.10 only | minimum |
| 2 | Django 3.2.9 | 3.10 only | minimum |
| 3 | FastAPI 0.141.1 / Starlette 1.6.0 | 3.10-3.14 | latest |
| 4 | Django 5.2 LTS | 3.10-3.14 | latest |
| 5 | Django 6.1 | 3.12-3.14 | latest |
| 6 | any declared later version | 3.15 | allowed to fail |

Empirical evidence (verified by the coordinator, 2026-09-10): FastAPI 0.71.0 synchronous and asynchronous correct on 3.10 via Starlette 0.17.1's `run_in_threadpool` with explicit `contextvars.copy_context()`; Django 3.2.9 synchronous and asynchronous over ASGI and WSGI, correct on 3.10 ([FASTAPI-RELEASES], [DJANGO-3.2.9], [PYVERSIONS]). Test: task 4.10-4.13.

**CP-004**: Releases MUST follow SemVer 2.0.0, the changelog MUST follow the Keep a Changelog format, commit messages MUST follow Conventional Commits, and the license MUST be declared as SPDX `Apache-2.0` ([SEMVER], [KEEPACHANGELOG], [CONVCOMMITS], [SPDX]). Test: task 4.14.

**CP-005**: A throughput/latency benchmark against `loguru`, `structlog` and a standard library `logging` baseline MUST run only after the implementation and the test matrix are complete. The methodology (subjects, scenarios, fairness rules, repetitions and the environment disclosure the harness reports on every run) and the measurement harness (`benchmark/`, with the command to run it) MUST be published in BENCHMARKS.md, so that the measurement is reproducible; README.md and README.es.md need not mention the benchmark. The benchmark results (measured figures, results tables or charts, environment data from a specific run and comparative conclusions) MUST NOT be published in README.md, README.es.md or BENCHMARKS.md until the maintainer validates them; unmeasured performance claims MUST NOT be published. The benchmark MUST use only standard library measurement tools (CP-012). Test: task 5.1-5.5.

(Previously: the methodology, the environment and the measured numbers had to be published in BENCHMARKS.md, and every performance figure shown by README.md or README.es.md had to be one of those published in BENCHMARKS.md; now the methodology and the harness are published, and the results are withheld until the maintainer validates them. BENCHMARKS.md also had to be linked from README.md and README.es.md; that link is no longer required.)

> **Design decision (CP-005).** Rationale: an unmeasured performance claim is misleading, and benchmarking a still incomplete library produces noise instead of evidence; a measured figure the maintainer has not validated yet can also be misleading, even when it comes from a real run, while publishing the methodology and the harness lets anyone reproduce the measurement. Rejected alternative: publishing performance targets as if they were already measured claims, running the benchmark before the implementation and the compatibility matrix are finished, or publishing the results of a run before the maintainer validates them.

**CP-008**: Any tool, library or package outside the Python standard library, used anywhere in development, testing, building or benchmarking, MUST be: (a) from the standard library, (b) one of the test/benchmark subjects explicitly approved by the user (FastAPI, Starlette, Django, loguru, structlog), (c) `ruff` (approved for lint and format, locally and in continuous integration), (d) GitHub Actions (approved for continuous integration), (e) the approved build backend (section 11.1), (f) the `uv` CLI (Astral), approved as the build frontend and development environment manager (`uv build`, `uv sync`, `uv run`), used in development and in continuous integration, never at runtime, or (g) a tool explicitly approved by the user before its use. `mypy`, `coverage.py`, `jsonschema`, `gunicorn` and `uvicorn` are NOT approved; where their function would be needed, the standard library alternative is used (the in-house JSON Schema subset validator instead of `jsonschema`; no third-party coverage tool; instead of `gunicorn` and `uvicorn`, the tests exercise the middlewares through the standard library's `wsgiref` for WSGI (CP-010) and a hand-written ASGI `scope`/`receive`/`send` caller for ASGI (CP-011)). The exception for the build backend and frontend exists because `distutils` was removed from the standard library in Python 3.12 ([PEP632]); the backend is declared through the PEP 517 interface and the PEP 518 `[build-system]` table ([PEP517], [PEP518]). Test: task 4.2/4.7.

**CP-009**: All unit and integration tests of the library's own code MUST run under the standard library's `unittest` (`python -m unittest discover`), not under a third-party test framework such as `pytest` ([UNITTEST]). Test: task 4.16 (the full suite).

**CP-010**: Tests that exercise the WSGI middleware MUST drive it through the standard library's `wsgiref` (or a hand-built WSGI `environ` using only the standard library), not a third-party WSGI test client ([PEP3333], [WSGIREF]). Test: task 3.1.

**CP-011**: Tests that exercise the ASGI middleware MUST drive it through a minimal `scope`/`receive`/`send` caller, hand-written in the library's own test code, not a third-party ASGI test client ([ASGI]). Test: task 3.3.

**CP-012**: The benchmark MUST measure time with the standard library's `timeit` and/or `time.perf_counter_ns`, and MUST measure memory with the standard library's `tracemalloc`. It MUST NOT depend on `pyperf`, `pytest-benchmark`, or any other third-party benchmark package ([TIMEIT], [TRACEMALLOC]). Test: task 5.3.

**CP-013**: The only third-party packages usable anywhere in the project are: FastAPI, Starlette, Django (compatibility matrix subjects); `loguru`, `structlog` (benchmark comparison subjects); `ruff` (lint and format tool); and the approved build backend together with its frontend CLI (`uv_build` and the `uv` CLI, section 11.1), needed because `distutils` was removed from the standard library in Python 3.12 ([PEP632]) and every build backend is declared according to PEP 517 and PEP 518 ([PEP517], [PEP518]). Any other third-party package MUST NOT be introduced without new explicit approval from the maintainer. Test: task 4.2.

### 11.1 Build backend

**CP-014**: The project's build backend (PEP 517/518) MUST be `uv_build` (Astral), declared in `pyproject.toml` as:

```toml
[build-system]
requires = ["uv_build>=0.12.13,<0.13"]
build-backend = "uv_build"
```

The upper version bound follows the recommendation of the `uv` documentation. It is the only deliberate, declared exception to the standard-library-only policy of section 11 (CP-008/CP-013), because `distutils` was removed in Python 3.12 ([PEP632]) and the standard library includes no build backend implementation: PEP 517 defines the interface a build backend must satisfy and PEP 518 requires declaring the build system in `pyproject.toml`, but neither provides an implementation ([PEP517], [PEP518]). `uv_build` is a build-time-only tool: the installed package still declares zero runtime dependencies (LP-001, CP-003). It produces pure Python packages (suitable for SEMLOG), supports the `src/<package>/__init__.py` layout by default (configurable through `module-root`/`module-name`, without this document prescribing a file layout), and is distributed under the MIT OR Apache-2.0 license, which is the license of the tool itself, distinct from SEMLOG's license (section 13). The approved build frontend, the tool invoked to operate this backend during development and in continuous integration, is the `uv` CLI (Astral), per DOC-008. Test: task 4.15.

**DOC-008**: The tooling policy of this document and of AGENTS.md MUST explicitly declare: the policy of using only the standard library by default; the fixed list of third-party test/benchmark subjects approved by the user (FastAPI, Starlette, Django, loguru, structlog); `ruff` as the approved lint and format tool; the approval of GitHub Actions for continuous integration; the approved build backend, `uv_build`; the `uv` CLI (Astral), approved as the build frontend and development environment manager (`uv build`, `uv sync`, `uv run`); the `pip`-based fallback commands, documented for when `uv` is unavailable (`python -m pip wheel . --no-deps --wheel-dir dist` and `pip install -e .`); an explicit statement that `uv` is used in development and in continuous integration, never at runtime, and that the built wheel keeps zero `Requires-Dist` regardless of this choice; the explicit non-approval of `mypy`, `coverage.py`, `jsonschema`, `gunicorn` and `uvicorn`; any future item pending approval (none pending in this revision); and the existence of the documentation set aimed at coding agents (`llms.txt`, `AGENTS.md` and the agent guide packaged inside the library; see DOC-009, DOC-010, DOC-011), recording that writing it introduces no new runtime or development dependency beyond what this policy already approves ([PEP517], [PEP518], [PEP632]). Test: self-review, task 1.8; tasks 1.10-1.12.

### 11.2 Documentation for coding agents

**DOC-009**: The system MUST publish an `llms.txt` file at the repository root, written in English and conforming to the llms.txt proposal (v2): a level-1 heading naming the project (`# semlog`); a summary in blockquote format; optionally, a short paragraph with the core call-site usage rules (static event names, variables only through `extra`, `logger.exception` called exactly once at the boundary that handles the exception); and one or more level-2 sections, each with a list of links in `[name](url): notes` format, pointing at least to README.md, STANDARDS.md, the agent guide (DOC-011) and the published JSON Schema documents (DOC-004). An `## Optional` section MAY list secondary links that an agent MAY skip under a reduced context budget. Every link in `llms.txt` MUST resolve to an existing file in the repository ([LLMSTXT]). Test: task 1.11/4.17.

**DOC-010**: The system MUST publish an `AGENTS.md` file at the repository root, written in English and conforming to the agents.md convention, aimed at coding agents that CONTRIBUTE to SEMLOG, not at call sites that consume the published library. It MUST state the exact commands to set up the environment (`uv sync`), run the test suite (`uv run python -m unittest discover`), lint (`uv run ruff check`), check formatting (`uv run ruff format --check`) and build the package with the approved `uv_build` backend through the uv CLI (`uv build`); it MUST state the `pip`-based fallback for each of these commands when `uv` is unavailable (`pip install -e .` for setup; `python -m unittest discover`, `ruff check` and `ruff format --check` once installed; `python -m pip wheel . --no-deps --wheel-dir dist` for the build); the project's hard rules (standard-library-only tooling per CP-008, zero runtime dependencies, the flat three-stage architecture per CP-016, and the source line budget per CP-017); the strict TDD workflow; the list of approved third-party packages (CP-013); the prohibition of any company or vendor name in code or defaults; and references to STANDARDS.md and to the project specification ([AGENTSMD]). Test: task 1.12/4.19.

**DOC-011**: The system MUST include a single, self-sufficient agent guide, as a Markdown document written in English, that lets a coding agent apply SEMLOG correctly at the call site OFFLINE, without network access. It MUST be included INSIDE the distributed package as package data, so that its content always stays paired with the installed library version; it is the ONLY agent-facing document included in the distributed wheel (`llms.txt`, `AGENTS.md`, this document (STANDARDS.md), README.md and the JSON schemas remain in the repository only). The stored guide file MUST NOT contain the literal of the installed SEMLOG version or of the pinned OTel semconv version; instead, the OUTPUT of `semlog.llm()` and `python -m semlog llm` (CP-018) MUST be preceded by a header, generated at read time (no cache), that states the installed SEMLOG version, read from the installed distribution's metadata through the standard library's `importlib.metadata` (the same lookup used for `telemetry.sdk.version`), and the pinned OTel semconv version, read from a single constant in the package source code. Its content MUST include: the complete public API surface (each public name with its signature); the call-site MUST/MUST NOT rules (static event names per the event name grammar; variables passed only through `extra`, under the configured custom namespace or a declared OTel semconv key; `logger.exception` called exactly once at the boundary that handles the exception; no f-string, dictionary interpolation or `json.dumps` used to build the log message; no secret or request/response body logged; no call to `print`); the level-to-severity semantics table (per DOC-006); a summary of the output contract with at least one real single-line JSON example; the configuration parameters and their environment variable precedence, including the `mode` parameter (its three values, resolution precedence, and that an invalid value raises `ValueError`) and the `semlog=True` keyword (accepted in every mode once `semlog` is imported, its per-mode effect, and the hybrid non-stream-handler limitation of LM-004); integration recipes for FastAPI and Django; guidance on when to call `configure`, the two middlewares, `bind`, `inject`, `operation` and `flush`; a before/after call-site pattern migration checklist; and a review checklist that a coding agent can apply mechanically. This document is the ONLY source of the agent-facing narrative content: `llms.txt` and `AGENTS.md` MUST link to it instead of duplicating its call-site rules or its checklists. On a broken installation (guide file missing), the resource error MUST propagate unmodified, instead of returning a fallback text that would mislead the agent ([AGENTSMD], [LLMSTXT]). Test: `tests/test_agent_docs_anti_drift.py`.

(Previously: covered call-site rules for the pre-existing API only; now also covers `mode` and the `semlog=True` keyword.)

**DOC-012**: README.md and README.es.md MUST document, in parity (DOC-002): the three modes and their resolution precedence (LM-001); the `semlog=True` keyword and its cross-mode guarantees (LM-002); the hybrid non-stream-handler limitation (LM-004); and `off` mode as the rollback switch, including the documented caveat that uninstalling `semlog` while marked call sites remain in the code raises `TypeError` at the call site rather than failing silently. `llms.txt`'s optional short usage paragraph (DOC-009), if present, MUST stay consistent with this content. Test: `tests/test_readme_i18n.py::ReadmeModesParityTests`.

> **Design decision (DOC-012).** Rationale: extends DOC-002's existing README parity policy to this change's new user-facing surface, so the two languages and the agent-facing files never diverge on how to adopt or roll back the feature. Rejected alternative: leaving mode/keyword documentation solely to STANDARDS.md and the agent guide, which would leave human adopters reading only README.md without any parity guarantee and risk README.md and README.es.md diverging silently.

**DOC-013**: CHANGELOG.md MUST include a `## [0.2.0]` section, in Keep a Changelog format ([KEEPACHANGELOG], CP-004), with a non-empty `### Added` subsection covering the three modes and the `semlog=True` keyword, and a non-empty `### Fixed` subsection covering the writer stream-resolution and dead-writer-detection fixes (LP-006, LP-011). Test: `tests/test_packaging_hygiene.py::Changelog020Tests`.

### 11.3 Python support

Python 3.10 reaches its end of life in October 2026; `pyproject.toml`-based identity detection is not supported on that version (section 2.3, SI-001/SI-005). The framework-free core is also tested on 3.14t (no global interpreter lock, *free-threaded*) on an informative basis, without blocking continuous integration.

### 11.4 Publishing and provenance

**PRV-001**: Releases MUST be published to PyPI only through Trusted Publishing (OIDC-based, with no long-lived API tokens stored anywhere), run from GitHub Actions through the approved `pypa/gh-action-pypi-publish` action. That action MUST be pinned by its full commit SHA, and PEP 740 attestations MUST be enabled (the action's own default behavior) (External: [PYPI-TP], [PEP740]). Test: task 4.29.

**PRV-002**: Every publishing workflow MUST use a dedicated, protected GitHub environment (`pypi`) for its publishing job; MUST grant `id-token: write` only to the publishing jobs, to no other; MUST declare least-privilege `permissions` in every workflow of the repository; MUST publish only a version identified by its `v<version>` tag: pushed manually, created in the same run when merging into `main` (PRV-006), or already existing and given as a required input when the release workflow is run manually, in which case continuous integration, verification and the build MUST operate on that tag's commit; MUST run the publication in a job of the top-level workflow itself, never inside a reusable workflow, because PyPI does not support a reusable workflow as a trusted publisher ([PYPI-TP-TROUBLESHOOTING]); and MUST NOT publish while the repository is private (External: [PYPI-TP], [PYPI-TP-TROUBLESHOOTING]). Test: task 4.27/4.28.

**PRV-003**: The documentation MUST tell consumers of the library how to verify the PEP 740 attestations of a downloaded distribution (for example, with `pypi-attestations verify pypi ...`). The documentation MUST NOT claim that `pip` or `uv` verify attestations automatically at install time, and MUST NOT claim any SLSA level (External: [PYPI-ATTESTATIONS]). Test: task 1.28.

**PRV-004**: The build frontend MUST be `uv build` to produce the distributable artifacts (wheel and sdist). `uv publish` MUST NOT be used to upload those artifacts to PyPI; the upload MUST be done only through the approved `pypa/gh-action-pypi-publish` action (PRV-001), because `uv publish` supports Trusted Publishing but does not generate PEP 740 attestations by itself (External: [UV-PUBLISH]). Test: task 4.29.

**PRV-005**: The version of every change merged into `main` MUST be derived from the commit messages since the last release tag, that is, the highest `vMAJOR.MINOR.PATCH` tag, up to the head of the pull request, according to Conventional Commits ([CONVCOMMITS]): `feat` requires a minor version; `fix` and `perf`, a patch version; a `!` after the type or scope, or a `BREAKING CHANGE:` footer, requires a major version, except while the major version is 0 (initial development, [SEMVER] item 4), in which case it requires a minor version; `docs`, `test`, `ci`, `chore`, `refactor`, `build` and `style` require no release; and merge commits are ignored. The highest level among all those commits wins. A subject that does not follow Conventional Commits, a type outside that list, or a `v*` tag that does not have the `vMAJOR.MINOR.PATCH` form, MUST be rejected as an error and MUST NOT be treated as "no release". An automated check of every pull request to `main` MUST fail when the `pyproject.toml` version is not the expected one: the released version bumped by exactly the required level, or the released version unchanged if no commit requires a release; and, before the first release (with no release tag at all), the version the base branch already declares, unchanged, while still validating the pull request's own commits. It MUST also fail when a version not yet released, including the first release, has no non-empty `## [X.Y.Z] - YYYY-MM-DD` section in CHANGELOG.md ([KEEPACHANGELOG]). That decision logic MUST be implemented with the standard library only (CP-008) (External: [CONVCOMMITS], [SEMVER], [KEEPACHANGELOG]). Test: `tests/test_release_policy.py`, `tests/test_repository_policy.py`.

**PRV-006**: Every merge into `main` MUST create the annotated tag `v<version>` for the version declared in `pyproject.toml`, with that version's CHANGELOG.md section as its message, only after continuous integration and the build of that same commit succeed. If the tag already exists in the remote repository, the workflow MUST finish successfully, without changes, and say so in its log. The tag MUST be created by `github-actions[bot]` with `git` and the default `GITHUB_TOKEN`, without third-party actions; `contents: write` MUST be granted only to the job that creates the tag, that job MUST NOT run repository code, and its checkout MUST be the only one that keeps credentials. The publication of that version MUST happen in the same run and MUST wait for manual approval of the protected `pypi` environment (PRV-002). Test: `tests/test_repository_policy.py`, `tests/test_release_policy.py`.

> **Design decision (PRV-006).** Rationale: every version merged into `main` is identified by its tag without a manual step that could be forgotten, while publication stays subject to explicit human approval; since a tag pushed with the `GITHUB_TOKEN` does not start other workflows, publication happens in the same run. Rejected alternative: tagging and publishing by hand, which depends on the maintainer's memory, or creating the tag with a third-party action, which would add an unapproved dependency (CP-008).

### 11.5 OpenSSF signals and security policy

**OSF-001**: Before the first public release, the project MUST satisfy the mandatory criteria of the OpenSSF Best Practices Badge "passing" level: a publicly readable version control repository; a FLOSS license; a published vulnerability reporting process (a `SECURITY.md` file) with a documented commitment to an initial response within 14 days; and an automated test suite (External: [OPENSSF-BADGE]). Test: task 4.27/4.28.

**OSF-002**: Continuous integration MUST follow the Scorecard-aligned practices that require no new GitHub action: every action referenced by any workflow MUST be pinned by commit SHA; every workflow MUST declare least-privilege `GITHUB_TOKEN` permissions; and the branch protection rules MUST be documented in the repository (External: [OPENSSF-SCORECARD]). Test: task 4.27/4.28.

**OSF-003**: OpenSSF Scorecard itself MUST be run manually, either through the Scorecard CLI or the public scorecard.dev viewer, once the repository is public. A Scorecard GitHub action MUST NOT be added to continuous integration; adding one requires first requesting explicit approval from the project owner (not granted yet). Test: task 4.27/4.28.

> **Design decision (OSF-003).** Rationale: the Scorecard action is not approved by the project owner, and a manual run produces the same signal without adding a continuous integration dependency. Rejected alternative: adding the Scorecard action to continuous integration.

**OSF-004**: Applying for the OpenSSF Best Practices Badge MUST be a manual step, started by the maintainer, as a self-assessment on bestpractices.dev; it MUST NOT be automated as part of any continuous integration workflow (External: [OPENSSF-BADGE]). Test: task 4.27/4.28.

## 12. Normative and informative references

### Normative

- [RFC2119] Bradner, S., "Key words for use in RFCs to Indicate Requirement Levels", BCP 14, RFC 2119, March 1997. https://www.rfc-editor.org/rfc/rfc2119
- [RFC8174] Leiba, B., "Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words", BCP 14, RFC 8174, May 2017. https://www.rfc-editor.org/rfc/rfc8174
- [RFC8259] Bray, T., Ed., "The JavaScript Object Notation (JSON) Data Interchange Format", STD 90, RFC 8259, December 2017. https://www.rfc-editor.org/rfc/rfc8259
- [RFC3339] Klyne, G. and C. Newman, "Date and Time on the Internet: Timestamps", RFC 3339, July 2002. https://www.rfc-editor.org/rfc/rfc3339
- [ISO8601-1] ISO 8601-1:2019/Amd 1:2022, "Date and time: Representations for information interchange, Part 1: Basic rules". https://www.iso.org/standard/70907.html
- [RFC4648] Josefsson, S., "The Base16, Base32, and Base64 Data Encodings", RFC 4648, October 2006. https://www.rfc-editor.org/rfc/rfc4648
- [RFC9562] Davis, K., Peabody, B. and P. Leach, "Universally Unique IDentifiers (UUIDs)", RFC 9562, May 2024. https://www.rfc-editor.org/rfc/rfc9562
- [RFC6648] Saint-Andre, P., Crocker, D. and M. Nottingham, "Deprecating the 'X-' Prefix and Similar Constructs in Application Protocols", BCP 178, RFC 6648, June 2012. https://www.rfc-editor.org/rfc/rfc6648
- [TRACECONTEXT] W3C, "Trace Context", W3C Recommendation, 23 November 2021. https://www.w3.org/TR/trace-context/
- [BAGGAGE] W3C, "Propagation format for distributed context: Baggage", W3C Candidate Recommendation Snapshot, 30 May 2024. https://www.w3.org/TR/baggage/
- [OTEL-LOGS-DATAMODEL] OpenTelemetry, "Logs Data Model" (Stable). https://opentelemetry.io/docs/specs/otel/logs/data-model/
- [OTEL-NONOTLP-TRACE] OpenTelemetry, "Trace Context in non-OTLP Log Formats" (Stable). https://opentelemetry.io/docs/specs/otel/compatibility/logging_trace_context/
- [OTEL-SEMCONV] OpenTelemetry, "Semantic Conventions", pinned version v1.44.0. https://opentelemetry.io/docs/specs/semconv/
- [OTEL-SDK-ENV] OpenTelemetry, "SDK Environment Variables" (variables cited in section 6, stable). https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/
- [ECS-EVENT] Elastic Common Schema, field `event.duration` (long, nanoseconds). https://www.elastic.co/guide/en/ecs/current/ecs-event.html
- [PEP567] "Context Variables", Final, Python 3.7. https://peps.python.org/pep-0567/
- [PEP621] "Storing project metadata in pyproject.toml", Final. https://peps.python.org/pep-0621/
- [PEP440] "Version Identification and Dependency Specification", Final. https://peps.python.org/pep-0440/
- [PEP517] "A build-system independent format for source trees", Final. https://peps.python.org/pep-0517/
- [PEP518] "Specifying Minimum Build System Requirements for Python Projects", Final. https://peps.python.org/pep-0518/
- [PEP632] "Deprecate distutils module", Final. https://peps.python.org/pep-0632/
- [PEP3333] "Python Web Server Gateway Interface v1.0.1", Final. https://peps.python.org/pep-3333/
- [ASGI] "ASGI (Asynchronous Server Gateway Interface) Specification", v3.0, 2019-03-20. https://asgi.readthedocs.io/en/latest/specs/main.html
- [QUEUEHANDLER] Python Software Foundation, "logging.handlers: QueueHandler and QueueListener". https://docs.python.org/3/library/logging.handlers.html
- [QUEUELIB] Python Software Foundation, "queue: A synchronized queue class". https://docs.python.org/3/library/queue.html
- [OS-FORK] Python Software Foundation, "os.register_at_fork". https://docs.python.org/3/library/os.html#os.register_at_fork
- [LOGRECORD] Python Software Foundation, "logging: Logging facility for Python, LogRecord attributes". https://docs.python.org/3/library/logging.html#logrecord-attributes
- [LOGGINGHOWTO] Python Software Foundation, "Logging HOWTO". https://docs.python.org/3/howto/logging.html
- [UNITTEST] Python Software Foundation, "unittest: Unit testing framework". https://docs.python.org/3/library/unittest.html
- [WSGIREF] Python Software Foundation, "wsgiref: WSGI Utilities and Reference Implementation". https://docs.python.org/3/library/wsgiref.html
- [TIMEIT] Python Software Foundation, "timeit: Measure execution time of small code snippets". https://docs.python.org/3/library/timeit.html
- [TRACEMALLOC] Python Software Foundation, "tracemalloc: Trace memory allocations". https://docs.python.org/3/library/tracemalloc.html
- [OTEL-ATTR-NAMING] OpenTelemetry, "Attribute Naming" (Semantic Conventions, General). https://opentelemetry.io/docs/specs/semconv/general/attribute-naming/
- [SEMVER] Preston-Werner, T., "Semantic Versioning 2.0.0". https://semver.org/spec/v2.0.0.html
- [JSONSCHEMA] "JSON Schema Specification, 2020-12". https://json-schema.org/specification
- [SPDX] "SPDX Specification" (= ISO/IEC 5962:2021), license identifier Apache-2.0. https://spdx.dev/
- [PYVERSIONS] Python Developer's Guide, "Status of Python versions". https://devguide.python.org/versions/
- [SPEC0] "SPEC 0: Minimum Supported Dependencies". https://scientific-python.org/specs/spec-0000/
- [DJANGO-3.2.9] Django Software Foundation, "Django 3.2.9 release notes" (2021-11-01; "Django 3.2.9 fixes a bug in 3.2.8 and adds compatibility with Python 3.10"). https://docs.djangoproject.com/en/5.2/releases/3.2.9/
- [FASTAPI-RELEASES] FastAPI, "Release Notes" (0.71.0 upgrades Starlette to 0.17.1 and adds docs and tests for Python 3.9 and 3.10). https://fastapi.tiangolo.com/release-notes/
- [PYPI-TP] PyPI, "Using a Trusted Publisher". https://docs.pypi.org/trusted-publishers/using-a-publisher/
- [PYPI-TP-TROUBLESHOOTING] PyPI, "Trusted Publishers: Troubleshooting", section "Reusable workflows on GitHub". https://docs.pypi.org/trusted-publishers/troubleshooting/
- [PEP740] "Index support for digital attestations", Final. https://peps.python.org/pep-0740/

### Informative

- [JSONLINES] JSON Lines, community convention. https://jsonlines.org/
- [MSGTEMPLATES] Message Templates (precedent for "static messages, variables as fields"). https://messagetemplates.org/
- [RFC5424] Gerhards, R., "The Syslog Protocol", RFC 5424 (only as an informative reference for severities; there is no one-to-one mapping with OTel/Python). https://www.rfc-editor.org/rfc/rfc5424
- [12FACTOR] "The Twelve-Factor App", XI. Logs (informative only: design rationale for event streams on `stdout`). https://12factor.net/logs
- [KEEPACHANGELOG] "Keep a Changelog" v1.1.0. https://keepachangelog.com/en/1.1.0/
- [CONVCOMMITS] "Conventional Commits" v1.0.0. https://www.conventionalcommits.org/en/v1.0.0/
- [OWASP-LOGGING] OWASP, "Logging Cheat Sheet". https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- [OWASP-ASVS] OWASP Application Security Verification Standard v5.0.0, published 2025-05-30, chapter V16 "Security Logging and Error Handling", pinned to the `v5.0.0` tag. https://github.com/OWASP/ASVS/blob/v5.0.0/5.0/en/0x25-V16-Security-Logging-and-Error-Handling.md
- [ISO27001] ISO/IEC 27001:2022, Annex A 8.11/8.15/8.16/8.17, **unverified against the primary source** (see DOC-007 and Annex B).
- [ISO27002] ISO/IEC 27002:2022, **unverified against the primary source** (see DOC-007 and Annex B).
- [PCIDSS] PCI DSS v4.0.1, requirements 10 (including 10.2.2, per-event content, and 10.6, time synchronization; numbering verified against secondary sources) and 3.4.1, **unverified against the primary source** (see DOC-007 and Annex B).
- [LGPD-A6] Lei 13.709/2018 (LGPD), Art. 6 III, **unverified against the primary source** (see DOC-007).
- [NIST-SP800-53R5] NIST, "Security and Privacy Controls for Information Systems and Organizations", SP 800-53 Rev. 5, controls AU-3 and AU-8. https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final
- [CWE-117] MITRE, "CWE-117: Improper Output Neutralization for Logs". https://cwe.mitre.org/data/definitions/117.html
- [CWE-532] MITRE, "CWE-532: Insertion of Sensitive Information into Log File". https://cwe.mitre.org/data/definitions/532.html
- [CWE-778] MITRE, "CWE-778: Insufficient Logging". https://cwe.mitre.org/data/definitions/778.html
- [OWASP-TOP10] OWASP, "OWASP Top 10" project. The edition this document cites normatively is OWASP Top 10:2025, A09:2025 "Security Logging and Alerting Failures"; the previous edition, A09:2021 "Security Logging and Monitoring Failures", is named only as a historical reference. Verified against the primary source on 2026-09-11: category A09:2025 maps, among others, CWE-117, CWE-532 and CWE-778. https://top10.owasp.org/2025/A09_2025-Security_Logging_and_Alerting_Failures
- [PYPI-ATTESTATIONS] PyPI Docs, "Consuming attestations". https://docs.pypi.org/attestations/consuming-attestations/
- [UV-PUBLISH] Astral, "uv: Publishing a package". https://docs.astral.sh/uv/guides/package/
- [OPENSSF-BADGE] OpenSSF, "Best Practices Badge: Criteria (passing level)". https://www.bestpractices.dev/en/criteria/0
- [OPENSSF-SCORECARD] OpenSSF, "Scorecard: Check Documentation". https://github.com/ossf/scorecard/blob/main/docs/checks.md
- [LLMSTXT] Howard, J., "llms.txt", community convention proposal (not a formal standard), version 2, August 2026. https://llmstxt.org/
- [AGENTSMD] Agentic AI Foundation (Linux Foundation), "AGENTS.md: a README for agents", community convention. https://agents.md/

## 13. License

SEMLOG is distributed under the Apache-2.0 license (SPDX identifier `Apache-2.0`, [SPDX]).

---

## Annex A: requirement traceability

The specification's requirement identifiers, with the section of this document that covers each one, its backing (DOC-003/TRC-004) and the planned test from Phase 2 onward. None is left without a normative keyword or a planned test.

| Requirement | Section | Backing | Planned test |
|---|---|---|---|
| LRC-001 | 2 | External: [RFC8259], [JSONLINES] | 2.13, 2.27 |
| LRC-002 | 2 | External: [RFC3339], [ISO8601-1] | 2.13 |
| LRC-003 | 3 | External: [OTEL-LOGS-DATAMODEL], [RFC5424] | 2.13 |
| LRC-004 | 2 | External: [MSGTEMPLATES], [OTEL-LOGS-DATAMODEL] | 2.13 |
| LRC-005 | 2 | External: [OTEL-NONOTLP-TRACE], [RFC4648] | 2.13 |
| LRC-006 | 4 | External: [OTEL-SEMCONV] | 2.15 |
| LRC-007 | 5 | External: [OTEL-ATTR-NAMING] | 2.17 |
| LRC-008 | 5 | Design decision | 2.19 |
| LRC-009 | 6 | External: [OTEL-SDK-ENV] | 2.21 |
| LRC-010 | 6 | External: [OTEL-SDK-ENV] | 2.21 |
| LRC-011 | 4 | External: [LOGRECORD] | 2.15, 2.16 |
| LRC-012 | 2 | Design decision | 2.13 |
| LRC-013 | 5 | External: [OTEL-SEMCONV] | 2.17 |
| LP-001 | 10 | External: [PEP621] | 4.3, 4.4 |
| LP-002 | 10 | External: [QUEUEHANDLER] | 2.29, 2.30 |
| LP-003 | 10 | External: [QUEUEHANDLER] | 2.33, 2.35 |
| LP-004 | 7 | External: [OTEL-SEMCONV], [OWASP-ASVS] | 2.25, 2.26 |
| LP-005 | 9 | External: [OWASP-LOGGING], [OWASP-ASVS] | 2.23, 2.24 |
| LP-006 | 10 | External: [QUEUEHANDLER] | `tests/test_transport.py::WriterStreamResolutionTests` |
| LP-007 | 10 | External: [QUEUEHANDLER], [QUEUELIB] | 2.31, 2.32 |
| LP-008 | 10 | External: [QUEUELIB], [QUEUEHANDLER], [OS-FORK] | 2.35, 2.37 |
| LP-009 | 10 | External: [QUEUEHANDLER], [QUEUELIB], [12FACTOR] | 2.31, 2.37 |
| LP-010 | 10 | External: [LOGGINGHOWTO] | `tests/test_modes.py::RootLevelScopeTests`, `tests/test_configure.py::ExplicitRootLevelTests` |
| LP-011 | 10 | Design decision | `tests/test_transport.py::FlushDeadWriterTests` |
| LP-012 | 10 | Design decision | `tests/test_transport.py::ShutdownNonRootTests` |
| LM-001 | 10.1 | Design decision | `tests/test_modes.py::ModeResolutionTests` |
| LM-002 | 10.1 | Design decision | `tests/test_call_site_keyword.py` |
| LM-003 | 10.1 | Design decision | `tests/test_hybrid.py` |
| LM-004 | 10.1 | Design decision | `tests/test_readme_i18n.py::ReadmeModesParityTests`, `tests/test_agent_docs_anti_drift.py::AgentGuideModesTests`, `tests/test_hybrid.py::HybridRoutingTests` |
| LM-005 | 10.1 | Design decision | `tests/test_off_mode.py`, `tests/test_modes.py::PreConfigureOffTests` |
| SI-001 | 2.3 | External: [PEP621], [OTEL-SDK-ENV] | 2.1, 2.2 |
| SI-002 | 2.3 | External: [OTEL-SDK-ENV], [PEP621], [PEP440] | 2.1, 2.2 |
| SI-003 | 2.3 | External: [OTEL-SEMCONV] | 2.1, 2.2 |
| SI-004 | 2.3 | Design decision | 2.1, 2.2 |
| SI-005 | 2.3 | External: [PEP621] | 2.1, 2.2 |
| TCP-001 | 8, 8.1 | External: [TRACECONTEXT] | 2.7, 4.8 |
| TCP-002 | 8, 8.1 | External: [TRACECONTEXT] | 2.7, 4.8 |
| TCP-003 | 8, 8.1 | External: [TRACECONTEXT] | 2.7, 4.8 |
| TCP-004 | 8 | External: [TRACECONTEXT] | 2.7 |
| TCP-005 | 8 | External: [PEP567] | 2.5, 2.6, 3.7 |
| TCP-006 | 8, 8.3 | External: [TRACECONTEXT], [BAGGAGE] | 3.11, 3.12 |
| TCP-007 | 8 | External: [BAGGAGE] | 2.9, 2.10, 4.9 |
| TCP-008 | 8, 8.3 | External: [BAGGAGE] | 3.11, 3.12 |
| TCP-009 | 8, 8.3 | External: [RFC6648] | 3.11, 3.12 |
| TCP-010 | 8 | External: [RFC9562] | 2.11, 2.12 |
| TCP-011 | 8, 8.3 | External: [TRACECONTEXT] | 3.11, 3.12 |
| TCP-012 | 8.3 | Design decision | `tests/test_bind.py`, `tests/test_off_mode.py::OffDiagnosticsTests` |
| HTM-001 | 8.2 | External: [PEP3333], [TRACECONTEXT] | 3.1, 3.2 |
| HTM-002 | 8.2 | External: [ASGI] | 3.3, 3.4 |
| HTM-003 | 8.2 | External: [PEP3333], [ASGI] | 3.1, 3.3 |
| HTM-004 | 8.2 | External: [PEP3333], [ASGI] | 3.1, 3.3 |
| HTM-005 | 8.2 | External: [ASGI] | 3.3, 3.4 |
| HTM-006 | 8.2 | External: [PEP567] | 3.5, 3.6 |
| HTM-007 | 8.2 | External: [ECS-EVENT] | `tests/test_log_requests.py`, `tests/test_hybrid.py::HybridRequestEventTests`, `tests/test_off_mode.py::OffRequestEventTests` |
| DOC-001 | preamble | Design decision | 1.9 |
| DOC-002 | 1, whole document | Design decision | 1.8 |
| DOC-003 | 1.1, Annex A | Design decision | 1.8 |
| DOC-004 | 2.1, 5, 11.2 | External: [JSONSCHEMA] | 1.6, 1.7 |
| DOC-005 | 6 | Design decision | 1.8 |
| DOC-006 | 3 | External: [OTEL-LOGS-DATAMODEL], [RFC5424] | 1.8 |
| DOC-007 | 9 | External: [ISO27001], [ISO27002], [PCIDSS], [LGPD-A6] | 1.8 |
| DOC-008 | 11 | External: [PEP517], [PEP518], [PEP632] | 1.8 |
| DOC-009 | 11.2 | External: [LLMSTXT] | 1.11, 4.17 |
| DOC-010 | 11.2 | External: [AGENTSMD] | 1.12, 4.19 |
| DOC-011 | 11.2 | External: [AGENTSMD], [LLMSTXT] | 1.10, 3.15, 3.16, 4.16, 4.18, 4.20, 4.21, 4.22, 4.23 |
| DOC-012 | 11.2 | Design decision | `tests/test_readme_i18n.py::ReadmeModesParityTests` |
| DOC-013 | 11.2 | External: [KEEPACHANGELOG] | `tests/test_packaging_hygiene.py::Changelog020Tests` |
| CP-001 | 11 | External: [PYVERSIONS], [SPEC0] | 4.1, 4.16 |
| CP-002 | 11 | External: [FASTAPI-RELEASES], [DJANGO-3.2.9], [PYVERSIONS] | 4.10, 4.11, 4.12, 4.13 |
| CP-003 | 10 | External: [PEP621], [PEP517], [PEP518] | 4.3, 4.4 |
| CP-004 | 11 | External: [SEMVER], [KEEPACHANGELOG], [CONVCOMMITS], [SPDX] | 4.14 |
| CP-005 | 11 (and BENCHMARKS.md) | Design decision | 5.1, 5.2, 5.3, 5.4, 5.5 |
| CP-006 | 10 | External: [QUEUELIB] | 2.31, 5.2 |
| CP-007 | 10 | External: [QUEUEHANDLER] | 2.37, 5.2 |
| CP-008 | 11 | External: [PEP517], [PEP518], [PEP632] | 4.2, 4.7 |
| CP-009 | 11 | External: [UNITTEST] | 4.16 |
| CP-010 | 11 | External: [PEP3333], [WSGIREF] | 3.1 |
| CP-011 | 11 | External: [ASGI] | 3.3 |
| CP-012 | 11 | External: [TIMEIT], [TRACEMALLOC] | 5.3 |
| CP-013 | 11 | External: [PEP517], [PEP518], [PEP632] | 4.2 |
| CP-014 | 11.1 | External: [PEP632], [PEP517], [PEP518] | 4.15 |
| CP-015 | 10 | External: [SEMVER] | 3.17, 4.3, 2.39, 2.40 |
| CP-016 | 10 | Design decision | 2.29, 2.30 |
| CP-017 | 10 | Design decision | 4.5, 4.6 |
| CP-018 | 10 | External: [AGENTSMD] | 3.15, 3.16, 4.21, 4.22, 4.23, 4.24 |
| CP-019 | 10 | Design decision | `tests/test_doc_conformance.py::Cp019InternalBenchmarkUntrackedTests` |
| TRC-001 | 1.1 | Design decision | 2.1, 2.2 |
| TRC-002 | 1.1 | Design decision | 2.1, 2.2 |
| TRC-003 | 1.1 | Design decision | 2.1, 2.2 |
| TRC-004 | 1.1 | Design decision | 1.20 |
| CMA-001 | 9.1 | External: [OWASP-ASVS], [ISO27001], [ISO27002], [NIST-SP800-53R5], [PCIDSS], [CWE-117], [CWE-532], [CWE-778], [OWASP-LOGGING], [OWASP-TOP10] | 4.30 |
| CMA-002 | 9.1 | Design decision | 4.30 |
| CMA-003 | 9.1 | Design decision | 4.30 |
| CMA-004 | 9.1 | External: [OWASP-ASVS], [NIST-SP800-53R5], [PCIDSS], [CWE-117] | 4.30 |
| PRV-001 | 11.4 | External: [PYPI-TP], [PEP740] | 4.29 |
| PRV-002 | 11.4 | External: [PYPI-TP], [PYPI-TP-TROUBLESHOOTING] | 4.27, 4.28 |
| PRV-003 | 11.4 | External: [PYPI-ATTESTATIONS] | 1.28 |
| PRV-004 | 11.4 | External: [UV-PUBLISH] | 4.29 |
| PRV-005 | 11.4 | External: [CONVCOMMITS], [SEMVER], [KEEPACHANGELOG] | `tests/test_release_policy.py`, `tests/test_repository_policy.py` |
| PRV-006 | 11.4 | Design decision | `tests/test_repository_policy.py`, `tests/test_release_policy.py` |
| OSF-001 | 11.5 | External: [OPENSSF-BADGE] | 4.27, 4.28 |
| OSF-002 | 11.5 | External: [OPENSSF-SCORECARD] | 4.27, 4.28 |
| OSF-003 | 11.5 | Design decision | 4.27, 4.28 |
| OSF-004 | 11.5 | External: [OPENSSF-BADGE] | 4.27, 4.28 |

## Annex B: audit control mapping (informative)

This annex is informative. Using SEMLOG does not make an organization compliant with any control or standard in this table, and SEMLOG holds no certification. The "SEMLOG contributes" column only lists requirements of this document that have a conformance test; compliance depends on the adopting organization.

| Control | What it requires (paraphrase) | SEMLOG contributes | The adopting organization must |
|---|---|---|---|
| OWASP ASVS 5.0.0 16.1.1 | Log inventory per layer, with a retention policy ([OWASP-ASVS]). | LRC-007, LRC-008 (the event catalog as a per-service inventory) | Keep the inventory across all layers; define retention |
| OWASP ASVS 16.2.1 | When, where, who and what metadata in every entry ([OWASP-ASVS]). | LRC-002, SI-001, SI-002, SI-003, LRC-003, LRC-004, LRC-005, TCP-010 | Provide the who as a declared attribute; choose the events to log |
| OWASP ASVS 16.2.2 | Synchronized clock; UTC or an explicit time offset ([OWASP-ASVS]). | LRC-002 | Synchronize the host clocks |
| OWASP ASVS 16.2.4 | A common format that the log processor can correlate ([OWASP-ASVS]). | LRC-001, LRC-005, LRC-006, DOC-004 | Ingest the logs with the published schema |
| OWASP ASVS 16.2.5 | Sensitive data excluded or protected according to its classification ([OWASP-ASVS]). | LP-005, HTM-007 | Define `redact_keys` from its data classification; never log full bodies |
| OWASP ASVS 16.4.1 | Encoding that prevents log injection ([OWASP-ASVS]). | LRC-001 | Do not write to the same stream by other means |
| OWASP ASVS 16.2.3, 16.3.1-16.3.4, 16.4.2, 16.4.3, 16.5.1-16.5.4 | Allowed destinations, security events, log protection, secure transport and error handling ([OWASP-ASVS]). | none | All of the above |
| ISO/IEC 27001:2022 A.8.15 Logging (unverified) | Produce, store, protect and analyze logs of activities, exceptions and events ([ISO27001], [ISO27002]). | LRC-001, LRC-002, LRC-003, LP-004, SI-001, SI-003, LP-005 | Storage, protection against tampering, retention, review |
| ISO/IEC 27001:2022 A.8.17 Clock synchronization (unverified) | Clocks synchronized with an approved source ([ISO27001], [ISO27002]). | LRC-002 | Host clock synchronization |
| NIST SP 800-53r5 AU-3 | Event type, time, location, source, outcome and identity ([NIST-SP800-53R5]). | LRC-004, LRC-002, SI-001, SI-003, LRC-005, LRC-007 | Log the outcome and the identity; choose additional enhancements |
| NIST SP 800-53r5 AU-8 | Timestamps from internal clocks, in UTC or with a fixed offset ([NIST-SP800-53R5]). | LRC-002 | Clock accuracy and granularity policy |
| PCI DSS v4.0.1 10.2.2 (unverified) | Audit logs enabled, with the required detail per event ([PCIDSS]). | LP-010, LRC-002, LRC-004, SI-001 | Coverage of every in-scope component, user identity, retention, access review |
| PCI DSS v4.0.1 10.6 (unverified) | Time synchronization ([PCIDSS]). | LRC-002 | NTP; protect the time data |
| CWE-117 | Neutralize log injection ([CWE-117]). | LRC-001 | Do not perform raw writes (`print`) to the same stream |
| CWE-532 | No sensitive data in logs ([CWE-532]). | LP-005, HTM-007 | Sensitive key list; no secrets or full bodies; control access to the destination |
| CWE-778 | Log security-critical events with sufficient detail ([CWE-778]). | LP-010, LP-007, LP-004 (no silent loss; full exception detail) | Decide which security events to log |
| OWASP Logging Cheat Sheet | When, where, who and what; sanitize; exclude secrets ([OWASP-LOGGING]). | LRC-001, LRC-002, LRC-004, SI-001, LRC-005, LP-005, DOC-004 | Centralize, protect, monitor, alert |
| OWASP Top 10:2025 A09:2025 | Security logging and alerting failures ([OWASP-TOP10]). | LRC-001, LP-005, LP-010 | Monitoring, alerting, and the categories not covered by the library |

SOC 2 (criterion CC7.2) is not included: its text could not be verified against the primary source.
