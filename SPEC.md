# OmniMock Technical Specification

**Document status:** Draft for implementation  
**Project:** OmniMock  
**Document version:** 0.1.0  
**Target release:** 0.1.x foundation and first simulators  
**Last updated:** 2026-07-21  
**Primary implementation language:** Python 3.12+  
**License recommendation:** Apache-2.0

> **OmniMock is a contract-first, deterministic-first, LLM-assisted platform for
> simulating data services across APIs, streams, filesystems, databases, object
> stores, webhooks, and Model Context Protocol (MCP).**

---

## 1. Purpose

OmniMock gives developers one consistent way to create realistic, stateful,
observable, and reproducible simulations of external data services.

A user should be able to supply one or more of the following:

- an OpenAPI, AsyncAPI, GraphQL, protobuf, SQL, JSON Schema, or MCP contract;
- recorded traffic;
- fixtures and deterministic rules;
- a natural-language description;
- an existing filesystem or dataset;
- a real service to proxy, learn from, or record.

OmniMock then exposes one or more simulated interfaces, such as:

- HTTP/REST and webhooks;
- GraphQL;
- gRPC;
- Server-Sent Events and WebSocket streams;
- Kafka-, MQTT-, AMQP-, or NATS-style messaging through transport plugins;
- a sandboxed filesystem;
- S3-compatible object storage;
- SQL, key-value, document, or vector data services;
- MCP tools, resources, and prompts.

The LLM is an **authoring and fallback component**, not the source of truth.
Contract validation, deterministic rules, fixtures, recorded interactions, and
state transitions take precedence over model-generated behavior.

---

## 2. Requirements language

The words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**, and
**MAY** are used in the RFC 2119 sense.

A requirement marked **MVP** is required for the first stable public release.
A requirement marked **Later** is part of the architectural contract but may be
implemented after the MVP.

---

## 3. Product goals

### 3.1 Primary goals

1. **One scenario, many interfaces**  
   A single domain model and state store can drive REST responses, stream
   events, files, database rows, and MCP tools consistently.

2. **Deterministic by default**  
   Given the same scenario revision, seed, logical clock, request sequence, and
   state snapshot, OmniMock MUST produce the same results.

3. **LLM-assisted, not LLM-dependent**  
   Scenarios MUST remain runnable without model access after generated artifacts
   have been materialized.

4. **Contract-first correctness**  
   Inputs and outputs MUST be validated against declared contracts whenever a
   contract exists.

5. **Modular protocol support**  
   Each simulation type MUST be an independently testable plugin behind stable
   core ports.

6. **Local-first and CI-friendly**  
   The default experience MUST work on a developer laptop and in an isolated CI
   runner without a hosted control plane.

7. **Safe by default**  
   Filesystem access, outbound network access, template execution, secrets, and
   LLM data exposure MUST be constrained.

8. **Production-grade operability**  
   Configuration, logs, metrics, traces, health checks, graceful shutdown,
   error handling, and deployment behavior MUST follow service best practices.

### 3.2 Secondary goals

- record and replay traffic;
- generate coherent synthetic datasets;
- simulate latency, outages, throttling, duplicates, reordering, and malformed
  data;
- branch and reset scenario state;
- export generated artifacts for use without OmniMock;
- support third-party plugins without modifying the core repository.

---

## 4. Non-goals

OmniMock is not intended to:

- replace a production database, broker, object store, or API gateway;
- guarantee bug-for-bug compatibility with every vendor;
- execute arbitrary model-generated Python, shell, SQL, templates, or binaries;
- infer business rules perfectly from sparse examples;
- hide nondeterminism while claiming reproducibility;
- become a general workflow engine;
- send real side effects to external systems unless an explicit proxy or
  passthrough policy authorizes the destination;
- store production secrets or unredacted production traffic by default.

For complex protocols such as Kafka or PostgreSQL, OmniMock SHOULD prefer
orchestrating a real disposable backing service over reimplementing the entire
wire protocol in Python.

---

## 5. Core principles

### 5.1 Contract before creativity

The simulator resolves behavior in this order:

1. explicit failure or latency policy;
2. exact deterministic rule;
3. state-machine transition;
4. fixture;
5. recorded interaction;
6. deterministic synthetic generator;
7. precomputed LLM artifact;
8. live LLM fallback, only when enabled;
9. protocol-appropriate unmatched-operation error.

A live model MUST NOT silently override a matching rule, contract, fixture, or
recording.

### 5.2 Materialize before serving

The preferred workflow is:

```text
describe/import -> compile -> generate -> validate -> materialize -> serve
```

Live generation on a request path is opt-in. Build-time or cache-miss generation
is preferred because it improves latency, reproducibility, cost control, and
security.

### 5.3 One semantic core, multiple protocol projections

The same entity can appear through several interfaces. For example, creating an
order through REST can:

- insert an order into state;
- publish an `order.created` event;
- create an invoice file;
- expose the order through an MCP resource;
- make the row visible through a SQL simulator.

These are projections of one committed state transition, not independent mock
responses.

### 5.4 Explicit capability boundaries

A plugin declares exactly what it supports. Unsupported features MUST fail
validation before the runtime starts, rather than degrading silently.

### 5.5 Replaceability

Core business logic MUST depend on ports, not on FastAPI, a model SDK, SQLite,
Kafka, Docker, or any other adapter implementation.

---

## 6. Representative user stories

### 6.1 Contract-driven API

```bash
omnimock init
omnimock import openapi ./contracts/payments.yaml
omnimock generate --scenario payments-demo
omnimock serve
```

A client can call the generated HTTP API, receive schema-valid responses, and
observe stateful behavior across create, read, update, and delete operations.

### 6.2 Multi-interface scenario

A scenario describes a logistics platform. OmniMock serves:

- a REST shipment API;
- a WebSocket location stream;
- a filesystem containing manifests;
- an MCP server exposing `find_shipment` and `reroute_shipment`;
- a SQL database projection.

A state change through any write-capable interface is visible through the other
interfaces after the same transaction commits.

### 6.3 Record and replay

```bash
omnimock record \
  --service billing \
  --upstream https://sandbox.example.invalid \
  --redaction config/redaction.yaml

omnimock replay --scenario billing-recording
```

Requests are matched using configurable canonicalization. Dynamic identifiers,
timestamps, and secrets are normalized or redacted.

### 6.4 Failure simulation

```bash
omnimock run checkout-chaos \
  --set faults.payment.timeout_probability=0.15 \
  --set faults.inventory.latency.p95=800ms
```

The run records the resolved seed and fault decisions so failures can be
reproduced exactly.

---

## 7. System architecture

### 7.1 Logical architecture

```text
                         ┌──────────────────────────┐
                         │ CLI / Control API / UI   │
                         └─────────────┬────────────┘
                                       │
                         ┌─────────────▼────────────┐
                         │ Application Services     │
                         │ compile, run, inspect,   │
                         │ reset, record, export    │
                         └─────────────┬────────────┘
                                       │
               ┌───────────────────────▼───────────────────────┐
               │                  Domain Core                  │
               │ scenario, contract, rules, state, clock,     │
               │ envelopes, transitions, faults, provenance   │
               └───────────┬──────────────┬─────────────┬──────┘
                           │              │             │
                  ┌────────▼──────┐ ┌─────▼──────┐ ┌──▼───────────┐
                  │ Simulator     │ │ Generation │ │ Persistence  │
                  │ plugin ports  │ │/model port │ │ ports        │
                  └───────┬───────┘ └─────┬──────┘ └──┬───────────┘
                          │               │             │
        ┌─────────────────┼───────────────┼─────────────┼───────────┐
        │                 │               │             │           │
   HTTP/GraphQL       Streams/MCP     Model adapters  SQLite     Proxy/record
   gRPC/Webhooks      FS/Object       and routers     memory     containers
```

