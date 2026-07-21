# AGENTS.md — OmniMock Engineering Instructions

This file defines the working agreement for coding agents and human
contributors in the OmniMock repository.

Read `SPEC.md` before making architectural or user-visible changes.

---

## 1. Mission

Build OmniMock as a secure, deterministic, modular, contract-first Python
platform for simulating APIs, streams, filesystems, data stores, webhooks, and
MCP services.

The implementation must remain useful without live LLM access. Model calls are
optional authoring or fallback operations whose outputs are validated and can
be materialized.

---

## 2. Instruction priority

When instructions conflict, use this order:

1. the current user or issue request;
2. security and safety requirements;
3. accepted architecture decision records;
4. `SPEC.md`;
5. this file;
6. existing local conventions.

Do not silently resolve a material conflict. State the conflict in the change
summary and choose the narrowest safe interpretation.

---

## 3. Non-negotiable invariants

1. **Deterministic first**  
   Rules, state machines, fixtures, recordings, and deterministic generators
   precede live model generation.

2. **No hard-coded models**  
   Provider IDs, model IDs, endpoints, capability assumptions, timeouts, rate
   limits, and request extras belong in validated files under `models/` and
   routing config under `config/`.

3. **No secrets in source or config files**  
   Store only environment-variable or secret-provider references.

4. **Contract validation at boundaries**  
   Validate inputs and outputs whenever a contract exists.

5. **No arbitrary generated code execution**  
   Never execute model-generated Python, shell, templates with ambient access,
   or unrestricted SQL.

6. **Sandbox host access**  
   Filesystem and network access are denied unless explicitly allowed.

7. **Bounded concurrency**  
   No unbounded queues, background tasks, retries, output size, prompt size, or
   request body size.

8. **Framework-independent domain**  
   Domain packages do not import web frameworks, provider SDKs, Pydantic,
   databases, or simulator implementations.

9. **Structured observability**  
   Logs are structured and correlated. Secrets and payloads are excluded by
   default.

10. **Tests do not require paid services**  
    Unit and default CI tests use fakes, temporary resources, and deterministic
    fixtures.

---

## 4. Repository map

Expected top-level structure:

```text
src/omnimock/
  cli/              command parsing and presentation
  application/      use cases, commands, queries, orchestration
  domain/           pure domain values, rules, invariants, errors
  ports/            interfaces required by application/domain
  infrastructure/   concrete config, persistence, models, telemetry, security
  simulators/       protocol and data-service plugins
  plugins/          discovery, manifests, compatibility

models/             one validated nonsecret profile per model/provider use
config/             routing, logging, redaction, environment-neutral defaults
schemas/            JSON Schemas for all declarative formats
contracts/          imported or authored protocol contracts
scenarios/          scenario definitions
fixtures/           deterministic fixture data
examples/           runnable examples
tests/              unit, contract, conformance, integration, e2e, property
docs/               architecture, operations, protocol guides, ADRs
```

Do not create broad dumping-ground modules such as `utils.py`, `helpers.py`, or
`common.py`. Name modules after a cohesive responsibility.

---

## 5. Architecture boundaries

Allowed dependency direction:

```text
domain          -> standard library where practical
ports           -> domain
application     -> domain + ports
infrastructure  -> application + ports + domain
simulators      -> application + ports + domain
cli             -> application + infrastructure composition
```

### 5.1 Domain rules

The domain layer:

- contains immutable values, invariants, transition planning, matching,
  determinism, and stable errors;
- accepts injected clocks, random sources, ID generators, and stores through
  ports;
- never reads environment variables;
- never configures logging;
- never opens files, sockets, or database connections;
- never imports concrete model or protocol SDKs;
- never returns framework request/response types.

Prefer frozen dataclasses, enums, typed aliases, and small pure functions.
Raise domain-specific errors rather than leaking adapter exceptions.

### 5.2 Application rules

Application services:

