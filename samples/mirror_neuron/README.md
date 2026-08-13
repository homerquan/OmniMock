# Mirror Neuron runtime API sample

This sample is a deterministic, model-free substitute for the Mirror Neuron
runtime used by OtterDesk. [`manifest.json`](manifest.json) declares all 140
HTTP method/path pairs and all six WebSocket routes exposed by the current
`mn-api` route modules. [`cli-manifest.json`](cli-manifest.json) declares the
bounded `mn` command responses used by desktop runtime checks and developer
workflows. Each HTTP or WebSocket route is served beneath both `/api/v1` and
`/api/v2`, matching OtterDesk's compatibility and stable-job clients.

The manifest contains materialized catalog, job, run, workflow-progress,
observability, resource, schedule, service, model, deployment, artifact, and
health responses. It makes no relay, container, model, filesystem-host, or
outbound-network calls.

## Run the mock

From the OmniMock repository:

```bash
uv run omnimock --root samples/mirror_neuron validate
uv run omnimock --root samples/mirror_neuron --output json inspect routes
uv run omnimock --root samples/mirror_neuron --output json inspect commands
uv run omnimock --root samples/mirror_neuron serve desktop
```

The mock listens on `http://127.0.0.1:54001`. A quick check is:

```bash
curl http://127.0.0.1:54001/api/v2/health
curl http://127.0.0.1:54001/api/v2/jobs
curl http://127.0.0.1:54001/api/v2/runs/mn-run-demo/monitor
samples/mirror_neuron/bin/mn runtime health --json --timeout 2
samples/mirror_neuron/bin/mn job status mn-job-demo --json
```

Runtime state, if created by OmniMock administration commands, stays below this
sample's `.omnimock/` directory.

## Drive OtterDesk with the mock

Start OtterDesk with an explicit API base URL and disable its runtime
auto-installer for the isolated session:

```bash
OTTERDESK_MIRROR_NEURON_API_BASE_URL=http://127.0.0.1:54001/api/v1 \
MN_API_BASE_URL=http://127.0.0.1:54001/api/v1 \
MN_COMMAND=/Users/homer/Projects/OmniMock/samples/mirror_neuron/bin/mn \
OTTERDESK_DISABLE_MIRROR_NEURON_AUTO_INSTALL=true \
npm run dev
```

Run that command from `/Users/homer/Projects/otterdesk-desktop-app`. OtterDesk
derives `/api/v2` from the configured `/api/v1` URL for stable jobs and runs.
No API token is needed because this local mock declares authentication disabled.
`MN_COMMAND` also redirects OtterDesk's `mn runtime health`, start, and stop
process calls to the deterministic manifest runner instead of the live CLI.

## Manifest conventions

- `base_paths` expands every relative route under both API versions.
- `{name}` captures one path segment; `{name:path}` captures a bounded path tail.
- `{{path.name}}`, `{{query.name}}`, `{{body.name}}`, and `{{clock.now}}` are the
  only response substitutions. They are data lookups, not an expression or
  template execution language.
- WebSocket definitions can emit opening messages, accept bounded JSON client
  messages, match partial JSON objects, send response scripts, and close with a
  deterministic code. Client frames, frame size, interaction count, idle time,
  and emitted messages are all bounded.
- CLI commands match exact argument arrays with optional data-only `{name}`
  captures. Unknown commands return exit code 2. OmniMock never joins arguments
  into a shell command or executes manifest-defined code.
- Inline responses, WebSocket messages, and CLI output are bounded by `limits`.
- Unknown fields, duplicate routes, unsafe paths, and out-of-project manifest
  references fail validation before the listener binds.

The route inventory was reconciled against
`/Users/homer/Projects/mirror-neuron-set/mn-api/mn_api/routes` on 2026-08-13.
The CLI sample was reconciled against OtterDesk's `MN_COMMAND` integration and
the current `mn-cli` command groups on the same date.