### 7.2 Control plane and data plane

The **control plane** is responsible for:

- loading and validating configuration;
- compiling contracts;
- materializing generated artifacts;
- starting and stopping services;
- inspecting state, recordings, and telemetry;
- resetting, snapshotting, branching, and exporting scenarios.

The **data plane** is responsible for:

- accepting simulated protocol traffic;
- normalizing it into internal envelopes;
- resolving behavior;
- reading and mutating state;
- enforcing faults, limits, and authorization;
- returning protocol-native responses or events.

Control-plane operations MUST NOT be exposed publicly by default.

### 7.3 Execution modes

Each service declares one execution mode:

| Mode | Description | Typical uses |
|---|---|---|
| `native` | OmniMock implements the interface in-process | HTTP, SSE, WebSocket, MCP, filesystem control |
| `managed` | OmniMock provisions/configures a real disposable backing service | PostgreSQL, Kafka, Redis, MinIO |
| `proxy` | Traffic is forwarded to an allowed upstream and optionally recorded | contract discovery, sandbox integration |
| `replay` | Recorded interactions are served without an upstream | CI, offline development |
| `hybrid` | Rules/fixtures are served locally; selected misses may proxy or generate | incremental adoption |

A plugin MUST report its supported modes in its manifest.

---

## 8. Domain model

### 8.1 Workspace

A workspace is a repository or directory containing all declarative project
artifacts:

```text
omnimock.yaml
models/
config/
contracts/
scenarios/
fixtures/
recordings/
schemas/
```

Runtime state and generated caches SHOULD live outside version control under
`.omnimock/` unless explicitly exported.

### 8.2 Scenario

A `Scenario` is an immutable definition plus mutable runtime state.

Immutable definition:

- scenario identifier and semantic version;
- imported contracts and their digests;
- service declarations;
- initial state;
- behavior rules;
- generator policies;
- model routing;
- fault policies;
- authorization policy;
- deterministic seed policy.

Mutable runtime data:

- current logical clock;
- entity state;
- append-only transition journal;
- stream offsets;
- idempotency records;
- generated artifact cache;
- fault decisions;
- snapshots.

### 8.3 Service

A `Service` is one protocol projection owned by a simulator plugin.

Required fields:

- `id`;
- `kind`;
- `plugin`;
- `mode`;
- `contract`;
- `listen` or managed-service connection policy;
- `behavior`;
- `state_namespace`;
- `limits`;
- `faults`;
- `auth`;
- `tags`.

### 8.4 Operation

An `Operation` is an addressable action in a service contract, such as:

- `POST /orders`;
- GraphQL `Mutation.createOrder`;
- gRPC `OrderService/CreateOrder`;
- publish to `orders.created`;
- read `/invoices/{id}.json`;
- SQL `INSERT INTO orders`;
- MCP tool `create_order`.

Every operation has a stable internal `operation_id`. Contract importers MUST
generate a deterministic ID when the source contract lacks one.

### 8.5 Canonical envelopes

Protocol plugins translate traffic to and from canonical envelopes.

```python
@dataclass(frozen=True, slots=True)
class RequestEnvelope:
    request_id: str
    run_id: str
    service_id: str
    operation_id: str
    protocol: str
    received_at: datetime
    logical_time: datetime
    metadata: Mapping[str, JsonValue]
    payload: JsonValue | bytes | None
    principal: Principal | None
    trace_context: TraceContext | None
```

```python
@dataclass(frozen=True, slots=True)
class ResponseEnvelope:
    outcome: Literal["success", "error", "timeout", "disconnect"]
    metadata: Mapping[str, JsonValue]
    payload: JsonValue | bytes | None
    state_version: int
    provenance: Provenance
```

```python
@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    event_type: str
    source: str
    subject: str | None
    occurred_at: datetime
    logical_time: datetime
    schema_ref: str | None
    headers: Mapping[str, JsonValue]
    payload: JsonValue | bytes | None
```

Protocol-specific details remain in typed metadata namespaces. Core logic MUST
not depend on raw framework request objects.

### 8.6 Provenance

Every response, event, generated file, or row MUST be attributable to one of:

- `rule`;
- `state_machine`;
- `fixture`;
- `recording`;
- `deterministic_generator`;
- `llm_materialized`;
- `llm_live`;
- `proxy`;
- `managed_backing_service`.

Provenance includes the scenario revision, contract digest, seed, rule or
recording ID, model profile ID when applicable, and state version.

---

## 9. Behavior engine

### 9.1 Request pipeline

```text
accept
  -> normalize
  -> authenticate/authorize
  -> validate input
  -> canonicalize
  -> resolve operation
  -> resolve behavior
  -> plan state transition
  -> validate planned output
  -> commit transition
  -> apply protocol-safe fault/latency
  -> emit response/events
  -> journal provenance and telemetry
```

### 9.2 Rule matching

Rules MAY match on:

- service and operation ID;
- method, path, channel, tool, resource URI, or file path;
- canonical headers and metadata;
- JSONPath-like payload predicates;
- current state predicates;
- call count;
- logical time;
- principal or claims;
- prior events;
- deterministic percentage buckets.

Rules MUST have explicit priority. Ties MUST be rejected during validation.

### 9.3 State transitions

State-changing behavior uses a declarative transition plan:

```yaml
when:
  operation: orders.create
validate:
  - expression: input.quantity > 0
mutate:
  - put:
      collection: orders
      key: "${uuid.deterministic('order', request.request_id)}"
      value:
        id: "${mutation.key}"
        status: pending
        sku: "${input.sku}"
        quantity: "${input.quantity}"
        created_at: "${clock.now}"
emit:
  - type: order.created
    channel: orders.events
    payload: "${mutation.value}"
respond:
  status: 201
  body: "${mutation.value}"
```

The expression language MUST be side-effect-free, bounded, and sandboxed.
Arbitrary Python evaluation is forbidden.

### 9.4 Transaction semantics

A behavior resolution produces a `TransitionPlan`. The plan is validated before
commit. A successful commit atomically:

1. checks the expected state version;
2. writes state mutations;
3. appends the transition journal record;
4. stores idempotency data;
5. schedules resulting events;
6. increments the state version.

If a protocol supports a response before asynchronous publication, the journal
still records publication intent in the same transaction.

### 9.5 Idempotency

Plugins SHOULD map native idempotency mechanisms into the core idempotency port.
For HTTP, configurable headers such as `Idempotency-Key` can identify a request.
Repeated requests with the same key and canonical input MUST return the original
outcome unless the scenario explicitly models different behavior.

---

## 10. Simulator plugin architecture

### 10.1 Plugin contract

Third-party plugins are discovered through Python package entry points:

```toml
[project.entry-points."omnimock.simulators"]
my_protocol = "omnimock_my_protocol.plugin:create_plugin"
```

A plugin implements:

```python
class SimulatorPlugin(Protocol):
    manifest: PluginManifest

    def contract_compilers(self) -> Sequence[ContractCompiler]: ...
    def create_runtime(self, context: RuntimeContext) -> SimulatorRuntime: ...
    def diagnostics(self) -> Sequence[DiagnosticCheck]: ...
```

A runtime implements:

```python
class SimulatorRuntime(Protocol):
    async def prepare(self) -> None: ...
    async def start(self) -> Sequence[BoundEndpoint]: ...
    async def drain(self) -> None: ...
    async def stop(self) -> None: ...
```