- orchestrate domain behavior and ports;
- define transaction and lifecycle boundaries;
- are independent of CLI and HTTP presentation;
- accept typed command/query objects;
- return typed results;
- do not parse YAML/JSON or environment variables directly.

### 5.3 Adapter rules

Infrastructure and simulator adapters:

- translate external values at the boundary;
- catch vendor/framework exceptions and map them to stable errors;
- implement timeouts, limits, cancellation, and telemetry;
- do not duplicate domain rules;
- do not import sibling simulator implementations;
- expose only the capabilities declared in their plugin manifest.

### 5.4 Composition root

Concrete wiring belongs in a small composition root invoked by the CLI or
runtime entry point. Do not use a global service locator.

Dependency injection may be explicit constructor injection. A DI framework is
not required.

---

## 6. Standard development workflow

Before editing:

1. read the relevant section of `SPEC.md`;
2. inspect nearby implementation and tests;
3. identify the package boundary and public contract;
4. search for an existing abstraction before adding one;
5. note any schema, migration, security, or compatibility impact.

During implementation:

1. make the smallest coherent change;
2. add or update tests with the code;
3. preserve deterministic behavior;
4. keep adapter details out of the core;
5. update schemas and examples together;
6. add an ADR when changing a cross-cutting architectural decision.

Before reporting completion:

```bash
uv sync --all-extras --dev
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
uv run python -m build
```

Run narrower commands during iteration, then the full applicable suite before
completion. Report commands actually run; do not claim unrun checks passed.

---

## 7. Python standards

### 7.1 Supported Python

Target Python 3.12 or newer unless the project metadata says otherwise. Do not
use a newer-language feature without verifying the supported matrix.

### 7.2 Formatting and lint

- Ruff is the canonical formatter and linter.
- Do not hand-format around the formatter.
- Prefer clear code over suppressions.
- A suppression requires the narrowest code and an explanatory comment when the
  reason is not obvious.
- Do not disable a rule repository-wide to accommodate one local design.

### 7.3 Typing

- Core and public plugin APIs use strict typing.
- Avoid `Any`. When unavoidable at an untyped library boundary, isolate it,
  validate immediately, and convert to a typed value.
- Use `Protocol` for ports and structural plugin contracts.
- Use `Mapping`/`Sequence` for read-only inputs and concrete mutable types only
  when mutation is required.
- Use `TypeAlias` and typed IDs when they prevent accidental mixing.
- Do not return ambiguous tuples from public functions; use named result types.
- Exhaustively handle enums and tagged unions.

### 7.4 Data models

Use:

- frozen dataclasses for internal domain values;
- Pydantic or equivalent only for external configuration and I/O validation;
- explicit conversion functions between boundary models and domain models.

Do not pass Pydantic models throughout the domain merely for convenience.

### 7.5 Functions and classes

- Prefer small pure functions.
- Introduce a class when it owns lifecycle, state, a protocol, or a coherent
  strategy.
- Avoid inheritance for code reuse. Prefer composition.
- Use abstract base classes only when nominal inheritance is a real contract;
  otherwise use protocols.
- Keep constructors side-effect-free.
- Do not perform network, file, or model work at import time.

### 7.6 Exceptions

- Raise stable OmniMock error types.
- Preserve the original exception with `raise ... from exc`.
- Include safe operator context, not secrets or full payloads.
- Do not catch `Exception` unless at a process, task, or adapter boundary where
  it is mapped, logged, and handled intentionally.
- Never use exceptions as normal rule-matching control flow.

---

## 8. Async and concurrency

- Use structured concurrency and owned task groups.
- Every spawned task must have a parent lifecycle.
- Every queue must have a maximum size.
- Every wait on external work must have a timeout or caller-owned cancellation.
- Retries must be bounded and apply only to classified transient failures.
- Do not retry non-idempotent operations unless an idempotency mechanism makes
  the retry safe.
- Move blocking I/O or CPU-heavy work to bounded worker capacity.
- Do not call `asyncio.create_task()` without registering and supervising the
  task.
- Do not use global event loops or install an event-loop policy from a library
  module.
- Ensure shutdown drains or cancels tasks predictably.

Tests must verify backpressure and cancellation for streaming adapters.

---

## 9. Determinism rules

Domain and behavior code must not directly use:

- `datetime.now()` or `time.time()`;
- module-level `random`;
- nondeterministic UUID generation;
- unordered iteration where order affects output;
- process-dependent hash values;
- ambient locale or timezone;
- uncontrolled filesystem timestamps.

Use injected ports for clock, randomness, IDs, sequence allocation, locale, and
timezone.

Every randomness decision that affects client-visible behavior or faults must be
reproducible from a run seed and journaled when appropriate.

When adding a new generator:

1. define its deterministic inputs;
2. specify which fields may vary with seed;
3. add same-seed equality tests;
4. add different-seed scope tests;
5. avoid provider-generated values in hot paths unless materialized.

---

## 10. Configuration rules

### 10.1 Sources and precedence

Keep the documented precedence:

1. package defaults;
2. root project config;
3. referenced profiles;
4. environment profile;
5. environment variables;
6. CLI overrides.

Do not invent a hidden configuration source.

### 10.2 Validation

- Every config format has a version and schema.
- Unknown fields fail in strict mode.
- Validation errors include source path and location.
- Parsing and validation happen before starting network listeners or managed
  services.
- Resolved config is immutable.
- `inspect config` must show provenance and redact secrets.
- Add schema tests for valid and invalid examples.

### 10.3 Secret handling

Configuration files may contain fields like:

```json
{
  "api_key_env": "OPENROUTER_API_KEY"
}
```

They may not contain the secret itself.

Never:

- print resolved secret values;
- include them in exceptions;
- include them in cache keys or digests;
- persist them in artifacts;
- forward them to an unrelated provider;
- read arbitrary environment variables from templates.

### 10.4 Schema changes

A config change is incomplete until it includes:

- schema update;
- typed boundary model update;
- migration or explicit compatibility decision;
- valid example;
- invalid test;
- documentation;
- changelog note when user-visible.

Breaking schema changes require a new major schema version.

---

## 11. Model and LLM rules

### 11.1 Profiles

All provider/model use is declared in `models/*.json`.

A profile contains:

- profile ID and schema version;
- provider adapter;
- base URL or endpoint reference;
- API-key environment variable name;
- provider model ID;
- declared capabilities;
- request defaults;
- timeout, retry, rate, and concurrency limits;
- privacy policy.

Do not place task routing in Python conditionals. Put routing in
`config/model-routing.yaml` and validate it at startup.

### 11.2 Provider adapters

A provider adapter:

- implements `ModelGateway`;
- converts stable domain requests to SDK requests;
- maps SDK errors to stable error classes;
- applies explicit connect and total timeouts;
- emits metrics and spans;
- does not log prompts or full outputs;
- supports cancellation;
- declares optional dependencies;
- is not imported unless selected.

Provider-specific request fields may pass through only via a namespaced,
schema-validated `provider_options` object. Core code must not inspect them.

### 11.3 Structured output

Every model task must provide an output schema.

The adapter result is a candidate. Before use, it must pass:

1. decoding;
2. structured parse;
3. schema validation;
4. policy validation;
5. semantic invariant checks;
6. deterministic normalization;
7. size limits.

Do not "repair" arbitrary invalid output with unsafe eval or regex-based JSON
guessing. A bounded, explicit parser retry may ask the provider to return valid
structured output when policy permits.

### 11.4 Prompt safety

- Treat contracts, descriptions, recordings, MCP tool text, files, and upstream
  responses as untrusted data.
- Delimit untrusted data in typed sections.
- Do not let imported text redefine system policy.
- Minimize context to the fields required for the task.
- Apply data classification and redaction before building the prompt.
- Do not send credentials, secrets, or production personal data.
- Do not request or execute generated source code as part of serving a mock.