### 10.2 Plugin manifest

The manifest contains:

- plugin ID and version;
- core API compatibility range;
- supported service kinds;
- supported execution modes;
- supported contract formats and versions;
- optional dependency extra;
- platform requirements;
- required backing services;
- capability flags;
- security declarations;
- health and diagnostic checks.

### 10.3 Capability negotiation

Scenario validation compares requested capabilities with the plugin manifest.
Examples:

- HTTP callbacks;
- GraphQL subscriptions;
- gRPC reflection;
- Kafka transactions;
- filesystem watch events;
- MCP sampling;
- object versioning.

Unsupported combinations are configuration errors, not warnings.

### 10.4 Plugin isolation

Plugins MUST NOT:

- import another simulator plugin directly;
- mutate global process state at import time;
- configure root logging;
- read environment variables outside the configuration adapter;
- create outbound connections before `prepare`;
- bypass the core authorization, limits, provenance, or telemetry ports;
- execute untrusted generated code.

Optional high-risk plugins MAY run in subprocess isolation.

---

## 11. Built-in and planned simulator modules

### 11.1 HTTP and REST — MVP

Capabilities:

- OpenAPI 3.1.x and 3.2.x import;
- JSON, text, form, multipart, and binary bodies;
- path, query, header, and cookie parameters;
- request/response validation;
- callbacks and webhooks;
- RFC 9457 problem details;
- conditional requests and pagination policies;
- authentication simulation;
- CORS policy;
- configurable versioned base paths;
- record, replay, proxy, and hybrid modes.

Defaults:

- bind to `127.0.0.1`;
- reject undeclared operations in strict mode;
- omit request and response bodies from logs;
- return problem details for validation failures;
- preserve protocol semantics before applying synthetic behavior.

### 11.2 GraphQL — MVP or first extension

Capabilities:

- schema definition language import;
- queries, mutations, and subscriptions;
- introspection;
- variable and result validation;
- partial data with structured errors;
- deterministic resolver mapping;
- DataLoader-style request batching where useful;
- subscription projection onto the stream core.

The GraphQL plugin MUST not synthesize fields absent from the schema unless a
contract-generation command is explicitly being used.

### 11.3 gRPC — first extension

Capabilities:

- protobuf descriptor set or `.proto` import;
- unary, client-streaming, server-streaming, and bidirectional methods;
- metadata and deadlines;
- canonical gRPC status codes;
- health checking;
- optional server reflection;
- deterministic stream scripts.

Generated messages MUST pass protobuf serialization before being emitted.

### 11.4 WebSocket and Server-Sent Events — MVP

Capabilities:

- scripted connection lifecycle;
- message schemas;
- heartbeats;
- reconnect and resume tokens;
- backpressure;
- delayed, dropped, duplicated, and reordered messages;
- deterministic disconnects;
- event projections from shared state.

Unbounded per-client queues are forbidden.

### 11.5 Broker-neutral streams — MVP core, transports in phases

The domain stream core provides:

- named channels;
- partitions;
- offsets;
- consumer groups;
- delivery attempts;
- acknowledgements;
- retention policy;
- ordering policy;
- schema references;
- dead-letter routes;
- replay from an offset or logical time.

Transport plugins map this model to:

- in-memory test streams;
- SSE and WebSocket;
- Kafka through a managed container or compatible broker;
- MQTT through a managed broker;
- AMQP/NATS/Pulsar through optional plugins.

OmniMock SHOULD orchestrate real disposable brokers for protocol fidelity instead
of implementing complex wire protocols itself.

### 11.6 Filesystem — MVP

The filesystem simulator manages a dedicated sandbox root and supports:

- deterministic directory and file generation;
- text and binary fixtures;
- metadata, permissions, ownership-like labels, and timestamps;
- atomic writes and renames;
- configured read/write errors;
- capacity limits;
- watch-event publication;
- snapshots and reset;
- materialization into a real temporary directory;
- optional FUSE projection on supported platforms.

Security requirements:

- all paths are normalized relative to the sandbox root;
- symlinks that escape the root are rejected;
- path traversal and device files are rejected;
- executable bits are disabled by default;
- generated content cannot trigger arbitrary post-processing hooks;
- host paths require explicit allowlisting.

### 11.7 Object storage — extension

The object-storage plugin supports an S3-compatible subset:

- buckets and keys;
- metadata and content types;
- range reads;
- multipart upload;
- versioning policy;
- pre-signed URL simulation;
- list consistency modes;
- lifecycle events;
- fault and latency injection.

The preferred full-fidelity mode is managed MinIO or another compatible
disposable service.

### 11.8 SQL databases — extension

The SQL simulator is a managed-service harness, not a new SQL parser or wire
protocol.

Capabilities:

- schema import from SQL or migrations;
- synthetic seed data;
- PostgreSQL and SQLite first;
- transaction reset and snapshot;
- query logging with value redaction;
- deterministic sequences and generated values;
- fault controls such as lock waits, connection failures, and read replicas;
- projection from shared OmniMock state where configured.

Raw model-generated SQL MUST be parsed, allowlisted, and executed only against a
disposable simulator database. It MUST never run against an arbitrary user
connection.

### 11.9 Key-value, document, cache, and vector stores — extension

Adapters MAY manage Redis-compatible, document, or vector backing services.
Each must declare the supported command or query subset and persistence model.

Vector generation SHOULD use configured embedding profiles and materialize
vectors ahead of runtime. Tests MUST be able to use deterministic fake
embeddings.

### 11.10 MCP — MVP

OmniMock primarily simulates MCP servers and MAY later simulate clients.

Server capabilities:

- tools;
- resources and resource templates;
- prompts;
- logging, progress, and cancellation;
- capability negotiation;
- supported transports from the selected MCP specification;
- stateful sessions;
- schema-valid tool inputs and results;
- deterministic errors and latency;
- resources projected from files, entities, events, or recordings.

MCP sampling, elicitation, roots, or other client-provided capabilities MUST be
disabled unless explicitly configured. Tool descriptions are untrusted input.
Any tool that could access host data or perform side effects requires an
authorization policy.

### 11.11 Webhooks and scheduled producers — MVP

A scheduler can emit webhook calls, stream messages, file changes, or state
transitions according to logical time.

Real outbound webhook destinations are denied by default. Allowed destinations
MUST match explicit scheme, host, port, and network-range policies.

---

## 12. Scenario configuration

### 12.1 Root file

The canonical root is `omnimock.yaml`.

```yaml
schema_version: "1"

project:
  id: commerce-demo
  default_scenario: checkout
  artifact_dir: .omnimock/artifacts

runtime:
  host: 127.0.0.1
  control_port: 7777
  strict: true
  seed: 428193
  clock:
    mode: logical
    start: "2026-01-01T00:00:00Z"
  generation_mode: materialized
  outbound_network: deny

config:
  model_dir: models
  scenario_dir: scenarios
  contract_dir: contracts
  fixture_dir: fixtures

state:
  driver: sqlite
  url_env: OMNIMOCK_STATE_URL
  sqlite_path: .omnimock/state.db
  snapshot_interval: 500

models:
  routing_file: config/model-routing.yaml

observability:
  log_format: json
  log_level: INFO
  payload_logging: metadata_only
  otlp_endpoint_env: OTEL_EXPORTER_OTLP_ENDPOINT

services:
  - id: orders-api
    kind: http
    plugin: builtin.http
    mode: native
    contract: contracts/orders.openapi.yaml
    state_namespace: commerce
    listen:
      port: 8081

  - id: order-events
    kind: stream
    plugin: builtin.stream.websocket
    mode: native
    contract: contracts/orders.asyncapi.yaml
    state_namespace: commerce
    listen:
      port: 8082

  - id: order-files
    kind: filesystem
    plugin: builtin.filesystem
    mode: native
    state_namespace: commerce
    root: .omnimock/mounts/orders

  - id: order-tools
    kind: mcp
    plugin: builtin.mcp
    mode: native
    contract: contracts/orders.mcp.yaml
    state_namespace: commerce
```

### 12.2 Configuration layering

Resolved configuration precedence, lowest to highest:

1. package defaults;
2. `omnimock.yaml`;
3. referenced profile files;
4. optional environment profile;
5. environment variables;
6. CLI `--set` overrides.

Rules:

- secrets MUST come from environment variables, mounted secret files, or a
  secret-manager adapter;
- configuration files MUST contain secret references, not secret values;
- unknown keys MUST fail validation in strict mode;
- the resolved configuration MUST be inspectable with secrets redacted;
- configuration provenance MUST identify the source of each value;
- environment variable names are stable public API;
- deployment-varying config belongs outside code, while versioned contracts,
  rules, schemas, and nonsecret model capability profiles remain in version
  control.

### 12.3 Schema evolution

Every declarative file has `schema_version` or `$schema`.

- Breaking changes require a new schema major version.
- The CLI provides `omnimock migrate-config`.
- Migrations are explicit and create backups.
- Runtime code MUST NOT guess how to interpret an unknown major version.
- Deprecations produce actionable diagnostics with a removal release.

---

## 13. Model configuration

### 13.1 Design requirement

Provider and model use is declared in files under `models/`, following the
one-profile-per-file pattern demonstrated by GomokuBench.

No application module may hard-code:

- a model identifier;
- a provider base URL;
- an API key;
- provider-specific request extras;
- model capability assumptions;
- timeouts, retry counts, or rate limits.

### 13.2 Directory layout

```text
models/
├── README.md
├── reasoning-pro.json
├── generation-fast.json
├── local-openai-compatible.json
└── disabled.json

schemas/
└── model-profile.schema.json

config/
└── model-routing.yaml
```

### 13.3 Model profile example

```json
{
  "$schema": "../schemas/model-profile.schema.json",
  "schema_version": "1",
  "id": "reasoning-pro",
  "provider": {
    "id": "openrouter",
    "adapter": "openai_compatible",
    "display_name": "OpenRouter",
    "options": {
      "base_url": "https://openrouter.ai/api/v1",
      "api_key_env": "OPENROUTER_API_KEY"
    }
  },
  "model": {
    "id": "provider/model-id",
    "display_name": "Reasoning Pro",
    "capabilities": {
      "structured_output": true,
      "tools": true,
      "streaming": true,
      "reasoning": true
    },
    "request_defaults": {
      "temperature": 0,
      "max_output_tokens": 8192
    }
  },
  "runtime": {
    "timeout_seconds": 120,
    "connect_timeout_seconds": 10,
    "max_retries": 2,
    "max_concurrency": 4,
    "rate_limit_rpm": 30,
    "circuit_breaker": {
      "failure_threshold": 5,
      "recovery_seconds": 60
    }
  },
  "privacy": {
    "allow_payload_classes": [
      "synthetic",
      "public",
      "redacted"
    ],
    "deny_payload_classes": [
      "secret",
      "credential",
      "production_personal_data"
    ]
  }
}
```

The model ID above is intentionally a deployer-supplied provider identifier.
Examples MUST not imply that a model is universally available.

### 13.4 Routing configuration

```yaml
schema_version: "1"

defaults:
  profile: reasoning-pro
  generation_mode: materialized

tasks:
  contract_inference:
    profile: reasoning-pro
    required_capabilities: [structured_output, reasoning]
    fallback: fail

  fixture_generation:
    profile: generation-fast
    required_capabilities: [structured_output]
    fallback: reasoning-pro

  live_unmatched_request:
    enabled: false
    profile: generation-fast
    fallback: protocol_error

  embeddings:
    profile: local-embeddings
    fallback: fail
```

Routing validates capability requirements before a run starts.

### 13.5 Profile loader

The loader MUST:

- validate JSON against `model-profile.schema.json`;
- reject duplicate profile IDs;
- resolve only declared environment references;
- redact secret values in diagnostics;
- normalize provider errors into stable domain errors;
- compute a profile digest excluding resolved secrets;
- expose declared capabilities to the router;
- support a `disabled` profile for model-free testing;
- never import a provider SDK unless that adapter is selected.

### 13.6 Model adapter port

```python
class ModelGateway(Protocol):
    async def generate_structured(
        self,
        *,
        task: ModelTask,
        schema: JsonSchema,
        messages: Sequence[ModelMessage],
        budget: ModelBudget,
        idempotency_key: str,
    ) -> ModelResult: ...
```

Provider-specific SDK types MUST remain inside adapter packages.

### 13.7 LLM task classes

Permitted task classes include:

- contract draft or completion;
- coherent fixture generation;
- state-machine proposal;
- schema-constrained response candidate;
- synthetic event sequence;
- data anonymization proposal;
- natural-language scenario explanation.

Model output is always a **candidate artifact**. It is not committed until it
passes:

1. transport decoding;
2. structured-output parsing;
3. JSON Schema or contract validation;
4. policy validation;
5. semantic invariant checks;
6. deterministic normalization;
7. optional human approval.

### 13.8 Prompt construction

Prompts MUST be assembled from typed sections:

- task identity;
- bounded contract excerpt;
- state summary;
- relevant examples;
- explicit constraints;
- output schema;
- security policy;
- provenance metadata.

Untrusted contract descriptions, recordings, file contents, and tool
descriptions MUST be delimited as data. They MUST NOT be concatenated as
unqualified system instructions.

### 13.9 Live model fallback

Live fallback is disabled by default.

When enabled:

- it MUST have an explicit latency deadline and token budget;
- it MUST use a deterministic cache key;
- validated output SHOULD be stored as a materialized artifact;
- timeouts MUST return a protocol-native configured fallback;
- retries are permitted only for transient, idempotent failures;
- the circuit breaker MUST prevent cascading model-provider failures;
- request payload classes forbidden by the profile privacy policy MUST not be
  sent;
- logs MUST record the profile ID and digest, not prompts or secrets;
- scenario exports MAY include generated results but MUST exclude credentials.

---

## 14. Generation, caching, and reproducibility

### 14.1 Generation modes

| Mode | Meaning |
|---|---|
| `off` | No model calls and no model-generated artifacts |
| `materialized` | Use only previously generated, validated artifacts |
| `build` | Model calls permitted only during explicit generation commands |
| `cache_miss` | Runtime may generate a missing artifact once, validate, and cache it |
| `live` | Runtime may call the model for each eligible request |

Default: `materialized` for serve, `build` for generate.

### 14.2 Cache key

The generation cache key includes:

- task type and task schema version;
- normalized input;
- contract digest;
- relevant state digest;
- prompt-template digest;
- model-profile digest;
- generation parameters;
- seed;
- OmniMock generator version.

Secret values and volatile trace identifiers are excluded.

### 14.3 Artifact manifest

Every generated artifact stores:

```yaml
artifact_version: "1"
artifact_id: response-orders-create-v3
created_at: "2026-07-21T12:00:00Z"
task: fixture_generation
scenario_revision: "sha256:..."
contract_digest: "sha256:..."
prompt_template_digest: "sha256:..."
model_profile_id: reasoning-pro
model_profile_digest: "sha256:..."
seed: 428193
validation:
  schema: schemas/order.schema.json
  status: passed
content_digest: "sha256:..."
```