### 11.5 Live calls

Live calls on the request path are disabled by default.

A live-capable change must include:

- explicit config gate;
- timeout behavior;
- cache/idempotency behavior;
- protocol-native fallback;
- circuit-breaker behavior;
- cost/token budget;
- privacy classification;
- deterministic fake tests;
- operator telemetry.

### 11.6 Tests

Default tests use a scripted fake gateway. Never make the normal test suite
depend on API keys, internet access, quota, provider uptime, or nondeterministic
model prose.

---

## 12. Adding a simulator plugin

A new simulator must provide:

1. plugin manifest;
2. service configuration schema;
3. contract compiler or explicit no-contract design;
4. runtime lifecycle implementation;
5. canonical envelope mapping;
6. protocol-native error mapping;
7. limits and backpressure policy;
8. authentication/authorization integration;
9. fault integration;
10. provenance and telemetry;
11. health diagnostics;
12. shared conformance tests;
13. protocol-specific tests;
14. runnable example;
15. documentation;
16. optional-dependency extra when appropriate.

### 12.1 Native protocol checklist

For an in-process protocol:

- bind only after all validation succeeds;
- support ephemeral ports in tests;
- set body/message/frame limits;
- reject malformed input safely;
- translate to canonical envelopes immediately;
- never pass framework request objects into application/domain code;
- support graceful drain;
- avoid leaking stack traces;
- expose readiness separately from liveness.

### 12.2 Managed-service checklist

For a real disposable backing service:

- pin the tested image by version or digest;
- isolate its network;
- use generated credentials;
- wait on a real readiness probe;
- make provisioning idempotent;
- clean up on partial startup failure;
- expose connection information through typed results;
- avoid privileged containers;
- make reuse opt-in;
- document host requirements;
- provide a fake or skipped path for environments without containers.

### 12.3 Cross-interface state

A simulator may not update another simulator directly. It commits shared state
and emits domain events; other projections observe the committed transition.

---

## 13. Behavior engine changes

When changing matching, transitions, or precedence:

- preserve the documented resolution order;
- reject ambiguous equal-priority rules;
- keep planning separate from commit;
- validate output before state mutation;
- ensure idempotency behavior is explicit;
- add journal/provenance fields as needed;
- write property tests for canonicalization and matching;
- write conflict tests for concurrent commits;
- verify failed validation leaves state unchanged.

Do not introduce hidden fallback behavior. An unmatched operation must have a
configured outcome or a protocol-appropriate error.

---

## 14. Filesystem safety

All filesystem code must use the sandbox abstraction.

Required checks:

- normalize paths;
- reject absolute paths unless a host-root policy explicitly allows them;
- reject `..` escape after normalization;
- reject symlink escape;
- avoid time-of-check/time-of-use races where practical;
- reject devices, sockets, and special files by default;
- set generated files nonexecutable;
- impose file count, file size, and total capacity limits;
- use atomic write/rename patterns;
- never follow a model-supplied host path without policy validation.

Every path-safety change requires adversarial tests.

---

## 15. Network and proxy safety

Outbound network is denied by default.

A feature that opens outbound connections must:

- use the outbound-policy port;
- validate scheme, host, port, resolved address, and redirects;
- prevent DNS rebinding where relevant;
- block link-local, loopback, metadata, and private ranges unless explicitly
  allowed;
- apply connect, read, write, and total timeouts;
- cap response size;
- redact authorization data;
- document whether credentials may be forwarded;
- test redirects and blocked address classes.

Do not add a raw "allow all" default for convenience.

---

## 16. Logging and telemetry

### 16.1 Logging API

Library modules use the project logging facade or standard logging integration;
they do not configure handlers.

Use stable event names, not prose-only messages.

Good:

```python
logger.info(
    "simulation.request.completed",
    extra={
        "service_id": service_id,
        "operation_id": operation_id,
        "outcome": "success",
        "duration_ms": duration_ms,
    },
)
```