The raw prompt and raw provider response are excluded by default and MAY be
retained only under an explicit secure-debug policy.

### 14.4 Deterministic primitives

All generators use injected ports for:

- clock;
- random number generation;
- UUID generation;
- sequence allocation;
- locale;
- timezone;
- hash algorithm.

Code MUST NOT call wall-clock, global random, or random UUID functions directly
inside domain or behavior logic.

---

## 15. State, journal, snapshots, and reset

### 15.1 State store port

```python
class StateStore(Protocol):
    async def read(self, namespace: str, key: StateKey) -> StateValue | None: ...
    async def query(self, namespace: str, query: StateQuery) -> Sequence[StateRow]: ...
    async def commit(self, plan: TransitionPlan) -> CommitResult: ...
    async def snapshot(self, run_id: str) -> SnapshotRef: ...
    async def restore(self, snapshot: SnapshotRef) -> None: ...
```

### 15.2 Default stores

- in-memory: unit tests and ephemeral runs;
- SQLite: default local persistence;
- PostgreSQL: multi-process or higher-concurrency deployments;
- plugin stores: optional.

SQLite runs MUST use a single-writer strategy and bounded lock waits.

### 15.3 Journal

The transition journal is append-only and records:

- sequence;
- run and request IDs;
- logical time;
- operation ID;
- canonical input digest;
- matched behavior source;
- planned mutation digest;
- committed state version;
- resulting event IDs;
- fault decision;
- provenance.

Sensitive values are redacted or hashed according to policy.

### 15.4 Reset and branching

```bash
omnimock snapshot create baseline
omnimock state reset --to baseline
omnimock run branch --from baseline --name timeout-experiment
```

Reset MUST be atomic from the perspective of the data plane. Active runs are
drained or rejected according to policy.

---

## 16. Fault and chaos model

### 16.1 Fault types

- fixed or distributed latency;
- timeout;
- connection reset;
- HTTP/gRPC/protocol error;
- throttling;
- malformed but serializable payload;
- schema violation, only when explicitly enabled;
- duplicate delivery;
- message loss;
- reordering;
- partial response;
- stale read;
- inconsistent pagination;
- disk-full or permission errors;
- broker partition or consumer rebalance;
- database lock or connection exhaustion.

### 16.2 Deterministic decisions

Probability-based faults use a stable hash of:

```text
scenario seed + run ID + service ID + operation ID + canonical request key
```

The decision and distribution sample are journaled. Replaying the same run
reproduces the fault.

### 16.3 Safety

Faults MUST be constrained to simulator resources. A chaos policy cannot alter
host files, arbitrary containers, unrelated processes, or external networks.

---

## 17. Contract import and validation

### 17.1 Supported standards

Initial targets:

- OpenAPI 3.1.x and 3.2.x;
- AsyncAPI 3.0.x and 3.1.x;
- JSON Schema 2020-12;
- GraphQL schemas;
- protobuf descriptor sets;
- MCP specification version 2025-11-25;
- SQL DDL and migration directories;
- CloudEvents-compatible event envelopes where useful.

A compiler normalizes source contracts into the internal contract model while
retaining source locations for diagnostics.

### 17.2 Diagnostics

Diagnostics have stable codes and source spans:

```text
OMC-CONTRACT-014 error
contracts/orders.yaml:143:9
Operation "createOrder" has no success response schema and no configured fixture.
Suggested fix: add a 2xx response schema or set behavior.allow_untyped_response=true.
```

Warnings do not silently change behavior. Strict CI mode MAY promote selected
warning classes to errors.

### 17.3 Round-trip preservation

Importers SHOULD preserve extensions and source references. Exporters MUST not
claim lossless round-tripping for unsupported constructs.

---

## 18. CLI

### 18.1 Command surface

```text
omnimock init
omnimock validate [PATH]
omnimock compile [SCENARIO]
omnimock generate [SCENARIO]
omnimock serve [SCENARIO]
omnimock run [SCENARIO]
omnimock record [SERVICE]
omnimock replay [RECORDING]
omnimock inspect config|contract|state|journal|routes|models
omnimock state get|query|put|reset
omnimock snapshot create|list|restore|delete
omnimock export scenario|fixtures|recording|contract
omnimock models list|validate|probe
omnimock plugins list|doctor
omnimock migrate-config
omnimock doctor
```

### 18.2 CLI behavior

- stdout is for command results;
- stderr is for diagnostics and logs;
- `--output json` provides machine-readable output;
- noninteractive commands never prompt unless `--interactive` is supplied;
- destructive commands require `--yes` in noninteractive mode;
- exit codes are documented and stable;
- secret values never appear in shell-completion output or diagnostic dumps.

### 18.3 Recommended exit codes

| Code | Meaning |
|---:|---|
| 0 | success |
| 2 | CLI usage error |
| 3 | configuration validation failure |
| 4 | contract validation failure |
| 5 | runtime startup failure |
| 6 | model/profile failure |
| 7 | plugin failure |
| 8 | recording or replay mismatch |
| 9 | security policy denial |
| 10 | partial export or migration |

---

## 19. Control API

An optional local control API MAY expose:

- health and readiness;
- scenario and service status;
- routes and contracts;
- state query and reset;
- snapshots;
- journal inspection;
- fault-policy updates;
- model and plugin diagnostics;
- metrics.

Defaults:

- disabled or loopback-only;
- separate port from simulated services;
- authenticated when bound beyond loopback;
- no raw secrets;
- no unrestricted file access;
- no arbitrary configuration mutation.

---

## 20. Repository and code layout

Use a `src` layout and explicit package boundaries.

```text
omnimock/
├── AGENTS.md
├── SPEC.md
├── README.md
├── SECURITY.md
├── CONTRIBUTING.md
├── LICENSE
├── pyproject.toml
├── uv.lock
├── .python-version
├── .editorconfig
├── .gitignore
├── .pre-commit-config.yaml
├── models/
│   ├── README.md
│   └── *.json
├── config/
│   ├── model-routing.yaml
│   ├── logging.yaml
│   └── redaction.yaml
├── contracts/
├── scenarios/
├── fixtures/
├── schemas/
│   ├── omnimock.schema.json
│   ├── scenario.schema.json
│   ├── model-profile.schema.json
│   └── plugin-manifest.schema.json
├── examples/
│   ├── commerce/
│   ├── filesystem/
│   ├── streaming/
│   └── mcp/
├── docs/
│   ├── architecture/
│   │   ├── decisions/
│   │   └── diagrams/
│   ├── protocols/
│   ├── operations/
│   └── development/
├── src/
│   └── omnimock/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli/
│       ├── application/
│       │   ├── commands/
│       │   ├── queries/
│       │   ├── services/
│       │   └── dto/
│       ├── domain/
│       │   ├── contracts/
│       │   ├── scenarios/
│       │   ├── behavior/
│       │   ├── state/
│       │   ├── streams/
│       │   ├── faults/
│       │   ├── models/
│       │   └── errors.py
│       ├── ports/
│       │   ├── model_gateway.py
│       │   ├── state_store.py
│       │   ├── artifact_store.py
│       │   ├── clock.py
│       │   ├── event_bus.py
│       │   ├── secret_provider.py
│       │   └── telemetry.py
│       ├── infrastructure/
│       │   ├── config/
│       │   ├── logging/
│       │   ├── telemetry/
│       │   ├── persistence/
│       │   ├── models/
│       │   ├── containers/
│       │   └── security/
│       ├── simulators/
│       │   ├── http/
│       │   ├── graphql/
│       │   ├── grpc/
│       │   ├── websocket/
│       │   ├── stream/
│       │   ├── filesystem/
│       │   ├── object_store/
│       │   ├── sql/
│       │   └── mcp/
│       └── plugins/
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── conformance/
│   ├── integration/
│   ├── e2e/
│   ├── property/
│   ├── golden/
│   └── fixtures/
└── tools/
    ├── release/
    ├── schemas/
    └── benchmarks/
```