Avoid:

```python
logger.info(f"Handled {request_body} using {api_key}")
```

### 16.2 Required context

Where available, include:

- run ID;
- request/event ID;
- trace and span IDs;
- scenario ID and revision;
- service and operation ID;
- simulator kind;
- outcome;
- provenance;
- state version;
- duration.

### 16.3 Prohibited log data

Do not log:

- API keys, tokens, passwords, cookies, or authorization headers;
- raw prompts or model responses by default;
- full request/response bodies by default;
- unrestricted file contents;
- full SQL bind values;
- personal data without an explicit redaction policy.

### 16.4 Metrics

Labels must be bounded. Never use request IDs, paths with user identifiers,
object keys, prompts, or error messages as metric labels.

---

## 17. Testing standards

### 17.1 Unit tests

Unit tests must be:

- deterministic;
- isolated;
- fast;
- free of network and containers;
- independent of wall clock;
- explicit about seed;
- readable as behavior documentation.

Use fakes for clocks, randomness, state stores, artifact stores, model gateways,
and telemetry.

### 17.2 Property tests

Use property-based tests for:

- canonical request normalization;
- matching precedence;
- JSON/schema round trips;
- path normalization and escape resistance;
- stream ordering and duplication rules;
- snapshot/restore invariants;
- idempotency;
- deterministic generation.

Persist a minimized regression example when a property test finds a bug.

### 17.3 Golden files

Golden files are appropriate for:

- compiled contract output;
- structured diagnostics;
- artifact manifests;
- deterministic event journals;
- exported scenario bundles.

A golden update must be reviewed as a semantic change. Do not regenerate all
goldens without explaining why.

### 17.4 Integration tests

Integration tests:

- use ephemeral ports and temporary directories;
- set explicit deadlines;
- clean up in `finally`/fixtures;
- never depend on test ordering;
- mark container/provider requirements;
- capture useful diagnostics on failure;
- test lifecycle and shutdown, not only happy-path requests.

### 17.5 Regression tests

A bug fix must include a test that fails before the fix and passes after it,
unless the bug cannot be reproduced. In that case, document the verification
method.

---

## 18. Security-sensitive review triggers

Treat a change as security-sensitive when it touches:

- path or archive handling;
- proxying or redirects;
- authentication or authorization;
- secret resolution;
- model prompt construction;
- template or expression evaluation;
- SQL execution;
- plugin loading;
- subprocesses or containers;
- control API exposure;
- recording/redaction;
- cryptography or signing;
- deserialization of untrusted data.

Security-sensitive changes require adversarial tests and a brief threat analysis
in the pull request or change summary.

---

## 19. Dependency policy

Before adding a dependency:

1. determine whether the standard library or an existing dependency suffices;
2. verify license compatibility;
3. assess maintenance and security posture;
4. estimate transitive dependencies and image-size impact;
5. isolate it behind an optional extra when it serves one protocol;
6. keep its types and exceptions at the adapter boundary;
7. add a compatibility test;
8. update lock files intentionally.

Do not add two libraries for the same responsibility without an ADR.

---

## 20. Documentation rules

Update documentation in the same change when behavior changes.

At minimum:

- config keys require schema descriptions and examples;
- public CLI changes require help text and docs;
- new plugins require a capability table and runnable example;
- new errors require a troubleshooting entry when operator-actionable;
- architecture changes require an ADR;
- security changes require `SECURITY.md` or operations documentation updates;
- deprecations require replacement guidance and removal timing.

Examples are tests. CI should validate or smoke-test them.

---

## 21. Git and change hygiene

- Keep changes scoped to the requested behavior.
- Do not reformat unrelated files.
- Do not replace working abstractions merely for stylistic preference.
- Preserve public compatibility unless the task explicitly allows a break.
- Separate mechanical migrations from behavioral changes where practical.
- Do not commit generated runtime state, credentials, recordings with sensitive
  data, caches, or local sandbox contents.