### 20.1 Dependency rules

```text
domain          -> Python standard library only where practical
ports           -> domain
application     -> domain + ports
infrastructure  -> application + ports + domain
simulators      -> application + ports + domain
cli             -> application + infrastructure composition root
```

Additional rules:

- `domain` MUST NOT import Pydantic, FastAPI, SDK clients, SQLAlchemy, or plugin
  implementations.
- Pydantic or equivalent validation models belong at I/O boundaries.
- Framework objects MUST be translated into domain values at the adapter edge.
- Cross-simulator behavior occurs through application services or domain events,
  not direct imports.
- The composition root is the only place that wires concrete adapters.
- Avoid generic `utils.py`; use cohesive modules with domain names.
- Public package APIs are exported deliberately; internal modules are not
  re-exported casually.

### 20.2 Recommended design patterns

| Concern | Pattern |
|---|---|
| Protocol and provider integration | Ports and Adapters / Hexagonal Architecture |
| Simulator selection | Abstract Factory plus plugin registry |
| Behavior selection | Chain of Responsibility |
| Response or event generation | Strategy |
| Request lifecycle | Pipeline / middleware |
| State change | Command plus transactional Unit of Work |
| State projections | Observer / domain events |
| Fault policies | Decorator around behavior result |
| Model routing | Strategy plus capability-based policy |
| Provider resilience | Circuit Breaker, timeout, bounded retry |
| Reusable simulator lifecycle | Template Method where it reduces duplication |
| Reproducibility | Event journal plus snapshot |
| Complex scenario evolution | Explicit state machine |
| Configuration | Layered immutable configuration object |

Patterns are tools, not goals. Implementations SHOULD prefer simple functions
and immutable values when a pattern adds no clarity.

---

## 21. Configuration and Twelve-Factor alignment

| Factor | OmniMock practice |
|---|---|
| Codebase | One version-controlled codebase; plugins may be separate packages with declared compatibility |
| Dependencies | All dependencies declared in `pyproject.toml`; optional protocol extras isolated |
| Config | Deploy-varying values in environment/secret providers; versioned nonsecret contracts and profiles in files |
| Backing services | Databases, brokers, model providers, and object stores are replaceable attached resources |
| Build, release, run | Build wheel/container once; combine with immutable config for release; run without modifying source |
| Processes | Runtime processes are stateless except through configured stores and sandbox roots |
| Port binding | Native services expose explicit ports; no hidden web-server dependency |
| Concurrency | Scale by service/process role; state coordination uses the selected store |
| Disposability | Fast startup, bounded drain, signal handling, and idempotent shutdown |
| Dev/prod parity | Same artifacts and adapters across local, CI, and deployed environments where practical |
| Logs | Structured event streams to stdout/stderr; routing and storage handled externally |
| Admin processes | Migration, generation, snapshot, and repair operations are one-off CLI commands using the same release |

A model profile file is treated as a versioned capability declaration, not as a
place to store deploy secrets.

---

## 22. Logging

### 22.1 Principles

- application logs are structured JSON by default;
- logs are emitted to stdout/stderr;
- log storage, rotation, and routing are deployment concerns;
- event names are stable and machine-queryable;
- traces, metrics, and logs share correlation identifiers;
- payloads are excluded by default;
- secrets and credentials are always redacted;
- logging failure MUST NOT crash a simulator request path.

### 22.2 Required fields

```json
{
  "timestamp": "2026-07-21T12:00:00.123456Z",
  "severity": "INFO",
  "event": "simulation.request.completed",
  "service": {
    "name": "omnimock",
    "version": "0.1.0"
  },
  "run_id": "run_...",
  "request_id": "req_...",
  "trace_id": "...",
  "span_id": "...",
  "scenario_id": "checkout",
  "scenario_revision": "sha256:...",
  "simulator_kind": "http",
  "service_id": "orders-api",
  "operation_id": "orders.create",
  "outcome": "success",
  "provenance": "rule",
  "state_version": 42,
  "duration_ms": 8.4
}
```

### 22.3 Event naming

Use dotted lowercase names:

```text
runtime.started
runtime.stopping
config.loaded
config.validation_failed
contract.compiled
simulation.request.received
simulation.request.completed
simulation.request.failed
state.transition.committed
stream.message.published
model.request.started
model.request.completed
model.request.failed
security.access_denied
plugin.health_failed
```

### 22.4 Log levels

- `DEBUG`: local diagnostic details, still redacted;
- `INFO`: lifecycle and completed operations;
- `WARNING`: recoverable degradation or deprecated configuration;
- `ERROR`: failed operation requiring attention;
- `CRITICAL`: runtime cannot safely continue.

Expected client validation failures are normally `INFO` or `WARNING`, not
`ERROR`.

### 22.5 Payload policy

`payload_logging` values:

- `none`;
- `metadata_only` — default;
- `redacted`;
- `full_synthetic_only`.

Full payload logging is forbidden for secrets, credentials, or production
personal data regardless of the selected value.

---

## 23. Metrics and tracing

### 23.1 Metrics

Recommended names:

```text
omnimock_requests_total
omnimock_request_duration_seconds
omnimock_active_connections
omnimock_stream_queue_depth
omnimock_stream_messages_total
omnimock_state_commits_total
omnimock_state_conflicts_total
omnimock_model_requests_total
omnimock_model_duration_seconds
omnimock_model_tokens_total
omnimock_model_cache_hits_total
omnimock_faults_injected_total
omnimock_validation_failures_total
```

Metric labels MUST have bounded cardinality. Request IDs, file paths, raw routes,
user IDs, object keys, and prompts are forbidden metric labels.

### 23.2 Tracing

A request span includes child spans for:

- validation;
- behavior matching;
- state read;
- model generation;
- state commit;
- fault delay;
- protocol serialization;
- event publication.

Trace context SHOULD propagate through simulated HTTP, gRPC, and event metadata
when the contract permits it.

### 23.3 Health

- **liveness:** process event loop is functioning;
- **readiness:** required configuration, contracts, stores, and bound endpoints
  are ready;
- **startup:** optional endpoint while managed services are provisioning;
- **plugin health:** detailed diagnostic, excluded from public data-plane ports
  by default.

---

## 24. Error model

### 24.1 Domain errors

Use a stable hierarchy:

```text
OmniMockError
├── ConfigurationError
├── ContractError
├── ValidationError
├── BehaviorResolutionError
├── StateConflictError
├── ModelGatewayError
├── PluginError
├── SecurityPolicyError
├── RecordingError
└── RuntimeLifecycleError
```

Errors carry:

- stable code;
- safe public message;
- operator detail;
- retry classification;
- source location when available;
- causal exception;
- redaction classification.

### 24.2 Protocol mapping

Plugins map domain errors to native forms:

- HTTP: status plus RFC 9457 problem details;
- GraphQL: errors with safe extensions;
- gRPC: status and structured details;
- MCP: JSON-RPC/MCP error;
- streams: dead-letter or delivery failure policy;
- filesystem: native-style error code;
- SQL: managed database error or simulator control error.

Stack traces MUST NOT be returned to simulated clients by default.

---