- Lock-file changes must correspond to intentional dependency changes.
- Do not claim a task is complete while leaving failing tests you caused.

Suggested commit messages:

```text
feat(http): add RFC 9457 validation errors
fix(state): preserve idempotent response across snapshot restore
refactor(models): isolate provider SDK behind gateway
docs(mcp): document tool authorization policy
test(filesystem): cover symlink escape race
```

---

## 22. Task-specific playbooks

### 22.1 Bug fix

1. reproduce with a failing test;
2. identify the violated invariant;
3. fix at the narrowest correct layer;
4. verify no state mutation occurs on failed validation;
5. run the focused test and relevant broader suite;
6. document compatibility or migration impact.

### 22.2 New configuration field

1. define semantics and default;
2. update JSON Schema;
3. update boundary model;
4. update resolver and provenance;
5. add valid, invalid, env-override, and redaction tests;
6. update examples and docs;
7. decide migration/deprecation behavior.

### 22.3 New model provider

1. add an optional dependency extra;
2. implement `ModelGateway`;
3. add a model profile example without secrets;
4. map provider capabilities;
5. map timeouts, cancellation, rate limits, and errors;
6. add scripted unit tests;
7. add opt-in integration tests;
8. ensure routing code remains provider-agnostic.

### 22.4 New model profile

A profile-only addition should usually require no Python change.

1. add `models/<profile>.json`;
2. validate against schema;
3. use environment indirection for secrets;
4. declare capabilities honestly;
5. add routing only when a task should use it;
6. run profile validation;
7. do not include a real credential.

### 22.5 New simulator

Follow the full simulator checklist in section 12. Start with one end-to-end
vertical slice before adding every protocol feature.

### 22.6 Performance optimization

1. add or identify a benchmark;
2. measure before;
3. optimize without weakening correctness or limits;
4. measure after under the same conditions;
5. add regression thresholds carefully;
6. preserve observability and cancellation;
7. document memory/latency tradeoffs.

---

## 23. Definition of done

A change is done when applicable items are satisfied:

- behavior matches the request and `SPEC.md`;
- package boundaries are preserved;
- schemas and migrations are correct;
- deterministic behavior is maintained;
- security controls are present;
- errors are stable and safe;
- logs and metrics are structured and bounded;
- unit and integration tests cover the change;
- model tests have deterministic fakes;
- docs and examples are updated;
- formatting, lint, typing, tests, and build pass;
- no secret or sensitive artifact was introduced;
- the final report lists changes, validation performed, and any remaining risk.

---

## 24. Final report format for agents

Use a compact report:

```text
Summary
- What changed and why.

Validation
- Exact commands run and outcomes.

Compatibility / security
- Schema, API, migration, determinism, or security implications.

Remaining risk
- Only concrete unresolved items; write "None identified" when appropriate.
```

Do not include hidden reasoning or a long chronological work log. Report
evidence and decisions.

---

## 25. Prohibited shortcuts

Do not:

- hard-code a provider or model in application logic;
- make live LLM calls from unit tests;
- accept model output without validation;
- execute model-generated code;
- use unrestricted `eval`, `exec`, shell invocation, or unsafe deserialization;
- configure root logging from a library;
- swallow exceptions;
- use an unbounded queue or retry loop;
- mutate state before response validation;
- let a plugin bypass authorization or provenance;
- expose control endpoints publicly by default;
- write outside the filesystem sandbox;
- proxy to arbitrary destinations;
- store secrets in examples;
- claim protocol support that is not covered by conformance tests;
- silently ignore unknown configuration;
- change a public schema without versioning or migration analysis.

---

## 26. Guiding question

For every change, ask:

> Can the same scenario still run safely, deterministically, observably, and
> without a live model after its artifacts have been materialized?

When the answer is no, the design needs an explicit exception, configuration
gate, tests, and documentation.