## 25. Security

### 25.1 Default posture

- loopback binding;
- deny outbound network;
- no control API exposure;
- no shell or arbitrary code execution;
- no host filesystem access outside allowlisted roots;
- no secret values in configuration files;
- no raw payload logs;
- model calls disabled on serve unless explicitly enabled;
- managed containers use isolated networks and least privilege;
- generated files are nonexecutable;
- strict contract and schema validation.

### 25.2 Threats and controls

| Threat | Control |
|---|---|
| Path traversal or symlink escape | canonicalize paths, sandbox root, reject escapes |
| SSRF through proxy/webhook/model URL | explicit destination allowlists and private-range policy |
| Prompt injection in contracts or recordings | delimit untrusted data, typed prompts, no generated code execution |
| Secret leakage to logs or models | classification, redaction, env indirection, payload policy |
| Malicious plugin | signed/trusted plugin policy, declared capabilities, optional subprocess isolation |
| Resource exhaustion | body limits, queue bounds, timeouts, concurrency limits, quotas |
| Unsafe generated SQL | disposable target only, parse and allowlist, no arbitrary connections |
| Control-plane takeover | separate bind, authentication, authorization, disabled by default |
| Replay of write requests | idempotency records and optional nonce policy |
| Cross-scenario contamination | namespace isolation and run-scoped state |
| MCP tool abuse | explicit authorization and side-effect declarations |

### 25.3 Data classification

Minimum classes:

- `synthetic`;
- `public`;
- `internal`;
- `personal`;
- `sensitive`;
- `credential`;
- `secret`.

Policies govern logging, recording retention, model exposure, export, and
proxying for each class.

### 25.4 Templates and expressions

The template engine MUST:

- expose only allowlisted pure functions;
- enforce evaluation time and output-size limits;
- have no import, attribute traversal, file, network, process, or environment
  access;
- escape for the target context where appropriate;
- reject unknown functions and variables;
- support deterministic clock, sequence, fake-data, and hash helpers.

---

## 26. Concurrency, backpressure, and lifecycle

### 26.1 Async runtime

Network-facing adapters SHOULD use structured asynchronous concurrency through
AnyIO-compatible primitives.

Rules:

- no untracked background tasks;
- every task belongs to a lifecycle task group;
- blocking work runs in bounded worker capacity;
- cancellation propagates;
- timeouts are explicit;
- queues are bounded;
- producers have drop, block, or dead-letter policy;
- shared mutable globals are forbidden.

### 26.2 Startup

1. parse CLI;
2. load and validate configuration;
3. discover and validate plugins;
4. compile contracts;
5. resolve model profiles without exposing secrets;
6. connect state and artifact stores;
7. prepare managed services;
8. bind native endpoints;
9. mark ready;
10. emit `runtime.started`.

A startup failure tears down already-prepared resources in reverse order.

### 26.3 Shutdown

On `SIGTERM` or `SIGINT`:

1. mark not ready;
2. stop accepting new control operations;
3. stop or pause new data-plane work;
4. drain active requests up to a configured deadline;
5. flush journals and telemetry;
6. stop plugins in reverse dependency order;
7. close stores;
8. exit with a meaningful code.

Shutdown is idempotent.

---

## 27. Dependency and packaging policy

### 27.1 Packaging

- PEP 621 metadata in `pyproject.toml`;
- `src` package layout;
- a locked development environment;
- reproducible wheel and OCI image builds;
- no undeclared runtime dependency;
- optional protocol dependencies behind extras.

Example extras:

```toml
[project.optional-dependencies]
http = ["fastapi", "uvicorn"]
graphql = ["graphql-core"]
grpc = ["grpcio", "protobuf"]
mcp = ["mcp"]
postgres = ["asyncpg"]
containers = ["testcontainers"]
otel = ["opentelemetry-sdk", "opentelemetry-exporter-otlp"]
dev = ["pytest", "pytest-cov", "hypothesis", "ruff", "pyright"]
```

Exact versions belong in the lock file and compatibility policy, not this
architecture document.

### 27.2 Dependency review

A new dependency requires:

- documented purpose;
- license compatibility;
- maintenance and security assessment;
- comparison with standard-library or existing dependency options;
- size and transitive-dependency impact;
- optional extra when not needed by the core;
- tests covering the integration boundary.

---

## 28. Testing strategy

### 28.1 Test layers

1. **Unit tests**  
   Pure domain behavior, no network, real time, global random, containers, or
   model providers.

2. **Contract tests**  
   Configuration and imported contract validation.

3. **Plugin conformance tests**  
   A shared suite verifies lifecycle, envelopes, errors, limits, telemetry, and
   determinism for every simulator.

4. **Integration tests**  
   Concrete adapters with temporary stores and local endpoints.

5. **Managed-service tests**  
   Disposable containers for PostgreSQL, Kafka, Redis, or object storage.

6. **End-to-end tests**  
   CLI through client-visible protocol behavior.

7. **Property tests**  
   Canonicalization, matching, state transitions, path safety, serialization,
   and replay invariants.

8. **Golden tests**  
   Stable compiled contracts, generated artifacts, and structured diagnostics.

9. **Fuzz tests**  
   Parsers, importers, protocol boundaries, and template inputs.

10. **Performance tests**  
    Deterministic hot path, stream backpressure, startup, and snapshot restore.

### 28.2 Determinism test

Every behavior engine test SHOULD run twice with the same seed and assert equal:

- response payload and metadata, excluding declared volatile fields;
- state journal;
- event sequence;
- fault decisions;
- generated IDs;
- logical timestamps.

A companion test SHOULD change the seed and assert that only declared synthetic
fields may differ.

### 28.3 Model tests

- unit tests use a scripted `FakeModelGateway`;
- provider adapters use opt-in integration tests;
- CI does not require paid model access;
- golden model artifacts are schema-valid and record profile digests;
- tests never assert on prose style when structured semantics are sufficient;
- a live provider test cannot merge unless it also has a deterministic fake
  equivalent.

### 28.4 Coverage expectations

- domain and behavior core: at least 90% branch coverage;
- security-sensitive path, configuration resolution, and state commit: 100% of
  specified branches;
- simulator plugins: at least 80% branch coverage plus conformance suite;
- coverage is a guardrail, not a substitute for meaningful assertions.

---

## 29. Code quality

Canonical checks:

```bash
uv sync --all-extras --dev
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
uv run python -m build
```

Recommended additional checks:

- import-boundary enforcement;
- JSON/YAML schema validation;
- dependency vulnerability scan;
- secret scan;
- package metadata and license check;
- container scan;
- documentation link check;
- deterministic golden-file verification.

Type checking is strict in core packages. Public functions and plugin ports
require complete type annotations.

---

## 30. CI/CD and release

### 30.1 Pull-request pipeline

1. formatting and lint;
2. type checking;
3. unit and property tests;
4. schema validation;
5. plugin conformance tests;
6. integration tests;
7. build wheel/sdist;
8. build OCI image;
9. security and secret scans;
10. example scenario smoke tests.

Managed-service and cross-platform matrices MAY run in parallel or on protected
branches.

### 30.2 Release policy

- Semantic Versioning for the Python package;
- independent, explicit scenario-schema versioning;
- plugin API compatibility range;
- signed release artifacts where supported;
- generated software bill of materials;
- changelog with migration notes;
- no mutable container tags as the only release identifier.

### 30.3 Compatibility

A release must state compatibility for:

- Python versions;
- scenario schema versions;
- model-profile schema versions;
- plugin API versions;
- imported contract versions;
- managed-service images used in test matrices.

---

## 31. Performance and resource targets

Targets are measured with a published benchmark profile, excluding configured
synthetic latency and external provider time.

### 31.1 MVP targets

- core-only cold start to readiness: p95 under 2 seconds on the reference CI
  runner;
- deterministic in-memory HTTP operation overhead: p95 under 10 ms;
- SQLite deterministic operation overhead: p95 under 25 ms;
- graceful shutdown with no active requests: under 2 seconds;
- bounded memory under sustained stream load;
- zero unbounded queues;
- no model call on a fully materialized deterministic path;
- snapshot restore time grows approximately linearly with snapshot size.

Hardware, dataset, concurrency, and benchmark commands MUST be published with
results. These are engineering targets, not universal guarantees.

---

## 32. Operational profiles

### 32.1 Local development

- loopback ports;
- SQLite;
- materialized artifacts;
- human-readable logs allowed on TTY, JSON available;
- local sandbox directories;
- optional local model provider.

### 32.2 CI

- fixed seed;
- logical clock;
- no outbound network;
- in-memory or temporary SQLite;
- JSON logs;
- deterministic fake model;
- generated artifacts checked for drift;
- ephemeral ports.

### 32.3 Shared test environment

- authenticated control API;
- PostgreSQL state store;
- OTLP telemetry;
- quotas per scenario;
- separate namespaces;
- immutable release plus environment configuration;
- model access through approved profiles only.

---

## 33. Roadmap

### Milestone 0 — foundation

- repository skeleton;
- configuration schemas and resolver;
- domain envelopes;
- plugin API;
- state store ports with memory and SQLite;
- journal, logical clock, deterministic random;
- model profile loader and fake gateway;
- structured logging and health;
- `init`, `validate`, `serve`, and `doctor`.

### Milestone 1 — useful vertical slice

- OpenAPI import and HTTP simulator;
- SSE/WebSocket stream projection;
- filesystem sandbox;
- MCP server;
- shared state across all four;
- fixtures, rules, snapshots, and fault injection;
- build-time LLM artifact generation;
- complete commerce example.

### Milestone 2 — replay and richer contracts

- proxy, recording, redaction, and replay;
- GraphQL;
- AsyncAPI compiler;
- broker-neutral stream core;
- scheduled producers and webhooks;
- OpenTelemetry export.

### Milestone 3 — managed services

- PostgreSQL;
- Kafka-compatible broker;
- Redis-compatible store;
- S3-compatible object storage;
- container orchestration adapter;
- managed-service conformance tests.

### Milestone 4 — ecosystem

- gRPC;
- plugin SDK and template repository;
- scenario registry format;
- optional web UI;
- distributed runtime;
- policy packs;
- additional protocol plugins.

---

## 34. MVP acceptance criteria

The MVP is complete when all of the following are true:

1. A fresh clone can install and run with documented commands.
2. `omnimock validate` rejects unknown or invalid configuration with source
   locations.
3. Model profiles are loaded from files; no provider or model is hard-coded.
4. A complete example exposes HTTP, WebSocket/SSE, filesystem, and MCP from one
   shared scenario state.
5. The example works with model access disabled after artifacts are generated.
6. All generated responses pass their declared schemas.
7. The same seed and request sequence produce identical journal and outputs.
8. A snapshot can be created, state mutated, and state restored.
9. Fault injection is deterministic and journaled.
10. Logs are structured, correlated, and free of configured secrets.
11. The filesystem simulator cannot escape its sandbox.
12. The runtime starts and stops cleanly under signals.
13. Every built-in plugin passes the common conformance suite.
14. Unit tests require no paid service and no outbound network.
15. Documentation covers configuration, security, plugin development, and
    troubleshooting.

---

## 35. Architecture decisions to record

The implementation SHOULD create ADRs for at least:

- ADR-0001: hexagonal architecture and package boundaries;
- ADR-0002: deterministic-first behavior precedence;
- ADR-0003: immutable scenario definitions plus mutable run state;
- ADR-0004: append-only transition journal and snapshots;
- ADR-0005: one-file-per-model profile and environment secret indirection;
- ADR-0006: expression language and sandbox;
- ADR-0007: native versus managed protocol simulation;
- ADR-0008: plugin discovery and compatibility;
- ADR-0009: structured logging and OpenTelemetry correlation;
- ADR-0010: configuration layering and schema migration;
- ADR-0011: proxy, recording, and redaction policy;
- ADR-0012: data classification and LLM privacy boundary.

---

## 36. Example end-to-end scenario

```yaml
schema_version: "1"
id: fulfillment-demo
version: "1.0.0"

seed: 84172

initial_state:
  collections:
    orders: {}
    inventory:
      SKU-RED:
        available: 100

rules:
  - id: create-order
    priority: 100
    when:
      operation: orders.create
    validate:
      - expression: input.quantity >= 1
      - expression: state.inventory[input.sku].available >= input.quantity
    mutate:
      - decrement:
          path: "inventory.${input.sku}.available"
          by: "${input.quantity}"
      - put:
          collection: orders
          key: "${uuid.deterministic('order', request.request_id)}"
          value:
            id: "${mutation.key}"
            sku: "${input.sku}"
            quantity: "${input.quantity}"
            status: accepted
            created_at: "${clock.now}"
    emit:
      - channel: order-events
        type: order.created
        payload: "${mutation.value}"
      - filesystem:
          path: "orders/${mutation.key}.json"
          content: "${json.encode(mutation.value)}"
      - mcp_resource:
          uri: "order://${mutation.key}"
          value: "${mutation.value}"
    respond:
      status: 201
      body: "${mutation.value}"

faults:
  profiles:
    degraded:
      rules:
        - operation: orders.create
          latency:
            distribution: lognormal
            p50_ms: 120
            p95_ms: 700
          timeout_probability: 0.02
```

This scenario illustrates one committed transition projected to multiple
interfaces.

---

## 37. Glossary

**Artifact**  
A validated, versioned output produced during compilation or model-assisted
generation.

**Behavior**  
The policy that resolves a canonical request into a response, events, and state
transition.

**Contract compiler**  
A component that converts an external contract format into OmniMock's internal
contract model.

**Logical clock**  
A controlled source of scenario time that can advance independently of wall
time.

**Managed service**  
A real disposable backing service provisioned and configured by a plugin.

**Materialized generation**  
Model-generated data stored and validated before the data plane serves it.

**Projection**  
A protocol-specific view of shared scenario state.

**Run**  
A concrete execution of a scenario with a resolved seed, clock, configuration,
and state namespace.

**Simulator plugin**  
An adapter that exposes or manages a protocol/data-service interface.

---

## 38. References

- The Twelve-Factor App: https://12factor.net/
- GomokuBench model profiles: https://github.com/homerquan/GomokuBench/tree/main/models
- OpenAPI Specification 3.2.0: https://spec.openapis.org/oas/latest.html
- AsyncAPI Specification 3.1.0: https://www.asyncapi.com/docs/reference/specification/latest
- Model Context Protocol specification: https://modelcontextprotocol.io/specification/latest
- JSON Schema 2020-12: https://json-schema.org/draft/2020-12
- RFC 9110, HTTP Semantics: https://www.rfc-editor.org/rfc/rfc9110.html
- RFC 9457, Problem Details for HTTP APIs: https://www.rfc-editor.org/rfc/rfc9457.html
- OpenTelemetry logs specification: https://opentelemetry.io/docs/specs/otel/logs/
- Python Packaging User Guide, `src` layout: https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/
- W3C Trace Context: https://www.w3.org/TR/trace-context/
- CloudEvents specification: https://cloudevents.io/
